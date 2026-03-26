"""Click entrypoints for the agpack CLI."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import click
from rich.progress import Progress
from rich.progress import TaskID
from rich.rule import Rule
from rich.table import Table

from agpack import __version__
from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import DependencySource
from agpack.config import load_config
from agpack.deployer import cleanup_deployed_files
from agpack.deployer import deploy_single_agent
from agpack.deployer import deploy_single_command
from agpack.deployer import deploy_single_skill
from agpack.deployer import detect_agent_items
from agpack.deployer import detect_command_items
from agpack.deployer import detect_skill_items
from agpack.display import console
from agpack.display import create_sync_progress
from agpack.envsubst import resolve_config
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


def _source_file_count(deployed: list[str]) -> int:
    """Count unique source files from a deployed-paths list.

    Deployed paths look like ``<target_dir>/<resource>/<file…>`` where
    ``<target_dir>`` is always two components (e.g. ``.claude/skills``).
    Stripping those two components and deduplicating gives the number of
    unique source files, regardless of how many targets received copies.
    """
    if not deployed:
        return 0
    return len({Path(f).parts[2:] for f in deployed})


@dataclass
class SyncResult:
    """Outcome of syncing one resource type."""

    count: int = 0
    verbose_lines: list[str] = field(default_factory=list)


def _sync_resource_type(
    deps: list[DependencySource],
    detect_fn: Callable[[FetchResult], list[tuple[str, Path]]],
    deploy_item_fn: Callable[[str, Path, list[str], Path, bool, bool], list[str]],
    resource_type: str,
    config: AgpackConfig,
    project_root: Path,
    new_lockfile: Lockfile,
    progress: Progress,
    *,
    dry_run: bool,
    verbose: bool,
) -> SyncResult:
    """Fetch and deploy a list of dependencies of a single type.

    On error, writes a partial lockfile (with what has been synced so far)
    before raising ClickException.
    """
    if not deps:
        return SyncResult()

    # Phase 1: parallel fetch — collect all results and errors
    results: list[tuple[DependencySource, FetchResult]] = []
    errors: list[str] = []

    # Add a progress row per dependency
    task_ids: dict[str, TaskID] = {}
    for dep in deps:
        task_ids[dep.identity] = progress.add_task(
            f"{resource_type} [bold]{dep.name}[/bold]",
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
    sync = SyncResult()
    for dep, result in results:
        tid = task_ids[dep.identity]

        try:
            items = detect_fn(result)
        except Exception as exc:
            progress.update(tid, completed=2, icon="[red]✗[/red]", detail=str(exc))
            cleanup_fetch(result)
            if not dry_run:
                write_lockfile(project_root, new_lockfile)
            raise click.ClickException(
                f"Error deploying {resource_type} '{dep.name}': {exc}"
            ) from exc

        is_expanded = len(items) > 1

        # Add sub-rows for expanded dependencies
        sub_task_ids: list[TaskID] = []
        if is_expanded:
            for i, (item_name, _) in enumerate(items):
                is_last = i == len(items) - 1
                branch = "└── " if is_last else "├── "
                sub_tid = progress.add_task(
                    f"[dim]    {branch}[/dim]{item_name}",
                    total=1,
                    icon=" ",
                    detail="",
                )
                sub_task_ids.append(sub_tid)

        # Deploy each item individually
        all_deployed: list[str] = []
        for idx, (item_name, item_path) in enumerate(items):
            try:
                files = deploy_item_fn(
                    item_name,
                    item_path,
                    config.targets,
                    project_root,
                    dry_run,
                    False,
                )
            except Exception as exc:
                if is_expanded:
                    progress.update(
                        sub_task_ids[idx],
                        completed=1,
                        icon="[red]✗[/red]",
                        detail=str(exc),
                    )
                progress.update(tid, completed=2, icon="[red]✗[/red]", detail=str(exc))
                cleanup_fetch(result)
                if not dry_run:
                    write_lockfile(project_root, new_lockfile)
                raise click.ClickException(
                    f"Error deploying {resource_type} '{dep.name}': {exc}"
                ) from exc

            all_deployed.extend(files)

            if is_expanded:
                n = _source_file_count(files)
                progress.update(
                    sub_task_ids[idx],
                    completed=1,
                    icon="[green]✓[/green]",
                    detail=f"Copied {n} {'file' if n == 1 else 'files'}",
                )

        # Update parent row — show source file count, not total across targets
        src_files = _source_file_count(all_deployed)
        if is_expanded:
            item_label = f"{resource_type}s" if len(items) != 1 else resource_type
            detail = f"Copied {len(items)} {item_label}"
            sync.count += len(items)
        else:
            file_label = "file" if src_files == 1 else "files"
            detail = f"Copied {src_files} {file_label}"
            sync.count += 1

        progress.update(tid, completed=2, icon="[green]✓[/green]", detail=detail)

        if verbose:
            sync.verbose_lines.extend(f"  {f}" for f in all_deployed)

        new_lockfile.installed.append(
            InstalledEntry(
                url=dep.url,
                path=dep.path,
                resolved_ref=result.resolved_ref,
                type=resource_type,
                deployed_files=all_deployed,
            )
        )
        cleanup_fetch(result)

    return sync


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

    # 2. Resolve ${VAR} references in config values
    try:
        resolve_config(config, project_root, verbose=verbose)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    # 3. Read existing lockfile
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

    resource_types = [
        (config.skills, detect_skill_items, deploy_single_skill, "skill"),
        (config.commands, detect_command_items, deploy_single_command, "command"),
        (config.agents, detect_agent_items, deploy_single_agent, "agent"),
    ]

    all_verbose_lines: list[str] = []

    with create_sync_progress() as progress:
        for deps, detect_fn, deploy_item_fn, resource_type in resource_types:
            sync = _sync_resource_type(
                deps,
                detect_fn,
                deploy_item_fn,
                resource_type,
                config,
                project_root,
                new_lockfile,
                progress,
                dry_run=dry_run,
                verbose=verbose,
            )
            counts[resource_type] = sync.count
            all_verbose_lines.extend(sync.verbose_lines)

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
    items = [
        ("skills", "skill"),
        ("commands", "command"),
        ("agents", "agent"),
    ]
    parts = [f"[bold]{counts.get(k, 0)}[/bold] {name}" for name, k in items]
    parts.append(f"[bold]{mcp_count}[/bold] MCP servers")
    summary = ", ".join(parts)
    targets = f"[bold]{target_count}[/bold] targets"
    console.print()
    console.print(Rule(f"{summary} → {targets} [dim]({elapsed:.2f}s)[/dim]"))


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
        table = Table(
            title=label,
            title_style="bold",
            show_header=False,
            box=None,
            padding=(0, 1),
            title_justify="left",
        )
        table.add_column(style="bold", no_wrap=True)
        table.add_column(style="dim")
        if not deps:
            table.add_row("[dim]no dependencies configured[/dim]")
        else:
            for dep in deps:
                installed = installed_map.get(dep.identity)
                if installed:
                    short_ref = installed.resolved_ref[:7]
                    table.add_row(
                        f"[green]✓[/green] {dep.name}",
                        f"{dep.url} @ {short_ref}",
                    )
                else:
                    table.add_row(
                        f"[red]✗[/red] {dep.name}",
                        "not yet synced",
                    )
        console.print(table)
        console.print()

    # MCP
    table = Table(
        title="MCP Servers",
        title_style="bold",
        show_header=False,
        box=None,
        padding=(0, 1),
        title_justify="left",
    )
    table.add_column(style="bold", no_wrap=True)
    table.add_column(style="dim")
    if not config.mcp:
        table.add_row("[dim]no servers configured[/dim]")
    else:
        for server in config.mcp:
            mcp_installed = mcp_map.get(server.name)
            if mcp_installed and mcp_installed.targets:
                targets_str = ", ".join(mcp_installed.targets)
                table.add_row(
                    f"[green]✓[/green] {server.name}",
                    f"→ {targets_str}",
                )
            else:
                table.add_row(
                    f"[red]✗[/red] {server.name}",
                    "not yet synced",
                )
    console.print(table)


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
    # Point to a single skill folder:
    # - url: https://github.com/owner/repo
    #   path: skills/my-skill
    #   ref: v1.0.0
    # Or to a directory of skill folders (each subfolder is deployed separately):
    # - url: https://github.com/owner/repo
    #   path: skills

  commands:
    # Point to a single file or a directory of command files:
    # - url: https://github.com/owner/repo
    #   path: commands/my-command.md

  agents:
    # Point to a single file or a directory of agent files:
    # - url: https://github.com/owner/repo
    #   path: agents/my-agent.md

  mcp:
    # - name: my-server
    #   command: npx
    #   args: ["-y", "@example/mcp-server"]
    #   env:
    #     API_KEY: ${API_KEY}   # resolved from .env or shell environment
"""
    path.write_text(template, encoding="utf-8")
    console.print(f"[green]✓[/green] Created [bold]{path.name}[/bold]")
