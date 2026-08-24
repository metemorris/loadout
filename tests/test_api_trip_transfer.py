from fastapi.testclient import TestClient
import pytest

import inventory_toolkit.execution as execution_module
from api.app import create_app
from inventory_toolkit.loader import load_inventory
from inventory_toolkit.repository import YamlCatalogRepository


pytestmark = pytest.mark.api


@pytest.fixture
def client(example_data):
    return TestClient(create_app(repository=YamlCatalogRepository(example_data)))


def _request(items, **overrides):
    return {
        "items": [
            {"item_id": item_id, "source": source}
            for item_id, source in items
        ],
        "destination": "home",
        "reason": "Unpacked at the current destination during the ongoing trip.",
        **overrides,
    }


def test_luggage_transfer_requires_confirmation(client, example_data):
    before = load_inventory(example_data).resolve_item("home-white-t-shirt")

    response = client.post(
        "/api/trips/sample-trip/luggage-transfer",
        json=_request([(before.id, "suitcase")]),
    )

    assert response.status_code == 428
    after = load_inventory(example_data).resolve_item(before.id)
    assert after.current_location == "suitcase"
    assert after.movements == before.movements


def test_luggage_transfer_moves_selected_items_and_records_trip_actions(
    client, example_data, monkeypatch
):
    item_ids = ("home-white-t-shirt", "suitcase-travel-towel")
    preferred_locations = {
        item_id: load_inventory(example_data).resolve_item(item_id).preferred_location
        for item_id in item_ids
    }
    original_mutate = execution_module._mutate_executions
    original_move = execution_module.move_items
    ledger_writes = 0
    inventory_writes = []

    def count_ledger_write(data_dir, mutator):
        nonlocal ledger_writes
        ledger_writes += 1
        return original_mutate(data_dir, mutator)

    def count_inventory_write(item_ids, source, destination, **kwargs):
        inventory_writes.append((tuple(item_ids), source, destination))
        return original_move(item_ids, source, destination, **kwargs)

    monkeypatch.setattr(execution_module, "_mutate_executions", count_ledger_write)
    monkeypatch.setattr(execution_module, "move_items", count_inventory_write)

    response = client.post(
        "/api/trips/sample-trip/luggage-transfer",
        json=_request(
            [(item_id, "suitcase") for item_id in item_ids],
            confirmed=True,
        ),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    actions = payload["executions"][0]["actions"][-2:]
    assert [action["item"] for action in actions] == list(item_ids)
    assert all(action["kind"] == "transferred" for action in actions)
    assert all(action["source"] == "suitcase" for action in actions)
    assert all(action["destination"] == "home" for action in actions)
    assert all(action["states"][-1]["status"] == "applied" for action in actions)
    assert ledger_writes == 2
    assert inventory_writes == [(item_ids, "suitcase", "home")]
    for item_id in item_ids:
        item = load_inventory(example_data).resolve_item(item_id)
        assert item.current_location == "home"
        assert item.preferred_location == preferred_locations[item_id]


def test_luggage_transfer_validates_every_expected_source_before_writing(
    client, example_data
):
    item_ids = ("home-white-t-shirt", "suitcase-travel-towel")

    response = client.post(
        "/api/trips/sample-trip/luggage-transfer",
        json=_request(
            [(item_ids[0], "suitcase"), (item_ids[1], "carry-on")],
            confirmed=True,
        ),
    )

    assert response.status_code == 409
    assert "expected at 'carry-on' but is at 'suitcase'" in response.json()["detail"]
    inventory = load_inventory(example_data)
    assert all(
        inventory.resolve_item(item_id).current_location == "suitcase"
        for item_id in item_ids
    )


def test_luggage_transfer_rejects_a_container_destination(client, example_data):
    response = client.post(
        "/api/trips/sample-trip/luggage-transfer",
        json=_request(
            [("home-white-t-shirt", "suitcase")],
            destination="carry-on",
            confirmed=True,
        ),
    )

    assert response.status_code == 409
    assert "require a home destination" in response.json()["detail"]
    assert (
        load_inventory(example_data)
        .resolve_item("home-white-t-shirt")
        .current_location
        == "suitcase"
    )
