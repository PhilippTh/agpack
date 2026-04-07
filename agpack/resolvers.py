"""Resolvers — transform fetched content into WriteOp lists.

Each resolver is a pure function: it inspects fetched files and the
target list, then returns ``WriteOp`` objects describing what to write.
No filesystem writes happen here.

Also contains rule-specific format helpers (frontmatter parsing,
MDC generation) since they are only used during resolution.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from agpack.config import McpServer
from agpack.fetcher import FetchResult
from agpack.targets import AGENT_DIRS
from agpack.targets import COMMAND_DIRS
from agpack.targets import IGNORE_FILES
from agpack.targets import MCP_TARGETS
from agpack.targets import RULE_TARGETS
from agpack.targets import SKILL_DIRS
from agpack.writer import CopyFileOp
from agpack.writer import CopyTreeOp
from agpack.writer import IgnoreSectionOp
from agpack.writer import ManagedSectionOp
from agpack.writer import MergeJsonOp
from agpack.writer import MergeTomlOp
from agpack.writer import WriteOp
from agpack.writer import WriteTextOp

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ResolveError(Exception):
    """Raised when item detection or resolution fails."""


# ---------------------------------------------------------------------------
# Directory scanning helpers
# ---------------------------------------------------------------------------


def _find_top_level_files(path: Path) -> list[Path]:
    """Return non-hidden files at the top level of a directory."""
    return sorted(item for item in path.iterdir() if item.is_file() and not item.name.startswith("."))


def _find_asset_subfolders(path: Path) -> list[Path]:
    """Return immediate subdirectories that contain at least one file."""
    subfolders: list[Path] = []
    for item in sorted(path.iterdir()):
        if item.is_dir() and not item.name.startswith(".git"):
            has_files = any(
                f.is_file() and not any(p.startswith(".git") for p in f.relative_to(item).parts)
                for f in item.rglob("*")
            )
            if has_files:
                subfolders.append(item)
    return subfolders


# ---------------------------------------------------------------------------
# Item detection
# ---------------------------------------------------------------------------


def detect_single_file_items(fetch_result: FetchResult, resource_type: str) -> list[tuple[str, Path]]:
    """Detect items for single-file resources (commands, agents, rules).

    * Single file → one item.
    * Directory with top-level files → one item per file.
    * Directory with only subdirectories → recurse into each.
    """
    local_path = fetch_result.local_path

    if local_path.is_dir():
        files = _find_top_level_files(local_path)
        if not files:
            for sf in _find_asset_subfolders(local_path):
                files.extend(_find_top_level_files(sf))
        if not files:
            article = "an" if resource_type[0] in "aeiou" else "a"
            raise ResolveError(
                f"'{fetch_result.source.name}' is a directory but does not contain "
                f"any {resource_type} files. Provide a path to {article} "
                f"{resource_type} file or a directory containing "
                f"{resource_type} files."
            )
        return [(f.name, f) for f in files]

    return [(fetch_result.source.name, local_path)]


def detect_skill_items(fetch_result: FetchResult) -> list[tuple[str, Path]]:
    """Detect items for skill resources.

    A directory with top-level files is treated as a single skill.
    A directory with only subdirectories expands to one skill per subfolder.
    """
    local_path = fetch_result.local_path

    if local_path.is_dir() and not _find_top_level_files(local_path):
        subfolders = _find_asset_subfolders(local_path)
        if not subfolders:
            raise ResolveError(
                f"'{fetch_result.source.name}' is a directory but does not contain "
                f"any skill folders. Provide a path to a skill folder or a "
                f"directory containing skill folders."
            )
        return [(sf.name, sf) for sf in subfolders]

    return [(fetch_result.source.name, local_path)]


# ---------------------------------------------------------------------------
# Rule format helpers (frontmatter, MDC generation)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_rule_frontmatter(content: str) -> tuple[dict[str, object], str]:
    """Split YAML frontmatter from the markdown body.

    Returns:
        A tuple of (frontmatter_dict, body_string).
        If no frontmatter is found, returns ({}, full content).
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    raw = match.group(1)
    body = content[match.end() :]

    try:
        fm = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}, content

    if not isinstance(fm, dict):
        return {}, content

    return fm, body


def normalize_frontmatter_for_cursor(fm: dict[str, object]) -> dict[str, object]:
    """Produce Cursor-native frontmatter from a source frontmatter dict.

    * Translates ``applyTo`` → ``globs`` (comma-separated string → list).
    * If neither ``globs``/``applyTo`` nor ``alwaysApply`` are present,
      defaults ``alwaysApply`` to ``true`` so the rule is not invisible.
    * Strips all fields Cursor does not understand.
    """
    out: dict[str, object] = {}

    if "applyTo" in fm and "globs" not in fm:
        raw = fm["applyTo"]
        if isinstance(raw, str):
            out["globs"] = [g.strip() for g in raw.split(",") if g.strip()]
        elif isinstance(raw, list):
            out["globs"] = raw
    elif "globs" in fm:
        out["globs"] = fm["globs"]

    if "description" in fm:
        out["description"] = fm["description"]
    if "alwaysApply" in fm:
        out["alwaysApply"] = fm["alwaysApply"]

    if "globs" not in out and "alwaysApply" not in out:
        out["alwaysApply"] = True

    return out


def generate_mdc(frontmatter: dict[str, object], body: str) -> str:
    """Produce a Cursor ``.mdc`` file from parsed frontmatter and body."""
    cursor_fm = normalize_frontmatter_for_cursor(frontmatter)

    lines = ["---"]
    if "description" in cursor_fm:
        lines.append(f"description: {_yaml_scalar(cursor_fm['description'])}")
    if "globs" in cursor_fm:
        globs = cursor_fm["globs"]
        if isinstance(globs, list):
            lines.append(f"globs: {_yaml_inline_list(globs)}")
        else:
            lines.append(f"globs: {_yaml_scalar(globs)}")
    if "alwaysApply" in cursor_fm:
        val = "true" if cursor_fm["alwaysApply"] else "false"
        lines.append(f"alwaysApply: {val}")
    lines.append("---")

    return "\n".join(lines) + "\n" + body


def get_rule_name(frontmatter: dict[str, object], filename: str) -> str:
    """Derive the rule name from frontmatter or filename stem."""
    if "name" in frontmatter and frontmatter["name"]:
        return str(frontmatter["name"])
    return Path(filename).stem


def _yaml_scalar(value: object) -> str:
    s = str(value)
    if any(c in s for c in ":{}[],'\"&*?|->!%@`#"):
        return f'"{s}"'
    return s


def _yaml_inline_list(items: list[object]) -> str:
    parts = [f'"{item}"' for item in items]
    return "[" + ", ".join(parts) + "]"


# ---------------------------------------------------------------------------
# Skill resolver
# ---------------------------------------------------------------------------


def resolve_skills(fetch_result: FetchResult, targets: list[str]) -> list[WriteOp]:
    """Produce write ops for skill items across all targets."""
    items = detect_skill_items(fetch_result)
    ops: list[WriteOp] = []

    for name, path in items:
        for target in targets:
            target_dir = SKILL_DIRS.get(target)
            if target_dir is None:
                continue
            if path.is_dir():
                ops.append(CopyTreeOp(src_dir=path, dst_rel=f"{target_dir}/{name}"))
            else:
                ops.append(CopyFileOp(src=path, dst_rel=f"{target_dir}/{name}/{path.name}"))

    return ops


# ---------------------------------------------------------------------------
# Command resolver
# ---------------------------------------------------------------------------


def resolve_commands(fetch_result: FetchResult, targets: list[str]) -> list[WriteOp]:
    """Produce write ops for command items across all targets."""
    items = detect_single_file_items(fetch_result, "command")
    ops: list[WriteOp] = []

    for name, path in items:
        for target in targets:
            target_dir = COMMAND_DIRS.get(target)
            if target_dir is None:
                continue
            ops.append(CopyFileOp(src=path, dst_rel=f"{target_dir}/{name}"))

    return ops


# ---------------------------------------------------------------------------
# Agent resolver
# ---------------------------------------------------------------------------


def resolve_agents(fetch_result: FetchResult, targets: list[str]) -> list[WriteOp]:
    """Produce write ops for agent items across all targets."""
    items = detect_single_file_items(fetch_result, "agent")
    ops: list[WriteOp] = []

    for name, path in items:
        for target in targets:
            target_dir = AGENT_DIRS.get(target)
            if target_dir is None:
                continue
            ops.append(CopyFileOp(src=path, dst_rel=f"{target_dir}/{name}"))

    return ops


# ---------------------------------------------------------------------------
# Rule resolver
# ---------------------------------------------------------------------------


def resolve_rules(
    fetch_result: FetchResult,
    targets: list[str],
) -> tuple[list[WriteOp], list[tuple[str, str]]]:
    """Produce file-based write ops for rules and collect bodies for append targets.

    Returns:
        A tuple of (write_ops, rule_bodies) where rule_bodies is a list
        of (name, body) pairs for use with ``resolve_rules_append``.
    """
    items = detect_single_file_items(fetch_result, "rule")
    ops: list[WriteOp] = []
    bodies: list[tuple[str, str]] = []

    for name, path in items:
        content = path.read_text(encoding="utf-8")
        fm, body = parse_rule_frontmatter(content)
        rule_name = get_rule_name(fm, name)
        bodies.append((rule_name, body))

        for target in targets:
            cfg = RULE_TARGETS.get(target)
            if cfg is None or cfg.strategy != "file":
                continue

            if target == "cursor":
                ops.append(
                    WriteTextOp(
                        content=generate_mdc(fm, body),
                        dst_rel=f"{cfg.path}/{rule_name}.mdc",
                    )
                )
            elif target == "windsurf":
                ops.append(
                    WriteTextOp(
                        content=body,
                        dst_rel=f"{cfg.path}/{rule_name}.md",
                    )
                )

    return ops, bodies


def resolve_rules_append(
    all_bodies: list[tuple[str, str]],
    targets: list[str],
) -> list[WriteOp]:
    """Produce ManagedSectionOps for append-based rule targets.

    Deduplicates shared output files (e.g. AGENTS.md used by
    codex / opencode / copilot).
    """
    if not all_bodies:
        return []

    ops: list[WriteOp] = []
    seen: set[str] = set()

    for target in targets:
        cfg = RULE_TARGETS.get(target)
        if cfg is None or cfg.strategy != "append":
            continue
        if cfg.path in seen:
            continue
        seen.add(cfg.path)
        ops.append(ManagedSectionOp(entries=all_bodies, dst_rel=cfg.path))

    return ops


# ---------------------------------------------------------------------------
# Ignore resolver
# ---------------------------------------------------------------------------


def resolve_ignores(
    fetch_result: FetchResult,
    targets: list[str],
) -> tuple[list[WriteOp], list[str]]:
    """Read ignore pattern content from a fetched source.

    Returns:
        A tuple of (write_ops, patterns) where write_ops is always empty
        (ignore files are written via ``resolve_ignores_append``) and
        patterns is a list of pattern strings to aggregate.
    """
    local_path = fetch_result.local_path
    items = detect_single_file_items(fetch_result, "ignore")

    patterns: list[str] = []
    for _name, path in items:
        content = path.read_text(encoding="utf-8").strip()
        if content:
            patterns.append(content)

    return [], patterns


def resolve_ignores_append(
    all_patterns: list[str],
    targets: list[str],
) -> list[WriteOp]:
    """Produce IgnoreSectionOps for all targets that have ignore files.

    Deduplicates shared output files.
    """
    if not all_patterns:
        return []

    combined = "\n".join(all_patterns)
    ops: list[WriteOp] = []
    seen: set[str] = set()

    for target in targets:
        ignore_file = IGNORE_FILES.get(target)
        if ignore_file is None:
            continue
        if ignore_file in seen:
            continue
        seen.add(ignore_file)
        ops.append(IgnoreSectionOp(patterns=combined, dst_rel=ignore_file))

    return ops


# ---------------------------------------------------------------------------
# MCP resolver
# ---------------------------------------------------------------------------


def _build_server_object(server: McpServer, target: str = "") -> dict[str, Any]:
    """Build the config object for a single MCP server."""
    if target == "opencode":
        return _build_opencode_server_object(server)

    if server.type == "stdio":
        obj: dict[str, Any] = {}
        if target == "copilot":
            obj["type"] = "stdio"
        obj["command"] = server.command
        if server.args:
            obj["args"] = server.args
        if server.env:
            obj["env"] = server.env
        return obj
    return {"url": server.url, "type": server.type}


def _build_opencode_server_object(server: McpServer) -> dict[str, Any]:
    """Build an MCP server object in opencode's expected schema."""
    if server.type == "stdio":
        cmd = [server.command] + server.args if server.args else [server.command]
        obj: dict[str, Any] = {"type": "local", "command": cmd}
        if server.env:
            obj["environment"] = server.env
        return obj
    return {"type": "remote", "url": server.url}


def resolve_mcp(
    servers: list[McpServer],
    targets: list[str],
) -> list[WriteOp]:
    """Produce merge ops for MCP server configs."""
    ops: list[WriteOp] = []

    for server in servers:
        for target in targets:
            target_cfg = MCP_TARGETS.get(target)
            if target_cfg is None:
                continue

            server_obj = _build_server_object(server, target=target)

            if target_cfg.format == "json":
                ops.append(
                    MergeJsonOp(
                        data={server.name: server_obj},
                        dst_rel=target_cfg.config_path,
                        key=target_cfg.servers_key,
                    )
                )
            elif target_cfg.format == "toml":
                ops.append(
                    MergeTomlOp(
                        data={server.name: server_obj},
                        dst_rel=target_cfg.config_path,
                        key=target_cfg.servers_key,
                    )
                )

    return ops
