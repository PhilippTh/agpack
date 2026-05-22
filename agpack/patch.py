"""The patch data model — shared between config parsing and edit-file deployment.

A :class:`Patch` is a top-level domain type: it appears in ``agpack.yml`` (parsed by :mod:`agpack.config`), it gets
applied to a structured config file (by :mod:`agpack.kinds.edit_file`), and a hash of its resolved value is recorded
in the lockfile. Keeping it in its own leaf module means neither parsing nor application has to import the other.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from typing import Literal

Strategy = Literal["replace", "append"]


@dataclass(frozen=True)
class Patch:
    """A single change to apply to a structured config file.

    Attributes:
        key: Dotted path into the config file (``mcpServers.filesystem``
            or ``hooks.PreToolUse``). Intermediate dicts are auto-created.
        value: What to put at the path. For ``append``, this is a single
            element appended to the list at ``key``.
        strategy: ``"replace"`` overwrites whatever's at the path;
            ``"append"`` requires the path to resolve to a list (created
            empty if absent) and appends ``value``.
    """

    key: str
    value: Any
    strategy: Strategy = "replace"


def match_key(strategy: str, key: str, value_hash: str) -> tuple[Any, ...]:
    """Identity tuple for diffing patches across syncs.

    ``replace`` patches identify by ``(key,)`` — same key with a different value is still the same *slot* (an update).
    ``append`` patches identify by ``(key, value_hash)`` — different appended values are distinct list elements.

    Callers normalise to primitives before calling: pull ``strategy`` and ``key`` from the patch directly, and use
    :func:`_value_hash` on the resolved value for a :class:`Patch` or the stored ``value_hash`` for an applied entry.
    """
    if strategy == "replace":
        return ("replace", key)
    return ("append", key, value_hash)


def value_hash(value: Any) -> str:
    """SHA256 of the canonical JSON form of *value* (post-:func:`_unwrap` of tomlkit wrappers).

    Used both as in-memory identity for ``append`` patches and as the lockfile's record of what was applied. Storing
    the hash rather than the value keeps secrets interpolated via ``${VAR}`` out of the lockfile.
    """
    canonical = json.dumps(_unwrap(value), sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unwrap(value: Any) -> Any:
    """Convert a tomlkit ``Item`` to its plain-Python equivalent.

    :meth:`tomlkit.items.Item.unwrap` already recurses through nested Tables and Arrays, so a single call returns a
    fully-plain dict / list / scalar. For values that are already plain (JSON-loaded data, lockfile values, primitives)
    this is a no-op. Used at the boundary between tomlkit-managed data and the lockfile / equality checks, which both
    want plain Python.
    """
    if hasattr(value, "unwrap"):
        try:
            return value.unwrap()
        except Exception:  # noqa: BLE001, S110  # defensive: any tomlkit wrapper failure falls through to the plain value
            pass
    return value
