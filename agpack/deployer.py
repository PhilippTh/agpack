"""Shared deployment utilities used by asset-type modules.

Asset-specific logic lives in :mod:`skills`, :mod:`commands`,
:mod:`agents`, :mod:`rules`, and :mod:`mcp`.  This module provides
the common building blocks they share.
"""

from __future__ import annotations

from pathlib import Path

from agpack.display import console
from agpack.fetcher import FetchResult
from agpack.fileutil import atomic_copy_file


class DeployError(Exception):
    """Raised when a file deployment fails."""


# ---------------------------------------------------------------------------
# Directory scanning helpers
# ---------------------------------------------------------------------------


def _find_asset_subfolders(path: Path) -> list[Path]:
    """Return immediate subdirectories that contain at least one file.

    Only non-.git subdirectories are considered.  Files may be nested
    arbitrarily deep inside the subfolder.
    """
    subfolders: list[Path] = []
    for item in sorted(path.iterdir()):
        if item.is_dir() and not item.name.startswith(".git"):
            has_files = any(
                f.is_file()
                and not any(p.startswith(".git") for p in f.relative_to(item).parts)
                for f in item.rglob("*")
            )
            if has_files:
                subfolders.append(item)
    return subfolders


def _find_top_level_files(path: Path) -> list[Path]:
    """Return non-hidden files at the top level of a directory."""
    return sorted(
        item
        for item in path.iterdir()
        if item.is_file() and not item.name.startswith(".")
    )


# ---------------------------------------------------------------------------
# Tree copy
# ---------------------------------------------------------------------------


def _list_tree_files(src_dir: Path) -> list[Path]:
    """Return all non-.git files under *src_dir*, sorted."""
    return sorted(
        f
        for f in src_dir.rglob("*")
        if f.is_file()
        and not any(part.startswith(".git") for part in f.relative_to(src_dir).parts)
    )


def _copy_tree(src_dir: Path, dst_dir: Path, *, dry_run: bool = False) -> list[str]:
    """Recursively copy a directory, returning list of destination paths.

    Uses atomic file copies for each file.  In dry-run mode the
    destination paths are computed but no files are written.
    """
    deployed: list[str] = []
    for src_file in _list_tree_files(src_dir):
        rel = src_file.relative_to(src_dir)
        dst_file = dst_dir / rel
        if not dry_run:
            atomic_copy_file(src_file, dst_file)
        deployed.append(str(dst_file))
    return deployed


# ---------------------------------------------------------------------------
# Generic detection and single-file deployment
# ---------------------------------------------------------------------------


def detect_file_items(
    fetch_result: FetchResult, asset_type: str
) -> list[tuple[str, Path]]:
    """Return ``(name, path)`` pairs for file assets (commands, agents, rules)."""
    local_path = fetch_result.local_path

    if local_path.is_dir():
        files = _find_top_level_files(local_path)
        if not files:
            for sf in _find_asset_subfolders(local_path):
                files.extend(_find_top_level_files(sf))
        if not files:
            article = "an" if asset_type[0] in "aeiou" else "a"
            raise DeployError(
                f"'{fetch_result.source.name}' is a directory but does not contain "
                f"any {asset_type} files. Provide a path to {article} {asset_type} "
                f"file or a directory containing {asset_type} files."
            )
        return [(f.name, f) for f in files]

    return [(fetch_result.source.name, local_path)]


def deploy_single_file(
    filename: str,
    file_path: Path,
    targets: list[str],
    target_dirs: dict[str, str],
    project_root: Path,
    dry_run: bool,
    verbose: bool,
    subdirectory: str | None = None,
) -> list[str]:
    """Deploy a single file to all applicable target directories.

    When *subdirectory* is given the file is placed inside
    ``<target_dir>/<subdirectory>/<filename>`` (used for single-file skills).
    """
    all_deployed: list[str] = []

    for target in targets:
        target_dir = target_dirs.get(target)
        if target_dir is None:
            continue

        base = project_root / target_dir
        if subdirectory:
            base = base / subdirectory
        dst = base / filename

        if dry_run:
            if verbose:
                console.print(f"[dry-run]   copy → {dst}")
            all_deployed.append(str(dst.relative_to(project_root)))
            continue

        atomic_copy_file(file_path, dst)
        all_deployed.append(str(dst.relative_to(project_root)))

        if verbose:
            console.print(f"  {dst.relative_to(project_root)}")

    return all_deployed


# ---------------------------------------------------------------------------
# Cleanup
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
    """Remove empty parent directories left behind after file deletion.

    Only removes directories that are within known agpack-managed prefixes.
    """
    # Collect unique parent directories, sorted deepest-first
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
