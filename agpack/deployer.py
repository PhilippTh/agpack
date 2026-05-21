"""Deployment — fetches a resource and writes it to each target.

This module is the single home for both kinds of deployment:

* **File resources** (skills / commands / agents): cloned from a git
  repo and copied verbatim into each target's resource directory.
* **MCP servers**: declared inline in ``agpack.yml`` and merged into
  each target's structured MCP config file (JSON or TOML).

The two paths share targets, lockfile bookkeeping, and atomic-write
discipline, but the data shapes diverge (filesystem tree vs. JSON/TOML
mapping), so each has its own helpers below — grouped under section
headers.  Public surface: :func:`detect_items`, :func:`deploy_item`,
:func:`deploy_mcp_servers`, :func:`cleanup_deployed_files`,
:func:`cleanup_mcp_server`.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from agpack.config import McpServer
from agpack.display import console
from agpack.fetcher import FetchResult
from agpack.lockfile import McpTargetRef
from agpack.target_schema import McpSpec
from agpack.target_schema import TargetDef
from agpack.target_schema import TransportSpec


class DeployError(Exception):
    """Raised when a file deployment fails."""


class McpError(Exception):
    """Raised when an MCP config merge fails."""


# ===========================================================================
# Section 1 — atomic-write primitives (shared)
# ===========================================================================


def _atomic_copy_file(src: Path, dst: Path) -> None:
    """Copy a file atomically using write-to-temp-then-rename."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dst.parent, prefix=".agpack-tmp-")
    try:
        os.close(fd)
        shutil.copy2(str(src), tmp_path)
        os.replace(tmp_path, str(dst))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write(path: Path, content: str) -> None:
    """Write text content to a file atomically."""
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


def _copy_tree(src_dir: Path, dst_dir: Path) -> list[str]:
    """Recursively copy a directory, returning list of destination paths."""
    deployed: list[str] = []
    for src_file in sorted(src_dir.rglob("*")):
        if src_file.is_file():
            rel = src_file.relative_to(src_dir)
            if any(part.startswith(".git") for part in rel.parts):
                continue
            dst_file = dst_dir / rel
            _atomic_copy_file(src_file, dst_file)
            deployed.append(str(dst_file))
    return deployed


def _find_asset_subfolders(path: Path) -> list[Path]:
    """Return immediate subdirectories that contain at least one file."""
    subfolders: list[Path] = []
    for item in sorted(path.iterdir()):
        if item.is_dir() and not item.name.startswith(".git"):
            has_files = any(
                f.is_file()
                and not any(p.startswith(".git") for p in f.relative_to(item).parts)
                for f in item.rglob("*")
            )
            if has_files:
                subfolders.append(item)
    return subfolders


def _find_top_level_files(path: Path) -> list[Path]:
    """Return non-hidden files at the top level of a directory."""
    return sorted(
        item
        for item in path.iterdir()
        if item.is_file() and not item.name.startswith(".")
    )


# ===========================================================================
# Section 2 — file resources: item detection
# ===========================================================================


def _detect_skill_items(fetch_result: FetchResult) -> list[tuple[str, Path]]:
    """Return ``(name, path)`` pairs for skill items in a fetch result.

    A directory with top-level files is treated as a single skill.
    A directory with only subdirectories expands to one skill per subfolder.
    """
    local_path = fetch_result.local_path

    if local_path.is_dir() and not _find_top_level_files(local_path):
        subfolders = _find_asset_subfolders(local_path)
        if not subfolders:
            raise DeployError(
                f"'{fetch_result.source.name}' is a directory but does not contain "
                f"any skill folders. Provide a path to a skill folder or a "
                f"directory containing skill folders."
            )
        return [(sf.name, sf) for sf in subfolders]

    return [(fetch_result.source.name, local_path)]


def _detect_file_items(
    fetch_result: FetchResult, label: str
) -> list[tuple[str, Path]]:
    """Return ``(name, path)`` pairs for file assets (commands / agents)."""
    local_path = fetch_result.local_path

    if local_path.is_dir():
        files = _find_top_level_files(local_path)
        if not files:
            for sf in _find_asset_subfolders(local_path):
                files.extend(_find_top_level_files(sf))
        if not files:
            article = "an" if label[0] in "aeiou" else "a"
            raise DeployError(
                f"'{fetch_result.source.name}' is a directory but does not contain "
                f"any {label} files. Provide a path to {article} {label} "
                f"file or a directory containing {label} files."
            )
        return [(f.name, f) for f in files]

    return [(fetch_result.source.name, local_path)]


def detect_items(
    fetch_result: FetchResult, resource_type: str
) -> list[tuple[str, Path]]:
    """Return ``(name, path)`` pairs for the items in a fetch result.

    Dispatches on ``resource_type``: ``"skills"`` expands a directory of
    subfolders into one item per subfolder; ``"commands"`` or
    ``"agents"`` expand a directory of files into one item per file.
    """
    if resource_type == "skills":
        return _detect_skill_items(fetch_result)
    # commands / agents → file-style detection; label loses the trailing 's'.
    return _detect_file_items(fetch_result, resource_type[:-1])


# ===========================================================================
# Section 3 — file resources: deployment
# ===========================================================================


def deploy_item(
    name: str,
    src_path: Path,
    resource_type: str,
    targets: list[TargetDef],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Deploy one item to every target that supports ``resource_type``.

    For ``layout: directory`` the destination is ``<path>/<name>/`` and
    either the whole source tree or a single source file is placed
    inside.  For ``layout: file`` the destination is ``<path>/<name>``
    directly (single file copy).  Targets without a layout for this
    resource type are silently skipped.
    """
    deployed: list[str] = []
    for target in targets:
        layout = target.resources.get(resource_type)
        if layout is None:
            continue

        dst = project_root / layout.path / name

        if layout.layout == "directory":
            if dry_run:
                if src_path.is_dir():
                    for f in sorted(src_path.rglob("*")):
                        if f.is_file() and not any(
                            p.startswith(".git")
                            for p in f.relative_to(src_path).parts
                        ):
                            rel = dst / f.relative_to(src_path)
                            deployed.append(str(rel.relative_to(project_root)))
                else:
                    deployed.append(
                        str((dst / src_path.name).relative_to(project_root))
                    )
                if verbose:
                    console.print(f"[dry-run]   copy {src_path} → {dst}")
                continue

            if src_path.is_dir():
                for copied in _copy_tree(src_path, dst):
                    deployed.append(str(Path(copied).relative_to(project_root)))
            else:
                dst_file = dst / src_path.name
                _atomic_copy_file(src_path, dst_file)
                deployed.append(str(dst_file.relative_to(project_root)))
        else:
            if dry_run:
                deployed.append(str(dst.relative_to(project_root)))
                if verbose:
                    console.print(f"[dry-run]   copy → {dst}")
                continue

            _atomic_copy_file(src_path, dst)
            deployed.append(str(dst.relative_to(project_root)))

    if verbose and not dry_run:
        for entry in deployed:
            console.print(f"  {entry}")

    return deployed


# ===========================================================================
# Section 4 — MCP servers: encoding
# ===========================================================================


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


def _read_existing(config_path: Path, format_: str) -> dict[str, Any]:
    """Read an existing JSON/TOML config file, or return an empty dict."""
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


# ===========================================================================
# Section 5 — MCP servers: deployment
# ===========================================================================


def deploy_mcp_servers(
    mcp_servers: list[McpServer],
    targets: list[TargetDef],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict[str, list[McpTargetRef]]:
    """Deploy MCP server definitions to each target's MCP config file.

    Returns a dict mapping server name to the list of
    :class:`McpTargetRef` records (path + servers_key + format) that
    were written.  Targets without an ``mcp`` block in their manifest,
    or that don't support the server's transport, are skipped silently;
    a server matched by *no* target produces a stderr warning.
    """
    result: dict[str, list[McpTargetRef]] = {}

    for server in mcp_servers:
        written_to: list[McpTargetRef] = []

        for target in targets:
            if target.mcp is None:
                continue

            transport_spec = target.mcp.transports.get(server.type)
            if transport_spec is None:
                continue

            server_obj = _encode_server(server, transport_spec)
            servers_dict = {server.name: server_obj}

            config_path = project_root / target.mcp.path
            ref = McpTargetRef(
                path=target.mcp.path,
                servers_key=target.mcp.servers_key,
                format=target.mcp.format,
            )

            if dry_run:
                if verbose:
                    console.print(
                        f"[dry-run]   merge MCP '{server.name}' → {ref.path}"
                    )
                written_to.append(ref)
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
                console.print(f"  MCP '{server.name}' → {ref.path}")

            written_to.append(ref)

        if not written_to:
            console.print(
                f"[yellow]warning[/yellow]: MCP server '{server.name}' "
                f"({server.type} transport) was not written to any target — "
                "no configured target supports this transport or has an mcp block."
            )

        result[server.name] = written_to

    return result


# ===========================================================================
# Section 6 — cleanup (file resources + MCP)
# ===========================================================================


def cleanup_deployed_files(
    deployed_files: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Remove previously deployed files and clean up empty directories."""
    for rel_path in deployed_files:
        full_path = project_root / rel_path
        if full_path.exists():
            if dry_run:
                if verbose:
                    console.print(f"[dry-run]   delete {rel_path}")
                continue
            full_path.unlink()
            if verbose:
                console.print(f"  deleted {rel_path}")

    if not dry_run:
        _cleanup_empty_dirs(deployed_files, project_root)


def _cleanup_empty_dirs(deployed_files: list[str], project_root: Path) -> None:
    """Remove empty parent directories left behind after file deletion."""
    dirs_to_check: set[Path] = set()
    for rel_path in deployed_files:
        path = project_root / rel_path
        parent = path.parent
        while parent != project_root:
            dirs_to_check.add(parent)
            parent = parent.parent

    for d in sorted(dirs_to_check, key=lambda p: len(p.parts), reverse=True):
        if d.exists() and d.is_dir() and not any(d.iterdir()):
            d.rmdir()


def cleanup_mcp_server(
    server_name: str,
    target_refs: list[McpTargetRef],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Remove an MCP server from each config file recorded in the lockfile.

    Each :class:`McpTargetRef` carries the ``servers_key`` and
    ``format`` that were used when the server was last written, so
    cleanup never needs to consult the current target manifests.  Refs
    missing ``servers_key`` or ``format`` (read from a pre-0.4.0
    lockfile) are skipped — they will be cleaned up on the next sync.
    """
    for ref in target_refs:
        if not ref.servers_key or not ref.format:
            if verbose:
                console.print(
                    f"  skipping cleanup of MCP '{server_name}' from {ref.path}: "
                    "lockfile missing servers_key/format (pre-0.4.0 entry)"
                )
            continue

        config_path = project_root / ref.path
        if not config_path.exists():
            continue

        if dry_run:
            if verbose:
                console.print(
                    f"[dry-run]   remove MCP '{server_name}' from {ref.path}"
                )
            continue

        _remove_server(config_path, ref.format, ref.servers_key, server_name)

        if verbose:
            console.print(f"  removed MCP '{server_name}' from {ref.path}")


def _remove_server(
    config_path: Path,
    format_: str,
    servers_key: str,
    server_name: str,
) -> None:
    """Remove a server entry from a config file's servers_key mapping."""
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

    servers = data.get(servers_key)
    if isinstance(servers, dict) and server_name in servers:
        del servers[server_name]
        _atomic_write(config_path, _dump(data, format_))
