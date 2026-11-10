"""Esquemas, tabelas e validação de tipos."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minidb.errors import CatalogError, ConstraintError
from minidb.storage.btree import BPlusTree, DuplicateKeyError

Row = tuple  # uma linha é uma tupla na ordem das colunas do esquema


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    type: str
    primary_key: bool = False
    not_null: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "primary_key": self.primary_key,
            "not_null": self.not_null,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ColumnSchema:
        return cls(d["name"], d["type"], d["primary_key"], d["not_null"])


def coerce(value: Any, column: ColumnSchema) -> Any:
    """Converte ``value`` para o tipo da coluna ou lança ``ConstraintError``."""
    if value is None:
        if column.not_null:
            raise ConstraintError(f"coluna '{column.name}' não aceita NULL")
        return None

    t = column.type
    if t == "INTEGER":
        if isinstance(value, bool):
            raise ConstraintError(f"coluna '{column.name}' espera INTEGER, recebeu BOOLEAN")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    elif t == "REAL":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    elif t == "TEXT":
        if isinstance(value, str):
            return value
    elif t == "BOOLEAN":
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)

    raise ConstraintError(
        f"coluna '{column.name}' espera {t}, recebeu {type_name(value)} ({value!r})"
    )


def type_name(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


class Table:
    """Uma tabela armazenada em uma B+ tree indexada pela chave primária.

    Se o esquema não declara PRIMARY KEY, um ``rowid`` interno e
    autoincrementado é usado como chave da árvore.
    """

    def __init__(self, name: str, columns: list[ColumnSchema], btree_order: int = 32) -> None:
        if not columns:
            raise CatalogError("uma tabela precisa de ao menos uma coluna")
        names = [c.name for c in columns]
        if len(set(names)) != len(names):
            raise CatalogError(f"colunas duplicadas na tabela '{name}'")
        pks = [i for i, c in enumerate(columns) if c.primary_key]
        if len(pks) > 1:
            raise CatalogError("apenas uma coluna pode ser PRIMARY KEY")

        self.name = name
        self.columns = columns
        self.pk_index: int | None = pks[0] if pks else None
        self.rows = BPlusTree(order=btree_order)
        self.next_rowid = 1
        self._positions = {c.name: i for i, c in enumerate(columns)}

    @property
    def pk_column(self) -> ColumnSchema | None:
        return None if self.pk_index is None else self.columns[self.pk_index]

    def column_index(self, name: str) -> int:
        try:
            return self._positions[name]
        except KeyError:
            raise CatalogError(f"coluna '{name}' não existe na tabela '{self.name}'") from None

    def has_column(self, name: str) -> bool:
        return name in self._positions

    def validate_row(self, values: list[Any]) -> Row:
        return tuple(coerce(v, c) for v, c in zip(values, self.columns))

    def key_of(self, row: Row) -> Any:
        if self.pk_index is None:
            raise ValueError("tabela sem chave primária explícita")
        return row[self.pk_index]

    def allocate_key(self, row: Row) -> Any:
        if self.pk_index is not None:
            return row[self.pk_index]
        key = self.next_rowid
        self.next_rowid += 1
        return key

    # As operações abaixo assumem que a validação já foi feita pelo executor.

    def insert(self, key: Any, row: Row) -> None:
        try:
            self.rows.insert(key, row)
        except DuplicateKeyError:
            raise ConstraintError(
                f"chave primária duplicada {key!r} na tabela '{self.name}'"
            ) from None
        if self.pk_index is None and key >= self.next_rowid:
            self.next_rowid = key + 1

    def delete(self, key: Any) -> None:
        self.rows.delete(key)

    def schema_dict(self) -> dict[str, Any]:
        return {"name": self.name, "columns": [c.to_dict() for c in self.columns]}

    def describe(self) -> str:
        parts = []
        for c in self.columns:
            s = f"{c.name} {c.type}"
            if c.primary_key:
                s += " PRIMARY KEY"
            elif c.not_null:
                s += " NOT NULL"
            parts.append(s)
        return f"CREATE TABLE {self.name} ({', '.join(parts)});"


class Catalog:
    def __init__(self) -> None:
        self.tables: dict[str, Table] = {}

    def get(self, name: str) -> Table:
        try:
            return self.tables[name]
        except KeyError:
            raise CatalogError(f"tabela '{name}' não existe") from None

    def __contains__(self, name: str) -> bool:
        return name in self.tables
