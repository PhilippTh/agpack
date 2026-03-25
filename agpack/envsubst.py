"""Environment variable substitution for agpack config values."""

from __future__ import annotations

import os
import re
from pathlib import Path

from agpack.config import AgpackConfig
from agpack.config import ConfigError

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


def resolve_config(
    config: AgpackConfig,
    project_root: Path,
    *,
    verbose: bool = False,
) -> None:
    """Resolve ``${VAR}`` references in config values in-place.

    Currently resolves MCP server ``env`` values. The substitution
    infrastructure is general-purpose and can be extended to other
    config fields as needed.

    Resolution order: ``.env`` file values take precedence over the
    shell environment.
    """
    dotenv = load_dotenv(project_root)
    merged = {**os.environ, **dotenv}

    if verbose and dotenv:
        from agpack.display import console

        console.print(f"  Loaded {len(dotenv)} variable(s) from .env")

    # MCP server env values
    for server in config.mcp:
        ctx = f"mcp server '{server.name}'"
        for key, value in server.env.items():
            server.env[key] = resolve_env_vars(value, merged, context=ctx)
