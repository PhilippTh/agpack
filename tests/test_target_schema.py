"""Tests for the target manifest parser and validator."""

from __future__ import annotations

import pytest

from agpack.target_schema import ResourceLayout
from agpack.target_schema import TargetDef
from agpack.target_schema import TargetSchemaError
from agpack.target_schema import TransportSpec
from agpack.target_schema import parse_target_def


def test_minimal_target_parses() -> None:
    target = parse_target_def({})
    assert target.resources == {}
    assert target.mcp is None


def test_full_target_parses() -> None:
    raw = {
        "skills": {"layout": "directory", "path": ".demo/skills"},
        "commands": {"layout": "file", "path": ".demo/commands"},
        "agents": {"layout": "file", "path": ".demo/agents"},
        "mcp": {
            "path": ".demo/mcp.json",
            "format": "json",
            "servers_key": "mcpServers",
            "defaults": {"$schema": "https://example.com/schema.json"},
            "transports": {
                "stdio": {
                    "type_value": "local",
                    "command_format": "array",
                    "env_key": "environment",
                },
                "http": {"type_value": "remote", "url_key": "httpUrl"},
                "sse": {"type_value": "remote"},
            },
        },
    }

    target = parse_target_def(raw)

    assert isinstance(target, TargetDef)
    assert target.resources["skills"] == ResourceLayout(
        layout="directory", path=".demo/skills"
    )
    assert target.mcp is not None
    assert target.mcp.format == "json"
    assert target.mcp.defaults == {"$schema": "https://example.com/schema.json"}
    stdio = target.mcp.transports["stdio"]
    assert stdio == TransportSpec(
        type_value="local",
        command_format="array",
        env_key="environment",
    )
    assert target.mcp.transports["http"].url_key == "httpUrl"
    assert target.mcp.transports["sse"].type_value == "remote"


def test_transport_defaults_applied() -> None:
    target = parse_target_def(
        {
            "mcp": {
                "path": ".x.json",
                "format": "json",
                "servers_key": "mcpServers",
                "transports": {"stdio": {}},
            },
        }
    )
    assert target.mcp is not None
    stdio = target.mcp.transports["stdio"]
    assert stdio.type_value is None
    assert stdio.type_field == "type"
    assert stdio.command_key == "command"
    assert stdio.command_format == "string"
    assert stdio.args_key == "args"
    assert stdio.env_key == "env"
    assert stdio.url_key == "url"
    assert stdio.headers_key == "headers"


def test_non_mapping_raises() -> None:
    with pytest.raises(TargetSchemaError, match="expected a mapping"):
        parse_target_def("not a dict")


def test_unknown_top_level_key_raises() -> None:
    with pytest.raises(TargetSchemaError, match="unknown keys"):
        parse_target_def({"bogus": 1})


def test_invalid_layout_raises() -> None:
    with pytest.raises(TargetSchemaError, match="layout"):
        parse_target_def({"skills": {"layout": "weird", "path": ".x"}})


def test_missing_resource_path_raises() -> None:
    with pytest.raises(TargetSchemaError, match="path"):
        parse_target_def({"skills": {"layout": "file"}})


def test_resource_unknown_field_raises() -> None:
    with pytest.raises(TargetSchemaError, match="unknown keys"):
        parse_target_def(
            {"skills": {"layout": "file", "path": ".x", "extra": 1}}
        )


def test_invalid_mcp_format_raises() -> None:
    with pytest.raises(TargetSchemaError, match="format"):
        parse_target_def(
            {
                "mcp": {
                    "path": ".x",
                    "format": "yaml",
                    "servers_key": "mcpServers",
                },
            }
        )


def test_unknown_transport_raises() -> None:
    with pytest.raises(TargetSchemaError, match="unknown transport"):
        parse_target_def(
            {
                "mcp": {
                    "path": ".x",
                    "format": "json",
                    "servers_key": "mcpServers",
                    "transports": {"websocket": {}},
                },
            }
        )


def test_invalid_command_format_raises() -> None:
    with pytest.raises(TargetSchemaError, match="command_format"):
        parse_target_def(
            {
                "mcp": {
                    "path": ".x",
                    "format": "json",
                    "servers_key": "mcpServers",
                    "transports": {"stdio": {"command_format": "tuple"}},
                },
            }
        )


def test_type_value_must_be_string_or_null() -> None:
    with pytest.raises(TargetSchemaError, match="type_value"):
        parse_target_def(
            {
                "mcp": {
                    "path": ".x",
                    "format": "json",
                    "servers_key": "mcpServers",
                    "transports": {"stdio": {"type_value": 42}},
                },
            }
        )


def test_transport_unknown_field_raises() -> None:
    with pytest.raises(TargetSchemaError, match="unknown keys"):
        parse_target_def(
            {
                "mcp": {
                    "path": ".x",
                    "format": "json",
                    "servers_key": "mcpServers",
                    "transports": {"stdio": {"bogus": 1}},
                },
            }
        )


def test_mcp_unknown_field_raises() -> None:
    with pytest.raises(TargetSchemaError, match="unknown keys"):
        parse_target_def(
            {
                "mcp": {
                    "path": ".x",
                    "format": "json",
                    "servers_key": "mcpServers",
                    "bogus": True,
                },
            }
        )


def test_mcp_with_no_transports_is_valid() -> None:
    target = parse_target_def(
        {
            "mcp": {
                "path": ".x.json",
                "format": "json",
                "servers_key": "mcpServers",
            },
        }
    )
    assert target.mcp is not None
    assert target.mcp.transports == {}


def test_context_appears_in_error() -> None:
    with pytest.raises(TargetSchemaError, match="my-source.skills.layout"):
        parse_target_def(
            {"skills": {"layout": "weird", "path": ".x"}},
            context="my-source",
        )
