"""Tests for the edit-file Patch engine."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest

from agpack.kinds import EditFileError
from agpack.kinds import EditFileResource
from agpack.kinds import Patch
from agpack.kinds import _apply_patch
from agpack.kinds import _atomic_write
from agpack.kinds import _cleanup_patch
from agpack.kinds import infer_config_format

# ---------------------------------------------------------------------------
# infer_config_format
# ---------------------------------------------------------------------------


def test_infer_format_json() -> None:
    assert infer_config_format(".mcp.json") == "json"
    assert infer_config_format("nested/path/foo.JSON") == "json"


def test_infer_format_toml() -> None:
    assert infer_config_format(".codex/config.toml") == "toml"


def test_infer_format_unknown_extension_raises() -> None:
    with pytest.raises(EditFileError, match="cannot infer"):
        infer_config_format(".mcp.yaml")


# ---------------------------------------------------------------------------
# _apply_patch — replace
# ---------------------------------------------------------------------------


def test_apply_replace_at_leaf() -> None:
    root: dict = {}
    _apply_patch(root, Patch(key="a", value=1))
    assert root == {"a": 1}


def test_apply_replace_creates_nested_dicts() -> None:
    root: dict = {}
    _apply_patch(root, Patch(key="a.b.c", value=42))
    assert root == {"a": {"b": {"c": 42}}}


def test_apply_replace_overwrites_existing() -> None:
    root: dict = {"a": {"b": "old"}}
    _apply_patch(root, Patch(key="a.b", value="new"))
    assert root == {"a": {"b": "new"}}


def test_apply_replace_preserves_siblings() -> None:
    root: dict = {"a": {"keep": 1}}
    _apply_patch(root, Patch(key="a.new", value=2))
    assert root == {"a": {"keep": 1, "new": 2}}


def test_apply_replace_through_non_dict_raises() -> None:
    root: dict = {"a": "scalar"}
    with pytest.raises(EditFileError, match="non-dict"):
        _apply_patch(root, Patch(key="a.b", value=1))


# ---------------------------------------------------------------------------
# _apply_patch — append
# ---------------------------------------------------------------------------


def test_apply_append_creates_missing_list() -> None:
    root: dict = {}
    _apply_patch(root, Patch(key="hooks", value="x", strategy="append"))
    assert root == {"hooks": ["x"]}


def test_apply_append_extends_existing_list() -> None:
    root: dict = {"hooks": ["a"]}
    _apply_patch(root, Patch(key="hooks", value="b", strategy="append"))
    assert root == {"hooks": ["a", "b"]}


def test_apply_append_nested_path() -> None:
    root: dict = {}
    _apply_patch(
        root,
        Patch(
            key="hooks.PreToolUse",
            value={"matcher": "Write", "hooks": [{"type": "command"}]},
            strategy="append",
        ),
    )
    assert root["hooks"]["PreToolUse"] == [
        {"matcher": "Write", "hooks": [{"type": "command"}]}
    ]


def test_apply_append_on_non_list_raises() -> None:
    root: dict = {"hooks": "not a list"}
    with pytest.raises(EditFileError, match="non-list"):
        _apply_patch(root, Patch(key="hooks", value="x", strategy="append"))


# ---------------------------------------------------------------------------
# _cleanup_patch — replace
# ---------------------------------------------------------------------------


def test_cleanup_replace_deletes_leaf() -> None:
    root: dict = {"a": {"b": 1, "c": 2}}
    changed = _cleanup_patch(root, Patch(key="a.b", value=1))
    assert changed is True
    assert root == {"a": {"c": 2}}


def test_cleanup_replace_missing_is_noop() -> None:
    root: dict = {"a": {}}
    changed = _cleanup_patch(root, Patch(key="a.b", value=1))
    assert changed is False
    assert root == {"a": {}}


def test_cleanup_replace_missing_intermediate_is_noop() -> None:
    root: dict = {}
    changed = _cleanup_patch(root, Patch(key="x.y.z", value=1))
    assert changed is False


# ---------------------------------------------------------------------------
# _cleanup_patch — append
# ---------------------------------------------------------------------------


def test_cleanup_append_removes_first_match() -> None:
    root: dict = {"hooks": ["a", "b", "c"]}
    changed = _cleanup_patch(root, Patch(key="hooks", value="b", strategy="append"))
    assert changed is True
    assert root == {"hooks": ["a", "c"]}


def test_cleanup_append_deep_equality() -> None:
    root: dict = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Read", "hooks": []},
                {"matcher": "Write", "hooks": [{"type": "command", "command": "x"}]},
            ]
        }
    }
    changed = _cleanup_patch(
        root,
        Patch(
            key="hooks.PreToolUse",
            value={"matcher": "Write", "hooks": [{"type": "command", "command": "x"}]},
            strategy="append",
        ),
    )
    assert changed is True
    assert root["hooks"]["PreToolUse"] == [{"matcher": "Read", "hooks": []}]


def test_cleanup_append_no_match_is_noop() -> None:
    root: dict = {"hooks": ["a"]}
    changed = _cleanup_patch(root, Patch(key="hooks", value="z", strategy="append"))
    assert changed is False
    assert root == {"hooks": ["a"]}


# ---------------------------------------------------------------------------
# EditFileResource.apply_patches — JSON file end-to-end
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


class TestApplyPatchesJson:
    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        resource = EditFileResource(path=".mcp.json")
        resource.apply_patches(
            [
                Patch(
                    key="mcpServers.fs",
                    value={"command": "npx", "args": ["-y", "fs"]},
                )
            ],
            tmp_path,
        )
        cfg = _read_json(tmp_path / ".mcp.json")
        assert cfg == {"mcpServers": {"fs": {"command": "npx", "args": ["-y", "fs"]}}}

    def test_preserves_existing_unrelated_keys(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"$schema": "x", "mcpServers": {"old": {"command": "old"}}}),
            encoding="utf-8",
        )
        resource = EditFileResource(path=".mcp.json")
        resource.apply_patches(
            [Patch(key="mcpServers.new", value={"command": "new"})],
            tmp_path,
        )
        cfg = _read_json(tmp_path / ".mcp.json")
        assert cfg["$schema"] == "x"
        assert cfg["mcpServers"]["old"]["command"] == "old"
        assert cfg["mcpServers"]["new"]["command"] == "new"

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        resource = EditFileResource(path=".mcp.json")
        resource.apply_patches(
            [Patch(key="mcpServers.fs", value={"command": "npx"})],
            tmp_path,
            dry_run=True,
        )
        assert not (tmp_path / ".mcp.json").exists()

    def test_append_creates_list_and_extends(self, tmp_path: Path) -> None:
        (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
        resource = EditFileResource(path="settings.json")
        resource.apply_patches(
            [
                Patch(
                    key="hooks.PreToolUse",
                    value={"matcher": "Write", "hooks": [{"type": "command"}]},
                    strategy="append",
                ),
                Patch(
                    key="permissions.allow",
                    value="Read(/etc/**)",
                    strategy="append",
                ),
            ],
            tmp_path,
        )
        cfg = _read_json(tmp_path / "settings.json")
        assert cfg["hooks"]["PreToolUse"] == [
            {"matcher": "Write", "hooks": [{"type": "command"}]}
        ]
        assert cfg["permissions"]["allow"] == ["Read(/etc/**)"]


class TestApplyPatchesToml:
    def test_writes_toml_inferred_from_extension(self, tmp_path: Path) -> None:
        resource = EditFileResource(path="config.toml")
        resource.apply_patches(
            [Patch(key="mcp_servers.fs", value={"command": "npx"})],
            tmp_path,
        )
        cfg = _read_toml(tmp_path / "config.toml")
        assert cfg == {"mcp_servers": {"fs": {"command": "npx"}}}


# ---------------------------------------------------------------------------
# EditFileResource.cleanup_patches
# ---------------------------------------------------------------------------


class TestCleanupPatches:
    def test_undoes_replace(self, tmp_path: Path) -> None:
        resource = EditFileResource(path=".mcp.json")
        resource.apply_patches(
            [
                Patch(key="mcpServers.fs", value={"command": "npx"}),
                Patch(key="mcpServers.other", value={"command": "stay"}),
            ],
            tmp_path,
        )
        resource.cleanup_patches(
            [Patch(key="mcpServers.fs", value={"command": "npx"})],
            tmp_path,
        )
        cfg = _read_json(tmp_path / ".mcp.json")
        assert cfg == {"mcpServers": {"other": {"command": "stay"}}}

    def test_undoes_append(self, tmp_path: Path) -> None:
        resource = EditFileResource(path="settings.json")
        resource.apply_patches(
            [
                Patch(
                    key="hooks.PreToolUse",
                    value={"matcher": "Write"},
                    strategy="append",
                ),
                Patch(
                    key="hooks.PreToolUse",
                    value={"matcher": "Read"},
                    strategy="append",
                ),
            ],
            tmp_path,
        )
        resource.cleanup_patches(
            [
                Patch(
                    key="hooks.PreToolUse",
                    value={"matcher": "Write"},
                    strategy="append",
                )
            ],
            tmp_path,
        )
        cfg = _read_json(tmp_path / "settings.json")
        assert cfg == {"hooks": {"PreToolUse": [{"matcher": "Read"}]}}

    def test_cleanup_missing_file_is_noop(self, tmp_path: Path) -> None:
        resource = EditFileResource(path=".mcp.json")
        # File doesn't exist — should not raise.
        resource.cleanup_patches(
            [Patch(key="mcpServers.x", value={})],
            tmp_path,
        )


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrors:
    def test_top_level_must_be_mapping(self, tmp_path: Path) -> None:
        (tmp_path / "settings.json").write_text("[1, 2, 3]", encoding="utf-8")
        resource = EditFileResource(path="settings.json")
        with pytest.raises(EditFileError, match="top-level must be a mapping"):
            resource.apply_patches([Patch(key="x", value=1)], tmp_path)

    def test_corrupt_json_raises_on_apply(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text("not json!", encoding="utf-8")
        resource = EditFileResource(path=".mcp.json")
        with pytest.raises(EditFileError, match="Failed to read"):
            resource.apply_patches([Patch(key="x", value=1)], tmp_path)

    def test_oserror_on_write_wrapped(self, tmp_path: Path) -> None:
        resource = EditFileResource(path=".mcp.json")
        with mock_patch(
            "agpack.kinds._atomic_write", side_effect=OSError("disk full")
        ):
            with pytest.raises(EditFileError, match="Failed to write.*disk full"):
                resource.apply_patches([Patch(key="x", value=1)], tmp_path)


# ---------------------------------------------------------------------------
# _atomic_write failure cleanup
# ---------------------------------------------------------------------------


class TestAtomicWriteFailure:
    def test_cleans_up_temp_file_on_replace_failure(self, tmp_path: Path) -> None:
        target = tmp_path / "output.json"
        with (
            mock_patch("agpack.kinds.os.replace", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            _atomic_write(target, '{"test": true}\n')
        leftover = list(tmp_path.glob(".agpack-edit-*"))
        assert leftover == []
        assert not target.exists()
