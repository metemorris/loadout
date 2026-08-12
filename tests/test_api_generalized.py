import pytest
from fastapi.testclient import TestClient

from api.app import create_app


pytestmark = pytest.mark.api


@pytest.fixture
def client(example_repository):
    return TestClient(create_app(repository=example_repository))


def test_overview_uses_injected_catalog(client):
    payload = client.get("/api/overview").json()
    assert payload["totals"] == {
        "items": 16, "definitions": 16, "homes": 1, "containers": 2,
    }


def test_trip_detail_exposes_all_plan_sections_and_execution(client):
    response = client.get("/api/trips/sample-trip")
    assert response.status_code == 200
    payload = response.json()
    assert payload["plans"][0]["id"] == "sample-trip-plan"
    assert len(payload["plans"][0]["sections"]) == 9
    assert payload["executions"][0]["id"] == "sample-trip-execution"
    suitcase = next(value for value in payload["containers"] if value["id"] == "suitcase")
    assert suitcase["capacity"] == {
        "capacityLiters": 90,
        "maxLoadKg": 23,
        "estimatedUsedSpaceLiters": 3.5,
        "estimatedLoadKg": 0.65,
        "remainingSpaceLiters": 86.5,
        "remainingLoadKg": 22.35,
        "spaceUtilizationPercent": 3.9,
        "loadUtilizationPercent": 2.8,
        "basis": "rough_item_type_defaults",
    }


def test_activewear_bucket_contains_synthetic_swimwear(client):
    categories = client.get("/api/locations/home").json()["categories"]
    activewear = next(value for value in categories if value["id"] == "activewear")
    assert any(item["id"] == "home-swimwear" for item in activewear["items"])


def test_movement_preview_does_not_mutate_memory_repository(client):
    request = {
        "item_ids": ["home-underwear"], "source": "home",
        "destination": "carry-on", "reason": "Preview",
    }
    assert client.post("/api/movements/preview", json=request).status_code == 200
    assert client.get("/api/items/home-underwear").json()["currentLocation"] == "home"


def test_confirmed_movement_updates_current_not_preferred(client):
    response = client.post(
        "/api/movements/confirm",
        json={
            "item_ids": ["home-underwear"], "source": "home",
            "destination": "carry-on", "reason": "Packed in memory",
            "confirmed": True,
        },
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["currentLocation"] == "carry-on"
    assert item["preferredLocation"] == "home"


def test_unconfirmed_movement_is_rejected(client):
    response = client.post(
        "/api/movements/confirm",
        json={"item_ids": ["home-socks"], "source": "home", "destination": "carry-on"},
    )
    assert response.status_code == 428
