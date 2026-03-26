"""File copy logic and directory mapping for deploying resources."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from agpack.display import console
from agpack.fetcher import FetchResult
from agpack.targets import AGENT_DIRS
from agpack.targets import COMMAND_DIRS
from agpack.targets import SKILL_DIRS


class DeployError(Exception):
    """Raised when a file deployment fails."""


@dataclass
class DeployResult:
    """Result of deploying one or more assets from a single dependency."""

    files: list[str] = field(default_factory=list)
    expanded_items: list[str] = field(default_factory=list)


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
        item for item in path.iterdir() if item.is_file() and not item.name.startswith(".")
    )


# ---------------------------------------------------------------------------
# Item detection — figure out what a dependency expands to
# ---------------------------------------------------------------------------


def detect_skill_items(fetch_result: FetchResult) -> list[tuple[str, Path]]:
    """Return ``(name, path)`` pairs for the skill items in a fetch result.

    A directory with top-level files is treated as a single skill.
    A directory with only subdirectories expands to one skill per subfolder.
    """
    local_path = fetch_result.local_path

    if local_path.is_dir() and not _find_top_level_files(local_path):
        subfolders = _find_asset_subfolders(local_path)
        if not subfolders:
            raise DeployError(
                f"'{fetch_result.source.name}' is a directory but does not contain "
                f"any skill folders. Provide a path to a skill folder or a "
                f"directory containing skill folders."
            )
        return [(sf.name, sf) for sf in subfolders]

    return [(fetch_result.source.name, local_path)]


def detect_file_items(
    fetch_result: FetchResult, asset_type: str
) -> list[tuple[str, Path]]:
    """Return ``(name, path)`` pairs for file assets (commands / agents)."""
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


def detect_command_items(fetch_result: FetchResult) -> list[tuple[str, Path]]:
    """Return ``(name, path)`` pairs for command items."""
    return detect_file_items(fetch_result, "command")


def detect_agent_items(fetch_result: FetchResult) -> list[tuple[str, Path]]:
    """Return ``(name, path)`` pairs for agent items."""
    return detect_file_items(fetch_result, "agent")


# ---------------------------------------------------------------------------
# Single-item deployment
# ---------------------------------------------------------------------------


def deploy_single_skill(
    skill_name: str,
    skill_path: Path,
    targets: list[str],
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    """Deploy a single skill folder/file to all applicable target directories."""
    all_deployed: list[str] = []

    for target in targets:
        target_dir = SKILL_DIRS.get(target)
        if target_dir is None:
            continue

        dst = project_root / target_dir / skill_name

        if dry_run:
            if verbose:
                console.print(f"[dry-run]   copy {skill_path} → {dst}")
            if skill_path.is_dir():
                for f in sorted(skill_path.rglob("*")):
                    if f.is_file() and not any(
                        p.startswith(".git")
                        for p in f.relative_to(skill_path).parts
                    ):
                        rel = dst / f.relative_to(skill_path)
                        all_deployed.append(str(rel.relative_to(project_root)))
            else:
                all_deployed.append(
                    str((dst / skill_path.name).relative_to(project_root))
                )
            continue

        newly_deployed: list[str] = []
        if skill_path.is_dir():
            deployed = _copy_tree(skill_path, dst)
            newly_deployed = [str(Path(d).relative_to(project_root)) for d in deployed]
        else:
            dst_file = dst / skill_path.name
            _atomic_copy_file(skill_path, dst_file)
            newly_deployed = [str(dst_file.relative_to(project_root))]

        all_deployed.extend(newly_deployed)

        if verbose:
            for deployed_path in newly_deployed:
                console.print(f"  {deployed_path}")

    return all_deployed


def deploy_skill(
    fetch_result: FetchResult,
    targets: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> DeployResult:
    """Deploy a skill to all applicable target directories.

    If the fetched path is a directory that contains files, it is deployed as
    a single skill.  If it contains only subdirectories (no top-level files),
    each subdirectory that itself contains files is deployed as a separate
    skill.  An error is raised when neither condition is met.
    """
    items = detect_skill_items(fetch_result)
    expanded_items = [name for name, _ in items] if len(items) > 1 else []

    all_deployed: list[str] = []
    for skill_name, skill_path in items:
        deployed = deploy_single_skill(
            skill_name, skill_path, targets, project_root, dry_run, verbose
        )
        all_deployed.extend(deployed)

    return DeployResult(files=all_deployed, expanded_items=expanded_items)


# ---------------------------------------------------------------------------
# Commands & Agents (single-file assets)
# ---------------------------------------------------------------------------


def deploy_single_file(
    filename: str,
    file_path: Path,
    targets: list[str],
    target_dirs: dict[str, str],
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    """Deploy a single file to all applicable target directories."""
    all_deployed: list[str] = []

    for target in targets:
        target_dir = target_dirs.get(target)
        if target_dir is None:
            continue

        dst = project_root / target_dir / filename

        if dry_run:
            if verbose:
                console.print(f"[dry-run]   copy → {dst}")
            all_deployed.append(str(dst.relative_to(project_root)))
            continue

        _atomic_copy_file(file_path, dst)
        all_deployed.append(str(dst.relative_to(project_root)))

        if verbose:
            console.print(f"  {dst.relative_to(project_root)}")

    return all_deployed


def deploy_single_command(
    filename: str,
    file_path: Path,
    targets: list[str],
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    """Deploy a single command file to all applicable target directories."""
    return deploy_single_file(
        filename, file_path, targets, COMMAND_DIRS, project_root, dry_run, verbose
    )


def deploy_single_agent(
    filename: str,
    file_path: Path,
    targets: list[str],
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    """Deploy a single agent file to all applicable target directories."""
    return deploy_single_file(
        filename, file_path, targets, AGENT_DIRS, project_root, dry_run, verbose
    )


def _deploy_file_asset(
    fetch_result: FetchResult,
    targets: list[str],
    target_dirs: dict[str, str],
    asset_type: str,
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> DeployResult:
    """Deploy single-file asset(s) to all applicable target directories.

    If the fetched path is a directory, non-hidden files are collected from
    the top level (or from subfolders if the top level has none).  An error
    is raised when no deployable files are found.
    """
    items = detect_file_items(fetch_result, asset_type)
    expanded_items = [name for name, _ in items] if len(items) > 1 else []

    all_deployed: list[str] = []
    for filename, file_path in items:
        deployed = deploy_single_file(
            filename, file_path, targets, target_dirs, project_root, dry_run, verbose
        )
        all_deployed.extend(deployed)

    return DeployResult(files=all_deployed, expanded_items=expanded_items)


def deploy_command(
    fetch_result: FetchResult,
    targets: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> DeployResult:
    """Deploy command file(s) to all applicable target directories."""
    return _deploy_file_asset(
        fetch_result, targets, COMMAND_DIRS, "command", project_root, dry_run, verbose
    )


def deploy_agent(
    fetch_result: FetchResult,
    targets: list[str],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> DeployResult:
    """Deploy agent file(s) to all applicable target directories."""
    return _deploy_file_asset(
        fetch_result, targets, AGENT_DIRS, "agent", project_root, dry_run, verbose
    )


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
