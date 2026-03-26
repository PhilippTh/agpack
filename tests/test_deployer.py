"""Tests for agpack.deployer — file copy logic and directory mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from agpack.config import DependencySource
from agpack.deployer import DeployError
from agpack.deployer import DeployResult
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

        result = deploy_skill(fr, ALL_TARGETS, project)

        assert isinstance(result, DeployResult)
        assert result.expanded_items == []

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
        assert len(result.files) == len(SKILL_DIRS) * 2  # 2 files per target

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

        result = deploy_skill(fr, ["claude"], project)

        # Only SKILL.md should be deployed (everything .git* is skipped)
        assert len(result.files) == 1
        assert result.files[0].endswith("SKILL.md")

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

        result = deploy_skill(fr, ALL_TARGETS, project, dry_run=True)

        # Should report what would be deployed...
        assert len(result.files) > 0
        # ...but nothing actually written
        for rel in result.files:
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

        result = deploy_skill(fr, ["opencode"], project)

        assert any("custom-name" in p for p in result.files)
        assert (project / ".opencode" / "skills" / "custom-name" / "README.md").exists()


# ---------------------------------------------------------------------------
# deploy_command
# ---------------------------------------------------------------------------


class TestDeployCommand:
    def test_copies_to_supported_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="lint.md")

        result = deploy_command(fr, ALL_TARGETS, project)

        assert isinstance(result, DeployResult)
        assert result.expanded_items == []

        from agpack.targets import COMMAND_DIRS

        # Only targets with entries in COMMAND_DIRS should have files
        assert len(result.files) == len(COMMAND_DIRS)
        for target, base in COMMAND_DIRS.items():
            dst = project / base / "lint.md"
            assert dst.exists(), f"missing for {target}"
            assert dst.read_text() == "hello"

    def test_skips_unsupported_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="lint.md")

        # codex and cursor don't support commands
        result = deploy_command(fr, ["codex", "cursor"], project)

        assert result.files == []

    def test_dry_run_no_files_created(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="lint.md")

        result = deploy_command(fr, ["claude"], project, dry_run=True)

        assert len(result.files) == 1
        assert not (project / result.files[0]).exists()


# ---------------------------------------------------------------------------
# deploy_agent
# ---------------------------------------------------------------------------


class TestDeployAgent:
    def test_copies_to_supported_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="reviewer.md")

        result = deploy_agent(fr, ALL_TARGETS, project)

        assert isinstance(result, DeployResult)
        assert result.expanded_items == []

        from agpack.targets import AGENT_DIRS

        assert len(result.files) == len(AGENT_DIRS)
        for target, base in AGENT_DIRS.items():
            dst = project / base / "reviewer.md"
            assert dst.exists(), f"missing for {target}"
            assert dst.read_text() == "hello"

    def test_skips_unsupported_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="reviewer.md")

        # codex doesn't support agents
        result = deploy_agent(fr, ["codex"], project)

        assert result.files == []

    def test_dry_run_no_files_created(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="reviewer.md")

        result = deploy_agent(fr, ["claude", "cursor"], project, dry_run=True)

        assert len(result.files) == 2
        for rel in result.files:
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
        result = deploy_skill(fr, ["claude"], project)

        # Sanity: files exist
        for rel in result.files:
            assert (project / rel).exists()

        cleanup_deployed_files(result.files, project)

        # Files gone
        for rel in result.files:
            assert not (project / rel).exists()

        # Empty directories should also be removed
        skill_dir = project / ".claude" / "skills" / "my-skill"
        assert not skill_dir.exists()

    def test_preserves_non_empty_dirs(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        fr = _make_dir_fetch(tmp_path)
        result = deploy_skill(fr, ["claude"], project)

        # Add an extra file that wasn't deployed by agpack
        extra = project / ".claude" / "skills" / "my-skill" / "KEEP.md"
        extra.write_text("keep me")

        cleanup_deployed_files(result.files, project)

        # Deployed files gone, but directory and extra file stay
        for rel in result.files:
            assert not (project / rel).exists()
        assert extra.exists()

    def test_dry_run_no_files_removed(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        fr = _make_dir_fetch(tmp_path)
        result = deploy_skill(fr, ["claude"], project)

        cleanup_deployed_files(result.files, project, dry_run=True)

        # Everything should still be there
        for rel in result.files:
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


# ---------------------------------------------------------------------------
# Folder-of-skills detection
# ---------------------------------------------------------------------------


def _make_folder_of_skills_fetch(
    tmp_path: Path,
    name: str = "my-skills",
    skills: dict[str, dict[str, str]] | None = None,
) -> FetchResult:
    """Create a FetchResult pointing at a directory that contains skill subfolders."""
    if skills is None:
        skills = {
            "skill-a": {"SKILL.md": "# Skill A"},
            "skill-b": {"SKILL.md": "# Skill B", "lib/util.py": "pass"},
        }
    src = tmp_path / "src" / name
    for skill_name, files in skills.items():
        for rel, content in files.items():
            p = src / skill_name / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    return FetchResult(
        source=DependencySource(url=f"https://github.com/org/{name}", path=name),
        local_path=src,
        resolved_ref="abc1234",
    )


def _make_dir_command_fetch(
    tmp_path: Path,
    name: str = "my-commands",
    files: dict[str, str] | None = None,
) -> FetchResult:
    """Create a FetchResult pointing at a directory containing command files."""
    if files is None:
        files = {"lint.md": "# Lint", "format.md": "# Format"}
    src = tmp_path / "src" / name
    for rel, content in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return FetchResult(
        source=DependencySource(url=f"https://github.com/org/{name}", path=name),
        local_path=src,
        resolved_ref="abc1234",
    )


class TestDeploySkillFolderDetection:
    def test_deploys_each_subfolder_as_separate_skill(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_folder_of_skills_fetch(tmp_path)

        result = deploy_skill(fr, ["claude"], project)

        skill_a = project / ".claude" / "skills" / "skill-a" / "SKILL.md"
        skill_b = project / ".claude" / "skills" / "skill-b" / "SKILL.md"
        skill_b_lib = project / ".claude" / "skills" / "skill-b" / "lib" / "util.py"
        assert skill_a.exists()
        assert skill_a.read_text() == "# Skill A"
        assert skill_b.exists()
        assert skill_b_lib.exists()
        # 1 file for skill-a + 2 files for skill-b = 3 per target
        assert len(result.files) == 3
        assert result.expanded_items == ["skill-a", "skill-b"]

    def test_folder_of_skills_to_multiple_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_folder_of_skills_fetch(tmp_path)

        result = deploy_skill(fr, ALL_TARGETS, project)

        from agpack.targets import SKILL_DIRS

        # 3 files × 5 targets = 15
        assert len(result.files) == 3 * len(SKILL_DIRS)
        assert result.expanded_items == ["skill-a", "skill-b"]
        for target, base in SKILL_DIRS.items():
            assert (project / base / "skill-a" / "SKILL.md").exists()
            assert (project / base / "skill-b" / "SKILL.md").exists()

    def test_folder_of_skills_dry_run(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_folder_of_skills_fetch(tmp_path)

        result = deploy_skill(fr, ["claude"], project, dry_run=True)

        assert len(result.files) == 3
        assert result.expanded_items == ["skill-a", "skill-b"]
        for rel in result.files:
            assert not (project / rel).exists()

    def test_errors_on_empty_directory(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        src = tmp_path / "src" / "empty"
        src.mkdir(parents=True)
        fr = FetchResult(
            source=DependencySource(url="https://github.com/org/empty", path="empty"),
            local_path=src,
            resolved_ref="abc1234",
        )

        with pytest.raises(DeployError, match="does not contain any skill folders"):
            deploy_skill(fr, ["claude"], project)

    def test_errors_on_dir_with_only_empty_subdirs(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        src = tmp_path / "src" / "parent"
        (src / "empty-a").mkdir(parents=True)
        (src / "empty-b").mkdir(parents=True)
        fr = FetchResult(
            source=DependencySource(url="https://github.com/org/parent", path="parent"),
            local_path=src,
            resolved_ref="abc1234",
        )

        with pytest.raises(DeployError, match="does not contain any skill folders"):
            deploy_skill(fr, ["claude"], project)

    def test_single_skill_folder_still_works(self, tmp_path: Path) -> None:
        """A directory with top-level files is still deployed as a single skill."""
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_dir_fetch(tmp_path, name="my-skill")

        result = deploy_skill(fr, ["claude"], project)

        assert (project / ".claude" / "skills" / "my-skill" / "SKILL.md").exists()
        assert len(result.files) == 2  # SKILL.md + lib/helper.py
        assert result.expanded_items == []


# ---------------------------------------------------------------------------
# Folder-of-commands detection
# ---------------------------------------------------------------------------


class TestDeployCommandFolderDetection:
    def test_deploys_each_file_from_directory(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_dir_command_fetch(tmp_path)

        result = deploy_command(fr, ["claude"], project)

        assert (project / ".claude" / "commands" / "lint.md").exists()
        assert (project / ".claude" / "commands" / "format.md").exists()
        assert (project / ".claude" / "commands" / "lint.md").read_text() == "# Lint"
        assert result.expanded_items == ["format.md", "lint.md"]

    def test_deploys_files_from_subfolders(self, tmp_path: Path) -> None:
        """When top level has no files, files from subfolders are deployed."""
        project = tmp_path / "project"
        project.mkdir()
        src = tmp_path / "src" / "cmds"
        (src / "group-a").mkdir(parents=True)
        (src / "group-a" / "lint.md").write_text("# Lint")
        (src / "group-b").mkdir(parents=True)
        (src / "group-b" / "format.md").write_text("# Format")
        fr = FetchResult(
            source=DependencySource(url="https://github.com/org/cmds", path="cmds"),
            local_path=src,
            resolved_ref="abc1234",
        )

        result = deploy_command(fr, ["claude"], project)

        assert (project / ".claude" / "commands" / "lint.md").exists()
        assert (project / ".claude" / "commands" / "format.md").exists()
        assert sorted(result.expanded_items) == ["format.md", "lint.md"]

    def test_errors_on_empty_directory(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        src = tmp_path / "src" / "empty"
        src.mkdir(parents=True)
        fr = FetchResult(
            source=DependencySource(url="https://github.com/org/empty", path="empty"),
            local_path=src,
            resolved_ref="abc1234",
        )

        with pytest.raises(DeployError, match="does not contain any command files"):
            deploy_command(fr, ["claude"], project)

    def test_dry_run_with_directory(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_dir_command_fetch(tmp_path)

        result = deploy_command(fr, ["claude"], project, dry_run=True)

        assert len(result.files) == 2
        for rel in result.files:
            assert not (project / rel).exists()


# ---------------------------------------------------------------------------
# Folder-of-agents detection
# ---------------------------------------------------------------------------


class TestDeployAgentFolderDetection:
    def test_deploys_each_file_from_directory(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        src = tmp_path / "src" / "agents"
        src.mkdir(parents=True)
        (src / "reviewer.md").write_text("# Reviewer")
        (src / "planner.md").write_text("# Planner")
        fr = FetchResult(
            source=DependencySource(url="https://github.com/org/agents", path="agents"),
            local_path=src,
            resolved_ref="abc1234",
        )

        result = deploy_agent(fr, ["claude"], project)

        assert (project / ".claude" / "agents" / "reviewer.md").exists()
        assert (project / ".claude" / "agents" / "planner.md").exists()
        assert result.expanded_items == ["planner.md", "reviewer.md"]

    def test_deploys_files_from_subfolders(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        src = tmp_path / "src" / "agents"
        (src / "group").mkdir(parents=True)
        (src / "group" / "reviewer.md").write_text("# Reviewer")
        fr = FetchResult(
            source=DependencySource(url="https://github.com/org/agents", path="agents"),
            local_path=src,
            resolved_ref="abc1234",
        )

        result = deploy_agent(fr, ["claude"], project)

        assert (project / ".claude" / "agents" / "reviewer.md").exists()
        # Single file — no expansion reported
        assert result.expanded_items == []

    def test_errors_on_empty_directory(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        src = tmp_path / "src" / "empty"
        src.mkdir(parents=True)
        fr = FetchResult(
            source=DependencySource(url="https://github.com/org/empty", path="empty"),
            local_path=src,
            resolved_ref="abc1234",
        )

        with pytest.raises(DeployError, match="does not contain any agent files"):
            deploy_agent(fr, ["claude"], project)
