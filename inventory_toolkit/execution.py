"""Trip execution ledger, confirmed side effects, and completion reconciliation."""

import fcntl
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml

from .loader import load_inventory
from .movement import move_items, plan_movement, register_purchased_item, update_item_status
from .paths import default_data_directory
from .packing import PLAN_SECTIONS, PackingPlan, load_packing_plan, load_packing_plans
from .trips import (
    TripNotFoundError,
    complete_trip_after_reconciliation,
    load_trip,
    load_trips,
    set_trip_status,
)

ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DECISION_PATTERN = re.compile(r"^([a-z_]+):([1-9][0-9]*)$")
EXECUTION_FIELDS = {
    "id", "trip", "packing_plan", "status", "created_at", "actions", "reconciliation", "notes",
}
ACTION_FIELDS = {
    "id", "timestamp", "kind", "item", "description", "decision", "leg", "source",
    "destination", "reason", "states",
}
STATE_FIELDS = {"timestamp", "status", "notes"}
RECONCILIATION_FIELDS = {
    "what_came_back", "stayed_at_destination", "never_used", "purchased", "incidents",
    "rejected_or_changed", "notes", "completed_at",
}
RECONCILIATION_TOPICS = (
    "what_came_back",
    "stayed_at_destination",
    "never_used",
    "purchased",
    "incidents",
    "rejected_or_changed",
)
MOVEMENT_KINDS = frozenset({"packed", "transferred", "returned", "stayed"})
STATUS_BY_KIND = {"damaged": "damaged", "discarded": "discarded", "lost": "lost"}


class ExecutionValidationError(ValueError):
    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(errors)
        super().__init__("Execution validation failed:\n- " + "\n- ".join(self.errors))


class ExecutionConfirmationRequiredError(ExecutionValidationError):
    pass


class TripExecutionNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ActionState:
    timestamp: str
    status: str
    notes: Optional[str] = None


@dataclass(frozen=True)
class ExecutionAction:
    id: str
    timestamp: str
    kind: str
    item: Optional[str]
    description: Optional[str]
    decision: Optional[str]
    leg: Optional[str]
    source: Optional[str]
    destination: Optional[str]
    reason: str
    states: Tuple[ActionState, ...]

    @property
    def state(self) -> str:
        return self.states[-1].status


@dataclass(frozen=True)
class BatchPackingResult:
    execution: "TripExecution"
    applied_decisions: Tuple[str, ...]
    failed_decisions: Tuple[str, ...]


@dataclass(frozen=True)
class TripReconciliation:
    what_came_back: bool = False
    stayed_at_destination: bool = False
    never_used: bool = False
    purchased: bool = False
    incidents: bool = False
    rejected_or_changed: bool = False
    notes: Optional[str] = None
    completed_at: Optional[str] = None

    @property
    def ready(self) -> bool:
        return all(getattr(self, topic) for topic in RECONCILIATION_TOPICS)


@dataclass(frozen=True)
class TripExecution:
    id: str
    trip: str
    packing_plan: str
    status: str
    created_at: str
    actions: Tuple[ExecutionAction, ...]
    reconciliation: TripReconciliation
    notes: Optional[str] = None


class TripExecutionCatalog:
    def __init__(self, executions: Sequence[TripExecution]):
        self._executions = tuple(executions)
        self._by_id = MappingProxyType({execution.id: execution for execution in executions})

    @property
    def executions(self) -> Tuple[TripExecution, ...]:
        return self._executions

    def get(self, execution_id: str) -> TripExecution:
        try:
            return self._by_id[execution_id]
        except KeyError:
            raise TripExecutionNotFoundError("unknown trip execution {!r}".format(execution_id))


def _data_directory(data_dir: Optional[Path]) -> Path:
    return Path(data_dir) if data_dir is not None else default_data_directory()


def _read_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError:
        raise ExecutionValidationError(["missing file: {}".format(path)])
    except yaml.YAMLError as exc:
        raise ExecutionValidationError(["{}: invalid YAML: {}".format(path.name, exc)])


def _timestamp(value: Optional[str] = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif not isinstance(value, str) or not value.strip():
        raise ExecutionValidationError(["timestamp must be a non-empty ISO 8601 string"])
    else:
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ExecutionValidationError(["timestamp must be valid ISO 8601"])
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ExecutionValidationError(["timestamp must include a timezone"])
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _optional_string(value: Any, label: str, errors: List[str]) -> bool:
    valid = value is None or (isinstance(value, str) and bool(value.strip()))
    if not valid:
        errors.append("{} must be a non-empty string or null".format(label))
    return valid


def _required(schema: Mapping[str, Any], section: str, errors: List[str]) -> Sequence[str]:
    fields = schema.get(section, {}).get("required_fields") if isinstance(schema.get(section), dict) else None
    if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
        errors.append("schema.yaml: {}.required_fields must be a list of strings".format(section))
        return ()
    return fields


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


def _decision_entry(plan: PackingPlan, reference: str) -> Any:
    match = DECISION_PATTERN.fullmatch(reference or "")
    if match is None or match.group(1) not in PLAN_SECTIONS:
        raise ExecutionValidationError(["invalid packing decision reference {!r}".format(reference)])
    entries = plan.sections[match.group(1)]
    index = int(match.group(2)) - 1
    if index >= len(entries):
        raise ExecutionValidationError(["unknown packing decision {!r}".format(reference)])
    return match.group(1), entries[index]


def _parse_store(raw: Any, schema: Any, data_dir: Path) -> TripExecutionCatalog:
    errors: List[str] = []
    if not isinstance(schema, dict) or schema.get("version") != 8:
        errors.append("schema.yaml: version must be 8")
        schema = schema if isinstance(schema, dict) else {}
    if not isinstance(schema.get("trip_execution_store"), dict) or schema["trip_execution_store"].get("root") != "mapping":
        errors.append("schema.yaml: trip_execution_store.root must be 'mapping'")
    for section in (
        "trip_executions", "trip_execution_actions", "trip_execution_action_states"
    ):
        if not isinstance(schema.get(section), dict) or schema[section].get("root") != "list":
            errors.append("schema.yaml: {}.root must be 'list'".format(section))
    if not isinstance(schema.get("trip_reconciliation"), dict) or schema["trip_reconciliation"].get("root") != "mapping":
        errors.append("schema.yaml: trip_reconciliation.root must be 'mapping'")
    if not isinstance(raw, dict) or set(raw) != {"executions"} or not isinstance(raw.get("executions"), list):
        errors.append("trip_executions.yaml: root must contain only an executions list")
        raw = {"executions": []}
    inventory = load_inventory(data_dir)
    inventory_ids = {item.id for item in inventory.items}
    location_ids = {location.id for location in inventory.locations}
    trips = load_trips(data_dir)
    plans = load_packing_plans(data_dir)
    allowed_statuses = schema.get("trip_executions", {}).get("allowed_statuses", [])
    allowed_kinds = schema.get("trip_execution_actions", {}).get("allowed_kinds", [])
    allowed_action_states = schema.get("trip_execution_action_states", {}).get("allowed_statuses", [])
    executions = []
    seen_execution_ids = set()
    seen_trip_ids = set()
    for execution_index, value in enumerate(raw["executions"], 1):
        label = "trip_executions.yaml execution {}".format(execution_index)
        if not isinstance(value, dict):
            errors.append("{} must be a mapping".format(label))
            continue
        valid = _check_fields(
            value, _required(schema, "trip_executions", errors), EXECUTION_FIELDS, label, errors
        )
        execution_id = value.get("id")
        if not isinstance(execution_id, str) or not ID_PATTERN.fullmatch(execution_id):
            errors.append("{} has invalid id {!r}".format(label, execution_id))
            valid = False
        if execution_id in seen_execution_ids:
            errors.append("{} has duplicate id {!r}".format(label, execution_id))
            valid = False
        seen_execution_ids.add(execution_id)
        trip_id = value.get("trip")
        try:
            trip = trips.get(trip_id)
        except TripNotFoundError:
            errors.append("{} has unknown trip {!r}".format(label, trip_id))
            valid = False
            trip = None
        if trip_id in seen_trip_ids:
            errors.append("trip {!r} has more than one execution ledger".format(trip_id))
            valid = False
        seen_trip_ids.add(trip_id)
        plan_id = value.get("packing_plan")
        try:
            plan = plans.get(plan_id)
        except LookupError:
            errors.append("{} has unknown packing plan {!r}".format(label, plan_id))
            valid = False
            plan = None
        if plan and (plan.trip != trip_id or plan.status != "confirmed"):
            errors.append("{} packing plan must be confirmed and belong to its trip".format(label))
            valid = False
        status = value.get("status")
        if status not in allowed_statuses:
            errors.append("{} has unknown status {!r}".format(label, status))
            valid = False
        try:
            created_at = _timestamp(value.get("created_at"))
            if created_at != value.get("created_at"):
                errors.append("{}.created_at must be normalized UTC".format(label))
                valid = False
        except ExecutionValidationError as exc:
            errors.extend("{}.created_at: {}".format(label, error) for error in exc.errors)
            created_at = ""
            valid = False
        valid &= _optional_string(value.get("notes"), "{}.notes".format(label), errors)
        raw_actions = value.get("actions")
        if not isinstance(raw_actions, list):
            errors.append("{}.actions must be a list".format(label))
            raw_actions = []
            valid = False
        action_ids = set()
        actions = []
        previous_action_time = None
        known_legs = {leg.id for leg in trip.legs} if trip else set()
        for action_index, raw_action in enumerate(raw_actions, 1):
            action_label = "{}.actions entry {}".format(label, action_index)
            if not isinstance(raw_action, dict):
                errors.append("{} must be a mapping".format(action_label))
                valid = False
                continue
            action_valid = _check_fields(
                raw_action,
                _required(schema, "trip_execution_actions", errors),
                ACTION_FIELDS,
                action_label,
                errors,
            )
            action_id = raw_action.get("id")
            if not isinstance(action_id, str) or not ID_PATTERN.fullmatch(action_id):
                errors.append("{} has invalid id {!r}".format(action_label, action_id))
                action_valid = False
            if action_id in action_ids:
                errors.append("{} has duplicate id {!r}".format(label, action_id))
                action_valid = False
            action_ids.add(action_id)
            try:
                action_time = _timestamp(raw_action.get("timestamp"))
                if action_time != raw_action.get("timestamp"):
                    errors.append("{}.timestamp must be normalized UTC".format(action_label))
                    action_valid = False
            except ExecutionValidationError as exc:
                errors.extend("{}.timestamp: {}".format(action_label, error) for error in exc.errors)
                action_time = ""
                action_valid = False
            if previous_action_time and action_time and action_time < previous_action_time:
                errors.append("{} is earlier than the preceding action".format(action_label))
                action_valid = False
            previous_action_time = action_time or previous_action_time
            kind = raw_action.get("kind")
            if kind not in allowed_kinds:
                errors.append("{} has unknown kind {!r}".format(action_label, kind))
                action_valid = False
            item = raw_action.get("item")
            if item is not None and item not in inventory_ids and kind != "purchased":
                errors.append("{} has unknown item {!r}".format(action_label, item))
                action_valid = False
            description = raw_action.get("description")
            action_valid &= _optional_string(description, "{}.description".format(action_label), errors)
            if item is None and description is None:
                errors.append("{} must identify an item or description".format(action_label))
                action_valid = False
            decision = raw_action.get("decision")
            if decision is not None:
                try:
                    _section, planned_entry = _decision_entry(plan, decision) if plan else (None, None)
                    if planned_entry and item != planned_entry.item:
                        errors.append("{} item does not match packing decision".format(action_label))
                        action_valid = False
                except ExecutionValidationError as exc:
                    errors.extend("{}: {}".format(action_label, error) for error in exc.errors)
                    action_valid = False
            leg = raw_action.get("leg")
            if leg is not None and leg not in known_legs:
                errors.append("{} has unknown leg {!r}".format(action_label, leg))
                action_valid = False
            source = raw_action.get("source")
            destination = raw_action.get("destination")
            if (source is None) != (destination is None):
                errors.append("{} source and destination must both be set or both be null".format(action_label))
                action_valid = False
            for field, location in (("source", source), ("destination", destination)):
                if location is not None and location not in location_ids:
                    errors.append("{} has unknown {} {!r}".format(action_label, field, location))
                    action_valid = False
            if source is not None and kind not in MOVEMENT_KINDS:
                errors.append("{} kind {!r} cannot record a location movement".format(action_label, kind))
                action_valid = False
            if kind in ("transferred", "returned") and source is None:
                errors.append("{} kind {!r} requires a location movement".format(action_label, kind))
                action_valid = False
            reason = raw_action.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                errors.append("{}.reason must be a non-empty string".format(action_label))
                action_valid = False
            raw_states = raw_action.get("states")
            if not isinstance(raw_states, list) or not raw_states:
                errors.append("{}.states must be a non-empty list".format(action_label))
                raw_states = []
                action_valid = False
            states = []
            previous_state_time = None
            for state_index, raw_state in enumerate(raw_states, 1):
                state_label = "{}.states entry {}".format(action_label, state_index)
                if not isinstance(raw_state, dict):
                    errors.append("{} must be a mapping".format(state_label))
                    action_valid = False
                    continue
                state_valid = _check_fields(
                    raw_state,
                    _required(schema, "trip_execution_action_states", errors),
                    STATE_FIELDS,
                    state_label,
                    errors,
                )
                state_status = raw_state.get("status")
                if state_status not in allowed_action_states:
                    errors.append("{} has unknown status {!r}".format(state_label, state_status))
                    state_valid = False
                try:
                    state_time = _timestamp(raw_state.get("timestamp"))
                    if state_time != raw_state.get("timestamp"):
                        errors.append("{}.timestamp must be normalized UTC".format(state_label))
                        state_valid = False
                except ExecutionValidationError as exc:
                    errors.extend("{}.timestamp: {}".format(state_label, error) for error in exc.errors)
                    state_time = ""
                    state_valid = False
                if previous_state_time and state_time and state_time < previous_state_time:
                    errors.append("{} is earlier than the preceding state".format(state_label))
                    state_valid = False
                previous_state_time = state_time or previous_state_time
                state_valid &= _optional_string(raw_state.get("notes"), "{}.notes".format(state_label), errors)
                if state_valid:
                    states.append(ActionState(state_time, state_status, raw_state.get("notes")))
                else:
                    action_valid = False
            if states and states[0].status != "confirmed":
                errors.append("{}.states must begin with confirmed".format(action_label))
                action_valid = False
            if len(states) > 2 or (len(states) == 2 and states[1].status not in ("applied", "failed")):
                errors.append("{}.states must be confirmed then applied or failed".format(action_label))
                action_valid = False
            if action_valid:
                actions.append(
                    ExecutionAction(
                        action_id, action_time, kind, item, description, decision, leg,
                        source, destination, reason, tuple(states),
                    )
                )
            else:
                valid = False
        raw_reconciliation = value.get("reconciliation")
        reconciliation = TripReconciliation()
        if not isinstance(raw_reconciliation, dict):
            errors.append("{}.reconciliation must be a mapping".format(label))
            valid = False
        else:
            valid &= _check_fields(
                raw_reconciliation,
                _required(schema, "trip_reconciliation", errors),
                RECONCILIATION_FIELDS,
                "{}.reconciliation".format(label),
                errors,
            )
            for topic in RECONCILIATION_TOPICS:
                if not isinstance(raw_reconciliation.get(topic), bool):
                    errors.append("{}.reconciliation.{} must be boolean".format(label, topic))
                    valid = False
            valid &= _optional_string(
                raw_reconciliation.get("notes"), "{}.reconciliation.notes".format(label), errors
            )
            completed_at = raw_reconciliation.get("completed_at")
            if completed_at is not None:
                try:
                    completed_at = _timestamp(completed_at)
                    if completed_at != raw_reconciliation.get("completed_at"):
                        errors.append("{}.reconciliation.completed_at must be normalized UTC".format(label))
                        valid = False
                except ExecutionValidationError as exc:
                    errors.extend("{}.reconciliation.completed_at: {}".format(label, error) for error in exc.errors)
                    valid = False
            reconciliation = TripReconciliation(
                *(raw_reconciliation.get(topic, False) for topic in RECONCILIATION_TOPICS),
                notes=raw_reconciliation.get("notes"),
                completed_at=completed_at,
            )
        if status == "completed" and (not reconciliation.ready or reconciliation.completed_at is None):
            errors.append("{} completed execution requires finished reconciliation".format(label))
            valid = False
        if status == "completed" and trip and trip.status != "completed":
            errors.append("{} completed execution requires completed trip status".format(label))
            valid = False
        if status == "cancelled" and trip and trip.status != "cancelled":
            errors.append("{} cancelled execution requires cancelled trip status".format(label))
            valid = False
        if valid:
            executions.append(
                TripExecution(
                    execution_id, trip_id, plan_id, status, created_at, tuple(actions),
                    reconciliation, value.get("notes"),
                )
            )
    if errors:
        raise ExecutionValidationError(errors)
    return TripExecutionCatalog(executions)


def load_trip_executions(data_dir: Optional[Path] = None) -> TripExecutionCatalog:
    directory = _data_directory(data_dir)
    return _parse_store(
        _read_yaml(directory / "trip_executions.yaml"),
        _read_yaml(directory / "schema.yaml"),
        directory,
    )


def load_trip_execution(execution_id: str, data_dir: Optional[Path] = None) -> TripExecution:
    return load_trip_executions(data_dir).get(execution_id)


def _action_to_raw(action: ExecutionAction) -> Dict[str, Any]:
    return {
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
        "states": [
            {"timestamp": state.timestamp, "status": state.status, "notes": state.notes}
            for state in action.states
        ],
    }


def _execution_to_raw(execution: TripExecution) -> Dict[str, Any]:
    return {
        "id": execution.id,
        "trip": execution.trip,
        "packing_plan": execution.packing_plan,
        "status": execution.status,
        "created_at": execution.created_at,
        "actions": [_action_to_raw(action) for action in execution.actions],
        "reconciliation": {
            topic: getattr(execution.reconciliation, topic) for topic in RECONCILIATION_TOPICS
        } | {
            "notes": execution.reconciliation.notes,
            "completed_at": execution.reconciliation.completed_at,
        },
        "notes": execution.notes,
    }


def _atomic_replace(path: Path, payload: str) -> None:
    mode = path.stat().st_mode
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".trip-executions-", suffix=".yaml",
            dir=str(path.parent), delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, str(path))
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def _mutate_executions(
    data_dir: Optional[Path], mutation: Callable[[List[Dict[str, Any]], TripExecutionCatalog], None]
) -> TripExecutionCatalog:
    directory = _data_directory(data_dir)
    path = directory / "trip_executions.yaml"
    lock_path = directory / ".trip-executions.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        raw = _read_yaml(path)
        schema = _read_yaml(directory / "schema.yaml")
        current = _parse_store(raw, schema, directory)
        mutation(raw["executions"], current)
        validated = _parse_store(raw, schema, directory)
        payload = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=1000)
        _parse_store(yaml.safe_load(payload), schema, directory)
        _atomic_replace(path, payload)
        return validated


def create_trip_execution(
    execution_id: str,
    trip_id: str,
    packing_plan_id: str,
    *,
    confirmed: bool = False,
    timestamp: Optional[str] = None,
    notes: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> TripExecution:
    if not confirmed:
        raise ExecutionConfirmationRequiredError(["confirmation required to create trip execution"])
    trip = load_trip(trip_id, data_dir)
    plan = load_packing_plan(packing_plan_id, data_dir)
    if trip.status != "planned":
        raise ExecutionValidationError(["trip must be planned when execution is created"])
    if plan.trip != trip.id or plan.status != "confirmed":
        raise ExecutionValidationError(["execution requires a confirmed plan for the same trip"])
    execution = TripExecution(
        execution_id, trip.id, plan.id, "preparing", _timestamp(timestamp), (),
        TripReconciliation(), notes,
    )

    def add(raw_executions: List[Dict[str, Any]], catalog: TripExecutionCatalog) -> None:
        if any(value.id == execution.id for value in catalog.executions):
            raise ExecutionValidationError(["execution {!r} already exists".format(execution.id)])
        if any(value.trip == trip.id for value in catalog.executions):
            raise ExecutionValidationError(["trip {!r} already has an execution".format(trip.id)])
        raw_executions.append(_execution_to_raw(execution))

    return _mutate_executions(data_dir, add).get(execution.id)


def begin_trip_execution(
    execution_id: str, *, confirmed: bool = False, data_dir: Optional[Path] = None
) -> TripExecution:
    if not confirmed:
        raise ExecutionConfirmationRequiredError(["confirmation required to begin trip execution"])
    execution = load_trip_execution(execution_id, data_dir)
    trip = load_trip(execution.trip, data_dir)
    if execution.status not in ("preparing", "in_progress"):
        raise ExecutionValidationError(["execution must be preparing before trip start"])
    if trip.status not in ("planned", "in_progress"):
        raise ExecutionValidationError(
            ["trip must be planned or in progress when execution begins"]
        )
    if trip.status == "planned":
        set_trip_status(execution.trip, "in_progress", data_dir=data_dir)
    if execution.status == "in_progress":
        return load_trip_execution(execution_id, data_dir)

    def update(raw_executions: List[Dict[str, Any]], _catalog: TripExecutionCatalog) -> None:
        raw = next(value for value in raw_executions if value["id"] == execution_id)
        raw["status"] = "in_progress"

    return _mutate_executions(data_dir, update).get(execution_id)


def _append_action(execution_id: str, action: ExecutionAction, data_dir: Optional[Path]) -> TripExecution:
    def add(raw_executions: List[Dict[str, Any]], catalog: TripExecutionCatalog) -> None:
        execution = catalog.get(execution_id)
        if execution.status not in ("preparing", "in_progress", "reconciling"):
            raise ExecutionValidationError(["execution status does not accept actions"])
        if any(existing.id == action.id for existing in execution.actions):
            raise ExecutionValidationError(["action {!r} already exists".format(action.id)])
        raw = next(value for value in raw_executions if value["id"] == execution_id)
        raw["actions"].append(_action_to_raw(action))

    return _mutate_executions(data_dir, add).get(execution_id)


def _finish_action(
    execution_id: str,
    action_id: str,
    status: str,
    timestamp: str,
    notes: Optional[str],
    data_dir: Optional[Path],
) -> TripExecution:
    def finish(raw_executions: List[Dict[str, Any]], catalog: TripExecutionCatalog) -> None:
        action = next(
            (value for value in catalog.get(execution_id).actions if value.id == action_id), None
        )
        if action is None or action.state != "confirmed":
            raise ExecutionValidationError(["action must be pending confirmation before finishing"])
        raw_execution = next(value for value in raw_executions if value["id"] == execution_id)
        raw_action = next(value for value in raw_execution["actions"] if value["id"] == action_id)
        raw_action["states"].append({"timestamp": timestamp, "status": status, "notes": notes})

    return _mutate_executions(data_dir, finish).get(execution_id)


def record_execution_action(
    execution_id: str,
    action_id: str,
    kind: str,
    *,
    item: Optional[str] = None,
    description: Optional[str] = None,
    decision: Optional[str] = None,
    leg: Optional[str] = None,
    source: Optional[str] = None,
    destination: Optional[str] = None,
    reason: str,
    confirmed: bool = False,
    timestamp: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> TripExecution:
    """Record a confirmed factual action and apply its inventory side effect."""

    if not confirmed:
        raise ExecutionConfirmationRequiredError(["confirmation required to record execution action"])
    execution = load_trip_execution(execution_id, data_dir)
    action_time = _timestamp(timestamp)
    inventory = load_inventory(data_dir)
    if item is not None and kind != "purchased":
        inventory.resolve_item(item)
    if kind == "purchased" and item is not None:
        try:
            inventory.resolve_item(item)
        except LookupError:
            raise ExecutionValidationError(
                ["unknown retained purchase; use the purchase registration command"]
            )
    if source is not None or destination is not None:
        if item is None or source is None or destination is None:
            raise ExecutionValidationError(["movement actions require item, source, and destination"])
        plan_movement([item], source, destination, data_dir=data_dir, reason=reason, timestamp=action_time)
    action = ExecutionAction(
        action_id, action_time, kind, item, description, decision, leg, source, destination,
        reason, (ActionState(action_time, "confirmed", None),),
    )
    _append_action(execution.id, action, data_dir)
    try:
        if source is not None:
            move_items(
                [item], source, destination, confirmed=True, data_dir=data_dir,
                reason="Trip {} action {}: {}".format(execution.trip, action.id, reason),
                timestamp=action_time,
            )
        elif kind in STATUS_BY_KIND:
            if item is None:
                raise ExecutionValidationError(["{} action requires an item".format(kind)])
            update_item_status(item, STATUS_BY_KIND[kind], confirmed=True, data_dir=data_dir)
        return _finish_action(execution.id, action.id, "applied", action_time, None, data_dir)
    except Exception as exc:
        _finish_action(execution.id, action.id, "failed", action_time, str(exc), data_dir)
        raise


def confirm_packing_decision(
    execution_id: str,
    action_id: str,
    decision: str,
    *,
    accepted: bool,
    confirmed: bool = False,
    destination: Optional[str] = None,
    reason: Optional[str] = None,
    timestamp: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> TripExecution:
    execution = load_trip_execution(execution_id, data_dir)
    plan = load_packing_plan(execution.packing_plan, data_dir)
    section, entry = _decision_entry(plan, decision)
    if not accepted:
        return record_execution_action(
            execution_id, action_id, "rejected", item=entry.item,
            description=entry.requirement, decision=decision, leg=entry.leg,
            reason=reason or "Rejected planned {} decision.".format(section),
            confirmed=confirmed, timestamp=timestamp, data_dir=data_dir,
        )
    move_destination = None
    move_source = None
    kind = "accepted"
    if section == "pack":
        kind = "packed"
        move_destination = entry.container
    elif section == "wear_in_transit":
        kind = "packed"
        move_destination = destination
    if move_destination is not None and entry.item is not None:
        move_source = load_inventory(data_dir).resolve_item(entry.item).current_location
    return record_execution_action(
        execution_id, action_id, kind, item=entry.item,
        description=entry.requirement, decision=decision, leg=entry.leg,
        source=move_source, destination=move_destination,
        reason=reason or "Accepted planned {} decision.".format(section),
        confirmed=confirmed, timestamp=timestamp, data_dir=data_dir,
    )


def confirm_packing_decisions(
    execution_id: str,
    action_id_prefix: str,
    decisions: Sequence[str],
    *,
    confirmed: bool = False,
    reason: str = "Packed selected items.",
    timestamp: Optional[str] = None,
    data_dir: Optional[Path] = None,
    execution_snapshot: Optional[TripExecution] = None,
    plan_snapshot: Optional[PackingPlan] = None,
    inventory_snapshot: Optional[Any] = None,
) -> BatchPackingResult:
    """Apply multiple pack decisions with grouped moves and one ledger write."""

    if not confirmed:
        raise ExecutionConfirmationRequiredError(
            ["confirmation required to pack selected decisions"]
        )
    if not decisions:
        raise ExecutionValidationError(["at least one packing decision is required"])
    if len(set(decisions)) != len(decisions):
        raise ExecutionValidationError(["packing decisions must not be repeated"])
    execution = execution_snapshot or load_trip_execution(execution_id, data_dir)
    if execution.status not in ("preparing", "in_progress", "reconciling"):
        raise ExecutionValidationError(["execution status does not accept actions"])
    plan = plan_snapshot or load_packing_plan(execution.packing_plan, data_dir)
    existing_decisions = {
        action.decision
        for action in execution.actions
        if action.decision and action.state != "failed"
    }
    repeated = [decision for decision in decisions if decision in existing_decisions]
    if repeated:
        raise ExecutionValidationError(
            ["packing decisions already have outcomes: {}".format(", ".join(repeated))]
        )

    inventory = inventory_snapshot or load_inventory(data_dir)
    resolved = []
    seen_items = set()
    movement_groups: Dict[Tuple[str, str], List[Tuple[str, Any]]] = {}
    for decision in decisions:
        section, entry = _decision_entry(plan, decision)
        if section != "pack" or entry.item is None or entry.container is None:
            raise ExecutionValidationError(
                ["decision {!r} is not a physical pack decision".format(decision)]
            )
        if entry.item in seen_items:
            raise ExecutionValidationError(["selected decisions repeat a physical item"])
        seen_items.add(entry.item)
        item = inventory.resolve_item(entry.item)
        if item.current_location == entry.container:
            raise ExecutionValidationError(
                ["{} is already in {}".format(item.id, entry.container)]
            )
        inventory.resolve_location(entry.container)
        resolved.append((decision, entry, item))
        movement_groups.setdefault((item.current_location, entry.container), []).append(
            (decision, item)
        )

    action_time = _timestamp(timestamp)
    actions = []
    for index, (decision, entry, _item) in enumerate(resolved, 1):
        actions.append(
            ExecutionAction(
                "{}-{}".format(action_id_prefix, index),
                action_time,
                "packed",
                entry.item,
                entry.requirement,
                decision,
                entry.leg,
                _item.current_location,
                entry.container,
                reason,
                (ActionState(action_time, "confirmed", None),),
            )
        )

    def add_batch(raw_executions: List[Dict[str, Any]], catalog: TripExecutionCatalog) -> None:
        current = catalog.get(execution_id)
        if current.status not in ("preparing", "in_progress", "reconciling"):
            raise ExecutionValidationError(["execution status does not accept actions"])
        if any(action.id in {existing.id for existing in current.actions} for action in actions):
            raise ExecutionValidationError(["batch action ID already exists"])
        raw = next(value for value in raw_executions if value["id"] == execution_id)
        raw["actions"].extend(_action_to_raw(action) for action in actions)

    # Commit the user's confirmed intent before changing physical inventory. If
    # the process stops after this point, recovery can reconcile a visible
    # pending action; no physical move can become an unlogged fact.
    _mutate_executions(data_dir, add_batch)

    outcomes: Dict[str, Tuple[str, Optional[str]]] = {}
    for (source, destination), values in movement_groups.items():
        group_decisions = [decision for decision, _item in values]
        try:
            move_items(
                [item.id for _decision, item in values],
                source,
                destination,
                confirmed=True,
                data_dir=data_dir,
                reason="Trip {} batch {}: {}".format(
                    execution.trip, action_id_prefix, reason
                ),
                timestamp=action_time,
            )
            outcomes.update((decision, ("applied", None)) for decision in group_decisions)
        except Exception as exc:
            outcomes.update((decision, ("failed", str(exc))) for decision in group_decisions)

    states_by_id = {
        action.id: outcomes[action.decision]
        for action in actions
        if action.decision is not None
    }

    def finish_batch(
        raw_executions: List[Dict[str, Any]], catalog: TripExecutionCatalog
    ) -> None:
        current = catalog.get(execution_id)
        current_by_id = {action.id: action for action in current.actions}
        raw = next(value for value in raw_executions if value["id"] == execution_id)
        raw_by_id = {action["id"]: action for action in raw["actions"]}
        for action_id, (status, notes) in states_by_id.items():
            current_action = current_by_id.get(action_id)
            if current_action is None or current_action.state != "confirmed":
                raise ExecutionValidationError(
                    ["batch action {!r} is not pending".format(action_id)]
                )
            raw_by_id[action_id]["states"].append(
                {"timestamp": action_time, "status": status, "notes": notes}
            )

    updated = _mutate_executions(data_dir, finish_batch).get(execution_id)
    return BatchPackingResult(
        updated,
        tuple(decision for decision in decisions if outcomes[decision][0] == "applied"),
        tuple(decision for decision in decisions if outcomes[decision][0] == "failed"),
    )


def recover_pending_execution_actions(
    execution_id: str,
    *,
    confirmed: bool = False,
    data_dir: Optional[Path] = None,
) -> TripExecution:
    """Explicitly reconcile actions left pending by an interrupted write."""

    if not confirmed:
        raise ExecutionConfirmationRequiredError(
            ["confirmation required to recover pending execution actions"]
        )
    execution = load_trip_execution(execution_id, data_dir)
    pending = [action for action in execution.actions if action.state == "confirmed"]
    if not pending:
        return execution

    inventory = load_inventory(data_dir)
    outcomes: Dict[str, Tuple[str, Optional[str]]] = {}
    for action in pending:
        try:
            if action.source is not None and action.destination is not None:
                if action.item is None:
                    raise ExecutionValidationError(
                        ["movement action {!r} has no item".format(action.id)]
                    )
                item = load_inventory(data_dir).resolve_item(action.item)
                if item.current_location == action.destination:
                    outcomes[action.id] = (
                        "applied",
                        "Recovered: item was already at the recorded destination.",
                    )
                    continue
                if item.current_location != action.source:
                    raise ExecutionValidationError(
                        [
                            "item {!r} is at {!r}, expected {!r} or {!r}".format(
                                item.id,
                                item.current_location,
                                action.source,
                                action.destination,
                            )
                        ]
                    )
                move_items(
                    [item.id],
                    action.source,
                    action.destination,
                    confirmed=True,
                    data_dir=data_dir,
                    reason="Recovered interrupted execution action {}: {}".format(
                        action.id, action.reason
                    ),
                    timestamp=action.timestamp,
                )
            elif action.kind in STATUS_BY_KIND:
                if action.item is None:
                    raise ExecutionValidationError(
                        ["{} action requires an item".format(action.kind)]
                    )
                current = inventory.resolve_item(action.item)
                expected_status = STATUS_BY_KIND[action.kind]
                if current.status != expected_status:
                    update_item_status(
                        action.item,
                        expected_status,
                        confirmed=True,
                        data_dir=data_dir,
                    )
            elif action.kind == "purchased":
                if action.item is None:
                    raise ExecutionValidationError(["purchase action requires an item"])
                load_inventory(data_dir).resolve_item(action.item)
            outcomes[action.id] = ("applied", "Recovered interrupted action.")
        except Exception as exc:
            outcomes[action.id] = ("failed", "Recovery failed: {}".format(exc))

    recovered_at = _timestamp()

    def finish_pending(
        raw_executions: List[Dict[str, Any]], catalog: TripExecutionCatalog
    ) -> None:
        current = catalog.get(execution_id)
        current_by_id = {action.id: action for action in current.actions}
        raw = next(value for value in raw_executions if value["id"] == execution_id)
        raw_by_id = {action["id"]: action for action in raw["actions"]}
        for action_id, (status, notes) in outcomes.items():
            if current_by_id[action_id].state != "confirmed":
                raise ExecutionValidationError(
                    ["action {!r} is no longer pending".format(action_id)]
                )
            raw_by_id[action_id]["states"].append(
                {"timestamp": recovered_at, "status": status, "notes": notes}
            )

    return _mutate_executions(data_dir, finish_pending).get(execution_id)


def record_purchased_item(
    execution_id: str,
    action_id: str,
    item_id: str,
    definition_id: str,
    current_location: str,
    *,
    name: Optional[str] = None,
    item_type: Optional[str] = None,
    attributes: Optional[Mapping[str, Any]] = None,
    uses: Sequence[str] = (),
    preferred_location: Optional[str] = None,
    condition: Optional[str] = None,
    notes: Optional[str] = None,
    leg: Optional[str] = None,
    reason: str,
    confirmed: bool = False,
    timestamp: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> TripExecution:
    if not confirmed:
        raise ExecutionConfirmationRequiredError(["confirmation required to register purchase"])
    action_time = _timestamp(timestamp)
    action = ExecutionAction(
        action_id, action_time, "purchased", item_id, name, None, leg, None, None, reason,
        (ActionState(action_time, "confirmed", None),),
    )
    _append_action(execution_id, action, data_dir)
    try:
        register_purchased_item(
            item_id, definition_id, current_location, confirmed=True, data_dir=data_dir,
            name=name, item_type=item_type, attributes=attributes, uses=uses,
            preferred_location=preferred_location, condition=condition, notes=notes,
        )
        return _finish_action(execution_id, action_id, "applied", action_time, None, data_dir)
    except Exception as exc:
        _finish_action(execution_id, action_id, "failed", action_time, str(exc), data_dir)
        raise


def review_reconciliation(
    execution_id: str,
    topics: Sequence[str],
    *,
    confirmed: bool = False,
    notes: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> TripExecution:
    if not confirmed:
        raise ExecutionConfirmationRequiredError(["confirmation required to save reconciliation"])
    unknown = set(topics) - set(RECONCILIATION_TOPICS)
    if unknown:
        raise ExecutionValidationError(["unknown reconciliation topics: {}".format(", ".join(sorted(unknown)))])

    def update(raw_executions: List[Dict[str, Any]], catalog: TripExecutionCatalog) -> None:
        execution = catalog.get(execution_id)
        if execution.status not in ("in_progress", "reconciling"):
            raise ExecutionValidationError(["execution must be in progress for reconciliation"])
        raw = next(value for value in raw_executions if value["id"] == execution_id)
        raw["status"] = "reconciling"
        for topic in topics:
            raw["reconciliation"][topic] = True
        if notes is not None:
            raw["reconciliation"]["notes"] = notes

    return _mutate_executions(data_dir, update).get(execution_id)


def complete_trip_execution(
    execution_id: str,
    *,
    confirmed: bool = False,
    timestamp: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> TripExecution:
    if not confirmed:
        raise ExecutionConfirmationRequiredError(["confirmation required to complete trip"])
    execution = load_trip_execution(execution_id, data_dir)
    trip = load_trip(execution.trip, data_dir)
    if execution.status == "completed":
        if trip.status != "completed":
            complete_trip_after_reconciliation(execution.trip, data_dir=data_dir)
        return load_trip_execution(execution_id, data_dir)
    if execution.status != "reconciling" or not execution.reconciliation.ready:
        missing = [
            topic for topic in RECONCILIATION_TOPICS
            if not getattr(execution.reconciliation, topic)
        ]
        raise ExecutionValidationError(
            ["reconciliation is incomplete: {}".format(", ".join(missing) or "not started")]
        )
    pending = [action.id for action in execution.actions if action.state == "confirmed"]
    if pending:
        raise ExecutionValidationError(["pending actions must be resolved: {}".format(", ".join(pending))])
    completed_at = _timestamp(timestamp)
    if trip.status == "in_progress":
        complete_trip_after_reconciliation(execution.trip, data_dir=data_dir)
    elif trip.status != "completed":
        raise ExecutionValidationError(
            ["trip must be in progress before execution completion"]
        )

    def finish(raw_executions: List[Dict[str, Any]], _catalog: TripExecutionCatalog) -> None:
        raw = next(value for value in raw_executions if value["id"] == execution_id)
        raw["status"] = "completed"
        raw["reconciliation"]["completed_at"] = completed_at

    return _mutate_executions(data_dir, finish).get(execution_id)


def cancel_trip_execution(
    execution_id: str, *, confirmed: bool = False, data_dir: Optional[Path] = None
) -> TripExecution:
    if not confirmed:
        raise ExecutionConfirmationRequiredError(["confirmation required to cancel trip execution"])
    execution = load_trip_execution(execution_id, data_dir)
    trip = load_trip(execution.trip, data_dir)
    if execution.status == "cancelled":
        if trip.status != "cancelled":
            set_trip_status(execution.trip, "cancelled", data_dir=data_dir)
        return load_trip_execution(execution_id, data_dir)
    if execution.status == "completed":
        raise ExecutionValidationError(["execution is already terminal"])
    if trip.status != "cancelled":
        set_trip_status(execution.trip, "cancelled", data_dir=data_dir)

    def cancel(raw_executions: List[Dict[str, Any]], _catalog: TripExecutionCatalog) -> None:
        raw = next(value for value in raw_executions if value["id"] == execution_id)
        raw["status"] = "cancelled"

    return _mutate_executions(data_dir, cancel).get(execution_id)


def item_execution_history(
    item_id: str, data_dir: Optional[Path] = None
) -> Tuple[Tuple[str, str, str, str, str], ...]:
    """Return execution/action/kind/state/timestamp facts for future planning."""

    history = []
    for execution in load_trip_executions(data_dir).executions:
        for action in execution.actions:
            if action.item == item_id:
                history.append(
                    (execution.trip, execution.id, action.kind, action.state, action.timestamp)
                )
    return tuple(history)
