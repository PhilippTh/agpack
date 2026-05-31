"""Orchestration over :mod:`agpack.kinds`.

Per-kind behavior lives on the resource dataclasses themselves (in :mod:`agpack.kinds`); this module only loops over
targets and forwards to the right kind. The two public entrypoints handle the two fundamentally different deployment
shapes:

* Copy kinds (``copy-directory`` / ``copy-file``): a tree of items is fetched from a git repo, detected, and copied
  to each target that declares the matching resource type. :func:`detect_items` and :func:`deploy_item` cover this
  path.
* Edit kind (``edit-file``): a list of :class:`~agpack.kinds.Patch` operations declared inline in ``agpack.yml`` is
  reconciled against the lockfile's record of previously-applied patches. :func:`sync_edit_resource` walks every
  target with a matching edit-file resource and does a diff-based per-file sync: removed ``replace`` patches delete
  the leaf; removed ``append`` patches drop the previously-appended list entry; unchanged patches are left strictly
  alone so the file isn't even written when nothing semantically changed.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from agpack.display import console
from agpack.errors import DeployError
from agpack.fetcher import FetchResult
from agpack.kinds import CopyResource
from agpack.kinds import EditFileResource
from agpack.kinds import ResourceDef
from agpack.lockfile import AppliedPatch
from agpack.patch import Patch
from agpack.target_schema import TargetDef

# ===========================================================================
# Copy kinds — fetch + detect + deploy
# ===========================================================================


def detect_items(fetch_result: FetchResult, resource: ResourceDef, label: str) -> list[tuple[str, Path]]:
    """Return ``(name, source-path)`` pairs for the items in a fetch result."""
    if isinstance(resource, CopyResource):
        return resource.detect(fetch_result, label)
    msg = f"detect_items called with a {resource.kind} resource; only copy kinds support detection"
    raise DeployError(msg)


def deploy_item(
    name: str,
    src_path: Path,
    resource_type: str,
    targets: list[TargetDef],
    project_root: Path,
) -> list[str]:
    """Deploy one item to every target that supports ``resource_type``."""
    deployed: list[str] = []
    for target in targets:
        resource = target.resources.get(resource_type)
        if not isinstance(resource, CopyResource):
            continue
        deployed.extend(resource.deploy_item(name, src_path, project_root))

    return deployed


# ===========================================================================
# Edit-file kind — patches
# ===========================================================================


def sync_edit_resource(
    resource_type: str,
    desired: list[Patch],
    applied_old: list[AppliedPatch],
    targets: list[TargetDef],
    project_root: Path,
    env_vars: dict[str, str] | None = None,
    *,
    verbose: bool = False,
) -> list[AppliedPatch]:
    """Reconcile every target's edit-file resource of ``resource_type``.

    ``desired`` is the current list of patches from ``agpack.yml``. ``applied_old`` is the lockfile's record of what
    was applied for this resource type on the previous sync (pass the entries from one ``EditLockEntry``).

    Each target with a matching edit-file resource gets its own per-file diff: matching patches are left alone, removed
    patches are reversed (``replace`` deletes the leaf; ``append`` drops the previously-appended entry), added patches
    are applied fresh. Targets that don't declare this resource type are silently skipped; targets with a non-edit-file
    kind for this name are also skipped (the cross-target kind consistency check in ``cli._resource_kinds`` should have
    caught any actual conflict).

    The returned :class:`AppliedPatch` list is the new authoritative state for the lockfile.
    """
    # Group old applied entries by file_path so each target picks up only what was previously written to *its* file.
    old_by_file: dict[str, list[AppliedPatch]] = defaultdict(list)
    for entry in applied_old:
        old_by_file[entry.file_path].append(entry)

    new_applied: list[AppliedPatch] = []
    matched_any = False
    touched_files: set[str] = set()

    # Dedup by `resource.path`: when two targets resolve to the same edit-file (duplicate `targets:` entries, or two
    # distinct targets both pointing at e.g. `.mcp.json`), reconciling once per target double-applies every `append`
    # patch on every sync, leaving orphaned entries the lockfile can't track.
    for target in targets:
        resource = target.resources.get(resource_type)
        if not isinstance(resource, EditFileResource):
            continue
        matched_any = True
        if resource.path in touched_files:
            continue
        touched_files.add(resource.path)
        new_applied.extend(
            resource.sync_patches(
                applied_old=old_by_file.get(resource.path, []),
                desired_new=desired,
                project_root=project_root,
                env_vars=env_vars,
                verbose=verbose,
            )
        )

    # Files that used to be touched by this resource type but aren't any more (e.g. a target was removed from
    # ``targets:``) need their old patches reversed too. Resolved keys live on the entries, so cleanup needs no
    # target lookup.
    for file_path, leftovers in old_by_file.items():
        if file_path in touched_files or not leftovers:
            continue
        EditFileResource(path=file_path).cleanup_patches(leftovers, project_root, verbose=verbose)

    if not matched_any and desired:
        console.print(
            f"[yellow]warning[/yellow]: {len(desired)} '{resource_type}' "
            f"patch(es) configured but no target declares an edit-file "
            f"resource for '{resource_type}'."
        )

    return new_applied


def cleanup_orphaned_edits(
    applied_old: list[AppliedPatch],
    project_root: Path,
    *,
    verbose: bool = False,
) -> None:
    """Reverse every recorded patch for a resource type that no longer exists in ``dependencies:``.

    Each :class:`AppliedPatch` carries its already-resolved key, so cleanup needs neither the originating target nor
    the env table — it dispatches directly to the file for each path it has records for.
    """
    by_file: dict[str, list[AppliedPatch]] = defaultdict(list)
    for ap in applied_old:
        by_file[ap.file_path].append(ap)
    for file_path, entries in by_file.items():
        EditFileResource(path=file_path).cleanup_patches(entries, project_root, verbose=verbose)


# ===========================================================================
# Cleanup (copy-kind files)
# ===========================================================================


def cleanup_deployed_files(
    deployed_files: list[str],
    project_root: Path,
    verbose: bool = False,
) -> None:
    """Remove previously deployed files and clean up empty directories."""
    for rel_path in deployed_files:
        full_path = project_root / rel_path
        if full_path.exists():
            full_path.unlink()
            if verbose:
                console.print(f"  deleted {rel_path}")

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
