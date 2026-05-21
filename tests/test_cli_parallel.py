"""Tests for parallel fetch behaviour in _sync_resource_type."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import click
import pytest

from agpack.cli import _MAX_FETCH_WORKERS
from agpack.cli import _resource_kinds
from agpack.cli import _sync_resource_type
from agpack.config import DependencySource
from agpack.display import create_sync_progress
from agpack.fetcher import FetchError
from agpack.fetcher import FetchResult
from agpack.lockfile import Lockfile
from agpack.registry import load_builtin
from agpack.target_schema import TargetDef


def _make_dep(name: str) -> DependencySource:
    return DependencySource(urls=[f"https://github.com/owner/{name}"])


def _make_result(dep: DependencySource, tmp_path: Path) -> FetchResult:
    d = tmp_path / dep.name
    d.mkdir(exist_ok=True)
    return FetchResult(source=dep, local_path=d, resolved_ref="abc1234", _tmpdir=d)


def _make_targets(names: list[str] | None = None) -> list[TargetDef]:
    return [load_builtin(n) for n in (names or ["claude"])]


def _fake_detect_items(
    result: FetchResult, _resource: object, _resource_type: str
) -> list[tuple[str, Path]]:
    """Stand-in for ``agpack.deployer.detect_items``."""
    return [(result.source.name, result.local_path)]


def _fake_deploy_item(
    name: str,
    _path: Path,
    _resource_type: str,
    _targets: list[TargetDef],
    _project_root: Path,
    _dry_run: bool = False,
    _verbose: bool = False,
) -> list[str]:
    """Stand-in for ``agpack.deployer.deploy_item``."""
    return [f"{name}.md"]


class TestParallelFetchAllSucceed:
    def test_all_fetched_and_deployed(self, tmp_path: Path) -> None:
        deps = [_make_dep("a"), _make_dep("b"), _make_dep("c")]
        fake_results = {dep.name: _make_result(dep, tmp_path) for dep in deps}
        target_defs = _make_targets()
        new_lockfile = Lockfile()

        def fake_fetch(dep: DependencySource) -> FetchResult:
            return fake_results[dep.name]

        deploy_item_mock = MagicMock(side_effect=_fake_deploy_item)

        with (
            patch("agpack.cli.fetch_dependency", side_effect=fake_fetch),
            patch("agpack.cli.cleanup_fetch"),
            patch("agpack.cli.detect_items", side_effect=_fake_detect_items),
            patch("agpack.cli.deploy_item", deploy_item_mock),
            create_sync_progress() as progress,
        ):
            sync = _sync_resource_type(
                deps,
                "skills",
                target_defs,
                tmp_path,
                new_lockfile,
                progress,
                dry_run=False,
                verbose=False,
            )

        assert sync.count == 3
        assert deploy_item_mock.call_count == 3
        assert len(new_lockfile.installed) == 3

    def test_lockfile_entries_added(self, tmp_path: Path) -> None:
        deps = [_make_dep("x")]
        fake_result = _make_result(deps[0], tmp_path)
        target_defs = _make_targets()
        new_lockfile = Lockfile()

        with (
            patch("agpack.cli.fetch_dependency", return_value=fake_result),
            patch("agpack.cli.cleanup_fetch"),
            patch("agpack.cli.detect_items", side_effect=_fake_detect_items),
            patch("agpack.cli.deploy_item", side_effect=_fake_deploy_item),
            create_sync_progress() as progress,
        ):
            _sync_resource_type(
                deps,
                "skills",
                target_defs,
                tmp_path,
                new_lockfile,
                progress,
                dry_run=False,
                verbose=False,
            )

        assert len(new_lockfile.installed) == 1
        assert new_lockfile.installed[0].url == deps[0].url


class TestParallelFetchCollectAllErrors:
    def test_all_errors_reported(self, tmp_path: Path) -> None:
        deps = [_make_dep("a"), _make_dep("b"), _make_dep("c")]
        target_defs = _make_targets()
        new_lockfile = Lockfile()

        def fake_fetch(dep: DependencySource) -> FetchResult:
            raise FetchError(f"failed {dep.name}")

        detect_items_mock = MagicMock()
        deploy_item_mock = MagicMock()

        with (
            patch("agpack.cli.fetch_dependency", side_effect=fake_fetch),
            patch("agpack.cli.write_lockfile") as mock_write,
            patch("agpack.cli.detect_items", detect_items_mock),
            patch("agpack.cli.deploy_item", deploy_item_mock),
            create_sync_progress() as progress,
            pytest.raises(click.ClickException) as exc_info,
        ):
            _sync_resource_type(
                deps,
                "skills",
                target_defs,
                tmp_path,
                new_lockfile,
                progress,
                dry_run=False,
                verbose=False,
            )

        msg = str(exc_info.value)
        assert "failed a" in msg
        assert "failed b" in msg
        assert "failed c" in msg
        assert "3" in msg
        detect_items_mock.assert_not_called()
        deploy_item_mock.assert_not_called()
        mock_write.assert_called_once()

    def test_partial_failure_cleans_up_successes(self, tmp_path: Path) -> None:
        deps = [_make_dep("ok"), _make_dep("bad")]
        fake_result = _make_result(deps[0], tmp_path)
        target_defs = _make_targets()

        def fake_fetch(dep: DependencySource) -> FetchResult:
            if dep.name == "bad":
                raise FetchError("boom")
            return fake_result

        with (
            patch("agpack.cli.fetch_dependency", side_effect=fake_fetch),
            patch("agpack.cli.cleanup_fetch") as mock_cleanup,
            patch("agpack.cli.write_lockfile"),
            patch("agpack.cli.detect_items", MagicMock()),
            patch("agpack.cli.deploy_item", MagicMock()),
            create_sync_progress() as progress,
            pytest.raises(click.ClickException),
        ):
            _sync_resource_type(
                deps,
                "skills",
                target_defs,
                tmp_path,
                Lockfile(),
                progress,
                dry_run=False,
                verbose=False,
            )

        mock_cleanup.assert_called_once_with(fake_result)

    def test_dry_run_skips_lockfile_write(self, tmp_path: Path) -> None:
        deps = [_make_dep("bad")]
        target_defs = _make_targets()

        with (
            patch("agpack.cli.fetch_dependency", side_effect=FetchError("boom")),
            patch("agpack.cli.write_lockfile") as mock_write,
            patch("agpack.cli.cleanup_fetch"),
            create_sync_progress() as progress,
            pytest.raises(click.ClickException),
        ):
            _sync_resource_type(
                deps,
                "skills",
                target_defs,
                tmp_path,
                Lockfile(),
                progress,
                dry_run=True,
                verbose=False,
            )

        mock_write.assert_not_called()

    def test_deploy_not_called_when_any_fetch_fails(self, tmp_path: Path) -> None:
        deps = [_make_dep("a"), _make_dep("b")]
        fake_result = _make_result(deps[0], tmp_path)
        target_defs = _make_targets()
        detect_items_mock = MagicMock()
        deploy_item_mock = MagicMock()

        def fake_fetch(dep: DependencySource) -> FetchResult:
            if dep.name == "b":
                raise FetchError("nope")
            return fake_result

        with (
            patch("agpack.cli.fetch_dependency", side_effect=fake_fetch),
            patch("agpack.cli.cleanup_fetch"),
            patch("agpack.cli.write_lockfile"),
            patch("agpack.cli.detect_items", detect_items_mock),
            patch("agpack.cli.deploy_item", deploy_item_mock),
            create_sync_progress() as progress,
            pytest.raises(click.ClickException),
        ):
            _sync_resource_type(
                deps,
                "skills",
                target_defs,
                tmp_path,
                Lockfile(),
                progress,
                dry_run=False,
                verbose=False,
            )

        detect_items_mock.assert_not_called()
        deploy_item_mock.assert_not_called()


class TestParallelFetchEdgeCases:
    def test_empty_deps_returns_zero(self, tmp_path: Path) -> None:
        with (
            patch("agpack.cli.fetch_dependency") as mock_fetch,
            create_sync_progress() as progress,
        ):
            sync = _sync_resource_type(
                [],
                "skills",
                _make_targets(),
                tmp_path,
                Lockfile(),
                progress,
                dry_run=False,
                verbose=False,
            )
        assert sync.count == 0
        mock_fetch.assert_not_called()

    def test_concurrency_capped_at_max_workers(self, tmp_path: Path) -> None:
        deps = [_make_dep(str(i)) for i in range(20)]
        target_defs = _make_targets()

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
            patch("agpack.cli.detect_items", side_effect=_fake_detect_items),
            patch("agpack.cli.deploy_item", side_effect=_fake_deploy_item),
            patch.object(ThreadPoolExecutor, "__init__", capturing_init),
            create_sync_progress() as progress,
        ):
            _sync_resource_type(
                deps,
                "skills",
                target_defs,
                tmp_path,
                Lockfile(),
                progress,
                dry_run=False,
                verbose=False,
            )

        assert captured, "ThreadPoolExecutor was not constructed"
        assert all(w <= _MAX_FETCH_WORKERS for w in captured)

    def test_deploy_error_writes_lockfile(self, tmp_path: Path) -> None:
        deps = [_make_dep("a")]
        fake_result = _make_result(deps[0], tmp_path)
        target_defs = _make_targets()

        with (
            patch("agpack.cli.fetch_dependency", return_value=fake_result),
            patch("agpack.cli.cleanup_fetch"),
            patch("agpack.cli.write_lockfile") as mock_write,
            patch("agpack.cli.detect_items", side_effect=_fake_detect_items),
            patch(
                "agpack.cli.deploy_item",
                MagicMock(side_effect=RuntimeError("disk full")),
            ),
            create_sync_progress() as progress,
            pytest.raises(Exception, match="Error deploying"),
        ):
            _sync_resource_type(
                deps,
                "skills",
                target_defs,
                tmp_path,
                Lockfile(),
                progress,
                dry_run=False,
                verbose=False,
            )

        mock_write.assert_called_once()

class TestResourceKinds:
    def test_returns_union_of_target_resources(self) -> None:
        kinds = _resource_kinds(_make_targets(["claude", "codex"]))
        # claude has skills/commands/agents/mcp; codex has skills/agents/mcp.
        assert kinds["skills"] == "copy-directory"
        assert kinds["commands"] == "copy-file"
        assert kinds["agents"] == "copy-file"
        assert kinds["mcp"] == "edit-file"

    def test_raises_on_conflicting_kinds(self) -> None:
        from agpack.target_schema import parse_target_def

        target_a = parse_target_def(
            {"rules": {"kind": "copy-directory", "path": ".a/rules"}}
        )
        target_b = parse_target_def(
            {"rules": {"kind": "copy-file", "path": ".b/rules"}}
        )
        with pytest.raises(click.ClickException, match="conflicting kinds"):
            _resource_kinds([target_a, target_b])

    def test_arbitrary_resource_name_supported(self) -> None:
        from agpack.target_schema import parse_target_def

        target = parse_target_def(
            {"rules": {"kind": "copy-file", "path": ".my-tool/rules"}}
        )
        kinds = _resource_kinds([target])
        assert kinds == {"rules": "copy-file"}
