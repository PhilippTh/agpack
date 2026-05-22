"""Environment variable substitution for agpack config values."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import DependencySource
from agpack.config import GlobalConfig
from agpack.kinds import Patch

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
                f"{hint}environment variable '{var_name}' is not set. "
                f"Define it in .env or your shell environment."
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
            console.print(
                f"  Loaded {len(project_dotenv)} variable(s) from project .env"
            )

    return merged


def resolve_config(
    config: AgpackConfig,
    project_root: Path,
    *,
    global_config: GlobalConfig | None = None,
    verbose: bool = False,
) -> None:
    """Resolve ``${VAR}`` references in config values in-place.

    Substitutes ``${VAR}`` references in every string field reachable
    from the config:

    - Fetch dependencies: ``urls``, ``path``, ``ref``.
    - Patch entries: the ``key`` plus every string nested anywhere
      inside ``value`` (recursing through dicts and lists).

    Resolution order for variables (highest priority first):

    1. Project ``.env`` (from *project_root*)
    2. Global ``.env`` (from the global config directory, if provided)
    3. Shell environment (``os.environ``)
    """
    merged = _build_env(project_root, global_config, verbose=verbose)

    for rt, entries in config.dependencies.items():
        for i, entry in enumerate(entries):
            if isinstance(entry, DependencySource):
                ctx = f"dependency '{entry.name}'"
                entry.urls = [
                    resolve_env_vars(u, merged, context=ctx) for u in entry.urls
                ]
                if entry.path is not None:
                    entry.path = resolve_env_vars(entry.path, merged, context=ctx)
                if entry.ref is not None:
                    entry.ref = resolve_env_vars(entry.ref, merged, context=ctx)
            elif isinstance(entry, Patch):
                ctx = f"patch {rt}[{i}] ({entry.key})"
                resolved_key = resolve_env_vars(entry.key, merged, context=ctx)
                resolved_value = _resolve_recursive(entry.value, merged, ctx)
                # Patch is frozen; rewrite in place via list assignment.
                entries[i] = Patch(
                    key=resolved_key,
                    value=resolved_value,
                    strategy=entry.strategy,
                )


def _resolve_recursive(value: Any, env: dict[str, str], context: str) -> Any:
    """Walk a JSON-ish value, substituting ${VAR} in every string leaf."""
    if isinstance(value, str):
        return resolve_env_vars(value, env, context=context)
    if isinstance(value, dict):
        return {k: _resolve_recursive(v, env, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_recursive(v, env, context) for v in value]
    return value
