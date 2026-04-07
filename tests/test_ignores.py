"""Tests for ignore file resolution — managed sections, deployment via resolvers + writer,
and cleanup.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agpack.cleanup import cleanup_ignore_files
from agpack.config import DependencySource
from agpack.config import load_resolved_config
from agpack.fetcher import FetchResult
from agpack.resolvers import resolve_ignores
from agpack.resolvers import resolve_ignores_append
from agpack.writer import IGNORE_END_MARKER
from agpack.writer import IGNORE_START_MARKER
from agpack.writer import build_ignore_section
from agpack.writer import execute_write_ops
from agpack.writer import merge_into_ignore_section
from agpack.writer import remove_ignore_section


# ---------------------------------------------------------------------------
# build_ignore_section
# ---------------------------------------------------------------------------


class TestBuildIgnoreSection:
    def test_simple_patterns(self) -> None:
        result = build_ignore_section(".env*\n*.pem\n*.key")
        assert IGNORE_START_MARKER in result
        assert IGNORE_END_MARKER in result
        assert ".env*" in result
        assert "*.pem" in result
        assert "*.key" in result

    def test_empty_patterns(self) -> None:
        result = build_ignore_section("")
        assert IGNORE_START_MARKER in result
        assert IGNORE_END_MARKER in result

    def test_strips_leading_trailing_whitespace(self) -> None:
        result = build_ignore_section("\n  .env*  \n  *.pem  \n")
        assert IGNORE_START_MARKER in result
        assert ".env*" in result


# ---------------------------------------------------------------------------
# merge_into_ignore_section
# ---------------------------------------------------------------------------


class TestMergeIntoIgnoreSection:
    def test_append_to_empty_file(self) -> None:
        result = merge_into_ignore_section("", ".env*\n*.pem")
        assert IGNORE_START_MARKER in result
        assert ".env*" in result

    def test_append_to_existing_content(self) -> None:
        existing = "# My ignore patterns\nnode_modules/\n"
        result = merge_into_ignore_section(existing, ".env*")
        assert result.startswith("# My ignore patterns")
        assert IGNORE_START_MARKER in result
        assert ".env*" in result

    def test_replace_existing_managed_section(self) -> None:
        existing = (
            f"# Custom\nnode_modules/\n\n{IGNORE_START_MARKER}\n# old patterns\n.env\n{IGNORE_END_MARKER}\n\n# Footer\n"
        )
        result = merge_into_ignore_section(existing, "*.pem\n*.key")
        assert "old patterns" not in result
        assert "*.pem" in result
        assert "*.key" in result
        assert "# Footer" in result
        assert result.count(IGNORE_START_MARKER) == 1

    def test_preserves_content_outside_markers(self) -> None:
        existing = f"# Header\nnode_modules/\n\n{IGNORE_START_MARKER}\n.env\n{IGNORE_END_MARKER}\n\ndist/\n"
        result = merge_into_ignore_section(existing, "*.pem")
        assert "# Header" in result
        assert "node_modules/" in result
        assert "dist/" in result


# ---------------------------------------------------------------------------
# remove_ignore_section
# ---------------------------------------------------------------------------


class TestRemoveIgnoreSection:
    def test_removes_section(self) -> None:
        content = f"# Header\nnode_modules/\n\n{IGNORE_START_MARKER}\n.env*\n*.pem\n{IGNORE_END_MARKER}\n\ndist/\n"
        result = remove_ignore_section(content)
        assert IGNORE_START_MARKER not in result
        assert IGNORE_END_MARKER not in result
        assert "# Header" in result
        assert "dist/" in result

    def test_returns_empty_for_fully_managed(self) -> None:
        content = f"{IGNORE_START_MARKER}\n.env*\n{IGNORE_END_MARKER}\n"
        result = remove_ignore_section(content)
        assert result == ""

    def test_no_section_returns_content_unchanged(self) -> None:
        content = "node_modules/\ndist/\n"
        result = remove_ignore_section(content)
        assert result == content


# ---------------------------------------------------------------------------
# resolve_ignores — reads patterns from fetched files
# ---------------------------------------------------------------------------


class TestResolveIgnores:
    def _make_ignore_file(self, tmp_path: Path, name: str, content: str) -> FetchResult:
        src = tmp_path / "src" / name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(content)
        return FetchResult(
            source=DependencySource(urls=["https://github.com/org/ignores"], path=f"ignores/{name}"),
            local_path=src,
            resolved_ref="abc1234",
        )

    def test_reads_patterns_from_file(self, tmp_path: Path) -> None:
        fr = self._make_ignore_file(tmp_path, "security.ignore", ".env*\n*.pem\n*.key\n")
        ops, patterns = resolve_ignores(fr, ["claude", "cursor"])
        assert ops == []
        assert len(patterns) == 1
        assert ".env*" in patterns[0]
        assert "*.pem" in patterns[0]

    def test_empty_file_no_patterns(self, tmp_path: Path) -> None:
        fr = self._make_ignore_file(tmp_path, "empty.ignore", "")
        ops, patterns = resolve_ignores(fr, ["claude"])
        assert ops == []
        assert patterns == []

    def test_whitespace_only_file_no_patterns(self, tmp_path: Path) -> None:
        fr = self._make_ignore_file(tmp_path, "blank.ignore", "   \n\n  \n")
        ops, patterns = resolve_ignores(fr, ["claude"])
        assert ops == []
        assert patterns == []

    def test_directory_of_ignore_files(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "ignores"
        src.mkdir(parents=True)
        (src / "security.ignore").write_text(".env*\n*.pem\n")
        (src / "build.ignore").write_text("dist/\nbuild/\n")
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/repo"], path="ignores"),
            local_path=src,
            resolved_ref="abc1234",
        )
        ops, patterns = resolve_ignores(fr, ["claude"])
        assert ops == []
        assert len(patterns) == 2


# ---------------------------------------------------------------------------
# resolve_ignores_append — produces IgnoreSectionOps for targets
# ---------------------------------------------------------------------------


class TestResolveIgnoresAppend:
    def test_creates_ignore_files(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        ops = resolve_ignores_append([".env*\n*.pem"], ["claude", "cursor"])
        deployed = execute_write_ops(ops, project)

        assert ".claudeignore" in deployed
        assert ".cursorignore" in deployed

        claude_content = (project / ".claudeignore").read_text()
        assert IGNORE_START_MARKER in claude_content
        assert ".env*" in claude_content

        cursor_content = (project / ".cursorignore").read_text()
        assert IGNORE_START_MARKER in cursor_content
        assert "*.pem" in cursor_content

    def test_updates_existing_ignore_file(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / ".claudeignore").write_text("# My patterns\nnode_modules/\n")

        ops = resolve_ignores_append([".env*"], ["claude"])
        execute_write_ops(ops, project)

        content = (project / ".claudeignore").read_text()
        assert "# My patterns" in content
        assert "node_modules/" in content
        assert IGNORE_START_MARKER in content
        assert ".env*" in content

    def test_windsurf_target(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        ops = resolve_ignores_append([".env*"], ["windsurf"])
        deployed = execute_write_ops(ops, project)

        assert ".codeiumignore" in deployed
        content = (project / ".codeiumignore").read_text()
        assert ".env*" in content

    def test_skips_targets_without_ignore_file(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        ops = resolve_ignores_append([".env*"], ["opencode", "gemini", "codex"])
        deployed = execute_write_ops(ops, project)
        assert deployed == []

    def test_empty_patterns_no_ops(self) -> None:
        ops = resolve_ignores_append([], ["claude", "cursor"])
        assert ops == []

    def test_combines_multiple_pattern_sources(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        ops = resolve_ignores_append([".env*\n*.pem", "dist/\nbuild/"], ["claude"])
        deployed = execute_write_ops(ops, project)

        content = (project / ".claudeignore").read_text()
        assert ".env*" in content
        assert "*.pem" in content
        assert "dist/" in content
        assert "build/" in content

    def test_dry_run_no_write(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        ops = resolve_ignores_append([".env*"], ["claude"])
        deployed = execute_write_ops(ops, project, dry_run=True)

        assert ".claudeignore" in deployed
        assert not (project / ".claudeignore").exists()


# ---------------------------------------------------------------------------
# cleanup_ignore_files
# ---------------------------------------------------------------------------


class TestCleanupIgnoreFiles:
    def test_removes_managed_section(self, tmp_path: Path) -> None:
        managed = f"{IGNORE_START_MARKER}\n.env*\n*.pem\n{IGNORE_END_MARKER}"
        target = tmp_path / ".claudeignore"
        target.write_text(f"# My patterns\nnode_modules/\n\n{managed}\n")
        cleanup_ignore_files(["claude"], tmp_path)
        content = target.read_text()
        assert IGNORE_START_MARKER not in content
        assert "# My patterns" in content
        assert "node_modules/" in content

    def test_no_op_if_file_missing(self, tmp_path: Path) -> None:
        cleanup_ignore_files(["claude"], tmp_path)

    def test_dry_run_no_change(self, tmp_path: Path) -> None:
        managed = f"{IGNORE_START_MARKER}\n.env*\n{IGNORE_END_MARKER}"
        target = tmp_path / ".claudeignore"
        original = f"# Patterns\n\n{managed}\n"
        target.write_text(original)
        cleanup_ignore_files(["claude"], tmp_path, dry_run=True)
        assert target.read_text() == original

    def test_skips_targets_without_ignore_file(self, tmp_path: Path) -> None:
        # Should not fail for targets that don't have ignore files
        cleanup_ignore_files(["opencode", "gemini", "codex"], tmp_path)

    def test_multiple_targets(self, tmp_path: Path) -> None:
        managed = f"{IGNORE_START_MARKER}\n.env*\n{IGNORE_END_MARKER}"
        for name in [".claudeignore", ".cursorignore", ".codeiumignore"]:
            (tmp_path / name).write_text(f"# Custom\n\n{managed}\n")

        cleanup_ignore_files(["claude", "cursor", "windsurf"], tmp_path)

        for name in [".claudeignore", ".cursorignore", ".codeiumignore"]:
            content = (tmp_path / name).read_text()
            assert IGNORE_START_MARKER not in content
            assert "# Custom" in content


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigIgnores:
    def test_ignores_parsed_from_config(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "agpack.yml"
        cfg_path.write_text("""\
targets:
  - claude
  - cursor
dependencies:
  ignores:
    - url: https://github.com/org/ignores-repo
      path: ignores/security.ignore
    - url: https://github.com/org/ignores-repo
      path: ignores/build.ignore
      ref: v1.0
""")
        cfg = load_resolved_config(cfg_path, no_global=True)
        assert len(cfg.ignores) == 2
        assert cfg.ignores[0].path == "ignores/security.ignore"
        assert cfg.ignores[1].ref == "v1.0"

    def test_empty_ignores(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "agpack.yml"
        cfg_path.write_text("targets:\n  - claude\n")
        cfg = load_resolved_config(cfg_path, no_global=True)
        assert cfg.ignores == []

    def test_ignores_merged_from_global(self, tmp_path: Path) -> None:
        import os

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "agpack.yml").write_text("""\
targets:
  - claude
dependencies:
  ignores:
    - url: https://github.com/a/b
      path: ignores/proj
""")
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        (global_dir / "agpack.yml").write_text("""\
dependencies:
  ignores:
    - url: https://github.com/c/d
      path: ignores/global
""")
        old = os.environ.get("AGPACK_GLOBAL_CONFIG")
        os.environ["AGPACK_GLOBAL_CONFIG"] = str(global_dir / "agpack.yml")
        try:
            merged = load_resolved_config(project_dir / "agpack.yml")
        finally:
            if old is None:
                os.environ.pop("AGPACK_GLOBAL_CONFIG", None)
            else:
                os.environ["AGPACK_GLOBAL_CONFIG"] = old
        assert len(merged.ignores) == 2

    def test_ignores_deduped_on_merge(self, tmp_path: Path) -> None:
        import os

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "agpack.yml").write_text("""\
targets:
  - claude
dependencies:
  ignores:
    - url: https://github.com/a/b
      path: ignores/shared
""")
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        (global_dir / "agpack.yml").write_text("""\
dependencies:
  ignores:
    - url: https://github.com/a/b
      path: ignores/shared
""")
        old = os.environ.get("AGPACK_GLOBAL_CONFIG")
        os.environ["AGPACK_GLOBAL_CONFIG"] = str(global_dir / "agpack.yml")
        try:
            merged = load_resolved_config(project_dir / "agpack.yml")
        finally:
            if old is None:
                os.environ.pop("AGPACK_GLOBAL_CONFIG", None)
            else:
                os.environ["AGPACK_GLOBAL_CONFIG"] = old
        assert len(merged.ignores) == 1
