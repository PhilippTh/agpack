"""Tests for ``.env`` loading and ``${VAR}`` substitution (lives in :mod:`agpack.config`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agpack.config import AgpackConfig
from agpack.config import ConfigError
from agpack.config import DependencyEntry
from agpack.config import DependencySource
from agpack.config import GlobalConfig
from agpack.config import resolve_config
from agpack.envsubst import load_dotenv
from agpack.envsubst import resolve_env_vars
from agpack.patch import Patch

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
    with pytest.raises(ConfigError, match="variable 'MISSING' is not defined"):
        resolve_env_vars("${MISSING}", {})


def test_resolve_partial_missing_raises() -> None:
    with pytest.raises(ConfigError, match="'MISSING'"):
        resolve_env_vars("${EXISTS}-${MISSING}", {"EXISTS": "ok"})


# ---------------------------------------------------------------------------
# 3. resolve_config — dependency URL/path/ref
# ---------------------------------------------------------------------------


def _make_config(**deps: list[DependencyEntry]) -> AgpackConfig:
    return AgpackConfig(targets=["claude"], dependencies=dict(deps))


def test_resolve_validates_but_does_not_mutate_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Templates with resolvable ${VAR}s stay verbatim on DependencySource.

    The lockfile, progress output, and FetchError messages all read these fields, so substituting in place would leak
    ${GITHUB_TOKEN}. Actual substitution happens inside fetch_dependency.
    """
    monkeypatch.setenv("GH_ORG", "my-org")
    dep = DependencySource(urls=["https://github.com/${GH_ORG}/repo"])
    config = _make_config(skills=[dep])
    resolve_config(config, tmp_path)
    # Template preserved — substitution is deferred to clone time.
    assert config.dependencies["skills"][0].urls == [  # type: ignore[union-attr]
        "https://github.com/${GH_ORG}/repo"
    ]


def test_resolve_validates_but_does_not_mutate_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKILL_NAME", "my-skill")
    dep = DependencySource(urls=["https://github.com/org/repo"], path="skills/${SKILL_NAME}")
    config = _make_config(skills=[dep])
    resolve_config(config, tmp_path)
    assert config.dependencies["skills"][0].path == "skills/${SKILL_NAME}"  # type: ignore[union-attr]


def test_resolve_validates_but_does_not_mutate_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAG", "v2.0")
    dep = DependencySource(urls=["https://github.com/org/repo"], ref="${TAG}")
    config = _make_config(commands=[dep])
    resolve_config(config, tmp_path)
    assert config.dependencies["commands"][0].ref == "${TAG}"  # type: ignore[union-attr]


def test_resolve_validates_every_url_in_fallback_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each URL in a fallback list is validated; none are mutated."""
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
        "https://github.com/${GH_ORG}/repo",
        "git@github.com:${GH_ORG}/repo.git",
    ]


def test_resolve_raises_for_missing_var(tmp_path: Path) -> None:
    """An unresolvable ${VAR} fails at validate time, before any clones."""
    dep = DependencySource(urls=["https://github.com/${MISSING_VAR_XYZ}/repo"])
    config = _make_config(skills=[dep])
    with pytest.raises(Exception, match="MISSING_VAR_XYZ"):
        resolve_config(config, tmp_path)


# ---------------------------------------------------------------------------
# 4. resolve_config — patches are NOT substituted at load time
# ---------------------------------------------------------------------------


def test_patches_pass_through_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Patches keep their ${...} templates after resolve_config.

    Substitution happens per-target at apply time so target ``vars`` can win over env vars on collision (see
    test_patches.py).
    """
    monkeypatch.setenv("MCP_BIN", "/usr/local/bin/my-server")
    patch = Patch(key="${bucket}.s", value="${MCP_BIN}")
    config = _make_config(mcp=[patch])
    resolve_config(config, tmp_path)
    # Templates intact: substitution is deferred to apply time.
    assert config.dependencies["mcp"][0].key == "${bucket}.s"
    assert config.dependencies["mcp"][0].value == "${MCP_BIN}"


def test_resolve_config_returns_env_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_config returns the merged env table for downstream apply."""
    (tmp_path / ".env").write_text("API_KEY=secret\n")
    monkeypatch.setenv("OTHER", "from-shell")
    config = _make_config()
    env = resolve_config(config, tmp_path)
    assert env["API_KEY"] == "secret"
    assert env["OTHER"] == "from-shell"


# ---------------------------------------------------------------------------
# 5. Three-tier .env (project > global > shell) — verified via fetch deps
# ---------------------------------------------------------------------------


def test_three_tier_project_dotenv_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_VAR", "from-shell")
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / ".env").write_text("MY_VAR=from-global\n")
    global_cfg = GlobalConfig(config_dir=global_dir)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".env").write_text("MY_VAR=from-project\n")

    dep = DependencySource(urls=["https://example.com/${MY_VAR}"])
    config = _make_config(skills=[dep])
    env = resolve_config(config, project_dir, global_config=global_cfg)
    # Precedence in the returned env table (consumed at clone time).
    assert env["MY_VAR"] == "from-project"


def test_three_tier_global_dotenv_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_VAR", "from-shell")
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / ".env").write_text("MY_VAR=from-global\n")
    global_cfg = GlobalConfig(config_dir=global_dir)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    dep = DependencySource(urls=["https://example.com/${MY_VAR}"])
    config = _make_config(skills=[dep])
    env = resolve_config(config, project_dir, global_config=global_cfg)
    assert env["MY_VAR"] == "from-global"


def test_three_tier_shell_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_VAR", "from-shell")
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    global_cfg = GlobalConfig(config_dir=global_dir)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    dep = DependencySource(urls=["https://example.com/${MY_VAR}"])
    config = _make_config(skills=[dep])
    env = resolve_config(config, project_dir, global_config=global_cfg)
    assert env["MY_VAR"] == "from-shell"


def test_three_tier_global_env_applies_to_deps(tmp_path: Path) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / ".env").write_text("GH_ORG=my-global-org\n")
    global_cfg = GlobalConfig(config_dir=global_dir)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    dep = DependencySource(urls=["https://github.com/${GH_ORG}/repo"])
    config = _make_config(skills=[dep])
    env = resolve_config(config, project_dir, global_config=global_cfg)
    assert env["GH_ORG"] == "my-global-org"


def test_resolve_empty_config_no_op(tmp_path: Path) -> None:
    config = _make_config()
    resolve_config(config, tmp_path)
