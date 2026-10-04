"""Detailed live-coverage regression TCs (2026-09-25 broad live sweep).

Every case runs OFFLINE on fixtures captured from the official law.go.kr DRF
endpoints (OC=test) by validation/live_sweep_20260925.py. TC IDs are mapped to
contract clauses in docs/TC_MATRIX.md.

Coverage axes:
  TC-SEARCH-*  search row parsing / current-version selection
  TC-REF-*     lsDelegated reference observations (counts, families, order, loss rules)
  TC-EFLAW-*   eflaw article projection (identity, temporal raw text, official date preference)
  TC-PROJ-*    projection filters
  TC-ERR-*     live error-envelope mappings of the §6 error contract
  TC-JOSUB-*   josub endpoint shape documentation (future tool surface)
"""
from __future__ import annotations

import functools
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from law_mcp.card_source import (
    ProviderResponseError,
    parse_eflaw_article_xml,
    parse_lsdelegated_xml,
    project_references,
)

FIXTURES = Path(__file__).parent / "fixtures"

# (statute, slug, mst, expected_effective_date)
SEARCH_TABLE = [
    ("민법", "civil", "284415", "20260317"),
    ("형법", "criminal", "284025", "20260913"),
    ("개인정보 보호법", "pipa", "283839", "20260911"),
    ("근로기준법", "labor", "283457", "20260820"),
    ("행정절차법", "admin_proc", "239291", "20230324"),
    ("지방자치법", "localgov", "284005", "20260701"),
    ("건축법", "building", "273437", "20260227"),
    ("국가재정법", "finance", "283845", "20260911"),
    ("조세범 처벌법", "tax_penalty", "224875", "20210101"),
    ("환경정책기본법", "environment", "276839", "20260102"),
    ("민사소송법", "civil_proc", "252393", "20250712"),
]

# (statute, slug, mst, records, families, comma_range_records)
REF_TABLE = [
    ("민법", "civil", "284415", 27, {"law": 27}, 0),
    ("형법", "criminal", "284025", 29, {"law": 29}, 10),
    ("개인정보 보호법", "pipa", "283839", 761, {"law": 713, "admin_rule": 48}, 4),
    ("근로기준법", "labor", "283457", 229, {"law": 203, "admin_rule": 1, "institution_rule": 25}, 2),
    ("행정절차법", "admin_proc", "239291", 85, {"law": 85}, 1),
    ("지방자치법", "localgov", "284005", 15760, {"law": 397, "ordinance": 15363}, 2),
    ("건축법", "building", "273437", 5854, {"law": 1280, "ordinance": 4561, "admin_rule": 13}, 27),
    ("국가재정법", "finance", "283845", 336, {"law": 323, "admin_rule": 4, "institution_rule": 9}, 2),
    ("조세범 처벌법", "tax_penalty", "224875", 88, {"law": 88}, 3),
    ("환경정책기본법", "environment", "276839", 361, {"law": 222, "admin_rule": 6, "ordinance": 124, "institution_rule": 9}, 2),
]

# (statute, fixture, mst, article, branch, has_temporal_text, official_effective_date)
EFLAW_TABLE = [
    ("민법", "eflaw_284415_civil_art312b2.xml", "284415", "312", "2", False, "20260317"),
    ("형법", "eflaw_284025_criminal_art99.xml", "284025", "99", "", True, "20260913"),
    ("개인정보 보호법", "eflaw_283839_pipa_art2.xml", "283839", "2", "", True, "20260911"),
    ("근로기준법", "eflaw_283457_labor_art11.xml", "283457", "11", "", False, "20260820"),
    ("행정절차법", "eflaw_239291_admin_proc_art3.xml", "239291", "3", "", True, "20230324"),
    ("지방자치법", "eflaw_284005_localgov_art3.xml", "284005", "3", "", False, "20260701"),
    ("건축법", "eflaw_273437_building_art2.xml", "273437", "2", "", False, "20260227"),
    ("국가재정법", "eflaw_283845_finance_art4.xml", "283845", "4", "", False, "20260911"),
    ("조세범 처벌법", "eflaw_224875_tax_penalty_art3.xml", "224875", "3", "", False, "20210101"),
    ("환경정책기본법", "eflaw_276839_environment_art4.xml", "276839", "4", "", True, "20260102"),
    ("민사소송법", "eflaw_252393_civil_proc_art1.xml", "252393", "1", "", False, "20250712"),
]


@functools.lru_cache(maxsize=None)
def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@functools.lru_cache(maxsize=None)
def _parsed_refs(mst: str, slug: str) -> dict:
    return parse_lsdelegated_xml(_read(f"lsDelegated_{mst}_{slug}.xml"))


def _family_counts(refs):
    out = {}
    for r in refs:
        out[r["api_family"]] = out.get(r["api_family"], 0) + 1
    return out


# ---------------------------------------------------------------- TC-SEARCH
@pytest.mark.parametrize("name,slug,mst,eff", SEARCH_TABLE, ids=[s for _, s, _, _ in SEARCH_TABLE])
def test_tc_search_001_exact_current_row_and_identity(name, slug, mst, eff):
    """TC-SEARCH-001: 검색 fixture에 동일 법령명 + 현행 행이 있고 MST/시행일자가 일치한다."""
    root = ET.fromstring(_read(f"search_{slug}.xml"))
    rows = [
        {n.tag: (n.text or "").strip() for n in list(r) if len(n) == 0}
        for r in root.findall(".//law")
    ]
    exact = [r for r in rows if r.get("법령명한글") == name and r.get("현행연혁코드") == "현행"]
    assert exact, f"no exact current row for {name}"
    picked = max(exact, key=lambda r: int(r.get("법령일련번호") or 0))
    assert picked["법령일련번호"] == mst
    assert picked["시행일자"] == eff
    assert re.fullmatch(r"\d{8}", picked["시행일자"])
    assert re.fullmatch(r"\d+", picked["법령ID"])


def test_tc_search_002_commercial_search_ranking_limitation():
    """TC-SEARCH-002: '상법' 검색은 상위 20행에 정확 일치 행을 주지 않는다(provider 검색 랭킹 특성).

    어댑터 결함이 아니며, 쿼리 '상법'이 '…보상법'류에 부분 매칭되어 밀려난 현상을 고정해
    provider 행동이 바뀌면 깨지는 감시 테스트로 둔다.
    """
    root = ET.fromstring(_read("search_commercial.xml"))
    names = [
        ((n.text or "").strip())
        for r in root.findall(".//law")
        for n in list(r)
        if n.tag == "법령명한글"
    ]
    assert names, "search rows missing"
    assert "상법" not in names
    assert any("보상법" in nm for nm in names), "expected substring-ranked rows (보상법)"


# ------------------------------------------------------------------ TC-REF
@pytest.mark.parametrize("name,slug,mst,records,families,ranges", REF_TABLE, ids=[s for _, s, *_ in REF_TABLE])
def test_tc_ref_001_exact_counts_and_families(name, slug, mst, records, families, ranges):
    """TC-REF-001: lsDelegated 레코드 수/파생 family 분포가 캡처 시점과 정확히 일치한다(무손실 고정)."""
    refs = _parsed_refs(mst, slug)["references"]
    assert len(refs) == records
    assert _family_counts(refs) == families


@pytest.mark.parametrize("name,slug,mst,records,families,ranges", REF_TABLE, ids=[s for _, s, *_ in REF_TABLE])
def test_tc_ref_002_order_preserved_and_no_unknown_fields(name, slug, mst, records, families, ranges):
    """TC-REF-002: delegate 내 record 순서 보존 + 미맵핑 필드 없음."""
    parsed = _parsed_refs(mst, slug)
    assert parsed["meta"]["unknown_leaf_fields"] == []
    per_delegate = {}
    for r in parsed["references"]:
        key = (r["provider_source_group_index"], r["provider_delegate_index"])
        per_delegate.setdefault(key, []).append(r["provider_record_index"])
    for idxs in per_delegate.values():
        assert idxs == sorted(idxs)


@pytest.mark.parametrize("name,slug,mst,records,families,ranges", [row for row in REF_TABLE if row[5] > 0], ids=[s for _, s, *_ in [row for row in REF_TABLE if row[5] > 0]])
def test_tc_loss_001_comma_ranges_never_expanded(name, slug, mst, records, families, ranges):
    """TC-LOSS-001: 범위 참조값('362,363,364' 등)이 개별 edge로 전개되지 않고 보존된다.

    provider의 배열/스칼라 형상은 비대칭일 수 있다(linked_article 배열 + linked_branch
    스칼라 '0' 관측). 손실 방지는 "있는 그대로 보존"이지 형상 정렬이 아니다.
    """
    refs = _parsed_refs(mst, slug)["references"]
    comma = [r for r in refs if "," in (r.get("linked_article") or "")]
    assert len(comma) == ranges
    for r in comma:
        arts = r["linked_article"].split(",")
        assert len(arts) >= 2
        assert all(a.strip() for a in arts), "no empty member inside a preserved range"
        br = r.get("linked_branch") or ""
        if "," in br:
            # 배열-배열 쌍은 provider가 정렬해 주므로 길이가 맞아야 한다.
            assert len(arts) == len(br.split(","))
        else:
            # 배열-스칼라 비대칭 형상은 스칼라를 그대로 유지해야 한다.
            assert br in ("", "0") or br.strip().isdigit()


def test_tc_loss_003_asymmetric_range_shape_preserved_verbatim():
    """TC-LOSS-003: linked_article 배열 + linked_branch 스칼라('0') 비대칭 형상이 실재하며
    문자열 그대로 보존된다(형법/개인정보 보호법 라이브 캡처)."""
    refs = _parsed_refs("283839", "pipa")["references"]
    asymmetric = [
        r for r in refs
        if "," in (r.get("linked_article") or "") and "," not in (r.get("linked_branch") or "")
    ]
    assert asymmetric, "expected asymmetric array/scalar range shapes in the pipa capture"
    for r in asymmetric:
        assert r["linked_branch"] == "0"
        assert "," in r["linked_article"]


def test_tc_loss_002_empty_official_values_not_filled_from_siblings():
    """TC-LOSS-002: 빈 linked_article_title은 형제 레코드 제목으로 채워지지 않는다(형법 범위 레코드)."""
    refs = _parsed_refs("284025", "criminal")["references"]
    empties = [r for r in refs if r.get("linked_article_title") == ""]
    assert empties, "expected empty official titles in the criminal-code capture"
    titled = [r for r in refs if r.get("linked_article_title")]
    assert titled, "fixture must contain titled siblings to make this meaningful"
    for r in empties:
        assert r["linked_article_title"] == ""


# ----------------------------------------------------------------- TC-EFLAW
@pytest.mark.parametrize("name,fixture,mst,article,branch,has_temporal,eff", EFLAW_TABLE, ids=[s[1] for s in EFLAW_TABLE])
def test_tc_eflaw_001_identity_and_official_date_preference(name, fixture, mst, article, branch, has_temporal, eff):
    """TC-EFLAW-001: source.effective_date는 공식 시행일자를 유지하고, 요청값은 meta에만 남는다."""
    out = parse_eflaw_article_xml(
        _read(fixture), mst=mst, effective_date="19990101", article=article, branch=branch or None
    )
    assert out["source"]["law"] == name
    assert out["source"]["mst"] == mst
    assert out["source"]["effective_date"] == eff
    assert out["meta"]["requested_effective_date"] == "19990101"
    assert out["meta"]["response_sha256"]
    assert out["article"]["number"] == article
    assert out["article"]["branch"] == (branch or "")


@pytest.mark.parametrize("name,fixture,mst,article,branch,has_temporal,eff", EFLAW_TABLE, ids=[s[1] for s in EFLAW_TABLE])
def test_tc_eflaw_002_temporal_text_raw_or_empty(name, fixture, mst, article, branch, has_temporal, eff):
    """TC-EFLAW-002: 조문시행일자문자열은 미해석 원문으로 존재하거나 빈 값 그대로 보존된다."""
    out = parse_eflaw_article_xml(
        _read(fixture), mst=mst, effective_date=eff, article=article, branch=branch or None
    )
    text = out["source"]["article_effective_date_text"]
    if has_temporal:
        assert text, f"{name}: expected raw temporal text"
        assert re.match(r"\d{8}:", text), "raw provider form starts with YYYYMMDD:"
        assert out["source"]["annex_effective_date_text"] is not None
    else:
        assert text == "", f"{name}: empty official value must stay empty"


# ------------------------------------------------------ TC-PROJ / grouping
def test_tc_proj_001_article_and_branch_filters_narrow_output():
    """TC-PROJ-001: projection 필터가 해당 조문/가지로만 좁혀지고 전개는 하지 않는다."""
    parsed = _parsed_refs("273437", "building")  # ranges 27, refs 5854
    out = project_references(parsed, article=2)
    assert out["references"]
    assert all(r["source_article"] == "2" for r in out["references"])
    out2 = project_references(parsed, article=2, branch="")
    assert all(r["source_branch"] == "" for r in out2["references"])


def test_tc_grp_001_provider_group_identity_consistent():
    """TC-GRP-001: 같은 provider_group의 모든 조각은 동일 source/target 식별자를 가진다."""
    parsed = _parsed_refs("276839", "environment")
    out = project_references(parsed)
    groups = {}
    for r in out["references"]:
        groups.setdefault(r["provider_group"], []).append(r)
    assert len(groups) > 50
    for members in groups.values():
        identities = {
            (
                m["source_article"], m["source_branch"], m["at"], m.get("context", ""),
                m["target"]["api_family"], m["target"]["observed_seq"], m["target"]["observed_title"],
            )
            for m in members
        }
        assert len(identities) == 1


# ------------------------------------------------------------- TC-ERR (live)
def test_tc_err_live_001_unknown_mst_eflaw_maps_provider_nodata_envelope():
    """TC-ERR-LIVE-001: 미등록 MST eflaw 응답은 provider 무데이터 봉투(<Law>…)로, §6 UNEXPECTED_ROOT에 매핑된다."""
    with pytest.raises(ProviderResponseError) as exc_info:
        parse_eflaw_article_xml(_read("probe_unknown_mst_eflaw.xml"), mst="99999999", effective_date="20260101", article=1)
    err = exc_info.value
    assert err.error_code == "UNEXPECTED_ROOT"
    assert err.retryable is False
    assert "일치하는" in err.raw_preview
    assert "법령명을 확인" in err.raw_preview


def test_tc_err_live_002_unknown_mst_lsdelegated_maps_html_page():
    """TC-ERR-LIVE-002: 미등록 MST lsDelegated 응답은 HTML 오류 페이지로, HTML_RESPONSE(retryable)에 매핑된다."""
    with pytest.raises(ProviderResponseError) as exc_info:
        parse_lsdelegated_xml(_read("probe_unknown_mst_lsDelegated.xml"))
    err = exc_info.value
    assert err.error_code == "HTML_RESPONSE"
    assert err.retryable is True
    assert "<html" in err.raw_preview.lower()


def test_tc_err_live_003_past_efyd_maps_provider_nodata_envelope():
    """TC-ERR-LIVE-003: 범위 밖 efYd(19000101) 요청도 provider 무데이터 봉투로 회신된다(버전 미스와 동일 경로)."""
    with pytest.raises(ProviderResponseError) as exc_info:
        parse_eflaw_article_xml(_read("probe_past_efyd_283839.xml"), mst="283839", effective_date="19000101", article=1)
    assert exc_info.value.error_code == "UNEXPECTED_ROOT"


def test_tc_err_live_004_wrong_version_efyd_maps_provider_nodata_envelope():
    """TC-ERR-LIVE-004: efYd가 실제 판본과 어긋나면(형법 20260911 vs 20260913) 무데이터 봉투가 온다."""
    with pytest.raises(ProviderResponseError) as exc_info:
        parse_eflaw_article_xml(_read("probe_bogus_article_284025.xml"), mst="284025", effective_date="20260911", article=99)
    assert exc_info.value.error_code == "UNEXPECTED_ROOT"


def test_tc_err_live_005_http500_body_is_html_shaped():
    """TC-ERR-LIVE-005: 민사소송법 lsDelegated는 HTTP 500 + HTML 바디를 준다(전송 계층 오류, 재현 가능).

    HTTP 상태 자체는 클라이언트 계층(UPSTREAM_INTEGRATION) 책임이며, 바디 형태만 여기서 고정한다.
    """
    raw = _read("probe_lsdelegated_252393_civil_proc_http500.xml")
    with pytest.raises(ProviderResponseError) as exc_info:
        parse_lsdelegated_xml(raw)
    assert exc_info.value.error_code == "HTML_RESPONSE"
    assert exc_info.value.retryable is True


# ----------------------------------------------------------- TC-JOSUB (docs)
@pytest.mark.parametrize("fixture", ["probe_eflawjosub_283839.xml", "probe_lawjosub_283839.xml"])
def test_tc_josub_001_endpoint_shape_documented(fixture):
    """TC-JOSUB-001: josub 계열 endpoint는 법령 루트 + 기본정보/조문 구조를 반환한다(계약 외 계열, 형상 문서화)."""
    root = ET.fromstring(_read(fixture))
    assert root.tag == "법령"
    tags = [c.tag for c in list(root)]
    assert "기본정보" in tags
    assert "조문" in tags
