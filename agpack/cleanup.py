"""Cleanup logic — remove previously deployed files and configs.

This is the reverse of writing: it removes files tracked in the lockfile
when dependencies or MCP servers are removed from the config between syncs.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import tomli_w

from agpack.display import console
from agpack.fileutil import atomic_write_text
from agpack.targets import HOOK_CONFIG_TARGETS
from agpack.targets import IGNORE_FILES
from agpack.targets import MCP_TARGETS
from agpack.targets import RULE_TARGETS
from agpack.targets import McpTargetConfig
from agpack.writer import IGNORE_END_MARKER
from agpack.writer import IGNORE_START_MARKER
from agpack.writer import RULES_END_MARKER
from agpack.writer import RULES_START_MARKER

# ---------------------------------------------------------------------------
# File cleanup
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Rule append-target cleanup
# ---------------------------------------------------------------------------


_MANAGED_SECTION_RE = re.compile(
    re.escape(RULES_START_MARKER) + r".*?" + re.escape(RULES_END_MARKER),
    re.DOTALL,
)


def _remove_managed_section(content: str) -> str:
    """Remove the managed section from file content."""
    result = _MANAGED_SECTION_RE.sub("", content)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.rstrip("\n") + "\n" if result.strip() else ""


def cleanup_rule_append_targets(
    targets: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Remove managed rule sections from all append-based targets.

    Called when all rule dependencies have been removed.
    """
    seen_paths: set[str] = set()

    for target in targets:
        cfg = RULE_TARGETS.get(target)
        if cfg is None or cfg.strategy != "append":
            continue
        if cfg.path in seen_paths:
            continue
        seen_paths.add(cfg.path)

        target_path = project_root / cfg.path
        if not target_path.exists():
            continue

        if dry_run:
            if verbose:
                console.print(f"  [dry-run] clean managed section in {cfg.path}")
            continue

        content = target_path.read_text(encoding="utf-8")
        cleaned = _remove_managed_section(content)
        atomic_write_text(target_path, cleaned if cleaned else "")

        if verbose:
            console.print(f"  cleaned managed section in {cfg.path}")


# ---------------------------------------------------------------------------
# Ignore file cleanup
# ---------------------------------------------------------------------------

_IGNORE_SECTION_RE = re.compile(
    re.escape(IGNORE_START_MARKER) + r".*?" + re.escape(IGNORE_END_MARKER),
    re.DOTALL,
)


def _remove_ignore_section(content: str) -> str:
    """Remove the managed ignore section from file content."""
    result = _IGNORE_SECTION_RE.sub("", content)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.rstrip("\n") + "\n" if result.strip() else ""


def cleanup_ignore_files(
    targets: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Remove managed ignore sections from all target ignore files.

    Called when all ignore dependencies have been removed.
    """
    seen_paths: set[str] = set()

    for target in targets:
        ignore_file = IGNORE_FILES.get(target)
        if ignore_file is None:
            continue
        if ignore_file in seen_paths:
            continue
        seen_paths.add(ignore_file)

        target_path = project_root / ignore_file
        if not target_path.exists():
            continue

        if dry_run:
            if verbose:
                console.print(f"  [dry-run] clean managed section in {ignore_file}")
            continue

        content = target_path.read_text(encoding="utf-8")
        cleaned = _remove_ignore_section(content)
        atomic_write_text(target_path, cleaned if cleaned else "")

        if verbose:
            console.print(f"  cleaned managed section in {ignore_file}")


# ---------------------------------------------------------------------------
# Hook config cleanup
# ---------------------------------------------------------------------------


def _remove_hooks_from_json(config_path: Path, hooks_key: str, target: str) -> None:
    """Remove the hooks key from a JSON config file."""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    if hooks_key not in data:
        return

    del data[hooks_key]
    # For Cursor, also remove the version key if hooks were the only content
    if target == "cursor" and "version" in data and len(data) == 1:
        data.pop("version", None)

    if data:
        atomic_write_text(config_path, json.dumps(data, indent=2) + "\n")
    else:
        config_path.unlink()


def cleanup_hook_configs(
    targets: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Remove agpack-managed hook entries from target config files.

    Called when all hook_configs have been removed from the config.
    Removes the ``hooks`` key from each target's config file.
    """
    seen_paths: set[str] = set()

    for target in targets:
        target_cfg = HOOK_CONFIG_TARGETS.get(target)
        if target_cfg is None:
            continue
        if target_cfg.config_path in seen_paths:
            continue
        seen_paths.add(target_cfg.config_path)

        config_path = project_root / target_cfg.config_path
        if not config_path.exists():
            continue

        if dry_run:
            if verbose:
                console.print(f"  [dry-run] remove hooks from {target_cfg.config_path}")
            continue

        _remove_hooks_from_json(config_path, target_cfg.hooks_key, target)

        if verbose:
            console.print(f"  removed hooks from {target_cfg.config_path}")


# ---------------------------------------------------------------------------
# MCP server cleanup
# ---------------------------------------------------------------------------


def cleanup_mcp_server(  # noqa: C901
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

        target_cfg: McpTargetConfig | None = None
        for target in targets:
            cfg = MCP_TARGETS.get(target)
            if cfg and cfg.config_path == rel_path:
                target_cfg = cfg
                break

        if target_cfg is None:
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


# ---------------------------------------------------------------------------
# JSON / TOML removal helpers
# ---------------------------------------------------------------------------


def _remove_server_from_json(config_path: Path, servers_key: str, server_name: str) -> None:
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    servers = data.get(servers_key, {})
    if server_name in servers:
        del servers[server_name]
        atomic_write_text(config_path, json.dumps(data, indent=2) + "\n")


def _remove_server_from_toml(config_path: Path, servers_key: str, server_name: str) -> None:
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return
    servers = data.get(servers_key, {})
    if server_name in servers:
        del servers[server_name]
        atomic_write_text(config_path, tomli_w.dumps(data))


def _remove_from_json(config_path: Path, server_name: str, dry_run: bool) -> None:
    if dry_run:
        return
    for key in ("mcpServers", "mcp", "servers"):
        _remove_server_from_json(config_path, key, server_name)


def _remove_from_toml(config_path: Path, server_name: str, dry_run: bool) -> None:
    if dry_run:
        return
    for key in ("mcp_servers",):
        _remove_server_from_toml(config_path, key, server_name)
