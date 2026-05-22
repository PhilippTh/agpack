"""Tests for agpack.envsubst – .env loading and ${VAR} substitution."""

from __future__ import annotations

from pathlib import Path

import pytest

from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import DependencyEntry
from agpack.config import DependencySource
from agpack.config import GlobalConfig
from agpack.envsubst import load_dotenv
from agpack.envsubst import resolve_config
from agpack.envsubst import resolve_env_vars
from agpack.kinds import Patch

# ---------------------------------------------------------------------------
# 1. load_dotenv
# ---------------------------------------------------------------------------


def test_load_dotenv_basic(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("FOO=bar\nBAZ=qux\n")
    assert load_dotenv(tmp_path) == {"FOO": "bar", "BAZ": "qux"}


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


def test_resolve_partial_missing_raises() -> None:
    with pytest.raises(ConfigError, match="'MISSING'"):
        resolve_env_vars("${EXISTS}-${MISSING}", {"EXISTS": "ok"})


# ---------------------------------------------------------------------------
# 3. resolve_config — dependency URL/path/ref
# ---------------------------------------------------------------------------


def _make_config(**deps: list[DependencyEntry]) -> AgpackConfig:
    return AgpackConfig(targets=["claude"], dependencies=dict(deps))


def test_resolve_dependency_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_ORG", "my-org")
    dep = DependencySource(urls=["https://github.com/${GH_ORG}/repo"])
    config = _make_config(skills=[dep])
    resolve_config(config, tmp_path)
    assert config.dependencies["skills"][0].urls == [  # type: ignore[union-attr]
        "https://github.com/my-org/repo"
    ]


def test_resolve_dependency_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_NAME", "my-skill")
    dep = DependencySource(
        urls=["https://github.com/org/repo"], path="skills/${SKILL_NAME}"
    )
    config = _make_config(skills=[dep])
    resolve_config(config, tmp_path)
    assert config.dependencies["skills"][0].path == "skills/my-skill"  # type: ignore[union-attr]


def test_resolve_dependency_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAG", "v2.0")
    dep = DependencySource(urls=["https://github.com/org/repo"], ref="${TAG}")
    config = _make_config(commands=[dep])
    resolve_config(config, tmp_path)
    assert config.dependencies["commands"][0].ref == "v2.0"  # type: ignore[union-attr]


def test_resolve_multiple_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_ORG", "my-org")
    dep = DependencySource(
        urls=[
            "https://github.com/${GH_ORG}/repo",
            "git@github.com:${GH_ORG}/repo.git",
        ],
    )
    config = _make_config(skills=[dep])
    resolve_config(config, tmp_path)
    assert config.dependencies["skills"][0].urls == [  # type: ignore[union-attr]
        "https://github.com/my-org/repo",
        "git@github.com:my-org/repo.git",
    ]


# ---------------------------------------------------------------------------
# 4. resolve_config — patch values (recursive)
# ---------------------------------------------------------------------------


def test_resolve_patch_string_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_BIN", "/usr/local/bin/my-server")
    patch = Patch(key="mcpServers.s", value="${MCP_BIN}")
    config = _make_config(mcp=[patch])
    resolve_config(config, tmp_path)
    assert config.dependencies["mcp"][0].value == "/usr/local/bin/my-server"


def test_resolve_patch_nested_dict_values(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("API_KEY=secret\nPORT=9090\n")
    patch = Patch(
        key="mcpServers.s",
        value={
            "command": "node",
            "args": ["--port", "${PORT}"],
            "env": {"API_KEY": "${API_KEY}"},
        },
    )
    config = _make_config(mcp=[patch])
    resolve_config(config, tmp_path)
    resolved = config.dependencies["mcp"][0].value  # type: ignore[union-attr]
    assert resolved["args"] == ["--port", "9090"]
    assert resolved["env"]["API_KEY"] == "secret"


def test_resolve_patch_key_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SERVER_NAME", "filesystem")
    patch = Patch(key="mcpServers.${SERVER_NAME}", value={"command": "x"})
    config = _make_config(mcp=[patch])
    resolve_config(config, tmp_path)
    assert config.dependencies["mcp"][0].key == "mcpServers.filesystem"


def test_resolve_patch_missing_var_raises(tmp_path: Path) -> None:
    patch = Patch(key="x", value={"env": {"K": "${UNDEFINED}"}})
    config = _make_config(mcp=[patch])
    with pytest.raises(ConfigError, match="'UNDEFINED'"):
        resolve_config(config, tmp_path)


def test_resolve_patch_non_string_leaves_untouched(tmp_path: Path) -> None:
    """Numbers, bools, None pass through unchanged."""
    patch = Patch(key="x", value={"n": 42, "b": True, "z": None})
    config = _make_config(mcp=[patch])
    resolve_config(config, tmp_path)
    assert config.dependencies["mcp"][0].value == {"n": 42, "b": True, "z": None}


# ---------------------------------------------------------------------------
# 5. Three-tier .env (project > global > shell)
# ---------------------------------------------------------------------------


def test_three_tier_project_dotenv_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_VAR", "from-shell")
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / ".env").write_text("MY_VAR=from-global\n")
    global_cfg = GlobalConfig(config_dir=global_dir)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".env").write_text("MY_VAR=from-project\n")

    patch = Patch(key="x.v", value="${MY_VAR}")
    config = _make_config(mcp=[patch])
    resolve_config(config, project_dir, global_config=global_cfg)
    assert config.dependencies["mcp"][0].value == "from-project"


def test_three_tier_global_dotenv_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_VAR", "from-shell")
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / ".env").write_text("MY_VAR=from-global\n")
    global_cfg = GlobalConfig(config_dir=global_dir)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    patch = Patch(key="x.v", value="${MY_VAR}")
    config = _make_config(mcp=[patch])
    resolve_config(config, project_dir, global_config=global_cfg)
    assert config.dependencies["mcp"][0].value == "from-global"


def test_three_tier_shell_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_VAR", "from-shell")
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    global_cfg = GlobalConfig(config_dir=global_dir)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    patch = Patch(key="x.v", value="${MY_VAR}")
    config = _make_config(mcp=[patch])
    resolve_config(config, project_dir, global_config=global_cfg)
    assert config.dependencies["mcp"][0].value == "from-shell"


def test_three_tier_global_env_applies_to_deps(tmp_path: Path) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / ".env").write_text("GH_ORG=my-global-org\n")
    global_cfg = GlobalConfig(config_dir=global_dir)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    dep = DependencySource(urls=["https://github.com/${GH_ORG}/repo"])
    config = _make_config(skills=[dep])
    resolve_config(config, project_dir, global_config=global_cfg)
    assert config.dependencies["skills"][0].url == (  # type: ignore[union-attr]
        "https://github.com/my-global-org/repo"
    )


def test_resolve_empty_config_no_op(tmp_path: Path) -> None:
    config = _make_config()
    resolve_config(config, tmp_path)
