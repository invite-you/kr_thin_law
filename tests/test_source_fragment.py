from __future__ import annotations

import hashlib

import pytest

from law_mcp.card_source import ProviderResponseError
from law_mcp.source_fragment import fetch_source_fragment, parse_provider_fragment_xml


LAW_XML = '''<?xml version="1.0" encoding="utf-8"?>
<법령 법령키="0113572026031021445"><기본정보><법령ID>011357</법령ID>
<공포일자>20260310</공포일자><시행일자>20260401</시행일자>
<법령명_한글>개인정보 보호법</법령명_한글></기본정보><조문><조문단위 조문키="0002000">
<조문번호>2</조문번호><조문가지번호>0</조문가지번호><조문여부>조문</조문여부>
<조문제목>정의</조문제목><조문시행일자>20260401</조문시행일자><조문내용>제2조 내용</조문내용>
<항><항번호>1</항번호><항내용>첫째 항</항내용><호><호번호>1</호번호><호내용>첫째 호</호내용></호></항>
</조문단위></조문></법령>'''.encode("utf-8")


def _provider_xml(*, identity="9001", sequence="9001", effective="20260401", issued="20260310"):
    return f'''<AdmRulService><행정규칙><행정규칙ID>{identity}</행정규칙ID>
<행정규칙일련번호>{sequence}</행정규칙일련번호><행정규칙명>시험 행정규칙</행정규칙명>
<발령일자>{issued}</발령일자><시행일자>{effective}</시행일자><조문>
<조문내용>본문</조문내용><부칙><부칙내용>부칙 원문</부칙내용></부칙>
<별표><별표번호>별표 1</별표번호><별표내용>별표 원문</별표내용></별표>
</조문></행정규칙></AdmRulService>'''.encode("utf-8")


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _call(self, url, params):
        self.calls.append((url, dict(params)))
        return self.responses.pop(0)


def _binding(**changes):
    result = {
        "status": "RESOLVED", "provider_id": "9001", "version_id": "v-9001-20260401",
        "effective_date": "20260401", "provider_id_param": "ID",
    }
    result.update(changes)
    return result


def test_p0_admin_rule_contract_and_raw_provenance():
    client = FakeClient([_provider_xml()])
    result = fetch_source_fragment(client, api_family="admin_rule", observed_identity="old-observed",
                                   version_binding=_binding())
    assert result["status"] == "OK" and result["support_eligible"] is True
    assert result["source"]["issued_date"] == "20260310"
    assert result["source"]["effective_date"] == "20260401"
    assert result["blocks"] and result["attachments"][0]["provider_tag"] == "별표"
    assert result["provider_tree"]["provider_tag"] == "AdmRulService"
    raw = result["meta"]["raw_xml"].encode("utf-8")
    assert result["meta"]["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["meta"]["raw_bytes_base64"]
    assert client.calls[0][1] == {"target": "admrul", "ID": "9001", "type": "XML"}
    assert result["observed_identity"] == "old-observed"


def test_p0_ordinance_version_and_dates_are_separate():
    xml = """<법령><자치법규일련번호>2023421</자치법규일련번호><자치법규ID>2251458</자치법규ID>
    <자치법규명>조례</자치법규명><공포일자>20241230</공포일자><시행일자>20241231</시행일자>
    <조내용>조례 본문</조내용></법령>""".encode("utf-8")
    client = FakeClient([xml])
    result = fetch_source_fragment(client, api_family="ordinance", observed_identity="2023999",
        version_binding=_binding(provider_id="2023421", provider_id_param="MST", version_id="v-ordin-1",
                                 effective_date="20241231"))
    assert result["status"] == "OK"
    assert result["source"]["issued_date"] == "20241230"
    assert result["source"]["effective_date"] == "20241231"


def test_p0_treaty_compares_effective_not_signed_date():
    xml = """<조약><조약일련번호>983</조약일련번호><조약명_한글>협정</조약명_한글>
    <서명일자>20041026</서명일자><발효일자>20041217</발효일자><조약내용>본문</조약내용></조약>""".encode("utf-8")
    client = FakeClient([xml])
    result = fetch_source_fragment(client, api_family="treaty", observed_identity="983",
                                   version_binding=_binding(provider_id="983", version_id="trty-v1",
                                                            effective_date="20041217"))
    assert result["status"] == "OK" and result["support_eligible"]
    assert result["source"]["issued_date"] == "20041026"
    assert result["source"]["effective_date"] == "20041217"


def test_p0_institution_rule_does_not_infer_effective_date_from_issued_date():
    client = FakeClient([_provider_xml(effective="")])
    result = fetch_source_fragment(client, api_family="institution_rule", observed_identity="7000",
        version_binding=_binding(provider_target="school", provider_id="9001", version_id="school-v1"))
    assert result["status"] == "EFFECTIVE_DATE_NOT_MACHINE_READABLE"
    assert result["support_eligible"] is False
    assert result["source"]["issued_date"] == "20260310"
    assert result["source"]["effective_date"] == ""
    assert result["blocks"] and result["meta"]["raw_xml"]
    assert result["source_completeness"]["complete"] is True
    assert result["structural_context"]["preserved"] is True


def test_p0_unresolved_institution_subtype_fails_before_io():
    client = FakeClient([_provider_xml()])
    result = fetch_source_fragment(client, api_family="institution_rule", observed_identity="x",
                                   version_binding=_binding())
    assert result["status"] == "TARGET_SUBTYPE_UNRESOLVED"
    assert result["support_eligible"] is False
    assert client.calls == []


def test_p0_unresolved_version_fails_before_io_and_observed_is_not_binding():
    client = FakeClient([_provider_xml()])
    result = fetch_source_fragment(client, api_family="admin_rule", observed_identity="9001",
        version_binding={"status": "OPEN", "provider_id": "9001", "effective_date": "20260401"})
    assert result["status"] == "TARGET_VERSION_UNRESOLVED"
    assert client.calls == []


def test_p0_provider_identity_mismatch_is_ineligible():
    client = FakeClient([_provider_xml(sequence="different")])
    result = fetch_source_fragment(client, api_family="admin_rule", observed_identity="9001",
                                   version_binding=_binding())
    assert result["status"] == "PROVIDER_IDENTITY_MISMATCH"
    assert result["support_eligible"] is False
    assert result["meta"]["raw_xml"]


def test_p0_unsupported_identity_parameter_fails_before_io():
    client = FakeClient([_provider_xml()])
    result = fetch_source_fragment(client, api_family="treaty", observed_identity="x",
        version_binding=_binding(provider_id_param="LID"))
    assert result["status"] == "IDENTITY_PARAMETER_UNSUPPORTED"
    assert client.calls == []


def test_stable_lid_requires_and_checks_resolved_provider_sequence():
    client = FakeClient([_provider_xml(identity="doc-1", sequence="9001")])
    result = fetch_source_fragment(client, api_family="admin_rule", observed_identity="9001",
        version_binding=_binding(provider_id="doc-1", provider_id_param="LID",
                                 expected_provider_sequence="9001", version_id="v-9001"))
    assert result["status"] == "OK"
    assert client.calls[0][1]["LID"] == "doc-1"

    no_sequence_client = FakeClient([_provider_xml(identity="doc-1", sequence="9001")])
    no_sequence = fetch_source_fragment(no_sequence_client, api_family="admin_rule", observed_identity="9001",
        version_binding=_binding(provider_id="doc-1", provider_id_param="LID", version_id="v-9001"))
    assert no_sequence["status"] == "TARGET_VERSION_UNRESOLVED"
    assert no_sequence_client.calls == []


def test_stable_ordinance_id_requires_and_checks_sequence():
    xml = """<법령><자치법규ID>doc-1</자치법규ID><자치법규일련번호>2023421</자치법규일련번호>
    <자치법규명>조례</자치법규명><시행일자>20241231</시행일자><조내용>본문</조내용></법령>""".encode("utf-8")
    client = FakeClient([xml])
    result = fetch_source_fragment(client, api_family="ordinance", observed_identity="2023421",
        version_binding=_binding(provider_id="doc-1", provider_id_param="ID", version_id="v-ordin",
                                 expected_provider_sequence="2023421", effective_date="20241231"))
    assert result["status"] == "OK"


def test_provider_sequence_and_effective_date_mismatch_fail_closed():
    sequence_result = parse_provider_fragment_xml(_provider_xml(identity="doc-1", sequence="9999"), api_family="admin_rule",
        resolved_identity="doc-1", id_param="LID", provider_target="admrul",
        expected_effective_date="20260401", expected_sequence="9001", version_id="v1")
    assert sequence_result["status"] == "PROVIDER_VERSION_MISMATCH"
    assert not sequence_result["support_eligible"]

    date_result = parse_provider_fragment_xml(_provider_xml(effective="20260402"), api_family="admin_rule",
        resolved_identity="9001", id_param="ID", provider_target="admrul",
        expected_effective_date="20260401", expected_sequence="9001", version_id="v1")
    assert date_result["status"] == "EFFECTIVE_DATE_MISMATCH"
    assert not date_result["support_eligible"]


def test_law_requires_explicit_version_evidence_and_compares_real_source_fields():
    base = {"status": "RESOLVED", "provider_id": "283839", "version_id": "pipa-v1",
            "effective_date": "20260401", "law_id": "011357"}
    client = FakeClient([LAW_XML])
    unverifiable = fetch_source_fragment(client, api_family="law", observed_identity="213857",
        version_binding=base, locator={"article": 2})
    assert unverifiable["status"] == "TARGET_VERSION_UNVERIFIABLE"
    assert not unverifiable["support_eligible"]
    assert unverifiable["source"]["law_key"] == "0113572026031021445"
    assert client.calls

    client = FakeClient([LAW_XML])
    bound = fetch_source_fragment(client, api_family="law", observed_identity="213857",
        version_binding={**base, "expected_law_key": "0113572026031021445"}, locator={"article": 2})
    assert bound["status"] == "OK" and bound["support_eligible"]
    assert bound["source"]["effective_date"] == "20260401"
    assert bound["source"]["law_id"] == "011357"
    assert bound["blocks"][0]["structure"]


def test_law_effective_date_mismatch_and_law_key_mismatch_are_ineligible():
    base = {"status": "RESOLVED", "provider_id": "283839", "version_id": "v1",
            "effective_date": "20260402", "expected_law_key": "0113572026031021445"}
    date_mismatch = fetch_source_fragment(FakeClient([LAW_XML]), api_family="law", observed_identity="x",
                                          version_binding=base, locator={"article": 2})
    assert date_mismatch["status"] == "EFFECTIVE_DATE_MISMATCH"
    assert not date_mismatch["support_eligible"]

    key_mismatch = fetch_source_fragment(FakeClient([LAW_XML]), api_family="law", observed_identity="x",
        version_binding={**base, "effective_date": "20260401", "expected_law_key": "wrong"},
        locator={"article": 2})
    assert key_mismatch["status"] == "PROVIDER_VERSION_MISMATCH"
    assert not key_mismatch["support_eligible"]


def test_returned_mst_does_not_replace_independent_law_key_evidence():
    raw = LAW_XML.replace("<기본정보>".encode("utf-8"), "<기본정보><MST>283839</MST>".encode("utf-8"))
    result = fetch_source_fragment(FakeClient([raw]), api_family="law", observed_identity=None,
        version_binding={"status": "RESOLVED", "provider_id": "283839", "version_id": "caller-version",
                         "effective_date": "20260401"}, locator={"article": 2})
    assert result["status"] == "TARGET_VERSION_UNVERIFIABLE"
    assert result["support_eligible"] is False


def test_nonlaw_locator_is_reported_and_full_document_is_kept():
    result = fetch_source_fragment(FakeClient([_provider_xml()]), api_family="admin_rule", observed_identity="x",
        version_binding=_binding(), locator={"article": 2})
    assert result["status"] == "LOCATOR_UNSUPPORTED_FULL_DOCUMENT"
    assert not result["support_eligible"]
    assert result["blocks"] and result["meta"]["raw_xml"]


def test_general_attachment_link_is_candidate_not_assumed_necessary():
    xml = _provider_xml().replace(
        b"</AdmRulService>",
        "<첨부파일><첨부파일명>별표.pdf</첨부파일명>"
        "<첨부파일링크>https://example.invalid/table.pdf</첨부파일링크></첨부파일>"
        "</AdmRulService>".encode("utf-8"),
    )
    result = fetch_source_fragment(FakeClient([xml]), api_family="admin_rule", observed_identity="x",
                                   version_binding=_binding())
    assert result["status"] == "OK"
    assert result["support_eligible"] is True
    assert result["source_completeness"]["complete"] is True
    assert result["source_completeness"]["scope"] == "provider_inline_body"
    assert result["source_completeness"]["unfetched_attachment_count"] == 1
    assert result["attachment_retrieval_candidates"][0]["role"] == "RETRIEVAL_CANDIDATE"
    assert "table.pdf" in result["meta"]["raw_xml"]


def test_annex_form_link_without_inline_body_is_missing_structural_context():
    xml = _provider_xml().replace(
        b"</AdmRulService>",
        "<별표><별표단위><별표번호>별표 1</별표번호>"
        "<별표서식파일링크>/LSW/flDownload.do?flSeq=123</별표서식파일링크>"
        "</별표단위></별표></AdmRulService>".encode("utf-8"),
    )
    result = fetch_source_fragment(FakeClient([xml]), api_family="admin_rule", observed_identity="x",
                                   version_binding=_binding())
    assert result["status"] == "ATTACHMENT_CONTENT_UNAVAILABLE"
    assert result["support_eligible"] is False
    assert result["source_completeness"]["complete"] is False
    assert result["source_completeness"]["missing_context"][0]["kind"] == "ATTACHMENT_CONTENT_UNAVAILABLE"
    assert result["source_completeness"]["missing_context"][0]["provider_tag"] == "별표서식파일링크"


@pytest.mark.parametrize("payload,code", [
    (b"<html>proxy error</html>", "HTML_RESPONSE"),
    ("<AdmRulService><행정규칙>".encode("utf-8"), "PARSE_ERROR"),
    (b"<Error><message>missing</message></Error>", "UNEXPECTED_ROOT"),
])
def test_bad_provider_payloads_rejected(payload, code):
    with pytest.raises(ProviderResponseError) as caught:
        parse_provider_fragment_xml(payload, api_family="admin_rule", resolved_identity="9001",
                                    id_param="ID", provider_target="admrul")
    assert caught.value.error_code == code


def test_invalid_provider_date_rejected_and_missing_date_never_uses_issued_date():
    with pytest.raises(ProviderResponseError) as caught:
        parse_provider_fragment_xml(_provider_xml(effective="20260230"), api_family="admin_rule",
            resolved_identity="9001", id_param="ID", provider_target="admrul",
            expected_effective_date="20260401", expected_sequence="9001")
    assert caught.value.error_code == "INVALID_PROVIDER_DATE"

    missing = parse_provider_fragment_xml(_provider_xml(effective=""), api_family="admin_rule",
        resolved_identity="9001", id_param="ID", provider_target="admrul",
        expected_effective_date="20260401", expected_sequence="9001")
    assert missing["status"] == "EFFECTIVE_DATE_NOT_MACHINE_READABLE"
    assert not missing["support_eligible"]
    assert missing["source"]["issued_date"] == "20260310"
    assert missing["source"]["effective_date"] == ""


def test_unknown_family_and_bad_locator_are_explicit():
    no_fetch = FakeClient([])
    unknown = fetch_source_fragment(no_fetch, api_family="unknown", observed_identity="x",
                                   version_binding={"status": "RESOLVED"})
    assert unknown["status"] == "FETCH_CAPABILITY_MISSING"
    assert no_fetch.calls == []

    law = FakeClient([])
    bad_locator = fetch_source_fragment(law, api_family="law", observed_identity="x",
        version_binding={"status": "RESOLVED", "provider_id": "283839", "version_id": "v1",
                         "effective_date": "20260401"}, locator={"article": 2, "page": 4})
    assert bad_locator["status"] == "LOCATOR_UNSUPPORTED"
    assert law.calls == []
