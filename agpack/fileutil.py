"""Shared file-system utilities."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write *content* to *path* atomically (write-to-temp-then-rename).

    Creates parent directories as needed.  On failure the temp file is
    cleaned up so no partial writes are left on disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".agpack-tmp-")
    try:
        os.close(fd)
        Path(tmp_path).write_text(content, encoding=encoding)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_copy_file(src: Path, dst: Path) -> None:
    """Copy *src* to *dst* atomically using write-to-temp-then-rename.

    Creates parent directories as needed.
    """
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
