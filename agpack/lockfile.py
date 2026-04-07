"""Lockfile read/write/cleanup logic."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from agpack import __version__
from agpack.config import make_identity
from agpack.fileutil import atomic_write_text

LOCKFILE_NAME = ".agpack.lock.yml"


@dataclass
class InstalledEntry:
    """A single installed dependency in the lockfile."""

    url: str
    path: str | None
    resolved_ref: str
    type: str  # "skill", "command", "agent", "rule", "ignore"
    deployed_files: list[str] = field(default_factory=list)

    @property
    def identity(self) -> str:
        """Unique key matching DependencySource.identity."""
        return make_identity(self.url, self.path)


@dataclass
class McpLockEntry:
    """A single MCP server entry in the lockfile."""

    name: str
    targets: list[str] = field(default_factory=list)


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
        lockfile.installed.append(
            InstalledEntry(
                url=entry_data.get("url", ""),
                path=entry_data.get("path"),
                resolved_ref=entry_data.get("resolved_ref", "unknown"),
                type=entry_data.get("type", ""),
                deployed_files=entry_data.get("deployed_files", []),
            )
        )

    for mcp_data in data.get("mcp", []):
        if not isinstance(mcp_data, dict):
            continue
        lockfile.mcp.append(
            McpLockEntry(
                name=mcp_data.get("name", ""),
                targets=mcp_data.get("targets", []),
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
                "targets": mcp_entry.targets,
            }
        )

    path = project_root / LOCKFILE_NAME
    content = yaml.dump(data, default_flow_style=False, sort_keys=False)
    atomic_write_text(path, content)
