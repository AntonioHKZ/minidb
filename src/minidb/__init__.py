"""MiniDB — um banco de dados relacional pequeno, escrito do zero em Python."""

from minidb.database import Database, Result, connect
from minidb.errors import (
    CatalogError,
    ConstraintError,
    ExecutionError,
    MiniDBError,
    SQLSyntaxError,
    StorageError,
)

__version__ = "0.3.0"

__all__ = [
    "Database",
    "Result",
    "connect",
    "MiniDBError",
    "SQLSyntaxError",
    "CatalogError",
    "ConstraintError",
    "ExecutionError",
    "StorageError",
]
