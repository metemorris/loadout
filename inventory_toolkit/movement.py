"""Confirmed, atomic inventory movement operations.

This module owns state-change rules and persistence. It deliberately contains
no CLI presentation and never infers which items should move.
"""

import fcntl
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

import yaml

from .loader import load_inventory
from .models import PhysicalItem
from .paths import default_data_directory


class MovementError(ValueError):
    pass


class ConfirmationRequiredError(MovementError):
    def __init__(self, plan: "MovementPlan"):
        self.plan = plan
        super().__init__(
            "confirmation required to move {} physical item{} from {!r} to {!r}: {}".format(
                len(plan.item_ids),
                "" if len(plan.item_ids) == 1 else "s",
                plan.source,
                plan.destination,
                ", ".join(plan.item_ids),
            )
        )


class StateConfirmationRequiredError(MovementError):
    pass


@dataclass(frozen=True)
class MovementPlan:
    item_ids: Tuple[str, ...]
    source: str
    destination: str
    timestamp: str
    reason: Optional[str]
    update_preferred: bool


@dataclass(frozen=True)
class MovementResult:
    plan: MovementPlan
    applied: bool


@dataclass(frozen=True)
class ItemStateResult:
    item: PhysicalItem
    applied: bool


def _data_directory(data_dir: Optional[Path]) -> Path:
    return Path(data_dir) if data_dir is not None else default_data_directory()


def _normalize_timestamp(value: Optional[str]) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif not isinstance(value, str) or not value.strip():
        raise MovementError("timestamp must be a non-empty ISO 8601 string")
    else:
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise MovementError("timestamp must be valid ISO 8601")
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise MovementError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def plan_movement(
    item_ids: Sequence[str],
    source: str,
    destination: str,
    *,
    data_dir: Optional[Path] = None,
    reason: Optional[str] = None,
    timestamp: Optional[str] = None,
    update_preferred: bool = False,
) -> MovementPlan:
    """Validate and describe a movement without changing inventory state."""

    if not item_ids:
        raise MovementError("at least one physical item ID is required")
    normalized_ids = tuple(item_ids)
    if len(set(normalized_ids)) != len(normalized_ids):
        raise MovementError("physical item IDs must not be repeated")
    if reason is not None:
        if not isinstance(reason, str) or not reason.strip():
            raise MovementError("reason must be a non-empty string when supplied")
        reason = reason.strip()

    inventory = load_inventory(_data_directory(data_dir))
    source_id = inventory.resolve_location(source).id
    destination_id = inventory.resolve_location(destination).id
    if source_id == destination_id:
        raise MovementError("source and destination must differ")

    wrong_source = []
    for item_id in normalized_ids:
        item = inventory.resolve_item(item_id)
        if item.current_location != source_id:
            wrong_source.append("{} is at {}".format(item.id, item.current_location))
    if wrong_source:
        raise MovementError(
            "movement source check failed; " + "; ".join(wrong_source)
        )

    return MovementPlan(
        item_ids=normalized_ids,
        source=source_id,
        destination=destination_id,
        timestamp=_normalize_timestamp(timestamp),
        reason=reason,
        update_preferred=bool(update_preferred),
    )


def _validated_yaml(candidate: dict, data_dir: Path) -> str:
    payload = yaml.safe_dump(
        candidate,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    )
    # Validate the exact serialized representation before replacing live data.
    with tempfile.TemporaryDirectory(prefix=".inventory-validate-", dir=str(data_dir)) as temporary:
        temporary_dir = Path(temporary)
        (temporary_dir / "clothes.yaml").write_text(payload, encoding="utf-8")
        shutil.copy2(str(data_dir / "schema.yaml"), str(temporary_dir / "schema.yaml"))
        shutil.copy2(str(data_dir / "locations.yaml"), str(temporary_dir / "locations.yaml"))
        load_inventory(temporary_dir)
    return payload


def _atomic_replace(path: Path, payload: str) -> None:
    mode = path.stat().st_mode
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".clothes-",
            suffix=".yaml",
            dir=str(path.parent),
            delete=False,
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


def move_items(
    item_ids: Sequence[str],
    source: str,
    destination: str,
    *,
    confirmed: bool = False,
    data_dir: Optional[Path] = None,
    reason: Optional[str] = None,
    timestamp: Optional[str] = None,
    update_preferred: bool = False,
) -> MovementResult:
    """Move selected physical items after explicit confirmation.

    The expected source makes stale commands fail rather than moving an item
    from an unobserved location. Multi-item moves are validated and committed as
    one atomic operation.
    """

    directory = _data_directory(data_dir)
    initial_plan = plan_movement(
        item_ids,
        source,
        destination,
        data_dir=directory,
        reason=reason,
        timestamp=timestamp,
        update_preferred=update_preferred,
    )
    if not confirmed:
        raise ConfirmationRequiredError(initial_plan)

    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".inventory.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        # Re-check all facts under the write lock to protect against stale plans.
        plan = plan_movement(
            item_ids,
            source,
            destination,
            data_dir=directory,
            reason=reason,
            timestamp=initial_plan.timestamp,
            update_preferred=update_preferred,
        )
        path = directory / "clothes.yaml"
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
        instances = {instance["id"]: instance for instance in raw["instances"]}
        movement = {
            "timestamp": plan.timestamp,
            "source": plan.source,
            "destination": plan.destination,
            "reason": plan.reason,
        }
        for item_id in plan.item_ids:
            instance = instances[item_id]
            instance["current_location"] = plan.destination
            if plan.update_preferred:
                instance["preferred_location"] = plan.destination
            instance["movements"].append(dict(movement))

        payload = _validated_yaml(raw, directory)
        _atomic_replace(path, payload)
        return MovementResult(plan=plan, applied=True)


def update_item_status(
    item_id: str,
    status: Optional[str],
    *,
    confirmed: bool = False,
    data_dir: Optional[Path] = None,
) -> ItemStateResult:
    """Explicitly update physical status without changing either location."""

    if status is not None and (not isinstance(status, str) or not status.strip()):
        raise MovementError("status must be a non-empty string or null")
    if not confirmed:
        raise StateConfirmationRequiredError(
            "confirmation required to update status for {!r}".format(item_id)
        )
    directory = _data_directory(data_dir)
    lock_path = directory / ".inventory.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        inventory = load_inventory(directory)
        before = inventory.resolve_item(item_id)
        path = directory / "clothes.yaml"
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
        instance = next(value for value in raw["instances"] if value["id"] == item_id)
        instance["status"] = status.strip() if isinstance(status, str) else None
        payload = _validated_yaml(raw, directory)
        _atomic_replace(path, payload)
        updated = load_inventory(directory).resolve_item(item_id)
        if (updated.current_location, updated.preferred_location) != (
            before.current_location, before.preferred_location,
        ):
            raise MovementError("status update unexpectedly changed an item location")
        return ItemStateResult(updated, True)


def register_purchased_item(
    item_id: str,
    definition_id: str,
    current_location: str,
    *,
    confirmed: bool = False,
    data_dir: Optional[Path] = None,
    name: Optional[str] = None,
    item_type: Optional[str] = None,
    attributes: Optional[Mapping[str, Any]] = None,
    uses: Sequence[str] = (),
    preferred_location: Optional[str] = None,
    condition: Optional[str] = None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
) -> ItemStateResult:
    """Register a retained purchase as one new physical possession."""

    if not confirmed:
        raise StateConfirmationRequiredError(
            "confirmation required to register purchased item {!r}".format(item_id)
        )
    directory = _data_directory(data_dir)
    lock_path = directory / ".inventory.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        inventory = load_inventory(directory)
        if any(item.id == item_id for item in inventory.items):
            raise MovementError("physical item {!r} already exists".format(item_id))
        location_id = inventory.resolve_location(current_location).id
        preferred_id = inventory.resolve_location(preferred_location or location_id).id
        path = directory / "clothes.yaml"
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
        definitions = {value["id"]: value for value in raw["definitions"]}
        source_ids = {
            source["id"] for definition in raw["definitions"] for source in definition["sources"]
        }
        if item_id in source_ids:
            raise MovementError("purchase source id {!r} already exists".format(item_id))
        definition = definitions.get(definition_id)
        if definition is None:
            if not isinstance(name, str) or not name.strip():
                raise MovementError("name is required for a new purchased-item definition")
            if not isinstance(item_type, str) or not item_type.strip():
                raise MovementError("item_type is required for a new purchased-item definition")
            definition = {
                "id": definition_id,
                "name": name.strip(),
                "type": item_type.strip(),
                "attributes": dict(attributes or {}),
                "use": list(uses),
                "notes": None,
                "sources": [],
            }
            raw["definitions"].append(definition)
        elif any(value is not None for value in (name, item_type, attributes)) or uses:
            raise MovementError(
                "shared definition fields must be omitted when definition {!r} already exists".format(
                    definition_id
                )
            )
        definition["sources"].append(
            {
                "id": item_id,
                "original_location": location_id,
                "original_quantity": 1,
                "original_condition": condition,
                "notes": "Purchased during trip execution.",
            }
        )
        raw["instances"].append(
            {
                "id": item_id,
                "definition": definition_id,
                "source": item_id,
                "current_location": location_id,
                "preferred_location": preferred_id,
                "condition": condition,
                "status": status,
                "notes": notes,
                "movements": [],
            }
        )
        payload = _validated_yaml(raw, directory)
        _atomic_replace(path, payload)
        return ItemStateResult(load_inventory(directory).resolve_item(item_id), True)
