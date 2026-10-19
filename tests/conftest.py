"""Tests import the plugin from the repo root and isolate HERMES_HOME per test."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    return tmp_path / "hermes"


@pytest.fixture(autouse=True)
def fresh_measurements(monkeypatch):
    # Measurements are cached for a few seconds: no test sees another one's.
    from orquehelx import quota
    monkeypatch.setattr(quota, "_measured", {})
