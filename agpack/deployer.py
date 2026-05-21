"""Orchestration over :mod:`agpack.kinds`.

Per-kind behavior lives on the resource dataclasses themselves (in
:mod:`agpack.kinds`); this module only loops over targets and forwards
to the right kind. The two public entrypoints handle the two
fundamentally different deployment shapes:

* Copy kinds (``copy-directory`` / ``copy-file``): a tree of items is
  fetched from a git repo, detected, and copied to each target that
  declares the matching resource type. :func:`detect_items` and
  :func:`deploy_item` cover this path.
* Edit kind (``edit-file``): a list of structured entries
  (currently only MCP servers) declared inline in ``agpack.yml`` is
  merged into each target's config file. :func:`deploy_mcp_servers`
  covers this path.

Cleanup is split the same way: :func:`cleanup_deployed_files` removes
files written by copy kinds, :func:`cleanup_mcp_server` removes entries
written by edit-file resources.
"""

from __future__ import annotations

from pathlib import Path

from agpack.config import McpServer
from agpack.display import console
from agpack.fetcher import FetchResult
from agpack.kinds import CopyResource
from agpack.kinds import DeployError
from agpack.kinds import EditFileResource
from agpack.kinds import MergeMcpServers
from agpack.kinds import ResourceDef
from agpack.lockfile import McpTargetRef
from agpack.target_schema import TargetDef

# ===========================================================================
# Copy kinds — fetch + detect + deploy
# ===========================================================================


def detect_items(
    fetch_result: FetchResult, resource: ResourceDef, label: str
) -> list[tuple[str, Path]]:
    """Return ``(name, source-path)`` pairs for the items in a fetch result.

    Only copy kinds (``copy-directory`` / ``copy-file``) have a detect
    phase — edit-file resources get their entries inline from
    ``config.mcp:``. Passing an edit-file resource is a programmer
    error.
    """
    if isinstance(resource, CopyResource):
        return resource.detect(fetch_result, label)
    raise DeployError(
        f"detect_items called with a {resource.kind} resource; "
        "only copy kinds support detection"
    )


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

    Targets that don't declare the resource type, or that declare it
    with a non-copy kind, are silently skipped.
    """
    deployed: list[str] = []
    for target in targets:
        resource = target.resources.get(resource_type)
        if not isinstance(resource, CopyResource):
            continue
        deployed.extend(
            resource.deploy_item(
                name, src_path, project_root, dry_run=dry_run, verbose=verbose
            )
        )

    if verbose and not dry_run:
        for entry in deployed:
            console.print(f"  {entry}")

    return deployed


# ===========================================================================
# Edit-file kind (MCP servers) — encode + merge
# ===========================================================================


def deploy_mcp_servers(
    mcp_servers: list[McpServer],
    targets: list[TargetDef],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict[str, list[McpTargetRef]]:
    """Deploy MCP server definitions to every edit-file resource.

    Every :class:`EditFileResource` in every configured target receives
    every MCP server (filtered by transport support inside the
    resource's encoder). Targets without an edit-file resource, or
    whose transports don't include the server's type, are skipped
    silently; a server matched by *no* edit-file resource produces a
    stderr warning.
    """
    result: dict[str, list[McpTargetRef]] = {}

    for server in mcp_servers:
        written_to: list[McpTargetRef] = []

        for target in targets:
            for resource in target.resources.values():
                if not isinstance(resource, EditFileResource):
                    continue
                ref = resource.deploy_server(
                    server, project_root, dry_run=dry_run, verbose=verbose
                )
                if ref is not None:
                    written_to.append(ref)

        if not written_to:
            console.print(
                f"[yellow]warning[/yellow]: MCP server '{server.name}' "
                f"({server.type} transport) was not written to any target — "
                "no configured target has a matching edit-file resource or "
                "supports this transport."
            )

        result[server.name] = written_to

    return result


# ===========================================================================
# Cleanup (file resources + MCP)
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

    For each ref we rebuild a minimal :class:`EditFileResource` and
    delegate. Refs missing ``servers_key`` (pre-0.4.0 lockfiles) are
    skipped — they'll be re-recorded with full metadata on the next
    sync. Format-inference failures are absorbed inside the kind's
    own cleanup.
    """
    for ref in target_refs:
        if not ref.servers_key:
            if verbose:
                console.print(
                    f"  skipping cleanup of MCP '{server_name}' from {ref.path}: "
                    "lockfile missing servers_key (pre-0.4.0 entry)"
                )
            continue

        resource = EditFileResource(
            path=ref.path,
            merge=MergeMcpServers(servers_key=ref.servers_key),
        )
        resource.cleanup_entry(
            server_name, project_root, dry_run=dry_run, verbose=verbose
        )
