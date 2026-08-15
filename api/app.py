"""FastAPI application assembly for LoadOut's thin HTTP adapter."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from inventory_toolkit.execution import ExecutionValidationError
from inventory_toolkit.loader import InventoryValidationError
from inventory_toolkit.packing import PackingValidationError
from inventory_toolkit.repository import CatalogRepository
from inventory_toolkit.trips import TripValidationError

from .context import ApiContext
from .routes import inventory_router, packing_router, trip_router


VALIDATION_ERRORS = (
    InventoryValidationError,
    TripValidationError,
    PackingValidationError,
    ExecutionValidationError,
)


def create_app(repository: Optional[CatalogRepository] = None) -> FastAPI:
    """Build the HTTP adapter around an optional injected catalog repository."""

    application = FastAPI(title="LoadOut API", version="0.3.0")
    application.state.api_context = ApiContext(repository)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def validation_error(_request: Any, exc: Any) -> JSONResponse:
        """Render domain validation errors consistently at the HTTP boundary."""

        errors = list(getattr(exc, "errors", (str(exc),)))
        return JSONResponse(
            status_code=422,
            content={"code": "validation_error", "errors": errors},
        )

    for error_type in VALIDATION_ERRORS:
        application.add_exception_handler(error_type, validation_error)
    application.include_router(inventory_router)
    application.include_router(trip_router)
    application.include_router(packing_router)
    return application


app = create_app()


def run() -> None:
    """Run the local API service on its loopback-only default address."""

    import uvicorn

    uvicorn.run("api.app:app", host="127.0.0.1", port=8000)
