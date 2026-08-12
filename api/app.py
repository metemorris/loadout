from __future__ import annotations

import os
import uuid
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from fastapi import FastAPI, HTTPException, Query as ApiQuery
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from inventory_toolkit.execution import (
    ExecutionValidationError,
    TripExecutionNotFoundError,
    confirm_packing_decision,
    confirm_packing_decisions,
    create_trip_execution,
    load_trip_executions,
    record_execution_action,
)
from inventory_toolkit.loader import InventoryValidationError, load_inventory
from inventory_toolkit.models import PhysicalItem, Query
from inventory_toolkit.movement import MovementError, move_items, plan_movement
from inventory_toolkit.repository import CatalogRepository
from inventory_toolkit.packing import (
    PackingPlan,
    PackingPlanEntry,
    PackingPlanNotFoundError,
    PackingValidationError,
    confirm_packing_plan,
    load_packing_plan,
    load_packing_plans,
    save_packing_plan,
)
from inventory_toolkit.trips import TripNotFoundError, TripValidationError, load_trips


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "data"
SNAPSHOT_FILES = (
    "schema.yaml", "clothes.yaml", "locations.yaml", "trips.yaml",
    "packing_plans.yaml", "trip_executions.yaml",
)

CATEGORY_TYPES = {
    "activewear": {
        "swim_trunks", "swimwear", "towel", "beach_towel",
    },
    "tops": {
        "t_shirt", "shirt", "tank_top", "long_sleeve_shirt", "vest",
    },
    "bottoms": {
        "jeans", "shorts", "sports_shorts", "sweatpants", "thermal_pants",
    },
    "outerwear": {
        "hoodie", "jacket", "sweater", "zip_up", "suit_jacket", "robe",
    },
    "footwear": {
        "shoes", "dress_shoes", "athletic_shoes", "soccer_shoes", "boots", "flip_flops",
    },
    "formal": {"suit", "tuxedo", "tie", "belt"},
    "accessories": {
        "hat", "beanie", "bandana", "scarf", "gloves", "neck_gaiter",
    },
    "essentials": {
        "socks", "underwear",
    },
}

CATEGORY_META = {
    "activewear": ("Activewear & Other", "Swim, sport, towels, and travel extras"),
    "tops": ("Tops", "Everyday shirts and layers"),
    "bottoms": ("Bottoms", "Denim, shorts, and pants"),
    "outerwear": ("Outerwear", "Warm layers and jackets"),
    "footwear": ("Footwear", "Shoes and boots"),
    "formal": ("Formal", "Suits and finishing pieces"),
    "accessories": ("Accessories", "Hats, scarves, and small pieces"),
    "essentials": ("Essentials", "Basics, swim, and towels"),
    "other": ("Other", "Uncategorized possessions"),
}


def _data_dir() -> Path:
    configured = os.environ.get("LOADOUT_DATA_DIR")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DATA_DIR


@lru_cache(maxsize=8)
def _load_catalog_snapshot(directory_name: str, signature: tuple) -> tuple:
    """Cache validated read models until one of their source files changes."""

    directory = Path(directory_name)
    return (
        load_inventory(directory),
        load_trips(directory),
        load_packing_plans(directory),
        load_trip_executions(directory),
    )


def _catalog_snapshot() -> tuple:
    directory = _data_dir()
    signature = tuple(
        (name, (directory / name).stat().st_mtime_ns, (directory / name).stat().st_size)
        for name in SNAPSHOT_FILES
    )
    return _load_catalog_snapshot(str(directory), signature)


def _plain(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _category(item_type: str) -> str:
    for category, types in CATEGORY_TYPES.items():
        if item_type in types:
            return category
    return "other"


def _item(item: PhysicalItem) -> Dict[str, Any]:
    color = item.attributes.get("color")
    if isinstance(color, tuple):
        color = color[0] if color else None
    return {
        "id": item.id,
        "definitionId": item.definition_id,
        "name": item.name,
        "type": item.type,
        "category": _category(item.type),
        "color": color,
        "attributes": _plain(item.attributes),
        "uses": list(item.uses),
        "currentLocation": item.current_location,
        "preferredLocation": item.preferred_location,
        "condition": item.condition,
        "status": item.status,
        "notes": item.notes,
        "movements": [_plain(movement) for movement in item.movements],
    }


def _location(location: Any, items: Sequence[PhysicalItem]) -> Dict[str, Any]:
    grouped: Dict[str, int] = {}
    for item in items:
        category = _category(item.type)
        grouped[category] = grouped.get(category, 0) + 1
    return {
        "id": location.id,
        "name": location.name,
        "kind": location.kind,
        "city": location.city,
        "region": location.region,
        "country": location.country,
        "notes": location.notes,
        "itemCount": len(items),
        "definitionCount": len({item.definition_id for item in items}),
        "categoryCounts": grouped,
    }


def _trip(trip: Any) -> Dict[str, Any]:
    return {
        "id": trip.id,
        "name": trip.name,
        "status": trip.status,
        "startDate": trip.start_date.isoformat(),
        "endDate": trip.end_date.isoformat(),
        "durationDays": trip.duration_days,
        "places": [_plain(place) for place in trip.places],
        "legs": [_plain(leg) for leg in trip.legs],
        "luggage": list(trip.luggage),
        "attachments": [_plain(attachment) for attachment in trip.attachments],
        "planning": _plain(trip.planning),
        "notes": trip.notes,
    }


def _trip_detail_payload(trip_id: str, snapshot: Optional[tuple] = None) -> Dict[str, Any]:
    inventory, trip_catalog, plan_catalog, execution_catalog = snapshot or _catalog_snapshot()
    try:
        trip = trip_catalog.get(trip_id)
    except TripNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    plans = [plan for plan in plan_catalog.plans if plan.trip == trip.id]
    executions = [
        execution for execution in execution_catalog.executions
        if execution.trip == trip.id
    ]
    referenced_item_ids = {
        entry.item
        for plan in plans
        for entries in plan.sections.values()
        for entry in entries
        if entry.item is not None
    } | {
        action.item
        for execution in executions
        for action in execution.actions
        if action.item is not None
    }
    return {
        "trip": _trip(trip),
        "containers": [
            _location(inventory.resolve_location(container_id), inventory.container_contents(container_id))
            for container_id in trip.luggage
        ],
        "plans": [_plain(plan) for plan in plans],
        "executions": [_plain(execution) for execution in executions],
        "items": [_item(item) for item in inventory.items if item.id in referenced_item_ids],
    }


class MovementRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1)
    source: str
    destination: str
    reason: Optional[str] = None
    update_preferred: bool = False
    confirmed: bool = False


class PackingActionRequest(BaseModel):
    plan_id: str
    section: str
    entry_index: int = Field(ge=1)
    action: str
    replacement_item_id: Optional[str] = None
    notes: Optional[str] = None
    confirmed: bool = False


class PackingDecisionRequest(BaseModel):
    section: str
    entry_index: int = Field(ge=1)


class PackingBatchRequest(BaseModel):
    plan_id: str
    decisions: list[PackingDecisionRequest] = Field(min_length=1)
    confirmed: bool = False


class PackingPlanItemRequest(BaseModel):
    plan_id: str
    item_id: str
    container: str
    reason: Optional[str] = None
    confirmed: bool = False


class PackingContainerRequest(BaseModel):
    plan_id: str
    section: str
    entry_index: int = Field(ge=1)
    container: str
    confirmed: bool = False


class PackingUnpackRequest(BaseModel):
    plan_id: str
    section: str
    entry_index: int = Field(ge=1)
    confirmed: bool = False


def _execution_for_trip(
    trip_id: str, plan_id: str, *, data_dir: Path, create: bool = False
) -> Any:
    existing = next(
        (
            execution
            for execution in load_trip_executions(data_dir).executions
            if execution.trip == trip_id
        ),
        None,
    )
    if existing is not None:
        if existing.packing_plan != plan_id:
            raise ExecutionValidationError(
                ["trip execution belongs to a different packing plan"]
            )
        return existing
    if not create:
        return None
    return create_trip_execution(
        "{}-execution".format(trip_id),
        trip_id,
        plan_id,
        confirmed=True,
        notes="Created from the LoadOut trip packing surface.",
        data_dir=data_dir,
    )


def _replace_draft_entry(
    plan: PackingPlan,
    section: str,
    entry_index: int,
    replacement: Optional[PackingPlanEntry],
) -> PackingPlan:
    sections = {name: list(entries) for name, entries in plan.sections.items()}
    entries = sections.get(section)
    if entries is None or entry_index > len(entries):
        raise PackingValidationError(
            ["unknown packing decision '{}:{}'".format(section, entry_index)]
        )
    if replacement is None:
        entries.pop(entry_index - 1)
    else:
        entries[entry_index - 1] = replacement
    return PackingPlan(
        id=plan.id,
        trip=plan.trip,
        status=plan.status,
        created_at=plan.created_at,
        sections={name: tuple(values) for name, values in sections.items()},
        notes=plan.notes,
    )


def _editable_plan(plan: PackingPlan) -> PackingPlan:
    if plan.status == "draft":
        return plan
    if plan.status != "confirmed":
        raise PackingValidationError(
            ["only draft or confirmed packing plans can be edited"]
        )
    return PackingPlan(
        id="{}-ui-rev-{}".format(plan.id, uuid.uuid4().hex[:8]),
        trip=plan.trip,
        status="draft",
        created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        sections=plan.sections,
        notes="UI revision of {}. {}".format(
            plan.id, plan.notes or ""
        ).strip(),
    )


def create_app(repository: Optional[CatalogRepository] = None) -> FastAPI:
    app = FastAPI(title="LoadOut API", version="0.1.0")

    def catalog_snapshot() -> tuple:
        return repository.snapshot().as_tuple() if repository is not None else _catalog_snapshot()

    def durable_data_dir() -> Path:
        if repository is None:
            return _data_dir()
        if repository.data_dir is None:
            raise HTTPException(
                status_code=501,
                detail="This operation requires a durable catalog repository",
            )
        return repository.data_dir
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(InventoryValidationError)
    @app.exception_handler(TripValidationError)
    @app.exception_handler(PackingValidationError)
    @app.exception_handler(ExecutionValidationError)
    async def validation_error(_request: Any, exc: Any):
        from fastapi.responses import JSONResponse

        errors = list(getattr(exc, "errors", (str(exc),)))
        return JSONResponse(status_code=422, content={"code": "validation_error", "errors": errors})

    @app.get("/api/health")
    def health() -> Dict[str, str]:
        catalog_snapshot()
        return {"status": "ok", "mode": "local"}

    @app.get("/api/overview")
    def overview() -> Dict[str, Any]:
        inventory = catalog_snapshot()[0]
        locations = [
            _location(location, inventory.list_at_location(location.id))
            for location in inventory.locations
        ]
        return {
            "locations": locations,
            "totals": {
                "items": len(inventory.items),
                "definitions": len(inventory.definitions),
                "homes": sum(location.kind == "home" for location in inventory.locations),
                "containers": sum(location.kind == "travel_container" for location in inventory.locations),
            },
            "categories": [
                {"id": category, "name": name, "description": description}
                for category, (name, description) in CATEGORY_META.items()
            ],
        }

    @app.get("/api/locations/{location_id}")
    def location_detail(location_id: str) -> Dict[str, Any]:
        inventory = catalog_snapshot()[0]
        try:
            location = inventory.resolve_location(location_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        items = inventory.list_at_location(location.id)
        categories = []
        for category, (name, description) in CATEGORY_META.items():
            category_items = [_item(item) for item in items if _category(item.type) == category]
            if category_items:
                categories.append(
                    {
                        "id": category,
                        "name": name,
                        "description": description,
                        "count": len(category_items),
                        "items": category_items,
                    }
                )
        return {"location": _location(location, items), "categories": categories}

    @app.get("/api/items/{item_id}")
    def item_detail(item_id: str) -> Dict[str, Any]:
        try:
            return _item(catalog_snapshot()[0].resolve_item(item_id))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/api/inventory")
    def query_inventory(
        text: Optional[str] = None,
        location: Optional[str] = None,
        item_type: Optional[str] = ApiQuery(default=None, alias="type"),
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        inventory = catalog_snapshot()[0]
        try:
            items = inventory.find(Query(text=text, location=location, item_type=item_type, status=status))
        except (LookupError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {"items": [_item(item) for item in items], "count": len(items)}

    @app.post("/api/movements/preview")
    def preview_movement(request: MovementRequest) -> Dict[str, Any]:
        try:
            if repository is not None:
                plan = repository.preview_movement(
                    request.item_ids, request.source, request.destination,
                    reason=request.reason, update_preferred=request.update_preferred,
                )
            else:
                plan = plan_movement(
                    request.item_ids, request.source, request.destination,
                    data_dir=durable_data_dir(), reason=request.reason,
                    update_preferred=request.update_preferred,
                )
        except (MovementError, LookupError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"movement": _plain(plan), "requiresConfirmation": True}

    @app.post("/api/movements/confirm")
    def confirm_movement(request: MovementRequest) -> Dict[str, Any]:
        if not request.confirmed:
            raise HTTPException(status_code=428, detail="Explicit confirmation is required")
        try:
            if repository is not None:
                result = repository.confirm_movement(
                    request.item_ids, request.source, request.destination,
                    reason=request.reason, update_preferred=request.update_preferred,
                    confirmed=True,
                )
            else:
                result = move_items(
                    request.item_ids, request.source, request.destination,
                    data_dir=durable_data_dir(), reason=request.reason,
                    update_preferred=request.update_preferred, confirmed=True,
                )
        except (MovementError, LookupError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        inventory = catalog_snapshot()[0]
        return {
            "movement": _plain(result.plan),
            "applied": result.applied,
            "items": [_item(inventory.resolve_item(item_id)) for item_id in request.item_ids],
        }

    @app.get("/api/trips")
    def trips() -> Dict[str, Any]:
        _inventory, catalog, plans, executions = catalog_snapshot()
        return {
            "trips": [
                {
                    **_trip(trip),
                    "planCount": sum(plan.trip == trip.id for plan in plans.plans),
                    "execution": next(
                        (
                            {"id": execution.id, "status": execution.status}
                            for execution in executions.executions
                            if execution.trip == trip.id
                        ),
                        None,
                    ),
                }
                for trip in catalog.trips
            ]
        }

    @app.get("/api/trips/{trip_id}")
    def trip_detail(trip_id: str) -> Dict[str, Any]:
        return _trip_detail_payload(trip_id, catalog_snapshot())

    @app.get("/api/trips/{trip_id}/packing-plans/{plan_id}/swap-candidates")
    def swap_candidates(trip_id: str, plan_id: str, item_id: str) -> Dict[str, Any]:
        try:
            inventory, trip_catalog, plan_catalog, _executions = catalog_snapshot()
            trip = trip_catalog.get(trip_id)
            plan = plan_catalog.get(plan_id)
            if plan.trip != trip.id:
                raise PackingValidationError(["packing plan does not belong to this trip"])
            original = inventory.resolve_item(item_id)
            planned_ids = {
                entry.item
                for entries in plan.sections.values()
                for entry in entries
                if entry.item is not None
            }
            unavailable = {"missing", "lost", "loaned", "repair", "discarded"}
            candidates = sorted(
                (
                    item for item in inventory.items
                    if item.id not in planned_ids
                    and item.type == original.type
                    and (item.status or "") not in unavailable
                ),
                key=lambda item: (
                    item.current_location != original.current_location,
                    item.name.lower(),
                    item.id,
                ),
            )
            return {"items": [_item(item) for item in candidates], "count": len(candidates)}
        except (PackingPlanNotFoundError, TripNotFoundError, LookupError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except PackingValidationError as exc:
            raise HTTPException(status_code=409, detail=" · ".join(exc.errors))

    @app.post("/api/trips/{trip_id}/packing-plan-items")
    def add_packing_plan_item(
        trip_id: str, request: PackingPlanItemRequest
    ) -> Dict[str, Any]:
        if not request.confirmed:
            raise HTTPException(status_code=428, detail="Explicit confirmation is required")
        try:
            inventory, trip_catalog, plan_catalog, _executions = catalog_snapshot()
            trip = trip_catalog.get(trip_id)
            plan = plan_catalog.get(request.plan_id)
            if plan.trip != trip.id:
                raise PackingValidationError(["packing plan does not belong to this trip"])
            if request.container not in trip.luggage:
                raise PackingValidationError(["container is not assigned to this trip"])
            item = inventory.resolve_item(request.item_id)
            if (item.status or "") in {"missing", "lost", "loaned", "repair", "discarded"}:
                raise PackingValidationError(["item is not currently available for packing"])
            if any(
                entry.item == item.id
                for entries in plan.sections.values()
                for entry in entries
            ):
                raise PackingValidationError(["item is already in this packing plan"])
            editable = _editable_plan(plan)
            sections = {name: list(entries) for name, entries in editable.sections.items()}
            sections["pack"].append(
                PackingPlanEntry(
                    item=item.id,
                    requirement="manual_addition",
                    leg=trip.legs[0].id if trip.legs else None,
                    container=request.container,
                    source=item.current_location,
                    destination=None,
                    quantity=1,
                    reason=(request.reason or "Added manually from the trip packing surface.").strip(),
                )
            )
            updated = replace(
                editable,
                sections={name: tuple(entries) for name, entries in sections.items()},
            )
            save_packing_plan(
                updated,
                replace_existing=updated.id == plan.id,
                data_dir=durable_data_dir(),
            )
            return _trip_detail_payload(trip.id, catalog_snapshot())
        except (PackingPlanNotFoundError, TripNotFoundError, LookupError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except PackingValidationError as exc:
            raise HTTPException(status_code=409, detail=" · ".join(exc.errors))

    @app.post("/api/trips/{trip_id}/packing-plan-containers")
    def change_packing_plan_container(
        trip_id: str, request: PackingContainerRequest
    ) -> Dict[str, Any]:
        if not request.confirmed:
            raise HTTPException(status_code=428, detail="Explicit confirmation is required")
        try:
            inventory, trip_catalog, plan_catalog, execution_catalog = catalog_snapshot()
            trip = trip_catalog.get(trip_id)
            plan = plan_catalog.get(request.plan_id)
            if plan.trip != trip.id:
                raise PackingValidationError(["packing plan does not belong to this trip"])
            if request.container not in trip.luggage:
                raise PackingValidationError(["container is not assigned to this trip"])
            editable = _editable_plan(plan)
            entries = editable.sections.get(request.section)
            if entries is None or request.entry_index > len(entries):
                raise PackingValidationError(
                    ["unknown packing decision '{}:{}'".format(request.section, request.entry_index)]
                )
            entry = entries[request.entry_index - 1]
            if entry.item is None:
                raise PackingValidationError(["this packing decision has no physical item"])
            if entry.container == request.container:
                raise PackingValidationError(["choose a different container"])
            updated = _replace_draft_entry(
                editable,
                request.section,
                request.entry_index,
                replace(entry, container=request.container),
            )
            item = inventory.resolve_item(entry.item)
            execution = next(
                (
                    value for value in execution_catalog.executions
                    if value.trip == trip.id
                    and value.status in ("preparing", "in_progress", "reconciling")
                ),
                None,
            )
            was_packed = execution is not None and any(
                action.item == item.id
                and action.kind == "packed"
                and action.state == "applied"
                for action in execution.actions
            )
            if was_packed and item.current_location != request.container:
                plan_movement(
                    [item.id], item.current_location, request.container,
                    data_dir=durable_data_dir(), reason="Trip packing container change preview.",
                )
            save_packing_plan(
                updated,
                replace_existing=updated.id == plan.id,
                data_dir=durable_data_dir(),
            )
            if was_packed and item.current_location != request.container:
                record_execution_action(
                    execution.id,
                    "ui-container-transfer-{}".format(uuid.uuid4().hex),
                    "transferred",
                    item=item.id,
                    description="Changed packing container",
                    source=item.current_location,
                    destination=request.container,
                    reason="Changed the assigned bag from the trip packing surface.",
                    confirmed=True,
                    data_dir=durable_data_dir(),
                )
            return _trip_detail_payload(trip.id, catalog_snapshot())
        except (PackingPlanNotFoundError, TripNotFoundError, LookupError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (PackingValidationError, ExecutionValidationError, MovementError) as exc:
            errors = getattr(exc, "errors", None)
            raise HTTPException(status_code=409, detail=" · ".join(errors) if errors else str(exc))

    @app.post("/api/trips/{trip_id}/packing-unpack")
    def unpack_packing_item(
        trip_id: str, request: PackingUnpackRequest
    ) -> Dict[str, Any]:
        if not request.confirmed:
            raise HTTPException(status_code=428, detail="Explicit confirmation is required")
        try:
            inventory, trip_catalog, plan_catalog, execution_catalog = catalog_snapshot()
            trip = trip_catalog.get(trip_id)
            plan = plan_catalog.get(request.plan_id)
            if plan.trip != trip.id:
                raise PackingValidationError(["packing plan does not belong to this trip"])
            entries = plan.sections.get(request.section)
            if entries is None or request.entry_index > len(entries):
                raise PackingValidationError(
                    ["unknown packing decision '{}:{}'".format(request.section, request.entry_index)]
                )
            entry = entries[request.entry_index - 1]
            if entry.item is None:
                raise PackingValidationError(["this packing decision has no physical item"])
            execution = next(
                (
                    value for value in execution_catalog.executions
                    if value.trip == trip.id
                    and value.status in ("preparing", "in_progress", "reconciling")
                ),
                None,
            )
            if execution is None:
                raise ExecutionValidationError(["trip has no active execution"])
            item = inventory.resolve_item(entry.item)
            if item.current_location not in trip.luggage:
                raise ExecutionValidationError(["item is not currently in trip luggage"])
            original_source = next(
                (
                    action.source for action in execution.actions
                    if action.item == item.id
                    and action.kind == "packed"
                    and action.state == "applied"
                    and action.source is not None
                    and action.source not in trip.luggage
                ),
                None,
            )
            if original_source is None:
                raise ExecutionValidationError(
                    ["packed item has no recorded pre-pack source"]
                )
            record_execution_action(
                execution.id,
                "ui-unpack-{}".format(uuid.uuid4().hex),
                "returned",
                item=item.id,
                description="Unpacked during trip preparation",
                source=item.current_location,
                destination=original_source,
                reason="Unpacked from the trip packing surface.",
                confirmed=True,
                data_dir=durable_data_dir(),
            )
            return _trip_detail_payload(trip.id, catalog_snapshot())
        except (PackingPlanNotFoundError, TripNotFoundError, LookupError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (PackingValidationError, ExecutionValidationError, MovementError) as exc:
            errors = getattr(exc, "errors", None)
            raise HTTPException(status_code=409, detail=" · ".join(errors) if errors else str(exc))

    @app.post("/api/trips/{trip_id}/packing-batch")
    def packing_batch(trip_id: str, request: PackingBatchRequest) -> Dict[str, Any]:
        if not request.confirmed:
            raise HTTPException(status_code=428, detail="Explicit confirmation is required")
        try:
            inventory_snapshot, trip_catalog, plan_catalog, execution_catalog = catalog_snapshot()
            trip = trip_catalog.get(trip_id)
            plan = plan_catalog.get(request.plan_id)
            if plan.trip != trip.id:
                raise PackingValidationError(["packing plan does not belong to this trip"])
            existing_execution = next(
                (
                    value for value in execution_catalog.executions
                    if value.trip == trip.id
                ),
                None,
            )
            decisions = [
                "{}:{}".format(value.section, value.entry_index)
                for value in request.decisions
            ]
            if existing_execution is not None and existing_execution.packing_plan != plan.id:
                if plan.status != "draft":
                    raise ExecutionValidationError(
                        ["active trip execution belongs to a different packing plan"]
                    )
                applied_decisions = []
                failed_decisions = []
                affected_item_ids = set()
                execution = existing_execution
                for requested, decision in zip(request.decisions, decisions):
                    entries = plan.sections.get(requested.section)
                    if (
                        requested.section != "pack"
                        or entries is None
                        or requested.entry_index > len(entries)
                    ):
                        raise PackingValidationError(
                            ["unknown physical pack decision {!r}".format(decision)]
                        )
                    entry = entries[requested.entry_index - 1]
                    if entry.item is None or entry.container is None:
                        raise PackingValidationError(
                            ["decision {!r} is not a physical pack decision".format(decision)]
                        )
                    affected_item_ids.add(entry.item)
                    item = load_inventory(durable_data_dir()).resolve_item(entry.item)
                    if item.current_location == entry.container:
                        failed_decisions.append(decision)
                        continue
                    source = item.current_location if item.current_location != entry.container else None
                    destination = entry.container if source is not None else None
                    try:
                        execution = record_execution_action(
                            execution.id,
                            "ui-revision-pack-{}".format(uuid.uuid4().hex),
                            "packed",
                            item=entry.item,
                            description=entry.requirement,
                            leg=entry.leg,
                            source=source,
                            destination=destination,
                            reason=(
                                "Packed updated selection {} from draft plan {}."
                                .format(decision, plan.id)
                            ),
                            confirmed=True,
                            data_dir=durable_data_dir(),
                        )
                        applied_decisions.append(decision)
                    except (ExecutionValidationError, MovementError):
                        failed_decisions.append(decision)
                        execution = load_trip_executions(durable_data_dir()).get(execution.id)
                inventory = load_inventory(durable_data_dir())
                return {
                    "execution": _plain(execution),
                    "items": [
                        _item(item) for item in inventory.items
                        if item.id in affected_item_ids
                    ],
                    "appliedDecisions": applied_decisions,
                    "failedDecisions": failed_decisions,
                }
            if plan.status == "draft":
                plan = confirm_packing_plan(plan.id, data_dir=durable_data_dir())
                execution = existing_execution or create_trip_execution(
                    "{}-execution".format(trip.id), trip.id, plan.id, confirmed=True,
                    notes="Created from the LoadOut trip packing surface.", data_dir=durable_data_dir(),
                )
            else:
                execution = existing_execution
                if execution is None:
                    execution = create_trip_execution(
                        "{}-execution".format(trip.id),
                        trip.id,
                        plan.id,
                        confirmed=True,
                        notes="Created from the LoadOut trip packing surface.",
                        data_dir=durable_data_dir(),
                    )
            result = confirm_packing_decisions(
                execution.id,
                "ui-pack-{}".format(uuid.uuid4().hex),
                decisions,
                confirmed=True,
                reason="Packed selected items from the LoadOut trip packing surface.",
                data_dir=durable_data_dir(),
                execution_snapshot=execution,
                plan_snapshot=plan,
                inventory_snapshot=inventory_snapshot,
            )
            inventory = load_inventory(durable_data_dir())
            moved_item_ids = {
                action.item
                for action in result.execution.actions
                if action.decision in result.applied_decisions and action.item is not None
            }
            return {
                "execution": _plain(result.execution),
                "items": [_item(item) for item in inventory.items if item.id in moved_item_ids],
                "appliedDecisions": list(result.applied_decisions),
                "failedDecisions": list(result.failed_decisions),
            }
        except (PackingPlanNotFoundError, TripNotFoundError, LookupError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (PackingValidationError, ExecutionValidationError, MovementError) as exc:
            errors = getattr(exc, "errors", None)
            raise HTTPException(status_code=409, detail=" · ".join(errors) if errors else str(exc))

    @app.post("/api/trips/{trip_id}/packing-actions")
    def packing_action(trip_id: str, request: PackingActionRequest) -> Dict[str, Any]:
        if not request.confirmed:
            raise HTTPException(status_code=428, detail="Explicit confirmation is required")
        if request.action not in {"pack", "swap", "remove"}:
            raise HTTPException(status_code=422, detail="Action must be pack, swap, or remove")
        if request.action == "swap" and not (request.notes or "").strip():
            raise HTTPException(
                status_code=422,
                detail="Swap notes are required so the decision can inform future recommendations",
            )
        try:
            trip = load_trips(durable_data_dir()).get(trip_id)
            plan = load_packing_plan(request.plan_id, durable_data_dir())
            if plan.trip != trip.id:
                raise PackingValidationError(["packing plan does not belong to this trip"])
            entries = plan.sections.get(request.section)
            if entries is None or request.entry_index > len(entries):
                raise PackingValidationError(
                    ["unknown packing decision '{}:{}'".format(request.section, request.entry_index)]
                )
            entry = entries[request.entry_index - 1]
            if entry.item is None:
                raise PackingValidationError(["this packing decision has no physical item"])
            decision = "{}:{}".format(request.section, request.entry_index)

            if request.action == "remove" and plan.status == "draft":
                updated = _replace_draft_entry(plan, request.section, request.entry_index, None)
                save_packing_plan(updated, replace_existing=True, data_dir=durable_data_dir())
                return _trip_detail_payload(trip_id, catalog_snapshot())

            replacement_item = None
            if request.action == "swap":
                swap_notes = request.notes.strip()
                if not request.replacement_item_id:
                    raise PackingValidationError(["a replacement item is required for swap"])
                replacement_item = load_inventory(durable_data_dir()).resolve_item(request.replacement_item_id)
                if replacement_item.id == entry.item:
                    raise PackingValidationError(["choose a different replacement item"])
                if any(
                    candidate.item == replacement_item.id
                    for section_entries in plan.sections.values()
                    for candidate in section_entries
                ):
                    raise PackingValidationError(["replacement item is already in this packing plan"])
                if plan.status == "draft":
                    updated_entry = replace(
                        entry,
                        item=replacement_item.id,
                        source=replacement_item.current_location,
                        reason="Swap note: {} Original recommendation: {}".format(
                            swap_notes, entry.reason
                        ),
                    )
                    updated = _replace_draft_entry(
                        plan, request.section, request.entry_index, updated_entry
                    )
                    save_packing_plan(updated, replace_existing=True, data_dir=durable_data_dir())
                    return _trip_detail_payload(trip_id, catalog_snapshot())

            execution = _execution_for_trip(
                trip.id, plan.id, data_dir=durable_data_dir(), create=False
            )
            if plan.status == "draft":
                plan = confirm_packing_plan(plan.id, data_dir=durable_data_dir())
            execution = execution or _execution_for_trip(
                trip.id, plan.id, data_dir=durable_data_dir(), create=True
            )
            if any(action.decision == decision for action in execution.actions):
                raise ExecutionValidationError(["this packing decision already has an outcome"])

            if request.action == "pack":
                confirm_packing_decision(
                    execution.id,
                    "ui-pack-{}".format(uuid.uuid4().hex),
                    decision,
                    accepted=True,
                    confirmed=True,
                    reason="Packed from the LoadOut trip packing surface.",
                    data_dir=durable_data_dir(),
                )
            elif request.action == "remove":
                confirm_packing_decision(
                    execution.id,
                    "ui-remove-{}".format(uuid.uuid4().hex),
                    decision,
                    accepted=False,
                    confirmed=True,
                    reason="Removed while reviewing the packing list.",
                    data_dir=durable_data_dir(),
                )
            else:
                assert replacement_item is not None
                if entry.container is None:
                    raise PackingValidationError(["this decision has no destination container"])
                plan_movement(
                    [replacement_item.id],
                    replacement_item.current_location,
                    entry.container,
                    data_dir=durable_data_dir(),
                    reason="Packing-list swap preview.",
                )
                confirm_packing_decision(
                    execution.id,
                    "ui-swap-remove-{}".format(uuid.uuid4().hex),
                    decision,
                    accepted=False,
                    confirmed=True,
                    reason="Replaced by {} while packing. Swap note: {}".format(
                        replacement_item.name, swap_notes
                    ),
                    data_dir=durable_data_dir(),
                )
                record_execution_action(
                    execution.id,
                    "ui-swap-pack-{}".format(uuid.uuid4().hex),
                    "packed",
                    item=replacement_item.id,
                    description="Replacement for {}".format(decision),
                    leg=entry.leg,
                    source=replacement_item.current_location,
                    destination=entry.container,
                    reason="Swapped for {} from the confirmed packing plan. Swap note: {}".format(
                        entry.item, swap_notes
                    ),
                    confirmed=True,
                    data_dir=durable_data_dir(),
                )
            return _trip_detail_payload(trip_id, catalog_snapshot())
        except (PackingPlanNotFoundError, TripNotFoundError, LookupError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (PackingValidationError, ExecutionValidationError, MovementError) as exc:
            errors = getattr(exc, "errors", None)
            raise HTTPException(status_code=409, detail=" · ".join(errors) if errors else str(exc))

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("api.app:app", host="127.0.0.1", port=8000, reload=True)
