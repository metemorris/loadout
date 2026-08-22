"""Health, inventory-query, location, and physical-movement routes."""

import re
import unicodedata
from typing import Any, Dict, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query as ApiQuery

from inventory_toolkit.models import Query
from inventory_toolkit.movement import (
    MovementError,
    move_items,
    plan_movement,
    register_inventory_item,
)
from inventory_toolkit.paths import default_data_directory

from ..context import ApiContext, get_context
from ..requests import InventoryItemRequest, MovementRequest
from ..serializers import (
    category_payloads,
    grouped_item_payloads,
    item_payload,
    location_payload,
    plain,
)


router = APIRouter(prefix="/api")


def _new_item_id(name: str, preferred_location: str, inventory: Any) -> str:
    """Build a readable, collision-free ID without exposing IDs in the UI."""

    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-") or "item"
    base = "{}-{}".format(preferred_location, slug)
    taken = {item.id for item in inventory.items}
    taken.update(definition.id for definition in inventory.definitions)
    taken.update(
        source.id for definition in inventory.definitions for source in definition.sources
    )
    if base not in taken:
        return base
    suffix = 2
    while "{}-{:02d}".format(base, suffix) in taken:
        suffix += 1
    return "{}-{:02d}".format(base, suffix)


@router.get("/health")
def health(context: ApiContext = Depends(get_context)) -> Dict[str, str]:
    """Validate catalog readability and report local-service health."""

    context.snapshot()
    return {"status": "ok", "mode": "local"}


@router.get("/overview")
def overview(context: ApiContext = Depends(get_context)) -> Dict[str, Any]:
    """Return locations, aggregate counts, and shared category metadata."""

    inventory = context.snapshot().inventory
    locations = [
        location_payload(location, inventory.list_at_location(location.id))
        for location in inventory.locations
    ]
    return {
        "locations": locations,
        "totals": {
            "items": len(inventory.items),
            "definitions": len(inventory.definitions),
            "homes": sum(location.kind == "home" for location in inventory.locations),
            "containers": sum(
                location.kind == "travel_container" for location in inventory.locations
            ),
        },
        "categories": category_payloads(),
    }


@router.get("/locations/{location_id}")
def location_detail(
    location_id: str,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Return one exact location with its physical items grouped for display."""

    inventory = context.snapshot().inventory
    try:
        location = inventory.resolve_location(location_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    items = inventory.list_at_location(location.id)
    return {
        "location": location_payload(location, items),
        "categories": grouped_item_payloads(items),
    }


@router.get("/items/{item_id}")
def item_detail(
    item_id: str,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Return one physical inventory item by its exact ID."""

    try:
        return item_payload(context.snapshot().inventory.resolve_item(item_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/inventory")
def query_inventory(
    text: Optional[str] = None,
    location: Optional[str] = None,
    item_type: Optional[str] = ApiQuery(default=None, alias="type"),
    status: Optional[str] = None,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Search physical inventory with optional text, location, type, and status filters."""

    inventory = context.snapshot().inventory
    try:
        items = inventory.find(
            Query(text=text, location=location, item_type=item_type, status=status)
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": [item_payload(item) for item in items], "count": len(items)}


@router.get("/inventory/options")
def inventory_options(context: ApiContext = Depends(get_context)) -> Dict[str, Any]:
    """Return schema-backed choices used by the add-item form."""

    repository_dir = context.repository.data_dir if context.repository is not None else None
    schema_path = (repository_dir or default_data_directory()) / "schema.yaml"
    with schema_path.open("r", encoding="utf-8") as handle:
        schema = yaml.safe_load(handle)
    definitions = schema.get("definitions", {}) if isinstance(schema, dict) else {}
    return {
        "itemTypes": definitions.get("allowed_types", []),
        "uses": definitions.get("allowed_uses", []),
    }


@router.post("/inventory/items")
def create_inventory_item(
    request: InventoryItemRequest,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Register one confirmed physical possession in the durable inventory."""

    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Explicit confirmation is required")
    inventory = context.snapshot().inventory
    try:
        current_location = inventory.resolve_location(request.current_location).id
        preferred_location = inventory.resolve_location(request.preferred_location).id
        item_id = _new_item_id(request.name, preferred_location, inventory)
        result = register_inventory_item(
            item_id,
            item_id,
            current_location,
            confirmed=True,
            data_dir=context.durable_data_dir(),
            name=request.name,
            item_type=request.item_type,
            attributes=request.attributes,
            uses=request.uses,
            preferred_location=preferred_location,
            condition=request.condition,
            notes=request.notes,
        )
    except (MovementError, LookupError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"item": item_payload(result.item), "applied": result.applied}


@router.post("/movements/preview")
def preview_movement(
    request: MovementRequest,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Validate a proposed physical move without mutating inventory."""

    try:
        if context.repository is not None:
            plan = context.repository.preview_movement(
                request.item_ids,
                request.source,
                request.destination,
                reason=request.reason,
                update_preferred=request.update_preferred,
            )
        else:
            plan = plan_movement(
                request.item_ids,
                request.source,
                request.destination,
                data_dir=context.durable_data_dir(),
                reason=request.reason,
                update_preferred=request.update_preferred,
            )
    except (MovementError, LookupError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"movement": plain(plan), "requiresConfirmation": True}


@router.post("/movements/confirm")
def confirm_movement(
    request: MovementRequest,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Apply an explicitly confirmed move and return the resulting item states."""

    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Explicit confirmation is required")
    try:
        if context.repository is not None:
            result = context.repository.confirm_movement(
                request.item_ids,
                request.source,
                request.destination,
                reason=request.reason,
                update_preferred=request.update_preferred,
                confirmed=True,
            )
        else:
            result = move_items(
                request.item_ids,
                request.source,
                request.destination,
                data_dir=context.durable_data_dir(),
                reason=request.reason,
                update_preferred=request.update_preferred,
                confirmed=True,
            )
    except (MovementError, LookupError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    inventory = context.snapshot().inventory
    return {
        "movement": plain(result.plan),
        "applied": result.applied,
        "items": [
            item_payload(inventory.resolve_item(item_id)) for item_id in request.item_ids
        ],
    }
