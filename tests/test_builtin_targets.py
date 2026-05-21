"""Regression tests for the eight shipped built-in target manifests.

These assertions lock in the bug fixes baked into the YAML manifests so a
typo or accidental edit can't silently re-introduce a known-bad path or
encoding.
"""

from __future__ import annotations

from agpack.kinds import CopyDirectoryResource
from agpack.kinds import CopyFileResource
from agpack.kinds import EditFileResource
from agpack.registry import load_builtin


def _mcp(target):  # type: ignore[no-untyped-def]
    """Return the target's edit-file resource (i.e. its MCP block), or None."""
    res = target.resources.get("mcp")
    return res if isinstance(res, EditFileResource) else None


def test_claude_paths_and_mcp() -> None:
    target = load_builtin("claude")
    assert target.resources["skills"].path == ".claude/skills"
    assert isinstance(target.resources["skills"], CopyDirectoryResource)
    assert target.resources["commands"].path == ".claude/commands"
    assert isinstance(target.resources["commands"], CopyFileResource)
    assert target.resources["agents"].path == ".claude/agents"

    mcp = _mcp(target)
    assert mcp is not None
    assert mcp.path == ".mcp.json"
    assert mcp.format == "json"
    assert mcp.merge.servers_key == "mcpServers"
    assert mcp.merge.transports["stdio"].type_value is None
    assert mcp.merge.transports["http"].type_value == "http"
    assert mcp.merge.transports["sse"].type_value == "sse"


def test_opencode_quirks() -> None:
    target = load_builtin("opencode")
    assert target.resources["skills"].path == ".opencode/skills"
    assert target.resources["commands"].path == ".opencode/commands"
    assert target.resources["agents"].path == ".opencode/agents"

    mcp = _mcp(target)
    assert mcp is not None
    assert mcp.path == "opencode.json"
    assert mcp.merge.servers_key == "mcp"
    assert mcp.merge.defaults == {"$schema": "https://opencode.ai/config.json"}

    stdio = mcp.merge.transports["stdio"]
    assert stdio.type_value == "local"
    assert stdio.command_format == "array"
    assert stdio.env_key == "environment"

    assert mcp.merge.transports["http"].type_value == "remote"
    assert mcp.merge.transports["sse"].type_value == "remote"


def test_codex_bug_fix_skills_path_and_agents_added() -> None:
    target = load_builtin("codex")
    # Bug fix: skills now under .codex/, not .agents/
    assert target.resources["skills"].path == ".codex/skills"
    # Bug fix: agents now supported (copy-file kind)
    assert isinstance(target.resources["agents"], CopyFileResource)
    assert target.resources["agents"].path == ".codex/agents"
    # Codex does not support project-level commands
    assert "commands" not in target.resources

    mcp = _mcp(target)
    assert mcp is not None
    assert mcp.format == "toml"
    assert mcp.merge.servers_key == "mcp_servers"
    # No explicit type field for any transport (presence of command/url infers)
    assert mcp.merge.transports["stdio"].type_value is None
    http = mcp.merge.transports["http"]
    assert http.type_value is None
    assert http.headers_key == "http_headers"


def test_cursor_bug_fix_removes_agents_adds_commands() -> None:
    target = load_builtin("cursor")
    assert target.resources["skills"].path == ".cursor/skills"
    # Bug fix: commands path now exists
    assert target.resources["commands"].path == ".cursor/commands"
    # Bug fix: .cursor/agents/ was fictional; not present
    assert "agents" not in target.resources

    mcp = _mcp(target)
    assert mcp is not None
    assert mcp.path == ".cursor/mcp.json"
    assert mcp.merge.servers_key == "mcpServers"


def test_copilot_paths_and_explicit_stdio_type() -> None:
    target = load_builtin("copilot")
    assert target.resources["skills"].path == ".github/skills"
    assert target.resources["commands"].path == ".github/prompts"
    assert target.resources["agents"].path == ".github/agents"

    mcp = _mcp(target)
    assert mcp is not None
    assert mcp.path == ".vscode/mcp.json"
    assert mcp.merge.servers_key == "servers"
    # Copilot/VS Code requires explicit "type": "stdio"
    assert mcp.merge.transports["stdio"].type_value == "stdio"
    assert mcp.merge.transports["http"].type_value == "http"


def test_gemini_bug_fix_mcp_encoding() -> None:
    target = load_builtin("gemini")
    assert target.resources["skills"].path == ".gemini/skills"
    assert target.resources["commands"].path == ".gemini/commands"
    assert "agents" not in target.resources

    mcp = _mcp(target)
    assert mcp is not None
    assert mcp.path == ".gemini/settings.json"
    assert mcp.merge.servers_key == "mcpServers"
    # Bug fix: no type field anywhere; transport inferred from field presence
    assert mcp.merge.transports["stdio"].type_value is None
    # Bug fix: Streamable HTTP uses httpUrl, not url
    assert mcp.merge.transports["http"].type_value is None
    assert mcp.merge.transports["http"].url_key == "httpUrl"
    # SSE keeps the default url
    assert mcp.merge.transports["sse"].type_value is None
    assert mcp.merge.transports["sse"].url_key == "url"


def test_windsurf_workflows_added_no_mcp() -> None:
    target = load_builtin("windsurf")
    assert target.resources["skills"].path == ".windsurf/skills"
    # Added: workflows mapped under the "commands" resource type
    assert target.resources["commands"].path == ".windsurf/workflows"
    assert "agents" not in target.resources
    # Windsurf MCP is global-only — not managed here
    assert "mcp" not in target.resources


def test_antigravity_own_namespace_no_mcp() -> None:
    target = load_builtin("antigravity")
    # Antigravity uses the singular .agent/ workspace namespace
    assert target.resources["skills"].path == ".agent/skills"
    assert target.resources["commands"].path == ".agent/workflows"
    assert "agents" not in target.resources
    # Antigravity MCP is global-only
    assert "mcp" not in target.resources
