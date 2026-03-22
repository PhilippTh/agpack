"""Tests for agpack.config – YAML loading, validation, and data classes."""

from __future__ import annotations

from pathlib import Path

import pytest

from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import DependencySource
from agpack.config import load_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, text: str) -> Path:
    """Write *text* to ``agpack.yml`` inside *tmp_path* and return the path."""
    p = tmp_path / "agpack.yml"
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. Valid config with all fields
# ---------------------------------------------------------------------------


def test_load_valid_full_config(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: my-pack
version: "1.0"
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
    cfg = load_config(cfg_path)

    assert isinstance(cfg, AgpackConfig)
    assert cfg.name == "my-pack"
    assert cfg.version == "1.0"
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
# 2. Missing name
# ---------------------------------------------------------------------------


def test_missing_name(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
version: "1.0"
targets:
  - claude
""",
    )
    with pytest.raises(ConfigError, match="Missing required field 'name'"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 3. Missing version
# ---------------------------------------------------------------------------


def test_missing_version(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
targets:
  - claude
""",
    )
    with pytest.raises(ConfigError, match="Missing required field 'version'"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 4. Missing / invalid targets
# ---------------------------------------------------------------------------


def test_missing_targets(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
""",
    )
    with pytest.raises(ConfigError, match="Missing or invalid 'targets'"):
        load_config(cfg_path)


def test_targets_not_a_list(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets: claude
""",
    )
    with pytest.raises(ConfigError, match="Missing or invalid 'targets'"):
        load_config(cfg_path)


def test_empty_targets(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets: []
""",
    )
    with pytest.raises(ConfigError, match="Missing or invalid 'targets'"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 5. Unrecognised target name
# ---------------------------------------------------------------------------


def test_unrecognised_target(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets:
  - not-a-target
""",
    )
    with pytest.raises(ConfigError, match="Unrecognised target 'not-a-target'"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 6. Dependency with missing url
# ---------------------------------------------------------------------------


def test_dependency_missing_url(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets:
  - claude
dependencies:
  skills:
    - path: some/path
""",
    )
    with pytest.raises(ConfigError, match="missing required field 'url'"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 7. Dependency with non-string url
# ---------------------------------------------------------------------------


def test_dependency_url_must_be_string(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets:
  - claude
dependencies:
  skills:
    - url: 123
""",
    )
    with pytest.raises(ConfigError, match="'url' must be a string"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 8. Dependency with all optional fields (path, ref)
# ---------------------------------------------------------------------------


def test_dependency_all_optional_fields(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets:
  - claude
dependencies:
  commands:
    - url: https://gitlab.com/org/repo
      path: sub/dir
      ref: main
""",
    )
    cfg = load_config(cfg_path)
    dep = cfg.commands[0]
    assert dep.url == "https://gitlab.com/org/repo"
    assert dep.path == "sub/dir"
    assert dep.ref == "main"


# ---------------------------------------------------------------------------
# 9. MCP stdio server – valid
# ---------------------------------------------------------------------------


def test_mcp_stdio_valid(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
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
    cfg = load_config(cfg_path)
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
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets:
  - claude
dependencies:
  mcp:
    - name: bad-stdio
      type: stdio
""",
    )
    with pytest.raises(ConfigError, match="missing required field 'command'"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 11. MCP sse server – valid
# ---------------------------------------------------------------------------


def test_mcp_sse_valid(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets:
  - claude
dependencies:
  mcp:
    - name: sse-srv
      type: sse
      url: http://localhost:3000/sse
""",
    )
    cfg = load_config(cfg_path)
    srv = cfg.mcp[0]
    assert srv.name == "sse-srv"
    assert srv.type == "sse"
    assert srv.url == "http://localhost:3000/sse"
    assert srv.command is None


# ---------------------------------------------------------------------------
# 12. MCP sse server – missing url
# ---------------------------------------------------------------------------


def test_mcp_sse_missing_url(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets:
  - claude
dependencies:
  mcp:
    - name: bad-sse
      type: sse
""",
    )
    with pytest.raises(ConfigError, match="missing required field 'url'"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 13. MCP http server – valid
# ---------------------------------------------------------------------------


def test_mcp_http_valid(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets:
  - claude
dependencies:
  mcp:
    - name: http-srv
      type: http
      url: http://localhost:9000/api
""",
    )
    cfg = load_config(cfg_path)
    srv = cfg.mcp[0]
    assert srv.name == "http-srv"
    assert srv.type == "http"
    assert srv.url == "http://localhost:9000/api"


# ---------------------------------------------------------------------------
# 14. MCP entry missing name
# ---------------------------------------------------------------------------


def test_mcp_missing_name(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets:
  - claude
dependencies:
  mcp:
    - type: stdio
      command: node
""",
    )
    with pytest.raises(ConfigError, match="missing required field 'name'"):
        load_config(cfg_path)


# ---------------------------------------------------------------------------
# 15. DependencySource.name property
# ---------------------------------------------------------------------------


def test_dependency_source_name_from_path() -> None:
    dep = DependencySource(url="https://github.com/org/repo", path="skills/my-skill")
    assert dep.name == "my-skill"


def test_dependency_source_name_from_path_trailing_slash() -> None:
    dep = DependencySource(url="https://github.com/org/repo", path="skills/my-skill/")
    assert dep.name == "my-skill"


def test_dependency_source_name_from_url() -> None:
    dep = DependencySource(url="https://github.com/org/repo-name")
    assert dep.name == "repo-name"


def test_dependency_source_name_from_url_with_dotgit() -> None:
    dep = DependencySource(url="https://github.com/org/repo-name.git")
    assert dep.name == "repo-name"


def test_dependency_source_name_from_url_trailing_slash() -> None:
    dep = DependencySource(url="https://github.com/org/repo-name/")
    assert dep.name == "repo-name"


# ---------------------------------------------------------------------------
# 16. DependencySource.identity property
# ---------------------------------------------------------------------------


def test_dependency_source_identity_without_path() -> None:
    dep = DependencySource(url="https://github.com/org/repo")
    assert dep.identity == "https://github.com/org/repo"


def test_dependency_source_identity_with_path() -> None:
    dep = DependencySource(url="https://github.com/org/repo", path="sub/dir")
    assert dep.identity == "https://github.com/org/repo::sub/dir"


def test_dependency_source_identity_different_url() -> None:
    dep = DependencySource(url="https://gitlab.com/org/repo")
    assert dep.identity == "https://gitlab.com/org/repo"


# ---------------------------------------------------------------------------
# 17. Empty dependencies section
# ---------------------------------------------------------------------------


def test_empty_dependencies(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets:
  - claude
dependencies: {}
""",
    )
    cfg = load_config(cfg_path)
    assert cfg.skills == []
    assert cfg.commands == []
    assert cfg.agents == []
    assert cfg.mcp == []


def test_no_dependencies_key(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
targets:
  - claude
""",
    )
    cfg = load_config(cfg_path)
    assert cfg.skills == []
    assert cfg.commands == []
    assert cfg.agents == []
    assert cfg.mcp == []


# ---------------------------------------------------------------------------
# 18. Mixed dependency formats
# ---------------------------------------------------------------------------


def test_mixed_dependencies(tmp_path: Path) -> None:
    cfg_path = _write_config(
        tmp_path,
        """\
name: pack
version: "1"
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
    cfg = load_config(cfg_path)

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
