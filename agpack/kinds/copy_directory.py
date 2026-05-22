"""``kind: copy-directory`` — items deploy as ``<path>/<name>/<files…>``.

Used for resources whose consumer expects a *folder per item* on disk (Claude skills are the canonical example: each
skill is a directory containing ``SKILL.md`` plus assets). A source directory of only-subdirectories expands to one
bundle per subfolder; a source directory with top-level files becomes a single bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import ClassVar

from agpack.display import console
from agpack.kinds._shared import DeployError
from agpack.kinds._shared import atomic_copy_file
from agpack.kinds._shared import copy_tree
from agpack.kinds._shared import find_asset_subfolders
from agpack.kinds._shared import find_top_level_files

if TYPE_CHECKING:
    from agpack.fetcher import FetchResult


@dataclass(frozen=True)
class CopyDirectoryResource:
    """Deploys items as directory bundles under ``<path>/<name>/``.

    A directory dependency with top-level files is treated as a single bundle; a directory containing only
    subdirectories expands to one bundle per subfolder.
    """

    path: str
    kind: ClassVar[str] = "copy-directory"

    def detect(self, fetch_result: FetchResult, label: str) -> list[tuple[str, Path]]:
        local_path = fetch_result.local_path

        if local_path.is_dir() and not find_top_level_files(local_path):
            subfolders = find_asset_subfolders(local_path)
            if not subfolders:
                msg = (
                    f"'{fetch_result.source.name}' is a directory but does not "
                    f"contain any {label} folders. Provide a path to a {label} "
                    f"folder or a directory containing {label} folders."
                )
                raise DeployError(msg)
            return [(sf.name, sf) for sf in subfolders]

        return [(fetch_result.source.name, local_path)]

    def deploy_item(
        self,
        item_name: str,
        src_path: Path,
        project_root: Path,
        *,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> list[str]:
        dst = project_root / self.path / item_name
        deployed: list[str] = []

        if dry_run:
            if src_path.is_dir():
                for f in sorted(src_path.rglob("*")):
                    if f.is_file() and not any(p.startswith(".git") for p in f.relative_to(src_path).parts):
                        rel = dst / f.relative_to(src_path)
                        deployed.append(str(rel.relative_to(project_root)))
            else:
                deployed.append(str((dst / src_path.name).relative_to(project_root)))
            if verbose:
                console.print(f"[dry-run]   copy {src_path} → {dst}")
            return deployed

        if src_path.is_dir():
            for copied in copy_tree(src_path, dst):
                deployed.append(str(Path(copied).relative_to(project_root)))
        else:
            dst_file = dst / src_path.name
            atomic_copy_file(src_path, dst_file)
            deployed.append(str(dst_file.relative_to(project_root)))

        return deployed
