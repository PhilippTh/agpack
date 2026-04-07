"""Tests for env var substitution — .env loading and ${VAR} resolution.

All behaviour is tested through :func:`load_resolved_config`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import load_resolved_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_project(
    tmp_path: Path,
    config_text: str,
    dotenv: str | None = None,
) -> Path:
    """Write a project agpack.yml (and optionally .env) and return the yml path."""
    cfg_path = tmp_path / "agpack.yml"
    cfg_path.write_text(config_text, encoding="utf-8")
    if dotenv is not None:
        (tmp_path / ".env").write_text(dotenv, encoding="utf-8")
    return cfg_path


def _mcp_config(var: str = "MY_VAR") -> str:
    return f"""\
targets:
  - claude
dependencies:
  mcp:
    - name: s
      command: cmd
      env:
        KEY: ${{{var}}}
"""


# ---------------------------------------------------------------------------
# 1. .env loading (tested through load_resolved_config)
# ---------------------------------------------------------------------------


def test_dotenv_basic(tmp_path: Path) -> None:
    cfg_path = _write_project(
        tmp_path,
        """\
targets:
  - claude
dependencies:
  mcp:
    - name: s
      command: cmd
      env:
        A: "${FOO}"
        B: "${BAZ}"
""",
        dotenv="FOO=bar\nBAZ=qux\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env == {"A": "bar", "B": "qux"}


def test_dotenv_with_double_quotes(tmp_path: Path) -> None:
    cfg_path = _write_project(tmp_path, _mcp_config("KEY"), dotenv='KEY="hello world"\n')
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env["KEY"] == "hello world"


def test_dotenv_with_single_quotes(tmp_path: Path) -> None:
    cfg_path = _write_project(tmp_path, _mcp_config("KEY"), dotenv="KEY='hello world'\n")
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env["KEY"] == "hello world"


def test_dotenv_comments_and_blanks(tmp_path: Path) -> None:
    cfg_path = _write_project(
        tmp_path,
        _mcp_config("KEY"),
        dotenv="# comment\n\nKEY=val\n  # indented comment\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env["KEY"] == "val"


def test_dotenv_export_prefix(tmp_path: Path) -> None:
    cfg_path = _write_project(tmp_path, _mcp_config("SECRET"), dotenv="export SECRET=abc123\n")
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env["KEY"] == "abc123"


def test_dotenv_missing_file_uses_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_VAR", "from-shell")
    cfg_path = _write_project(tmp_path, _mcp_config())  # no .env
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env["KEY"] == "from-shell"


def test_dotenv_value_with_equals(tmp_path: Path) -> None:
    cfg_path = _write_project(tmp_path, _mcp_config("URL"), dotenv="URL=https://example.com?a=1&b=2\n")
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env["KEY"] == "https://example.com?a=1&b=2"


# ---------------------------------------------------------------------------
# 2. Variable resolution
# ---------------------------------------------------------------------------


def test_resolve_single_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOO", "bar")
    cfg_path = _write_project(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  skills:\n    - url: ${FOO}\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.skills[0].url == "bar"


def test_resolve_multiple_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A", "hello")
    monkeypatch.setenv("B", "world")
    cfg_path = _write_project(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  skills:\n    - url: ${A}-${B}\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.skills[0].url == "hello-world"


def test_resolve_no_vars(tmp_path: Path) -> None:
    cfg_path = _write_project(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  skills:\n    - url: https://github.com/org/repo\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.skills[0].url == "https://github.com/org/repo"


def test_resolve_missing_var_raises(tmp_path: Path) -> None:
    cfg_path = _write_project(tmp_path, _mcp_config("MISSING"))
    with pytest.raises(ConfigError, match="environment variable 'MISSING' is not set"):
        load_resolved_config(cfg_path, no_global=True)


# ---------------------------------------------------------------------------
# 3. MCP server field resolution
# ---------------------------------------------------------------------------


def test_resolve_mcp_env_from_dotenv(tmp_path: Path) -> None:
    cfg_path = _write_project(tmp_path, _mcp_config("API_KEY"), dotenv="API_KEY=secret-from-dotenv\n")
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env["KEY"] == "secret-from-dotenv"


def test_resolve_mcp_env_from_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELL_VAR", "from-shell")
    cfg_path = _write_project(tmp_path, _mcp_config("SHELL_VAR"))
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env["KEY"] == "from-shell"


def test_resolve_mcp_dotenv_takes_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_VAR", "from-shell")
    cfg_path = _write_project(tmp_path, _mcp_config(), dotenv="MY_VAR=from-dotenv\n")
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env["KEY"] == "from-dotenv"


def test_resolve_no_substitution_needed(tmp_path: Path) -> None:
    cfg_path = _write_project(
        tmp_path,
        """\
targets:
  - claude
dependencies:
  mcp:
    - name: s
      command: cmd
      env:
        KEY: plain-value
""",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env["KEY"] == "plain-value"


def test_resolve_empty_mcp_list(tmp_path: Path) -> None:
    cfg_path = _write_project(tmp_path, "targets:\n  - claude\n")
    cfg = load_resolved_config(cfg_path, no_global=True)  # should not raise
    assert cfg.mcp == []


def test_resolve_multiple_servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN_A", "aaa")
    monkeypatch.setenv("TOKEN_B", "bbb")
    cfg_path = _write_project(
        tmp_path,
        """\
targets:
  - claude
dependencies:
  mcp:
    - name: a
      command: cmd
      env:
        T: "${TOKEN_A}"
    - name: b
      command: cmd
      env:
        T: "${TOKEN_B}"
""",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env["T"] == "aaa"
    assert cfg.mcp[1].env["T"] == "bbb"


# ---------------------------------------------------------------------------
# 4. Dependency field resolution (url, path, ref)
# ---------------------------------------------------------------------------


def test_resolve_dependency_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_ORG", "my-org")
    cfg_path = _write_project(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  skills:\n    - url: https://github.com/${GH_ORG}/repo\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.skills[0].url == "https://github.com/my-org/repo"


def test_resolve_dependency_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILL_NAME", "my-skill")
    cfg_path = _write_project(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  skills:\n    - url: https://github.com/org/repo\n      path: skills/${SKILL_NAME}\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.skills[0].path == "skills/my-skill"


def test_resolve_dependency_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAG", "v2.0")
    cfg_path = _write_project(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  commands:\n    - url: https://github.com/org/repo\n      ref: ${TAG}\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.commands[0].ref == "v2.0"


def test_resolve_dependency_no_vars_unchanged(tmp_path: Path) -> None:
    cfg_path = _write_project(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  agents:\n    - url: https://github.com/org/repo\n      path: skills/foo\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.agents[0].url == "https://github.com/org/repo"
    assert cfg.agents[0].path == "skills/foo"


def test_resolve_dependency_path_none_stays_none(tmp_path: Path) -> None:
    cfg_path = _write_project(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  skills:\n    - url: https://github.com/org/repo\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.skills[0].path is None


def test_resolve_dependency_ref_none_stays_none(tmp_path: Path) -> None:
    cfg_path = _write_project(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  skills:\n    - url: https://github.com/org/repo\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.skills[0].ref is None


# ---------------------------------------------------------------------------
# 5. MCP command, args, url field resolution
# ---------------------------------------------------------------------------


def test_resolve_mcp_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_BIN", "/usr/local/bin/my-server")
    cfg_path = _write_project(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  mcp:\n    - name: s\n      command: ${MCP_BIN}\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].command == "/usr/local/bin/my-server"


def test_resolve_mcp_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "9090")
    cfg_path = _write_project(
        tmp_path,
        'targets:\n  - claude\ndependencies:\n  mcp:\n    - name: s\n      command: node\n      args: ["--port", "${PORT}"]\n',
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].args == ["--port", "9090"]


def test_resolve_mcp_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HOST", "private.example.com")
    cfg_path = _write_project(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  mcp:\n    - name: s\n      type: sse\n      url: https://${MCP_HOST}/sse\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].url == "https://private.example.com/sse"


# ---------------------------------------------------------------------------
# 6. Three-tier .env resolution (project > global > shell)
# ---------------------------------------------------------------------------


def _setup_three_tier(
    tmp_path: Path,
    *,
    project_dotenv: str | None = None,
    global_dotenv: str | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
    shell_var: tuple[str, str] | None = None,
) -> AgpackConfig:
    """Set up a project + global config with .env files and load."""
    import os

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "agpack.yml").write_text(
        "targets:\n  - claude\ndependencies:\n  mcp:\n    - name: s\n      command: cmd\n      env:\n        V: ${MY_VAR}\n"
    )
    if project_dotenv is not None:
        (project_dir / ".env").write_text(project_dotenv)

    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / "agpack.yml").write_text("")
    if global_dotenv is not None:
        (global_dir / ".env").write_text(global_dotenv)

    if shell_var and monkeypatch:
        monkeypatch.setenv(shell_var[0], shell_var[1])

    old = os.environ.get("AGPACK_GLOBAL_CONFIG")
    os.environ["AGPACK_GLOBAL_CONFIG"] = str(global_dir / "agpack.yml")
    try:
        return load_resolved_config(project_dir / "agpack.yml")
    finally:
        if old is None:
            os.environ.pop("AGPACK_GLOBAL_CONFIG", None)
        else:
            os.environ["AGPACK_GLOBAL_CONFIG"] = old


def test_three_tier_project_dotenv_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Project .env takes highest priority."""
    cfg = _setup_three_tier(
        tmp_path,
        project_dotenv="MY_VAR=from-project\n",
        global_dotenv="MY_VAR=from-global\n",
        monkeypatch=monkeypatch,
        shell_var=("MY_VAR", "from-shell"),
    )
    assert cfg.mcp[0].env["V"] == "from-project"


def test_three_tier_global_dotenv_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Global .env used when project .env doesn't define the var."""
    cfg = _setup_three_tier(
        tmp_path,
        global_dotenv="MY_VAR=from-global\n",
        monkeypatch=monkeypatch,
        shell_var=("MY_VAR", "from-shell"),
    )
    assert cfg.mcp[0].env["V"] == "from-global"


def test_three_tier_shell_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shell env used when neither .env defines the var."""
    cfg = _setup_three_tier(
        tmp_path,
        monkeypatch=monkeypatch,
        shell_var=("MY_VAR", "from-shell"),
    )
    assert cfg.mcp[0].env["V"] == "from-shell"


def test_three_tier_no_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When no global config, only project .env + shell are used."""
    monkeypatch.setenv("MY_VAR", "from-shell")
    cfg_path = _write_project(tmp_path, _mcp_config(), dotenv="MY_VAR=from-project\n")
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.mcp[0].env["KEY"] == "from-project"


def test_three_tier_global_env_applies_to_deps(tmp_path: Path) -> None:
    """Global .env vars are available for dependency field substitution too."""
    import os

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "agpack.yml").write_text(
        "targets:\n  - claude\ndependencies:\n  skills:\n    - url: https://github.com/${GH_ORG}/repo\n"
    )

    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / "agpack.yml").write_text("")
    (global_dir / ".env").write_text("GH_ORG=my-global-org\n")

    old = os.environ.get("AGPACK_GLOBAL_CONFIG")
    os.environ["AGPACK_GLOBAL_CONFIG"] = str(global_dir / "agpack.yml")
    try:
        cfg = load_resolved_config(project_dir / "agpack.yml")
    finally:
        if old is None:
            os.environ.pop("AGPACK_GLOBAL_CONFIG", None)
        else:
            os.environ["AGPACK_GLOBAL_CONFIG"] = old

    assert cfg.skills[0].url == "https://github.com/my-global-org/repo"


# ---------------------------------------------------------------------------
# 7. Multiple URL substitution
# ---------------------------------------------------------------------------


def test_resolve_multiple_urls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_ORG", "my-org")
    cfg_path = _write_project(
        tmp_path,
        """\
targets:
  - claude
dependencies:
  skills:
    - url:
        - https://github.com/${GH_ORG}/repo
        - git@github.com:${GH_ORG}/repo.git
""",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.skills[0].urls == [
        "https://github.com/my-org/repo",
        "git@github.com:my-org/repo.git",
    ]


def test_resolve_single_url_unchanged(tmp_path: Path) -> None:
    cfg_path = _write_project(
        tmp_path,
        "targets:\n  - claude\ndependencies:\n  skills:\n    - url: https://github.com/org/repo\n",
    )
    cfg = load_resolved_config(cfg_path, no_global=True)
    assert cfg.skills[0].urls == ["https://github.com/org/repo"]
