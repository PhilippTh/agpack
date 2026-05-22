"""``${VAR}`` substitution and ``.env`` loading.

Leaf module: depends only on :mod:`agpack.errors`. Used by :mod:`agpack.config` (eager fetch-dep validation),
:mod:`agpack.fetcher` (URL/path/ref substitution at clone time), and :mod:`agpack.kinds.edit_file` (per-target patch
resolution at apply time).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from agpack.errors import ConfigError

# Matches either ``$$`` (escape — emit literal ``$``) or ``${name}`` (substitute). ``$${name}`` therefore writes a
# literal ``${name}`` to the destination, letting users pass through variables resolved by the consuming tool at
# runtime (e.g. Claude Code's ``${CLAUDE_PROJECT_DIR}`` inside hook commands).
_VAR_PATTERN = re.compile(r"\$\$|\$\{([^}]+)}")


def resolve_env_vars(value: str, env: dict[str, str], *, context: str = "") -> str:
    """Replace all ``${name}`` references in *value* from *env*.

    ``$$`` writes a literal ``$`` (so ``$${X}`` produces ``${X}``).
    Raises :class:`ConfigError` if a ``${name}`` reference is not defined.
    """

    def _replace(match: re.Match[str]) -> str:
        if match.group(0) == "$$":
            return "$"
        name = match.group(1)
        if name in env:
            return env[name]
        hint = context + ": " if context else ""
        msg = (
            f"{hint}variable '{name}' is not defined. "
            f"Define it in .env, your shell environment, or as a target "
            f"var; or use $${{{name}}} to write a literal ${{{name}}}."
        )
        raise ConfigError(msg) from None

    return _VAR_PATTERN.sub(_replace, value)


def resolve_env_vars_recursive(value: Any, env: dict[str, str], *, context: str = "") -> Any:
    """Walk a JSON-ish value substituting ``${name}`` in every string leaf.

    Used for patch values, where the same ``${name}`` semantics apply to nested dicts and lists. Non-string scalars
    pass through unchanged.
    """
    if isinstance(value, str):
        return resolve_env_vars(value, env, context=context)
    if isinstance(value, dict):
        return {k: resolve_env_vars_recursive(v, env, context=context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_vars_recursive(v, env, context=context) for v in value]
    return value


def load_dotenv(project_root: Path) -> dict[str, str]:
    """Load variables from a ``.env`` file in *project_root*.

    Returns an empty dict when the file does not exist. Supports ``KEY=VALUE``, optional quoting, ``# comments``, blank
    lines, and an optional ``export`` prefix.
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


def build_env(
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

    Takes ``global_config_dir`` as a :class:`Path` (not a ``GlobalConfig`` object) so this module stays a leaf — the
    caller in :mod:`agpack.config` unwraps the directory before calling.
    """
    global_dotenv = load_dotenv(global_config_dir) if global_config_dir is not None else {}
    project_dotenv = load_dotenv(project_root)
    merged = {**os.environ, **global_dotenv, **project_dotenv}

    if verbose:
        from agpack.display import console

        if global_dotenv:
            console.print(f"  Loaded {len(global_dotenv)} variable(s) from global .env")
        if project_dotenv:
            console.print(f"  Loaded {len(project_dotenv)} variable(s) from project .env")

    return merged
