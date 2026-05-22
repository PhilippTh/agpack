"""The three asset kinds agpack knows how to deploy.

A *kind* is the fundamental way agpack interacts with the filesystem:

* :class:`CopyDirectoryResource` (``kind: copy-directory``) — copy a
  directory tree from a fetched git repo into ``<path>/<name>/`` on
  the target. Used by skill bundles.
* :class:`CopyFileResource` (``kind: copy-file``) — copy individual
  files from a fetched git repo into ``<path>/<name>`` on the target.
  Used by commands and agents.
* :class:`EditFileResource` (``kind: edit-file``) — read a structured
  (JSON / TOML) config file, apply :class:`Patch` operations, write
  it back. Patches are fully generic — a list of
  ``{key, value, strategy}`` triples that the engine applies without
  any per-domain knowledge.

Each kind owns its own ``detect`` (where applicable), ``deploy_*``,
and ``cleanup_*`` logic in its own submodule; the deployer and CLI
orchestrate but never branch on kind themselves.

This module re-exports only the *public* surface. Private helpers
(``_apply_patch``, ``_atomic_write``, etc.) live in their submodules
and should be imported from there directly when needed (e.g. tests).
"""

from agpack.kinds._shared import DeployError
from agpack.kinds._shared import EditFileError
from agpack.kinds.copy_directory import CopyDirectoryResource
from agpack.kinds.copy_file import CopyFileResource
from agpack.kinds.edit_file import EditFileResource
from agpack.kinds.edit_file import Patch
from agpack.kinds.edit_file import Strategy
from agpack.kinds.edit_file import infer_config_format

ResourceDef = CopyDirectoryResource | CopyFileResource | EditFileResource
CopyResource = CopyDirectoryResource | CopyFileResource

__all__ = [
    "CopyDirectoryResource",
    "CopyFileResource",
    "CopyResource",
    "DeployError",
    "EditFileError",
    "EditFileResource",
    "Patch",
    "ResourceDef",
    "Strategy",
    "infer_config_format",
]
