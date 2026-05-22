"""Tests for the built-in target registry."""

from __future__ import annotations

import pytest

from agpack.registry import list_builtins
from agpack.registry import load_all_builtins
from agpack.registry import load_builtin
from agpack.target_schema import TargetSchemaError

EXPECTED_BUILTINS = {
    "antigravity",
    "claude",
    "codex",
    "copilot",
    "cursor",
    "gemini",
    "opencode",
    "windsurf",
}


def test_list_builtins_returns_all_shipped_targets() -> None:
    assert set(list_builtins()) == EXPECTED_BUILTINS


def test_list_builtins_is_sorted() -> None:
    names = list_builtins()
    assert names == sorted(names)


@pytest.mark.parametrize("name", sorted(EXPECTED_BUILTINS))
def test_each_builtin_loads(name: str) -> None:
    # Just verify the manifest parses successfully.
    load_builtin(name)


def test_load_builtin_unknown_raises() -> None:
    with pytest.raises(TargetSchemaError, match="No built-in target"):
        load_builtin("nonexistent")


def test_load_all_builtins_returns_full_map() -> None:
    assert set(load_all_builtins()) == EXPECTED_BUILTINS
