"""Immutable domain models returned by the inventory toolkit."""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Optional, Tuple, Union

Scalar = Union[None, str, int, float, bool]
AttributeValue = Union[Scalar, Tuple[Scalar, ...]]


def empty_attributes() -> Mapping[str, AttributeValue]:
    return MappingProxyType({})


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    kind: str
    aliases: Tuple[str, ...]
    notes: Optional[str]
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    capacity_liters: Optional[float] = None
    max_load_kg: Optional[float] = None


@dataclass(frozen=True)
class ItemTypeDefaults:
    """Rough packing estimates shared by every item of one inventory type."""

    item_type: str
    default_space_liters: float
    default_weight_kg: float


@dataclass(frozen=True)
class InventorySource:
    """A preserved pre-v2 quantity record used to audit the migration."""

    id: str
    original_location: str
    original_quantity: int
    original_condition: Optional[str]
    notes: Optional[str]


@dataclass(frozen=True)
class ItemDefinition:
    """Shared conceptual data for one kind of possession."""

    id: str
    name: str
    type: str
    attributes: Mapping[str, AttributeValue]
    uses: Tuple[str, ...]
    notes: Optional[str]
    sources: Tuple[InventorySource, ...]
    type_defaults: Optional[ItemTypeDefaults] = None


@dataclass(frozen=True)
class Movement:
    """A factual, append-only physical-location change."""

    timestamp: str
    source: str
    destination: str
    reason: Optional[str]


@dataclass(frozen=True)
class PhysicalItem:
    """One individually trackable physical possession."""

    id: str
    definition: ItemDefinition
    current_location: str
    preferred_location: str
    condition: Optional[str]
    status: Optional[str]
    notes: Optional[str]
    source: Optional[str]
    movements: Tuple[Movement, ...]

    @property
    def definition_id(self) -> str:
        return self.definition.id

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def type(self) -> str:
        return self.definition.type

    @property
    def attributes(self) -> Mapping[str, AttributeValue]:
        return self.definition.attributes

    @property
    def uses(self) -> Tuple[str, ...]:
        return self.definition.uses

    @property
    def definition_notes(self) -> Optional[str]:
        return self.definition.notes

    @property
    def location(self) -> str:
        """Compatibility alias for the current location."""
        return self.current_location

    @property
    def estimated_space_liters(self) -> float:
        defaults = self.definition.type_defaults
        return defaults.default_space_liters if defaults is not None else 0.0

    @property
    def estimated_weight_kg(self) -> float:
        defaults = self.definition.type_defaults
        return defaults.default_weight_kg if defaults is not None else 0.0


# Kept as an import-compatible alias; inventory records now represent physical
# items and contain no quantity field.
InventoryItem = PhysicalItem


@dataclass(frozen=True)
class Query:
    """Criteria combined with AND semantics by :meth:`Inventory.find`."""

    location: Optional[str] = None
    preferred_location: Optional[str] = None
    name: Optional[str] = None
    item_type: Optional[str] = None
    attributes: Mapping[str, AttributeValue] = field(default_factory=empty_attributes)
    uses: Tuple[str, ...] = ()
    condition: Optional[str] = None
    status: Optional[str] = None
    text: Optional[str] = None
    include_related_types: bool = True


@dataclass(frozen=True)
class InventoryCount:
    """Distinct shared definitions and individually tracked possessions."""

    definitions: int
    physical_items: int


@dataclass(frozen=True)
class LocationSummary:
    location: Location
    total: InventoryCount
    by_type: Mapping[str, InventoryCount]
    by_use: Mapping[str, InventoryCount]
    by_condition: Mapping[str, InventoryCount]
    by_status: Mapping[str, InventoryCount]


@dataclass(frozen=True)
class CapacityEstimate:
    """A rough container estimate based on item-type defaults, not measurements."""

    location: Location
    item_count: int
    estimated_space_liters: float
    estimated_weight_kg: float
    remaining_space_liters: Optional[float]
    remaining_load_kg: Optional[float]
    space_utilization_percent: Optional[float]
    load_utilization_percent: Optional[float]


@dataclass(frozen=True)
class InventoryComparison:
    left: LocationSummary
    right: LocationSummary
    by_type: Mapping[str, Tuple[InventoryCount, InventoryCount]]
    shared_item_names: Tuple[str, ...]
    only_left: Tuple[PhysicalItem, ...]
    only_right: Tuple[PhysicalItem, ...]


@dataclass(frozen=True)
class DuplicateCandidate:
    left: ItemDefinition
    right: ItemDefinition
    kind: str
    score: float
    reasons: Tuple[str, ...]
