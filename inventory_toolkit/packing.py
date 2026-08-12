"""Whole-trip packing facts, deterministic matches, and plan persistence.

Matching deliberately returns candidates rather than final choices. Packing
plans are recommendations stored separately from factual possession state.
"""

import fcntl
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml

from .loader import load_inventory
from .load_context import cached_catalog_load
from .models import Movement, PhysicalItem
from .paths import default_data_directory
from .planning import TripReadinessReport, inspect_trip_readiness
from .requirements import ResolvedTripRequirements, load_requirement_templates, resolve_trip_requirements
from .trips import Trip, TripLegPlanning, TripNotFoundError, load_trip, load_trips

CATEGORY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ATTRIBUTE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
MATCHER_FIELDS = {"category", "any"}
CLAUSE_FIELDS = {"types", "tags", "attributes"}
PLAN_FIELDS = {"id", "trip", "status", "created_at", "sections", "notes"}
ENTRY_FIELDS = {
    "item", "requirement", "leg", "container", "source", "destination", "quantity", "reason",
}
PLAN_SECTIONS = (
    "pack",
    "wear_in_transit",
    "already_there",
    "optional",
    "do_not_pack",
    "missing",
    "leave_at_destination",
    "bring_back",
    "move_between_legs",
)
UNAVAILABLE_STATUSES = frozenset({"missing", "lost", "loaned", "repair", "discarded"})


class PackingValidationError(ValueError):
    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("Packing validation failed:\n- " + "\n- ".join(self.errors))


class PackingPlanNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class MatcherClause:
    types: Tuple[str, ...]
    tags: Tuple[str, ...]
    attributes: Mapping[str, Any]


@dataclass(frozen=True)
class RequirementMatcher:
    category: str
    any: Tuple[MatcherClause, ...]


class RequirementMatcherCatalog:
    def __init__(self, matchers: Sequence[RequirementMatcher]):
        self._matchers = tuple(matchers)
        self._by_category = MappingProxyType({matcher.category: matcher for matcher in matchers})

    @property
    def matchers(self) -> Tuple[RequirementMatcher, ...]:
        return self._matchers

    def get(self, category: str) -> Optional[RequirementMatcher]:
        return self._by_category.get(category)


@dataclass(frozen=True)
class PriorPlanUsage:
    plan_id: str
    trip_id: str
    created_at: str
    sections: Tuple[str, ...]


@dataclass(frozen=True)
class PriorExecutionUsage:
    trip_id: str
    execution_id: str
    kind: str
    state: str
    timestamp: str


@dataclass(frozen=True)
class PackingCandidate:
    item_id: str
    definition_id: str
    name: str
    item_type: str
    attributes: Mapping[str, Any]
    uses: Tuple[str, ...]
    current_location: str
    preferred_location: str
    condition: Optional[str]
    status: Optional[str]
    available: bool
    movement_history: Tuple[Movement, ...]
    prior_plan_history: Tuple[PriorPlanUsage, ...]
    execution_history: Tuple[PriorExecutionUsage, ...]


@dataclass(frozen=True)
class RequirementMatch:
    category: str
    requested_count: int
    optional: bool
    sources: Tuple[str, ...]
    use_locations: Tuple[str, ...]
    candidates: Tuple[PackingCandidate, ...]
    at_origin: Tuple[PackingCandidate, ...]
    already_at_destinations: Tuple[PackingCandidate, ...]
    transfer_candidates: Tuple[PackingCandidate, ...]
    available_quantity: int
    raw_shortfall: int


@dataclass(frozen=True)
class LegPackingContext:
    id: str
    origin: str
    destination: str
    departure: str
    arrival: str
    laundry_available: Optional[bool]
    weather: Mapping[str, Any]
    context: Tuple[str, ...]
    attachments: Tuple[str, ...]


@dataclass(frozen=True)
class WholeTripPackingContext:
    trip_id: str
    trip_name: str
    status: str
    start_date: str
    end_date: str
    luggage: Tuple[str, ...]
    legs: Tuple[LegPackingContext, ...]
    requirements: ResolvedTripRequirements
    matches: Tuple[RequirementMatch, ...]
    unmet_required: Tuple[RequirementMatch, ...]
    luggage_contents: Mapping[str, Tuple[str, ...]]
    readiness: TripReadinessReport
    context_warnings: Tuple[str, ...]


@dataclass(frozen=True)
class PackingPlanEntry:
    item: Optional[str]
    requirement: Optional[str]
    leg: Optional[str]
    container: Optional[str]
    source: Optional[str]
    destination: Optional[str]
    quantity: int
    reason: str


@dataclass(frozen=True)
class PackingPlan:
    id: str
    trip: str
    status: str
    created_at: str
    sections: Mapping[str, Tuple[PackingPlanEntry, ...]]
    notes: Optional[str] = None


class PackingPlanCatalog:
    def __init__(self, plans: Sequence[PackingPlan]):
        self._plans = tuple(plans)
        self._by_id = MappingProxyType({plan.id: plan for plan in plans})

    @property
    def plans(self) -> Tuple[PackingPlan, ...]:
        return self._plans

    def get(self, plan_id: str) -> PackingPlan:
        try:
            return self._by_id[plan_id]
        except KeyError:
            raise PackingPlanNotFoundError("unknown packing plan {!r}".format(plan_id))


def _data_directory(data_dir: Optional[Path]) -> Path:
    return Path(data_dir) if data_dir is not None else default_data_directory()


def _read_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError:
        raise PackingValidationError(["missing file: {}".format(path)])
    except yaml.YAMLError as exc:
        raise PackingValidationError(["{}: invalid YAML: {}".format(path.name, exc)])


def _required(schema: Mapping[str, Any], section: str, errors: List[str]) -> Sequence[str]:
    value = schema.get(section, {}).get("required_fields") if isinstance(schema.get(section), dict) else None
    if not isinstance(value, list) or not all(isinstance(field, str) for field in value):
        errors.append("schema.yaml: {}.required_fields must be a list of strings".format(section))
        return ()
    return value


def _check_fields(
    value: Mapping[str, Any], required: Sequence[str], allowed: set, label: str, errors: List[str]
) -> bool:
    missing = set(required) - set(value)
    extra = set(value) - allowed
    if missing:
        errors.append("{} missing fields: {}".format(label, ", ".join(sorted(missing))))
    if extra:
        errors.append("{} has unknown fields: {}".format(label, ", ".join(sorted(extra))))
    return not missing and not extra


def _nonempty(value: Any, label: str, errors: List[str]) -> bool:
    valid = isinstance(value, str) and bool(value.strip())
    if not valid:
        errors.append("{} must be a non-empty string".format(label))
    return valid


def _optional_string(value: Any, label: str, errors: List[str]) -> bool:
    valid = value is None or (isinstance(value, str) and bool(value.strip()))
    if not valid:
        errors.append("{} must be a non-empty string or null".format(label))
    return valid


def _timestamp(value: Any, label: str, errors: List[str]) -> bool:
    if not isinstance(value, str):
        errors.append("{} must be a timezone-aware ISO 8601 timestamp".format(label))
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    valid = parsed is not None and parsed.tzinfo is not None and parsed.utcoffset() is not None
    if not valid:
        errors.append("{} must be a timezone-aware ISO 8601 timestamp".format(label))
    return valid


def load_requirement_matchers(data_dir: Optional[Path] = None) -> RequirementMatcherCatalog:
    directory = _data_directory(data_dir)
    raw = _read_yaml(directory / "requirement_matchers.yaml")
    schema = _read_yaml(directory / "schema.yaml")
    errors: List[str] = []
    if not isinstance(schema, dict) or schema.get("version") != 8:
        errors.append("schema.yaml: version must be 8")
        schema = schema if isinstance(schema, dict) else {}
    if not isinstance(schema.get("matcher_store"), dict) or schema["matcher_store"].get("root") != "mapping":
        errors.append("schema.yaml: matcher_store.root must be 'mapping'")
    for section in ("requirement_matchers", "requirement_matcher_clauses"):
        if not isinstance(schema.get(section), dict) or schema[section].get("root") != "list":
            errors.append("schema.yaml: {}.root must be 'list'".format(section))
    if not isinstance(raw, dict) or set(raw) != {"matchers"} or not isinstance(raw.get("matchers"), list):
        errors.append("requirement_matchers.yaml: root must contain only a matchers list")
        raw = {"matchers": []}
    matchers: List[RequirementMatcher] = []
    seen = set()
    for index, value in enumerate(raw["matchers"], 1):
        label = "requirement_matchers.yaml entry {}".format(index)
        if not isinstance(value, dict):
            errors.append("{} must be a mapping".format(label))
            continue
        valid = _check_fields(
            value, _required(schema, "requirement_matchers", errors), MATCHER_FIELDS, label, errors
        )
        category = value.get("category")
        if not isinstance(category, str) or not CATEGORY_PATTERN.fullmatch(category):
            errors.append("{} has invalid category {!r}".format(label, category))
            valid = False
        if category in seen:
            errors.append("{} has duplicate category {!r}".format(label, category))
            valid = False
        seen.add(category)
        raw_clauses = value.get("any")
        if not isinstance(raw_clauses, list):
            errors.append("{}.any must be a list".format(label))
            valid = False
            raw_clauses = []
        clauses = []
        for clause_index, raw_clause in enumerate(raw_clauses, 1):
            clause_label = "{}.any entry {}".format(label, clause_index)
            if not isinstance(raw_clause, dict):
                errors.append("{} must be a mapping".format(clause_label))
                valid = False
                continue
            clause_valid = _check_fields(
                raw_clause,
                _required(schema, "requirement_matcher_clauses", errors),
                CLAUSE_FIELDS,
                clause_label,
                errors,
            )
            types = raw_clause.get("types")
            tags = raw_clause.get("tags")
            attributes = raw_clause.get("attributes")
            if not isinstance(types, list) or not all(isinstance(item, str) and item for item in types):
                errors.append("{}.types must be a list of non-empty strings".format(clause_label))
                clause_valid = False
                types = []
            if not isinstance(tags, list) or not all(isinstance(item, str) and item for item in tags):
                errors.append("{}.tags must be a list of non-empty strings".format(clause_label))
                clause_valid = False
                tags = []
            if len(types) != len(set(types)) or len(tags) != len(set(tags)):
                errors.append("{} types and tags must not contain duplicates".format(clause_label))
                clause_valid = False
            if not isinstance(attributes, dict) or not all(
                isinstance(key, str) and ATTRIBUTE_KEY_PATTERN.fullmatch(key)
                and (value is None or isinstance(value, (str, int, float, bool)))
                for key, value in (attributes.items() if isinstance(attributes, dict) else ())
            ):
                errors.append("{}.attributes must map valid keys to scalar values".format(clause_label))
                clause_valid = False
                attributes = {}
            if clause_valid:
                clauses.append(
                    MatcherClause(tuple(types), tuple(tags), MappingProxyType(dict(attributes)))
                )
            else:
                valid = False
        if valid:
            matchers.append(RequirementMatcher(category, tuple(clauses)))

    template_categories = {
        requirement.category
        for template in load_requirement_templates(data_dir).templates
        for requirement in template.required + template.optional
    }
    missing_matchers = template_categories - seen
    if missing_matchers:
        errors.append(
            "requirement_matchers.yaml missing template categories: {}".format(
                ", ".join(sorted(missing_matchers))
            )
        )
    if errors:
        raise PackingValidationError(errors)
    return RequirementMatcherCatalog(matchers)


def _normalize(value: Any) -> str:
    return str(value).strip().casefold()


def _matches_clause(item: PhysicalItem, clause: MatcherClause) -> bool:
    if clause.types and item.type not in clause.types:
        return False
    if not set(clause.tags).issubset(set(item.uses)):
        return False
    for key, expected in clause.attributes.items():
        actual = item.attributes.get(key)
        if isinstance(actual, tuple):
            if _normalize(expected) not in {_normalize(value) for value in actual}:
                return False
        elif _normalize(actual) != _normalize(expected):
            return False
    return True


def item_satisfies_requirement(
    item: PhysicalItem, category: str, matchers: RequirementMatcherCatalog
) -> bool:
    matcher = matchers.get(category)
    if matcher is None:
        return item.type == category or category in item.uses
    return any(_matches_clause(item, clause) for clause in matcher.any)


def _candidate(
    item: PhysicalItem,
    prior_plan_history: Tuple[PriorPlanUsage, ...] = (),
    execution_history: Tuple[PriorExecutionUsage, ...] = (),
) -> PackingCandidate:
    available = item.status is None or _normalize(item.status) not in UNAVAILABLE_STATUSES
    return PackingCandidate(
        item_id=item.id,
        definition_id=item.definition_id,
        name=item.name,
        item_type=item.type,
        attributes=item.attributes,
        uses=item.uses,
        current_location=item.current_location,
        preferred_location=item.preferred_location,
        condition=item.condition,
        status=item.status,
        available=available,
        movement_history=item.movements,
        prior_plan_history=prior_plan_history,
        execution_history=execution_history,
    )


def _requirement_use_locations(trip: Trip, source_ids: Sequence[str]) -> Tuple[str, ...]:
    attachments = {attachment.id: attachment for attachment in trip.attachments}
    legs = {leg.id: leg for leg in trip.legs}
    result = []
    for source_id in source_ids:
        attachment = attachments.get(source_id)
        if attachment is None:
            continue
        location = attachment.location
        if location is None and attachment.leg in legs:
            location = legs[attachment.leg].destination
        if location is None and attachment.date is not None:
            arrived = [leg for leg in trip.legs if leg.arrival <= attachment.date]
            location = arrived[-1].destination if arrived else trip.legs[0].origin
        if location and location not in result:
            result.append(location)
    return tuple(result)


def match_trip_requirements(
    trip_id: str, data_dir: Optional[Path] = None
) -> Tuple[RequirementMatch, ...]:
    trip = load_trip(trip_id, data_dir)
    requirements = resolve_trip_requirements(trip_id, data_dir)
    inventory = load_inventory(data_dir)
    matchers = load_requirement_matchers(data_dir)
    usage_index = _prior_plan_usage_index(load_packing_plans(data_dir))
    execution_index = _execution_usage_index(data_dir)
    return _match_loaded_requirements(
        trip, requirements, inventory, matchers, usage_index, execution_index
    )


def _match_loaded_requirements(
    trip: Trip,
    requirements: ResolvedTripRequirements,
    inventory: Any,
    matchers: RequirementMatcherCatalog,
    usage_index: Mapping[str, Tuple[PriorPlanUsage, ...]],
    execution_index: Mapping[str, Tuple[PriorExecutionUsage, ...]],
) -> Tuple[RequirementMatch, ...]:
    initial_origin = trip.legs[0].origin
    inventory_location_ids = {location.id for location in inventory.locations}
    trip_destinations = {
        leg.destination for leg in trip.legs
        if leg.destination in inventory_location_ids and leg.destination != initial_origin
    }
    origin_locations = {initial_origin, *trip.luggage}
    matches = []
    for optional, resolved_values in (
        (False, requirements.required), (True, requirements.optional)
    ):
        for requirement in resolved_values:
            candidates = tuple(
                _candidate(
                    item,
                    usage_index.get(item.id, ()),
                    execution_index.get(item.id, ()),
                )
                for item in inventory.items
                if item_satisfies_requirement(item, requirement.category, matchers)
            )
            available = tuple(candidate for candidate in candidates if candidate.available)
            use_locations = _requirement_use_locations(trip, requirement.sources)
            relevant_destinations = (
                set(use_locations) & inventory_location_ids
            ) or trip_destinations
            at_origin = tuple(
                candidate for candidate in available
                if candidate.current_location in origin_locations
            )
            already = tuple(
                candidate for candidate in available
                if candidate.current_location in relevant_destinations
            )
            transfer = tuple(
                candidate for candidate in available
                if (
                    candidate.current_location not in origin_locations
                    and (
                        candidate.current_location not in use_locations
                        or any(
                            location != candidate.current_location
                            for location in use_locations
                        )
                    )
                )
            )
            matches.append(
                RequirementMatch(
                    category=requirement.category,
                    requested_count=requirement.count,
                    optional=optional,
                    sources=requirement.sources,
                    use_locations=use_locations,
                    candidates=candidates,
                    at_origin=at_origin,
                    already_at_destinations=already,
                    transfer_candidates=transfer,
                    available_quantity=len(available),
                    raw_shortfall=max(0, requirement.count - len(available)),
                )
            )
    return tuple(matches)


def _weather_dict(planning: TripLegPlanning) -> Mapping[str, Any]:
    return MappingProxyType(
        {
            "summary": planning.weather.summary,
            "low_c": planning.weather.low_c,
            "high_c": planning.weather.high_c,
            "precipitation": planning.weather.precipitation,
            "source": planning.weather.source,
            "checked_at": planning.weather.checked_at,
        }
    )


def compile_packing_context(
    trip_id: str, data_dir: Optional[Path] = None
) -> WholeTripPackingContext:
    trip = load_trip(trip_id, data_dir)
    requirements = resolve_trip_requirements(trip_id, data_dir)
    inventory = load_inventory(data_dir)
    matchers = load_requirement_matchers(data_dir)
    plans = load_packing_plans(data_dir)
    matches = _match_loaded_requirements(
        trip,
        requirements,
        inventory,
        matchers,
        _prior_plan_usage_index(plans),
        _execution_usage_index(data_dir),
    )
    planning_by_leg = {entry.leg: entry for entry in trip.planning.legs}
    attachments_by_leg: Dict[str, List[str]] = {leg.id: [] for leg in trip.legs}
    for attachment in trip.attachments:
        if attachment.leg:
            attachments_by_leg[attachment.leg].append(attachment.id)
    legs = []
    warnings = []
    for leg in trip.legs:
        planning = planning_by_leg[leg.id]
        weather = _weather_dict(planning)
        if not any(value is not None for value in weather.values()):
            warnings.append("No weather context is recorded for leg {}.".format(leg.id))
        legs.append(
            LegPackingContext(
                id=leg.id,
                origin=leg.origin,
                destination=leg.destination,
                departure=leg.departure.isoformat(),
                arrival=leg.arrival.isoformat(),
                laundry_available=planning.laundry_available,
                weather=weather,
                context=planning.context,
                attachments=tuple(attachments_by_leg[leg.id]),
            )
        )
    luggage_contents = MappingProxyType(
        {
            location: tuple(item.id for item in inventory.container_contents(location))
            for location in trip.luggage
        }
    )
    return WholeTripPackingContext(
        trip_id=trip.id,
        trip_name=trip.name,
        status=trip.status,
        start_date=trip.start_date.isoformat(),
        end_date=trip.end_date.isoformat(),
        luggage=trip.luggage,
        legs=tuple(legs),
        requirements=requirements,
        matches=matches,
        unmet_required=tuple(
            match for match in matches if not match.optional and match.raw_shortfall > 0
        ),
        luggage_contents=luggage_contents,
        readiness=inspect_trip_readiness(trip_id, data_dir),
        context_warnings=tuple(warnings),
    )


def _parse_plan_store(raw: Any, schema: Any, data_dir: Path) -> PackingPlanCatalog:
    errors: List[str] = []
    if not isinstance(schema, dict) or schema.get("version") != 8:
        errors.append("schema.yaml: version must be 8")
        schema = schema if isinstance(schema, dict) else {}
    if not isinstance(schema.get("packing_plan_store"), dict) or schema["packing_plan_store"].get("root") != "mapping":
        errors.append("schema.yaml: packing_plan_store.root must be 'mapping'")
    for section in ("packing_plans", "packing_plan_entries"):
        if not isinstance(schema.get(section), dict) or schema[section].get("root") != "list":
            errors.append("schema.yaml: {}.root must be 'list'".format(section))
    if not isinstance(schema.get("packing_plan_sections"), dict) or schema["packing_plan_sections"].get("root") != "mapping":
        errors.append("schema.yaml: packing_plan_sections.root must be 'mapping'")
    if not isinstance(raw, dict) or set(raw) != {"plans"} or not isinstance(raw.get("plans"), list):
        errors.append("packing_plans.yaml: root must contain only a plans list")
        raw = {"plans": []}
    inventory = load_inventory(data_dir)
    trips = load_trips(data_dir)
    inventory_items = {item.id: item for item in inventory.items}
    inventory_locations = {location.id: location for location in inventory.locations}
    allowed_statuses = schema.get("packing_plans", {}).get("allowed_statuses", [])
    plans = []
    seen_ids = set()
    for plan_index, value in enumerate(raw["plans"], 1):
        label = "packing_plans.yaml plan {}".format(plan_index)
        if not isinstance(value, dict):
            errors.append("{} must be a mapping".format(label))
            continue
        valid = _check_fields(value, _required(schema, "packing_plans", errors), PLAN_FIELDS, label, errors)
        plan_id = value.get("id")
        if not isinstance(plan_id, str) or not ID_PATTERN.fullmatch(plan_id):
            errors.append("{} has invalid id {!r}".format(label, plan_id))
            valid = False
        if plan_id in seen_ids:
            errors.append("{} has duplicate id {!r}".format(label, plan_id))
            valid = False
        seen_ids.add(plan_id)
        trip_id = value.get("trip")
        try:
            trip = trips.get(trip_id)
        except TripNotFoundError:
            errors.append("{} has unknown trip {!r}".format(label, trip_id))
            valid = False
            trip = None
        status = value.get("status")
        if status not in allowed_statuses:
            errors.append("{} has unknown status {!r}".format(label, status))
            valid = False
        valid &= _timestamp(value.get("created_at"), "{}.created_at".format(label), errors)
        valid &= _optional_string(value.get("notes"), "{}.notes".format(label), errors)
        raw_sections = value.get("sections")
        section_values: Dict[str, Tuple[PackingPlanEntry, ...]] = {}
        if not isinstance(raw_sections, dict):
            errors.append("{}.sections must be a mapping".format(label))
            valid = False
            raw_sections = {}
        missing_sections = set(PLAN_SECTIONS) - set(raw_sections)
        extra_sections = set(raw_sections) - set(PLAN_SECTIONS)
        if missing_sections:
            errors.append("{}.sections missing: {}".format(label, ", ".join(sorted(missing_sections))))
            valid = False
        if extra_sections:
            errors.append("{}.sections unknown: {}".format(label, ", ".join(sorted(extra_sections))))
            valid = False
        known_locations = set(inventory_locations)
        known_legs = set()
        if trip:
            known_locations.update(place.id for place in trip.places)
            known_legs.update(leg.id for leg in trip.legs)
        for section in PLAN_SECTIONS:
            raw_entries = raw_sections.get(section)
            if not isinstance(raw_entries, list):
                errors.append("{}.sections.{} must be a list".format(label, section))
                valid = False
                raw_entries = []
            entries = []
            for entry_index, raw_entry in enumerate(raw_entries, 1):
                entry_label = "{}.sections.{} entry {}".format(label, section, entry_index)
                if not isinstance(raw_entry, dict):
                    errors.append("{} must be a mapping".format(entry_label))
                    valid = False
                    continue
                entry_valid = _check_fields(
                    raw_entry,
                    _required(schema, "packing_plan_entries", errors),
                    ENTRY_FIELDS,
                    entry_label,
                    errors,
                )
                item = raw_entry.get("item")
                requirement = raw_entry.get("requirement")
                item_object = inventory_items.get(item) if item is not None else None
                if item is not None and item_object is None:
                    errors.append("{} has unknown item {!r}".format(entry_label, item))
                    entry_valid = False
                if requirement is not None and (
                    not isinstance(requirement, str) or not CATEGORY_PATTERN.fullmatch(requirement)
                ):
                    errors.append("{} has invalid requirement {!r}".format(entry_label, requirement))
                    entry_valid = False
                if item is None and requirement is None:
                    errors.append("{} must identify an item or requirement".format(entry_label))
                    entry_valid = False
                if section == "missing":
                    if item is not None or requirement is None:
                        errors.append("{} missing entries require a category and no item".format(entry_label))
                        entry_valid = False
                elif item is None:
                    errors.append("{} requires a physical item".format(entry_label))
                    entry_valid = False
                leg_id = raw_entry.get("leg")
                if leg_id is not None and leg_id not in known_legs:
                    errors.append("{} has unknown leg {!r}".format(entry_label, leg_id))
                    entry_valid = False
                container = raw_entry.get("container")
                if container is not None:
                    location = inventory_locations.get(container)
                    if location is None or location.kind != "travel_container":
                        errors.append("{} has invalid container {!r}".format(entry_label, container))
                        entry_valid = False
                    elif trip and container not in trip.luggage:
                        errors.append("{} container {!r} is not available for the trip".format(entry_label, container))
                        entry_valid = False
                source = raw_entry.get("source")
                destination = raw_entry.get("destination")
                for field, location_id in (("source", source), ("destination", destination)):
                    if location_id is not None and location_id not in known_locations:
                        errors.append("{} has unknown {} {!r}".format(entry_label, field, location_id))
                        entry_valid = False
                if section == "move_between_legs" and (source is None or destination is None):
                    errors.append("{} requires source and destination".format(entry_label))
                    entry_valid = False
                if section == "leave_at_destination" and destination is None:
                    errors.append("{} requires destination".format(entry_label))
                    entry_valid = False
                if section == "pack" and container is None:
                    errors.append("{} requires a container".format(entry_label))
                    entry_valid = False
                if section == "wear_in_transit" and container is not None:
                    errors.append("{} must not specify a container".format(entry_label))
                    entry_valid = False
                if section == "already_there":
                    if destination is None:
                        errors.append("{} requires destination".format(entry_label))
                        entry_valid = False
                    elif item_object is not None and item_object.current_location != destination:
                        errors.append(
                            "{} item is currently at {!r}, not {!r}".format(
                                entry_label, item_object.current_location, destination
                            )
                        )
                        entry_valid = False
                quantity = raw_entry.get("quantity")
                if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                    errors.append("{}.quantity must be a positive integer".format(entry_label))
                    entry_valid = False
                entry_valid &= _nonempty(raw_entry.get("reason"), "{}.reason".format(entry_label), errors)
                if entry_valid:
                    entries.append(
                        PackingPlanEntry(
                            item=item,
                            requirement=requirement,
                            leg=leg_id,
                            container=container,
                            source=source,
                            destination=destination,
                            quantity=quantity,
                            reason=raw_entry["reason"],
                        )
                    )
                else:
                    valid = False
            section_values[section] = tuple(entries)
        for section, entries in section_values.items():
            physical_items = [entry.item for entry in entries if entry.item is not None]
            duplicates = sorted(
                item_id for item_id in set(physical_items)
                if physical_items.count(item_id) > 1
            )
            if duplicates:
                errors.append(
                    "{}.sections.{} repeats physical items: {}".format(
                        label, section, ", ".join(duplicates)
                    )
                )
                valid = False
        packed = {entry.item for entry in section_values["pack"]}
        worn = {entry.item for entry in section_values["wear_in_transit"]}
        already_there = {entry.item for entry in section_values["already_there"]}
        excluded = {entry.item for entry in section_values["do_not_pack"]}
        conflicts = (packed | worn) & excluded
        if conflicts:
            errors.append(
                "{} marks items both brought and do_not_pack: {}".format(
                    label, ", ".join(sorted(conflicts))
                )
            )
            valid = False
        conflicts = packed & already_there
        if conflicts:
            errors.append(
                "{} marks items both pack and already_there: {}".format(
                    label, ", ".join(sorted(conflicts))
                )
            )
            valid = False
        if valid:
            plans.append(
                PackingPlan(
                    id=plan_id,
                    trip=trip_id,
                    status=status,
                    created_at=value["created_at"],
                    sections=MappingProxyType(section_values),
                    notes=value["notes"],
                )
            )
    if errors:
        raise PackingValidationError(errors)
    return PackingPlanCatalog(plans)


def load_packing_plans(data_dir: Optional[Path] = None) -> PackingPlanCatalog:
    directory = _data_directory(data_dir)
    return cached_catalog_load(
        "packing_plans",
        str(directory.resolve()),
        lambda: _parse_plan_store(
            _read_yaml(directory / "packing_plans.yaml"),
            _read_yaml(directory / "schema.yaml"),
            directory,
        ),
    )


def load_packing_plan(plan_id: str, data_dir: Optional[Path] = None) -> PackingPlan:
    return load_packing_plans(data_dir).get(plan_id)


def _prior_plan_usage_index(
    catalog: PackingPlanCatalog,
) -> Mapping[str, Tuple[PriorPlanUsage, ...]]:
    history: Dict[str, List[PriorPlanUsage]] = {}
    for plan in catalog.plans:
        sections_by_item: Dict[str, List[str]] = {}
        for section in PLAN_SECTIONS:
            for entry in plan.sections.get(section, ()):
                if entry.item is not None:
                    item_sections = sections_by_item.setdefault(entry.item, [])
                    if section not in item_sections:
                        item_sections.append(section)
        for item_id, sections in sections_by_item.items():
            history.setdefault(item_id, []).append(
                PriorPlanUsage(
                    plan_id=plan.id,
                    trip_id=plan.trip,
                    created_at=plan.created_at,
                    sections=tuple(sections),
                )
            )
    return MappingProxyType(
        {item_id: tuple(values) for item_id, values in history.items()}
    )


def _execution_usage_index(
    data_dir: Optional[Path],
) -> Mapping[str, Tuple[PriorExecutionUsage, ...]]:
    # Local import avoids a module cycle: execution validates its confirmed
    # packing-plan reference through this module.
    from .execution import load_trip_executions

    history: Dict[str, List[PriorExecutionUsage]] = {}
    for execution in load_trip_executions(data_dir).executions:
        for action in execution.actions:
            if action.item is None:
                continue
            history.setdefault(action.item, []).append(
                PriorExecutionUsage(
                    trip_id=execution.trip,
                    execution_id=execution.id,
                    kind=action.kind,
                    state=action.state,
                    timestamp=action.timestamp,
                )
            )
    return MappingProxyType(
        {item_id: tuple(values) for item_id, values in history.items()}
    )


def _entry_to_raw(entry: PackingPlanEntry) -> Dict[str, Any]:
    return {
        "item": entry.item,
        "requirement": entry.requirement,
        "leg": entry.leg,
        "container": entry.container,
        "source": entry.source,
        "destination": entry.destination,
        "quantity": entry.quantity,
        "reason": entry.reason,
    }


def _plan_to_raw(plan: PackingPlan) -> Dict[str, Any]:
    return {
        "id": plan.id,
        "trip": plan.trip,
        "status": plan.status,
        "created_at": plan.created_at,
        "sections": {
            section: [_entry_to_raw(entry) for entry in plan.sections.get(section, ())]
            for section in PLAN_SECTIONS
        },
        "notes": plan.notes,
    }


def _atomic_replace(path: Path, payload: str) -> None:
    mode = path.stat().st_mode
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".packing-plans-", suffix=".yaml",
            dir=str(path.parent), delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, str(path))
        temporary_name = None
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def save_packing_plan(
    plan: PackingPlan,
    *,
    replace_existing: bool = False,
    data_dir: Optional[Path] = None,
) -> PackingPlan:
    """Validate and save a recommendation without writing possession state."""

    directory = _data_directory(data_dir)
    path = directory / "packing_plans.yaml"
    lock_path = directory / ".packing-plans.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        raw = _read_yaml(path)
        schema = _read_yaml(directory / "schema.yaml")
        catalog = _parse_plan_store(raw, schema, directory)
        existing = {value.id for value in catalog.plans}
        if plan.id in existing and not replace_existing:
            raise PackingValidationError(
                ["packing plan {!r} already exists; replacement must be explicit".format(plan.id)]
            )
        existing_plan = catalog.get(plan.id) if plan.id in existing else None
        if existing_plan is not None and existing_plan.status == "confirmed":
            raise PackingValidationError(
                ["confirmed packing plan {!r} is immutable; create a new plan ID".format(plan.id)]
            )
        raw_plan = _plan_to_raw(plan)
        raw_plans = raw["plans"]
        if plan.id in existing:
            raw_plans[next(index for index, value in enumerate(raw_plans) if value.get("id") == plan.id)] = raw_plan
        else:
            raw_plans.append(raw_plan)
        validated = _parse_plan_store(raw, schema, directory)
        payload = yaml.safe_dump(
            raw, allow_unicode=True, default_flow_style=False, sort_keys=False, width=1000
        )
        _parse_plan_store(yaml.safe_load(payload), schema, directory)
        _atomic_replace(path, payload)
        return validated.get(plan.id)


def confirm_packing_plan(
    plan_id: str, *, data_dir: Optional[Path] = None
) -> PackingPlan:
    """Explicitly accept a draft plan and make its decision references immutable."""

    plan = load_packing_plan(plan_id, data_dir)
    if plan.status != "draft":
        raise PackingValidationError(
            ["packing plan {!r} is already {!r}".format(plan_id, plan.status)]
        )
    return save_packing_plan(
        PackingPlan(
            id=plan.id,
            trip=plan.trip,
            status="confirmed",
            created_at=plan.created_at,
            sections=plan.sections,
            notes=plan.notes,
        ),
        replace_existing=True,
        data_dir=data_dir,
    )


def load_packing_plan_document(path: Path) -> PackingPlan:
    """Load one standalone plan document for the CLI; store validation happens on save."""

    raw = _read_yaml(Path(path))
    if not isinstance(raw, dict):
        raise PackingValidationError(["packing plan input must be a mapping"])
    missing = PLAN_FIELDS - set(raw)
    extra = set(raw) - PLAN_FIELDS
    if missing or extra:
        details = []
        if missing:
            details.append("missing fields: {}".format(", ".join(sorted(missing))))
        if extra:
            details.append("unknown fields: {}".format(", ".join(sorted(extra))))
        raise PackingValidationError(["packing plan input {}".format("; ".join(details))])
    sections = raw.get("sections")
    if not isinstance(sections, dict):
        raise PackingValidationError(["packing plan input sections must be a mapping"])
    missing_sections = set(PLAN_SECTIONS) - set(sections)
    extra_sections = set(sections) - set(PLAN_SECTIONS)
    if missing_sections or extra_sections:
        details = []
        if missing_sections:
            details.append("missing sections: {}".format(", ".join(sorted(missing_sections))))
        if extra_sections:
            details.append("unknown sections: {}".format(", ".join(sorted(extra_sections))))
        raise PackingValidationError(["packing plan input {}".format("; ".join(details))])
    parsed_sections = {}
    for section in PLAN_SECTIONS:
        entries = sections.get(section)
        if not isinstance(entries, list):
            raise PackingValidationError(["packing plan input section {!r} must be a list".format(section)])
        parsed_entries = []
        for index, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                raise PackingValidationError(["packing plan input entries must be mappings"])
            if set(entry) != ENTRY_FIELDS:
                raise PackingValidationError(
                    [
                        "packing plan input section {!r} entry {} must contain exactly: {}".format(
                            section, index, ", ".join(sorted(ENTRY_FIELDS))
                        )
                    ]
                )
            parsed_entries.append(
                PackingPlanEntry(
                    item=entry.get("item"),
                    requirement=entry.get("requirement"),
                    leg=entry.get("leg"),
                    container=entry.get("container"),
                    source=entry.get("source"),
                    destination=entry.get("destination"),
                    quantity=entry.get("quantity"),
                    reason=entry.get("reason"),
                )
            )
        parsed_sections[section] = tuple(parsed_entries)
    return PackingPlan(
        id=raw.get("id"), trip=raw.get("trip"), status=raw.get("status"),
        created_at=raw.get("created_at"), sections=MappingProxyType(parsed_sections), notes=raw.get("notes"),
    )
