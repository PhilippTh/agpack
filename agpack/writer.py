"""Uniform write operations for all resource types.

Every resolver produces a list of ``WriteOp`` objects — plain data
describing *what* to write.  The single ``execute_write_ops`` function
is the only I/O boundary and handles all of them uniformly.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import tomli_w

from agpack.display import console
from agpack.fileutil import atomic_copy_file
from agpack.fileutil import atomic_write_text

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WriteError(Exception):
    """Raised when a write operation fails."""


# Keep the old name as an alias for backward compatibility in tests.
DeployError = WriteError


# ---------------------------------------------------------------------------
# WriteOp types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CopyFileOp:
    """Copy a single source file to a destination path."""

    src: Path
    dst_rel: str  # relative to project_root


@dataclass(frozen=True)
class CopyTreeOp:
    """Recursively copy an entire directory tree."""

    src_dir: Path
    dst_rel: str  # relative to project_root


@dataclass(frozen=True)
class WriteTextOp:
    """Write generated text content to a file."""

    content: str
    dst_rel: str  # relative to project_root


@dataclass(frozen=True)
class ManagedSectionOp:
    """Merge content into a managed section of a shared file."""

    entries: list[tuple[str, str]] = field(default_factory=list)  # (name, body) pairs
    dst_rel: str = ""  # relative to project_root


@dataclass(frozen=True)
class IgnoreSectionOp:
    """Merge ignore patterns into a managed section of an ignore file."""

    patterns: str = ""  # newline-separated ignore patterns
    dst_rel: str = ""  # relative to project_root


@dataclass(frozen=True)
class MergeJsonOp:
    """Merge a dict into a key of a JSON config file."""

    data: dict[str, Any] = field(default_factory=dict)
    dst_rel: str = ""  # relative to project_root
    key: str = ""  # top-level key to merge into


@dataclass(frozen=True)
class MergeTomlOp:
    """Merge a dict into a key of a TOML config file."""

    data: dict[str, Any] = field(default_factory=dict)
    dst_rel: str = ""  # relative to project_root
    key: str = ""  # top-level key to merge into


WriteOp = CopyFileOp | CopyTreeOp | WriteTextOp | ManagedSectionOp | IgnoreSectionOp | MergeJsonOp | MergeTomlOp


# ---------------------------------------------------------------------------
# Tree helpers
# ---------------------------------------------------------------------------


def _list_tree_files(src_dir: Path) -> list[Path]:
    """Return all non-.git files under *src_dir*, sorted."""
    return sorted(
        f
        for f in src_dir.rglob("*")
        if f.is_file() and not any(part.startswith(".git") for part in f.relative_to(src_dir).parts)
    )


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
    """Build the full managed section content from a list of (name, body) pairs."""
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

    sep = "\n\n" if existing_content and not existing_content.endswith("\n\n") else ""
    if existing_content and not existing_content.endswith("\n"):
        sep = "\n\n"
    return existing_content + sep + new_section + "\n"


def remove_managed_section(content: str) -> str:
    """Remove the managed section from file content."""
    result = _MANAGED_SECTION_RE.sub("", content)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.rstrip("\n") + "\n" if result.strip() else ""


# ---------------------------------------------------------------------------
# Ignore section logic
# ---------------------------------------------------------------------------

IGNORE_START_MARKER = "# agpack-managed-ignores-start"
IGNORE_END_MARKER = "# agpack-managed-ignores-end"
IGNORE_HEADER = "# DO NOT EDIT between these markers -- managed by agpack"

_IGNORE_SECTION_RE = re.compile(
    re.escape(IGNORE_START_MARKER) + r".*?" + re.escape(IGNORE_END_MARKER),
    re.DOTALL,
)


def build_ignore_section(patterns: str) -> str:
    """Build the full managed ignore section from pattern content."""
    lines = [IGNORE_START_MARKER, IGNORE_HEADER]
    stripped = patterns.strip()
    if stripped:
        lines.append(stripped)
    lines.append(IGNORE_END_MARKER)
    return "\n".join(lines)


def merge_into_ignore_section(existing_content: str, patterns: str) -> str:
    """Merge ignore patterns into a file's managed ignore section.

    If the managed section already exists, it is replaced.
    If not, the section is appended to the end of the file.
    """
    new_section = build_ignore_section(patterns)

    if _IGNORE_SECTION_RE.search(existing_content):
        return _IGNORE_SECTION_RE.sub(new_section, existing_content)

    sep = "\n\n" if existing_content and not existing_content.endswith("\n\n") else ""
    if existing_content and not existing_content.endswith("\n"):
        sep = "\n\n"
    return existing_content + sep + new_section + "\n"


def remove_ignore_section(content: str) -> str:
    """Remove the managed ignore section from file content."""
    result = _IGNORE_SECTION_RE.sub("", content)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.rstrip("\n") + "\n" if result.strip() else ""


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def execute_write_ops(
    ops: list[WriteOp],
    project_root: Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Execute all write operations uniformly.

    Returns a list of relative paths that were written (for lockfile tracking).
    """
    deployed: list[str] = []

    for op in ops:
        match op:
            case CopyFileOp(src=src, dst_rel=dst_rel):
                dst = project_root / dst_rel
                if not dry_run:
                    atomic_copy_file(src, dst)
                deployed.append(dst_rel)
                if verbose:
                    prefix = "[dry-run]   " if dry_run else "  "
                    console.print(f"{prefix}{dst_rel}")

            case CopyTreeOp(src_dir=src_dir, dst_rel=dst_rel):
                dst = project_root / dst_rel
                for src_file in _list_tree_files(src_dir):
                    rel = src_file.relative_to(src_dir)
                    file_dst_rel = str(Path(dst_rel) / rel)
                    if not dry_run:
                        atomic_copy_file(src_file, dst / rel)
                    deployed.append(file_dst_rel)
                    if verbose:
                        prefix = "[dry-run]   " if dry_run else "  "
                        console.print(f"{prefix}{file_dst_rel}")

            case WriteTextOp(content=content, dst_rel=dst_rel):
                if not dry_run:
                    atomic_write_text(project_root / dst_rel, content)
                deployed.append(dst_rel)
                if verbose:
                    prefix = "[dry-run]   " if dry_run else "  "
                    console.print(f"{prefix}{dst_rel}")

            case ManagedSectionOp(entries=entries, dst_rel=dst_rel):
                target_path = project_root / dst_rel
                if not dry_run:
                    existing = ""
                    if target_path.exists():
                        existing = target_path.read_text(encoding="utf-8")
                    new_content = merge_into_managed_section(existing, entries)
                    atomic_write_text(target_path, new_content)
                deployed.append(dst_rel)
                if verbose:
                    prefix = "[dry-run]   " if dry_run else "  "
                    console.print(f"{prefix}{dst_rel}")

            case IgnoreSectionOp(patterns=patterns, dst_rel=dst_rel):
                target_path = project_root / dst_rel
                if not dry_run:
                    existing = ""
                    if target_path.exists():
                        existing = target_path.read_text(encoding="utf-8")
                    new_content = merge_into_ignore_section(existing, patterns)
                    atomic_write_text(target_path, new_content)
                deployed.append(dst_rel)
                if verbose:
                    prefix = "[dry-run]   " if dry_run else "  "
                    console.print(f"{prefix}{dst_rel}")

            case MergeJsonOp(data=data, dst_rel=dst_rel, key=key):
                config_path = project_root / dst_rel
                if not dry_run:
                    _merge_json(config_path, key, data)
                deployed.append(dst_rel)
                if verbose:
                    prefix = "[dry-run]   " if dry_run else "  "
                    console.print(f"{prefix}{dst_rel}")

            case MergeTomlOp(data=data, dst_rel=dst_rel, key=key):
                config_path = project_root / dst_rel
                if not dry_run:
                    _merge_toml(config_path, key, data)
                deployed.append(dst_rel)
                if verbose:
                    prefix = "[dry-run]   " if dry_run else "  "
                    console.print(f"{prefix}{dst_rel}")

    return deployed


# ---------------------------------------------------------------------------
# JSON / TOML merge helpers
# ---------------------------------------------------------------------------


def _merge_json(config_path: Path, key: str, data: dict[str, Any]) -> None:
    """Merge data into a key of a JSON config file.

    When *key* is empty, merges directly into the top level.
    """
    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise WriteError(f"Failed to read {config_path}: {exc}") from exc

    if not key:
        existing.update(data)
    else:
        if key not in existing:
            existing[key] = {}
        existing[key].update(data)

    atomic_write_text(config_path, json.dumps(existing, indent=2) + "\n")


def _merge_toml(config_path: Path, key: str, data: dict[str, Any]) -> None:
    """Merge data into a key of a TOML config file."""
    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            raise WriteError(f"Failed to read {config_path}: {exc}") from exc

    if key not in existing:
        existing[key] = {}
    existing[key].update(data)

    atomic_write_text(config_path, tomli_w.dumps(existing))
