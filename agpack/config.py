"""agpack.yml parsing and validation."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml

from agpack.kinds import Patch
from agpack.target_schema import TargetDef
from agpack.target_schema import TargetSchemaError
from agpack.target_schema import parse_target_def

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_GLOBAL_CONFIG_DIR = Path.home() / ".config" / "agpack"

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DependencySource:
    """A parsed skill, command, or agent dependency (copy-kind input).

    The ``urls`` list contains one or more git clone URLs. The first entry is the canonical (primary) URL used for
    identity and display. Remaining entries are fallback URLs tried in order when earlier ones fail.
    """

    urls: list[str]
    path: str | None = None
    ref: str | None = None

    @property
    def url(self) -> str:
        """The primary (first) URL."""
        return self.urls[0]

    @property
    def name(self) -> str:
        """Derive the resource name (last path segment, or url basename)."""
        if self.path:
            return self.path.rstrip("/").rsplit("/", 1)[-1]
        cleaned = self.url.rstrip("/")
        if cleaned.endswith(".git"):
            cleaned = cleaned[:-4]
        return cleaned.rsplit("/", 1)[-1]

    @property
    def identity(self) -> str:
        """A unique key for this dependency (used for lockfile matching)."""
        key = self.url
        if self.path:
            key = f"{key}::{self.path}"
        return key


# A dependency entry under ``dependencies.<rt>`` is either a fetched resource (``DependencySource``) for copy kinds,
# or a structured patch (``Patch``) for edit-file kinds.
DependencyEntry = DependencySource | Patch


@dataclass
class AgpackConfig:
    """Parsed and validated agpack.yml.

    ``dependencies`` is an open dict keyed by resource type name. Each entry is either a :class:`DependencySource`
    (copy kinds — fetched from git) or a :class:`Patch` (edit-file kinds — applied to a structured config file). All
    entries under a given key must be of the same type; the actual kind is enforced at sync time against the target
    manifest.
    """

    targets: list[str]
    dependencies: dict[str, list[DependencyEntry]] = field(default_factory=dict)
    use_global: bool = True
    target_definitions: dict[str, TargetDef] = field(default_factory=dict)


@dataclass
class GlobalConfig:
    """Parsed global config (~/.config/agpack/agpack.yml).

    Contains only dependencies — no targets.
    """

    dependencies: dict[str, list[DependencyEntry]] = field(default_factory=dict)
    target_definitions: dict[str, TargetDef] = field(default_factory=dict)
    config_dir: Path = field(default_factory=lambda: DEFAULT_GLOBAL_CONFIG_DIR)
    """Directory containing the global config (used to locate .env)."""


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when agpack.yml is invalid."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_fetch_entry(raw: dict[str, Any], context: str) -> DependencySource:
    """Parse a fetch (copy-kind) dependency entry: ``url`` (+ optional path/ref)."""
    raw_url = raw.get("url")
    if isinstance(raw_url, str):
        if not raw_url:
            msg = f"{context}: 'url' must not be empty"
            raise ConfigError(msg)
        urls = [raw_url]
    elif isinstance(raw_url, list):
        if not raw_url:
            msg = f"{context}: 'url' must not be empty"
            raise ConfigError(msg)
        urls = [str(u) for u in raw_url]
    else:
        msg = f"{context}: 'url' must be a string or list of strings"
        raise ConfigError(msg)

    path = raw.get("path")
    if path is not None and not isinstance(path, str):
        msg = f"{context}: 'path' must be a string"
        raise ConfigError(msg)

    ref = raw.get("ref")
    if ref is not None:
        ref = str(ref)

    known = {"url", "path", "ref"}
    extra = set(raw) - known
    if extra:
        msg = f"{context}: unknown fields {sorted(extra)}"
        raise ConfigError(msg)

    return DependencySource(urls=urls, path=path, ref=ref)


_VALID_STRATEGIES = ("replace", "append")


def _parse_patch_entry(raw: dict[str, Any], context: str) -> Patch:
    """Parse an edit-file entry: ``key``, ``value``, optional ``strategy``."""
    key = raw.get("key")
    if not isinstance(key, str) or not key:
        msg = f"{context}: 'key' must be a non-empty string"
        raise ConfigError(msg)

    if "value" not in raw:
        msg = f"{context}: missing required field 'value'"
        raise ConfigError(msg)

    strategy = raw.get("strategy", "replace")
    if strategy not in _VALID_STRATEGIES:
        msg = f"{context}: 'strategy' must be one of {_VALID_STRATEGIES}, got {strategy!r}"
        raise ConfigError(msg)

    known = {"key", "value", "strategy"}
    extra = set(raw) - known
    if extra:
        msg = f"{context}: unknown fields {sorted(extra)}"
        raise ConfigError(msg)

    return Patch(key=key, value=raw["value"], strategy=strategy)


def _parse_dependency_entry(raw: Any, context: str) -> DependencyEntry:
    """Parse one entry; the shape decides fetch vs patch.

    An entry with ``url`` is a fetch entry (copy kind). An entry with ``key`` is a patch entry (edit-file kind).
    Anything else is an error.
    """
    if not isinstance(raw, dict):
        msg = f"{context}: expected an object, got {type(raw).__name__}"
        raise ConfigError(msg)
    has_url = "url" in raw
    has_key = "key" in raw
    if has_url and has_key:
        msg = f"{context}: entry has both 'url' (fetch) and 'key' (patch) — these are mutually exclusive"
        raise ConfigError(msg)
    if has_url:
        return _parse_fetch_entry(raw, context)
    if has_key:
        return _parse_patch_entry(raw, context)
    msg = f"{context}: entry must have either 'url' (fetch) or 'key' (patch)"
    raise ConfigError(msg)


def _parse_target_definitions(raw: Any, prefix: str = "") -> dict[str, TargetDef]:
    """Parse a ``target_definitions`` mapping into TargetDef objects."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        msg = f"{prefix}target_definitions: must be a mapping, got {type(raw).__name__}"
        raise ConfigError(msg)

    result: dict[str, TargetDef] = {}
    for key, value in raw.items():
        context = f"{prefix}target_definitions.{key}"
        if not isinstance(key, str) or not key:
            msg = f"{context}: target name must be a non-empty string"
            raise ConfigError(msg)
        try:
            result[key] = parse_target_def(value, name=key, context=context)
        except TargetSchemaError as exc:
            raise ConfigError(str(exc)) from exc

    return result


def _parse_dependencies(deps: dict[str, Any], prefix: str = "") -> dict[str, list[DependencyEntry]]:
    """Parse the ``dependencies`` mapping into a {resource_type: [entries]} dict.

    Each entry is either a :class:`DependencySource` (fetch) or a :class:`Patch` (patch) depending on whether it has
    ``url:`` or ``key:``. Mixed lists are rejected — a resource type is either fetch-only or patch-only.

    Patch duplicates are *not* rejected here; they're caught at apply time in
    :meth:`agpack.kinds.edit_file.EditFileResource.sync_patches`, where the resolved keys (post ``${var}``
    substitution) are known.
    """
    out: dict[str, list[DependencyEntry]] = {}
    for rt, raw_list in deps.items():
        if not isinstance(rt, str) or not rt:
            msg = f"{prefix}dependencies: keys must be non-empty strings, got {rt!r}"
            raise ConfigError(msg)
        items = raw_list or []
        entries: list[DependencyEntry] = [
            _parse_dependency_entry(item, f"{prefix}dependencies.{rt}[{i}]") for i, item in enumerate(items)
        ]
        # Reject mixed fetch + patch within one resource type.
        if entries:
            first_kind = type(entries[0])
            for i, entry in enumerate(entries[1:], 1):
                if type(entry) is not first_kind:
                    msg = (
                        f"{prefix}dependencies.{rt}[{i}]: cannot mix fetch and "
                        f"patch entries under the same resource type"
                    )
                    raise ConfigError(msg)
        out[rt] = entries
    return out


def load_config(path: Path) -> AgpackConfig:
    """Load and validate agpack.yml."""
    if not path.exists():
        msg = f"Config file not found: {path}"
        raise ConfigError(msg)

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"Failed to parse YAML: {exc}"
        raise ConfigError(msg) from exc

    if not isinstance(data, dict):
        msg = "Config file must be a YAML mapping"
        raise ConfigError(msg)

    targets = data.get("targets")
    if not targets or not isinstance(targets, list):
        msg = "Missing or invalid 'targets' (must be a list)"
        raise ConfigError(msg)

    for t in targets:
        if not isinstance(t, str) or not t:
            msg = f"'targets' entries must be non-empty strings, got {t!r}"
            raise ConfigError(msg)

    use_global = data.get("global", True)
    if not isinstance(use_global, bool):
        msg = "'global' must be true or false"
        raise ConfigError(msg)

    deps = data.get("dependencies", {})
    if not isinstance(deps, dict):
        msg = "'dependencies' must be a mapping"
        raise ConfigError(msg)

    dependencies = _parse_dependencies(deps)
    target_definitions = _parse_target_definitions(data.get("target_definitions"))

    return AgpackConfig(
        targets=targets,
        dependencies=dependencies,
        use_global=use_global,
        target_definitions=target_definitions,
    )


def resolve_global_config_path() -> Path:
    """Return the global config file path.

    Respects the ``AGPACK_GLOBAL_CONFIG`` environment variable.
    """
    override = os.environ.get("AGPACK_GLOBAL_CONFIG")
    if override:
        return Path(override).resolve()
    return DEFAULT_GLOBAL_CONFIG_DIR / "agpack.yml"


def load_global_config(path: Path | None = None) -> GlobalConfig | None:
    """Load the global agpack config (or None if missing)."""
    if path is None:
        path = resolve_global_config_path()

    if not path.exists():
        return None

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"Failed to parse global config YAML: {exc}"
        raise ConfigError(msg) from exc

    if data is None:
        return GlobalConfig(config_dir=path.parent)

    if not isinstance(data, dict):
        msg = "Global config file must be a YAML mapping"
        raise ConfigError(msg)

    deps = data.get("dependencies", {})
    if not isinstance(deps, dict):
        msg = "Global config 'dependencies' must be a mapping"
        raise ConfigError(msg)

    dependencies = _parse_dependencies(deps, prefix="global ")
    target_definitions = _parse_target_definitions(data.get("target_definitions"), prefix="global ")

    return GlobalConfig(
        dependencies=dependencies,
        target_definitions=target_definitions,
        config_dir=path.parent,
    )


def merge_configs(project: AgpackConfig, global_cfg: GlobalConfig) -> AgpackConfig:
    """Merge a global config into a project config.

    Global dependencies are appended after project dependencies. Fetch entries are deduplicated by
    :attr:`DependencySource.identity`; patch entries are deduplicated by ``(key, value, strategy)`` content. Project
    entries always win on conflict.

    Returns a **new** :class:`AgpackConfig`; the inputs are not mutated.
    """
    dependencies: dict[str, list[DependencyEntry]] = {rt: list(deps) for rt, deps in project.dependencies.items()}
    for rt, global_deps in global_cfg.dependencies.items():
        bucket = dependencies.setdefault(rt, [])
        seen: set[Any] = {_dedup_key(e) for e in bucket}
        for dep in global_deps:
            k = _dedup_key(dep)
            if k not in seen:
                bucket.append(dep)
                seen.add(k)

    target_definitions = dict(project.target_definitions)
    for name, target in global_cfg.target_definitions.items():
        if name not in target_definitions:
            target_definitions[name] = target

    return AgpackConfig(
        targets=project.targets,
        dependencies=dependencies,
        use_global=project.use_global,
        target_definitions=target_definitions,
    )


def _dedup_key(entry: DependencyEntry) -> Any:
    """Return a hashable identity for an entry, for dedup during merge.

    Fetch entries are deduped by url+path. Patch entries depend on
    strategy:

    - ``replace``: deduped by ``key`` — two patches that both set the
      same key are duplicates (project wins).
    - ``append``: deduped by ``(key, value)`` — two appends at the
      same key with different values are *distinct* entries.
    """
    if isinstance(entry, DependencySource):
        return ("fetch", entry.identity)
    if entry.strategy == "replace":
        return ("patch", "replace", entry.key)
    # ``default=str`` covers any oddballs that aren't natively JSON-encodable (datetimes, custom scalars from YAML).
    # The output is a deterministic string identity for dict-key use, not something we round-trip.
    value_repr = json.dumps(entry.value, sort_keys=True, default=str)
    return ("patch", "append", entry.key, value_repr)
