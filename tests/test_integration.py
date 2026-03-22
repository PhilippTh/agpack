"""End-to-end integration test using a local bare git repo."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml
from click.testing import CliRunner

from agpack.cli import main


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
        "name": "test-project",
        "version": "1.0.0",
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
                    "name": "filesystem",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                },
            ],
        },
    }

    config_path = project_dir / "agpack.yml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))

    # Run sync
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path), "--verbose"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, f"sync failed:\n{result.output}"
    assert "1 skills, 1 commands, 1 agents, 1 MCP servers" in result.output

    # Verify skill files
    for target_dir in [".claude/skills/my-skill", ".opencode/skills/my-skill"]:
        skill_md = project_dir / target_dir / "SKILL.md"
        assert skill_md.exists(), f"Missing {skill_md}"
        assert "My Skill" in skill_md.read_text()

        util_py = project_dir / target_dir / "helpers" / "util.py"
        assert util_py.exists(), f"Missing {util_py}"

    # Verify command files
    assert (project_dir / ".claude/commands/review.md").exists()
    assert (project_dir / ".opencode/commands/review.md").exists()

    # Verify agent files
    assert (project_dir / ".claude/agents/backend-expert.md").exists()
    assert (project_dir / ".opencode/agents/backend-expert.md").exists()

    # Verify MCP configs
    claude_mcp = json.loads((project_dir / ".mcp.json").read_text())
    assert "filesystem" in claude_mcp["mcpServers"]
    assert claude_mcp["mcpServers"]["filesystem"]["command"] == "npx"

    opencode_mcp = json.loads((project_dir / "opencode.json").read_text())
    assert "filesystem" in opencode_mcp["mcp"]

    # Verify lockfile
    lockfile_path = project_dir / ".agpack.lock.yml"
    assert lockfile_path.exists()
    lockfile = yaml.safe_load(lockfile_path.read_text())
    assert len(lockfile["installed"]) == 3
    assert len(lockfile["mcp"]) == 1
    assert lockfile["mcp"][0]["name"] == "filesystem"


def test_sync_cleanup_removed_dependency(tmp_path: Path) -> None:
    """Test that removing a dependency from agpack.yml cleans up its files."""
    bare_repo = _create_bare_repo(tmp_path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # First sync with skill + command
    config = {
        "name": "test-project",
        "version": "1.0.0",
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
    config_path.write_text(yaml.dump(config, default_flow_style=False))

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
    config_path.write_text(yaml.dump(config, default_flow_style=False))

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
        "name": "test-project",
        "version": "1.0.0",
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
    config_path.write_text(yaml.dump(config, default_flow_style=False))

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
        "name": "test-project",
        "version": "1.0.0",
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
    config_path.write_text(yaml.dump(config, default_flow_style=False))

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
    """Test the init command."""
    runner = CliRunner()

    import os

    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)

        result = runner.invoke(main, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Created" in result.output

        config_path = tmp_path / "agpack.yml"
        assert config_path.exists()

        content = config_path.read_text()
        assert "name:" in content
        assert "targets:" in content
        assert "dependencies:" in content

        # Running again should be a no-op
        result = runner.invoke(main, ["init"], catch_exceptions=False)
        assert "already exists" in result.output
    finally:
        os.chdir(old_cwd)


def test_sync_mcp_cleanup(tmp_path: Path) -> None:
    """Test that removing an MCP server from config cleans it from target files."""
    _create_bare_repo(tmp_path)

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    config = {
        "name": "test-project",
        "version": "1.0.0",
        "targets": ["claude"],
        "dependencies": {
            "mcp": [
                {
                    "name": "filesystem",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                },
                {
                    "name": "other-server",
                    "command": "node",
                    "args": ["server.js"],
                },
            ],
        },
    }

    config_path = project_dir / "agpack.yml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sync", "--config", str(config_path)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0

    # Verify both servers exist
    mcp_data = json.loads((project_dir / ".mcp.json").read_text())
    assert "filesystem" in mcp_data["mcpServers"]
    assert "other-server" in mcp_data["mcpServers"]

    # Remove filesystem from config
    config["dependencies"]["mcp"] = [
        {"name": "other-server", "command": "node", "args": ["server.js"]}
    ]
    config_path.write_text(yaml.dump(config, default_flow_style=False))

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
