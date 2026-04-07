"""Target directory mapping constants for each supported AI coding tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleTargetConfig:
    """Describes how a target consumes rule files."""

    strategy: Literal["append", "file"]
    """Either ``"append"`` (managed section in a shared file) or
    ``"file"`` (one generated file per rule)."""

    path: str
    """For ``append`` targets: the path to the shared file (e.g. ``AGENTS.md``).
    For ``file`` targets: the directory to place generated files in."""


# ---------------------------------------------------------------------------
# Ignore files
# ---------------------------------------------------------------------------

# Maps target name to the ignore file it uses.
# Only targets with a dedicated tool-specific ignore file are listed.
# Copilot and Codex use .gitignore which is skipped by default.
# OpenCode and Gemini/Antigravity have no dedicated ignore file.
IGNORE_FILES: dict[str, str] = {
    "claude": ".claudeignore",
    "cursor": ".cursorignore",
    "windsurf": ".codeiumignore",
}


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookConfigTarget:
    """Describes how a target stores hook configuration."""

    config_path: str
    hooks_key: str  # top-level key holding the hooks dict


HOOK_CONFIG_TARGETS: dict[str, HookConfigTarget] = {
    "claude": HookConfigTarget(
        config_path=".claude/settings.json",
        hooks_key="hooks",
    ),
    "cursor": HookConfigTarget(
        config_path=".cursor/hooks.json",
        hooks_key="hooks",
    ),
}

# Cursor copies hook scripts to .cursor/hooks/; Claude references them by path.
HOOK_SCRIPT_DIRS: dict[str, str] = {
    "cursor": ".cursor/hooks",
}

# Event name mapping: canonical (Claude) name → target-specific name.
# Claude uses its own event names as canonical; other targets translate.
HOOK_EVENT_MAP: dict[str, dict[str, str]] = {
    "cursor": {
        "PreToolUse": "beforeFileEdit",
        "PostToolUse": "afterFileEdit",
    },
}


def translate_hook_event(event: str, target: str) -> str:
    """Translate a canonical event name to the target-specific name."""
    mapping = HOOK_EVENT_MAP.get(target, {})
    return mapping.get(event, event)


RULE_TARGETS: dict[str, RuleTargetConfig] = {
    "claude": RuleTargetConfig(strategy="append", path="CLAUDE.md"),
    "codex": RuleTargetConfig(strategy="append", path="AGENTS.md"),
    "opencode": RuleTargetConfig(strategy="append", path="AGENTS.md"),
    "copilot": RuleTargetConfig(strategy="append", path="AGENTS.md"),
    "cursor": RuleTargetConfig(strategy="file", path=".cursor/rules"),
    "windsurf": RuleTargetConfig(strategy="file", path=".windsurf/rules"),
    "gemini": RuleTargetConfig(strategy="append", path=".gemini/GEMINI.md"),
    "antigravity": RuleTargetConfig(strategy="append", path=".gemini/GEMINI.md"),
}
