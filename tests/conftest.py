import pytest

from minidb import Database


@pytest.fixture
def db():
    database = Database()
    database.execute(
        """
        CREATE TABLE alunos (
            id INTEGER PRIMARY KEY,
            nome TEXT NOT NULL,
            curso TEXT,
            semestre INTEGER,
            cr REAL,
            ativo BOOLEAN
        );
        INSERT INTO alunos VALUES
            (1, 'Ana',     'CC', 4, 8.7, TRUE),
            (2, 'Bruno',   'SI', 2, 6.1, TRUE),
            (3, 'Carla',   'CC', 6, 9.2, FALSE),
            (4, 'Diego',   'EC', 4, NULL, TRUE),
            (5, 'Eduarda', 'CC', 2, 7.5, TRUE),
            (6, 'Felipe',  NULL, 1, 5.0, FALSE);
        """
    )
    return database
