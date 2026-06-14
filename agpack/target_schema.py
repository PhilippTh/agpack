"""Target manifest schema — parser, validator, and the resource-type unions.

A *target* describes the filesystem location of each resource type a single AI tool consumes. Each top-level key is
the resource type name (``skills``, ``commands``, ``mcp``, ``settings``, anything user-defined); each value declares a
resource definition via the ``kind:`` field.

The actual deploy/cleanup behavior lives on the kind classes in :mod:`agpack.kinds.copy_directory`,
:mod:`agpack.kinds.copy_file`, and :mod:`agpack.kinds.edit_file`. This module is only responsible for turning YAML
into well-typed resource definitions and exposing the union types used in signatures elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from agpack.errors import TargetSchemaError
from agpack.kinds import CopyDirectoryResource
from agpack.kinds import CopyFileResource
from agpack.kinds import EditFileResource
from agpack.kinds import ResourceDef
from agpack.kinds import infer_config_format

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_KINDS = ("copy-directory", "copy-file", "edit-file")


# ---------------------------------------------------------------------------
# TargetDef
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetDef:
    """A fully-resolved target manifest.

    :attr:`name` is the YAML filename (built-ins) or the mapping key under ``target_definitions:`` in ``agpack.yml``.
    It's recorded in the lockfile so cleanup can re-resolve ``${var}`` references against this target's ``vars`` long
    after the resource type has been removed from ``dependencies:``.
    """

    name: str = ""
    resources: dict[str, ResourceDef] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = f"{context}: expected a mapping, got {type(value).__name__}"
        raise TargetSchemaError(msg)
    return value


def _require_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value:
        msg = f"{context}: expected a non-empty string"
        raise TargetSchemaError(msg)
    return value


def _reject_extra(known: set[str], data: dict[str, Any], context: str) -> None:
    extra = set(data) - known
    if extra:
        msg = f"{context}: unknown keys {sorted(extra)}; valid: {sorted(known)}"
        raise TargetSchemaError(msg)


# ---------------------------------------------------------------------------
# Per-kind parsers
# ---------------------------------------------------------------------------


def _make_path(raw: str) -> str:
    return str(Path(raw).expanduser())


def _parse_copy_directory(data: dict[str, Any], context: str) -> CopyDirectoryResource:
    _reject_extra({"kind", "path"}, data, context)
    path = _make_path(_require_string(data.get("path"), f"{context}.path"))
    return CopyDirectoryResource(path=path)


def _parse_copy_file(data: dict[str, Any], context: str) -> CopyFileResource:
    _reject_extra({"kind", "path"}, data, context)
    path = _make_path(_require_string(data.get("path"), f"{context}.path"))
    return CopyFileResource(path=path)


def _parse_edit_file(data: dict[str, Any], context: str) -> EditFileResource:
    _reject_extra({"kind", "path", "vars"}, data, context)
    path = _make_path(_require_string(data.get("path"), f"{context}.path"))
    # Validate extension at parse time so malformed manifests fail loudly.
    try:
        infer_config_format(path)
    except Exception as exc:
        msg = f"{context}.path: {exc}"
        raise TargetSchemaError(msg) from exc

    raw_vars = data.get("vars", {})
    if not isinstance(raw_vars, dict):
        msg = f"{context}.vars: must be a mapping, got {type(raw_vars).__name__}"
        raise TargetSchemaError(msg)
    target_vars: dict[str, str] = {}
    for key, value in raw_vars.items():
        if not isinstance(key, str) or not key:
            msg = f"{context}.vars: keys must be non-empty strings, got {key!r}"
            raise TargetSchemaError(msg)
        if not isinstance(value, str):
            msg = f"{context}.vars.{key}: value must be a string, got {type(value).__name__}"
            raise TargetSchemaError(msg)
        target_vars[key] = value

    return EditFileResource(path=path, vars=target_vars)


# ---------------------------------------------------------------------------
# Resource block parser (kind dispatch)
# ---------------------------------------------------------------------------


def _parse_resource(raw: Any, context: str) -> ResourceDef:
    data = _require_mapping(raw, context)

    if "layout" in data:
        msg = (
            f"{context}.layout: deprecated — use 'kind: copy-directory' or "
            f"'kind: copy-file' instead. (Was: layout: {data['layout']!r}.)"
        )
        raise TargetSchemaError(msg)
    if "merge" in data:
        msg = (
            f"{context}.merge: removed — edit-file resources now take only "
            f"'kind' and 'path'. Patches live under dependencies in agpack.yml."
        )
        raise TargetSchemaError(msg)

    kind = data.get("kind")
    if kind not in _VALID_KINDS:
        msg = f"{context}.kind: must be one of {_VALID_KINDS}, got {kind!r}"
        raise TargetSchemaError(msg)

    if kind == "copy-directory":
        return _parse_copy_directory(data, context)
    if kind == "copy-file":
        return _parse_copy_file(data, context)
    return _parse_edit_file(data, context)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def parse_target_def(raw: Any, *, name: str = "", context: str = "target") -> TargetDef:
    """Parse a target manifest from a raw dict (loaded YAML).

    Each top-level key is a resource type name (``skills`` / ``commands`` / ``mcp`` / any user-defined name). Each
    block must declare a ``kind:`` (``copy-directory`` / ``copy-file`` / ``edit-file``) and a ``path:``.

    *name* is stored verbatim on the returned :class:`TargetDef` — callers know what the target is called (built-in
    filename or the mapping key under ``target_definitions:``) and pass it in.

    Raises:
        TargetSchemaError: If the manifest is malformed.
    """
    data = _require_mapping(raw, context)

    resources: dict[str, ResourceDef] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            msg = f"{context}: keys must be non-empty strings, got {key!r}"
            raise TargetSchemaError(msg)
        resources[key] = _parse_resource(value, f"{context}.{key}")

    return TargetDef(name=name, resources=resources)
