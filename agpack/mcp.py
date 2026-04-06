"""MCP config merge logic (JSON + TOML)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from agpack.config import McpServer
from agpack.display import console
from agpack.fileutil import atomic_write_text
from agpack.targets import MCP_TARGETS
from agpack.targets import McpTargetConfig


class McpError(Exception):
    """Raised when an MCP config merge fails."""


def _build_server_object(server: McpServer, target: str = "") -> dict[str, Any]:
    """Build the config object for a single MCP server.

    Different targets expect different schemas, so the output varies by target.
    """
    if target == "opencode":
        return _build_opencode_server_object(server)

    if server.type == "stdio":
        obj: dict[str, Any] = {}
        # VS Code / Copilot requires an explicit "type" field for stdio servers
        if target == "copilot":
            obj["type"] = "stdio"
        obj["command"] = server.command
        if server.args:
            obj["args"] = server.args
        if server.env:
            obj["env"] = server.env
        return obj
    else:
        obj = {"url": server.url, "type": server.type}
        return obj


def _build_opencode_server_object(server: McpServer) -> dict[str, Any]:
    """Build an MCP server object in opencode's expected schema.

    opencode uses: type "local"/"remote", command as array, "environment" key.
    """
    if server.type == "stdio":
        cmd = [server.command] + server.args if server.args else [server.command]
        obj: dict[str, Any] = {"type": "local", "command": cmd}
        if server.env:
            obj["environment"] = server.env
        return obj
    else:
        return {"type": "remote", "url": server.url}


def _merge_json(
    config_path: Path,
    servers_key: str,
    servers: dict[str, dict[str, Any]],
) -> None:
    """Merge MCP servers into a JSON config file."""
    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise McpError(f"Failed to read {config_path}: {exc}") from exc

    if servers_key not in existing:
        existing[servers_key] = {}

    existing[servers_key].update(servers)

    atomic_write_text(config_path, json.dumps(existing, indent=2) + "\n")


def _merge_toml(
    config_path: Path,
    servers_key: str,
    servers: dict[str, dict[str, Any]],
) -> None:
    """Merge MCP servers into a TOML config file."""
    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            raise McpError(f"Failed to read {config_path}: {exc}") from exc

    if servers_key not in existing:
        existing[servers_key] = {}

    existing[servers_key].update(servers)

    atomic_write_text(config_path, tomli_w.dumps(existing))


def deploy_mcp_servers(
    mcp_servers: list[McpServer],
    targets: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict[str, list[str]]:
    """Deploy MCP server definitions to all applicable target config files.

    Returns a dict mapping server name to list of config file paths
    (relative to project_root) that were written.
    """
    result: dict[str, list[str]] = {}

    for server in mcp_servers:
        written_to: list[str] = []

        for target in targets:
            target_cfg = MCP_TARGETS.get(target)
            if target_cfg is None:
                continue

            server_obj = _build_server_object(server, target=target)
            servers_dict = {server.name: server_obj}

            config_path = project_root / target_cfg.config_path
            rel_path = target_cfg.config_path

            if dry_run:
                if verbose:
                    console.print(f"[dry-run]   merge MCP '{server.name}' → {rel_path}")
                written_to.append(rel_path)
                continue

            try:
                if target_cfg.format == "json":
                    _merge_json(config_path, target_cfg.servers_key, servers_dict)
                elif target_cfg.format == "toml":
                    _merge_toml(config_path, target_cfg.servers_key, servers_dict)
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


def cleanup_mcp_server(
    server_name: str,
    target_paths: list[str],
    project_root: Path,
    targets: list[str],
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Remove an MCP server from all listed config files."""
    for rel_path in target_paths:
        config_path = project_root / rel_path
        if not config_path.exists():
            continue

        # Find the target config for this path
        target_cfg: McpTargetConfig | None = None
        for target in targets:
            cfg = MCP_TARGETS.get(target)
            if cfg and cfg.config_path == rel_path:
                target_cfg = cfg
                break

        if target_cfg is None:
            # Try to infer format from the file extension
            if rel_path.endswith(".json"):
                _remove_from_json(config_path, server_name, dry_run)
            elif rel_path.endswith(".toml"):
                _remove_from_toml(config_path, server_name, dry_run)
            continue

        if dry_run:
            if verbose:
                console.print(f"[dry-run]   remove MCP '{server_name}' from {rel_path}")
            continue

        if target_cfg.format == "json":
            _remove_server_from_json(config_path, target_cfg.servers_key, server_name)
        elif target_cfg.format == "toml":
            _remove_server_from_toml(config_path, target_cfg.servers_key, server_name)

        if verbose:
            console.print(f"  removed MCP '{server_name}' from {rel_path}")


def _remove_server_from_json(
    config_path: Path,
    servers_key: str,
    server_name: str,
) -> None:
    """Remove a server from a JSON config file."""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    servers = data.get(servers_key, {})
    if server_name in servers:
        del servers[server_name]
        atomic_write_text(config_path, json.dumps(data, indent=2) + "\n")


def _remove_server_from_toml(
    config_path: Path,
    servers_key: str,
    server_name: str,
) -> None:
    """Remove a server from a TOML config file."""
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return

    servers = data.get(servers_key, {})
    if server_name in servers:
        del servers[server_name]
        atomic_write_text(config_path, tomli_w.dumps(data))


def _remove_from_json(
    config_path: Path,
    server_name: str,
    dry_run: bool,
) -> None:
    """Remove a server from a JSON file, trying common server keys."""
    if dry_run:
        return
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    for key in ("mcpServers", "mcp", "servers"):
        servers = data.get(key, {})
        if isinstance(servers, dict) and server_name in servers:
            del servers[server_name]
            atomic_write_text(config_path, json.dumps(data, indent=2) + "\n")
            return


def _remove_from_toml(
    config_path: Path,
    server_name: str,
    dry_run: bool,
) -> None:
    """Remove a server from a TOML file, trying common server keys."""
    if dry_run:
        return
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return

    for key in ("mcp_servers",):
        servers = data.get(key, {})
        if isinstance(servers, dict) and server_name in servers:
            del servers[server_name]
            atomic_write_text(config_path, tomli_w.dumps(data))
            return
