"""File copy logic and deployment driven by TargetDef.resources."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from agpack.display import console
from agpack.fetcher import FetchResult
from agpack.target_schema import TargetDef


class DeployError(Exception):
    """Raised when a file deployment fails."""


@dataclass
class DeployResult:
    """Result of deploying one or more assets from a single dependency."""

    files: list[str] = field(default_factory=list)
    expanded_items: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Atomic copy primitives
# ---------------------------------------------------------------------------


def _atomic_copy_file(src: Path, dst: Path) -> None:
    """Copy a file atomically using write-to-temp-then-rename."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dst.parent, prefix=".agpack-tmp-")
    try:
        os.close(fd)
        shutil.copy2(str(src), tmp_path)
        os.replace(tmp_path, str(dst))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _copy_tree(src_dir: Path, dst_dir: Path) -> list[str]:
    """Recursively copy a directory, returning list of destination paths."""
    deployed: list[str] = []
    for src_file in sorted(src_dir.rglob("*")):
        if src_file.is_file():
            rel = src_file.relative_to(src_dir)
            if any(part.startswith(".git") for part in rel.parts):
                continue
            dst_file = dst_dir / rel
            _atomic_copy_file(src_file, dst_file)
            deployed.append(str(dst_file))
    return deployed


def _find_asset_subfolders(path: Path) -> list[Path]:
    """Return immediate subdirectories that contain at least one file."""
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
# Single-item deployment — generic across resource types
# ---------------------------------------------------------------------------


def _deploy_item_to_target(
    name: str,
    src_path: Path,
    target: TargetDef,
    resource_type: str,
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    """Deploy one item to a single target, or no-op if unsupported.

    For ``layout: directory`` the destination is ``<path>/<name>/`` and
    either the whole source tree or a single source file is placed
    inside.  For ``layout: file`` the destination is ``<path>/<name>``
    directly (single file copy).
    """
    layout = target.resources.get(resource_type)
    if layout is None:
        return []

    dst = project_root / layout.path / name
    deployed: list[str] = []

    if layout.layout == "directory":
        if dry_run:
            if src_path.is_dir():
                for f in sorted(src_path.rglob("*")):
                    if f.is_file() and not any(
                        p.startswith(".git") for p in f.relative_to(src_path).parts
                    ):
                        rel = dst / f.relative_to(src_path)
                        deployed.append(str(rel.relative_to(project_root)))
            else:
                deployed.append(str((dst / src_path.name).relative_to(project_root)))
            if verbose:
                console.print(f"[dry-run]   copy {src_path} → {dst}")
            return deployed

        if src_path.is_dir():
            for copied in _copy_tree(src_path, dst):
                deployed.append(str(Path(copied).relative_to(project_root)))
        else:
            dst_file = dst / src_path.name
            _atomic_copy_file(src_path, dst_file)
            deployed.append(str(dst_file.relative_to(project_root)))
    else:
        if dry_run:
            deployed.append(str(dst.relative_to(project_root)))
            if verbose:
                console.print(f"[dry-run]   copy → {dst}")
            return deployed

        _atomic_copy_file(src_path, dst)
        deployed.append(str(dst.relative_to(project_root)))

    if verbose:
        for entry in deployed:
            console.print(f"  {entry}")

    return deployed


def deploy_single_skill(
    skill_name: str,
    skill_path: Path,
    targets: list[TargetDef],
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    """Deploy a single skill folder/file to all applicable targets."""
    all_deployed: list[str] = []
    for target in targets:
        all_deployed.extend(
            _deploy_item_to_target(
                skill_name,
                skill_path,
                target,
                "skills",
                project_root,
                dry_run,
                verbose,
            )
        )
    return all_deployed


def deploy_single_command(
    filename: str,
    file_path: Path,
    targets: list[TargetDef],
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    """Deploy a single command file to all applicable targets."""
    all_deployed: list[str] = []
    for target in targets:
        all_deployed.extend(
            _deploy_item_to_target(
                filename,
                file_path,
                target,
                "commands",
                project_root,
                dry_run,
                verbose,
            )
        )
    return all_deployed


def deploy_single_agent(
    filename: str,
    file_path: Path,
    targets: list[TargetDef],
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    """Deploy a single agent file to all applicable targets."""
    all_deployed: list[str] = []
    for target in targets:
        all_deployed.extend(
            _deploy_item_to_target(
                filename,
                file_path,
                target,
                "agents",
                project_root,
                dry_run,
                verbose,
            )
        )
    return all_deployed


# ---------------------------------------------------------------------------
# Whole-fetch deployment — detects items and deploys each
# ---------------------------------------------------------------------------


def _deploy_fetch_items(
    items: list[tuple[str, Path]],
    resource_type: str,
    targets: list[TargetDef],
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> DeployResult:
    expanded_items = [name for name, _ in items] if len(items) > 1 else []
    all_deployed: list[str] = []
    for item_name, item_path in items:
        for target in targets:
            all_deployed.extend(
                _deploy_item_to_target(
                    item_name,
                    item_path,
                    target,
                    resource_type,
                    project_root,
                    dry_run,
                    verbose,
                )
            )
    return DeployResult(files=all_deployed, expanded_items=expanded_items)


def deploy_skill(
    fetch_result: FetchResult,
    targets: list[TargetDef],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> DeployResult:
    """Deploy a fetched skill (or directory of skills) to all targets."""
    return _deploy_fetch_items(
        detect_skill_items(fetch_result),
        "skills",
        targets,
        project_root,
        dry_run,
        verbose,
    )


def deploy_command(
    fetch_result: FetchResult,
    targets: list[TargetDef],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> DeployResult:
    """Deploy fetched command file(s) to all applicable targets."""
    return _deploy_fetch_items(
        detect_command_items(fetch_result),
        "commands",
        targets,
        project_root,
        dry_run,
        verbose,
    )


def deploy_agent(
    fetch_result: FetchResult,
    targets: list[TargetDef],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> DeployResult:
    """Deploy fetched agent file(s) to all applicable targets."""
    return _deploy_fetch_items(
        detect_agent_items(fetch_result),
        "agents",
        targets,
        project_root,
        dry_run,
        verbose,
    )


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
