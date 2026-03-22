"""Git fetch logic — clone repos and extract files."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agpack.config import DependencySource

# Match 7-40 hex chars (commit SHA)
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)


class FetchError(Exception):
    """Raised when a git fetch operation fails."""


@dataclass
class FetchResult:
    """Result of fetching a dependency."""

    source: DependencySource
    local_path: Path  # path to the extracted content (file or directory)
    resolved_ref: str  # full commit SHA
    _tmpdir: Path | None = None  # temp directory to clean up


def _is_sha(ref: str) -> bool:
    """Check if a ref looks like a commit SHA."""
    return bool(_SHA_RE.match(ref))


def _run_git(
    args: list[str], cwd: str | Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _clone(
    url: str,
    ref: str | None,
    tmpdir: Path,
    use_sparse: bool = False,
) -> Path:
    """Clone a repo from the given URL.

    Returns the path to the cloned repo directory.
    """
    clone_dir = tmpdir / "repo"

    is_sha = ref is not None and _is_sha(ref)

    cmd: list[str] = ["clone", "--depth", "1"]

    if use_sparse:
        cmd.extend(["--filter=blob:none", "--sparse"])

    # For branches/tags, use --branch. For SHAs, clone without --branch.
    if ref and not is_sha:
        cmd.extend(["--branch", ref])

    cmd.extend([url, str(clone_dir)])

    result = _run_git(cmd)

    if result.returncode != 0:
        raise FetchError(f"Failed to clone {url}:\n{result.stderr}")

    # If ref is a SHA, we need to fetch and checkout that specific commit
    if is_sha and ref is not None:
        _checkout_sha(clone_dir, ref)

    return clone_dir


def _checkout_sha(clone_dir: Path, sha: str) -> None:
    """Fetch and checkout a specific commit SHA."""
    # Try fetching the specific SHA (works on GitHub, not all hosts)
    result = _run_git(["fetch", "origin", sha], cwd=clone_dir)
    if result.returncode == 0:
        checkout = _run_git(["checkout", sha], cwd=clone_dir)
        if checkout.returncode == 0:
            return

    # Fallback: full fetch then checkout
    result = _run_git(["fetch", "--unshallow"], cwd=clone_dir)
    if result.returncode != 0:
        # If unshallow fails (e.g. already full), try a regular fetch
        result = _run_git(["fetch", "origin"], cwd=clone_dir)
        if result.returncode != 0:
            raise FetchError(f"Failed to fetch commit {sha}:\n{result.stderr}")

    checkout = _run_git(["checkout", sha], cwd=clone_dir)
    if checkout.returncode != 0:
        raise FetchError(f"Failed to checkout commit {sha}:\n{checkout.stderr}")


def _setup_sparse_checkout(clone_dir: Path, path: str) -> bool:
    """Set up sparse checkout for a specific path.

    Returns True on success, False on failure.
    """
    result = _run_git(["sparse-checkout", "set", path], cwd=clone_dir)
    return result.returncode == 0


def _get_resolved_ref(clone_dir: Path) -> str:
    """Get the full commit SHA of HEAD."""
    result = _run_git(["rev-parse", "HEAD"], cwd=clone_dir)
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip()


def fetch_dependency(source: DependencySource) -> FetchResult:
    """Fetch a dependency from a remote git repo.

    Clones the repo (with sparse checkout when possible), extracts the
    relevant path, and returns a FetchResult with the local path to
    the extracted content.

    The caller is responsible for cleaning up the returned local_path's
    parent temp directory when done.

    Args:
        source: The dependency to fetch.

    Returns:
        A FetchResult with the local path and resolved ref.

    Raises:
        FetchError: If the fetch fails.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="agpack-"))

    try:
        clone_dir: Path | None = None

        # Try sparse checkout first if we have a path
        if source.path is not None:
            try:
                clone_dir = _clone(
                    url=source.url,
                    ref=source.ref,
                    tmpdir=tmpdir,
                    use_sparse=True,
                )
                if not _setup_sparse_checkout(clone_dir, source.path):
                    # Sparse checkout failed, retry with full clone
                    shutil.rmtree(clone_dir)
                    clone_dir = None
            except FetchError:
                # Sparse clone itself failed, retry with full clone
                clone_dir = None

        if clone_dir is None:
            clone_dir = _clone(
                url=source.url,
                ref=source.ref,
                tmpdir=tmpdir,
                use_sparse=False,
            )

        resolved_ref = _get_resolved_ref(clone_dir)

        # Determine the local path to the content
        if source.path:
            content_path = clone_dir / source.path
            if not content_path.exists():
                raise FetchError(f"Path '{source.path}' not found in {source.url}")
        else:
            content_path = clone_dir

        return FetchResult(
            source=source,
            local_path=content_path,
            resolved_ref=resolved_ref,
            _tmpdir=tmpdir,
        )

    except Exception:
        # Clean up on failure
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def cleanup_fetch(result: FetchResult) -> None:
    """Clean up temporary files from a fetch operation."""
    if result._tmpdir is not None:
        shutil.rmtree(result._tmpdir, ignore_errors=True)
