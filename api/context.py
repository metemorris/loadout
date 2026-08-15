"""Application configuration and catalog access for HTTP routes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request

from inventory_toolkit.execution import load_trip_executions
from inventory_toolkit.loader import load_inventory
from inventory_toolkit.packing import load_packing_plans
from inventory_toolkit.paths import default_data_directory
from inventory_toolkit.repository import CatalogRepository, CatalogSnapshot
from inventory_toolkit.trips import load_trips


SNAPSHOT_FILES = (
    "schema.yaml",
    "clothes.yaml",
    "locations.yaml",
    "trips.yaml",
    "packing_plans.yaml",
    "trip_executions.yaml",
)


def configured_data_dir() -> Path:
    """Resolve the mutable YAML catalog directory for this process."""

    configured = os.environ.get("LOADOUT_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return default_data_directory()


@lru_cache(maxsize=8)
def _load_snapshot(directory_name: str, signature: tuple) -> CatalogSnapshot:
    """Load a validated snapshot identified by its source-file signature."""

    del signature  # It exists solely as the cache invalidation key.
    directory = Path(directory_name)
    return CatalogSnapshot(
        inventory=load_inventory(directory),
        trips=load_trips(directory),
        plans=load_packing_plans(directory),
        executions=load_trip_executions(directory),
    )


def load_snapshot() -> CatalogSnapshot:
    """Return a cached snapshot, reloading whenever a YAML source changes."""

    directory = configured_data_dir()
    signature = tuple(
        (name, (directory / name).stat().st_mtime_ns, (directory / name).stat().st_size)
        for name in SNAPSHOT_FILES
    )
    return _load_snapshot(str(directory), signature)


@dataclass(frozen=True)
class ApiContext:
    """Provide routes with one explicit catalog and persistence boundary."""

    repository: Optional[CatalogRepository] = None

    def snapshot(self) -> CatalogSnapshot:
        """Return the latest catalog snapshot from the configured repository."""

        return self.repository.snapshot() if self.repository is not None else load_snapshot()

    def durable_data_dir(self) -> Path:
        """Return writable storage or reject operations on an in-memory repository."""

        if self.repository is None:
            return configured_data_dir()
        if self.repository.data_dir is None:
            raise HTTPException(
                status_code=501,
                detail="This operation requires a durable catalog repository",
            )
        return self.repository.data_dir


def get_context(request: Request) -> ApiContext:
    """Resolve the application context for a route dependency."""

    return request.app.state.api_context
