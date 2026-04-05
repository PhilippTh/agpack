# agpack

[![PyPI](https://img.shields.io/pypi/v/agpack)](https://pypi.org/project/agpack/)
[![CI](https://github.com/PhilippTh/agpack/actions/workflows/ci.yml/badge.svg)](https://github.com/PhilippTh/agpack/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/type--checked-mypy-blue)](https://mypy-lang.org/)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-green)](LICENSE)
[![Sponsor](https://img.shields.io/badge/Sponsor-GitHub%20Sponsors-ea4aaa)](https://github.com/sponsors/PhilippTh)

Declare your AI agent resources in a YAML file, run `agpack sync`, and they get deployed to every coding tool you use.

agpack fetches skills, commands, agents, and MCP server configs from git repos and copies them to the right places for Claude Code, OpenCode, Codex, Cursor, and GitHub Copilot.

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

  mcp:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
```

```bash
agpack sync
```

Skills get copied to `.claude/skills/`, `.opencode/skills/`, etc. Commands and agents go to their respective directories. MCP server definitions get merged into each tool's config file. Run `agpack sync` again after editing the config -- removed dependencies get cleaned up automatically.

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

Use `${VAR_NAME}` in any string value to reference environment variables. This works in URLs, paths, refs, MCP commands, args, env values, and server URLs.

```yaml
dependencies:
  skills:
    - url: https://github.com/${GITHUB_ORG}/shared-skills
      path: skills/my-skill

  mcp:
    - name: context7
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
    - name: context7
      command: npx
      args: ["-y", "@upstash/context7-mcp@latest"]
      env:
        CONTEXT7_API_KEY: ${CONTEXT7_API_KEY}
```

Global dependencies are merged with the project config during sync. If the same dependency or MCP server appears in both, the project version wins.

To skip the global config, either pass `--no-global` on the command line or add `global: false` to your project's `agpack.yml`. The default path (`~/.config/agpack/agpack.yml`) can be overridden with the `AGPACK_GLOBAL_CONFIG` environment variable.

## Target mapping

| Target | Skills | Commands | Agents | MCP Config |
|--------|--------|----------|--------|------------|
| Claude | `.claude/skills/<name>/` | `.claude/commands/<file>` | `.claude/agents/<file>` | `.mcp.json` |
| OpenCode | `.opencode/skills/<name>/` | `.opencode/commands/<file>` | `.opencode/agents/<file>` | `opencode.json` |
| Codex | `.agents/skills/<name>/` | -- | -- | `.codex/config.toml` |
| Cursor | `.cursor/skills/<name>/` | -- | `.cursor/agents/<file>` | `.cursor/mcp.json` |
| Copilot | `.github/skills/<name>/` | `.github/prompts/<file>` | `.github/agents/<file>` | `.vscode/mcp.json` |

Unsupported resource types are skipped silently. MCP definitions are merged into each tool's config file without touching servers agpack didn't create.

## Commands

```
agpack init    [--config PATH] [--global]       Scaffold a new config file
agpack sync    [--config PATH] [--no-global]    Fetch and deploy all dependencies
               [--dry-run] [--verbose]
agpack status  [--config PATH] [--no-global]    Show installed vs configured state
```

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
