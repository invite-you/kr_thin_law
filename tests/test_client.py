from __future__ import annotations

import io
import http.client
import json
import urllib.error

import pytest

from law_mcp.card_source import ProviderResponseError, SERVICE_URL
from law_mcp.client import FixtureClient, OfficialClient


class Response(io.BytesIO):
    status = 200


def scripted(events):
    calls = []

    def open_request(request, timeout):
        calls.append((request.full_url, timeout))
        event = events.pop(0)
        if isinstance(event, Exception):
            raise event
        return Response(event)

    return open_request, calls


@pytest.mark.parametrize("bad,code", [
    (b"", "EMPTY_RESPONSE"), (b"<html>failure</html>", "HTML_RESPONSE"),
    (b"<broken", "PARSE_ERROR"),
])
def test_retry_preserves_every_attempt_and_recovers(tmp_path, bad, code):
    opener, calls = scripted([bad, b"<valid/>"])
    delays = []
    client = OfficialClient(
        "private-credential", capture_dir=tmp_path, opener=opener, sleep=delays.append,
        max_attempts=2, retry_delay=0.25,
    )
    assert client._call(SERVICE_URL, {"target": "admrul", "ID": "1"}) == b"<valid/>"
    assert len(calls) == 2 and delays == [0.25]
    assert [r["status"] for r in client.attempts] == [code, "OK"]
    assert len(list(tmp_path.glob("*.xml"))) == 2
    assert "private-credential" not in (tmp_path / "attempts.jsonl").read_text(encoding="utf-8")
    assert "private-credential" not in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")


def test_retry_exhaustion_is_bounded():
    opener, calls = scripted([b"", b"", b""])
    delays = []
    client = OfficialClient("test", opener=opener, sleep=delays.append, max_attempts=3)
    with pytest.raises(ProviderResponseError, match="EMPTY_RESPONSE"):
        client._call(SERVICE_URL, {"target": "eflaw"})
    assert len(calls) == 3 and delays == [0.5, 1.0]


@pytest.mark.parametrize("code,retries", [(429, 2), (503, 2), (403, 1)])
def test_http_status_retry_policy(code, retries):
    error = urllib.error.HTTPError(SERVICE_URL, code, "error", {}, io.BytesIO(b"<error/>"))
    opener, calls = scripted([error, b"<valid/>"])
    client = OfficialClient("test", opener=opener, sleep=lambda _: None)
    if retries == 1:
        with pytest.raises(ProviderResponseError) as raised:
            client._call(SERVICE_URL, {"target": "eflaw"})
        assert raised.value.retryable is False
    else:
        assert client._call(SERVICE_URL, {"target": "eflaw"}) == b"<valid/>"
    assert len(calls) == retries


def test_transport_error_does_not_leak_request_credential(tmp_path):
    error = urllib.error.URLError("https://law.go.kr?OC=hidden")
    opener, _ = scripted([error])
    client = OfficialClient("hidden", opener=opener, max_attempts=1, capture_dir=tmp_path)
    with pytest.raises(ProviderResponseError) as raised:
        client._call(SERVICE_URL, {"target": "eflaw"})
    assert "hidden" not in str(raised.value)
    assert "hidden" not in json.dumps(client.attempts)


def test_same_payload_is_retrieved_again_first_seen_stays(tmp_path):
    opener, calls = scripted([b"<valid/>", b"<valid/>"])
    client = OfficialClient("test", opener=opener, capture_dir=tmp_path)
    client._call(SERVICE_URL, {"target": "eflaw"})
    first = client.ledger.rows()[0]["first_seen"]
    client._call(SERVICE_URL, {"target": "eflaw"})
    assert len(calls) == 2
    assert client.ledger.rows()[0]["first_seen"] == first
    assert client.ledger.rows()[0]["observation_count"] == 2


def test_size_limit_captures_failure(tmp_path):
    opener, calls = scripted([b"123456"])
    client = OfficialClient("test", opener=opener, capture_dir=tmp_path, max_bytes=5)
    with pytest.raises(ProviderResponseError, match="RESPONSE_TOO_LARGE"):
        client._call(SERVICE_URL, {"target": "eflaw"})
    assert len(calls) == 1 and client.attempts[0]["bytes"] == 6


def test_missing_credential_blocks_io(monkeypatch):
    monkeypatch.delenv("LAW_API_OC", raising=False)
    opener, calls = scripted([])
    client = OfficialClient(opener=opener)
    with pytest.raises(ProviderResponseError, match="API_CREDENTIAL_MISSING"):
        client._call(SERVICE_URL, {"target": "eflaw"})
    assert not calls


def test_fixture_path_cannot_escape_explicit_root(tmp_path):
    folder = tmp_path / "fixtures"
    folder.mkdir()
    (tmp_path / "outside.xml").write_text("<secret/>", encoding="utf-8")
    manifest = folder / "manifest.json"
    manifest.write_text(json.dumps({"requests": [{"params": {"target": "eflaw"}, "file": "../outside.xml"}]}), encoding="utf-8")
    client = FixtureClient(manifest)
    with pytest.raises(ProviderResponseError, match="FIXTURE_PATH_OUTSIDE_ROOT"):
        client._call(SERVICE_URL, {"target": "eflaw"})
    client = FixtureClient(manifest, allowed_root=tmp_path)
    assert client._call(SERVICE_URL, {"target": "eflaw"}) == b"<secret/>"


def test_capture_redacts_percent_encoded_credential_marker(tmp_path):
    credential = "private-credential"
    payload = (
        "<valid><url>https://example.test/path?next=OC%3D"
        + credential
        + "</url></valid>"
    ).encode("utf-8")
    opener, _ = scripted([payload])
    client = OfficialClient(
        credential,
        opener=opener,
        capture_dir=tmp_path,
        max_attempts=1,
    )
    client._call(SERVICE_URL, {"target": "eflaw"})

    captured = b"".join(path.read_bytes() for path in tmp_path.glob("*.xml"))
    assert credential.encode("utf-8") not in captured
    assert credential not in (tmp_path / "attempts.jsonl").read_text(encoding="utf-8")
    assert credential not in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("http_error", [False, True])
@pytest.mark.parametrize("read_error", ["partial", "timeout"])
def test_interrupted_response_read_is_captured_and_retried(tmp_path, http_error, read_error):
    class InterruptedResponse(Response):
        def read(self, size=-1):
            if read_error == "partial":
                raise http.client.IncompleteRead(b"<url>OC=private-credential", 10)
            raise TimeoutError("read timed out")

    events = [InterruptedResponse(), Response(b"<valid/>")]
    if http_error:
        events[0] = urllib.error.HTTPError(SERVICE_URL, 503, "error", {}, events[0])

    def opener(request, timeout):
        event = events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event

    client = OfficialClient("private-credential", opener=opener, capture_dir=tmp_path,
                            max_attempts=2, sleep=lambda _: None)
    assert client._call(SERVICE_URL, {"target": "eflaw"}) == b"<valid/>"
    assert [row["status"] for row in client.attempts] == ["TRANSPORT_ERROR", "OK"]
    assert client.attempts[0]["http_status"] == (503 if http_error else 200)
    captured = b"".join(path.read_bytes() for path in tmp_path.glob("*.xml"))
    assert b"private-credential" not in captured
    if read_error == "partial":
        assert b"OC=REDACTED" in captured
