"""Reusable inventory query and aggregation logic."""

import difflib
import re
import unicodedata
from collections import defaultdict
from types import MappingProxyType
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .models import (
    AttributeValue,
    DuplicateCandidate,
    InventoryComparison,
    InventoryCount,
    ItemDefinition,
    Location,
    LocationSummary,
    Movement,
    PhysicalItem,
    Query,
)

TYPE_FAMILIES = {
    "shoes": frozenset({"shoes", "dress_shoes", "athletic_shoes", "soccer_shoes", "boots", "flip_flops"}),
    "shirts": frozenset({"shirt", "t_shirt", "long_sleeve_shirt"}),
    "shorts": frozenset({"shorts", "sports_shorts"}),
    "swimwear": frozenset({"swimwear", "swim_trunks"}),
    "hats": frozenset({"hat", "beanie"}),
    "outerwear": frozenset({"hoodie", "jacket", "sweater", "zip_up", "vest"}),
}
TYPE_ALIASES = {
    "shoe": "shoes",
    "shirt": "shirt",
    "tshirt": "t_shirt",
    "tshirts": "t_shirt",
    "t_shirts": "t_shirt",
    "tee": "t_shirt",
    "tees": "t_shirt",
    "hat": "hat",
}


class LocationNotFoundError(LookupError):
    pass


class DefinitionNotFoundError(LookupError):
    pass


class PhysicalItemNotFoundError(LookupError):
    pass


def _normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value)).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _normalize_type(value: str) -> str:
    normalized = "_".join(_normalize_text(value).split())
    return TYPE_ALIASES.get(normalized, normalized)


def _value_matches(actual: AttributeValue, expected: AttributeValue) -> bool:
    if isinstance(actual, tuple):
        expected_values = expected if isinstance(expected, tuple) else (expected,)
        normalized_actual = {_normalize_text(value) for value in actual}
        return all(_normalize_text(value) in normalized_actual for value in expected_values)
    if isinstance(expected, tuple):
        return len(expected) == 1 and _normalize_text(actual) == _normalize_text(expected[0])
    return _normalize_text(actual) == _normalize_text(expected)


def _count(items: Iterable[PhysicalItem]) -> InventoryCount:
    materialized = tuple(items)
    return InventoryCount(
        definitions=len({item.definition_id for item in materialized}),
        physical_items=len(materialized),
    )


class Inventory:
    """Validated, immutable inventory plus composable query operations."""

    def __init__(
        self,
        items: Sequence[PhysicalItem],
        definitions: Sequence[ItemDefinition],
        locations: Sequence[Location],
    ):
        self._items = tuple(items)
        self._definitions = tuple(definitions)
        self._locations = tuple(locations)
        self._items_by_id = MappingProxyType({item.id: item for item in items})
        self._definitions_by_id = MappingProxyType(
            {definition.id: definition for definition in definitions}
        )
        self._locations_by_id = MappingProxyType({location.id: location for location in locations})
        alias_index: Dict[str, Set[str]] = defaultdict(set)
        for location in locations:
            terms = [location.id, location.name, *location.aliases]
            terms.extend(
                part for part in (location.city, location.region, location.country) if part
            )
            for term in terms:
                alias_index[_normalize_text(term)].add(location.id)
        self._location_aliases = MappingProxyType(
            {alias: frozenset(ids) for alias, ids in alias_index.items()}
        )

    @property
    def items(self) -> Tuple[PhysicalItem, ...]:
        """All physical instances."""
        return self._items

    @property
    def definitions(self) -> Tuple[ItemDefinition, ...]:
        return self._definitions

    @property
    def locations(self) -> Tuple[Location, ...]:
        return self._locations

    def resolve_location(self, reference: str) -> Location:
        """Resolve a location ID, name, geographic field, or alias."""
        normalized = _normalize_text(reference)
        ids = self._location_aliases.get(normalized, frozenset())
        if not ids:
            raise LocationNotFoundError("unknown location {!r}".format(reference))
        if len(ids) > 1:
            raise LocationNotFoundError(
                "ambiguous location {!r}; matches {}".format(reference, ", ".join(sorted(ids)))
            )
        return self._locations_by_id[next(iter(ids))]

    def definition_for(self, item: PhysicalItem) -> ItemDefinition:
        return item.definition

    def resolve_definition(self, reference: str) -> ItemDefinition:
        """Resolve an exact definition ID or unambiguous normalized name."""
        if reference in self._definitions_by_id:
            return self._definitions_by_id[reference]
        normalized = _normalize_text(reference)
        matches = tuple(
            definition for definition in self._definitions if _normalize_text(definition.name) == normalized
        )
        if not matches:
            raise DefinitionNotFoundError("unknown item group {!r}".format(reference))
        if len(matches) > 1:
            raise DefinitionNotFoundError(
                "ambiguous item group {!r}; matches {}".format(
                    reference, ", ".join(definition.id for definition in matches)
                )
            )
        return matches[0]

    def resolve_item(self, item_id: str) -> PhysicalItem:
        try:
            return self._items_by_id[item_id]
        except KeyError:
            raise PhysicalItemNotFoundError("unknown physical item {!r}".format(item_id))

    def instances_for(self, definition: str) -> Tuple[PhysicalItem, ...]:
        definition_id = self.resolve_definition(definition).id
        return tuple(item for item in self._items if item.definition_id == definition_id)

    def location_for(self, item: PhysicalItem) -> Location:
        return self._locations_by_id[item.current_location]

    def preferred_location_for(self, item: PhysicalItem) -> Location:
        return self._locations_by_id[item.preferred_location]

    def list_at_location(self, location: str) -> Tuple[PhysicalItem, ...]:
        location_id = self.resolve_location(location).id
        return tuple(item for item in self._items if item.current_location == location_id)

    def list_by_preferred_location(self, location: str) -> Tuple[PhysicalItem, ...]:
        location_id = self.resolve_location(location).id
        return tuple(item for item in self._items if item.preferred_location == location_id)

    def items_away_from_preferred_location(self) -> Tuple[PhysicalItem, ...]:
        return tuple(
            item for item in self._items if item.current_location != item.preferred_location
        )

    def container_contents(self, location: str) -> Tuple[PhysicalItem, ...]:
        resolved = self.resolve_location(location)
        if resolved.kind != "travel_container":
            raise ValueError("location {!r} is not a travel container".format(resolved.id))
        return self.list_at_location(resolved.id)

    def movement_history(self, item_id: str) -> Tuple[Movement, ...]:
        return self.resolve_item(item_id).movements

    def definitions_at_location(self, location: str) -> Tuple[ItemDefinition, ...]:
        definition_ids = {item.definition_id for item in self.list_at_location(location)}
        return tuple(definition for definition in self._definitions if definition.id in definition_ids)

    def filter_by_type(
        self,
        item_type: str,
        items: Optional[Iterable[PhysicalItem]] = None,
        include_related: bool = True,
    ) -> Tuple[PhysicalItem, ...]:
        normalized = _normalize_type(item_type)
        allowed = TYPE_FAMILIES.get(normalized, frozenset({normalized})) if include_related else frozenset({normalized})
        return tuple(item for item in (items if items is not None else self._items) if item.type in allowed)

    def filter_by_name(
        self, name: str, items: Optional[Iterable[PhysicalItem]] = None
    ) -> Tuple[PhysicalItem, ...]:
        expected = _normalize_text(name)
        return tuple(
            item
            for item in (items if items is not None else self._items)
            if expected in _normalize_text(item.name)
        )

    def filter_by_attributes(
        self,
        attributes: Mapping[str, AttributeValue],
        items: Optional[Iterable[PhysicalItem]] = None,
    ) -> Tuple[PhysicalItem, ...]:
        criteria = {"_".join(_normalize_text(key).split()): value for key, value in attributes.items()}
        return tuple(
            item
            for item in (items if items is not None else self._items)
            if all(key in item.attributes and _value_matches(item.attributes[key], value) for key, value in criteria.items())
        )

    def filter_by_use(
        self, uses: Iterable[str], items: Optional[Iterable[PhysicalItem]] = None
    ) -> Tuple[PhysicalItem, ...]:
        required = {_normalize_type(use) for use in uses}
        return tuple(
            item
            for item in (items if items is not None else self._items)
            if required.issubset({_normalize_type(use) for use in item.uses})
        )

    def filter_by_tag(
        self, tags: Iterable[str], items: Optional[Iterable[PhysicalItem]] = None
    ) -> Tuple[PhysicalItem, ...]:
        """Alias for use-tag filtering."""
        return self.filter_by_use(tags, items)

    def filter_by_condition(
        self, condition: str, items: Optional[Iterable[PhysicalItem]] = None
    ) -> Tuple[PhysicalItem, ...]:
        expected = _normalize_text(condition)
        return tuple(
            item
            for item in (items if items is not None else self._items)
            if item.condition is not None and expected in _normalize_text(item.condition)
        )

    def filter_by_status(
        self, status: str, items: Optional[Iterable[PhysicalItem]] = None
    ) -> Tuple[PhysicalItem, ...]:
        expected = _normalize_text(status)
        return tuple(
            item
            for item in (items if items is not None else self._items)
            if item.status is not None and expected == _normalize_text(item.status)
        )

    def search(
        self, text: str, items: Optional[Iterable[PhysicalItem]] = None
    ) -> Tuple[PhysicalItem, ...]:
        """Search physical state, shared definition data, locations, and preserved notes."""
        needle = _normalize_text(text)
        candidates = tuple(items if items is not None else self._items)
        if not needle:
            return candidates
        matches = []
        for item in candidates:
            current = self.location_for(item)
            preferred = self.preferred_location_for(item)
            definition = item.definition
            haystack_parts = [
                item.id,
                definition.id,
                definition.name,
                definition.type,
                item.current_location,
                item.preferred_location,
                item.condition or "",
                item.status or "",
                item.notes or "",
                definition.notes or "",
                current.name,
                preferred.name,
                *current.aliases,
                *preferred.aliases,
                *definition.uses,
            ]
            for location in (current, preferred):
                haystack_parts.extend(part for part in (location.city, location.region, location.country) if part)
            for key, value in definition.attributes.items():
                haystack_parts.append(key)
                haystack_parts.extend(value if isinstance(value, tuple) else (value,))
            for source in definition.sources:
                haystack_parts.extend((source.id, source.notes or "", source.original_condition or ""))
            for movement in item.movements:
                haystack_parts.extend(
                    (movement.timestamp, movement.source, movement.destination, movement.reason or "")
                )
            if needle in _normalize_text(" ".join(str(part) for part in haystack_parts if part is not None)):
                matches.append(item)
        return tuple(matches)

    def find(self, query: Query) -> Tuple[PhysicalItem, ...]:
        result: Tuple[PhysicalItem, ...] = self._items
        if query.location:
            location_id = self.resolve_location(query.location).id
            result = tuple(item for item in result if item.current_location == location_id)
        if query.preferred_location:
            preferred_id = self.resolve_location(query.preferred_location).id
            result = tuple(item for item in result if item.preferred_location == preferred_id)
        if query.name:
            result = self.filter_by_name(query.name, result)
        if query.item_type:
            result = self.filter_by_type(query.item_type, result, query.include_related_types)
        if query.attributes:
            result = self.filter_by_attributes(query.attributes, result)
        if query.uses:
            result = self.filter_by_use(query.uses, result)
        if query.condition:
            result = self.filter_by_condition(query.condition, result)
        if query.status:
            result = self.filter_by_status(query.status, result)
        if query.text:
            result = self.search(query.text, result)
        return result

    def find_definitions(
        self,
        name: Optional[str] = None,
        item_type: Optional[str] = None,
        tags: Iterable[str] = (),
        attributes: Optional[Mapping[str, AttributeValue]] = None,
        location: Optional[str] = None,
    ) -> Tuple[ItemDefinition, ...]:
        """Find shared groups using the same fields exposed for physical items."""
        matches = self.find(
            Query(
                location=location,
                name=name,
                item_type=item_type,
                uses=tuple(tags),
                attributes=attributes or MappingProxyType({}),
            )
        )
        ids = {item.definition_id for item in matches}
        return tuple(definition for definition in self._definitions if definition.id in ids)

    def count(self, items: Optional[Iterable[PhysicalItem]] = None) -> InventoryCount:
        return _count(items if items is not None else self._items)

    def summarize_location(self, location: str) -> LocationSummary:
        resolved = self.resolve_location(location)
        items = self.list_at_location(resolved.id)
        by_type_items: Dict[str, List[PhysicalItem]] = defaultdict(list)
        by_use_items: Dict[str, List[PhysicalItem]] = defaultdict(list)
        by_condition_items: Dict[str, List[PhysicalItem]] = defaultdict(list)
        by_status_items: Dict[str, List[PhysicalItem]] = defaultdict(list)
        for item in items:
            by_type_items[item.type].append(item)
            for use in item.uses:
                by_use_items[use].append(item)
            if item.condition is not None:
                by_condition_items[item.condition].append(item)
            if item.status is not None:
                by_status_items[item.status].append(item)
        return LocationSummary(
            location=resolved,
            total=_count(items),
            by_type=MappingProxyType({key: _count(value) for key, value in sorted(by_type_items.items())}),
            by_use=MappingProxyType({key: _count(value) for key, value in sorted(by_use_items.items())}),
            by_condition=MappingProxyType({key: _count(value) for key, value in sorted(by_condition_items.items())}),
            by_status=MappingProxyType({key: _count(value) for key, value in sorted(by_status_items.items())}),
        )

    def compare_locations(self, left: str, right: str) -> InventoryComparison:
        left_summary = self.summarize_location(left)
        right_summary = self.summarize_location(right)
        left_items = self.list_at_location(left_summary.location.id)
        right_items = self.list_at_location(right_summary.location.id)
        all_types = sorted(set(left_summary.by_type) | set(right_summary.by_type))
        zero = InventoryCount(0, 0)
        by_type = MappingProxyType(
            {
                item_type: (
                    left_summary.by_type.get(item_type, zero),
                    right_summary.by_type.get(item_type, zero),
                )
                for item_type in all_types
            }
        )
        left_definition_ids = {item.definition_id for item in left_items}
        right_definition_ids = {item.definition_id for item in right_items}
        shared_ids = left_definition_ids & right_definition_ids
        return InventoryComparison(
            left=left_summary,
            right=right_summary,
            by_type=by_type,
            shared_item_names=tuple(sorted(self._definitions_by_id[item_id].name for item_id in shared_ids)),
            only_left=tuple(item for item in left_items if item.definition_id not in right_definition_ids),
            only_right=tuple(item for item in right_items if item.definition_id not in left_definition_ids),
        )

    def compare_homes(self, left: str, right: str) -> InventoryComparison:
        left_location = self.resolve_location(left)
        right_location = self.resolve_location(right)
        for location in (left_location, right_location):
            if location.kind != "home":
                raise ValueError("location {!r} is not a home".format(location.id))
        return self.compare_locations(left_location.id, right_location.id)

    def find_duplicates(
        self,
        definitions: Optional[Iterable[ItemDefinition]] = None,
        location: Optional[str] = None,
        threshold: float = 0.9,
    ) -> Tuple[DuplicateCandidate, ...]:
        """Report near-duplicate shared definitions without merging them."""
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        candidates = tuple(definitions if definitions is not None else self._definitions)
        if location:
            allowed_ids = {definition.id for definition in self.definitions_at_location(location)}
            candidates = tuple(definition for definition in candidates if definition.id in allowed_ids)
        results: List[DuplicateCandidate] = []
        for index, left in enumerate(candidates):
            for right in candidates[index + 1 :]:
                if left.type != right.type:
                    continue
                left_name = _normalize_text(left.name)
                right_name = _normalize_text(right.name)
                exact = (
                    left_name == right_name
                    and dict(left.attributes) == dict(right.attributes)
                    and set(left.uses) == set(right.uses)
                )
                if exact:
                    results.append(
                        DuplicateCandidate(
                            left=left,
                            right=right,
                            kind="exact",
                            score=1.0,
                            reasons=("same normalized name, type, attributes, and uses",),
                        )
                    )
                    continue
                sequence_score = difflib.SequenceMatcher(None, left_name, right_name).ratio()
                left_tokens = set(left_name.split())
                right_tokens = set(right_name.split())
                union = left_tokens | right_tokens
                token_score = len(left_tokens & right_tokens) / len(union) if union else 0.0
                score = max(sequence_score, token_score, 0.98 if left_name == right_name else 0.0)
                if score >= threshold:
                    reasons = ("similar normalized names",)
                    if left_name == right_name:
                        reasons = ("same normalized name", "different attributes or uses")
                    results.append(
                        DuplicateCandidate(
                            left=left,
                            right=right,
                            kind="near",
                            score=round(score, 3),
                            reasons=reasons,
                        )
                    )
        return tuple(sorted(results, key=lambda candidate: (-candidate.score, candidate.left.id, candidate.right.id)))
