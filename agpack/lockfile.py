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

    Cleanup looks up the file by :attr:`file_path`, navigates to :attr:`key`, and reverses the operation. For
    ``append`` strategy, :attr:`value` is used to locate the entry via deep-equality.

    For ``replace`` strategy, :attr:`key_existed` and :attr:`previous_value` capture what was there *before* the patch
    ran. Cleanup restores the previous value if the key existed, or deletes the key if it did not — so agpack never
    silently destroys user data when a patch is removed from ``agpack.yml``.
    """

    file_path: str
    key: str
    strategy: str
    value: Any
    key_existed: bool = False
    previous_value: Any = None


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
    "Treating lockfile as missing — every patch will be re-snapshotted "
    "from the current file contents, so 'previous_value' will reflect "
    "whatever is on disk now (possibly agpack-written content), not "
    "your pre-agpack values. Surgical cleanup of removed patches "
    "may restore agpack content instead of your originals. "
    "Restore the lockfile from version control if you have it."
)


def read_lockfile(project_root: Path) -> Lockfile | None:  # noqa: C901
    """Read the lockfile from disk.

    Returns ``None`` if the file is absent. If the file exists but is unreadable, malformed, or has the wrong top-level
    shape, emits a loud warning explaining what guarantee is now broken (so the user isn't silently dropped into a
    state where ``previous_value`` snapshots are recaptured from agpack-written content) and returns ``None``.
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
                    key=raw.get("key", ""),
                    strategy=raw.get("strategy", "replace"),
                    value=raw.get("value"),
                    key_existed=bool(raw.get("key_existed", False)),
                    previous_value=raw.get("previous_value"),
                )
            )
        lockfile.edits.append(
            EditLockEntry(
                resource_type=edit_data.get("resource_type", ""),
                applied=applied,
            )
        )

    return lockfile


def _serialise_applied(p: AppliedPatch) -> dict[str, Any]:
    """Build the YAML-friendly mapping for one AppliedPatch.

    ``previous_value`` and ``key_existed`` are only emitted for ``replace`` patches; on ``append`` they have no
    semantic meaning and would just clutter the lockfile.
    """
    out: dict[str, Any] = {
        "file_path": p.file_path,
        "key": p.key,
        "strategy": p.strategy,
        "value": p.value,
    }
    if p.strategy == "replace":
        out["key_existed"] = p.key_existed
        if p.key_existed:
            out["previous_value"] = p.previous_value
    return out


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
                "applied": [_serialise_applied(p) for p in edit.applied],
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
