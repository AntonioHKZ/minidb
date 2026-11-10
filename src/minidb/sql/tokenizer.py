"""Analisador léxico: transforma o texto SQL em uma lista de tokens."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from minidb.errors import SQLSyntaxError

KEYWORDS = {
    "SELECT",
    "FROM",
    "WHERE",
    "INSERT",
    "INTO",
    "VALUES",
    "UPDATE",
    "SET",
    "DELETE",
    "CREATE",
    "DROP",
    "TABLE",
    "PRIMARY",
    "KEY",
    "NOT",
    "NULL",
    "AND",
    "OR",
    "IS",
    "LIKE",
    "ORDER",
    "BY",
    "ASC",
    "DESC",
    "LIMIT",
    "OFFSET",
    "GROUP",
    "AS",
    "TRUE",
    "FALSE",
    "INTEGER",
    "INT",
    "TEXT",
    "REAL",
    "FLOAT",
    "BOOLEAN",
    "BOOL",
    "EXPLAIN",
    "IF",
    "EXISTS",
    "DISTINCT",
    "BETWEEN",
    "IN",
    "CHECKPOINT",
}


class TokenType(Enum):
    KEYWORD = auto()
    IDENT = auto()
    INTEGER = auto()
    FLOAT = auto()
    STRING = auto()
    OPERATOR = auto()
    PUNCT = auto()
    EOF = auto()


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    pos: int

    def is_keyword(self, *names: str) -> bool:
        return self.type is TokenType.KEYWORD and self.value in names

    def is_punct(self, *chars: str) -> bool:
        return self.type is TokenType.PUNCT and self.value in chars


_TWO_CHAR_OPS = {"<=", ">=", "<>", "!=", "||"}
_ONE_CHAR_OPS = set("=<>+-*/%")
_PUNCT = set("(),;.")


def tokenize(sql: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(sql)

    while i < n:
        ch = sql[i]

        if ch.isspace():
            i += 1
            continue

        # comentário de linha
        if sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
            continue

        start = i

        if ch.isalpha() or ch == "_":
            while i < n and (sql[i].isalnum() or sql[i] == "_"):
                i += 1
            word = sql[start:i]
            upper = word.upper()
            if upper in KEYWORDS:
                tokens.append(Token(TokenType.KEYWORD, upper, start))
            else:
                tokens.append(Token(TokenType.IDENT, word.lower(), start))
            continue

        # identificador entre aspas duplas: "minha coluna"
        if ch == '"':
            i += 1
            while i < n and sql[i] != '"':
                i += 1
            if i >= n:
                raise SQLSyntaxError("identificador entre aspas não foi fechado", start)
            tokens.append(Token(TokenType.IDENT, sql[start + 1 : i], start))
            i += 1
            continue

        if ch.isdigit() or (ch == "." and i + 1 < n and sql[i + 1].isdigit()):
            is_float = False
            while i < n and (sql[i].isdigit() or sql[i] == "."):
                if sql[i] == ".":
                    if is_float:
                        raise SQLSyntaxError("número mal formado", start)
                    is_float = True
                i += 1
            if i < n and sql[i] in "eE":
                is_float = True
                i += 1
                if i < n and sql[i] in "+-":
                    i += 1
                if i >= n or not sql[i].isdigit():
                    raise SQLSyntaxError("expoente mal formado", start)
                while i < n and sql[i].isdigit():
                    i += 1
            kind = TokenType.FLOAT if is_float else TokenType.INTEGER
            tokens.append(Token(kind, sql[start:i], start))
            continue

        if ch == "'":
            i += 1
            parts: list[str] = []
            while True:
                if i >= n:
                    raise SQLSyntaxError("string não foi fechada", start)
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":  # '' vira '
                        parts.append("'")
                        i += 2
                        continue
                    i += 1
                    break
                parts.append(sql[i])
                i += 1
            tokens.append(Token(TokenType.STRING, "".join(parts), start))
            continue

        two = sql[i : i + 2]
        if two in _TWO_CHAR_OPS:
            tokens.append(Token(TokenType.OPERATOR, two, start))
            i += 2
            continue
        if ch in _ONE_CHAR_OPS:
            tokens.append(Token(TokenType.OPERATOR, ch, start))
            i += 1
            continue
        if ch in _PUNCT:
            tokens.append(Token(TokenType.PUNCT, ch, start))
            i += 1
            continue

        raise SQLSyntaxError(f"caractere inesperado {ch!r}", start)

    tokens.append(Token(TokenType.EOF, "", n))
    return tokens
