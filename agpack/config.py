"""agpack.yml parsing, validation, global config merging, and env resolution.

The main entry point is :func:`load_resolved_config`, which performs all
config loading steps in one call:

1. Parse and validate the project ``agpack.yml``.
2. Load and merge the global config (unless opted out).
3. Resolve ``${VAR}`` references from ``.env`` files and the shell.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml

from agpack.targets import VALID_TARGETS

DEFAULT_GLOBAL_CONFIG_DIR = Path.home() / ".config" / "agpack"

# Dependency field names on AgpackConfig that hold list[DependencySource].
# Used to drive parsing, merging, and env resolution generically.
_DEP_FIELDS = ("skills", "commands", "agents", "rules", "ignores")

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_identity(url: str, path: str | None) -> str:
    """Build a unique identity key from a URL and optional path.

    Used by both :class:`DependencySource` and
    :class:`~agpack.lockfile.InstalledEntry` so the two stay in sync.
    """
    key = url
    if path:
        key = f"{key}::{path}"
    return key


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
        return make_identity(self.url, self.path)


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
    rules: list[DependencySource] = field(default_factory=list)
    ignores: list[DependencySource] = field(default_factory=list)
    mcp: list[McpServer] = field(default_factory=list)
    use_global: bool = True


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when agpack.yml is invalid."""


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_dependency(raw: dict[str, Any], context: str) -> DependencySource:
    """Parse a single dependency entry (object form)."""
    if not isinstance(raw, dict):
        raise ConfigError(f"{context}: expected an object with 'url' key, got {type(raw).__name__}")

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
        raise ConfigError(f"{context}: 'type' must be 'stdio', 'sse', or 'http', got '{server_type}'")

    if server_type == "stdio":
        command = raw.get("command")
        if not command:
            raise ConfigError(f"{context}: stdio MCP server '{name}' is missing required field 'command'")
        return McpServer(
            name=name,
            type=server_type,
            command=str(command),
            args=[str(a) for a in raw.get("args", [])],
            env={str(k): str(v) for k, v in raw.get("env", {}).items()},
        )
    url = raw.get("url")
    if not url:
        raise ConfigError(f"{context}: {server_type} MCP server '{name}' is missing required field 'url'")
    return McpServer(
        name=name,
        type=server_type,
        url=str(url),
    )


def _parse_dependencies(deps: dict[str, Any], prefix: str = "") -> dict[str, list[Any]]:
    """Parse all dependency lists from a ``dependencies`` mapping.

    Returns a dict keyed by field name (matching :class:`AgpackConfig`
    attributes), e.g. ``{"skills": [...], "commands": [...], ...}``.
    """
    result: dict[str, list[Any]] = {}
    for dep_field in _DEP_FIELDS:
        result[dep_field] = [
            _parse_dependency(entry, f"{prefix}dependencies.{dep_field}[{i}]")
            for i, entry in enumerate(deps.get(dep_field) or [])
        ]
    result["mcp"] = [_parse_mcp(m, f"{prefix}dependencies.mcp[{i}]") for i, m in enumerate(deps.get("mcp") or [])]
    return result


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_yaml(path: Path, *, yaml_label: str = "YAML", mapping_label: str = "Config file") -> dict[str, Any] | None:
    """Load and validate a YAML file as a mapping.

    Returns the parsed dict, or *None* for an empty file.
    Raises :class:`ConfigError` on parse failure or non-mapping content.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse {yaml_label}: {exc}") from exc

    if data is None:
        return None

    if not isinstance(data, dict):
        raise ConfigError(f"{mapping_label} must be a YAML mapping")

    return data


def _parse_config_data(
    data: dict[str, Any],
    *,
    require_targets: bool,
    prefix: str = "",
) -> AgpackConfig:
    """Build an :class:`AgpackConfig` from a parsed YAML dict.

    When *require_targets* is ``True`` (project configs), the ``targets``
    key is validated.  When ``False`` (global configs), ``targets``
    defaults to ``[]``.
    """
    targets: list[str] = []
    use_global = True

    if require_targets:
        targets = data.get("targets")  # type: ignore[assignment]
        if not targets or not isinstance(targets, list):
            raise ConfigError("Missing or invalid 'targets' (must be a list)")
        for t in targets:
            if t not in VALID_TARGETS:
                raise ConfigError(f"Unrecognised target '{t}'. Valid targets: {sorted(VALID_TARGETS)}")

        use_global = data.get("global", True)
        if not isinstance(use_global, bool):
            raise ConfigError("'global' must be true or false")

    deps = data.get("dependencies", {})
    if not isinstance(deps, dict):
        if prefix:
            raise ConfigError(f"{prefix.strip().capitalize()} config 'dependencies' must be a mapping")
        raise ConfigError("'dependencies' must be a mapping")

    parsed = _parse_dependencies(deps, prefix=prefix)
    return AgpackConfig(targets=targets, use_global=use_global, **parsed)


def _resolve_global_config_path() -> Path:
    """Return the global config file path.

    Respects the ``AGPACK_GLOBAL_CONFIG`` environment variable.
    Falls back to ``~/.config/agpack/agpack.yml``.
    """
    override = os.environ.get("AGPACK_GLOBAL_CONFIG")
    if override:
        return Path(override).resolve()
    return DEFAULT_GLOBAL_CONFIG_DIR / "agpack.yml"


def _load_project_config(path: Path) -> AgpackConfig:
    """Load and validate a project ``agpack.yml``."""
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    data = _load_yaml(path)
    if data is None:
        raise ConfigError("Config file must be a YAML mapping")
    return _parse_config_data(data, require_targets=True)


def _load_global_config(path: Path | None = None) -> tuple[AgpackConfig, Path] | None:
    """Load the global agpack config.

    Returns a ``(config, config_dir)`` tuple, or *None* if the file does
    not exist.  The returned :class:`AgpackConfig` has ``targets=[]``.
    """
    if path is None:
        path = _resolve_global_config_path()
    if not path.exists():
        return None

    data = _load_yaml(path, yaml_label="global config YAML", mapping_label="Global config file")
    if data is None:
        return AgpackConfig(targets=[]), path.parent

    cfg = _parse_config_data(data, require_targets=False, prefix="global ")
    return cfg, path.parent


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


def _merge_deps(
    project_list: list[DependencySource],
    global_list: list[DependencySource],
) -> list[DependencySource]:
    """Merge two dependency lists, deduplicating by identity."""
    seen = {d.identity for d in project_list}
    merged = list(project_list)
    for dep in global_list:
        if dep.identity not in seen:
            merged.append(dep)
            seen.add(dep.identity)
    return merged


def _merge_configs(project: AgpackConfig, global_cfg: AgpackConfig) -> AgpackConfig:
    """Merge a global config into a project config.

    Global dependencies are appended after project dependencies.
    Duplicates are resolved in favour of the project config.

    Returns a **new** :class:`AgpackConfig`; the inputs are not mutated.
    """
    merged_deps = {
        dep_field: _merge_deps(getattr(project, dep_field), getattr(global_cfg, dep_field)) for dep_field in _DEP_FIELDS
    }

    project_mcp_names = {m.name for m in project.mcp}
    merged_mcp = list(project.mcp) + [s for s in global_cfg.mcp if s.name not in project_mcp_names]

    return AgpackConfig(
        targets=project.targets,
        mcp=merged_mcp,
        use_global=project.use_global,
        **merged_deps,
    )


# ---------------------------------------------------------------------------
# Environment variable substitution
# ---------------------------------------------------------------------------

_VAR_PATTERN = re.compile(r"\$\{([^}]+)}")


def _load_dotenv(project_root: Path) -> dict[str, str]:
    """Load variables from a ``.env`` file in *project_root*.

    Returns an empty dict when the file does not exist.
    """
    env_path = project_root / ".env"
    if not env_path.exists():
        return {}

    result: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export ") :]

        key, _, value = line.partition("=")
        if not _:
            continue

        key = key.strip()
        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        result[key] = value

    return result


def _resolve_env_vars(value: str, env: dict[str, str], *, context: str = "") -> str:
    """Replace all ``${VAR}`` references in *value* from *env*.

    Raises :class:`ConfigError` if a referenced variable is not defined.
    """

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        try:
            return env[var_name]
        except KeyError:
            hint = context + ": " if context else ""
            raise ConfigError(
                f"{hint}environment variable '{var_name}' is not set. Define it in .env or your shell environment."
            ) from None

    return _VAR_PATTERN.sub(_replace, value)


def _build_env(
    project_root: Path,
    global_config_dir: Path | None = None,
    *,
    verbose: bool = False,
) -> dict[str, str]:
    """Build a merged environment dict for variable substitution.

    Resolution order (highest priority first):
      1. Project ``.env`` (from *project_root*)
      2. Global ``.env`` (from *global_config_dir*)
      3. Shell environment (``os.environ``)
    """
    global_dotenv: dict[str, str] = {}
    if global_config_dir is not None:
        global_dotenv = _load_dotenv(global_config_dir)

    project_dotenv = _load_dotenv(project_root)
    merged = {**os.environ, **global_dotenv, **project_dotenv}

    if verbose:
        from agpack.display import console

        if global_dotenv:
            console.print(f"  Loaded {len(global_dotenv)} variable(s) from global .env")
        if project_dotenv:
            console.print(f"  Loaded {len(project_dotenv)} variable(s) from project .env")

    return merged


def _resolve_config(
    config: AgpackConfig,
    project_root: Path,
    *,
    global_config_dir: Path | None = None,
    verbose: bool = False,
) -> None:
    """Resolve ``${VAR}`` references in config values in-place.

    Substitutes ``${VAR}`` references in **all** string fields across
    the config: dependency URLs, paths, refs, MCP commands, args, env
    values, and MCP URLs.
    """
    merged = _build_env(project_root, global_config_dir, verbose=verbose)

    for dep_field in _DEP_FIELDS:
        for dep in getattr(config, dep_field):
            ctx = f"dependency '{dep.name}'"
            dep.urls = [_resolve_env_vars(u, merged, context=ctx) for u in dep.urls]
            if dep.path is not None:
                dep.path = _resolve_env_vars(dep.path, merged, context=ctx)
            if dep.ref is not None:
                dep.ref = _resolve_env_vars(dep.ref, merged, context=ctx)

    for server in config.mcp:
        ctx = f"mcp server '{server.name}'"
        if server.command is not None:
            server.command = _resolve_env_vars(server.command, merged, context=ctx)
        server.args = [_resolve_env_vars(a, merged, context=ctx) for a in server.args]
        for key, value in server.env.items():
            server.env[key] = _resolve_env_vars(value, merged, context=ctx)
        if server.url is not None:
            server.url = _resolve_env_vars(server.url, merged, context=ctx)


# ---------------------------------------------------------------------------
# High-level: load, merge, resolve in one call
# ---------------------------------------------------------------------------


def load_resolved_config(
    config_path: Path,
    *,
    no_global: bool = False,
    verbose: bool = False,
) -> AgpackConfig:
    """Load, merge global config, and resolve env vars — all in one step.

    This is the main entry point for obtaining a fully-ready config.

    Args:
        config_path: Path to the project ``agpack.yml``.
        no_global: If *True*, skip loading the global config.
        verbose: Print diagnostic messages.

    Returns:
        A fully resolved :class:`AgpackConfig`.

    Raises:
        ConfigError: If any config file is invalid or a ``${VAR}`` cannot
            be resolved.
    """
    project_root = config_path.parent

    config = _load_project_config(config_path)

    global_config_dir: Path | None = None
    if not no_global and config.use_global:
        result = _load_global_config()
        if result is not None:
            global_cfg, global_config_dir = result
            if verbose:
                from agpack.display import console

                console.print("  Loaded global config")
            config = _merge_configs(config, global_cfg)

    _resolve_config(config, project_root, global_config_dir=global_config_dir, verbose=verbose)

    return config
