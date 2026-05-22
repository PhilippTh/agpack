"""``kind: copy-file`` — items deploy as ``<path>/<name>`` (flat files).

Used for resources whose consumer expects a *file per item* on disk (Claude commands, agents, Codex prompts). When the
source is a directory, top-level files become items; if the top level has no files, agpack recurses into
subdirectories to find them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import ClassVar

from agpack.display import console
from agpack.errors import DeployError
from agpack.kinds._shared import atomic_copy_file
from agpack.kinds._shared import find_asset_subfolders
from agpack.kinds._shared import find_top_level_files

if TYPE_CHECKING:
    from agpack.fetcher import FetchResult


@dataclass(frozen=True)
class CopyFileResource:
    """Deploys items as individual files at ``<path>/<name>``."""

    path: str
    kind: ClassVar[str] = "copy-file"

    def detect(self, fetch_result: FetchResult, label: str) -> list[tuple[str, Path]]:
        local_path = fetch_result.local_path

        if local_path.is_dir():
            files = find_top_level_files(local_path)
            if not files:
                for sf in find_asset_subfolders(local_path):
                    files.extend(find_top_level_files(sf))
            if not files:
                article = "an" if label[0] in "aeiou" else "a"
                msg = (
                    f"'{fetch_result.source.name}' is a directory but does not "
                    f"contain any {label} files. Provide a path to {article} "
                    f"{label} file or a directory containing {label} files."
                )
                raise DeployError(msg)
            return [(f.name, f) for f in files]

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

        if dry_run:
            if verbose:
                console.print(f"[dry-run]   copy → {dst}")
            return [str(dst.relative_to(project_root))]

        atomic_copy_file(src_path, dst)
        return [str(dst.relative_to(project_root))]
