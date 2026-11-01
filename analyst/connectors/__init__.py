"""플러그형 DB 커넥터. 새 DB는 BaseConnector를 구현하고 register()로 등록합니다."""
from .base import BaseConnector, QueryResult
from .sqlite import SQLiteConnector

_REGISTRY: dict[str, type[BaseConnector]] = {"sqlite": SQLiteConnector}


def register(name: str, cls: type[BaseConnector]) -> None:
    _REGISTRY[name] = cls


def get_connector(name: str, dsn: str, **kwargs) -> BaseConnector:
    if name not in _REGISTRY:
        raise ValueError(f"알 수 없는 커넥터: {name} (사용 가능: {list(_REGISTRY)})")
    return _REGISTRY[name](dsn, **kwargs)


__all__ = ["BaseConnector", "QueryResult", "get_connector", "register"]
