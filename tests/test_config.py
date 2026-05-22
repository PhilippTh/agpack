"""Tests for agpack.config – YAML loading, validation, and data classes."""

from __future__ import annotations

from pathlib import Path

import pytest

from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import DependencyEntry
from agpack.config import DependencySource
from agpack.config import GlobalConfig
from agpack.config import load_config
from agpack.config import load_global_config
from agpack.config import merge_configs
from agpack.patch import Patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "agpack.yml"
    p.write_text(text, encoding="utf-8")
    return p


def _write_global_config(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "agpack.yml"
    p.write_text(text, encoding="utf-8")
    return p


def _make_project_config(
    *,
    skills: list[DependencyEntry] | None = None,
    commands: list[DependencyEntry] | None = None,
    agents: list[DependencyEntry] | None = None,
    mcp: list[DependencyEntry] | None = None,
    **kwargs: object,
) -> AgpackConfig:
    deps: dict[str, list[DependencyEntry]] = {}
    for name, lst in (
        ("skills", skills),
        ("commands", commands),
        ("agents", agents),
        ("mcp", mcp),
    ):
        if lst:
            deps[name] = lst
    defaults: dict[str, object] = {"targets": ["claude"], "dependencies": deps}
    defaults.update(kwargs)
    return AgpackConfig(**defaults)  # type: ignore[arg-type]


def _make_global_config(
    *,
    skills: list[DependencyEntry] | None = None,
    commands: list[DependencyEntry] | None = None,
    agents: list[DependencyEntry] | None = None,
    mcp: list[DependencyEntry] | None = None,
    **kwargs: object,
) -> GlobalConfig:
    deps: dict[str, list[DependencyEntry]] = {}
    for name, lst in (
        ("skills", skills),
        ("commands", commands),
        ("agents", agents),
        ("mcp", mcp),
    ):
        if lst:
            deps[name] = lst
    return GlobalConfig(dependencies=deps, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Valid config: fetch + patch dependencies
# ---------------------------------------------------------------------------


def test_load_valid_full_config(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets:
  - claude
  - opencode
dependencies:
  skills:
    - url: https://gitlab.com/owner/skill-repo
      path: skills/foo
      ref: v2.0
  commands:
    - url: https://github.com/owner/cmd-repo
  agents:
    - url: https://github.com/owner/agent-repo
  mcp:
    - key: mcpServers.my-server
      value:
        command: node
        args: ["server.js"]
        env:
          TOKEN: abc
""",
    )
    cfg = load_config(cfg_path)

    assert isinstance(cfg, AgpackConfig)
    assert cfg.targets == ["claude", "opencode"]
    skills = cfg.dependencies["skills"]
    assert len(skills) == 1
    assert isinstance(skills[0], DependencySource)
    assert skills[0].url == "https://gitlab.com/owner/skill-repo"
    assert skills[0].ref == "v2.0"

    mcp = cfg.dependencies["mcp"]
    assert len(mcp) == 1
    assert isinstance(mcp[0], Patch)
    assert mcp[0].key == "mcpServers.my-server"
    assert mcp[0].strategy == "replace"
    assert mcp[0].value["command"] == "node"


def test_patch_with_append_strategy(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets:
  - claude
dependencies:
  settings:
    - key: hooks.PreToolUse
      strategy: append
      value:
        matcher: "Write|Edit"
        hooks: [{type: command, command: "x"}]
""",
    )
    cfg = load_config(cfg_path)
    entry = cfg.dependencies["settings"][0]
    assert isinstance(entry, Patch)
    assert entry.strategy == "append"
    assert entry.value["matcher"] == "Write|Edit"


# ---------------------------------------------------------------------------
# 2. Missing / invalid targets
# ---------------------------------------------------------------------------


def test_missing_targets(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, "name: pack\nversion: '1'\n")
    with pytest.raises(ConfigError, match="Missing or invalid 'targets'"):
        load_config(cfg_path)


def test_targets_not_a_list(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, "targets: claude\n")
    with pytest.raises(ConfigError, match="Missing or invalid 'targets'"):
        load_config(cfg_path)


def test_empty_targets(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, "targets: []\n")
    with pytest.raises(ConfigError, match="Missing or invalid 'targets'"):
        load_config(cfg_path)


def test_unknown_target_name_accepted_at_parse_time(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, "targets:\n  - not-a-builtin\n")
    config = load_config(cfg_path)
    assert config.targets == ["not-a-builtin"]


def test_target_must_be_non_empty_string(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, "targets:\n  - ''\n")
    with pytest.raises(ConfigError, match="non-empty strings"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 3. Fetch entry validation
# ---------------------------------------------------------------------------


def test_fetch_entry_missing_url(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets: [claude]
dependencies:
  skills:
    - path: some/path
""",
    )
    with pytest.raises(ConfigError, match="either 'url' .* or 'key'"):
        load_config(cfg_path)


def test_fetch_entry_url_must_be_string_or_list(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets: [claude]
dependencies:
  skills:
    - url: 42
""",
    )
    with pytest.raises(ConfigError, match="'url' must be a string or list"):
        load_config(cfg_path)


def test_fetch_entry_url_empty(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets: [claude]
dependencies:
  skills:
    - url: ""
""",
    )
    with pytest.raises(ConfigError, match="'url' must not be empty"):
        load_config(cfg_path)


def test_fetch_entry_url_as_list(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets: [claude]
dependencies:
  skills:
    - url:
        - https://github.com/owner/repo
        - git@github.com:owner/repo.git
      path: skills/foo
""",
    )
    cfg = load_config(cfg_path)
    skill = cfg.dependencies["skills"][0]
    assert isinstance(skill, DependencySource)
    assert skill.urls == [
        "https://github.com/owner/repo",
        "git@github.com:owner/repo.git",
    ]


def test_fetch_entry_with_all_optional_fields(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets: [claude]
dependencies:
  commands:
    - url: https://gitlab.com/org/repo
      path: sub/dir
      ref: main
""",
    )
    cfg = load_config(cfg_path)
    dep = cfg.dependencies["commands"][0]
    assert isinstance(dep, DependencySource)
    assert dep.path == "sub/dir"
    assert dep.ref == "main"


# ---------------------------------------------------------------------------
# 4. Patch entry validation
# ---------------------------------------------------------------------------


def test_patch_missing_value(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets: [claude]
dependencies:
  mcp:
    - key: mcpServers.fs
""",
    )
    with pytest.raises(ConfigError, match="missing required field 'value'"):
        load_config(cfg_path)


def test_patch_invalid_strategy(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets: [claude]
dependencies:
  mcp:
    - key: mcpServers.fs
      value: {command: x}
      strategy: merge
""",
    )
    with pytest.raises(ConfigError, match="'strategy' must be one of"):
        load_config(cfg_path)


def test_patch_unknown_field(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets: [claude]
dependencies:
  mcp:
    - key: mcpServers.fs
      value: {}
      bogus: 1
""",
    )
    with pytest.raises(ConfigError, match="unknown fields"):
        load_config(cfg_path)


def test_append_patches_with_distinct_values_allowed(tmp_path: Path) -> None:
    """Two append patches at the same key with different values are distinct list elements — both must survive."""
    cfg_path = _write_config(
        tmp_path,
        """\
targets: [claude]
dependencies:
  permissions:
    - key: permissions.allow
      strategy: append
      value: "Read(/etc/**)"
    - key: permissions.allow
      strategy: append
      value: "Read(/var/**)"
""",
    )
    cfg = load_config(cfg_path)
    assert len(cfg.dependencies["permissions"]) == 2


def test_mixed_fetch_and_patch_rejected(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets: [claude]
dependencies:
  mixed:
    - url: https://github.com/owner/repo
    - key: foo
      value: 1
""",
    )
    with pytest.raises(ConfigError, match="cannot mix fetch and patch"):
        load_config(cfg_path)


def test_entry_with_both_url_and_key_rejected(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets: [claude]
dependencies:
  mcp:
    - url: https://github.com/owner/repo
      key: mcpServers.x
      value: {}
""",
    )
    with pytest.raises(ConfigError, match="mutually exclusive"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 5. DependencySource properties
# ---------------------------------------------------------------------------


def test_dependency_source_name_from_path() -> None:
    dep = DependencySource(urls=["https://github.com/org/repo"], path="skills/my-skill")
    assert dep.name == "my-skill"


def test_dependency_source_name_from_url_with_dotgit() -> None:
    dep = DependencySource(urls=["https://github.com/org/repo.git"])
    assert dep.name == "repo"


def test_dependency_source_identity_with_path() -> None:
    dep = DependencySource(urls=["https://github.com/org/repo"], path="sub/dir")
    assert dep.identity == "https://github.com/org/repo::sub/dir"


# ---------------------------------------------------------------------------
# 6. Empty / minimal configs
# ---------------------------------------------------------------------------


def test_empty_dependencies(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, "targets: [claude]\ndependencies: {}\n")
    cfg = load_config(cfg_path)
    assert cfg.dependencies == {}


def test_no_dependencies_key(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, "targets: [claude]\n")
    cfg = load_config(cfg_path)
    assert cfg.dependencies == {}


def test_dependencies_preserve_yaml_order(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets: [claude]
dependencies:
  agents:
    - url: https://github.com/a/agents
  skills:
    - url: https://github.com/a/skills
  commands:
    - url: https://github.com/a/commands
""",
    )
    cfg = load_config(cfg_path)
    assert list(cfg.dependencies) == ["agents", "skills", "commands"]


# ---------------------------------------------------------------------------
# 7. use_global field
# ---------------------------------------------------------------------------


def test_use_global_defaults_to_true(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, "targets: [claude]\n")
    assert load_config(cfg_path).use_global is True


def test_use_global_false(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, "global: false\ntargets: [claude]\n")
    assert load_config(cfg_path).use_global is False


def test_use_global_non_bool_raises(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, "global: 'yes'\ntargets: [claude]\n")
    with pytest.raises(ConfigError, match="'global' must be true or false"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 8. Global config
# ---------------------------------------------------------------------------


def test_load_global_config_full(tmp_path: Path) -> None:
    path = _write_global_config(
        tmp_path,
        """\
dependencies:
  skills:
    - url: https://github.com/org/skills
      path: skills/shared
  mcp:
    - key: mcpServers.global-srv
      value:
        command: npx
        args: ["-y", "@example/server"]
""",
    )
    cfg = load_global_config(path)
    assert cfg is not None
    assert len(cfg.dependencies["skills"]) == 1
    mcp = cfg.dependencies["mcp"]
    assert isinstance(mcp[0], Patch)
    assert mcp[0].key == "mcpServers.global-srv"
    assert cfg.config_dir == tmp_path


def test_load_global_config_missing_file(tmp_path: Path) -> None:
    assert load_global_config(tmp_path / "nonexistent.yml") is None


def test_load_global_config_empty_file(tmp_path: Path) -> None:
    cfg = load_global_config(_write_global_config(tmp_path, ""))
    assert cfg is not None
    assert cfg.dependencies == {}


def test_load_global_config_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    custom_path = custom_dir / "my-global.yml"
    custom_path.write_text("dependencies:\n  skills:\n    - url: https://example.com/repo\n")
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(custom_path))
    cfg = load_global_config()
    assert cfg is not None
    assert cfg.config_dir == custom_dir


def test_load_global_config_malformed_yaml(tmp_path: Path) -> None:
    path = _write_global_config(tmp_path, ":\n  - [invalid yaml")
    with pytest.raises(ConfigError, match="Failed to parse global config YAML"):
        load_global_config(path)


def test_load_global_config_not_a_mapping(tmp_path: Path) -> None:
    path = _write_global_config(tmp_path, "- a list\n- not a mapping\n")
    with pytest.raises(ConfigError, match="Global config file must be a YAML mapping"):
        load_global_config(path)


def test_load_global_config_dependencies_not_a_mapping(tmp_path: Path) -> None:
    path = _write_global_config(tmp_path, "dependencies: [bad]\n")
    with pytest.raises(ConfigError, match="Global config 'dependencies' must be a mapping"):
        load_global_config(path)


# ---------------------------------------------------------------------------
# 9. merge_configs
# ---------------------------------------------------------------------------


def test_merge_basic() -> None:
    project = _make_project_config(
        skills=[DependencySource(urls=["https://github.com/a/b"], path="skills/proj")],
    )
    global_skill = DependencySource(urls=["https://github.com/c/d"], path="skills/global")
    global_cfg = _make_global_config(
        skills=[global_skill],
        commands=[DependencySource(urls=["https://github.com/e/f"])],
    )
    merged = merge_configs(project, global_cfg)
    assert len(merged.dependencies["skills"]) == 2
    assert merged.dependencies["skills"][0].url == "https://github.com/a/b"  # type: ignore[union-attr]


def test_merge_dedupes_fetch_by_identity() -> None:
    dep = DependencySource(urls=["https://github.com/a/b"], path="skills/shared")
    project = _make_project_config(skills=[dep])
    dup = DependencySource(urls=["https://github.com/a/b"], path="skills/shared")
    global_cfg = _make_global_config(skills=[dup])
    merged = merge_configs(project, global_cfg)
    assert len(merged.dependencies["skills"]) == 1


def test_merge_dedupes_patches_by_content() -> None:
    p = Patch(key="mcpServers.fs", value={"command": "npx"})
    project = _make_project_config(mcp=[p])
    dup = Patch(key="mcpServers.fs", value={"command": "npx"})
    global_cfg = _make_global_config(mcp=[dup])
    merged = merge_configs(project, global_cfg)
    assert len(merged.dependencies["mcp"]) == 1


def test_merge_keeps_distinct_patches() -> None:
    project = _make_project_config(mcp=[Patch(key="mcpServers.fs", value={"command": "npx"})])
    global_cfg = _make_global_config(mcp=[Patch(key="mcpServers.other", value={"command": "x"})])
    merged = merge_configs(project, global_cfg)
    assert len(merged.dependencies["mcp"]) == 2


def test_merge_does_not_mutate_inputs() -> None:
    project = _make_project_config(
        skills=[DependencySource(urls=["https://github.com/a/b"])],
    )
    global_cfg = _make_global_config(
        skills=[DependencySource(urls=["https://github.com/c/d"])],
    )
    orig_project = list(project.dependencies["skills"])
    orig_global = list(global_cfg.dependencies["skills"])
    merge_configs(project, global_cfg)
    assert project.dependencies["skills"] == orig_project
    assert global_cfg.dependencies["skills"] == orig_global


# ---------------------------------------------------------------------------
# 10. load_config error paths
# ---------------------------------------------------------------------------


def test_config_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path / "nonexistent.yml")


def test_config_malformed_yaml(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, ":\n  - [invalid yaml")
    with pytest.raises(ConfigError, match="Failed to parse YAML"):
        load_config(cfg_path)


def test_config_not_a_mapping(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, "- a list\n- not a mapping\n")
    with pytest.raises(ConfigError, match="Config file must be a YAML mapping"):
        load_config(cfg_path)


def test_config_dependencies_not_a_mapping(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, 'targets: [claude]\ndependencies: "oops"\n')
    with pytest.raises(ConfigError, match="'dependencies' must be a mapping"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 11. target_definitions parsing
# ---------------------------------------------------------------------------


def test_target_definitions_parses_new_target(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets:
  - my-tool

target_definitions:
  my-tool:
    skills:
      kind: copy-directory
      path: .my-tool/skills
    mcp:
      kind: edit-file
      path: .my-tool/config.json
""",
    )
    config = load_config(cfg_path)
    td = config.target_definitions["my-tool"]
    assert td.resources["skills"].path == ".my-tool/skills"
    assert td.resources["mcp"].path == ".my-tool/config.json"


def test_target_definitions_overrides_builtin(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets:
  - claude

target_definitions:
  claude:
    skills:
      kind: copy-directory
      path: .my-claude/skills
""",
    )
    config = load_config(cfg_path)
    td = config.target_definitions["claude"]
    assert td.resources["skills"].path == ".my-claude/skills"
    # Replace semantics: nothing else inherited.
    assert "mcp" not in td.resources
    assert "commands" not in td.resources


def test_target_definitions_not_a_mapping(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        'targets: [claude]\ntarget_definitions: "oops"\n',
    )
    with pytest.raises(ConfigError, match="target_definitions: must be a mapping"):
        load_config(cfg_path)


def test_target_definitions_invalid_inner_schema_raises(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
targets:
  - my-tool
target_definitions:
  my-tool:
    skills:
      kind: not-a-real-kind
      path: .x/skills
""",
    )
    with pytest.raises(ConfigError, match="kind"):
        load_config(cfg_path)


def test_merge_carries_global_target_definitions(tmp_path: Path) -> None:
    project_path = _write_config(tmp_path, "targets:\n  - shared-tool\n")
    project = load_config(project_path)
    global_path = tmp_path / "global.yml"
    global_path.write_text(
        """\
target_definitions:
  shared-tool:
    skills:
      kind: copy-directory
      path: .shared/skills
""",
        encoding="utf-8",
    )
    global_cfg = load_global_config(global_path)
    assert global_cfg is not None
    merged = merge_configs(project, global_cfg)
    assert "shared-tool" in merged.target_definitions
