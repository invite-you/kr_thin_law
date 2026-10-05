from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import Client

from law_mcp.server import create_server


ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"


class PairFixtureClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.body = (FIX / "eflaw_full_283839_pipa.xml").read_bytes()
        self.relations = (FIX / "lsDelegated_283839_pipa.xml").read_bytes()

    def _call(self, url: str, params: dict[str, object]) -> bytes:
        clean = {key: str(value) for key, value in params.items()}
        self.calls.append(clean)
        if clean["target"] == "eflaw":
            return self.body
        if clean["target"] == "lsDelegated":
            return self.relations
        raise AssertionError(clean)


def test_mcp_wire_accepts_valid_version_selector_and_reaches_provider():
    async def run() -> None:
        upstream = PairFixtureClient()
        server = create_server(upstream)
        async with Client(server) as client:
            result = await client.call_tool(
                "law_reference_bundle",
                {
                    "selector": {
                        "mode": "version",
                        "mst": "283839",
                        "effective_date": "20260911",
                        "expected_law_key": "0113572026031021445",
                        "expected_law_id": "011357",
                    },
                    "text_mode": "graph_only",
                },
            )
            assert not result.is_error, result
            assert result.structured_content["status"] == "OK"
            assert upstream.calls == [
                {"target": "eflaw", "MST": "283839", "efYd": "20260911"},
                {"target": "lsDelegated", "MST": "283839"},
            ]

    asyncio.run(run())


def test_mcp_wire_accepts_valid_current_selector_and_reaches_provider():
    async def run() -> None:
        upstream = PairFixtureClient()
        server = create_server(upstream)
        async with Client(server) as client:
            result = await client.call_tool(
                "law_reference_bundle",
                {
                    "selector": {
                        "mode": "current",
                        "law_id": "011357",
                    },
                    "text_mode": "graph_only",
                },
            )
            assert not result.is_error, result
            assert result.structured_content["status"] == "OK"
            assert upstream.calls == [
                {"target": "eflaw", "ID": "011357"},
                {"target": "lsDelegated", "ID": "011357"},
            ]

    asyncio.run(run())
