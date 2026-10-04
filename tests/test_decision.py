from __future__ import annotations

from pathlib import Path

import pytest

from law_mcp.client import FixtureClient
from law_mcp.decision import fetch_decision
from law_mcp.search import search_documents

FIX = Path(__file__).resolve().parent / "fixtures"


def client() -> FixtureClient:
    return FixtureClient(FIX / "decision_manifest.json")


def test_ppc_body_search_lists_candidates():
    r = search_documents(client(), target="ppc", query="제29조", display=5, search_body=True)
    assert r["status"] == "OK" and r["total_count"] == 873 and len(r["rows"]) == 5
    assert all(row["결정문일련번호"] for row in r["rows"]) and r["support_eligible"] is False


def test_prec_body_search_lists_candidates():
    r = search_documents(client(), target="prec", query="안전성 확보에 필요한 조치", display=5, search_body=True)
    assert r["total_count"] == 213 and {"사건번호", "법원명"} <= set(r["rows"][0])


def test_ppc_decision_fields_verbatim():
    r = fetch_decision(client(), target="ppc", decision_id="4835")
    assert r["status"] == "OK" and r["decision_date"] == "2023.10.11."
    assert {"이유", "의결일자"} <= {f["tag"] for f in r["fields"]}
    reason = next(f["text"] for f in r["fields"] if f["tag"] == "이유")
    assert "舊개인정보 보호법" in reason  # 옛 법 적용 각주가 글 그대로 남는다


def test_prec_decision_has_reference_provisions():
    r = fetch_decision(client(), target="prec", decision_id="618177")
    assert r["status"] == "OK" and r["decision_date"] == "20260226"
    assert any(f["tag"] == "참조조문" and "개인정보 보호법" in f["text"] for f in r["fields"])


def test_decision_rejects_bad_input():
    with pytest.raises(ValueError):
        fetch_decision(client(), target="expc", decision_id="1")
    with pytest.raises(ValueError):
        fetch_decision(client(), target="ppc", decision_id="12a")
