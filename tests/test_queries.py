import pytest

from minidb import CatalogError, ConstraintError, Database, ExecutionError


def names(result):
    return [r[0] for r in result]


def test_select_star_keeps_column_order(db):
    result = db.execute("SELECT * FROM alunos WHERE id = 1")
    assert result.columns == ["id", "nome", "curso", "semestre", "cr", "ativo"]
    assert result.rows == [(1, "Ana", "CC", 4, 8.7, True)]


def test_where_and_order(db):
    result = db.execute("SELECT nome FROM alunos WHERE curso = 'CC' ORDER BY cr DESC")
    assert names(result) == ["Carla", "Ana", "Eduarda"]


def test_null_semantics(db):
    # comparação com NULL nunca é verdadeira
    assert db.query("SELECT COUNT(*) FROM alunos WHERE curso = NULL") == [(0,)]
    assert names(db.execute("SELECT nome FROM alunos WHERE curso IS NULL")) == ["Felipe"]
    assert db.execute("SELECT COUNT(*) FROM alunos WHERE curso IS NOT NULL").scalar() == 5
    # NULL OR TRUE = TRUE, NULL AND FALSE = FALSE
    assert db.execute("SELECT NULL OR TRUE").scalar() is True
    assert db.execute("SELECT NULL AND FALSE").scalar() is False
    assert db.execute("SELECT NULL AND TRUE").scalar() is None


def test_order_by_nulls_first(db):
    result = db.execute("SELECT nome FROM alunos ORDER BY cr")
    assert names(result)[0] == "Diego"


def test_order_by_multiple_keys_and_position(db):
    result = db.execute("SELECT curso, nome FROM alunos WHERE curso IS NOT NULL ORDER BY 1, 2 DESC")
    assert result.rows == [
        ("CC", "Eduarda"),
        ("CC", "Carla"),
        ("CC", "Ana"),
        ("EC", "Diego"),
        ("SI", "Bruno"),
    ]


def test_limit_offset(db):
    assert names(db.execute("SELECT id FROM alunos ORDER BY id LIMIT 2 OFFSET 3")) == [4, 5]


def test_expressions_and_aliases(db):
    result = db.execute(
        "SELECT UPPER(nome) AS n, semestre * 10 + 1 AS x, nome || '!' FROM alunos WHERE id = 2"
    )
    assert result.columns == ["n", "x", "nome || '!'"]
    assert result.rows == [("BRUNO", 21, "Bruno!")]


def test_integer_division_truncates_toward_zero():
    db = Database()
    assert db.query("SELECT 7 / 2, -7 / 2, 7 % 3, -7 % 3, 7.0 / 2") == [(3, -3, 1, -1, 3.5)]


def test_like_in_between(db):
    assert names(db.execute("SELECT nome FROM alunos WHERE nome LIKE '%a' ORDER BY id")) == [
        "Ana",
        "Carla",
        "Eduarda",
    ]
    assert names(db.execute("SELECT nome FROM alunos WHERE nome LIKE '_r%'")) == ["Bruno"]
    assert names(db.execute("SELECT id FROM alunos WHERE semestre IN (1, 6) ORDER BY id")) == [3, 6]
    assert names(db.execute("SELECT id FROM alunos WHERE cr BETWEEN 7 AND 9 ORDER BY id")) == [1, 5]
    assert names(db.execute("SELECT id FROM alunos WHERE id NOT IN (1,2,3,4) ORDER BY id")) == [
        5,
        6,
    ]


def test_aggregates(db):
    row = db.query("SELECT COUNT(*), COUNT(cr), SUM(semestre), MIN(nome), MAX(cr) FROM alunos")[0]
    assert row == (6, 5, 19, "Ana", 9.2)
    avg = db.execute("SELECT AVG(cr) FROM alunos").scalar()
    assert avg == pytest.approx((8.7 + 6.1 + 9.2 + 7.5 + 5.0) / 5)


def test_aggregate_on_empty_table():
    db = Database()
    db.execute("CREATE TABLE t (x INTEGER)")
    assert db.query("SELECT COUNT(*), SUM(x), MAX(x) FROM t") == [(0, None, None)]


def test_group_by(db):
    result = db.execute(
        "SELECT curso, COUNT(*) AS qtd, AVG(semestre) FROM alunos "
        "WHERE curso IS NOT NULL GROUP BY curso ORDER BY qtd DESC, curso"
    )
    assert result.rows == [("CC", 3, 4.0), ("EC", 1, 4.0), ("SI", 1, 2.0)]


def test_count_distinct_and_select_distinct(db):
    assert db.execute("SELECT COUNT(DISTINCT semestre) FROM alunos").scalar() == 4
    assert db.query("SELECT DISTINCT semestre FROM alunos ORDER BY semestre") == [
        (1,),
        (2,),
        (4,),
        (6,),
    ]


def test_insert_with_column_list_fills_null():
    db = Database()
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, a TEXT, b REAL)")
    db.execute("INSERT INTO t (b, id) VALUES (2, 10)")
    assert db.query("SELECT * FROM t") == [(10, None, 2.0)]


def test_constraints(db):
    with pytest.raises(ConstraintError, match="duplicada"):
        db.execute("INSERT INTO alunos (id, nome) VALUES (1, 'Repetido')")
    with pytest.raises(ConstraintError, match="NULL"):
        db.execute("INSERT INTO alunos (id) VALUES (99)")
    with pytest.raises(ConstraintError, match="INTEGER"):
        db.execute("INSERT INTO alunos (id, nome, semestre) VALUES (99, 'X', 'quarto')")


def test_failed_multi_row_insert_is_atomic(db):
    with pytest.raises(ConstraintError):
        db.execute("INSERT INTO alunos (id, nome) VALUES (50, 'Ok'), (51, NULL)")
    assert db.query("SELECT COUNT(*) FROM alunos WHERE id >= 50") == [(0,)]


def test_update(db):
    result = db.execute(
        "UPDATE alunos SET semestre = semestre + 1, ativo = FALSE WHERE curso = 'CC'"
    )
    assert result.rowcount == 3
    assert db.query("SELECT semestre FROM alunos WHERE curso = 'CC' ORDER BY id") == [
        (5,),
        (7,),
        (3,),
    ]
    assert db.execute("SELECT COUNT(*) FROM alunos WHERE ativo").scalar() == 2


def test_update_primary_key_shift():
    db = Database()
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    db.execute("INSERT INTO t VALUES (1, 'a'), (2, 'b'), (3, 'c')")
    db.execute("UPDATE t SET id = id + 1")
    assert db.query("SELECT * FROM t") == [(2, "a"), (3, "b"), (4, "c")]
    with pytest.raises(ConstraintError):
        db.execute("UPDATE t SET id = 4 WHERE id = 2")


def test_delete(db):
    assert db.execute("DELETE FROM alunos WHERE ativo = FALSE").rowcount == 2
    assert db.execute("SELECT COUNT(*) FROM alunos").scalar() == 4
    db.execute("DELETE FROM alunos")
    assert db.query("SELECT * FROM alunos") == []


def test_table_without_primary_key_uses_rowid():
    db = Database()
    db.execute("CREATE TABLE log (msg TEXT)")
    db.execute("INSERT INTO log VALUES ('a'), ('a'), ('b')")
    assert db.query("SELECT msg FROM log") == [("a",), ("a",), ("b",)]
    db.execute("DELETE FROM log WHERE msg = 'a'")
    db.execute("INSERT INTO log VALUES ('c')")
    assert db.query("SELECT msg FROM log") == [("b",), ("c",)]


def test_catalog_errors(db):
    with pytest.raises(CatalogError):
        db.execute("SELECT * FROM nao_existe")
    with pytest.raises(CatalogError):
        db.execute("SELECT coluna_x FROM alunos")
    with pytest.raises(CatalogError):
        db.execute("CREATE TABLE alunos (x INTEGER)")
    db.execute("CREATE TABLE IF NOT EXISTS alunos (x INTEGER)")
    db.execute("DROP TABLE alunos")
    db.execute("DROP TABLE IF EXISTS alunos")
    assert db.tables() == []


def test_runtime_errors(db):
    with pytest.raises(ExecutionError, match="zero"):
        db.execute("SELECT 1 / 0")
    with pytest.raises(ExecutionError, match="comparar"):
        db.execute("SELECT * FROM alunos WHERE nome > 3")
    with pytest.raises(ExecutionError):
        db.execute("SELECT * FROM alunos WHERE COUNT(*) > 1")


def test_scalar_functions():
    db = Database()
    assert db.query(
        "SELECT LENGTH('abc'), ABS(-2), ROUND(2.567, 2), COALESCE(NULL, NULL, 'x')"
    ) == [
        (3, 2, 2.57, "x"),
    ]
    assert db.execute("SELECT LOWER(NULL)").scalar() is None
