"""Shared infrastructure for the three deploy kinds.

Sits below all three kind modules in the import graph: error classes, atomic file I/O, directory-traversal helpers
used by both copy kinds. Nothing kind-specific belongs here — if you find yourself about to add behaviour that knows
about a single kind, put it in that kind's module instead.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path


class DeployError(Exception):
    """Raised when a copy-kind deployment fails."""


class EditFileError(Exception):
    """Raised when an edit-file deployment or cleanup fails."""


def atomic_copy_file(src: Path, dst: Path) -> None:
    """Copy a file atomically using write-to-temp-then-rename."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dst.parent, prefix=".agpack-tmp-")
    tmp = Path(tmp_path)
    try:
        os.close(fd)
        shutil.copy2(src, tmp)
        tmp.replace(dst)
    except Exception:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _atomic_write(path: Path, content: str) -> None:
    """Write text content to a file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".agpack-edit-")
    tmp = Path(tmp_path)
    try:
        os.close(fd)
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def write_if_changed(path: Path, new_text: str) -> bool:
    """Atomic write, but skip when the file already has this exact content.

    Returns ``True`` if a write happened, ``False`` if we noticed the file was already byte-identical and skipped. This
    is what keeps every-sync churn off the user's structured config files when nothing semantically changed.
    """
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == new_text:
                return False
        except OSError:
            pass  # Fall through to the write — file may be unreadable.
    _atomic_write(path, new_text)
    return True


def copy_tree(src_dir: Path, dst_dir: Path) -> list[str]:
    """Recursively copy a directory; return absolute destination paths."""
    deployed: list[str] = []
    for src_file in sorted(src_dir.rglob("*")):
        if src_file.is_file():
            rel = src_file.relative_to(src_dir)
            if any(part.startswith(".git") for part in rel.parts):
                continue
            dst_file = dst_dir / rel
            atomic_copy_file(src_file, dst_file)
            deployed.append(str(dst_file))
    return deployed


def find_asset_subfolders(path: Path) -> list[Path]:
    """Return immediate subdirectories that contain at least one file."""
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


def find_top_level_files(path: Path) -> list[Path]:
    """Return non-hidden files at the top level of a directory."""
    return sorted(item for item in path.iterdir() if item.is_file() and not item.name.startswith("."))
