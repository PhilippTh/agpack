"""Orchestration over :mod:`agpack.kinds`.

Per-kind behavior lives on the resource dataclasses themselves (in
:mod:`agpack.kinds`); this module only loops over targets and forwards
to the right kind. The two public entrypoints handle the two
fundamentally different deployment shapes:

* Copy kinds (``copy-directory`` / ``copy-file``): a tree of items is
  fetched from a git repo, detected, and copied to each target that
  declares the matching resource type. :func:`detect_items` and
  :func:`deploy_item` cover this path.
* Edit kind (``edit-file``): a list of :class:`~agpack.kinds.Patch`
  operations declared inline in ``agpack.yml`` is applied to each
  matching target's config file. :func:`apply_patches_to_targets` and
  :func:`cleanup_applied_patches` cover this path.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import cast

from agpack.display import console
from agpack.fetcher import FetchResult
from agpack.kinds import CopyResource
from agpack.kinds import DeployError
from agpack.kinds import EditFileResource
from agpack.kinds import Patch
from agpack.kinds import ResourceDef
from agpack.kinds import Strategy
from agpack.lockfile import AppliedPatch
from agpack.target_schema import TargetDef

# ===========================================================================
# Copy kinds — fetch + detect + deploy
# ===========================================================================


def detect_items(
    fetch_result: FetchResult, resource: ResourceDef, label: str
) -> list[tuple[str, Path]]:
    """Return ``(name, source-path)`` pairs for the items in a fetch result."""
    if isinstance(resource, CopyResource):
        return resource.detect(fetch_result, label)
    raise DeployError(
        f"detect_items called with a {resource.kind} resource; "
        "only copy kinds support detection"
    )


def deploy_item(
    name: str,
    src_path: Path,
    resource_type: str,
    targets: list[TargetDef],
    project_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Deploy one item to every target that supports ``resource_type``."""
    deployed: list[str] = []
    for target in targets:
        resource = target.resources.get(resource_type)
        if not isinstance(resource, CopyResource):
            continue
        deployed.extend(
            resource.deploy_item(
                name, src_path, project_root, dry_run=dry_run, verbose=verbose
            )
        )

    if verbose and not dry_run:
        for entry in deployed:
            console.print(f"  {entry}")

    return deployed


# ===========================================================================
# Edit-file kind — patches
# ===========================================================================


def apply_patches_to_targets(
    resource_type: str,
    patches: list[Patch],
    targets: list[TargetDef],
    project_root: Path,
    env_vars: dict[str, str] | None = None,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[AppliedPatch]:
    """Apply ``patches`` to every target's edit-file resource of ``resource_type``.

    ``${name}`` references in each patch are resolved per-target at
    apply time, with the target's own ``vars`` taking precedence over
    ``env_vars``. Targets that don't declare this resource type, or
    that declare it with a non-edit-file kind, are silently skipped.

    The returned :class:`AppliedPatch` records carry the
    *post-substitution* keys and values so cleanup can reverse each
    operation by deep equality without rerunning substitution.
    """
    if not patches:
        return []

    applied: list[AppliedPatch] = []
    matched_any = False

    for target in targets:
        resource = target.resources.get(resource_type)
        if not isinstance(resource, EditFileResource):
            continue
        matched_any = True
        resolved = resource.apply_patches(
            patches, project_root, env_vars,
            dry_run=dry_run, verbose=verbose,
        )
        for patch in resolved:
            applied.append(
                AppliedPatch(
                    file_path=resource.path,
                    key=patch.key,
                    strategy=patch.strategy,
                    value=patch.value,
                )
            )

    if not matched_any:
        console.print(
            f"[yellow]warning[/yellow]: {len(patches)} '{resource_type}' "
            f"patch(es) configured but no target declares an edit-file "
            f"resource for '{resource_type}'."
        )

    return applied


def cleanup_applied_patches(
    applied: list[AppliedPatch],
    project_root: Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Reverse every recorded patch, grouped by target file for efficiency.

    Patches with no recoverable file (missing, bad extension) are
    silently skipped — they'll naturally disappear on the next sync.
    """
    by_file: dict[str, list[Patch]] = defaultdict(list)
    for entry in applied:
        by_file[entry.file_path].append(
            Patch(
                key=entry.key,
                value=entry.value,
                strategy=cast(Strategy, entry.strategy),
            )
        )

    for file_path, patches in by_file.items():
        resource = EditFileResource(path=file_path)
        resource.cleanup_patches(
            patches, project_root, dry_run=dry_run, verbose=verbose
        )


# ===========================================================================
# Cleanup (copy-kind files)
# ===========================================================================


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
