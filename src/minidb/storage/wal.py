"""Write-Ahead Log (WAL).

Cada alteração é registrada no log *antes* de ser aplicada na memória.
Se o processo morrer, o estado é reconstruído carregando o último snapshot
e reaplicando os registros do log com LSN maior que o do snapshot.

Formato de cada linha::

    <crc32 em hex, 8 dígitos> <json do registro>\\n

O CRC permite detectar uma escrita parcial (o processo caiu no meio de um
``write``). Uma linha corrompida no *final* do arquivo é descartada; uma
linha corrompida no meio indica dano real e gera ``StorageError``.
"""

from __future__ import annotations

import json
import os
import zlib
from collections.abc import Iterator
from typing import Any

from minidb.errors import StorageError


def _encode(record: dict[str, Any]) -> bytes:
    payload = json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    crc = zlib.crc32(payload)
    return f"{crc:08x} ".encode("ascii") + payload + b"\n"


def _decode(line: bytes) -> dict[str, Any] | None:
    """Devolve o registro ou ``None`` se a linha estiver corrompida."""
    if not line.endswith(b"\n") or len(line) < 10 or line[8:9] != b" ":
        return None
    payload = line[9:-1]
    try:
        expected = int(line[:8], 16)
    except ValueError:
        return None
    if zlib.crc32(payload) != expected:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


class WriteAheadLog:
    def __init__(self, path: str, sync: bool = True) -> None:
        self.path = path
        self.sync = sync
        self._file = open(path, "ab")
        self.records_since_checkpoint = 0

    def append(self, record: dict[str, Any]) -> None:
        self._file.write(_encode(record))
        self._file.flush()
        if self.sync:
            os.fsync(self._file.fileno())
        self.records_since_checkpoint += 1

    def replay(self) -> Iterator[dict[str, Any]]:
        """Lê todos os registros válidos e remove uma cauda corrompida."""
        with open(self.path, "rb") as f:
            lines = f.readlines()

        valid_bytes = 0
        for idx, line in enumerate(lines):
            record = _decode(line)
            if record is None:
                if idx == len(lines) - 1:
                    # escrita parcial no fim do arquivo: descarta
                    self._truncate(valid_bytes)
                    return
                raise StorageError(f"WAL corrompido na linha {idx + 1} de {self.path}")
            valid_bytes += len(line)
            self.records_since_checkpoint += 1
            yield record

    def _truncate(self, size: int) -> None:
        self._file.close()
        with open(self.path, "r+b") as f:
            f.truncate(size)
            f.flush()
            os.fsync(f.fileno())
        self._file = open(self.path, "ab")

    def reset(self) -> None:
        self._truncate(0)
        self.records_since_checkpoint = 0

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()
