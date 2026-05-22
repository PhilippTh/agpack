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
from agpack.kinds import infer_config_format
from agpack.kinds._shared import _atomic_write
from agpack.kinds.edit_file import _apply_patch
from agpack.kinds.edit_file import _cleanup_patch

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
# _apply_patch — dotted-key escaping
# ---------------------------------------------------------------------------


class TestDottedKeyEscape:
    """``\\.`` and ``\\\\`` let users address keys that contain dots."""

    def test_literal_dot_in_segment(self) -> None:
        root: dict = {}
        _apply_patch(root, Patch(key="mcpServers.example\\.com", value="x"))
        assert root == {"mcpServers": {"example.com": "x"}}

    def test_dot_only_in_leaf(self) -> None:
        root: dict = {"mcpServers": {}}
        _apply_patch(root, Patch(key="mcpServers.a\\.b\\.c", value=1))
        assert root == {"mcpServers": {"a.b.c": 1}}

    def test_literal_backslash_in_segment(self) -> None:
        root: dict = {}
        _apply_patch(root, Patch(key="a\\\\b.c", value=1))
        assert root == {"a\\b": {"c": 1}}

    def test_backslash_followed_by_separator(self) -> None:
        """``\\\\.`` is a literal backslash and then a separator."""
        root: dict = {}
        _apply_patch(root, Patch(key="a\\\\.b", value=1))
        assert root == {"a\\": {"b": 1}}

    def test_empty_key_rejected(self) -> None:
        with pytest.raises(EditFileError, match="non-empty"):
            _apply_patch({}, Patch(key="", value=1))

    def test_empty_segment_rejected(self) -> None:
        with pytest.raises(EditFileError, match="empty segment"):
            _apply_patch({}, Patch(key="a..b", value=1))

    def test_trailing_dot_rejected(self) -> None:
        with pytest.raises(EditFileError, match="empty segment"):
            _apply_patch({}, Patch(key="a.", value=1))

    def test_leading_dot_rejected(self) -> None:
        with pytest.raises(EditFileError, match="empty segment"):
            _apply_patch({}, Patch(key=".a", value=1))

    def test_cleanup_understands_escapes(self) -> None:
        """Round-trip: a key applied with an escaped dot can also be undone with the same escaped key."""
        root: dict = {}
        _apply_patch(root, Patch(key="mcpServers.example\\.com", value="x"))
        from agpack.kinds.edit_file import _cleanup_patch as cleanup

        assert cleanup(root, Patch(key="mcpServers.example\\.com", value="x"))
        assert root == {"mcpServers": {}}


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
    assert root["hooks"]["PreToolUse"] == [{"matcher": "Write", "hooks": [{"type": "command"}]}]


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
        assert cfg["hooks"]["PreToolUse"] == [{"matcher": "Write", "hooks": [{"type": "command"}]}]
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
# Safety fixes — previous-value snapshot, format preservation, idempotency
# ---------------------------------------------------------------------------


class TestPreviousValueRestoration:
    """Removing a ``replace`` patch must restore the user's prior value.

    The pre-fix behaviour was to delete the leaf on cleanup, silently destroying any data that was at the key before
    agpack first ran.
    """

    def test_replace_records_pre_existing_value(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"fs": {"command": "user-tool"}}}),
            encoding="utf-8",
        )
        resource = EditFileResource(path=".mcp.json")
        applied = resource.sync_patches(
            applied_old=[],
            desired_new=[Patch(key="mcpServers.fs", value={"command": "agpack"})],
            project_root=tmp_path,
        )
        assert applied[0].key_existed is True
        assert applied[0].previous_value == {"command": "user-tool"}

    def test_replace_records_absent_key(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text("{}", encoding="utf-8")
        resource = EditFileResource(path=".mcp.json")
        applied = resource.sync_patches(
            applied_old=[],
            desired_new=[Patch(key="mcpServers.new", value={"command": "x"})],
            project_root=tmp_path,
        )
        assert applied[0].key_existed is False
        assert applied[0].previous_value is None

    def test_cleanup_restores_user_value(self, tmp_path: Path) -> None:
        """The data-loss kill-shot: user data must survive patch removal."""
        original = {"mcpServers": {"fs": {"command": "user-tool", "args": ["secret"]}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(original), encoding="utf-8")
        resource = EditFileResource(path=".mcp.json")
        applied = resource.sync_patches(
            applied_old=[],
            desired_new=[Patch(key="mcpServers.fs", value={"command": "agpack"})],
            project_root=tmp_path,
        )
        # User removed the patch from their config — sync with empty desired.
        resource.sync_patches(
            applied_old=applied,
            desired_new=[],
            project_root=tmp_path,
        )
        restored = json.loads((tmp_path / ".mcp.json").read_text())
        assert restored == original

    def test_cleanup_deletes_agpack_created_key(self, tmp_path: Path) -> None:
        """When agpack created the key (no prior value), cleanup deletes it."""
        (tmp_path / ".mcp.json").write_text("{}", encoding="utf-8")
        resource = EditFileResource(path=".mcp.json")
        applied = resource.sync_patches(
            applied_old=[],
            desired_new=[Patch(key="mcpServers.new", value={"command": "x"})],
            project_root=tmp_path,
        )
        resource.sync_patches(
            applied_old=applied,
            desired_new=[],
            project_root=tmp_path,
        )
        cfg = json.loads((tmp_path / ".mcp.json").read_text())
        assert "new" not in cfg.get("mcpServers", {})

    def test_value_change_preserves_original_previous_value(self, tmp_path: Path) -> None:
        """A patch whose value updates must keep the original previous_value so a future removal still restores the
        user's pre-agpack content."""
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"fs": {"command": "user"}}}),
            encoding="utf-8",
        )
        resource = EditFileResource(path=".mcp.json")
        first = resource.sync_patches(
            applied_old=[],
            desired_new=[Patch(key="mcpServers.fs", value={"command": "v1"})],
            project_root=tmp_path,
        )
        # User edits agpack.yml — patch value changes.
        second = resource.sync_patches(
            applied_old=first,
            desired_new=[Patch(key="mcpServers.fs", value={"command": "v2"})],
            project_root=tmp_path,
        )
        # The original user value is still what cleanup will restore.
        assert second[0].previous_value == {"command": "user"}
        # And the file now has v2.
        cfg = json.loads((tmp_path / ".mcp.json").read_text())
        assert cfg["mcpServers"]["fs"] == {"command": "v2"}


class TestTomlPreservation:
    """tomlkit retains comments and ordering on untouched sections."""

    def test_comments_survive_round_trip(self, tmp_path: Path) -> None:
        original = (
            "# leading comment\n"
            "[mcp_servers]\n"
            "# inline comment\n"
            'existing = { command = "old" }\n'
            "\n"
            "[other]\n"
            "# unrelated\n"
            'key = "value"\n'
        )
        (tmp_path / "config.toml").write_text(original, encoding="utf-8")
        resource = EditFileResource(path="config.toml")
        resource.sync_patches(
            applied_old=[],
            desired_new=[Patch(key="mcp_servers.new", value={"command": "x"})],
            project_root=tmp_path,
        )
        after = (tmp_path / "config.toml").read_text()
        assert "# leading comment" in after
        assert "# inline comment" in after
        assert "# unrelated" in after
        assert "existing" in after


class TestIdempotency:
    """No-op syncs must not rewrite the file."""

    def test_resync_identical_state_does_not_touch_file(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text("{}", encoding="utf-8")
        resource = EditFileResource(path=".mcp.json")
        applied = resource.sync_patches(
            applied_old=[],
            desired_new=[Patch(key="mcpServers.fs", value={"command": "x"})],
            project_root=tmp_path,
        )
        path = tmp_path / ".mcp.json"
        mtime_before = path.stat().st_mtime_ns
        # Re-running with the same desired state must produce no write.
        with mock_patch(
            "agpack.kinds._shared._atomic_write",
            side_effect=AssertionError("file was rewritten despite no change"),
        ):
            resource.sync_patches(
                applied_old=applied,
                desired_new=[Patch(key="mcpServers.fs", value={"command": "x"})],
                project_root=tmp_path,
            )
        assert path.stat().st_mtime_ns == mtime_before

    def test_empty_sync_does_not_create_file(self, tmp_path: Path) -> None:
        resource = EditFileResource(path=".mcp.json")
        resource.sync_patches(
            applied_old=[],
            desired_new=[],
            project_root=tmp_path,
        )
        assert not (tmp_path / ".mcp.json").exists()


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
        with (
            mock_patch("agpack.kinds._shared._atomic_write", side_effect=OSError("disk full")),
            pytest.raises(EditFileError, match="Failed to write.*disk full"),
        ):
            resource.apply_patches([Patch(key="x", value=1)], tmp_path)


# ---------------------------------------------------------------------------
# _atomic_write failure cleanup
# ---------------------------------------------------------------------------


class TestVariableSubstitution:
    """${name} in patch keys and values resolves at apply time, with the target's own ``vars`` taking precedence over
    env_vars."""

    def test_target_var_substituted_in_key(self, tmp_path: Path) -> None:
        resource = EditFileResource(path=".mcp.json", vars={"bucket": "mcpServers"})
        resource.apply_patches(
            [Patch(key="${bucket}.fs", value={"command": "npx"})],
            tmp_path,
            env_vars={},
        )
        cfg = _read_json(tmp_path / ".mcp.json")
        assert cfg == {"mcpServers": {"fs": {"command": "npx"}}}

    def test_env_var_substituted_in_value(self, tmp_path: Path) -> None:
        resource = EditFileResource(path=".mcp.json")
        resource.apply_patches(
            [
                Patch(
                    key="mcpServers.fs",
                    value={"env": {"API_KEY": "${API_KEY}"}},
                )
            ],
            tmp_path,
            env_vars={"API_KEY": "secret"},
        )
        cfg = _read_json(tmp_path / ".mcp.json")
        assert cfg["mcpServers"]["fs"]["env"]["API_KEY"] == "secret"

    def test_target_var_overrides_env_var(self, tmp_path: Path) -> None:
        """Same-name collision: target wins."""
        resource = EditFileResource(path=".mcp.json", vars={"bucket": "from-target"})
        resource.apply_patches(
            [Patch(key="${bucket}.fs", value={"command": "x"})],
            tmp_path,
            env_vars={"bucket": "from-env"},
        )
        cfg = _read_json(tmp_path / ".mcp.json")
        assert "from-target" in cfg
        assert "from-env" not in cfg

    def test_substitutes_recursively_in_value(self, tmp_path: Path) -> None:
        resource = EditFileResource(path=".mcp.json", vars={"bucket": "mcpServers"})
        resource.apply_patches(
            [
                Patch(
                    key="${bucket}.fs",
                    value={
                        "command": "node",
                        "args": ["--port", "${PORT}"],
                        "env": {"TOKEN": "${TOKEN}"},
                    },
                )
            ],
            tmp_path,
            env_vars={"PORT": "9090", "TOKEN": "abc"},
        )
        cfg = _read_json(tmp_path / ".mcp.json")
        srv = cfg["mcpServers"]["fs"]
        assert srv["args"] == ["--port", "9090"]
        assert srv["env"]["TOKEN"] == "abc"

    def test_missing_var_raises(self, tmp_path: Path) -> None:
        resource = EditFileResource(path=".mcp.json")
        with pytest.raises(EditFileError, match="'UNDEFINED' is not defined"):
            resource.apply_patches(
                [Patch(key="${UNDEFINED}.x", value=1)],
                tmp_path,
                env_vars={},
            )

    def test_no_env_vars_argument_defaults_to_empty(self, tmp_path: Path) -> None:
        """env_vars=None is equivalent to {}; target vars still resolve."""
        resource = EditFileResource(path=".mcp.json", vars={"bucket": "x"})
        resource.apply_patches(
            [Patch(key="${bucket}.fs", value={})],
            tmp_path,
        )
        cfg = _read_json(tmp_path / ".mcp.json")
        assert cfg == {"x": {"fs": {}}}

    def test_dollar_dollar_escapes_to_literal_dollar(self, tmp_path: Path) -> None:
        """$${X} writes ${X} literally — needed for runtime vars (e.g. Claude Code's ${CLAUDE_PROJECT_DIR} inside hook
        commands)."""
        resource = EditFileResource(path="settings.json")
        resource.apply_patches(
            [
                Patch(
                    key="hooks.PreToolUse",
                    strategy="append",
                    value={
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "$${CLAUDE_PROJECT_DIR}/.claude/block.sh",
                            }
                        ],
                    },
                ),
            ],
            tmp_path,
            env_vars={},
        )
        cfg = _read_json(tmp_path / "settings.json")
        cmd = cfg["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert cmd == "${CLAUDE_PROJECT_DIR}/.claude/block.sh"

    def test_escape_alongside_substitution(self, tmp_path: Path) -> None:
        """$$ and ${} can appear in the same string."""
        resource = EditFileResource(path=".mcp.json", vars={"bucket": "mcpServers"})
        resource.apply_patches(
            [
                Patch(
                    key="${bucket}.fs",
                    value="literal $${X} and substituted ${SUB}",
                ),
            ],
            tmp_path,
            env_vars={"SUB": "OK"},
        )
        cfg = _read_json(tmp_path / ".mcp.json")
        assert cfg["mcpServers"]["fs"] == "literal ${X} and substituted OK"

    def test_resolved_patches_returned(self, tmp_path: Path) -> None:
        """Return value carries the post-substitution keys/values for the lockfile to record."""
        resource = EditFileResource(path=".mcp.json", vars={"bucket": "mcpServers"})
        resolved = resource.apply_patches(
            [
                Patch(key="${bucket}.fs", value={"env": {"K": "${K}"}}),
            ],
            tmp_path,
            env_vars={"K": "v"},
        )
        assert len(resolved) == 1
        assert resolved[0].key == "mcpServers.fs"
        assert resolved[0].value == {"env": {"K": "v"}}


class TestAtomicWriteFailure:
    def test_cleans_up_temp_file_on_replace_failure(self, tmp_path: Path) -> None:
        target = tmp_path / "output.json"
        with (
            mock_patch(
                "agpack.kinds._shared.os.replace",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(OSError, match="disk full"),
        ):
            _atomic_write(target, '{"test": true}\n')
        leftover = list(tmp_path.glob(".agpack-edit-*"))
        assert leftover == []
        assert not target.exists()
