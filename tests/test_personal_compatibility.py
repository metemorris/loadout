from pathlib import Path

import pytest

from inventory_toolkit import load_inventory, load_packing_plans, load_trip_executions, load_trips


pytestmark = pytest.mark.personal_data


def test_local_catalog_is_schema_compatible_without_assuming_its_contents():
    data_dir = Path(__file__).resolve().parents[1] / "data"
    inventory = load_inventory(data_dir)
    trips = load_trips(data_dir)
    plans = load_packing_plans(data_dir)
    executions = load_trip_executions(data_dir)
    assert inventory.items
    assert all(plan.trip in {trip.id for trip in trips.trips} for plan in plans.plans)
    assert all(execution.trip in {trip.id for trip in trips.trips} for execution in executions.executions)
