"""Regression tests for the shipped built-in target manifests.

Built-in manifests are intentionally tiny in the patch-based world:
each entry is just ``kind`` + ``path``. These assertions lock in
the file locations so a typo can't silently break a target.
"""

from __future__ import annotations

from agpack.kinds import CopyDirectoryResource
from agpack.kinds import CopyFileResource
from agpack.kinds import EditFileResource
from agpack.registry import load_builtin


def test_claude_paths() -> None:
    target = load_builtin("claude")
    assert isinstance(target.resources["skills"], CopyDirectoryResource)
    assert target.resources["skills"].path == ".claude/skills"
    assert isinstance(target.resources["commands"], CopyFileResource)
    assert target.resources["commands"].path == ".claude/commands"
    assert target.resources["agents"].path == ".claude/agents"
    mcp = target.resources["mcp"]
    assert isinstance(mcp, EditFileResource)
    assert mcp.path == ".mcp.json"
    settings = target.resources["settings"]
    assert isinstance(settings, EditFileResource)
    assert settings.path == ".claude/settings.json"


def test_opencode_paths() -> None:
    target = load_builtin("opencode")
    assert target.resources["skills"].path == ".opencode/skills"
    assert target.resources["commands"].path == ".opencode/commands"
    assert target.resources["agents"].path == ".opencode/agents"
    mcp = target.resources["mcp"]
    assert isinstance(mcp, EditFileResource)
    assert mcp.path == "opencode.json"


def test_codex_paths() -> None:
    target = load_builtin("codex")
    assert target.resources["skills"].path == ".codex/skills"
    assert isinstance(target.resources["agents"], CopyFileResource)
    assert target.resources["agents"].path == ".codex/agents"
    assert "commands" not in target.resources
    mcp = target.resources["mcp"]
    assert isinstance(mcp, EditFileResource)
    assert mcp.format == "toml"


def test_cursor_paths() -> None:
    target = load_builtin("cursor")
    assert target.resources["skills"].path == ".cursor/skills"
    assert target.resources["commands"].path == ".cursor/commands"
    assert "agents" not in target.resources
    mcp = target.resources["mcp"]
    assert isinstance(mcp, EditFileResource)
    assert mcp.path == ".cursor/mcp.json"


def test_copilot_paths() -> None:
    target = load_builtin("copilot")
    assert target.resources["skills"].path == ".github/skills"
    assert target.resources["commands"].path == ".github/prompts"
    assert target.resources["agents"].path == ".github/agents"
    mcp = target.resources["mcp"]
    assert isinstance(mcp, EditFileResource)
    assert mcp.path == ".vscode/mcp.json"


def test_gemini_paths() -> None:
    target = load_builtin("gemini")
    assert target.resources["skills"].path == ".gemini/skills"
    assert target.resources["commands"].path == ".gemini/commands"
    assert "agents" not in target.resources
    mcp = target.resources["mcp"]
    assert isinstance(mcp, EditFileResource)
    assert mcp.path == ".gemini/settings.json"


def test_windsurf_paths() -> None:
    target = load_builtin("windsurf")
    assert target.resources["skills"].path == ".windsurf/skills"
    assert target.resources["commands"].path == ".windsurf/workflows"
    assert "agents" not in target.resources
    assert "mcp" not in target.resources


def test_antigravity_paths() -> None:
    target = load_builtin("antigravity")
    assert target.resources["skills"].path == ".agent/skills"
    assert target.resources["commands"].path == ".agent/workflows"
    assert "agents" not in target.resources
    assert "mcp" not in target.resources
