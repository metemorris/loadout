import yaml
import pytest

import inventory_toolkit.execution as execution_module
from inventory_toolkit import (
    RECONCILIATION_TOPICS,
    ExecutionValidationError,
    PackingValidationError,
    begin_trip_execution,
    cancel_trip_execution,
    complete_trip_execution,
    confirm_packing_decisions,
    load_inventory,
    load_packing_plans,
    load_trip,
    load_trip_execution,
    recover_pending_execution_actions,
    review_reconciliation,
    set_trip_status,
)
from inventory_toolkit.trips import complete_trip_after_reconciliation


pytestmark = pytest.mark.e2e


def test_duplicate_physical_item_in_one_plan_section_is_rejected(example_data):
    path = example_data / "packing_plans.yaml"
    document = yaml.safe_load(path.read_text())
    entries = document["plans"][0]["sections"]["pack"]
    entries.append(dict(entries[1]))
    path.write_text(yaml.safe_dump(document, sort_keys=False))

    with pytest.raises(PackingValidationError, match="repeats physical items"):
        load_packing_plans(example_data)


def test_duplicate_batch_id_cannot_move_an_unlogged_item(example_data):
    confirm_packing_decisions(
        "sample-trip-execution", "collision", ["pack:2"],
        confirmed=True, data_dir=example_data,
    )

    with pytest.raises(ExecutionValidationError, match="batch action ID already exists"):
        confirm_packing_decisions(
            "sample-trip-execution", "collision", ["pack:3"],
            confirmed=True, data_dir=example_data,
        )

    assert load_inventory(example_data).resolve_item("home-socks").current_location == "home"
    assert all(
        action.decision != "pack:3"
        for action in load_trip_execution("sample-trip-execution", example_data).actions
    )


def test_interrupted_batch_is_visible_and_recoverable(example_data, monkeypatch):
    original_mutate = execution_module._mutate_executions
    call_count = 0

    def interrupt_final_ledger_write(data_dir, mutator):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated process interruption")
        return original_mutate(data_dir, mutator)

    monkeypatch.setattr(execution_module, "_mutate_executions", interrupt_final_ledger_write)
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        confirm_packing_decisions(
            "sample-trip-execution", "interrupted", ["pack:2"],
            confirmed=True, data_dir=example_data,
        )

    pending = load_trip_execution("sample-trip-execution", example_data)
    action = next(value for value in pending.actions if value.id == "interrupted-1")
    assert action.state == "confirmed"
    assert load_inventory(example_data).resolve_item("home-underwear").current_location == "carry-on"

    monkeypatch.setattr(execution_module, "_mutate_executions", original_mutate)
    recovered = recover_pending_execution_actions(
        "sample-trip-execution", confirmed=True, data_dir=example_data
    )
    assert next(value for value in recovered.actions if value.id == "interrupted-1").state == "applied"


def test_begin_repairs_partial_trip_transition(example_data):
    set_trip_status("sample-trip", "in_progress", data_dir=example_data)
    assert load_trip_execution("sample-trip-execution", example_data).status == "preparing"

    execution = begin_trip_execution(
        "sample-trip-execution", confirmed=True, data_dir=example_data
    )
    assert execution.status == "in_progress"
    assert load_trip("sample-trip", example_data).status == "in_progress"


def test_complete_repairs_partial_execution_transition(example_data):
    begin_trip_execution("sample-trip-execution", confirmed=True, data_dir=example_data)
    recover_pending_execution_actions(
        "sample-trip-execution", confirmed=True, data_dir=example_data
    )
    review_reconciliation(
        "sample-trip-execution", RECONCILIATION_TOPICS,
        confirmed=True, data_dir=example_data,
    )
    complete_trip_after_reconciliation("sample-trip", data_dir=example_data)

    execution = complete_trip_execution(
        "sample-trip-execution", confirmed=True, data_dir=example_data
    )
    assert execution.status == "completed"
    assert load_trip("sample-trip", example_data).status == "completed"


def test_cancel_repairs_partial_execution_transition(example_data):
    set_trip_status("sample-trip", "cancelled", data_dir=example_data)

    execution = cancel_trip_execution(
        "sample-trip-execution", confirmed=True, data_dir=example_data
    )
    assert execution.status == "cancelled"
