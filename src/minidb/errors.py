class MiniDBError(Exception):
    """Classe base de todos os erros do MiniDB."""


class SQLSyntaxError(MiniDBError):
    def __init__(self, message: str, pos: int | None = None) -> None:
        self.pos = pos
        if pos is not None:
            message = f"{message} (posição {pos})"
        super().__init__(message)


class CatalogError(MiniDBError):
    """Tabela ou coluna inexistente, tabela duplicada etc."""


class ConstraintError(MiniDBError):
    """Violação de PRIMARY KEY, NOT NULL ou tipo."""


class ExecutionError(MiniDBError):
    """Erro em tempo de execução (divisão por zero, agregação inválida...)."""


class StorageError(MiniDBError):
    """Problemas ao ler ou gravar os arquivos do banco."""
