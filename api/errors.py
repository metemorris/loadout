"""HTTP error translation shared by route groups."""

from fastapi import HTTPException


def domain_conflict(exc: Exception) -> HTTPException:
    """Translate a packing, execution, or movement conflict to HTTP 409."""

    errors = getattr(exc, "errors", None)
    detail = " · ".join(errors) if errors else str(exc)
    return HTTPException(status_code=409, detail=detail)
