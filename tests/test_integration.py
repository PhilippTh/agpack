"""End-to-end integration test using a local bare git repo."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from agpack.cli import main
from agpack.config import load_config
from agpack.kinds import EditFileError
from agpack.target_schema import parse_target_def


def _run_git(args: list[str], cwd: Path) -> None:
    """Run a git command in a directory."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


def _create_bare_repo(tmp_path: Path) -> Path:
    """Create a bare git repo with test content.

    Structure:
        skills/
            my-skill/
                SKILL.md
                helpers/
                    util.py
        commands/
            review.md
        agents/
            backend-expert.md
    """
    # Create a working directory to set up content
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    _run_git(["init"], work_dir)
    _run_git(["config", "user.email", "test@test.com"], work_dir)
    _run_git(["config", "user.name", "Test"], work_dir)

    # Create skill files
    skill_dir = work_dir / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# My Skill\nThis is a test skill.\n")
    helpers_dir = skill_dir / "helpers"
    helpers_dir.mkdir()
    (helpers_dir / "util.py").write_text("def helper():\n    pass\n")

    # Create command file
    cmd_dir = work_dir / "commands"
    cmd_dir.mkdir()
    (cmd_dir / "review.md").write_text("# Review Command\nReview the code.\n")

    # Create agent file
    agent_dir = work_dir / "agents"
    agent_dir.mkdir()
    (agent_dir / "backend-expert.md").write_text("# Backend Expert\nI am an expert.\n")

    _run_git(["add", "."], work_dir)
    _run_git(["commit", "-m", "initial"], work_dir)

    # Create a bare clone
    bare_dir = tmp_path / "bare-repo.git"
    _run_git(["clone", "--bare", str(work_dir), str(bare_dir)], tmp_path)

    return bare_dir


def test_full_sync_flow(tmp_path: Path) -> None:
    """Test the complete sync flow with a local bare git repo."""
    bare_repo = _create_bare_repo(tmp_path)

    # Set up project directory
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Write agpack.yml pointing to the local bare repo
    config = {
        "targets": ["claude", "opencode"],
        "dependencies": {
            "skills": [
                {
                    "url": str(bare_repo),
                    "path": "skills/my-skill",
                },
            ],
            "commands": [
                {
                    "url": str(bare_repo),
                    "path": "commands/review.md",
                },
            ],
            "agents": [
                {
                    "url": str(bare_repo),
                    "path": "agents/backend-expert.md",
                },
            ],
            "mcp": [
                {
                    # ${bucket} resolves per target: 'mcpServers' for
                    # claude, 'mcp' for opencode. One patch, both
                    # targets, correct bucket name on each.
                    "key": "${bucket}.filesystem",
                    "value": {
                        "command": "npx",
                        "args": [
                            "-y",
                            "@modelcontextprotocol/server-filesystem",
                            ".",
                        ],
                    },
                },
            ],
        },
    }

    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path), "--verbose"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, f"sync failed:\n{result.output}"
    assert "1 skills, 1 commands, 1 agents, 1 mcp" in result.output

    for target_dir in [".claude/skills/my-skill", ".opencode/skills/my-skill"]:
        skill_md = project_dir / target_dir / "SKILL.md"
        assert skill_md.exists(), f"Missing {skill_md}"
        assert "My Skill" in skill_md.read_text()
        util_py = project_dir / target_dir / "helpers" / "util.py"
        assert util_py.exists(), f"Missing {util_py}"

    assert (project_dir / ".claude/commands/review.md").exists()
    assert (project_dir / ".opencode/commands/review.md").exists()
    assert (project_dir / ".claude/agents/backend-expert.md").exists()
    assert (project_dir / ".opencode/agents/backend-expert.md").exists()

    # MCP — each target uses its own bucket name via ${bucket}.
    claude_mcp = json.loads((project_dir / ".mcp.json").read_text())
    assert claude_mcp["mcpServers"]["filesystem"]["command"] == "npx"
    opencode_mcp = json.loads((project_dir / "opencode.json").read_text())
    assert opencode_mcp["mcp"]["filesystem"]["command"] == "npx"

    # Verify lockfile
    lockfile_path = project_dir / ".agpack.lock.yml"
    assert lockfile_path.exists()
    lockfile = yaml.safe_load(lockfile_path.read_text())
    assert len(lockfile["installed"]) == 3
    edits = {e["resource_type"]: e for e in lockfile["edits"]}
    assert "mcp" in edits
    # The single patch was applied to two targets (claude + opencode).
    assert len(edits["mcp"]["applied"]) == 2


def test_sync_cleanup_removed_dependency(tmp_path: Path) -> None:
    """Test that removing a dependency from agpack.yml cleans up its files."""
    bare_repo = _create_bare_repo(tmp_path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # First sync with skill + command
    config = {
        "targets": ["claude"],
        "dependencies": {
            "skills": [
                {
                    "url": str(bare_repo),
                    "path": "skills/my-skill",
                },
            ],
            "commands": [
                {
                    "url": str(bare_repo),
                    "path": "commands/review.md",
                },
            ],
        },
    }

    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    # Verify files exist
    assert (project_dir / ".claude/skills/my-skill/SKILL.md").exists()
    assert (project_dir / ".claude/commands/review.md").exists()

    # Remove the skill from config, keep command
    config["dependencies"]["skills"] = []
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    # Second sync
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    # Skill should be cleaned up
    assert not (project_dir / ".claude/skills/my-skill/SKILL.md").exists()
    assert not (project_dir / ".claude/skills/my-skill").exists()

    # Command should still be there
    assert (project_dir / ".claude/commands/review.md").exists()


def test_sync_dry_run(tmp_path: Path) -> None:
    """Test that --dry-run doesn't create any files."""
    bare_repo = _create_bare_repo(tmp_path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    config = {
        "targets": ["claude"],
        "dependencies": {
            "skills": [
                {
                    "url": str(bare_repo),
                    "path": "skills/my-skill",
                },
            ],
        },
    }

    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path), "--dry-run"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    # No files should have been created
    assert not (project_dir / ".claude").exists()
    assert not (project_dir / ".agpack.lock.yml").exists()


def test_status_command(tmp_path: Path) -> None:
    """Test the status command output."""
    bare_repo = _create_bare_repo(tmp_path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    config = {
        "targets": ["claude"],
        "dependencies": {
            "skills": [
                {
                    "url": str(bare_repo),
                    "path": "skills/my-skill",
                },
            ],
            "commands": [
                {
                    "url": str(bare_repo),
                    "path": "commands/review.md",
                },
            ],
        },
    }

    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()

    # Status before sync — nothing synced
    result = runner.invoke(
        main,
        ["status", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "not yet synced" in result.output

    # Sync
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    # Status after sync — everything synced
    result = runner.invoke(
        main,
        ["status", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    # Should show checkmarks, not "not yet synced"
    assert "✓" in result.output


def test_init_command(tmp_path: Path) -> None:
    """Test the init command with --config."""
    runner = CliRunner()

    config_path = tmp_path / "agpack.yml"

    result = runner.invoke(
        main,
        ["init", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "Created" in result.output

    assert config_path.exists()

    content = config_path.read_text()
    assert "targets:" in content
    assert "dependencies:" in content

    # Running again should be a no-op
    result = runner.invoke(
        main,
        ["init", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert "already exists" in result.output


def test_sync_mcp_cleanup(tmp_path: Path) -> None:
    """Test that removing an MCP server from config cleans it from target files."""
    _create_bare_repo(tmp_path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    config = {
        "targets": ["claude"],
        "dependencies": {
            "mcp": [
                {
                    "key": "mcpServers.filesystem",
                    "value": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                    },
                },
                {
                    "key": "mcpServers.other-server",
                    "value": {"command": "node", "args": ["server.js"]},
                },
            ],
        },
    }

    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    mcp_data = json.loads((project_dir / ".mcp.json").read_text())
    assert "filesystem" in mcp_data["mcpServers"]
    assert "other-server" in mcp_data["mcpServers"]

    config["dependencies"]["mcp"] = [
        {
            "key": "mcpServers.other-server",
            "value": {"command": "node", "args": ["server.js"]},
        }
    ]
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    # Re-sync
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    # filesystem should be removed, other-server should remain
    mcp_data = json.loads((project_dir / ".mcp.json").read_text())
    assert "filesystem" not in mcp_data["mcpServers"]
    assert "other-server" in mcp_data["mcpServers"]


# ---------------------------------------------------------------------------
# Global config integration tests
# ---------------------------------------------------------------------------


def test_sync_with_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Global config dependencies are included in sync."""
    bare_repo = _create_bare_repo(tmp_path)

    # Set up global config
    global_dir = tmp_path / "global_config"
    global_dir.mkdir()
    global_config = {
        "dependencies": {
            "agents": [
                {
                    "url": str(bare_repo),
                    "path": "agents/backend-expert.md",
                },
            ],
        },
    }
    global_path = global_dir / "agpack.yml"
    global_path.write_text(
        yaml.dump(global_config, default_flow_style=False, sort_keys=False)
    )
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(global_path))

    # Set up project directory with skills only
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config = {
        "targets": ["claude"],
        "dependencies": {
            "skills": [
                {
                    "url": str(bare_repo),
                    "path": "skills/my-skill",
                },
            ],
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    # Project skill deployed
    assert (project_dir / ".claude/skills/my-skill/SKILL.md").exists()
    # Global agent deployed
    assert (project_dir / ".claude/agents/backend-expert.md").exists()


def test_sync_no_global_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--no-global flag prevents global config from being loaded."""
    bare_repo = _create_bare_repo(tmp_path)

    # Set up global config with an agent
    global_dir = tmp_path / "global_config"
    global_dir.mkdir()
    global_config = {
        "dependencies": {
            "agents": [
                {
                    "url": str(bare_repo),
                    "path": "agents/backend-expert.md",
                },
            ],
        },
    }
    global_path = global_dir / "agpack.yml"
    global_path.write_text(
        yaml.dump(global_config, default_flow_style=False, sort_keys=False)
    )
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(global_path))

    # Project with skills only
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config = {
        "targets": ["claude"],
        "dependencies": {
            "skills": [
                {
                    "url": str(bare_repo),
                    "path": "skills/my-skill",
                },
            ],
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path), "--no-global"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    # Project skill deployed
    assert (project_dir / ".claude/skills/my-skill/SKILL.md").exists()
    # Global agent NOT deployed
    assert not (project_dir / ".claude/agents/backend-expert.md").exists()


def test_sync_global_false_in_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'global: false' in project config prevents global config from loading."""
    bare_repo = _create_bare_repo(tmp_path)

    # Set up global config with an agent
    global_dir = tmp_path / "global_config"
    global_dir.mkdir()
    global_config = {
        "dependencies": {
            "agents": [
                {
                    "url": str(bare_repo),
                    "path": "agents/backend-expert.md",
                },
            ],
        },
    }
    global_path = global_dir / "agpack.yml"
    global_path.write_text(
        yaml.dump(global_config, default_flow_style=False, sort_keys=False)
    )
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(global_path))

    # Project config with global: false
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_text = f"""\
global: false
targets:
  - claude
dependencies:
  skills:
    - url: {bare_repo}
      path: skills/my-skill
"""
    config_path = project_dir / "agpack.yml"
    config_path.write_text(config_text)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    # Project skill deployed
    assert (project_dir / ".claude/skills/my-skill/SKILL.md").exists()
    # Global agent NOT deployed
    assert not (project_dir / ".claude/agents/backend-expert.md").exists()


def test_sync_global_mcp_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Global MCP servers are merged and deployed alongside project ones."""
    _create_bare_repo(tmp_path)

    # Global config with an MCP server
    global_dir = tmp_path / "global_config"
    global_dir.mkdir()
    global_config = {
        "dependencies": {
            "mcp": [
                {
                    "key": "mcpServers.global-server",
                    "value": {"command": "node", "args": ["global.js"]},
                },
            ],
        },
    }
    global_path = global_dir / "agpack.yml"
    global_path.write_text(
        yaml.dump(global_config, default_flow_style=False, sort_keys=False)
    )
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(global_path))

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config = {
        "targets": ["claude"],
        "dependencies": {
            "mcp": [
                {
                    "key": "mcpServers.project-server",
                    "value": {"command": "npx", "args": ["-y", "project-pkg"]},
                },
            ],
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    mcp_data = json.loads((project_dir / ".mcp.json").read_text())
    assert "project-server" in mcp_data["mcpServers"]
    assert "global-server" in mcp_data["mcpServers"]


def test_sync_global_mcp_project_wins_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When project and global define the same MCP server name, project wins."""
    _create_bare_repo(tmp_path)

    global_dir = tmp_path / "global_config"
    global_dir.mkdir()
    global_config = {
        "dependencies": {
            "mcp": [
                {
                    "key": "mcpServers.shared",
                    "value": {"command": "node", "args": ["global-version.js"]},
                },
            ],
        },
    }
    global_path = global_dir / "agpack.yml"
    global_path.write_text(
        yaml.dump(global_config, default_flow_style=False, sort_keys=False)
    )
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(global_path))

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config = {
        "targets": ["claude"],
        "dependencies": {
            "mcp": [
                {
                    "key": "mcpServers.shared",
                    "value": {"command": "npx", "args": ["project-version"]},
                },
            ],
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    mcp_data = json.loads((project_dir / ".mcp.json").read_text())
    assert mcp_data["mcpServers"]["shared"]["command"] == "npx"
    assert mcp_data["mcpServers"]["shared"]["args"] == ["project-version"]


def test_init_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 'agpack init --global' scaffolds global config."""
    global_path = tmp_path / "agpack.yml"
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(global_path))

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--global"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "Created" in result.output
    assert global_path.exists()

    content = global_path.read_text()
    assert "dependencies:" in content
    assert "skills:" in content
    assert "mcp:" in content
    # Should NOT have project-specific top-level fields
    assert "name: my-project" not in content
    assert "\nversion:" not in content
    assert "\ntargets:" not in content


def test_init_global_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 'agpack init --global' when file already exists."""
    global_path = tmp_path / "agpack.yml"
    global_path.write_text("existing content\n")
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(global_path))

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--global"],
        catch_exceptions=False,
    )
    assert "already exists" in result.output
    assert global_path.read_text() == "existing content\n"


def test_status_with_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status command includes global config dependencies."""
    bare_repo = _create_bare_repo(tmp_path)

    # Global config with an agent
    global_dir = tmp_path / "global_config"
    global_dir.mkdir()
    global_config = {
        "dependencies": {
            "agents": [
                {
                    "url": str(bare_repo),
                    "path": "agents/backend-expert.md",
                },
            ],
        },
    }
    global_path = global_dir / "agpack.yml"
    global_path.write_text(
        yaml.dump(global_config, default_flow_style=False, sort_keys=False)
    )
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(global_path))

    # Project with skills only
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config = {
        "targets": ["claude"],
        "dependencies": {
            "skills": [
                {
                    "url": str(bare_repo),
                    "path": "skills/my-skill",
                },
            ],
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()

    # Status should show both project + global deps
    result = runner.invoke(
        main,
        ["status", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "my-skill" in result.output
    assert "backend-expert" in result.output


def test_status_no_global_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Status --no-global excludes global config."""
    bare_repo = _create_bare_repo(tmp_path)

    global_dir = tmp_path / "global_config"
    global_dir.mkdir()
    global_config = {
        "dependencies": {
            "agents": [
                {
                    "url": str(bare_repo),
                    "path": "agents/backend-expert.md",
                },
            ],
        },
    }
    global_path = global_dir / "agpack.yml"
    global_path.write_text(
        yaml.dump(global_config, default_flow_style=False, sort_keys=False)
    )
    monkeypatch.setenv("AGPACK_GLOBAL_CONFIG", str(global_path))

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config = {
        "targets": ["claude"],
        "dependencies": {
            "skills": [
                {
                    "url": str(bare_repo),
                    "path": "skills/my-skill",
                },
            ],
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["status", "--config", str(config_path), "--no-global"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert "my-skill" in result.output
    assert "backend-expert" not in result.output


# ---------------------------------------------------------------------------
# Alt URL fallback integration tests
# ---------------------------------------------------------------------------


def test_sync_url_fallback(tmp_path: Path) -> None:
    """When first URL is invalid, second URL is used to clone."""
    bare_repo = _create_bare_repo(tmp_path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config = {
        "targets": ["claude"],
        "dependencies": {
            "skills": [
                {
                    "url": [
                        "https://invalid.example.com/nonexistent/repo",
                        str(bare_repo),
                    ],
                    "path": "skills/my-skill",
                },
            ],
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path), "--no-global"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert (project_dir / ".claude/skills/my-skill/SKILL.md").exists()


def test_sync_detect_failure_writes_partial_lockfile(tmp_path: Path) -> None:
    """When detect_fn raises mid-sync, a partial lockfile is written."""
    from unittest.mock import patch

    bare_repo = _create_bare_repo(tmp_path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Two skills: first will succeed, second will have detect fail
    config = {
        "targets": ["claude"],
        "dependencies": {
            "skills": [
                {
                    "url": str(bare_repo),
                    "path": "skills/my-skill",
                },
            ],
            "commands": [
                {
                    "url": str(bare_repo),
                    "path": "commands/review.md",
                },
            ],
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    # First sync succeeds, establishing a lockfile
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path), "--no-global"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    # Now make item detection raise on the commands pass
    def failing_detect(fetch_result, layout, resource_type):  # noqa: ARG001
        if resource_type == "commands":
            raise RuntimeError("detection failed")
        return [(fetch_result.source.name, fetch_result.local_path)]

    with patch("agpack.cli.detect_items", side_effect=failing_detect):
        result = runner.invoke(
            main,
            ["sync", "--config", str(config_path), "--no-global"],
        )

    assert result.exit_code != 0
    assert "detection failed" in result.output

    # Partial lockfile should still exist (the skills that succeeded are preserved)
    lockfile_path = project_dir / ".agpack.lock.yml"
    assert lockfile_path.exists()
    lockfile = yaml.safe_load(lockfile_path.read_text())
    # The skill sync succeeded before commands failed
    assert len(lockfile["installed"]) >= 1


def test_sync_mcp_failure_writes_partial_lockfile(tmp_path: Path) -> None:
    """When deploy_mcp_servers raises EditFileError, partial lockfile is written."""
    from unittest.mock import patch

    bare_repo = _create_bare_repo(tmp_path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    config = {
        "targets": ["claude"],
        "dependencies": {
            "skills": [
                {
                    "url": str(bare_repo),
                    "path": "skills/my-skill",
                },
            ],
            "mcp": [
                {
                    "key": "mcpServers.bad-server",
                    "value": {"command": "npx", "args": ["-y", "bad-server"]},
                },
            ],
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    with patch(
        "agpack.cli.apply_patches_to_targets",
        side_effect=EditFileError("corrupt config file"),
    ):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["sync", "--config", str(config_path), "--no-global"],
        )

    assert result.exit_code != 0
    assert "corrupt config file" in result.output

    # Partial lockfile should exist with the successfully synced skill.
    lockfile_path = project_dir / ".agpack.lock.yml"
    assert lockfile_path.exists()
    lockfile = yaml.safe_load(lockfile_path.read_text())
    assert len(lockfile["installed"]) == 1
    assert lockfile["installed"][0]["type"] == "skills"
    assert lockfile.get("edits", []) == []


def test_sync_with_claude_hooks_and_permissions(tmp_path: Path) -> None:
    """End-to-end: hooks + permissions resources deploy realistic
    Claude Code settings.json content via the patch model."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    config = {
        "targets": ["claude"],
        "dependencies": {
            "hooks": [
                {
                    "key": "${bucket}.PreToolUse",
                    "strategy": "append",
                    "value": {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                # $${} escapes — Claude Code resolves this
                                # at hook execution time, not agpack.
                                "command": (
                                    "$${CLAUDE_PROJECT_DIR}/.claude/hooks/block.sh"
                                ),
                            }
                        ],
                    },
                },
            ],
            "permissions": [
                {
                    "key": "${bucket}.allow",
                    "strategy": "append",
                    "value": "Read(/etc/**)",
                },
            ],
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path), "--no-global"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    settings = json.loads((project_dir / ".claude/settings.json").read_text())
    # ${bucket} resolved to "hooks" and "permissions" respectively;
    # the runtime variable inside the command was preserved literally.
    assert settings["hooks"]["PreToolUse"] == [
        {
            "matcher": "Bash",
            "hooks": [
                {
                    "type": "command",
                    "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/block.sh",
                }
            ],
        }
    ]
    assert settings["permissions"]["allow"] == ["Read(/etc/**)"]


def test_sync_with_target_definitions_overriding_builtin(tmp_path: Path) -> None:
    """A target_definitions entry fully replaces the built-in of the same name."""
    bare_repo = _create_bare_repo(tmp_path)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    config = {
        "targets": ["claude"],
        "dependencies": {
            "skills": [{"url": str(bare_repo), "path": "skills/my-skill"}],
        },
        "target_definitions": {
            "claude": {
                "skills": {"kind": "copy-directory", "path": ".my-claude/skills"},
            },
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path), "--no-global"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    # Files land at the overridden path, not at the built-in .claude/skills
    assert (project_dir / ".my-claude/skills/my-skill/SKILL.md").exists()
    assert not (project_dir / ".claude/skills").exists()


def test_sync_with_brand_new_custom_target(tmp_path: Path) -> None:
    """A target name absent from built-ins is resolved from target_definitions."""
    bare_repo = _create_bare_repo(tmp_path)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    config = {
        "targets": ["my-internal-tool"],
        "dependencies": {
            "skills": [{"url": str(bare_repo), "path": "skills/my-skill"}],
            "mcp": [
                {
                    "key": "mcpServers.filesystem",
                    "value": {"command": "npx", "args": ["-y", "fs"]},
                },
            ],
        },
        "target_definitions": {
            "my-internal-tool": {
                "skills": {"kind": "copy-directory", "path": ".myaitool/skills"},
                "mcp": {
                    "kind": "edit-file",
                    "path": ".myaitool/config.json",
                },
            },
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path), "--no-global"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert (project_dir / ".myaitool/skills/my-skill/SKILL.md").exists()
    mcp_cfg = json.loads((project_dir / ".myaitool/config.json").read_text())
    assert "filesystem" in mcp_cfg["mcpServers"]


def test_targets_list_shows_all_builtins(tmp_path: Path) -> None:
    """`agpack targets list` shows the eight built-in targets."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["targets", "list", "--config", str(project_dir / "agpack.yml"), "--no-global"],
    )
    assert result.exit_code == 0, result.output

    for name in [
        "claude",
        "opencode",
        "codex",
        "cursor",
        "copilot",
        "gemini",
        "windsurf",
        "antigravity",
    ]:
        assert name in result.output


def test_targets_list_marks_user_override(tmp_path: Path) -> None:
    """User-defined entries that share a name with a built-in are flagged."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(
            {
                "targets": ["claude"],
                "target_definitions": {
                    "claude": {
                        "skills": {
                            "kind": "copy-directory",
                            "path": ".my-claude/skills",
                        },
                    },
                    "my-tool": {
                        "skills": {
                            "kind": "copy-directory",
                            "path": ".my-tool/skills",
                        },
                    },
                },
            },
            default_flow_style=False,
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["targets", "list", "--config", str(config_path), "--no-global"],
    )
    assert result.exit_code == 0, result.output
    assert "overrides built-in" in result.output
    assert "my-tool" in result.output


def test_targets_show_prints_yaml_for_builtin(tmp_path: Path) -> None:
    """`agpack targets show <name>` prints a valid manifest as YAML."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "targets",
            "show",
            "claude",
            "--config",
            str(project_dir / "agpack.yml"),
            "--no-global",
        ],
    )
    assert result.exit_code == 0, result.output

    # Output must be valid YAML that parses back into a TargetDef
    parsed = parse_target_def(yaml.safe_load(result.output))
    assert parsed.resources["skills"].path == ".claude/skills"


def test_targets_show_uses_user_definition(tmp_path: Path) -> None:
    """A user definition shadows the built-in when shown."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(
            {
                "targets": ["claude"],
                "target_definitions": {
                    "claude": {
                        "skills": {
                            "kind": "copy-directory",
                            "path": ".my-claude/skills",
                        },
                    },
                },
            },
            default_flow_style=False,
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["targets", "show", "claude", "--config", str(config_path), "--no-global"],
    )
    assert result.exit_code == 0, result.output

    parsed = yaml.safe_load(result.output)
    assert parsed["skills"]["path"] == ".my-claude/skills"
    # User definition has no mcp block — the built-in's mcp must NOT leak in
    assert "mcp" not in parsed


def test_targets_show_unknown_name_errors(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "targets",
            "show",
            "bogus-tool",
            "--config",
            str(project_dir / "agpack.yml"),
            "--no-global",
        ],
    )
    assert result.exit_code != 0
    assert "Unknown target 'bogus-tool'" in result.output


def test_init_template_parses_when_uncommented(tmp_path: Path) -> None:
    """The scaffolded agpack.yml must parse without errors after `init`."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_path = project_dir / "agpack.yml"

    runner = CliRunner()
    init_result = runner.invoke(main, ["init", "--config", str(config_path)])
    assert init_result.exit_code == 0, init_result.output
    assert config_path.exists()

    # The scaffold has only commented entries — load should report missing
    # targets, since 'targets' is required.
    with pytest.raises(Exception, match="targets"):
        load_config(config_path)


def test_sync_unknown_target_lists_options_in_error(tmp_path: Path) -> None:
    """An unknown target name surfaces a CLI error mentioning both pools."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(
            {"targets": ["bogus-tool"], "dependencies": {}},
            default_flow_style=False,
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path), "--no-global"],
    )

    assert result.exit_code != 0
    assert "Unknown target 'bogus-tool'" in result.output
    assert "Built-in targets:" in result.output
    assert "target_definitions" in result.output


def test_sync_url_multiple_fallbacks(tmp_path: Path) -> None:
    """Multiple URLs: first two invalid, third valid."""
    bare_repo = _create_bare_repo(tmp_path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config = {
        "targets": ["claude"],
        "dependencies": {
            "skills": [
                {
                    "url": [
                        "https://invalid1.example.com/repo",
                        "https://invalid2.example.com/repo",
                        str(bare_repo),
                    ],
                    "path": "skills/my-skill",
                },
            ],
        },
    }
    config_path = project_dir / "agpack.yml"
    config_path.write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False)
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path), "--no-global"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert (project_dir / ".claude/skills/my-skill/SKILL.md").exists()
