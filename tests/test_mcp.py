"""Tests for agpack.mcp – MCP config merge / deploy / cleanup logic."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from agpack.config import McpServer
from agpack.mcp import cleanup_mcp_server
from agpack.mcp import deploy_mcp_servers

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stdio_server(
    name: str = "my-server",
    command: str = "npx",
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> McpServer:
    return McpServer(
        name=name,
        type="stdio",
        command=command,
        args=args or ["-y", "my-server"],
        env=env or {},
    )


def _sse_server(
    name: str = "remote",
    url: str = "https://mcp.example.com/sse",
) -> McpServer:
    return McpServer(name=name, type="sse", url=url)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Create new JSON config file with stdio server
# ---------------------------------------------------------------------------


class TestCreateJsonStdio:
    def test_creates_file_with_stdio_server(self, tmp_path: Path) -> None:
        server = _stdio_server(env={"API_KEY": "secret"})
        deploy_mcp_servers([server], ["claude"], tmp_path)

        cfg = _read_json(tmp_path / ".mcp.json")
        assert "mcpServers" in cfg
        srv = cfg["mcpServers"]["my-server"]
        assert srv["command"] == "npx"
        assert srv["args"] == ["-y", "my-server"]
        assert srv["env"] == {"API_KEY": "secret"}

    def test_stdio_server_omits_empty_args_and_env(self, tmp_path: Path) -> None:
        server = McpServer(name="bare", type="stdio", command="run")
        deploy_mcp_servers([server], ["claude"], tmp_path)

        srv = _read_json(tmp_path / ".mcp.json")["mcpServers"]["bare"]
        assert "args" not in srv
        assert "env" not in srv


# ---------------------------------------------------------------------------
# 2. Create new JSON config file with SSE server
# ---------------------------------------------------------------------------


class TestCreateJsonSse:
    def test_creates_file_with_sse_server(self, tmp_path: Path) -> None:
        server = _sse_server()
        deploy_mcp_servers([server], ["claude"], tmp_path)

        cfg = _read_json(tmp_path / ".mcp.json")
        srv = cfg["mcpServers"]["remote"]
        assert srv == {"url": "https://mcp.example.com/sse", "type": "sse"}


# ---------------------------------------------------------------------------
# 3. Merge into existing JSON config – add new server, preserve existing
# ---------------------------------------------------------------------------


class TestMergeJsonAddServer:
    def test_preserves_existing_and_adds_new(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".mcp.json"
        config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "existing": {"command": "old-cmd", "args": ["--flag"]},
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        deploy_mcp_servers([_stdio_server()], ["claude"], tmp_path)

        cfg = _read_json(config_path)
        assert "existing" in cfg["mcpServers"]
        assert "my-server" in cfg["mcpServers"]
        # Original untouched
        assert cfg["mcpServers"]["existing"]["command"] == "old-cmd"

    def test_preserves_non_mcp_keys(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".mcp.json"
        config_path.write_text(
            json.dumps({"otherSetting": True, "mcpServers": {}}),
            encoding="utf-8",
        )

        deploy_mcp_servers([_stdio_server()], ["claude"], tmp_path)

        cfg = _read_json(config_path)
        assert cfg["otherSetting"] is True


# ---------------------------------------------------------------------------
# 4. Merge into existing JSON config – overwrite existing server
# ---------------------------------------------------------------------------


class TestMergeJsonOverwrite:
    def test_overwrites_existing_server_entry(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".mcp.json"
        config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "my-server": {"command": "old-cmd"},
                    }
                }
            ),
            encoding="utf-8",
        )

        deploy_mcp_servers([_stdio_server()], ["claude"], tmp_path)

        srv = _read_json(config_path)["mcpServers"]["my-server"]
        assert srv["command"] == "npx"


# ---------------------------------------------------------------------------
# 5. Create new TOML config file (codex format)
# ---------------------------------------------------------------------------


class TestCreateToml:
    def test_creates_toml_file(self, tmp_path: Path) -> None:
        server = _stdio_server()
        deploy_mcp_servers([server], ["codex"], tmp_path)

        config_path = tmp_path / ".codex" / "config.toml"
        assert config_path.exists()

        cfg = _read_toml(config_path)
        assert "mcp_servers" in cfg
        srv = cfg["mcp_servers"]["my-server"]
        assert srv["command"] == "npx"
        assert srv["args"] == ["-y", "my-server"]


# ---------------------------------------------------------------------------
# 6. Merge into existing TOML config
# ---------------------------------------------------------------------------


class TestMergeToml:
    def test_preserves_existing_and_adds_new(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".codex"
        config_dir.mkdir()
        config_path = config_dir / "config.toml"
        config_path.write_text(
            '[mcp_servers.existing]\ncommand = "old-cmd"\n',
            encoding="utf-8",
        )

        deploy_mcp_servers([_stdio_server()], ["codex"], tmp_path)

        cfg = _read_toml(config_path)
        assert "existing" in cfg["mcp_servers"]
        assert "my-server" in cfg["mcp_servers"]
        assert cfg["mcp_servers"]["existing"]["command"] == "old-cmd"

    def test_preserves_non_mcp_keys_in_toml(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".codex"
        config_dir.mkdir()
        config_path = config_dir / "config.toml"
        config_path.write_text(
            'model = "o3"\n\n[mcp_servers]\n',
            encoding="utf-8",
        )

        deploy_mcp_servers([_stdio_server()], ["codex"], tmp_path)

        cfg = _read_toml(config_path)
        assert cfg["model"] == "o3"


# ---------------------------------------------------------------------------
# 7. deploy_mcp_servers with multiple targets
# ---------------------------------------------------------------------------


class TestDeployMultipleTargets:
    def test_writes_to_all_requested_targets(self, tmp_path: Path) -> None:
        server = _stdio_server()
        targets = ["claude", "cursor", "codex"]
        result = deploy_mcp_servers([server], targets, tmp_path)

        assert ".mcp.json" in result["my-server"]
        assert ".cursor/mcp.json" in result["my-server"]
        assert ".codex/config.toml" in result["my-server"]

        # Verify each file actually exists and has the server
        claude_cfg = _read_json(tmp_path / ".mcp.json")
        assert "my-server" in claude_cfg["mcpServers"]

        cursor_cfg = _read_json(tmp_path / ".cursor" / "mcp.json")
        assert "my-server" in cursor_cfg["mcpServers"]

        codex_cfg = _read_toml(tmp_path / ".codex" / "config.toml")
        assert "my-server" in codex_cfg["mcp_servers"]

    def test_skips_unknown_targets(self, tmp_path: Path) -> None:
        server = _stdio_server()
        result = deploy_mcp_servers([server], ["claude", "nonexistent"], tmp_path)

        assert result["my-server"] == [".mcp.json"]

    def test_deploys_multiple_servers(self, tmp_path: Path) -> None:
        servers = [_stdio_server(), _sse_server()]
        result = deploy_mcp_servers(servers, ["claude"], tmp_path)

        cfg = _read_json(tmp_path / ".mcp.json")
        assert "my-server" in cfg["mcpServers"]
        assert "remote" in cfg["mcpServers"]
        assert "my-server" in result
        assert "remote" in result


# ---------------------------------------------------------------------------
# 8. deploy_mcp_servers dry_run mode
# ---------------------------------------------------------------------------


class TestDeployDryRun:
    def test_dry_run_does_not_create_files(self, tmp_path: Path) -> None:
        server = _stdio_server()
        result = deploy_mcp_servers(
            [server], ["claude", "codex"], tmp_path, dry_run=True
        )

        assert ".mcp.json" in result["my-server"]
        assert ".codex/config.toml" in result["my-server"]

        # No files should be written
        assert not (tmp_path / ".mcp.json").exists()
        assert not (tmp_path / ".codex" / "config.toml").exists()

    def test_dry_run_does_not_modify_existing(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".mcp.json"
        original = json.dumps({"mcpServers": {}})
        config_path.write_text(original, encoding="utf-8")

        deploy_mcp_servers([_stdio_server()], ["claude"], tmp_path, dry_run=True)

        assert config_path.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# 9. cleanup_mcp_server – removes from JSON file
# ---------------------------------------------------------------------------


class TestCleanupJson:
    def test_removes_server_from_json(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".mcp.json"
        config_path.write_text(
            json.dumps(
                {"mcpServers": {"my-server": {"command": "npx"}}},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        cleanup_mcp_server(
            "my-server",
            [".mcp.json"],
            tmp_path,
            targets=["claude"],
        )

        cfg = _read_json(config_path)
        assert "my-server" not in cfg["mcpServers"]

    def test_noop_when_server_not_present(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".mcp.json"
        original = {"mcpServers": {"other": {"command": "x"}}}
        config_path.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")

        cleanup_mcp_server(
            "nonexistent",
            [".mcp.json"],
            tmp_path,
            targets=["claude"],
        )

        assert _read_json(config_path) == original

    def test_noop_when_file_missing(self, tmp_path: Path) -> None:
        # Should not raise
        cleanup_mcp_server(
            "my-server",
            [".mcp.json"],
            tmp_path,
            targets=["claude"],
        )


# ---------------------------------------------------------------------------
# 10. cleanup_mcp_server – removes from TOML file
# ---------------------------------------------------------------------------


class TestCleanupToml:
    def test_removes_server_from_toml(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".codex"
        config_dir.mkdir()
        config_path = config_dir / "config.toml"
        config_path.write_text(
            '[mcp_servers.my-server]\ncommand = "npx"\nargs = ["-y"]\n',
            encoding="utf-8",
        )

        cleanup_mcp_server(
            "my-server",
            [".codex/config.toml"],
            tmp_path,
            targets=["codex"],
        )

        cfg = _read_toml(config_path)
        assert "my-server" not in cfg.get("mcp_servers", {})


# ---------------------------------------------------------------------------
# 11. cleanup_mcp_server – preserves other servers
# ---------------------------------------------------------------------------


class TestCleanupPreservesOthers:
    def test_preserves_other_servers_in_json(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".mcp.json"
        config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "keep-me": {"command": "keep"},
                        "remove-me": {"command": "bye"},
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        cleanup_mcp_server(
            "remove-me",
            [".mcp.json"],
            tmp_path,
            targets=["claude"],
        )

        cfg = _read_json(config_path)
        assert "keep-me" in cfg["mcpServers"]
        assert "remove-me" not in cfg["mcpServers"]

    def test_preserves_other_servers_in_toml(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".codex"
        config_dir.mkdir()
        config_path = config_dir / "config.toml"
        config_path.write_text(
            '[mcp_servers.keep-me]\ncommand = "keep"\n\n'
            '[mcp_servers.remove-me]\ncommand = "bye"\n',
            encoding="utf-8",
        )

        cleanup_mcp_server(
            "remove-me",
            [".codex/config.toml"],
            tmp_path,
            targets=["codex"],
        )

        cfg = _read_toml(config_path)
        assert "keep-me" in cfg["mcp_servers"]
        assert "remove-me" not in cfg["mcp_servers"]


# ---------------------------------------------------------------------------
# 12. Different servers_key per target
# ---------------------------------------------------------------------------


class TestServersKeyPerTarget:
    @pytest.mark.parametrize(
        ("target", "config_rel", "servers_key"),
        [
            ("claude", ".mcp.json", "mcpServers"),
            ("opencode", "opencode.json", "mcp"),
            ("cursor", ".cursor/mcp.json", "mcpServers"),
            ("copilot", ".vscode/mcp.json", "servers"),
        ],
    )
    def test_json_targets_use_correct_key(
        self,
        tmp_path: Path,
        target: str,
        config_rel: str,
        servers_key: str,
    ) -> None:
        server = _stdio_server()
        deploy_mcp_servers([server], [target], tmp_path)

        cfg = _read_json(tmp_path / config_rel)
        assert servers_key in cfg, f"Expected key '{servers_key}' in {config_rel}"
        assert "my-server" in cfg[servers_key]

    def test_codex_uses_mcp_servers_key(self, tmp_path: Path) -> None:
        deploy_mcp_servers([_stdio_server()], ["codex"], tmp_path)
        cfg = _read_toml(tmp_path / ".codex" / "config.toml")
        assert "mcp_servers" in cfg
        assert "my-server" in cfg["mcp_servers"]

    def test_deploy_and_cleanup_roundtrip(self, tmp_path: Path) -> None:
        """Deploy to all targets then clean up – verify all files are cleaned."""
        server = _stdio_server()
        targets = ["claude", "opencode", "cursor", "copilot", "codex"]
        result = deploy_mcp_servers([server], targets, tmp_path)

        cleanup_mcp_server(
            "my-server",
            result["my-server"],
            tmp_path,
            targets=targets,
        )

        # Every target config should have the server removed
        for _target, rel in [
            ("claude", ".mcp.json"),
            ("opencode", "opencode.json"),
            ("cursor", ".cursor/mcp.json"),
            ("copilot", ".vscode/mcp.json"),
        ]:
            cfg = _read_json(tmp_path / rel)
            for key in cfg.values():
                if isinstance(key, dict):
                    assert "my-server" not in key

        codex_cfg = _read_toml(tmp_path / ".codex" / "config.toml")
        assert "my-server" not in codex_cfg.get("mcp_servers", {})
