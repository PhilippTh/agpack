"""Tests for agpack.config – YAML loading, validation, merging, and resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import DependencySource
from agpack.config import McpServer
from agpack.config import load_resolved_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, text: str) -> Path:
    """Write *text* to ``agpack.yml`` inside *tmp_path* and return the path."""
    p = tmp_path / "agpack.yml"
    p.write_text(text, encoding="utf-8")
    return p


def _load(tmp_path: Path, text: str) -> AgpackConfig:
    """Shortcut: write config and load with global config disabled."""
    return load_resolved_config(_write_config(tmp_path, text), no_global=True)


# ---------------------------------------------------------------------------
# 1. Valid config with all fields
# ---------------------------------------------------------------------------


def test_load_valid_full_config(tmp_path: Path) -> None:
    cfg = _load(
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
    - name: my-server
      type: stdio
      command: node
      args: ["server.js"]
      env:
        TOKEN: abc
""",
    )

    assert isinstance(cfg, AgpackConfig)
    assert cfg.targets == ["claude", "opencode"]

    assert len(cfg.skills) == 1
    assert cfg.skills[0].url == "https://gitlab.com/owner/skill-repo"
    assert cfg.skills[0].path == "skills/foo"
    assert cfg.skills[0].ref == "v2.0"

    assert len(cfg.commands) == 1
    assert cfg.commands[0].url == "https://github.com/owner/cmd-repo"

    assert len(cfg.agents) == 1
    assert cfg.agents[0].url == "https://github.com/owner/agent-repo"

    assert len(cfg.mcp) == 1
    mcp = cfg.mcp[0]
    assert mcp.name == "my-server"
    assert mcp.type == "stdio"
    assert mcp.command == "node"
    assert mcp.args == ["server.js"]
    assert mcp.env == {"TOKEN": "abc"}


# ---------------------------------------------------------------------------
# 2. Missing / invalid targets
# ---------------------------------------------------------------------------


def test_missing_targets(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Missing or invalid 'targets'"):
        _load(tmp_path, "name: pack\nversion: '1'\n")


def test_targets_not_a_list(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Missing or invalid 'targets'"):
        _load(tmp_path, "targets: claude\n")


def test_empty_targets(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Missing or invalid 'targets'"):
        _load(tmp_path, "targets: []\n")


# ---------------------------------------------------------------------------
# 5. Unrecognised target name
# ---------------------------------------------------------------------------


def test_unrecognised_target(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Unrecognised target 'not-a-target'"):
        _load(tmp_path, "targets:\n  - not-a-target\n")


# ---------------------------------------------------------------------------
# 6. Dependency with missing url
# ---------------------------------------------------------------------------


def test_dependency_missing_url(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing required field 'url'"):
        _load(
            tmp_path,
            "targets:\n  - claude\ndependencies:\n  skills:\n    - path: some/path\n",
        )


# ---------------------------------------------------------------------------
# 7. Dependency with non-string url
# ---------------------------------------------------------------------------


def test_dependency_url_must_be_string(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'url' must be a string"):
        _load(
            tmp_path,
            "targets:\n  - claude\ndependencies:\n  skills:\n    - url: 123\n",
        )


# ---------------------------------------------------------------------------
# 8. Dependency with all optional fields (path, ref)
# ---------------------------------------------------------------------------


def test_dependency_all_optional_fields(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        """\
targets:
  - claude
dependencies:
  commands:
    - url: https://gitlab.com/org/repo
      path: sub/dir
      ref: main
""",
    )
    dep = cfg.commands[0]
    assert dep.url == "https://gitlab.com/org/repo"
    assert dep.path == "sub/dir"
    assert dep.ref == "main"


# ---------------------------------------------------------------------------
# 9. MCP stdio server – valid
# ---------------------------------------------------------------------------


def test_mcp_stdio_valid(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        """\
targets:
  - claude
dependencies:
  mcp:
    - name: stdio-srv
      type: stdio
      command: python
      args: ["-m", "server"]
      env:
        PORT: "8080"
""",
    )
    srv = cfg.mcp[0]
    assert srv.name == "stdio-srv"
    assert srv.type == "stdio"
    assert srv.command == "python"
    assert srv.args == ["-m", "server"]
    assert srv.env == {"PORT": "8080"}
    assert srv.url is None


# ---------------------------------------------------------------------------
# 10. MCP stdio server – missing command
# ---------------------------------------------------------------------------


def test_mcp_stdio_missing_command(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing required field 'command'"):
        _load(
            tmp_path,
            """\
targets:
  - claude
dependencies:
  mcp:
    - name: bad-stdio
      type: stdio
""",
        )


# ---------------------------------------------------------------------------
# 11. MCP sse server – valid
# ---------------------------------------------------------------------------


def test_mcp_sse_valid(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        """\
targets:
  - claude
dependencies:
  mcp:
    - name: sse-srv
      type: sse
      url: http://localhost:3000/sse
""",
    )
    srv = cfg.mcp[0]
    assert srv.name == "sse-srv"
    assert srv.type == "sse"
    assert srv.url == "http://localhost:3000/sse"
    assert srv.command is None


# ---------------------------------------------------------------------------
# 12. MCP sse server – missing url
# ---------------------------------------------------------------------------


def test_mcp_sse_missing_url(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing required field 'url'"):
        _load(
            tmp_path,
            "targets:\n  - claude\ndependencies:\n  mcp:\n    - name: bad-sse\n      type: sse\n",
        )


# ---------------------------------------------------------------------------
# 13. MCP http server – valid
# ---------------------------------------------------------------------------


def test_mcp_http_valid(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        """\
targets:
  - claude
dependencies:
  mcp:
    - name: http-srv
      type: http
      url: http://localhost:9000/api
""",
    )
    srv = cfg.mcp[0]
    assert srv.name == "http-srv"
    assert srv.type == "http"
    assert srv.url == "http://localhost:9000/api"


# ---------------------------------------------------------------------------
# 14. MCP entry missing name
# ---------------------------------------------------------------------------


def test_mcp_missing_name(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing required field 'name'"):
        _load(
            tmp_path,
            "targets:\n  - claude\ndependencies:\n  mcp:\n    - type: stdio\n      command: node\n",
        )


# ---------------------------------------------------------------------------
# 15. DependencySource.name property
# ---------------------------------------------------------------------------


def test_dependency_source_name_from_path() -> None:
    dep = DependencySource(urls=["https://github.com/org/repo"], path="skills/my-skill")
    assert dep.name == "my-skill"


def test_dependency_source_name_from_path_trailing_slash() -> None:
    dep = DependencySource(urls=["https://github.com/org/repo"], path="skills/my-skill/")
    assert dep.name == "my-skill"


def test_dependency_source_name_from_url() -> None:
    dep = DependencySource(urls=["https://github.com/org/repo-name"])
    assert dep.name == "repo-name"


def test_dependency_source_name_from_url_with_dotgit() -> None:
    dep = DependencySource(urls=["https://github.com/org/repo-name.git"])
    assert dep.name == "repo-name"


def test_dependency_source_name_from_url_trailing_slash() -> None:
    dep = DependencySource(urls=["https://github.com/org/repo-name/"])
    assert dep.name == "repo-name"


# ---------------------------------------------------------------------------
# 16. DependencySource.identity property
# ---------------------------------------------------------------------------


def test_dependency_source_identity_without_path() -> None:
    dep = DependencySource(urls=["https://github.com/org/repo"])
    assert dep.identity == "https://github.com/org/repo"


def test_dependency_source_identity_with_path() -> None:
    dep = DependencySource(urls=["https://github.com/org/repo"], path="sub/dir")
    assert dep.identity == "https://github.com/org/repo::sub/dir"


def test_dependency_source_identity_different_url() -> None:
    dep = DependencySource(urls=["https://gitlab.com/org/repo"])
    assert dep.identity == "https://gitlab.com/org/repo"


# ---------------------------------------------------------------------------
# 17. Empty dependencies section
# ---------------------------------------------------------------------------


def test_empty_dependencies(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "targets:\n  - claude\ndependencies: {}\n")
    assert cfg.skills == []
    assert cfg.commands == []
    assert cfg.agents == []
    assert cfg.ignores == []
    assert cfg.mcp == []


def test_no_dependencies_key(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "targets:\n  - claude\n")
    assert cfg.skills == []
    assert cfg.commands == []
    assert cfg.agents == []
    assert cfg.ignores == []
    assert cfg.mcp == []


# ---------------------------------------------------------------------------
# 18. Mixed dependency formats
# ---------------------------------------------------------------------------


def test_mixed_dependencies(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        """\
targets:
  - cursor
  - copilot
dependencies:
  skills:
    - url: https://github.com/a/b
    - url: https://github.com/c/d
      path: deep/nested
      ref: v1
  commands:
    - url: https://github.com/e/f
  agents:
    - url: https://gitlab.com/g/h
  mcp:
    - name: s1
      type: stdio
      command: node
    - name: s2
      type: sse
      url: http://example.com
""",
    )

    assert len(cfg.skills) == 2
    assert cfg.skills[0].url == "https://github.com/a/b"
    assert cfg.skills[0].path is None
    assert cfg.skills[1].url == "https://github.com/c/d"
    assert cfg.skills[1].path == "deep/nested"
    assert cfg.skills[1].ref == "v1"

    assert len(cfg.commands) == 1

    assert len(cfg.agents) == 1
    assert cfg.agents[0].url == "https://gitlab.com/g/h"

    assert len(cfg.mcp) == 2
    assert cfg.mcp[0].type == "stdio"
    assert cfg.mcp[1].type == "sse"


# ---------------------------------------------------------------------------
# 19. use_global field
# ---------------------------------------------------------------------------


def test_use_global_defaults_to_true(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "targets:\n  - claude\n")
    assert cfg.use_global is True


def test_use_global_false(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "global: false\ntargets:\n  - claude\n")
    assert cfg.use_global is False


def test_use_global_true_explicit(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "global: true\ntargets:\n  - claude\n")
    assert cfg.use_global is True


def test_use_global_non_bool_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'global' must be true or false"):
        _load(tmp_path, 'global: "yes"\ntargets:\n  - claude\n')


# ---------------------------------------------------------------------------
# 20. Global config loading (tested through load_resolved_config)
# ---------------------------------------------------------------------------


def _setup_global(
    tmp_path: Path,
    global_text: str,
    project_text: str = "targets:\n  - claude\n",
) -> AgpackConfig:
    """Write a project config and a global config, then load with merging."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "agpack.yml").write_text(project_text, encoding="utf-8")

    global_dir = tmp_path / "global"
    global_dir.mkdir()
    global_path = global_dir / "agpack.yml"
    global_path.write_text(global_text, encoding="utf-8")

    import os

    old = os.environ.get("AGPACK_GLOBAL_CONFIG")
    os.environ["AGPACK_GLOBAL_CONFIG"] = str(global_path)
    try:
        return load_resolved_config(project_dir / "agpack.yml")
    finally:
        if old is None:
            os.environ.pop("AGPACK_GLOBAL_CONFIG", None)
        else:
            os.environ["AGPACK_GLOBAL_CONFIG"] = old


def test_global_config_full(tmp_path: Path) -> None:
    cfg = _setup_global(
        tmp_path,
        """\
dependencies:
  skills:
    - url: https://github.com/org/skills
      path: skills/shared
  commands:
    - url: https://github.com/org/commands
  agents:
    - url: https://github.com/org/agents
      path: agents/shared.md
  mcp:
    - name: global-server
      command: npx
      args: ["-y", "@example/server"]
      env:
        KEY: value
""",
    )
    assert len(cfg.skills) == 1
    assert cfg.skills[0].url == "https://github.com/org/skills"
    assert cfg.skills[0].path == "skills/shared"
    assert len(cfg.commands) == 1
    assert len(cfg.agents) == 1
    assert len(cfg.mcp) == 1
    assert cfg.mcp[0].name == "global-server"


def test_global_config_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the global config file doesn't exist, loading succeeds with no extras."""
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(tmp_path / "nonexistent.yml"))
    cfg = _load(tmp_path, "targets:\n  - claude\n")
    # No error, no global deps merged
    assert cfg.skills == []


def test_global_config_empty_file(tmp_path: Path) -> None:
    cfg = _setup_global(tmp_path, "")
    assert cfg.skills == []
    assert cfg.commands == []
    assert cfg.agents == []
    assert cfg.mcp == []


def test_global_config_empty_dependencies(tmp_path: Path) -> None:
    cfg = _setup_global(tmp_path, "dependencies: {}\n")
    assert cfg.skills == []


def test_global_config_malformed_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Failed to parse global config YAML"):
        _setup_global(tmp_path, ":\n  - [invalid yaml")


def test_global_config_not_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Global config file must be a YAML mapping"):
        _setup_global(tmp_path, "- a list\n- not a mapping\n")


def test_global_config_dependencies_not_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Global config 'dependencies' must be a mapping"):
        _setup_global(tmp_path, "dependencies: [bad]\n")


def test_global_config_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    custom_path = custom_dir / "my-global.yml"
    custom_path.write_text("dependencies:\n  skills:\n    - url: https://example.com/repo\n")
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(custom_path))

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "agpack.yml").write_text("targets:\n  - claude\n")

    cfg = load_resolved_config(project_dir / "agpack.yml")
    assert len(cfg.skills) == 1


def test_global_config_env_var_nonexistent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(tmp_path / "nope.yml"))
    cfg = _load(tmp_path, "targets:\n  - claude\n")
    assert cfg.skills == []


def test_global_config_skills_only(tmp_path: Path) -> None:
    cfg = _setup_global(
        tmp_path,
        """\
dependencies:
  skills:
    - url: https://github.com/org/repo
      path: skills/only
""",
    )
    assert len(cfg.skills) == 1
    assert cfg.commands == []
    assert cfg.agents == []
    assert cfg.mcp == []


# ---------------------------------------------------------------------------
# 21. Merge behaviour (tested through load_resolved_config)
# ---------------------------------------------------------------------------


def test_merge_basic(tmp_path: Path) -> None:
    cfg = _setup_global(
        tmp_path,
        global_text="""\
dependencies:
  skills:
    - url: https://github.com/c/d
      path: skills/global
  commands:
    - url: https://github.com/e/f
""",
        project_text="""\
targets:
  - claude
dependencies:
  skills:
    - url: https://github.com/a/b
      path: skills/proj
""",
    )
    assert len(cfg.skills) == 2
    assert cfg.skills[0].url == "https://github.com/a/b"  # project first
    assert cfg.skills[1].url == "https://github.com/c/d"  # global appended
    assert len(cfg.commands) == 1
    assert cfg.commands[0].url == "https://github.com/e/f"


def test_merge_project_wins_on_duplicate_dep(tmp_path: Path) -> None:
    cfg = _setup_global(
        tmp_path,
        global_text="""\
dependencies:
  skills:
    - url: https://github.com/a/b
      path: skills/shared
""",
        project_text="""\
targets:
  - claude
dependencies:
  skills:
    - url: https://github.com/a/b
      path: skills/shared
""",
    )
    # Duplicate deduped — only the project entry survives
    assert len(cfg.skills) == 1


def test_merge_project_wins_on_duplicate_mcp(tmp_path: Path) -> None:
    cfg = _setup_global(
        tmp_path,
        global_text="""\
dependencies:
  mcp:
    - name: ctx7
      command: npx
      args: ["global-version"]
""",
        project_text="""\
targets:
  - claude
dependencies:
  mcp:
    - name: ctx7
      command: npx
      args: ["project-version"]
""",
    )
    assert len(cfg.mcp) == 1
    assert cfg.mcp[0].args == ["project-version"]


def test_merge_empty_global(tmp_path: Path) -> None:
    cfg = _setup_global(
        tmp_path,
        global_text="",
        project_text="""\
targets:
  - claude
dependencies:
  skills:
    - url: https://github.com/a/b
""",
    )
    assert len(cfg.skills) == 1
    assert cfg.commands == []


def test_merge_empty_project_deps(tmp_path: Path) -> None:
    cfg = _setup_global(
        tmp_path,
        global_text="""\
dependencies:
  skills:
    - url: https://github.com/c/d
  mcp:
    - name: s1
      command: cmd
""",
        project_text="targets:\n  - claude\n",
    )
    assert len(cfg.skills) == 1
    assert cfg.skills[0].url == "https://github.com/c/d"
    assert len(cfg.mcp) == 1


def test_merge_preserves_project_metadata(tmp_path: Path) -> None:
    cfg = _setup_global(
        tmp_path,
        global_text="dependencies:\n  skills:\n    - url: https://github.com/a/b\n",
        project_text="targets:\n  - opencode\nglobal: true\n",
    )
    assert cfg.targets == ["opencode"]


def test_merge_cross_type_identity_not_deduped(tmp_path: Path) -> None:
    """A skill and a command with the same identity are NOT deduped."""
    cfg = _setup_global(
        tmp_path,
        global_text="""\
dependencies:
  commands:
    - url: https://github.com/a/b
      path: shared
""",
        project_text="""\
targets:
  - claude
dependencies:
  skills:
    - url: https://github.com/a/b
      path: shared
""",
    )
    assert len(cfg.skills) == 1
    assert len(cfg.commands) == 1


# ---------------------------------------------------------------------------
# 22. url as list (multiple URLs / fallbacks)
# ---------------------------------------------------------------------------


def test_url_as_list(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        """\
targets:
  - claude
dependencies:
  skills:
    - url:
        - https://github.com/owner/repo
        - git@github.com:owner/repo.git
      path: skills/foo
""",
    )
    assert cfg.skills[0].urls == [
        "https://github.com/owner/repo",
        "git@github.com:owner/repo.git",
    ]
    assert cfg.skills[0].url == "https://github.com/owner/repo"


def test_url_as_string(tmp_path: Path) -> None:
    cfg = _load(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  skills:\n    - url: https://github.com/owner/repo\n",
    )
    assert cfg.skills[0].urls == ["https://github.com/owner/repo"]


def test_url_empty_list_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'url' must not be empty"):
        _load(
            tmp_path,
            "targets:\n  - claude\ndependencies:\n  skills:\n    - url: []\n",
        )


def test_url_invalid_type_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'url' must be a string or list"):
        _load(
            tmp_path,
            "targets:\n  - claude\ndependencies:\n  skills:\n    - url: 42\n",
        )


def test_url_as_list_in_global_config(tmp_path: Path) -> None:
    cfg = _setup_global(
        tmp_path,
        """\
dependencies:
  skills:
    - url:
        - https://github.com/org/repo
        - git@github.com:org/repo.git
      path: skills/shared
""",
    )
    assert cfg.skills[0].urls == [
        "https://github.com/org/repo",
        "git@github.com:org/repo.git",
    ]


# ---------------------------------------------------------------------------
# 23. _parse_dependency validation errors
# ---------------------------------------------------------------------------


def test_dependency_entry_not_a_dict(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="expected an object"):
        _load(
            tmp_path,
            "targets:\n  - claude\ndependencies:\n  skills:\n    - just-a-string\n",
        )


def test_dependency_url_empty_string(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'url' must not be empty"):
        _load(
            tmp_path,
            'targets:\n  - claude\ndependencies:\n  skills:\n    - url: ""\n',
        )


def test_dependency_path_not_a_string(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'path' must be a string"):
        _load(
            tmp_path,
            "targets:\n  - claude\ndependencies:\n  skills:\n    - url: https://github.com/org/repo\n      path: 123\n",
        )


def test_mcp_entry_not_a_dict(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="expected an object"):
        _load(
            tmp_path,
            "targets:\n  - claude\ndependencies:\n  mcp:\n    - just-a-string\n",
        )


def test_mcp_invalid_type(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'type' must be 'stdio', 'sse', or 'http'"):
        _load(
            tmp_path,
            "targets:\n  - claude\ndependencies:\n  mcp:\n    - name: bad\n      type: grpc\n      command: something\n",
        )


# ---------------------------------------------------------------------------
# 24. load_config validation errors
# ---------------------------------------------------------------------------


def test_config_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config file not found"):
        load_resolved_config(tmp_path / "nonexistent.yml", no_global=True)


def test_config_malformed_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Failed to parse YAML"):
        _load(tmp_path, ":\n  - [invalid yaml")


def test_config_not_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config file must be a YAML mapping"):
        _load(tmp_path, "- a list\n- not a mapping\n")


def test_config_dependencies_not_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'dependencies' must be a mapping"):
        _load(tmp_path, 'targets:\n  - claude\ndependencies: "oops"\n')
