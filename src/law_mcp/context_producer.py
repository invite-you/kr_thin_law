"""Assemble caller-selected source fragments into a transport context packet.

This module preserves source text and structure. It does not select legal needs,
resolve editions, or approve a legal conclusion.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Iterable

from .card_source import ProviderResponseError
from .context_packet import content_hash, validate_packet


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _identity(value: Any) -> str:
    return value if isinstance(value, str) else _stable(value)


def _version_id(binding: Any) -> str | None:
    if isinstance(binding, str):
        return binding or None
    if isinstance(binding, dict):
        for key in ("version_id", "official_version_id", "version", "mst", "seq"):
            if binding.get(key) not in (None, ""):
                return str(binding[key])
    return None


def _unit_id(need: dict[str, Any]) -> str:
    for key in ("source_unit_id", "unit_id", "need_id", "id"):
        if need.get(key) not in (None, ""):
            return str(need[key])
    seed = {k: need.get(k) for k in ("api_family", "observed_identity", "version_binding", "locator")}
    return "unit:" + hashlib.sha256(_stable(seed).encode("utf-8")).hexdigest()[:24]


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _source_id(fragment: dict[str, Any], need: dict[str, Any]) -> str:
    source_value = fragment.get("source")
    source: dict[str, Any] = source_value if isinstance(source_value, dict) else {}
    for key in ("document_id", "source_id", "law_id", "resolved_identity", "provider_identity"):
        value = source.get(key)
        if value not in (None, ""):
            return _identity(value) if key in {"source_id", "law_id"} else f"{need.get('api_family')}:{_identity(value)}"
    observed = need.get("observed_identity")
    if isinstance(observed, dict):
        for key in ("resolved_identity", "provider_identity", "source_id", "law_id", "document_id", "id"):
            if observed.get(key) not in (None, ""):
                return str(observed[key])
    return f"{need.get('api_family') or 'unknown-family'}:{_identity(observed) or 'unknown-source'}"


def _fragment_payload(fragment: dict[str, Any]) -> dict[str, Any]:
    """Flatten source_fragment's wrapper while retaining its legacy projection."""
    nested = fragment.get("fragment")
    if not isinstance(nested, dict):
        return fragment
    payload = dict(nested)
    payload.update({key: value for key, value in fragment.items() if key != "fragment"})
    nested_source = nested.get("source")
    outer_source = fragment.get("source")
    if isinstance(nested_source, dict) and isinstance(outer_source, dict):
        payload["source"] = {**nested_source, **outer_source}
    return payload


def _request_key(need: dict[str, Any]) -> str:
    # Keep observations distinct while sharing I/O for the same actual bound request.
    request = {key: need.get(key) for key in ("api_family", "version_binding", "locator")}
    return _stable(request)


def _context_key(need: dict[str, Any]) -> str:
    """Share I/O independently of each observation's explicitly requested role."""
    return _stable({"request": _request_key(need), "role": need.get("evidence_role"),
                    "support_eligible": need.get("support_eligible"),
                    "relation_to_root": need.get("relation_to_root")})


def _iter_nodes(value: Any, parent_id: str, prefix: str) -> Iterable[tuple[str, str, dict[str, Any] | None, str]]:
    """Yield source structure in provider order, retaining labels and parent links."""
    if isinstance(value, list):
        for index, node in enumerate(value):
            yield from _iter_nodes(node, parent_id, f"{prefix}.{index + 1}")
    elif isinstance(value, dict):
        fields = ("text",) if _text(value.get("text")) else ("header", "heading", "lead_in", "title", "caption", "content", "내용", "value")
        parts: list[str] = []
        for key in fields:
            candidate = _text(value.get(key))
            if candidate and candidate not in parts:
                parts.append(candidate)
        rows = value.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    cells = [str(v).strip() for v in row.values() if isinstance(v, (str, int, float)) and str(v).strip()]
                    if cells:
                        parts.append(" | ".join(cells))
                elif isinstance(row, list):
                    cells = [str(v).strip() for v in row if isinstance(v, (str, int, float)) and str(v).strip()]
                    if cells:
                        parts.append(" | ".join(cells))
                elif isinstance(row, (str, int, float)) and str(row).strip():
                    parts.append(str(row).strip())
        rendered = "\n".join(parts)
        node_id = f"{prefix}:{hashlib.sha256(_stable(value).encode('utf-8')).hexdigest()[:10]}"
        if rendered:
            yield node_id, rendered, value, parent_id
        for key in ("children", "structure", "items", "blocks"):
            children = value.get(key)
            if isinstance(children, (list, dict)):
                yield from _iter_nodes(children, node_id if rendered else parent_id, prefix)


class ContextProducer:
    """Build one packet from explicit needs and caller-supplied source bindings.

    ``fetch_fragment`` is a callable accepting keyword arguments
    ``api_family``, ``observed_identity``, ``version_binding`` and ``locator``.
    A single produce call fetches each exact request once; this instance keeps no
    cache between calls.
    """

    def __init__(self, fetch_fragment: Callable[..., dict[str, Any]]) -> None:
        self.fetch_fragment = fetch_fragment

    def produce(
        self,
        root_need: dict[str, Any],
        selected_needs: Iterable[dict[str, Any]] = (),
        mode: str = "CANONICAL_BUILD",
        as_of: str | None = None,
        coverage: dict[str, Any] | None = None,
        transform_decisions: Iterable[dict[str, Any]] = (),
        temporal_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        needs = [root_need, *list(selected_needs)]
        transform_decisions = list(transform_decisions)
        fragments: dict[str, dict[str, Any]] = {}
        observations: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for need in needs:
            if not isinstance(need, dict):
                raise TypeError("each need must be a dict")
            request_key = _request_key(need)
            fetch_error: str | None = None
            if request_key not in fragments:
                try:
                    fragments[request_key] = self.fetch_fragment(
                        api_family=need.get("api_family"),
                        observed_identity=need.get("observed_identity"),
                        version_binding=need.get("version_binding"),
                        locator=need.get("locator"),
                    )
                    if not isinstance(fragments[request_key], dict):
                        fetch_error = "FETCH_RESULT_NOT_OBJECT"
                        fragments[request_key] = {}
                except ProviderResponseError:
                    # Upstream/provider failures are transport failures, not
                    # "missing context". Preserve them as real MCP errors.
                    raise
                except Exception as exc:  # retain non-provider assembly failures, then continue
                    fetch_error = f"{type(exc).__name__}: {exc}"
                    fragments[request_key] = {}
            fragment = fragments[request_key]
            status = str(fragment.get("status") or "UNKNOWN").upper()
            if fetch_error:
                status = "ERROR"
            unit_id = _unit_id(need)
            fragment_meta_value = fragment.get("meta")
            fragment_meta: dict[str, Any] = fragment_meta_value if isinstance(fragment_meta_value, dict) else {}
            fragment_source_value = fragment.get("source")
            fragment_source: dict[str, Any] = fragment_source_value if isinstance(fragment_source_value, dict) else {}
            observations.append({
                "source_unit_id": unit_id,
                "api_family": need.get("api_family"),
                "observed_identity": need.get("observed_identity"),
                "version_binding": need.get("version_binding"),
                "locator": need.get("locator"),
                "evidence_role": need.get("evidence_role"),
                "support_requested": need.get("support_eligible"),
                "relation_to_root": need.get("relation_to_root"),
                "discovery_channel": need.get("discovery_channel"),
                "request_key": hashlib.sha256(request_key.encode("utf-8")).hexdigest(),
                "status": status,
                "fragment_hash": hashlib.sha256(_stable(fragment).encode("utf-8")).hexdigest() if fragment else None,
                "source_identity": {key: fragment_source.get(key) for key in ("resolved_identity", "provider_identity", "provider_sequence", "law_id", "law_key", "mst", "version_id") if fragment_source.get(key) not in (None, "")},
                "source_hashes": {key: fragment_meta.get(key) for key in ("response_sha256", "text_sha256") if fragment_meta.get(key)},
                "source_request": fragment_meta.get("request"),
                "retrieved_at": fragment_meta.get("retrieved_at"),
                "clock_source": fragment_meta.get("clock_source"),
                "raw_capture_ref": fragment_meta.get("response_sha256"),
                "source_completeness": fragment.get("source_completeness"),
                "error": fetch_error,
            })
            if status != "OK":
                missing.append({"source_unit_id": unit_id, "status": status, "error": fetch_error})
            elif need.get("support_eligible") is True and fragment.get("support_eligible") is False:
                missing.append({"source_unit_id": unit_id, "status": status, "reason": "SUPPORT_NOT_ELIGIBLE"})

        blocks: list[dict[str, Any]] = []
        blocks_by_request: dict[str, list[str]] = {}
        root_observation = fragments[_request_key(root_need)]
        root_fragment = _fragment_payload(root_observation)
        root_article_value = root_fragment.get("article")
        root_article: dict[str, Any] = root_article_value if isinstance(root_article_value, dict) else {}
        root_blocks_value = root_fragment.get("blocks")
        root_blocks_input: list[Any] = root_blocks_value if isinstance(root_blocks_value, list) else []
        root_first_block = root_blocks_input[0] if root_blocks_input and isinstance(root_blocks_input[0], dict) else {}
        root_text = _text(root_article.get("head_text") or root_article.get("text") or root_first_block.get("text") or root_fragment.get("text"))
        root_ok = str(root_observation.get("status") or "UNKNOWN").upper() == "OK" and bool(root_text)
        root_id = _unit_id(root_need)
        root_version = _version_id(root_need.get("version_binding"))

        def add_block(
            *, block_id: str, text: str, need: dict[str, Any], fragment: dict[str, Any],
            role: str | None, relation: str, parent_id: str | None = None,
            fragment_status: str = "OK", force_candidate: bool = False,
            structure: Any = None,
        ) -> None:
            if not text.strip():
                return
            version_id = _version_id(need.get("version_binding"))
            requested_role = role or need.get("evidence_role")
            valid_role = requested_role in {"ROOT_NORMATIVE", "GOVERNING_NORMATIVE", "TARGET_NORMATIVE", "TEMPORAL_NORMATIVE", "RETRIEVAL_CANDIDATE", "EXTERNAL_AUDIT", "EXPLANATORY"}
            requested_support = need.get("support_eligible") is True and fragment.get("support_eligible") is not False
            support = bool(valid_role and requested_role in {"ROOT_NORMATIVE", "GOVERNING_NORMATIVE", "TARGET_NORMATIVE", "TEMPORAL_NORMATIVE"} and requested_support and version_id and fragment_status == "OK" and not force_candidate)
            final_role = requested_role if valid_role else "RETRIEVAL_CANDIDATE"
            if not support and fragment_status != "OK":
                final_role = "RETRIEVAL_CANDIDATE"
            if not support and requested_role in {"ROOT_NORMATIVE", "GOVERNING_NORMATIVE", "TARGET_NORMATIVE", "TEMPORAL_NORMATIVE"} and not requested_support:
                final_role = "RETRIEVAL_CANDIDATE"
            if not support and not version_id and final_role in {"ROOT_NORMATIVE", "GOVERNING_NORMATIVE", "TARGET_NORMATIVE", "TEMPORAL_NORMATIVE"}:
                final_role = "RETRIEVAL_CANDIDATE"
            item: dict[str, Any] = {
                "block_id": block_id,
                "source_id": _source_id(fragment, need),
                "source_unit_id": _unit_id(need) if parent_id is None else block_id,
                "version_id": version_id,
                "content_hash": content_hash(text),
                "text": text,
                "role": final_role,
                "support_eligible": support,
                "relation_to_root": relation,
                "discovery_channel": need.get("discovery_channel"),
            }
            if parent_id:
                item["parent_block_id"] = parent_id
            if structure is not None:
                item["structure"] = structure
            blocks.append(item)

        root_block_id = f"root:{hashlib.sha256(root_id.encode('utf-8')).hexdigest()[:16]}"
        add_block(block_id=root_block_id, text=root_text, need=root_need, fragment=root_fragment,
                  role=root_need.get("evidence_role"), relation="root", fragment_status="OK" if root_ok else "MISSING",
                  force_candidate=not root_ok, structure=root_article.get("structure") or {key: value for key, value in root_first_block.items() if key != "text"})
        root_blocks = [root_block_id] if root_text else []
        root_structure = root_article.get("structure") or root_first_block.get("structure")
        if not root_structure and len(root_blocks_input) > 1:
            root_structure = root_blocks_input[1:]
        for node_id, node_text, node, parent in _iter_nodes(root_structure, root_block_id, "root:child"):
            add_block(block_id=node_id, text=node_text, need=root_need, fragment=root_fragment,
                      role=root_need.get("evidence_role"), relation="structural_child_of_root",
                      parent_id=parent, fragment_status="OK" if root_ok else "MISSING",
                      force_candidate=not root_ok, structure={k: v for k, v in (node or {}).items() if k != "text"})
            root_blocks.append(node_id)
        blocks_by_request[_context_key(root_need)] = root_blocks

        for need in needs[1:]:
            req = _request_key(need)
            context_key = _context_key(need)
            if context_key in blocks_by_request:
                continue
            fragment_observation = fragments[req]
            fragment = _fragment_payload(fragment_observation)
            status = str(fragment_observation.get("status") or "UNKNOWN").upper()
            article_value = fragment.get("article")
            article: dict[str, Any] = article_value if isinstance(article_value, dict) else {}
            candidates = article.get("structure") or fragment.get("structure") or fragment.get("blocks") or []
            if not candidates and need.get("evidence_role") in {"EXPLANATORY", "EXTERNAL_AUDIT"}:
                candidates = fragment.get("provider_tree") or []
            top_text = _text(article.get("head_text") or article.get("text") or fragment.get("text"))
            first_body_metadata: dict[str, Any] = {}
            if not top_text and isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) and _text(candidates[0].get("text")):
                first_body_metadata = {key: value for key, value in candidates[0].items() if key != "text"}
                top_text = _text(candidates[0].get("text"))
                candidates = candidates[1:]
            base_id = str(need.get("block_id") or ("target:" + hashlib.sha256(context_key.encode("utf-8")).hexdigest()[:16]))
            out_ids: list[str] = []
            if top_text:
                add_block(block_id=base_id, text=top_text, need=need, fragment=fragment,
                role=need.get("evidence_role"), relation=str(need.get("relation_to_root") or "caller_selected_need"),
                          fragment_status=status, force_candidate=(not need.get("support_eligible", False) or status != "OK"),
                          structure=first_body_metadata or candidates)
                out_ids.append(base_id)
            for node_id, node_text, node, parent in _iter_nodes(candidates, base_id, base_id + ":child"):
                add_block(block_id=node_id, text=node_text, need=need, fragment=fragment,
                          role=need.get("evidence_role"), relation=str(need.get("relation_to_root") or "structural_context"),
                          parent_id=parent, fragment_status=status,
                          force_candidate=(not need.get("support_eligible", False) or status != "OK"),
                          structure={k: v for k, v in (node or {}).items() if k != "text"})
                out_ids.append(node_id)
            if not out_ids:
                missing.append({"source_unit_id": _unit_id(need), "status": status, "reason": "EMPTY_FRAGMENT"})
            blocks_by_request[context_key] = out_ids

        cov = dict(coverage or {})
        required = cov.get("required_channels", [])
        completed = cov.get("completed_channels", [])
        if not isinstance(required, list):
            required = []
        if not isinstance(completed, list):
            completed = []
        for need in needs:
            channel = need.get("discovery_channel")
            if channel and channel not in completed and _request_key(need) in fragments and str(fragments[_request_key(need)].get("status") or "UNKNOWN").upper() == "OK":
                completed.append(channel)
        existing_missing = cov.get("missing_context", [])
        if not isinstance(existing_missing, list):
            existing_missing = [{"reason": "MALFORMED_CALLER_MISSING_CONTEXT", "value": repr(existing_missing)}]
        source_completeness = cov.get("source_completeness")
        for need in needs:
            frag = fragments[_request_key(need)]
            fragment_completeness = frag.get("source_completeness", (frag.get("meta") or {}).get("source_completeness") if isinstance(frag.get("meta"), dict) else None)
            is_incomplete = fragment_completeness is False or isinstance(fragment_completeness, dict) and fragment_completeness.get("complete") is False
            if is_incomplete:
                source_completeness = False
                missing.append({"source_unit_id": _unit_id(need), "reason": "SOURCE_INCOMPLETE"})
                if isinstance(fragment_completeness, dict) and isinstance(fragment_completeness.get("missing_context"), list):
                    missing.extend({"source_unit_id": _unit_id(need), **item} for item in fragment_completeness["missing_context"] if isinstance(item, dict))
        cov_packet: dict[str, Any] = {
            "required_channels": list(dict.fromkeys(required)),
            "completed_channels": list(dict.fromkeys(completed)),
            "residual_discovery_complete": cov.get("residual_discovery_complete") is True,
            "missing_context": [*existing_missing, *missing],
        }
        if source_completeness is not None:
            cov_packet["source_completeness"] = source_completeness
        temporal = dict(temporal_binding or {})
        temporal.setdefault("as_of", as_of)
        temporal.setdefault("event_anchor", None)
        temporal.setdefault("cutover_resolved", False)
        temporal.setdefault("mixed_versions", False)
        # A same-source version disagreement is mechanically visible; the
        # producer flags it but leaves resolution to the caller.
        source_versions: dict[str, set[str]] = {}
        for block in blocks:
            if block.get("source_id") and block.get("version_id"):
                source_versions.setdefault(block["source_id"], set()).add(block["version_id"])
        if any(len(versions) > 1 for versions in source_versions.values()):
            temporal["mixed_versions"] = True
        requested_status = "READY" if not cov_packet["missing_context"] and cov_packet["residual_discovery_complete"] and set(cov_packet["required_channels"]).issubset(cov_packet["completed_channels"]) else "PARTIAL"
        # Gate booleans describe observable mechanics only, never semantic sign-off.
        gate = {
            "version_bound": bool(root_version) and all(not b["support_eligible"] or bool(b.get("version_id")) for b in blocks),
            "structural_context_bound": bool(root_text) and all(not b.get("parent_block_id") or b["parent_block_id"] in {x["block_id"] for x in blocks} for b in blocks),
            "evidence_roles_valid": all(b["role"] in {"ROOT_NORMATIVE", "GOVERNING_NORMATIVE", "TARGET_NORMATIVE", "TEMPORAL_NORMATIVE", "RETRIEVAL_CANDIDATE", "EXTERNAL_AUDIT", "EXPLANATORY"} and (not b["support_eligible"] or b["role"] in {"ROOT_NORMATIVE", "GOVERNING_NORMATIVE", "TARGET_NORMATIVE", "TEMPORAL_NORMATIVE"}) for b in blocks),
            "candidate_coverage_complete": set(cov_packet["required_channels"]).issubset(cov_packet["completed_channels"]),
            "transform_audit_clear": all(t.get("kind") != "AMBIGUOUS" and t.get("audit_status") not in {"REJECT", "REVIEW_REQUIRED"} and (t.get("kind") != "CONTEXTUAL_INFERRED" or t.get("audit_status") == "PASS") for t in transform_decisions),
            "temporal_binding_clear": not temporal.get("mixed_versions") or bool(temporal.get("cutover_resolved")),
        }
        packet = {
            "schema": "dps-context-v0.3",
            "packet_id": str(root_need.get("packet_id") or ("ctx:" + hashlib.sha256(_stable([_unit_id(n) for n in needs]).encode("utf-8")).hexdigest()[:20])),
            "mode": mode,
            "as_of": as_of,
            "transport_status": requested_status,
            "root": {"source_unit_id": root_id, "version_id": root_version, "text_hash": content_hash(root_text) if root_text else None},
            "context_blocks": blocks,
            "coverage": cov_packet,
            "transform_decisions": list(transform_decisions),
            "temporal_binding": temporal,
            "shared_context_refs": [b["block_id"] for b in blocks if b.get("parent_block_id")],
            "observations": observations,
            "retrieval_candidates": [
                {**candidate, "request_key": hashlib.sha256(req.encode("utf-8")).hexdigest(),
                 "role": "RETRIEVAL_CANDIDATE", "support_eligible": False}
                for req, fragment in fragments.items()
                for candidate in fragment.get("attachment_retrieval_candidates", [])
                if isinstance(candidate, dict)
            ],
            "commit_gate": gate,
        }
        packet["validation_errors"] = validate_packet(packet)
        if packet["validation_errors"]:
            packet["transport_status"] = "PARTIAL"
        return packet
