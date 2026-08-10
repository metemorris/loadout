"""Command-line presentation for the inventory toolkit."""

import argparse
import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .loader import InventoryValidationError, load_inventory
from .execution import (
    RECONCILIATION_TOPICS,
    TripExecution,
    begin_trip_execution,
    cancel_trip_execution,
    complete_trip_execution,
    confirm_packing_decision,
    create_trip_execution,
    item_execution_history,
    load_trip_execution,
    load_trip_executions,
    record_execution_action,
    record_purchased_item,
    review_reconciliation,
)
from .models import InventoryCount, ItemDefinition, Location, LocationSummary, Movement, PhysicalItem, Query
from .movement import ConfirmationRequiredError, MovementPlan, move_items
from .packing import (
    PLAN_SECTIONS,
    PackingCandidate,
    PackingPlan,
    PackingValidationError,
    RequirementMatch,
    WholeTripPackingContext,
    compile_packing_context,
    confirm_packing_plan,
    load_packing_plan,
    load_packing_plan_document,
    load_packing_plans,
    load_requirement_matchers,
    match_trip_requirements,
    save_packing_plan,
)
from .planning import (
    InventoryPlanningContext,
    TripPlanningContext,
    TripReadinessReport,
    inspect_trip_readiness,
    load_trip_context,
)
from .requirements import (
    CategoryRequirement,
    RequirementOverrides,
    RequirementTemplate,
    ResolvedTripRequirements,
    load_requirement_templates,
    resolve_trip_requirements,
)
from .trips import (
    Trip,
    TripAttachment,
    TripLeg,
    TripPlace,
    TripWeather,
    add_trip_attachment,
    create_trip,
    load_trip,
    load_trips,
    set_trip_status,
    update_trip,
    update_trip_leg_planning,
)


def _definition_dict(definition: ItemDefinition) -> Dict[str, Any]:
    return {
        "id": definition.id,
        "name": definition.name,
        "type": definition.type,
        "attributes": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in definition.attributes.items()
        },
        "use": list(definition.uses),
        "notes": definition.notes,
        "sources": [
            {
                "id": source.id,
                "original_location": source.original_location,
                "original_quantity": source.original_quantity,
                "original_condition": source.original_condition,
                "notes": source.notes,
            }
            for source in definition.sources
        ],
    }


def _item_dict(item: PhysicalItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "definition": item.definition_id,
        "name": item.name,
        "type": item.type,
        "current_location": item.current_location,
        "preferred_location": item.preferred_location,
        "condition": item.condition,
        "status": item.status,
        "attributes": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in item.attributes.items()
        },
        "use": list(item.uses),
        "definition_notes": item.definition_notes,
        "notes": item.notes,
        "source": item.source,
        "movements": [_movement_dict(movement) for movement in item.movements],
    }


def _movement_dict(movement: Movement) -> Dict[str, Any]:
    return {
        "timestamp": movement.timestamp,
        "source": movement.source,
        "destination": movement.destination,
        "reason": movement.reason,
    }


def _movement_plan_dict(plan: MovementPlan) -> Dict[str, Any]:
    return {
        "item_ids": list(plan.item_ids),
        "source": plan.source,
        "destination": plan.destination,
        "timestamp": plan.timestamp,
        "reason": plan.reason,
        "update_preferred": plan.update_preferred,
    }


def _trip_dict(trip: Trip) -> Dict[str, Any]:
    return {
        "id": trip.id,
        "name": trip.name,
        "status": trip.status,
        "start_date": trip.start_date.isoformat(),
        "end_date": trip.end_date.isoformat(),
        "duration_days": trip.duration_days,
        "places": [
            {"id": place.id, "name": place.name, "notes": place.notes}
            for place in trip.places
        ],
        "legs": [_leg_dict(leg) for leg in trip.legs],
        "luggage": list(trip.luggage),
        "attachments": [
            {
                "id": attachment.id,
                "kind": attachment.kind,
                "name": attachment.name,
                "type": attachment.type,
                "leg": attachment.leg,
                "location": attachment.location,
                "date": attachment.date.isoformat() if attachment.date else None,
                "frequency": attachment.frequency,
                "requirements": {
                    "add": [_category_requirement_dict(value) for value in attachment.requirements.add],
                    "add_optional": [
                        _category_requirement_dict(value)
                        for value in attachment.requirements.add_optional
                    ],
                    "remove": list(attachment.requirements.remove),
                    "remove_optional": list(attachment.requirements.remove_optional),
                },
                "notes": attachment.notes,
            }
            for attachment in trip.attachments
        ],
        "planning": {
            "legs": [
                {
                    "leg": entry.leg,
                    "activities_complete": entry.activities_complete,
                    "laundry_available": entry.laundry_available,
                    "weather": {
                        "summary": entry.weather.summary,
                        "low_c": entry.weather.low_c,
                        "high_c": entry.weather.high_c,
                        "precipitation": entry.weather.precipitation,
                        "source": entry.weather.source,
                        "checked_at": entry.weather.checked_at,
                    },
                    "context": list(entry.context),
                    "notes": entry.notes,
                }
                for entry in trip.planning.legs
            ],
            "notes": trip.planning.notes,
        },
        "notes": trip.notes,
    }


def _category_requirement_dict(requirement: CategoryRequirement) -> Dict[str, Any]:
    return {"category": requirement.category, "count": requirement.count}


def _template_dict(template: RequirementTemplate) -> Dict[str, Any]:
    return {
        "id": template.id,
        "name": template.name,
        "required": [_category_requirement_dict(value) for value in template.required],
        "optional": [_category_requirement_dict(value) for value in template.optional],
        "notes": template.notes,
    }


def _resolved_requirements_dict(resolved: ResolvedTripRequirements) -> Dict[str, Any]:
    return {
        "trip_id": resolved.trip_id,
        "required": [
            {"category": value.category, "count": value.count, "sources": list(value.sources)}
            for value in resolved.required
        ],
        "optional": [
            {"category": value.category, "count": value.count, "sources": list(value.sources)}
            for value in resolved.optional
        ],
        "activities": [
            {
                "attachment_id": activity.attachment_id,
                "name": activity.name,
                "kind": activity.kind,
                "template": activity.template_id,
                "frequency": activity.frequency,
                "required": [_category_requirement_dict(value) for value in activity.required],
                "optional": [_category_requirement_dict(value) for value in activity.optional],
            }
            for activity in resolved.activities
        ],
    }


def _readiness_dict(report: TripReadinessReport) -> Dict[str, Any]:
    return {
        "trip_id": report.trip_id,
        "ready_for_packing_recommendations": report.ready,
        "missing": [
            {
                "code": value.code,
                "scope": value.scope,
                "leg": value.leg_id,
                "question": value.question,
            }
            for value in report.missing
        ],
        "blockers": [
            {"code": value.code, "message": value.message}
            for value in report.blockers
        ],
    }


def _inventory_planning_dict(context: InventoryPlanningContext) -> Dict[str, Any]:
    return {
        "departure_locations": [
            {
                "location": location.location_id,
                "definitions": location.definitions,
                "physical_items": len(location.items),
                "item_ids": [item.id for item in location.items],
                "by_type": dict(location.type_counts),
                "by_tag": dict(location.tag_counts),
            }
            for location in context.departure_locations
        ],
        "luggage_contents": {
            location: [item.id for item in items]
            for location, items in context.luggage_contents.items()
        },
        "away_from_preferred": [item.id for item in context.away_from_preferred],
    }


def _planning_context_dict(context: TripPlanningContext) -> Dict[str, Any]:
    return {
        "trip": _trip_dict(context.trip),
        "requirements": _resolved_requirements_dict(context.requirements),
        "inventory": _inventory_planning_dict(context.inventory),
        "readiness": _readiness_dict(context.readiness),
    }


def _candidate_dict(candidate: PackingCandidate) -> Dict[str, Any]:
    last_movement = candidate.movement_history[-1] if candidate.movement_history else None
    return {
        "item": candidate.item_id,
        "definition": candidate.definition_id,
        "name": candidate.name,
        "type": candidate.item_type,
        "attributes": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in candidate.attributes.items()
        },
        "use": list(candidate.uses),
        "current_location": candidate.current_location,
        "preferred_location": candidate.preferred_location,
        "condition": candidate.condition,
        "status": candidate.status,
        "available": candidate.available,
        "movement_count": len(candidate.movement_history),
        "last_movement": _movement_dict(last_movement) if last_movement else None,
        "prior_plan_history": [
            {
                "plan": value.plan_id,
                "trip": value.trip_id,
                "created_at": value.created_at,
                "sections": list(value.sections),
            }
            for value in candidate.prior_plan_history
        ],
        "execution_history": [
            {
                "trip": value.trip_id,
                "execution": value.execution_id,
                "kind": value.kind,
                "state": value.state,
                "timestamp": value.timestamp,
            }
            for value in candidate.execution_history
        ],
    }


def _requirement_match_dict(
    match: RequirementMatch, candidate_limit: Optional[int] = None
) -> Dict[str, Any]:
    candidates = match.candidates if candidate_limit is None else match.candidates[:candidate_limit]
    return {
        "category": match.category,
        "requested_count": match.requested_count,
        "optional": match.optional,
        "sources": list(match.sources),
        "use_locations": list(match.use_locations),
        "available_quantity": match.available_quantity,
        "raw_shortfall": match.raw_shortfall,
        "at_origin": [value.item_id for value in match.at_origin],
        "already_at_destinations": [value.item_id for value in match.already_at_destinations],
        "transfer_candidates": [value.item_id for value in match.transfer_candidates],
        "candidates": [_candidate_dict(value) for value in candidates],
        "candidate_count": len(match.candidates),
        "candidates_truncated": len(match.candidates) - len(candidates),
    }


def _whole_trip_context_dict(context: WholeTripPackingContext) -> Dict[str, Any]:
    return {
        "trip": {
            "id": context.trip_id,
            "name": context.trip_name,
            "status": context.status,
            "start_date": context.start_date,
            "end_date": context.end_date,
            "luggage": list(context.luggage),
        },
        "legs": [
            {
                "id": leg.id,
                "origin": leg.origin,
                "destination": leg.destination,
                "departure": leg.departure,
                "arrival": leg.arrival,
                "laundry_available": leg.laundry_available,
                "weather": dict(leg.weather),
                "context": list(leg.context),
                "attachments": list(leg.attachments),
            }
            for leg in context.legs
        ],
        "requirements": _resolved_requirements_dict(context.requirements),
        "matches": [_requirement_match_dict(value, 12) for value in context.matches],
        "unmet_required": [value.category for value in context.unmet_required],
        "luggage_contents": {
            key: list(value) for key, value in context.luggage_contents.items()
        },
        "readiness": _readiness_dict(context.readiness),
        "context_warnings": list(context.context_warnings),
    }


def _packing_plan_dict(plan: PackingPlan) -> Dict[str, Any]:
    return {
        "id": plan.id,
        "trip": plan.trip,
        "status": plan.status,
        "created_at": plan.created_at,
        "sections": {
            section: [
                {
                    "item": entry.item,
                    "requirement": entry.requirement,
                    "leg": entry.leg,
                    "container": entry.container,
                    "source": entry.source,
                    "destination": entry.destination,
                    "quantity": entry.quantity,
                    "reason": entry.reason,
                }
                for entry in plan.sections.get(section, ())
            ]
            for section in PLAN_SECTIONS
        },
        "notes": plan.notes,
    }


def _execution_dict(execution: TripExecution) -> Dict[str, Any]:
    return {
        "id": execution.id,
        "trip": execution.trip,
        "packing_plan": execution.packing_plan,
        "status": execution.status,
        "created_at": execution.created_at,
        "actions": [
            {
                "id": action.id,
                "timestamp": action.timestamp,
                "kind": action.kind,
                "item": action.item,
                "description": action.description,
                "decision": action.decision,
                "leg": action.leg,
                "source": action.source,
                "destination": action.destination,
                "reason": action.reason,
                "state": action.state,
                "states": [
                    {"timestamp": state.timestamp, "status": state.status, "notes": state.notes}
                    for state in action.states
                ],
            }
            for action in execution.actions
        ],
        "reconciliation": {
            **{
                topic: getattr(execution.reconciliation, topic)
                for topic in RECONCILIATION_TOPICS
            },
            "ready": execution.reconciliation.ready,
            "notes": execution.reconciliation.notes,
            "completed_at": execution.reconciliation.completed_at,
        },
        "notes": execution.notes,
    }


def _leg_dict(leg: TripLeg) -> Dict[str, Any]:
    return {
        "id": leg.id,
        "origin": leg.origin,
        "destination": leg.destination,
        "departure": leg.departure.isoformat(),
        "arrival": leg.arrival.isoformat(),
        "transport_notes": leg.transport_notes,
    }


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("date must use YYYY-MM-DD: {!r}".format(value))


def _parse_leg(value: str) -> TripLeg:
    parts = value.split(":", 5)
    if len(parts) not in (5, 6) or not all(part.strip() for part in parts[:5]):
        raise ValueError(
            "leg must use ID:ORIGIN:DESTINATION:DEPARTURE:ARRIVAL[:TRANSPORT_NOTES]"
        )
    return TripLeg(
        id=parts[0].strip(),
        origin=parts[1].strip(),
        destination=parts[2].strip(),
        departure=_parse_date(parts[3].strip()),
        arrival=_parse_date(parts[4].strip()),
        transport_notes=parts[5].strip() if len(parts) == 6 and parts[5].strip() else None,
    )


def _parse_place(value: str) -> TripPlace:
    if "=" not in value:
        raise ValueError("place must use ID=NAME")
    place_id, name = value.split("=", 1)
    if not place_id.strip() or not name.strip():
        raise ValueError("place must use non-empty ID=NAME")
    return TripPlace(id=place_id.strip(), name=name.strip())


def _parse_yes_no(value: str) -> bool:
    return value == "yes"


def _parse_category_requirement(value: str) -> CategoryRequirement:
    category, separator, raw_count = value.partition("=")
    if not category.strip():
        raise ValueError("requirement must use CATEGORY or CATEGORY=COUNT")
    count = 1
    if separator:
        try:
            count = int(raw_count)
        except ValueError:
            raise ValueError("requirement count must be a positive integer")
        if count <= 0:
            raise ValueError("requirement count must be a positive integer")
    return CategoryRequirement(category.strip(), count)


def _location_dict(location: Location) -> Dict[str, Any]:
    return {
        "id": location.id,
        "name": location.name,
        "kind": location.kind,
        "city": location.city,
        "region": location.region,
        "country": location.country,
        "aliases": list(location.aliases),
        "notes": location.notes,
    }


def _count_dict(count: InventoryCount) -> Dict[str, int]:
    return {"definitions": count.definitions, "physical_items": count.physical_items}


def _summary_dict(summary: LocationSummary) -> Dict[str, Any]:
    return {
        "location": _location_dict(summary.location),
        "total": _count_dict(summary.total),
        "by_type": {key: _count_dict(value) for key, value in summary.by_type.items()},
        "by_use": {key: _count_dict(value) for key, value in summary.by_use.items()},
        "by_condition": {key: _count_dict(value) for key, value in summary.by_condition.items()},
        "by_status": {key: _count_dict(value) for key, value in summary.by_status.items()},
    }


def _print_items(items: Sequence[PhysicalItem]) -> None:
    if not items:
        print("No matching physical items.")
        return
    print("CURRENT          PREFERRED        TYPE                  NAME [PHYSICAL ID -> DEFINITION]")
    print("-" * 110)
    for item in items:
        print(
            "{:<16} {:<16} {:<21} {} [{} -> {}]".format(
                item.current_location,
                item.preferred_location,
                item.type,
                item.name,
                item.id,
                item.definition_id,
            )
        )


def _print_definitions(definitions: Sequence[ItemDefinition]) -> None:
    if not definitions:
        print("No matching definitions.")
        return
    print("TYPE                  NAME [DEFINITION ID]")
    print("-" * 80)
    for definition in definitions:
        print("{:<21} {} [{}]".format(definition.type, definition.name, definition.id))


def _print_summary(summary: LocationSummary) -> None:
    location = summary.location
    place = ", ".join(part for part in (location.city, location.region, location.country) if part)
    suffix = " — {}".format(place) if place else ""
    print("{} ({}, {}){}".format(location.name, location.id, location.kind, suffix))
    print("Definitions: {}  Physical items: {}".format(summary.total.definitions, summary.total.physical_items))
    print("\nBy type")
    for item_type, count in summary.by_type.items():
        print("  {:<20} {:>3} definitions  {:>4} items".format(item_type, count.definitions, count.physical_items))
    if summary.by_use:
        print("\nBy use")
        for use, count in summary.by_use.items():
            print("  {:<20} {:>3} definitions  {:>4} items".format(use, count.definitions, count.physical_items))
    if summary.by_condition:
        print("\nRecorded conditions")
        for condition, count in summary.by_condition.items():
            print("  {:<20} {:>3} definitions  {:>4} items".format(condition, count.definitions, count.physical_items))
    if summary.by_status:
        print("\nRecorded statuses")
        for status, count in summary.by_status.items():
            print("  {:<20} {:>3} definitions  {:>4} items".format(status, count.definitions, count.physical_items))


def _parse_attributes(values: Iterable[str]) -> Mapping[str, str]:
    attributes: Dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("attribute must use KEY=VALUE syntax: {!r}".format(value))
        key, expected = value.split("=", 1)
        if not key.strip() or not expected.strip():
            raise ValueError("attribute must use non-empty KEY=VALUE syntax: {!r}".format(value))
        attributes[key.strip()] = expected.strip()
    return attributes


def _query_from_args(args: argparse.Namespace) -> Query:
    return Query(
        location=getattr(args, "location", None),
        preferred_location=getattr(args, "preferred_location", None),
        name=getattr(args, "name", None),
        item_type=getattr(args, "item_type", None),
        attributes=_parse_attributes(getattr(args, "attribute", []) or []),
        uses=tuple(getattr(args, "uses", []) or []),
        condition=getattr(args, "condition", None),
        status=getattr(args, "status", None),
        text=getattr(args, "text", None),
        include_related_types=not getattr(args, "exact_type", False),
    )


def _add_query_arguments(parser: argparse.ArgumentParser, include_text: bool = True) -> None:
    parser.add_argument("--location", help="current location ID, name, city, region, or alias")
    parser.add_argument("--preferred-location", help="preferred location ID, name, city, region, or alias")
    parser.add_argument("--name", help="item-group name substring")
    parser.add_argument("--type", dest="item_type", help="item type or family (for example shoes)")
    parser.add_argument("--exact-type", action="store_true", help="disable related type-family matching")
    parser.add_argument("--use", "--tag", dest="uses", action="append", help="required use tag; repeat for AND matching")
    parser.add_argument("--condition", help="physical condition text, including broader values such as worn")
    parser.add_argument("--status", help="exact physical status")
    parser.add_argument(
        "--attribute", action="append", default=[], metavar="KEY=VALUE", help="required shared attribute; repeat for AND matching"
    )
    if include_text:
        parser.add_argument("--text", help="also require a text-search match")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inventory", description="Query individually tracked possessions")
    parser.add_argument("--data-dir", type=Path, help="directory containing clothes.yaml, locations.yaml, and schema.yaml")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="load and validate all inventory YAML")
    subparsers.add_parser("locations", help="list homes and travel-container locations")
    templates_parser = subparsers.add_parser("templates", help="list or inspect activity requirement templates")
    templates_parser.add_argument("template_id", nargs="?")
    definitions_parser = subparsers.add_parser("definitions", help="list shared item definitions")
    definitions_parser.add_argument("--location", help="only definitions with a copy currently at this location")
    definitions_parser.add_argument("--name", help="definition-name substring")
    definitions_parser.add_argument("--type", dest="item_type", help="item type or family")
    definitions_parser.add_argument("--tag", dest="tags", action="append", help="required use tag; repeat for AND matching")
    definitions_parser.add_argument("--attribute", action="append", default=[], metavar="KEY=VALUE")

    list_parser = subparsers.add_parser("list", help="list physical item instances")
    list_locations = list_parser.add_mutually_exclusive_group()
    list_locations.add_argument("--location", help="current location ID, name, city, region, or alias")
    list_locations.add_argument("--preferred-location", help="preferred location ID, name, city, region, or alias")

    instances_parser = subparsers.add_parser("instances", help="show all physical copies of an item group")
    instances_parser.add_argument("group", help="exact definition ID or unambiguous group name")
    subparsers.add_parser("away", help="show items away from their preferred location")
    contents_parser = subparsers.add_parser("contents", help="show travel-container contents")
    contents_parser.add_argument("location", help="suitcase, carry-on, or duffel-bag")
    history_parser = subparsers.add_parser("history", help="show movement history for one physical item")
    history_parser.add_argument("item_id")

    find_parser = subparsers.add_parser("find", help="find physical items matching several criteria")
    _add_query_arguments(find_parser)
    search_parser = subparsers.add_parser("search", help="search definitions, physical state, notes, and locations")
    search_parser.add_argument("query")
    search_parser.add_argument("--location", help="optionally limit search to a current location")
    count_parser = subparsers.add_parser("count", help="count matching definitions and physical items")
    _add_query_arguments(count_parser)
    summary_parser = subparsers.add_parser("summary", help="summarize one location")
    summary_parser.add_argument("--location", required=True)
    compare_parser = subparsers.add_parser("compare", help="compare possessions at two homes")
    compare_parser.add_argument("left")
    compare_parser.add_argument("right")
    duplicate_parser = subparsers.add_parser("duplicates", help="show near-duplicate shared definitions")
    duplicate_parser.add_argument("--location")
    duplicate_parser.add_argument("--threshold", type=float, default=0.9)

    move_parser = subparsers.add_parser("move", help="move explicitly selected physical items")
    move_parser.add_argument("item_ids", nargs="+", help="one or more exact physical item IDs")
    move_parser.add_argument("--from", dest="source", required=True, help="expected current location")
    move_parser.add_argument("--to", dest="destination", required=True, help="destination location")
    move_parser.add_argument("--reason", help="optional factual reason")
    move_parser.add_argument("--at", dest="timestamp", help="ISO 8601 timestamp; defaults to now")
    move_parser.add_argument(
        "--update-preferred",
        action="store_true",
        help="explicitly set preferred location to the destination as part of this move",
    )
    move_parser.add_argument(
        "--confirm",
        action="store_true",
        help="required acknowledgement that this command will change inventory state",
    )

    trip_parser = subparsers.add_parser("trip", help="create, update, and inspect structured trips")
    trip_commands = trip_parser.add_subparsers(dest="trip_command", required=True)
    trip_commands.add_parser("list", help="list trips")
    trip_show = trip_commands.add_parser("show", help="show one trip")
    trip_show.add_argument("trip_id")
    for command, help_text in (
        ("current", "show the current leg on a date"),
        ("next", "show the next leg after a date"),
    ):
        leg_query = trip_commands.add_parser(command, help=help_text)
        leg_query.add_argument("trip_id")
        leg_query.add_argument("--on", dest="on_date", help="query date; defaults to today")
    trip_destinations = trip_commands.add_parser("destinations", help="list trip destinations")
    trip_destinations.add_argument("trip_id")
    trip_duration = trip_commands.add_parser("duration", help="show inclusive trip duration")
    trip_duration.add_argument("trip_id")
    trip_requirements = trip_commands.add_parser("requirements", help="resolve abstract trip requirements")
    trip_requirements.add_argument("trip_id")
    trip_context = trip_commands.add_parser(
        "context", help="load itinerary, requirements, inventory context, and readiness"
    )
    trip_context.add_argument("trip_id")
    trip_readiness = trip_commands.add_parser(
        "readiness", help="report missing interview facts and Phase 6 readiness"
    )
    trip_readiness.add_argument("trip_id")

    trip_create = trip_commands.add_parser("create", help="create a trip")
    trip_create.add_argument("trip_id")
    trip_create.add_argument("--name", required=True)
    trip_create.add_argument(
        "--status", choices=("planned", "in_progress", "completed", "cancelled"), default="planned"
    )
    trip_create.add_argument("--start", required=True)
    trip_create.add_argument("--end", required=True)
    trip_create.add_argument(
        "--leg", action="append", required=True,
        help="ID:ORIGIN:DESTINATION:DEPARTURE:ARRIVAL[:TRANSPORT_NOTES]",
    )
    trip_create.add_argument("--place", action="append", help="trip-only location as ID=NAME")
    trip_create.add_argument("--luggage", action="append", help="available travel-container location ID")
    trip_create.add_argument("--notes")

    trip_update = trip_commands.add_parser("update", help="explicitly update trip details, excluding status")
    trip_update.add_argument("trip_id")
    trip_update.add_argument("--name")
    trip_update.add_argument("--start")
    trip_update.add_argument("--end")
    trip_update.add_argument("--leg", action="append", help="replace legs using the create syntax")
    trip_update.add_argument("--place", action="append", help="replace trip-only places using ID=NAME")
    trip_update.add_argument("--luggage", action="append", help="replace available luggage")
    notes_group = trip_update.add_mutually_exclusive_group()
    notes_group.add_argument("--notes")
    notes_group.add_argument("--clear-notes", action="store_true")

    trip_status = trip_commands.add_parser("status", help="explicitly change trip status")
    trip_status.add_argument("trip_id")
    trip_status.add_argument("status", choices=("planned", "in_progress", "completed", "cancelled"))

    trip_attach = trip_commands.add_parser("attach", help="attach an activity or event to a leg/date")
    trip_attach.add_argument("trip_id")
    trip_attach.add_argument("attachment_id")
    trip_attach.add_argument("--kind", choices=("activity", "event"), required=True)
    trip_attach.add_argument("--name", required=True)
    trip_attach.add_argument("--type", required=True, help="activity requirement template ID")
    trip_attach.add_argument("--leg")
    trip_attach.add_argument("--location")
    trip_attach.add_argument("--date")
    trip_attach.add_argument("--frequency", type=int, default=1)
    trip_attach.add_argument("--add", action="append", help="required CATEGORY or CATEGORY=COUNT override")
    trip_attach.add_argument("--add-optional", action="append", help="optional CATEGORY or CATEGORY=COUNT override")
    trip_attach.add_argument("--remove", action="append", help="remove a required template category")
    trip_attach.add_argument("--remove-optional", action="append", help="remove an optional template category")
    trip_attach.add_argument("--notes")

    trip_answer = trip_commands.add_parser(
        "answer", help="persist confirmed planning answers for one leg"
    )
    trip_answer.add_argument("trip_id")
    trip_answer.add_argument("--leg", required=True)
    trip_answer.add_argument(
        "--activities-complete", choices=("yes", "no"),
        help="whether all packing-relevant activities/events are recorded",
    )
    trip_answer.add_argument(
        "--laundry-available", choices=("yes", "no"),
        help="whether laundry is available during this leg's stay",
    )
    answer_notes = trip_answer.add_mutually_exclusive_group()
    answer_notes.add_argument("--notes")
    answer_notes.add_argument("--clear-notes", action="store_true")

    trip_answer.add_argument("--weather-summary")
    trip_answer.add_argument("--weather-low-c", type=float)
    trip_answer.add_argument("--weather-high-c", type=float)
    trip_answer.add_argument("--weather-precipitation")
    trip_answer.add_argument("--weather-source")
    trip_answer.add_argument("--weather-checked-at")
    trip_answer.add_argument("--clear-weather", action="store_true")
    context_group = trip_answer.add_mutually_exclusive_group()
    context_group.add_argument(
        "--context-note", action="append",
        help="replace leg-specific packing context notes; repeat as needed",
    )
    context_group.add_argument("--clear-context", action="store_true")

    packing_parser = subparsers.add_parser(
        "packing", help="compile packing facts and store plan-only recommendations"
    )
    packing_commands = packing_parser.add_subparsers(dest="packing_command", required=True)
    packing_context = packing_commands.add_parser(
        "context", help="compile compact whole-trip context for Codex"
    )
    packing_context.add_argument("trip_id")
    packing_matches = packing_commands.add_parser(
        "matches", help="inspect deterministic item candidates"
    )
    packing_matches.add_argument("trip_id")
    packing_matches.add_argument("--requirement", help="limit output to one category")
    packing_plan = packing_commands.add_parser("plan", help="list, show, or save packing plans")
    packing_plan_commands = packing_plan.add_subparsers(dest="packing_plan_command", required=True)
    packing_plan_commands.add_parser("list", help="list saved plans")
    packing_plan_show = packing_plan_commands.add_parser("show", help="show one saved plan")
    packing_plan_show.add_argument("plan_id")
    packing_plan_save = packing_plan_commands.add_parser(
        "save", help="validate and save one standalone YAML plan document"
    )
    packing_plan_save.add_argument("file", type=Path)
    packing_plan_save.add_argument(
        "--replace", action="store_true", help="explicitly replace a plan with the same ID"
    )
    packing_plan_confirm = packing_plan_commands.add_parser(
        "confirm", help="explicitly accept and freeze a draft plan"
    )
    packing_plan_confirm.add_argument("plan_id")

    execution_parser = subparsers.add_parser(
        "execution", help="apply confirmed trip actions and reconcile actual outcomes"
    )
    execution_commands = execution_parser.add_subparsers(dest="execution_command", required=True)
    execution_commands.add_parser("list", help="list trip execution ledgers")
    execution_show = execution_commands.add_parser("show", help="show one execution ledger")
    execution_show.add_argument("execution_id")
    execution_create = execution_commands.add_parser("create", help="create from a confirmed plan")
    execution_create.add_argument("execution_id")
    execution_create.add_argument("--trip", required=True)
    execution_create.add_argument("--plan", required=True)
    execution_create.add_argument("--at")
    execution_create.add_argument("--notes")
    execution_create.add_argument("--confirm", action="store_true")
    execution_begin = execution_commands.add_parser("begin", help="transition planned trip to in_progress")
    execution_begin.add_argument("execution_id")
    execution_begin.add_argument("--confirm", action="store_true")
    execution_decision = execution_commands.add_parser("decision", help="accept or reject one plan decision")
    execution_decision.add_argument("execution_id")
    execution_decision.add_argument("action_id")
    execution_decision.add_argument("decision", help="stable SECTION:INDEX reference, for example pack:1")
    decision_choice = execution_decision.add_mutually_exclusive_group(required=True)
    decision_choice.add_argument("--accept", action="store_true")
    decision_choice.add_argument("--reject", action="store_true")
    execution_decision.add_argument("--destination", help="explicit travel bag for transit-wear decisions")
    execution_decision.add_argument("--reason")
    execution_decision.add_argument("--at")
    execution_decision.add_argument("--confirm", action="store_true")
    execution_action = execution_commands.add_parser("action", help="record one actual trip action")
    execution_action.add_argument("execution_id")
    execution_action.add_argument("action_id")
    execution_action.add_argument(
        "--kind",
        required=True,
        choices=(
            "accepted", "packed", "used", "unused", "rejected", "transferred",
            "purchased", "damaged", "discarded", "lost", "returned", "stayed",
        ),
    )
    execution_action.add_argument("--item")
    execution_action.add_argument("--description")
    execution_action.add_argument("--decision")
    execution_action.add_argument("--leg")
    execution_action.add_argument("--from", dest="source")
    execution_action.add_argument("--to", dest="destination")
    execution_action.add_argument("--reason", required=True)
    execution_action.add_argument("--at")
    execution_action.add_argument("--confirm", action="store_true")
    execution_purchase = execution_commands.add_parser(
        "purchase", help="register and record a retained purchased possession"
    )
    execution_purchase.add_argument("execution_id")
    execution_purchase.add_argument("action_id")
    execution_purchase.add_argument("item_id")
    execution_purchase.add_argument("--definition", required=True)
    execution_purchase.add_argument("--location", required=True)
    execution_purchase.add_argument("--preferred-location")
    execution_purchase.add_argument("--name")
    execution_purchase.add_argument("--type", dest="item_type")
    execution_purchase.add_argument("--attribute", action="append", default=[])
    execution_purchase.add_argument("--use", action="append")
    execution_purchase.add_argument("--condition")
    execution_purchase.add_argument("--notes")
    execution_purchase.add_argument("--leg")
    execution_purchase.add_argument("--reason", required=True)
    execution_purchase.add_argument("--at")
    execution_purchase.add_argument("--confirm", action="store_true")
    execution_reconcile = execution_commands.add_parser(
        "reconcile", help="mark reviewed completion topics"
    )
    execution_reconcile.add_argument("execution_id")
    execution_reconcile.add_argument("--all", action="store_true")
    for topic in RECONCILIATION_TOPICS:
        execution_reconcile.add_argument("--" + topic.replace("_", "-"), action="store_true")
    execution_reconcile.add_argument("--notes")
    execution_reconcile.add_argument("--confirm", action="store_true")
    execution_complete = execution_commands.add_parser(
        "complete", help="complete only after all reconciliation topics"
    )
    execution_complete.add_argument("execution_id")
    execution_complete.add_argument("--at")
    execution_complete.add_argument("--confirm", action="store_true")
    execution_cancel = execution_commands.add_parser("cancel", help="cancel execution and trip")
    execution_cancel.add_argument("execution_id")
    execution_cancel.add_argument("--confirm", action="store_true")
    execution_history = execution_commands.add_parser("history", help="show trip action history for an item")
    execution_history.add_argument("item_id")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        inventory = load_inventory(args.data_dir)

        if args.command == "validate":
            trip_catalog = load_trips(args.data_dir)
            template_catalog = load_requirement_templates(args.data_dir)
            matcher_catalog = load_requirement_matchers(args.data_dir)
            plan_catalog = load_packing_plans(args.data_dir)
            execution_catalog = load_trip_executions(args.data_dir)
            payload = {
                "definitions": len(inventory.definitions),
                "physical_items": len(inventory.items),
                "locations": len(inventory.locations),
                "trips": len(trip_catalog.trips),
                "activity_templates": len(template_catalog.templates),
                "requirement_matchers": len(matcher_catalog.matchers),
                "packing_plans": len(plan_catalog.plans),
                "trip_executions": len(execution_catalog.executions),
            }
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(
                    "Valid inventory: {definitions} definitions, {physical_items} physical items, {locations} locations, {trips} trips, {activity_templates} activity templates, {requirement_matchers} requirement matchers, {packing_plans} packing plans, {trip_executions} trip executions.".format(**payload)
                )
            return 0

        if args.command == "locations":
            if args.json:
                print(json.dumps([_location_dict(location) for location in inventory.locations], indent=2, sort_keys=True))
            else:
                for location in inventory.locations:
                    geography = ", ".join(part for part in (location.city, location.region, location.country) if part)
                    suffix = " — {}".format(geography) if geography else ""
                    print("{}  {} ({}){}".format(location.id, location.name, location.kind, suffix))
                    if location.aliases:
                        print("    aliases: {}".format(", ".join(location.aliases)))
            return 0

        if args.command == "templates":
            catalog = load_requirement_templates(args.data_dir)
            templates = (
                (catalog.get(args.template_id),)
                if args.template_id
                else catalog.templates
            )
            if args.json:
                print(json.dumps([_template_dict(template) for template in templates], indent=2, sort_keys=True))
            else:
                for template in templates:
                    required = ", ".join(
                        "{}{}".format(value.category, " x{}".format(value.count) if value.count != 1 else "")
                        for value in template.required
                    )
                    optional = ", ".join(value.category for value in template.optional) or "none"
                    print("{} [{}]\n  required: {}\n  optional: {}".format(template.name, template.id, required, optional))
            return 0

        if args.command == "definitions":
            definitions = inventory.find_definitions(
                name=args.name,
                item_type=args.item_type,
                tags=args.tags or (),
                attributes=_parse_attributes(args.attribute),
                location=args.location,
            )
            if args.json:
                print(json.dumps([_definition_dict(definition) for definition in definitions], indent=2, sort_keys=True))
            else:
                _print_definitions(definitions)
            return 0

        if args.command in ("list", "find", "search", "instances", "away", "contents"):
            if args.command == "list":
                if args.location:
                    items = inventory.list_at_location(args.location)
                elif args.preferred_location:
                    items = inventory.list_by_preferred_location(args.preferred_location)
                else:
                    items = inventory.items
            elif args.command == "find":
                items = inventory.find(_query_from_args(args))
            elif args.command == "search":
                items = inventory.search(args.query)
                if args.location:
                    location_id = inventory.resolve_location(args.location).id
                    items = tuple(item for item in items if item.current_location == location_id)
            elif args.command == "instances":
                items = inventory.instances_for(args.group)
            elif args.command == "away":
                items = inventory.items_away_from_preferred_location()
            else:
                items = inventory.container_contents(args.location)
            if args.json:
                print(json.dumps([_item_dict(item) for item in items], indent=2, sort_keys=True))
            else:
                _print_items(items)
            return 0

        if args.command == "count":
            count = inventory.count(inventory.find(_query_from_args(args)))
            if args.json:
                print(json.dumps(_count_dict(count), indent=2, sort_keys=True))
            else:
                print("Definitions: {}\nPhysical items: {}".format(count.definitions, count.physical_items))
            return 0

        if args.command == "summary":
            summary = inventory.summarize_location(args.location)
            if args.json:
                print(json.dumps(_summary_dict(summary), indent=2, sort_keys=True))
            else:
                _print_summary(summary)
            return 0

        if args.command == "history":
            item = inventory.resolve_item(args.item_id)
            if args.json:
                print(json.dumps([_movement_dict(movement) for movement in item.movements], indent=2, sort_keys=True))
            elif not item.movements:
                print("No recorded movements for {}.".format(item.id))
            else:
                for movement in item.movements:
                    suffix = " — {}".format(movement.reason) if movement.reason else ""
                    print("{}  {} -> {}{}".format(movement.timestamp, movement.source, movement.destination, suffix))
            return 0

        if args.command == "compare":
            comparison = inventory.compare_homes(args.left, args.right)
            if args.json:
                payload = {
                    "left": _summary_dict(comparison.left),
                    "right": _summary_dict(comparison.right),
                    "by_type": {
                        key: {"left": _count_dict(value[0]), "right": _count_dict(value[1])}
                        for key, value in comparison.by_type.items()
                    },
                    "shared_item_names": list(comparison.shared_item_names),
                    "only_left": [_item_dict(item) for item in comparison.only_left],
                    "only_right": [_item_dict(item) for item in comparison.only_right],
                }
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print("Comparing {} with {}".format(comparison.left.location.name, comparison.right.location.name))
                print(
                    "Physical items: {} vs {}".format(
                        comparison.left.total.physical_items, comparison.right.total.physical_items
                    )
                )
                print("\nBy type")
                for item_type, (left_count, right_count) in comparison.by_type.items():
                    print("  {:<20} {:>4} {:>4}".format(item_type, left_count.physical_items, right_count.physical_items))
                print("\nShared definitions: {}".format(len(comparison.shared_item_names)))
            return 0

        if args.command == "move":
            result = move_items(
                args.item_ids,
                args.source,
                args.destination,
                confirmed=args.confirm,
                data_dir=args.data_dir,
                reason=args.reason,
                timestamp=args.timestamp,
                update_preferred=args.update_preferred,
            )
            if args.json:
                print(json.dumps({"applied": result.applied, **_movement_plan_dict(result.plan)}, indent=2, sort_keys=True))
            else:
                preferred = " Preferred locations were also updated." if result.plan.update_preferred else ""
                print(
                    "Moved {} physical item{} from {} to {} at {}.{}".format(
                        len(result.plan.item_ids),
                        "" if len(result.plan.item_ids) == 1 else "s",
                        result.plan.source,
                        result.plan.destination,
                        result.plan.timestamp,
                        preferred,
                    )
                )
            return 0

        if args.command == "packing":
            if args.packing_command == "context":
                context = compile_packing_context(args.trip_id, args.data_dir)
                payload = _whole_trip_context_dict(context)
                if args.json:
                    print(json.dumps(payload, indent=2, sort_keys=True))
                else:
                    print("{} [{}]".format(context.trip_name, context.trip_id))
                    print("Requirements: {}  Raw unmet: {}".format(
                        len(context.matches), len(context.unmet_required)
                    ))
                    print("Ready interview context: {}".format("yes" if context.readiness.ready else "no"))
                    for warning in context.context_warnings:
                        print("  WARNING {}".format(warning))
                    for match in context.unmet_required:
                        print("  MISSING {} x{} (available {})".format(
                            match.category, match.requested_count, match.available_quantity
                        ))
                return 0
            if args.packing_command == "matches":
                matches = match_trip_requirements(args.trip_id, args.data_dir)
                if args.requirement:
                    matches = tuple(value for value in matches if value.category == args.requirement)
                    if not matches:
                        raise ValueError("trip has no resolved requirement {!r}".format(args.requirement))
                if args.json:
                    print(json.dumps(
                        [_requirement_match_dict(value) for value in matches], indent=2, sort_keys=True
                    ))
                else:
                    for match in matches:
                        print("{}{}: requested {}, available {}, origin {}, destinations {}, transfer {}".format(
                            "optional " if match.optional else "",
                            match.category,
                            match.requested_count,
                            match.available_quantity,
                            len(match.at_origin),
                            len(match.already_at_destinations),
                            len(match.transfer_candidates),
                        ))
                return 0
            if args.packing_command == "plan":
                if args.packing_plan_command == "list":
                    plans = load_packing_plans(args.data_dir).plans
                    if args.json:
                        print(json.dumps([_packing_plan_dict(plan) for plan in plans], indent=2, sort_keys=True))
                    elif not plans:
                        print("No packing plans recorded.")
                    else:
                        for plan in plans:
                            print("{}  {}  {} [{}]".format(plan.status, plan.created_at, plan.trip, plan.id))
                    return 0
                if args.packing_plan_command == "show":
                    plan = load_packing_plan(args.plan_id, args.data_dir)
                elif args.packing_plan_command == "save":
                    plan = save_packing_plan(
                        load_packing_plan_document(args.file),
                        replace_existing=args.replace,
                        data_dir=args.data_dir,
                    )
                elif args.packing_plan_command == "confirm":
                    plan = confirm_packing_plan(args.plan_id, data_dir=args.data_dir)
                else:
                    parser.error("unknown packing plan command")
                    return 2
                if args.json:
                    print(json.dumps(_packing_plan_dict(plan), indent=2, sort_keys=True))
                else:
                    action = "Saved" if args.packing_plan_command == "save" else "Packing"
                    if args.packing_plan_command == "confirm":
                        action = "Confirmed"
                    print("{} plan {} for trip {} [{}].".format(action, plan.id, plan.trip, plan.status))
                return 0
            parser.error("unknown packing command")
            return 2

        if args.command == "execution":
            if args.execution_command == "list":
                executions = load_trip_executions(args.data_dir).executions
                if args.json:
                    print(json.dumps([_execution_dict(value) for value in executions], indent=2, sort_keys=True))
                elif not executions:
                    print("No trip executions recorded.")
                else:
                    for execution in executions:
                        print("{}  {}  plan={} [{}]".format(
                            execution.status, execution.trip, execution.packing_plan, execution.id
                        ))
                return 0
            if args.execution_command == "show":
                execution = load_trip_execution(args.execution_id, args.data_dir)
            elif args.execution_command == "create":
                execution = create_trip_execution(
                    args.execution_id, args.trip, args.plan,
                    confirmed=args.confirm, timestamp=args.at, notes=args.notes,
                    data_dir=args.data_dir,
                )
            elif args.execution_command == "begin":
                execution = begin_trip_execution(
                    args.execution_id, confirmed=args.confirm, data_dir=args.data_dir
                )
            elif args.execution_command == "decision":
                execution = confirm_packing_decision(
                    args.execution_id, args.action_id, args.decision,
                    accepted=args.accept, confirmed=args.confirm,
                    destination=args.destination, reason=args.reason, timestamp=args.at,
                    data_dir=args.data_dir,
                )
            elif args.execution_command == "action":
                execution = record_execution_action(
                    args.execution_id, args.action_id, args.kind,
                    item=args.item, description=args.description, decision=args.decision,
                    leg=args.leg, source=args.source, destination=args.destination,
                    reason=args.reason, confirmed=args.confirm, timestamp=args.at,
                    data_dir=args.data_dir,
                )
            elif args.execution_command == "purchase":
                execution = record_purchased_item(
                    args.execution_id, args.action_id, args.item_id,
                    args.definition, args.location,
                    name=args.name, item_type=args.item_type,
                    attributes=_parse_attributes(args.attribute), uses=args.use or (),
                    preferred_location=args.preferred_location, condition=args.condition,
                    notes=args.notes, leg=args.leg, reason=args.reason,
                    confirmed=args.confirm, timestamp=args.at, data_dir=args.data_dir,
                )
            elif args.execution_command == "reconcile":
                topics = RECONCILIATION_TOPICS if args.all else tuple(
                    topic for topic in RECONCILIATION_TOPICS if getattr(args, topic)
                )
                if not topics:
                    raise ValueError("select at least one reconciliation topic or --all")
                execution = review_reconciliation(
                    args.execution_id, topics, confirmed=args.confirm,
                    notes=args.notes, data_dir=args.data_dir,
                )
            elif args.execution_command == "complete":
                execution = complete_trip_execution(
                    args.execution_id, confirmed=args.confirm,
                    timestamp=args.at, data_dir=args.data_dir,
                )
            elif args.execution_command == "cancel":
                execution = cancel_trip_execution(
                    args.execution_id, confirmed=args.confirm, data_dir=args.data_dir
                )
            elif args.execution_command == "history":
                history = item_execution_history(args.item_id, args.data_dir)
                if args.json:
                    print(json.dumps([
                        {
                            "trip": value[0], "execution": value[1], "kind": value[2],
                            "state": value[3], "timestamp": value[4],
                        }
                        for value in history
                    ], indent=2, sort_keys=True))
                elif not history:
                    print("No trip execution history for {}.".format(args.item_id))
                else:
                    for trip_id, execution_id, kind, state, timestamp in history:
                        print("{}  {}  {}  {} [{}]".format(timestamp, kind, state, trip_id, execution_id))
                return 0
            else:
                parser.error("unknown execution command")
                return 2
            if args.json:
                print(json.dumps(_execution_dict(execution), indent=2, sort_keys=True))
            else:
                print("Execution {} for trip {} [{}].".format(
                    execution.id, execution.trip, execution.status
                ))
            return 0

        if args.command == "trip":
            if args.trip_command == "list":
                trips = load_trips(args.data_dir).trips
                if args.json:
                    print(json.dumps([_trip_dict(trip) for trip in trips], indent=2, sort_keys=True))
                elif not trips:
                    print("No trips recorded.")
                else:
                    for trip in trips:
                        print(
                            "{}  {} to {}  ({} days)  {} [{}]".format(
                                trip.status, trip.start_date, trip.end_date,
                                trip.duration_days, trip.name, trip.id,
                            )
                        )
                return 0

            if args.trip_command == "create":
                trip = create_trip(
                    args.trip_id,
                    args.name,
                    args.status,
                    args.start,
                    args.end,
                    [_parse_leg(value) for value in args.leg],
                    places=[_parse_place(value) for value in (args.place or [])],
                    luggage=args.luggage or (),
                    notes=args.notes,
                    data_dir=args.data_dir,
                )
            elif args.trip_command == "update":
                updates: Dict[str, Any] = {}
                if args.name is not None:
                    updates["name"] = args.name
                if args.start is not None:
                    updates["start_date"] = args.start
                if args.end is not None:
                    updates["end_date"] = args.end
                if args.leg is not None:
                    updates["legs"] = [_parse_leg(value) for value in args.leg]
                if args.place is not None:
                    updates["places"] = [_parse_place(value) for value in args.place]
                if args.luggage is not None:
                    updates["luggage"] = args.luggage
                if args.notes is not None:
                    updates["notes"] = args.notes
                elif args.clear_notes:
                    updates["notes"] = None
                trip = update_trip(args.trip_id, data_dir=args.data_dir, **updates)
            elif args.trip_command == "status":
                trip = set_trip_status(args.trip_id, args.status, data_dir=args.data_dir)
            elif args.trip_command == "attach":
                attachment = TripAttachment(
                    id=args.attachment_id,
                    kind=args.kind,
                    name=args.name,
                    type=args.type,
                    leg=args.leg,
                    location=args.location,
                    date=_parse_date(args.date) if args.date else None,
                    frequency=args.frequency,
                    requirements=RequirementOverrides(
                        add=tuple(_parse_category_requirement(value) for value in (args.add or [])),
                        add_optional=tuple(
                            _parse_category_requirement(value) for value in (args.add_optional or [])
                        ),
                        remove=tuple(args.remove or ()),
                        remove_optional=tuple(args.remove_optional or ()),
                    ),
                    notes=args.notes,
                )
                trip = add_trip_attachment(args.trip_id, attachment, data_dir=args.data_dir)
            elif args.trip_command == "answer":
                answers: Dict[str, Any] = {}
                if args.activities_complete is not None:
                    answers["activities_complete"] = _parse_yes_no(args.activities_complete)
                if args.laundry_available is not None:
                    answers["laundry_available"] = _parse_yes_no(args.laundry_available)
                if args.notes is not None:
                    answers["notes"] = args.notes
                elif args.clear_notes:
                    answers["notes"] = None
                weather_values = (
                    args.weather_summary,
                    args.weather_low_c,
                    args.weather_high_c,
                    args.weather_precipitation,
                    args.weather_source,
                    args.weather_checked_at,
                )
                if args.clear_weather and any(value is not None for value in weather_values):
                    raise ValueError("--clear-weather cannot be combined with weather values")
                if args.clear_weather:
                    answers["weather"] = TripWeather()
                elif any(value is not None for value in weather_values):
                    current = load_trip(args.trip_id, args.data_dir)
                    current_leg_planning = next(
                        (value for value in current.planning.legs if value.leg == args.leg),
                        None,
                    )
                    if current_leg_planning is None:
                        raise ValueError(
                            "trip {!r} has no leg {!r}".format(args.trip_id, args.leg)
                        )
                    answers["weather"] = replace(
                        current_leg_planning.weather,
                        summary=(
                            current_leg_planning.weather.summary
                            if args.weather_summary is None else args.weather_summary
                        ),
                        low_c=(
                            current_leg_planning.weather.low_c
                            if args.weather_low_c is None else args.weather_low_c
                        ),
                        high_c=(
                            current_leg_planning.weather.high_c
                            if args.weather_high_c is None else args.weather_high_c
                        ),
                        precipitation=(
                            current_leg_planning.weather.precipitation
                            if args.weather_precipitation is None else args.weather_precipitation
                        ),
                        source=(
                            current_leg_planning.weather.source
                            if args.weather_source is None else args.weather_source
                        ),
                        checked_at=(
                            current_leg_planning.weather.checked_at
                            if args.weather_checked_at is None else args.weather_checked_at
                        ),
                    )
                if args.context_note is not None:
                    answers["context"] = tuple(args.context_note)
                elif args.clear_context:
                    answers["context"] = ()
                trip = update_trip_leg_planning(
                    args.trip_id,
                    args.leg,
                    data_dir=args.data_dir,
                    **answers
                )
            else:
                if args.trip_command == "context":
                    context = load_trip_context(args.trip_id, args.data_dir)
                    if args.json:
                        print(json.dumps(_planning_context_dict(context), indent=2, sort_keys=True))
                    else:
                        print("{} [{}] — {}".format(context.trip.name, context.trip.id, context.trip.status))
                        print(
                            "Requirements: {} required, {} optional categories".format(
                                len(context.requirements.required), len(context.requirements.optional)
                            )
                        )
                        for location in context.inventory.departure_locations:
                            print(
                                "Inventory at {}: {} definitions, {} physical items".format(
                                    location.location_id, location.definitions, len(location.items)
                                )
                            )
                        for luggage_id, items in context.inventory.luggage_contents.items():
                            print("{} contents: {} physical items".format(luggage_id, len(items)))
                        print(
                            "Ready for packing recommendations: {}".format(
                                "yes" if context.readiness.ready else "no"
                            )
                        )
                        for missing in context.readiness.missing:
                            print("  [{}] {}".format(missing.code, missing.question))
                        for blocker in context.readiness.blockers:
                            print("  BLOCKED [{}] {}".format(blocker.code, blocker.message))
                    return 0
                if args.trip_command == "readiness":
                    readiness = inspect_trip_readiness(args.trip_id, args.data_dir)
                    if args.json:
                        print(json.dumps(_readiness_dict(readiness), indent=2, sort_keys=True))
                    else:
                        print(
                            "Ready for packing recommendations: {}".format(
                                "yes" if readiness.ready else "no"
                            )
                        )
                        for missing in readiness.missing:
                            print("  [{}] {}".format(missing.code, missing.question))
                        for blocker in readiness.blockers:
                            print("  BLOCKED [{}] {}".format(blocker.code, blocker.message))
                    return 0
                trip = load_trip(args.trip_id, args.data_dir)
                if args.trip_command == "show":
                    if args.json:
                        print(json.dumps(_trip_dict(trip), indent=2, sort_keys=True))
                    else:
                        print("{} [{}] — {}".format(trip.name, trip.id, trip.status))
                        print("{} to {} ({} days)".format(trip.start_date, trip.end_date, trip.duration_days))
                        print("Luggage: {}".format(", ".join(trip.luggage) if trip.luggage else "none"))
                        for leg in trip.legs:
                            print("  {}: {} -> {} ({} to {})".format(leg.id, leg.origin, leg.destination, leg.departure, leg.arrival))
                        if trip.attachments:
                            print("Activities and events:")
                            for attachment in trip.attachments:
                                anchors = [
                                    value
                                    for value in (
                                        "leg={}".format(attachment.leg) if attachment.leg else None,
                                        "location={}".format(attachment.location) if attachment.location else None,
                                        "date={}".format(attachment.date) if attachment.date else None,
                                    )
                                    if value
                                ]
                                print(
                                    "  {}: {} [{} x{}] ({})".format(
                                        attachment.id,
                                        attachment.name,
                                        attachment.type,
                                        attachment.frequency,
                                        ", ".join(anchors),
                                    )
                                )
                    return 0
                if args.trip_command in ("current", "next"):
                    leg = (
                        trip.current_leg(args.on_date)
                        if args.trip_command == "current"
                        else trip.next_leg(args.on_date)
                    )
                    if args.json:
                        print(json.dumps(_leg_dict(leg) if leg else None, indent=2, sort_keys=True))
                    elif leg is None:
                        print("No {} leg.".format(args.trip_command))
                    else:
                        print("{}: {} -> {} ({} to {})".format(leg.id, leg.origin, leg.destination, leg.departure, leg.arrival))
                    return 0
                if args.trip_command == "destinations":
                    if args.json:
                        print(json.dumps(list(trip.destinations), indent=2))
                    else:
                        print("\n".join(trip.destinations))
                    return 0
                if args.trip_command == "duration":
                    if args.json:
                        print(json.dumps({"trip_id": trip.id, "duration_days": trip.duration_days}, indent=2, sort_keys=True))
                    else:
                        print("{} days".format(trip.duration_days))
                    return 0
                if args.trip_command == "requirements":
                    resolved = resolve_trip_requirements(args.trip_id, args.data_dir)
                    payload = _resolved_requirements_dict(resolved)
                    if args.json:
                        print(json.dumps(payload, indent=2, sort_keys=True))
                    else:
                        print("Required")
                        for value in resolved.required:
                            print("  {} x{}  ({})".format(value.category, value.count, ", ".join(value.sources)))
                        print("Optional")
                        for value in resolved.optional:
                            print("  {} x{}  ({})".format(value.category, value.count, ", ".join(value.sources)))
                    return 0
                parser.error("unknown trip command")
                return 2

            if args.json:
                print(json.dumps(_trip_dict(trip), indent=2, sort_keys=True))
            else:
                print("Saved trip {} [{}] with status {}.".format(trip.name, trip.id, trip.status))
            return 0

        if args.command == "duplicates":
            candidates = inventory.find_duplicates(location=args.location, threshold=args.threshold)
            if args.json:
                payload = [
                    {
                        "kind": candidate.kind,
                        "score": candidate.score,
                        "reasons": list(candidate.reasons),
                        "left": _definition_dict(candidate.left),
                        "right": _definition_dict(candidate.right),
                    }
                    for candidate in candidates
                ]
                print(json.dumps(payload, indent=2, sort_keys=True))
            elif not candidates:
                print("No duplicate definition candidates found.")
            else:
                for candidate in candidates:
                    print(
                        "{} {:.3f}  {} [{}]  <->  {} [{}]".format(
                            candidate.kind.upper(),
                            candidate.score,
                            candidate.left.name,
                            candidate.left.id,
                            candidate.right.name,
                            candidate.right.id,
                        )
                    )
                    print("    {}".format("; ".join(candidate.reasons)))
            return 0

        parser.error("unknown command")
        return 2
    except ConfirmationRequiredError as exc:
        if args.json:
            print(json.dumps({"applied": False, "confirmation_required": True, **_movement_plan_dict(exc.plan)}, indent=2, sort_keys=True))
        else:
            print("inventory: {}. Re-run with --confirm to apply it.".format(exc), file=sys.stderr)
        return 2
    except (InventoryValidationError, LookupError, ValueError) as exc:
        print("inventory: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
