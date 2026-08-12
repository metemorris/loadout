from inventory_toolkit import execution, loader, packing, trips
from inventory_toolkit.load_context import catalog_load_context


def test_snapshot_context_reuses_nested_catalog_loads(example_data, monkeypatch):
    counts = {"inventory": 0, "trips": 0, "plans": 0}

    for module, name, key in (
        (loader, "_load_inventory", "inventory"),
        (trips, "_parse_trips", "trips"),
        (packing, "_parse_plan_store", "plans"),
    ):
        original = getattr(module, name)

        def counted(*args, _original=original, _key=key, **kwargs):
            counts[_key] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, counted)

    with catalog_load_context():
        inventory = loader.load_inventory(example_data)
        trip_catalog = trips.load_trips(example_data)
        plan_catalog = packing.load_packing_plans(example_data)
        execution.load_trip_executions(example_data)

    assert inventory.items
    assert trip_catalog.trips
    assert plan_catalog.plans
    assert counts == {"inventory": 1, "trips": 1, "plans": 1}


def test_loads_are_not_cached_between_contexts(example_data):
    with catalog_load_context():
        first = loader.load_inventory(example_data)
        assert loader.load_inventory(example_data) is first
    with catalog_load_context():
        assert loader.load_inventory(example_data) is not first
