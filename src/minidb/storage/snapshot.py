"""Snapshot do banco inteiro em um único arquivo JSON.

A gravação é atômica: escrevemos em um arquivo temporário, chamamos
``fsync`` e só então usamos ``os.replace``, que substitui o arquivo antigo
de forma atômica tanto no Linux quanto no Windows. Assim, um crash durante
o checkpoint nunca deixa um snapshot pela metade.
"""

from __future__ import annotations

import json
import os
from typing import Any

from minidb.errors import StorageError

FORMAT_VERSION = 1


def write_snapshot(path: str, data: dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": FORMAT_VERSION, **data}, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_snapshot(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError(f"não foi possível ler o snapshot {path}: {exc}") from exc
    if data.get("version") != FORMAT_VERSION:
        raise StorageError(f"versão de snapshot não suportada: {data.get('version')}")
    return data
