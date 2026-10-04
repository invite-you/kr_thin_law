from __future__ import annotations

import io
from pathlib import Path

import pytest

from law_mcp.admin_article import fetch_admin_rule_articles
from law_mcp.card_source import SEARCH_URL, parse_eflaw_article_xml
from law_mcp.client import FixtureClient, OfficialClient
from law_mcp.flat_structure import StructureDerivationError, derive_article
from law_mcp.search import search_documents

FIX = Path(__file__).resolve().parent / "fixtures"
CURRENT = {"status": "RESOLVED", "provider_id": "2100000281400", "version_id": "2100000281400",
           "effective_date": "20260701"}


def fixture_client() -> FixtureClient:
    return FixtureClient(FIX / "admrul_safety_manifest.json")


def walk(nodes, depth=0):
    for node in nodes:
        yield depth, node
        yield from walk(node["children"], depth + 1)


def test_search_lists_versions_without_selecting():
    result = search_documents(fixture_client(), target="admrul", query="개인정보의 안전성 확보조치 기준",
                              include_history=True)
    assert result["status"] == "OK" and result["support_eligible"] is False
    numbers = {row["발령번호"] for row in result["rows"] if row["행정규칙ID"] == "73493"}
    assert {"2023-6", "2025-9", "2026-9"} <= numbers
    assert result["total_count"] == len(result["rows"]) == 11


def test_search_rejects_unknown_target():
    with pytest.raises(ValueError):
        search_documents(fixture_client(), target="lsHistory", query="x")


def test_credential_echoed_in_search_links_is_redacted(tmp_path):
    secret = "private-credential"
    body = f"<LawSearch><totalCnt>0</totalCnt><link>/DRF/lawService.do?OC={secret}</link></LawSearch>".encode()

    class Response(io.BytesIO):
        status = 200

    client = OfficialClient(secret, capture_dir=tmp_path, opener=lambda request, timeout: Response(body))
    raw = client._call(SEARCH_URL, {"target": "admrul", "query": "x"})
    assert secret.encode() not in raw and b"OC=REDACTED" in raw
    for path in tmp_path.iterdir():
        assert secret.encode() not in path.read_bytes()


def test_redaction_never_alters_body_text_equal_to_credential():
    secret = "usb"  # 짧은 이용값이 본문 낱말과 같아도 본문은 그대로여야 한다
    body = f"<r><link>/DRF/lawService.do?OC={secret}&amp;x=1</link><t>usb 메모리</t></r>".encode()

    class Response(io.BytesIO):
        status = 200

    client = OfficialClient(secret, opener=lambda request, timeout: Response(body))
    raw = client._call(SEARCH_URL, {"target": "admrul", "query": "x"})
    assert b"<t>usb \xeb\xa9\x94\xeb\xaa\xa8\xeb\xa6\xac</t>" in raw and b"OC=usb" not in raw


def test_admin_rule_articles_use_law_shape_and_rebuild_exactly():
    result = fetch_admin_rule_articles(fixture_client(), version_binding=CURRENT)
    assert result["status"] == "OK" and result["support_eligible"] is True
    articles = result["articles"]
    assert len(articles) == 20  # 제1~19조 + 제6조의2
    assert [h["text"] for h in result["headings"]][0].startswith("제1장")
    for art in articles:
        slices = [art["text"][slice(*art["head_span"])]]
        slices += [art["text"][slice(*n["span"])] for _, n in walk(art["structure"])]
        assert "".join(slices) == art["text"]
    art8 = next(a for a in articles if a["number"] == "8")
    assert art8["head_text"] == "제8조(접속기록의 보관 및 점검)"
    shape = [(d, n["type"], n["label"]) for d, n in walk(art8["structure"])]
    assert shape == [(0, "paragraph", "①"), (1, "item", "1."), (1, "item", "2."), (1, "item", "3."),
                     (0, "paragraph", "②"), (0, "paragraph", "③")]
    assert art8["structure"][0]["text"].startswith("① 개인정보처리자는")
    assert any(a["number"] == "6" and a["branch"] == "2" for a in articles)


def test_admin_rule_article_locator_and_missing_article():
    one = fetch_admin_rule_articles(fixture_client(), version_binding=CURRENT, article=18)
    assert [a["title"] for a in one["articles"]] == ["공공시스템운영기관의 취약점 점검 및 조치"]
    old = {"status": "RESOLVED", "provider_id": "2100000229672", "version_id": "2100000229672",
           "effective_date": "20230922"}
    missing = fetch_admin_rule_articles(fixture_client(), version_binding=old, article=19)
    assert missing["status"] == "LOCATOR_NOT_FOUND" and missing["support_eligible"] is False


def test_admin_rule_unverified_version_is_not_structured():
    result = fetch_admin_rule_articles(fixture_client(), version_binding={**CURRENT, "effective_date": "20250101"})
    assert result["status"] != "OK" and "articles" not in result


def test_derived_shape_matches_law_article_shape():
    raw = (FIX / "eflaw_full_283839_pipa.xml").read_bytes()
    law = parse_eflaw_article_xml(raw, mst="283839", effective_date=None, article=15, branch=None)["article"]
    derived = derive_article(law["text"])
    strip = lambda nodes: [(n["type"], n["label"], n["text"], strip(n["children"])) for n in nodes]
    assert derived["head_text"] == law["head_text"]
    assert strip(derived["structure"]) == strip(law["structure"])


def test_text_without_heading_is_rejected():
    with pytest.raises(StructureDerivationError):
        derive_article("① 제목 없는 글")
