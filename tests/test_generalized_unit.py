import pytest

from inventory_toolkit import ConfirmationRequiredError, MovementError, PLAN_SECTIONS


pytestmark = pytest.mark.unit


def test_synthetic_catalog_covers_the_complete_trip_lifecycle(example_repository):
    snapshot = example_repository.snapshot()
    assert len(snapshot.inventory.items) == 16
    assert [trip.id for trip in snapshot.trips.trips] == ["sample-trip"]
    plan = snapshot.plans.get("sample-trip-plan")
    assert tuple(plan.sections) == PLAN_SECTIONS
    assert all(plan.sections[section] for section in PLAN_SECTIONS)
    execution = snapshot.executions.get("sample-trip-execution")
    assert {action.state for action in execution.actions} == {"applied", "failed", "confirmed"}
    assert any(action.kind == "rejected" for action in execution.actions)


def test_in_memory_preview_is_read_only(example_repository):
    before = example_repository.snapshot().inventory.resolve_item("home-underwear")
    plan = example_repository.preview_movement(
        [before.id], "home", "carry-on", reason="Preview only"
    )
    after = example_repository.snapshot().inventory.resolve_item(before.id)
    assert plan.destination == "carry-on"
    assert after == before


def test_in_memory_move_requires_confirmation(example_repository):
    with pytest.raises(ConfirmationRequiredError):
        example_repository.confirm_movement(
            ["home-underwear"], "home", "carry-on", reason="Test move"
        )
    assert example_repository.snapshot().inventory.resolve_item(
        "home-underwear"
    ).current_location == "home"


def test_in_memory_move_preserves_preferred_location(example_repository):
    example_repository.confirm_movement(
        ["home-underwear", "home-socks"], "home", "carry-on",
        reason="Test batch", confirmed=True,
    )
    inventory = example_repository.snapshot().inventory
    for item_id in ("home-underwear", "home-socks"):
        item = inventory.resolve_item(item_id)
        assert item.current_location == "carry-on"
        assert item.preferred_location == "home"
        assert item.movements[-1].reason == "Test batch"


def test_in_memory_move_rejects_a_stale_source(example_repository):
    with pytest.raises(MovementError, match="source check failed"):
        example_repository.confirm_movement(
            ["home-white-t-shirt"], "home", "carry-on", confirmed=True
        )


def test_container_capacity_uses_item_type_defaults(example_repository):
    inventory = example_repository.snapshot().inventory
    estimate = inventory.estimate_container_capacity("suitcase")
    assert estimate.item_count == 2
    assert estimate.estimated_space_liters == 3.5
    assert estimate.estimated_weight_kg == 0.65
    assert estimate.remaining_space_liters == 86.5
    assert estimate.space_utilization_percent == 3.9
