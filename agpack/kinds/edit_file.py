"""``kind: edit-file`` — surgically patch a structured config file.

Reads a JSON or TOML file, applies a list of :class:`Patch` operations (``replace`` or ``append``), and writes it
back. The diff-based :meth:`EditFileResource.sync_patches` is what makes this safe across syncs:

* TOML files round-trip through :mod:`tomlkit` so comments, key ordering, and whitespace on untouched sections
  survive unchanged.
* JSON files use :mod:`json` (no format-preserving alternative in stdlib); ``write_if_changed`` skips the write
  entirely when the serialised text is byte-identical to disk, so unchanged files don't churn either.
* Cleanup is symmetric with copy-kind cleanup: a removed ``replace`` patch deletes the key (just like a removed
  copy file is unlinked), and a removed ``append`` patch removes the previously-appended list entry. agpack does
  not try to remember what existed at a key before it first applied a ``replace`` — that value was overwritten when
  the patch was first applied, the same way ``cp`` overwrites an existing file.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import ClassVar
from typing import Literal

import tomlkit
from tomlkit.exceptions import TOMLKitError

from agpack.display import console
from agpack.kinds._shared import EditFileError
from agpack.kinds._shared import write_if_changed
from agpack.lockfile import AppliedPatch

# ---------------------------------------------------------------------------
# Format inference
# ---------------------------------------------------------------------------


_FORMAT_BY_SUFFIX = {".json": "json", ".toml": "toml"}


def infer_config_format(path: str) -> Literal["json", "toml"]:
    """Return the format for an edit-file config path.

    The extension is the single source of truth — there is no override.
    """
    lower = path.lower()
    if lower.endswith(".toml"):
        return "toml"
    if lower.endswith(".json"):
        return "json"
    valid = ", ".join(sorted(_FORMAT_BY_SUFFIX))
    msg = f"cannot infer config format from '{path}' — path must end in one of: {valid}"
    raise EditFileError(msg)


# ---------------------------------------------------------------------------
# ${name} substitution
# ---------------------------------------------------------------------------


# Matches either ``$$`` (escape — emit literal ``$``) or ``${name}`` (substitute). ``$${name}`` therefore writes a
# literal ``${name}`` to the target file, which lets users pass through runtime variables resolved by the consuming
# tool (e.g. Claude Code's ``${CLAUDE_PROJECT_DIR}`` inside hook commands).
_VAR_PATTERN = re.compile(r"\$\$|\$\{([^}]+)}")


def _substitute(value: str, table: dict[str, str], context: str) -> str:
    """Substitute ``${name}`` references in ``value`` using ``table``.

    ``$$`` writes a literal ``$`` (so ``$${X}`` produces ``${X}``). Raises :class:`EditFileError` listing the missing
    name and the surrounding context if a ``${name}`` reference cannot be resolved.
    """

    def _replace(match: re.Match[str]) -> str:
        if match.group(0) == "$$":
            return "$"
        name = match.group(1)
        if name in table:
            return table[name]
        msg = (
            f"{context}: variable '{name}' is not defined "
            f"(checked target vars and environment). "
            f"Use $${{{name}}} to write a literal ${{{name}}}."
        )
        raise EditFileError(msg)

    return _VAR_PATTERN.sub(_replace, value)


def _substitute_recursive(value: Any, table: dict[str, str], context: str) -> Any:
    """Walk a JSON-ish value substituting ``${name}`` in every string leaf."""
    if isinstance(value, str):
        return _substitute(value, table, context)
    if isinstance(value, dict):
        return {k: _substitute_recursive(v, table, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_recursive(v, table, context) for v in value]
    return value


# ---------------------------------------------------------------------------
# Patch model
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Dotted-path navigation
# ---------------------------------------------------------------------------


def _split_key(key: str) -> list[str]:
    """Split a dotted patch key into segments, honouring backslash escapes.

    ``.`` separates segments; ``\\.`` produces a literal dot inside a segment; ``\\\\`` produces a literal backslash.
    This lets users address keys that contain dots (e.g. MCP server names with dotted identifiers, Java package keys,
    hostnames):

    * ``mcpServers.foo``              → ``["mcpServers", "foo"]``
    * ``mcpServers.example\\.com/srv`` → ``["mcpServers", "example.com/srv"]``
    * ``a\\\\b.c``                    → ``["a\\b", "c"]``

    A trailing unescaped backslash is taken literally. Empty segments (``a..b``, ``.x``, ``x.``) are rejected so that
    ambiguous keys fail loudly instead of silently navigating into ``""``.
    """
    if not key:
        msg = "patch key must be non-empty"
        raise EditFileError(msg)

    segments: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(key):
        c = key[i]
        if c == "\\" and i + 1 < len(key):
            # Escape: emit the next character literally. Covers both ``\.`` (literal dot, no segment break) and ``\\``
            # (literal backslash).
            current.append(key[i + 1])
            i += 2
            continue
        if c == ".":
            segments.append("".join(current))
            current = []
            i += 1
            continue
        current.append(c)
        i += 1
    segments.append("".join(current))

    if any(not s for s in segments):
        msg = f"patch key {key!r} has an empty segment — use '\\.' to embed a literal dot inside a segment"
        raise EditFileError(msg)
    return segments


def _walk_to_parent(root: dict[str, Any], segments: list[str]) -> tuple[dict[str, Any], str]:
    """Walk ``root`` along ``segments[:-1]``, returning (parent, last_segment).

    Missing intermediate dicts are auto-created. If an existing intermediate value is *not* a dict, raises
    :class:`EditFileError` rather than silently overwriting user data.
    """
    parent: dict[str, Any] = root
    for seg in segments[:-1]:
        if seg in parent and not isinstance(parent[seg], dict):
            msg = f"patch path traverses non-dict at '{seg}': existing value is {type(parent[seg]).__name__}"
            raise EditFileError(msg)
        parent = parent.setdefault(seg, {})
    return parent, segments[-1]


def _walk_readonly(root: dict[str, Any], segments: list[str]) -> tuple[dict[str, Any] | None, str]:
    """Walk ``root`` along ``segments[:-1]`` without auto-creating anything.

    Returns ``(parent_dict, leaf_segment)`` if every intermediate segment resolved to a dict; ``(None, "")`` if any
    segment was missing or pointed at a non-dict. Used by every read/undo path where missing keys must silently no-op
    (cleanup, previous-value capture, undo).
    """
    parent: Any = root
    for seg in segments[:-1]:
        if not isinstance(parent, dict) or seg not in parent:
            return None, ""
        parent = parent[seg]
    if not isinstance(parent, dict):
        return None, ""
    return parent, segments[-1]


def _apply_patch(root: dict[str, Any], patch: Patch) -> None:
    """Apply ``patch`` to ``root`` in-place."""
    segments = _split_key(patch.key)
    parent, leaf = _walk_to_parent(root, segments)

    if patch.strategy == "replace":
        parent[leaf] = patch.value
        return

    # append
    bucket = parent.setdefault(leaf, [])
    if not isinstance(bucket, list):
        msg = f"patch with strategy='append' targets non-list at '{patch.key}': got {type(bucket).__name__}"
        raise EditFileError(msg)
    bucket.append(patch.value)


def _undo_resolved(root: dict[str, Any], strategy: str, resolved_key: str, value_hash: str) -> bool:
    """Reverse one applied patch on ``root`` in-place.

    The caller pre-resolves ``${var}`` substitutions in the key; the lockfile stores keys unresolved (so secret
    interpolations never land on disk) and a SHA256 hash of the resolved value.

    ``replace`` deletes the leaf. ``append`` walks the list at the path, hashes each element, and removes the first
    hash-match. Silent no-op if the target is missing or already gone.
    """
    parent, leaf = _walk_readonly(root, _split_key(resolved_key))
    if parent is None or leaf not in parent:
        return False

    if strategy == "replace":
        del parent[leaf]
        return True

    bucket = parent[leaf]
    if not isinstance(bucket, list):
        return False
    for i, item in enumerate(bucket):
        if _value_hash(item) == value_hash:
            del bucket[i]
            return True
    return False


# ---------------------------------------------------------------------------
# Diff identity + tomlkit boundary helpers
# ---------------------------------------------------------------------------


def match_key(strategy: str, key: str, value_hash: str) -> tuple[Any, ...]:
    """Identity tuple for diffing patches across syncs.

    ``replace`` patches identify by ``(key,)`` — same key with a different value is still the same *slot* (an update).
    ``append`` patches identify by ``(key, value_hash)`` — different appended values are distinct list elements.

    Callers normalise to primitives before calling: pull ``strategy`` and ``key`` from the patch directly, and use
    :func:`_value_hash` on the resolved value for a :class:`Patch` or the stored ``value_hash`` for an
    :class:`AppliedPatch`.
    """
    if strategy == "replace":
        return ("replace", key)
    return ("append", key, value_hash)


def _value_hash(value: Any) -> str:
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


# ---------------------------------------------------------------------------
# JSON / TOML I/O
# ---------------------------------------------------------------------------


def _read_existing(config_path: Path, format_: str) -> dict[str, Any]:
    """Read an existing JSON/TOML config file, or return an empty dict.

    TOML files are parsed with :mod:`tomlkit` so comments, key ordering, and whitespace survive the round-trip. The
    returned :class:`tomlkit.TOMLDocument` behaves as a dict for navigation and mutation; ``tomlkit.dumps`` later
    re-emits the document preserving everything we didn't touch.

    JSON has no equivalent format-preserving parser in stdlib; canonicalization on write is unavoidable, but the
    :func:`_write_if_changed` guard at write time prevents no-op churn on unchanged content.
    """
    if not config_path.exists():
        return tomlkit.document() if format_ == "toml" else {}
    text = config_path.read_text(encoding="utf-8")
    try:
        data: Any
        data = json.loads(text) if format_ == "json" else tomlkit.parse(text)
    except (json.JSONDecodeError, TOMLKitError, OSError) as exc:
        msg = f"Failed to read {config_path}: {exc}"
        raise EditFileError(msg) from exc

    if not isinstance(data, dict):
        msg = f"{config_path}: top-level must be a mapping"
        raise EditFileError(msg)
    return data


def _dump(data: dict[str, Any], format_: str) -> str:
    """Serialise a dict back to JSON or TOML text.

    For TOML, ``data`` is always a :class:`TOMLDocument` because :func:`_read_existing` returns one (a fresh
    :func:`tomlkit.document` if the file didn't exist, otherwise the parsed document). ``tomlkit.dumps`` accepts any
    mapping, but passing the document we already have keeps comments, ordering, and whitespace intact on untouched
    sections.
    """
    if format_ == "json":
        return json.dumps(data, indent=2) + "\n"
    return tomlkit.dumps(data)


# ---------------------------------------------------------------------------
# EditFileResource
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EditFileResource:
    """Applies :class:`Patch` operations to a JSON or TOML config file.

    The file format is inferred from :attr:`path`'s extension. Patches are fully generic — agpack reads the file,
    applies each patch (replace or append), and writes it back atomically.

    :attr:`vars` is a mapping of substitution variables made available to every patch when this resource is the apply
    target. They are referenced as ``${name}`` in patch ``key`` strings and recursively in patch ``value`` strings.
    Target ``vars`` win over environment variables on name collision — the target manifest is the canonical source for
    per-target structure like bucket names.
    """

    path: str
    vars: dict[str, str] = field(default_factory=dict)
    kind: ClassVar[str] = "edit-file"

    @property
    def format(self) -> Literal["json", "toml"]:
        return infer_config_format(self.path)

    def apply_patches(
        self,
        patches: list[Patch],
        project_root: Path,
        env_vars: dict[str, str] | None = None,
        *,
        target_name: str = "",
        dry_run: bool = False,
        verbose: bool = False,
    ) -> list[AppliedPatch]:
        """Apply each patch to the config file at :attr:`path`.

        Thin wrapper around :meth:`sync_patches` with no prior state — every patch is treated as freshly added.
        *target_name* is stamped onto every resulting :class:`AppliedPatch` so cleanup can re-resolve ``${var}``
        references against the originating target's manifest.
        """
        return self.sync_patches(
            applied_old=[],
            desired_new=patches,
            project_root=project_root,
            env_vars=env_vars,
            target_name=target_name,
            dry_run=dry_run,
            verbose=verbose,
        )

    def _resolve_patch(self, patch: Patch, env_vars: dict[str, str]) -> Patch:
        """Return a new Patch with all ${name} references substituted.

        Target ``vars`` override env vars on collision.
        """
        table = {**env_vars, **self.vars}
        ctx = f"patch {self.path}:{patch.key}"
        return Patch(
            key=_substitute(patch.key, table, ctx),
            value=_substitute_recursive(patch.value, table, ctx),
            strategy=patch.strategy,
        )

    def cleanup_patches(
        self,
        patches: list[AppliedPatch],
        project_root: Path,
        env_vars: dict[str, str] | None = None,
        *,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        """Undo a list of previously-applied patches on the config file at :attr:`path`.

        Thin wrapper over :meth:`sync_patches` with empty ``desired_new``: every entry falls into the removes
        branch of the diff, which resolves the key via ``self.vars`` + *env_vars* and undoes the patch
        (``replace`` deletes the leaf; ``append`` removes the previously-appended list entry by hash match).

        Silent no-op if the file is missing — nothing to clean up.
        """
        if not patches:
            return
        if not (project_root / self.path).exists():
            return
        self.sync_patches(
            applied_old=patches,
            desired_new=[],
            project_root=project_root,
            env_vars=env_vars,
            dry_run=dry_run,
            verbose=verbose,
        )

    def sync_patches(  # noqa: C901  # three-way diff: matches/removes/adds + dry-run + format inference
        self,
        applied_old: list[AppliedPatch],
        desired_new: list[Patch],
        project_root: Path,
        env_vars: dict[str, str] | None = None,
        *,
        target_name: str = "",
        dry_run: bool = False,
        verbose: bool = False,
    ) -> list[AppliedPatch]:
        """Reconcile the file at :attr:`path` to match *desired_new*.

        The diff-based path that backs both ``apply`` and ``cleanup``:

        * Patches in *applied_old* but not in *desired_new* are undone (``replace`` deletes the leaf; ``append``
          removes the previously-appended list entry, located by value hash).
        * Patches in *desired_new* but not in *applied_old* are applied fresh.
        * Patches that appear in both with identical value hashes are left untouched — no file mutation, no churn.
        * For ``replace`` patches whose key matches but value hash differs, the file is updated to the new value.

        Diff identity uses the **unresolved** key (as the user wrote it in YAML) plus the value hash, so the diff is
        stable across syncs even when ``${var}`` resolution context changes. Resolution is only performed when
        navigating to the file on disk — to apply (forward) or undo (backward).

        Returns the list of :class:`AppliedPatch` records to write to the lockfile. Each one stores ``target_name``
        so cleanup later can re-resolve ``${var}`` references against the originating target's manifest.
        """
        env_vars = env_vars or {}

        # Resolve current desired patches.
        resolved_new = [self._resolve_patch(p, env_vars) for p in desired_new]

        # Catch two patches that resolve to the same identity (e.g. literal ``mcpServers.foo`` and ``${bucket}.foo``
        # with ``bucket=mcpServers``). Parse-time can't see this because target vars aren't known yet, so this is
        # the earliest moment the collision is detectable. Failing here also prevents the diff dict from silently
        # last-write-wins on collisions.
        seen: dict[tuple[Any, ...], int] = {}
        for i, p in enumerate(resolved_new):
            mk = match_key(p.strategy, p.key, _value_hash(p.value))
            if mk in seen:
                first = seen[mk]
                first_src = desired_new[first].key
                second_src = desired_new[i].key
                src_detail = "" if first_src == second_src else f" (unresolved keys: {first_src!r} and {second_src!r})"
                msg = (
                    f"{self.path}: patches at indices {first} and {i} resolve to the same identity "
                    f"(strategy={p.strategy}, key={p.key!r}){src_detail}."
                )
                raise EditFileError(msg)
            seen[mk] = i

        config_path = project_root / self.path

        if dry_run:
            if verbose:
                for p in resolved_new:
                    console.print(f"[dry-run]   {p.strategy} {self.path}:{p.key}")
            return [
                AppliedPatch(
                    file_path=self.path,
                    target_name=target_name,
                    key=src.key,
                    strategy=src.strategy,
                    value_hash=_value_hash(rp.value),
                )
                for src, rp in zip(desired_new, resolved_new, strict=True)
            ]

        if not applied_old and not resolved_new and not config_path.exists():
            return []

        try:
            format_ = self.format
        except EditFileError:
            # Stale lockfile pointing at an unknown extension — drop the old records.
            return []

        data = _read_existing(config_path, format_)

        # Diff identities use UNRESOLVED keys + value hashes — stable across var changes between syncs.
        old_by_match: dict[tuple[Any, ...], AppliedPatch] = {
            match_key(ap.strategy, ap.key, ap.value_hash): ap for ap in applied_old
        }
        new_by_match: dict[tuple[Any, ...], tuple[Patch, Patch]] = {}
        for src, rp in zip(desired_new, resolved_new, strict=True):
            new_by_match[match_key(src.strategy, src.key, _value_hash(rp.value))] = (src, rp)

        result: list[AppliedPatch] = []
        verbose_lines: list[str] = []
        table = {**env_vars, **self.vars}

        for mk in old_by_match.keys() & new_by_match.keys():
            ap = old_by_match[mk]
            src, rp = new_by_match[mk]
            new_hash = _value_hash(rp.value)
            if ap.value_hash == new_hash:
                # Unchanged — carry forward without touching the file.
                result.append(ap)
                continue
            # ``replace`` with same key, different value: apply the new value.
            _apply_patch(data, rp)
            result.append(
                AppliedPatch(
                    file_path=self.path,
                    target_name=target_name,
                    key=src.key,
                    strategy=src.strategy,
                    value_hash=new_hash,
                )
            )
            verbose_lines.append(f"  update {self.path}:{rp.key}")

        for mk in old_by_match.keys() - new_by_match.keys():
            ap = old_by_match[mk]
            try:
                rk = _substitute(ap.key, table, f"applied patch {self.path}:{ap.key}")
            except EditFileError:
                # Old patch referenced a var that's gone — best effort: skip the undo.
                continue
            if _undo_resolved(data, ap.strategy, rk, ap.value_hash):
                verbose_lines.append(f"  remove {self.path}:{rk}")

        for mk in new_by_match.keys() - old_by_match.keys():
            src, rp = new_by_match[mk]
            _apply_patch(data, rp)
            result.append(
                AppliedPatch(
                    file_path=self.path,
                    target_name=target_name,
                    key=src.key,
                    strategy=src.strategy,
                    value_hash=_value_hash(rp.value),
                )
            )
            verbose_lines.append(f"  {rp.strategy} {self.path}:{rp.key}")

        new_text = _dump(data, format_)
        try:
            wrote = write_if_changed(config_path, new_text)
        except (OSError, TypeError, ValueError) as exc:
            msg = f"Failed to write {config_path}: {exc}"
            raise EditFileError(msg) from exc

        if verbose and wrote:
            for line in verbose_lines:
                console.print(line)

        return result
