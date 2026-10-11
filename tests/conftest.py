"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def no_focus_requests(tmp_path, monkeypatch):
    """Walk tests must not ask the real Mac to bring Wizard101 forward, nor wait for it."""
    from wiz101_auto import walk

    monkeypatch.setattr(walk, "FOCUS_REQUEST", tmp_path / "focus.request")
    monkeypatch.setattr(walk, "FOCUS_SETTLE", 0.0)
    monkeypatch.setattr(walk, "_focused_once", [True])
    monkeypatch.setattr(walk, "_last_focus", [-1e9])
