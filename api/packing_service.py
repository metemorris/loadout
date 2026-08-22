"""Small API-specific orchestration helpers for packing-plan edits."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from inventory_toolkit.execution import (
    ExecutionValidationError,
    create_trip_execution,
    load_trip_executions,
)
from inventory_toolkit.packing import (
    PackingPlan,
    PackingPlanCatalog,
    PackingPlanEntry,
    PackingValidationError,
)
from inventory_toolkit.repository import CatalogSnapshot


def execution_for_trip(
    trip_id: str,
    plan_id: str,
    *,
    data_dir: Path,
    create: bool = False,
) -> Any:
    """Return a trip execution and optionally create it after explicit confirmation."""

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


def replace_draft_entry(
    plan: PackingPlan,
    section: str,
    entry_index: int,
    replacement: Optional[PackingPlanEntry],
) -> PackingPlan:
    """Return a draft plan with one indexed entry replaced or removed."""

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


def editable_plan(plan: PackingPlan) -> PackingPlan:
    """Return a draft plan, creating a revision of a confirmed plan when needed."""

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
        notes="UI revision of {}. {}".format(plan.id, plan.notes or "").strip(),
    )


def snapshot_with_plan(
    snapshot: CatalogSnapshot,
    updated_plan: PackingPlan,
) -> CatalogSnapshot:
    """Return the already-loaded catalog with one saved plan added or replaced."""

    plans = list(snapshot.plans.plans)
    for index, plan in enumerate(plans):
        if plan.id == updated_plan.id:
            plans[index] = updated_plan
            break
    else:
        plans.append(updated_plan)
    return CatalogSnapshot(
        inventory=snapshot.inventory,
        trips=snapshot.trips,
        plans=PackingPlanCatalog(plans),
        executions=snapshot.executions,
    )
