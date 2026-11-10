# MiniDB

[![CI](https://github.com/AntonioHKZ/minidb/actions/workflows/ci.yml/badge.svg)](https://github.com/AntonioHKZ/minidb/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Um banco de dados relacional pequeno, escrito do zero em Python puro (sem dependências),
para entender o que acontece entre digitar um `SELECT` e receber as linhas de volta.

Tem um parser SQL próprio, armazenamento em **B+ tree**, um planejador que decide quando
usar o índice da chave primária e durabilidade com **write-ahead log** + snapshots.

```
minidb> CREATE TABLE alunos (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, curso TEXT, cr REAL);
tabela 'alunos' criada
minidb> INSERT INTO alunos VALUES (1, 'Ana', 'CC', 8.7), (2, 'Bruno', 'SI', 6.1), (3, 'Carla', 'CC', 9.2);
3 linha(s) inserida(s)
minidb> SELECT curso, COUNT(*) AS qtd, AVG(cr) AS media FROM alunos GROUP BY curso ORDER BY media DESC;
+-------+-----+-------+
| curso | qtd | media |
+-------+-----+-------+
| CC    | 2   | 8.95  |
| SI    | 1   | 6.1   |
+-------+-----+-------+
(2 linhas)
minidb> EXPLAIN SELECT * FROM alunos WHERE id BETWEEN 2 AND 10;
+--------------------------------------------------------------+
| plan                                                         |
+--------------------------------------------------------------+
| SEARCH alunos USING PRIMARY KEY RANGE (id >= 2 AND id <= 10) |
| FILTER id BETWEEN 2 AND 10                                   |
+--------------------------------------------------------------+
(2 linhas)
```

## Funcionalidades

| Área | O que tem |
|---|---|
| **DDL** | `CREATE TABLE [IF NOT EXISTS]`, `DROP TABLE [IF EXISTS]`, `PRIMARY KEY`, `NOT NULL` |
| **Tipos** | `INTEGER`, `REAL`, `TEXT`, `BOOLEAN` e `NULL`, com checagem de tipo na escrita |
| **DML** | `INSERT` (várias linhas, lista de colunas), `UPDATE`, `DELETE` |
| **Consultas** | `WHERE`, `ORDER BY` (várias chaves, `ASC`/`DESC`, posição), `LIMIT`/`OFFSET`, `DISTINCT`, aliases |
| **Agregação** | `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, `COUNT(DISTINCT ...)`, `GROUP BY` |
| **Expressões** | aritmética, `||`, `AND`/`OR`/`NOT` com lógica de três valores, `LIKE`, `IN`, `BETWEEN`, `IS NULL` |
| **Funções** | `UPPER`, `LOWER`, `LENGTH`, `ABS`, `ROUND`, `COALESCE` |
| **Índice** | B+ tree na chave primária: busca pontual, `IN` e intervalos em O(log n) |
| **Otimização** | `EXPLAIN` mostra o plano; contradições (`id = 1 AND id = 2`) viram plano vazio |
| **Durabilidade** | WAL com CRC32 + `fsync`, snapshot atômico, recuperação após queda |

## Como rodar

Requer Python 3.10+.

```bash
git clone https://github.com/AntonioHKZ/minidb.git
cd minidb
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Shell interativo:

```bash
python -m minidb               # banco só em memória
python -m minidb ./meu_banco   # persiste os dados na pasta ./meu_banco
python -m minidb ./escola < examples/escola.sql
```

Comandos do shell: `.tables`, `.schema [tabela]`, `.timer on`, `.help`, `.exit`.

Como biblioteca:

```python
from minidb import Database

with Database("dados") as db:
    db.execute(
        "CREATE TABLE IF NOT EXISTS tarefas (id INTEGER PRIMARY KEY, titulo TEXT, feita BOOLEAN)"
    )
    db.execute("INSERT INTO tarefas VALUES (1, 'estudar B+ tree', FALSE)")
    for id_, titulo in db.query("SELECT id, titulo FROM tarefas WHERE NOT feita"):
        print(id_, titulo)
```

## Arquitetura

```
           SQL
            │
     ┌──────▼──────┐   tokenizer.py   texto  → tokens
     │   sql/      │   parser.py      tokens → AST (descendente recursivo)
     └──────┬──────┘
            │ AST
     ┌──────▼──────┐   planner.py     escolhe SCAN / SEARCH pela PK / RANGE
     │  database   │   expressions.py avalia WHERE, SELECT, agregações
     │  (executor) │   catalog.py     esquemas e validação de tipos
     └──────┬──────┘
            │ registro lógico (insert/update/delete...)
     ┌──────▼──────┐   wal.py         append + fsync antes de aplicar
     │  storage/   │   btree.py       linhas indexadas pela chave primária
     └─────────────┘   snapshot.py    checkpoint atômico (tmp + os.replace)
```

Os detalhes de cada camada, incluindo o algoritmo de remoção da B+ tree e o protocolo de
recuperação, estão em [`docs/arquitetura.md`](docs/arquitetura.md).

## Testes

```bash
pytest                         # 94 testes
pytest --cov=minidb            # ~91% de cobertura
ruff check . && ruff format --check .
```

Alguns testes que valem destacar:

- **B+ tree vs `dict`** ([`tests/test_btree.py`](tests/test_btree.py)): milhares de inserções e
  remoções aleatórias com várias ordens de árvore, verificando as invariantes (ocupação mínima
  dos nós, folhas na mesma profundidade, chaves ordenadas) ao longo do caminho.
- **Índice vs varredura** ([`tests/test_planner.py`](tests/test_planner.py)): a mesma consulta
  executada com e sem o índice precisa devolver exatamente as mesmas linhas.
- **Queda do processo** ([`tests/test_persistence.py`](tests/test_persistence.py)): recuperação
  só pelo WAL, escrita parcial no fim do log, log corrompido no meio e queda entre o snapshot e
  a limpeza do WAL.

## Desempenho

`python benchmarks/bench_index.py 50000` (Python 3.15, Windows):

```
inserção de 50000 linhas: 0.79 s
busca por PK (índice):     0.043 ms/consulta
busca por PK (scan):     130.621 ms/consulta
range de 101 linhas:       0.434 ms/consulta
```

## Limitações conhecidas

É um projeto de estudo, então algumas coisas ficaram de fora de propósito:

- sem `JOIN`, subconsultas ou índices secundários (próximos passos, ver abaixo);
- sem transações com várias instruções — cada comando é atômico sozinho;
- a tabela inteira fica em memória; o disco é usado só para durabilidade;
- `GROUP BY` não valida se colunas fora da agregação estão no agrupamento (igual ao SQLite).

## Próximos passos

- [ ] `INNER JOIN` com nested loop e depois hash join
- [ ] `CREATE INDEX` em colunas que não são a chave primária
- [ ] `BEGIN` / `COMMIT` / `ROLLBACK`
- [ ] páginas de tamanho fixo em disco no lugar do snapshot JSON

## Referências

- *Database System Concepts* — Silberschatz, Korth, Sudarshan (cap. de indexação e recuperação)
- [SQLite — Architecture](https://www.sqlite.org/arch.html) e [Write-Ahead Logging](https://www.sqlite.org/wal.html)
- [CMU 15-445 — Database Systems](https://15445.courses.cs.cmu.edu/)
- [Crafting Interpreters](https://craftinginterpreters.com/) — Robert Nystrom (parsing)

## Licença

[MIT](LICENSE)
