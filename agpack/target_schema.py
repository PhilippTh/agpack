"""Target manifest schema — parser and validator.

A *target* describes where a single AI coding tool expects resources and
how its structured config files are merged. Each resource block is
identified by an arbitrary name (``skills``, ``commands``, ``mcp``, or
anything user-defined) and declares a :data:`~agpack.kinds.ResourceDef`
via the ``kind:`` field.

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
from agpack.kinds import MergeMcpServers
from agpack.kinds import ResourceDef
from agpack.kinds import TransportSpec
from agpack.kinds import infer_mcp_format

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TargetSchemaError(Exception):
    """Raised when a target manifest fails to parse or validate."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_KINDS = ("copy-directory", "copy-file", "edit-file")
_VALID_COMMAND_FORMATS = ("string", "array")
_VALID_TRANSPORTS = ("stdio", "http", "sse")


# ---------------------------------------------------------------------------
# TargetDef
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetDef:
    """A fully-resolved target manifest.

    The target's name is the YAML filename (built-ins) or the mapping
    key under ``target_definitions:`` in ``agpack.yml``; it is not
    stored on the dataclass.

    Attributes:
        resources: All resource definitions declared by this target,
            keyed by resource type name. ``mcp`` is no longer a
            reserved name — it's a regular entry whose kind happens to
            be ``edit-file``.
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
            f"{context}: unknown keys {sorted(extra)}; "
            f"valid: {sorted(known)}"
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


def _parse_transport(raw: Any, context: str) -> TransportSpec:
    data = _require_mapping(raw, context)

    known = {
        "type_value",
        "type_field",
        "command_key",
        "command_format",
        "args_key",
        "env_key",
        "url_key",
        "headers_key",
    }
    _reject_extra(known, data, context)

    type_value = data.get("type_value")
    if type_value is not None and not isinstance(type_value, str):
        raise TargetSchemaError(
            f"{context}.type_value: must be a string or null, "
            f"got {type(type_value).__name__}"
        )

    command_format = data.get("command_format", "string")
    if command_format not in _VALID_COMMAND_FORMATS:
        raise TargetSchemaError(
            f"{context}.command_format: must be one of {_VALID_COMMAND_FORMATS}, "
            f"got {command_format!r}"
        )

    kwargs: dict[str, Any] = {
        "type_value": type_value,
        "command_format": command_format,
    }
    for key in (
        "type_field",
        "command_key",
        "args_key",
        "env_key",
        "url_key",
        "headers_key",
    ):
        if key in data:
            kwargs[key] = _require_string(data[key], f"{context}.{key}")

    return TransportSpec(**kwargs)


def _parse_merge_mcp_servers(raw: Any, context: str) -> MergeMcpServers:
    data = _require_mapping(raw, context)
    _reject_extra({"servers_key", "defaults", "transports"}, data, context)

    servers_key = _require_string(data.get("servers_key"), f"{context}.servers_key")

    defaults_raw = data.get("defaults", {})
    if not isinstance(defaults_raw, dict):
        raise TargetSchemaError(f"{context}.defaults: must be a mapping")

    transports_raw = data.get("transports", {})
    transports_map = _require_mapping(transports_raw, f"{context}.transports")
    transports: dict[str, TransportSpec] = {}
    for transport_name, transport_raw in transports_map.items():
        if transport_name not in _VALID_TRANSPORTS:
            raise TargetSchemaError(
                f"{context}.transports: unknown transport {transport_name!r}; "
                f"valid: {_VALID_TRANSPORTS}"
            )
        transports[transport_name] = _parse_transport(
            transport_raw, f"{context}.transports.{transport_name}"
        )

    return MergeMcpServers(
        servers_key=servers_key,
        defaults=dict(defaults_raw),
        transports=transports,
    )


def _parse_edit_file(data: dict[str, Any], context: str) -> EditFileResource:
    _reject_extra({"kind", "path", "merge"}, data, context)

    path = _require_string(data.get("path"), f"{context}.path")
    # Validate the path has a recognised extension at parse time so
    # bad manifests fail loudly (not at deploy time).
    try:
        infer_mcp_format(path)
    except Exception as exc:
        raise TargetSchemaError(f"{context}.path: {exc}") from exc

    if "merge" not in data:
        raise TargetSchemaError(
            f"{context}.merge: required for kind: edit-file (use the "
            f"mcp-servers encoder shape)"
        )
    merge = _parse_merge_mcp_servers(data["merge"], f"{context}.merge")

    return EditFileResource(path=path, merge=merge)


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
    if "format" in data:
        raise TargetSchemaError(
            f"{context}.format: drop this field — the MCP config format "
            f"is inferred from the file extension of 'path'."
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
    ``edit-file``) plus the kind-specific fields.

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
