"""Tests for agpack.deployer — file copy logic and directory mapping."""

from __future__ import annotations

from pathlib import Path

from agpack.config import DependencySource
from agpack.deployer import _atomic_copy_file
from agpack.deployer import cleanup_deployed_files
from agpack.deployer import deploy_agent
from agpack.deployer import deploy_command
from agpack.deployer import deploy_skill
from agpack.fetcher import FetchResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_TARGETS = ["claude", "opencode", "codex", "cursor", "copilot"]


def _make_source(name: str = "my-skill") -> DependencySource:
    return DependencySource(url=f"https://github.com/org/{name}", path=name)


def _make_file_fetch(
    tmp_path: Path,
    filename: str = "run.md",
    content: str = "hello",
    source_name: str = "run.md",
) -> FetchResult:
    """Create a FetchResult pointing at a single temp file."""
    src = tmp_path / "src" / filename
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(content)
    return FetchResult(
        source=DependencySource(
            url=f"https://github.com/org/{source_name}", path=source_name
        ),
        local_path=src,
        resolved_ref="abc1234",
    )


def _make_dir_fetch(
    tmp_path: Path,
    name: str = "my-skill",
    files: dict[str, str] | None = None,
) -> FetchResult:
    """Create a FetchResult pointing at a temp directory tree."""
    if files is None:
        files = {
            "SKILL.md": "# Skill\nHello",
            "lib/helper.py": "def helper(): ...",
        }
    src = tmp_path / "src" / name
    for rel, content in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return FetchResult(
        source=_make_source(name),
        local_path=src,
        resolved_ref="abc1234",
    )


# ---------------------------------------------------------------------------
# deploy_skill
# ---------------------------------------------------------------------------


class TestDeploySkill:
    def test_copies_directory_to_all_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_dir_fetch(tmp_path, name="my-skill")

        deployed = deploy_skill(fr, ALL_TARGETS, project)

        # Should produce files under every target's skill dir
        from agpack.targets import SKILL_DIRS

        for target, base in SKILL_DIRS.items():
            skill_md = project / base / "my-skill" / "SKILL.md"
            helper = project / base / "my-skill" / "lib" / "helper.py"
            assert skill_md.exists(), f"missing SKILL.md for {target}"
            assert helper.exists(), f"missing helper.py for {target}"
            assert skill_md.read_text() == "# Skill\nHello"
            assert helper.read_text() == "def helper(): ..."

        # deployed list should contain relative paths
        assert len(deployed) == len(SKILL_DIRS) * 2  # 2 files per target

    def test_skips_git_files(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_dir_fetch(
            tmp_path,
            files={
                "SKILL.md": "content",
                ".git/config": "should be skipped",
                ".gitignore": "should be skipped too",
                "sub/.gitkeep": "also skipped",
            },
        )

        deployed = deploy_skill(fr, ["claude"], project)

        # Only SKILL.md should be deployed (everything .git* is skipped)
        assert len(deployed) == 1
        assert deployed[0].endswith("SKILL.md")

        git_config = project / ".claude" / "skills" / "my-skill" / ".git" / "config"
        assert not git_config.exists()

    def test_creates_target_directories(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_dir_fetch(tmp_path)

        deploy_skill(fr, ["claude"], project)

        assert (project / ".claude" / "skills" / "my-skill").is_dir()

    def test_dry_run_no_files_created(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_dir_fetch(tmp_path)

        deployed = deploy_skill(fr, ALL_TARGETS, project, dry_run=True)

        # Should report what would be deployed...
        assert len(deployed) > 0
        # ...but nothing actually written
        for rel in deployed:
            assert not (project / rel).exists()

    def test_skill_name_derived_from_source_path(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        source = DependencySource(
            url="https://github.com/org/repo", path="skills/custom-name"
        )
        src_dir = tmp_path / "src" / "custom-name"
        src_dir.mkdir(parents=True)
        (src_dir / "README.md").write_text("hi")
        fr = FetchResult(source=source, local_path=src_dir, resolved_ref="aaa")

        deployed = deploy_skill(fr, ["opencode"], project)

        assert any("custom-name" in p for p in deployed)
        assert (project / ".opencode" / "skills" / "custom-name" / "README.md").exists()


# ---------------------------------------------------------------------------
# deploy_command
# ---------------------------------------------------------------------------


class TestDeployCommand:
    def test_copies_to_supported_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="lint.md")

        deployed = deploy_command(fr, ALL_TARGETS, project)

        from agpack.targets import COMMAND_DIRS

        # Only targets with entries in COMMAND_DIRS should have files
        assert len(deployed) == len(COMMAND_DIRS)
        for target, base in COMMAND_DIRS.items():
            dst = project / base / "lint.md"
            assert dst.exists(), f"missing for {target}"
            assert dst.read_text() == "hello"

    def test_skips_unsupported_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="lint.md")

        # codex and cursor don't support commands
        deployed = deploy_command(fr, ["codex", "cursor"], project)

        assert deployed == []

    def test_dry_run_no_files_created(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="lint.md")

        deployed = deploy_command(fr, ["claude"], project, dry_run=True)

        assert len(deployed) == 1
        assert not (project / deployed[0]).exists()


# ---------------------------------------------------------------------------
# deploy_agent
# ---------------------------------------------------------------------------


class TestDeployAgent:
    def test_copies_to_supported_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="reviewer.md")

        deployed = deploy_agent(fr, ALL_TARGETS, project)

        from agpack.targets import AGENT_DIRS

        assert len(deployed) == len(AGENT_DIRS)
        for target, base in AGENT_DIRS.items():
            dst = project / base / "reviewer.md"
            assert dst.exists(), f"missing for {target}"
            assert dst.read_text() == "hello"

    def test_skips_unsupported_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="reviewer.md")

        # codex doesn't support agents
        deployed = deploy_agent(fr, ["codex"], project)

        assert deployed == []

    def test_dry_run_no_files_created(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="reviewer.md")

        deployed = deploy_agent(fr, ["claude", "cursor"], project, dry_run=True)

        assert len(deployed) == 2
        for rel in deployed:
            assert not (project / rel).exists()


# ---------------------------------------------------------------------------
# cleanup_deployed_files
# ---------------------------------------------------------------------------


class TestCleanupDeployedFiles:
    def test_removes_files_and_empty_dirs(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        # Deploy first, then clean up
        fr = _make_dir_fetch(tmp_path)
        deployed = deploy_skill(fr, ["claude"], project)

        # Sanity: files exist
        for rel in deployed:
            assert (project / rel).exists()

        cleanup_deployed_files(deployed, project)

        # Files gone
        for rel in deployed:
            assert not (project / rel).exists()

        # Empty directories should also be removed
        skill_dir = project / ".claude" / "skills" / "my-skill"
        assert not skill_dir.exists()

    def test_preserves_non_empty_dirs(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        fr = _make_dir_fetch(tmp_path)
        deployed = deploy_skill(fr, ["claude"], project)

        # Add an extra file that wasn't deployed by agpack
        extra = project / ".claude" / "skills" / "my-skill" / "KEEP.md"
        extra.write_text("keep me")

        cleanup_deployed_files(deployed, project)

        # Deployed files gone, but directory and extra file stay
        for rel in deployed:
            assert not (project / rel).exists()
        assert extra.exists()

    def test_dry_run_no_files_removed(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        fr = _make_dir_fetch(tmp_path)
        deployed = deploy_skill(fr, ["claude"], project)

        cleanup_deployed_files(deployed, project, dry_run=True)

        # Everything should still be there
        for rel in deployed:
            assert (project / rel).exists()

    def test_handles_already_missing_files(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        # Files that don't exist — should not raise
        cleanup_deployed_files(
            [".claude/skills/gone/SKILL.md"],
            project,
        )


# ---------------------------------------------------------------------------
# _atomic_copy_file
# ---------------------------------------------------------------------------


class TestAtomicCopyFile:
    def test_file_exists_after_copy(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("payload")
        dst = tmp_path / "out" / "dst.txt"

        _atomic_copy_file(src, dst)

        assert dst.exists()
        assert dst.read_text() == "payload"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst = tmp_path / "a" / "b" / "c" / "file.txt"

        _atomic_copy_file(src, dst)

        assert dst.exists()
        assert dst.read_text() == "data"

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("new")
        dst = tmp_path / "dst.txt"
        dst.write_text("old")

        _atomic_copy_file(src, dst)

        assert dst.read_text() == "new"
