# agpack

[![PyPI](https://img.shields.io/pypi/v/agpack)](https://pypi.org/project/agpack/)
[![CI](https://github.com/PhilippTh/agpack/actions/workflows/ci.yml/badge.svg)](https://github.com/PhilippTh/agpack/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/PhilippTh/agpack/graph/badge.svg)](https://codecov.io/gh/PhilippTh/agpack)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/type--checked-mypy-blue)](https://mypy-lang.org/)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-green)](LICENSE)
[![Sponsor](https://img.shields.io/badge/Sponsor-GitHub%20Sponsors-ea4aaa)](https://github.com/sponsors/PhilippTh)

A package manager for AI coding tools. Declare skills, commands, agents, MCP servers, hooks, and any other resource your editor or CLI consumes in one `agpack.yml`. Run `agpack sync` and agpack fetches them from git and deploys them where each tool expects — Claude Code, Codex, Cursor, GitHub Copilot, Gemini CLI, OpenCode, Windsurf, Google Antigravity, and any custom tool you describe in a YAML manifest.

## Why

Every AI coding tool has its own conventions: `.claude/skills/`, `.cursor/commands/`, `.codex/agents/`, `.github/prompts/`. MCP server configs live in subtly different shapes across tools (`mcpServers` vs `mcp_servers` vs `mcp` vs `servers`). Sharing the same resources across tools — and updating them across projects — turns into manual copy-paste with quiet drift.

agpack replaces that with a single declarative file. It owns the keys it writes, leaves everything else alone, and remembers exactly what it did so removing a dependency cleanly rolls back.

## Install

```bash
pipx install agpack    # or: uv tool install agpack
```

Requires Python 3.11+ and `git` on PATH.

## Quick start

```bash
agpack init            # creates agpack.yml with commented examples
```

A minimal `agpack.yml`:

```yaml
targets:
  - claude
  - opencode

dependencies:
  skills:
    - url: https://github.com/owner/repo
      path: skills/my-skill

  # MCP server entries deploy as patches into each target's config file.
  # ${bucket} is supplied by the target manifest, so the same patch
  # writes mcpServers.filesystem on Claude and mcp.filesystem on OpenCode.
  mcp:
    - key: ${bucket}.filesystem
      value:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
```

```bash
agpack sync
```

Skills land in `.claude/skills/my-skill/` and `.opencode/skills/my-skill/`. The MCP server is added to `.mcp.json` and `opencode.json`. Run `agpack sync` again after editing — removed entries are cleaned up automatically and the lockfile remembers what to restore.

## The model

Every resource agpack deploys falls into one of three **kinds**. Once you know the kinds, the rest of the tool is just "declare resources of these kinds; declare which tools (targets) get them."

| Kind             | What it does                                                                                                          | What you write in `agpack.yml`         |
|------------------|-----------------------------------------------------------------------------------------------------------------------|----------------------------------------|
| `copy-directory` | Copy a directory tree from a fetched git repo into `<path>/<name>/`. A folder-of-folders expands to one bundle each.  | `{ url, path?, ref? }`                 |
| `copy-file`      | Copy individual files from a fetched git repo into `<path>/<name>`. A folder-of-files expands to one item per file.  | `{ url, path?, ref? }`                 |
| `edit-file`      | Read a JSON or TOML config, apply patches, write it back. Only touches keys agpack owns; everything else is preserved. | `{ key, value, strategy? }`            |

A **target** is a YAML manifest that maps resource type names (`skills`, `commands`, `mcp`, anything you like) to a kind + destination path. agpack ships built-in manifests for the eight common tools and lets you override them or add your own.

## Dependencies

`dependencies:` is keyed by resource type name. The value is a list of entries. The shape of each entry depends on the kind the target uses for that resource type:

- **copy-directory / copy-file** entries are fetched from git: `{ url, path?, ref? }`.
- **edit-file** entries are inline patches: `{ key, value, strategy? }`.

```yaml
dependencies:

  skills:                             # copy-directory on every built-in
    - url: https://github.com/owner/skills-repo
      path: skills/code-review
      ref: v1.2.0                     # tag, branch, or commit SHA

  commands:                           # copy-file
    - url: https://github.com/owner/cmds
      path: commands/review.md

  agents:                             # copy-file
    - url: https://github.com/owner/agents
      path: agents/backend-expert.md

  mcp:                                # edit-file
    - key: ${bucket}.filesystem
      value:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
```

### URLs, pinning, and fallbacks

`url` takes any string `git clone` accepts — HTTPS, SSH, local paths. Auth goes through your system git config (SSH keys, credential helpers, etc.).

`url` can also be a list of fallback URLs, tried in order:

```yaml
- url:
    - git@github.com:owner/repo.git    # SSH for team members with keys
    - https://github.com/owner/repo    # HTTPS fallback
  path: skills/my-skill
  ref: v1.2.0
```

### Directory expansion

`path` can point at a single file, a single folder, or a parent directory containing multiple items. agpack figures out what's inside:

| `path:` points at…                | What deploys                                |
|-----------------------------------|---------------------------------------------|
| One skill folder (with files)     | One skill bundle named after the folder    |
| A folder of skill subfolders      | One bundle per subfolder                   |
| One command/agent file            | One file                                   |
| A folder of command/agent files   | Every non-hidden file                      |
| A folder of subfolders            | Files collected from each subfolder        |

Sync fails with an explicit error if a folder contains nothing deployable.

## Patches (`edit-file`)

A patch is a `{ key, value, strategy }` triple. `key` is a dotted path into the destination config file. `value` is whatever Python value the consuming tool expects to find there (a dict for an MCP server entry, a string for a permission, a dict for a hook entry — agpack is schema-agnostic). `strategy` is `replace` (the default — overwrites whatever's at the path) or `append` (treats the path as a list and adds one element).

```yaml
dependencies:

  mcp:                                # strategy defaults to replace
    - key: ${bucket}.filesystem
      value:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
        env:
          API_KEY: ${API_KEY}

  hooks:                              # an append patch — hooks live in a list
    - key: ${bucket}.PreToolUse
      strategy: append
      value:
        matcher: "Write|Edit"
        hooks:
          - type: command
            command: "$${CLAUDE_PROJECT_DIR}/lint.sh"

  permissions:
    - key: ${bucket}.allow
      strategy: append
      value: "Read(/etc/**)"
```

A few things to know:

- **`${bucket}`** is a per-target variable supplied by each target manifest. Claude's `mcp` resource ships `bucket: mcpServers`, Codex's ships `bucket: mcp_servers`, OpenCode's ships `bucket: mcp`. One patch deploys correctly to all three.
- **`$${X}`** writes a literal `${X}` to the destination file. Useful when the consuming tool resolves variables at runtime — Claude Code itself expands `${CLAUDE_PROJECT_DIR}` when a hook fires.
- **Dotted keys with literal dots**: use `\.` inside a segment to embed a literal dot. `mcpServers.example\.com` writes to `mcpServers["example.com"]`.
- **Intermediate dicts auto-create** for replace patches. `append` requires the path to resolve to a list (created empty if absent).
- **Cleanup is surgical**: `replace` restores the value that was there *before* agpack overwrote it (or deletes the key if agpack created it); `append` removes the exact item agpack added by deep-equality. agpack never deletes content it didn't write.

## Variables and substitution

Use `${name}` in any string. The lookup table merges two sources, target wins on collision:

1. **Target vars** — declared by an `edit-file` resource's `vars:` block. Per-target, only visible inside patches targeting that resource.
2. **Environment vars** — project `.env`, then global `.env`, then shell environment. Available in dependency URLs/paths/refs and recursively in patch keys and values.

```yaml
dependencies:
  skills:
    - url: https://github.com/${GITHUB_ORG}/shared-skills
      path: skills/my-skill

  mcp:
    - key: ${bucket}.context7         # ${bucket} from the target manifest
      value:
        command: npx
        args: ["-y", "@context7/mcp-server"]
        env:
          CONTEXT7_API_KEY: ${CONTEXT7_API_KEY}   # from env
```

`$$` writes a literal `$` (so `$${X}` becomes `${X}` in the file — pass runtime variables through to the consuming tool). Missing `${name}` references error at apply time, naming the variable and the patch context.

## Targets

```bash
agpack targets list                  # show every available target
agpack targets show claude           # print Claude's manifest as YAML
```

### Bundled targets

agpack ships manifests for eight tools: `claude`, `codex`, `copilot`, `cursor`, `gemini`, `opencode`, `windsurf`, `antigravity`. The manifests themselves are the source of truth — browse them at [`agpack/builtin_targets/`](agpack/builtin_targets/) or introspect locally:

```bash
agpack targets list                   # every target + its resource types
agpack targets show claude            # the full manifest as YAML
```

One thing worth knowing that isn't obvious from the file names:

- **Windsurf and Antigravity have no per-project MCP config.** Their MCP configs live in user-global locations (`~/.codeium/windsurf/mcp_config.json` and `~/.gemini/antigravity/mcp_config.json`), which agpack does not manage.

### Custom and overridden targets

Add a `target_definitions:` block to override a built-in or add a new tool:

```yaml
targets:
  - claude
  - my-internal-tool                   # custom target, defined below

target_definitions:

  # Override the built-in claude target — full replacement, no deep merge.
  claude:
    skills:
      kind: copy-directory
      path: .my-claude/skills
    commands:
      kind: copy-file
      path: .my-claude/commands

  # Brand-new target. Declare any resource type names you want; the same
  # names must appear under `dependencies:` to be deployed.
  my-internal-tool:
    skills:
      kind: copy-directory
      path: .myaitool/skills
    rules:
      kind: copy-file
      path: .myaitool/rules
    settings:
      kind: edit-file
      path: .myaitool/settings.json    # format inferred from .json/.toml
      vars:
        bucket: mcpServers
```

Precedence (highest first): project `target_definitions:` → global `target_definitions:` → bundled built-in. When a name appears in `target_definitions:`, that entry **fully replaces** the built-in; agpack does not deep-merge.

Tip: `agpack targets show <name>` prints the resolved manifest as YAML — copy-paste it into `target_definitions:` as a starting point.

### Resource type names are open

`skills`, `commands`, `agents`, `mcp`, `hooks` are not reserved — they're just the names the built-in target manifests use. Your custom targets can declare any name (`rules`, `prompts`, `personas`, `lints`, `examples`). agpack only matches names between `dependencies:` and target resource blocks; if the same name appears in multiple targets they must agree on `kind:`.

## Global config

A global config shares dependencies across every project on your machine — skills, agents, or MCP servers you want everywhere.

```bash
agpack init --global                  # creates ~/.config/agpack/agpack.yml
```

```yaml
# ~/.config/agpack/agpack.yml — same shape, no `targets:` (those stay per-project)
dependencies:
  skills:
    - url: https://github.com/owner/shared-skills
      path: skills/code-review

  mcp:
    - key: ${bucket}.context7
      value:
        command: npx
        args: ["-y", "@upstash/context7-mcp@latest"]
        env:
          CONTEXT7_API_KEY: ${CONTEXT7_API_KEY}
```

Global entries are merged into each project sync. Fetch entries are deduplicated by URL+path; patch entries by key (for `replace`) or full content (for `append`). Project entries win on conflict.

Skip the global config with `--no-global` on the command line or `global: false` in `agpack.yml`. Override the default path with `AGPACK_GLOBAL_CONFIG`.

## Safety

agpack writes files the user often hand-edits. Three guarantees keep that safe:

- **TOML format preservation.** TOML files (e.g. `.codex/config.toml`) round-trip through `tomlkit`. Comments, key ordering, and whitespace on sections agpack didn't touch survive every sync.
- **Idempotent writes.** Files are only written when the serialised text actually differs from disk. Running `agpack sync` twice in a row never modifies a file the second time. No mtime churn, no spurious git diffs.
- **Surgical cleanup.** Every `replace` patch snapshots the value that was at its key *before* agpack first ran. If you remove that patch from `agpack.yml`, the next sync restores the snapshot — your hand-written `mcpServers.foo` survives even if agpack temporarily owned it. Patches agpack created from nothing get deleted; patches that overwrote existing data get reverted. `append` patches are removed by deep-equality from their target list.

The lockfile (`.agpack.lock.yml`) is the source of truth — commit it alongside `agpack.yml` so the whole team's syncs converge. Every file write is atomic (write-to-temp-then-rename); agpack never partially writes a file.

## Recipes

### One MCP server across multiple tools

```yaml
targets: [claude, codex, opencode]
dependencies:
  mcp:
    - key: ${bucket}.context7         # bucket differs per target
      value:
        command: npx
        args: ["-y", "@upstash/context7-mcp@latest"]
```

### Private skills with a token from `.env`

```yaml
# .env
GITHUB_TOKEN=ghp_xxx
```

```yaml
dependencies:
  skills:
    - url: https://x-access-token:${GITHUB_TOKEN}@github.com/company/private-skills
      path: skills/internal
```

(SSH keys via `git@github.com:...` are usually simpler — `${GITHUB_TOKEN}` in URLs is for CI where SSH isn't available.)

### Pin to a tag / commit

```yaml
- url: https://github.com/owner/skills-repo
  path: skills/my-skill
  ref: v1.2.0                         # tag, branch, or commit SHA
```

### Add a Claude Code hook

```yaml
dependencies:
  hooks:
    - key: ${bucket}.PreToolUse
      strategy: append
      value:
        matcher: "Write|Edit"
        hooks:
          - type: command
            command: "$${CLAUDE_PROJECT_DIR}/.claude/hooks/lint.sh"
```

`$${CLAUDE_PROJECT_DIR}` is written verbatim — Claude Code expands it when the hook fires.

### Support a tool agpack doesn't ship a target for

```yaml
targets: [my-cli]
dependencies:
  rules:
    - url: https://github.com/owner/rules-repo
      path: rules

target_definitions:
  my-cli:
    rules:
      kind: copy-file
      path: .mycli/rules
    settings:
      kind: edit-file
      path: .mycli/settings.json
```

### Address a config key that contains a dot

```yaml
dependencies:
  mcp:
    - key: ${bucket}.example\.com     # writes mcpServers["example.com"]
      value: { command: ... }
```

### Roll back

Delete the entry from `agpack.yml`, run `agpack sync`. The lockfile diff drives cleanup: copy-kind files get removed, edit-file `replace` patches restore the prior value, `append` patches get the exact item removed.

## Commands

```
agpack init    [--config PATH] [--global]                Scaffold a new config file
agpack sync    [--config PATH] [--no-global] [--verbose] Fetch and deploy all dependencies
agpack status  [--config PATH] [--no-global]             Show installed vs configured state
agpack targets list  [--config PATH] [--no-global]       Show all available targets and source
agpack targets show <name> [--config PATH] [--no-global] Print the resolved manifest for one
```

## Limitations

- **JSON formatting is not preserved.** Python's stdlib `json` has no format-preserving parser. agpack canonicalises (`indent=2`) on the *first* write to a JSON file; subsequent syncs are idempotent and won't rewrite it. Hand-edit JSON files in the same shape agpack emits to avoid churn.
- **Edit-file currently supports only JSON and TOML.** Format is inferred from the path extension; other config formats (YAML, INI, custom DSLs) are not patchable.
- **`target_definitions:` is full replacement, not extension.** Naming a built-in under `target_definitions:` replaces it wholesale — there is no deep-merge. To add one resource type to Claude (e.g. `personas:`) you have to restate every resource type Claude already declares. Tip: `agpack targets show claude` prints the resolved manifest, copy it under `target_definitions.claude:` as a starting point. The trade-off is intentional — deep-merge across built-in upgrades was judged too magical to ship behind a single config knob.
- **Cross-target resource types must share a kind.** If two targets both declare a `commands` resource, they have to agree on whether `commands` is `copy-file` or `copy-directory`. When you genuinely need different shapes, declare them under different names and list each entry under the corresponding name. The repetition is the price of clarity:

  ```yaml
  targets: [tool-a, tool-b]

  dependencies:
    commands-files:                   # for targets that want flat files
      - url: https://github.com/owner/cmds
        path: commands/review.md
    commands-patches:                 # for targets that want config patches
      - key: ${bucket}.review
        value: { command: "..." }

  target_definitions:
    tool-a:
      commands-files:
        kind: copy-file
        path: .tool-a/commands
    tool-b:
      commands-patches:
        kind: edit-file
        path: .tool-b/config.json
        vars: { bucket: commands }
  ```
- **Windsurf and Antigravity MCP configs are global**, not per-project. agpack doesn't write user-global config files.

## Changelog

See [CHANGELOG.md](CHANGELOG.md). On first `agpack sync` after a major version upgrade, files in the old (pre-upgrade) locations are cleaned up automatically — the lockfile remembers exactly where the previous sync wrote them.

## How it works

1. Loads `agpack.yml` and (optionally) the global config; merges them.
2. Resolves `${VAR}` references from `.env` files and the shell env (for fetch entries). Patches resolve `${name}` per-target at apply time.
3. Reads `.agpack.lock.yml` to diff against the previous state.
4. Cleans up files from removed copy-kind dependencies.
5. Shallow-clones each repo (sparse checkout when `path` is set) and copies files to every target that declares the matching resource type.
6. Reconciles edit-file resources: per file, diff old applied patches against the desired set, undo what's gone, apply what's new, leave matched patches alone. Files are only written when their text actually changed.
7. Writes the updated lockfile.

## License

GPL-3.0 — see [LICENSE](LICENSE).
