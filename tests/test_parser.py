import pytest

from minidb.errors import SQLSyntaxError
from minidb.sql import ast, parse, parse_one
from minidb.sql.tokenizer import TokenType, tokenize


def test_tokenizer_basics():
    tokens = tokenize("SELECT nome, 3.5 FROM t WHERE x <> 'it''s' -- comentário")
    kinds = [t.type for t in tokens]
    assert kinds == [
        TokenType.KEYWORD,
        TokenType.IDENT,
        TokenType.PUNCT,
        TokenType.FLOAT,
        TokenType.KEYWORD,
        TokenType.IDENT,
        TokenType.KEYWORD,
        TokenType.IDENT,
        TokenType.OPERATOR,
        TokenType.STRING,
        TokenType.EOF,
    ]
    assert tokens[9].value == "it's"


def test_identifiers_are_case_insensitive():
    stmt = parse_one("select NoMe from Usuarios")
    assert stmt.table == "usuarios"
    assert stmt.items[0].expr == ast.Column("nome")


def test_operator_precedence():
    stmt = parse_one("SELECT 1 + 2 * 3")
    expr = stmt.items[0].expr
    assert expr == ast.BinaryOp(
        "+", ast.Literal(1), ast.BinaryOp("*", ast.Literal(2), ast.Literal(3))
    )


def test_and_binds_tighter_than_or():
    expr = parse_one("SELECT * FROM t WHERE a = 1 OR b = 2 AND c = 3").where
    assert isinstance(expr, ast.BinaryOp) and expr.op == "OR"
    assert isinstance(expr.right, ast.BinaryOp) and expr.right.op == "AND"


def test_create_table():
    stmt = parse_one(
        "CREATE TABLE IF NOT EXISTS p (id INT PRIMARY KEY, nome TEXT NOT NULL, preco FLOAT)"
    )
    assert isinstance(stmt, ast.CreateTable)
    assert stmt.if_not_exists
    assert [(c.name, c.type, c.primary_key, c.not_null) for c in stmt.columns] == [
        ("id", "INTEGER", True, True),
        ("nome", "TEXT", False, True),
        ("preco", "REAL", False, False),
    ]


def test_select_full():
    stmt = parse_one(
        "SELECT DISTINCT cidade, COUNT(*) AS total FROM clientes "
        "WHERE idade BETWEEN 18 AND 30 AND nome NOT LIKE 'A%' "
        "GROUP BY cidade ORDER BY total DESC, cidade LIMIT 5 OFFSET 2"
    )
    assert stmt.distinct
    assert stmt.items[1].alias == "total"
    assert stmt.items[1].expr == ast.FuncCall("COUNT", (ast.Star(),))
    assert stmt.group_by == [ast.Column("cidade")]
    assert [o.descending for o in stmt.order_by] == [True, False]
    assert (stmt.limit, stmt.offset) == (5, 2)


def test_multiple_statements():
    stmts = parse("CREATE TABLE a (x INT); INSERT INTO a VALUES (1), (2);; SELECT * FROM a;")
    assert [type(s).__name__ for s in stmts] == ["CreateTable", "Insert", "Select"]
    assert len(stmts[1].rows) == 2


def test_negative_literal_is_folded():
    assert parse_one("SELECT -5").items[0].expr == ast.Literal(-5)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT",
        "SELECT * FROM",
        "INSERT INTO t VALUES (1",
        "CREATE TABLE t (x BLOB)",
        "SELECT 'abc",
        "UPDATE t SET x 1",
        "SELECT * FROM t WHERE a NOT 5",
        "SELECT 1 SELECT 2",
        "SELECT #",
    ],
)
def test_syntax_errors(sql):
    with pytest.raises(SQLSyntaxError):
        parse(sql)


def test_error_message_has_position():
    with pytest.raises(SQLSyntaxError, match="posição 14"):
        parse("SELECT * FROM 123")
