"""The three asset kinds agpack knows how to deploy.

A *kind* is the fundamental way agpack interacts with the filesystem:

* :class:`CopyDirectoryResource` (``kind: copy-directory``) — copy a
  directory tree from a fetched git repo into ``<path>/<name>/`` on
  the target. Used by skill bundles.
* :class:`CopyFileResource` (``kind: copy-file``) — copy individual
  files from a fetched git repo into ``<path>/<name>`` on the target.
  Used by commands and agents.
* :class:`EditFileResource` (``kind: edit-file``) — read a structured
  (JSON / TOML) config file, apply :class:`Patch` operations, write it
  back. Patches are fully generic — a list of ``{key, value, strategy}``
  triples that the engine applies without any per-domain knowledge.
  Used for MCP server configs, Claude Code hooks, permissions, VS Code
  extensions, or any other structured config the user can describe.

Each kind owns its own ``detect`` (where applicable), ``deploy_*``, and
``cleanup_*`` logic; the deployer and CLI orchestrate but never branch
on kind themselves.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar
from typing import Literal

import tomli_w

from agpack.display import console

if TYPE_CHECKING:
    from agpack.fetcher import FetchResult

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DeployError(Exception):
    """Raised when a copy-kind deployment fails."""


class EditFileError(Exception):
    """Raised when an edit-file deployment or cleanup fails."""


# ---------------------------------------------------------------------------
# Format inference
# ---------------------------------------------------------------------------


_FORMAT_BY_SUFFIX = {".json": "json", ".toml": "toml"}


def infer_config_format(path: str) -> Literal["json", "toml"]:
    """Return the format for an edit-file config path.

    The extension is the single source of truth — there is no override.
    """
    lower = path.lower()
    if lower.endswith(".toml"):
        return "toml"
    if lower.endswith(".json"):
        return "json"
    valid = ", ".join(sorted(_FORMAT_BY_SUFFIX))
    raise EditFileError(
        f"cannot infer config format from '{path}' — "
        f"path must end in one of: {valid}"
    )


# ---------------------------------------------------------------------------
# Atomic-write primitives (shared by all kinds)
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


def _atomic_write(path: Path, content: str) -> None:
    """Write text content to a file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".agpack-edit-")
    try:
        os.close(fd)
        Path(tmp_path).write_text(content, encoding="utf-8")
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _copy_tree(src_dir: Path, dst_dir: Path) -> list[str]:
    """Recursively copy a directory; return absolute destination paths."""
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
# kind: copy-directory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CopyDirectoryResource:
    """Deploys items as directory bundles under ``<path>/<name>/``.

    A directory dependency with top-level files is treated as a single
    bundle; a directory containing only subdirectories expands to one
    bundle per subfolder.
    """

    path: str
    kind: ClassVar[str] = "copy-directory"

    def detect(
        self, fetch_result: FetchResult, label: str
    ) -> list[tuple[str, Path]]:
        local_path = fetch_result.local_path

        if local_path.is_dir() and not _find_top_level_files(local_path):
            subfolders = _find_asset_subfolders(local_path)
            if not subfolders:
                raise DeployError(
                    f"'{fetch_result.source.name}' is a directory but does not "
                    f"contain any {label} folders. Provide a path to a {label} "
                    f"folder or a directory containing {label} folders."
                )
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
                    if f.is_file() and not any(
                        p.startswith(".git")
                        for p in f.relative_to(src_path).parts
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

        return deployed


# ---------------------------------------------------------------------------
# kind: copy-file
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CopyFileResource:
    """Deploys items as individual files at ``<path>/<name>``."""

    path: str
    kind: ClassVar[str] = "copy-file"

    def detect(
        self, fetch_result: FetchResult, label: str
    ) -> list[tuple[str, Path]]:
        local_path = fetch_result.local_path

        if local_path.is_dir():
            files = _find_top_level_files(local_path)
            if not files:
                for sf in _find_asset_subfolders(local_path):
                    files.extend(_find_top_level_files(sf))
            if not files:
                article = "an" if label[0] in "aeiou" else "a"
                raise DeployError(
                    f"'{fetch_result.source.name}' is a directory but does not "
                    f"contain any {label} files. Provide a path to {article} "
                    f"{label} file or a directory containing {label} files."
                )
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

        _atomic_copy_file(src_path, dst)
        return [str(dst.relative_to(project_root))]


# ---------------------------------------------------------------------------
# kind: edit-file — Patch model
# ---------------------------------------------------------------------------


Strategy = Literal["replace", "append"]


@dataclass(frozen=True)
class Patch:
    """A single change to apply to a structured config file.

    Attributes:
        key: Dotted path into the config file (``mcpServers.filesystem``
            or ``hooks.PreToolUse``). Intermediate dicts are auto-created.
        value: What to put at the path. For ``append``, this is a single
            element appended to the list at ``key``.
        strategy: ``"replace"`` overwrites whatever's at the path;
            ``"append"`` requires the path to resolve to a list (created
            empty if absent) and appends ``value``.
    """

    key: str
    value: Any
    strategy: Strategy = "replace"


# ---------------------------------------------------------------------------
# Dotted-path navigation
# ---------------------------------------------------------------------------


def _split_key(key: str) -> list[str]:
    if not key:
        raise EditFileError("patch key must be non-empty")
    return key.split(".")


def _walk_to_parent(
    root: dict[str, Any], segments: list[str]
) -> tuple[dict[str, Any], str]:
    """Walk ``root`` along ``segments[:-1]``, returning (parent, last_segment).

    Missing intermediate dicts are auto-created. If an existing
    intermediate value is *not* a dict, raises :class:`EditFileError`
    rather than silently overwriting user data.
    """
    parent: dict[str, Any] = root
    for seg in segments[:-1]:
        if seg in parent and not isinstance(parent[seg], dict):
            raise EditFileError(
                f"patch path traverses non-dict at '{seg}': "
                f"existing value is {type(parent[seg]).__name__}"
            )
        parent = parent.setdefault(seg, {})
    return parent, segments[-1]


def _apply_patch(root: dict[str, Any], patch: Patch) -> None:
    """Apply ``patch`` to ``root`` in-place."""
    segments = _split_key(patch.key)
    parent, leaf = _walk_to_parent(root, segments)

    if patch.strategy == "replace":
        parent[leaf] = patch.value
        return

    # append
    bucket = parent.setdefault(leaf, [])
    if not isinstance(bucket, list):
        raise EditFileError(
            f"patch with strategy='append' targets non-list at '{patch.key}': "
            f"got {type(bucket).__name__}"
        )
    bucket.append(patch.value)


def _cleanup_patch(root: dict[str, Any], patch: Patch) -> bool:
    """Undo ``patch`` on ``root`` in-place. Returns True if anything changed.

    ``replace`` deletes the leaf key. ``append`` scans the list at the
    path and removes the first deep-equal match. Either way, silent
    no-op if the target is missing or already gone.
    """
    segments = _split_key(patch.key)

    # Walk read-only — don't auto-create on cleanup.
    parent: Any = root
    for seg in segments[:-1]:
        if not isinstance(parent, dict) or seg not in parent:
            return False
        parent = parent[seg]
    if not isinstance(parent, dict):
        return False

    leaf = segments[-1]
    if leaf not in parent:
        return False

    if patch.strategy == "replace":
        del parent[leaf]
        return True

    # append: remove the first deep-equal match from the list
    bucket = parent[leaf]
    if not isinstance(bucket, list):
        return False
    for i, item in enumerate(bucket):
        if item == patch.value:
            del bucket[i]
            return True
    return False


# ---------------------------------------------------------------------------
# kind: edit-file
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EditFileResource:
    """Applies :class:`Patch` operations to a JSON or TOML config file.

    The file format is inferred from :attr:`path`'s extension. The
    patch shape is fully generic — agpack reads the file, applies each
    patch (replace or append), and writes it back atomically. No
    per-domain (MCP, hooks, etc.) knowledge lives here.
    """

    path: str
    kind: ClassVar[str] = "edit-file"

    @property
    def format(self) -> Literal["json", "toml"]:
        return infer_config_format(self.path)

    def apply_patches(
        self,
        patches: list[Patch],
        project_root: Path,
        *,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        """Apply each patch to the config file at :attr:`path`.

        Reads the file once, applies all patches in order, writes once.
        """
        if not patches:
            return

        config_path = project_root / self.path

        if dry_run:
            if verbose:
                for p in patches:
                    console.print(
                        f"[dry-run]   {p.strategy} {self.path}:{p.key}"
                    )
            return

        data = _read_existing(config_path, self.format)
        for patch in patches:
            _apply_patch(data, patch)

        try:
            _atomic_write(config_path, _dump(data, self.format))
        except OSError as exc:
            raise EditFileError(
                f"Failed to write {config_path}: {exc}"
            ) from exc

        if verbose:
            for p in patches:
                console.print(f"  {p.strategy} {self.path}:{p.key}")

    def cleanup_patches(
        self,
        patches: list[Patch],
        project_root: Path,
        *,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        """Undo each patch on the config file at :attr:`path`.

        Silent no-op if the file is missing or any individual patch
        has nothing to remove. Format-inference failures (stale
        lockfile entries pointing at unknown extensions) are absorbed.
        """
        if not patches:
            return

        config_path = project_root / self.path
        if not config_path.exists():
            return

        try:
            format_ = self.format
        except EditFileError as exc:
            if verbose:
                console.print(
                    f"  skipping cleanup of {self.path}: {exc}"
                )
            return

        if dry_run:
            if verbose:
                for p in patches:
                    console.print(
                        f"[dry-run]   remove {self.path}:{p.key}"
                    )
            return

        data = _read_existing(config_path, format_)
        changed = False
        for patch in patches:
            if _cleanup_patch(data, patch):
                changed = True

        if changed:
            _atomic_write(config_path, _dump(data, format_))

        if verbose:
            for p in patches:
                console.print(f"  removed {self.path}:{p.key}")


# ---------------------------------------------------------------------------
# JSON / TOML I/O
# ---------------------------------------------------------------------------


def _read_existing(config_path: Path, format_: str) -> dict[str, Any]:
    """Read an existing JSON/TOML config file, or return an empty dict."""
    if not config_path.exists():
        return {}
    text = config_path.read_text(encoding="utf-8")
    try:
        data: Any
        if format_ == "json":
            data = json.loads(text)
        else:
            data = tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, OSError) as exc:
        raise EditFileError(f"Failed to read {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise EditFileError(f"{config_path}: top-level must be a mapping")
    return data


def _dump(data: dict[str, Any], format_: str) -> str:
    """Serialise a dict back to JSON or TOML text."""
    if format_ == "json":
        return json.dumps(data, indent=2) + "\n"
    return tomli_w.dumps(data)


# ---------------------------------------------------------------------------
# Type alias for any kind
# ---------------------------------------------------------------------------


ResourceDef = CopyDirectoryResource | CopyFileResource | EditFileResource
CopyResource = CopyDirectoryResource | CopyFileResource
