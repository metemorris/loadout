"""Confirmed packing execution routes."""

from dataclasses import replace
from typing import Any, Dict
import uuid

from fastapi import APIRouter, Depends, HTTPException

from inventory_toolkit.execution import (
    ExecutionValidationError,
    confirm_packing_decision,
    confirm_packing_decisions,
    create_trip_execution,
    load_trip_executions,
    record_execution_action,
)
from inventory_toolkit.loader import load_inventory
from inventory_toolkit.movement import MovementError, plan_movement
from inventory_toolkit.packing import (
    PackingPlanNotFoundError,
    PackingValidationError,
    confirm_packing_plan,
    save_packing_plan,
)
from inventory_toolkit.trips import TripNotFoundError

from ..context import ApiContext, get_context
from ..errors import domain_conflict
from ..packing_service import (
    editable_plan,
    execution_for_trip,
    replace_draft_entry,
    snapshot_with_plan,
)
from ..requests import PackingActionRequest, PackingBatchRequest
from ..serializers import item_payload, plain, trip_detail_payload


router = APIRouter(prefix="/api/trips")


@router.post("/{trip_id}/packing-batch")
def packing_batch(
    trip_id: str,
    request: PackingBatchRequest,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Apply an explicitly confirmed batch of physical packing decisions."""

    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Explicit confirmation is required")
    try:
        data_dir = context.durable_data_dir()
        snapshot = context.snapshot()
        trip = snapshot.trips.get(trip_id)
        plan = snapshot.plans.get(request.plan_id)
        if plan.trip != trip.id:
            raise PackingValidationError(["packing plan does not belong to this trip"])
        existing_execution = next(
            (
                value for value in snapshot.executions.executions if value.trip == trip.id
            ),
            None,
        )
        decisions = [
            "{}:{}".format(value.section, value.entry_index)
            for value in request.decisions
        ]

        if existing_execution is not None and existing_execution.packing_plan != plan.id:
            return _pack_draft_revision(
                request,
                decisions,
                plan,
                existing_execution,
                data_dir,
            )

        if plan.status == "draft":
            plan = confirm_packing_plan(plan.id, data_dir=data_dir)
            execution = existing_execution or _create_execution(trip.id, plan.id, data_dir)
        else:
            execution = existing_execution or _create_execution(trip.id, plan.id, data_dir)

        result = confirm_packing_decisions(
            execution.id,
            "ui-pack-{}".format(uuid.uuid4().hex),
            decisions,
            confirmed=True,
            reason="Packed selected items from the LoadOut trip packing surface.",
            data_dir=data_dir,
            execution_snapshot=execution,
            plan_snapshot=plan,
            inventory_snapshot=snapshot.inventory,
        )
        inventory = load_inventory(data_dir)
        moved_item_ids = {
            action.item
            for action in result.execution.actions
            if action.decision in result.applied_decisions and action.item is not None
        }
        return {
            "execution": plain(result.execution),
            "items": [
                item_payload(item) for item in inventory.items if item.id in moved_item_ids
            ],
            "appliedDecisions": list(result.applied_decisions),
            "failedDecisions": list(result.failed_decisions),
        }
    except (PackingPlanNotFoundError, TripNotFoundError, LookupError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PackingValidationError, ExecutionValidationError, MovementError) as exc:
        raise domain_conflict(exc) from exc


def _create_execution(trip_id: str, plan_id: str, data_dir: Any) -> Any:
    """Create the confirmed execution used by the packing UI workflow."""

    return create_trip_execution(
        "{}-execution".format(trip_id),
        trip_id,
        plan_id,
        confirmed=True,
        notes="Created from the LoadOut trip packing surface.",
        data_dir=data_dir,
    )


def _pack_draft_revision(
    request: PackingBatchRequest,
    decisions: list,
    plan: Any,
    execution: Any,
    data_dir: Any,
) -> Dict[str, Any]:
    """Pack selections from a draft revision into an existing trip execution."""

    if plan.status != "draft":
        raise ExecutionValidationError([
            "active trip execution belongs to a different packing plan"
        ])
    applied_decisions = []
    failed_decisions = []
    affected_item_ids = set()
    for requested, decision in zip(request.decisions, decisions):
        entries = plan.sections.get(requested.section)
        if (
            requested.section != "pack"
            or entries is None
            or requested.entry_index > len(entries)
        ):
            raise PackingValidationError([
                "unknown physical pack decision {!r}".format(decision)
            ])
        entry = entries[requested.entry_index - 1]
        if entry.item is None or entry.container is None:
            raise PackingValidationError([
                "decision {!r} is not a physical pack decision".format(decision)
            ])
        affected_item_ids.add(entry.item)
        item = load_inventory(data_dir).resolve_item(entry.item)
        if item.current_location == entry.container:
            failed_decisions.append(decision)
            continue
        try:
            execution = record_execution_action(
                execution.id,
                "ui-revision-pack-{}".format(uuid.uuid4().hex),
                "packed",
                item=entry.item,
                description=entry.requirement,
                leg=entry.leg,
                source=item.current_location,
                destination=entry.container,
                reason="Packed updated selection {} from draft plan {}.".format(
                    decision, plan.id
                ),
                confirmed=True,
                data_dir=data_dir,
            )
            applied_decisions.append(decision)
        except (ExecutionValidationError, MovementError):
            failed_decisions.append(decision)
            execution = load_trip_executions(data_dir).get(execution.id)
    inventory = load_inventory(data_dir)
    return {
        "execution": plain(execution),
        "items": [
            item_payload(item) for item in inventory.items if item.id in affected_item_ids
        ],
        "appliedDecisions": applied_decisions,
        "failedDecisions": failed_decisions,
    }


@router.post("/{trip_id}/packing-actions")
def packing_action(
    trip_id: str,
    request: PackingActionRequest,
    context: ApiContext = Depends(get_context),
) -> Dict[str, Any]:
    """Apply one explicitly confirmed pack, swap, or remove action."""

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
        data_dir = context.durable_data_dir()
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
        decision = "{}:{}".format(request.section, request.entry_index)

        if request.action == "remove" and (
            plan.status == "draft" or request.section == "wear_in_transit"
        ):
            editable = editable_plan(plan)
            updated = replace_draft_entry(
                editable, request.section, request.entry_index, None
            )
            saved = save_packing_plan(
                updated,
                replace_existing=updated.id == plan.id,
                data_dir=data_dir,
            )
            return trip_detail_payload(trip_id, snapshot_with_plan(snapshot, saved))

        replacement_item = None
        swap_notes = None
        if request.action == "swap":
            swap_notes = request.notes.strip()
            replacement_item = _resolve_replacement(
                request, entry, plan, snapshot.inventory
            )
            if plan.status == "draft" or request.section == "wear_in_transit":
                editable = editable_plan(plan)
                updated_entry = replace(
                    entry,
                    item=replacement_item.id,
                    source=replacement_item.current_location,
                    reason="Swap note: {} Original recommendation: {}".format(
                        swap_notes, entry.reason
                    ),
                )
                updated = replace_draft_entry(
                    editable, request.section, request.entry_index, updated_entry
                )
                saved = save_packing_plan(
                    updated,
                    replace_existing=updated.id == plan.id,
                    data_dir=data_dir,
                )
                return trip_detail_payload(
                    trip_id, snapshot_with_plan(snapshot, saved)
                )

        execution = execution_for_trip(
            trip.id, plan.id, data_dir=data_dir, create=False
        )
        if plan.status == "draft":
            plan = confirm_packing_plan(plan.id, data_dir=data_dir)
        execution = execution or execution_for_trip(
            trip.id, plan.id, data_dir=data_dir, create=True
        )
        if any(action.decision == decision for action in execution.actions):
            raise ExecutionValidationError(["this packing decision already has an outcome"])

        _record_packing_action(
            request.action,
            decision,
            entry,
            execution,
            replacement_item,
            swap_notes,
            data_dir,
        )
        return trip_detail_payload(trip_id, context.snapshot())
    except (PackingPlanNotFoundError, TripNotFoundError, LookupError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PackingValidationError, ExecutionValidationError, MovementError) as exc:
        raise domain_conflict(exc) from exc


def _resolve_replacement(
    request: PackingActionRequest,
    entry: Any,
    plan: Any,
    inventory: Any,
) -> Any:
    """Validate and return the physical replacement for a swap request."""

    if not request.replacement_item_id:
        raise PackingValidationError(["a replacement item is required for swap"])
    original = inventory.resolve_item(entry.item)
    replacement = inventory.resolve_item(request.replacement_item_id)
    if replacement.id == entry.item:
        raise PackingValidationError(["choose a different replacement item"])
    if replacement.type != original.type:
        raise PackingValidationError([
            "replacement item must have the same type as the original item"
        ])
    if any(
        candidate.item == replacement.id
        for section_entries in plan.sections.values()
        for candidate in section_entries
    ):
        raise PackingValidationError([
            "replacement item is already in this packing plan"
        ])
    return replacement


def _record_packing_action(
    action: str,
    decision: str,
    entry: Any,
    execution: Any,
    replacement_item: Any,
    swap_notes: Any,
    data_dir: Any,
) -> None:
    """Record the selected action through the execution domain service."""

    if action in {"pack", "remove"}:
        accepted = action == "pack"
        confirm_packing_decision(
            execution.id,
            "ui-{}-{}".format(action, uuid.uuid4().hex),
            decision,
            accepted=accepted,
            confirmed=True,
            reason=(
                "Packed from the LoadOut trip packing surface."
                if accepted else "Removed while reviewing the packing list."
            ),
            data_dir=data_dir,
        )
        return

    if replacement_item is None or swap_notes is None:
        raise PackingValidationError(["swap requires a replacement item and notes"])
    if entry.container is None:
        raise PackingValidationError(["this decision has no destination container"])
    plan_movement(
        [replacement_item.id],
        replacement_item.current_location,
        entry.container,
        data_dir=data_dir,
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
        data_dir=data_dir,
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
        data_dir=data_dir,
    )
