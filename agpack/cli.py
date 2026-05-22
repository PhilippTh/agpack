"""Click entrypoints for the agpack CLI."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from dataclasses import dataclass
from dataclasses import field
from importlib.resources import files as importlib_files
from pathlib import Path
from typing import Any

import click
import yaml
from rich.progress import Progress
from rich.progress import TaskID
from rich.rule import Rule
from rich.table import Table

from agpack import __version__
from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import DependencySource
from agpack.config import GlobalConfig
from agpack.config import load_config
from agpack.config import load_global_config
from agpack.config import merge_configs
from agpack.config import resolve_global_config_path
from agpack.deployer import cleanup_deployed_files
from agpack.deployer import cleanup_orphaned_edits
from agpack.deployer import deploy_item
from agpack.deployer import detect_items
from agpack.deployer import sync_edit_resource
from agpack.display import console
from agpack.display import create_sync_progress
from agpack.envsubst import resolve_config
from agpack.fetcher import FetchError
from agpack.fetcher import FetchResult
from agpack.fetcher import cleanup_fetch
from agpack.fetcher import fetch_dependency
from agpack.kinds import EditFileError
from agpack.kinds import Patch
from agpack.lockfile import AppliedPatch
from agpack.lockfile import EditLockEntry
from agpack.lockfile import InstalledEntry
from agpack.lockfile import Lockfile
from agpack.lockfile import find_removed_dependencies
from agpack.lockfile import read_lockfile
from agpack.lockfile import write_lockfile
from agpack.registry import list_builtins
from agpack.registry import load_builtin
from agpack.target_schema import TargetDef
from agpack.target_schema import TargetSchemaError

_MAX_FETCH_WORKERS = 8


def _source_file_count(item_path: Path) -> int:
    """Count source files in a deploy item.

    A file item is 1; a directory item is the number of non-``.git``
    files in its tree.  Counting on the source side avoids any
    assumption about target path depth — user-defined targets can put
    deployments at any nesting level.
    """
    if item_path.is_file():
        return 1
    return sum(
        1
        for f in item_path.rglob("*")
        if f.is_file()
        and not any(p.startswith(".git") for p in f.relative_to(item_path).parts)
    )


@dataclass
class SyncResult:
    """Outcome of syncing one resource type."""

    count: int = 0
    verbose_lines: list[str] = field(default_factory=list)


def _sync_resource_type(
    deps: list[DependencySource],
    resource_type: str,
    target_defs: list[TargetDef],
    project_root: Path,
    new_lockfile: Lockfile,
    progress: Progress,
    *,
    dry_run: bool,
    verbose: bool,
) -> SyncResult:
    """Fetch and deploy a list of copy-kind dependencies.

    The resource type's kind (``copy-directory`` / ``copy-file``) is
    looked up from any target that declares it — all targets sharing
    the resource type share the kind (validated by
    :func:`_resource_kinds`). On error, writes a partial lockfile
    (with what has been synced so far) before raising ClickException.
    """
    if not deps:
        return SyncResult()

    # Pick a representative resource for the detect phase — any target
    # supporting this resource type works, since _resource_kinds
    # validated they all use the same kind.
    detect_resource = next(
        target.resources[resource_type]
        for target in target_defs
        if resource_type in target.resources
    )

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
            "\n".join(
                [f"Failed to fetch {len(errors)} {resource_type} dep(s):"] + errors
            )
        )

    # Phase 3: sequential deploy — update progress rows, collect verbose output
    sync = SyncResult()
    for dep, result in results:
        tid = task_ids[dep.identity]

        try:
            items = detect_items(result, detect_resource, resource_type)
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
                files = deploy_item(
                    item_name,
                    item_path,
                    resource_type,
                    target_defs,
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
                n = _source_file_count(item_path)
                progress.update(
                    sub_task_ids[idx],
                    completed=1,
                    icon="[green]✓[/green]",
                    detail=f"Copied {n} {'file' if n == 1 else 'files'}",
                )

        # Update parent row — count source files (not multiplied across targets)
        if is_expanded:
            detail = f"Copied {len(items)} {resource_type}"
            sync.count += len(items)
        else:
            src_files = _source_file_count(items[0][1])
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


def _sync_edit_resource(
    resource_type: str,
    patches: list[Patch],
    applied_old: list[AppliedPatch],
    target_defs: list[TargetDef],
    project_root: Path,
    new_lockfile: Lockfile,
    env_vars: dict[str, str],
    *,
    dry_run: bool,
    verbose: bool,
) -> int:
    """Reconcile edit-file patches and record the new applied set.

    Diffs ``patches`` against ``applied_old`` per file and only
    touches each file once. Returns the count for the summary line
    (number of currently-desired patches, regardless of whether the
    file was rewritten).
    """
    if not patches and not applied_old:
        return 0
    try:
        applied_new = sync_edit_resource(
            resource_type,
            desired=patches,
            applied_old=applied_old,
            targets=target_defs,
            project_root=project_root,
            env_vars=env_vars,
            dry_run=dry_run,
            verbose=verbose,
        )
    except EditFileError as exc:
        if not dry_run:
            write_lockfile(project_root, new_lockfile)
        raise click.ClickException(str(exc)) from exc
    if applied_new:
        new_lockfile.edits.append(
            EditLockEntry(resource_type=resource_type, applied=applied_new)
        )
    return len(patches)


def _resource_kinds(targets: list[TargetDef]) -> dict[str, str]:
    """Build the resource-type → kind map across all configured targets.

    Raises:
        click.ClickException: If two targets declare the same resource
            type name with different kinds. Resource type names are
            cross-target identifiers; agpack will not treat
            ``commands`` as copy-directory for one target and
            copy-file for another.
    """
    kinds: dict[str, str] = {}
    sources: dict[str, str] = {}
    for idx, target in enumerate(targets):
        target_label = f"target #{idx}"
        for rt, resource in target.resources.items():
            seen = kinds.get(rt)
            if seen is None:
                kinds[rt] = resource.kind
                sources[rt] = target_label
            elif seen != resource.kind:
                raise click.ClickException(
                    f"Resource '{rt}' has conflicting kinds across "
                    f"configured targets: '{seen}' (from {sources[rt]}) vs "
                    f"'{resource.kind}' (from {target_label}). All targets "
                    f"declaring the same resource type must agree on its kind."
                )
    return kinds


def _resolve_targets(config: AgpackConfig) -> list[TargetDef]:
    """Resolve ``config.targets`` to TargetDef objects.

    Precedence: ``config.target_definitions`` (already merged from
    project + global) → bundled built-in manifests.  Unknown names
    raise a ``ClickException`` listing both pools.
    """
    resolved: list[TargetDef] = []
    for name in config.targets:
        if name in config.target_definitions:
            resolved.append(config.target_definitions[name])
            continue
        try:
            resolved.append(load_builtin(name))
        except TargetSchemaError as exc:
            builtins = ", ".join(list_builtins())
            user_defs = ", ".join(sorted(config.target_definitions)) or "(none)"
            raise click.ClickException(
                f"Unknown target '{name}'.\n"
                f"  Built-in targets: {builtins}\n"
                f"  Your target_definitions: {user_defs}\n"
                "Add an entry under 'target_definitions' in agpack.yml to "
                "define a custom target."
            ) from exc
    return resolved


def _load_and_merge_global(
    config: AgpackConfig,
    *,
    verbose: bool = False,
) -> tuple[AgpackConfig, GlobalConfig | None]:
    """Load the global config and merge it into *config*.

    Returns the (possibly merged) config and the raw global config
    (needed later for ``.env`` resolution).  If the global config
    doesn't exist or is disabled, the original config is returned
    unchanged together with ``None``.
    """
    try:
        global_cfg = load_global_config()
    except ConfigError as exc:
        raise click.ClickException(f"Global config error: {exc}") from exc

    if global_cfg is not None:
        if verbose:
            console.print("  Loaded global config")
        config = merge_configs(config, global_cfg)

    return config, global_cfg


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
def sync(dry_run: bool, config_path: str, verbose: bool, no_global: bool) -> None:
    """Fetch all dependencies and deploy to all target directories."""
    cfg_path = Path(config_path).resolve()
    project_root = cfg_path.parent
    start_time = time.monotonic()

    # 1. Load and validate config
    try:
        config = load_config(cfg_path)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    # 2. Load and merge global config
    global_cfg: GlobalConfig | None = None
    if not no_global and config.use_global:
        config, global_cfg = _load_and_merge_global(config, verbose=verbose)

    # 3. Resolve ${VAR} references in fetch dependencies; patches
    # are resolved per-target at apply time using `env_vars` plus the
    # target's own `vars`.
    try:
        env_vars = resolve_config(
            config, project_root, global_config=global_cfg, verbose=verbose
        )
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    # 4. Resolve target names to manifests + build the kind map
    target_defs = _resolve_targets(config)
    resource_kinds = _resource_kinds(target_defs)

    # 5. Read existing lockfile and build the set of current
    # fetch-dependency identities (copy kinds only — patches are
    # tracked separately under lockfile.edits).
    old_lockfile = read_lockfile(project_root)
    current_identities: set[str] = set()
    for deps_list in config.dependencies.values():
        for dep in deps_list:
            if isinstance(dep, DependencySource):
                current_identities.add(dep.identity)

    # 6. Clean up removed copy-kind dependencies.
    removed_deps = find_removed_dependencies(old_lockfile, current_identities)
    for entry in removed_deps:
        if verbose or dry_run:
            console.print(f"Removing {entry.type} '{entry.identity}'...")
        cleanup_deployed_files(
            entry.deployed_files, project_root, dry_run=dry_run, verbose=verbose
        )

    # 7. Index old applied edits by resource type. We don't unapply
    # them here — sync_edit_resource diffs against this and touches
    # each file at most once. Files only get written when their text
    # actually changes (combined with tomlkit for TOML, comments and
    # key ordering on untouched sections survive across syncs).
    old_applied_by_rt: dict[str, list[AppliedPatch]] = {}
    if old_lockfile is not None:
        for edit in old_lockfile.edits:
            old_applied_by_rt[edit.resource_type] = list(edit.applied)

    # 8. Fetch and deploy dependencies in YAML order. Copy kinds go
    # through fetch+detect+deploy; edit-file kinds run the diff-based
    # sync against the lockfile's prior applied state.
    new_lockfile = Lockfile()
    counts: dict[str, int] = {}
    all_verbose_lines: list[str] = []

    with create_sync_progress() as progress:
        for resource_type in config.dependencies:
            kind = resource_kinds.get(resource_type)
            if kind is None:
                # Configured but no target supports it — silently skip.
                continue
            entries = config.dependencies[resource_type]
            if kind == "edit-file":
                counts[resource_type] = _sync_edit_resource(
                    resource_type,
                    [e for e in entries if isinstance(e, Patch)],
                    old_applied_by_rt.pop(resource_type, []),
                    target_defs,
                    project_root,
                    new_lockfile,
                    env_vars,
                    dry_run=dry_run,
                    verbose=verbose,
                )
                continue
            # Copy kind.
            fetch_entries = [e for e in entries if isinstance(e, DependencySource)]
            sync = _sync_resource_type(
                fetch_entries,
                resource_type,
                target_defs,
                project_root,
                new_lockfile,
                progress,
                dry_run=dry_run,
                verbose=verbose,
            )
            counts[resource_type] = sync.count
            all_verbose_lines.extend(sync.verbose_lines)

    # 8b. Resource types that existed in the old lockfile but are gone
    # from the current config — unapply every patch they recorded so
    # the user's structured config files don't keep stale agpack content.
    for resource_type, leftovers in old_applied_by_rt.items():
        if not leftovers:
            continue
        if verbose or dry_run:
            console.print(
                f"Removing all '{resource_type}' patches (no longer in config)..."
            )
        cleanup_orphaned_edits(
            leftovers, project_root, dry_run=dry_run, verbose=verbose
        )

    for line in all_verbose_lines:
        console.print(line)

    # 9. Write lockfile.
    if not dry_run:
        write_lockfile(project_root, new_lockfile)

    # 10. Summary — one chip per resource type that had configured deps.
    elapsed = time.monotonic() - start_time
    target_count = len(config.targets)
    parts = [
        f"[bold]{counts.get(rt, 0)}[/bold] {rt}"
        for rt in config.dependencies
    ]
    summary = ", ".join(parts) if parts else "[dim]nothing to deploy[/dim]"
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
@click.option(
    "--no-global",
    is_flag=True,
    help="Ignore the global config file.",
)
def status(config_path: str, no_global: bool) -> None:
    """Show the current state of installed resources vs the config."""
    cfg_path = Path(config_path).resolve()
    project_root = cfg_path.parent

    try:
        config = load_config(cfg_path)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    # Load and merge global config
    if not no_global and config.use_global:
        config, _ = _load_and_merge_global(config)

    lockfile = read_lockfile(project_root)

    installed_map: dict[str, InstalledEntry] = {}
    edits_by_type: dict[str, EditLockEntry] = {}
    if lockfile:
        for entry in lockfile.installed:
            installed_map[entry.identity] = entry
        for edit in lockfile.edits:
            edits_by_type[edit.resource_type] = edit

    for resource_type, deps in config.dependencies.items():
        table = Table(
            title=resource_type.capitalize(),
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
                if isinstance(dep, DependencySource):
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
                else:
                    # Patch entry — synced if the lockfile recorded it.
                    recorded = edits_by_type.get(resource_type)
                    synced = recorded is not None and any(
                        ap.key == dep.key and ap.strategy == dep.strategy
                        and ap.value == dep.value
                        for ap in recorded.applied
                    )
                    mark = "[green]✓[/green]" if synced else "[red]✗[/red]"
                    detail = dep.strategy if synced else "not yet synced"
                    table.add_row(f"{mark} {dep.key}", detail)
        console.print(table)
        console.print()


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
    if is_global:
        path = resolve_global_config_path()
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

  mcp:
    # Shared MCP servers as patches. ${bucket} is supplied by each
    # built-in target (mcpServers / mcp_servers / mcp / servers), so
    # one patch deploys correctly to every target.
    # - key: ${bucket}.my-server
    #   value:
    #     command: npx
    #     args: ["-y", "@example/mcp-server"]
    #     env:
    #       API_KEY: ${API_KEY}            # from .env or shell env
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

  # edit-file resources take patches instead of git URLs. ${bucket}
  # is supplied by the target manifest (e.g. mcpServers for Claude,
  # mcp for OpenCode, mcp_servers for Codex), so a single patch
  # deploys to every configured target.
  mcp:
    # - key: ${bucket}.my-server
    #   value:
    #     command: npx
    #     args: ["-y", "@example/mcp-server"]
    #     env:
    #       API_KEY: ${API_KEY}            # from .env or shell env

  # Claude Code hooks (.claude/settings.json → hooks.<event>):
  # hooks:
  #   - key: ${bucket}.PreToolUse        # bucket="hooks" on Claude
  #     strategy: append                  # default is 'replace'
  #     value:
  #       matcher: "Write|Edit"
  #       hooks:
  #         - type: command
  #           # $${} escapes — Claude Code resolves at hook fire time:
  #           command: "$${CLAUDE_PROJECT_DIR}/.claude/hooks/lint.sh"
  #
  # permissions:
  #   - key: ${bucket}.allow              # bucket="permissions"
  #     strategy: append
  #     value: "Read(/etc/**)"

# Override a built-in target or define a brand-new one.
# Use 'agpack targets list' to see all available targets and
# 'agpack targets show <name>' to print a starting manifest you can copy here.
# target_definitions:
#   claude:
#     # Override the built-in claude target — replace semantics (no merge).
#     skills:
#       kind: copy-directory       # one of: copy-directory, copy-file, edit-file
#       path: .my-claude/skills
#     commands:
#       kind: copy-file
#       path: .my-claude/commands
#     mcp:
#       kind: edit-file            # patches written from dependencies.mcp
#       path: .mcp.json            # format (json|toml) inferred from extension
#       vars:                      # exposed to patches as ${name}
#         bucket: mcpServers       # so patch ${bucket}.x → mcpServers.x
#
#   my-internal-tool:
#     # Brand-new target — also listed under 'targets:' above to be used.
#     skills:
#       kind: copy-directory
#       path: .myaitool/skills
#     # Resource type names are open — declare any name (rules, prompts,
#     # personas, …) and use the same name in 'dependencies:' above.
#     rules:
#       kind: copy-file
#       path: .myaitool/rules
#     settings:
#       kind: edit-file
#       path: .myaitool/settings.json
"""
    path.write_text(template, encoding="utf-8")
    console.print(f"[green]✓[/green] Created [bold]{path.name}[/bold]")


# ---------------------------------------------------------------------------
# `agpack targets` — inspect available target manifests
# ---------------------------------------------------------------------------


def _load_user_target_definitions(
    config_path: str, no_global: bool
) -> dict[str, TargetDef]:
    """Load target_definitions from project + global config.

    A missing project config is fine (the user may not have run ``init``
    yet) but a broken one is not — config errors are propagated so the
    user sees the parse failure instead of silently losing their
    target_definitions.  Project entries win by name over global.
    """
    cfg_path = Path(config_path).resolve()
    project_defs: dict[str, TargetDef] = {}
    include_global = not no_global

    if cfg_path.exists():
        try:
            config = load_config(cfg_path)
        except ConfigError as exc:
            raise click.ClickException(str(exc)) from exc
        project_defs = dict(config.target_definitions)
        include_global = include_global and config.use_global

    if not include_global:
        return project_defs

    try:
        global_cfg = load_global_config()
    except ConfigError as exc:
        raise click.ClickException(f"Global config error: {exc}") from exc

    if global_cfg is None:
        return project_defs

    merged = dict(project_defs)
    for name, td in global_cfg.target_definitions.items():
        merged.setdefault(name, td)
    return merged


def _load_raw_target_definition(
    name: str, config_path: str, no_global: bool
) -> dict[str, Any] | None:
    """Re-read the raw YAML for a user-defined target, or None if absent.

    Used by ``targets show`` so we can print exactly what the user wrote
    rather than round-tripping through TargetDef and reconstructing.
    """
    cfg_path = Path(config_path).resolve()
    include_global = not no_global
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        td = (data.get("target_definitions") or {}).get(name)
        if isinstance(td, dict):
            return td
        include_global = include_global and data.get("global", True)

    if not include_global:
        return None

    global_path = resolve_global_config_path()
    if not global_path.exists():
        return None
    data = yaml.safe_load(global_path.read_text(encoding="utf-8")) or {}
    td = (data.get("target_definitions") or {}).get(name)
    return td if isinstance(td, dict) else None


def _read_builtin_yaml(name: str) -> str:
    """Return the on-disk YAML text for a built-in target."""
    return (
        importlib_files("agpack.builtin_targets")
        .joinpath(f"{name}.yml")
        .read_text(encoding="utf-8")
    )


def _resource_summary(target: TargetDef) -> str:
    if not target.resources:
        return "[dim]none[/dim]"
    return ", ".join(target.resources)


@main.group()
def targets() -> None:
    """Inspect available target manifests (built-in and user-defined)."""


@targets.command("list")
@click.option(
    "--config",
    "config_path",
    default="./agpack.yml",
    type=click.Path(),
    help="Path to config file (used to discover user target_definitions).",
)
@click.option(
    "--no-global",
    is_flag=True,
    help="Ignore the global config file.",
)
def targets_list(config_path: str, no_global: bool) -> None:
    """List all available targets — built-ins and user-defined."""
    user_defs = _load_user_target_definitions(config_path, no_global)
    builtin_names = set(list_builtins())

    table = Table(
        title="Available targets",
        title_style="bold",
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 2),
        title_justify="left",
    )
    table.add_column("Name")
    table.add_column("Source")
    table.add_column("Resources")

    all_names = sorted(builtin_names | set(user_defs))
    for name in all_names:
        if name in user_defs:
            target = user_defs[name]
            if name in builtin_names:
                source = "[yellow]user (overrides built-in)[/yellow]"
            else:
                source = "[cyan]user[/cyan]"
        else:
            target = load_builtin(name)
            source = "[dim]built-in[/dim]"
        table.add_row(name, source, _resource_summary(target))

    console.print(table)


@targets.command("show")
@click.argument("name")
@click.option(
    "--config",
    "config_path",
    default="./agpack.yml",
    type=click.Path(),
    help="Path to config file (used to discover user target_definitions).",
)
@click.option(
    "--no-global",
    is_flag=True,
    help="Ignore the global config file.",
)
def targets_show(name: str, config_path: str, no_global: bool) -> None:
    """Print the resolved manifest for *name* as YAML.

    Useful as a starting point for copying into ``target_definitions:``
    to customise a built-in.
    """
    raw = _load_raw_target_definition(name, config_path, no_global)
    if raw is not None:
        click.echo(
            yaml.safe_dump(raw, default_flow_style=False, sort_keys=False), nl=False
        )
        return

    if name in list_builtins():
        click.echo(_read_builtin_yaml(name), nl=False)
        return

    user_defs = _load_user_target_definitions(config_path, no_global)
    builtins = ", ".join(list_builtins())
    user_names = ", ".join(sorted(user_defs)) or "(none)"
    raise click.ClickException(
        f"Unknown target '{name}'.\n"
        f"  Built-in targets: {builtins}\n"
        f"  Your target_definitions: {user_names}"
    )
