"""Lockfile read/write/cleanup logic."""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from agpack import __version__
from agpack.display import console

LOCKFILE_NAME = ".agpack.lock.yml"


@dataclass
class InstalledEntry:
    """A single installed copy-kind dependency in the lockfile."""

    url: str
    path: str | None
    resolved_ref: str
    type: str
    """Resource type name as it appears in agpack.yml (``skills`` / ``commands`` / ``agents`` / any user-defined
    name)."""
    deployed_files: list[str] = field(default_factory=list)

    @property
    def identity(self) -> str:
        """Unique key matching DependencySource.identity."""
        key = self.url
        if self.path:
            key = f"{key}::{self.path}"
        return key


@dataclass
class AppliedPatch:
    """An edit-file patch recorded for future cleanup.

    The lockfile never stores resolved ``${var}`` substitutions verbatim: :attr:`key` is the unresolved string the
    user wrote in ``agpack.yml`` (no secrets if a user references ``${API_KEY}``), and :attr:`value_hash` is a
    SHA256 fingerprint of the resolved value (not the value itself).

    Cleanup re-resolves :attr:`key` using the originating target's ``vars`` plus the current env. The originating
    target is looked up by :attr:`target_name`, so even after the resource type is removed from ``dependencies:``
    we can still find ``${bucket}`` and friends.

    * ``replace`` cleanup deletes the leaf — symmetric with how copy kinds clean up the files they wrote. If the
      user had a value at that key before agpack first ran, ``replace`` overwrote it; cleanup does not try to
      magically restore it.
    * ``append`` cleanup walks the list at the path, hashes each element, and removes the first hash-match.
    """

    file_path: str
    target_name: str
    key: str
    strategy: str
    value_hash: str


@dataclass
class EditLockEntry:
    """All patches applied for one resource type across all targets."""

    resource_type: str
    applied: list[AppliedPatch] = field(default_factory=list)


@dataclass
class Lockfile:
    """The full lockfile state."""

    generated_at: str = ""
    agpack_version: str = __version__
    installed: list[InstalledEntry] = field(default_factory=list)
    edits: list[EditLockEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------


_CORRUPT_LOCKFILE_WARNING = (
    "Treating lockfile as missing — agpack has no record of which patches "
    "it previously applied, so cleanup of patches you have since removed "
    "from agpack.yml will not happen automatically. Restore the lockfile "
    "from version control if you have it, or remove leftover agpack-written "
    "entries from your config files by hand."
)


def read_lockfile(project_root: Path) -> Lockfile | None:  # noqa: C901
    """Read the lockfile from disk.

    Returns ``None`` if the file is absent. If the file exists but is unreadable, malformed, or has the wrong top-level
    shape, emits a loud warning explaining what guarantee is now broken (without the lockfile, agpack cannot clean up
    patches the user has since removed from ``agpack.yml``) and returns ``None``.
    """
    path = project_root / LOCKFILE_NAME
    if not path.exists():
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        console.print(
            f"[bold yellow]warning[/bold yellow]: cannot read lockfile {path}: {exc}.\n  {_CORRUPT_LOCKFILE_WARNING}"
        )
        return None

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        console.print(
            f"[bold yellow]warning[/bold yellow]: lockfile {path} is corrupt: {exc}.\n  {_CORRUPT_LOCKFILE_WARNING}"
        )
        return None

    if not isinstance(data, dict):
        console.print(
            f"[bold yellow]warning[/bold yellow]: lockfile {path} has "
            f"unexpected shape (expected a YAML mapping, got "
            f"{type(data).__name__}).\n  {_CORRUPT_LOCKFILE_WARNING}"
        )
        return None

    lockfile = Lockfile(
        generated_at=data.get("generated_at", ""),
        agpack_version=data.get("agpack_version", ""),
    )

    for entry_data in data.get("installed", []):
        if not isinstance(entry_data, dict):
            continue
        lockfile.installed.append(
            InstalledEntry(
                url=entry_data.get("url", ""),
                path=entry_data.get("path"),
                resolved_ref=entry_data.get("resolved_ref", "unknown"),
                type=entry_data.get("type", ""),
                deployed_files=entry_data.get("deployed_files", []),
            )
        )

    for edit_data in data.get("edits", []):
        if not isinstance(edit_data, dict):
            continue
        applied: list[AppliedPatch] = []
        for raw in edit_data.get("applied", []):
            if not isinstance(raw, dict):
                continue
            applied.append(
                AppliedPatch(
                    file_path=raw.get("file_path", ""),
                    target_name=raw.get("target_name", ""),
                    key=raw.get("key", ""),
                    strategy=raw.get("strategy", "replace"),
                    value_hash=raw.get("value_hash", ""),
                )
            )
        lockfile.edits.append(
            EditLockEntry(
                resource_type=edit_data.get("resource_type", ""),
                applied=applied,
            )
        )

    return lockfile


def write_lockfile(project_root: Path, lockfile: Lockfile) -> None:
    """Write the lockfile to disk atomically."""
    lockfile.generated_at = datetime.now(UTC).isoformat()
    lockfile.agpack_version = __version__

    data: dict[str, Any] = {
        "generated_at": lockfile.generated_at,
        "agpack_version": lockfile.agpack_version,
        "installed": [],
        "edits": [],
    }

    for entry in lockfile.installed:
        entry_data: dict[str, Any] = {
            "url": entry.url,
            "resolved_ref": entry.resolved_ref,
            "type": entry.type,
            "deployed_files": entry.deployed_files,
        }
        if entry.path:
            entry_data["path"] = entry.path
        data["installed"].append(entry_data)

    for edit in lockfile.edits:
        data["edits"].append(
            {
                "resource_type": edit.resource_type,
                "applied": [
                    {
                        "file_path": p.file_path,
                        "target_name": p.target_name,
                        "key": p.key,
                        "strategy": p.strategy,
                        "value_hash": p.value_hash,
                    }
                    for p in edit.applied
                ],
            }
        )

    path = project_root / LOCKFILE_NAME
    content = yaml.dump(data, default_flow_style=False, sort_keys=False)

    fd, tmp_path = tempfile.mkstemp(dir=project_root, prefix=".agpack-lock-tmp-")
    tmp = Path(tmp_path)
    try:
        os.close(fd)
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------


def find_removed_dependencies(
    old_lockfile: Lockfile | None,
    current_identities: set[str],
) -> list[InstalledEntry]:
    """Find copy-kind dependencies present in the old lockfile but absent now."""
    if old_lockfile is None:
        return []
    return [entry for entry in old_lockfile.installed if entry.identity not in current_identities]
