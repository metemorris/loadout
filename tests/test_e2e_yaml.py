import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from inventory_toolkit import (
    confirm_packing_decisions,
    compile_packing_context,
    load_inventory,
    load_trip_executions,
    move_items,
    record_execution_action,
)
from inventory_toolkit.repository import YamlCatalogRepository


pytestmark = pytest.mark.e2e


def test_yaml_catalog_loads_and_compiles_complete_context(example_data):
    context = compile_packing_context("sample-trip", example_data)
    assert context.readiness.ready
    assert context.luggage == ("suitcase", "carry-on")
    assert len(context.legs) == 3
    assert context.matches


def test_yaml_movement_is_atomic_and_preserves_preference(example_data):
    move_items(
        ["home-rain-jacket"], "home", "carry-on", confirmed=True,
        reason="Durable adapter contract", data_dir=example_data,
    )
    item = load_inventory(example_data).resolve_item("home-rain-jacket")
    assert item.current_location == "carry-on"
    assert item.preferred_location == "home"
    assert item.movements[-1].reason == "Durable adapter contract"


def test_yaml_batch_retries_failed_and_applies_new_decisions(example_data):
    result = confirm_packing_decisions(
        "sample-trip-execution", "contract-batch", ["pack:2", "pack:3"],
        confirmed=True, reason="Durable batch contract", data_dir=example_data,
    )
    assert result.applied_decisions == ("pack:2", "pack:3")
    inventory = load_inventory(example_data)
    assert inventory.resolve_item("home-underwear").current_location == "carry-on"
    assert inventory.resolve_item("home-socks").current_location == "carry-on"


def test_yaml_repository_drives_the_real_batch_api(example_data):
    client = TestClient(create_app(repository=YamlCatalogRepository(example_data)))
    response = client.post(
        "/api/trips/sample-trip/packing-batch",
        json={
            "plan_id": "sample-trip-plan",
            "decisions": [{"section": "pack", "entry_index": 3}],
            "confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["appliedDecisions"] == ["pack:3"]
    assert load_inventory(example_data).resolve_item("home-socks").current_location == "carry-on"


def test_yaml_execution_feedback_round_trips_without_moving_inventory(example_data):
    before = load_inventory(example_data).resolve_item("home-black-cap")
    record_execution_action(
        "sample-trip-execution", "contract-rejection", "rejected",
        item=before.id, description="Changed recommendation", decision=None,
        leg="home-to-coast", reason="Synthetic user feedback note.",
        confirmed=True, data_dir=example_data,
    )
    execution = load_trip_executions(example_data).get("sample-trip-execution")
    saved = next(action for action in execution.actions if action.id == "contract-rejection")
    after = load_inventory(example_data).resolve_item(before.id)
    assert saved.reason == "Synthetic user feedback note."
    assert saved.state == "applied"
    assert after.current_location == before.current_location
    assert after.movements == before.movements
