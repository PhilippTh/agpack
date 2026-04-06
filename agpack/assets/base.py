"""Base class for asset handlers.

Provides the default detect/deploy behaviour shared by most asset
types.  Subclasses that follow the common single-file pattern only
need to set ``resource_type`` and ``target_dirs``.
"""

from __future__ import annotations

from pathlib import Path

from agpack.config import DependencySource
from agpack.display import console
from agpack.fetcher import FetchResult
from agpack.fileutil import atomic_copy_file


class DeployError(Exception):
    """Raised when a file deployment fails."""


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


# ---------------------------------------------------------------------------
# Asset handler base class
# ---------------------------------------------------------------------------


class AssetHandler:
    """Base class for all fetchable asset types.

    The default ``detect_items`` scans a fetched directory for files
    (or falls back to subfolder scanning).  The default ``deploy_item``
    copies a single file to every applicable target directory.

    Subclasses like ``CommandHandler`` / ``AgentHandler`` only need to
    set ``resource_type`` and ``target_dirs``.  More specialised types
    (skills, rules) override methods as needed.
    """

    resource_type: str = ""
    target_dirs: dict[str, str] = {}

    def __init__(self, deps: list[DependencySource]) -> None:
        self.deps = deps

    # ---------------------------------------------------------------------------
    # Directory scanning helpers
    # ---------------------------------------------------------------------------

    def _find_asset_subfolders(self, path: Path) -> list[Path]:
        """Return immediate subdirectories that contain at least one file.

        Only non-.git subdirectories are considered.  Files may be nested
        arbitrarily deep inside the subfolder.
        """
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

    def _find_top_level_files(self, path: Path) -> list[Path]:
        """Return non-hidden files at the top level of a directory."""
        return sorted(item for item in path.iterdir() if item.is_file() and not item.name.startswith("."))

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_items(self, fetch_result: FetchResult) -> list[tuple[str, Path]]:
        """
        Default case: single file detection.
        Override in subclasses that need more complex detection logic.

        Returns a list of ``(name, path)`` pairs for items in a fetch result.

        * Single file → one item.
        * Directory with top-level files → one item per file.
        * Directory with only subdirectories → recurse into each.
        """
        local_path = fetch_result.local_path

        if local_path.is_dir():
            files = self._find_top_level_files(local_path)
            if not files:
                for sf in self._find_asset_subfolders(local_path):
                    files.extend(self._find_top_level_files(sf))
            if not files:
                article = "an" if self.resource_type[0] in "aeiou" else "a"
                raise DeployError(
                    f"'{fetch_result.source.name}' is a directory but does not contain "
                    f"any {self.resource_type} files. Provide a path to {article} "
                    f"{self.resource_type} file or a directory containing "
                    f"{self.resource_type} files."
                )
            return [(f.name, f) for f in files]

        return [(fetch_result.source.name, local_path)]

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------

    def deploy_item(
        self,
        name: str,
        path: Path,
        targets: list[str],
        project_root: Path,
        dry_run: bool,
        verbose: bool,
    ) -> list[str]:
        """
        Default case: single file deployment to target directories.
        Override in subclasses that need more complex deployment logic.

        Returns a list of relative paths of deployed files.
        """
        all_deployed: list[str] = []

        for target in targets:
            target_dir = self.target_dirs.get(target)
            if target_dir is None:
                continue

            dst = project_root / target_dir / name

            if dry_run:
                if verbose:
                    console.print(f"[dry-run]   copy → {dst}")
                all_deployed.append(str(dst.relative_to(project_root)))
                continue

            atomic_copy_file(path, dst)
            all_deployed.append(str(dst.relative_to(project_root)))

            if verbose:
                console.print(f"  {dst.relative_to(project_root)}")

        return all_deployed

    # ------------------------------------------------------------------
    # Post-deploy hook
    # ------------------------------------------------------------------

    def finalize(self, targets: list[str], project_root: Path, dry_run: bool, verbose: bool) -> list[str]:  # noqa: ARG002
        """Post-deploy hook called after all items have been deployed.

        The default implementation is a no-op.  Override in subclasses
        that need batch processing (e.g. rules append targets).

        Returns a list of relative paths of additionally deployed files.
        """
        return []
