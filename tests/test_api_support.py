"""Focused unit coverage for the API adapter's extracted support modules."""

import pytest

from api.catalog import categories, category_for
from api.packing_service import editable_plan, replace_draft_entry
from api.serializers import category_payloads, item_payload, plain


pytestmark = pytest.mark.unit


def test_category_catalog_has_one_fallback_and_unique_type_assignments():
    catalog = categories()
    assigned_types = [item_type for category in catalog for item_type in category.item_types]

    assert catalog[-1].id == "other"
    assert len(assigned_types) == len(set(assigned_types))
    assert category_for("swimwear").id == "activewear"
    assert category_for("synthetic_unknown_type").id == "other"
    assert category_payloads()[0]["artwork"].startswith("/assets/")


def test_item_payload_uses_shared_category_metadata(example_repository):
    item = example_repository.snapshot().inventory.resolve_item("home-swimwear")

    payload = item_payload(item)

    assert payload["id"] == item.id
    assert payload["category"] == "activewear"
    assert payload["currentLocation"] == "home"


def test_plain_serializes_immutable_domain_models(example_repository):
    trip = example_repository.snapshot().trips.get("sample-trip")

    payload = plain(trip)

    assert payload["id"] == "sample-trip"
    assert isinstance(payload["legs"], list)


def test_confirmed_plan_edit_creates_draft_without_changing_original(example_repository):
    plan = example_repository.snapshot().plans.get("sample-trip-plan")

    revision = editable_plan(plan)

    assert plan.status == "confirmed"
    assert revision.status == "draft"
    assert revision.id.startswith("sample-trip-plan-ui-rev-")
    assert revision.sections == plan.sections


def test_replace_draft_entry_removes_only_selected_entry(example_repository):
    plan = editable_plan(example_repository.snapshot().plans.get("sample-trip-plan"))
    original_count = len(plan.sections["pack"])

    updated = replace_draft_entry(plan, "pack", 1, None)

    assert len(updated.sections["pack"]) == original_count - 1
    assert len(plan.sections["pack"]) == original_count
