# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `agpack targets list` / `agpack targets show <name>` — inspect built-in
  and user-defined target manifests; `show` prints the on-disk YAML for
  built-ins and the user's raw YAML for project/global definitions.
- `target_definitions:` in `agpack.yml` and the global config — users can
  override a built-in target or define a brand-new one in YAML. Project
  entries take precedence over global; both replace the matching
  built-in entirely (no deep merge).
- Built-in target manifests under `agpack/builtin_targets/*.yml` — every
  per-tool quirk (paths, MCP key, format, transport encoding, opencode's
  array-form `command`, Copilot's explicit `type: stdio`, Gemini's
  `httpUrl`, Codex's `http_headers`, opencode's `environment`, …) now
  lives in YAML instead of Python.
- Verbose stderr warning when an MCP server matches no configured
  target (transport not supported, or the only configured targets have
  no `mcp` block).

### Changed

- **Schema is flat** — a target manifest's top-level keys are now
  `skills` / `commands` / `agents` / `mcp` directly; the previous
  `resources:` wrapper was removed. The `name:` and `description:`
  fields are gone too: the target name is its YAML filename (built-ins)
  or its key under `target_definitions:` (user manifests), and
  human-readable context belongs in YAML comments.
- **Lockfile MCP entries** now carry `{path, servers_key, format}` per
  config file (previously just the path). Cleanup no longer guesses
  the servers key — it reads it from the lockfile. Pre-0.4.0 string
  entries are read with `servers_key=""` and skipped on cleanup; the
  next sync rewrites the entry in the new format.
- `deploy_item(name, src, resource_type, targets, …)` is now the single
  file-deployment entrypoint (was `deploy_single_skill` /
  `deploy_single_command` / `deploy_single_agent`).
- `detect_items(fetch_result, resource_type)` is the single detection
  entrypoint (replacing `detect_skill_items` / `detect_command_items`
  / `detect_agent_items`).
- File deployment and MCP deployment now both live in `agpack.deployer`
  (the separate `agpack.mcp` module was folded in).
- `agpack targets show` prints YAML directly from the built-in file or
  the user's `agpack.yml` (no longer round-trips through a serializer).

### Fixed

- **Sync summary** no longer assumes a target's deployment path has
  exactly two components. User-defined targets can now use any path
  depth without inflating the "Copied N files" count.
- **Codex** skills moved from `.agents/skills/` to `.codex/skills/`,
  and `.codex/agents/` is now populated (TOML files).
- **Cursor** never had `.cursor/agents/`; that path is removed.
  `.cursor/commands/` is now populated.
- **Gemini** MCP no longer writes a `type:` field; HTTP servers use
  `httpUrl` instead of `url`.
- **Antigravity** uses its own `.agent/skills/` and `.agent/workflows/`
  namespaces (no longer sharing `.gemini/`).
- **Windsurf** populates `.windsurf/workflows/` from the `commands:`
  dependency type.
- **MCP cleanup** correctly uses each target's actual `servers_key`
  instead of falling back to a hard-coded list — important for
  user-defined targets removed between syncs.

### Removed

- `agpack.mcp` module (merged into `agpack.deployer`).
- `agpack.targets` module (replaced by YAML manifests + `agpack.registry`).
- `target_def_to_dict` serializer (~60 lines): `agpack targets show`
  now prints YAML directly from disk.
- `description:` field on target manifests — never displayed anywhere;
  use YAML comments to add human-readable context.
- `name:` field on target manifests — the manifest's name is its
  filename (built-ins) or mapping key (`target_definitions`).
- `_load_user_target_definitions` no longer swallows `ConfigError`:
  a broken `agpack.yml` now surfaces as an error from
  `agpack targets list` / `agpack targets show` instead of silently
  hiding user definitions.

### Migration notes

- **Lockfile**: 0.4.0 changes the on-disk MCP entry shape. Existing
  lockfiles still load; cleanup of MCP entries written by older agpack
  versions is skipped until the next sync rewrites them in the new
  format. No manual action required.
- **YAML schema for custom `target_definitions`**: the `resources:`
  wrapper is gone; lift skills/commands/agents/mcp to the top level
  of each entry. The `name:` and `description:` fields, if present,
  must be removed (they now raise "unknown keys").

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
