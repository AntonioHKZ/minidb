"""Parser descendente recursivo.

Comandos são reconhecidos por funções ``_parse_<comando>``. Expressões usam
*precedence climbing*, da menor para a maior precedência:

    OR < AND < NOT < comparação/LIKE/IS/BETWEEN/IN < + - || < * / % < unário
"""

from __future__ import annotations

from minidb.errors import SQLSyntaxError
from minidb.sql import ast
from minidb.sql.tokenizer import Token, TokenType, tokenize

_TYPE_ALIASES = {
    "INTEGER": "INTEGER",
    "INT": "INTEGER",
    "REAL": "REAL",
    "FLOAT": "REAL",
    "TEXT": "TEXT",
    "BOOLEAN": "BOOLEAN",
    "BOOL": "BOOLEAN",
}

_COMPARISON_OPS = {"=", "<>", "!=", "<", "<=", ">", ">="}
AGGREGATES = {"COUNT", "SUM", "AVG", "MIN", "MAX"}


def parse(sql: str) -> list[ast.Statement]:
    """Converte uma string com um ou mais comandos separados por ``;``."""
    return Parser(tokenize(sql)).parse_script()


def parse_one(sql: str) -> ast.Statement:
    statements = parse(sql)
    if len(statements) != 1:
        raise SQLSyntaxError(f"esperado 1 comando, encontrados {len(statements)}")
    return statements[0]


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    # ------------------------------------------------------------ utilitários

    @property
    def current(self) -> Token:
        return self.tokens[self.pos]

    def _peek(self, offset: int = 1) -> Token:
        idx = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[idx]

    def _advance(self) -> Token:
        tok = self.current
        if tok.type is not TokenType.EOF:
            self.pos += 1
        return tok

    def _error(self, expected: str) -> SQLSyntaxError:
        tok = self.current
        found = "fim do comando" if tok.type is TokenType.EOF else repr(tok.value)
        return SQLSyntaxError(f"esperado {expected}, encontrado {found}", tok.pos)

    def _accept_keyword(self, *names: str) -> bool:
        if self.current.is_keyword(*names):
            self._advance()
            return True
        return False

    def _expect_keyword(self, name: str) -> None:
        if not self._accept_keyword(name):
            raise self._error(name)

    def _accept_punct(self, ch: str) -> bool:
        if self.current.is_punct(ch):
            self._advance()
            return True
        return False

    def _expect_punct(self, ch: str) -> None:
        if not self._accept_punct(ch):
            raise self._error(f"'{ch}'")

    def _expect_ident(self) -> str:
        tok = self.current
        if tok.type is not TokenType.IDENT:
            raise self._error("um identificador")
        self._advance()
        return tok.value

    def _expect_int(self) -> int:
        tok = self.current
        if tok.type is not TokenType.INTEGER:
            raise self._error("um número inteiro")
        self._advance()
        return int(tok.value)

    # --------------------------------------------------------------- comandos

    def parse_script(self) -> list[ast.Statement]:
        statements: list[ast.Statement] = []
        while self.current.type is not TokenType.EOF:
            if self._accept_punct(";"):
                continue
            statements.append(self.parse_statement())
            if self.current.type is not TokenType.EOF:
                self._expect_punct(";")
        return statements

    def parse_statement(self) -> ast.Statement:
        tok = self.current
        if tok.is_keyword("SELECT"):
            return self._parse_select()
        if tok.is_keyword("INSERT"):
            return self._parse_insert()
        if tok.is_keyword("UPDATE"):
            return self._parse_update()
        if tok.is_keyword("DELETE"):
            return self._parse_delete()
        if tok.is_keyword("CREATE"):
            return self._parse_create()
        if tok.is_keyword("DROP"):
            return self._parse_drop()
        if tok.is_keyword("EXPLAIN"):
            self._advance()
            if not self.current.is_keyword("SELECT"):
                raise self._error("SELECT após EXPLAIN")
            return ast.Explain(self._parse_select())
        if tok.is_keyword("CHECKPOINT"):
            self._advance()
            return ast.Checkpoint()
        raise self._error("um comando SQL")

    def _parse_create(self) -> ast.CreateTable:
        self._expect_keyword("CREATE")
        self._expect_keyword("TABLE")
        if_not_exists = False
        if self._accept_keyword("IF"):
            self._expect_keyword("NOT")
            self._expect_keyword("EXISTS")
            if_not_exists = True
        name = self._expect_ident()
        self._expect_punct("(")
        columns = [self._parse_column_def()]
        while self._accept_punct(","):
            columns.append(self._parse_column_def())
        self._expect_punct(")")
        return ast.CreateTable(name, columns, if_not_exists)

    def _parse_column_def(self) -> ast.ColumnDef:
        name = self._expect_ident()
        tok = self.current
        if tok.type is not TokenType.KEYWORD or tok.value not in _TYPE_ALIASES:
            raise self._error("um tipo (INTEGER, REAL, TEXT, BOOLEAN)")
        self._advance()
        col = ast.ColumnDef(name, _TYPE_ALIASES[tok.value])
        while True:
            if self._accept_keyword("PRIMARY"):
                self._expect_keyword("KEY")
                col.primary_key = True
                col.not_null = True
            elif self._accept_keyword("NOT"):
                self._expect_keyword("NULL")
                col.not_null = True
            else:
                break
        return col

    def _parse_drop(self) -> ast.DropTable:
        self._expect_keyword("DROP")
        self._expect_keyword("TABLE")
        if_exists = False
        if self._accept_keyword("IF"):
            self._expect_keyword("EXISTS")
            if_exists = True
        return ast.DropTable(self._expect_ident(), if_exists)

    def _parse_insert(self) -> ast.Insert:
        self._expect_keyword("INSERT")
        self._expect_keyword("INTO")
        table = self._expect_ident()
        columns = None
        if self._accept_punct("("):
            columns = [self._expect_ident()]
            while self._accept_punct(","):
                columns.append(self._expect_ident())
            self._expect_punct(")")
        self._expect_keyword("VALUES")
        rows = [self._parse_value_tuple()]
        while self._accept_punct(","):
            rows.append(self._parse_value_tuple())
        return ast.Insert(table, columns, rows)

    def _parse_value_tuple(self) -> list[ast.Expr]:
        self._expect_punct("(")
        values = [self.parse_expr()]
        while self._accept_punct(","):
            values.append(self.parse_expr())
        self._expect_punct(")")
        return values

    def _parse_update(self) -> ast.Update:
        self._expect_keyword("UPDATE")
        table = self._expect_ident()
        self._expect_keyword("SET")
        assignments = [self._parse_assignment()]
        while self._accept_punct(","):
            assignments.append(self._parse_assignment())
        where = self.parse_expr() if self._accept_keyword("WHERE") else None
        return ast.Update(table, assignments, where)

    def _parse_assignment(self) -> tuple[str, ast.Expr]:
        name = self._expect_ident()
        if not (self.current.type is TokenType.OPERATOR and self.current.value == "="):
            raise self._error("'='")
        self._advance()
        return name, self.parse_expr()

    def _parse_delete(self) -> ast.Delete:
        self._expect_keyword("DELETE")
        self._expect_keyword("FROM")
        table = self._expect_ident()
        where = self.parse_expr() if self._accept_keyword("WHERE") else None
        return ast.Delete(table, where)

    def _parse_select(self) -> ast.Select:
        self._expect_keyword("SELECT")
        distinct = self._accept_keyword("DISTINCT")
        items = [self._parse_select_item()]
        while self._accept_punct(","):
            items.append(self._parse_select_item())

        table = None
        if self._accept_keyword("FROM"):
            table = self._expect_ident()

        stmt = ast.Select(items, table, distinct=distinct)
        if self._accept_keyword("WHERE"):
            stmt.where = self.parse_expr()
        if self._accept_keyword("GROUP"):
            self._expect_keyword("BY")
            stmt.group_by.append(self.parse_expr())
            while self._accept_punct(","):
                stmt.group_by.append(self.parse_expr())
        if self._accept_keyword("ORDER"):
            self._expect_keyword("BY")
            stmt.order_by.append(self._parse_order_item())
            while self._accept_punct(","):
                stmt.order_by.append(self._parse_order_item())
        if self._accept_keyword("LIMIT"):
            stmt.limit = self._expect_int()
            if self._accept_keyword("OFFSET"):
                stmt.offset = self._expect_int()
        return stmt

    def _parse_select_item(self) -> ast.SelectItem:
        tok = self.current
        if tok.type is TokenType.OPERATOR and tok.value == "*":
            self._advance()
            return ast.SelectItem(ast.Star())
        expr = self.parse_expr()
        alias = None
        if self._accept_keyword("AS"):
            alias = self._expect_ident()
        elif self.current.type is TokenType.IDENT:
            alias = self._advance().value
        return ast.SelectItem(expr, alias)

    def _parse_order_item(self) -> ast.OrderItem:
        expr = self.parse_expr()
        descending = False
        if self._accept_keyword("DESC"):
            descending = True
        else:
            self._accept_keyword("ASC")
        return ast.OrderItem(expr, descending)

    # ------------------------------------------------------------- expressões

    def parse_expr(self) -> ast.Expr:
        return self._parse_or()

    def _parse_or(self) -> ast.Expr:
        left = self._parse_and()
        while self._accept_keyword("OR"):
            left = ast.BinaryOp("OR", left, self._parse_and())
        return left

    def _parse_and(self) -> ast.Expr:
        left = self._parse_not()
        while self._accept_keyword("AND"):
            left = ast.BinaryOp("AND", left, self._parse_not())
        return left

    def _parse_not(self) -> ast.Expr:
        if self._accept_keyword("NOT"):
            return ast.UnaryOp("NOT", self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> ast.Expr:
        left = self._parse_additive()
        tok = self.current

        if tok.type is TokenType.OPERATOR and tok.value in _COMPARISON_OPS:
            self._advance()
            op = "<>" if tok.value == "!=" else tok.value
            return ast.BinaryOp(op, left, self._parse_additive())

        if tok.is_keyword("IS"):
            self._advance()
            negated = self._accept_keyword("NOT")
            self._expect_keyword("NULL")
            return ast.IsNull(left, negated)

        negated = False
        if tok.is_keyword("NOT") and self._peek().is_keyword("LIKE", "BETWEEN", "IN"):
            self._advance()
            negated = True

        if self._accept_keyword("LIKE"):
            expr: ast.Expr = ast.BinaryOp("LIKE", left, self._parse_additive())
            return ast.UnaryOp("NOT", expr) if negated else expr

        if self._accept_keyword("BETWEEN"):
            low = self._parse_additive()
            self._expect_keyword("AND")
            high = self._parse_additive()
            return ast.Between(left, low, high, negated)

        if self._accept_keyword("IN"):
            self._expect_punct("(")
            items = [self.parse_expr()]
            while self._accept_punct(","):
                items.append(self.parse_expr())
            self._expect_punct(")")
            return ast.InList(left, tuple(items), negated)

        if negated:
            raise self._error("LIKE, BETWEEN ou IN")
        return left

    def _parse_additive(self) -> ast.Expr:
        left = self._parse_multiplicative()
        while self.current.type is TokenType.OPERATOR and self.current.value in ("+", "-", "||"):
            op = self._advance().value
            left = ast.BinaryOp(op, left, self._parse_multiplicative())
        return left

    def _parse_multiplicative(self) -> ast.Expr:
        left = self._parse_unary()
        while self.current.type is TokenType.OPERATOR and self.current.value in ("*", "/", "%"):
            op = self._advance().value
            left = ast.BinaryOp(op, left, self._parse_unary())
        return left

    def _parse_unary(self) -> ast.Expr:
        tok = self.current
        if tok.type is TokenType.OPERATOR and tok.value in ("-", "+"):
            self._advance()
            operand = self._parse_unary()
            if tok.value == "+":
                return operand
            if (
                isinstance(operand, ast.Literal)
                and isinstance(operand.value, (int, float))
                and not isinstance(operand.value, bool)
            ):
                return ast.Literal(-operand.value)
            return ast.UnaryOp("-", operand)
        return self._parse_primary()

    def _parse_primary(self) -> ast.Expr:
        tok = self.current

        if tok.type is TokenType.INTEGER:
            self._advance()
            return ast.Literal(int(tok.value))
        if tok.type is TokenType.FLOAT:
            self._advance()
            return ast.Literal(float(tok.value))
        if tok.type is TokenType.STRING:
            self._advance()
            return ast.Literal(tok.value)
        if tok.is_keyword("NULL"):
            self._advance()
            return ast.Literal(None)
        if tok.is_keyword("TRUE", "FALSE"):
            self._advance()
            return ast.Literal(tok.value == "TRUE")

        if self._accept_punct("("):
            expr = self.parse_expr()
            self._expect_punct(")")
            return expr

        if tok.type is TokenType.IDENT:
            self._advance()
            if self.current.is_punct("("):
                return self._parse_call(tok.value.upper())
            if self._accept_punct("."):
                return ast.Column(self._expect_ident(), table=tok.value)
            return ast.Column(tok.value)

        raise self._error("uma expressão")

    def _parse_call(self, name: str) -> ast.FuncCall:
        self._expect_punct("(")
        if (
            name == "COUNT"
            and self.current.type is TokenType.OPERATOR
            and self.current.value == "*"
        ):
            self._advance()
            self._expect_punct(")")
            return ast.FuncCall("COUNT", (ast.Star(),))
        distinct = self._accept_keyword("DISTINCT")
        args: list[ast.Expr] = []
        if not self.current.is_punct(")"):
            args.append(self.parse_expr())
            while self._accept_punct(","):
                args.append(self.parse_expr())
        self._expect_punct(")")
        return ast.FuncCall(name, tuple(args), distinct)
