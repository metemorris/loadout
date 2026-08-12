"""Request-scoped memoization for constructing a consistent catalog snapshot."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Dict, Iterator, Optional, Tuple


_CACHE: ContextVar[Optional[Dict[Tuple[str, str], Any]]] = ContextVar(
    "catalog_load_cache", default=None
)


@contextmanager
def catalog_load_context() -> Iterator[None]:
    """Share validated loaders during one snapshot build, never across writes."""

    token = _CACHE.set({})
    try:
        yield
    finally:
        _CACHE.reset(token)


def cached_catalog_load(kind: str, directory: str, loader: Callable[[], Any]) -> Any:
    cache = _CACHE.get()
    if cache is None:
        return loader()
    key = (kind, directory)
    if key not in cache:
        cache[key] = loader()
    return cache[key]
