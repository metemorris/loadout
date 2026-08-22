import pytest
import yaml
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


def test_yaml_api_requires_confirmation_before_adding_inventory(example_data):
    client = TestClient(create_app(repository=YamlCatalogRepository(example_data)))
    before = len(load_inventory(example_data).items)

    response = client.post(
        "/api/inventory/items",
        json={
            "name": "Lightweight Green T-Shirt",
            "type": "t_shirt",
            "current_location": "carry-on",
            "preferred_location": "home",
        },
    )

    assert response.status_code == 428
    assert len(load_inventory(example_data).items) == before


def test_yaml_api_adds_one_confirmed_physical_inventory_item(example_data):
    client = TestClient(create_app(repository=YamlCatalogRepository(example_data)))

    response = client.post(
        "/api/inventory/items",
        json={
            "name": "Lightweight Green T-Shirt",
            "type": "t_shirt",
            "current_location": "carry-on",
            "preferred_location": "home",
            "attributes": {"color": "green", "feature": "breathable"},
            "uses": ["casual", "travel"],
            "condition": "new",
            "notes": "Added from the wardrobe UI contract test.",
            "confirmed": True,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["applied"] is True
    assert payload["item"]["id"] == "home-lightweight-green-t-shirt"
    item = load_inventory(example_data).resolve_item(payload["item"]["id"])
    assert item.current_location == "carry-on"
    assert item.preferred_location == "home"
    assert item.attributes["color"] == "green"
    assert item.uses == ("casual", "travel")
    assert item.movements == ()


def test_inventory_options_are_schema_backed(example_data):
    client = TestClient(create_app(repository=YamlCatalogRepository(example_data)))

    response = client.get("/api/inventory/options")

    assert response.status_code == 200
    assert "t_shirt" in response.json()["itemTypes"]
    assert "travel" in response.json()["uses"]


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


def test_yaml_wear_in_transit_swap_creates_revision_without_moving_inventory(
    example_data,
):
    clothes_path = example_data / "clothes.yaml"
    clothes = yaml.safe_load(clothes_path.read_text(encoding="utf-8"))
    next(
        value for value in clothes["definitions"] if value["id"] == "home-blue-jeans"
    )["type"] = "shoes"
    clothes_path.write_text(
        yaml.safe_dump(clothes, sort_keys=False), encoding="utf-8"
    )
    client = TestClient(create_app(repository=YamlCatalogRepository(example_data)))
    before = load_inventory(example_data).resolve_item("home-blue-jeans")

    candidates = client.get(
        "/api/trips/sample-trip/packing-plans/sample-trip-plan/swap-candidates",
        params={"item_id": "home-white-sneakers"},
    )
    assert candidates.status_code == 200
    assert [value["id"] for value in candidates.json()["items"]] == ["home-blue-jeans"]

    response = client.post(
        "/api/trips/sample-trip/packing-actions",
        json={
            "plan_id": "sample-trip-plan",
            "section": "wear_in_transit",
            "entry_index": 1,
            "action": "swap",
            "replacement_item_id": "home-blue-jeans",
            "notes": "Prefer this pair for the flight.",
            "confirmed": True,
        },
    )

    assert response.status_code == 200, response.text
    revision = next(
        value for value in response.json()["plans"]
        if value["id"].startswith("sample-trip-plan-ui-rev-")
    )
    assert revision["sections"]["wear_in_transit"][0]["item"] == "home-blue-jeans"
    after = load_inventory(example_data).resolve_item("home-blue-jeans")
    assert after.current_location == before.current_location
    assert after.movements == before.movements


def test_yaml_swap_rejects_replacement_of_a_different_type(example_data):
    client = TestClient(create_app(repository=YamlCatalogRepository(example_data)))

    response = client.post(
        "/api/trips/sample-trip/packing-actions",
        json={
            "plan_id": "sample-trip-plan",
            "section": "wear_in_transit",
            "entry_index": 1,
            "action": "swap",
            "replacement_item_id": "home-blue-jeans",
            "notes": "A different item type must not be accepted.",
            "confirmed": True,
        },
    )

    assert response.status_code == 409
    assert "same type" in response.json()["detail"]


def test_yaml_wear_in_transit_supports_add_and_remove_without_movement(example_data):
    client = TestClient(create_app(repository=YamlCatalogRepository(example_data)))
    before = load_inventory(example_data).resolve_item("home-blue-jeans")

    added = client.post(
        "/api/trips/sample-trip/packing-plan-items",
        json={
            "plan_id": "sample-trip-plan",
            "item_id": "home-blue-jeans",
            "section": "wear_in_transit",
            "reason": "Reported transit outfit.",
            "confirmed": True,
        },
    )

    assert added.status_code == 200, added.text
    revision = next(
        value for value in added.json()["plans"]
        if value["id"].startswith("sample-trip-plan-ui-rev-")
    )
    wear_entries = revision["sections"]["wear_in_transit"]
    assert wear_entries[-1]["item"] == "home-blue-jeans"
    assert wear_entries[-1]["container"] is None

    removed = client.post(
        "/api/trips/sample-trip/packing-actions",
        json={
            "plan_id": revision["id"],
            "section": "wear_in_transit",
            "entry_index": len(wear_entries),
            "action": "remove",
            "confirmed": True,
        },
    )

    assert removed.status_code == 200, removed.text
    updated = next(
        value for value in removed.json()["plans"] if value["id"] == revision["id"]
    )
    assert all(
        value["item"] != "home-blue-jeans"
        for value in updated["sections"]["wear_in_transit"]
    )
    after = load_inventory(example_data).resolve_item("home-blue-jeans")
    assert after.current_location == before.current_location
    assert after.movements == before.movements


def test_yaml_change_bag_can_add_an_empty_container_to_the_trip(example_data):
    locations_path = example_data / "locations.yaml"
    locations = yaml.safe_load(locations_path.read_text(encoding="utf-8"))
    locations.append({
        "id": "backpack",
        "name": "Backpack",
        "kind": "travel_container",
        "capacity_liters": 25,
        "max_load_kg": 10,
        "aliases": [],
        "notes": None,
    })
    locations_path.write_text(
        yaml.safe_dump(locations, sort_keys=False), encoding="utf-8"
    )
    client = TestClient(create_app(repository=YamlCatalogRepository(example_data)))

    response = client.post(
        "/api/trips/sample-trip/packing-plan-containers",
        json={
            "plan_id": "sample-trip-plan",
            "section": "pack",
            "entry_index": 1,
            "container": "backpack",
            "confirmed": True,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "backpack" in payload["trip"]["luggage"]
    assert any(value["id"] == "backpack" for value in payload["containers"])
    revision = next(value for value in payload["plans"] if value["status"] == "draft")
    assert revision["sections"]["pack"][0]["container"] == "backpack"
    assert load_inventory(example_data).resolve_item(
        "home-white-t-shirt"
    ).current_location == "backpack"
    transfer = next(
        action for action in payload["executions"][0]["actions"]
        if action["kind"] == "transferred" and action["item"] == "home-white-t-shirt"
    )
    assert transfer["source"] == "suitcase"
    assert transfer["destination"] == "backpack"
