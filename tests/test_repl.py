import io

from minidb import Database
from minidb.repl import Shell


def run(script: str) -> str:
    out = io.StringIO()
    Shell(Database(), out).loop(io.StringIO(script))
    return out.getvalue()


def test_shell_executes_multiline_sql_and_prints_table():
    output = run(
        "CREATE TABLE t (id INTEGER PRIMARY KEY, nome TEXT);\n"
        "INSERT INTO t VALUES\n"
        "  (1, 'um'), (2, NULL);\n"
        "SELECT * FROM t;\n"
    )
    assert "tabela 't' criada" in output
    assert "| id | nome |" in output
    assert "| 2  | NULL |" in output
    assert "(2 linhas)" in output


def test_shell_meta_commands_and_errors():
    output = run(
        "CREATE TABLE a (x INTEGER PRIMARY KEY);\n"
        ".tables\n"
        ".schema a\n"
        "SELECT * FROM b;\n"
        ".nada\n"
        ".exit\n"
        "SELECT 1;\n"
    )
    assert "\na\n" in output
    assert "CREATE TABLE a (x INTEGER PRIMARY KEY);" in output
    assert "erro: tabela 'b' não existe" in output
    assert "comando desconhecido" in output
    assert "| 1 |" not in output  # nada executa depois do .exit
