from __future__ import annotations

from pathlib import Path

from law_mcp.client import FixtureClient
from law_mcp.law_supplement import fetch_law_supplements

FIX = Path(__file__).resolve().parent / "fixtures"
LAW = {"status": "RESOLVED", "provider_id": "283839", "version_id": "283839", "effective_date": "20260911",
       "expected_law_key": "0113572026031021445", "expected_law_id": "011357"}
DECREE = {"status": "RESOLVED", "provider_id": "289537", "version_id": "289537", "effective_date": "20260911",
          "expected_law_key": "0114682026091036671", "expected_law_id": "011468"}


def client() -> FixtureClient:
    return FixtureClient(FIX / "law_supplement_manifest.json")


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node["children"])


def test_addenda_are_split_into_articles_and_rebuild_exactly():
    result = fetch_law_supplements(client(), version_binding=LAW)
    assert result["status"] == "OK" and result["support_eligible"] is True
    addenda = result["addenda"]
    assert len(addenda) == 13
    for add in addenda:
        assert add["structure_derivation"] in {"TEXT_MARKER_SPLIT", "NO_ARTICLE_MARKER"}
        rebuilt = add["text"][slice(*add["head_span"])] + "".join(
            add["text"][slice(*a["span"])] for a in add["articles"])
        assert rebuilt == add["text"]
        for art in add["articles"]:
            pieces = [art["text"][slice(*art["head_span"])]] + [art["text"][slice(*n["span"])]
                                                                  for n in walk(art["structure"])]
            assert "".join(pieces) == art["text"]
    latest = next(a for a in addenda if a["key"] == "2026031021445")
    assert latest["promulgation_date"] == "20260310"
    art3 = next(a for a in latest["articles"] if a["number"] == "3")
    assert art3["title"] == "과징금 부과에 관한 적용례"
    assert [n["label"] for n in art3["structure"]][:3] == ["①", "②", "③"]
    assert result["annexes"] == []


def test_decree_annexes_are_returned_unchanged():
    result = fetch_law_supplements(client(), version_binding=DECREE)
    assert result["status"] == "OK"
    annex = next(a for a in result["annexes"] if a["title"].startswith("과태료의 부과기준"))
    assert annex["number"] == "0002" and annex["kind"] == "별표" and annex["effective_date"] == "20260911"
    assert "안전성 확보에" in annex["text"]
    assert len(result["annexes"]) == 6


def test_wrong_version_key_returns_no_blocks():
    result = fetch_law_supplements(client(), version_binding={**LAW, "expected_law_key": "0113572023031419234"})
    assert result["status"] == "PROVIDER_VERSION_MISMATCH" and "addenda" not in result


def test_unverifiable_and_unresolved_bindings_are_refused():
    no_key = {k: v for k, v in LAW.items() if k != "expected_law_key"}
    assert fetch_law_supplements(client(), version_binding=no_key)["status"] == "TARGET_VERSION_UNVERIFIABLE"
    raw = client()
    assert fetch_law_supplements(raw, version_binding={"status": "UNRESOLVED"})["status"] == "TARGET_VERSION_UNRESOLVED"
    assert raw.calls == []
