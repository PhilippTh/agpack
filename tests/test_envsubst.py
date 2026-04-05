"""Tests for agpack.envsubst – .env loading and ${VAR} substitution."""

from __future__ import annotations

from pathlib import Path

import pytest

from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import DependencySource
from agpack.config import GlobalConfig
from agpack.config import McpServer
from agpack.envsubst import load_dotenv
from agpack.envsubst import resolve_config
from agpack.envsubst import resolve_env_vars

# ---------------------------------------------------------------------------
# 1. load_dotenv
# ---------------------------------------------------------------------------


def test_load_dotenv_basic(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("FOO=bar\nBAZ=qux\n")
    result = load_dotenv(tmp_path)
    assert result == {"FOO": "bar", "BAZ": "qux"}


def test_load_dotenv_with_double_quotes(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text('KEY="hello world"\n')
    assert load_dotenv(tmp_path) == {"KEY": "hello world"}


def test_load_dotenv_with_single_quotes(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("KEY='hello world'\n")
    assert load_dotenv(tmp_path) == {"KEY": "hello world"}


def test_load_dotenv_comments_and_blanks(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("# comment\n\nKEY=val\n  # indented comment\n")
    assert load_dotenv(tmp_path) == {"KEY": "val"}


def test_load_dotenv_export_prefix(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("export SECRET=abc123\n")
    assert load_dotenv(tmp_path) == {"SECRET": "abc123"}


def test_load_dotenv_missing_file(tmp_path: Path) -> None:
    assert load_dotenv(tmp_path) == {}


def test_load_dotenv_empty_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("")
    assert load_dotenv(tmp_path) == {}


def test_load_dotenv_malformed_lines_skipped(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("GOOD=val\nno-equals-sign\n")
    assert load_dotenv(tmp_path) == {"GOOD": "val"}


def test_load_dotenv_value_with_equals(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("URL=https://example.com?a=1&b=2\n")
    assert load_dotenv(tmp_path) == {"URL": "https://example.com?a=1&b=2"}


# ---------------------------------------------------------------------------
# 2. resolve_env_vars
# ---------------------------------------------------------------------------


def test_resolve_single_var() -> None:
    assert resolve_env_vars("${FOO}", {"FOO": "bar"}) == "bar"


def test_resolve_multiple_vars() -> None:
    env = {"A": "hello", "B": "world"}
    assert resolve_env_vars("${A}-${B}", env) == "hello-world"


def test_resolve_no_vars() -> None:
    assert resolve_env_vars("plain-string", {}) == "plain-string"


def test_resolve_mixed_literal_and_var() -> None:
    result = resolve_env_vars("prefix-${KEY}-suffix", {"KEY": "val"})
    assert result == "prefix-val-suffix"


def test_resolve_missing_var_raises() -> None:
    with pytest.raises(ConfigError, match="environment variable 'MISSING' is not set"):
        resolve_env_vars("${MISSING}", {})


def test_resolve_missing_var_includes_context() -> None:
    with pytest.raises(ConfigError, match="mcp server 'ctx7'"):
        resolve_env_vars("${NOPE}", {}, context="mcp server 'ctx7'")


def test_resolve_partial_missing_raises() -> None:
    with pytest.raises(ConfigError, match="'MISSING'"):
        resolve_env_vars("${EXISTS}-${MISSING}", {"EXISTS": "ok"})


# ---------------------------------------------------------------------------
# 3. resolve_config – MCP server env
# ---------------------------------------------------------------------------


def _make_config(mcp: list[McpServer] | None = None) -> AgpackConfig:
    return AgpackConfig(
        name="test",
        version="1",
        targets=["claude"],
        mcp=mcp or [],
    )


def test_resolve_config_from_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("API_KEY=secret-from-dotenv\n")
    server = McpServer(name="s", command="cmd", env={"API_KEY": "${API_KEY}"})
    config = _make_config([server])

    resolve_config(config, tmp_path)

    assert config.mcp[0].env["API_KEY"] == "secret-from-dotenv"


def test_resolve_config_from_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHELL_VAR", "from-shell")
    server = McpServer(name="s", command="cmd", env={"KEY": "${SHELL_VAR}"})
    config = _make_config([server])

    resolve_config(config, tmp_path)

    assert config.mcp[0].env["KEY"] == "from-shell"


def test_resolve_config_dotenv_takes_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_VAR", "from-shell")
    (tmp_path / ".env").write_text("MY_VAR=from-dotenv\n")
    server = McpServer(name="s", command="cmd", env={"V": "${MY_VAR}"})
    config = _make_config([server])

    resolve_config(config, tmp_path)

    assert config.mcp[0].env["V"] == "from-dotenv"


def test_resolve_config_no_substitution_needed(tmp_path: Path) -> None:
    server = McpServer(name="s", command="cmd", env={"KEY": "plain-value"})
    config = _make_config([server])

    resolve_config(config, tmp_path)

    assert config.mcp[0].env["KEY"] == "plain-value"


def test_resolve_config_missing_var_raises(tmp_path: Path) -> None:
    server = McpServer(name="ctx7", command="cmd", env={"K": "${UNDEFINED}"})
    config = _make_config([server])

    with pytest.raises(ConfigError, match="'UNDEFINED'"):
        resolve_config(config, tmp_path)


def test_resolve_config_empty_mcp_list(tmp_path: Path) -> None:
    config = _make_config([])
    resolve_config(config, tmp_path)  # should not raise


def test_resolve_config_multiple_servers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOKEN_A", "aaa")
    monkeypatch.setenv("TOKEN_B", "bbb")
    servers = [
        McpServer(name="a", command="cmd", env={"T": "${TOKEN_A}"}),
        McpServer(name="b", command="cmd", env={"T": "${TOKEN_B}"}),
    ]
    config = _make_config(servers)

    resolve_config(config, tmp_path)

    assert config.mcp[0].env["T"] == "aaa"
    assert config.mcp[1].env["T"] == "bbb"


# ---------------------------------------------------------------------------
# 4. resolve_config – dependency fields (url, path, ref)
# ---------------------------------------------------------------------------


def test_resolve_dependency_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_ORG", "my-org")
    dep = DependencySource(url="https://github.com/${GH_ORG}/repo")
    config = _make_config()
    config.skills = [dep]

    resolve_config(config, tmp_path)

    assert config.skills[0].url == "https://github.com/my-org/repo"


def test_resolve_dependency_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_NAME", "my-skill")
    dep = DependencySource(
        url="https://github.com/org/repo", path="skills/${SKILL_NAME}"
    )
    config = _make_config()
    config.skills = [dep]

    resolve_config(config, tmp_path)

    assert config.skills[0].path == "skills/my-skill"


def test_resolve_dependency_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAG", "v2.0")
    dep = DependencySource(url="https://github.com/org/repo", ref="${TAG}")
    config = _make_config()
    config.commands = [dep]

    resolve_config(config, tmp_path)

    assert config.commands[0].ref == "v2.0"


def test_resolve_dependency_no_vars_unchanged(tmp_path: Path) -> None:
    dep = DependencySource(url="https://github.com/org/repo", path="skills/foo")
    config = _make_config()
    config.agents = [dep]

    resolve_config(config, tmp_path)

    assert config.agents[0].url == "https://github.com/org/repo"
    assert config.agents[0].path == "skills/foo"


def test_resolve_dependency_path_none_stays_none(tmp_path: Path) -> None:
    dep = DependencySource(url="https://github.com/org/repo")
    config = _make_config()
    config.skills = [dep]

    resolve_config(config, tmp_path)

    assert config.skills[0].path is None


def test_resolve_dependency_ref_none_stays_none(tmp_path: Path) -> None:
    dep = DependencySource(url="https://github.com/org/repo")
    config = _make_config()
    config.skills = [dep]

    resolve_config(config, tmp_path)

    assert config.skills[0].ref is None


# ---------------------------------------------------------------------------
# 5. resolve_config – MCP command, args, url fields
# ---------------------------------------------------------------------------


def test_resolve_mcp_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_BIN", "/usr/local/bin/my-server")
    server = McpServer(name="s", command="${MCP_BIN}")
    config = _make_config([server])

    resolve_config(config, tmp_path)

    assert config.mcp[0].command == "/usr/local/bin/my-server"


def test_resolve_mcp_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "9090")
    server = McpServer(name="s", command="node", args=["--port", "${PORT}"])
    config = _make_config([server])

    resolve_config(config, tmp_path)

    assert config.mcp[0].args == ["--port", "9090"]


def test_resolve_mcp_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HOST", "private.example.com")
    server = McpServer(name="s", type="sse", url="https://${MCP_HOST}/sse")
    config = _make_config([server])

    resolve_config(config, tmp_path)

    assert config.mcp[0].url == "https://private.example.com/sse"


# ---------------------------------------------------------------------------
# 6. resolve_config – three-tier .env resolution (project > global > shell)
# ---------------------------------------------------------------------------


def test_three_tier_project_dotenv_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project .env takes highest priority."""
    monkeypatch.setenv("MY_VAR", "from-shell")

    # Global .env
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / ".env").write_text("MY_VAR=from-global\n")
    global_cfg = GlobalConfig(config_dir=global_dir)

    # Project .env
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".env").write_text("MY_VAR=from-project\n")

    server = McpServer(name="s", command="cmd", env={"V": "${MY_VAR}"})
    config = _make_config([server])

    resolve_config(config, project_dir, global_config=global_cfg)

    assert config.mcp[0].env["V"] == "from-project"


def test_three_tier_global_dotenv_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Global .env used when project .env doesn't define the var."""
    monkeypatch.setenv("MY_VAR", "from-shell")

    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / ".env").write_text("MY_VAR=from-global\n")
    global_cfg = GlobalConfig(config_dir=global_dir)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # No project .env

    server = McpServer(name="s", command="cmd", env={"V": "${MY_VAR}"})
    config = _make_config([server])

    resolve_config(config, project_dir, global_config=global_cfg)

    assert config.mcp[0].env["V"] == "from-global"


def test_three_tier_shell_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shell env used when neither .env defines the var."""
    monkeypatch.setenv("MY_VAR", "from-shell")

    global_dir = tmp_path / "global"
    global_dir.mkdir()
    # No global .env
    global_cfg = GlobalConfig(config_dir=global_dir)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # No project .env

    server = McpServer(name="s", command="cmd", env={"V": "${MY_VAR}"})
    config = _make_config([server])

    resolve_config(config, project_dir, global_config=global_cfg)

    assert config.mcp[0].env["V"] == "from-shell"


def test_three_tier_no_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no global config is provided, only project .env + shell are used."""
    monkeypatch.setenv("MY_VAR", "from-shell")
    (tmp_path / ".env").write_text("MY_VAR=from-project\n")

    server = McpServer(name="s", command="cmd", env={"V": "${MY_VAR}"})
    config = _make_config([server])

    resolve_config(config, tmp_path)  # no global_config

    assert config.mcp[0].env["V"] == "from-project"


def test_three_tier_global_env_applies_to_deps(tmp_path: Path) -> None:
    """Global .env vars are available for dependency field substitution too."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / ".env").write_text("GH_ORG=my-global-org\n")
    global_cfg = GlobalConfig(config_dir=global_dir)

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    dep = DependencySource(url="https://github.com/${GH_ORG}/repo")
    config = _make_config()
    config.skills = [dep]

    resolve_config(config, project_dir, global_config=global_cfg)

    assert config.skills[0].url == "https://github.com/my-global-org/repo"
