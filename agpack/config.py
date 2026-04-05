"""agpack.yml parsing and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml

from agpack.targets import VALID_TARGETS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_GLOBAL_CONFIG_DIR = Path.home() / ".config" / "agpack"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DependencySource:
    """A parsed skill, command, or agent dependency.

    The ``urls`` list contains one or more git clone URLs.  The first
    entry is the canonical (primary) URL used for identity and display.
    Remaining entries are fallback URLs tried in order when earlier ones
    fail.
    """

    urls: list[str]
    path: str | None = None
    ref: str | None = None

    @property
    def url(self) -> str:
        """The primary (first) URL."""
        return self.urls[0]

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

    targets: list[str]
    skills: list[DependencySource] = field(default_factory=list)
    commands: list[DependencySource] = field(default_factory=list)
    agents: list[DependencySource] = field(default_factory=list)
    mcp: list[McpServer] = field(default_factory=list)
    use_global: bool = True


@dataclass
class GlobalConfig:
    """Parsed global config (~/.config/agpack/agpack.yml).

    Contains only dependencies — no targets.
    """

    skills: list[DependencySource] = field(default_factory=list)
    commands: list[DependencySource] = field(default_factory=list)
    agents: list[DependencySource] = field(default_factory=list)
    mcp: list[McpServer] = field(default_factory=list)
    config_dir: Path = field(default_factory=lambda: DEFAULT_GLOBAL_CONFIG_DIR)
    """Directory containing the global config (used to locate .env)."""


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

    raw_url = raw.get("url")
    if raw_url is None:
        raise ConfigError(f"{context}: missing required field 'url'")

    if isinstance(raw_url, str):
        if not raw_url:
            raise ConfigError(f"{context}: 'url' must not be empty")
        urls = [raw_url]
    elif isinstance(raw_url, list):
        if not raw_url:
            raise ConfigError(f"{context}: 'url' must not be empty")
        urls = [str(u) for u in raw_url]
    else:
        raise ConfigError(f"{context}: 'url' must be a string or list of strings")

    path = raw.get("path")
    if path is not None and not isinstance(path, str):
        raise ConfigError(f"{context}: 'path' must be a string")

    ref = raw.get("ref")
    if ref is not None:
        ref = str(ref)

    return DependencySource(urls=urls, path=path, ref=ref)


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


def _parse_dependencies(
    deps: dict[str, Any], prefix: str = ""
) -> tuple[
    list[DependencySource],
    list[DependencySource],
    list[DependencySource],
    list[McpServer],
]:
    """Parse all dependency lists from a ``dependencies`` mapping.

    Args:
        deps: The raw ``dependencies`` dict from YAML.
        prefix: Optional prefix for error context (e.g. ``"global "``).

    Returns:
        A tuple of (skills, commands, agents, mcp).
    """
    skills = [
        _parse_dependency(s, f"{prefix}dependencies.skills[{i}]")
        for i, s in enumerate(deps.get("skills") or [])
    ]
    commands = [
        _parse_dependency(c, f"{prefix}dependencies.commands[{i}]")
        for i, c in enumerate(deps.get("commands") or [])
    ]
    agents = [
        _parse_dependency(a, f"{prefix}dependencies.agents[{i}]")
        for i, a in enumerate(deps.get("agents") or [])
    ]
    mcp = [
        _parse_mcp(m, f"{prefix}dependencies.mcp[{i}]")
        for i, m in enumerate(deps.get("mcp") or [])
    ]
    return skills, commands, agents, mcp


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

    # Targets
    targets = data.get("targets")
    if not targets or not isinstance(targets, list):
        raise ConfigError("Missing or invalid 'targets' (must be a list)")

    for t in targets:
        if t not in VALID_TARGETS:
            raise ConfigError(
                f"Unrecognised target '{t}'. Valid targets: {sorted(VALID_TARGETS)}"
            )

    # Global config opt-out
    use_global = data.get("global", True)
    if not isinstance(use_global, bool):
        raise ConfigError("'global' must be true or false")

    # Dependencies
    deps = data.get("dependencies", {})
    if not isinstance(deps, dict):
        raise ConfigError("'dependencies' must be a mapping")

    skills, commands, agents, mcp = _parse_dependencies(deps)

    return AgpackConfig(
        targets=targets,
        skills=skills,
        commands=commands,
        agents=agents,
        mcp=mcp,
        use_global=use_global,
    )


def _resolve_global_config_path() -> Path:
    """Return the global config file path.

    Respects the ``AGPACK_GLOBAL_CONFIG`` environment variable.
    Falls back to ``~/.config/agpack/agpack.yml``.
    """
    override = os.environ.get("AGPACK_GLOBAL_CONFIG")
    if override:
        return Path(override).resolve()
    return DEFAULT_GLOBAL_CONFIG_DIR / "agpack.yml"


def load_global_config(path: Path | None = None) -> GlobalConfig | None:
    """Load the global agpack config.

    Args:
        path: Explicit path to the global config file.
              If *None*, the path is resolved via ``AGPACK_GLOBAL_CONFIG``
              or the default ``~/.config/agpack/agpack.yml``.

    Returns:
        A :class:`GlobalConfig` if the file exists and is valid,
        or *None* if the file does not exist.

    Raises:
        ConfigError: If the file exists but is malformed.
    """
    if path is None:
        path = _resolve_global_config_path()

    if not path.exists():
        return None

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse global config YAML: {exc}") from exc

    # An empty file yields None from safe_load
    if data is None:
        return GlobalConfig(config_dir=path.parent)

    if not isinstance(data, dict):
        raise ConfigError("Global config file must be a YAML mapping")

    deps = data.get("dependencies", {})
    if not isinstance(deps, dict):
        raise ConfigError("Global config 'dependencies' must be a mapping")

    skills, commands, agents, mcp = _parse_dependencies(deps, prefix="global ")

    return GlobalConfig(
        skills=skills,
        commands=commands,
        agents=agents,
        mcp=mcp,
        config_dir=path.parent,
    )


def merge_configs(project: AgpackConfig, global_cfg: GlobalConfig) -> AgpackConfig:
    """Merge a global config into a project config.

    Global dependencies are appended after project dependencies.
    Duplicates are resolved in favour of the project config:

    - Dependencies are deduplicated by :attr:`DependencySource.identity`.
    - MCP servers are deduplicated by :attr:`McpServer.name`.

    Returns a **new** :class:`AgpackConfig`; the inputs are not mutated.
    """
    project_mcp_names = {m.name for m in project.mcp}

    def _merge_deps(
        project_list: list[DependencySource],
        global_list: list[DependencySource],
    ) -> list[DependencySource]:
        seen = {d.identity for d in project_list}
        merged = list(project_list)
        for dep in global_list:
            if dep.identity not in seen:
                merged.append(dep)
                seen.add(dep.identity)
        return merged

    skills = _merge_deps(project.skills, global_cfg.skills)
    commands = _merge_deps(project.commands, global_cfg.commands)
    agents = _merge_deps(project.agents, global_cfg.agents)

    # Merge MCP — project names take precedence
    mcp = list(project.mcp)
    for server in global_cfg.mcp:
        if server.name not in project_mcp_names:
            mcp.append(server)

    return AgpackConfig(
        targets=project.targets,
        skills=skills,
        commands=commands,
        agents=agents,
        mcp=mcp,
        use_global=project.use_global,
    )
