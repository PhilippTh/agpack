"""Command detection and deployment."""

from __future__ import annotations

from pathlib import Path

from agpack.deployer import deploy_single_file
from agpack.deployer import detect_file_items
from agpack.fetcher import FetchResult
from agpack.targets import COMMAND_DIRS


def detect_command_items(fetch_result: FetchResult) -> list[tuple[str, Path]]:
    """Return ``(name, path)`` pairs for command items."""
    return detect_file_items(fetch_result, "command")


def deploy_single_command(
    filename: str,
    file_path: Path,
    targets: list[str],
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    """Deploy a single command file to all applicable target directories."""
    return deploy_single_file(
        filename, file_path, targets, COMMAND_DIRS, project_root, dry_run, verbose
    )
