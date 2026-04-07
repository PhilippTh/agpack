"""Tests for resolvers and writer — detection logic, file copy, and directory mapping."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agpack.cleanup import cleanup_deployed_files
from agpack.config import DependencySource
from agpack.fetcher import FetchResult
from agpack.fileutil import atomic_copy_file
from agpack.resolvers import ResolveError
from agpack.resolvers import detect_single_file_items
from agpack.resolvers import detect_skill_items
from agpack.resolvers import resolve_agents
from agpack.resolvers import resolve_commands
from agpack.resolvers import resolve_skills
from agpack.writer import execute_write_ops

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_TARGETS = [
    "claude",
    "opencode",
    "codex",
    "cursor",
    "copilot",
    "gemini",
    "windsurf",
    "antigravity",
]


def _make_source(name: str = "my-skill") -> DependencySource:
    return DependencySource(urls=[f"https://github.com/org/{name}"], path=name)


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
        source=DependencySource(urls=[f"https://github.com/org/{source_name}"], path=source_name),
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
        source=DependencySource(urls=[f"https://github.com/org/{name}"], path=name),
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
        source=DependencySource(urls=[f"https://github.com/org/{name}"], path=name),
        local_path=src,
        resolved_ref="abc1234",
    )


# ---------------------------------------------------------------------------
# resolve_skills — directory
# ---------------------------------------------------------------------------


class TestResolveSkillDirectory:
    def test_copies_directory_to_all_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_dir_fetch(tmp_path, name="my-skill")

        ops = resolve_skills(fr, ALL_TARGETS)
        all_deployed = execute_write_ops(ops, project)

        from agpack.targets import SKILL_DIRS

        for target, base in SKILL_DIRS.items():
            skill_md = project / base / "my-skill" / "SKILL.md"
            helper = project / base / "my-skill" / "lib" / "helper.py"
            assert skill_md.exists(), f"missing SKILL.md for {target}"
            assert helper.exists(), f"missing helper.py for {target}"
            assert skill_md.read_text() == "# Skill\nHello"
            assert helper.read_text() == "def helper(): ..."

        assert len(all_deployed) == len(SKILL_DIRS) * 2  # 2 files per target

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

        ops = resolve_skills(fr, ["claude"])
        deployed = execute_write_ops(ops, project)

        assert len(deployed) == 1
        assert deployed[0].endswith("SKILL.md")

        git_config = project / ".claude" / "skills" / "my-skill" / ".git" / "config"
        assert not git_config.exists()

    def test_creates_target_directories(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_dir_fetch(tmp_path)

        ops = resolve_skills(fr, ["claude"])
        execute_write_ops(ops, project)

        assert (project / ".claude" / "skills" / "my-skill").is_dir()

    def test_dry_run_no_files_created(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_dir_fetch(tmp_path)

        ops = resolve_skills(fr, ALL_TARGETS)
        all_deployed = execute_write_ops(ops, project, dry_run=True)

        assert len(all_deployed) > 0
        for rel in all_deployed:
            assert not (project / rel).exists()

    def test_skill_name_derived_from_source_path(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        source = DependencySource(urls=["https://github.com/org/repo"], path="skills/custom-name")
        src_dir = tmp_path / "src" / "custom-name"
        src_dir.mkdir(parents=True)
        (src_dir / "README.md").write_text("hi")
        fr = FetchResult(source=source, local_path=src_dir, resolved_ref="aaa")

        ops = resolve_skills(fr, ["opencode"])
        all_deployed = execute_write_ops(ops, project)

        assert any("custom-name" in p for p in all_deployed)
        assert (project / ".opencode" / "skills" / "custom-name" / "README.md").exists()

    def test_deploys_single_file_as_skill(self, tmp_path: Path) -> None:
        """A single file (not a directory) is deployed as a skill."""
        project = tmp_path / "project"
        project.mkdir()

        skill_file = tmp_path / "src" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# My Skill")

        source = DependencySource(urls=["https://github.com/org/my-skill"], path="my-skill")
        fr = FetchResult(source=source, local_path=skill_file, resolved_ref="aaa")

        ops = resolve_skills(fr, ["claude"])
        result = execute_write_ops(ops, project)

        assert len(result) == 1
        deployed = project / result[0]
        assert deployed.exists()
        assert deployed.read_text() == "# My Skill"
        assert "my-skill" in str(deployed)

    def test_deploys_single_file_skill_to_multiple_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        skill_file = tmp_path / "src" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# My Skill")

        source = DependencySource(urls=["https://github.com/org/my-skill"], path="my-skill")
        fr = FetchResult(source=source, local_path=skill_file, resolved_ref="aaa")

        ops = resolve_skills(fr, ALL_TARGETS)
        result = execute_write_ops(ops, project)

        from agpack.targets import SKILL_DIRS

        assert len(result) == len(SKILL_DIRS)
        for rel in result:
            assert (project / rel).exists()

    def test_dry_run_single_file_skill(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        skill_file = tmp_path / "src" / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("# My Skill")

        source = DependencySource(urls=["https://github.com/org/my-skill"], path="my-skill")
        fr = FetchResult(source=source, local_path=skill_file, resolved_ref="aaa")

        ops = resolve_skills(fr, ["claude"])
        result = execute_write_ops(ops, project, dry_run=True)

        assert len(result) == 1
        assert not (project / result[0]).exists()


# ---------------------------------------------------------------------------
# resolve_commands
# ---------------------------------------------------------------------------


class TestResolveCommand:
    def test_copies_to_supported_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="lint.md")

        ops = resolve_commands(fr, ALL_TARGETS)
        all_deployed = execute_write_ops(ops, project)

        from agpack.targets import COMMAND_DIRS

        assert len(all_deployed) == len(COMMAND_DIRS)
        for target, base in COMMAND_DIRS.items():
            dst = project / base / "lint.md"
            assert dst.exists(), f"missing for {target}"
            assert dst.read_text() == "hello"

    def test_skips_unsupported_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="lint.md")

        # codex and cursor don't support commands
        ops = resolve_commands(fr, ["codex", "cursor"])
        all_deployed = execute_write_ops(ops, project)

        assert all_deployed == []

    def test_dry_run_no_files_created(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="lint.md")

        ops = resolve_commands(fr, ["claude"])
        all_deployed = execute_write_ops(ops, project, dry_run=True)

        assert len(all_deployed) == 1
        assert not (project / all_deployed[0]).exists()


# ---------------------------------------------------------------------------
# resolve_agents
# ---------------------------------------------------------------------------


class TestResolveAgent:
    def test_copies_to_supported_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="reviewer.md")

        ops = resolve_agents(fr, ALL_TARGETS)
        all_deployed = execute_write_ops(ops, project)

        from agpack.targets import AGENT_DIRS

        assert len(all_deployed) == len(AGENT_DIRS)
        for target, base in AGENT_DIRS.items():
            dst = project / base / "reviewer.md"
            assert dst.exists(), f"missing for {target}"
            assert dst.read_text() == "hello"

    def test_skips_unsupported_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="reviewer.md")

        ops = resolve_agents(fr, ["codex"])
        all_deployed = execute_write_ops(ops, project)

        assert all_deployed == []

    def test_dry_run_no_files_created(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_file_fetch(tmp_path, source_name="reviewer.md")

        ops = resolve_agents(fr, ["claude", "cursor"])
        all_deployed = execute_write_ops(ops, project, dry_run=True)

        assert len(all_deployed) == 2
        for rel in all_deployed:
            assert not (project / rel).exists()


# ---------------------------------------------------------------------------
# cleanup_deployed_files
# ---------------------------------------------------------------------------


class TestCleanupDeployedFiles:
    def test_removes_files_and_empty_dirs(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        fr = _make_dir_fetch(tmp_path)
        ops = resolve_skills(fr, ["claude"])
        deployed = execute_write_ops(ops, project)

        for rel in deployed:
            assert (project / rel).exists()

        cleanup_deployed_files(deployed, project)

        for rel in deployed:
            assert not (project / rel).exists()

        skill_dir = project / ".claude" / "skills" / "my-skill"
        assert not skill_dir.exists()

    def test_preserves_non_empty_dirs(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        fr = _make_dir_fetch(tmp_path)
        ops = resolve_skills(fr, ["claude"])
        deployed = execute_write_ops(ops, project)

        extra = project / ".claude" / "skills" / "my-skill" / "KEEP.md"
        extra.write_text("keep me")

        cleanup_deployed_files(deployed, project)

        for rel in deployed:
            assert not (project / rel).exists()
        assert extra.exists()

    def test_dry_run_no_files_removed(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        fr = _make_dir_fetch(tmp_path)
        ops = resolve_skills(fr, ["claude"])
        deployed = execute_write_ops(ops, project)

        cleanup_deployed_files(deployed, project, dry_run=True)

        for rel in deployed:
            assert (project / rel).exists()

    def test_handles_already_missing_files(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()

        cleanup_deployed_files(
            [".claude/skills/gone/SKILL.md"],
            project,
        )


# ---------------------------------------------------------------------------
# atomic_copy_file
# ---------------------------------------------------------------------------


class TestAtomicCopyFile:
    def test_file_exists_after_copy(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("payload")
        dst = tmp_path / "out" / "dst.txt"

        atomic_copy_file(src, dst)

        assert dst.exists()
        assert dst.read_text() == "payload"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst = tmp_path / "a" / "b" / "c" / "file.txt"

        atomic_copy_file(src, dst)

        assert dst.exists()
        assert dst.read_text() == "data"

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("new")
        dst = tmp_path / "dst.txt"
        dst.write_text("old")

        atomic_copy_file(src, dst)

        assert dst.read_text() == "new"


class TestAtomicCopyFailure:
    def test_cleans_up_temp_file_on_copy_failure(self, tmp_path: Path) -> None:
        """When shutil.copy2 fails, the temp file is cleaned up and error re-raised."""
        src = tmp_path / "src.txt"
        src.write_text("payload")
        dst = tmp_path / "out" / "dst.txt"

        with (
            patch("agpack.fileutil.shutil.copy2", side_effect=OSError("no space")),
            pytest.raises(OSError, match="no space"),
        ):
            atomic_copy_file(src, dst)

        leftover = list((tmp_path / "out").glob(".agpack-tmp-*"))
        assert leftover == []


# ---------------------------------------------------------------------------
# detect_skill_items — folder-of-skills expansion
# ---------------------------------------------------------------------------


class TestDetectSkillItems:
    def test_single_skill_directory(self, tmp_path: Path) -> None:
        """A directory with top-level files is detected as a single skill."""
        fr = _make_dir_fetch(tmp_path, name="my-skill")
        items = detect_skill_items(fr)

        assert len(items) == 1
        assert items[0][0] == "my-skill"

    def test_folder_of_skills(self, tmp_path: Path) -> None:
        """A directory with only subdirectories expands to one skill per subfolder."""
        fr = _make_folder_of_skills_fetch(tmp_path)
        items = detect_skill_items(fr)

        assert len(items) == 2
        names = [n for n, _ in items]
        assert names == ["skill-a", "skill-b"]

    def test_errors_on_empty_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "empty"
        src.mkdir(parents=True)
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/empty"], path="empty"),
            local_path=src,
            resolved_ref="abc1234",
        )

        with pytest.raises(ResolveError, match="does not contain any skill folders"):
            detect_skill_items(fr)

    def test_errors_on_dir_with_only_empty_subdirs(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "parent"
        (src / "empty-a").mkdir(parents=True)
        (src / "empty-b").mkdir(parents=True)
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/parent"], path="parent"),
            local_path=src,
            resolved_ref="abc1234",
        )

        with pytest.raises(ResolveError, match="does not contain any skill folders"):
            detect_skill_items(fr)


class TestResolveSkillFolderIntegration:
    """Test resolve + write together for folder-of-skills."""

    def test_deploys_each_subfolder_as_separate_skill(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_folder_of_skills_fetch(tmp_path)

        ops = resolve_skills(fr, ["claude"])
        all_deployed = execute_write_ops(ops, project)

        skill_a = project / ".claude" / "skills" / "skill-a" / "SKILL.md"
        skill_b = project / ".claude" / "skills" / "skill-b" / "SKILL.md"
        skill_b_lib = project / ".claude" / "skills" / "skill-b" / "lib" / "util.py"
        assert skill_a.exists()
        assert skill_a.read_text() == "# Skill A"
        assert skill_b.exists()
        assert skill_b_lib.exists()
        assert len(all_deployed) == 3

    def test_folder_of_skills_to_multiple_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_folder_of_skills_fetch(tmp_path)

        ops = resolve_skills(fr, ALL_TARGETS)
        all_deployed = execute_write_ops(ops, project)

        from agpack.targets import SKILL_DIRS

        assert len(all_deployed) == 3 * len(SKILL_DIRS)
        for _target, base in SKILL_DIRS.items():
            assert (project / base / "skill-a" / "SKILL.md").exists()
            assert (project / base / "skill-b" / "SKILL.md").exists()

    def test_folder_of_skills_dry_run(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_folder_of_skills_fetch(tmp_path)

        ops = resolve_skills(fr, ["claude"])
        all_deployed = execute_write_ops(ops, project, dry_run=True)

        assert len(all_deployed) == 3
        for rel in all_deployed:
            assert not (project / rel).exists()


# ---------------------------------------------------------------------------
# detect_single_file_items — folder-of-commands expansion
# ---------------------------------------------------------------------------


class TestDetectCommandItems:
    def test_single_file(self, tmp_path: Path) -> None:
        fr = _make_file_fetch(tmp_path, source_name="lint.md")
        items = detect_single_file_items(fr, "command")
        assert len(items) == 1
        assert items[0][0] == "lint.md"

    def test_directory_of_files(self, tmp_path: Path) -> None:
        fr = _make_dir_command_fetch(tmp_path)
        items = detect_single_file_items(fr, "command")
        assert len(items) == 2
        names = sorted(n for n, _ in items)
        assert names == ["format.md", "lint.md"]

    def test_files_from_subfolders(self, tmp_path: Path) -> None:
        """When top level has no files, files from subfolders are detected."""
        src = tmp_path / "src" / "cmds"
        (src / "group-a").mkdir(parents=True)
        (src / "group-a" / "lint.md").write_text("# Lint")
        (src / "group-b").mkdir(parents=True)
        (src / "group-b" / "format.md").write_text("# Format")
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/cmds"], path="cmds"),
            local_path=src,
            resolved_ref="abc1234",
        )

        items = detect_single_file_items(fr, "command")
        names = sorted(n for n, _ in items)
        assert names == ["format.md", "lint.md"]

    def test_errors_on_empty_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "empty"
        src.mkdir(parents=True)
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/empty"], path="empty"),
            local_path=src,
            resolved_ref="abc1234",
        )

        with pytest.raises(ResolveError, match="does not contain any command files"):
            detect_single_file_items(fr, "command")


class TestResolveCommandFolderIntegration:
    def test_deploys_each_file_from_directory(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_dir_command_fetch(tmp_path)

        ops = resolve_commands(fr, ["claude"])
        execute_write_ops(ops, project)

        assert (project / ".claude" / "commands" / "lint.md").exists()
        assert (project / ".claude" / "commands" / "format.md").exists()
        assert (project / ".claude" / "commands" / "lint.md").read_text() == "# Lint"

    def test_deploys_files_from_subfolders(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        src = tmp_path / "src" / "cmds"
        (src / "group-a").mkdir(parents=True)
        (src / "group-a" / "lint.md").write_text("# Lint")
        (src / "group-b").mkdir(parents=True)
        (src / "group-b" / "format.md").write_text("# Format")
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/cmds"], path="cmds"),
            local_path=src,
            resolved_ref="abc1234",
        )

        ops = resolve_commands(fr, ["claude"])
        execute_write_ops(ops, project)

        assert (project / ".claude" / "commands" / "lint.md").exists()
        assert (project / ".claude" / "commands" / "format.md").exists()

    def test_dry_run_with_directory(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = _make_dir_command_fetch(tmp_path)

        ops = resolve_commands(fr, ["claude"])
        all_deployed = execute_write_ops(ops, project, dry_run=True)

        assert len(all_deployed) == 2
        for rel in all_deployed:
            assert not (project / rel).exists()


# ---------------------------------------------------------------------------
# detect_single_file_items — folder-of-agents expansion
# ---------------------------------------------------------------------------


class TestDetectAgentItems:
    def test_single_file(self, tmp_path: Path) -> None:
        fr = _make_file_fetch(tmp_path, source_name="reviewer.md")
        items = detect_single_file_items(fr, "agent")
        assert len(items) == 1
        assert items[0][0] == "reviewer.md"

    def test_directory_of_files(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "agents"
        src.mkdir(parents=True)
        (src / "reviewer.md").write_text("# Reviewer")
        (src / "planner.md").write_text("# Planner")
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/agents"], path="agents"),
            local_path=src,
            resolved_ref="abc1234",
        )
        items = detect_single_file_items(fr, "agent")
        names = sorted(n for n, _ in items)
        assert names == ["planner.md", "reviewer.md"]

    def test_files_from_subfolders(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "agents"
        (src / "group").mkdir(parents=True)
        (src / "group" / "reviewer.md").write_text("# Reviewer")
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/agents"], path="agents"),
            local_path=src,
            resolved_ref="abc1234",
        )
        items = detect_single_file_items(fr, "agent")
        assert len(items) == 1
        assert items[0][0] == "reviewer.md"

    def test_errors_on_empty_directory(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "empty"
        src.mkdir(parents=True)
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/empty"], path="empty"),
            local_path=src,
            resolved_ref="abc1234",
        )

        with pytest.raises(ResolveError, match="does not contain any agent files"):
            detect_single_file_items(fr, "agent")


class TestResolveAgentFolderIntegration:
    def test_deploys_each_file_from_directory(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        src = tmp_path / "src" / "agents"
        src.mkdir(parents=True)
        (src / "reviewer.md").write_text("# Reviewer")
        (src / "planner.md").write_text("# Planner")
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/agents"], path="agents"),
            local_path=src,
            resolved_ref="abc1234",
        )

        ops = resolve_agents(fr, ["claude"])
        execute_write_ops(ops, project)

        assert (project / ".claude" / "agents" / "reviewer.md").exists()
        assert (project / ".claude" / "agents" / "planner.md").exists()

    def test_deploys_files_from_subfolders(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        src = tmp_path / "src" / "agents"
        (src / "group").mkdir(parents=True)
        (src / "group" / "reviewer.md").write_text("# Reviewer")
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/agents"], path="agents"),
            local_path=src,
            resolved_ref="abc1234",
        )

        ops = resolve_agents(fr, ["claude"])
        execute_write_ops(ops, project)

        assert (project / ".claude" / "agents" / "reviewer.md").exists()
