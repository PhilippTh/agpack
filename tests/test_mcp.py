"""Tests for agpack.mcp – MCP config merge / deploy / cleanup logic."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
import tomli_w

from agpack.config import McpServer
from agpack.mcp import McpError
from agpack.mcp import _atomic_write
from agpack.mcp import _merge_json
from agpack.mcp import _merge_toml
from agpack.mcp import _remove_from_json
from agpack.mcp import _remove_from_toml
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
            ("gemini", ".gemini/settings.json", "mcpServers"),
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


# ---------------------------------------------------------------------------
# 13. Opencode-specific MCP format
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 13a. Copilot/VS Code-specific MCP format
# ---------------------------------------------------------------------------


class TestCopilotFormat:
    def test_stdio_server_includes_type_field(self, tmp_path: Path) -> None:
        server = _stdio_server(env={"API_KEY": "secret"})
        deploy_mcp_servers([server], ["copilot"], tmp_path)

        srv = _read_json(tmp_path / ".vscode" / "mcp.json")["servers"]["my-server"]
        assert srv["type"] == "stdio"
        assert srv["command"] == "npx"
        assert srv["args"] == ["-y", "my-server"]
        assert srv["env"] == {"API_KEY": "secret"}

    def test_sse_server_includes_type_field(self, tmp_path: Path) -> None:
        server = _sse_server()
        deploy_mcp_servers([server], ["copilot"], tmp_path)

        srv = _read_json(tmp_path / ".vscode" / "mcp.json")["servers"]["remote"]
        assert srv == {"url": "https://mcp.example.com/sse", "type": "sse"}


# ---------------------------------------------------------------------------
# 14. Opencode-specific MCP format
# ---------------------------------------------------------------------------


class TestOpencodeFormat:
    def test_stdio_server_uses_local_type_and_array_command(
        self, tmp_path: Path
    ) -> None:
        server = _stdio_server(env={"API_KEY": "secret"})
        deploy_mcp_servers([server], ["opencode"], tmp_path)

        srv = _read_json(tmp_path / "opencode.json")["mcp"]["my-server"]
        assert srv["type"] == "local"
        assert srv["command"] == ["npx", "-y", "my-server"]
        assert srv["environment"] == {"API_KEY": "secret"}
        assert "args" not in srv
        assert "env" not in srv

    def test_stdio_server_omits_empty_environment(self, tmp_path: Path) -> None:
        server = McpServer(name="bare", type="stdio", command="run")
        deploy_mcp_servers([server], ["opencode"], tmp_path)

        srv = _read_json(tmp_path / "opencode.json")["mcp"]["bare"]
        assert srv["type"] == "local"
        assert srv["command"] == ["run"]
        assert "environment" not in srv

    def test_sse_server_uses_remote_type(self, tmp_path: Path) -> None:
        server = _sse_server()
        deploy_mcp_servers([server], ["opencode"], tmp_path)

        srv = _read_json(tmp_path / "opencode.json")["mcp"]["remote"]
        assert srv == {"type": "remote", "url": "https://mcp.example.com/sse"}

    def test_codex_uses_mcp_servers_key(self, tmp_path: Path) -> None:
        deploy_mcp_servers([_stdio_server()], ["codex"], tmp_path)
        cfg = _read_toml(tmp_path / ".codex" / "config.toml")
        assert "mcp_servers" in cfg
        assert "my-server" in cfg["mcp_servers"]

    def test_deploy_and_cleanup_roundtrip(self, tmp_path: Path) -> None:
        """Deploy to all targets then clean up – verify all files are cleaned."""
        server = _stdio_server()
        targets = ["claude", "opencode", "cursor", "copilot", "codex", "gemini"]
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
            ("gemini", ".gemini/settings.json"),
        ]:
            cfg = _read_json(tmp_path / rel)
            for key in cfg.values():
                if isinstance(key, dict):
                    assert "my-server" not in key

        codex_cfg = _read_toml(tmp_path / ".codex" / "config.toml")
        assert "my-server" not in codex_cfg.get("mcp_servers", {})


# ---------------------------------------------------------------------------
# 15. _merge_json / _merge_toml with corrupt files
# ---------------------------------------------------------------------------


class TestMergeCorruptFiles:
    def test_merge_json_corrupt_file_raises(self, tmp_path: Path) -> None:
        config_path = tmp_path / "bad.json"
        config_path.write_text("not valid json {{{{", encoding="utf-8")

        with pytest.raises(McpError, match="Failed to read"):
            _merge_json(config_path, "mcpServers", {"s": {"command": "x"}})

    def test_merge_toml_corrupt_file_raises(self, tmp_path: Path) -> None:
        config_path = tmp_path / "bad.toml"
        config_path.write_text("= invalid toml", encoding="utf-8")

        with pytest.raises(McpError, match="Failed to read"):
            _merge_toml(config_path, "mcp_servers", {"s": {"command": "x"}})


# ---------------------------------------------------------------------------
# 16. _remove_from_json – fuzzy key matching
# ---------------------------------------------------------------------------


class TestRemoveFromJsonFuzzyKeys:
    @pytest.mark.parametrize("key", ["mcpServers", "mcp", "servers"])
    def test_removes_server_under_various_keys(self, tmp_path: Path, key: str) -> None:
        config_path = tmp_path / "config.json"
        data = {key: {"my-server": {"command": "npx"}, "keep": {"command": "y"}}}
        config_path.write_text(json.dumps(data), encoding="utf-8")

        _remove_from_json(config_path, "my-server", dry_run=False)

        result = json.loads(config_path.read_text(encoding="utf-8"))
        assert "my-server" not in result[key]
        assert "keep" in result[key]

    def test_noop_when_server_not_found(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        original = {"mcpServers": {"other": {"command": "x"}}}
        config_path.write_text(json.dumps(original), encoding="utf-8")

        _remove_from_json(config_path, "nonexistent", dry_run=False)

        assert json.loads(config_path.read_text(encoding="utf-8")) == original

    def test_dry_run_skips_removal(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        original = {"mcpServers": {"my-server": {"command": "x"}}}
        config_path.write_text(json.dumps(original), encoding="utf-8")

        _remove_from_json(config_path, "my-server", dry_run=True)

        assert json.loads(config_path.read_text(encoding="utf-8")) == original

    def test_corrupt_file_returns_silently(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config_path.write_text("not json!", encoding="utf-8")

        # Should not raise
        _remove_from_json(config_path, "my-server", dry_run=False)


# ---------------------------------------------------------------------------
# 17. _remove_from_toml – fuzzy key matching
# ---------------------------------------------------------------------------


class TestRemoveFromTomlFuzzyKeys:
    def test_removes_server_under_mcp_servers_key(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        data = {
            "mcp_servers": {"my-server": {"command": "npx"}, "keep": {"command": "y"}}
        }
        config_path.write_text(tomli_w.dumps(data), encoding="utf-8")

        _remove_from_toml(config_path, "my-server", dry_run=False)

        result = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert "my-server" not in result["mcp_servers"]
        assert "keep" in result["mcp_servers"]

    def test_noop_when_server_not_found(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        data = {"mcp_servers": {"other": {"command": "x"}}}
        config_path.write_text(tomli_w.dumps(data), encoding="utf-8")

        _remove_from_toml(config_path, "nonexistent", dry_run=False)

        result = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert result["mcp_servers"] == {"other": {"command": "x"}}

    def test_dry_run_skips_removal(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        data = {"mcp_servers": {"my-server": {"command": "x"}}}
        config_path.write_text(tomli_w.dumps(data), encoding="utf-8")

        _remove_from_toml(config_path, "my-server", dry_run=True)

        result = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert "my-server" in result["mcp_servers"]

    def test_corrupt_file_returns_silently(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.toml"
        config_path.write_text("= invalid toml", encoding="utf-8")

        # Should not raise
        _remove_from_toml(config_path, "my-server", dry_run=False)


# ---------------------------------------------------------------------------
# 18. cleanup_mcp_server – unknown target config (infer from extension)
# ---------------------------------------------------------------------------


class TestCleanupUnknownTarget:
    def test_cleanup_json_file_with_unknown_target(self, tmp_path: Path) -> None:
        """When target config is not found, format is inferred from .json extension."""
        config_path = tmp_path / "custom.json"
        data = {"mcpServers": {"my-server": {"command": "npx"}}}
        config_path.write_text(json.dumps(data), encoding="utf-8")

        cleanup_mcp_server(
            "my-server",
            ["custom.json"],
            tmp_path,
            targets=[],  # no targets match
        )

        result = json.loads(config_path.read_text(encoding="utf-8"))
        assert "my-server" not in result["mcpServers"]

    def test_cleanup_toml_file_with_unknown_target(self, tmp_path: Path) -> None:
        """When target config is not found, format is inferred from .toml extension."""
        config_dir = tmp_path / "custom"
        config_dir.mkdir()
        config_path = config_dir / "config.toml"
        data = {"mcp_servers": {"my-server": {"command": "npx"}}}
        config_path.write_text(tomli_w.dumps(data), encoding="utf-8")

        cleanup_mcp_server(
            "my-server",
            ["custom/config.toml"],
            tmp_path,
            targets=[],  # no targets match
        )

        result = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert "my-server" not in result["mcp_servers"]


# ---------------------------------------------------------------------------
# 19. cleanup_mcp_server – dry-run with known target
# ---------------------------------------------------------------------------


class TestCleanupDryRun:
    def test_dry_run_does_not_remove(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".mcp.json"
        data = {"mcpServers": {"my-server": {"command": "npx"}}}
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        cleanup_mcp_server(
            "my-server",
            [".mcp.json"],
            tmp_path,
            targets=["claude"],
            dry_run=True,
        )

        result = _read_json(config_path)
        assert "my-server" in result["mcpServers"]


# ---------------------------------------------------------------------------
# 20. deploy_mcp_servers – error wrapping
# ---------------------------------------------------------------------------


class TestDeployErrorHandling:
    def test_non_mcp_error_wrapped(self, tmp_path: Path) -> None:
        """Non-McpError exceptions from file writes are wrapped in McpError."""
        server = _stdio_server()

        with (
            patch("agpack.mcp._merge_json", side_effect=OSError("disk full")),
            pytest.raises(McpError, match="Failed to write MCP config.*disk full"),
        ):
            deploy_mcp_servers([server], ["claude"], tmp_path)

    def test_mcp_error_re_raised_directly(self, tmp_path: Path) -> None:
        """McpError from _merge_json is re-raised without wrapping."""
        server = _stdio_server()

        with (
            patch(
                "agpack.mcp._merge_json",
                side_effect=McpError("corrupt config"),
            ),
            pytest.raises(McpError, match="corrupt config"),
        ):
            deploy_mcp_servers([server], ["claude"], tmp_path)


# ---------------------------------------------------------------------------
# 21. _remove_server_from_json / _remove_server_from_toml – corrupt files
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 20b. _atomic_write failure cleanup
# ---------------------------------------------------------------------------


class TestAtomicWriteFailure:
    def test_cleans_up_temp_file_on_replace_failure(self, tmp_path: Path) -> None:
        """When os.replace fails, the temp file is cleaned up and error re-raised."""
        target = tmp_path / "output.json"

        with (
            patch("agpack.mcp.os.replace", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            _atomic_write(target, '{"test": true}\n')

        # No temp files should be left behind
        leftover = list(tmp_path.glob(".agpack-mcp-*"))
        assert leftover == []

        # The target file should not have been created
        assert not target.exists()


# ---------------------------------------------------------------------------
# 21. _remove_server_from_json / _remove_server_from_toml – corrupt files
# ---------------------------------------------------------------------------


class TestRemoveServerCorruptFiles:
    def test_remove_server_from_corrupt_json(self, tmp_path: Path) -> None:
        config_path = tmp_path / ".mcp.json"
        config_path.write_text("not json!", encoding="utf-8")

        # Should not raise — silently returns
        cleanup_mcp_server(
            "my-server",
            [".mcp.json"],
            tmp_path,
            targets=["claude"],
        )

    def test_remove_server_from_corrupt_toml(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".codex"
        config_dir.mkdir()
        config_path = config_dir / "config.toml"
        config_path.write_text("= invalid toml", encoding="utf-8")

        # Should not raise — silently returns
        cleanup_mcp_server(
            "my-server",
            [".codex/config.toml"],
            tmp_path,
            targets=["codex"],
        )
