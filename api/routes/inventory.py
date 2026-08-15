"""Health, inventory-query, location, and physical-movement routes."""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query as ApiQuery

from inventory_toolkit.models import Query
from inventory_toolkit.movement import MovementError, move_items, plan_movement

from ..context import ApiContext, get_context
from ..requests import MovementRequest
from ..serializers import (
    category_payloads,
    grouped_item_payloads,
    item_payload,
    location_payload,
    plain,
)


router = APIRouter(prefix="/api")


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
