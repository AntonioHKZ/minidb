import os

import pytest

from minidb import Database, StorageError
from minidb.database import SNAPSHOT_FILE, WAL_FILE


def populate(db):
    db.execute("CREATE TABLE contas (id INTEGER PRIMARY KEY, dono TEXT, saldo REAL)")
    db.execute("INSERT INTO contas VALUES (1, 'ana', 100), (2, 'beto', 50.5)")
    db.execute("UPDATE contas SET saldo = saldo - 30 WHERE id = 1")
    db.execute("DELETE FROM contas WHERE id = 2")
    db.execute("INSERT INTO contas VALUES (3, 'caio', 0)")


EXPECTED = [(1, "ana", 70.0), (3, "caio", 0.0)]


def test_reopen_after_clean_close(tmp_path):
    with Database(str(tmp_path)) as db:
        populate(db)
    with Database(str(tmp_path)) as db:
        assert db.query("SELECT * FROM contas") == EXPECTED


def test_recovery_from_wal_only(tmp_path):
    """Simula uma queda: o processo nunca chamou close()."""
    db = Database(str(tmp_path), sync=False)
    populate(db)
    db._wal.close()  # libera o arquivo sem checkpoint
    assert not os.path.exists(tmp_path / SNAPSHOT_FILE)

    recovered = Database(str(tmp_path))
    assert recovered.query("SELECT * FROM contas") == EXPECTED
    recovered.close()


def test_torn_write_at_end_of_wal_is_discarded(tmp_path):
    db = Database(str(tmp_path), sync=False)
    populate(db)
    db._wal.close()
    with open(tmp_path / WAL_FILE, "ab") as f:
        f.write(b'1234abcd {"op":"insert","tab')  # escrita interrompida

    recovered = Database(str(tmp_path))
    assert recovered.query("SELECT * FROM contas") == EXPECTED
    recovered.execute("INSERT INTO contas VALUES (4, 'davi', 1)")
    recovered._wal.close()

    again = Database(str(tmp_path))
    assert len(again.query("SELECT * FROM contas")) == 3
    again.close()


def test_corruption_in_middle_of_wal_is_an_error(tmp_path):
    db = Database(str(tmp_path), sync=False)
    populate(db)
    db._wal.close()
    path = tmp_path / WAL_FILE
    lines = path.read_bytes().splitlines(keepends=True)
    lines[1] = lines[1].replace(b"ana", b"ANA")  # CRC não bate mais
    path.write_bytes(b"".join(lines))

    with pytest.raises(StorageError, match="corrompido"):
        Database(str(tmp_path))


def test_crash_between_snapshot_and_wal_truncate(tmp_path):
    """Se o snapshot foi gravado mas o WAL não foi limpo, nada é aplicado duas vezes."""
    db = Database(str(tmp_path), sync=False)
    populate(db)
    wal_content = (tmp_path / WAL_FILE).read_bytes()
    db.checkpoint()
    db._wal.close()
    (tmp_path / WAL_FILE).write_bytes(wal_content)  # "desfaz" o truncate

    recovered = Database(str(tmp_path))
    assert recovered.query("SELECT * FROM contas") == EXPECTED
    recovered.close()


def test_automatic_checkpoint(tmp_path):
    with Database(str(tmp_path), checkpoint_every=10, sync=False) as db:
        db.execute("CREATE TABLE t (x INTEGER)")
        for i in range(25):
            db.execute(f"INSERT INTO t VALUES ({i})")
        assert db._wal.records_since_checkpoint < 10
        assert os.path.exists(tmp_path / SNAPSHOT_FILE)
    with Database(str(tmp_path)) as db:
        assert db.execute("SELECT SUM(x) FROM t").scalar() == sum(range(25))


def test_rowid_survives_restart(tmp_path):
    with Database(str(tmp_path)) as db:
        db.execute("CREATE TABLE t (x TEXT)")
        db.execute("INSERT INTO t VALUES ('a'), ('b')")
        db.execute("DELETE FROM t WHERE x = 'b'")
    with Database(str(tmp_path)) as db:
        db.execute("INSERT INTO t VALUES ('c')")
        assert db.query("SELECT x FROM t") == [("a",), ("c",)]


def test_drop_table_is_persisted(tmp_path):
    with Database(str(tmp_path)) as db:
        populate(db)
        db.execute("DROP TABLE contas")
    with Database(str(tmp_path)) as db:
        assert db.tables() == []
