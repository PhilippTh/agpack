"""Target directory mapping constants for each supported AI coding tool."""

from __future__ import annotations

from dataclasses import dataclass

# Recognised target names
VALID_TARGETS = frozenset({"claude", "opencode", "codex", "cursor", "copilot"})

# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

SKILL_DIRS: dict[str, str] = {
    "claude": ".claude/skills",
    "opencode": ".opencode/skills",
    "codex": ".agents/skills",
    "cursor": ".cursor/skills",
    "copilot": ".github/skills",
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

COMMAND_DIRS: dict[str, str] = {
    "claude": ".claude/commands",
    "opencode": ".opencode/commands",
    # codex and cursor do not support commands
    "copilot": ".github/prompts",
}

# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

AGENT_DIRS: dict[str, str] = {
    "claude": ".claude/agents",
    "opencode": ".opencode/agents",
    # codex does not support agents
    "cursor": ".cursor/agents",
    "copilot": ".github/agents",
}

# ---------------------------------------------------------------------------
# MCP config files
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class McpTargetConfig:
    """Describes how a target stores MCP server definitions."""

    config_path: str
    format: str  # "json" or "toml"
    servers_key: str  # top-level key in the config that holds the servers dict


MCP_TARGETS: dict[str, McpTargetConfig] = {
    "claude": McpTargetConfig(
        config_path=".mcp.json",
        format="json",
        servers_key="mcpServers",
    ),
    "opencode": McpTargetConfig(
        config_path="opencode.json",
        format="json",
        servers_key="mcp",
    ),
    "codex": McpTargetConfig(
        config_path=".codex/config.toml",
        format="toml",
        servers_key="mcp_servers",
    ),
    "cursor": McpTargetConfig(
        config_path=".cursor/mcp.json",
        format="json",
        servers_key="mcpServers",
    ),
    "copilot": McpTargetConfig(
        config_path=".vscode/mcp.json",
        format="json",
        servers_key="servers",
    ),
}
