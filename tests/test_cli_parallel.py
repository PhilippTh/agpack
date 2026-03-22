"""Tests for parallel fetch behaviour in _sync_resource_type."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import click
import pytest

from agpack.cli import _MAX_FETCH_WORKERS
from agpack.cli import _sync_resource_type
from agpack.config import AgpackConfig
from agpack.config import DependencySource
from agpack.fetcher import FetchError
from agpack.fetcher import FetchResult
from agpack.lockfile import Lockfile


def _make_dep(name: str) -> DependencySource:
    return DependencySource(url=f"https://github.com/owner/{name}")


def _make_result(dep: DependencySource, tmp_path: Path) -> FetchResult:
    d = tmp_path / dep.name
    d.mkdir(exist_ok=True)
    return FetchResult(source=dep, local_path=d, resolved_ref="abc1234", _tmpdir=d)


def _make_config(targets: list[str] | None = None) -> AgpackConfig:
    return AgpackConfig(
        name="test",
        version="0.1.0",
        targets=targets or ["claude"],
    )


class TestParallelFetchAllSucceed:
    def test_all_fetched_and_deployed(self, tmp_path: Path) -> None:
        deps = [_make_dep("a"), _make_dep("b"), _make_dep("c")]
        fake_results = {dep.name: _make_result(dep, tmp_path) for dep in deps}
        config = _make_config()
        new_lockfile = Lockfile()

        def fake_fetch(dep: DependencySource) -> FetchResult:
            return fake_results[dep.name]

        deploy_fn = MagicMock(return_value=["file.md"])

        with (
            patch("agpack.cli.fetch_dependency", side_effect=fake_fetch),
            patch("agpack.cli.cleanup_fetch"),
        ):
            count = _sync_resource_type(
                deps,
                deploy_fn,
                "skill",
                config,
                tmp_path,
                new_lockfile,
                dry_run=False,
                verbose=False,
            )

        assert count == 3
        assert deploy_fn.call_count == 3
        assert len(new_lockfile.installed) == 3

    def test_lockfile_entries_added(self, tmp_path: Path) -> None:
        deps = [_make_dep("x")]
        fake_result = _make_result(deps[0], tmp_path)
        config = _make_config()
        new_lockfile = Lockfile()

        with (
            patch("agpack.cli.fetch_dependency", return_value=fake_result),
            patch("agpack.cli.cleanup_fetch"),
        ):
            _sync_resource_type(
                deps,
                MagicMock(return_value=["f.md"]),
                "skill",
                config,
                tmp_path,
                new_lockfile,
                dry_run=False,
                verbose=False,
            )

        assert len(new_lockfile.installed) == 1
        assert new_lockfile.installed[0].url == deps[0].url


class TestParallelFetchCollectAllErrors:
    def test_all_errors_reported(self, tmp_path: Path) -> None:
        deps = [_make_dep("a"), _make_dep("b"), _make_dep("c")]
        config = _make_config()
        new_lockfile = Lockfile()

        def fake_fetch(dep: DependencySource) -> FetchResult:
            raise FetchError(f"failed {dep.name}")

        deploy_fn = MagicMock()

        with (
            patch("agpack.cli.fetch_dependency", side_effect=fake_fetch),
            patch("agpack.cli.write_lockfile") as mock_write,
            pytest.raises(click.ClickException) as exc_info,
        ):
            _sync_resource_type(
                deps,
                deploy_fn,
                "skill",
                config,
                tmp_path,
                new_lockfile,
                dry_run=False,
                verbose=False,
            )

        msg = str(exc_info.value)
        assert "failed a" in msg
        assert "failed b" in msg
        assert "failed c" in msg
        assert "3" in msg
        deploy_fn.assert_not_called()
        mock_write.assert_called_once()

    def test_partial_failure_cleans_up_successes(self, tmp_path: Path) -> None:
        deps = [_make_dep("ok"), _make_dep("bad")]
        fake_result = _make_result(deps[0], tmp_path)
        config = _make_config()

        def fake_fetch(dep: DependencySource) -> FetchResult:
            if dep.name == "bad":
                raise FetchError("boom")
            return fake_result

        with (
            patch("agpack.cli.fetch_dependency", side_effect=fake_fetch),
            patch("agpack.cli.cleanup_fetch") as mock_cleanup,
            patch("agpack.cli.write_lockfile"),
            pytest.raises(click.ClickException),
        ):
            _sync_resource_type(
                deps,
                MagicMock(),
                "skill",
                config,
                tmp_path,
                Lockfile(),
                dry_run=False,
                verbose=False,
            )

        mock_cleanup.assert_called_once_with(fake_result)

    def test_dry_run_skips_lockfile_write(self, tmp_path: Path) -> None:
        deps = [_make_dep("bad")]
        config = _make_config()

        with (
            patch("agpack.cli.fetch_dependency", side_effect=FetchError("boom")),
            patch("agpack.cli.write_lockfile") as mock_write,
            patch("agpack.cli.cleanup_fetch"),
            pytest.raises(click.ClickException),
        ):
            _sync_resource_type(
                deps,
                MagicMock(),
                "skill",
                config,
                tmp_path,
                Lockfile(),
                dry_run=True,
                verbose=False,
            )

        mock_write.assert_not_called()

    def test_deploy_not_called_when_any_fetch_fails(self, tmp_path: Path) -> None:
        deps = [_make_dep("a"), _make_dep("b")]
        fake_result = _make_result(deps[0], tmp_path)
        config = _make_config()
        deploy_fn = MagicMock()

        def fake_fetch(dep: DependencySource) -> FetchResult:
            if dep.name == "b":
                raise FetchError("nope")
            return fake_result

        with (
            patch("agpack.cli.fetch_dependency", side_effect=fake_fetch),
            patch("agpack.cli.cleanup_fetch"),
            patch("agpack.cli.write_lockfile"),
            pytest.raises(click.ClickException),
        ):
            _sync_resource_type(
                deps,
                deploy_fn,
                "skill",
                config,
                tmp_path,
                Lockfile(),
                dry_run=False,
                verbose=False,
            )

        deploy_fn.assert_not_called()


class TestParallelFetchEdgeCases:
    def test_empty_deps_returns_zero(self, tmp_path: Path) -> None:
        with patch("agpack.cli.fetch_dependency") as mock_fetch:
            count = _sync_resource_type(
                [],
                MagicMock(),
                "skill",
                _make_config(),
                tmp_path,
                Lockfile(),
                dry_run=False,
                verbose=False,
            )
        assert count == 0
        mock_fetch.assert_not_called()

    def test_concurrency_capped_at_max_workers(self, tmp_path: Path) -> None:
        deps = [_make_dep(str(i)) for i in range(20)]
        config = _make_config()

        captured: list[int] = []
        real_init = ThreadPoolExecutor.__init__

        def capturing_init(self, *args, max_workers=None, **kwargs):
            if max_workers is not None:
                captured.append(max_workers)
            real_init(self, *args, max_workers=max_workers, **kwargs)

        def fake_fetch(dep: DependencySource) -> FetchResult:
            return _make_result(dep, tmp_path)

        with (
            patch("agpack.cli.fetch_dependency", side_effect=fake_fetch),
            patch("agpack.cli.cleanup_fetch"),
            patch.object(ThreadPoolExecutor, "__init__", capturing_init),
        ):
            _sync_resource_type(
                deps,
                MagicMock(return_value=[]),
                "skill",
                config,
                tmp_path,
                Lockfile(),
                dry_run=False,
                verbose=False,
            )

        assert captured, "ThreadPoolExecutor was not constructed"
        assert all(w <= _MAX_FETCH_WORKERS for w in captured)

    def test_deploy_error_writes_lockfile(self, tmp_path: Path) -> None:
        deps = [_make_dep("a")]
        fake_result = _make_result(deps[0], tmp_path)
        config = _make_config()

        with (
            patch("agpack.cli.fetch_dependency", return_value=fake_result),
            patch("agpack.cli.cleanup_fetch"),
            patch("agpack.cli.write_lockfile") as mock_write,
            pytest.raises(Exception, match="Error deploying"),
        ):
            _sync_resource_type(
                deps,
                MagicMock(side_effect=RuntimeError("disk full")),
                "skill",
                config,
                tmp_path,
                Lockfile(),
                dry_run=False,
                verbose=False,
            )

        mock_write.assert_called_once()
