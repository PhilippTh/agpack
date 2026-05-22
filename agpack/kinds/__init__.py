"""The three asset kinds agpack knows how to deploy.

Each kind owns its own dataclass + behaviour in its own submodule (``copy_directory``, ``copy_file``, ``edit_file``).
This package's ``__init__`` re-exports the resource classes and the two union aliases for callers that want them
together. Domain types that are *not* kind behaviour (the patch model, the exception hierarchy) live elsewhere —
:mod:`agpack.patch` and :mod:`agpack.errors` — so importing one kind module doesn't drag those in.
"""

from agpack.kinds.copy_directory import CopyDirectoryResource
from agpack.kinds.copy_file import CopyFileResource
from agpack.kinds.edit_file import EditFileResource
from agpack.kinds.edit_file import infer_config_format

ResourceDef = CopyDirectoryResource | CopyFileResource | EditFileResource
CopyResource = CopyDirectoryResource | CopyFileResource

__all__ = [
    "CopyDirectoryResource",
    "CopyFileResource",
    "CopyResource",
    "EditFileResource",
    "ResourceDef",
    "infer_config_format",
]
