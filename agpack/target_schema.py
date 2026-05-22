"""Target manifest schema — parser and validator.

A *target* describes the filesystem location of each resource type a
single AI tool consumes. Each top-level key is the resource type name
(``skills``, ``commands``, ``mcp``, ``settings``, anything user-defined);
each value declares a :data:`~agpack.kinds.ResourceDef` via the ``kind:``
field.

The actual deploy/cleanup behavior lives on the kind classes in
:mod:`agpack.kinds`; this module is only responsible for turning YAML
into well-typed resource definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

from agpack.kinds import CopyDirectoryResource
from agpack.kinds import CopyFileResource
from agpack.kinds import EditFileResource
from agpack.kinds import ResourceDef
from agpack.kinds import infer_config_format

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TargetSchemaError(Exception):
    """Raised when a target manifest fails to parse or validate."""


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

    The target's name is the YAML filename (built-ins) or the mapping
    key under ``target_definitions:`` in ``agpack.yml``; it is not
    stored on the dataclass.
    """

    resources: dict[str, ResourceDef] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TargetSchemaError(
            f"{context}: expected a mapping, got {type(value).__name__}"
        )
    return value


def _require_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise TargetSchemaError(f"{context}: expected a non-empty string")
    return value


def _reject_extra(known: set[str], data: dict[str, Any], context: str) -> None:
    extra = set(data) - known
    if extra:
        raise TargetSchemaError(
            f"{context}: unknown keys {sorted(extra)}; valid: {sorted(known)}"
        )


# ---------------------------------------------------------------------------
# Per-kind parsers
# ---------------------------------------------------------------------------


def _parse_copy_directory(data: dict[str, Any], context: str) -> CopyDirectoryResource:
    _reject_extra({"kind", "path"}, data, context)
    path = _require_string(data.get("path"), f"{context}.path")
    return CopyDirectoryResource(path=path)


def _parse_copy_file(data: dict[str, Any], context: str) -> CopyFileResource:
    _reject_extra({"kind", "path"}, data, context)
    path = _require_string(data.get("path"), f"{context}.path")
    return CopyFileResource(path=path)


def _parse_edit_file(data: dict[str, Any], context: str) -> EditFileResource:
    _reject_extra({"kind", "path"}, data, context)
    path = _require_string(data.get("path"), f"{context}.path")
    # Validate extension at parse time so malformed manifests fail loudly.
    try:
        infer_config_format(path)
    except Exception as exc:
        raise TargetSchemaError(f"{context}.path: {exc}") from exc
    return EditFileResource(path=path)


# ---------------------------------------------------------------------------
# Resource block parser (kind dispatch)
# ---------------------------------------------------------------------------


def _parse_resource(raw: Any, context: str) -> ResourceDef:
    data = _require_mapping(raw, context)

    if "layout" in data:
        raise TargetSchemaError(
            f"{context}.layout: deprecated — use 'kind: copy-directory' or "
            f"'kind: copy-file' instead. (Was: layout: {data['layout']!r}.)"
        )
    if "merge" in data:
        raise TargetSchemaError(
            f"{context}.merge: removed — edit-file resources now take only "
            f"'kind' and 'path'. Patches live under dependencies in agpack.yml."
        )

    kind = data.get("kind")
    if kind not in _VALID_KINDS:
        raise TargetSchemaError(
            f"{context}.kind: must be one of {_VALID_KINDS}, got {kind!r}"
        )

    if kind == "copy-directory":
        return _parse_copy_directory(data, context)
    if kind == "copy-file":
        return _parse_copy_file(data, context)
    return _parse_edit_file(data, context)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def parse_target_def(raw: Any, context: str = "target") -> TargetDef:
    """Parse a target manifest from a raw dict (loaded YAML).

    Each top-level key is a resource type name (``skills`` /
    ``commands`` / ``mcp`` / any user-defined name). Each block must
    declare a ``kind:`` (``copy-directory`` / ``copy-file`` /
    ``edit-file``) and a ``path:``.

    Raises:
        TargetSchemaError: If the manifest is malformed.
    """
    data = _require_mapping(raw, context)

    resources: dict[str, ResourceDef] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            raise TargetSchemaError(
                f"{context}: keys must be non-empty strings, got {key!r}"
            )
        resources[key] = _parse_resource(value, f"{context}.{key}")

    return TargetDef(resources=resources)
