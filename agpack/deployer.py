"""File copy logic and directory mapping for deploying resources."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from agpack.display import console
from agpack.fetcher import FetchResult
from agpack.targets import AGENT_DIRS
from agpack.targets import COMMAND_DIRS
from agpack.targets import SKILL_DIRS


class DeployError(Exception):
    """Raised when a file deployment fails."""


def _atomic_copy_file(src: Path, dst: Path) -> None:
    """Copy a file atomically using write-to-temp-then-rename.

    Creates parent directories as needed.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Write to a temp file in the same directory, then rename
    fd, tmp_path = tempfile.mkstemp(dir=dst.parent, prefix=".agpack-tmp-")
    try:
        os.close(fd)
        shutil.copy2(str(src), tmp_path)
        os.replace(tmp_path, str(dst))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _copy_tree(src_dir: Path, dst_dir: Path) -> list[str]:
    """Recursively copy a directory, returning list of relative paths written.

    Uses atomic file copies for each file.
    """
    deployed: list[str] = []
    for src_file in sorted(src_dir.rglob("*")):
        if src_file.is_file():
            # Skip git metadata
            rel = src_file.relative_to(src_dir)
            if any(part.startswith(".git") for part in rel.parts):
                continue
            dst_file = dst_dir / rel
            _atomic_copy_file(src_file, dst_file)
            deployed.append(str(dst_file))
    return deployed


def deploy_skill(
    fetch_result: FetchResult,
    targets: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Deploy a skill to all applicable target directories.

    Returns list of all deployed file paths (relative to project_root).
    """
    skill_name = fetch_result.source.name
    all_deployed: list[str] = []

    for target in targets:
        target_dir = SKILL_DIRS.get(target)
        if target_dir is None:
            continue

        dst = project_root / target_dir / skill_name

        if dry_run:
            if verbose:
                console.print(f"[dry-run]   copy {fetch_result.local_path} → {dst}")
            # Collect what would be deployed
            if fetch_result.local_path.is_dir():
                for f in sorted(fetch_result.local_path.rglob("*")):
                    if f.is_file() and not any(
                        p.startswith(".git")
                        for p in f.relative_to(fetch_result.local_path).parts
                    ):
                        rel = dst / f.relative_to(fetch_result.local_path)
                        all_deployed.append(str(rel.relative_to(project_root)))
            else:
                all_deployed.append(
                    str((dst / fetch_result.local_path.name).relative_to(project_root))
                )
            continue

        newly_deployed: list[str] = []
        if fetch_result.local_path.is_dir():
            deployed = _copy_tree(fetch_result.local_path, dst)
            newly_deployed = [str(Path(d).relative_to(project_root)) for d in deployed]
        else:
            dst_file = dst / fetch_result.local_path.name
            _atomic_copy_file(fetch_result.local_path, dst_file)
            newly_deployed = [str(dst_file.relative_to(project_root))]

        all_deployed.extend(newly_deployed)

        if verbose:
            for deployed_path in newly_deployed:
                console.print(f"  {deployed_path}")

    return all_deployed


def deploy_command(
    fetch_result: FetchResult,
    targets: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Deploy a command file to all applicable target directories.

    Returns list of all deployed file paths (relative to project_root).
    """
    filename = fetch_result.source.name
    all_deployed: list[str] = []

    for target in targets:
        target_dir = COMMAND_DIRS.get(target)
        if target_dir is None:
            # This target doesn't support commands — skip silently
            continue

        dst = project_root / target_dir / filename

        if dry_run:
            if verbose:
                console.print(f"[dry-run]   copy → {dst}")
            all_deployed.append(str(dst.relative_to(project_root)))
            continue

        _atomic_copy_file(fetch_result.local_path, dst)
        all_deployed.append(str(dst.relative_to(project_root)))

        if verbose:
            console.print(f"  {dst.relative_to(project_root)}")

    return all_deployed


def deploy_agent(
    fetch_result: FetchResult,
    targets: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Deploy an agent file to all applicable target directories.

    Returns list of all deployed file paths (relative to project_root).
    """
    filename = fetch_result.source.name
    all_deployed: list[str] = []

    for target in targets:
        target_dir = AGENT_DIRS.get(target)
        if target_dir is None:
            # This target doesn't support agents — skip silently
            continue

        dst = project_root / target_dir / filename

        if dry_run:
            if verbose:
                console.print(f"[dry-run]   copy → {dst}")
            all_deployed.append(str(dst.relative_to(project_root)))
            continue

        _atomic_copy_file(fetch_result.local_path, dst)
        all_deployed.append(str(dst.relative_to(project_root)))

        if verbose:
            console.print(f"  {dst.relative_to(project_root)}")

    return all_deployed


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
