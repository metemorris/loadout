"""Storage boundaries for catalog reads and inventory movements."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Optional, Protocol, Sequence, Tuple

from .execution import TripExecutionCatalog, load_trip_executions
from .loader import load_inventory
from .models import Movement
from .movement import (
    ConfirmationRequiredError,
    MovementError,
    MovementPlan,
    MovementResult,
    move_items,
    plan_movement,
)
from .packing import PackingPlanCatalog, load_packing_plans
from .query import Inventory
from .trips import TripCatalog, load_trips


@dataclass(frozen=True)
class CatalogSnapshot:
    inventory: Inventory
    trips: TripCatalog
    plans: PackingPlanCatalog
    executions: TripExecutionCatalog

    def as_tuple(self) -> Tuple[Inventory, TripCatalog, PackingPlanCatalog, TripExecutionCatalog]:
        return self.inventory, self.trips, self.plans, self.executions


class CatalogRepository(Protocol):
    @property
    def data_dir(self) -> Optional[Path]: ...

    def snapshot(self) -> CatalogSnapshot: ...

    def preview_movement(
        self, item_ids: Sequence[str], source: str, destination: str, *,
        reason: Optional[str] = None, update_preferred: bool = False,
    ) -> MovementPlan: ...

    def confirm_movement(
        self, item_ids: Sequence[str], source: str, destination: str, *,
        confirmed: bool = False, reason: Optional[str] = None,
        update_preferred: bool = False,
    ) -> MovementResult: ...


class YamlCatalogRepository:
    """Durable repository backed by the validated YAML documents."""

    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir).expanduser().resolve()

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def snapshot(self) -> CatalogSnapshot:
        return CatalogSnapshot(
            load_inventory(self._data_dir),
            load_trips(self._data_dir),
            load_packing_plans(self._data_dir),
            load_trip_executions(self._data_dir),
        )

    def preview_movement(self, item_ids, source, destination, **kwargs) -> MovementPlan:
        return plan_movement(item_ids, source, destination, data_dir=self._data_dir, **kwargs)

    def confirm_movement(self, item_ids, source, destination, **kwargs) -> MovementResult:
        return move_items(item_ids, source, destination, data_dir=self._data_dir, **kwargs)


class InMemoryCatalogRepository:
    """Fast isolated catalog used by domain and API tests."""

    def __init__(self, snapshot: CatalogSnapshot):
        self._snapshot = snapshot
        self._lock = RLock()

    @classmethod
    def from_directory(cls, data_dir: Path) -> "InMemoryCatalogRepository":
        return cls(YamlCatalogRepository(data_dir).snapshot())

    @property
    def data_dir(self) -> None:
        return None

    def snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def preview_movement(
        self, item_ids: Sequence[str], source: str, destination: str, *,
        reason: Optional[str] = None, update_preferred: bool = False,
    ) -> MovementPlan:
        ids = tuple(item_ids)
        if not ids or any(not isinstance(item_id, str) or not item_id for item_id in ids):
            raise MovementError("at least one physical item ID is required")
        if len(set(ids)) != len(ids):
            raise MovementError("physical item IDs must not be repeated")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise MovementError("reason must be a non-empty string when supplied")
        inventory = self._snapshot.inventory
        source_id = inventory.resolve_location(source).id
        destination_id = inventory.resolve_location(destination).id
        if source_id == destination_id:
            raise MovementError("source and destination must differ")
        wrong_source = [
            "{} is at {}".format(item_id, inventory.resolve_item(item_id).current_location)
            for item_id in ids
            if inventory.resolve_item(item_id).current_location != source_id
        ]
        if wrong_source:
            raise MovementError("movement source check failed; " + "; ".join(wrong_source))
        return MovementPlan(
            ids, source_id, destination_id, self._timestamp(),
            reason.strip() if reason else None, bool(update_preferred),
        )

    def confirm_movement(
        self, item_ids: Sequence[str], source: str, destination: str, *,
        confirmed: bool = False, reason: Optional[str] = None,
        update_preferred: bool = False,
    ) -> MovementResult:
        with self._lock:
            plan = self.preview_movement(
                item_ids, source, destination, reason=reason,
                update_preferred=update_preferred,
            )
            if not confirmed:
                raise ConfirmationRequiredError(plan)
            movement = Movement(
                plan.timestamp, plan.source, plan.destination, plan.reason
            )
            selected = set(plan.item_ids)
            items = tuple(
                replace(
                    item,
                    current_location=plan.destination,
                    preferred_location=plan.destination if plan.update_preferred else item.preferred_location,
                    movements=(*item.movements, movement),
                ) if item.id in selected else item
                for item in self._snapshot.inventory.items
            )
            inventory = Inventory(
                items, self._snapshot.inventory.definitions,
                self._snapshot.inventory.locations,
            )
            self._snapshot = replace(self._snapshot, inventory=inventory)
            return MovementResult(plan, True)
