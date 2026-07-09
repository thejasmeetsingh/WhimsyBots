"""Tests for 'src/mcp_tools/tools.py'.

The registry module centralises the tool definitions exposed to the
model. It maps function names to their callables in 'mcp_tools' and
declares the JSON schemas used to advertise each tool to the LLM.

These tests cover:
  - The FUNCTION_NAME_TO_CALLABLE_MAP is complete and points to the
    same callables that the underlying modules expose.
  - Each advertised tool group ('CRON_JOB_TOOLS', 'WEB_SEARCH_TOOLS',
    'PDF_GENERATOR_TOOLS') is shaped like an OpenAI function tool
    (type='function', with name/description/parameters).
  - The required fields for mutating tools are correctly flagged.
"""

from __future__ import annotations

import inspect

import pytest

from mcp_tools import cron_job, pdf_generator, web_search
from mcp_tools.tools import (
    CREATE_CRON,
    CRON_JOB_TOOLS,
    DELETE_CRON,
    FETCH_AND_EXTRACT,
    FUNCTION_NAME_TO_CALLABLE_MAP,
    GENERATE_PDF,
    LIST_CRONS,
    PDF_GENERATOR_TOOLS,
    UPDATE_CRON,
    WEB_SEARCH,
    WEB_SEARCH_TOOLS,
)

# ──────────────────────────────────────────────
# Function-name constants
# ──────────────────────────────────────────────


def test_list_crons_constant_value():
    assert LIST_CRONS == "list_cron_jobs"


def test_create_cron_constant_value():
    assert CREATE_CRON == "create_cron_job"


def test_update_cron_constant_value():
    assert UPDATE_CRON == "update_cron_job"


def test_delete_cron_constant_value():
    assert DELETE_CRON == "delete_cron_job"


def test_web_search_constant_value():
    assert WEB_SEARCH == "web_search"


def test_fetch_and_extract_constant_value():
    assert FETCH_AND_EXTRACT == "fetch_and_extract"


def test_generate_pdf_constant_value():
    assert GENERATE_PDF == "generate_and_send_report"


# ──────────────────────────────────────────────
# Function-name → callable map
# ──────────────────────────────────────────────


def test_callable_map_keys_match_documented_constants():
    """Every well-known function name appears in the map."""
    expected = {
        LIST_CRONS,
        CREATE_CRON,
        UPDATE_CRON,
        DELETE_CRON,
        WEB_SEARCH,
        FETCH_AND_EXTRACT,
        GENERATE_PDF,
    }
    assert set(FUNCTION_NAME_TO_CALLABLE_MAP.keys()) == expected


def test_callable_map_points_to_actual_module_callables():
    """Each mapped callable is the very same function object exported
    by the corresponding sub-module — no wrappers, no surprises.
    """
    assert FUNCTION_NAME_TO_CALLABLE_MAP[LIST_CRONS] is cron_job.list
    assert FUNCTION_NAME_TO_CALLABLE_MAP[CREATE_CRON] is cron_job.create
    assert FUNCTION_NAME_TO_CALLABLE_MAP[UPDATE_CRON] is cron_job.update
    assert FUNCTION_NAME_TO_CALLABLE_MAP[DELETE_CRON] is cron_job.delete
    assert FUNCTION_NAME_TO_CALLABLE_MAP[WEB_SEARCH] is web_search.web_search
    assert FUNCTION_NAME_TO_CALLABLE_MAP[FETCH_AND_EXTRACT] is web_search.fetch_and_extract
    assert FUNCTION_NAME_TO_CALLABLE_MAP[GENERATE_PDF] is pdf_generator.generate_and_send_report


def test_all_callables_are_callable():
    """Sanity check — every value in the map is actually callable."""
    for name, fn in FUNCTION_NAME_TO_CALLABLE_MAP.items():
        assert callable(fn), f"{name} is not callable"


# ──────────────────────────────────────────────
# Tool groups — schema shape
# ──────────────────────────────────────────────


def _function(tool: dict) -> dict:
    """Helper to pull the inner 'function' dict from a tool spec."""
    return tool["function"]


@pytest.mark.parametrize("tool_group", [CRON_JOB_TOOLS, WEB_SEARCH_TOOLS, PDF_GENERATOR_TOOLS])
def test_tool_group_is_well_formed_function_spec(tool_group):
    """Every tool in every group has the OpenAI-compatible shape:
    'type'='function' plus a 'function' dict with name/description/parameters.
    """
    assert len(tool_group) >= 1
    for tool in tool_group:
        assert tool["type"] == "function"
        fn = _function(tool)
        assert "name" in fn
        assert "description" in fn
        assert "parameters" in fn
        assert fn["parameters"]["type"] == "object"


# ──────────────────────────────────────────────
# CRON_JOB_TOOLS — required fields
# ──────────────────────────────────────────────


def test_cron_tools_list_crons_spec():
    tool = next(t for t in CRON_JOB_TOOLS if _function(t)["name"] == LIST_CRONS)
    # is_active is optional.
    assert "is_active" not in _function(tool)["parameters"].get("required", [])


def test_cron_tools_create_requires_name_description_cron_expression():
    tool = next(t for t in CRON_JOB_TOOLS if _function(t)["name"] == CREATE_CRON)
    required = set(_function(tool)["parameters"]["required"])
    assert required == {"name", "description", "cron_expression"}


def test_cron_tools_update_requires_id_only():
    """Update is a partial update — only the id is required."""
    tool = next(t for t in CRON_JOB_TOOLS if _function(t)["name"] == UPDATE_CRON)
    required = set(_function(tool)["parameters"]["required"])
    assert required == {"id"}


def test_cron_tools_delete_requires_id():
    tool = next(t for t in CRON_JOB_TOOLS if _function(t)["name"] == DELETE_CRON)
    required = set(_function(tool)["parameters"]["required"])
    assert required == {"id"}


# ──────────────────────────────────────────────
# WEB_SEARCH_TOOLS — required fields
# ──────────────────────────────────────────────


def test_web_search_tool_requires_query():
    tool = next(t for t in WEB_SEARCH_TOOLS if _function(t)["name"] == WEB_SEARCH)
    required = set(_function(tool)["parameters"]["required"])
    assert required == {"query"}


def test_fetch_and_extract_tool_requires_url():
    tool = next(t for t in WEB_SEARCH_TOOLS if _function(t)["name"] == FETCH_AND_EXTRACT)
    required = set(_function(tool)["parameters"]["required"])
    assert required == {"url"}


# ──────────────────────────────────────────────
# PDF_GENERATOR_TOOLS — required fields
# ──────────────────────────────────────────────


def test_pdf_generator_tool_requires_contents():
    tool = PDF_GENERATOR_TOOLS[0]
    assert _function(tool)["name"] == GENERATE_PDF
    required = set(_function(tool)["parameters"]["required"])
    assert required == {"contents"}


# ──────────────────────────────────────────────
# Sanity: every advertised function name has a description
# ──────────────────────────────────────────────


def test_every_advertised_tool_has_non_empty_description():
    """Empty descriptions hide the tool's intent from the model."""
    for tool in CRON_JOB_TOOLS + WEB_SEARCH_TOOLS + PDF_GENERATOR_TOOLS:
        desc = _function(tool).get("description", "")
        assert desc.strip(), f"Empty description for {_function(tool)['name']}"


def test_callable_signatures_have_expected_params():
    """The map's callables accept the same arguments advertised in
    their JSON schema. We don't enforce type compatibility — just that
    the callable's signature has matching parameter names for the
    required fields. This is a smoke test for drift between schema
    and implementation.
    """
    # Pick one tool from each group and verify.
    create_tool = next(t for t in CRON_JOB_TOOLS if _function(t)["name"] == CREATE_CRON)
    sig = inspect.signature(cron_job.create)
    expected = set(_function(create_tool)["parameters"]["required"])
    assert expected.issubset(set(sig.parameters.keys()))

    pdf_tool = PDF_GENERATOR_TOOLS[0]
    sig = inspect.signature(pdf_generator.generate_and_send_report)
    required = set(_function(pdf_tool)["parameters"]["required"])
    assert required.issubset(set(sig.parameters.keys()))
