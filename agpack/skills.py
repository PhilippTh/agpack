"""Skill detection and deployment."""

from __future__ import annotations

from pathlib import Path

from agpack.deployer import DeployError
from agpack.deployer import _copy_tree
from agpack.deployer import _find_asset_subfolders
from agpack.deployer import _find_top_level_files
from agpack.deployer import deploy_single_file
from agpack.display import console
from agpack.fetcher import FetchResult
from agpack.targets import SKILL_DIRS


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


def deploy_single_skill(
    skill_name: str,
    skill_path: Path,
    targets: list[str],
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    """Deploy a single skill folder/file to all applicable target directories."""
    # Single-file skills use the same path as deploy_single_file
    if not skill_path.is_dir():
        return deploy_single_file(
            skill_path.name,
            skill_path,
            targets,
            SKILL_DIRS,
            project_root,
            dry_run,
            verbose,
            subdirectory=skill_name,
        )

    all_deployed: list[str] = []

    for target in targets:
        target_dir = SKILL_DIRS.get(target)
        if target_dir is None:
            continue

        dst = project_root / target_dir / skill_name

        if dry_run and verbose:
            console.print(f"[dry-run]   copy {skill_path} → {dst}")

        deployed = _copy_tree(skill_path, dst, dry_run=dry_run)
        newly_deployed = [str(Path(d).relative_to(project_root)) for d in deployed]
        all_deployed.extend(newly_deployed)

        if verbose and not dry_run:
            for deployed_path in newly_deployed:
                console.print(f"  {deployed_path}")

    return all_deployed
