"""Trip reads and packing-plan edit routes."""

from dataclasses import replace
from typing import Any, Dict
import uuid

from fastapi import APIRouter, Depends, HTTPException

from inventory_toolkit.execution import (
    ExecutionValidationError,
    record_execution_action,
)
from inventory_toolkit.movement import MovementError, plan_movement
from inventory_toolkit.packing import (
    PackingPlanEntry,
    PackingPlanNotFoundError,
    PackingValidationError,
    save_packing_plan,
)
from inventory_toolkit.trips import TripNotFoundError, TripValidationError, update_trip

from ..context import ApiContext, get_context
from ..errors import domain_conflict
from ..packing_service import editable_plan, replace_draft_entry, snapshot_with_plan
from ..requests import (
    PackingContainerRequest,
    PackingPlanItemRequest,
    PackingUnpackRequest,
)
from ..serializers import item_payload, trip_detail_payload, trip_payload


router = APIRouter(prefix="/api/trips")
UNAVAILABLE_ITEM_STATUSES = {"missing", "lost", "loaned", "repair", "discarded"}
ACTIVE_EXECUTION_STATUSES = {"preparing", "in_progress", "reconciling"}


@router.get("")
def list_trips(context: ApiContext = Depends(get_context)) -> Dict[str, Any]:
    """Return trips with plan counts and their current execution summaries."""

    snapshot = context.snapshot()
    return {
        "trips": [
            {
                **trip_payload(trip),
                "planCount": sum(plan.trip == trip.id for plan in snapshot.plans.plans),
                "execution": next(
                    (
                        {"id": execution.id, "status": execution.status}
                        for execution in snapshot.executions.executions
                        if execution.trip == trip.id
                    ),
                    None,
                ),
            }
            for trip in snapshot.trips.trips
        ]
    }


@router.get("/{trip_id}")
def trip_detail(
    trip_id: str,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Return the complete planning and execution view for one trip."""

    return trip_detail_payload(trip_id, context.snapshot())


@router.get("/{trip_id}/packing-plans/{plan_id}/swap-candidates")
def swap_candidates(
    trip_id: str,
    plan_id: str,
    item_id: str,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Return available like-for-like items not already present in the plan."""

    try:
        snapshot = context.snapshot()
        trip = snapshot.trips.get(trip_id)
        plan = snapshot.plans.get(plan_id)
        if plan.trip != trip.id:
            raise PackingValidationError(["packing plan does not belong to this trip"])
        original = snapshot.inventory.resolve_item(item_id)
        planned_ids = {
            entry.item
            for entries in plan.sections.values()
            for entry in entries
            if entry.item is not None
        }
        candidates = sorted(
            (
                item for item in snapshot.inventory.items
                if item.id not in planned_ids
                and item.type == original.type
                and (item.status or "") not in UNAVAILABLE_ITEM_STATUSES
            ),
            key=lambda item: (
                item.current_location != original.current_location,
                item.name.lower(),
                item.id,
            ),
        )
        return {
            "items": [item_payload(item) for item in candidates],
            "count": len(candidates),
        }
    except (PackingPlanNotFoundError, TripNotFoundError, LookupError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PackingValidationError as exc:
        raise domain_conflict(exc) from exc


@router.post("/{trip_id}/packing-plan-items")
def add_packing_plan_item(
    trip_id: str,
    request: PackingPlanItemRequest,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Add one confirmed, available physical item to an editable plan."""

    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Explicit confirmation is required")
    try:
        snapshot = context.snapshot()
        trip = snapshot.trips.get(trip_id)
        plan = snapshot.plans.get(request.plan_id)
        if plan.trip != trip.id:
            raise PackingValidationError(["packing plan does not belong to this trip"])
        if request.section not in {"pack", "wear_in_transit"}:
            raise PackingValidationError([
                "items can only be added to pack or wear_in_transit"
            ])
        if request.section == "pack" and request.container not in trip.luggage:
            raise PackingValidationError(["container is not assigned to this trip"])
        if request.section == "wear_in_transit" and request.container is not None:
            raise PackingValidationError([
                "wear_in_transit items must not specify a container"
            ])
        item = snapshot.inventory.resolve_item(request.item_id)
        if (item.status or "") in UNAVAILABLE_ITEM_STATUSES:
            raise PackingValidationError(["item is not currently available for packing"])
        if any(
            entry.item == item.id
            for entries in plan.sections.values()
            for entry in entries
        ):
            raise PackingValidationError(["item is already in this packing plan"])

        editable = editable_plan(plan)
        sections = {name: list(entries) for name, entries in editable.sections.items()}
        sections[request.section].append(PackingPlanEntry(
            item=item.id,
            requirement="manual_addition",
            leg=trip.legs[0].id if trip.legs else None,
            container=request.container if request.section == "pack" else None,
            source=item.current_location,
            destination=None,
            quantity=1,
            reason=(request.reason or "Added manually to {} from the trip packing surface.".format(
                request.section.replace("_", " ")
            )).strip(),
        ))
        updated = replace(
            editable,
            sections={name: tuple(entries) for name, entries in sections.items()},
        )
        saved = save_packing_plan(
            updated,
            replace_existing=updated.id == plan.id,
            data_dir=context.durable_data_dir(),
        )
        return trip_detail_payload(trip.id, snapshot_with_plan(snapshot, saved))
    except (PackingPlanNotFoundError, TripNotFoundError, LookupError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PackingValidationError as exc:
        raise domain_conflict(exc) from exc


@router.post("/{trip_id}/packing-plan-containers")
def change_packing_plan_container(
    trip_id: str,
    request: PackingContainerRequest,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Change a plan assignment and record a transfer if the item was already packed."""

    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Explicit confirmation is required")
    try:
        snapshot = context.snapshot()
        trip = snapshot.trips.get(trip_id)
        plan = snapshot.plans.get(request.plan_id)
        if plan.trip != trip.id:
            raise PackingValidationError(["packing plan does not belong to this trip"])
        destination = snapshot.inventory.resolve_location(request.container)
        if destination.kind != "travel_container":
            raise PackingValidationError(["destination must be a travel container"])
        container_id = destination.id
        editable = editable_plan(plan)
        entries = editable.sections.get(request.section)
        if entries is None or request.entry_index > len(entries):
            raise PackingValidationError([
                "unknown packing decision '{}:{}'".format(
                    request.section, request.entry_index
                )
            ])
        entry = entries[request.entry_index - 1]
        if entry.item is None:
            raise PackingValidationError(["this packing decision has no physical item"])
        if entry.container == container_id:
            raise PackingValidationError(["choose a different container"])

        updated = replace_draft_entry(
            editable,
            request.section,
            request.entry_index,
            replace(entry, container=container_id),
        )
        item = snapshot.inventory.resolve_item(entry.item)
        execution = next(
            (
                value for value in snapshot.executions.executions
                if value.trip == trip.id and value.status in ACTIVE_EXECUTION_STATUSES
            ),
            None,
        )
        was_packed = execution is not None and any(
            action.item == item.id
            and action.kind == "packed"
            and action.state == "applied"
            for action in execution.actions
        )
        data_dir = context.durable_data_dir()
        if was_packed and item.current_location != container_id:
            plan_movement(
                [item.id],
                item.current_location,
                container_id,
                data_dir=data_dir,
                reason="Trip packing container change preview.",
            )
        if container_id not in trip.luggage:
            update_trip(
                trip.id,
                luggage=(*trip.luggage, container_id),
                data_dir=data_dir,
            )
        save_packing_plan(
            updated,
            replace_existing=updated.id == plan.id,
            data_dir=data_dir,
        )
        if was_packed and item.current_location != container_id:
            record_execution_action(
                execution.id,
                "ui-container-transfer-{}".format(uuid.uuid4().hex),
                "transferred",
                item=item.id,
                description="Changed packing container",
                source=item.current_location,
                destination=container_id,
                reason="Changed the assigned bag from the trip packing surface.",
                confirmed=True,
                data_dir=data_dir,
            )
        return trip_detail_payload(trip.id, context.snapshot())
    except (PackingPlanNotFoundError, TripNotFoundError, LookupError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PackingValidationError, ExecutionValidationError, MovementError, TripValidationError) as exc:
        raise domain_conflict(exc) from exc


@router.post("/{trip_id}/packing-unpack")
def unpack_packing_item(
    trip_id: str,
    request: PackingUnpackRequest,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Record a confirmed return from trip luggage to the pre-pack source."""

    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Explicit confirmation is required")
    try:
        snapshot = context.snapshot()
        trip = snapshot.trips.get(trip_id)
        plan = snapshot.plans.get(request.plan_id)
        if plan.trip != trip.id:
            raise PackingValidationError(["packing plan does not belong to this trip"])
        entries = plan.sections.get(request.section)
        if entries is None or request.entry_index > len(entries):
            raise PackingValidationError([
                "unknown packing decision '{}:{}'".format(
                    request.section, request.entry_index
                )
            ])
        entry = entries[request.entry_index - 1]
        if entry.item is None:
            raise PackingValidationError(["this packing decision has no physical item"])
        execution = next(
            (
                value for value in snapshot.executions.executions
                if value.trip == trip.id and value.status in ACTIVE_EXECUTION_STATUSES
            ),
            None,
        )
        if execution is None:
            raise ExecutionValidationError(["trip has no active execution"])
        item = snapshot.inventory.resolve_item(entry.item)
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
            raise ExecutionValidationError(["packed item has no recorded pre-pack source"])
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
            data_dir=context.durable_data_dir(),
        )
        return trip_detail_payload(trip.id, context.snapshot())
    except (PackingPlanNotFoundError, TripNotFoundError, LookupError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PackingValidationError, ExecutionValidationError, MovementError) as exc:
        raise domain_conflict(exc) from exc
