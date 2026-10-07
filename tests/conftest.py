"""Tests import the plugin from the repo root and isolate HERMES_HOME per test."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    # User-facing text follows Hermes' language; tests expect English unless they set another one.
    monkeypatch.setenv("HERMES_LANGUAGE", "en")
    return tmp_path / "hermes"
