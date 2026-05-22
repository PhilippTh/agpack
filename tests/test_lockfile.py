"""Tests for agpack.lockfile."""

from __future__ import annotations

from pathlib import Path

import yaml

from agpack import __version__
from agpack.lockfile import LOCKFILE_NAME
from agpack.lockfile import AppliedPatch
from agpack.lockfile import EditLockEntry
from agpack.lockfile import InstalledEntry
from agpack.lockfile import Lockfile
from agpack.lockfile import find_removed_dependencies
from agpack.lockfile import read_lockfile
from agpack.lockfile import write_lockfile


def _write_raw(tmp_path: Path, content: str) -> Path:
    p = tmp_path / LOCKFILE_NAME
    p.write_text(content, encoding="utf-8")
    return p


def _make_lockfile_yaml(data: dict) -> str:
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# read_lockfile
# ---------------------------------------------------------------------------


class TestReadLockfile:
    def test_returns_none_when_no_lockfile_exists(self, tmp_path: Path) -> None:
        assert read_lockfile(tmp_path) is None

    def test_parses_valid_lockfile(self, tmp_path: Path) -> None:
        data = {
            "generated_at": "2026-05-22T00:00:00+00:00",
            "agpack_version": __version__,
            "installed": [
                {
                    "url": "https://github.com/owner/repo",
                    "resolved_ref": "abc123",
                    "type": "skills",
                    "deployed_files": ["skills/foo.md"],
                    "path": "skills/foo",
                },
            ],
            "edits": [
                {
                    "resource_type": "mcp",
                    "applied": [
                        {
                            "file_path": ".mcp.json",
                            "key": "mcpServers.fs",
                            "strategy": "replace",
                            "value": {"command": "npx"},
                        },
                    ],
                },
            ],
        }
        _write_raw(tmp_path, _make_lockfile_yaml(data))

        lf = read_lockfile(tmp_path)
        assert lf is not None
        assert lf.installed[0].type == "skills"
        assert lf.installed[0].url == "https://github.com/owner/repo"
        edit = lf.edits[0]
        assert edit.resource_type == "mcp"
        assert len(edit.applied) == 1
        assert edit.applied[0].file_path == ".mcp.json"
        assert edit.applied[0].key == "mcpServers.fs"
        assert edit.applied[0].strategy == "replace"
        assert edit.applied[0].value == {"command": "npx"}

    def test_returns_none_for_corrupt_yaml(self, tmp_path: Path) -> None:
        _write_raw(tmp_path, "{{{{not: valid: yaml::::")
        assert read_lockfile(tmp_path) is None

    def test_returns_none_for_non_dict_yaml(self, tmp_path: Path) -> None:
        _write_raw(tmp_path, "- just\n- a\n- list\n")
        assert read_lockfile(tmp_path) is None

    def test_handles_empty_file(self, tmp_path: Path) -> None:
        _write_raw(tmp_path, "")
        assert read_lockfile(tmp_path) is None

    def test_skips_non_dict_entries_in_installed(self, tmp_path: Path) -> None:
        data = {
            "generated_at": "",
            "agpack_version": "",
            "installed": [
                "not-a-dict",
                {
                    "url": "https://github.com/owner/repo",
                    "resolved_ref": "abc",
                    "type": "skills",
                },
            ],
            "edits": [],
        }
        _write_raw(tmp_path, _make_lockfile_yaml(data))
        lf = read_lockfile(tmp_path)
        assert lf is not None
        assert len(lf.installed) == 1
        assert lf.installed[0].url == "https://github.com/owner/repo"

    def test_skips_non_dict_entries_in_edits(self, tmp_path: Path) -> None:
        data = {
            "generated_at": "",
            "agpack_version": "",
            "installed": [],
            "edits": [42, {"resource_type": "mcp", "applied": []}],
        }
        _write_raw(tmp_path, _make_lockfile_yaml(data))
        lf = read_lockfile(tmp_path)
        assert lf is not None
        assert len(lf.edits) == 1
        assert lf.edits[0].resource_type == "mcp"


# ---------------------------------------------------------------------------
# write_lockfile + round-trip
# ---------------------------------------------------------------------------


class TestWriteLockfile:
    def test_creates_new_lockfile(self, tmp_path: Path) -> None:
        lf = Lockfile(installed=[])
        write_lockfile(tmp_path, lf)
        assert (tmp_path / LOCKFILE_NAME).exists()

    def test_includes_generated_at_and_version(self, tmp_path: Path) -> None:
        lf = Lockfile()
        write_lockfile(tmp_path, lf)
        data = yaml.safe_load((tmp_path / LOCKFILE_NAME).read_text())
        assert data["agpack_version"] == __version__
        assert data["generated_at"]

    def test_omits_path_when_none(self, tmp_path: Path) -> None:
        lf = Lockfile(
            installed=[
                InstalledEntry(
                    url="https://github.com/o/r",
                    path=None,
                    resolved_ref="abc",
                    type="skills",
                ),
            ],
        )
        write_lockfile(tmp_path, lf)
        data = yaml.safe_load((tmp_path / LOCKFILE_NAME).read_text())
        assert "path" not in data["installed"][0]

    def test_writes_edits(self, tmp_path: Path) -> None:
        lf = Lockfile(
            edits=[
                EditLockEntry(
                    resource_type="mcp",
                    applied=[
                        AppliedPatch(
                            file_path=".mcp.json",
                            key="mcpServers.fs",
                            strategy="replace",
                            value={"command": "npx"},
                        ),
                    ],
                ),
            ],
        )
        write_lockfile(tmp_path, lf)
        data = yaml.safe_load((tmp_path / LOCKFILE_NAME).read_text())
        assert data["edits"][0]["resource_type"] == "mcp"
        assert data["edits"][0]["applied"][0]["key"] == "mcpServers.fs"


class TestRoundTrip:
    def test_write_then_read_preserves_data(self, tmp_path: Path) -> None:
        original = Lockfile(
            installed=[
                InstalledEntry(
                    url="https://github.com/o/r-a",
                    path="skills/foo",
                    resolved_ref="abc123",
                    type="skills",
                    deployed_files=["skills/foo.md"],
                ),
            ],
            edits=[
                EditLockEntry(
                    resource_type="settings",
                    applied=[
                        AppliedPatch(
                            file_path=".claude/settings.json",
                            key="hooks.PreToolUse",
                            strategy="append",
                            value={"matcher": "Write", "hooks": [{"type": "command"}]},
                        ),
                    ],
                ),
            ],
        )
        write_lockfile(tmp_path, original)
        restored = read_lockfile(tmp_path)
        assert restored is not None
        assert restored.installed[0].url == original.installed[0].url
        assert restored.installed[0].type == "skills"
        assert restored.edits[0].resource_type == "settings"
        assert restored.edits[0].applied[0].value == original.edits[0].applied[0].value


# ---------------------------------------------------------------------------
# find_removed_dependencies
# ---------------------------------------------------------------------------


class TestFindRemovedDependencies:
    def test_returns_removed_entries(self) -> None:
        old = Lockfile(
            installed=[
                InstalledEntry(
                    url="https://github.com/o/r1",
                    path=None,
                    resolved_ref="x",
                    type="skills",
                ),
                InstalledEntry(
                    url="https://github.com/o/r2",
                    path=None,
                    resolved_ref="x",
                    type="skills",
                ),
            ],
        )
        removed = find_removed_dependencies(old, {"https://github.com/o/r1"})
        assert len(removed) == 1
        assert removed[0].url == "https://github.com/o/r2"

    def test_returns_empty_when_nothing_removed(self) -> None:
        old = Lockfile(
            installed=[
                InstalledEntry(
                    url="https://github.com/o/r",
                    path=None,
                    resolved_ref="x",
                    type="skills",
                ),
            ],
        )
        assert find_removed_dependencies(old, {"https://github.com/o/r"}) == []

    def test_returns_empty_when_old_lockfile_is_none(self) -> None:
        assert find_removed_dependencies(None, {"x"}) == []


# ---------------------------------------------------------------------------
# InstalledEntry.identity
# ---------------------------------------------------------------------------


class TestInstalledEntryIdentity:
    def test_identity_without_path(self) -> None:
        e = InstalledEntry(
            url="https://github.com/o/r", path=None, resolved_ref="x", type="skills"
        )
        assert e.identity == "https://github.com/o/r"

    def test_identity_with_path(self) -> None:
        e = InstalledEntry(
            url="https://github.com/o/r",
            path="sub/dir",
            resolved_ref="x",
            type="skills",
        )
        assert e.identity == "https://github.com/o/r::sub/dir"
