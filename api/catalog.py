"""Load presentation metadata shared by the API and web client."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

from inventory_toolkit.paths import default_data_directory


@dataclass(frozen=True)
class Category:
    """A display category and the inventory types assigned to it."""

    id: str
    name: str
    description: str
    artwork: str
    item_types: Tuple[str, ...]

    def payload(self) -> Dict[str, Any]:
        """Return the fields consumed by the web client."""

        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "artwork": self.artwork,
        }


def _catalog_path() -> Path:
    """Return the installed presentation-catalog path."""

    return default_data_directory() / "categories.yaml"


@lru_cache(maxsize=1)
def categories() -> Tuple[Category, ...]:
    """Load and validate the ordered category catalog once per process."""

    path = _catalog_path()
    with path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    records = document.get("categories") if isinstance(document, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError("categories.yaml must contain a non-empty categories list")

    result = []
    seen_ids = set()
    seen_types = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError("categories[{}] must be a mapping".format(index))
        category_id = record.get("id")
        name = record.get("name")
        description = record.get("description")
        artwork = record.get("artwork")
        item_types = record.get("item_types")
        if not all(isinstance(value, str) and value for value in (
            category_id, name, description, artwork
        )):
            raise ValueError("categories[{}] has missing display metadata".format(index))
        if category_id in seen_ids:
            raise ValueError("duplicate category id {!r}".format(category_id))
        if not isinstance(item_types, list) or any(
            not isinstance(item_type, str) or not item_type for item_type in item_types
        ):
            raise ValueError("categories[{}].item_types must be a string list".format(index))
        duplicates = seen_types.intersection(item_types)
        if duplicates:
            raise ValueError(
                "item types assigned to multiple categories: {}".format(
                    ", ".join(sorted(duplicates))
                )
            )
        seen_ids.add(category_id)
        seen_types.update(item_types)
        result.append(Category(category_id, name, description, artwork, tuple(item_types)))

    if result[-1].id != "other":
        raise ValueError("the final category must be the 'other' fallback")
    return tuple(result)


def category_for(item_type: str) -> Category:
    """Return the configured category for an inventory item type."""

    catalog = categories()
    return next(
        (category for category in catalog if item_type in category.item_types),
        catalog[-1],
    )
