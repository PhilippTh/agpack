# agpack

[![PyPI](https://img.shields.io/pypi/v/agpack)](https://pypi.org/project/agpack/)
[![CI](https://github.com/PhilippTh/agpack/actions/workflows/ci.yml/badge.svg)](https://github.com/PhilippTh/agpack/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/PhilippTh/agpack/graph/badge.svg)](https://codecov.io/gh/PhilippTh/agpack)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/type--checked-mypy-blue)](https://mypy-lang.org/)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-green)](LICENSE)
[![Sponsor](https://img.shields.io/badge/Sponsor-GitHub%20Sponsors-ea4aaa)](https://github.com/sponsors/PhilippTh)

Declare your AI agent resources in a YAML file, run `agpack sync`, and they get deployed to every coding tool you use.

agpack fetches skills, commands, agents, and MCP server configs from git repos and copies them to the right places for Claude Code, OpenCode, Codex, Cursor, GitHub Copilot, Gemini CLI, Windsurf, and Google Antigravity.

## Why

Every AI coding tool has its own directory structure for skills, its own config format for MCP servers, its own spot for custom commands. If you use more than one tool -- or share resources across projects -- you end up manually copying files and keeping multiple configs in sync.

agpack replaces that with a single `agpack.yml` that describes what you want and where it comes from.

## Install

```bash
pipx install agpack   # or: uv tool install agpack
```

Requires Python 3.11+ and `git` on PATH.

## Quick start

```bash
agpack init          # creates agpack.yml with commented-out examples
```

Edit `agpack.yml`:

```yaml
targets:
  - claude
  - opencode

dependencies:
  skills:
    - url: https://github.com/owner/repo
      path: skills/my-skill

  commands:
    - url: https://github.com/owner/repo
      path: commands/review.md

  agents:
    - url: https://github.com/owner/repo
      path: agents/backend-expert.md

  # edit-file resources take patches: a key path into the target file
  # and a value to put there.
  mcp:
    - key: mcpServers.filesystem
      value:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
```

```bash
agpack sync
```

Skills get copied to `.claude/skills/`, `.opencode/skills/`, etc. Commands and agents go to their respective directories. Patches are applied to each target's structured config file (e.g. `.mcp.json`, `.claude/settings.json`). Run `agpack sync` again after editing the config — removed dependencies and patches get cleaned up automatically.

## Dependencies

### URLs and pinning

The `url` field takes any valid `git clone` URL -- HTTPS, SSH, local paths, whatever git understands. Authentication is handled by your system git config (SSH keys, credential helpers, etc.).

Use `ref` to pin a dependency to a specific tag or commit:

```yaml
- url: https://github.com/owner/repo
  path: skills/my-skill
  ref: v1.2.0

- url: git@gitlab.com:myorg/myrepo.git
  path: skills/my-skill
  ref: abc1234
```

### Fallback URLs

`url` can be a list. When it is, agpack tries each URL in order until one succeeds. This is useful when team members use different auth methods (SSH vs HTTPS), or when you want to fall back to a mirror:

```yaml
# Tried in order -- works for both SSH and HTTPS users
- url:
    - https://github.com/owner/repo
    - git@github.com:owner/repo.git
  path: skills/my-skill

# Internal mirror with public fallback
- url:
    - https://git.internal.company.com/team/repo
    - https://github.com/company/repo
  path: skills/my-skill
```

### Directory expansion

The `path` field can point to a single file, a single folder, or a parent directory containing multiple items. When it points at a directory, agpack figures out what's inside:

- **Skills** -- a directory with top-level files is deployed as one skill. A directory containing only subdirectories deploys each subfolder as a separate skill.
- **Commands & Agents** -- every non-hidden file is deployed individually. If the directory only contains subdirectories, files inside those are collected instead.

```yaml
skills:
  - url: https://github.com/owner/repo
    path: skills/my-skill       # deploys one skill

  - url: https://github.com/owner/repo
    path: skills                 # deploys each subfolder as a separate skill

commands:
  - url: https://github.com/owner/repo
    path: commands/review.md     # deploys one file

  - url: https://github.com/owner/repo
    path: commands               # deploys every file inside
```

If the directory contains no deployable files, sync fails with an error.

### Environment variables

Use `${VAR_NAME}` in any string value to reference environment variables. Substitution recurses through dependency URLs/paths/refs and through every string inside a patch's `key` or `value` (including nested dicts and lists).

```yaml
dependencies:
  skills:
    - url: https://github.com/${GITHUB_ORG}/shared-skills
      path: skills/my-skill

  mcp:
    - key: mcpServers.context7
      value:
        command: npx
        args: ["-y", "@context7/mcp-server"]
        env:
          CONTEXT7_API_KEY: ${CONTEXT7_API_KEY}
```

Variables are resolved from up to three sources (highest priority first):

1. `.env` in the project root (same directory as `agpack.yml`)
2. `.env` in the global config directory (`~/.config/agpack/`)
3. Shell environment

If a referenced variable is not found in any source, sync fails with an error. The `.env` parser supports `KEY=VALUE`, quoted values, `# comments`, blank lines, and `export` prefixes.

## Global config

A global config defines dependencies shared across all your projects -- skills, agents, or MCP servers you want everywhere without repeating them in each `agpack.yml`.

```bash
agpack init --global   # creates ~/.config/agpack/agpack.yml
```

The global config uses the same `dependencies` block but has no `targets` (those are always per-project):

```yaml
# ~/.config/agpack/agpack.yml
dependencies:
  skills:
    - url: https://github.com/owner/shared-skills
      path: skills/my-standard-skill

  mcp:
    - key: mcpServers.context7
      value:
        command: npx
        args: ["-y", "@upstash/context7-mcp@latest"]
        env:
          CONTEXT7_API_KEY: ${CONTEXT7_API_KEY}
```

Global dependencies are merged with the project config during sync. Fetch entries are deduped by URL+path; patch entries are deduped by key (for `replace`) or by full content (for `append`). When both define the same patch key, the project version wins.

To skip the global config, either pass `--no-global` on the command line or add `global: false` to your project's `agpack.yml`. The default path (`~/.config/agpack/agpack.yml`) can be overridden with the `AGPACK_GLOBAL_CONFIG` environment variable.

## Target mapping

| Target | Skills | Commands | Agents | MCP Config |
|--------|--------|----------|--------|------------|
| Claude | `.claude/skills/<name>/` | `.claude/commands/<file>` | `.claude/agents/<file>` | `.mcp.json` |
| OpenCode | `.opencode/skills/<name>/` | `.opencode/commands/<file>` | `.opencode/agents/<file>` | `opencode.json` |
| Codex | `.codex/skills/<name>/` | -- | `.codex/agents/<file>` | `.codex/config.toml` |
| Cursor | `.cursor/skills/<name>/` | `.cursor/commands/<file>` | -- | `.cursor/mcp.json` |
| Copilot | `.github/skills/<name>/` | `.github/prompts/<file>` | `.github/agents/<file>` | `.vscode/mcp.json` |
| Gemini CLI | `.gemini/skills/<name>/` | `.gemini/commands/<file>` | -- | `.gemini/settings.json` |
| Windsurf | `.windsurf/skills/<name>/` | `.windsurf/workflows/<file>` | -- | -- *(global only)* |
| Antigravity | `.agent/skills/<name>/` | `.agent/workflows/<file>` | -- | -- *(global only)* |

Unsupported resource types are skipped silently. MCP definitions are merged into each tool's config file without touching servers agpack didn't create. Windsurf and Antigravity store MCP configs globally (`~/.codeium/windsurf/mcp_config.json` and `~/.gemini/antigravity/mcp_config.json`), so agpack does not manage them.

These paths are not hardcoded — they live in YAML manifests bundled with agpack and can be overridden per-project, see [Customising targets](#customising-targets) below.

## Customising targets

Targets are declarative. Each one is described by a YAML manifest that tells agpack where to deploy skills/commands/agents and how to encode the MCP config. agpack ships built-in manifests for the eight tools in the table above. You can override any of them — or define brand-new ones for tools agpack doesn't know about — by adding a `target_definitions:` block to your `agpack.yml`.

```yaml
targets:
  - claude
  - my-internal-tool          # custom target defined below

target_definitions:

  # Override a built-in: full replacement, no deep-merge.
  claude:
    skills:
      kind: copy-directory
      path: .my-claude/skills

    commands:
      kind: copy-file
      path: .my-claude/commands

  # Define a brand-new target — also list it under `targets:` to use it.
  my-internal-tool:
    skills:
      kind: copy-directory
      path: .myaitool/skills

    mcp:
      kind: edit-file
      path: .myaitool/config.json    # format inferred from .json/.toml suffix
```

Resolution precedence (highest first): project `target_definitions` → global `target_definitions` (in `~/.config/agpack/agpack.yml`) → bundled built-in. When a name appears in `target_definitions`, that entry **fully replaces** the built-in; agpack does not deep-merge.

### The three kinds

Every resource block declares a `kind:` that tells agpack how to deploy it. There are exactly three:

| Kind | What it does | Dependency shape |
|------|--------------|------------------|
| `copy-directory` | Copies a directory tree from a fetched git repo into `<path>/<name>/`. A dependency that points at a folder of subfolders expands to one bundle per subfolder. | `{ url, path?, ref? }` |
| `copy-file` | Copies individual files from a fetched git repo into `<path>/<name>`. A dependency that points at a folder of files expands to one item per file. | `{ url, path?, ref? }` |
| `edit-file` | Reads a structured config file (JSON or TOML, inferred from the path extension), applies patches, writes it back. Only touches keys agpack put there. | `{ key, value, strategy? }` |

### Patches (edit-file)

An edit-file dependency is a **patch**: a dotted `key` path into the file, a `value` to put there, and an optional `strategy` (`replace` — default — or `append`). The same engine handles every JSON/TOML config a tool might use: MCP servers, Claude Code hooks, permissions, VS Code extensions, anything.

```yaml
dependencies:
  # Replace a single key (default strategy)
  mcp:
    - key: mcpServers.filesystem
      value:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "."]

  # Append to a list — for hooks, permissions, extensions, etc.
  settings:
    - key: hooks.PreToolUse
      strategy: append
      value:
        matcher: "Write|Edit"
        hooks: [{ type: command, command: "echo ${TOOL_INPUT}" }]
    - key: permissions.allow
      strategy: append
      value: "Read(/etc/**)"
```

Intermediate dicts are auto-created. `append` requires the path to resolve to a list (created empty if absent). Cleanup of removed patches: `replace` deletes the key; `append` finds the entry by deep-equality against the lockfile-recorded value and removes it — agpack never deletes anything it didn't write.

**Per-target bucket keys.** agpack does not translate between targets — the patch key you write is applied verbatim to every target that supports the resource type. For tools whose MCP bucket differs (Claude/Cursor/Gemini use `mcpServers`, Codex uses `mcp_servers`, OpenCode uses `mcp`), write one patch per bucket — or restrict your `targets:` list to tools that share the bucket name.

### Arbitrary resource types

`skills`, `commands`, `agents`, and `mcp` are not reserved — they are just the resource type names the built-in targets happen to declare. Your own targets can declare any resource type by name (`rules`, `prompts`, `personas`, `lints`, anything your tool consumes):

```yaml
targets:
  - my-tool

dependencies:
  rules:                            # arbitrary resource type name
    - url: https://github.com/owner/rules-repo
      path: rules

target_definitions:
  my-tool:
    rules:                          # matched by name
      kind: copy-file
      path: .my-tool/rules
```

agpack does not interpret the name; it only matches it between `dependencies:` and the target's resource block. If the same resource type appears in multiple targets, they must agree on `kind:` — agpack refuses to deploy `commands` as a folder bundle for one target and as a flat file for another. Resource types configured under `dependencies:` but not declared by any target are silently skipped.

To see a starting point for customisation, run:

```bash
agpack targets show claude         # prints the resolved manifest as YAML
```

Copy the output under `target_definitions:` and edit the parts you want to change.

### Manifest schema

A manifest is a flat YAML mapping. The target's name is the YAML
filename (built-ins) or the mapping key under `target_definitions:`
in `agpack.yml` — there is no `name:` field on the manifest itself.
For human-readable context, use YAML comments at the top of the file.

```yaml
# Comments at the top of the file (built-in manifests use these in
# place of a "description" field).

skills:                         # one entry per supported resource type;
  kind: copy-directory          # omit unsupported types
  path: <relative path>
commands: { kind: copy-file, path: ... }
agents:   { kind: copy-file, path: ... }

mcp:                            # omit if the target has no per-project MCP
  kind: edit-file
  path: <relative path>         # extension (.json|.toml) drives the format

# Any name + edit-file works. agpack ships built-ins for `mcp` and (on
# Claude only) `settings`, but you can declare your own for any
# JSON/TOML config the tool reads.
settings:
  kind: edit-file
  path: .my-tool/settings.json
```

## Commands

```
agpack init    [--config PATH] [--global]       Scaffold a new config file
agpack sync    [--config PATH] [--no-global]    Fetch and deploy all dependencies
               [--dry-run] [--verbose]
agpack status  [--config PATH] [--no-global]    Show installed vs configured state
agpack targets list  [--config PATH] [--no-global]
                                                Show all available targets and their source
agpack targets show <name> [--config PATH] [--no-global]
                                                Print the resolved manifest for one target
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the per-version list of changes.
On first `agpack sync` after upgrading a major version, files in the
old (pre-upgrade) locations are cleaned up automatically because the
lockfile remembers exactly where the previous sync wrote them.

## How it works

1. Loads `agpack.yml` and the global config (if present), merges them
2. Resolves `${VAR}` references from `.env` files and the shell
3. Reads `.agpack.lock.yml` to diff against the previous state
4. Cleans up files from removed dependencies
5. Shallow-clones each repo (sparse checkout when `path` is set), copies files to all target directories
6. Merges MCP configs into each tool's config file
7. Writes an updated lockfile

Every file write is atomic (write-to-temp-then-rename). agpack never partially writes a file and never deletes anything it didn't create.

## License

GPL-3.0 -- see [LICENSE](LICENSE).
