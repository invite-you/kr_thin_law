"""Mechanical validation for downstream context packets.

The gate checks packet shape and provenance flags; it does not certify legal
meaning or decide whether a packet is substantively complete.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


NORMATIVE_ROLES = {
    "ROOT_NORMATIVE", "GOVERNING_NORMATIVE", "TARGET_NORMATIVE",
    "TEMPORAL_NORMATIVE",
}
NON_SUPPORT_ROLES = {"RETRIEVAL_CANDIDATE", "EXTERNAL_AUDIT", "EXPLANATORY"}
ALL_ROLES = NORMATIVE_ROLES | NON_SUPPORT_ROLES
_GATE_FIELDS = (
    "version_bound", "structural_context_bound", "evidence_roles_valid",
    "candidate_coverage_complete", "transform_audit_clear",
    "temporal_binding_clear",
)


def content_hash(text: str) -> str:
    """Return the stable SHA-256 digest of UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def validate_packet(packet: dict[str, Any]) -> list[str]:
    """Return sorted mechanical contract errors; never infer semantic approval."""
    errors: list[str] = []
    if not isinstance(packet, dict):
        return ["PACKET_NOT_OBJECT"]
    if packet.get("schema") != "dps-context-v0.3":
        errors.append("UNKNOWN_SCHEMA")
    if not isinstance(packet.get("packet_id"), str) or not packet["packet_id"].strip():
        errors.append("PACKET_ID_MISSING")
    mode = packet.get("mode")
    if not isinstance(mode, str) or mode not in {"CANONICAL_BUILD", "EXTERNAL_AUDIT", "REPLAY"}:
        errors.append("MODE_INVALID")

    root = packet.get("root")
    if not isinstance(root, dict):
        errors.append("ROOT_MISSING")
        root = {}
    for key in ("source_unit_id", "version_id"):
        if not isinstance(root.get(key), str) or not root[key].strip():
            errors.append(f"ROOT_{key.upper()}_MISSING")
    if not _is_sha256(root.get("text_hash")):
        errors.append("ROOT_TEXT_HASH_INVALID")

    raw_blocks = packet.get("context_blocks")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        errors.append("CONTEXT_BLOCKS_EMPTY")
        raw_blocks = []
    blocks: dict[str, dict[str, Any]] = {}
    duplicate_ids: set[str] = set()
    for index, block in enumerate(raw_blocks):
        if not isinstance(block, dict):
            errors.append(f"BLOCK_NOT_OBJECT:{index}")
            continue
        block_id = block.get("block_id")
        if not isinstance(block_id, str) or not block_id.strip():
            errors.append(f"BLOCK_ID_MISSING:{index}")
            continue
        if block_id in blocks:
            duplicate_ids.add(block_id)
        else:
            blocks[block_id] = block
        if not isinstance(block.get("text"), str) or not block["text"].strip():
            errors.append(f"BLOCK_TEXT_EMPTY:{block_id}")
        elif not _is_sha256(block.get("content_hash")) or content_hash(block["text"]) != block.get("content_hash"):
            errors.append(f"CONTENT_HASH_MISMATCH:{block_id}")
        if not isinstance(block.get("source_id"), str) or not block["source_id"].strip():
            errors.append(f"SOURCE_ID_MISSING:{block_id}")
        role = block.get("role")
        role_valid = isinstance(role, str) and role in ALL_ROLES
        if not role_valid:
            errors.append(f"ROLE_UNKNOWN:{block_id}")
        if not isinstance(block.get("support_eligible"), bool):
            errors.append(f"SUPPORT_FLAG_INVALID:{block_id}")
        elif block["support_eligible"] and (not isinstance(role, str) or role not in NORMATIVE_ROLES):
            errors.append(f"NON_NORMATIVE_SUPPORT:{block_id}")
        if isinstance(role, str) and role in NORMATIVE_ROLES and block.get("support_eligible") and (not isinstance(block.get("version_id"), str) or not block["version_id"].strip()):
            errors.append(f"NORMATIVE_VERSION_MISSING:{block_id}")
        if not isinstance(block.get("relation_to_root"), str) or not block["relation_to_root"].strip():
            errors.append(f"RELATION_MISSING:{block_id}")
        channel = block.get("discovery_channel")
        if channel is not None and not isinstance(channel, str):
            errors.append(f"DISCOVERY_CHANNEL_INVALID:{block_id}")
    errors.extend(f"DUPLICATE_BLOCK_ID:{bid}" for bid in duplicate_ids)
    if isinstance(root, dict) and _is_sha256(root.get("text_hash")):
        root_blocks = [b for b in blocks.values() if b.get("source_unit_id") == root.get("source_unit_id") and b.get("relation_to_root") == "root"]
        if not root_blocks:
            errors.append("ROOT_BLOCK_MISSING")
        elif mode == "CANONICAL_BUILD" and (root_blocks[0].get("role") != "ROOT_NORMATIVE" or root_blocks[0].get("support_eligible") is not True):
            errors.append("ROOT_NORMATIVE_MISSING")
        elif root_blocks[0].get("content_hash") != root.get("text_hash"):
            errors.append("ROOT_BLOCK_HASH_MISMATCH")
        elif root_blocks[0].get("version_id") != root.get("version_id"):
            errors.append("ROOT_BLOCK_VERSION_MISMATCH")

    refs = packet.get("shared_context_refs")
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        errors.append("SHARED_CONTEXT_REFS_INVALID")
    else:
        for ref in refs:
            if ref not in blocks:
                errors.append(f"SHARED_CONTEXT_REF_MISSING:{ref}")

    coverage = packet.get("coverage")
    if not isinstance(coverage, dict):
        errors.append("COVERAGE_INVALID")
        coverage = {}
    required = coverage.get("required_channels")
    completed = coverage.get("completed_channels")
    channels_valid = isinstance(required, list) and isinstance(completed, list) and all(isinstance(ch, str) for ch in required) and all(isinstance(ch, str) for ch in completed)
    required_set = {ch for ch in required if isinstance(ch, str)} if isinstance(required, list) else set()
    completed_set = {ch for ch in completed if isinstance(ch, str)} if isinstance(completed, list) else set()
    if not channels_valid:
        errors.append("COVERAGE_CHANNELS_INVALID")
    elif not required_set.issubset(completed_set):
        errors.append("CANDIDATE_CHANNEL_INCOMPLETE")
    if not isinstance(coverage.get("residual_discovery_complete"), bool):
        errors.append("RESIDUAL_FLAG_INVALID")
    if not isinstance(coverage.get("missing_context"), list):
        errors.append("MISSING_CONTEXT_INVALID")
    status = packet.get("transport_status")
    if not isinstance(status, str) or status not in {"READY", "PARTIAL"}:
        errors.append("TRANSPORT_STATUS_INVALID")
    if status == "READY" and (not channels_valid or coverage.get("missing_context") or not coverage.get("residual_discovery_complete") or (channels_valid and not required_set.issubset(completed_set))):
        errors.append("READY_WITH_INCOMPLETE_COVERAGE")

    transforms = packet.get("transform_decisions")
    if not isinstance(transforms, list):
        errors.append("TRANSFORM_DECISIONS_INVALID")
        transforms = []
    for transform in transforms:
        if not isinstance(transform, dict):
            errors.append("TRANSFORM_ENTRY_INVALID")
            continue
        target_id = transform.get("target_block_id")
        if not isinstance(target_id, str) or target_id not in blocks:
            errors.append(f"TRANSFORM_TARGET_MISSING:{target_id}")
        kind = transform.get("kind")
        if not isinstance(kind, str) or kind not in {"NONE", "EXPLICIT", "CONTEXTUAL_INFERRED", "AMBIGUOUS"}:
            errors.append(f"TRANSFORM_KIND_INVALID:{target_id}")
        if not isinstance(transform.get("evidence_block_ids"), list):
            errors.append(f"TRANSFORM_EVIDENCE_INVALID:{target_id}")
        elif any(not isinstance(eid, str) or eid not in blocks for eid in transform["evidence_block_ids"]):
            errors.append(f"TRANSFORM_EVIDENCE_MISSING:{target_id}")
        audit = transform.get("audit_status")
        if not isinstance(audit, str) or audit not in {"PASS", "REVIEW_REQUIRED", "REJECT", "NOT_NEEDED"}:
            errors.append(f"TRANSFORM_AUDIT_INVALID:{target_id}")
        if kind == "AMBIGUOUS" or kind == "CONTEXTUAL_INFERRED" and audit != "PASS":
            errors.append(f"TRANSFORM_REVIEW_REQUIRED:{target_id}")
        if audit in ("REJECT", "REVIEW_REQUIRED"):
            errors.append(f"TRANSFORM_AUDIT_NOT_CLEAR:{target_id}")

    temporal = packet.get("temporal_binding")
    if not isinstance(temporal, dict):
        errors.append("TEMPORAL_BINDING_INVALID")
        temporal = {}
    if not isinstance(temporal.get("cutover_resolved"), bool) or not isinstance(temporal.get("mixed_versions"), bool):
        errors.append("TEMPORAL_FLAGS_INVALID")
    if temporal.get("mixed_versions") and not temporal.get("cutover_resolved"):
        errors.append("TEMPORAL_BINDING_UNRESOLVED")
    source_versions: dict[str, set[str]] = {}
    for block in blocks.values():
        if block.get("support_eligible") is True and isinstance(block.get("source_id"), str) and isinstance(block.get("version_id"), str):
            source_versions.setdefault(block["source_id"], set()).add(block["version_id"])
    if any(len(versions) > 1 for versions in source_versions.values()) and not temporal.get("cutover_resolved"):
        errors.append("TEMPORAL_BINDING_UNRESOLVED")

    gate = packet.get("commit_gate")
    if not isinstance(gate, dict):
        errors.append("COMMIT_GATE_INVALID")
        gate = {}
    for field in _GATE_FIELDS:
        if not isinstance(gate.get(field), bool):
            errors.append(f"COMMIT_GATE_FLAG_INVALID:{field}")
        elif gate[field] is not True:
            errors.append(f"COMMIT_GATE_FALSE:{field}")
    return sorted(set(errors))
