"""Click entrypoints for the agpack CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from agpack import __version__
from agpack.config import ConfigError
from agpack.config import load_config
from agpack.deployer import cleanup_deployed_files
from agpack.deployer import deploy_agent
from agpack.deployer import deploy_command
from agpack.deployer import deploy_skill
from agpack.fetcher import FetchError
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

    # 1. Load and validate config
    try:
        config = load_config(cfg_path)
    except ConfigError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

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
            click.echo(f"Removing {entry.type} '{entry.url}/{entry.path or ''}'...")
        cleanup_deployed_files(
            entry.deployed_files, project_root, dry_run=dry_run, verbose=verbose
        )

    # 5. Clean up removed MCP servers
    removed_mcp = find_removed_mcp_servers(old_lockfile, current_mcp_names)
    for entry in removed_mcp:
        if verbose or dry_run:
            click.echo(f"Removing MCP server '{entry.name}'...")
        cleanup_mcp_server(
            entry.name,
            entry.targets,
            project_root,
            config.targets,
            dry_run=dry_run,
            verbose=verbose,
        )

    # 6. Fetch and deploy dependencies
    new_lockfile = Lockfile()
    counts = {"skills": 0, "commands": 0, "agents": 0, "mcp": 0}

    # Skills
    for dep in config.skills:
        click.echo(f"Fetching skill '{dep.name}' from {dep.url}...")
        try:
            result = fetch_dependency(dep)
        except FetchError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(2)

        try:
            deployed = deploy_skill(
                result,
                config.targets,
                project_root,
                dry_run=dry_run,
                verbose=verbose,
            )
        except Exception as exc:
            click.echo(f"Error deploying skill '{dep.name}': {exc}", err=True)
            cleanup_fetch(result)
            sys.exit(3)

        new_lockfile.installed.append(
            InstalledEntry(
                url=dep.url,
                path=dep.path,
                resolved_ref=result.resolved_ref,
                type="skill",
                deployed_files=deployed,
            )
        )
        cleanup_fetch(result)
        counts["skills"] += 1

    # Commands
    for dep in config.commands:
        click.echo(f"Fetching command '{dep.name}' from {dep.url}...")
        try:
            result = fetch_dependency(dep)
        except FetchError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(2)

        try:
            deployed = deploy_command(
                result,
                config.targets,
                project_root,
                dry_run=dry_run,
                verbose=verbose,
            )
        except Exception as exc:
            click.echo(f"Error deploying command '{dep.name}': {exc}", err=True)
            cleanup_fetch(result)
            sys.exit(3)

        new_lockfile.installed.append(
            InstalledEntry(
                url=dep.url,
                path=dep.path,
                resolved_ref=result.resolved_ref,
                type="command",
                deployed_files=deployed,
            )
        )
        cleanup_fetch(result)
        counts["commands"] += 1

    # Agents
    for dep in config.agents:
        click.echo(f"Fetching agent '{dep.name}' from {dep.url}...")
        try:
            result = fetch_dependency(dep)
        except FetchError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(2)

        try:
            deployed = deploy_agent(
                result,
                config.targets,
                project_root,
                dry_run=dry_run,
                verbose=verbose,
            )
        except Exception as exc:
            click.echo(f"Error deploying agent '{dep.name}': {exc}", err=True)
            cleanup_fetch(result)
            sys.exit(3)

        new_lockfile.installed.append(
            InstalledEntry(
                url=dep.url,
                path=dep.path,
                resolved_ref=result.resolved_ref,
                type="agent",
                deployed_files=deployed,
            )
        )
        cleanup_fetch(result)
        counts["agents"] += 1

    # MCP servers
    if config.mcp:
        click.echo("Deploying MCP servers...")
        try:
            mcp_result = deploy_mcp_servers(
                config.mcp,
                config.targets,
                project_root,
                dry_run=dry_run,
                verbose=verbose,
            )
        except McpError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(3)

        for server_name, target_paths in mcp_result.items():
            new_lockfile.mcp.append(
                McpLockEntry(name=server_name, targets=target_paths)
            )
            counts["mcp"] += 1

    # 7. Write lockfile
    if not dry_run:
        write_lockfile(project_root, new_lockfile)

    # 8. Summary
    target_count = len(config.targets)
    click.echo(
        f"\n{counts['skills']} skills, {counts['commands']} commands, "
        f"{counts['agents']} agents, {counts['mcp']} MCP servers "
        f"synced to {target_count} targets."
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
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    lockfile = read_lockfile(project_root)

    # Build lookup from lockfile
    installed_map: dict[str, InstalledEntry] = {}
    if lockfile:
        for entry in lockfile.installed:
            installed_map[entry.identity] = entry

    mcp_map: dict[str, McpLockEntry] = {}
    if lockfile:
        for entry in lockfile.mcp:
            mcp_map[entry.name] = entry

    # Skills
    click.echo("Skills:")
    if not config.skills:
        click.echo("  (none configured)")
    else:
        for dep in config.skills:
            entry = installed_map.get(dep.identity)
            if entry:
                short_ref = entry.resolved_ref[:7]
                click.echo(f"  ✓ {dep.name:<20} ({dep.url} @ {short_ref})")
            else:
                click.echo(f"  ✗ {dep.name:<20} (not yet synced)")

    # Commands
    click.echo("\nCommands:")
    if not config.commands:
        click.echo("  (none configured)")
    else:
        for dep in config.commands:
            entry = installed_map.get(dep.identity)
            if entry:
                short_ref = entry.resolved_ref[:7]
                click.echo(f"  ✓ {dep.name:<20} ({dep.url} @ {short_ref})")
            else:
                click.echo(f"  ✗ {dep.name:<20} (not yet synced)")

    # Agents
    click.echo("\nAgents:")
    if not config.agents:
        click.echo("  (none configured)")
    else:
        for dep in config.agents:
            entry = installed_map.get(dep.identity)
            if entry:
                short_ref = entry.resolved_ref[:7]
                click.echo(f"  ✓ {dep.name:<20} ({dep.url} @ {short_ref})")
            else:
                click.echo(f"  ✗ {dep.name:<20} (not yet synced)")

    # MCP
    click.echo("\nMCP:")
    if not config.mcp:
        click.echo("  (none configured)")
    else:
        for server in config.mcp:
            entry = mcp_map.get(server.name)
            if entry and entry.targets:
                targets_str = ", ".join(entry.targets)
                click.echo(f"  ✓ {server.name:<20} → {targets_str}")
            else:
                click.echo(f"  ✗ {server.name:<20} (not yet synced)")


@main.command()
def init() -> None:
    """Scaffold a new agpack.yml in the current directory."""
    path = Path.cwd() / "agpack.yml"
    if path.exists():
        click.echo("agpack.yml already exists — doing nothing.")
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
    click.echo(f"Created {path}")
