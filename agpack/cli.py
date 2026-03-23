"""Click entrypoints for the agpack CLI."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from pathlib import Path
from typing import Any

import click
from rich.progress import Progress
from rich.progress import TaskID

from agpack import __version__
from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import DependencySource
from agpack.config import load_config
from agpack.deployer import cleanup_deployed_files
from agpack.deployer import deploy_agent
from agpack.deployer import deploy_command
from agpack.deployer import deploy_skill
from agpack.display import console
from agpack.display import create_sync_progress
from agpack.fetcher import FetchError
from agpack.fetcher import FetchResult
from agpack.fetcher import cleanup_fetch
from agpack.fetcher import fetch_dependency
from agpack.lockfile import InstalledEntry
from agpack.lockfile import Lockfile
from agpack.lockfile import McpLockEntry
from agpack.lockfile import find_removed_dependencies
from agpack.lockfile import find_removed_mcp_servers
from agpack.lockfile import read_lockfile
from agpack.lockfile import write_lockfile
from agpack.mcp import McpError
from agpack.mcp import cleanup_mcp_server
from agpack.mcp import deploy_mcp_servers

_MAX_FETCH_WORKERS = 8


def _sync_resource_type(
    deps: list[DependencySource],
    deploy_fn: Callable[..., list[str]],
    resource_type: str,
    config: AgpackConfig,
    project_root: Path,
    new_lockfile: Lockfile,
    progress: Progress,
    *,
    dry_run: bool,
    verbose: bool,
) -> tuple[int, list[str]]:
    """Fetch and deploy a list of dependencies of a single type.

    Returns a tuple of (count of synced resources, list of deployed file paths).
    On error, writes a partial lockfile (with what has been synced so far)
    before raising ClickException.
    """
    if not deps:
        return 0, []

    # Phase 1: parallel fetch — collect all results and errors
    results: list[tuple[DependencySource, FetchResult]] = []
    errors: list[str] = []

    # Add a progress row per dependency
    task_ids: dict[str, TaskID] = {}
    for dep in deps:
        task_ids[dep.identity] = progress.add_task(
            f"{resource_type} '{dep.name}'",
            total=2,
            icon=" ",
            detail=f"Fetching from {dep.url}",
        )

    with ThreadPoolExecutor(max_workers=min(_MAX_FETCH_WORKERS, len(deps))) as executor:
        futures = {executor.submit(fetch_dependency, dep): dep for dep in deps}
        for future in as_completed(futures):
            dep = futures[future]
            tid = task_ids[dep.identity]
            try:
                results.append((dep, future.result()))
                progress.update(tid, completed=1, detail="Deploying...")
            except FetchError as exc:
                errors.append(f"  - {resource_type} '{dep.name}': {exc}")
                progress.update(tid, completed=2, icon="[red]✗[/red]", detail=str(exc))

    # Phase 2: collect-all error handling
    if errors:
        for _, result in results:
            cleanup_fetch(result)
        if not dry_run:
            write_lockfile(project_root, new_lockfile)
        raise click.ClickException(
            "\n".join([f"Failed to fetch {len(errors)} {resource_type}(s):"] + errors)
        )

    # Phase 3: sequential deploy — update progress rows, collect verbose output
    count = 0
    verbose_lines: list[str] = []
    for dep, result in results:
        tid = task_ids[dep.identity]
        try:
            deployed = deploy_fn(
                result,
                config.targets,
                project_root,
                dry_run=dry_run,
                verbose=False,
            )
        except Exception as exc:
            progress.update(tid, completed=2, icon="[red]✗[/red]", detail=str(exc))
            cleanup_fetch(result)
            if not dry_run:
                write_lockfile(project_root, new_lockfile)
            raise click.ClickException(
                f"Error deploying {resource_type} '{dep.name}': {exc}"
            ) from exc

        file_count = len(deployed)
        label = "file" if file_count == 1 else "files"
        progress.update(
            tid,
            completed=2,
            icon="[green]✓[/green]",
            detail=f"Done ({file_count} {label})",
        )

        if verbose:
            verbose_lines.extend(f"  {f}" for f in deployed)

        new_lockfile.installed.append(
            InstalledEntry(
                url=dep.url,
                path=dep.path,
                resolved_ref=result.resolved_ref,
                type=resource_type,
                deployed_files=deployed,
            )
        )
        cleanup_fetch(result)
        count += 1

    return count, verbose_lines


@click.group()
@click.version_option(version=__version__)
def main() -> None:
    """agpack — fetch and deploy AI agent resources."""


@main.command()
@click.option("--dry-run", is_flag=True, help="Print actions without writing files.")
@click.option(
    "--config",
    "config_path",
    default="./agpack.yml",
    type=click.Path(),
    help="Path to config file.",
)
@click.option("--verbose", is_flag=True, help="Print each file being written.")
def sync(dry_run: bool, config_path: str, verbose: bool) -> None:
    """Fetch all dependencies and deploy to all target directories."""
    cfg_path = Path(config_path).resolve()
    project_root = cfg_path.parent
    start_time = time.monotonic()

    # 1. Load and validate config
    try:
        config = load_config(cfg_path)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    # 2. Read existing lockfile
    old_lockfile = read_lockfile(project_root)

    # 3. Build set of current dependency identities
    current_identities: set[str] = set()
    for dep in [*config.skills, *config.commands, *config.agents]:
        current_identities.add(dep.identity)

    current_mcp_names = {m.name for m in config.mcp}

    # 4. Clean up removed dependencies
    removed_deps = find_removed_dependencies(old_lockfile, current_identities)
    for entry in removed_deps:
        if verbose or dry_run:
            console.print(f"Removing {entry.type} '{entry.identity}'...")
        cleanup_deployed_files(
            entry.deployed_files, project_root, dry_run=dry_run, verbose=verbose
        )

    # 5. Clean up removed MCP servers
    removed_mcp = find_removed_mcp_servers(old_lockfile, current_mcp_names)
    for mcp_entry in removed_mcp:
        if verbose or dry_run:
            console.print(f"Removing MCP server '{mcp_entry.name}'...")
        cleanup_mcp_server(
            mcp_entry.name,
            mcp_entry.targets,
            project_root,
            config.targets,
            dry_run=dry_run,
            verbose=verbose,
        )

    # 6. Fetch and deploy dependencies
    new_lockfile = Lockfile()
    counts: dict[str, int] = {}

    resource_types: list[tuple[list[DependencySource], Callable[..., Any], str]] = [
        (config.skills, deploy_skill, "skill"),
        (config.commands, deploy_command, "command"),
        (config.agents, deploy_agent, "agent"),
    ]

    all_verbose_lines: list[str] = []

    with create_sync_progress() as progress:
        for deps, deploy_fn, resource_type in resource_types:
            count, verbose_lines = _sync_resource_type(
                deps,
                deploy_fn,
                resource_type,
                config,
                project_root,
                new_lockfile,
                progress,
                dry_run=dry_run,
                verbose=verbose,
            )
            counts[resource_type] = count
            all_verbose_lines.extend(verbose_lines)

    for line in all_verbose_lines:
        console.print(line)

    # MCP servers
    mcp_count = 0
    if config.mcp:
        with console.status("Deploying MCP servers..."):
            try:
                mcp_result = deploy_mcp_servers(
                    config.mcp,
                    config.targets,
                    project_root,
                    dry_run=dry_run,
                    verbose=verbose,
                )
            except McpError as exc:
                if not dry_run:
                    write_lockfile(project_root, new_lockfile)
                raise click.ClickException(str(exc)) from exc

        for server_name, target_paths in mcp_result.items():
            new_lockfile.mcp.append(
                McpLockEntry(name=server_name, targets=target_paths)
            )
            mcp_count += 1

    # 7. Write lockfile
    if not dry_run:
        write_lockfile(project_root, new_lockfile)

    # 8. Summary
    elapsed = time.monotonic() - start_time
    target_count = len(config.targets)
    console.print(
        f"\n{counts.get('skill', 0)} skills, {counts.get('command', 0)} commands, "
        f"{counts.get('agent', 0)} agents, {mcp_count} MCP servers "
        f"synced to {target_count} targets in {elapsed:.2f}s."
    )


@main.command()
@click.option(
    "--config",
    "config_path",
    default="./agpack.yml",
    type=click.Path(),
    help="Path to config file.",
)
def status(config_path: str) -> None:
    """Show the current state of installed resources vs the config."""
    cfg_path = Path(config_path).resolve()
    project_root = cfg_path.parent

    try:
        config = load_config(cfg_path)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    lockfile = read_lockfile(project_root)

    # Build lookup from lockfile
    installed_map: dict[str, InstalledEntry] = {}
    if lockfile:
        for entry in lockfile.installed:
            installed_map[entry.identity] = entry

    mcp_map: dict[str, McpLockEntry] = {}
    if lockfile:
        for mcp_entry in lockfile.mcp:
            mcp_map[mcp_entry.name] = mcp_entry

    resource_sections = [
        ("Skills", config.skills),
        ("Commands", config.commands),
        ("Agents", config.agents),
    ]
    for label, deps in resource_sections:
        console.print(f"\n{label}:" if label != "Skills" else f"{label}:")
        if not deps:
            console.print("  (none configured)")
        else:
            for dep in deps:
                installed = installed_map.get(dep.identity)
                if installed:
                    short_ref = installed.resolved_ref[:7]
                    console.print(f"  ✓ {dep.name:<20} ({dep.url} @ {short_ref})")
                else:
                    console.print(f"  ✗ {dep.name:<20} (not yet synced)")

    # MCP
    console.print("\nMCP:")
    if not config.mcp:
        console.print("  (none configured)")
    else:
        for server in config.mcp:
            mcp_installed = mcp_map.get(server.name)
            if mcp_installed and mcp_installed.targets:
                targets_str = ", ".join(mcp_installed.targets)
                console.print(f"  ✓ {server.name:<20} → {targets_str}")
            else:
                console.print(f"  ✗ {server.name:<20} (not yet synced)")


@main.command()
@click.option(
    "--config",
    "config_path",
    default="./agpack.yml",
    type=click.Path(),
    help="Path to write the config file.",
)
def init(config_path: str) -> None:
    """Scaffold a new agpack.yml."""
    path = Path(config_path).resolve()
    if path.exists():
        console.print(f"{path.name} already exists — doing nothing.")
        return

    template = """\
name: my-project
version: 0.1.0

targets:
  # - claude
  # - opencode
  # - codex
  # - cursor
  # - copilot

dependencies:
  skills:
    # - url: https://github.com/owner/repo
    #   path: skills/my-skill
    #   ref: v1.0.0

  commands:
    # - url: https://github.com/owner/repo
    #   path: commands/my-command.md

  agents:
    # - url: https://github.com/owner/repo
    #   path: agents/my-agent.md

  mcp:
    # - name: my-server
    #   command: npx
    #   args: ["-y", "@example/mcp-server"]
"""
    path.write_text(template, encoding="utf-8")
    console.print(f"Created {path}")
