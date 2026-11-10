"""Tests marked `household` start from the invented household setup in
fixtures/household_setup.json: a fresh test database applies it automatically (FINAPP_SETUP),
the same path a real data/personal_setup.json takes. Every other test starts from the generic
defaults only, whatever setup file the machine running the tests has."""

from pathlib import Path

import pytest

HOUSEHOLD_SETUP = Path(__file__).parent / "fixtures" / "household_setup.json"


@pytest.fixture(autouse=True)
def _household_setup(request, monkeypatch):
    if request.node.get_closest_marker("household"):
        monkeypatch.setenv("FINAPP_SETUP", str(HOUSEHOLD_SETUP))
    else:
        monkeypatch.setenv("FINAPP_SETUP", str(HOUSEHOLD_SETUP.parent / "no_setup.json"))
