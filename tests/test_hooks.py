"""Tests for hook resolution — script deployment, config merging, event translation,
and cleanup.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agpack.cleanup import cleanup_hook_configs
from agpack.config import DependencySource
from agpack.config import HookConfig
from agpack.config import load_resolved_config
from agpack.fetcher import FetchResult
from agpack.resolvers import resolve_hook_configs
from agpack.resolvers import resolve_hooks
from agpack.targets import translate_hook_event
from agpack.writer import execute_write_ops

# ---------------------------------------------------------------------------
# translate_hook_event
# ---------------------------------------------------------------------------


class TestTranslateHookEvent:
    def test_claude_passthrough(self) -> None:
        assert translate_hook_event("PostToolUse", "claude") == "PostToolUse"

    def test_cursor_post_tool_use(self) -> None:
        assert translate_hook_event("PostToolUse", "cursor") == "afterFileEdit"

    def test_cursor_pre_tool_use(self) -> None:
        assert translate_hook_event("PreToolUse", "cursor") == "beforeFileEdit"

    def test_unknown_event_passthrough(self) -> None:
        assert translate_hook_event("CustomEvent", "cursor") == "CustomEvent"

    def test_unknown_target_passthrough(self) -> None:
        assert translate_hook_event("PostToolUse", "opencode") == "PostToolUse"


# ---------------------------------------------------------------------------
# resolve_hooks — script file deployment
# ---------------------------------------------------------------------------


class TestResolveHooks:
    def _make_hook_file(self, tmp_path: Path, name: str, content: str) -> FetchResult:
        src = tmp_path / "src" / name
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(content)
        return FetchResult(
            source=DependencySource(
                urls=["https://github.com/org/hooks"],
                path=f"hooks/{name}",
            ),
            local_path=src,
            resolved_ref="abc1234",
        )

    def test_deploys_to_agpack_hooks(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = self._make_hook_file(tmp_path, "format.sh", "#!/bin/bash\necho format")

        ops = resolve_hooks(fr, ["claude"])
        deployed = execute_write_ops(ops, project)

        assert ".agpack/hooks/format.sh" in deployed
        content = (project / ".agpack/hooks/format.sh").read_text()
        assert "echo format" in content

    def test_deploys_to_cursor_hooks_dir(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = self._make_hook_file(tmp_path, "format.sh", "#!/bin/bash\necho format")

        ops = resolve_hooks(fr, ["cursor"])
        deployed = execute_write_ops(ops, project)

        assert ".agpack/hooks/format.sh" in deployed
        assert ".cursor/hooks/format.sh" in deployed

    def test_multiple_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = self._make_hook_file(tmp_path, "lint.sh", "#!/bin/bash\necho lint")

        ops = resolve_hooks(fr, ["claude", "cursor"])
        deployed = execute_write_ops(ops, project)

        assert ".agpack/hooks/lint.sh" in deployed
        assert ".cursor/hooks/lint.sh" in deployed

    def test_targets_without_script_dir(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = self._make_hook_file(tmp_path, "format.sh", "#!/bin/bash\necho format")

        ops = resolve_hooks(fr, ["opencode", "codex"])
        deployed = execute_write_ops(ops, project)

        # Only the shared .agpack/hooks/ location
        assert ".agpack/hooks/format.sh" in deployed
        assert len(deployed) == 1

    def test_directory_of_hook_files(self, tmp_path: Path) -> None:
        src = tmp_path / "src" / "hooks"
        src.mkdir(parents=True)
        (src / "format.sh").write_text("#!/bin/bash\necho format")
        (src / "lint.sh").write_text("#!/bin/bash\necho lint")
        fr = FetchResult(
            source=DependencySource(
                urls=["https://github.com/org/repo"],
                path="hooks",
            ),
            local_path=src,
            resolved_ref="abc1234",
        )

        project = tmp_path / "project"
        project.mkdir()
        ops = resolve_hooks(fr, ["claude"])
        deployed = execute_write_ops(ops, project)

        assert ".agpack/hooks/format.sh" in deployed
        assert ".agpack/hooks/lint.sh" in deployed

    def test_dry_run_no_write(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        fr = self._make_hook_file(tmp_path, "format.sh", "#!/bin/bash\necho format")

        ops = resolve_hooks(fr, ["claude"])
        deployed = execute_write_ops(ops, project, dry_run=True)

        assert ".agpack/hooks/format.sh" in deployed
        assert not (project / ".agpack/hooks/format.sh").exists()


# ---------------------------------------------------------------------------
# resolve_hook_configs — config merging
# ---------------------------------------------------------------------------


class TestResolveHookConfigs:
    def test_claude_hook_config(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        hooks = [
            HookConfig(
                name="auto-format",
                event="PostToolUse",
                command=".agpack/hooks/format.sh",
                matcher="Write",
            )
        ]
        ops = resolve_hook_configs(hooks, ["claude"])
        deployed = execute_write_ops(ops, project)

        assert ".claude/settings.json" in deployed
        data = json.loads((project / ".claude/settings.json").read_text())
        assert "hooks" in data
        assert "PostToolUse" in data["hooks"]
        entries = data["hooks"]["PostToolUse"]
        assert len(entries) == 1
        assert entries[0]["matcher"] == "Write"
        assert entries[0]["hooks"][0]["type"] == "command"
        assert entries[0]["hooks"][0]["command"] == ".agpack/hooks/format.sh"

    def test_claude_no_matcher(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        hooks = [
            HookConfig(
                name="post-hook",
                event="PostToolUse",
                command=".agpack/hooks/post.sh",
            )
        ]
        ops = resolve_hook_configs(hooks, ["claude"])
        execute_write_ops(ops, project)

        data = json.loads((project / ".claude/settings.json").read_text())
        entries = data["hooks"]["PostToolUse"]
        assert "matcher" not in entries[0]

    def test_cursor_hook_config(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        hooks = [
            HookConfig(
                name="auto-format",
                event="PostToolUse",
                command=".cursor/hooks/format.sh",
            )
        ]
        ops = resolve_hook_configs(hooks, ["cursor"])
        deployed = execute_write_ops(ops, project)

        assert ".cursor/hooks.json" in deployed
        data = json.loads((project / ".cursor/hooks.json").read_text())
        assert "version" in data
        assert data["version"] == 1
        assert "hooks" in data
        assert "afterFileEdit" in data["hooks"]
        entries = data["hooks"]["afterFileEdit"]
        assert len(entries) == 1
        assert entries[0]["command"] == ".cursor/hooks/format.sh"

    def test_event_translation_cursor(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        hooks = [
            HookConfig(name="pre", event="PreToolUse", command="pre.sh"),
            HookConfig(name="post", event="PostToolUse", command="post.sh"),
        ]
        ops = resolve_hook_configs(hooks, ["cursor"])
        execute_write_ops(ops, project)

        data = json.loads((project / ".cursor/hooks.json").read_text())
        assert "beforeFileEdit" in data["hooks"]
        assert "afterFileEdit" in data["hooks"]

    def test_multiple_hooks_same_event(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        hooks = [
            HookConfig(name="format", event="PostToolUse", command="format.sh", matcher="Write"),
            HookConfig(name="lint", event="PostToolUse", command="lint.sh", matcher="Write"),
        ]
        ops = resolve_hook_configs(hooks, ["claude"])
        execute_write_ops(ops, project)

        data = json.loads((project / ".claude/settings.json").read_text())
        entries = data["hooks"]["PostToolUse"]
        assert len(entries) == 2

    def test_both_targets(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        hooks = [
            HookConfig(name="format", event="PostToolUse", command="format.sh"),
        ]
        ops = resolve_hook_configs(hooks, ["claude", "cursor"])
        deployed = execute_write_ops(ops, project)

        assert ".claude/settings.json" in deployed
        assert ".cursor/hooks.json" in deployed

    def test_skips_unsupported_targets(self) -> None:
        hooks = [
            HookConfig(name="format", event="PostToolUse", command="format.sh"),
        ]
        ops = resolve_hook_configs(hooks, ["opencode", "codex", "gemini"])
        assert ops == []

    def test_empty_hook_configs(self) -> None:
        ops = resolve_hook_configs([], ["claude"])
        assert ops == []

    def test_merges_into_existing_settings(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        (project / ".claude").mkdir()
        (project / ".claude/settings.json").write_text(
            json.dumps({"permissions": {"allow": ["Read"]}}, indent=2) + "\n"
        )

        hooks = [
            HookConfig(name="format", event="PostToolUse", command="format.sh"),
        ]
        ops = resolve_hook_configs(hooks, ["claude"])
        execute_write_ops(ops, project)

        data = json.loads((project / ".claude/settings.json").read_text())
        assert "permissions" in data
        assert "hooks" in data

    def test_dry_run_no_write(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        hooks = [
            HookConfig(name="format", event="PostToolUse", command="format.sh"),
        ]
        ops = resolve_hook_configs(hooks, ["claude"])
        deployed = execute_write_ops(ops, project, dry_run=True)

        assert ".claude/settings.json" in deployed
        assert not (project / ".claude/settings.json").exists()


# ---------------------------------------------------------------------------
# cleanup_hook_configs
# ---------------------------------------------------------------------------


class TestCleanupHookConfigs:
    def test_removes_hooks_key_from_claude(self, tmp_path: Path) -> None:
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {
                    "permissions": {"allow": ["Read"]},
                    "hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "format.sh"}]}]},
                },
                indent=2,
            )
            + "\n"
        )
        cleanup_hook_configs(["claude"], tmp_path)
        data = json.loads(settings.read_text())
        assert "hooks" not in data
        assert "permissions" in data

    def test_removes_hooks_from_cursor(self, tmp_path: Path) -> None:
        hooks_json = tmp_path / ".cursor" / "hooks.json"
        hooks_json.parent.mkdir(parents=True)
        hooks_json.write_text(
            json.dumps(
                {"version": 1, "hooks": {"afterFileEdit": [{"command": "format.sh"}]}},
                indent=2,
            )
            + "\n"
        )
        cleanup_hook_configs(["cursor"], tmp_path)
        # Cursor file with only version+hooks should be deleted entirely
        assert not hooks_json.exists()

    def test_no_op_if_file_missing(self, tmp_path: Path) -> None:
        cleanup_hook_configs(["claude"], tmp_path)

    def test_dry_run_no_change(self, tmp_path: Path) -> None:
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        original = (
            json.dumps(
                {"hooks": {"PostToolUse": []}},
                indent=2,
            )
            + "\n"
        )
        settings.write_text(original)
        cleanup_hook_configs(["claude"], tmp_path, dry_run=True)
        assert settings.read_text() == original

    def test_skips_unsupported_targets(self, tmp_path: Path) -> None:
        cleanup_hook_configs(["opencode", "codex"], tmp_path)


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigHooks:
    def test_hooks_parsed_from_config(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "agpack.yml"
        cfg_path.write_text("""\
targets:
  - claude
  - cursor
dependencies:
  hooks:
    - url: https://github.com/org/hooks-repo
      path: hooks/format.sh
    - url: https://github.com/org/hooks-repo
      path: hooks/lint.sh
      ref: v1.0
""")
        cfg = load_resolved_config(cfg_path, no_global=True)
        assert len(cfg.hooks) == 2
        assert cfg.hooks[0].path == "hooks/format.sh"
        assert cfg.hooks[1].ref == "v1.0"

    def test_hook_configs_parsed_from_config(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "agpack.yml"
        cfg_path.write_text("""\
targets:
  - claude
dependencies:
  hook_configs:
    - name: auto-format
      event: PostToolUse
      matcher: Write
      command: .agpack/hooks/format.sh
    - name: pre-check
      event: PreToolUse
      command: .agpack/hooks/check.sh
""")
        cfg = load_resolved_config(cfg_path, no_global=True)
        assert len(cfg.hook_configs) == 2
        assert cfg.hook_configs[0].name == "auto-format"
        assert cfg.hook_configs[0].event == "PostToolUse"
        assert cfg.hook_configs[0].matcher == "Write"
        assert cfg.hook_configs[0].command == ".agpack/hooks/format.sh"
        assert cfg.hook_configs[1].name == "pre-check"
        assert cfg.hook_configs[1].matcher is None

    def test_empty_hooks(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "agpack.yml"
        cfg_path.write_text("targets:\n  - claude\n")
        cfg = load_resolved_config(cfg_path, no_global=True)
        assert cfg.hooks == []
        assert cfg.hook_configs == []

    def test_hook_config_missing_name(self, tmp_path: Path) -> None:
        from agpack.config import ConfigError

        cfg_path = tmp_path / "agpack.yml"
        cfg_path.write_text("""\
targets:
  - claude
dependencies:
  hook_configs:
    - event: PostToolUse
      command: format.sh
""")
        with pytest.raises(ConfigError, match="missing required field 'name'"):
            load_resolved_config(cfg_path, no_global=True)

    def test_hook_config_missing_event(self, tmp_path: Path) -> None:
        from agpack.config import ConfigError

        cfg_path = tmp_path / "agpack.yml"
        cfg_path.write_text("""\
targets:
  - claude
dependencies:
  hook_configs:
    - name: my-hook
      command: format.sh
""")
        with pytest.raises(ConfigError, match="missing required field 'event'"):
            load_resolved_config(cfg_path, no_global=True)

    def test_hook_config_missing_command(self, tmp_path: Path) -> None:
        from agpack.config import ConfigError

        cfg_path = tmp_path / "agpack.yml"
        cfg_path.write_text("""\
targets:
  - claude
dependencies:
  hook_configs:
    - name: my-hook
      event: PostToolUse
""")
        with pytest.raises(ConfigError, match="missing required field 'command'"):
            load_resolved_config(cfg_path, no_global=True)

    def test_hook_config_not_a_dict(self, tmp_path: Path) -> None:
        from agpack.config import ConfigError

        cfg_path = tmp_path / "agpack.yml"
        cfg_path.write_text("""\
targets:
  - claude
dependencies:
  hook_configs:
    - just-a-string
""")
        with pytest.raises(ConfigError, match="expected an object"):
            load_resolved_config(cfg_path, no_global=True)

    def test_hooks_merged_from_global(self, tmp_path: Path) -> None:
        import os

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "agpack.yml").write_text("""\
targets:
  - claude
dependencies:
  hooks:
    - url: https://github.com/a/b
      path: hooks/proj.sh
""")
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        (global_dir / "agpack.yml").write_text("""\
dependencies:
  hooks:
    - url: https://github.com/c/d
      path: hooks/global.sh
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
        assert len(merged.hooks) == 2

    def test_hook_configs_merged_from_global(self, tmp_path: Path) -> None:
        import os

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "agpack.yml").write_text("""\
targets:
  - claude
dependencies:
  hook_configs:
    - name: proj-hook
      event: PostToolUse
      command: proj.sh
""")
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        (global_dir / "agpack.yml").write_text("""\
dependencies:
  hook_configs:
    - name: global-hook
      event: PostToolUse
      command: global.sh
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
        assert len(merged.hook_configs) == 2

    def test_hook_configs_deduped_on_merge(self, tmp_path: Path) -> None:
        import os

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "agpack.yml").write_text("""\
targets:
  - claude
dependencies:
  hook_configs:
    - name: shared-hook
      event: PostToolUse
      command: shared.sh
""")
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        (global_dir / "agpack.yml").write_text("""\
dependencies:
  hook_configs:
    - name: shared-hook
      event: PostToolUse
      command: shared.sh
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
        assert len(merged.hook_configs) == 1

    def test_hook_config_env_var_resolution(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / ".env").write_text("HOOK_PATH=.agpack/hooks/format.sh\n")
        (project_dir / "agpack.yml").write_text("""\
targets:
  - claude
dependencies:
  hook_configs:
    - name: format
      event: PostToolUse
      command: ${HOOK_PATH}
""")
        cfg = load_resolved_config(project_dir / "agpack.yml", no_global=True)
        assert cfg.hook_configs[0].command == ".agpack/hooks/format.sh"
