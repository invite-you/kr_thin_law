from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import partial
from pathlib import Path

from law_mcp.context_packet import validate_packet
from law_mcp.context_producer import ContextProducer
from law_mcp.client import FixtureClient
from law_mcp.source_fragment import fetch_source_fragment


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _law_fragment(*, status="OK", support_eligible=True):
    return {
        "status": status,
        "support_eligible": support_eligible,
        "source": {"law_id": "L-1", "law": "예시법", "official_source": "law.go.kr"},
        "article": {
            "head_text": "제1조(목적) 이 법은 목적을 정한다.",
            "text": "제1조(목적) 이 법은 목적을 정한다.",
            "structure": [{"type": "paragraph", "label": "①", "text": "첫 문장",
                           "children": [{"type": "item", "label": "1.", "text": "호 문장",
                                         "children": [{"type": "subitem", "label": "가.", "text": "목 문장"}]}]}],
        },
        "meta": {"text_sha256": _sha("제1조(목적) 이 법은 목적을 정한다.")},
    }


def _root_need(**extra):
    return {
        "source_unit_id": "unit:root:1",
        "api_family": "law",
        "evidence_role": "ROOT_NORMATIVE",
        "observed_identity": {"law_id": "L-1"},
        "version_binding": {"version_id": "V1", "mst": "V1"},
        "locator": {"article": 1},
        "support_eligible": True,
        "discovery_channel": "law_article",
        **extra,
    }


def _target_need(locator=2, **extra):
    return {
        "source_unit_id": f"unit:target:{locator}",
        "api_family": "law",
        "observed_identity": {"law_id": "L-2"},
        "version_binding": {"version_id": "V2", "mst": "V2"},
        "locator": {"article": locator},
        "evidence_role": "TARGET_NORMATIVE",
        "support_eligible": True,
        "relation_to_root": "caller_selected_need",
        "discovery_channel": "law_reference",
        **extra,
    }


def _fetcher(calls, fragments):
    def fetch(*, api_family, observed_identity, version_binding, locator):
        source_key = observed_identity.get("law_id", observed_identity.get("source_id"))
        key = (api_family, source_key, version_binding["version_id"], locator.get("article", locator.get("annex")))
        calls.append(key)
        if key in fragments:
            return fragments[key]
        result = _law_fragment()
        result["source"]["law_id"] = source_key
        return result
    return fetch


def test_root_only_and_recursive_law_structure_are_bound():
    calls = []
    producer = ContextProducer(_fetcher(calls, {}))
    packet = producer.produce(_root_need(), coverage={
        "required_channels": ["law_article"], "completed_channels": [],
        "residual_discovery_complete": True,
    })

    assert len(calls) == 1
    assert packet["transport_status"] == "READY"
    assert len(packet["observations"]) == 1
    assert packet["root"]["source_unit_id"] == "unit:root:1"
    assert packet["root"]["text_hash"] == _sha("제1조(목적) 이 법은 목적을 정한다.")
    assert [block["role"] for block in packet["context_blocks"]] == [
        "ROOT_NORMATIVE", "ROOT_NORMATIVE", "ROOT_NORMATIVE", "ROOT_NORMATIVE"
    ]
    assert all(block["support_eligible"] for block in packet["context_blocks"])
    assert packet["validation_errors"] == []


def test_selected_targets_and_duplicate_requests_fetch_once_but_keep_observations():
    calls = []
    target = _target_need(2)
    same_request_observation = {**target, "source_unit_id": "unit:target:2-second-observation",
                                "observed_identity": {"law_id": "L-2", "record_alias": "alternate"},
                                "discovery_channel": "residual_pass"}
    packet = ContextProducer(_fetcher(calls, {})).produce(
        _root_need(), [target, same_request_observation, _target_need(3)],
        coverage={"required_channels": ["law_article", "law_reference", "residual_pass"],
                  "residual_discovery_complete": True},
    )

    assert len(calls) == 3  # root plus two distinct target locators
    assert len(packet["observations"]) == 4
    assert len({item["request_key"] for item in packet["observations"]}) == 3
    assert sum(block["role"] == "TARGET_NORMATIVE" and block["source_unit_id"].startswith("unit:target:") for block in packet["context_blocks"]) == 2
    assert packet["coverage"]["completed_channels"] == ["law_article", "law_reference", "residual_pass"]


def test_unresolved_or_non_support_fragments_remain_candidates():
    calls = []
    failed_key = ("law", "L-2", "V2", 2)
    unsupported_key = ("law", "L-2", "V2", 3)
    fragments = {
        failed_key: _law_fragment(status="TARGET_VERSION_UNVERIFIABLE", support_eligible=False),
        unsupported_key: _law_fragment(support_eligible=False),
    }
    for fragment in fragments.values():
        fragment["source"]["law_id"] = "L-2"
    packet = ContextProducer(_fetcher(calls, fragments)).produce(
        _root_need(), [_target_need(2), _target_need(3)],
        coverage={"required_channels": ["law_article", "law_reference"], "residual_discovery_complete": False},
    )

    candidates = [block for block in packet["context_blocks"] if block["source_id"] == "L-2"]
    assert len([block for block in candidates if block["source_unit_id"].startswith("unit:target:")]) == 2
    assert all(block["role"] == "RETRIEVAL_CANDIDATE" and not block["support_eligible"] for block in candidates)
    assert packet["transport_status"] == "PARTIAL"
    assert {entry["status"] for entry in packet["observations"] if entry["source_unit_id"].startswith("unit:target:")} == {"OK", "TARGET_VERSION_UNVERIFIABLE"}


def test_provider_tree_preserves_headers_tables_and_appendix_context():
    calls = []
    tree_fragment = {
        "status": "OK", "support_eligible": True,
        "source": {"source_id": "DOC-9"},
        "blocks": [],
        "provider_tree": [{
            "type": "table", "header": "별표 1. 적용 기준", "lead_in": "다음 표와 같다.",
            "rows": [["구분", "기준"], ["가", "보존 기간"]],
            "children": [{"type": "annex", "title": "부칙", "text": "이 규칙은 공포한 날부터 시행한다."}],
        }],
        "meta": {"raw_xml_base64": "PHg+PC94Pg==", "response_sha256": "abc"},
    }
    selected = {
        "source_unit_id": "unit:annex:1", "api_family": "document",
        "observed_identity": {"source_id": "DOC-9"}, "version_binding": {"version_id": "D9"},
        "locator": {"annex": 1}, "evidence_role": "EXPLANATORY", "support_eligible": False,
        "discovery_channel": "appendix_lookup",
    }
    fetch = _fetcher(calls, {("document", "DOC-9", "D9", 1): tree_fragment})
    packet = ContextProducer(fetch).produce(
        _root_need(), [selected], coverage={"required_channels": ["law_article", "appendix_lookup"],
                                           "residual_discovery_complete": True},
    )
    target_text = "\n".join(block["text"] for block in packet["context_blocks"] if block["source_id"] == "DOC-9")
    assert "별표 1. 적용 기준" in target_text
    assert "다음 표와 같다." in target_text
    assert "구분 | 기준" in target_text
    assert "가 | 보존 기간" in target_text
    assert "이 규칙은 공포한 날부터 시행한다." in target_text
    assert all(not block["support_eligible"] for block in packet["context_blocks"] if block["source_id"] == "DOC-9")


def test_large_selected_list_keeps_all_observations_and_fetches_every_distinct_request():
    calls = []
    targets = [_target_need(i) for i in range(1, 51)]
    packet = ContextProducer(_fetcher(calls, {})).produce(_root_need(), targets)
    assert len(calls) == 51
    assert len(packet["observations"]) == 51
    assert len([b for b in packet["context_blocks"] if b["role"] == "TARGET_NORMATIVE" and b["source_unit_id"].startswith("unit:target:")]) == 50


def test_packet_validator_rejects_empty_duplicate_bad_hash_and_invalid_roles():
    errors = validate_packet({"schema": "dps-context-v0.3", "mode": "CANONICAL_BUILD", "context_blocks": []})
    assert "CONTEXT_BLOCKS_EMPTY" in errors

    packet = ContextProducer(_fetcher([], {})).produce(_root_need(), coverage={"residual_discovery_complete": True})
    block = packet["context_blocks"][0]
    malformed = {**packet, "context_blocks": [
        {**block, "content_hash": "0" * 64, "role": "MYSTERY", "support_eligible": True},
        dict(block),
    ]}
    errors = validate_packet(malformed)
    assert any(error.startswith("DUPLICATE_BLOCK_ID:") for error in errors)
    assert any(error.startswith("CONTENT_HASH_MISMATCH:") for error in errors)
    assert any(error.startswith("ROLE_UNKNOWN:") for error in errors)
    assert any(error.startswith("NON_NORMATIVE_SUPPORT:") for error in errors)

    malformed = {**packet, "mode": [], "coverage": {"required_channels": [{}], "completed_channels": [],
                                                        "residual_discovery_complete": False, "missing_context": []},
                 "context_blocks": [{**block, "role": {"bad": "role"}}],
                 "transform_decisions": [{"target_block_id": {}, "kind": [], "audit_status": {},
                                           "evidence_block_ids": [{}]}]}
    errors = validate_packet(malformed)
    assert "MODE_INVALID" in errors
    assert "COVERAGE_CHANNELS_INVALID" in errors
    assert any(error.startswith("ROLE_UNKNOWN:") for error in errors)
    assert any(error.startswith("TRANSFORM_TARGET_MISSING:") for error in errors)


def test_validator_fails_closed_for_missing_channels_transform_and_mixed_versions():
    packet = ContextProducer(_fetcher([], {})).produce(
        _root_need(), [_target_need()], coverage={"required_channels": ["law_reference", "residual"],
                                                   "completed_channels": ["law_reference"],
                                                   "residual_discovery_complete": False},
        transform_decisions=[{"target_block_id": "missing", "kind": "AMBIGUOUS",
                              "evidence_block_ids": [], "audit_status": "PASS"}],
        temporal_binding={"mixed_versions": True, "cutover_resolved": False},
    )
    errors = validate_packet(packet)
    assert "CANDIDATE_CHANNEL_INCOMPLETE" in errors
    assert "TEMPORAL_BINDING_UNRESOLVED" in errors
    assert any(error.startswith("TRANSFORM_REVIEW_REQUIRED:") for error in errors)
    assert any(error.startswith("TRANSFORM_TARGET_MISSING:") for error in errors)


def test_real_fixture_client_source_fragment_pipeline_is_version_bound():
    project = Path(__file__).resolve().parents[1]
    client = FixtureClient(project / "samples" / "replay_manifest.json", allowed_root=project)
    request = json.loads((project / "samples" / "context_request.json").read_text(encoding="utf-8"))
    packet = ContextProducer(partial(fetch_source_fragment, client)).produce(
        request["root_need"], request["selected_needs"],
        as_of=request["as_of"], coverage=request["coverage"],
        temporal_binding=request["temporal_binding"],
    )

    assert len(client.calls) == 4
    assert packet["transport_status"] == "READY"
    assert packet["validation_errors"] == []
    assert packet["root"]["source_unit_id"] == "pipa-decree:289537:4:2"
    assert len([block for block in packet["context_blocks"] if block["role"] == "TARGET_NORMATIVE" and block["source_unit_id"].startswith("pipa:283839:")]) == 3
    assert all(observation["source_hashes"].get("response_sha256") for observation in packet["observations"])
    assert all("raw_xml" not in observation for observation in packet["observations"])


def test_same_raw_request_preserves_each_distinct_evidence_role():
    calls = []
    target = _target_need(2)
    audit = {**target, "source_unit_id": "audit:target:2", "evidence_role": "EXTERNAL_AUDIT",
             "support_eligible": False, "relation_to_root": "independent_audit"}
    packet = ContextProducer(_fetcher(calls, {})).produce(
        _root_need(), [target, audit], coverage={"residual_discovery_complete": True})
    assert len(calls) == 2
    assert {b["role"] for b in packet["context_blocks"]} >= {"TARGET_NORMATIVE", "EXTERNAL_AUDIT"}
    assert all(not b["support_eligible"] for b in packet["context_blocks"] if b["role"] == "EXTERNAL_AUDIT")
    assert [o["evidence_role"] for o in packet["observations"]][-2:] == ["TARGET_NORMATIVE", "EXTERNAL_AUDIT"]


def test_nonlaw_metadata_and_attachment_links_never_become_normative_support():
    project = Path(__file__).resolve().parents[1]
    client = FixtureClient(project / "samples/replay_manifest.json", allowed_root=project)
    request = json.loads((project / "samples/context_request.json").read_text(encoding="utf-8"))
    target = {"api_family": "admin_rule", "source_unit_id": "admin:9008952",
              "version_binding": {"status": "RESOLVED", "provider_id": "2000000091702",
                                  "version_id": "2000000091702", "effective_date": "20130415"},
              "evidence_role": "TARGET_NORMATIVE", "support_eligible": True}
    packet = ContextProducer(partial(fetch_source_fragment, client)).produce(
        request["root_need"], [target], coverage={"residual_discovery_complete": True})
    admin_blocks = [b for b in packet["context_blocks"] if b["source_id"] == "admin_rule:9008952"]
    assert admin_blocks and all(b["support_eligible"] for b in admin_blocks)
    assert all(b["text"] not in {"2000000091702", "9008952", "20130415"} for b in admin_blocks)
    assert all(not b["text"].startswith("http") for b in admin_blocks)
    assert any(c["role"] == "RETRIEVAL_CANDIDATE" and not c["support_eligible"] for c in packet["retrieval_candidates"])
    assert not packet["validation_errors"]


def test_validator_rejects_forged_root_role_and_bad_ready_channel_items():
    packet = ContextProducer(_fetcher([], {})).produce(_root_need(), coverage={"residual_discovery_complete": True})
    malformed = deepcopy(packet)
    malformed["context_blocks"][0]["role"] = "RETRIEVAL_CANDIDATE"
    malformed["context_blocks"][0]["support_eligible"] = False
    assert "ROOT_NORMATIVE_MISSING" in validate_packet(malformed)
    malformed = deepcopy(packet)
    malformed["coverage"]["required_channels"] = [{}]
    assert "COVERAGE_CHANNELS_INVALID" in validate_packet(malformed)


def test_explicit_rejected_transform_blocks_ready():
    packet = ContextProducer(_fetcher([], {})).produce(_root_need(), coverage={"residual_discovery_complete": True})
    bid = packet["context_blocks"][0]["block_id"]
    packet["transform_decisions"] = [{"target_block_id": bid, "kind": "EXPLICIT",
                                      "audit_status": "REJECT", "evidence_block_ids": [bid]}]
    assert f"TRANSFORM_AUDIT_NOT_CLEAR:{bid}" in validate_packet(packet)
