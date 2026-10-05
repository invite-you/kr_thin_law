from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import Client

from law_mcp.client import FixtureClient
from law_mcp.server import create_server


ROOT = Path(__file__).resolve().parents[1]


def test_real_mcp_protocol_tools_and_source_guard():
    async def run():
        raw_client = FixtureClient(ROOT / "samples/replay_manifest.json", allowed_root=ROOT)
        server = create_server(raw_client)
        async with Client(server) as client:
            result = await client.list_tools()
            assert {tool.name for tool in result.tools} == {
                "law_article", "law_references", "law_reference_bundle", "source_fragment",
                "context_packet", "validate_context_packet",
                "law_search", "admin_rule_article", "law_supplements", "decision_document",
            }
            bundle_tool = next(tool for tool in result.tools if tool.name == "law_reference_bundle")
            bundle_schema = bundle_tool.input_schema
            selector_schema = bundle_schema["properties"]["selector"]
            assert selector_schema["discriminator"]["propertyName"] == "mode"
            assert len(selector_schema["oneOf"]) == 2
            assert bundle_schema["properties"]["text_mode"]["enum"] == [
                "graph_only", "referenced_units", "full_document",
            ]
            result = await client.call_tool("law_article", {
                "mst": "283839", "effective_date": "20260911", "article": 2,
                "expected_law_key": "0113572026031021445", "expected_law_id": "011357",
            })
            assert not result.is_error
            assert result.structured_content["status"] == "OK"
            assert result.structured_content["execution_mode"] == "FIXTURE_REPLAY"
            assert result.structured_content["support_eligible"] is True
            result = await client.call_tool("source_fragment", {
                "api_family": "institution_rule", "version_binding": {"status": "UNRESOLVED"},
            })
            assert result.structured_content["status"] == "TARGET_VERSION_UNRESOLVED"
            assert len(raw_client.calls) == 1
            result = await client.call_tool("validate_context_packet", {"packet": {}})
            assert result.structured_content["status"] == "FAIL"
            assert result.structured_content["semantic_approval"] is False

    asyncio.run(run())
