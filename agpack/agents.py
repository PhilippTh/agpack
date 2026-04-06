"""Agent detection and deployment."""

from __future__ import annotations

from pathlib import Path

from agpack.deployer import deploy_single_file
from agpack.deployer import detect_file_items
from agpack.fetcher import FetchResult
from agpack.targets import AGENT_DIRS


def detect_agent_items(fetch_result: FetchResult) -> list[tuple[str, Path]]:
    """Return ``(name, path)`` pairs for agent items."""
    return detect_file_items(fetch_result, "agent")


def deploy_single_agent(
    filename: str,
    file_path: Path,
    targets: list[str],
    project_root: Path,
    dry_run: bool,
    verbose: bool,
) -> list[str]:
    """Deploy a single agent file to all applicable target directories."""
    return deploy_single_file(filename, file_path, targets, AGENT_DIRS, project_root, dry_run, verbose)
