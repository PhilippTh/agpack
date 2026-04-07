"""Click entrypoints for the agpack CLI."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Callable

import click
from rich.progress import Progress
from rich.progress import TaskID
from rich.rule import Rule
from rich.table import Table

from agpack import __version__
from agpack.cleanup import cleanup_deployed_files
from agpack.cleanup import cleanup_mcp_server
from agpack.cleanup import cleanup_rule_append_targets
from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import DependencySource
from agpack.config import load_resolved_config
from agpack.display import console
from agpack.display import create_sync_progress
from agpack.fetcher import FetchError
from agpack.fetcher import FetchResult
from agpack.fetcher import cleanup_fetch
from agpack.fetcher import fetch_dependency
from agpack.lockfile import InstalledEntry
from agpack.lockfile import Lockfile
from agpack.lockfile import McpLockEntry
from agpack.lockfile import read_lockfile
from agpack.lockfile import write_lockfile
from agpack.resolvers import ResolveError
from agpack.resolvers import resolve_agents
from agpack.resolvers import resolve_commands
from agpack.resolvers import resolve_mcp
from agpack.resolvers import resolve_rules
from agpack.resolvers import resolve_rules_append
from agpack.resolvers import resolve_skills
from agpack.writer import WriteError
from agpack.writer import WriteOp
from agpack.writer import execute_write_ops

_MAX_FETCH_WORKERS = 8

# ---------------------------------------------------------------------------
# Resolver registry — maps resource type name to its resolver function.
# Each resolver takes (FetchResult, targets) and returns a list of WriteOps.
# Rules are special-cased because they also return collected bodies.
# ---------------------------------------------------------------------------

_SIMPLE_RESOLVERS: dict[str, Callable[[FetchResult, list[str]], list[WriteOp]]] = {
    "skill": resolve_skills,
    "command": resolve_commands,
    "agent": resolve_agents,
}


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
class _DepSyncResult:
    """Per-dependency result after resolve + write."""

    items_count: int = 0
    deployed_files: list[str] = field(default_factory=list)


def _fetch_and_resolve(  # noqa: C901
    deps: list[DependencySource],
    resource_type: str,
    resolve_fn: Callable[[FetchResult, list[str]], list[WriteOp]],
    config: AgpackConfig,
    project_root: Path,
    new_lockfile: Lockfile,
    progress: Progress,
    *,
    dry_run: bool,
    verbose: bool,
    all_rule_bodies: list[tuple[str, str]] | None = None,
) -> int:
    """Fetch, resolve, and write dependencies of a single resource type.

    For rules, also populates *all_rule_bodies* with collected
    (name, body) pairs for the append-based targets.

    Returns the count of synced items.
    """
    if not deps:
        return 0

    # Phase 1: parallel fetch
    results: list[tuple[DependencySource, FetchResult]] = []
    errors: list[str] = []

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

    # Phase 2: error handling
    if errors:
        for _, result in results:
            cleanup_fetch(result)
        if not dry_run:
            write_lockfile(project_root, new_lockfile)
        raise click.ClickException("\n".join([f"Failed to fetch {len(errors)} {resource_type}(s):"] + errors))

    # Phase 3: resolve + write per dependency
    total_count = 0

    for dep, result in results:
        tid = task_ids[dep.identity]

        try:
            # Resolve: produce WriteOps from fetched content
            if resource_type == "rule" and all_rule_bodies is not None:
                ops, bodies = resolve_rules(result, config.targets)
                all_rule_bodies.extend(bodies)
                # Count items by number of rule bodies (one per detected rule file)
                items_count = len(bodies)
            else:
                ops = resolve_fn(result, config.targets)
                # Estimate items: for skills, count unique second-level paths;
                # for commands/agents, count unique filenames across targets
                items_count = _count_items_from_ops(ops, resource_type)
        except (ResolveError, Exception) as exc:
            progress.update(tid, completed=2, icon="[red]✗[/red]", detail=str(exc))
            cleanup_fetch(result)
            if not dry_run:
                write_lockfile(project_root, new_lockfile)
            raise click.ClickException(f"Error deploying {resource_type} '{dep.name}': {exc}") from exc

        is_expanded = items_count > 1

        # Add sub-rows for expanded dependencies
        sub_task_ids: list[TaskID] = []
        if is_expanded:
            item_names = _extract_item_names(ops, resource_type)
            for i, item_name in enumerate(item_names):
                is_last = i == len(item_names) - 1
                branch = "└── " if is_last else "├── "
                sub_tid = progress.add_task(
                    f"[dim]    {branch}[/dim]{item_name}",
                    total=1,
                    icon=" ",
                    detail="",
                )
                sub_task_ids.append(sub_tid)

        # Execute write ops
        try:
            deployed_files = execute_write_ops(
                ops,
                project_root,
                dry_run=dry_run,
                verbose=False,
            )
        except (WriteError, Exception) as exc:
            progress.update(tid, completed=2, icon="[red]✗[/red]", detail=str(exc))
            cleanup_fetch(result)
            if not dry_run:
                write_lockfile(project_root, new_lockfile)
            raise click.ClickException(f"Error deploying {resource_type} '{dep.name}': {exc}") from exc

        # Update sub-rows if expanded
        if is_expanded and sub_task_ids:
            # Group deployed files by item name for sub-row progress
            item_names = _extract_item_names(ops, resource_type)
            files_per_item = _group_deployed_by_item(deployed_files, resource_type)
            for idx, item_name in enumerate(item_names):
                if idx < len(sub_task_ids):
                    item_files = files_per_item.get(item_name, [])
                    n = _source_file_count(item_files) if item_files else 0
                    progress.update(
                        sub_task_ids[idx],
                        completed=1,
                        icon="[green]✓[/green]",
                        detail=f"Copied {n} {'file' if n == 1 else 'files'}",
                    )

        # Update parent row
        src_files = _source_file_count(deployed_files)
        if is_expanded:
            item_label = f"{resource_type}s" if items_count != 1 else resource_type
            detail = f"Copied {items_count} {item_label}"
            total_count += items_count
        else:
            file_label = "file" if src_files == 1 else "files"
            detail = f"Copied {src_files} {file_label}"
            total_count += 1

        progress.update(tid, completed=2, icon="[green]✓[/green]", detail=detail)

        # Record in lockfile
        new_lockfile.installed.append(
            InstalledEntry(
                url=dep.url,
                path=dep.path,
                resolved_ref=result.resolved_ref,
                type=resource_type,
                deployed_files=deployed_files,
            )
        )
        cleanup_fetch(result)

    return total_count


def _count_items_from_ops(ops: list[WriteOp], resource_type: str) -> int:
    """Count the number of distinct items from a list of write ops.

    For skills: count unique item names (3rd path component).
    For commands/agents: count unique filenames across targets.
    """
    if not ops:
        return 0

    if resource_type == "skill":
        # Skill dst_rel looks like: .claude/skills/<name>/...
        names = set()
        for op in ops:
            parts = Path(op.dst_rel).parts
            if len(parts) >= 3:
                names.add(parts[2])
        return max(len(names), 1)

    # Commands/agents: dst_rel looks like .claude/commands/<filename>
    names = set()
    for op in ops:
        parts = Path(op.dst_rel).parts
        if len(parts) >= 3:
            names.add(parts[2])
    return max(len(names), 1)


def _extract_item_names(ops: list[WriteOp], resource_type: str) -> list[str]:
    """Extract unique, ordered item names from write ops for progress display."""
    seen: set[str] = set()
    names: list[str] = []

    for op in ops:
        parts = Path(op.dst_rel).parts
        if len(parts) >= 3:
            name = parts[2]
            if name not in seen:
                seen.add(name)
                names.append(name)

    return names


def _group_deployed_by_item(deployed_files: list[str], resource_type: str) -> dict[str, list[str]]:
    """Group deployed file paths by their item name (3rd path component)."""
    groups: dict[str, list[str]] = {}
    for f in deployed_files:
        parts = Path(f).parts
        if len(parts) >= 3:
            name = parts[2]
            groups.setdefault(name, []).append(f)
    return groups


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
@click.option(
    "--no-global",
    is_flag=True,
    help="Ignore the global config file.",
)
def sync(dry_run: bool, config_path: str, verbose: bool, no_global: bool) -> None:  # noqa: C901
    """Fetch all dependencies and deploy to all target directories."""
    cfg_path = Path(config_path).resolve()
    project_root = cfg_path.parent
    start_time = time.monotonic()

    # 1. Load, merge global, and resolve env vars
    try:
        config = load_resolved_config(cfg_path, no_global=no_global, verbose=verbose)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    # 2. Read existing lockfile
    old_lockfile = read_lockfile(project_root)

    # 5. Build set of current dependency identities
    current_identities: set[str] = set()
    for dep in [*config.skills, *config.commands, *config.agents, *config.rules]:
        current_identities.add(dep.identity)

    current_mcp_names = {m.name for m in config.mcp}

    # 6. Clean up removed dependencies
    removed_deps = [e for e in (old_lockfile.installed if old_lockfile else []) if e.identity not in current_identities]
    removed_had_rules = False
    for entry in removed_deps:
        if verbose or dry_run:
            console.print(f"Removing {entry.type} '{entry.identity}'...")
        if entry.type == "rule":
            removed_had_rules = True
        cleanup_deployed_files(entry.deployed_files, project_root, dry_run=dry_run, verbose=verbose)

    # If all rules were removed, clean up managed sections from append targets
    if removed_had_rules and not config.rules:
        cleanup_rule_append_targets(config.targets, project_root, dry_run=dry_run, verbose=verbose)

    # 7. Clean up removed MCP servers
    removed_mcp = [e for e in (old_lockfile.mcp if old_lockfile else []) if e.name not in current_mcp_names]
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

    # 8. Fetch, resolve, and write — all resource types
    new_lockfile = Lockfile()
    counts: dict[str, int] = {}
    all_rule_bodies: list[tuple[str, str]] = []

    resource_types: list[tuple[str, list[DependencySource]]] = [
        ("skill", config.skills),
        ("command", config.commands),
        ("agent", config.agents),
        ("rule", config.rules),
    ]

    with create_sync_progress() as progress:
        for resource_type, deps in resource_types:
            resolve_fn = _SIMPLE_RESOLVERS.get(resource_type, resolve_skills)
            count = _fetch_and_resolve(
                deps,
                resource_type,
                resolve_fn,
                config,
                project_root,
                new_lockfile,
                progress,
                dry_run=dry_run,
                verbose=verbose,
                all_rule_bodies=all_rule_bodies if resource_type == "rule" else None,
            )
            counts[resource_type] = count

    # 9. Write append-based rule targets (uniform — no more finalize())
    if all_rule_bodies:
        append_ops = resolve_rules_append(all_rule_bodies, config.targets)
        try:
            append_deployed = execute_write_ops(
                append_ops,
                project_root,
                dry_run=dry_run,
                verbose=verbose,
            )
        except WriteError as exc:
            if not dry_run:
                write_lockfile(project_root, new_lockfile)
            raise click.ClickException(str(exc)) from exc

        # Track append-target files in the lockfile under the last rule entry
        # so they get cleaned up properly
        if append_deployed and new_lockfile.installed:
            for entry in reversed(new_lockfile.installed):
                if entry.type == "rule":
                    entry.deployed_files.extend(append_deployed)
                    break

    # 10. MCP servers — now resolved uniformly into WriteOps
    mcp_count = 0
    if config.mcp:
        with console.status("Deploying MCP servers..."):
            mcp_ops = resolve_mcp(config.mcp, config.targets)
            try:
                mcp_deployed = execute_write_ops(
                    mcp_ops,
                    project_root,
                    dry_run=dry_run,
                    verbose=verbose,
                )
            except WriteError as exc:
                if not dry_run:
                    write_lockfile(project_root, new_lockfile)
                raise click.ClickException(str(exc)) from exc

        # Build MCP lockfile entries (group deployed paths by server name)
        _record_mcp_lockfile(config, mcp_deployed, new_lockfile)
        mcp_count = len(config.mcp)

    # 11. Write lockfile
    if not dry_run:
        write_lockfile(project_root, new_lockfile)

    # 12. Summary
    elapsed = time.monotonic() - start_time
    target_count = len(config.targets)
    summary_items = [
        ("skills", "skill"),
        ("commands", "command"),
        ("agents", "agent"),
        ("rules", "rule"),
    ]
    parts = [f"[bold]{counts.get(k, 0)}[/bold] {name}" for name, k in summary_items]
    parts.append(f"[bold]{mcp_count}[/bold] MCP servers")
    summary = ", ".join(parts)
    targets = f"[bold]{target_count}[/bold] targets"
    console.print()
    console.print(Rule(f"{summary} → {targets} [dim]({elapsed:.2f}s)[/dim]"))


def _record_mcp_lockfile(
    config: AgpackConfig,
    mcp_deployed: list[str],
    new_lockfile: Lockfile,
) -> None:
    """Record MCP server entries in the lockfile.

    Groups deployed config file paths by server name so that cleanup
    knows which files to remove when a server is removed.
    """
    # Each server writes to one config file per target. Since we process
    # servers × targets in order, we can reconstruct the mapping.
    from agpack.targets import MCP_TARGETS

    for server in config.mcp:
        target_paths: list[str] = []
        for target in config.targets:
            target_cfg = MCP_TARGETS.get(target)
            if target_cfg is None:
                continue
            if target_cfg.config_path in mcp_deployed:
                target_paths.append(target_cfg.config_path)
        new_lockfile.mcp.append(McpLockEntry(name=server.name, targets=target_paths))


@main.command()
@click.option(
    "--config",
    "config_path",
    default="./agpack.yml",
    type=click.Path(),
    help="Path to config file.",
)
@click.option(
    "--no-global",
    is_flag=True,
    help="Ignore the global config file.",
)
def status(config_path: str, no_global: bool) -> None:  # noqa: C901
    """Show the current state of installed resources vs the config."""
    cfg_path = Path(config_path).resolve()
    project_root = cfg_path.parent

    try:
        config = load_resolved_config(cfg_path, no_global=no_global)
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
        ("Rules", config.rules),
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
@click.option(
    "--global",
    "is_global",
    is_flag=True,
    help="Scaffold the global config at ~/.config/agpack/agpack.yml.",
)
def init(config_path: str, is_global: bool) -> None:
    """Scaffold a new agpack.yml."""
    from agpack.config import _resolve_global_config_path

    if is_global:
        path = _resolve_global_config_path()
        if path.exists():
            console.print(f"{path} already exists — doing nothing.")
            return

        path.parent.mkdir(parents=True, exist_ok=True)

        template = """\
# Global agpack config — dependencies here are included in every project.
# Override per-project with 'global: false' in your project agpack.yml,
# or run agpack sync --no-global.

dependencies:
  skills:
    # Shared skills available in all projects:
    # - url: https://github.com/owner/repo
    #   path: skills/my-skill
    #   ref: v1.0.0

  commands:
    # Shared commands available in all projects:
    # - url: https://github.com/owner/repo
    #   path: commands/my-command.md

  agents:
    # Shared agents available in all projects:
    # - url: https://github.com/owner/repo
    #   path: agents/my-agent.md

  rules:
    # Shared rules available in all projects:
    # - url: https://github.com/owner/repo
    #   path: rules/my-rule.md

  mcp:
    # Shared MCP servers available in all projects:
    # - name: my-server
    #   command: npx
    #   args: ["-y", "@example/mcp-server"]
    #   env:
    #     API_KEY: ${API_KEY}   # resolved from .env or shell environment
"""
        path.write_text(template, encoding="utf-8")
        console.print(f"[green]✓[/green] Created [bold]{path}[/bold]")
        return

    path = Path(config_path).resolve()
    if path.exists():
        console.print(f"{path.name} already exists — doing nothing.")
        return

    template = """\
# Set to false to ignore the global config (~/.config/agpack/agpack.yml):
# global: false

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
    # Multiple URLs (tried in order, e.g. HTTPS + SSH fallback):
    # - url:
    #     - https://github.com/owner/repo
    #     - git@github.com:owner/repo.git
    #   path: skills/my-skill
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

  rules:
    # Point to a single rule file or a directory of rule files:
    # - url: https://github.com/owner/repo
    #   path: rules/my-rule.md

  mcp:
    # - name: my-server
    #   command: npx
    #   args: ["-y", "@example/mcp-server"]
    #   env:
    #     API_KEY: ${API_KEY}   # resolved from .env or shell environment
"""
    path.write_text(template, encoding="utf-8")
    console.print(f"[green]✓[/green] Created [bold]{path.name}[/bold]")
