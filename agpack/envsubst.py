"""Environment variable substitution for agpack config values."""

from __future__ import annotations

import os
import re
from pathlib import Path

from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import DependencySource
from agpack.config import GlobalConfig

# See agpack.kinds._VAR_PATTERN — same semantics: ``$$`` is an escape
# that produces a literal ``$``; ``${name}`` substitutes from env.
_VAR_PATTERN = re.compile(r"\$\$|\$\{([^}]+)}")


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

    ``$$`` writes a literal ``$`` (so ``$${X}`` produces ``${X}``).
    Raises :class:`ConfigError` if a ``${VAR}`` reference is not defined.
    """

    def _replace(match: re.Match[str]) -> str:
        if match.group(0) == "$$":
            return "$"
        var_name = match.group(1)
        try:
            return env[var_name]
        except KeyError:
            hint = context + ": " if context else ""
            raise ConfigError(
                f"{hint}environment variable '{var_name}' is not set. "
                f"Define it in .env or your shell environment, or use "
                f"$${{{var_name}}} to write a literal ${{{var_name}}}."
            ) from None

    return _VAR_PATTERN.sub(_replace, value)


def build_env(
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
) -> dict[str, str]:
    """Resolve ``${VAR}`` references in fetch dependencies in-place.

    Only fetch (copy-kind) dependency fields — URLs, paths, refs — are
    substituted here, because they have no per-target context. Patch
    entries (edit-file kind) are *not* substituted at load time: their
    ``${name}`` references are resolved per-target at apply time so
    that target ``vars`` can win over environment variables (see
    :meth:`agpack.kinds.EditFileResource.apply_patches`).

    Returns the merged environment table for downstream apply-time
    substitution.

    Resolution order (highest priority first):

    1. Project ``.env`` (from *project_root*)
    2. Global ``.env`` (from the global config directory, if provided)
    3. Shell environment (``os.environ``)
    """
    merged = build_env(project_root, global_config, verbose=verbose)

    for entries in config.dependencies.values():
        for entry in entries:
            if isinstance(entry, DependencySource):
                ctx = f"dependency '{entry.name}'"
                entry.urls = [
                    resolve_env_vars(u, merged, context=ctx) for u in entry.urls
                ]
                if entry.path is not None:
                    entry.path = resolve_env_vars(entry.path, merged, context=ctx)
                if entry.ref is not None:
                    entry.ref = resolve_env_vars(entry.ref, merged, context=ctx)

    return merged
