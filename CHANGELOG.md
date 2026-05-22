# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

> Breaking release. The manifest and `agpack.yml` schemas have both
> been overhauled to make `edit-file` resources fully generic. There
> is no migration path for the old `dependencies.mcp: [{name, ...}]`
> syntax — see the migration block below.

### Added

- **Per-target `vars:` on edit-file resources.** Built-in target
  manifests declare a `vars:` map (e.g. `bucket: mcpServers` for
  Claude, `bucket: mcp` for OpenCode, `bucket: mcp_servers` for Codex)
  exposed to patches as `${name}`. Users write a single patch like
  `key: ${bucket}.filesystem` and it deploys correctly to every
  target. Target vars take precedence over environment variables on
  name collision — the target manifest is the canonical source for
  per-target structural details.
- **`kind: edit-file` works for any structured config**, not just MCP.
  Claude Code hooks, permissions, VS Code extensions, EditorConfig, or
  any other JSON/TOML the user wants to merge into — all expressible
  as `{key, value, strategy}` patches under `dependencies:`. The
  built-in `claude` target ships an `edit-file` resource for
  `.claude/settings.json` (hooks/permissions) alongside the existing
  `mcp` resource.
- **`agpack.kinds.Patch`** — `{key, value, strategy: "replace" |
  "append"}` — is the universal edit-file input. The engine walks the
  dotted `key`, auto-creates intermediate dicts, and either replaces
  the leaf or appends to a list at the path. Cleanup undoes replaces
  by deleting the leaf and undoes appends by matching the recorded
  value via deep equality (no markers written to user files).
- **The three kinds (copy-directory, copy-file, edit-file)** are
  first-class in the manifest schema. Each resource block declares
  `kind:` and `path:` and nothing more (edit-file resources). Per-kind
  behavior lives in `agpack.kinds`; the deployer is a thin
  orchestration layer over the kinds.
- **Resource taxonomy is open.** `skills`, `commands`, `agents`, and
  `mcp` are conventional names used by the built-in targets — any
  string is a valid resource type name in both `dependencies:` and
  `target_definitions.<target>.<rt>:`.

### Removed

- **The MCP-specific encoder.** `McpServer`, transport-aware encoding
  (`env`→`environment`, `url`→`httpUrl`, command-as-array for
  opencode, etc.), and the `merge: {servers_key, defaults, transports}`
  manifest block are gone. Edit-file resources are now bare
  `{kind, path}`; per-target quirks become explicit patches in
  `agpack.yml` instead of implicit translation in the target manifest.
- **`format:` on edit-file resources.** Inferred from the path's
  extension (`.json`/`.toml`).
- **`layout: directory|file`** on copy resources. Replaced by
  `kind: copy-directory` / `kind: copy-file`.

### Migration from older releases

Old `agpack.yml`:

```yaml
dependencies:
  mcp:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
      env:
        API_KEY: ${API_KEY}
```

New `agpack.yml`:

```yaml
dependencies:
  mcp:
    - key: ${bucket}.filesystem         # resolves per-target via vars
      value:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
        env:
          API_KEY: ${API_KEY}
```

`${bucket}` resolves per target from each built-in's `vars:`:
`mcpServers` for Claude/Cursor/Gemini, `mcp_servers` for Codex,
`mcp` for OpenCode, `servers` for Copilot. One patch, all targets.

For custom `target_definitions:`, drop the `merge:` block from
`edit-file` resources, drop `format:`, and replace `layout:
directory|file` with `kind: copy-directory|copy-file`. Add a
`vars: { bucket: <key> }` block to edit-file resources whose patches
should use `${bucket}.<name>` for cross-target portability. The
lockfile format also changed; agpack does not read pre-release
lockfiles — delete `.agpack.lock.yml` before the first sync.
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
