"""The three asset kinds agpack knows how to deploy.

A *kind* is the fundamental way agpack interacts with the filesystem:

* :class:`CopyDirectoryResource` (``kind: copy-directory``) — copy a
  directory tree from a fetched git repo into ``<path>/<name>/`` on
  the target. Used by skill bundles.
* :class:`CopyFileResource` (``kind: copy-file``) — copy individual
  files from a fetched git repo into ``<path>/<name>`` on the target.
  Used by commands and agents.
* :class:`EditFileResource` (``kind: edit-file``) — read a structured
  (JSON / TOML) config file, merge entries in, write it back. Used by
  MCP servers. Encoder-specific config lives under :attr:`merge`; the
  only encoder shipped today is ``mcp-servers``.

Each kind owns its own ``detect`` (where applicable), ``deploy_*``, and
``cleanup_*`` logic; the deployer and CLI orchestrate but never branch
on kind themselves.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import tomllib
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar
from typing import Literal

import tomli_w

from agpack.display import console
from agpack.lockfile import McpTargetRef

if TYPE_CHECKING:
    from agpack.config import McpServer
    from agpack.fetcher import FetchResult

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DeployError(Exception):
    """Raised when a copy-kind deployment fails."""


class EditFileError(Exception):
    """Raised when an edit-file deployment or cleanup fails."""


# ---------------------------------------------------------------------------
# Format inference (used by edit-file)
# ---------------------------------------------------------------------------


_FORMAT_BY_SUFFIX = {".json": "json", ".toml": "toml"}


def infer_mcp_format(path: str) -> Literal["json", "toml"]:
    """Return the format for an MCP/edit-file config path.

    The extension is the single source of truth — there is no override.
    """
    lower = path.lower()
    if lower.endswith(".toml"):
        return "toml"
    if lower.endswith(".json"):
        return "json"
    valid = ", ".join(sorted(_FORMAT_BY_SUFFIX))
    raise EditFileError(
        f"cannot infer config format from '{path}' — "
        f"path must end in one of: {valid}"
    )


# ---------------------------------------------------------------------------
# Atomic-write primitives (shared by all kinds)
# ---------------------------------------------------------------------------


def _atomic_copy_file(src: Path, dst: Path) -> None:
    """Copy a file atomically using write-to-temp-then-rename."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dst.parent, prefix=".agpack-tmp-")
    try:
        os.close(fd)
        shutil.copy2(str(src), tmp_path)
        os.replace(tmp_path, str(dst))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write(path: Path, content: str) -> None:
    """Write text content to a file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".agpack-mcp-")
    try:
        os.close(fd)
        Path(tmp_path).write_text(content, encoding="utf-8")
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _copy_tree(src_dir: Path, dst_dir: Path) -> list[str]:
    """Recursively copy a directory; return absolute destination paths."""
    deployed: list[str] = []
    for src_file in sorted(src_dir.rglob("*")):
        if src_file.is_file():
            rel = src_file.relative_to(src_dir)
            if any(part.startswith(".git") for part in rel.parts):
                continue
            dst_file = dst_dir / rel
            _atomic_copy_file(src_file, dst_file)
            deployed.append(str(dst_file))
    return deployed


def _find_asset_subfolders(path: Path) -> list[Path]:
    """Return immediate subdirectories that contain at least one file."""
    subfolders: list[Path] = []
    for item in sorted(path.iterdir()):
        if item.is_dir() and not item.name.startswith(".git"):
            has_files = any(
                f.is_file()
                and not any(p.startswith(".git") for p in f.relative_to(item).parts)
                for f in item.rglob("*")
            )
            if has_files:
                subfolders.append(item)
    return subfolders


def _find_top_level_files(path: Path) -> list[Path]:
    """Return non-hidden files at the top level of a directory."""
    return sorted(
        item
        for item in path.iterdir()
        if item.is_file() and not item.name.startswith(".")
    )


# ---------------------------------------------------------------------------
# kind: copy-directory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CopyDirectoryResource:
    """Deploys items as directory bundles under ``<path>/<name>/``.

    A directory dependency with top-level files is treated as a single
    bundle; a directory containing only subdirectories expands to one
    bundle per subfolder.
    """

    path: str
    kind: ClassVar[str] = "copy-directory"

    def detect(
        self, fetch_result: FetchResult, label: str
    ) -> list[tuple[str, Path]]:
        local_path = fetch_result.local_path

        if local_path.is_dir() and not _find_top_level_files(local_path):
            subfolders = _find_asset_subfolders(local_path)
            if not subfolders:
                raise DeployError(
                    f"'{fetch_result.source.name}' is a directory but does not "
                    f"contain any {label} folders. Provide a path to a {label} "
                    f"folder or a directory containing {label} folders."
                )
            return [(sf.name, sf) for sf in subfolders]

        return [(fetch_result.source.name, local_path)]

    def deploy_item(
        self,
        item_name: str,
        src_path: Path,
        project_root: Path,
        *,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> list[str]:
        dst = project_root / self.path / item_name
        deployed: list[str] = []

        if dry_run:
            if src_path.is_dir():
                for f in sorted(src_path.rglob("*")):
                    if f.is_file() and not any(
                        p.startswith(".git")
                        for p in f.relative_to(src_path).parts
                    ):
                        rel = dst / f.relative_to(src_path)
                        deployed.append(str(rel.relative_to(project_root)))
            else:
                deployed.append(str((dst / src_path.name).relative_to(project_root)))
            if verbose:
                console.print(f"[dry-run]   copy {src_path} → {dst}")
            return deployed

        if src_path.is_dir():
            for copied in _copy_tree(src_path, dst):
                deployed.append(str(Path(copied).relative_to(project_root)))
        else:
            dst_file = dst / src_path.name
            _atomic_copy_file(src_path, dst_file)
            deployed.append(str(dst_file.relative_to(project_root)))

        return deployed


# ---------------------------------------------------------------------------
# kind: copy-file
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CopyFileResource:
    """Deploys items as individual files at ``<path>/<name>``."""

    path: str
    kind: ClassVar[str] = "copy-file"

    def detect(
        self, fetch_result: FetchResult, label: str
    ) -> list[tuple[str, Path]]:
        local_path = fetch_result.local_path

        if local_path.is_dir():
            files = _find_top_level_files(local_path)
            if not files:
                for sf in _find_asset_subfolders(local_path):
                    files.extend(_find_top_level_files(sf))
            if not files:
                article = "an" if label[0] in "aeiou" else "a"
                raise DeployError(
                    f"'{fetch_result.source.name}' is a directory but does not "
                    f"contain any {label} files. Provide a path to {article} "
                    f"{label} file or a directory containing {label} files."
                )
            return [(f.name, f) for f in files]

        return [(fetch_result.source.name, local_path)]

    def deploy_item(
        self,
        item_name: str,
        src_path: Path,
        project_root: Path,
        *,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> list[str]:
        dst = project_root / self.path / item_name

        if dry_run:
            if verbose:
                console.print(f"[dry-run]   copy → {dst}")
            return [str(dst.relative_to(project_root))]

        _atomic_copy_file(src_path, dst)
        return [str(dst.relative_to(project_root))]


# ---------------------------------------------------------------------------
# kind: edit-file
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransportSpec:
    """Encoding rules for one MCP transport (stdio / http / sse).

    Used by the ``mcp-servers`` encoder in :class:`MergeMcpServers`.
    All fields have sensible defaults so the most common case needs no
    configuration.
    """

    type_value: str | None = None
    type_field: str = "type"
    command_key: str = "command"
    command_format: Literal["string", "array"] = "string"
    args_key: str = "args"
    env_key: str = "env"
    url_key: str = "url"
    headers_key: str = "headers"


@dataclass(frozen=True)
class MergeMcpServers:
    """Encoder config for ``kind: edit-file`` with ``encoder: mcp-servers``.

    This is currently the only edit-file encoder agpack ships. Future
    encoders (e.g. for `.vscode/extensions.json` or `.editorconfig`)
    would add their own dataclass and slot in alongside.
    """

    servers_key: str
    defaults: dict[str, Any] = field(default_factory=dict)
    transports: dict[str, TransportSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class EditFileResource:
    """Merges structured entries into a JSON or TOML config file.

    The file format is inferred from the path extension. The
    :attr:`merge` field holds encoder-specific configuration — agpack
    currently only ships the ``mcp-servers`` encoder.
    """

    path: str
    merge: MergeMcpServers
    kind: ClassVar[str] = "edit-file"

    @property
    def format(self) -> Literal["json", "toml"]:
        return infer_mcp_format(self.path)

    def deploy_server(
        self,
        server: McpServer,
        project_root: Path,
        *,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> McpTargetRef | None:
        """Encode and merge one MCP server into this target's config.

        Returns the :class:`McpTargetRef` recorded for cleanup, or
        ``None`` if the server's transport isn't supported.
        """
        transport_spec = self.merge.transports.get(server.type)
        if transport_spec is None:
            return None

        server_obj = _encode_server(server, transport_spec)
        config_path = project_root / self.path
        ref = McpTargetRef(path=self.path, servers_key=self.merge.servers_key)

        if dry_run:
            if verbose:
                console.print(
                    f"[dry-run]   merge MCP '{server.name}' → {ref.path}"
                )
            return ref

        # _merge_into_config raises EditFileError on parse/structure issues;
        # OSError covers disk-full / permission / atomic-rename failures.
        try:
            _merge_into_config(self, config_path, {server.name: server_obj})
        except OSError as exc:
            raise EditFileError(
                f"Failed to write MCP config to {config_path}: {exc}"
            ) from exc

        if verbose:
            console.print(f"  MCP '{server.name}' → {ref.path}")
        return ref

    def cleanup_entry(
        self,
        entry_name: str,
        project_root: Path,
        *,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        """Remove ``entry_name`` from this config file, if present.

        Silently skips if the configured path's extension can't be
        mapped to a known format (a forgiving fallback for stale
        lockfile entries written by some future agpack that supported
        an extra format).
        """
        config_path = project_root / self.path
        if not config_path.exists():
            return

        try:
            format_ = self.format
        except EditFileError as exc:
            if verbose:
                console.print(
                    f"  skipping cleanup of '{entry_name}' from {self.path}: "
                    f"{exc}"
                )
            return

        if dry_run:
            if verbose:
                console.print(
                    f"[dry-run]   remove '{entry_name}' from {self.path}"
                )
            return

        _remove_entry(config_path, format_, self.merge.servers_key, entry_name)

        if verbose:
            console.print(f"  removed '{entry_name}' from {self.path}")


# ---------------------------------------------------------------------------
# Encoder helpers (mcp-servers)
# ---------------------------------------------------------------------------


def _encode_server(server: McpServer, spec: TransportSpec) -> dict[str, Any]:
    """Render a single MCP server entry per the transport spec."""
    obj: dict[str, Any] = {}

    if spec.type_value is not None:
        obj[spec.type_field] = spec.type_value

    if server.type == "stdio":
        if server.command is None:
            raise EditFileError(
                f"MCP server '{server.name}': stdio transport requires a command"
            )
        if spec.command_format == "array":
            obj[spec.command_key] = [server.command, *list(server.args)]
        else:
            obj[spec.command_key] = server.command
            if server.args:
                obj[spec.args_key] = list(server.args)
        if server.env:
            obj[spec.env_key] = dict(server.env)
    else:
        if server.url is None:
            raise EditFileError(
                f"MCP server '{server.name}': {server.type} transport requires a url"
            )
        obj[spec.url_key] = server.url

    return obj


def _read_existing(config_path: Path, format_: str) -> dict[str, Any]:
    """Read an existing JSON/TOML config file, or return an empty dict."""
    if not config_path.exists():
        return {}
    text = config_path.read_text(encoding="utf-8")
    try:
        data: Any
        if format_ == "json":
            data = json.loads(text)
        else:
            data = tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, OSError) as exc:
        raise EditFileError(f"Failed to read {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise EditFileError(f"{config_path}: top-level must be a mapping")
    return data


def _dump(data: dict[str, Any], format_: str) -> str:
    """Serialise a dict back to JSON or TOML text."""
    if format_ == "json":
        return json.dumps(data, indent=2) + "\n"
    return tomli_w.dumps(data)


def _merge_into_config(
    resource: EditFileResource,
    config_path: Path,
    servers: dict[str, dict[str, Any]],
) -> None:
    """Merge server entries into a target's MCP config file."""
    existing = _read_existing(config_path, resource.format)

    for key, value in resource.merge.defaults.items():
        existing.setdefault(key, value)

    bucket = existing.setdefault(resource.merge.servers_key, {})
    if not isinstance(bucket, dict):
        raise EditFileError(
            f"{config_path}: expected mapping at '{resource.merge.servers_key}', "
            f"got {type(bucket).__name__}"
        )
    bucket.update(servers)

    _atomic_write(config_path, _dump(existing, resource.format))


def _remove_entry(
    config_path: Path,
    format_: str,
    bucket_key: str,
    entry_name: str,
) -> None:
    """Remove a single entry from a config file's ``bucket_key`` mapping."""
    try:
        data = (
            json.loads(config_path.read_text(encoding="utf-8"))
            if format_ == "json"
            else tomllib.loads(config_path.read_text(encoding="utf-8"))
        )
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, OSError):
        return

    if not isinstance(data, dict):
        return

    bucket = data.get(bucket_key)
    if isinstance(bucket, dict) and entry_name in bucket:
        del bucket[entry_name]
        _atomic_write(config_path, _dump(data, format_))


# ---------------------------------------------------------------------------
# Type alias for any kind
# ---------------------------------------------------------------------------


ResourceDef = CopyDirectoryResource | CopyFileResource | EditFileResource
CopyResource = CopyDirectoryResource | CopyFileResource
