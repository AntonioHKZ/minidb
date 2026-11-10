"""Compara busca pela chave primária (B+ tree) com varredura completa.

Uso: python benchmarks/bench_index.py [quantidade_de_linhas]
"""

import random
import sys
import time

from minidb import Database


def timed(fn, repeat):
    start = time.perf_counter()
    for _ in range(repeat):
        fn()
    return (time.perf_counter() - start) / repeat * 1000


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50_000
    db = Database()
    db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, valor INTEGER)")

    batch = 1000
    start = time.perf_counter()
    for base in range(0, n, batch):
        values = ", ".join(f"({i}, {i * 7 % 1000})" for i in range(base, min(base + batch, n)))
        db.execute(f"INSERT INTO t VALUES {values}")
    print(f"inserção de {n} linhas: {time.perf_counter() - start:.2f} s")

    keys = [random.randrange(n) for _ in range(200)]
    it = iter(keys * 10)

    lookup = timed(lambda: db.query(f"SELECT * FROM t WHERE id = {next(it)}"), 200)
    # "id + 0" impede que o planejador use o índice
    it = iter(keys * 10)
    scan = timed(lambda: db.query(f"SELECT * FROM t WHERE id + 0 = {next(it)}"), 5)
    print(f"busca por PK (índice):  {lookup:8.3f} ms/consulta")
    print(f"busca por PK (scan):    {scan:8.3f} ms/consulta")
    print(f"ganho: {scan / lookup:.0f}x")

    rng = timed(lambda: db.query("SELECT COUNT(*) FROM t WHERE id BETWEEN 1000 AND 1100"), 50)
    print(f"range de 101 linhas:    {rng:8.3f} ms/consulta")


if __name__ == "__main__":
    main()
