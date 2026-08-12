"""Resolve source-checkout and installed-package data locations."""

from pathlib import Path
import sys


def default_data_directory() -> Path:
    """Return the writable catalog bundled for the current installation."""

    source_data = Path(__file__).resolve().parents[1] / "data"
    if (source_data / "schema.yaml").is_file():
        return source_data
    return Path(sys.prefix) / "share" / "loadout"
