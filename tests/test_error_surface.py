"""Regression tests for the v4.1 fix pass.

Covers the error surface (Fix 1), mechanical family precedence (Fix 2 / C-3)
and article-selection robustness (Fix 3 / C-2), plus the target-segment header
inheritance boundary that must never leak across segments.
"""
import pytest

from law_mcp.card_source import (
    ProviderResponseError,
    parse_eflaw_article_xml,
    parse_lsdelegated_xml,
)


EFLAW_OK = """<?xml version="1.0" encoding="utf-8"?>
<법령 법령키="00999920260101100"><기본정보>
<법령ID>009999</법령ID><공포일자>20260101</공포일자><공포번호>100</공포번호>
<법종구분 법종구분코드="A0002">법률</법종구분><법령명_한글>테스트법</법령명_한글><시행일자>20260101</시행일자>
</기본정보><조문>
<조문단위 조문키="0003000"><조문번호>3</조문번호><조문여부>전문</조문여부><조문내용>전문</조문내용></조문단위>
<조문단위 조문키="0003001"><조문번호>3</조문번호><조문여부>조문</조문여부>
<조문제목>적용 제외</조문제목><조문시행일자>20260101</조문시행일자><조문내용>제3조(적용 제외)</조문내용>
</조문단위>
<조문단위 조문키="0003002"><조문번호>3</조문번호><조문여부>조문</조문여부>
<조문시행일자>20260201</조문시행일자><조문내용>제3조(적용 제외) 개정</조문내용>
</조문단위>
</조문></법령>"""


EFLAW_ONE = """<?xml version="1.0" encoding="utf-8"?>
<법령 법령키="00999920260101100"><기본정보>
<법령ID>009999</법령ID><공포일자>20260101</공포일자><공포번호>100</공포번호>
<법종구분 법종구분코드="A0002">법률</법종구분><법령명_한글>테스트법</법령명_한글><시행일자>20260101</시행일자>
</기본정보><조문>
<조문단위 조문키="0003000"><조문번호>3</조문번호><조문여부>전문</조문여부><조문내용>전문</조문내용></조문단위>
<조문단위 조문키="0003001"><조문번호>3</조문번호><조문여부>조문</조문여부>
<조문제목>적용 제외</조문제목><조문시행일자>20260101</조문시행일자><조문내용>제3조(적용 제외)</조문내용>
</조문단위>
</조문></법령>"""


LS_HEADER = """<법령>
<법령정보><법령일련번호>1</법령일련번호><법령ID>000001</법령ID><법령명>기본법</법령명>
<공포일자>20200101</공포일자><공포번호>1</공포번호><시행일자>20200701</시행일자></법령정보>"""


def _parse_eflaw(xml, article=3, branch=None):
    return parse_eflaw_article_xml(
        xml, mst="1", effective_date=None, article=article, branch=branch
    )


def test_empty_payload_is_empty_response_and_retryable():
    for parse in (
        lambda: _parse_eflaw(b""),
        lambda: parse_lsdelegated_xml(b""),
    ):
        with pytest.raises(ProviderResponseError) as exc_info:
            parse()
        err = exc_info.value
        assert err.error_code == "EMPTY_RESPONSE"
        assert err.retryable is True
        assert isinstance(err, ValueError)


def test_html_error_page_is_html_response_and_retryable():
    payload = b"<html><body>502 Bad Gateway</body></html>"
    for parse in (
        lambda: _parse_eflaw(payload),
        lambda: parse_lsdelegated_xml(payload),
    ):
        with pytest.raises(ProviderResponseError) as exc_info:
            parse()
        err = exc_info.value
        assert err.error_code == "HTML_RESPONSE"
        assert err.retryable is True
        assert "502" in err.raw_preview


def test_truncated_xml_is_parse_error_and_retryable():
    for parse in (
        lambda: _parse_eflaw("<법령><기본정보><법령명>형법".encode("utf-8")),
        lambda: parse_lsdelegated_xml("<법령><법령정보><법령명>형법".encode("utf-8")),
    ):
        with pytest.raises(ProviderResponseError) as exc_info:
            parse()
        err = exc_info.value
        assert err.error_code == "PARSE_ERROR"
        assert err.retryable is True


def test_explicit_error_root_is_provider_error_not_retryable():
    payload = "<에러><메시지>없음</메시지></에러>"
    with pytest.raises(ProviderResponseError) as exc_info:
        _parse_eflaw(payload)
    assert exc_info.value.error_code == "PROVIDER_DECLARED_ERROR"
    assert exc_info.value.retryable is False
    assert exc_info.value.provider_response == payload

    with pytest.raises(ProviderResponseError) as exc_info:
        parse_lsdelegated_xml(payload)
    assert exc_info.value.error_code == "PROVIDER_DECLARED_ERROR"
    assert exc_info.value.retryable is False
    assert exc_info.value.provider_response == payload


def test_missing_basic_info_is_missing_structure():
    with pytest.raises(ProviderResponseError) as exc_info:
        _parse_eflaw("<법령><조문/></법령>")
    assert exc_info.value.error_code == "MISSING_STRUCTURE"
    assert exc_info.value.retryable is False


def test_article_not_found_is_structured():
    with pytest.raises(ProviderResponseError) as exc_info:
        _parse_eflaw(EFLAW_ONE, article=9)
    err = exc_info.value
    assert err.error_code == "ARTICLE_NOT_FOUND"
    assert err.retryable is False
    assert "제9조" in err.detail


def test_ambiguous_article_carries_candidates_and_ignores_jeonmun():
    # EFLAW_OK has one 전문 unit and two non-전문 units with number 3.
    with pytest.raises(ProviderResponseError) as exc_info:
        _parse_eflaw(EFLAW_OK)
    err = exc_info.value
    assert err.error_code == "AMBIGUOUS_ARTICLE"
    assert err.retryable is False
    assert [c["key"] for c in err.candidates] == ["0003001", "0003002"]
    assert all(c["number"] == "3" for c in err.candidates)
    assert err.candidates[0]["head_text"].startswith("제3조")
    # The unique non-전문 selection still succeeds.
    out = _parse_eflaw(EFLAW_ONE)
    assert out["article"]["key"] == "0003001"


def test_record_tag_family_beats_field_named_segment_header():
    # C-3 regression: the segment header declares a law target via 위임법령*
    # fields, but the record is wrapped in the institution-rule record tag and
    # carries its own institution-rule target fields. Mechanical precedence
    # (record tag > record's own fields > segment header) must label it
    # institution_rule with its own observed target.
    xml = LS_HEADER + """
<위임조문정보><조정보><조문번호>5</조문번호><조문가지번호>0</조문가지번호><조문제목>위임</조문제목></조정보>
<위임정보><위임구분>위임</위임구분>
<위임법령일련번호>100</위임법령일련번호><위임법령제목>시행령</위임법령제목>
<조항호목>제5조</조항호목><링크텍스트>법령으로 정하는</링크텍스트><라인텍스트>라인1</라인텍스트>
<위임규정조문정보><위임규정일련번호>400</위임규정일련번호><위임규정제목>기관규정</위임규정제목>
<조항호목>제6조</조항호목><링크텍스트>규정으로 정하는</링크텍스트><라인텍스트>라인2</라인텍스트>
</위임규정조문정보>
</위임정보></위임조문정보></법령>"""
    parsed = parse_lsdelegated_xml(xml)
    refs = parsed["references"]
    assert len(refs) == 2
    first, second = refs
    # The pending inline payload after the law header belongs to that header.
    assert first["api_family"] == "law"
    assert first["observed_target_seq"] == "100"
    assert first["observed_target_title"] == "시행령"
    # The record tag and its own target fields beat the field-derived header.
    assert second["api_family"] == "institution_rule"
    assert second["observed_target_seq"] == "400"
    assert second["observed_target_title"] == "기관규정"


def test_no_cross_family_header_inheritance():
    # An institution-rule record WITHOUT its own target fields must stay empty
    # rather than inherit the law segment header (same-family inheritance only).
    xml = LS_HEADER + """
<위임조문정보><조정보><조문번호>5</조문번호><조문가지번호>0</조문가지번호><조문제목>위임</조문제목></조정보>
<위임정보><위임구분>위임</위임구분>
<위임법령일련번호>100</위임법령일련번호><위임법령제목>시행령</위임법령제목>
<조항호목>제5조</조항호목><링크텍스트>법령으로 정하는</링크텍스트><라인텍스트>라인1</라인텍스트>
<위임규정조문정보><조항호목>제6조</조항호목><링크텍스트>규정으로 정하는</링크텍스트><라인텍스트>라인2</라인텍스트>
</위임규정조문정보>
</위임정보></위임조문정보></법령>"""
    parsed = parse_lsdelegated_xml(xml)
    refs = parsed["references"]
    assert len(refs) == 2
    second = refs[1]
    assert second["api_family"] == "institution_rule"
    assert second["observed_target_seq"] == ""
    assert second["observed_target_title"] == ""
    assert second["observed_target_seq"] != "100"


def test_segment_header_inheritance_stops_at_segment_boundary():
    xml = LS_HEADER + """
<위임조문정보><조정보><조문번호>7</조문번호><조문가지번호>0</조문가지번호><조문제목>인용</조문제목></조정보>
<위임정보><위임구분>인용법령</위임구분>
<위임법령일련번호>100</위임법령일련번호><위임법령제목>법A</위임법령제목>
<위임법령조문정보><조항호목>제7조</조항호목><링크텍스트>법A 링크</링크텍스트></위임법령조문정보>
<위임법령일련번호>200</위임법령일련번호><위임법령제목>법B</위임법령제목>
<위임법령조문정보><조항호목>제8조</조항호목><링크텍스트>법B 링크</링크텍스트></위임법령조문정보>
</위임정보></위임조문정보></법령>"""
    parsed = parse_lsdelegated_xml(xml)
    refs = parsed["references"]
    assert len(refs) == 2
    first, second = refs
    # Within a segment, records without their own target fields inherit the
    # provider-declared segment header values.
    assert first["observed_target_seq"] == "100"
    assert first["observed_target_title"] == "법A"
    # A different seq in the same delegate starts a new segment; the second
    # record must never inherit the first segment's target.
    assert second["observed_target_seq"] == "200"
    assert second["observed_target_title"] == "법B"
    assert first["provider_target_segment_index"] != second["provider_target_segment_index"]
