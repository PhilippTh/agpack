"""Target manifest discovery and loading.

Built-in target manifests ship as YAML files inside the package (``agpack/builtin_targets/``).  This module loads them
via ``importlib.resources`` so they continue to work when agpack is installed from a wheel.

The full resolution chain (project ``target_definitions`` → global ``target_definitions`` → built-in) lives in a later
commit; this module currently exposes only the built-in surface.
"""

from __future__ import annotations

from importlib.resources import files

import yaml

from agpack.target_schema import TargetDef
from agpack.target_schema import TargetSchemaError
from agpack.target_schema import parse_target_def

_BUILTIN_PACKAGE = "agpack.builtin_targets"
_BUILTIN_SUFFIX = ".yml"


def list_builtins() -> list[str]:
    """Return the sorted list of built-in target names."""
    names: list[str] = []
    for entry in files(_BUILTIN_PACKAGE).iterdir():
        if entry.is_file() and entry.name.endswith(_BUILTIN_SUFFIX):
            names.append(entry.name[: -len(_BUILTIN_SUFFIX)])
    return sorted(names)


def load_builtin(name: str) -> TargetDef:
    """Load a single built-in target manifest by name.

    Args:
        name: The target name (matches the YAML filename without
            extension, e.g. ``"claude"``).

    Raises:
        TargetSchemaError: If the manifest does not exist or is invalid.
    """
    resource = files(_BUILTIN_PACKAGE).joinpath(f"{name}{_BUILTIN_SUFFIX}")
    if not resource.is_file():
        available = ", ".join(list_builtins())
        msg = f"No built-in target named '{name}'. Available: {available}"
        raise TargetSchemaError(msg)

    try:
        data = yaml.safe_load(resource.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"Failed to parse built-in target '{name}': {exc}"
        raise TargetSchemaError(msg) from exc

    return parse_target_def(data, name=name, context=f"builtin_targets/{name}{_BUILTIN_SUFFIX}")


def load_all_builtins() -> dict[str, TargetDef]:
    """Load every shipped built-in manifest, keyed by name."""
    return {name: load_builtin(name) for name in list_builtins()}
