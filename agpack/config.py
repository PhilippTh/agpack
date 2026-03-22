"""agpack.yml parsing and validation."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml

from agpack.targets import VALID_TARGETS

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DependencySource:
    """A parsed skill, command, or agent dependency."""

    url: str
    path: str | None = None
    ref: str | None = None

    @property
    def name(self) -> str:
        """Derive the resource name (last path segment, or url basename)."""
        if self.path:
            return self.path.rstrip("/").rsplit("/", 1)[-1]
        # Strip trailing .git and take the last segment of the URL
        cleaned = self.url.rstrip("/")
        if cleaned.endswith(".git"):
            cleaned = cleaned[:-4]
        return cleaned.rsplit("/", 1)[-1]

    @property
    def identity(self) -> str:
        """A unique key for this dependency (used for lockfile matching)."""
        key = self.url
        if self.path:
            key = f"{key}::{self.path}"
        return key


@dataclass
class McpServer:
    """An MCP server definition."""

    name: str
    type: str = "stdio"  # stdio | sse | http
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None


@dataclass
class AgpackConfig:
    """Parsed and validated agpack.yml."""

    name: str
    version: str
    targets: list[str]
    skills: list[DependencySource] = field(default_factory=list)
    commands: list[DependencySource] = field(default_factory=list)
    agents: list[DependencySource] = field(default_factory=list)
    mcp: list[McpServer] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when agpack.yml is invalid."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_dependency(raw: dict[str, Any], context: str) -> DependencySource:
    """Parse a single dependency entry (object form).

    Args:
        raw: The raw YAML dict for this dependency.
        context: Human-readable location for error messages (e.g. "skills[0]").
    """
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{context}: expected an object with 'url' key, got {type(raw).__name__}"
        )

    url = raw.get("url")
    if not url:
        raise ConfigError(f"{context}: missing required field 'url'")
    if not isinstance(url, str):
        raise ConfigError(f"{context}: 'url' must be a string")

    path = raw.get("path")
    if path is not None and not isinstance(path, str):
        raise ConfigError(f"{context}: 'path' must be a string")

    ref = raw.get("ref")
    if ref is not None:
        ref = str(ref)

    return DependencySource(url=url, path=path, ref=ref)


def _parse_mcp(raw: dict[str, Any], context: str) -> McpServer:
    """Parse a single MCP server entry."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{context}: expected an object, got {type(raw).__name__}")

    name = raw.get("name")
    if not name:
        raise ConfigError(f"{context}: missing required field 'name'")

    server_type = str(raw.get("type", "stdio"))
    if server_type not in ("stdio", "sse", "http"):
        raise ConfigError(
            f"{context}: 'type' must be 'stdio', 'sse', or 'http', got '{server_type}'"
        )

    if server_type == "stdio":
        command = raw.get("command")
        if not command:
            raise ConfigError(
                f"{context}: stdio MCP server '{name}'"
                " is missing required field 'command'"
            )
        return McpServer(
            name=name,
            type=server_type,
            command=str(command),
            args=[str(a) for a in raw.get("args", [])],
            env={str(k): str(v) for k, v in raw.get("env", {}).items()},
        )
    else:
        url = raw.get("url")
        if not url:
            raise ConfigError(
                f"{context}: {server_type} MCP server '{name}'"
                " is missing required field 'url'"
            )
        return McpServer(
            name=name,
            type=server_type,
            url=str(url),
        )


def load_config(path: Path) -> AgpackConfig:
    """Load and validate agpack.yml.

    Args:
        path: Path to the agpack.yml file.

    Returns:
        A validated AgpackConfig.

    Raises:
        ConfigError: If the config is invalid.
    """
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError("Config file must be a YAML mapping")

    # Required top-level fields
    name = data.get("name")
    if not name:
        raise ConfigError("Missing required field 'name'")

    version = data.get("version")
    if not version:
        raise ConfigError("Missing required field 'version'")
    version = str(version)

    # Targets
    targets = data.get("targets")
    if not targets or not isinstance(targets, list):
        raise ConfigError("Missing or invalid 'targets' (must be a list)")

    for t in targets:
        if t not in VALID_TARGETS:
            raise ConfigError(
                f"Unrecognised target '{t}'. Valid targets: {sorted(VALID_TARGETS)}"
            )

    # Dependencies
    deps = data.get("dependencies", {})
    if not isinstance(deps, dict):
        raise ConfigError("'dependencies' must be a mapping")

    skills = [
        _parse_dependency(s, f"dependencies.skills[{i}]")
        for i, s in enumerate(deps.get("skills") or [])
    ]
    commands = [
        _parse_dependency(c, f"dependencies.commands[{i}]")
        for i, c in enumerate(deps.get("commands") or [])
    ]
    agents = [
        _parse_dependency(a, f"dependencies.agents[{i}]")
        for i, a in enumerate(deps.get("agents") or [])
    ]
    mcp = [
        _parse_mcp(m, f"dependencies.mcp[{i}]")
        for i, m in enumerate(deps.get("mcp") or [])
    ]

    return AgpackConfig(
        name=str(name),
        version=version,
        targets=targets,
        skills=skills,
        commands=commands,
        agents=agents,
        mcp=mcp,
    )
