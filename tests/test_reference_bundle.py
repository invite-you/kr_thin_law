from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from law_mcp.reference_bundle import (
    _parse_document,
    extract_same_document_references,
    fetch_law_reference_bundle,
    focused_reverse_paths,
)


ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"


class PairFixtureClient:
    def __init__(self, body: bytes, relations: bytes):
        self.body = body
        self.relations = relations
        self.calls: list[dict[str, str]] = []

    def _call(self, url: str, params: dict[str, object]) -> bytes:
        clean = {key: str(value) for key, value in params.items()}
        self.calls.append(clean)
        if clean["target"] == "eflaw":
            return self.body
        if clean["target"] == "lsDelegated":
            return self.relations
        raise AssertionError(clean)


def _pipa_client() -> PairFixtureClient:
    return PairFixtureClient(
        (FIX / "eflaw_full_283839_pipa.xml").read_bytes(),
        (FIX / "lsDelegated_283839_pipa.xml").read_bytes(),
    )


def _version_selector() -> dict[str, str]:
    return {
        "mode": "version",
        "mst": "283839",
        "effective_date": "20260911",
        "expected_law_key": "0113572026031021445",
        "expected_law_id": "011357",
    }


def test_version_bundle_calls_two_document_level_endpoints_and_adds_body_gap():
    client = _pipa_client()
    out = fetch_law_reference_bundle(
        client,
        selector=_version_selector(),
        text_mode="graph_only",
        focus={"targets": [{"article": 29}], "max_depth": 2},
    )

    assert client.calls == [
        {"target": "eflaw", "MST": "283839", "efYd": "20260911"},
        {"target": "lsDelegated", "MST": "283839"},
    ]
    assert out["status"] == "OK"
    assert out["articles"] == []
    assert out["coverage"]["scope"] == "single_document_version"
    assert out["coverage"]["provider_temporal_fidelity"] == "NOT_GUARANTEED_FOR_VERSION"
    assert out["coverage"]["external_incoming"] == "NOT_SEARCHED"
    assert out["coverage"]["semantic_relations"] == "NOT_EVALUATED"

    gap = [
        edge for edge in out["reference_edges"]
        if edge["source"]["article"] == "35"
        and edge["source"]["branch"] == "2"
        and edge["target"]["article"] == "29"
        and edge["same_document"]
    ]
    assert len(gap) == 1
    assert gap[0]["observed_by"] == ["body_explicit"]

    reverse_ids = out["reverse_index_same_document"]["article:29"]
    incoming_sources = {
        edge["source"]["node_key"]
        for edge in out["reference_edges"]
        if edge["edge_id"] in reverse_ids
    }
    assert "article:35-2" in incoming_sources
    assert out["focused_paths"][0]["target"]["node_key"] == "article:29"
    assert any(
        path["nodes"][0] == "article:35-2"
        for path in out["focused_paths"][0]["paths"]
        if path["depth"] == 1
    )


def test_current_selector_uses_id_without_hidden_effective_date():
    client = _pipa_client()
    out = fetch_law_reference_bundle(
        client,
        selector={"mode": "current", "law_id": "011357"},
        text_mode="referenced_units",
    )
    assert client.calls == [
        {"target": "eflaw", "ID": "011357"},
        {"target": "lsDelegated", "ID": "011357"},
    ]
    assert out["version"]["selector_mode"] == "current"
    assert out["version"]["effective_date"] == "20260911"
    assert out["coverage"]["provider_temporal_fidelity"] == "CURRENT"
    assert 0 < len(out["articles"]) < out["coverage"]["counts"]["document_articles"]


def test_version_selector_requires_independent_version_proof():
    client = _pipa_client()
    with pytest.raises(ValueError, match="expected_law_key or expected_response_sha256"):
        fetch_law_reference_bundle(
            client,
            selector={
                "mode": "version",
                "mst": "283839",
                "effective_date": "20260911",
            },
        )
    assert client.calls == []


def test_version_selector_accepts_response_hash_as_proof():
    body = (FIX / "eflaw_full_283839_pipa.xml").read_bytes()
    client = PairFixtureClient(body, (FIX / "lsDelegated_283839_pipa.xml").read_bytes())
    out = fetch_law_reference_bundle(
        client,
        selector={
            "mode": "version",
            "mst": "283839",
            "effective_date": "20260911",
            "expected_response_sha256": hashlib.sha256(body).hexdigest(),
            "expected_law_id": "011357",
        },
        text_mode="graph_only",
    )
    assert out["status"] == "OK"


def test_text_modes_do_not_repeat_full_document_unless_requested():
    referenced = fetch_law_reference_bundle(
        _pipa_client(), selector=_version_selector(), text_mode="referenced_units"
    )
    full = fetch_law_reference_bundle(
        _pipa_client(), selector=_version_selector(), text_mode="full_document"
    )
    assert len(referenced["articles"]) < len(full["articles"])
    assert len(full["articles"]) == 127


def test_external_and_document_relative_prefixes_are_not_same_document_edges():
    articles = [{
        "number": "1",
        "branch": "",
        "head_text": (
            "제1조(시험) 「형법」 제355조, 법 제29조, 같은 법 제30조, "
            "이 법 제2조 및 제3조를 본다."
        ),
        "structure": [],
    }]
    resolved, unresolved = extract_same_document_references(
        articles, source_title="테스트법"
    )
    targets = {(row["target"]["article"], row["target"]["branch"]) for row in resolved}
    assert targets == {("2", ""), ("3", "")}
    reasons = {row["reason"] for row in unresolved}
    assert "EXTERNAL_NAMED_DOCUMENT" in reasons
    assert "EXTERNAL_DOCUMENT_PREFIX" in reasons
    assert "RELATIVE_DOCUMENT_PREFIX" in reasons


def test_range_expansion_is_mechanical_and_bounded():
    articles = [{
        "number": "10",
        "branch": "",
        "head_text": (
            "제10조(시험) 제66조부터 제68조까지 및 "
            "제43조의2부터 제43조의4까지를 따른다."
        ),
        "structure": [],
    }]
    resolved, unresolved = extract_same_document_references(
        articles, source_title="테스트법"
    )
    assert unresolved == []
    targets = {(row["target"]["article"], row["target"]["branch"]) for row in resolved}
    assert targets == {
        ("66", ""), ("67", ""), ("68", ""),
        ("43", "2"), ("43", "3"), ("43", "4"),
    }
    assert all(row["range_expanded"] for row in resolved)


def test_enforcement_decree_law_prefix_does_not_create_false_same_document_reverse_edge():
    document = _parse_document(
        (FIX / "eflaw_full_289537_pipa_decree.xml").read_bytes(),
        requested_mst="289537",
        effective_date="20260911",
    )
    resolved, unresolved = extract_same_document_references(
        document["articles"], source_title=document["source"]["law"]
    )
    assert not any(
        row["source"]["article"] == "30" and row["target"]["article"] == "29"
        for row in resolved
    )
    assert any(
        row["source"]["article"] == "30"
        and row["reason"] == "EXTERNAL_DOCUMENT_PREFIX"
        and "법 제29조" in row["context"]
        for row in unresolved
    )


@pytest.mark.parametrize(
    ("name", "mst", "date", "minimum_articles"),
    [
        ("eflaw_full_283839_pipa.xml", "283839", "20260911", 120),
        ("eflaw_full_284025_criminal.xml", "284025", "20260312", 390),
        ("eflaw_full_284415_civil.xml", "284415", "20260101", 1100),
        ("eflaw_full_289537_pipa_decree.xml", "289537", "20260911", 70),
    ],
)
def test_full_document_parser_survives_large_real_fixtures(
    name: str, mst: str, date: str, minimum_articles: int
):
    out = _parse_document((FIX / name).read_bytes(), requested_mst=mst, effective_date=date)
    assert len(out["articles"]) >= minimum_articles
    node_keys = [article["node_key"] for article in out["articles"]]
    assert len(node_keys) == len(set(node_keys))
    assert out["meta"]["response_sha256"]


def test_reverse_path_expansion_is_cycle_safe():
    edges = [
        {
            "edge_id": "ref:1",
            "source": {"node_key": "article:2"},
            "target": {"node_key": "article:1"},
            "same_document": True,
        },
        {
            "edge_id": "ref:2",
            "source": {"node_key": "article:1"},
            "target": {"node_key": "article:2"},
            "same_document": True,
        },
    ]
    reverse = {"article:1": ["ref:1"], "article:2": ["ref:2"]}
    paths = focused_reverse_paths(
        "article:1", edges=edges, reverse_index=reverse, max_depth=3
    )
    assert paths == [{
        "depth": 1,
        "nodes": ["article:2", "article:1"],
        "edge_ids": ["ref:1"],
    }]


def test_invalid_focus_and_text_mode_fail_before_io():
    client = _pipa_client()
    with pytest.raises(ValueError, match="text_mode"):
        fetch_law_reference_bundle(
            client, selector=_version_selector(), text_mode="everything"
        )
    assert client.calls == []

    with pytest.raises(ValueError, match="max_depth"):
        fetch_law_reference_bundle(
            client,
            selector=_version_selector(),
            focus={"targets": [{"article": 29}], "max_depth": 4},
        )
    assert client.calls == []
