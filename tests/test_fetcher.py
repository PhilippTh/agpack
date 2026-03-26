"""Tests for agpack.fetcher — git clone, sparse checkout, and cleanup."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from agpack.config import DependencySource
from agpack.fetcher import _GIT_TIMEOUT_SECONDS
from agpack.fetcher import FetchError
from agpack.fetcher import FetchResult
from agpack.fetcher import _is_sha
from agpack.fetcher import _run_git
from agpack.fetcher import cleanup_fetch
from agpack.fetcher import fetch_dependency

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    """Return a successful CompletedProcess."""
    return subprocess.CompletedProcess(
        args=["git"], returncode=0, stdout=stdout, stderr=""
    )


def _fail(stderr: str = "error") -> subprocess.CompletedProcess[str]:
    """Return a failed CompletedProcess."""
    return subprocess.CompletedProcess(
        args=["git"], returncode=1, stdout="", stderr=stderr
    )


FAKE_SHA_FULL = "a" * 40
FAKE_SHA_SHORT = "abc1234"


# ---------------------------------------------------------------------------
# _is_sha
# ---------------------------------------------------------------------------


class TestIsSha:
    """Tests for _is_sha recognising valid and invalid SHA strings."""

    @pytest.mark.parametrize(
        "value",
        [
            "a" * 7,
            "a" * 40,
            "AbCdEf1",
            "0123456789abcdef0123456789abcdef01234567",
            "ABCDEF0",
        ],
    )
    def test_valid_sha(self, value: str) -> None:
        assert _is_sha(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "main",
            "v1.0.0",
            "a" * 6,  # too short
            "a" * 41,  # too long
            "ghijkl0",  # non-hex chars
            "",
            "abc123!",
        ],
    )
    def test_invalid_sha(self, value: str) -> None:
        assert _is_sha(value) is False


# ---------------------------------------------------------------------------
# fetch_dependency
# ---------------------------------------------------------------------------


class TestFetchDependency:
    """Tests for fetch_dependency with mocked git subprocess calls."""

    @patch("agpack.fetcher._run_git")
    def test_clone_succeeds(self, mock_git: MagicMock, tmp_path: Path) -> None:
        """Clone succeeds — URL is passed directly to git clone."""
        source = DependencySource(url="https://github.com/owner/repo", ref="main")

        def side_effect(args: list[str], cwd=None):  # noqa: ARG001
            if args[0] == "clone":
                # Simulate the clone creating the repo directory
                clone_dir = Path(args[-1])
                clone_dir.mkdir(parents=True, exist_ok=True)
                return _ok()
            if args[0] == "rev-parse":
                return _ok(stdout=FAKE_SHA_FULL + "\n")
            return _ok()

        mock_git.side_effect = side_effect

        with patch("agpack.fetcher.tempfile.mkdtemp", return_value=str(tmp_path)):
            result = fetch_dependency(source)

        assert result.resolved_ref == FAKE_SHA_FULL
        assert result.source is source

        # Verify the exact URL was passed to git clone
        clone_calls = [c for c in mock_git.call_args_list if c[0][0][0] == "clone"]
        assert len(clone_calls) == 1
        clone_args = clone_calls[0][0][0]
        assert "https://github.com/owner/repo" in clone_args
        assert "--branch" in clone_args
        assert "main" in clone_args

    @patch("agpack.fetcher._run_git")
    def test_clone_fails_raises_fetch_error(
        self, mock_git: MagicMock, tmp_path: Path
    ) -> None:
        """When clone fails, FetchError is raised."""
        source = DependencySource(url="https://github.com/owner/repo", ref="main")

        mock_git.return_value = _fail(stderr="connection refused")

        with (
            patch("agpack.fetcher.tempfile.mkdtemp", return_value=str(tmp_path)),
            pytest.raises(FetchError, match="Failed to clone"),
        ):
            fetch_dependency(source)

    @patch("agpack.fetcher._run_git")
    def test_sparse_checkout_when_path_set(
        self, mock_git: MagicMock, tmp_path: Path
    ) -> None:
        """When source.path is set, sparse checkout is attempted."""
        source = DependencySource(
            url="https://github.com/owner/repo", ref="main", path="skills/foo"
        )

        def side_effect(args: list[str], cwd=None):  # noqa: ARG001
            if args[0] == "clone":
                clone_dir = Path(args[-1])
                clone_dir.mkdir(parents=True, exist_ok=True)
                # Pre-create the path that would be checked later
                (clone_dir / "skills" / "foo").mkdir(parents=True, exist_ok=True)
                return _ok()
            if args[0] == "sparse-checkout":
                return _ok()
            if args[0] == "rev-parse":
                return _ok(stdout=FAKE_SHA_FULL + "\n")
            return _ok()

        mock_git.side_effect = side_effect

        with patch("agpack.fetcher.tempfile.mkdtemp", return_value=str(tmp_path)):
            result = fetch_dependency(source)

        assert result.local_path == tmp_path / "repo" / "skills" / "foo"

        # Verify clone was called with sparse flags
        clone_calls = [c for c in mock_git.call_args_list if c[0][0][0] == "clone"]
        assert len(clone_calls) == 1
        clone_args = clone_calls[0][0][0]
        assert "--sparse" in clone_args
        assert "--filter=blob:none" in clone_args

        # Verify sparse-checkout set was called
        sparse_calls = [
            c for c in mock_git.call_args_list if c[0][0][0] == "sparse-checkout"
        ]
        assert len(sparse_calls) == 1
        assert sparse_calls[0][0][0] == ["sparse-checkout", "set", "skills/foo"]

    @patch("agpack.fetcher._run_git")
    def test_sparse_checkout_fallback_to_full_clone(
        self, mock_git: MagicMock, tmp_path: Path
    ) -> None:
        """When sparse checkout fails, a full clone is performed."""
        source = DependencySource(
            url="https://github.com/owner/repo", ref="main", path="lib/bar"
        )
        clone_attempt = {"count": 0}

        def side_effect(args: list[str], cwd=None):  # noqa: ARG001
            if args[0] == "clone":
                clone_dir = Path(args[-1])
                clone_attempt["count"] += 1

                if clone_attempt["count"] == 1:
                    # First clone (sparse) succeeds
                    clone_dir.mkdir(parents=True, exist_ok=True)
                    return _ok()
                else:
                    # Second clone (full) succeeds
                    clone_dir.mkdir(parents=True, exist_ok=True)
                    (clone_dir / "lib" / "bar").mkdir(parents=True, exist_ok=True)
                    return _ok()

            if args[0] == "sparse-checkout":
                return _fail(stderr="sparse-checkout not supported")

            if args[0] == "rev-parse":
                return _ok(stdout=FAKE_SHA_FULL + "\n")

            return _ok()

        mock_git.side_effect = side_effect

        with patch("agpack.fetcher.tempfile.mkdtemp", return_value=str(tmp_path)):
            result = fetch_dependency(source)

        assert result.resolved_ref == FAKE_SHA_FULL
        assert result.local_path == tmp_path / "repo" / "lib" / "bar"

        # Verify two clones happened: first sparse, then full
        clone_calls = [c for c in mock_git.call_args_list if c[0][0][0] == "clone"]
        assert len(clone_calls) == 2

        sparse_clone_args = clone_calls[0][0][0]
        assert "--sparse" in sparse_clone_args

        full_clone_args = clone_calls[1][0][0]
        assert "--sparse" not in full_clone_args

    @patch("agpack.fetcher._run_git")
    def test_sha_ref_no_branch_flag(self, mock_git: MagicMock, tmp_path: Path) -> None:
        """When ref is a SHA, --branch is NOT used; fetch+checkout is done."""
        sha = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        source = DependencySource(url="https://github.com/owner/repo", ref=sha)

        def side_effect(args: list[str], cwd=None):  # noqa: ARG001
            if args[0] == "clone":
                clone_dir = Path(args[-1])
                clone_dir.mkdir(parents=True, exist_ok=True)
                return _ok()
            if args[0] == "fetch":
                return _ok()
            if args[0] == "checkout":
                return _ok()
            if args[0] == "rev-parse":
                return _ok(stdout=sha + "\n")
            return _ok()

        mock_git.side_effect = side_effect

        with patch("agpack.fetcher.tempfile.mkdtemp", return_value=str(tmp_path)):
            result = fetch_dependency(source)

        assert result.resolved_ref == sha

        # Verify clone did NOT use --branch
        clone_calls = [c for c in mock_git.call_args_list if c[0][0][0] == "clone"]
        assert len(clone_calls) == 1
        clone_args = clone_calls[0][0][0]
        assert "--branch" not in clone_args

        # Verify fetch+checkout of the SHA was performed
        fetch_calls = [c for c in mock_git.call_args_list if c[0][0][0] == "fetch"]
        assert len(fetch_calls) >= 1
        assert sha in fetch_calls[0][0][0]

        checkout_calls = [
            c for c in mock_git.call_args_list if c[0][0][0] == "checkout"
        ]
        assert len(checkout_calls) == 1
        assert sha in checkout_calls[0][0][0]

    @patch("agpack.fetcher._run_git")
    def test_path_not_found_raises_fetch_error(
        self, mock_git: MagicMock, tmp_path: Path
    ) -> None:
        """When source.path does not exist after clone, FetchError is raised."""
        source = DependencySource(
            url="https://github.com/owner/repo", ref="main", path="does/not/exist"
        )

        def side_effect(args: list[str], cwd=None):  # noqa: ARG001
            if args[0] == "clone":
                clone_dir = Path(args[-1])
                clone_dir.mkdir(parents=True, exist_ok=True)
                return _ok()
            if args[0] == "sparse-checkout":
                return _ok()
            if args[0] == "rev-parse":
                return _ok(stdout=FAKE_SHA_FULL + "\n")
            return _ok()

        mock_git.side_effect = side_effect

        with (
            patch("agpack.fetcher.tempfile.mkdtemp", return_value=str(tmp_path)),
            pytest.raises(FetchError, match="Path.*not found"),
        ):
            fetch_dependency(source)


# ---------------------------------------------------------------------------
# cleanup_fetch
# ---------------------------------------------------------------------------


class TestCleanupFetch:
    """Tests for cleanup_fetch removing the temp directory."""

    def test_removes_temp_directory(self, tmp_path: Path) -> None:
        """cleanup_fetch removes the _tmpdir stored in FetchResult."""
        agpack_dir = tmp_path / "agpack-xyz123"
        repo_dir = agpack_dir / "repo"
        content_dir = repo_dir / "skills" / "foo"
        content_dir.mkdir(parents=True)
        (content_dir / "SKILL.md").write_text("hello")

        source = DependencySource(
            url="https://github.com/owner/repo", path="skills/foo"
        )
        result = FetchResult(
            source=source,
            local_path=content_dir,
            resolved_ref=FAKE_SHA_FULL,
            _tmpdir=agpack_dir,
        )

        cleanup_fetch(result)

        assert not agpack_dir.exists()

    def test_no_error_when_already_gone(self, tmp_path: Path) -> None:
        """cleanup_fetch does not raise if the directory is already removed."""
        gone_dir = tmp_path / "agpack-gone"

        source = DependencySource(url="https://github.com/owner/repo")
        result = FetchResult(
            source=source,
            local_path=gone_dir / "repo",
            resolved_ref=FAKE_SHA_FULL,
            _tmpdir=gone_dir,
        )

        # Should not raise
        cleanup_fetch(result)

    def test_noop_when_tmpdir_is_none(self) -> None:
        """cleanup_fetch is a no-op when _tmpdir is None."""
        source = DependencySource(url="https://github.com/owner/repo")
        result = FetchResult(
            source=source,
            local_path=Path("/nonexistent"),
            resolved_ref=FAKE_SHA_FULL,
        )

        # Should not raise
        cleanup_fetch(result)


# ---------------------------------------------------------------------------
# _run_git — environment & timeout
# ---------------------------------------------------------------------------


class TestRunGit:
    """Tests for _run_git subprocess behaviour."""

    @patch("agpack.fetcher.subprocess.run", return_value=_ok())
    def test_sets_git_terminal_prompt_env(self, mock_run: MagicMock) -> None:
        """GIT_TERMINAL_PROMPT=0 is passed in the environment."""
        _run_git(["status"])

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"

    @patch("agpack.fetcher.subprocess.run", return_value=_ok())
    def test_passes_timeout(self, mock_run: MagicMock) -> None:
        """A timeout is forwarded to subprocess.run."""
        _run_git(["status"])

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == _GIT_TIMEOUT_SECONDS

    @patch(
        "agpack.fetcher.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git clone", timeout=120),
    )
    def test_timeout_returns_failed_result(self, mock_run: MagicMock) -> None:  # noqa: ARG002
        """TimeoutExpired is caught and converted to a failed CompletedProcess."""
        result = _run_git(["clone", "https://example.com/repo"])

        assert result.returncode == 1
        assert "timed out" in result.stderr
