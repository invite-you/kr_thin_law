from __future__ import annotations

import asyncio
import io
import urllib.error

import pytest
from mcp import Client

from law_mcp.card_source import (
    ProviderResponseError,
    SEARCH_URL,
    SERVICE_URL,
    parse_eflaw_article_xml,
    parse_lsdelegated_xml,
)
from law_mcp.client import OfficialClient
from law_mcp.search import search_documents
from law_mcp.server import create_server


LIVE_AUTH_FAILURE = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <header>
    <result>사용자 정보 검증에 실패하였습니다.</result>
    <msg>OPEN API 호출 시 사용자 검증을 위하여 정확한 서버장비의 IP주소 및 도메인주소를 등록해 주세요.</msg>
  </header>
</Response>
""".encode("utf-8")

DOCUMENTED_SEARCH_FAILURE = """<?xml version="1.0" encoding="UTF-8"?>
<LawSearch>
  <target>law</target>
  <totalCnt>0</totalCnt>
  <resultCode>01</resultCode>
  <resultMsg>fail</resultMsg>
</LawSearch>
""".encode("utf-8")

EMPTY_SEARCH_SUCCESS = """<?xml version="1.0" encoding="UTF-8"?>
<LawSearch>
  <target>law</target>
  <totalCnt>0</totalCnt>
  <page>1</page>
  <numOfRows>0</numOfRows>
  <resultCode>00</resultCode>
  <resultMsg>success</resultMsg>
</LawSearch>
""".encode("utf-8")


class Response(io.BytesIO):
    status = 200


class StaticClient:
    def __init__(self, body: bytes):
        self.body = body
        self.calls = []

    def _call(self, url, params):
        self.calls.append((url, dict(params)))
        return self.body


def test_live_lawgo_response_envelope_is_provider_error_not_success_empty():
    client = OfficialClient(
        "definitely-invalid-credential",
        opener=lambda request, timeout: Response(LIVE_AUTH_FAILURE),
        max_attempts=1,
    )
    with pytest.raises(ProviderResponseError) as caught:
        client._call(SEARCH_URL, {"target": "law", "query": "자동차관리법"})

    err = caught.value
    assert err.error_code == "PROVIDER_DECLARED_ERROR"
    assert err.http_status == 200
    assert err.retryable is False
    assert "사용자 정보 검증에 실패" in err.provider_message
    assert "IP주소 및 도메인주소" in err.provider_message
    assert err.provider_fields["result"].startswith("사용자 정보 검증")
    assert err.provider_fields["msg"].startswith("OPEN API 호출")
    assert err.provider_response == LIVE_AUTH_FAILURE.decode("utf-8")
    assert client.attempts[0]["status"] == "PROVIDER_DECLARED_ERROR"
    assert client.attempts[0]["http_status"] == 200
    assert "provider_message" in client.attempts[0]


@pytest.mark.parametrize("url", [SEARCH_URL, SERVICE_URL])
def test_provider_failure_is_rejected_at_common_transport_boundary(url):
    client = OfficialClient(
        "bad",
        opener=lambda request, timeout: Response(LIVE_AUTH_FAILURE),
        max_attempts=1,
    )
    with pytest.raises(ProviderResponseError, match="PROVIDER_DECLARED_ERROR"):
        client._call(url, {"target": "law"})
    assert client.attempts[0]["status"] == "PROVIDER_DECLARED_ERROR"


def test_documented_resultcode_failure_is_not_empty_search_success():
    with pytest.raises(ProviderResponseError) as caught:
        search_documents(
            StaticClient(DOCUMENTED_SEARCH_FAILURE),
            target="eflaw",
            query="없는 값",
        )
    err = caught.value
    assert err.error_code == "PROVIDER_DECLARED_ERROR"
    assert err.provider_code == "01"
    assert err.provider_message == "fail"


def test_legitimate_zero_result_search_remains_success():
    result = search_documents(
        StaticClient(EMPTY_SEARCH_SUCCESS),
        target="eflaw",
        query="실제로 결과가 없는 검색",
    )
    assert result["status"] == "OK"
    assert result["total_count"] == 0
    assert result["rows"] == []


@pytest.mark.parametrize(
    "payload,code",
    [
        (b"<LawSearch><resultCode>00</resultCode><resultMsg>success</resultMsg></LawSearch>",
         "MISSING_STRUCTURE"),
        (b"<Anything><totalCnt>0</totalCnt></Anything>", "UNEXPECTED_ROOT"),
        (b"<LawSearch><totalCnt>not-a-number</totalCnt></LawSearch>", "INVALID_PROVIDER_VALUE"),
    ],
)
def test_search_parser_never_defaults_malformed_payload_to_empty_success(payload, code):
    with pytest.raises(ProviderResponseError) as caught:
        search_documents(StaticClient(payload), target="eflaw", query="x")
    assert caught.value.error_code == code


@pytest.mark.parametrize(
    "parse",
    [
        lambda payload: parse_eflaw_article_xml(
            payload,
            mst="1",
            effective_date="20260101",
            article=1,
        ),
        lambda payload: parse_lsdelegated_xml(payload),
    ],
)
def test_direct_parsers_also_preserve_provider_declared_failure(parse):
    with pytest.raises(ProviderResponseError) as caught:
        parse(LIVE_AUTH_FAILURE)
    assert caught.value.error_code == "PROVIDER_DECLARED_ERROR"
    assert "IP주소 및 도메인주소" in caught.value.provider_message


def test_http_error_keeps_http_status_and_provider_body_cause():
    http_error = urllib.error.HTTPError(
        SERVICE_URL,
        503,
        "Service Unavailable",
        {},
        io.BytesIO(LIVE_AUTH_FAILURE),
    )

    def opener(request, timeout):
        raise http_error

    client = OfficialClient("bad", opener=opener, max_attempts=1)
    with pytest.raises(ProviderResponseError) as caught:
        client._call(SERVICE_URL, {"target": "eflaw"})

    err = caught.value
    assert err.error_code == "HTTP_ERROR"
    assert err.http_status == 503
    assert err.retryable is True
    assert "사용자 정보 검증에 실패" in err.provider_message
    assert err.provider_fields["msg"].startswith("OPEN API 호출")


def test_mcp_propagates_provider_failure_as_real_tool_error_with_raw_response():
    async def run():
        client = OfficialClient(
            "definitely-invalid-credential",
            opener=lambda request, timeout: Response(LIVE_AUTH_FAILURE),
            max_attempts=1,
        )
        server = create_server(client)
        async with Client(server) as mcp:
            result = await mcp.call_tool(
                "law_search",
                {"target": "eflaw", "query": "자동차관리법"},
            )
            assert result.is_error
            rendered = "\n".join(
                getattr(item, "text", str(item))
                for item in result.content
            )
            assert "PROVIDER_DECLARED_ERROR" in rendered
            assert "UPSTREAM_RESPONSE:" in rendered
            assert "<Response>" in rendered
            assert "<result>사용자 정보 검증에 실패하였습니다.</result>" in rendered
            assert "IP주소 및 도메인주소를 등록해 주세요." in rendered
            assert "rows" not in rendered

    asyncio.run(run())
