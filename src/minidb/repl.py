"""Shell interativo: ``python -m minidb [pasta]``."""

from __future__ import annotations

import sys
import time
from typing import Any, TextIO

from minidb.database import Database, Result
from minidb.errors import MiniDBError

HELP = """\
Comandos especiais:
  .tables            lista as tabelas
  .schema [tabela]   mostra o CREATE TABLE
  .timer on|off      mostra o tempo de cada comando
  .help              esta mensagem
  .exit              sai (também Ctrl+D / Ctrl+Z)

Comandos SQL terminam com ';' e podem ocupar várias linhas.
"""


def format_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return f"{value:.1f}" if value.is_integer() else f"{value:.6g}"
    return str(value)


def format_table(result: Result) -> str:
    if not result.columns:
        return result.message
    cells = [[format_value(v) for v in row] for row in result.rows]
    widths = [len(c) for c in result.columns]
    for row in cells:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(values: list[str]) -> str:
        return "| " + " | ".join(v.ljust(w) for v, w in zip(values, widths)) + " |"

    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    out = [sep, line(result.columns), sep, *(line(r) for r in cells), sep]
    out.append(f"({len(cells)} linha{'s' if len(cells) != 1 else ''})")
    return "\n".join(out)


class Shell:
    def __init__(self, db: Database, out: TextIO = sys.stdout) -> None:
        self.db = db
        self.out = out
        self.timer = False

    def print(self, text: str = "") -> None:
        print(text, file=self.out)

    def meta(self, line: str) -> bool:
        """Executa um comando que começa com '.'. Retorna False para sair."""
        parts = line.split()
        cmd, args = parts[0], parts[1:]
        if cmd in (".exit", ".quit"):
            return False
        if cmd == ".help":
            self.print(HELP)
        elif cmd == ".tables":
            self.print("\n".join(self.db.tables()) or "(nenhuma tabela)")
        elif cmd == ".schema":
            names = args or self.db.tables()
            for name in names:
                try:
                    self.print(self.db.catalog.get(name).describe())
                except MiniDBError as exc:
                    self.print(f"erro: {exc}")
        elif cmd == ".timer":
            self.timer = bool(args) and args[0].lower() == "on"
        else:
            self.print(f"comando desconhecido: {cmd} (use .help)")
        return True

    def run_sql(self, sql: str) -> None:
        start = time.perf_counter()
        try:
            result = self.db.execute(sql)
        except MiniDBError as exc:
            self.print(f"erro: {exc}")
            return
        self.print(format_table(result))
        if self.timer:
            self.print(f"tempo: {(time.perf_counter() - start) * 1000:.2f} ms")

    def loop(self, stdin: TextIO = sys.stdin) -> None:
        interactive = stdin.isatty()
        buffer: list[str] = []
        while True:
            if interactive:
                prompt = "minidb> " if not buffer else "   ...> "
                try:
                    line = input(prompt)
                except EOFError:
                    self.print()
                    break
                except KeyboardInterrupt:
                    self.print()
                    buffer.clear()
                    continue
            else:
                line = stdin.readline()
                if not line:
                    break
                line = line.rstrip("\n")

            stripped = line.strip()
            if not buffer and stripped.startswith("."):
                if not self.meta(stripped):
                    break
                continue
            if not stripped and not buffer:
                continue
            buffer.append(line)
            if stripped.endswith(";"):
                self.run_sql("\n".join(buffer))
                buffer.clear()

        if buffer:
            self.run_sql("\n".join(buffer))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print("uso: python -m minidb [pasta-do-banco]")
        return 0
    path = argv[0] if argv else None

    with Database(path) as db:
        shell = Shell(db)
        if sys.stdin.isatty():
            where = f"'{path}'" if path else "memória (os dados serão perdidos ao sair)"
            print(f"MiniDB — banco em {where}. Digite .help para ajuda.")
        shell.loop()
    return 0
