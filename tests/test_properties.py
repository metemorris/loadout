import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from inventory_toolkit import ConfirmationRequiredError
from inventory_toolkit.repository import InMemoryCatalogRepository


pytestmark = pytest.mark.property

HOME_ITEMS = (
    "home-rain-jacket", "home-blue-jeans", "home-gray-hoodie",
    "home-white-sneakers", "home-charcoal-suit", "home-black-cap",
    "home-running-shorts", "home-cream-sweater", "home-underwear",
    "home-socks", "home-swimwear", "home-navy-tie", "home-brown-belt",
    "home-black-t-shirt",
)


@given(st.lists(st.sampled_from(HOME_ITEMS), min_size=1, unique=True))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40)
def test_confirmed_moves_preserve_counts_ids_and_preferred_locations(
    example_repository, item_ids
):
    repository = InMemoryCatalogRepository(example_repository.snapshot())
    before = repository.snapshot().inventory
    repository.confirm_movement(
        item_ids, "home", "carry-on", reason="Generated move", confirmed=True
    )
    after = repository.snapshot().inventory
    assert len(after.items) == len(before.items)
    assert {item.id for item in after.items} == {item.id for item in before.items}
    for item_id in item_ids:
        item = after.resolve_item(item_id)
        assert item.current_location == "carry-on"
        assert item.preferred_location == "home"
        assert item.movements[-1].source == "home"
        assert item.movements[-1].destination == "carry-on"


@given(st.sampled_from(HOME_ITEMS), st.sampled_from(("suitcase", "carry-on")))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=30)
def test_unconfirmed_generated_moves_never_change_state(
    example_repository, item_id, destination
):
    repository = InMemoryCatalogRepository(example_repository.snapshot())
    before = repository.snapshot()
    with pytest.raises(ConfirmationRequiredError):
        repository.confirm_movement([item_id], "home", destination)
    assert repository.snapshot() == before


@given(st.sampled_from(HOME_ITEMS))
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=20)
def test_preferred_location_changes_only_when_explicitly_requested(
    example_repository, item_id
):
    repository = InMemoryCatalogRepository(example_repository.snapshot())
    repository.confirm_movement(
        [item_id], "home", "carry-on", confirmed=True, update_preferred=True
    )
    item = repository.snapshot().inventory.resolve_item(item_id)
    assert item.current_location == item.preferred_location == "carry-on"
