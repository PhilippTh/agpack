"""Target directory mapping constants for each supported AI coding tool."""

from __future__ import annotations

from dataclasses import dataclass

# Recognised target names
VALID_TARGETS = frozenset(
    {
        "claude",
        "opencode",
        "codex",
        "cursor",
        "copilot",
        "gemini",
        "windsurf",
        "antigravity",
    }
)

# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

SKILL_DIRS: dict[str, str] = {
    "claude": ".claude/skills",
    "opencode": ".opencode/skills",
    "codex": ".agents/skills",
    "cursor": ".cursor/skills",
    "copilot": ".github/skills",
    "gemini": ".gemini/skills",
    "windsurf": ".windsurf/skills",
    "antigravity": ".gemini/skills",  # Antigravity shares the .gemini/ namespace
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

# codex, cursor, and windsurf do not support commands
COMMAND_DIRS: dict[str, str] = {
    "claude": ".claude/commands",
    "opencode": ".opencode/commands",
    "copilot": ".github/prompts",
    "gemini": ".gemini/commands",
    "antigravity": ".gemini/commands",  # shares .gemini/ namespace
}

# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

# codex, gemini, antigravity, and windsurf do not support agents
AGENT_DIRS: dict[str, str] = {
    "claude": ".claude/agents",
    "opencode": ".opencode/agents",
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


# windsurf: MCP config is global (~/.codeium/windsurf/mcp_config.json), not per-project
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
    "gemini": McpTargetConfig(
        config_path=".gemini/settings.json",
        format="json",
        servers_key="mcpServers",
    ),
    "antigravity": McpTargetConfig(  # shares .gemini/ namespace
        config_path=".gemini/settings.json",
        format="json",
        servers_key="mcpServers",
    ),
}
