"""Nós da árvore sintática abstrata (AST)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

# --------------------------------------------------------------- expressões


@dataclass(frozen=True)
class Literal:
    value: Any  # int | float | str | bool | None


@dataclass(frozen=True)
class Column:
    name: str
    table: str | None = None


@dataclass(frozen=True)
class Star:
    pass


@dataclass(frozen=True)
class UnaryOp:
    op: str  # "-" | "NOT"
    operand: Expr


@dataclass(frozen=True)
class BinaryOp:
    op: str  # + - * / % || = <> < <= > >= AND OR LIKE
    left: Expr
    right: Expr


@dataclass(frozen=True)
class IsNull:
    operand: Expr
    negated: bool = False


@dataclass(frozen=True)
class Between:
    operand: Expr
    low: Expr
    high: Expr
    negated: bool = False


@dataclass(frozen=True)
class InList:
    operand: Expr
    items: tuple[Expr, ...]
    negated: bool = False


@dataclass(frozen=True)
class FuncCall:
    name: str  # sempre em maiúsculas
    args: tuple[Expr, ...]
    distinct: bool = False


Expr = Union[Literal, Column, Star, UnaryOp, BinaryOp, IsNull, Between, InList, FuncCall]

# --------------------------------------------------------------- comandos


@dataclass
class ColumnDef:
    name: str
    type: str  # INTEGER | REAL | TEXT | BOOLEAN
    primary_key: bool = False
    not_null: bool = False


@dataclass
class CreateTable:
    name: str
    columns: list[ColumnDef]
    if_not_exists: bool = False


@dataclass
class DropTable:
    name: str
    if_exists: bool = False


@dataclass
class Insert:
    table: str
    columns: list[str] | None
    rows: list[list[Expr]]


@dataclass
class SelectItem:
    expr: Expr
    alias: str | None = None


@dataclass
class OrderItem:
    expr: Expr
    descending: bool = False


@dataclass
class Select:
    items: list[SelectItem]
    table: str | None
    where: Expr | None = None
    group_by: list[Expr] = field(default_factory=list)
    order_by: list[OrderItem] = field(default_factory=list)
    limit: int | None = None
    offset: int | None = None
    distinct: bool = False


@dataclass
class Update:
    table: str
    assignments: list[tuple[str, Expr]]
    where: Expr | None = None


@dataclass
class Delete:
    table: str
    where: Expr | None = None


@dataclass
class Explain:
    statement: Select


@dataclass
class Checkpoint:
    pass


Statement = Union[CreateTable, DropTable, Insert, Select, Update, Delete, Explain, Checkpoint]
