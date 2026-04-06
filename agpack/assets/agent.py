"""Agent asset handler."""

from __future__ import annotations

from agpack.assets.base import AssetHandler
from agpack.targets import AGENT_DIRS


class AgentHandler(AssetHandler):
    """Handler for agent file assets."""

    resource_type = "agent"
    target_dirs = AGENT_DIRS
