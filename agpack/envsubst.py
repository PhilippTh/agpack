"""Environment variable substitution for agpack config values."""

from __future__ import annotations

import os
import re
from pathlib import Path

from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import GlobalConfig

_VAR_PATTERN = re.compile(r"\$\{([^}]+)}")


def load_dotenv(project_root: Path) -> dict[str, str]:
    """Load variables from a ``.env`` file in *project_root*.

    Returns an empty dict when the file does not exist.
    Supports ``KEY=VALUE``, optional quoting, ``# comments``,
    blank lines, and an optional ``export`` prefix.
    """
    env_path = project_root / ".env"
    if not env_path.exists():
        return {}

    result: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Strip optional "export " prefix
        if line.startswith("export "):
            line = line[len("export ") :]

        key, _, value = line.partition("=")
        if not _:
            continue  # no '=' found — skip malformed line

        key = key.strip()
        value = value.strip()

        # Strip matching outer quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        result[key] = value

    return result


def resolve_env_vars(value: str, env: dict[str, str], *, context: str = "") -> str:
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
    global_config: GlobalConfig | None = None,
    *,
    verbose: bool = False,
) -> dict[str, str]:
    """Build a merged environment dict for variable substitution.

    Resolution order (highest priority first):
      1. Project ``.env`` (from *project_root*)
      2. Global ``.env`` (from the global config directory)
      3. Shell environment (``os.environ``)
    """
    global_dotenv: dict[str, str] = {}
    if global_config is not None:
        global_dotenv = load_dotenv(global_config.config_dir)

    project_dotenv = load_dotenv(project_root)
    merged = {**os.environ, **global_dotenv, **project_dotenv}

    if verbose:
        from agpack.display import console

        if global_dotenv:
            console.print(f"  Loaded {len(global_dotenv)} variable(s) from global .env")
        if project_dotenv:
            console.print(f"  Loaded {len(project_dotenv)} variable(s) from project .env")

    return merged


def resolve_config(
    config: AgpackConfig,
    project_root: Path,
    *,
    global_config: GlobalConfig | None = None,
    verbose: bool = False,
) -> None:
    """Resolve ``${VAR}`` references in config values in-place.

    Substitutes ``${VAR}`` references in **all** string fields across
    the config: dependency URLs, paths, refs, MCP commands, args, env
    values, and MCP URLs.

    Resolution order for variables (highest priority first):

    1. Project ``.env`` (from *project_root*)
    2. Global ``.env`` (from the global config directory, if provided)
    3. Shell environment (``os.environ``)
    """
    merged = _build_env(project_root, global_config, verbose=verbose)

    # Dependency fields: urls, path, ref
    for dep in [*config.skills, *config.commands, *config.agents, *config.rules]:
        ctx = f"dependency '{dep.name}'"
        dep.urls = [resolve_env_vars(u, merged, context=ctx) for u in dep.urls]
        if dep.path is not None:
            dep.path = resolve_env_vars(dep.path, merged, context=ctx)
        if dep.ref is not None:
            dep.ref = resolve_env_vars(dep.ref, merged, context=ctx)

    # MCP server fields: command, args, env values, url
    for server in config.mcp:
        ctx = f"mcp server '{server.name}'"
        if server.command is not None:
            server.command = resolve_env_vars(server.command, merged, context=ctx)
        server.args = [resolve_env_vars(a, merged, context=ctx) for a in server.args]
        for key, value in server.env.items():
            server.env[key] = resolve_env_vars(value, merged, context=ctx)
        if server.url is not None:
            server.url = resolve_env_vars(server.url, merged, context=ctx)
