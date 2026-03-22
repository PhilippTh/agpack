"""Tests for agpack.lockfile module."""

from __future__ import annotations

from pathlib import Path

import yaml

from agpack import __version__
from agpack.lockfile import LOCKFILE_NAME
from agpack.lockfile import InstalledEntry
from agpack.lockfile import Lockfile
from agpack.lockfile import McpLockEntry
from agpack.lockfile import find_removed_dependencies
from agpack.lockfile import find_removed_mcp_servers
from agpack.lockfile import read_lockfile
from agpack.lockfile import write_lockfile

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lockfile_yaml(data: dict) -> str:
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def _write_raw(tmp_path: Path, content: str) -> Path:
    path = tmp_path / LOCKFILE_NAME
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# read_lockfile
# ---------------------------------------------------------------------------


class TestReadLockfile:
    def test_returns_none_when_no_lockfile_exists(self, tmp_path: Path):
        assert read_lockfile(tmp_path) is None

    def test_parses_valid_lockfile(self, tmp_path: Path):
        data = {
            "generated_at": "2025-01-01T00:00:00+00:00",
            "agpack_version": "0.1.0",
            "installed": [
                {
                    "url": "https://github.com/owner/repo",
                    "resolved_ref": "abc123",
                    "type": "skill",
                    "deployed_files": ["skills/foo.md"],
                    "path": "skills/foo",
                },
                {
                    "url": "https://gitlab.com/owner/other",
                    "resolved_ref": "def456",
                    "type": "command",
                    "deployed_files": [],
                },
            ],
            "mcp": [
                {"name": "my-server", "targets": ["claude"]},
            ],
        }
        _write_raw(tmp_path, _make_lockfile_yaml(data))

        lf = read_lockfile(tmp_path)
        assert lf is not None
        assert lf.generated_at == "2025-01-01T00:00:00+00:00"
        assert lf.agpack_version == "0.1.0"

        assert len(lf.installed) == 2
        first = lf.installed[0]
        assert first.url == "https://github.com/owner/repo"
        assert first.path == "skills/foo"
        assert first.resolved_ref == "abc123"
        assert first.type == "skill"
        assert first.deployed_files == ["skills/foo.md"]

        second = lf.installed[1]
        assert second.url == "https://gitlab.com/owner/other"
        assert second.path is None

        assert len(lf.mcp) == 1
        assert lf.mcp[0].name == "my-server"
        assert lf.mcp[0].targets == ["claude"]

    def test_returns_none_for_corrupt_yaml(self, tmp_path: Path):
        _write_raw(tmp_path, "{{{{not: valid: yaml::::")
        assert read_lockfile(tmp_path) is None

    def test_returns_none_for_non_dict_yaml(self, tmp_path: Path):
        _write_raw(tmp_path, "- just\n- a\n- list\n")
        assert read_lockfile(tmp_path) is None

    def test_handles_empty_file(self, tmp_path: Path):
        _write_raw(tmp_path, "")
        assert read_lockfile(tmp_path) is None

    def test_skips_non_dict_entries_in_installed(self, tmp_path: Path):
        data = {
            "generated_at": "",
            "agpack_version": "",
            "installed": [
                "not-a-dict",
                {
                    "url": "https://github.com/owner/repo",
                    "resolved_ref": "abc",
                    "type": "skill",
                },
            ],
            "mcp": [],
        }
        _write_raw(tmp_path, _make_lockfile_yaml(data))
        lf = read_lockfile(tmp_path)
        assert lf is not None
        assert len(lf.installed) == 1
        assert lf.installed[0].url == "https://github.com/owner/repo"

    def test_skips_non_dict_entries_in_mcp(self, tmp_path: Path):
        data = {
            "generated_at": "",
            "agpack_version": "",
            "installed": [],
            "mcp": [42, {"name": "srv", "targets": []}],
        }
        _write_raw(tmp_path, _make_lockfile_yaml(data))
        lf = read_lockfile(tmp_path)
        assert lf is not None
        assert len(lf.mcp) == 1
        assert lf.mcp[0].name == "srv"


# ---------------------------------------------------------------------------
# write_lockfile
# ---------------------------------------------------------------------------


class TestWriteLockfile:
    def test_creates_new_lockfile(self, tmp_path: Path):
        lf = Lockfile(
            installed=[
                InstalledEntry(
                    url="https://github.com/owner/repo",
                    path=None,
                    resolved_ref="abc123",
                    type="skill",
                ),
            ],
        )
        write_lockfile(tmp_path, lf)

        path = tmp_path / LOCKFILE_NAME
        assert path.exists()
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["installed"][0]["url"] == "https://github.com/owner/repo"

    def test_overwrites_existing_lockfile(self, tmp_path: Path):
        _write_raw(tmp_path, "old: data\n")

        lf = Lockfile(
            installed=[
                InstalledEntry(
                    url="https://github.com/new/repo",
                    path=None,
                    resolved_ref="new123",
                    type="agent",
                ),
            ],
        )
        write_lockfile(tmp_path, lf)

        data = yaml.safe_load((tmp_path / LOCKFILE_NAME).read_text(encoding="utf-8"))
        assert "old" not in data
        assert data["installed"][0]["url"] == "https://github.com/new/repo"

    def test_includes_generated_at_and_version(self, tmp_path: Path):
        lf = Lockfile()
        write_lockfile(tmp_path, lf)

        data = yaml.safe_load((tmp_path / LOCKFILE_NAME).read_text(encoding="utf-8"))
        assert data["agpack_version"] == __version__
        # generated_at should be a non-empty ISO timestamp
        assert data["generated_at"]
        assert "T" in data["generated_at"]

    def test_url_is_preserved_in_serialized_lockfile(self, tmp_path: Path):
        lf = Lockfile(
            installed=[
                InstalledEntry(
                    url="https://github.com/owner/repo-a",
                    path=None,
                    resolved_ref="aaa",
                    type="skill",
                ),
                InstalledEntry(
                    url="https://gitlab.com/owner/repo-b",
                    path=None,
                    resolved_ref="bbb",
                    type="skill",
                ),
            ],
        )
        write_lockfile(tmp_path, lf)

        data = yaml.safe_load((tmp_path / LOCKFILE_NAME).read_text(encoding="utf-8"))
        assert data["installed"][0]["url"] == "https://github.com/owner/repo-a"
        assert data["installed"][1]["url"] == "https://gitlab.com/owner/repo-b"

    def test_omits_path_when_none(self, tmp_path: Path):
        lf = Lockfile(
            installed=[
                InstalledEntry(
                    url="https://github.com/owner/repo",
                    path=None,
                    resolved_ref="aaa",
                    type="skill",
                ),
            ],
        )
        write_lockfile(tmp_path, lf)

        data = yaml.safe_load((tmp_path / LOCKFILE_NAME).read_text(encoding="utf-8"))
        assert "path" not in data["installed"][0]

    def test_includes_path_when_set(self, tmp_path: Path):
        lf = Lockfile(
            installed=[
                InstalledEntry(
                    url="https://github.com/owner/repo",
                    path="subdir/thing",
                    resolved_ref="aaa",
                    type="skill",
                ),
            ],
        )
        write_lockfile(tmp_path, lf)

        data = yaml.safe_load((tmp_path / LOCKFILE_NAME).read_text(encoding="utf-8"))
        assert data["installed"][0]["path"] == "subdir/thing"

    def test_writes_mcp_entries(self, tmp_path: Path):
        lf = Lockfile(
            mcp=[
                McpLockEntry(name="srv-a", targets=["claude", "cursor"]),
                McpLockEntry(name="srv-b", targets=[]),
            ],
        )
        write_lockfile(tmp_path, lf)

        data = yaml.safe_load((tmp_path / LOCKFILE_NAME).read_text(encoding="utf-8"))
        assert len(data["mcp"]) == 2
        assert data["mcp"][0] == {"name": "srv-a", "targets": ["claude", "cursor"]}
        assert data["mcp"][1] == {"name": "srv-b", "targets": []}


# ---------------------------------------------------------------------------
# find_removed_dependencies
# ---------------------------------------------------------------------------


class TestFindRemovedDependencies:
    def test_returns_removed_entries(self):
        old = Lockfile(
            installed=[
                InstalledEntry(
                    url="https://github.com/owner/keep",
                    path=None,
                    resolved_ref="a",
                    type="skill",
                ),
                InstalledEntry(
                    url="https://github.com/owner/remove",
                    path=None,
                    resolved_ref="b",
                    type="skill",
                ),
                InstalledEntry(
                    url="https://github.com/owner/also-remove",
                    path="sub",
                    resolved_ref="c",
                    type="command",
                ),
            ],
        )
        current = {"https://github.com/owner/keep"}
        removed = find_removed_dependencies(old, current)

        assert len(removed) == 2
        identities = {e.identity for e in removed}
        assert "https://github.com/owner/remove" in identities
        assert "https://github.com/owner/also-remove::sub" in identities

    def test_returns_empty_when_nothing_removed(self):
        old = Lockfile(
            installed=[
                InstalledEntry(
                    url="https://github.com/owner/a",
                    path=None,
                    resolved_ref="x",
                    type="skill",
                ),
            ],
        )
        current = {"https://github.com/owner/a"}
        assert find_removed_dependencies(old, current) == []

    def test_returns_empty_when_old_lockfile_is_none(self):
        assert find_removed_dependencies(None, {"https://github.com/owner/a"}) == []

    def test_returns_empty_for_empty_installed(self):
        old = Lockfile(installed=[])
        assert find_removed_dependencies(old, set()) == []


# ---------------------------------------------------------------------------
# find_removed_mcp_servers
# ---------------------------------------------------------------------------


class TestFindRemovedMcpServers:
    def test_returns_removed_servers(self):
        old = Lockfile(
            mcp=[
                McpLockEntry(name="keep-srv", targets=["claude"]),
                McpLockEntry(name="remove-srv", targets=[]),
            ],
        )
        current = {"keep-srv"}
        removed = find_removed_mcp_servers(old, current)

        assert len(removed) == 1
        assert removed[0].name == "remove-srv"

    def test_returns_empty_when_nothing_removed(self):
        old = Lockfile(
            mcp=[McpLockEntry(name="srv", targets=[])],
        )
        assert find_removed_mcp_servers(old, {"srv"}) == []

    def test_returns_empty_when_old_lockfile_is_none(self):
        assert find_removed_mcp_servers(None, {"srv"}) == []

    def test_returns_empty_for_empty_mcp(self):
        old = Lockfile(mcp=[])
        assert find_removed_mcp_servers(old, set()) == []


# ---------------------------------------------------------------------------
# InstalledEntry.identity
# ---------------------------------------------------------------------------


class TestInstalledEntryIdentity:
    def test_identity_without_path(self):
        e = InstalledEntry(
            url="https://github.com/owner/repo",
            path=None,
            resolved_ref="x",
            type="skill",
        )
        assert e.identity == "https://github.com/owner/repo"

    def test_identity_with_path(self):
        e = InstalledEntry(
            url="https://github.com/owner/repo",
            path="sub/dir",
            resolved_ref="x",
            type="skill",
        )
        assert e.identity == "https://github.com/owner/repo::sub/dir"

    def test_identity_with_different_host(self):
        e = InstalledEntry(
            url="https://gitlab.com/owner/repo",
            path=None,
            resolved_ref="x",
            type="skill",
        )
        assert e.identity == "https://gitlab.com/owner/repo"

    def test_identity_with_different_host_and_path(self):
        e = InstalledEntry(
            url="https://gitlab.com/owner/repo",
            path="p",
            resolved_ref="x",
            type="skill",
        )
        assert e.identity == "https://gitlab.com/owner/repo::p"


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_write_then_read_preserves_data(self, tmp_path: Path):
        original = Lockfile(
            installed=[
                InstalledEntry(
                    url="https://github.com/owner/repo-a",
                    path="skills/foo",
                    resolved_ref="abc123",
                    type="skill",
                    deployed_files=["skills/foo.md", "skills/foo/bar.txt"],
                ),
                InstalledEntry(
                    url="https://gitlab.com/owner/repo-b",
                    path=None,
                    resolved_ref="def456",
                    type="command",
                    deployed_files=[],
                ),
            ],
            mcp=[
                McpLockEntry(name="srv-1", targets=["claude", "cursor"]),
                McpLockEntry(name="srv-2", targets=[]),
            ],
        )

        write_lockfile(tmp_path, original)
        restored = read_lockfile(tmp_path)

        assert restored is not None
        assert restored.agpack_version == __version__
        assert restored.generated_at  # non-empty

        assert len(restored.installed) == len(original.installed)
        for orig, rest in zip(original.installed, restored.installed, strict=False):
            assert rest.url == orig.url
            assert rest.path == orig.path
            assert rest.resolved_ref == orig.resolved_ref
            assert rest.type == orig.type
            assert rest.deployed_files == orig.deployed_files
            assert rest.identity == orig.identity

        assert len(restored.mcp) == len(original.mcp)
        for orig, rest in zip(original.mcp, restored.mcp, strict=False):
            assert rest.name == orig.name
            assert rest.targets == orig.targets
