# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-05-22

> Breaking release. Target manifests and `agpack.yml` were overhauled to make every resource generic. The old MCP-specific syntax has no in-place upgrade — see the migration block below.

### Added

- **YAML target manifests + `target_definitions:` in `agpack.yml`.** Built-in targets (Claude, Cursor, Gemini, Codex, OpenCode, Copilot) now ship as YAML manifests; users can define or override targets directly in `agpack.yml`. New CLI: `agpack targets list` and `agpack targets show <name>`.
- **Three resource kinds: `copy-directory`, `copy-file`, `edit-file`.** Declared per resource as `kind:` + `path:`. Per-kind logic lives in `agpack.kinds`; the deployer is a thin orchestrator.
- **Generic `edit-file` for any JSON/TOML config**, not just MCP. Patches are `{key, value, strategy: replace | append}` under `dependencies:`. The engine walks the dotted `key`, auto-creates intermediates, and on cleanup reverses replaces by key and appends by deep-equality match (no markers in user files). The built-in `claude` target ships an `edit-file` resource for `.claude/settings.json` (hooks/permissions) alongside `mcp`.
- **Per-target `vars:` with `${name}` substitution in patches.** Each target manifest declares a `vars:` map (e.g. `bucket: mcpServers` for Claude, `mcp` for OpenCode, `mcp_servers` for Codex). One patch `key: ${bucket}.filesystem` deploys correctly everywhere. Target vars take precedence over env vars on collision.
- **Open resource taxonomy.** `skills`, `commands`, `agents`, `mcp` are conventions used by the built-ins — any string is a valid resource-type name in both `dependencies:` and `target_definitions`.

### Changed

- Configuration errors now raise structured exceptions instead of failing late during deploy.
- Logging no longer echoes secrets pulled from `env:` substitutions.

### Removed

- The MCP-specific encoder (`McpServer`, transport-aware encoding, `merge: {servers_key, defaults, transports}`). Per-target quirks are now explicit patches.
- `format:` on edit-file resources (inferred from path extension).
- `layout: directory | file` on copy resources (replaced by `kind: copy-directory | copy-file`).

### Migration from 0.3.x

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

`${bucket}` resolves per target from each built-in's `vars:`: `mcpServers` for Claude/Cursor/Gemini, `mcp_servers` for Codex, `mcp` for OpenCode, `servers` for Copilot.

For custom `target_definitions:`, drop the `merge:` block and `format:` from edit-file resources, replace `layout: directory|file` with `kind: copy-directory|copy-file`, and add `vars: { bucket: <key> }` on edit-file resources whose patches use `${bucket}.<name>`.

**Lockfile:** the format changed and agpack does not read pre-release lockfiles — delete `.agpack.lock.yml` before the first sync. Legacy singular resource-type keys (`skill`/`command`/`agent`) are remapped to plural on read, so cleanup of older lockfiles still works.

## [0.3.1] - 2026-04-05

### Added

- Multiple URLs per dependency: `url:` accepts a list, tried in order (e.g. HTTPS primary with SSH fallback). The first URL is canonical for identity and display.

## [0.3.0] - 2026-04-05

### Added

- Global config at `~/.config/agpack/agpack.yml` for dependencies and MCP servers shared across projects.
- Per-project opt-out via `global: false` in `agpack.yml`, or `agpack sync --no-global` from the CLI.
- Project dependencies and MCP servers win on conflict when merged with the global config.

## [0.2.1] - 2026-03-26

### Changed

- Improved progress / status styling.

## [0.2.0] - 2026-03-26

### Added

- Folder-of-skills support: a dependency `path` pointing at a directory of subfolders expands to one skill per subfolder.

## [0.1.7] - 2026-03-26

### Fixed

- Added a proper timeout for `git fetch` operations and disabled interactive git prompts so a stalled clone can no longer hang sync indefinitely (#2).

## [0.1.6] - 2026-03-25

### Added

- `${VAR}` env-variable substitution in MCP server `env:` and other config values, resolved from `.env` or the shell environment.

## [0.1.5] - 2026-03-23

### Added

- MCP server config merging into each tool's project-level config file.

### Changed

- User-feedback improvements during sync (status / progress output).

### Fixed

- Type-check failures and CI configuration.

[Unreleased]: https://github.com/PhilippTh/agpack/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/PhilippTh/agpack/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/PhilippTh/agpack/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/PhilippTh/agpack/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/PhilippTh/agpack/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/PhilippTh/agpack/compare/v0.1.7...v0.2.0
[0.1.7]: https://github.com/PhilippTh/agpack/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/PhilippTh/agpack/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/PhilippTh/agpack/releases/tag/v0.1.5
