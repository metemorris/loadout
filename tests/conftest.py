import shutil
from pathlib import Path

import pytest

from inventory_toolkit.repository import InMemoryCatalogRepository, YamlCatalogRepository


ROOT = Path(__file__).resolve().parents[1]
SHARED_CATALOG_FILES = ("schema.yaml", "activity_templates.yaml", "requirement_matchers.yaml")
EXAMPLE_STATE_FILES = (
    "clothes.yaml", "locations.yaml", "trips.yaml",
    "packing_plans.yaml", "trip_executions.yaml",
)


@pytest.fixture(scope="session")
def example_catalog_template(tmp_path_factory):
    destination = tmp_path_factory.mktemp("example-catalog")
    for name in SHARED_CATALOG_FILES:
        shutil.copy2(ROOT / "data" / name, destination / name)
    for name in EXAMPLE_STATE_FILES:
        shutil.copy2(ROOT / "data" / "examples" / name, destination / name)
    return destination


@pytest.fixture
def example_data(tmp_path, example_catalog_template):
    destination = tmp_path / "catalog"
    shutil.copytree(example_catalog_template, destination)
    return destination


@pytest.fixture(scope="session")
def example_snapshot(example_catalog_template):
    return YamlCatalogRepository(example_catalog_template).snapshot()


@pytest.fixture
def example_repository(example_snapshot):
    return InMemoryCatalogRepository(example_snapshot)
