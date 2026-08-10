"""Reusable activity templates and abstract trip-requirement resolution.

This module resolves possession categories only. It never reads physical item
state to select possessions and never writes inventory data.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml

CATEGORY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TEMPLATE_FIELDS = {"id", "name", "required", "optional", "notes"}
OVERRIDE_FIELDS = {"add", "add_optional", "remove", "remove_optional"}


class RequirementValidationError(ValueError):
    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("Requirement validation failed:\n- " + "\n- ".join(self.errors))


class RequirementTemplateNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class CategoryRequirement:
    category: str
    count: int = 1


@dataclass(frozen=True)
class RequirementTemplate:
    id: str
    name: str
    required: Tuple[CategoryRequirement, ...]
    optional: Tuple[CategoryRequirement, ...]
    notes: Optional[str]


@dataclass(frozen=True)
class RequirementOverrides:
    add: Tuple[CategoryRequirement, ...] = ()
    add_optional: Tuple[CategoryRequirement, ...] = ()
    remove: Tuple[str, ...] = ()
    remove_optional: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedActivityRequirements:
    attachment_id: str
    name: str
    kind: str
    template_id: str
    frequency: int
    required: Tuple[CategoryRequirement, ...]
    optional: Tuple[CategoryRequirement, ...]


@dataclass(frozen=True)
class ResolvedRequirement:
    category: str
    count: int
    sources: Tuple[str, ...]


@dataclass(frozen=True)
class ResolvedTripRequirements:
    trip_id: str
    required: Tuple[ResolvedRequirement, ...]
    optional: Tuple[ResolvedRequirement, ...]
    activities: Tuple[ResolvedActivityRequirements, ...]


class RequirementTemplateCatalog:
    def __init__(self, templates: Sequence[RequirementTemplate]):
        self._templates = tuple(templates)
        self._by_id = MappingProxyType({template.id: template for template in templates})

    @property
    def templates(self) -> Tuple[RequirementTemplate, ...]:
        return self._templates

    def get(self, template_id: str) -> RequirementTemplate:
        try:
            return self._by_id[template_id]
        except KeyError:
            raise RequirementTemplateNotFoundError(
                "unknown activity template {!r}".format(template_id)
            )


def _data_directory(data_dir: Optional[Path]) -> Path:
    return Path(data_dir) if data_dir is not None else Path(__file__).resolve().parents[1] / "data"


def _read_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError:
        raise RequirementValidationError(["missing file: {}".format(path)])
    except yaml.YAMLError as exc:
        raise RequirementValidationError(["{}: invalid YAML: {}".format(path.name, exc)])


def _optional_string(value: Any, label: str, errors: List[str]) -> bool:
    valid = value is None or (isinstance(value, str) and bool(value.strip()))
    if not valid:
        errors.append("{} must be a non-empty string or null".format(label))
    return valid


def _parse_requirement(
    value: Any, label: str, errors: List[str]
) -> Optional[CategoryRequirement]:
    if isinstance(value, str):
        category = value
        count = 1
    elif isinstance(value, dict) and set(value) == {"category", "count"}:
        category = value.get("category")
        count = value.get("count")
    else:
        errors.append("{} must be a category string or {{category, count}} mapping".format(label))
        return None
    if not isinstance(category, str) or not CATEGORY_PATTERN.fullmatch(category):
        errors.append("{} has invalid category {!r}".format(label, category))
        return None
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        errors.append("{}.count must be a positive integer".format(label))
        return None
    return CategoryRequirement(category=category, count=count)


def parse_requirement_list(
    raw: Any, label: str, errors: List[str]
) -> Tuple[CategoryRequirement, ...]:
    if not isinstance(raw, list):
        errors.append("{} must be a list".format(label))
        return ()
    parsed: List[CategoryRequirement] = []
    seen = set()
    for index, value in enumerate(raw, 1):
        requirement = _parse_requirement(value, "{} entry {}".format(label, index), errors)
        if requirement is None:
            continue
        if requirement.category in seen:
            errors.append("{} has duplicate category {!r}".format(label, requirement.category))
            continue
        seen.add(requirement.category)
        parsed.append(requirement)
    return tuple(parsed)


def parse_requirement_overrides(
    raw: Any,
    required_fields: Sequence[str],
    label: str,
    errors: List[str],
) -> RequirementOverrides:
    if not isinstance(raw, dict):
        errors.append("{} must be a mapping".format(label))
        return RequirementOverrides()
    missing = set(required_fields) - set(raw)
    extra = set(raw) - OVERRIDE_FIELDS
    if missing:
        errors.append("{} missing fields: {}".format(label, ", ".join(sorted(missing))))
    if extra:
        errors.append("{} has unknown fields: {}".format(label, ", ".join(sorted(extra))))
    add = parse_requirement_list(raw.get("add"), "{}.add".format(label), errors)
    add_optional = parse_requirement_list(
        raw.get("add_optional"), "{}.add_optional".format(label), errors
    )
    remove = _parse_category_list(raw.get("remove"), "{}.remove".format(label), errors)
    remove_optional = _parse_category_list(
        raw.get("remove_optional"), "{}.remove_optional".format(label), errors
    )
    return RequirementOverrides(
        add=add,
        add_optional=add_optional,
        remove=remove,
        remove_optional=remove_optional,
    )


def _parse_category_list(raw: Any, label: str, errors: List[str]) -> Tuple[str, ...]:
    if not isinstance(raw, list):
        errors.append("{} must be a list of category strings".format(label))
        return ()
    valid: List[str] = []
    for value in raw:
        if not isinstance(value, str) or not CATEGORY_PATTERN.fullmatch(value):
            errors.append("{} has invalid category {!r}".format(label, value))
        elif value in valid:
            errors.append("{} has duplicate category {!r}".format(label, value))
        else:
            valid.append(value)
    return tuple(valid)


def load_requirement_templates(data_dir: Optional[Path] = None) -> RequirementTemplateCatalog:
    directory = _data_directory(data_dir)
    raw = _read_yaml(directory / "activity_templates.yaml")
    schema = _read_yaml(directory / "schema.yaml")
    errors: List[str] = []
    if not isinstance(schema, dict):
        raise RequirementValidationError(["schema.yaml: root must be a mapping"])
    if schema.get("version") != 8:
        errors.append("schema.yaml: version must be 8")
    store_schema = schema.get("template_store")
    if not isinstance(store_schema, dict) or store_schema.get("root") != "mapping":
        errors.append("schema.yaml: template_store.root must be 'mapping'")
        store_schema = {}
    template_schema = schema.get("requirement_templates")
    if not isinstance(template_schema, dict) or template_schema.get("root") != "list":
        errors.append("schema.yaml: requirement_templates.root must be 'list'")
        template_schema = {}
    required = template_schema.get("required_fields", [])
    if not isinstance(required, list):
        errors.append("schema.yaml: requirement_templates.required_fields must be a list")
        required = []

    if not isinstance(raw, dict):
        errors.append("activity_templates.yaml: root must be a mapping")
        raw = {}
    required_sections = store_schema.get("required_sections", [])
    if not isinstance(required_sections, list):
        errors.append("schema.yaml: template_store.required_sections must be a list")
        required_sections = []
    missing_sections = set(required_sections) - set(raw)
    extra_sections = set(raw) - {"templates"}
    if missing_sections:
        errors.append(
            "activity_templates.yaml missing sections: {}".format(", ".join(sorted(missing_sections)))
        )
    if extra_sections:
        errors.append(
            "activity_templates.yaml has unknown sections: {}".format(", ".join(sorted(extra_sections)))
        )
    raw_templates = raw.get("templates")
    if not isinstance(raw_templates, list):
        errors.append("activity_templates.yaml: templates must be a list")
        raw_templates = []

    templates: List[RequirementTemplate] = []
    seen_ids = set()
    for index, value in enumerate(raw_templates, 1):
        label = "activity_templates.yaml template {}".format(index)
        if not isinstance(value, dict):
            errors.append("{} must be a mapping".format(label))
            continue
        missing = set(required) - set(value)
        extra = set(value) - TEMPLATE_FIELDS
        if missing:
            errors.append("{} missing fields: {}".format(label, ", ".join(sorted(missing))))
        if extra:
            errors.append("{} has unknown fields: {}".format(label, ", ".join(sorted(extra))))
        template_id = value.get("id")
        if not isinstance(template_id, str) or not ID_PATTERN.fullmatch(template_id):
            errors.append("{} has invalid id {!r}".format(label, template_id))
        elif template_id in seen_ids:
            errors.append("activity_templates.yaml has duplicate id {}".format(template_id))
        else:
            seen_ids.add(template_id)
        name = value.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("{}.name must be a non-empty string".format(label))
        required_requirements = parse_requirement_list(
            value.get("required"), "{}.required".format(label), errors
        )
        optional_requirements = parse_requirement_list(
            value.get("optional"), "{}.optional".format(label), errors
        )
        overlap = {entry.category for entry in required_requirements} & {
            entry.category for entry in optional_requirements
        }
        if overlap:
            errors.append(
                "{} categories cannot be both required and optional: {}".format(
                    label, ", ".join(sorted(overlap))
                )
            )
        notes_valid = _optional_string(value.get("notes"), "{}.notes".format(label), errors)
        if not missing and not extra and template_id in seen_ids and isinstance(name, str) and name.strip() and notes_valid and not overlap:
            templates.append(
                RequirementTemplate(
                    id=template_id,
                    name=name,
                    required=required_requirements,
                    optional=optional_requirements,
                    notes=value["notes"],
                )
            )
    if errors:
        raise RequirementValidationError(errors)
    return RequirementTemplateCatalog(templates)


def _apply_overrides(
    template: RequirementTemplate, overrides: RequirementOverrides
) -> Tuple[Tuple[CategoryRequirement, ...], Tuple[CategoryRequirement, ...]]:
    required: Dict[str, int] = {entry.category: entry.count for entry in template.required}
    optional: Dict[str, int] = {entry.category: entry.count for entry in template.optional}
    for category in overrides.remove:
        required.pop(category, None)
    for category in overrides.remove_optional:
        optional.pop(category, None)
    for entry in overrides.add:
        required[entry.category] = entry.count
        optional.pop(entry.category, None)
    for entry in overrides.add_optional:
        if entry.category not in required:
            optional[entry.category] = entry.count
    return (
        tuple(CategoryRequirement(category, count) for category, count in required.items()),
        tuple(CategoryRequirement(category, count) for category, count in optional.items()),
    )


def resolve_trip_requirements(
    trip_id: str, data_dir: Optional[Path] = None
) -> ResolvedTripRequirements:
    from .trips import load_trip  # Local import avoids a module cycle during validation.

    trip = load_trip(trip_id, data_dir)
    templates = load_requirement_templates(data_dir)
    activities: List[ResolvedActivityRequirements] = []
    required_totals: Dict[str, int] = {}
    optional_totals: Dict[str, int] = {}
    required_sources: Dict[str, List[str]] = {}
    optional_sources: Dict[str, List[str]] = {}

    for attachment in trip.attachments:
        template = templates.get(attachment.type)
        required, optional = _apply_overrides(template, attachment.requirements)
        scaled_required = tuple(
            CategoryRequirement(entry.category, entry.count * attachment.frequency)
            for entry in required
        )
        scaled_optional = tuple(
            CategoryRequirement(entry.category, entry.count * attachment.frequency)
            for entry in optional
        )
        activities.append(
            ResolvedActivityRequirements(
                attachment_id=attachment.id,
                name=attachment.name,
                kind=attachment.kind,
                template_id=template.id,
                frequency=attachment.frequency,
                required=scaled_required,
                optional=scaled_optional,
            )
        )
        for entry in scaled_required:
            required_totals[entry.category] = required_totals.get(entry.category, 0) + entry.count
            required_sources.setdefault(entry.category, []).append(attachment.id)
        for entry in scaled_optional:
            optional_totals[entry.category] = optional_totals.get(entry.category, 0) + entry.count
            optional_sources.setdefault(entry.category, []).append(attachment.id)

    # A category required by any concrete activity is not also reported as
    # optional in the complete trip-level set.
    for category in required_totals:
        optional_totals.pop(category, None)
        optional_sources.pop(category, None)

    return ResolvedTripRequirements(
        trip_id=trip.id,
        required=tuple(
            ResolvedRequirement(category, count, tuple(required_sources[category]))
            for category, count in required_totals.items()
        ),
        optional=tuple(
            ResolvedRequirement(category, count, tuple(optional_sources[category]))
            for category, count in optional_totals.items()
        ),
        activities=tuple(activities),
    )
