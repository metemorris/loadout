"""Load YAML files, validate them, and construct immutable domain objects."""

import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised only before installation
    raise ImportError(
        "PyYAML is required; install the project with `python3 -m pip install -e .`"
    ) from exc

from .models import (
    AttributeValue,
    InventorySource,
    ItemDefinition,
    Location,
    Movement,
    PhysicalItem,
)
from .query import Inventory

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
LOCATION_FIELDS = {"id", "name", "kind", "city", "region", "country", "aliases", "notes"}
DEFINITION_FIELDS = {"id", "name", "type", "attributes", "use", "notes", "sources"}
SOURCE_FIELDS = {"id", "original_location", "original_quantity", "original_condition", "notes"}
INSTANCE_FIELDS = {
    "id",
    "definition",
    "source",
    "current_location",
    "preferred_location",
    "condition",
    "status",
    "notes",
    "movements",
}
MOVEMENT_FIELDS = {"timestamp", "source", "destination", "reason"}


class InventoryValidationError(ValueError):
    """Raised when one or more inventory validation rules fail."""

    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("Inventory validation failed:\n- " + "\n- ".join(self.errors))


def _read_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError:
        raise InventoryValidationError(["missing file: {}".format(path)])
    except yaml.YAMLError as exc:
        raise InventoryValidationError(["{}: invalid YAML: {}".format(path.name, exc)])


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _nonempty_string(value: Any, label: str, errors: List[str]) -> bool:
    valid = isinstance(value, str) and bool(value.strip())
    if not valid:
        errors.append("{} must be a non-empty string".format(label))
    return valid


def _optional_string(value: Any, label: str, errors: List[str]) -> bool:
    valid = value is None or (isinstance(value, str) and bool(value.strip()))
    if not valid:
        errors.append("{} must be a non-empty string or null".format(label))
    return valid


def _string_list(value: Any, label: str, errors: List[str]) -> bool:
    valid = isinstance(value, list) and all(isinstance(entry, str) and entry.strip() for entry in value)
    if not valid:
        errors.append("{} must be a list of non-empty strings".format(label))
    return valid


def _required_fields(schema: Mapping[str, Any], section: str, errors: List[str]) -> Sequence[str]:
    section_schema = schema.get(section)
    value = section_schema.get("required_fields") if isinstance(section_schema, dict) else None
    if not isinstance(value, list) or not all(isinstance(field, str) for field in value):
        errors.append("schema.yaml: {}.required_fields must be a list of strings".format(section))
        return ()
    return value


def _freeze_attribute(value: Any) -> AttributeValue:
    return tuple(value) if isinstance(value, list) else value


def _valid_id(value: Any, label: str, errors: List[str]) -> bool:
    valid = isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None
    if not valid:
        errors.append("{} has invalid id {!r}".format(label, value))
    return valid


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


def _validate_locations(
    raw: Any,
    required: Sequence[str],
    allowed_kinds: frozenset,
    required_by_kind: Mapping[str, Any],
    errors: List[str],
) -> Tuple[Tuple[Location, ...], frozenset]:
    if not isinstance(raw, list):
        errors.append("locations.yaml: root must be a list")
        return (), frozenset()

    locations: List[Location] = []
    seen = set()
    for index, value in enumerate(raw, 1):
        label = "locations.yaml entry {}".format(index)
        if not isinstance(value, dict):
            errors.append("{} must be a mapping".format(label))
            continue
        valid = _check_fields(value, required, LOCATION_FIELDS, label, errors)
        identifier = value.get("id")
        id_valid = _valid_id(identifier, label, errors)
        if id_valid and identifier in seen:
            errors.append("locations.yaml has duplicate id {}".format(identifier))
            id_valid = False
        elif id_valid:
            seen.add(identifier)

        valid &= id_valid
        valid &= _nonempty_string(value.get("name"), "{}.name".format(label), errors)
        kind = value.get("kind")
        if kind not in allowed_kinds:
            errors.append("{} has unknown kind {!r}".format(label, kind))
            valid = False
        aliases_valid = _string_list(value.get("aliases"), "{}.aliases".format(label), errors)
        if aliases_valid and len(value["aliases"]) != len(set(value["aliases"])):
            errors.append("{}.aliases contains duplicates".format(label))
            aliases_valid = False
        valid &= aliases_valid
        valid &= _optional_string(value.get("notes"), "{}.notes".format(label), errors)

        for field in ("city", "region", "country"):
            if field in value:
                valid &= _optional_string(value.get(field), "{}.{}".format(label, field), errors)
        kind_required = required_by_kind.get(kind, []) if isinstance(required_by_kind, dict) else []
        if not isinstance(kind_required, list):
            errors.append("schema.yaml: locations.required_by_kind.{} must be a list".format(kind))
            kind_required = []
        missing_kind_fields = [field for field in kind_required if not value.get(field)]
        if missing_kind_fields:
            errors.append(
                "{} kind {!r} requires: {}".format(label, kind, ", ".join(sorted(missing_kind_fields)))
            )
            valid = False

        if valid:
            locations.append(
                Location(
                    id=identifier,
                    name=value["name"],
                    kind=kind,
                    city=value.get("city"),
                    region=value.get("region"),
                    country=value.get("country"),
                    aliases=tuple(value["aliases"]),
                    notes=value["notes"],
                )
            )
    return tuple(locations), frozenset(seen)


def _validate_attributes(raw: Any, label: str, errors: List[str]) -> Tuple[bool, Mapping[str, AttributeValue]]:
    if not isinstance(raw, dict):
        errors.append("{}.attributes must be a mapping".format(label))
        return False, MappingProxyType({})
    valid = True
    frozen: Dict[str, AttributeValue] = {}
    for key, attribute in raw.items():
        if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
            errors.append("{} has invalid attribute key {!r}".format(label, key))
            valid = False
        if key == "condition":
            errors.append("{}.attributes.condition belongs on physical instances".format(label))
            valid = False
        if not (_is_scalar(attribute) or (isinstance(attribute, list) and all(_is_scalar(v) for v in attribute))):
            errors.append("{}.attributes.{} must be a scalar or list of scalars".format(label, key))
            valid = False
        else:
            frozen[key] = _freeze_attribute(attribute)
    return valid, MappingProxyType(frozen)


def _validate_definitions(
    raw: Any,
    required: Sequence[str],
    source_required: Sequence[str],
    allowed_types: frozenset,
    allowed_uses: frozenset,
    location_ids: frozenset,
    errors: List[str],
) -> Tuple[Tuple[ItemDefinition, ...], Mapping[str, ItemDefinition], Mapping[str, Tuple[str, int, str]]]:
    if not isinstance(raw, list):
        errors.append("clothes.yaml: definitions must be a list")
        return (), {}, {}

    definitions: List[ItemDefinition] = []
    seen_definitions = set()
    source_index: Dict[str, Tuple[str, int, str]] = {}
    for index, value in enumerate(raw, 1):
        label = "clothes.yaml definition {}".format(index)
        if not isinstance(value, dict):
            errors.append("{} must be a mapping".format(label))
            continue
        valid = _check_fields(value, required, DEFINITION_FIELDS, label, errors)
        identifier = value.get("id")
        id_valid = _valid_id(identifier, label, errors)
        if id_valid and identifier in seen_definitions:
            errors.append("clothes.yaml has duplicate definition id {}".format(identifier))
            id_valid = False
        elif id_valid:
            seen_definitions.add(identifier)
        valid &= id_valid
        valid &= _nonempty_string(value.get("name"), "{}.name".format(label), errors)

        item_type = value.get("type")
        if item_type not in allowed_types:
            errors.append("{} has unknown type {!r}".format(label, item_type))
            valid = False
        attributes_valid, attributes = _validate_attributes(value.get("attributes"), label, errors)
        valid &= attributes_valid
        uses_valid = _string_list(value.get("use"), "{}.use".format(label), errors)
        if uses_valid:
            unknown = set(value["use"]) - allowed_uses
            if unknown:
                errors.append("{} has unknown uses: {}".format(label, ", ".join(sorted(unknown))))
                uses_valid = False
            if len(value["use"]) != len(set(value["use"])):
                errors.append("{} has duplicate use values".format(label))
                uses_valid = False
        valid &= uses_valid
        valid &= _optional_string(value.get("notes"), "{}.notes".format(label), errors)

        raw_sources = value.get("sources")
        sources: List[InventorySource] = []
        if not isinstance(raw_sources, list):
            errors.append("{}.sources must be a list".format(label))
            valid = False
            raw_sources = []
        for source_index_number, source in enumerate(raw_sources, 1):
            source_label = "{}.sources entry {}".format(label, source_index_number)
            if not isinstance(source, dict):
                errors.append("{} must be a mapping".format(source_label))
                valid = False
                continue
            source_valid = _check_fields(source, source_required, SOURCE_FIELDS, source_label, errors)
            source_id = source.get("id")
            source_valid &= _valid_id(source_id, source_label, errors)
            if source_id in source_index:
                errors.append("clothes.yaml has duplicate source id {}".format(source_id))
                source_valid = False
            original_location = source.get("original_location")
            if original_location not in location_ids:
                errors.append("{} has unknown original_location {!r}".format(source_label, original_location))
                source_valid = False
            quantity = source.get("original_quantity")
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                errors.append("{}.original_quantity must be a positive integer".format(source_label))
                source_valid = False
            source_valid &= _optional_string(
                source.get("original_condition"), "{}.original_condition".format(source_label), errors
            )
            source_valid &= _optional_string(source.get("notes"), "{}.notes".format(source_label), errors)
            if source_valid:
                source_index[source_id] = (identifier, quantity, original_location)
                sources.append(
                    InventorySource(
                        id=source_id,
                        original_location=original_location,
                        original_quantity=quantity,
                        original_condition=source["original_condition"],
                        notes=source["notes"],
                    )
                )
            else:
                valid = False

        if valid:
            definitions.append(
                ItemDefinition(
                    id=identifier,
                    name=value["name"],
                    type=item_type,
                    attributes=attributes,
                    uses=tuple(value["use"]),
                    notes=value["notes"],
                    sources=tuple(sources),
                )
            )

    by_id = {definition.id: definition for definition in definitions}
    return tuple(definitions), MappingProxyType(by_id), MappingProxyType(source_index)


def _validate_instances(
    raw: Any,
    required: Sequence[str],
    movement_required: Sequence[str],
    definitions: Mapping[str, ItemDefinition],
    sources: Mapping[str, Tuple[str, int, str]],
    location_ids: frozenset,
    errors: List[str],
) -> Tuple[PhysicalItem, ...]:
    if not isinstance(raw, list):
        errors.append("clothes.yaml: instances must be a list")
        return ()

    instances: List[PhysicalItem] = []
    seen = set()
    source_counts: Counter = Counter()
    for index, value in enumerate(raw, 1):
        label = "clothes.yaml instance {}".format(index)
        if not isinstance(value, dict):
            errors.append("{} must be a mapping".format(label))
            continue
        valid = _check_fields(value, required, INSTANCE_FIELDS, label, errors)
        identifier = value.get("id")
        id_valid = _valid_id(identifier, label, errors)
        if id_valid and identifier in seen:
            errors.append("clothes.yaml has duplicate physical item id {}".format(identifier))
            id_valid = False
        elif id_valid:
            seen.add(identifier)
        valid &= id_valid

        definition_id = value.get("definition")
        definition = definitions.get(definition_id)
        if definition is None:
            errors.append("{} has unknown definition {!r}".format(label, definition_id))
            valid = False
        source_id = value.get("source")
        if source_id is not None:
            expected = sources.get(source_id)
            if expected is None:
                errors.append("{} has unknown source {!r}".format(label, source_id))
                valid = False
            elif expected[0] != definition_id:
                errors.append("{} source {!r} belongs to definition {!r}".format(label, source_id, expected[0]))
                valid = False
            else:
                source_counts[source_id] += 1

        current = value.get("current_location")
        preferred = value.get("preferred_location")
        if current not in location_ids:
            errors.append("{} has unknown current_location {!r}".format(label, current))
            valid = False
        if preferred not in location_ids:
            errors.append("{} has unknown preferred_location {!r}".format(label, preferred))
            valid = False
        for field in ("condition", "status", "notes"):
            valid &= _optional_string(value.get(field), "{}.{}".format(label, field), errors)

        raw_movements = value.get("movements")
        movements: List[Movement] = []
        if not isinstance(raw_movements, list):
            errors.append("{}.movements must be a list".format(label))
            valid = False
            raw_movements = []
        previous_destination = sources[source_id][2] if source_id in sources else None
        previous_timestamp: Optional[datetime] = None
        for movement_index, movement in enumerate(raw_movements, 1):
            movement_label = "{}.movements entry {}".format(label, movement_index)
            if not isinstance(movement, dict):
                errors.append("{} must be a mapping".format(movement_label))
                valid = False
                continue
            movement_valid = _check_fields(
                movement, movement_required, MOVEMENT_FIELDS, movement_label, errors
            )
            timestamp = movement.get("timestamp")
            parsed_timestamp = None
            if not isinstance(timestamp, str):
                errors.append("{}.timestamp must be an ISO 8601 string".format(movement_label))
                movement_valid = False
            else:
                try:
                    parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
                        raise ValueError
                except ValueError:
                    errors.append("{}.timestamp must include an ISO 8601 timezone".format(movement_label))
                    movement_valid = False
            movement_source = movement.get("source")
            destination = movement.get("destination")
            if movement_source not in location_ids:
                errors.append("{} has unknown source {!r}".format(movement_label, movement_source))
                movement_valid = False
            if destination not in location_ids:
                errors.append("{} has unknown destination {!r}".format(movement_label, destination))
                movement_valid = False
            if movement_source == destination and movement_source in location_ids:
                errors.append("{} source and destination must differ".format(movement_label))
                movement_valid = False
            if previous_destination is not None and movement_source != previous_destination:
                errors.append(
                    "{} source {!r} does not continue from {!r}".format(
                        movement_label, movement_source, previous_destination
                    )
                )
                movement_valid = False
            if parsed_timestamp is not None and previous_timestamp is not None and parsed_timestamp < previous_timestamp:
                errors.append("{}.timestamp is earlier than the preceding movement".format(movement_label))
                movement_valid = False
            movement_valid &= _optional_string(
                movement.get("reason"), "{}.reason".format(movement_label), errors
            )
            if movement_valid:
                movements.append(
                    Movement(
                        timestamp=timestamp,
                        source=movement_source,
                        destination=destination,
                        reason=movement["reason"],
                    )
                )
                previous_destination = destination
                previous_timestamp = parsed_timestamp
            else:
                valid = False

        if raw_movements and previous_destination != current:
            errors.append(
                "{}.current_location {!r} does not match movement history destination {!r}".format(
                    label, current, previous_destination
                )
            )
            valid = False
        elif not raw_movements and source_id in sources and current != sources[source_id][2]:
            errors.append(
                "{}.current_location {!r} differs from original location {!r} without movement history".format(
                    label, current, sources[source_id][2]
                )
            )
            valid = False

        if valid:
            instances.append(
                PhysicalItem(
                    id=identifier,
                    definition=definition,
                    source=source_id,
                    current_location=current,
                    preferred_location=preferred,
                    condition=value["condition"],
                    status=value["status"],
                    notes=value["notes"],
                    movements=tuple(movements),
                )
            )

    for source_id, (_definition_id, expected_quantity, _original_location) in sources.items():
        actual_quantity = source_counts[source_id]
        if actual_quantity != expected_quantity:
            errors.append(
                "source {!r} expected {} physical instances but found {}".format(
                    source_id, expected_quantity, actual_quantity
                )
            )
    return tuple(instances)


def load_inventory(data_dir: Optional[Path] = None) -> Inventory:
    """Load and validate the inventory from *data_dir* without mutating YAML."""

    directory = Path(data_dir) if data_dir is not None else Path(__file__).resolve().parents[1] / "data"
    schema = _read_yaml(directory / "schema.yaml")
    locations_raw = _read_yaml(directory / "locations.yaml")
    inventory_raw = _read_yaml(directory / "clothes.yaml")
    errors: List[str] = []

    if not isinstance(schema, dict):
        raise InventoryValidationError(["schema.yaml: root must be a mapping"])
    if schema.get("version") != 8:
        errors.append("schema.yaml: version must be 8")
    for section in ("definitions", "instances", "movements", "sources", "locations"):
        section_schema = schema.get(section)
        if not isinstance(section_schema, dict) or section_schema.get("root") != "list":
            errors.append("schema.yaml: {}.root must be 'list'".format(section))
    inventory_schema = schema.get("inventory")
    if not isinstance(inventory_schema, dict) or inventory_schema.get("root") != "mapping":
        errors.append("schema.yaml: inventory.root must be 'mapping'")

    required_sections = inventory_schema.get("required_sections", []) if isinstance(inventory_schema, dict) else []
    if not isinstance(required_sections, list):
        errors.append("schema.yaml: inventory.required_sections must be a list")
        required_sections = []
    if not isinstance(inventory_raw, dict):
        errors.append("clothes.yaml: root must be a mapping")
        inventory_raw = {}
    missing_sections = set(required_sections) - set(inventory_raw)
    extra_sections = set(inventory_raw) - {"definitions", "instances"}
    if missing_sections:
        errors.append("clothes.yaml missing sections: {}".format(", ".join(sorted(missing_sections))))
    if extra_sections:
        errors.append("clothes.yaml has unknown sections: {}".format(", ".join(sorted(extra_sections))))

    definition_schema = schema.get("definitions") if isinstance(schema.get("definitions"), dict) else {}
    raw_types = definition_schema.get("allowed_types")
    raw_uses = definition_schema.get("allowed_uses")
    if not isinstance(raw_types, list) or not all(isinstance(value, str) for value in raw_types):
        errors.append("schema.yaml: definitions.allowed_types must be a list of strings")
        raw_types = []
    if not isinstance(raw_uses, list) or not all(isinstance(value, str) for value in raw_uses):
        errors.append("schema.yaml: definitions.allowed_uses must be a list of strings")
        raw_uses = []

    locations_schema = schema.get("locations") if isinstance(schema.get("locations"), dict) else {}
    raw_kinds = locations_schema.get("allowed_kinds")
    if not isinstance(raw_kinds, list) or not all(isinstance(value, str) for value in raw_kinds):
        errors.append("schema.yaml: locations.allowed_kinds must be a list of strings")
        raw_kinds = []

    locations, location_ids = _validate_locations(
        locations_raw,
        _required_fields(schema, "locations", errors),
        frozenset(raw_kinds),
        locations_schema.get("required_by_kind", {}),
        errors,
    )
    definitions, definitions_by_id, sources = _validate_definitions(
        inventory_raw.get("definitions"),
        _required_fields(schema, "definitions", errors),
        _required_fields(schema, "sources", errors),
        frozenset(raw_types),
        frozenset(raw_uses),
        location_ids,
        errors,
    )
    instances = _validate_instances(
        inventory_raw.get("instances"),
        _required_fields(schema, "instances", errors),
        _required_fields(schema, "movements", errors),
        definitions_by_id,
        sources,
        location_ids,
        errors,
    )
    if errors:
        raise InventoryValidationError(errors)
    return Inventory(items=instances, definitions=definitions, locations=locations)
