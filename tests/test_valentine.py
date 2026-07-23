"""Unit tests for src/zentinull/valentine.py."""

from __future__ import annotations

from zentinull.valentine import MANUAL_REGISTRY, _flatten, run_valentine


def test_flatten_nested_dict():
    nested = {
        "a": "1",
        "b": {"c": "2", "d": {"e": "3"}},
        "list_val": [1, 2, 3],
        "empty": None,
    }
    flattened = _flatten(nested)
    assert flattened["a"] == "1"
    assert flattened["b.c"] == "2"
    assert flattened["b.d.e"] == "3"
    assert "list_val" in flattened
    assert "empty" not in flattened


def test_run_valentine_fallback(tmp_path, monkeypatch):
    """Verify run_valentine returns manual registry when sources are missing."""
    reg = run_valentine()
    assert isinstance(reg, dict)
    assert "purchase_cost" in reg
    assert reg["purchase_cost"] == MANUAL_REGISTRY["purchase_cost"]
