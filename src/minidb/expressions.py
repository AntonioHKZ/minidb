"""Avaliação de expressões com a lógica de três valores do SQL.

Qualquer operação envolvendo NULL resulta em NULL (``None``), exceto
``IS [NOT] NULL``, ``AND``/``OR`` (que seguem a tabela-verdade do SQL) e
``COALESCE``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from minidb.errors import ExecutionError
from minidb.sql import ast
from minidb.sql.parser import AGGREGATES

# Recebe o nome da coluna e devolve seu valor na linha atual.
Resolver = Callable[[ast.Column], Any]


def evaluate(expr: ast.Expr, resolve: Resolver, aggregates: dict | None = None) -> Any:
    if isinstance(expr, ast.Literal):
        return expr.value

    if isinstance(expr, ast.Column):
        return resolve(expr)

    if isinstance(expr, ast.BinaryOp):
        if expr.op == "AND":
            return _and(
                evaluate(expr.left, resolve, aggregates),
                lambda: evaluate(expr.right, resolve, aggregates),
            )
        if expr.op == "OR":
            return _or(
                evaluate(expr.left, resolve, aggregates),
                lambda: evaluate(expr.right, resolve, aggregates),
            )
        left = evaluate(expr.left, resolve, aggregates)
        right = evaluate(expr.right, resolve, aggregates)
        return _binary(expr.op, left, right)

    if isinstance(expr, ast.UnaryOp):
        value = evaluate(expr.operand, resolve, aggregates)
        if value is None:
            return None
        if expr.op == "NOT":
            return not _truthy(value)
        if expr.op == "-":
            _require_number(value, "-")
            return -value
        raise ExecutionError(f"operador unário desconhecido {expr.op}")

    if isinstance(expr, ast.IsNull):
        is_null = evaluate(expr.operand, resolve, aggregates) is None
        return not is_null if expr.negated else is_null

    if isinstance(expr, ast.Between):
        value = evaluate(expr.operand, resolve, aggregates)
        low = evaluate(expr.low, resolve, aggregates)
        high = evaluate(expr.high, resolve, aggregates)
        result = _and(_binary(">=", value, low), lambda: _binary("<=", value, high))
        return _not(result) if expr.negated else result

    if isinstance(expr, ast.InList):
        value = evaluate(expr.operand, resolve, aggregates)
        if value is None:
            return None
        saw_null = False
        for item in expr.items:
            candidate = evaluate(item, resolve, aggregates)
            if candidate is None:
                saw_null = True
            elif _binary("=", value, candidate):
                return not expr.negated
        if saw_null:
            return None
        return expr.negated

    if isinstance(expr, ast.FuncCall):
        if expr.name in AGGREGATES:
            if aggregates is None or expr not in aggregates:
                raise ExecutionError(f"{expr.name}() só pode ser usado na lista do SELECT")
            return aggregates[expr]
        args = [evaluate(a, resolve, aggregates) for a in expr.args]
        return call_scalar(expr.name, args)

    if isinstance(expr, ast.Star):
        raise ExecutionError("'*' não pode ser usado dentro de uma expressão")

    raise ExecutionError(f"expressão não suportada: {expr!r}")


def is_true(value: Any) -> bool:
    """Um WHERE só aceita a linha quando a condição é verdadeira (não NULL)."""
    return value is not None and _truthy(value)


# ---------------------------------------------------------------- operadores


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        raise ExecutionError(f"valor TEXT {value!r} usado como condição")
    return bool(value)


def _not(value: Any) -> Any:
    return None if value is None else not _truthy(value)


def _and(left: Any, right_thunk: Callable[[], Any]) -> Any:
    if left is not None and not _truthy(left):
        return False
    right = right_thunk()
    if right is not None and not _truthy(right):
        return False
    if left is None or right is None:
        return None
    return True


def _or(left: Any, right_thunk: Callable[[], Any]) -> Any:
    if left is not None and _truthy(left):
        return True
    right = right_thunk()
    if right is not None and _truthy(right):
        return True
    if left is None or right is None:
        return None
    return False


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float))


def _require_number(v: Any, op: str) -> None:
    if not _is_number(v):
        raise ExecutionError(f"operador '{op}' requer números, recebeu {v!r}")


def _comparable(a: Any, b: Any) -> bool:
    return (_is_number(a) and _is_number(b)) or type(a) is type(b)


def _binary(op: str, a: Any, b: Any) -> Any:
    if a is None or b is None:
        return None

    if op in ("=", "<>", "<", "<=", ">", ">="):
        if not _comparable(a, b):
            raise ExecutionError(f"não é possível comparar {a!r} com {b!r}")
        if op == "=":
            return a == b
        if op == "<>":
            return a != b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        return a >= b

    if op == "LIKE":
        if not isinstance(a, str) or not isinstance(b, str):
            raise ExecutionError("LIKE requer valores TEXT")
        return _like_regex(b).fullmatch(a) is not None

    if op == "||":
        return _to_text(a) + _to_text(b)

    _require_number(a, op)
    _require_number(b, op)
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op in ("/", "%"):
        if b == 0:
            raise ExecutionError("divisão por zero")
        if isinstance(a, int) and isinstance(b, int):
            # divisão inteira truncando em direção a zero, como em C/SQLite
            q = abs(a) // abs(b)
            q = q if (a >= 0) == (b >= 0) else -q
            return q if op == "/" else a - b * q
        return a / b if op == "/" else a % b

    raise ExecutionError(f"operador desconhecido {op}")


def _to_text(v: Any) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return str(v)


@lru_cache(maxsize=256)
def _like_regex(pattern: str) -> re.Pattern[str]:
    parts = []
    for ch in pattern:
        if ch == "%":
            parts.append(".*")
        elif ch == "_":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    return re.compile("".join(parts), re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------- funções escalares


def _fn_upper(s):
    return s.upper()


def _fn_lower(s):
    return s.lower()


def _fn_length(s):
    return len(s)


def _fn_abs(x):
    return abs(x)


def _fn_round(x, digits=0):
    return round(x, int(digits)) if digits else float(round(x))


_SCALARS: dict[str, tuple[Callable[..., Any], int, int]] = {
    # nome: (função, mínimo de argumentos, máximo de argumentos)
    "UPPER": (_fn_upper, 1, 1),
    "LOWER": (_fn_lower, 1, 1),
    "LENGTH": (_fn_length, 1, 1),
    "ABS": (_fn_abs, 1, 1),
    "ROUND": (_fn_round, 1, 2),
}


def call_scalar(name: str, args: list[Any]) -> Any:
    if name == "COALESCE":
        return next((a for a in args if a is not None), None)
    if name not in _SCALARS:
        raise ExecutionError(f"função desconhecida {name}()")
    fn, lo, hi = _SCALARS[name]
    if not lo <= len(args) <= hi:
        raise ExecutionError(f"{name}() recebeu {len(args)} argumento(s)")
    if any(a is None for a in args):
        return None
    try:
        return fn(*args)
    except (TypeError, AttributeError):
        raise ExecutionError(f"argumento inválido para {name}(): {args!r}") from None


# ---------------------------------------------------------------- utilitários


def find_aggregates(expr: ast.Expr) -> list[ast.FuncCall]:
    """Lista as chamadas de agregação contidas em ``expr``."""
    found: list[ast.FuncCall] = []

    def walk(e: ast.Expr) -> None:
        if isinstance(e, ast.FuncCall):
            if e.name in AGGREGATES:
                for a in e.args:
                    if find_aggregates(a):
                        raise ExecutionError("agregações aninhadas não são permitidas")
                found.append(e)
                return
            for a in e.args:
                walk(a)
        elif isinstance(e, ast.BinaryOp):
            walk(e.left)
            walk(e.right)
        elif isinstance(e, ast.UnaryOp):
            walk(e.operand)
        elif isinstance(e, ast.IsNull):
            walk(e.operand)
        elif isinstance(e, ast.Between):
            walk(e.operand)
            walk(e.low)
            walk(e.high)
        elif isinstance(e, ast.InList):
            walk(e.operand)
            for i in e.items:
                walk(i)

    walk(expr)
    return found


def expr_to_sql(expr: ast.Expr) -> str:
    """Representação textual usada para nomear colunas do resultado."""
    if isinstance(expr, ast.Literal):
        v = expr.value
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, str):
            return "'" + v.replace("'", "''") + "'"
        return str(v)
    if isinstance(expr, ast.Column):
        return f"{expr.table}.{expr.name}" if expr.table else expr.name
    if isinstance(expr, ast.Star):
        return "*"
    if isinstance(expr, ast.UnaryOp):
        sep = " " if expr.op == "NOT" else ""
        return f"{expr.op}{sep}{expr_to_sql(expr.operand)}"
    if isinstance(expr, ast.BinaryOp):
        return f"{expr_to_sql(expr.left)} {expr.op} {expr_to_sql(expr.right)}"
    if isinstance(expr, ast.IsNull):
        return f"{expr_to_sql(expr.operand)} IS {'NOT ' if expr.negated else ''}NULL"
    if isinstance(expr, ast.Between):
        neg = "NOT " if expr.negated else ""
        return (
            f"{expr_to_sql(expr.operand)} {neg}BETWEEN "
            f"{expr_to_sql(expr.low)} AND {expr_to_sql(expr.high)}"
        )
    if isinstance(expr, ast.InList):
        neg = "NOT " if expr.negated else ""
        items = ", ".join(expr_to_sql(i) for i in expr.items)
        return f"{expr_to_sql(expr.operand)} {neg}IN ({items})"
    if isinstance(expr, ast.FuncCall):
        d = "DISTINCT " if expr.distinct else ""
        return f"{expr.name}({d}{', '.join(expr_to_sql(a) for a in expr.args)})"
    return repr(expr)
