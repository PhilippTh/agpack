"""Tests for the target manifest parser and validator."""

from __future__ import annotations

import pytest

from agpack.kinds import CopyDirectoryResource
from agpack.kinds import CopyFileResource
from agpack.kinds import EditFileResource
from agpack.kinds import TransportSpec
from agpack.target_schema import TargetDef
from agpack.target_schema import TargetSchemaError
from agpack.target_schema import parse_target_def


def test_minimal_target_parses() -> None:
    target = parse_target_def({})
    assert target.resources == {}


def test_full_target_parses() -> None:
    raw = {
        "skills": {"kind": "copy-directory", "path": ".demo/skills"},
        "commands": {"kind": "copy-file", "path": ".demo/commands"},
        "agents": {"kind": "copy-file", "path": ".demo/agents"},
        "mcp": {
            "kind": "edit-file",
            "path": ".demo/mcp.json",
            "merge": {
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
        },
    }

    target = parse_target_def(raw)

    assert isinstance(target, TargetDef)
    assert target.resources["skills"] == CopyDirectoryResource(path=".demo/skills")
    assert target.resources["commands"] == CopyFileResource(path=".demo/commands")
    mcp = target.resources["mcp"]
    assert isinstance(mcp, EditFileResource)
    assert mcp.path == ".demo/mcp.json"
    assert mcp.format == "json"
    assert mcp.merge.defaults == {"$schema": "https://example.com/schema.json"}
    stdio = mcp.merge.transports["stdio"]
    assert stdio == TransportSpec(
        type_value="local",
        command_format="array",
        env_key="environment",
    )
    assert mcp.merge.transports["http"].url_key == "httpUrl"
    assert mcp.merge.transports["sse"].type_value == "remote"


def test_transport_defaults_applied() -> None:
    target = parse_target_def(
        {
            "mcp": {
                "kind": "edit-file",
                "path": ".x.json",
                "merge": {
                    "servers_key": "mcpServers",
                    "transports": {"stdio": {}},
                },
            },
        }
    )
    mcp = target.resources["mcp"]
    assert isinstance(mcp, EditFileResource)
    stdio = mcp.merge.transports["stdio"]
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


def test_arbitrary_resource_type_parses() -> None:
    """Resource type names are open — any name with a valid kind is accepted."""
    target = parse_target_def(
        {"rules": {"kind": "copy-file", "path": ".mytool/rules"}}
    )
    assert target.resources["rules"] == CopyFileResource(path=".mytool/rules")


def test_non_mapping_resource_value_raises() -> None:
    with pytest.raises(TargetSchemaError, match="expected a mapping"):
        parse_target_def({"bogus": 1})


def test_empty_string_resource_key_raises() -> None:
    with pytest.raises(TargetSchemaError, match="keys must be non-empty"):
        parse_target_def({"": {"kind": "copy-file", "path": ".x"}})


def test_missing_kind_raises() -> None:
    with pytest.raises(TargetSchemaError, match="kind"):
        parse_target_def({"skills": {"path": ".x"}})


def test_invalid_kind_raises() -> None:
    with pytest.raises(TargetSchemaError, match="kind"):
        parse_target_def({"skills": {"kind": "weird", "path": ".x"}})


def test_legacy_layout_field_is_rejected() -> None:
    """The old 'layout: directory|file' form must point users at 'kind:'."""
    with pytest.raises(TargetSchemaError, match="deprecated.*kind"):
        parse_target_def({"skills": {"layout": "directory", "path": ".x"}})


def test_missing_resource_path_raises() -> None:
    with pytest.raises(TargetSchemaError, match="path"):
        parse_target_def({"skills": {"kind": "copy-file"}})


def test_resource_unknown_field_raises() -> None:
    with pytest.raises(TargetSchemaError, match="unknown keys"):
        parse_target_def(
            {"skills": {"kind": "copy-file", "path": ".x", "extra": 1}}
        )


def test_edit_file_unknown_path_extension_raises() -> None:
    """Path without .json/.toml suffix can't infer format."""
    with pytest.raises(TargetSchemaError, match="cannot infer"):
        parse_target_def(
            {
                "mcp": {
                    "kind": "edit-file",
                    "path": ".my-mcp-config",
                    "merge": {"servers_key": "mcpServers"},
                },
            }
        )


def test_explicit_format_field_is_rejected() -> None:
    """Manifests must not declare format: explicitly anywhere."""
    with pytest.raises(TargetSchemaError, match="drop this field"):
        parse_target_def(
            {
                "mcp": {
                    "kind": "edit-file",
                    "path": ".x.json",
                    "format": "json",
                    "merge": {"servers_key": "mcpServers"},
                },
            }
        )


def test_edit_file_missing_merge_raises() -> None:
    with pytest.raises(TargetSchemaError, match="merge"):
        parse_target_def(
            {"mcp": {"kind": "edit-file", "path": ".x.json"}}
        )


def test_unknown_transport_raises() -> None:
    with pytest.raises(TargetSchemaError, match="unknown transport"):
        parse_target_def(
            {
                "mcp": {
                    "kind": "edit-file",
                    "path": ".x.json",
                    "merge": {
                        "servers_key": "mcpServers",
                        "transports": {"websocket": {}},
                    },
                },
            }
        )


def test_invalid_command_format_raises() -> None:
    with pytest.raises(TargetSchemaError, match="command_format"):
        parse_target_def(
            {
                "mcp": {
                    "kind": "edit-file",
                    "path": ".x.json",
                    "merge": {
                        "servers_key": "mcpServers",
                        "transports": {"stdio": {"command_format": "tuple"}},
                    },
                },
            }
        )


def test_type_value_must_be_string_or_null() -> None:
    with pytest.raises(TargetSchemaError, match="type_value"):
        parse_target_def(
            {
                "mcp": {
                    "kind": "edit-file",
                    "path": ".x.json",
                    "merge": {
                        "servers_key": "mcpServers",
                        "transports": {"stdio": {"type_value": 42}},
                    },
                },
            }
        )


def test_transport_unknown_field_raises() -> None:
    with pytest.raises(TargetSchemaError, match="unknown keys"):
        parse_target_def(
            {
                "mcp": {
                    "kind": "edit-file",
                    "path": ".x.json",
                    "merge": {
                        "servers_key": "mcpServers",
                        "transports": {"stdio": {"bogus": 1}},
                    },
                },
            }
        )


def test_merge_unknown_field_raises() -> None:
    with pytest.raises(TargetSchemaError, match="unknown keys"):
        parse_target_def(
            {
                "mcp": {
                    "kind": "edit-file",
                    "path": ".x.json",
                    "merge": {"servers_key": "mcpServers", "bogus": True},
                },
            }
        )


def test_edit_file_with_no_transports_is_valid() -> None:
    target = parse_target_def(
        {
            "mcp": {
                "kind": "edit-file",
                "path": ".x.json",
                "merge": {"servers_key": "mcpServers"},
            },
        }
    )
    mcp = target.resources["mcp"]
    assert isinstance(mcp, EditFileResource)
    assert mcp.merge.transports == {}


def test_context_appears_in_error() -> None:
    with pytest.raises(TargetSchemaError, match="my-source.skills.kind"):
        parse_target_def(
            {"skills": {"kind": "weird", "path": ".x"}},
            context="my-source",
        )
