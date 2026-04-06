"""Skill asset handler."""

from __future__ import annotations

from pathlib import Path

from agpack.assets.base import AssetHandler
from agpack.assets.base import DeployError
from agpack.display import console
from agpack.fetcher import FetchResult
from agpack.fileutil import atomic_copy_file
from agpack.targets import SKILL_DIRS

# ---------------------------------------------------------------------------
# Tree copy
# ---------------------------------------------------------------------------


def _list_tree_files(src_dir: Path) -> list[Path]:
    """Return all non-.git files under *src_dir*, sorted."""
    return sorted(
        f
        for f in src_dir.rglob("*")
        if f.is_file() and not any(part.startswith(".git") for part in f.relative_to(src_dir).parts)
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


class SkillHandler(AssetHandler):
    """Handler for skill assets (directories or single files)."""

    resource_type = "skill"
    target_dirs = SKILL_DIRS

    def detect_items(self, fetch_result: FetchResult) -> list[tuple[str, Path]]:
        """Return ``(name, path)`` pairs for the skill items in a fetch result.

        A directory with top-level files is treated as a single skill.
        A directory with only subdirectories expands to one skill per subfolder.
        """
        local_path = fetch_result.local_path

        if local_path.is_dir() and not self._find_top_level_files(local_path):
            subfolders = self._find_asset_subfolders(local_path)
            if not subfolders:
                raise DeployError(
                    f"'{fetch_result.source.name}' is a directory but does not contain "
                    f"any skill folders. Provide a path to a skill folder or a "
                    f"directory containing skill folders."
                )
            return [(sf.name, sf) for sf in subfolders]

        return [(fetch_result.source.name, local_path)]

    def deploy_item(
        self,
        name: str,
        path: Path,
        targets: list[str],
        project_root: Path,
        dry_run: bool,
        verbose: bool,
    ) -> list[str]:
        """Deploy a single skill folder/file to all applicable target directories."""
        # Single-file skills go into a subdirectory named after the skill
        if not path.is_dir():
            return self._deploy_single_file_skill(name, path, targets, project_root, dry_run=dry_run, verbose=verbose)

        # Directory skills are tree-copied
        all_deployed: list[str] = []
        for target in targets:
            target_dir = self.target_dirs.get(target)
            if target_dir is None:
                continue

            dst = project_root / target_dir / name

            if dry_run and verbose:
                console.print(f"[dry-run]   copy {path} → {dst}")

            deployed = _copy_tree(path, dst, dry_run=dry_run)
            newly_deployed = [str(Path(d).relative_to(project_root)) for d in deployed]
            all_deployed.extend(newly_deployed)

            if verbose and not dry_run:
                for deployed_path in newly_deployed:
                    console.print(f"  {deployed_path}")

        return all_deployed

    def _deploy_single_file_skill(
        self,
        skill_name: str,
        file_path: Path,
        targets: list[str],
        project_root: Path,
        *,
        dry_run: bool,
        verbose: bool,
    ) -> list[str]:
        """Deploy a single-file skill into ``<target_dir>/<skill_name>/<file>``."""
        all_deployed: list[str] = []
        for target in targets:
            target_dir = self.target_dirs.get(target)
            if target_dir is None:
                continue

            dst = project_root / target_dir / skill_name / file_path.name

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
