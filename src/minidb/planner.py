"""Escolha do caminho de acesso às linhas de uma tabela.

O planejador olha para os termos do WHERE ligados por AND e procura
comparações entre a chave primária e um literal. Com isso decide entre:

* ``PrimaryKeyLookup`` – ``pk = 5`` ou ``pk IN (1, 2, 3)``: O(k log n)
* ``PrimaryKeyRange``  – ``pk > 10 AND pk <= 20``, ``pk BETWEEN ...``: O(log n + k)
* ``FullScan``         – qualquer outro caso: O(n)

O WHERE completo continua sendo avaliado depois sobre as linhas
retornadas, então o plano só precisa ser *conservador*: pode devolver
linhas a mais, nunca a menos.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from minidb.catalog import Table
from minidb.sql import ast

_FLIP = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "="}


class AccessPlan:
    def scan(self, table: Table) -> Iterator[tuple[Any, tuple]]:
        raise NotImplementedError

    def describe(self, table: Table) -> str:
        raise NotImplementedError


class FullScan(AccessPlan):
    def scan(self, table):
        return table.rows.items()

    def describe(self, table):
        return f"SCAN {table.name}"


@dataclass
class PrimaryKeyLookup(AccessPlan):
    keys: list[Any]

    def scan(self, table):
        for key in sorted(set(self.keys)):
            row = table.rows.get(key)
            if row is not None:
                yield key, row

    def describe(self, table):
        pk = table.pk_column.name  # type: ignore[union-attr]
        if len(self.keys) == 1:
            return f"SEARCH {table.name} USING PRIMARY KEY ({pk} = {self.keys[0]!r})"
        return f"SEARCH {table.name} USING PRIMARY KEY ({pk} IN {len(self.keys)} valores)"


@dataclass
class PrimaryKeyRange(AccessPlan):
    low: Any = None
    high: Any = None
    include_low: bool = True
    include_high: bool = True

    def scan(self, table):
        return table.rows.range(self.low, self.high, self.include_low, self.include_high)

    def describe(self, table):
        pk = table.pk_column.name  # type: ignore[union-attr]
        parts = []
        if self.low is not None:
            parts.append(f"{pk} {'>=' if self.include_low else '>'} {self.low!r}")
        if self.high is not None:
            parts.append(f"{pk} {'<=' if self.include_high else '<'} {self.high!r}")
        return f"SEARCH {table.name} USING PRIMARY KEY RANGE ({' AND '.join(parts)})"


@dataclass
class EmptyScan(AccessPlan):
    reason: str = "condição sempre falsa"

    def scan(self, table):
        return iter(())

    def describe(self, table):
        return f"EMPTY {table.name} ({self.reason})"


def split_conjuncts(expr: ast.Expr | None) -> list[ast.Expr]:
    if expr is None:
        return []
    if isinstance(expr, ast.BinaryOp) and expr.op == "AND":
        return split_conjuncts(expr.left) + split_conjuncts(expr.right)
    return [expr]


def _compatible(value: Any, column_type: str) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if column_type in ("INTEGER", "REAL"):
        return isinstance(value, (int, float))
    if column_type == "TEXT":
        return isinstance(value, str)
    return False


@dataclass
class _Bounds:
    equals: list[set] = field(default_factory=list)
    low: Any = None
    include_low: bool = True
    high: Any = None
    include_high: bool = True

    def add_low(self, value: Any, inclusive: bool) -> None:
        if self.low is None or value > self.low or (value == self.low and not inclusive):
            self.low, self.include_low = value, inclusive

    def add_high(self, value: Any, inclusive: bool) -> None:
        if self.high is None or value < self.high or (value == self.high and not inclusive):
            self.high, self.include_high = value, inclusive

    def admits(self, key: Any) -> bool:
        if self.low is not None and (key < self.low or (key == self.low and not self.include_low)):
            return False
        if self.high is not None and (
            key > self.high or (key == self.high and not self.include_high)
        ):
            return False
        return True


def _is_pk(expr: ast.Expr, table: Table) -> bool:
    pk = table.pk_column
    return (
        pk is not None
        and isinstance(expr, ast.Column)
        and expr.name == pk.name
        and expr.table in (None, table.name)
    )


def choose_plan(table: Table, where: ast.Expr | None) -> AccessPlan:
    pk = table.pk_column
    if pk is None or where is None:
        return FullScan()

    bounds = _Bounds()
    usable = False

    for term in split_conjuncts(where):
        if isinstance(term, ast.BinaryOp) and term.op in _FLIP:
            left, right, op = term.left, term.right, term.op
            if _is_pk(right, table) and isinstance(left, ast.Literal):
                left, right, op = right, left, _FLIP[op]
            if not (_is_pk(left, table) and isinstance(right, ast.Literal)):
                continue
            value = right.value
            if not _compatible(value, pk.type):
                continue
            usable = True
            if op == "=":
                bounds.equals.append({value})
            elif op == ">":
                bounds.add_low(value, False)
            elif op == ">=":
                bounds.add_low(value, True)
            elif op == "<":
                bounds.add_high(value, False)
            elif op == "<=":
                bounds.add_high(value, True)

        elif isinstance(term, ast.Between) and not term.negated and _is_pk(term.operand, table):
            if (
                isinstance(term.low, ast.Literal)
                and isinstance(term.high, ast.Literal)
                and _compatible(term.low.value, pk.type)
                and _compatible(term.high.value, pk.type)
            ):
                usable = True
                bounds.add_low(term.low.value, True)
                bounds.add_high(term.high.value, True)

        elif isinstance(term, ast.InList) and not term.negated and _is_pk(term.operand, table):
            if all(
                isinstance(i, ast.Literal) and _compatible(i.value, pk.type) for i in term.items
            ):
                usable = True
                bounds.equals.append({i.value for i in term.items})  # type: ignore[union-attr]

    if not usable:
        return FullScan()

    if bounds.equals:
        keys = set.intersection(*bounds.equals)
        keys = {k for k in keys if bounds.admits(k)}
        if not keys:
            return EmptyScan()
        return PrimaryKeyLookup(sorted(keys))

    if bounds.low is not None and bounds.high is not None:
        if bounds.low > bounds.high or (
            bounds.low == bounds.high and not (bounds.include_low and bounds.include_high)
        ):
            return EmptyScan()

    return PrimaryKeyRange(bounds.low, bounds.high, bounds.include_low, bounds.include_high)
