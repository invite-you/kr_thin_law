from pathlib import Path

from law_mcp.card_source import (
    encode_jo,
    project_article,
    parse_lsdelegated_xml,
    project_references,
    fetch_article,
    fetch_references,
    parse_eflaw_article_xml,
)


def test_encode_jo():
    assert encode_jo(3) == "000300"
    assert encode_jo(15, 2) == "001502"
    assert encode_jo("365") == "036500"


def test_project_article_keeps_original_text_and_version_fields():
    content = {
        "fields": {
            "법령ID": "011357",
            "법령명_한글": "테스트법",
            "공포일자": "20260310",
            "공포번호": "21445",
            "조문시행일자문자열": "20270701:제32조의2제1항 단서",
            "별표시행일자문자열": "20280101:별표 1",
        },
        "dates_normalized": {"시행일자": "2026-09-01"},
        "articles": [
            {
                "번호": "10",
                "가지번호": "",
                "제목": "요건",
                "구분": "조문",
                "시행일": "2026-09-01",
                "내용": "제10조(요건) 다음 각 호의 어느 하나에 해당하면 된다.\n  1. 첫째\n  2. 둘째",
            }
        ],
    }
    out = project_article(
        content,
        {"article_count": 1},
        mst="123",
        effective_date="20260901",
        article=10,
    )
    assert out["article"]["text"].endswith("2. 둘째")
    assert out["source"]["law_id"] == "011357"
    assert out["source"]["mst"] == "123"
    assert out["source"]["promulgation_date"] == "20260310"
    assert out["source"]["article_effective_date_text"].startswith("20270701:")
    assert out["source"]["annex_effective_date_text"] == "20280101:별표 1"


class FakeArticleClient:
    def __init__(self):
        self.params = None

    def _call(self, url, params):
        self.url = url
        self.params = params
        return """<?xml version="1.0" encoding="utf-8"?>
<법령 법령키="00999920260101100"><기본정보>
<법령ID>009999</법령ID><공포일자>20260101</공포일자><공포번호>100</공포번호>
<법종구분 법종구분코드="A0002">법률</법종구분><법령명_한글>건축법</법령명_한글><시행일자>20260101</시행일자>
</기본정보><조문><조문단위 조문키="0003001">
<조문번호>3</조문번호><조문여부>조문</조문여부><조문제목>적용 제외</조문제목>
<조문시행일자>20260101</조문시행일자><조문내용>제3조(적용 제외)</조문내용>
</조문단위></조문></법령>""".encode("utf-8")


def test_fetch_article_uses_eflaw_and_jo():
    client = FakeArticleClient()
    out = fetch_article(
        client, mst="276925", effective_date="20260101", article=3
    )
    assert client.params == {
        "target": "eflaw", "MST": "276925", "efYd": "20260101", "JO": "000300"
    }
    assert out["article"]["text"] == "제3조(적용 제외)"
    assert out["article"]["key"] == "0003001"
    assert out["meta"]["response_sha256"]
    assert out["meta"]["retrieved_at"]


LS = """<법령>
<법령정보><법령일련번호>999</법령일련번호><법령ID>000999</법령ID><법령명>테스트법</법령명>
<공포일자>20260101</공포일자><공포번호>100</공포번호><시행일자>20260901</시행일자></법령정보>
<위임조문정보><조정보><조문번호>25</조문번호><조문제목>등록취소</조문제목></조정보>
<위임정보><위임구분>인용법령</위임구분><위임법령일련번호>100</위임법령일련번호><위임법령제목>다른법</위임법령제목>
<위임법령조문정보><조항호목>제25조제1항</조항호목><링크텍스트>제362조부터 제364조까지</링크텍스트>
<라인텍스트>A</라인텍스트><위임법령조문번호>362,363,364</위임법령조문번호>
<위임법령조문가지번호>0,0,0</위임법령조문가지번호><위임법령조문제목></위임법령조문제목></위임법령조문정보></위임정보>
<위임정보><위임구분>행정규칙</위임구분>
<위임행정규칙조문정보><위임행정규칙일련번호>200</위임행정규칙일련번호><위임행정규칙제목>고시</위임행정규칙제목>
<조항호목>제25조제2항</조항호목><링크텍스트>고시로 정하는</링크텍스트></위임행정규칙조문정보></위임정보>
<위임정보><위임구분>자치법규</위임구분>
<위임자치법규조문정보><위임자치법규일련번호>300</위임자치법규일련번호><위임자치법규제목>조례</위임자치법규제목>
<조항호목>제25조제3항</조항호목><링크텍스트>조례로 정하는</링크텍스트></위임자치법규조문정보></위임정보>
<위임정보><위임구분>기관규정</위임구분>
<위임규정조문정보><위임규정일련번호>400</위임규정일련번호><위임규정제목>규정</위임규정제목>
<조항호목>제25조제4항</조항호목><링크텍스트>규정으로 정하는</링크텍스트></위임규정조문정보></위임정보>
<위임정보><위임구분>조약</위임구분><조약일련번호>500</조약일련번호><조약제목>협정</조약제목>
<조항호목>제25조제5항</조항호목><링크텍스트>협정에 따른</링크텍스트></위임정보>
</위임조문정보></법령>"""


def test_lsdelegated_preserves_all_families_and_empty_fields():
    parsed = parse_lsdelegated_xml(LS)
    families = {r["api_family"] for r in parsed["references"]}
    assert families == {
        "law",
        "admin_rule",
        "ordinance",
        "institution_rule",
        "treaty",
    }
    law = parsed["references"][0]
    assert law["text"] == "제362조부터 제364조까지"
    assert law["linked_article"] == "362,363,364"
    assert law["linked_branch"] == "0,0,0"
    assert law["linked_article_title"] == ""


def test_reference_projection_exposes_source_identity_and_observed_target():
    parsed = parse_lsdelegated_xml(LS)
    out = project_references(parsed, article=25)
    assert out["source"]["law_id"] == "000999"
    assert out["source"]["promulgation_date"] == "20260101"
    assert len(out["references"]) == 5
    first = out["references"][0]
    assert first["source_article"] == "25"
    assert first["target"]["api_family"] == "law"
    assert first["target"]["observed_seq"] == "100"
    assert first["target"]["linked_article_title"] == ""


class FakeRefClient:
    def _call(self, url, params):
        self.url = url
        self.params = params
        return LS.encode()


def test_fetch_references_calls_lsdelegated_once():
    client = FakeRefClient()
    out = fetch_references(client, mst="999", article=25)
    assert client.params == {"target": "lsDelegated", "MST": "999"}
    assert len(out["references"]) == 5


def test_reference_projection_keeps_official_line_context_without_semantic_processing():
    parsed = parse_lsdelegated_xml(LS)
    out = project_references(parsed, article=25)
    law = out["references"][0]
    assert law["context"] == "A"
