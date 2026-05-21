"""Target manifest schema — dataclasses, parser, and validator.

A *target* describes where a single AI coding tool expects resources and
how its MCP config file is encoded.  Built-in manifests ship as YAML
files under ``agpack/builtin_targets/`` and users may override them in
their ``agpack.yml`` via ``target_definitions``.

The schema is intentionally declarative: every per-tool quirk that used
to live in Python (file paths, MCP top-level key, JSON vs TOML, opencode's
``command``-as-array form, copilot's explicit ``type: stdio``, etc.) is
expressed here so the deployer and MCP encoder stay generic.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Literal

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TargetSchemaError(Exception):
    """Raised when a target manifest fails to parse or validate."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_LAYOUTS = ("directory", "file")
_VALID_FORMATS = ("json", "toml")
_VALID_COMMAND_FORMATS = ("string", "array")
_VALID_RESOURCE_TYPES = ("skills", "commands", "agents")
_VALID_TRANSPORTS = ("stdio", "http", "sse")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceLayout:
    """How a single resource type is deployed for a target.

    Attributes:
        layout: ``"directory"`` to copy each dependency item as a whole
            folder (skill bundles), ``"file"`` to copy individual files
            (commands, agents).
        path: Deployment path, relative to the project root.
    """

    layout: Literal["directory", "file"]
    path: str


@dataclass(frozen=True)
class TransportSpec:
    """Encoding rules for one MCP transport (stdio / http / sse).

    All fields have sensible defaults so the most common case
    (e.g. stdio with ``command``/``args``/``env``) needs no configuration.

    Attributes:
        type_value: Value written under :attr:`type_field`.  When
            ``None`` (the default), no type field is emitted — useful for
            tools that infer the transport from which field is present.
        type_field: Key name for the transport type (default ``"type"``).
        command_key: Output key for the stdio command (default
            ``"command"``).  Only used when :attr:`command_format` is
            ``"string"``; in ``"array"`` form the merged list is written
            under this key.
        command_format: ``"string"`` keeps ``command``/``args`` separate;
            ``"array"`` merges them into a single list under
            :attr:`command_key` (opencode convention).
        args_key: Output key for stdio args (default ``"args"``).
            Ignored when :attr:`command_format` is ``"array"``.
        env_key: Output key for stdio env (default ``"env"``;
            opencode uses ``"environment"``).
        url_key: Output key for the remote URL (default ``"url"``;
            Gemini's Streamable HTTP uses ``"httpUrl"``).
        headers_key: Output key for remote headers (default
            ``"headers"``; Codex uses ``"http_headers"``).
    """

    type_value: str | None = None
    type_field: str = "type"
    command_key: str = "command"
    command_format: Literal["string", "array"] = "string"
    args_key: str = "args"
    env_key: str = "env"
    url_key: str = "url"
    headers_key: str = "headers"


@dataclass(frozen=True)
class McpSpec:
    """How a target stores MCP server definitions.

    Attributes:
        path: Config file path, relative to the project root.
        format: ``"json"`` or ``"toml"``.
        servers_key: Top-level key inside the config that holds the
            ``{name: server-object}`` mapping (``"mcpServers"``, ``"mcp"``,
            ``"servers"``, ``"mcp_servers"``, …).
        defaults: Constant fields merged into the config file's root
            (e.g. opencode's ``"$schema"`` reference).
        transports: Per-transport encoding rules.  Only transports listed
            here are emitted; missing transports are treated as
            unsupported by the target.
    """

    path: str
    format: Literal["json", "toml"]
    servers_key: str
    defaults: dict[str, Any] = field(default_factory=dict)
    transports: dict[str, TransportSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetDef:
    """A fully-resolved target manifest.

    The target's name is not stored on the dataclass — it's the key
    that addresses the manifest (filename for built-ins, mapping key
    for ``target_definitions`` in ``agpack.yml``).  That key is the
    single source of truth, so there is nowhere for a stale or
    mismatched ``name`` to hide.

    Attributes:
        resources: Per-resource-type deployment layouts.  Missing keys
            mean the target does not support that resource type.
        mcp: MCP encoding spec, or ``None`` if the target has no
            project-level MCP config (e.g. Windsurf, Antigravity).
    """

    resources: dict[str, ResourceLayout] = field(default_factory=dict)
    mcp: McpSpec | None = None


# ---------------------------------------------------------------------------
# Parser
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


def _parse_resource(raw: Any, context: str) -> ResourceLayout:
    data = _require_mapping(raw, context)

    layout = data.get("layout")
    if layout not in _VALID_LAYOUTS:
        raise TargetSchemaError(
            f"{context}.layout: must be one of {_VALID_LAYOUTS}, got {layout!r}"
        )

    path = _require_string(data.get("path"), f"{context}.path")

    extra = set(data) - {"layout", "path"}
    if extra:
        raise TargetSchemaError(f"{context}: unknown keys {sorted(extra)}")

    return ResourceLayout(layout=layout, path=path)


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
    extra = set(data) - known
    if extra:
        raise TargetSchemaError(f"{context}: unknown keys {sorted(extra)}")

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


def _parse_mcp(raw: Any, context: str) -> McpSpec:
    data = _require_mapping(raw, context)

    known = {"path", "format", "servers_key", "defaults", "transports"}
    extra = set(data) - known
    if extra:
        raise TargetSchemaError(f"{context}: unknown keys {sorted(extra)}")

    path = _require_string(data.get("path"), f"{context}.path")

    format_ = data.get("format")
    if format_ not in _VALID_FORMATS:
        raise TargetSchemaError(
            f"{context}.format: must be one of {_VALID_FORMATS}, got {format_!r}"
        )

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

    return McpSpec(
        path=path,
        format=format_,
        servers_key=servers_key,
        defaults=dict(defaults_raw),
        transports=transports,
    )


def parse_target_def(raw: Any, context: str = "target") -> TargetDef:
    """Parse a target manifest from a raw dict (loaded YAML).

    The YAML is flat: each top-level key is either a resource type
    (``skills`` / ``commands`` / ``agents``) or ``mcp``.  Unknown keys
    are rejected.

    Args:
        raw: The mapping produced by ``yaml.safe_load``.
        context: Error-message prefix identifying the source
            (e.g. ``"builtin_targets/claude.yml"`` or
            ``"target_definitions.claude"``).

    Raises:
        TargetSchemaError: If the manifest is malformed.
    """
    data = _require_mapping(raw, context)

    known = set(_VALID_RESOURCE_TYPES) | {"mcp"}
    extra = set(data) - known
    if extra:
        raise TargetSchemaError(
            f"{context}: unknown keys {sorted(extra)}; "
            f"valid: {sorted(known)}"
        )

    resources: dict[str, ResourceLayout] = {}
    for resource_name in _VALID_RESOURCE_TYPES:
        if resource_name in data:
            resources[resource_name] = _parse_resource(
                data[resource_name], f"{context}.{resource_name}"
            )

    mcp_raw = data.get("mcp")
    mcp = _parse_mcp(mcp_raw, f"{context}.mcp") if mcp_raw is not None else None

    return TargetDef(resources=resources, mcp=mcp)
