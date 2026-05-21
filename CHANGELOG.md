# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Three asset kinds are now first-class in the manifest schema.**
  Every resource block declares ``kind:`` — one of ``copy-directory``,
  ``copy-file``, or ``edit-file`` — instead of the implicit
  ``layout: directory|file`` (for copy resources) + reserved ``mcp:``
  branch (for the edit-file case). Behavior:
  - `mcp:` is no longer a special top-level key; it's a regular
    resource entry whose ``kind`` is ``edit-file``. ``servers_key`` /
    ``defaults`` / ``transports`` move under a nested ``merge:`` block.
  - The old ``layout: directory|file`` form is rejected with an error
    pointing to the matching ``kind:`` value.
  - Per-kind deploy and cleanup logic lives on the kind classes
    themselves (in `agpack.kinds`); the deployer is now a thin
    orchestration layer.

  Migration: in any custom `target_definitions:` you maintain, replace
  ``layout: directory`` → ``kind: copy-directory``, ``layout: file`` →
  ``kind: copy-file``, and wrap the `mcp:` block's encoder fields under
  a ``merge:`` key (plus ``kind: edit-file`` on the block itself).

### Removed

- **`format:` field on MCP manifests.** The config format (`json` /
  `toml`) is now inferred from the file extension of `path`; the
  manifest can no longer declare it. Built-in targets and user
  `target_definitions:` should drop `format:`. A manifest that still
  sets it errors out with a clear "drop this field" message. Lockfile
  entries also drop the field (read silently ignores legacy `format`
  keys for back-compat).

### Changed

- **Resource taxonomy is now open.** `skills`, `commands`, and `agents`
  are no longer hard-coded into the schema — any string can be used as
  a resource type name in both `agpack.yml`'s `dependencies:` block
  and a target manifest's resource entries. Users with tools that need
  a different resource category (e.g. `rules`, `prompts`, `personas`)
  can now declare it directly without forking agpack.
- **`detect_items` is layout-driven.** Detection now branches on the
  `layout: directory|file` declared in the manifest instead of
  dispatching on the resource type name. As a consequence, a single
  resource type must use the same layout across every target that
  supports it; agpack rejects mismatched layouts at sync time with a
  clear error.
- **`AgpackConfig.dependencies` is now a dict.** The named fields
  `skills` / `commands` / `agents` on `AgpackConfig` and `GlobalConfig`
  are replaced by a single `dependencies: dict[str, list[…]]` keyed
  by resource type name. External callers reading these fields must
  update to `config.dependencies["skills"]` etc.
- **Lockfile `type` field stores the resource type verbatim.**
  Previously stored as singular (`skill` / `command` / `agent`); now
  stored as it appears in `agpack.yml` (`skills` / `commands` /
  `agents` / or any user-defined name). Legacy singulars are remapped
  on read for back-compat — existing lockfiles continue to clean up
  correctly without manual migration.

## [0.3.1] - 2026-04-05

### Added

- Multiple URLs per dependency: `url:` accepts a list, tried in order
  (e.g. HTTPS primary with SSH fallback). The first URL is canonical
  for identity and display.

## [0.3.0] - 2026-04-05

### Added

- Global config at `~/.config/agpack/agpack.yml` for dependencies and
  MCP servers shared across projects.
- Per-project opt-out via `global: false` in `agpack.yml`, or
  `agpack sync --no-global` from the CLI.
- Project dependencies and MCP servers win on conflict when merged
  with the global config.

## [0.2.1] - 2026-03-26

### Changed

- Improved progress / status styling.

## [0.2.0] - 2026-03-26

### Added

- Folder-of-skills support: a dependency `path` pointing at a directory
  of subfolders expands to one skill per subfolder.

## [0.1.7] - 2026-03-26

### Fixed

- Added a proper timeout for `git fetch` operations and disabled
  interactive git prompts so a stalled clone can no longer hang sync
  indefinitely (#2).

## [0.1.6] - 2026-03-25

### Added

- `${VAR}` env-variable substitution in MCP server `env:` and other
  config values, resolved from `.env` or the shell environment.

## [0.1.5] - 2026-03-23

### Added

- MCP server config merging into each tool's project-level config file.

### Changed

- User-feedback improvements during sync (status / progress output).

### Fixed

- Type-check failures and CI configuration.

[Unreleased]: https://github.com/PhilippTh/agpack/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/PhilippTh/agpack/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/PhilippTh/agpack/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/PhilippTh/agpack/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/PhilippTh/agpack/compare/v0.1.7...v0.2.0
[0.1.7]: https://github.com/PhilippTh/agpack/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/PhilippTh/agpack/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/PhilippTh/agpack/releases/tag/v0.1.5
