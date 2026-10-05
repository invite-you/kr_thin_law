"""Provenance anchor TCs — P1-B capture ledger / P1-C clock source / P1-D content hash.

Historical review records remain in the original local project.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from law_mcp.capture_ledger import CaptureLedger
from law_mcp.card_source import fetch_article, fetch_references, parse_eflaw_article_xml

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------- P1-B ledger
def test_tc_prov_001_first_seen_immutable_on_same_payload(tmp_path):
    """TC-PROV-001: 동일 payload 재수신 시 first_seen 불변, retrieved_at/카운트만 진행."""
    ledger = CaptureLedger(tmp_path / "ledger.jsonl")
    r1 = ledger.record(
        url="u", request={"MST": "1"}, raw=b"hello",
        retrieved_at="2026-01-01T00:00:00+00:00",
    )
    r2 = ledger.record(
        url="u", request={"MST": "1"}, raw=b"hello",
        retrieved_at="2026-02-01T00:00:00+00:00",
    )
    assert r1["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert r2["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert r2["retrieved_at"] == "2026-02-01T00:00:00+00:00"
    assert r2["observation_count"] == 2
    assert r1["response_sha256"] == r2["response_sha256"]


def test_tc_prov_002_new_payload_gets_own_first_seen(tmp_path):
    """TC-PROV-002: 신규 payload는 별도 행 + 자신의 first_seen."""
    ledger = CaptureLedger(tmp_path / "ledger.jsonl")
    a = ledger.record(url="u", request={"MST": "1"}, raw=b"one", retrieved_at="2026-01-01T00:00:00+00:00")
    b = ledger.record(url="u", request={"MST": "1"}, raw=b"two", retrieved_at="2026-03-01T00:00:00+00:00")
    assert a["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert b["first_seen"] == "2026-03-01T00:00:00+00:00"
    assert len(ledger.rows()) == 2


def test_tc_prov_003_first_seen_survives_reload(tmp_path):
    """TC-PROV-003: 원장 재로드 후에도 first_seen이 보존된다(영속성)."""
    path = tmp_path / "ledger.jsonl"
    ledger = CaptureLedger(path)
    ledger.record(url="u", request={"MST": "1"}, raw=b"hello", retrieved_at="2026-01-01T00:00:00+00:00")
    reloaded = CaptureLedger(path)
    again = reloaded.record(url="u", request={"MST": "1"}, raw=b"hello", retrieved_at="2026-04-01T00:00:00+00:00")
    assert again["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert again["observation_count"] == 2
    assert reloaded.seen(again["response_sha256"])["first_seen"] == "2026-01-01T00:00:00+00:00"


def test_independent_ledgers_preserve_rows_and_first_observation(tmp_path):
    path = tmp_path / "ledger.jsonl"
    first, second = CaptureLedger(path), CaptureLedger(path)
    original = first.record(url="u", request={}, raw=b"one", retrieved_at="2026-01-01")
    second.record(url="u", request={}, raw=b"two", retrieved_at="2026-01-02")
    repeated = second.record(url="u", request={}, raw=b"one", retrieved_at="2026-01-03")
    assert repeated["first_seen"] == original["first_seen"]
    assert repeated["observation_count"] == 2
    assert len(first.rows()) == len(CaptureLedger(path).rows()) == 2


def test_multiple_processes_preserve_all_capture_observations(tmp_path):
    path = tmp_path / "ledger.jsonl"
    gate = tmp_path / "start"
    ledger = CaptureLedger(path)
    script = """
import sys, time
from pathlib import Path
from law_mcp.capture_ledger import CaptureLedger
ledger = CaptureLedger(sys.argv[1])
gate = Path(sys.argv[3])
while not gate.exists():
    time.sleep(0.01)
for _ in range(10):
    ledger.record(url='u', request={}, raw=b'shared')
    ledger.record(url='u', request={}, raw=sys.argv[2].encode())
"""
    workers = [subprocess.Popen([sys.executable, "-c", script, str(path), str(i), str(gate)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
               for i in range(4)]
    try:
        gate.touch()
        for worker in workers:
            _, stderr = worker.communicate(timeout=30)
            assert worker.returncode == 0, stderr.decode("utf-8", errors="replace")
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
    assert len(ledger.rows()) == 5
    assert ledger.seen(hashlib.sha256(b"shared").hexdigest())["observation_count"] == 40
    assert all(ledger.seen(hashlib.sha256(str(i).encode()).hexdigest())["observation_count"] == 10
               for i in range(4))


def test_failed_ledger_replacement_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    ledger = CaptureLedger(path)
    ledger.record(url="u", request={}, raw=b"original")
    original = path.read_bytes()

    def fail_replace(self, target):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failure"):
        ledger.record(url="u", request={}, raw=b"new")
    assert path.read_bytes() == original
    assert len(CaptureLedger(path).rows()) == 1
    assert len(list(tmp_path.glob("ledger.jsonl.*"))) == 1  # Only the lock file remains.


# ------------------------------------------------------------ P1-C clock
class _FakeArticleClient:
    def _call(self, url, params):
        return (FIXTURES / "eflaw_283839_pipa_art2.xml").read_bytes()


class _FakeRefClient:
    def _call(self, url, params):
        return (FIXTURES / "lsDelegated_284025_criminal.xml").read_bytes()


def test_tc_prov_004_clock_source_marked_local():
    """TC-PROV-004: retrieved_at 출처가 로컬 시계임이 메타에 명시된다."""
    art = fetch_article(_FakeArticleClient(), mst="283839", effective_date="20260911", article=2)
    assert art["meta"]["clock_source"] == "local"
    assert art["meta"]["retrieved_at"]
    refs = fetch_references(_FakeRefClient(), mst="284025", article=365)
    assert refs["meta"]["clock_source"] == "local"
    assert refs["meta"]["retrieved_at"]


# ------------------------------------------------------- P1-D content hash
EFLAW_TEXT_V1 = """<?xml version="1.0" encoding="utf-8"?>
<법령 법령키="00999920260101100"><기본정보>
<법령ID>009999</법령ID><공포일자>20260101</공포일자><공포번호>100</공포번호>
<법종구분 법종구분코드="A0002">법률</법종구분><법령명_한글>테스트법</법령명_한글><시행일자>20260101</시행일자>
</기본정보><조문><조문단위 조문키="0003001">
<조문번호>3</조문번호><조문여부>조문</조문여부><조문내용>제3조(적용 제외)</조문내용>
</조문단위></조문></법령>"""

# Same article text, different raw metadata (공포일자 differs).
EFLAW_TEXT_V2 = EFLAW_TEXT_V1.replace("<공포일자>20260101</공포일자>", "<공포일자>20260102</공포일자>")

# One character changed in the article text itself.
EFLAW_TEXT_V3 = EFLAW_TEXT_V1.replace("제3조(적용 제외)", "제3조(적용 제외다)")


def test_tc_prov_005_text_sha256_separates_content_from_raw_meta():
    """TC-PROV-005(수정된 P1-D 기준): raw 메타만 변하면 response만, 본문이 변하면 둘 다 변한다."""
    out1 = parse_eflaw_article_xml(EFLAW_TEXT_V1, mst="1", effective_date="20260101", article=3)
    out2 = parse_eflaw_article_xml(EFLAW_TEXT_V2, mst="1", effective_date="20260101", article=3)
    out3 = parse_eflaw_article_xml(EFLAW_TEXT_V3, mst="1", effective_date="20260101", article=3)

    # raw-only change: response hash moves, content hash does not.
    assert out1["meta"]["response_sha256"] != out2["meta"]["response_sha256"]
    assert out1["meta"]["text_sha256"] == out2["meta"]["text_sha256"]

    # article text change: both move.
    assert out3["meta"]["text_sha256"] != out1["meta"]["text_sha256"]
    assert out3["meta"]["response_sha256"] != out1["meta"]["response_sha256"]

    # content hash is exactly the hash of the projected article text.
    expected = hashlib.sha256(out1["article"]["text"].encode("utf-8")).hexdigest()
    assert out1["meta"]["text_sha256"] == expected


# ------------------------------------------------------ P1-E batch dedup
def test_tc_batch_001_one_fetch_per_unique_request(tmp_path):
    """TC-BATCH-001: 동일 요청이 배치에 여러 번 있어도 다운로드는 1회, 원장에도 1행."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from batch_ingest import batch_collect, request_key

    calls = []

    def fake_fetch(url, params):
        calls.append((url, tuple(sorted(params.items()))))
        return f"payload-{params['MST']}".encode()

    ledger = CaptureLedger(tmp_path / "ledger.jsonl")
    jobs = [
        ("a", "u", {"MST": "1"}),
        ("b", "u", {"MST": "1"}),  # duplicate request
        ("c", "u", {"MST": "2"}),
    ]
    rows, duplicates = batch_collect(jobs, fake_fetch, ledger)
    assert len(calls) == 2, "one download per unique request"
    assert duplicates == 1
    assert rows["a"] is rows["b"]
    assert len(ledger.rows()) == 2
    assert request_key("u", {"MST": "1"}) == request_key("u", {"MST": "1"})
