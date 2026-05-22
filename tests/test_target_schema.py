"""Tests for the target manifest parser and validator."""

from __future__ import annotations

import pytest

from agpack.kinds import CopyDirectoryResource
from agpack.kinds import CopyFileResource
from agpack.kinds import EditFileResource
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
        "mcp": {"kind": "edit-file", "path": ".demo/mcp.json"},
        "settings": {"kind": "edit-file", "path": ".demo/settings.json"},
    }

    target = parse_target_def(raw)

    assert isinstance(target, TargetDef)
    assert target.resources["skills"] == CopyDirectoryResource(path=".demo/skills")
    assert target.resources["commands"] == CopyFileResource(path=".demo/commands")
    assert target.resources["agents"] == CopyFileResource(path=".demo/agents")
    mcp = target.resources["mcp"]
    assert isinstance(mcp, EditFileResource)
    assert mcp.path == ".demo/mcp.json"
    assert mcp.format == "json"


def test_non_mapping_raises() -> None:
    with pytest.raises(TargetSchemaError, match="expected a mapping"):
        parse_target_def("not a dict")


def test_arbitrary_resource_type_parses() -> None:
    """Resource type names are open — any name with a valid kind works."""
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


def test_legacy_merge_field_is_rejected() -> None:
    """The old 'merge:' block on edit-file resources is rejected with a hint."""
    with pytest.raises(TargetSchemaError, match="removed.*Patches"):
        parse_target_def(
            {
                "mcp": {
                    "kind": "edit-file",
                    "path": ".x.json",
                    "merge": {"servers_key": "mcpServers"},
                }
            }
        )


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
            {"mcp": {"kind": "edit-file", "path": ".my-mcp-config"}}
        )


def test_edit_file_with_vars_parses() -> None:
    target = parse_target_def(
        {
            "mcp": {
                "kind": "edit-file",
                "path": ".mcp.json",
                "vars": {"bucket": "mcpServers"},
            }
        }
    )
    mcp = target.resources["mcp"]
    assert isinstance(mcp, EditFileResource)
    assert mcp.vars == {"bucket": "mcpServers"}


def test_edit_file_vars_must_be_mapping() -> None:
    with pytest.raises(TargetSchemaError, match="vars: must be a mapping"):
        parse_target_def(
            {
                "mcp": {
                    "kind": "edit-file",
                    "path": ".mcp.json",
                    "vars": ["not", "a", "mapping"],
                }
            }
        )


def test_edit_file_var_value_must_be_string() -> None:
    with pytest.raises(TargetSchemaError, match="value must be a string"):
        parse_target_def(
            {
                "mcp": {
                    "kind": "edit-file",
                    "path": ".mcp.json",
                    "vars": {"bucket": 42},
                }
            }
        )


def test_context_appears_in_error() -> None:
    with pytest.raises(TargetSchemaError, match="my-source.skills.kind"):
        parse_target_def(
            {"skills": {"kind": "weird", "path": ".x"}},
            context="my-source",
        )
