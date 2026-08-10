"""Deterministic context and readiness helpers for the Codex trip interview.

This module reports facts and unanswered questions. It deliberately does not
select possessions, recommend packing, infer answers, or move inventory.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Optional, Tuple

from .loader import load_inventory
from .models import PhysicalItem
from .requirements import ResolvedTripRequirements, resolve_trip_requirements
from .trips import Trip, TripLeg, load_trip

LAUNDRY_QUESTION_MINIMUM_STAY_DAYS = 4


@dataclass(frozen=True)
class MissingPlanningInformation:
    code: str
    scope: str
    question: str
    leg_id: Optional[str] = None


@dataclass(frozen=True)
class PlanningBlocker:
    code: str
    message: str


@dataclass(frozen=True)
class TripReadinessReport:
    trip_id: str
    ready: bool
    missing: Tuple[MissingPlanningInformation, ...]
    blockers: Tuple[PlanningBlocker, ...]


@dataclass(frozen=True)
class InventoryLocationContext:
    location_id: str
    items: Tuple[PhysicalItem, ...]
    definitions: int
    type_counts: Mapping[str, int]
    tag_counts: Mapping[str, int]


@dataclass(frozen=True)
class InventoryPlanningContext:
    departure_locations: Tuple[InventoryLocationContext, ...]
    luggage_contents: Mapping[str, Tuple[PhysicalItem, ...]]
    away_from_preferred: Tuple[PhysicalItem, ...]


@dataclass(frozen=True)
class TripPlanningContext:
    trip: Trip
    requirements: ResolvedTripRequirements
    inventory: InventoryPlanningContext
    readiness: TripReadinessReport


def _stay_days(trip: Trip, leg_index: int) -> int:
    """Return inclusive days at a leg's destination before the next departure."""

    leg = trip.legs[leg_index]
    final_day = (
        trip.legs[leg_index + 1].departure
        if leg_index + 1 < len(trip.legs)
        else trip.end_date
    )
    return max(1, (final_day - leg.arrival).days + 1)


def _leg_description(leg: TripLeg) -> str:
    return "leg {} ({} to {})".format(leg.id, leg.origin, leg.destination)


def report_missing_planning_information(
    trip_id: str, data_dir: Optional[Path] = None
) -> Tuple[MissingPlanningInformation, ...]:
    """Return only unanswered facts that materially gate future packing logic."""

    trip = load_trip(trip_id, data_dir)
    missing = []
    if not trip.luggage:
        missing.append(
            MissingPlanningInformation(
                code="trip.luggage",
                scope="trip",
                question="Which travel containers will be available for this trip?",
            )
        )

    planning_by_leg = {entry.leg: entry for entry in trip.planning.legs}
    for index, leg in enumerate(trip.legs):
        planning = planning_by_leg[leg.id]
        description = _leg_description(leg)
        if planning.activities_complete is None:
            missing.append(
                MissingPlanningInformation(
                    code="leg.{}.activities_complete".format(leg.id),
                    scope="leg",
                    leg_id=leg.id,
                    question=(
                        "Are all packing-relevant activities and events recorded for {}?"
                    ).format(description),
                )
            )
        elif planning.activities_complete is False:
            missing.append(
                MissingPlanningInformation(
                    code="leg.{}.activities_complete".format(leg.id),
                    scope="leg",
                    leg_id=leg.id,
                    question=(
                        "What packing-relevant activities or events still need to be recorded for {}?"
                    ).format(description),
                )
            )

        stay_days = _stay_days(trip, index)
        if (
            stay_days >= LAUNDRY_QUESTION_MINIMUM_STAY_DAYS
            and planning.laundry_available is None
        ):
            missing.append(
                MissingPlanningInformation(
                    code="leg.{}.laundry_available".format(leg.id),
                    scope="leg",
                    leg_id=leg.id,
                    question=(
                        "Will laundry be available during the {}-day stay after {}?"
                    ).format(stay_days, description),
                )
            )
    return tuple(missing)


def inspect_trip_readiness(
    trip_id: str, data_dir: Optional[Path] = None
) -> TripReadinessReport:
    trip = load_trip(trip_id, data_dir)
    blockers = []
    if trip.status in ("completed", "cancelled"):
        blockers.append(
            PlanningBlocker(
                code="trip.status",
                message=(
                    "Trip status {!r} is not eligible for packing preparation; "
                    "change status explicitly if that fact is no longer correct."
                ).format(trip.status),
            )
        )
    missing = report_missing_planning_information(trip_id, data_dir)
    return TripReadinessReport(
        trip_id=trip.id,
        ready=not missing and not blockers,
        missing=missing,
        blockers=tuple(blockers),
    )


def inspect_relevant_inventory_context(
    trip_id: str, data_dir: Optional[Path] = None
) -> InventoryPlanningContext:
    """Summarize possessions available at itinerary origins and in trip luggage."""

    trip = load_trip(trip_id, data_dir)
    inventory = load_inventory(data_dir)
    inventory_location_ids = {location.id for location in inventory.locations}
    departure_ids = []
    for leg in trip.legs:
        if leg.origin in inventory_location_ids and leg.origin not in departure_ids:
            departure_ids.append(leg.origin)

    location_contexts = []
    for location_id in departure_ids:
        items = inventory.list_at_location(location_id)
        type_counts = Counter(item.type for item in items)
        tag_counts = Counter(tag for item in items for tag in item.uses)
        location_contexts.append(
            InventoryLocationContext(
                location_id=location_id,
                items=items,
                definitions=len({item.definition_id for item in items}),
                type_counts=MappingProxyType(dict(sorted(type_counts.items()))),
                tag_counts=MappingProxyType(dict(sorted(tag_counts.items()))),
            )
        )

    luggage_contents = MappingProxyType(
        {
            luggage_id: inventory.container_contents(luggage_id)
            for luggage_id in trip.luggage
        }
    )
    return InventoryPlanningContext(
        departure_locations=tuple(location_contexts),
        luggage_contents=luggage_contents,
        away_from_preferred=inventory.items_away_from_preferred_location(),
    )


def load_trip_context(
    trip_id: str, data_dir: Optional[Path] = None
) -> TripPlanningContext:
    """Load all deterministic context needed for the Phase 5 interview."""

    return TripPlanningContext(
        trip=load_trip(trip_id, data_dir),
        requirements=resolve_trip_requirements(trip_id, data_dir),
        inventory=inspect_relevant_inventory_context(trip_id, data_dir),
        readiness=inspect_trip_readiness(trip_id, data_dir),
    )


def trip_is_ready_for_packing_recommendations(
    trip_id: str, data_dir: Optional[Path] = None
) -> bool:
    """Report readiness only; Phase 5 never generates a recommendation."""

    return inspect_trip_readiness(trip_id, data_dir).ready
