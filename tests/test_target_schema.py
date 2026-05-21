"""Tests for the target manifest parser and validator."""

from __future__ import annotations

import pytest

from agpack.target_schema import ResourceLayout
from agpack.target_schema import TargetDef
from agpack.target_schema import TargetSchemaError
from agpack.target_schema import TransportSpec
from agpack.target_schema import parse_target_def


def _minimal() -> dict[str, object]:
    return {"name": "demo"}


def test_minimal_target_parses() -> None:
    target = parse_target_def(_minimal())
    assert target.name == "demo"
    assert target.description == ""
    assert target.resources == {}
    assert target.mcp is None


def test_full_target_parses() -> None:
    raw = {
        "name": "demo",
        "description": "Demo tool",
        "resources": {
            "skills": {"layout": "directory", "path": ".demo/skills"},
            "commands": {"layout": "file", "path": ".demo/commands"},
            "agents": {"layout": "file", "path": ".demo/agents"},
        },
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
    assert target.description == "Demo tool"
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
            "name": "demo",
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


def test_missing_name_raises() -> None:
    with pytest.raises(TargetSchemaError, match="name"):
        parse_target_def({})


def test_non_mapping_raises() -> None:
    with pytest.raises(TargetSchemaError, match="expected a mapping"):
        parse_target_def("not a dict")


def test_unknown_top_level_key_raises() -> None:
    with pytest.raises(TargetSchemaError, match="unknown keys"):
        parse_target_def({"name": "demo", "bogus": 1})


def test_unknown_resource_type_raises() -> None:
    with pytest.raises(TargetSchemaError, match="unknown resource type"):
        parse_target_def(
            {
                "name": "demo",
                "resources": {"rules": {"layout": "file", "path": ".demo/rules"}},
            }
        )


def test_invalid_layout_raises() -> None:
    with pytest.raises(TargetSchemaError, match="layout"):
        parse_target_def(
            {
                "name": "demo",
                "resources": {"skills": {"layout": "weird", "path": ".x"}},
            }
        )


def test_missing_resource_path_raises() -> None:
    with pytest.raises(TargetSchemaError, match="path"):
        parse_target_def({"name": "demo", "resources": {"skills": {"layout": "file"}}})


def test_resource_unknown_field_raises() -> None:
    with pytest.raises(TargetSchemaError, match="unknown keys"):
        parse_target_def(
            {
                "name": "demo",
                "resources": {
                    "skills": {"layout": "file", "path": ".x", "extra": 1},
                },
            }
        )


def test_invalid_mcp_format_raises() -> None:
    with pytest.raises(TargetSchemaError, match="format"):
        parse_target_def(
            {
                "name": "demo",
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
                "name": "demo",
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
                "name": "demo",
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
                "name": "demo",
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
                "name": "demo",
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
                "name": "demo",
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
            "name": "demo",
            "mcp": {
                "path": ".x.json",
                "format": "json",
                "servers_key": "mcpServers",
            },
        }
    )
    assert target.mcp is not None
    assert target.mcp.transports == {}


def test_description_must_be_string() -> None:
    with pytest.raises(TargetSchemaError, match="description"):
        parse_target_def({"name": "demo", "description": 42})


def test_context_appears_in_error() -> None:
    with pytest.raises(TargetSchemaError, match="my-source.resources.skills.layout"):
        parse_target_def(
            {
                "name": "demo",
                "resources": {"skills": {"layout": "weird", "path": ".x"}},
            },
            context="my-source",
        )


# ---------------------------------------------------------------------------
# target_def_to_dict — serializer
# ---------------------------------------------------------------------------


def test_serialize_minimal_target() -> None:
    from agpack.target_schema import target_def_to_dict

    raw = {"name": "demo"}
    out = target_def_to_dict(parse_target_def(raw))
    assert out == {"name": "demo"}


def test_serialize_omits_transport_defaults() -> None:
    """Transport fields equal to TransportSpec defaults must not appear."""
    from agpack.target_schema import target_def_to_dict

    raw = {
        "name": "demo",
        "mcp": {
            "path": ".x.json",
            "format": "json",
            "servers_key": "mcpServers",
            "transports": {
                "stdio": {},
                "http": {"type_value": "http"},
            },
        },
    }
    out = target_def_to_dict(parse_target_def(raw))
    # stdio had no overrides → empty dict
    assert out["mcp"]["transports"]["stdio"] == {}
    # http only differs in type_value → only that field shows up
    assert out["mcp"]["transports"]["http"] == {"type_value": "http"}


def test_serialize_full_target_roundtrips() -> None:
    """A complete manifest must survive a dump → parse roundtrip unchanged."""
    from agpack.target_schema import target_def_to_dict

    raw = {
        "name": "demo",
        "description": "Demo tool",
        "resources": {
            "skills": {"layout": "directory", "path": ".demo/skills"},
            "commands": {"layout": "file", "path": ".demo/commands"},
        },
        "mcp": {
            "path": ".demo/mcp.json",
            "format": "json",
            "servers_key": "mcp",
            "defaults": {"$schema": "https://example.com/schema.json"},
            "transports": {
                "stdio": {
                    "type_value": "local",
                    "command_format": "array",
                    "env_key": "environment",
                },
                "http": {"type_value": "remote", "url_key": "httpUrl"},
            },
        },
    }
    original = parse_target_def(raw)
    redumped = target_def_to_dict(original)
    reparsed = parse_target_def(redumped)
    assert reparsed == original


def test_every_builtin_roundtrips() -> None:
    """Every shipped manifest survives a serialize → parse cycle unchanged."""
    from agpack.registry import load_all_builtins
    from agpack.target_schema import target_def_to_dict

    for name, original in load_all_builtins().items():
        dumped = target_def_to_dict(original)
        reparsed = parse_target_def(dumped)
        assert reparsed == original, f"roundtrip mismatch for built-in '{name}'"
