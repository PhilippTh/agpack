"""MCP config merge logic — generic encoder driven by TargetDef.mcp."""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from agpack.config import McpServer
from agpack.display import console
from agpack.target_schema import McpSpec
from agpack.target_schema import TargetDef
from agpack.target_schema import TransportSpec


class McpError(Exception):
    """Raised when an MCP config merge fails."""


# ---------------------------------------------------------------------------
# Server-object encoding
# ---------------------------------------------------------------------------


def _encode_server(server: McpServer, spec: TransportSpec) -> dict[str, Any]:
    """Render a single MCP server entry per the transport spec.

    The output dict key order is deterministic: ``type`` (when emitted),
    then ``command``/``url``, then ``args``, ``env``/``environment``.
    """
    obj: dict[str, Any] = {}

    if spec.type_value is not None:
        obj[spec.type_field] = spec.type_value

    if server.type == "stdio":
        if server.command is None:
            raise McpError(
                f"MCP server '{server.name}': stdio transport requires a command"
            )
        if spec.command_format == "array":
            obj[spec.command_key] = [server.command, *list(server.args)]
        else:
            obj[spec.command_key] = server.command
            if server.args:
                obj[spec.args_key] = list(server.args)
        if server.env:
            obj[spec.env_key] = dict(server.env)
    else:
        if server.url is None:
            raise McpError(
                f"MCP server '{server.name}': {server.type} transport requires a url"
            )
        obj[spec.url_key] = server.url

    return obj


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".agpack-mcp-")
    try:
        os.close(fd)
        Path(tmp_path).write_text(content, encoding="utf-8")
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _read_existing(config_path: Path, format_: str) -> dict[str, Any]:
    """Read an existing JSON/TOML config file, or return empty dict."""
    if not config_path.exists():
        return {}
    text = config_path.read_text(encoding="utf-8")
    try:
        if format_ == "json":
            data = json.loads(text)
        else:
            data = tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, OSError) as exc:
        raise McpError(f"Failed to read {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise McpError(f"{config_path}: top-level must be a mapping")
    return data


def _dump(data: dict[str, Any], format_: str) -> str:
    """Serialise a dict back to JSON or TOML text."""
    if format_ == "json":
        return json.dumps(data, indent=2) + "\n"
    return tomli_w.dumps(data)


def _merge_into_config(
    mcp_spec: McpSpec,
    config_path: Path,
    servers: dict[str, dict[str, Any]],
) -> None:
    """Merge server entries into a target's MCP config file.

    Also applies any ``defaults`` from the spec to the root of the
    config file when those keys are not already present.
    """
    existing = _read_existing(config_path, mcp_spec.format)

    for key, value in mcp_spec.defaults.items():
        existing.setdefault(key, value)

    existing.setdefault(mcp_spec.servers_key, {})
    existing[mcp_spec.servers_key].update(servers)

    _atomic_write(config_path, _dump(existing, mcp_spec.format))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def deploy_mcp_servers(
    mcp_servers: list[McpServer],
    targets: list[TargetDef],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict[str, list[str]]:
    """Deploy MCP server definitions to each target's MCP config file.

    Returns a dict mapping server name to the list of config-file paths
    (relative to *project_root*) that were written.  Targets without an
    ``mcp`` block in their manifest, or that don't support the server's
    transport, are skipped silently.
    """
    result: dict[str, list[str]] = {}

    for server in mcp_servers:
        written_to: list[str] = []

        for target in targets:
            if target.mcp is None:
                continue

            transport_spec = target.mcp.transports.get(server.type)
            if transport_spec is None:
                continue

            server_obj = _encode_server(server, transport_spec)
            servers_dict = {server.name: server_obj}

            config_path = project_root / target.mcp.path
            rel_path = target.mcp.path

            if dry_run:
                if verbose:
                    console.print(f"[dry-run]   merge MCP '{server.name}' → {rel_path}")
                written_to.append(rel_path)
                continue

            try:
                _merge_into_config(target.mcp, config_path, servers_dict)
            except McpError:
                raise
            except Exception as exc:
                raise McpError(
                    f"Failed to write MCP config to {config_path}: {exc}"
                ) from exc

            if verbose:
                console.print(f"  MCP '{server.name}' → {rel_path}")

            written_to.append(rel_path)

        result[server.name] = written_to

    return result


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def _find_spec_for_path(targets: list[TargetDef], rel_path: str) -> McpSpec | None:
    """Return the McpSpec whose config path matches *rel_path*."""
    for target in targets:
        if target.mcp is not None and target.mcp.path == rel_path:
            return target.mcp
    return None


def _format_from_extension(rel_path: str) -> str | None:
    if rel_path.endswith(".json"):
        return "json"
    if rel_path.endswith(".toml"):
        return "toml"
    return None


def _candidate_servers_keys(format_: str) -> tuple[str, ...]:
    if format_ == "json":
        return ("mcpServers", "mcp", "servers")
    return ("mcp_servers",)


def cleanup_mcp_server(
    server_name: str,
    target_paths: list[str],
    project_root: Path,
    targets: list[TargetDef],
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Remove an MCP server from each listed config file.

    When a path in *target_paths* still corresponds to a configured
    target, that target's ``servers_key`` is used.  Otherwise (the
    target was removed from the project), the format is inferred from
    the file extension and known keys are tried.
    """
    for rel_path in target_paths:
        config_path = project_root / rel_path
        if not config_path.exists():
            continue

        if dry_run:
            if verbose:
                console.print(f"[dry-run]   remove MCP '{server_name}' from {rel_path}")
            continue

        spec = _find_spec_for_path(targets, rel_path)
        if spec is not None:
            _remove_server(config_path, spec.format, (spec.servers_key,), server_name)
        else:
            format_ = _format_from_extension(rel_path)
            if format_ is None:
                continue
            _remove_server(
                config_path,
                format_,
                _candidate_servers_keys(format_),
                server_name,
            )

        if verbose:
            console.print(f"  removed MCP '{server_name}' from {rel_path}")


def _remove_server(
    config_path: Path,
    format_: str,
    servers_keys: tuple[str, ...],
    server_name: str,
) -> None:
    """Remove a server entry from the first matching servers_key."""
    try:
        data = (
            json.loads(config_path.read_text(encoding="utf-8"))
            if format_ == "json"
            else tomllib.loads(config_path.read_text(encoding="utf-8"))
        )
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, OSError):
        return

    if not isinstance(data, dict):
        return

    for key in servers_keys:
        servers = data.get(key)
        if isinstance(servers, dict) and server_name in servers:
            del servers[server_name]
            _atomic_write(config_path, _dump(data, format_))
            return
