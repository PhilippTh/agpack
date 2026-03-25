"""Tests for agpack.envsubst – .env loading and ${VAR} substitution."""

from __future__ import annotations

from pathlib import Path

import pytest

from agpack.config import AgpackConfig
from agpack.config import ConfigError
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
