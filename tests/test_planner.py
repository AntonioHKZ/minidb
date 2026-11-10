import pytest

from minidb import Database


@pytest.fixture
def big():
    db = Database(btree_order=8)
    db.execute("CREATE TABLE n (id INTEGER PRIMARY KEY, par BOOLEAN)")
    values = ", ".join(f"({i}, {'TRUE' if i % 2 == 0 else 'FALSE'})" for i in range(1, 1001))
    db.execute(f"INSERT INTO n VALUES {values}")
    return db


def plan(db, sql):
    return [r[0] for r in db.execute("EXPLAIN " + sql)]


def test_point_lookup(big):
    assert plan(big, "SELECT * FROM n WHERE id = 500")[0].startswith("SEARCH n USING PRIMARY KEY")
    assert plan(big, "SELECT * FROM n WHERE 500 = id")[0].startswith("SEARCH n USING PRIMARY KEY")


def test_range_scan(big):
    p = plan(big, "SELECT * FROM n WHERE id > 10 AND id <= 20 AND par")
    assert p[0] == "SEARCH n USING PRIMARY KEY RANGE (id > 10 AND id <= 20)"
    assert big.query("SELECT id FROM n WHERE id > 10 AND id <= 20 AND par") == [
        (12,),
        (14,),
        (16,),
        (18,),
        (20,),
    ]


def test_between_and_in(big):
    assert "RANGE" in plan(big, "SELECT * FROM n WHERE id BETWEEN 5 AND 9")[0]
    assert "IN 3 valores" in plan(big, "SELECT * FROM n WHERE id IN (3, 1, 2)")[0]
    assert big.query("SELECT id FROM n WHERE id IN (3, 1, 2000)") == [(1,), (3,)]


def test_contradiction_is_empty(big):
    assert plan(big, "SELECT * FROM n WHERE id = 1 AND id = 2")[0].startswith("EMPTY")
    assert plan(big, "SELECT * FROM n WHERE id > 10 AND id < 5")[0].startswith("EMPTY")
    assert big.query("SELECT * FROM n WHERE id > 10 AND id < 5") == []


def test_falls_back_to_full_scan(big):
    assert plan(big, "SELECT * FROM n WHERE par")[0] == "SCAN n"
    # OR não é otimizado: o plano precisa ser conservador
    assert plan(big, "SELECT * FROM n WHERE id = 1 OR id = 2")[0] == "SCAN n"
    assert big.execute("SELECT COUNT(*) FROM n WHERE id = 1 OR id = 2").scalar() == 2


def test_explain_shows_pipeline(big):
    p = plan(big, "SELECT par, COUNT(*) FROM n GROUP BY par ORDER BY par DESC LIMIT 1")
    assert p == ["SCAN n", "GROUP BY par", "SORT par DESC", "LIMIT 1"]


@pytest.mark.parametrize(
    "where",
    [
        "id = 7",
        "id >= 990",
        "id < 4",
        "id BETWEEN 100 AND 103",
        "id IN (1, 999, 5000)",
        "id > 500 AND id < 503 AND NOT par",
        "id = 3.0",
    ],
)
def test_index_results_match_full_scan(big, where):
    """O plano com índice deve devolver exatamente o mesmo que uma varredura."""
    with_index = big.query(f"SELECT id FROM n WHERE {where}")
    # "TRUE AND (...)" esconde a estrutura do WHERE do planejador
    full_scan = big.query(f"SELECT id FROM n WHERE TRUE AND ({where}) OR FALSE")
    assert with_index == full_scan
