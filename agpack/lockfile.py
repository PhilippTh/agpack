"""Lockfile read/write/cleanup logic."""

from __future__ import annotations

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

LOCKFILE_NAME = ".agpack.lock.yml"


@dataclass
class InstalledEntry:
    """A single installed copy-kind dependency in the lockfile."""

    url: str
    path: str | None
    resolved_ref: str
    type: str
    """Resource type name as it appears in agpack.yml (``skills`` /
    ``commands`` / ``agents`` / any user-defined name)."""
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

    Cleanup looks up the file by :attr:`file_path`, navigates to
    :attr:`key`, and reverses the operation. For ``append`` strategy,
    :attr:`value` is used to locate the entry via deep-equality.
    """

    file_path: str
    key: str
    strategy: str
    value: Any


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


def read_lockfile(project_root: Path) -> Lockfile | None:
    """Read the lockfile from disk. Returns None if absent or unreadable."""
    path = project_root / LOCKFILE_NAME
    if not path.exists():
        return None

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return None

    if not isinstance(data, dict):
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
                    key=raw.get("key", ""),
                    strategy=raw.get("strategy", "replace"),
                    value=raw.get("value"),
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
                        "key": p.key,
                        "strategy": p.strategy,
                        "value": p.value,
                    }
                    for p in edit.applied
                ],
            }
        )

    path = project_root / LOCKFILE_NAME
    content = yaml.dump(data, default_flow_style=False, sort_keys=False)

    fd, tmp_path = tempfile.mkstemp(dir=project_root, prefix=".agpack-lock-tmp-")
    try:
        os.close(fd)
        Path(tmp_path).write_text(content, encoding="utf-8")
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
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
    return [
        entry
        for entry in old_lockfile.installed
        if entry.identity not in current_identities
    ]
