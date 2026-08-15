"""Translate domain read models into stable web-facing payloads."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from types import MappingProxyType
from typing import Any, Dict, Mapping, Sequence

from fastapi import HTTPException

from inventory_toolkit.models import PhysicalItem
from inventory_toolkit.repository import CatalogSnapshot
from inventory_toolkit.trips import TripNotFoundError

from .catalog import categories, category_for


def plain(value: Any) -> Any:
    """Recursively convert immutable domain values to JSON-compatible values."""

    if isinstance(value, (MappingProxyType, Mapping)):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    if is_dataclass(value):
        return {field.name: plain(getattr(value, field.name)) for field in fields(value)}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def item_payload(item: PhysicalItem) -> Dict[str, Any]:
    """Serialize one trackable physical item for the web client."""

    color = item.attributes.get("color")
    if isinstance(color, tuple):
        color = color[0] if color else None
    return {
        "id": item.id,
        "definitionId": item.definition_id,
        "name": item.name,
        "type": item.type,
        "category": category_for(item.type).id,
        "color": color,
        "attributes": plain(item.attributes),
        "uses": list(item.uses),
        "currentLocation": item.current_location,
        "preferredLocation": item.preferred_location,
        "condition": item.condition,
        "status": item.status,
        "notes": item.notes,
        "movements": [plain(movement) for movement in item.movements],
        "estimatedSpaceLiters": item.estimated_space_liters,
        "estimatedWeightKg": item.estimated_weight_kg,
    }


def location_payload(location: Any, items: Sequence[PhysicalItem]) -> Dict[str, Any]:
    """Serialize a location plus derived inventory and capacity totals."""

    grouped: Dict[str, int] = {}
    for item in items:
        category_id = category_for(item.type).id
        grouped[category_id] = grouped.get(category_id, 0) + 1
    estimated_space = round(sum(item.estimated_space_liters for item in items), 2)
    estimated_weight = round(sum(item.estimated_weight_kg for item in items), 2)
    capacity = None
    if location.kind == "travel_container":
        capacity = {
            "capacityLiters": location.capacity_liters,
            "maxLoadKg": location.max_load_kg,
            "estimatedUsedSpaceLiters": estimated_space,
            "estimatedLoadKg": estimated_weight,
            "remainingSpaceLiters": (
                round(location.capacity_liters - estimated_space, 2)
                if location.capacity_liters is not None else None
            ),
            "remainingLoadKg": (
                round(location.max_load_kg - estimated_weight, 2)
                if location.max_load_kg is not None else None
            ),
            "spaceUtilizationPercent": (
                round(estimated_space / location.capacity_liters * 100, 1)
                if location.capacity_liters is not None else None
            ),
            "loadUtilizationPercent": (
                round(estimated_weight / location.max_load_kg * 100, 1)
                if location.max_load_kg is not None else None
            ),
            "basis": "rough_item_type_defaults",
        }
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
        "capacity": capacity,
    }


def trip_payload(trip: Any) -> Dict[str, Any]:
    """Serialize one trip without its related plans and executions."""

    return {
        "id": trip.id,
        "name": trip.name,
        "status": trip.status,
        "startDate": trip.start_date.isoformat(),
        "endDate": trip.end_date.isoformat(),
        "durationDays": trip.duration_days,
        "places": [plain(place) for place in trip.places],
        "legs": [plain(leg) for leg in trip.legs],
        "luggage": list(trip.luggage),
        "attachments": [plain(attachment) for attachment in trip.attachments],
        "planning": plain(trip.planning),
        "notes": trip.notes,
    }


def trip_detail_payload(trip_id: str, snapshot: CatalogSnapshot) -> Dict[str, Any]:
    """Serialize a trip and every plan, execution, container, and referenced item."""

    inventory, trip_catalog, plan_catalog, execution_catalog = snapshot.as_tuple()
    try:
        trip = trip_catalog.get(trip_id)
    except TripNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    plans = [plan for plan in plan_catalog.plans if plan.trip == trip.id]
    executions = [
        execution for execution in execution_catalog.executions if execution.trip == trip.id
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
        "trip": trip_payload(trip),
        "containers": [
            location_payload(
                inventory.resolve_location(container_id),
                inventory.container_contents(container_id),
            )
            for container_id in trip.luggage
        ],
        "plans": [plain(plan) for plan in plans],
        "executions": [plain(execution) for execution in executions],
        "items": [
            item_payload(item) for item in inventory.items if item.id in referenced_item_ids
        ],
    }


def category_payloads() -> list:
    """Return the ordered presentation catalog for API responses."""

    return [category.payload() for category in categories()]


def grouped_item_payloads(items: Sequence[PhysicalItem]) -> list:
    """Group serialized items using the shared presentation catalog."""

    groups = []
    for category in categories():
        category_items = [
            item_payload(item) for item in items if category_for(item.type).id == category.id
        ]
        if category_items:
            groups.append({
                **category.payload(),
                "count": len(category_items),
                "items": category_items,
            })
    return groups
