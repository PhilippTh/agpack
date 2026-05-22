"""Git fetch logic — clone repos and extract files."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agpack.config import DependencySource
from agpack.envsubst import resolve_env_vars
from agpack.errors import FetchError

# Timeout (in seconds) for any single git subprocess call.  Prevents indefinite hangs when the remote is unreachable
# or git is waiting for credentials that will never arrive.
_GIT_TIMEOUT_SECONDS = 120

# Match 7-40 hex chars (commit SHA)
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)


def _redact(text: str) -> str:
    """Strip ``user:pass@`` userinfo from any ``scheme://...`` URL embedded in *text*.

    Git echoes the resolved URL it was asked to clone, which is the one place a resolved ``${GITHUB_TOKEN}`` still
    reaches user-visible output. :func:`_run_git` applies this unconditionally to every returned CompletedProcess
    (args, stdout, stderr); call sites use it directly on URL inputs they construct error messages from.

    SSH-style ``git@github.com:owner/repo`` URLs have no ``://`` and are left untouched — their ``user@host`` form is
    the scheme, not a secret.
    """
    return re.compile(r"([a-z][a-z0-9+\-.]*://)[^@/\s]+@").sub(r"\1", text)


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


def _run_git(args: list[str], cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result with userinfo scrubbed from stdout/stderr/args.

    The subprocess inherits the current environment with ``GIT_TERMINAL_PROMPT=0`` injected so that git never blocks
    waiting for interactive credentials input (e.g. when an HTTPS URL is used but only SSH authentication is
    configured). A timeout is also enforced to guard against network hangs.

    Every returned :class:`CompletedProcess` has its ``args``, ``stdout``, and ``stderr`` passed through
    :func:`_redact` so a resolved ``${GITHUB_TOKEN}`` in the URL never reaches a caller that might log the result.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=["git", *(_redact(a) for a in args)],
            returncode=1,
            stdout="",
            stderr=f"Git operation timed out after {_GIT_TIMEOUT_SECONDS} seconds",
        )
    return subprocess.CompletedProcess(
        args=["git", *(_redact(a) for a in args)],
        returncode=result.returncode,
        stdout=_redact(result.stdout),
        stderr=_redact(result.stderr),
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
        # ``url`` is the caller-supplied value (may carry a resolved ``${GITHUB_TOKEN}``) so it still needs scrubbing
        # here; ``result.stderr`` was already redacted inside _run_git.
        msg = f"Failed to clone {_redact(url)}:\n{result.stderr}"
        raise FetchError(msg)

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
            msg = f"Failed to fetch commit {sha}:\n{result.stderr}"
            raise FetchError(msg)

    checkout = _run_git(["checkout", sha], cwd=clone_dir)
    if checkout.returncode != 0:
        msg = f"Failed to checkout commit {sha}:\n{checkout.stderr}"
        raise FetchError(msg)


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


def _try_clone(
    url: str,
    ref: str | None,
    path: str | None,
    tmpdir: Path,
) -> Path:
    """Try to clone a repo from *url*, returning the clone directory.

    Attempts sparse checkout first when *path* is set, falling back to
    a full clone if sparse checkout isn't supported.

    Raises :class:`FetchError` if the clone fails.
    """
    clone_dir: Path | None = None

    # Try sparse checkout first if we have a path
    if path is not None:
        try:
            clone_dir = _clone(url=url, ref=ref, tmpdir=tmpdir, use_sparse=True)
            if not _setup_sparse_checkout(clone_dir, path):
                # Sparse checkout failed, retry with full clone
                shutil.rmtree(clone_dir)
                clone_dir = None
        except FetchError:
            # Sparse clone itself failed, retry with full clone
            clone_dir = None

    if clone_dir is None:
        clone_dir = _clone(url=url, ref=ref, tmpdir=tmpdir, use_sparse=False)

    return clone_dir


def fetch_dependency(source: DependencySource, env: dict[str, str] | None = None) -> FetchResult:
    """Fetch a dependency from a remote git repo.

    Clones the repo (with sparse checkout when possible), extracts the relevant path, and returns a FetchResult with
    the local path to the extracted content.

    When the primary URL fails and additional URLs are configured, each is tried in order until one succeeds.

    ``${VAR}`` references in :attr:`DependencySource.urls`, :attr:`~DependencySource.path`, and
    :attr:`~DependencySource.ref` are resolved here against *env* and used only for the ``git`` invocation — the
    resolved strings are never written back to ``source`` and never returned. Pre-cloning eager validation in
    :func:`agpack.config.resolve_config` guarantees missing variables fail before we get here, so a ``ConfigError``
    from this resolve path is a programmer error.

    The caller is responsible for cleaning up the returned local_path's parent temp directory when done.

    Args:
        source: The dependency to fetch (URL/path/ref are templates).
        env: Variable table for ``${VAR}`` substitution at clone time.
            Defaults to an empty dict (templates with no ``${VAR}``
            will work without one).

    Returns:
        A FetchResult with the local path and resolved ref.

    Raises:
        FetchError: If all URLs fail.
    """
    env = env or {}
    ctx = f"dependency '{source.name}'"

    # Resolve path/ref once (URL is resolved per-attempt below so each fallback URL gets its own context label in any
    # error message).
    resolved_path = resolve_env_vars(source.path, env, context=ctx) if source.path else None
    resolved_ref_template = resolve_env_vars(source.ref, env, context=ctx) if source.ref else None

    tmpdir = Path(tempfile.mkdtemp(prefix="agpack-"))

    try:
        last_error: FetchError | None = None

        for url_template in source.urls:
            resolved_url = resolve_env_vars(url_template, env, context=ctx)

            # Each attempt needs a clean clone directory
            clone_target = tmpdir / "repo"
            if clone_target.exists():
                shutil.rmtree(clone_target)

            try:
                clone_dir = _try_clone(
                    url=resolved_url,
                    ref=resolved_ref_template,
                    path=resolved_path,
                    tmpdir=tmpdir,
                )
            except FetchError as exc:
                last_error = exc
                continue

            head_sha = _get_resolved_ref(clone_dir)

            # Determine the local path to the content
            if resolved_path:
                content_path = clone_dir / resolved_path
                if not content_path.exists():
                    # ``url_template`` (not ``resolved_url``) so we never leak a substituted token even if the template
                    # itself appears in the surfaced error.
                    msg = f"Path '{resolved_path}' not found in {url_template}"
                    raise FetchError(msg)
            else:
                content_path = clone_dir

            return FetchResult(
                source=source,
                local_path=content_path,
                resolved_ref=head_sha,
                _tmpdir=tmpdir,
            )

        # All URLs failed — raise the last error
        raise last_error  # type: ignore[misc]

    except Exception:
        # Clean up on failure
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise


def cleanup_fetch(result: FetchResult) -> None:
    """Clean up temporary files from a fetch operation."""
    if result._tmpdir is not None:
        shutil.rmtree(result._tmpdir, ignore_errors=True)
