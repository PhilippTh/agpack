"""Command asset handler."""

from __future__ import annotations

from agpack.assets.base import AssetHandler
from agpack.targets import COMMAND_DIRS


class CommandHandler(AssetHandler):
    """Handler for command file assets."""

    resource_type = "command"
    target_dirs = COMMAND_DIRS
