"""Structured trip models, validation, queries, and persistence.

Trips are intentionally independent from possession movement state. Updating a
trip or its status writes only ``trips.yaml``.
"""

import fcntl
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import yaml

from .loader import load_inventory
from .load_context import cached_catalog_load
from .paths import default_data_directory
from .requirements import (
    RequirementOverrides,
    RequirementTemplateNotFoundError,
    load_requirement_templates,
    parse_requirement_overrides,
)

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TRIP_FIELDS = {
    "id", "name", "status", "start_date", "end_date", "places", "legs",
    "luggage", "attachments", "planning", "notes",
}
PLACE_FIELDS = {"id", "name", "notes"}
LEG_FIELDS = {"id", "origin", "destination", "departure", "arrival", "transport_notes"}
ATTACHMENT_FIELDS = {
    "id", "kind", "name", "type", "leg", "location", "date", "frequency",
    "requirements", "notes",
}
PLANNING_FIELDS = {"legs", "notes"}
LEG_PLANNING_FIELDS = {
    "leg", "activities_complete", "laundry_available", "weather", "context", "notes",
}
TRIP_STATUS_TRANSITIONS = {
    "planned": frozenset({"in_progress", "cancelled"}),
    "in_progress": frozenset({"completed", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}
WEATHER_FIELDS = {"summary", "low_c", "high_c", "precipitation", "source", "checked_at"}


class TripValidationError(ValueError):
    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("Trip validation failed:\n- " + "\n- ".join(self.errors))


class TripNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class TripPlace:
    id: str
    name: str
    notes: Optional[str] = None


@dataclass(frozen=True)
class TripLeg:
    id: str
    origin: str
    destination: str
    departure: date
    arrival: date
    transport_notes: Optional[str] = None


@dataclass(frozen=True)
class TripAttachment:
    id: str
    kind: str
    name: str
    type: str
    leg: Optional[str] = None
    location: Optional[str] = None
    date: Optional[date] = None
    frequency: int = 1
    requirements: RequirementOverrides = RequirementOverrides()
    notes: Optional[str] = None


@dataclass(frozen=True)
class TripWeather:
    summary: Optional[str] = None
    low_c: Optional[float] = None
    high_c: Optional[float] = None
    precipitation: Optional[str] = None
    source: Optional[str] = None
    checked_at: Optional[str] = None


@dataclass(frozen=True)
class TripLegPlanning:
    leg: str
    activities_complete: Optional[bool] = None
    laundry_available: Optional[bool] = None
    weather: TripWeather = TripWeather()
    context: Tuple[str, ...] = ()
    notes: Optional[str] = None


@dataclass(frozen=True)
class TripPlanning:
    legs: Tuple[TripLegPlanning, ...]
    notes: Optional[str] = None


@dataclass(frozen=True)
class Trip:
    id: str
    name: str
    status: str
    start_date: date
    end_date: date
    places: Tuple[TripPlace, ...]
    legs: Tuple[TripLeg, ...]
    luggage: Tuple[str, ...]
    attachments: Tuple[TripAttachment, ...]
    planning: TripPlanning
    notes: Optional[str]

    @property
    def duration_days(self) -> int:
        """Calendar-day duration, inclusive of both start and end dates."""
        return (self.end_date - self.start_date).days + 1

    @property
    def destinations(self) -> Tuple[str, ...]:
        return tuple(leg.destination for leg in self.legs)

    def current_leg(self, on_date: Optional[Union[str, date]] = None) -> Optional[TripLeg]:
        target = _coerce_query_date(on_date)
        # Date-only data cannot order two connections on the same date. The
        # first matching leg is treated as current and the following one as next.
        return next(
            (leg for leg in self.legs if leg.departure <= target <= leg.arrival),
            None,
        )

    def next_leg(self, on_date: Optional[Union[str, date]] = None) -> Optional[TripLeg]:
        target = _coerce_query_date(on_date)
        current = self.current_leg(target)
        if current is not None:
            index = self.legs.index(current) + 1
            return self.legs[index] if index < len(self.legs) else None
        return next((leg for leg in self.legs if leg.departure > target), None)


class TripCatalog:
    def __init__(self, trips: Sequence[Trip]):
        self._trips = tuple(trips)
        self._by_id = MappingProxyType({trip.id: trip for trip in trips})

    @property
    def trips(self) -> Tuple[Trip, ...]:
        return self._trips

    def get(self, trip_id: str) -> Trip:
        try:
            return self._by_id[trip_id]
        except KeyError:
            raise TripNotFoundError("unknown trip {!r}".format(trip_id))


def _data_directory(data_dir: Optional[Path]) -> Path:
    return Path(data_dir) if data_dir is not None else default_data_directory()


def _read_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError:
        raise TripValidationError(["missing file: {}".format(path)])
    except yaml.YAMLError as exc:
        raise TripValidationError(["{}: invalid YAML: {}".format(path.name, exc)])


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


def _optional_number(value: Any, label: str, errors: List[str]) -> bool:
    valid = value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))
    if not valid:
        errors.append("{} must be a number or null".format(label))
    return valid


def _optional_timestamp(value: Any, label: str, errors: List[str]) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        errors.append("{} must be a timezone-aware ISO 8601 timestamp or null".format(label))
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append("{} must be a timezone-aware ISO 8601 timestamp or null".format(label))
        return False
    return True


def _identifier(value: Any, label: str, errors: List[str]) -> bool:
    valid = isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None
    if not valid:
        errors.append("{} has invalid id {!r}".format(label, value))
    return valid


def _date_value(value: Any, label: str, errors: List[str]) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError
            return parsed
        except ValueError:
            pass
    errors.append("{} must be an ISO 8601 date (YYYY-MM-DD)".format(label))
    return None


def _coerce_query_date(value: Optional[Union[str, date]]) -> date:
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("query date must use YYYY-MM-DD")


def _parse_trips(raw: Any, schema: Any, data_dir: Path) -> TripCatalog:
    errors: List[str] = []
    if not isinstance(schema, dict):
        raise TripValidationError(["schema.yaml: root must be a mapping"])
    if schema.get("version") != 8:
        errors.append("schema.yaml: version must be 8")
    store_schema = schema.get("trip_store")
    if not isinstance(store_schema, dict) or store_schema.get("root") != "mapping":
        errors.append("schema.yaml: trip_store.root must be 'mapping'")
        store_schema = {}
    for section in ("trips", "trip_places", "trip_legs", "trip_attachments", "trip_leg_planning"):
        section_schema = schema.get(section)
        if not isinstance(section_schema, dict) or section_schema.get("root") != "list":
            errors.append("schema.yaml: {}.root must be 'list'".format(section))
    planning_schema = schema.get("trip_planning")
    if not isinstance(planning_schema, dict) or planning_schema.get("root") != "mapping":
        errors.append("schema.yaml: trip_planning.root must be 'mapping'")
    weather_schema = schema.get("trip_weather")
    if not isinstance(weather_schema, dict) or weather_schema.get("root") != "mapping":
        errors.append("schema.yaml: trip_weather.root must be 'mapping'")

    if not isinstance(raw, dict):
        errors.append("trips.yaml: root must be a mapping")
        raw = {}
    required_sections = store_schema.get("required_sections", [])
    if not isinstance(required_sections, list):
        errors.append("schema.yaml: trip_store.required_sections must be a list")
        required_sections = []
    missing_sections = set(required_sections) - set(raw)
    extra_sections = set(raw) - {"trips"}
    if missing_sections:
        errors.append("trips.yaml missing sections: {}".format(", ".join(sorted(missing_sections))))
    if extra_sections:
        errors.append("trips.yaml has unknown sections: {}".format(", ".join(sorted(extra_sections))))
    raw_trips = raw.get("trips")
    if not isinstance(raw_trips, list):
        errors.append("trips.yaml: trips must be a list")
        raw_trips = []

    inventory = load_inventory(data_dir)
    templates = load_requirement_templates(data_dir)
    inventory_locations = {location.id: location for location in inventory.locations}
    allowed_statuses = schema.get("trips", {}).get("allowed_statuses", [])
    allowed_attachment_kinds = schema.get("trip_attachments", {}).get("allowed_kinds", [])
    override_required = schema.get("requirement_overrides", {}).get("required_fields", [])
    if not isinstance(override_required, list):
        errors.append("schema.yaml: requirement_overrides.required_fields must be a list")
        override_required = []
    trips: List[Trip] = []
    seen_trip_ids = set()

    for trip_index, value in enumerate(raw_trips, 1):
        label = "trips.yaml trip {}".format(trip_index)
        if not isinstance(value, dict):
            errors.append("{} must be a mapping".format(label))
            continue
        valid = _check_fields(value, _required(schema, "trips", errors), TRIP_FIELDS, label, errors)
        trip_id = value.get("id")
        id_valid = _identifier(trip_id, label, errors)
        if id_valid and trip_id in seen_trip_ids:
            errors.append("trips.yaml has duplicate trip id {}".format(trip_id))
            id_valid = False
        elif id_valid:
            seen_trip_ids.add(trip_id)
        valid &= id_valid
        valid &= _nonempty(value.get("name"), "{}.name".format(label), errors)
        status = value.get("status")
        if status not in allowed_statuses:
            errors.append("{} has unknown status {!r}".format(label, status))
            valid = False
        start_date = _date_value(value.get("start_date"), "{}.start_date".format(label), errors)
        end_date = _date_value(value.get("end_date"), "{}.end_date".format(label), errors)
        if start_date and end_date and start_date > end_date:
            errors.append("{}.start_date must not be after end_date".format(label))
            valid = False
        valid &= _optional_string(value.get("notes"), "{}.notes".format(label), errors)

        places: List[TripPlace] = []
        raw_places = value.get("places")
        if not isinstance(raw_places, list):
            errors.append("{}.places must be a list".format(label))
            valid = False
            raw_places = []
        place_ids = set()
        for place_index, place in enumerate(raw_places, 1):
            place_label = "{}.places entry {}".format(label, place_index)
            if not isinstance(place, dict):
                errors.append("{} must be a mapping".format(place_label))
                valid = False
                continue
            place_valid = _check_fields(
                place, _required(schema, "trip_places", errors), PLACE_FIELDS, place_label, errors
            )
            place_id = place.get("id")
            place_valid &= _identifier(place_id, place_label, errors)
            if place_id in place_ids or place_id in inventory_locations:
                errors.append("{} id {!r} conflicts with another location".format(place_label, place_id))
                place_valid = False
            place_ids.add(place_id)
            place_valid &= _nonempty(place.get("name"), "{}.name".format(place_label), errors)
            place_valid &= _optional_string(place.get("notes"), "{}.notes".format(place_label), errors)
            if place_valid:
                places.append(TripPlace(id=place_id, name=place["name"], notes=place["notes"]))
            else:
                valid = False
        known_locations = set(inventory_locations) | place_ids

        legs: List[TripLeg] = []
        raw_legs = value.get("legs")
        if not isinstance(raw_legs, list) or not raw_legs:
            errors.append("{}.legs must be a non-empty list".format(label))
            valid = False
            raw_legs = []
        leg_ids = set()
        for leg_index, leg in enumerate(raw_legs, 1):
            leg_label = "{}.legs entry {}".format(label, leg_index)
            if not isinstance(leg, dict):
                errors.append("{} must be a mapping".format(leg_label))
                valid = False
                continue
            leg_valid = _check_fields(
                leg, _required(schema, "trip_legs", errors), LEG_FIELDS, leg_label, errors
            )
            leg_id = leg.get("id")
            leg_valid &= _identifier(leg_id, leg_label, errors)
            if leg_id in leg_ids:
                errors.append("{} has duplicate leg id {!r}".format(label, leg_id))
                leg_valid = False
            leg_ids.add(leg_id)
            origin = leg.get("origin")
            destination = leg.get("destination")
            if origin not in known_locations:
                errors.append("{} has unknown origin {!r}".format(leg_label, origin))
                leg_valid = False
            if destination not in known_locations:
                errors.append("{} has unknown destination {!r}".format(leg_label, destination))
                leg_valid = False
            if origin == destination and origin in known_locations:
                errors.append("{} origin and destination must differ".format(leg_label))
                leg_valid = False
            departure = _date_value(leg.get("departure"), "{}.departure".format(leg_label), errors)
            arrival = _date_value(leg.get("arrival"), "{}.arrival".format(leg_label), errors)
            if departure and arrival and departure > arrival:
                errors.append("{}.departure must not be after arrival".format(leg_label))
                leg_valid = False
            if start_date and departure and departure < start_date:
                errors.append("{}.departure is before trip start_date".format(leg_label))
                leg_valid = False
            if end_date and arrival and arrival > end_date:
                errors.append("{}.arrival is after trip end_date".format(leg_label))
                leg_valid = False
            leg_valid &= _optional_string(
                leg.get("transport_notes"), "{}.transport_notes".format(leg_label), errors
            )
            if leg_valid:
                legs.append(
                    TripLeg(
                        id=leg_id,
                        origin=origin,
                        destination=destination,
                        departure=departure,
                        arrival=arrival,
                        transport_notes=leg["transport_notes"],
                    )
                )
            else:
                valid = False

        for previous, following in zip(legs, legs[1:]):
            if following.departure < previous.arrival:
                errors.append(
                    "{} leg {!r} departs before preceding leg {!r} arrives".format(
                        label, following.id, previous.id
                    )
                )
                valid = False
            if following.origin != previous.destination:
                errors.append(
                    "{} leg {!r} origin {!r} does not continue from {!r}".format(
                        label, following.id, following.origin, previous.destination
                    )
                )
                valid = False

        raw_luggage = value.get("luggage")
        luggage: Tuple[str, ...] = ()
        if not isinstance(raw_luggage, list) or not all(isinstance(item, str) for item in raw_luggage):
            errors.append("{}.luggage must be a list of location IDs".format(label))
            valid = False
        else:
            if len(raw_luggage) != len(set(raw_luggage)):
                errors.append("{}.luggage contains duplicates".format(label))
                valid = False
            for luggage_id in raw_luggage:
                location = inventory_locations.get(luggage_id)
                if location is None or location.kind != "travel_container":
                    errors.append("{} luggage {!r} is not a travel-container location".format(label, luggage_id))
                    valid = False
            luggage = tuple(raw_luggage)

        attachments: List[TripAttachment] = []
        raw_attachments = value.get("attachments")
        if not isinstance(raw_attachments, list):
            errors.append("{}.attachments must be a list".format(label))
            valid = False
            raw_attachments = []
        attachment_ids = set()
        legs_by_id = {leg.id: leg for leg in legs}
        for attachment_index, attachment in enumerate(raw_attachments, 1):
            attachment_label = "{}.attachments entry {}".format(label, attachment_index)
            if not isinstance(attachment, dict):
                errors.append("{} must be a mapping".format(attachment_label))
                valid = False
                continue
            attachment_valid = _check_fields(
                attachment,
                _required(schema, "trip_attachments", errors),
                ATTACHMENT_FIELDS,
                attachment_label,
                errors,
            )
            attachment_id = attachment.get("id")
            attachment_valid &= _identifier(attachment_id, attachment_label, errors)
            if attachment_id in attachment_ids:
                errors.append("{} has duplicate attachment id {!r}".format(label, attachment_id))
                attachment_valid = False
            attachment_ids.add(attachment_id)
            kind = attachment.get("kind")
            if kind not in allowed_attachment_kinds:
                errors.append("{} has unknown kind {!r}".format(attachment_label, kind))
                attachment_valid = False
            template_id = attachment.get("type")
            try:
                templates.get(template_id)
            except RequirementTemplateNotFoundError:
                errors.append("{} has unknown type {!r}".format(attachment_label, template_id))
                attachment_valid = False
            attachment_valid &= _nonempty(
                attachment.get("name"), "{}.name".format(attachment_label), errors
            )
            leg_id = attachment.get("leg")
            location_id = attachment.get("location")
            attachment_date = None
            if attachment.get("date") is not None:
                attachment_date = _date_value(
                    attachment.get("date"), "{}.date".format(attachment_label), errors
                )
                if attachment_date is None:
                    attachment_valid = False
            if leg_id is not None and leg_id not in legs_by_id:
                errors.append("{} has unknown leg {!r}".format(attachment_label, leg_id))
                attachment_valid = False
            if location_id is not None and location_id not in known_locations:
                errors.append("{} has unknown location {!r}".format(attachment_label, location_id))
                attachment_valid = False
            if leg_id is None and location_id is None and attachment_date is None:
                errors.append("{} must attach to a leg, location, date, or a combination".format(attachment_label))
                attachment_valid = False
            if attachment_date and start_date and end_date and not start_date <= attachment_date <= end_date:
                errors.append("{}.date is outside the trip dates".format(attachment_label))
                attachment_valid = False
            if leg_id in legs_by_id and attachment_date:
                leg = legs_by_id[leg_id]
                if not leg.departure <= attachment_date <= leg.arrival:
                    errors.append("{}.date is outside its attached leg dates".format(attachment_label))
                    attachment_valid = False
            frequency = attachment.get("frequency")
            if isinstance(frequency, bool) or not isinstance(frequency, int) or frequency <= 0:
                errors.append("{}.frequency must be a positive integer".format(attachment_label))
                attachment_valid = False
            override_error_count = len(errors)
            overrides = parse_requirement_overrides(
                attachment.get("requirements"),
                override_required,
                "{}.requirements".format(attachment_label),
                errors,
            )
            if len(errors) != override_error_count:
                attachment_valid = False
            attachment_valid &= _optional_string(
                attachment.get("notes"), "{}.notes".format(attachment_label), errors
            )
            if attachment_valid:
                attachments.append(
                    TripAttachment(
                        id=attachment_id,
                        kind=kind,
                        name=attachment["name"],
                        type=template_id,
                        leg=leg_id,
                        location=location_id,
                        date=attachment_date,
                        frequency=frequency,
                        requirements=overrides,
                        notes=attachment["notes"],
                    )
                )
            else:
                valid = False

        planning_legs: List[TripLegPlanning] = []
        raw_planning = value.get("planning")
        planning_notes = None
        if not isinstance(raw_planning, dict):
            errors.append("{}.planning must be a mapping".format(label))
            valid = False
            raw_planning = {}
        else:
            valid &= _check_fields(
                raw_planning,
                _required(schema, "trip_planning", errors),
                PLANNING_FIELDS,
                "{}.planning".format(label),
                errors,
            )
            planning_notes = raw_planning.get("notes")
            valid &= _optional_string(
                planning_notes, "{}.planning.notes".format(label), errors
            )

        raw_planning_legs = raw_planning.get("legs")
        if not isinstance(raw_planning_legs, list):
            errors.append("{}.planning.legs must be a list".format(label))
            valid = False
            raw_planning_legs = []
        seen_planning_legs = set()
        for planning_index, entry in enumerate(raw_planning_legs, 1):
            planning_label = "{}.planning.legs entry {}".format(label, planning_index)
            if not isinstance(entry, dict):
                errors.append("{} must be a mapping".format(planning_label))
                valid = False
                continue
            entry_valid = _check_fields(
                entry,
                _required(schema, "trip_leg_planning", errors),
                LEG_PLANNING_FIELDS,
                planning_label,
                errors,
            )
            planning_leg_id = entry.get("leg")
            if planning_leg_id not in leg_ids:
                errors.append("{} has unknown leg {!r}".format(planning_label, planning_leg_id))
                entry_valid = False
            if planning_leg_id in seen_planning_legs:
                errors.append("{} has duplicate planning for leg {!r}".format(label, planning_leg_id))
                entry_valid = False
            seen_planning_legs.add(planning_leg_id)
            for field in ("activities_complete", "laundry_available"):
                answer = entry.get(field)
                if answer is not None and not isinstance(answer, bool):
                    errors.append("{}.{} must be true, false, or null".format(planning_label, field))
                    entry_valid = False
            entry_valid &= _optional_string(
                entry.get("notes"), "{}.notes".format(planning_label), errors
            )
            raw_weather = entry.get("weather")
            weather = TripWeather()
            if not isinstance(raw_weather, dict):
                errors.append("{}.weather must be a mapping".format(planning_label))
                entry_valid = False
            else:
                entry_valid &= _check_fields(
                    raw_weather,
                    _required(schema, "trip_weather", errors),
                    WEATHER_FIELDS,
                    "{}.weather".format(planning_label),
                    errors,
                )
                for field in ("summary", "precipitation", "source"):
                    entry_valid &= _optional_string(
                        raw_weather.get(field),
                        "{}.weather.{}".format(planning_label, field),
                        errors,
                    )
                for field in ("low_c", "high_c"):
                    entry_valid &= _optional_number(
                        raw_weather.get(field),
                        "{}.weather.{}".format(planning_label, field),
                        errors,
                    )
                entry_valid &= _optional_timestamp(
                    raw_weather.get("checked_at"),
                    "{}.weather.checked_at".format(planning_label),
                    errors,
                )
                low_c = raw_weather.get("low_c")
                high_c = raw_weather.get("high_c")
                if (
                    isinstance(low_c, (int, float)) and not isinstance(low_c, bool)
                    and isinstance(high_c, (int, float)) and not isinstance(high_c, bool)
                    and low_c > high_c
                ):
                    errors.append("{}.weather.low_c must not exceed high_c".format(planning_label))
                    entry_valid = False
                weather = TripWeather(
                    summary=raw_weather.get("summary"),
                    low_c=low_c,
                    high_c=high_c,
                    precipitation=raw_weather.get("precipitation"),
                    source=raw_weather.get("source"),
                    checked_at=raw_weather.get("checked_at"),
                )
            raw_context = entry.get("context")
            context: Tuple[str, ...] = ()
            if (
                not isinstance(raw_context, list)
                or not all(isinstance(note, str) and bool(note.strip()) for note in raw_context)
            ):
                errors.append("{}.context must be a list of non-empty strings".format(planning_label))
                entry_valid = False
            elif len(raw_context) != len(set(raw_context)):
                errors.append("{}.context contains duplicates".format(planning_label))
                entry_valid = False
            else:
                context = tuple(raw_context)
            if entry_valid:
                planning_legs.append(
                    TripLegPlanning(
                        leg=planning_leg_id,
                        activities_complete=entry["activities_complete"],
                        laundry_available=entry["laundry_available"],
                        weather=weather,
                        context=context,
                        notes=entry["notes"],
                    )
                )
            else:
                valid = False
        if seen_planning_legs != leg_ids:
            missing_planning = leg_ids - seen_planning_legs
            if missing_planning:
                errors.append(
                    "{}.planning is missing legs: {}".format(
                        label, ", ".join(sorted(missing_planning))
                    )
                )
            valid = False

        if valid and start_date and end_date:
            trips.append(
                Trip(
                    id=trip_id,
                    name=value["name"],
                    status=status,
                    start_date=start_date,
                    end_date=end_date,
                    places=tuple(places),
                    legs=tuple(legs),
                    luggage=luggage,
                    attachments=tuple(attachments),
                    planning=TripPlanning(tuple(planning_legs), planning_notes),
                    notes=value["notes"],
                )
            )

    if errors:
        raise TripValidationError(errors)
    return TripCatalog(trips)


def load_trips(data_dir: Optional[Path] = None) -> TripCatalog:
    directory = _data_directory(data_dir)
    return cached_catalog_load(
        "trips",
        str(directory.resolve()),
        lambda: _parse_trips(
            _read_yaml(directory / "trips.yaml"),
            _read_yaml(directory / "schema.yaml"),
            directory,
        ),
    )


def load_trip(trip_id: str, data_dir: Optional[Path] = None) -> Trip:
    return load_trips(data_dir).get(trip_id)


def _trip_to_raw(trip: Trip) -> Dict[str, Any]:
    return {
        "id": trip.id,
        "name": trip.name,
        "status": trip.status,
        "start_date": trip.start_date.isoformat(),
        "end_date": trip.end_date.isoformat(),
        "places": [
            {"id": place.id, "name": place.name, "notes": place.notes}
            for place in trip.places
        ],
        "legs": [
            {
                "id": leg.id,
                "origin": leg.origin,
                "destination": leg.destination,
                "departure": leg.departure.isoformat(),
                "arrival": leg.arrival.isoformat(),
                "transport_notes": leg.transport_notes,
            }
            for leg in trip.legs
        ],
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
                    "add": [_requirement_to_raw(entry) for entry in attachment.requirements.add],
                    "add_optional": [
                        _requirement_to_raw(entry) for entry in attachment.requirements.add_optional
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


def _requirement_to_raw(requirement: Any) -> Any:
    if requirement.count == 1:
        return requirement.category
    return {"category": requirement.category, "count": requirement.count}


def _atomic_replace(path: Path, payload: str) -> None:
    mode = path.stat().st_mode
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".trips-", suffix=".yaml",
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


def _mutate_trips(
    data_dir: Optional[Path],
    mutation: Callable[[List[Dict[str, Any]], TripCatalog], None],
) -> TripCatalog:
    directory = _data_directory(data_dir)
    path = directory / "trips.yaml"
    lock_path = directory / ".trips.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        raw = _read_yaml(path)
        if not isinstance(raw, dict) or not isinstance(raw.get("trips"), list):
            raise TripValidationError(["trips.yaml must contain a trips list"])
        schema = _read_yaml(directory / "schema.yaml")
        current_catalog = _parse_trips(raw, schema, directory)
        mutation(raw["trips"], current_catalog)
        catalog = _parse_trips(raw, schema, directory)
        payload = yaml.safe_dump(
            raw, allow_unicode=True, default_flow_style=False, sort_keys=False, width=1000
        )
        round_trip = yaml.safe_load(payload)
        _parse_trips(round_trip, schema, directory)
        _atomic_replace(path, payload)
        return catalog


def create_trip(
    trip_id: str,
    name: str,
    status: str,
    start_date: Union[str, date],
    end_date: Union[str, date],
    legs: Sequence[TripLeg],
    *,
    luggage: Sequence[str] = (),
    places: Sequence[TripPlace] = (),
    attachments: Sequence[TripAttachment] = (),
    notes: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> Trip:
    trip = Trip(
        id=trip_id,
        name=name,
        status=status,
        start_date=_coerce_query_date(start_date),
        end_date=_coerce_query_date(end_date),
        places=tuple(places),
        legs=tuple(legs),
        luggage=tuple(luggage),
        attachments=tuple(attachments),
        planning=TripPlanning(
            tuple(TripLegPlanning(leg=leg.id) for leg in legs)
        ),
        notes=notes,
    )

    def add(raw_trips: List[Dict[str, Any]], _catalog: TripCatalog) -> None:
        if any(value.get("id") == trip_id for value in raw_trips if isinstance(value, dict)):
            raise TripValidationError(["trip {!r} already exists".format(trip_id)])
        raw_trips.append(_trip_to_raw(trip))

    return _mutate_trips(data_dir, add).get(trip_id)


_UNSET = object()


def update_trip(
    trip_id: str,
    *,
    name: Any = _UNSET,
    start_date: Any = _UNSET,
    end_date: Any = _UNSET,
    legs: Any = _UNSET,
    luggage: Any = _UNSET,
    places: Any = _UNSET,
    attachments: Any = _UNSET,
    planning: Any = _UNSET,
    notes: Any = _UNSET,
    data_dir: Optional[Path] = None,
) -> Trip:
    """Update trip details without changing status or possession state."""

    updates = {
        "name": name,
        "start_date": start_date,
        "end_date": end_date,
        "legs": legs,
        "luggage": luggage,
        "places": places,
        "attachments": attachments,
        "planning": planning,
        "notes": notes,
    }
    if all(value is _UNSET for value in updates.values()):
        raise ValueError("at least one trip field update is required")

    def apply(raw_trips: List[Dict[str, Any]], catalog: TripCatalog) -> None:
        for index, raw in enumerate(raw_trips):
            if raw.get("id") != trip_id:
                continue
            current = catalog.get(trip_id)
            updated_legs = current.legs if legs is _UNSET else tuple(legs)
            if planning is not _UNSET:
                updated_planning = planning
            elif legs is _UNSET:
                updated_planning = current.planning
            else:
                prior = {entry.leg: entry for entry in current.planning.legs}
                updated_planning = TripPlanning(
                    tuple(prior.get(leg.id, TripLegPlanning(leg=leg.id)) for leg in updated_legs),
                    current.planning.notes,
                )
            updated = replace(
                current,
                name=current.name if name is _UNSET else name,
                start_date=current.start_date if start_date is _UNSET else _coerce_query_date(start_date),
                end_date=current.end_date if end_date is _UNSET else _coerce_query_date(end_date),
                legs=updated_legs,
                luggage=current.luggage if luggage is _UNSET else tuple(luggage),
                places=current.places if places is _UNSET else tuple(places),
                attachments=current.attachments if attachments is _UNSET else tuple(attachments),
                planning=updated_planning,
                notes=current.notes if notes is _UNSET else notes,
            )
            raw_trips[index] = _trip_to_raw(updated)
            return
        raise TripNotFoundError("unknown trip {!r}".format(trip_id))

    return _mutate_trips(data_dir, apply).get(trip_id)


def update_trip_leg_planning(
    trip_id: str,
    leg_id: str,
    *,
    activities_complete: Any = _UNSET,
    laundry_available: Any = _UNSET,
    weather: Any = _UNSET,
    context: Any = _UNSET,
    notes: Any = _UNSET,
    data_dir: Optional[Path] = None,
) -> Trip:
    """Persist explicit interview answers for one leg, without touching inventory."""

    if all(value is _UNSET for value in (activities_complete, laundry_available, weather, context, notes)):
        raise ValueError("at least one planning answer is required")
    for field, value in (
        ("activities_complete", activities_complete),
        ("laundry_available", laundry_available),
    ):
        if value is not _UNSET and value is not None and not isinstance(value, bool):
            raise ValueError("{} must be true, false, or null".format(field))

    def apply(raw_trips: List[Dict[str, Any]], catalog: TripCatalog) -> None:
        trip = catalog.get(trip_id)
        if leg_id not in {leg.id for leg in trip.legs}:
            raise ValueError("trip {!r} has no leg {!r}".format(trip_id, leg_id))
        updated_entries = []
        for entry in trip.planning.legs:
            if entry.leg == leg_id:
                entry = replace(
                    entry,
                    activities_complete=(
                        entry.activities_complete
                        if activities_complete is _UNSET
                        else activities_complete
                    ),
                    laundry_available=(
                        entry.laundry_available
                        if laundry_available is _UNSET
                        else laundry_available
                    ),
                    weather=entry.weather if weather is _UNSET else weather,
                    context=entry.context if context is _UNSET else tuple(context),
                    notes=entry.notes if notes is _UNSET else notes,
                )
            updated_entries.append(entry)
        updated = replace(
            trip,
            planning=replace(trip.planning, legs=tuple(updated_entries)),
        )
        for index, raw in enumerate(raw_trips):
            if raw.get("id") == trip_id:
                raw_trips[index] = _trip_to_raw(updated)
                return
        raise TripNotFoundError("unknown trip {!r}".format(trip_id))

    return _mutate_trips(data_dir, apply).get(trip_id)


def _transition_trip_status(
    trip_id: str,
    status: str,
    *,
    allow_completion: bool,
    data_dir: Optional[Path] = None,
) -> Trip:
    """Apply a validated forward-only status transition."""

    def apply(raw_trips: List[Dict[str, Any]], catalog: TripCatalog) -> None:
        current = catalog.get(trip_id)
        if status not in TRIP_STATUS_TRANSITIONS.get(current.status, frozenset()):
            raise TripValidationError(
                ["trip status cannot transition from {!r} to {!r}".format(current.status, status)]
            )
        if status == "completed" and not allow_completion:
            raise TripValidationError(
                ["trip completion requires the execution reconciliation workflow"]
            )
        for raw in raw_trips:
            if raw.get("id") == trip_id:
                raw["status"] = status
                return
        raise TripNotFoundError("unknown trip {!r}".format(trip_id))

    return _mutate_trips(data_dir, apply).get(trip_id)


def set_trip_status(trip_id: str, status: str, *, data_dir: Optional[Path] = None) -> Trip:
    """Explicitly advance or cancel a trip; completion is reconciliation-gated."""

    return _transition_trip_status(
        trip_id, status, allow_completion=False, data_dir=data_dir
    )


def complete_trip_after_reconciliation(
    trip_id: str, *, data_dir: Optional[Path] = None
) -> Trip:
    """Internal execution-layer hook after reconciliation has validated."""

    return _transition_trip_status(
        trip_id, "completed", allow_completion=True, data_dir=data_dir
    )


def add_trip_attachment(
    trip_id: str,
    attachment: TripAttachment,
    *,
    data_dir: Optional[Path] = None,
) -> Trip:
    def apply(raw_trips: List[Dict[str, Any]], catalog: TripCatalog) -> None:
        trip = catalog.get(trip_id)
        for index, raw in enumerate(raw_trips):
            if raw.get("id") == trip_id:
                raw_trips[index] = _trip_to_raw(
                    replace(trip, attachments=trip.attachments + (attachment,))
                )
                return
        raise TripNotFoundError("unknown trip {!r}".format(trip_id))

    return _mutate_trips(data_dir, apply).get(trip_id)
