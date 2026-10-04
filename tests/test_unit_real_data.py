"""Per-unit TCs on REAL captured data (2026-09-25 sweep).

One test per adapter unit where feasible, with pinned expected values taken
from actual law.go.kr responses (형법/민법/개인정보 보호법/근로기준법 등).
TC IDs: TC-UNIT-* (see docs/TC_MATRIX.md).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from law_mcp.capture_ledger import CaptureLedger
from law_mcp.card_source import (
    _direct_leaf_map,
    _node_text,
    _norm_branch,
    _preview,
    _require_xml_payload,
    _select_article,
    _text,
    encode_jo,
    fetch_article,
    fetch_references,
    parse_eflaw_article_xml,
    parse_lsdelegated_xml,
    project_article,
    project_references,
    ProviderResponseError,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --------------------------------------------------------------- locator units
def test_unit_encode_jo_real_locators():
    """TC-UNIT-ENC-001: 실제 조문의 locator 인코딩 (형법 제99조 / 시행령 제4조의2 / 민법 제312조의2)."""
    assert encode_jo(99) == "009900"
    assert encode_jo(4, 2) == "000402"
    assert encode_jo(312, 2) == "031202"


def test_unit_norm_branch_real_values():
    """TC-UNIT-BRANCH-001: 실측 조문가지번호 값의 정규화 ("", "0", "2", 콤마 배열은 비손상)."""
    assert _norm_branch("") == ""
    assert _norm_branch("0") == ""
    assert _norm_branch("2") == "2"
    assert _norm_branch("0,0,0") == "0,0,0"  # 배열 원문은 손대지 않음


# ------------------------------------------------------ eflaw parsing (real)
def test_unit_eflaw_criminal_art99_pinned():
    """TC-UNIT-EFLAW-001: 형법 제99조 실데이터 고정값."""
    out = parse_eflaw_article_xml(
        _read("eflaw_284025_criminal_art99.xml"),
        mst="284025", effective_date="20260913", article=99,
    )
    assert out["article"]["key"] == "0099001"
    assert out["article"]["number"] == "99"
    assert out["article"]["title"] == "일반이적"
    assert out["article"]["head_text"] == (
        "제99조(일반이적) 제92조부터 제98조까지에 기재한 이외에 대한민국의 군사상 이익을 "
        "해하거나 적국에 군사상 이익을 공여한 자는 무기 또는 3년 이상의 징역에 처한다."
    )
    assert out["source"]["law"] == "형법"
    assert out["source"]["promulgation_number"] == "21450"
    assert out["source"]["effective_date"] == "20260913"
    assert out["source"]["article_effective_date_text"] == "20260312:제123조의2"
    assert out["meta"]["text_sha256"].startswith("824142e2dd42")


def test_unit_eflaw_pipa_art2_pinned():
    """TC-UNIT-EFLAW-002: 개인정보 보호법 제2조 — 투명 <항> wrapper 아래 호 10개 실데이터."""
    out = parse_eflaw_article_xml(
        _read("eflaw_283839_pipa_art2.xml"),
        mst="283839", effective_date="20260911", article=2,
    )
    assert out["article"]["key"] == "0002001"
    assert out["article"]["head_text"].startswith("제2조(정의) 이 법에서 사용하는 용")
    top = out["article"]["structure"]
    assert len(top) == 10
    assert all(n["type"] == "item" for n in top)
    assert top[0]["label"] == "1."
    assert top[0]["text"].startswith('1. "개인정보"란 살아 있는 개인에 관한 정보로서 다')


def test_unit_eflaw_civil_art312_branch2_pinned():
    """TC-UNIT-EFLAW-003: 민법 제312조의2(전세금 증감청구권) 가지조문 실데이터."""
    out = parse_eflaw_article_xml(
        _read("eflaw_284415_civil_art312b2.xml"),
        mst="284415", effective_date="20260317", article=312, branch="2",
    )
    assert out["article"]["key"] == "0312021"
    assert out["article"]["number"] == "312"
    assert out["article"]["branch"] == "2"
    assert out["article"]["title"] == "전세금 증감청구권"
    assert out["article"]["head_text"].startswith("제312조의2(전세금 증감청구권) 전세금이 목적 부동산")
    assert out["source"]["law"] == "민법"
    assert out["article"]["structure"] == []


def test_unit_structure_ladder_real_labels():
    """TC-UNIT-STRUCT-001: 조→항→호→목 사다리 라벨 실데이터 (① / 2. / 다.)."""
    out = parse_eflaw_article_xml(
        _read("josub_04_eflawjosub_full_ladder_MOK.xml"),
        mst="193412", effective_date="20171019", article=3,
    )
    p = out["article"]["structure"][0]
    i = p["children"][0]
    m = i["children"][0]
    assert (p["type"], p["label"]) == ("paragraph", "①")
    assert (i["type"], i["label"]) == ("item", "2.")
    assert (m["type"], m["label"]) == ("subitem", "다.")
    assert m["text"].endswith("다. 플랫폼")


# -------------------------------------------------- lsDelegated parsing (real)
def test_unit_lsdelegated_criminal_range_record_pinned():
    """TC-UNIT-REF-001: 형법 제365조 범위 참조 레코드 고정값 (C-4 실데이터)."""
    refs = parse_lsdelegated_xml(_read("lsDelegated_284025_hyungbeob.xml"))["references"]
    hit = next(r for r in refs if r["linked_article"] == "362,363,364")
    assert hit["source_article"] == "365"
    assert hit["at"] == "제365조제1항"
    assert hit["text"] == "제362조부터 제364조"
    assert hit["line"] == "제362조부터 제364조"
    assert hit["kind"] == "인용법령"
    assert hit["linked_branch"] == "0,0,0"
    assert hit["linked_article_title"] == ""
    assert hit["observed_target_seq"] == "282557"
    assert hit["observed_target_title"] == "형법"


def test_unit_lsdelegated_pipa_source_identity_pinned():
    """TC-UNIT-REF-002: 개인정보 보호법 시행령 source 정체성 고정값."""
    parsed = parse_lsdelegated_xml(_read("lsDelegated_289537.xml"))
    assert parsed["source"]["seq"] == "289537"
    assert parsed["source"]["law_id"] == "011468"
    assert parsed["source"]["title"] == "개인정보 보호법 시행령"
    assert parsed["source"]["effective_date"] == "20260911"


def test_unit_project_references_real_target_pinned():
    """TC-UNIT-PROJ-001: 형법 제365조 projection의 target 필드 고정값."""
    out = project_references(
        parse_lsdelegated_xml(_read("lsDelegated_284025_hyungbeob.xml")), article=365
    )
    refs = [r for r in out["references"] if r["target"]["linked_article"] == "362,363,364"]
    assert refs
    t = refs[0]["target"]
    assert t["api_family"] == "law"
    assert t["observed_seq"] == "282557"
    assert t["observed_title"] == "형법"
    assert t["linked_branch"] == "0,0,0"
    assert t["linked_article_title"] == ""


# ------------------------------------------------------- P0-C family flag
def test_unit_family_ambiguous_all_false_on_live_captures():
    """TC-UNIT-FAM-001: 실측 lsDelegated는 전 레코드가 record-tag 판정 → family_ambiguous 전부 false."""
    for name in (
        "lsDelegated_289537.xml",
        "lsDelegated_284025_hyungbeob.xml",
        "lsDelegated_283457_labor.xml",
        "lsDelegated_276839_environment.xml",
    ):
        refs = parse_lsdelegated_xml(_read(name))["references"]
        assert refs
        assert all(r["family_ambiguous"] is False for r in refs), name


def test_unit_family_ambiguous_true_for_untagged_inline_record():
    """TC-UNIT-FAM-002: 레코드 태그 없는 인라인 레코드(조약 계열)는 ambiguous=true (C-3 잔여 노출)."""
    xml = """<법령>
<법령정보><법령일련번호>1</법령일련번호><법령ID>000001</법령ID><법령명>기본법</법령명>
<공포일자>20200101</공포일자><공포번호>1</공포번호><시행일자>20200701</시행일자></법령정보>
<위임조문정보><조정보><조문번호>25</조문번호><조문제목>위임</조문제목></조정보>
<위임정보><위임구분>조약</위임구분><조약일련번호>500</조약일련번호><조약제목>협정</조약제목>
<조항호목>제25조제5항</조항호목><링크텍스트>협정에 따른</링크텍스트></위임정보></위임조문정보></법령>"""
    refs = parse_lsdelegated_xml(xml)["references"]
    assert len(refs) == 1
    assert refs[0]["api_family"] == "treaty"
    assert refs[0]["family_ambiguous"] is True


# --------------------------------------------------- small parser units (real)
def test_unit_text_and_node_text_real_nodes():
    """TC-UNIT-TEXT-001: _text/_node_text의 실데이터 공백 처리."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(_read("eflaw_284025_criminal_art99.xml"))
    basic = root.find("./기본정보")
    assert _text(basic, "법령명_한글") == "형법"
    assert _text(basic, "없는태그") == ""
    assert _node_text(basic.find("./법종구분")) == "법률"


def test_unit_direct_leaf_map_real_record():
    """TC-UNIT-LEAF-001: _direct_leaf_map는 직속 leaf만 읽고 빈 값도 보존한다."""
    import xml.etree.ElementTree as ET

    xml = ("<r><위임규정일련번호>400</위임규정일련번호><위임규정제목></위임규정제목>"
           "<nested><x>1</x></nested></r>")
    el = ET.fromstring(xml)
    m = _direct_leaf_map(el)
    assert m == {"위임규정일련번호": "400", "위임규정제목": ""}
    assert "nested" not in m


def test_unit_preview_and_payload_guard_real_probes():
    """TC-UNIT-GUARD-001: 실제 probe payload에 대한 가드 동작."""
    assert _preview(b"abc" * 1000) == "abc" * 166 + "ab"  # 500 bytes cap
    _require_xml_payload(_read("eflaw_284025_criminal_art99.xml"))  # 정상 통과
    with pytest.raises(ProviderResponseError) as exc_info:
        _require_xml_payload(_read("probe_unknown_mst_lsDelegated.xml"))
    assert exc_info.value.error_code == "HTML_RESPONSE"
    with pytest.raises(ProviderResponseError) as exc_info:
        _require_xml_payload(b"")
    assert exc_info.value.error_code == "EMPTY_RESPONSE"


def test_unit_select_article_real_shape():
    """TC-UNIT-SEL-001: _select_article은 실데이터 형태 dict에서 조문만 고른다."""
    content = {
        "articles": [
            {"번호": "29", "가지번호": "", "제목": "", "구분": "전문", "조문키": "0029000"},
            {"번호": "29", "가지번호": "", "제목": "양벌규정", "구분": "조문", "조문키": "0029001"},
        ]
    }
    art = _select_article(content, 29)
    assert art["조문키"] == "0029001"


# --------------------------------------------------- fetch wrappers (real bytes)
class _RealArticleClient:
    def _call(self, url, params):
        self.params = params
        return _read("eflaw_284025_criminal_art99.xml")


class _RealRefClient:
    def _call(self, url, params):
        self.params = params
        return _read("lsDelegated_284025_hyungbeob.xml")


def test_unit_fetch_article_real_request_shape():
    """TC-UNIT-FETCH-001: fetch_article의 실측 요청 파라미터와 출력 고정값."""
    client = _RealArticleClient()
    out = fetch_article(client, mst="284025", effective_date="20260913", article=99)
    assert client.params == {
        "target": "eflaw", "MST": "284025", "efYd": "20260913", "JO": "009900"
    }
    assert out["article"]["title"] == "일반이적"
    assert out["meta"]["clock_source"] == "local"
    assert out["meta"]["text_sha256"].startswith("824142e2dd42")


def test_unit_fetch_references_real_request_shape():
    """TC-UNIT-FETCH-002: fetch_references의 실측 요청 파라미터와 출력 고정값."""
    client = _RealRefClient()
    out = fetch_references(client, mst="284025", article=365)
    assert client.params == {"target": "lsDelegated", "MST": "284025"}
    t = next(
        r["target"] for r in out["references"]
        if r["target"]["linked_article"] == "362,363,364"
    )
    assert t["observed_seq"] == "282557"
    assert out["meta"]["clock_source"] == "local"


# ------------------------------------------------- ledger on real bytes
def test_unit_ledger_real_fixture_bytes(tmp_path):
    """TC-UNIT-LEDGER-001: 실 fixture 바이트의 원장 기록 — sha가 파일 hash와 일치."""
    raw = _read("lsDelegated_284025_hyungbeob.xml")
    ledger = CaptureLedger(tmp_path / "ledger.jsonl")
    row = ledger.record(
        url="https://www.law.go.kr/DRF/lawService.do",
        request={"target": "lsDelegated", "MST": "284025"},
        raw=raw,
        retrieved_at="2026-09-25T00:00:00+00:00",
    )
    assert row["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert row["bytes"] == len(raw)


def test_unit_request_key_real_params():
    """TC-UNIT-BATCH-002: 실측 요청 파라미터의 키 안정성(순서 무관)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from batch_ingest import request_key

    a = request_key("u", {"OC": "test", "target": "lsDelegated", "MST": "289537"})
    b = request_key("u", {"MST": "289537", "target": "lsDelegated", "OC": "test"})
    c = request_key("u", {"OC": "test", "target": "lsDelegated", "MST": "283839"})
    assert a == b
    assert a != c
