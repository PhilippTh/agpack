"""Tests for rule resolution — frontmatter parsing, format generation, managed sections,
and deployment via resolvers + writer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agpack.cleanup import cleanup_rule_append_targets
from agpack.config import DependencySource
from agpack.fetcher import FetchResult
from agpack.resolvers import detect_single_file_items
from agpack.resolvers import generate_mdc
from agpack.resolvers import get_rule_name
from agpack.resolvers import normalize_frontmatter_for_cursor
from agpack.resolvers import parse_rule_frontmatter
from agpack.resolvers import resolve_rules
from agpack.resolvers import resolve_rules_append
from agpack.writer import RULES_END_MARKER
from agpack.writer import RULES_START_MARKER
from agpack.writer import build_managed_section
from agpack.writer import execute_write_ops
from agpack.writer import merge_into_managed_section
from agpack.writer import remove_managed_section

# ---------------------------------------------------------------------------
# parse_rule_frontmatter
# ---------------------------------------------------------------------------


class TestParseRuleFrontmatter:
    def test_with_frontmatter(self) -> None:
        content = """\
---
name: typescript-strict
description: Enforce strict TypeScript conventions
globs: ["*.ts", "*.tsx"]
alwaysApply: true
---

# TypeScript Standards
- Use strict typing.
"""
        fm, body = parse_rule_frontmatter(content)
        assert fm["name"] == "typescript-strict"
        assert fm["description"] == "Enforce strict TypeScript conventions"
        assert fm["globs"] == ["*.ts", "*.tsx"]
        assert fm["alwaysApply"] is True
        assert "# TypeScript Standards" in body

    def test_without_frontmatter(self) -> None:
        content = "# Just markdown\n- No frontmatter here.\n"
        fm, body = parse_rule_frontmatter(content)
        assert fm == {}
        assert body == content

    def test_empty_frontmatter(self) -> None:
        content = "---\n---\n\n# Body\n"
        fm, body = parse_rule_frontmatter(content)
        assert fm == {}

    def test_with_apply_to_field(self) -> None:
        content = """\
---
applyTo: "**/*.ts,**/*.tsx"
---

# TS rules
"""
        fm, body = parse_rule_frontmatter(content)
        assert fm["applyTo"] == "**/*.ts,**/*.tsx"
        assert "# TS rules" in body

    def test_malformed_yaml_returns_empty(self) -> None:
        content = "---\n: [\ninvalid yaml\n---\n\n# Body\n"
        fm, body = parse_rule_frontmatter(content)
        assert fm == {}

    def test_frontmatter_that_is_not_a_dict(self) -> None:
        content = "---\n- a list\n- not a dict\n---\n\n# Body\n"
        fm, body = parse_rule_frontmatter(content)
        assert fm == {}


# ---------------------------------------------------------------------------
# normalize_frontmatter_for_cursor
# ---------------------------------------------------------------------------


class TestNormalizeFrontmatterForCursor:
    def test_cursor_native_passthrough(self) -> None:
        fm = {"description": "Test", "globs": ["*.ts"], "alwaysApply": False}
        result = normalize_frontmatter_for_cursor(fm)
        assert result == {"description": "Test", "globs": ["*.ts"], "alwaysApply": False}

    def test_translate_apply_to_to_globs(self) -> None:
        fm = {"applyTo": "**/*.ts, **/*.tsx"}
        result = normalize_frontmatter_for_cursor(fm)
        assert result["globs"] == ["**/*.ts", "**/*.tsx"]
        assert "applyTo" not in result

    def test_apply_to_as_list(self) -> None:
        fm = {"applyTo": ["**/*.ts", "**/*.tsx"]}
        result = normalize_frontmatter_for_cursor(fm)
        assert result["globs"] == ["**/*.ts", "**/*.tsx"]

    def test_globs_takes_precedence_over_apply_to(self) -> None:
        fm = {"globs": ["*.py"], "applyTo": "*.ts"}
        result = normalize_frontmatter_for_cursor(fm)
        assert result["globs"] == ["*.py"]

    def test_default_always_apply_when_no_patterns(self) -> None:
        fm = {"description": "A rule"}
        result = normalize_frontmatter_for_cursor(fm)
        assert result["alwaysApply"] is True

    def test_no_default_when_globs_present(self) -> None:
        fm = {"globs": ["*.ts"]}
        result = normalize_frontmatter_for_cursor(fm)
        assert "alwaysApply" not in result

    def test_no_default_when_always_apply_explicitly_false(self) -> None:
        fm = {"alwaysApply": False, "description": "Agent decides"}
        result = normalize_frontmatter_for_cursor(fm)
        assert result["alwaysApply"] is False

    def test_empty_frontmatter_defaults_to_always_apply(self) -> None:
        result = normalize_frontmatter_for_cursor({})
        assert result == {"alwaysApply": True}

    def test_strips_unknown_fields(self) -> None:
        fm = {"name": "my-rule", "description": "Test", "custom_field": "value"}
        result = normalize_frontmatter_for_cursor(fm)
        assert "name" not in result
        assert "custom_field" not in result
        assert "description" in result


# ---------------------------------------------------------------------------
# generate_mdc
# ---------------------------------------------------------------------------


class TestGenerateMdc:
    def test_full_frontmatter(self) -> None:
        fm = {"description": "Test rule", "globs": ["*.ts", "*.tsx"], "alwaysApply": False}
        body = "\n# TypeScript\n- Use strict types.\n"
        result = generate_mdc(fm, body)
        assert result.startswith("---\n")
        assert "description: Test rule" in result
        assert 'globs: ["*.ts", "*.tsx"]' in result
        assert "alwaysApply: false" in result
        assert "# TypeScript" in result

    def test_always_apply_no_globs(self) -> None:
        fm: dict[str, object] = {}
        body = "\n# Always on\n"
        result = generate_mdc(fm, body)
        assert "alwaysApply: true" in result
        assert "globs" not in result

    def test_translates_apply_to(self) -> None:
        fm = {"applyTo": "**/*.py"}
        body = "\n# Python\n"
        result = generate_mdc(fm, body)
        assert 'globs: ["**/*.py"]' in result
        assert "applyTo" not in result

    def test_description_only_agent_requested(self) -> None:
        fm = {"description": "Database migrations", "alwaysApply": False}
        body = "\n# Migrations\n"
        result = generate_mdc(fm, body)
        assert "description: Database migrations" in result
        assert "alwaysApply: false" in result
        assert "globs" not in result


# ---------------------------------------------------------------------------
# Managed section logic
# ---------------------------------------------------------------------------


class TestBuildManagedSection:
    def test_single_rule(self) -> None:
        result = build_managed_section([("my-rule", "- Do this.")])
        assert RULES_START_MARKER in result
        assert RULES_END_MARKER in result
        assert "## my-rule" in result
        assert "- Do this." in result

    def test_multiple_rules(self) -> None:
        rules = [("rule-a", "- First rule."), ("rule-b", "- Second rule.")]
        result = build_managed_section(rules)
        assert "## rule-a" in result
        assert "## rule-b" in result
        assert result.index("## rule-a") < result.index("## rule-b")


class TestMergeIntoManagedSection:
    def test_append_to_empty_file(self) -> None:
        result = merge_into_managed_section("", [("rule-a", "- Content.")])
        assert RULES_START_MARKER in result
        assert "## rule-a" in result

    def test_append_to_existing_content(self) -> None:
        existing = "# My Project\n\nSome existing content.\n"
        result = merge_into_managed_section(existing, [("rule-a", "- Content.")])
        assert result.startswith("# My Project")
        assert RULES_START_MARKER in result

    def test_replace_existing_managed_section(self) -> None:
        existing = f"# Project\n\n{RULES_START_MARKER}\nold content\n{RULES_END_MARKER}\n\n# Footer\n"
        result = merge_into_managed_section(existing, [("new-rule", "- New.")])
        assert "old content" not in result
        assert "## new-rule" in result
        assert "# Footer" in result
        assert result.count(RULES_START_MARKER) == 1

    def test_preserves_content_outside_markers(self) -> None:
        existing = f"# Header\n\n{RULES_START_MARKER}\nold\n{RULES_END_MARKER}\n\n# Footer\n"
        result = merge_into_managed_section(existing, [("rule", "- Content.")])
        assert "# Header" in result
        assert "# Footer" in result


class TestRemoveManagedSection:
    def test_removes_section(self) -> None:
        content = f"# Header\n\n{RULES_START_MARKER}\n## rule\n- Content.\n{RULES_END_MARKER}\n\n# Footer\n"
        result = remove_managed_section(content)
        assert RULES_START_MARKER not in result
        assert RULES_END_MARKER not in result
        assert "# Header" in result
        assert "# Footer" in result

    def test_returns_empty_for_fully_managed(self) -> None:
        content = f"{RULES_START_MARKER}\n## rule\n- Content.\n{RULES_END_MARKER}\n"
        result = remove_managed_section(content)
        assert result == ""

    def test_no_section_returns_content_unchanged(self) -> None:
        content = "# Just content\n"
        result = remove_managed_section(content)
        assert result == content


# ---------------------------------------------------------------------------
# get_rule_name
# ---------------------------------------------------------------------------


class TestGetRuleName:
    def test_from_frontmatter(self) -> None:
        assert get_rule_name({"name": "my-rule"}, "other.md") == "my-rule"

    def test_from_filename_stem(self) -> None:
        assert get_rule_name({}, "typescript-strict.md") == "typescript-strict"

    def test_from_filename_mdc(self) -> None:
        assert get_rule_name({}, "react-patterns.mdc") == "react-patterns"

    def test_frontmatter_name_takes_precedence(self) -> None:
        assert get_rule_name({"name": "custom"}, "other.md") == "custom"


# ---------------------------------------------------------------------------
# resolve_rules — file-based targets via resolvers + writer
# ---------------------------------------------------------------------------


class TestResolveRulesFileTargets:
    def _make_rule_file(self, tmp_path: Path, name: str, content: str) -> FetchResult:
        src = tmp_path / "src" / name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(content)
        return FetchResult(
            source=DependencySource(urls=["https://github.com/org/rules"], path=f"rules/{name}"),
            local_path=src,
            resolved_ref="abc1234",
        )

    def test_cursor_mdc(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = self._make_rule_file(
            tmp_path,
            "ts-strict.md",
            '---\ndescription: Test\nglobs:\n  - "*.ts"\nalwaysApply: false\n---\n\n# TS\n- Strict.\n',
        )

        ops, bodies = resolve_rules(fr, ["cursor"])
        deployed = execute_write_ops(ops, project)

        assert deployed == [".cursor/rules/ts-strict.mdc"]
        content = (project / ".cursor/rules/ts-strict.mdc").read_text()
        assert "---" in content
        assert "# TS" in content

    def test_windsurf_md(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = self._make_rule_file(tmp_path, "my-rule.md", "\n# Rule\n- Content.\n")

        ops, bodies = resolve_rules(fr, ["windsurf"])
        deployed = execute_write_ops(ops, project)

        assert deployed == [".windsurf/rules/my-rule.md"]
        content = (project / ".windsurf/rules/my-rule.md").read_text()
        assert "# Rule" in content
        assert "---" not in content

    def test_skips_append_targets(self, tmp_path: Path) -> None:
        fr = self._make_rule_file(tmp_path, "rule.md", "body")
        ops, bodies = resolve_rules(fr, ["claude", "codex", "opencode"])
        assert ops == []

    def test_mixed_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = self._make_rule_file(tmp_path, "rule.md", "\n# Body\n")

        ops, bodies = resolve_rules(fr, ["claude", "cursor", "windsurf", "opencode"])
        deployed = execute_write_ops(ops, project)

        assert ".cursor/rules/rule.mdc" in deployed
        assert ".windsurf/rules/rule.md" in deployed
        assert len(deployed) == 2

    def test_dry_run_no_file(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = self._make_rule_file(tmp_path, "rule.md", "\n# Body\n")

        ops, bodies = resolve_rules(fr, ["cursor"])
        deployed = execute_write_ops(ops, project, dry_run=True)

        assert deployed == [".cursor/rules/rule.mdc"]
        assert not (project / ".cursor/rules/rule.mdc").exists()


# ---------------------------------------------------------------------------
# resolve_rules_append — append-based targets via resolvers + writer
# ---------------------------------------------------------------------------


class TestResolveRulesAppendTargets:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        ops = resolve_rules_append([("rule-a", "- Do this.")], ["claude"])
        deployed = execute_write_ops(ops, project)

        assert "CLAUDE.md" in deployed
        content = (project / "CLAUDE.md").read_text()
        assert RULES_START_MARKER in content
        assert "## rule-a" in content

    def test_updates_existing_file(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / "AGENTS.md").write_text("# My Project\n\nExisting.\n")

        ops = resolve_rules_append([("rule-a", "- Do this.")], ["opencode"])
        execute_write_ops(ops, project)

        content = (project / "AGENTS.md").read_text()
        assert "# My Project" in content
        assert RULES_START_MARKER in content

    def test_shared_agents_md_written_once(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        ops = resolve_rules_append([("rule-a", "- Content.")], ["codex", "opencode", "copilot"])
        deployed = execute_write_ops(ops, project)

        agents_count = sum(1 for d in deployed if d == "AGENTS.md")
        assert agents_count == 1

    def test_gemini_and_antigravity_deduplicated(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        ops = resolve_rules_append([("rule-a", "- Content.")], ["gemini", "antigravity"])
        deployed = execute_write_ops(ops, project)

        gemini_count = sum(1 for d in deployed if d == ".gemini/GEMINI.md")
        assert gemini_count == 1

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        ops = resolve_rules_append([("rule-a", "- Do this.")], ["gemini"])
        execute_write_ops(ops, project)
        assert (project / ".gemini" / "GEMINI.md").exists()

    def test_dry_run_no_write(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        ops = resolve_rules_append([("rule-a", "- Do this.")], ["claude"])
        deployed = execute_write_ops(ops, project, dry_run=True)

        assert "CLAUDE.md" in deployed
        assert not (project / "CLAUDE.md").exists()

    def test_mixed_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        ops = resolve_rules_append([("rule-a", "- Content.")], ["claude", "cursor", "opencode", "windsurf"])
        deployed = execute_write_ops(ops, project)

        assert "CLAUDE.md" in deployed
        assert "AGENTS.md" in deployed
        assert len(deployed) == 2


# ---------------------------------------------------------------------------
# cleanup_rule_append_targets
# ---------------------------------------------------------------------------


class TestCleanupRuleAppendTargets:
    def test_removes_managed_section(self, tmp_path: Path) -> None:
        managed = f"{RULES_START_MARKER}\n## rule\n- Content.\n{RULES_END_MARKER}"
        target = tmp_path / "AGENTS.md"
        target.write_text(f"# Header\n\n{managed}\n\n# Footer\n")
        cleanup_rule_append_targets(["opencode"], tmp_path)
        content = target.read_text()
        assert RULES_START_MARKER not in content
        assert "# Header" in content
        assert "# Footer" in content

    def test_no_op_if_file_missing(self, tmp_path: Path) -> None:
        cleanup_rule_append_targets(["claude"], tmp_path)

    def test_dry_run_no_change(self, tmp_path: Path) -> None:
        managed = f"{RULES_START_MARKER}\n## rule\n- Content.\n{RULES_END_MARKER}"
        target = tmp_path / "AGENTS.md"
        original = f"# Header\n\n{managed}\n"
        target.write_text(original)
        cleanup_rule_append_targets(["opencode"], tmp_path, dry_run=True)
        assert target.read_text() == original

    def test_deduplicates_shared_paths(self, tmp_path: Path) -> None:
        managed = f"{RULES_START_MARKER}\n## rule\n- Content.\n{RULES_END_MARKER}"
        target = tmp_path / "AGENTS.md"
        target.write_text(f"# Header\n\n{managed}\n")
        cleanup_rule_append_targets(["codex", "opencode", "copilot"], tmp_path)
        content = target.read_text()
        assert RULES_START_MARKER not in content


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigRules:
    def test_rules_parsed_from_config(self, tmp_path: Path) -> None:
        from agpack.config import load_resolved_config

        cfg_path = tmp_path / "agpack.yml"
        cfg_path.write_text("""\
targets:
  - claude
  - cursor
dependencies:
  rules:
    - url: https://github.com/org/rules-repo
      path: rules/typescript.md
    - url: https://github.com/org/rules-repo
      path: rules/react.md
      ref: v1.0
""")
        cfg = load_resolved_config(cfg_path, no_global=True)
        assert len(cfg.rules) == 2
        assert cfg.rules[0].path == "rules/typescript.md"
        assert cfg.rules[1].ref == "v1.0"

    def test_empty_rules(self, tmp_path: Path) -> None:
        from agpack.config import load_resolved_config

        cfg_path = tmp_path / "agpack.yml"
        cfg_path.write_text("targets:\n  - claude\n")
        cfg = load_resolved_config(cfg_path, no_global=True)
        assert cfg.rules == []

    def test_rules_merged_from_global(self, tmp_path: Path) -> None:
        import os

        from agpack.config import load_resolved_config

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "agpack.yml").write_text("""\
targets:
  - claude
dependencies:
  rules:
    - url: https://github.com/a/b
      path: rules/proj
""")
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        (global_dir / "agpack.yml").write_text("""\
dependencies:
  rules:
    - url: https://github.com/c/d
      path: rules/global
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
        assert len(merged.rules) == 2

    def test_rules_deduped_on_merge(self, tmp_path: Path) -> None:
        import os

        from agpack.config import load_resolved_config

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "agpack.yml").write_text("""\
targets:
  - claude
dependencies:
  rules:
    - url: https://github.com/a/b
      path: rules/shared
""")
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        (global_dir / "agpack.yml").write_text("""\
dependencies:
  rules:
    - url: https://github.com/a/b
      path: rules/shared
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
        assert len(merged.rules) == 1


# ---------------------------------------------------------------------------
# Rule item detection via resolvers
# ---------------------------------------------------------------------------


class TestDetectRuleItems:
    def test_single_file(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "my-rule.md"
        src.parent.mkdir(parents=True)
        src.write_text("# Rule\n- Content.\n")
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/repo"], path="rules/my-rule.md"),
            local_path=src,
            resolved_ref="abc1234",
        )
        items = detect_single_file_items(fr, "rule")
        assert len(items) == 1
        assert items[0][0] == "my-rule.md"

    def test_directory_of_rules(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "rules"
        src.mkdir(parents=True)
        (src / "ts.md").write_text("# TS\n")
        (src / "react.md").write_text("# React\n")
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/repo"], path="rules"),
            local_path=src,
            resolved_ref="abc1234",
        )
        items = detect_single_file_items(fr, "rule")
        assert len(items) == 2
        names = {name for name, _ in items}
        assert "ts.md" in names
        assert "react.md" in names
