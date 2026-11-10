"""Ponto de entrada da biblioteca: executa comandos SQL sobre o catálogo.

Fluxo de um comando que altera dados::

    SQL -> parser -> AST -> executor calcula as mudanças (sem aplicar)
        -> registro vai para o WAL (fsync) -> mudanças aplicadas na memória

Como tudo é validado antes de tocar no WAL, um comando que falha não deixa
nenhum efeito parcial (atomicidade por comando).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from minidb import planner
from minidb.catalog import Catalog, ColumnSchema, Table
from minidb.errors import CatalogError, ConstraintError, ExecutionError, MiniDBError
from minidb.expressions import evaluate, expr_to_sql, find_aggregates, is_true
from minidb.sql import ast
from minidb.sql.parser import parse
from minidb.storage.snapshot import read_snapshot, write_snapshot
from minidb.storage.wal import WriteAheadLog

SNAPSHOT_FILE = "data.snapshot"
WAL_FILE = "wal.log"


@dataclass
class Result:
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    rowcount: int = 0
    message: str = ""

    def __iter__(self) -> Iterator[tuple]:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    def scalar(self) -> Any:
        """Valor da primeira coluna da primeira linha (útil em testes)."""
        return self.rows[0][0] if self.rows else None


class Database:
    """Banco de dados SQL.

    ``Database()`` cria um banco apenas em memória. ``Database("pasta")``
    persiste os dados em ``pasta/`` usando snapshot + write-ahead log.
    """

    def __init__(
        self,
        path: str | None = None,
        *,
        btree_order: int = 32,
        checkpoint_every: int = 1000,
        sync: bool = True,
    ) -> None:
        self.path = path
        self.btree_order = btree_order
        self.checkpoint_every = checkpoint_every
        self.catalog = Catalog()
        self._lsn = 0
        self._wal: WriteAheadLog | None = None

        if path is not None:
            os.makedirs(path, exist_ok=True)
            self._recover(sync)

    # ----------------------------------------------------------- ciclo de vida

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._wal is not None:
            self.checkpoint()
            self._wal.close()
            self._wal = None

    def _recover(self, sync: bool) -> None:
        assert self.path is not None
        snapshot = read_snapshot(os.path.join(self.path, SNAPSHOT_FILE))
        if snapshot is not None:
            self._load_snapshot(snapshot)

        self._wal = WriteAheadLog(os.path.join(self.path, WAL_FILE), sync=sync)
        for record in self._wal.replay():
            # registros já incluídos no snapshot são ignorados; isso torna a
            # recuperação idempotente se o processo cair durante o checkpoint
            if record["lsn"] <= self._lsn:
                continue
            self._apply(record)
            self._lsn = record["lsn"]

    def checkpoint(self) -> None:
        """Grava um snapshot completo e esvazia o WAL."""
        if self.path is None or self._wal is None:
            return
        tables = {}
        for name, table in self.catalog.tables.items():
            tables[name] = {
                "schema": table.schema_dict(),
                "next_rowid": table.next_rowid,
                "rows": [[k, list(r)] for k, r in table.rows.items()],
            }
        write_snapshot(os.path.join(self.path, SNAPSHOT_FILE), {"lsn": self._lsn, "tables": tables})
        self._wal.reset()

    def _load_snapshot(self, data: dict[str, Any]) -> None:
        self._lsn = data["lsn"]
        for name, t in data["tables"].items():
            table = self._make_table(t["schema"])
            for key, row in t["rows"]:
                table.insert(key, tuple(row))
            table.next_rowid = t["next_rowid"]
            self.catalog.tables[name] = table

    # ------------------------------------------------------- WAL + aplicação

    def _commit(self, record: dict[str, Any]) -> None:
        """Escreve no log (se houver) e aplica o registro na memória."""
        self._lsn += 1
        record["lsn"] = self._lsn
        if self._wal is not None:
            self._wal.append(record)
        self._apply(record)
        if self._wal is not None and self._wal.records_since_checkpoint >= self.checkpoint_every:
            self.checkpoint()

    def _make_table(self, schema: dict[str, Any]) -> Table:
        columns = [ColumnSchema.from_dict(c) for c in schema["columns"]]
        return Table(schema["name"], columns, self.btree_order)

    def _apply(self, rec: dict[str, Any]) -> None:
        op = rec["op"]
        if op == "create":
            table = self._make_table(rec["schema"])
            self.catalog.tables[table.name] = table
        elif op == "drop":
            del self.catalog.tables[rec["table"]]
        elif op == "insert":
            table = self.catalog.get(rec["table"])
            for key, row in rec["rows"]:
                table.insert(key, tuple(row))
        elif op == "delete":
            table = self.catalog.get(rec["table"])
            for key in rec["keys"]:
                table.delete(key)
        elif op == "update":
            table = self.catalog.get(rec["table"])
            # remove tudo antes de inserir para permitir trocas de chave
            # como ``UPDATE t SET id = id + 1``
            for old_key, _, _ in rec["changes"]:
                table.delete(old_key)
            for _, new_key, row in rec["changes"]:
                table.insert(new_key, tuple(row))
        else:
            raise MiniDBError(f"registro de log desconhecido: {op}")

    # ----------------------------------------------------------- API pública

    def execute(self, sql: str) -> Result:
        """Executa um ou mais comandos e devolve o resultado do último."""
        result = Result()
        for statement in parse(sql):
            result = self.execute_statement(statement)
        return result

    def query(self, sql: str) -> list[tuple]:
        return self.execute(sql).rows

    def tables(self) -> list[str]:
        return sorted(self.catalog.tables)

    def execute_statement(self, stmt: ast.Statement) -> Result:
        if isinstance(stmt, ast.Select):
            return self._select(stmt)
        if isinstance(stmt, ast.Insert):
            return self._insert(stmt)
        if isinstance(stmt, ast.Update):
            return self._update(stmt)
        if isinstance(stmt, ast.Delete):
            return self._delete(stmt)
        if isinstance(stmt, ast.CreateTable):
            return self._create(stmt)
        if isinstance(stmt, ast.DropTable):
            return self._drop(stmt)
        if isinstance(stmt, ast.Explain):
            return self._explain(stmt.statement)
        if isinstance(stmt, ast.Checkpoint):
            self.checkpoint()
            return Result(message="checkpoint concluído")
        raise ExecutionError(f"comando não suportado: {type(stmt).__name__}")

    # ------------------------------------------------------------------ DDL

    def _create(self, stmt: ast.CreateTable) -> Result:
        if stmt.name in self.catalog:
            if stmt.if_not_exists:
                return Result(message=f"tabela '{stmt.name}' já existe")
            raise CatalogError(f"tabela '{stmt.name}' já existe")
        columns = [ColumnSchema(c.name, c.type, c.primary_key, c.not_null) for c in stmt.columns]
        # cria uma vez só para validar o esquema antes de ir para o log
        table = Table(stmt.name, columns, self.btree_order)
        self._commit({"op": "create", "schema": table.schema_dict()})
        return Result(message=f"tabela '{stmt.name}' criada")

    def _drop(self, stmt: ast.DropTable) -> Result:
        if stmt.name not in self.catalog:
            if stmt.if_exists:
                return Result(message=f"tabela '{stmt.name}' não existe")
            raise CatalogError(f"tabela '{stmt.name}' não existe")
        self._commit({"op": "drop", "table": stmt.name})
        return Result(message=f"tabela '{stmt.name}' removida")

    # ------------------------------------------------------------------ DML

    @staticmethod
    def _row_resolver(table: Table, row: tuple | None):
        def resolve(col: ast.Column) -> Any:
            if col.table is not None and col.table != table.name:
                raise CatalogError(f"tabela '{col.table}' não está no FROM")
            idx = table.column_index(col.name)
            return None if row is None else row[idx]

        return resolve

    def _matching(self, table: Table, where: ast.Expr | None) -> Iterator[tuple[Any, tuple]]:
        plan = planner.choose_plan(table, where)
        for key, row in plan.scan(table):
            if where is None or is_true(evaluate(where, self._row_resolver(table, row))):
                yield key, row

    def _insert(self, stmt: ast.Insert) -> Result:
        table = self.catalog.get(stmt.table)
        if stmt.columns is None:
            targets = list(range(len(table.columns)))
        else:
            if len(set(stmt.columns)) != len(stmt.columns):
                raise CatalogError("coluna repetida no INSERT")
            targets = [table.column_index(c) for c in stmt.columns]

        no_row = self._row_resolver(table, None)
        new_rows: list[list[Any]] = []
        seen_keys: set[Any] = set()
        next_rowid = table.next_rowid

        for values in stmt.rows:
            if len(values) != len(targets):
                raise ExecutionError(
                    f"INSERT com {len(values)} valores para {len(targets)} colunas"
                )
            raw: list[Any] = [None] * len(table.columns)
            for idx, expr in zip(targets, values):
                raw[idx] = evaluate(expr, no_row)
            row = table.validate_row(raw)

            if table.pk_index is None:
                key = next_rowid
                next_rowid += 1
            else:
                key = row[table.pk_index]
                if key in seen_keys or key in table.rows:
                    raise ConstraintError(
                        f"chave primária duplicada {key!r} na tabela '{table.name}'"
                    )
                seen_keys.add(key)
            new_rows.append([key, list(row)])

        self._commit({"op": "insert", "table": table.name, "rows": new_rows})
        return Result(rowcount=len(new_rows), message=f"{len(new_rows)} linha(s) inserida(s)")

    def _delete(self, stmt: ast.Delete) -> Result:
        table = self.catalog.get(stmt.table)
        keys = [key for key, _ in self._matching(table, stmt.where)]
        if keys:
            self._commit({"op": "delete", "table": table.name, "keys": keys})
        return Result(rowcount=len(keys), message=f"{len(keys)} linha(s) removida(s)")

    def _update(self, stmt: ast.Update) -> Result:
        table = self.catalog.get(stmt.table)
        assignments = [(table.column_index(name), expr) for name, expr in stmt.assignments]

        changes: list[list[Any]] = []
        for key, row in list(self._matching(table, stmt.where)):
            resolve = self._row_resolver(table, row)
            new = list(row)
            for idx, expr in assignments:
                new[idx] = evaluate(expr, resolve)  # usa sempre os valores antigos
            new_row = table.validate_row(new)
            new_key = key if table.pk_index is None else new_row[table.pk_index]
            changes.append([key, new_key, list(new_row)])

        if table.pk_index is not None:
            old_keys = {c[0] for c in changes}
            new_keys: set[Any] = set()
            for _, new_key, _ in changes:
                if new_key in new_keys or (new_key in table.rows and new_key not in old_keys):
                    raise ConstraintError(
                        f"chave primária duplicada {new_key!r} na tabela '{table.name}'"
                    )
                new_keys.add(new_key)

        if changes:
            self._commit({"op": "update", "table": table.name, "changes": changes})
        return Result(rowcount=len(changes), message=f"{len(changes)} linha(s) atualizada(s)")

    # --------------------------------------------------------------- SELECT

    def _select(self, stmt: ast.Select) -> Result:
        table = self.catalog.get(stmt.table) if stmt.table else None

        # 1. origem + filtro
        if table is None:
            if stmt.where is not None and not is_true(evaluate(stmt.where, _no_columns)):
                source: list[tuple] = []
            else:
                source = [()]
        else:
            source = [row for _, row in self._matching(table, stmt.where)]

        def resolver(row):
            return _no_columns if table is None else self._row_resolver(table, row)

        # 2. expande * e define nomes das colunas
        items: list[ast.SelectItem] = []
        for item in stmt.items:
            if isinstance(item.expr, ast.Star):
                if table is None:
                    raise ExecutionError("SELECT * requer uma cláusula FROM")
                items.extend(ast.SelectItem(ast.Column(c.name)) for c in table.columns)
            else:
                items.append(item)
        columns = [it.alias or expr_to_sql(it.expr) for it in items]
        aliases = {it.alias: it.expr for it in items if it.alias}

        # 3. agrupamento / agregação
        agg_calls: list[ast.FuncCall] = []
        for it in items:
            agg_calls.extend(find_aggregates(it.expr))
        for o in stmt.order_by:
            agg_calls.extend(find_aggregates(aliases.get(_alias_name(o.expr), o.expr)))

        # cada "grupo" é (linha representativa, valores das agregações)
        groups: list[tuple[tuple | None, dict | None]]
        if agg_calls or stmt.group_by:
            groups = self._group(source, stmt.group_by, agg_calls, resolver)
        else:
            groups = [(row, None) for row in source]

        # 4. projeção + chaves de ordenação
        output: list[tuple[tuple, list[Any]]] = []
        for rep, aggs in groups:
            resolve = resolver(rep)
            out = tuple(evaluate(it.expr, resolve, aggs) for it in items)
            sort_values = []
            for o in stmt.order_by:
                sort_values.append(self._order_value(o.expr, out, aliases, items, resolve, aggs))
            output.append((out, sort_values))

        # 5. DISTINCT
        if stmt.distinct:
            seen: set[tuple] = set()
            unique = []
            for out, sv in output:
                if out not in seen:
                    seen.add(out)
                    unique.append((out, sv))
            output = unique

        # 6. ORDER BY (ordenações estáveis da última chave para a primeira)
        for i in reversed(range(len(stmt.order_by))):
            desc = stmt.order_by[i].descending
            try:
                output.sort(key=lambda pair: _null_first(pair[1][i]), reverse=desc)
            except TypeError as exc:
                raise ExecutionError(f"ORDER BY com tipos incompatíveis: {exc}") from None

        rows = [out for out, _ in output]

        # 7. OFFSET / LIMIT
        start = stmt.offset or 0
        end = None if stmt.limit is None else start + stmt.limit
        rows = rows[start:end]
        return Result(columns, rows, rowcount=len(rows))

    @staticmethod
    def _order_value(expr, out, aliases, items, resolve, aggs):
        # ORDER BY 2 -> segunda coluna do resultado
        if (
            isinstance(expr, ast.Literal)
            and isinstance(expr.value, int)
            and not isinstance(expr.value, bool)
        ):
            pos = expr.value
            if not 1 <= pos <= len(items):
                raise ExecutionError(f"ORDER BY {pos} fora do intervalo 1..{len(items)}")
            return out[pos - 1]
        name = _alias_name(expr)
        if name is not None and name in aliases:
            return evaluate(aliases[name], resolve, aggs)
        return evaluate(expr, resolve, aggs)

    def _group(self, rows, group_by, agg_calls, resolver):
        buckets: dict[tuple, list[tuple]] = {}
        for row in rows:
            resolve = resolver(row)
            key = tuple(evaluate(g, resolve) for g in group_by)
            buckets.setdefault(key, []).append(row)

        # sem GROUP BY sempre existe exatamente um grupo, mesmo vazio
        if not group_by and not buckets:
            buckets[()] = []

        groups = []
        for members in buckets.values():
            aggs = {call: self._aggregate(call, members, resolver) for call in agg_calls}
            groups.append((members[0] if members else None, aggs))
        return groups

    @staticmethod
    def _aggregate(call: ast.FuncCall, rows: list[tuple], resolver) -> Any:
        if call.name == "COUNT" and call.args and isinstance(call.args[0], ast.Star):
            return len(rows)
        if len(call.args) != 1:
            raise ExecutionError(f"{call.name}() espera exatamente 1 argumento")

        values: Iterable[Any] = (evaluate(call.args[0], resolver(r)) for r in rows)
        values = [v for v in values if v is not None]
        if call.distinct:
            values = list(dict.fromkeys(values))

        if call.name == "COUNT":
            return len(values)
        if not values:
            return None
        if call.name in ("SUM", "AVG"):
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
                raise ExecutionError(f"{call.name}() requer valores numéricos")
            total = sum(values)
            return total if call.name == "SUM" else total / len(values)
        try:
            return min(values) if call.name == "MIN" else max(values)
        except TypeError:
            raise ExecutionError(f"{call.name}() com tipos incompatíveis") from None

    # -------------------------------------------------------------- EXPLAIN

    def _explain(self, stmt: ast.Select) -> Result:
        lines: list[str] = []
        if stmt.table is None:
            lines.append("CONSTANT ROW")
        else:
            table = self.catalog.get(stmt.table)
            plan = planner.choose_plan(table, stmt.where)
            lines.append(plan.describe(table))
            if stmt.where is not None:
                lines.append(f"FILTER {expr_to_sql(stmt.where)}")
        has_aggs = any(
            find_aggregates(i.expr) for i in stmt.items if not isinstance(i.expr, ast.Star)
        )
        if stmt.group_by:
            lines.append("GROUP BY " + ", ".join(expr_to_sql(g) for g in stmt.group_by))
        elif has_aggs:
            lines.append("AGGREGATE")
        if stmt.distinct:
            lines.append("DISTINCT")
        if stmt.order_by:
            keys = ", ".join(
                expr_to_sql(o.expr) + (" DESC" if o.descending else "") for o in stmt.order_by
            )
            lines.append(f"SORT {keys}")
        if stmt.limit is not None:
            lines.append(f"LIMIT {stmt.limit}" + (f" OFFSET {stmt.offset}" if stmt.offset else ""))
        return Result(["plan"], [(line,) for line in lines], rowcount=len(lines))


def _no_columns(col: ast.Column) -> Any:
    raise CatalogError(f"coluna '{col.name}' usada sem cláusula FROM")


def _alias_name(expr: ast.Expr) -> str | None:
    if isinstance(expr, ast.Column) and expr.table is None:
        return expr.name
    return None


def _null_first(value: Any) -> tuple:
    # NULL é considerado menor que qualquer valor (mesmo comportamento do SQLite)
    return (0, 0) if value is None else (1, value)


connect = Database
