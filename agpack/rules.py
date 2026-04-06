"""Rule detection, deployment, and cleanup.

Owns all rule-related logic: frontmatter parsing, format generation,
managed sections, and deployment I/O.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from agpack.deployer import detect_file_items
from agpack.display import console
from agpack.fetcher import FetchResult
from agpack.fileutil import atomic_write_text
from agpack.targets import RULE_TARGETS

# ---------------------------------------------------------------------------
# Frontmatter parsing
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


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_rule_items(fetch_result: FetchResult) -> list[tuple[str, Path]]:
    """Return ``(name, path)`` pairs for rule items in a fetch result.

    A single file is one rule; a directory expands to one rule per
    non-hidden file.
    """
    return detect_file_items(fetch_result, "rule")


# ---------------------------------------------------------------------------
# Frontmatter normalisation
# ---------------------------------------------------------------------------


def normalize_frontmatter_for_cursor(fm: dict[str, object]) -> dict[str, object]:
    """Produce Cursor-native frontmatter from a source frontmatter dict.

    * Translates ``applyTo`` → ``globs`` (comma-separated string → list).
    * If neither ``globs``/``applyTo`` nor ``alwaysApply`` are present,
      defaults ``alwaysApply`` to ``true`` so the rule is not invisible.
    * Strips all fields Cursor does not understand.
    """
    out: dict[str, object] = {}

    # Translate applyTo → globs
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

    # Default: if no globs/applyTo and no alwaysApply, make it always-apply
    if "globs" not in out and "alwaysApply" not in out:
        out["alwaysApply"] = True

    return out


# ---------------------------------------------------------------------------
# Format generators
# ---------------------------------------------------------------------------


def generate_mdc(frontmatter: dict[str, object], body: str) -> str:
    """Produce a Cursor ``.mdc`` file from parsed frontmatter and body.

    The output has YAML frontmatter with only Cursor-understood fields,
    followed by the markdown body.
    """
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


# ---------------------------------------------------------------------------
# Managed section logic
# ---------------------------------------------------------------------------

RULES_START_MARKER = "<!-- agpack-managed-rules-start -->"
RULES_END_MARKER = "<!-- agpack-managed-rules-end -->"
RULES_HEADER = "<!-- DO NOT EDIT between these markers -- managed by agpack -->"

_MANAGED_SECTION_RE = re.compile(
    re.escape(RULES_START_MARKER) + r".*?" + re.escape(RULES_END_MARKER),
    re.DOTALL,
)


def build_managed_section(rule_bodies: list[tuple[str, str]]) -> str:
    """Build the full managed section content from a list of (name, body) pairs.

    Args:
        rule_bodies: List of (rule_name, markdown_body) tuples, in declaration order.

    Returns:
        The complete managed section string including markers.
    """
    parts = [RULES_START_MARKER, RULES_HEADER, ""]

    for i, (name, body) in enumerate(rule_bodies):
        parts.append(f"## {name}")
        parts.append(body.strip())
        if i < len(rule_bodies) - 1:
            parts.append("")

    parts.append("")
    parts.append(RULES_END_MARKER)
    return "\n".join(parts)


def merge_into_managed_section(
    existing_content: str,
    rule_bodies: list[tuple[str, str]],
) -> str:
    """Merge rules into a file's managed section.

    If the managed section already exists, it is replaced.
    If not, the section is appended to the end of the file.
    """
    new_section = build_managed_section(rule_bodies)

    if _MANAGED_SECTION_RE.search(existing_content):
        return _MANAGED_SECTION_RE.sub(new_section, existing_content)

    # Append — ensure a blank line before the section
    sep = "\n\n" if existing_content and not existing_content.endswith("\n\n") else ""
    if existing_content and not existing_content.endswith("\n"):
        sep = "\n\n"
    return existing_content + sep + new_section + "\n"


def remove_managed_section(content: str) -> str:
    """Remove the managed section from file content.

    Returns the content with the managed section (and any surrounding blank
    lines) removed.
    """
    result = _MANAGED_SECTION_RE.sub("", content)
    # Clean up double blank lines left behind
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.rstrip("\n") + "\n" if result.strip() else ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _yaml_scalar(value: object) -> str:
    """Format a scalar value for YAML frontmatter output."""
    s = str(value)
    if any(c in s for c in ":{}[],'\"&*?|->!%@`#"):
        return f'"{s}"'
    return s


def _yaml_inline_list(items: list[object]) -> str:
    """Format a list as a YAML inline list: ["a", "b"]."""
    parts = [f'"{item}"' for item in items]
    return "[" + ", ".join(parts) + "]"


def get_rule_name(frontmatter: dict[str, object], filename: str) -> str:
    """Derive the rule name from frontmatter or filename stem."""
    if "name" in frontmatter and frontmatter["name"]:
        return str(frontmatter["name"])
    return Path(filename).stem


# ---------------------------------------------------------------------------
# Deployment — file-based targets (Cursor, Windsurf)
# ---------------------------------------------------------------------------


def deploy_single_rule(
    name: str,
    frontmatter: dict[str, object],
    body: str,
    targets: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Deploy a single rule to file-based targets (Cursor, Windsurf).

    Append-based targets are handled separately by
    :func:`deploy_rule_append_targets`.

    Returns:
        List of relative paths of deployed files.
    """
    all_deployed: list[str] = []

    for target in targets:
        cfg = RULE_TARGETS.get(target)
        if cfg is None or cfg.strategy != "file":
            continue

        if target == "cursor":
            content = generate_mdc(frontmatter, body)
            filename = f"{name}.mdc"
        elif target == "windsurf":
            content = body
            filename = f"{name}.md"
        else:
            continue

        dst = project_root / cfg.path / filename
        rel = str(dst.relative_to(project_root))

        if not dry_run:
            atomic_write_text(dst, content)

        all_deployed.append(rel)
        if verbose:
            console.print(f"  {rel}")

    return all_deployed


# ---------------------------------------------------------------------------
# Deployment — append-based targets (CLAUDE.md, AGENTS.md, GEMINI.md)
# ---------------------------------------------------------------------------


def deploy_rule_append_targets(
    rule_bodies: list[tuple[str, str]],
    targets: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Write managed sections to all append-based rule targets.

    Deduplicates shared output files (e.g. ``AGENTS.md`` used by
    codex / opencode / copilot).

    Returns:
        List of relative paths of written files.
    """
    seen_paths: set[str] = set()
    deployed: list[str] = []

    for target in targets:
        cfg = RULE_TARGETS.get(target)
        if cfg is None or cfg.strategy != "append":
            continue
        if cfg.path in seen_paths:
            continue
        seen_paths.add(cfg.path)

        target_path = project_root / cfg.path

        if not dry_run:
            existing = ""
            if target_path.exists():
                existing = target_path.read_text(encoding="utf-8")
            new_content = merge_into_managed_section(existing, rule_bodies)
            atomic_write_text(target_path, new_content)

        rel = str(target_path.relative_to(project_root))
        deployed.append(rel)
        if verbose:
            console.print(f"  {rel}")

    return deployed


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


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
        cleaned = remove_managed_section(content)
        atomic_write_text(target_path, cleaned if cleaned else "")

        if verbose:
            console.print(f"  cleaned managed section in {cfg.path}")
