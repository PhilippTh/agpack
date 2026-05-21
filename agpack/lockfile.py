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

# Lockfiles written by agpack ≤ 0.4.0 stored a singular resource type
# label on each installed entry ("skill"/"command"/"agent").  From the
# resource-taxonomy refactor onward, agpack stores the resource type
# name verbatim from agpack.yml ("skills"/"commands"/"agents" — or any
# user-defined name).  The legacy values are remapped on read so an
# old lockfile still cleans up correctly.
_LEGACY_TYPE_REMAP = {"skill": "skills", "command": "commands", "agent": "agents"}


@dataclass
class InstalledEntry:
    """A single installed dependency in the lockfile."""

    url: str
    path: str | None
    resolved_ref: str
    type: str
    """Resource type name as it appears in agpack.yml (``skills`` /
    ``commands`` / ``agents`` / any user-defined name). Legacy
    singular values (``skill`` / ``command`` / ``agent``) are remapped
    on read for back-compat — see :data:`_LEGACY_TYPE_REMAP`."""
    deployed_files: list[str] = field(default_factory=list)

    @property
    def identity(self) -> str:
        """Unique key matching DependencySource.identity."""
        key = self.url
        if self.path:
            key = f"{key}::{self.path}"
        return key


@dataclass
class McpTargetRef:
    """A single MCP config file an MCP server was written to.

    Carries enough metadata to remove the server later without
    consulting the target manifest — important because the target may
    have been deleted from agpack.yml between syncs. The config format
    is inferred from :attr:`path`'s extension at cleanup time.
    """

    path: str
    servers_key: str


@dataclass
class McpLockEntry:
    """A single MCP server entry in the lockfile."""

    name: str
    targets: list[McpTargetRef] = field(default_factory=list)


@dataclass
class Lockfile:
    """The full lockfile state."""

    generated_at: str = ""
    agpack_version: str = __version__
    installed: list[InstalledEntry] = field(default_factory=list)
    mcp: list[McpLockEntry] = field(default_factory=list)


def read_lockfile(project_root: Path) -> Lockfile | None:
    """Read the lockfile from disk.

    Returns None if the lockfile doesn't exist.
    """
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
        type_: str = entry_data.get("type") or ""
        type_ = _LEGACY_TYPE_REMAP.get(type_, type_)
        lockfile.installed.append(
            InstalledEntry(
                url=entry_data.get("url", ""),
                path=entry_data.get("path"),
                resolved_ref=entry_data.get("resolved_ref", "unknown"),
                type=type_,
                deployed_files=entry_data.get("deployed_files", []),
            )
        )

    for mcp_data in data.get("mcp", []):
        if not isinstance(mcp_data, dict):
            continue
        targets: list[McpTargetRef] = []
        for raw_t in mcp_data.get("targets", []):
            if isinstance(raw_t, dict):
                # Legacy "format" field is silently dropped — the
                # format is now inferred from the file extension.
                targets.append(
                    McpTargetRef(
                        path=raw_t.get("path", ""),
                        servers_key=raw_t.get("servers_key", ""),
                    )
                )
            elif isinstance(raw_t, str):
                # Pre-0.4.0 lockfile: only the path was stored. Cleanup
                # for such entries is best-effort; without servers_key
                # we skip them, and the next sync writes the new format.
                targets.append(McpTargetRef(path=raw_t, servers_key=""))
        lockfile.mcp.append(
            McpLockEntry(name=mcp_data.get("name", ""), targets=targets)
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
        "mcp": [],
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

    for mcp_entry in lockfile.mcp:
        data["mcp"].append(
            {
                "name": mcp_entry.name,
                "targets": [
                    {"path": t.path, "servers_key": t.servers_key}
                    for t in mcp_entry.targets
                ],
            }
        )

    path = project_root / LOCKFILE_NAME
    content = yaml.dump(data, default_flow_style=False, sort_keys=False)

    # Atomic write
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


def find_removed_dependencies(
    old_lockfile: Lockfile | None,
    current_identities: set[str],
) -> list[InstalledEntry]:
    """Find dependencies that were in the old lockfile but are no longer configured."""
    if old_lockfile is None:
        return []

    return [
        entry
        for entry in old_lockfile.installed
        if entry.identity not in current_identities
    ]


def find_removed_mcp_servers(
    old_lockfile: Lockfile | None,
    current_mcp_names: set[str],
) -> list[McpLockEntry]:
    """Find MCP servers that were in the old lockfile but are no longer configured."""
    if old_lockfile is None:
        return []

    return [entry for entry in old_lockfile.mcp if entry.name not in current_mcp_names]
