# agpack

[![Sponsor](https://img.shields.io/badge/Sponsor-GitHub%20Sponsors-ea4aaa)](https://github.com/sponsors/PhilippTh)

Declare your AI agent resources in a YAML file, run `agpack sync`, and they get deployed to every coding tool you use.

agpack fetches skills, commands, agents, and MCP server configs from git repos and copies them to the right places for Claude Code, OpenCode, Codex, Cursor, and GitHub Copilot.

## Why

Every AI coding tool has its own directory structure for skills, its own config format for MCP servers, its own spot for custom commands. If you use more than one tool -- or share resources across projects -- you end up manually copying files and keeping multiple configs in sync.

agpack replaces that with a single `agpack.yml` that describes what you want and where it comes from.

## Install

```bash
pip install agpack
# or
uv tool install agpack
```

Requires Python 3.10+ and `git` on PATH.

## Quick start

```bash
agpack init          # creates agpack.yml with commented-out examples
```

Edit `agpack.yml`:

```yaml
name: my-project
version: 0.1.0

targets:
  - claude
  - opencode

dependencies:
  skills:
    - url: https://github.com/PhilippTh/agent-assets
      path: skills/article-review
    - url: https://github.com/PhilippTh/agent-assets
      path: skills/deep-dive
      ref: v1.2.0

  commands:
    - url: https://github.com/PhilippTh/agent-assets
      path: commands/review.md

  agents:
    - url: https://github.com/PhilippTh/agent-assets
      path: agents/backend-expert.md

  mcp:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
```

Then:

```bash
agpack sync
```

That's it. Skills get copied to `.claude/skills/`, `.opencode/skills/`, etc. Commands and agents go to their respective directories. MCP server definitions get merged into each tool's config file.

## How dependencies work

The `url` field takes any valid `git clone` URL. HTTPS, SSH, local paths -- whatever git understands:

```yaml
# GitHub over HTTPS
- url: https://github.com/owner/repo
  path: skills/my-skill

# GitLab over SSH
- url: git@gitlab.com:myorg/myrepo.git
  path: skills/my-skill

# Azure DevOps
- url: https://dev.azure.com/myorg/myproject/_git/myrepo
  path: skills/my-skill

# Pinned to a tag
- url: https://github.com/owner/repo
  path: skills/my-skill
  ref: v1.0.0

# Pinned to a commit
- url: https://github.com/owner/repo
  path: skills/my-skill
  ref: abc1234
```

Authentication is handled entirely by your system git config -- SSH keys, credential helpers, whatever you already have set up.

## Where things go

| Target | Skills | Commands | Agents | MCP Config |
|--------|--------|----------|--------|------------|
| Claude | `.claude/skills/<name>/` | `.claude/commands/<file>` | `.claude/agents/<file>` | `.mcp.json` |
| OpenCode | `.opencode/skills/<name>/` | `.opencode/commands/<file>` | `.opencode/agents/<file>` | `opencode.json` |
| Codex | `.agents/skills/<name>/` | -- | -- | `.codex/config.toml` |
| Cursor | `.cursor/skills/<name>/` | -- | `.cursor/agents/<file>` | `.cursor/mcp.json` |
| Copilot | `.github/skills/<name>/` | `.github/prompts/<file>` | `.github/agents/<file>` | `.vscode/mcp.json` |

Unsupported resource types (`--`) are skipped silently. MCP server definitions are merged into each tool's config file without touching servers agpack didn't create.

## Commands

### `agpack sync`

Fetches everything and deploys it. Run it again after changing `agpack.yml` -- removed dependencies get cleaned up automatically.

```
agpack sync [--dry-run] [--verbose] [--config PATH]
```

### `agpack status`

Shows what's installed vs what's configured:

```
Skills:
  ✓ article-review       (https://github.com/PhilippTh/agent-resources @ abc1234)
  ✗ new-skill            (not yet synced)

Commands:
  ✓ review.md            (https://github.com/PhilippTh/agent-resources @ abc1234)

MCP:
  ✓ filesystem           → .mcp.json, opencode.json
```

### `agpack init`

Creates a starter `agpack.yml` with commented-out examples.

## How it works under the hood

1. Loads `agpack.yml`, validates it
2. Reads `.agpack.lock.yml` to see what was previously installed
3. Cleans up files from dependencies you've removed
4. For each dependency: shallow-clones the repo (with sparse checkout when a `path` is set), copies files to all target directories
5. Merges MCP configs into each tool's config file
6. Writes an updated lockfile

Every file write is atomic (write-to-temp-then-rename). agpack never partially writes a file and never deletes anything it didn't create.

## License

GPL-3.0 -- see [LICENSE](LICENSE).
