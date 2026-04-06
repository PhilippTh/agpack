"""Tests for agpack.rules — frontmatter parsing, format generation, managed sections,
and deployment.
"""

from __future__ import annotations

from pathlib import Path

from agpack.rules import RULES_END_MARKER
from agpack.rules import RULES_START_MARKER
from agpack.rules import build_managed_section
from agpack.rules import cleanup_rule_append_targets
from agpack.rules import deploy_rule_append_targets
from agpack.rules import deploy_single_rule
from agpack.rules import detect_rule_items
from agpack.rules import generate_mdc
from agpack.rules import get_rule_name
from agpack.rules import merge_into_managed_section
from agpack.rules import normalize_frontmatter_for_cursor
from agpack.rules import parse_rule_frontmatter
from agpack.rules import remove_managed_section

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
        # yaml.safe_load of empty string returns None → treated as no dict
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
        fm = {
            "description": "Test",
            "globs": ["*.ts"],
            "alwaysApply": False,
        }
        result = normalize_frontmatter_for_cursor(fm)
        assert result == {
            "description": "Test",
            "globs": ["*.ts"],
            "alwaysApply": False,
        }

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
        fm = {
            "name": "my-rule",
            "description": "Test",
            "custom_field": "value",
        }
        result = normalize_frontmatter_for_cursor(fm)
        assert "name" not in result
        assert "custom_field" not in result
        assert "description" in result


# ---------------------------------------------------------------------------
# generate_mdc
# ---------------------------------------------------------------------------


class TestGenerateMdc:
    def test_full_frontmatter(self) -> None:
        fm = {
            "description": "Test rule",
            "globs": ["*.ts", "*.tsx"],
            "alwaysApply": False,
        }
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
        rules = [
            ("rule-a", "- First rule."),
            ("rule-b", "- Second rule."),
        ]
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
        assert "## rule-a" in result

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
# deploy_single_rule (file-based targets)
# ---------------------------------------------------------------------------


class TestDeploySingleRule:
    def test_cursor_mdc(self, tmp_path: Path) -> None:
        fm = {
            "description": "Test",
            "globs": ["*.ts"],
            "alwaysApply": False,
        }
        deployed = deploy_single_rule(
            "ts-strict",
            fm,
            "\n# TS\n- Strict.\n",
            ["cursor"],
            tmp_path,
        )
        assert deployed == [".cursor/rules/ts-strict.mdc"]
        content = (tmp_path / ".cursor/rules/ts-strict.mdc").read_text()
        assert "---" in content
        assert "# TS" in content

    def test_windsurf_md(self, tmp_path: Path) -> None:
        deployed = deploy_single_rule(
            "my-rule",
            {},
            "\n# Rule\n- Content.\n",
            ["windsurf"],
            tmp_path,
        )
        assert deployed == [".windsurf/rules/my-rule.md"]
        content = (tmp_path / ".windsurf/rules/my-rule.md").read_text()
        assert "# Rule" in content
        assert "---" not in content

    def test_skips_append_targets(self, tmp_path: Path) -> None:
        """Append targets are not handled by deploy_single_rule."""
        deployed = deploy_single_rule(
            "rule",
            {},
            "body",
            ["claude", "codex", "opencode"],
            tmp_path,
        )
        assert deployed == []

    def test_mixed_targets(self, tmp_path: Path) -> None:
        deployed = deploy_single_rule(
            "rule",
            {},
            "\n# Body\n",
            ["claude", "cursor", "windsurf", "opencode"],
            tmp_path,
        )
        assert ".cursor/rules/rule.mdc" in deployed
        assert ".windsurf/rules/rule.md" in deployed
        assert len(deployed) == 2

    def test_dry_run_no_file(self, tmp_path: Path) -> None:
        deployed = deploy_single_rule(
            "rule",
            {},
            "\n# Body\n",
            ["cursor"],
            tmp_path,
            dry_run=True,
        )
        assert deployed == [".cursor/rules/rule.mdc"]
        assert not (tmp_path / ".cursor/rules/rule.mdc").exists()


# ---------------------------------------------------------------------------
# deploy_rule_append_targets
# ---------------------------------------------------------------------------


class TestDeployRuleAppendTargets:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        deployed = deploy_rule_append_targets(
            [("rule-a", "- Do this.")],
            ["claude"],
            tmp_path,
        )
        assert "CLAUDE.md" in deployed
        content = (tmp_path / "CLAUDE.md").read_text()
        assert RULES_START_MARKER in content
        assert "## rule-a" in content

    def test_updates_existing_file(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# My Project\n\nExisting.\n")
        deploy_rule_append_targets(
            [("rule-a", "- Do this.")],
            ["opencode"],
            tmp_path,
        )
        content = (tmp_path / "AGENTS.md").read_text()
        assert "# My Project" in content
        assert RULES_START_MARKER in content

    def test_shared_agents_md_written_once(self, tmp_path: Path) -> None:
        """codex, opencode, copilot all share AGENTS.md."""
        deployed = deploy_rule_append_targets(
            [("rule-a", "- Content.")],
            ["codex", "opencode", "copilot"],
            tmp_path,
        )
        agents_count = sum(1 for d in deployed if d == "AGENTS.md")
        assert agents_count == 1
        content = (tmp_path / "AGENTS.md").read_text()
        assert "## rule-a" in content

    def test_gemini_and_antigravity_deduplicated(self, tmp_path: Path) -> None:
        deployed = deploy_rule_append_targets(
            [("rule-a", "- Content.")],
            ["gemini", "antigravity"],
            tmp_path,
        )
        gemini_count = sum(1 for d in deployed if d == ".gemini/GEMINI.md")
        assert gemini_count == 1

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        deploy_rule_append_targets(
            [("rule-a", "- Do this.")],
            ["gemini"],
            tmp_path,
        )
        assert (tmp_path / ".gemini" / "GEMINI.md").exists()

    def test_dry_run_no_write(self, tmp_path: Path) -> None:
        deployed = deploy_rule_append_targets(
            [("rule-a", "- Do this.")],
            ["claude"],
            tmp_path,
            dry_run=True,
        )
        assert "CLAUDE.md" in deployed
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_mixed_targets(self, tmp_path: Path) -> None:
        deployed = deploy_rule_append_targets(
            [("rule-a", "- Content.")],
            ["claude", "cursor", "opencode", "windsurf"],
            tmp_path,
        )
        # Only append targets: CLAUDE.md and AGENTS.md
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
        # Should not raise
        cleanup_rule_append_targets(["claude"], tmp_path)

    def test_dry_run_no_change(self, tmp_path: Path) -> None:
        managed = f"{RULES_START_MARKER}\n## rule\n- Content.\n{RULES_END_MARKER}"
        target = tmp_path / "AGENTS.md"
        original = f"# Header\n\n{managed}\n"
        target.write_text(original)

        cleanup_rule_append_targets(
            ["opencode"],
            tmp_path,
            dry_run=True,
        )
        assert target.read_text() == original

    def test_deduplicates_shared_paths(self, tmp_path: Path) -> None:
        """codex + opencode + copilot should only clean AGENTS.md once."""
        managed = f"{RULES_START_MARKER}\n## rule\n- Content.\n{RULES_END_MARKER}"
        target = tmp_path / "AGENTS.md"
        target.write_text(f"# Header\n\n{managed}\n")

        cleanup_rule_append_targets(
            ["codex", "opencode", "copilot"],
            tmp_path,
        )
        content = target.read_text()
        assert RULES_START_MARKER not in content


# ---------------------------------------------------------------------------
# Config integration — rules are parsed correctly
# ---------------------------------------------------------------------------


class TestConfigRules:
    def test_rules_parsed_from_config(self, tmp_path: Path) -> None:
        from agpack.config import load_config

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
        cfg = load_config(cfg_path)
        assert len(cfg.rules) == 2
        assert cfg.rules[0].path == "rules/typescript.md"
        assert cfg.rules[1].ref == "v1.0"

    def test_empty_rules(self, tmp_path: Path) -> None:
        from agpack.config import load_config

        cfg_path = tmp_path / "agpack.yml"
        cfg_path.write_text("targets:\n  - claude\n")
        cfg = load_config(cfg_path)
        assert cfg.rules == []

    def test_rules_merged_from_global(self) -> None:
        from agpack.config import AgpackConfig
        from agpack.config import DependencySource
        from agpack.config import GlobalConfig
        from agpack.config import merge_configs

        project = AgpackConfig(
            targets=["claude"],
            rules=[DependencySource(urls=["https://github.com/a/b"], path="rules/proj")],
        )
        global_cfg = GlobalConfig(
            rules=[DependencySource(urls=["https://github.com/c/d"], path="rules/global")],
        )
        merged = merge_configs(project, global_cfg)
        assert len(merged.rules) == 2
        assert merged.rules[0].url == "https://github.com/a/b"
        assert merged.rules[1].url == "https://github.com/c/d"

    def test_rules_deduped_on_merge(self) -> None:
        from agpack.config import AgpackConfig
        from agpack.config import DependencySource
        from agpack.config import GlobalConfig
        from agpack.config import merge_configs

        dep = DependencySource(urls=["https://github.com/a/b"], path="rules/shared")
        project = AgpackConfig(targets=["claude"], rules=[dep])
        global_cfg = GlobalConfig(
            rules=[DependencySource(urls=["https://github.com/a/b"], path="rules/shared")],
        )
        merged = merge_configs(project, global_cfg)
        assert len(merged.rules) == 1


# ---------------------------------------------------------------------------
# Deployer integration — rule detection
# ---------------------------------------------------------------------------


class TestDetectRuleItems:
    def test_single_file(self, tmp_path: Path) -> None:
        from agpack.config import DependencySource
        from agpack.fetcher import FetchResult

        src = tmp_path / "src" / "my-rule.md"
        src.parent.mkdir(parents=True)
        src.write_text("# Rule\n- Content.\n")
        fr = FetchResult(
            source=DependencySource(
                urls=["https://github.com/org/repo"],
                path="rules/my-rule.md",
            ),
            local_path=src,
            resolved_ref="abc1234",
        )
        items = detect_rule_items(fr)
        assert len(items) == 1
        assert items[0][0] == "my-rule.md"

    def test_directory_of_rules(self, tmp_path: Path) -> None:
        from agpack.config import DependencySource
        from agpack.fetcher import FetchResult

        src = tmp_path / "src" / "rules"
        src.mkdir(parents=True)
        (src / "ts.md").write_text("# TS\n")
        (src / "react.md").write_text("# React\n")
        fr = FetchResult(
            source=DependencySource(urls=["https://github.com/org/repo"], path="rules"),
            local_path=src,
            resolved_ref="abc1234",
        )
        items = detect_rule_items(fr)
        assert len(items) == 2
        names = {name for name, _ in items}
        assert "ts.md" in names
        assert "react.md" in names
