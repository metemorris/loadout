from __future__ import annotations

import os
import uuid
from dataclasses import fields, is_dataclass, replace
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
    create_trip_execution,
    load_trip_executions,
    record_execution_action,
)
from inventory_toolkit.loader import InventoryValidationError, load_inventory
from inventory_toolkit.models import PhysicalItem, Query
from inventory_toolkit.movement import MovementError, move_items, plan_movement
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
        "socks", "underwear", "towel", "beach_towel", "swim_trunks", "swimwear",
    },
}

CATEGORY_META = {
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


def _trip_detail_payload(trip_id: str) -> Dict[str, Any]:
    inventory, trip_catalog, plan_catalog, execution_catalog = _catalog_snapshot()
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


def _execution_for_trip(trip_id: str, plan_id: str, *, create: bool = False) -> Any:
    existing = next(
        (
            execution
            for execution in load_trip_executions(_data_dir()).executions
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
        data_dir=_data_dir(),
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


def create_app() -> FastAPI:
    app = FastAPI(title="LoadOut API", version="0.1.0")
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
        load_inventory(_data_dir())
        return {"status": "ok", "mode": "local"}

    @app.get("/api/overview")
    def overview() -> Dict[str, Any]:
        inventory = load_inventory(_data_dir())
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
        inventory = load_inventory(_data_dir())
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
            return _item(load_inventory(_data_dir()).resolve_item(item_id))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/api/inventory")
    def query_inventory(
        text: Optional[str] = None,
        location: Optional[str] = None,
        item_type: Optional[str] = ApiQuery(default=None, alias="type"),
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        inventory = load_inventory(_data_dir())
        try:
            items = inventory.find(Query(text=text, location=location, item_type=item_type, status=status))
        except (LookupError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {"items": [_item(item) for item in items], "count": len(items)}

    @app.post("/api/movements/preview")
    def preview_movement(request: MovementRequest) -> Dict[str, Any]:
        try:
            plan = plan_movement(
                request.item_ids,
                request.source,
                request.destination,
                data_dir=_data_dir(),
                reason=request.reason,
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
            result = move_items(
                request.item_ids,
                request.source,
                request.destination,
                data_dir=_data_dir(),
                reason=request.reason,
                update_preferred=request.update_preferred,
                confirmed=True,
            )
        except (MovementError, LookupError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        inventory = load_inventory(_data_dir())
        return {
            "movement": _plain(result.plan),
            "applied": result.applied,
            "items": [_item(inventory.resolve_item(item_id)) for item_id in request.item_ids],
        }

    @app.get("/api/trips")
    def trips() -> Dict[str, Any]:
        _inventory, catalog, plans, executions = _catalog_snapshot()
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
        return _trip_detail_payload(trip_id)

    @app.get("/api/trips/{trip_id}/packing-plans/{plan_id}/swap-candidates")
    def swap_candidates(trip_id: str, plan_id: str, item_id: str) -> Dict[str, Any]:
        try:
            inventory, trip_catalog, plan_catalog, _executions = _catalog_snapshot()
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
            trip = load_trips(_data_dir()).get(trip_id)
            plan = load_packing_plan(request.plan_id, _data_dir())
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
                save_packing_plan(updated, replace_existing=True, data_dir=_data_dir())
                return _trip_detail_payload(trip_id)

            replacement_item = None
            if request.action == "swap":
                swap_notes = request.notes.strip()
                if not request.replacement_item_id:
                    raise PackingValidationError(["a replacement item is required for swap"])
                replacement_item = load_inventory(_data_dir()).resolve_item(request.replacement_item_id)
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
                    save_packing_plan(updated, replace_existing=True, data_dir=_data_dir())
                    return _trip_detail_payload(trip_id)

            if plan.status == "draft":
                plan = confirm_packing_plan(plan.id, data_dir=_data_dir())
            execution = _execution_for_trip(trip.id, plan.id, create=True)
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
                    data_dir=_data_dir(),
                )
            elif request.action == "remove":
                confirm_packing_decision(
                    execution.id,
                    "ui-remove-{}".format(uuid.uuid4().hex),
                    decision,
                    accepted=False,
                    confirmed=True,
                    reason="Removed while reviewing the packing list.",
                    data_dir=_data_dir(),
                )
            else:
                assert replacement_item is not None
                if entry.container is None:
                    raise PackingValidationError(["this decision has no destination container"])
                plan_movement(
                    [replacement_item.id],
                    replacement_item.current_location,
                    entry.container,
                    data_dir=_data_dir(),
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
                    data_dir=_data_dir(),
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
                    data_dir=_data_dir(),
                )
            return _trip_detail_payload(trip_id)
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
