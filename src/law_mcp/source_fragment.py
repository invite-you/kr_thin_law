"""Thin, version-bound retrieval for non-law and law source fragments.

This module transports official source material. It does not decide legal
meaning, promote evidence, or close a downstream obligation.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import re
import xml.etree.ElementTree as ET
from typing import Any

from .card_source import (
    ProviderResponseError,
    SERVICE_URL,
    _require_xml_payload,
    encode_jo,
    parse_eflaw_article_xml,
)


_SPECS: dict[str, dict[str, Any]] = {
    "law": {
        "id_params": ("MST",), "default": "MST", "stable_params": (),
    },
    "admin_rule": {
        "target": "admrul", "id_params": ("ID", "LID"), "default": "ID",
        "identity_tags": {"ID": ("일련번호", "행정규칙일련번호"), "LID": ("행정규칙ID",)},
        "sequence_tags": ("일련번호", "행정규칙일련번호"),
        "title_tags": ("행정규칙명",), "effective_tags": ("시행일자",),
        "issued_tags": ("발령일자",),
        "content_tags": ("조문내용", "부칙내용", "별표내용", "개정문내용", "제개정이유내용"),
        "stable_params": ("LID",),
        "roots": ("AdmRulService", "행정규칙", "AdmRul"),
    },
    "ordinance": {
        "target": "ordin", "id_params": ("MST", "ID"), "default": "MST",
        "identity_tags": {"MST": ("자치법규일련번호",), "ID": ("자치법규ID",)},
        "sequence_tags": ("자치법규일련번호",),
        "title_tags": ("자치법규명",), "effective_tags": ("시행일자",),
        "issued_tags": ("공포일자",),
        "content_tags": ("조내용", "부칙내용", "별표내용", "개정문내용", "제개정이유내용"),
        "stable_params": ("ID",),
        "roots": ("LawService", "법령", "자치법규", "OrdinService", "ordin"),
    },
    "treaty": {
        "target": "trty", "id_params": ("ID",), "default": "ID",
        "identity_tags": {"ID": ("조약일련번호",)},
        "sequence_tags": ("조약일련번호",),
        "title_tags": ("조약명_한글", "조약명한글"),
        "effective_tags": ("발효일자", "국내발효일자"),
        "issued_tags": ("서명일자", "체결일자"),
        "content_tags": ("조약내용",), "stable_params": (),
        "roots": ("BothTrtyService", "조약", "TrtyService", "TreatyService", "trty"),
    },
    "institution_rule": {
        "targets": ("school", "public", "pi"), "id_params": ("ID", "LID"), "default": "ID",
        "identity_tags": {"ID": ("일련번호", "행정규칙일련번호"), "LID": ("행정규칙ID",)},
        "sequence_tags": ("일련번호", "행정규칙일련번호"),
        "title_tags": ("행정규칙명",), "effective_tags": ("시행일자",),
        "issued_tags": ("발령일자",),
        "content_tags": ("조문내용", "부칙내용", "별표내용"), "stable_params": ("LID",),
        "roots": ("AdmRulService", "행정규칙", "AdmRul"),
    },
}
_ATTACHMENT_TAG = re.compile(r"별표|별지|첨부|부록|서식")


def _raw(value: bytes | str) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


def _text(node: ET.Element | None) -> str:
    return "" if node is None else "".join(node.itertext()).strip()


def _first(root: ET.Element, tags: tuple[str, ...]) -> str:
    for tag in tags:
        for node in root.iter(tag):
            value = _text(node)
            if value:
                return value
    return ""


def _date(value: Any, field: str) -> str:
    """Validate a provider/resolver date and return canonical YYYYMMDD."""
    value = str(value or "").strip()
    if not value:
        return ""
    normalized = value.replace("-", "")
    if not re.fullmatch(r"\d{8}", normalized):
        raise ValueError(f"{field} must be YYYYMMDD or YYYY-MM-DD")
    try:
        datetime.strptime(normalized, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid calendar date") from exc
    return normalized


def _tree(node: ET.Element, path: str) -> dict[str, Any]:
    counts: dict[str, int] = {}
    children = []
    for child in list(node):
        counts[child.tag] = counts.get(child.tag, 0) + 1
        children.append(_tree(child, f"{path}/{child.tag}[{counts[child.tag]}]"))
    return {
        "provider_tag": node.tag,
        "provider_path": path,
        "attributes": dict(node.attrib),
        "text": (node.text or "").strip(),
        "tail": (node.tail or "").strip(),
        "children": children,
    }


def _blocks(root: ET.Element, content_tags: tuple[str, ...]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    wanted = set(content_tags)
    blocks: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []

    header_tags = {"조문번호", "조문제목", "조제목", "별표제목", "별표번호", "별표가지번호", "별표구분", "부칙공포일자", "부칙공포번호"}

    def walk(node: ET.Element, path: str, ancestors: list[dict[str, Any]]) -> None:
        counts: dict[str, int] = {}
        for child in list(node):
            counts[child.tag] = counts.get(child.tag, 0) + 1
            child_path = f"{path}/{child.tag}[{counts[child.tag]}]"
            text = _text(child)
            headers = {item.tag: _text(item) for item in list(node) if item.tag in header_tags}
            context = [*ancestors, {"provider_tag": node.tag, "provider_path": path, "headers": headers}]
            entry: dict[str, Any] = {"provider_tag": child.tag, "provider_path": child_path, "text": text,
                     "provider_ancestry": context}
            if child.tag in wanted and text:
                if list(child):
                    entry["structure"] = _tree(child, child_path)
                blocks.append(entry)
            if _ATTACHMENT_TAG.search(child.tag):
                attachments.append({**entry, "tree": _tree(child, child_path)})
            walk(child, child_path, context)

    walk(root, f"/{root.tag}[1]", [])
    return blocks, attachments


def _source_completeness(
    root: ET.Element, blocks: list[dict[str, Any]], attachments: list[dict[str, Any]],
    content_tags: tuple[str, ...],
) -> dict[str, Any]:
    missing: list[dict[str, str]] = []
    unfetched: list[dict[str, Any]] = []
    parents = {child: parent for parent in root.iter() for child in list(parent)}
    for node in root.iter():
        if "링크" not in node.tag:
            continue
        link = _text(node)
        if not link:
            continue
        ancestor = parents.get(node)
        while ancestor is not None and not (
            _ATTACHMENT_TAG.search(ancestor.tag) or ancestor.tag in {"별표단위", "별지단위"}
        ):
            ancestor = parents.get(ancestor)
        has_inline_body = ancestor is not None and any(
            child.tag in content_tags and _text(child)
            for child in ancestor.iter()
        )
        if has_inline_body:
            continue
        pointer: dict[str, Any] = {
            "provider_tag": node.tag, "locator": link, "provider_path": _path_for(root, node),
            "status": "ATTACHMENT_CONTENT_UNFETCHED",
        }
        is_structural = bool(ancestor is not None and ancestor.tag in {"별표단위", "별지단위"})
        if is_structural:
            pointer["status"] = "ATTACHMENT_CONTENT_UNAVAILABLE"
            missing.append({"kind": "ATTACHMENT_CONTENT_UNAVAILABLE", **pointer})
        else:
            pointer["role"] = "RETRIEVAL_CANDIDATE"
        unfetched.append(pointer)
    inline_complete = bool(blocks) and not missing
    return {
        "scope": "provider_inline_body",
        "complete": inline_complete,
        "inline_normative_complete": inline_complete,
        "status": "COMPLETE" if inline_complete else (
            "ATTACHMENT_CONTENT_UNAVAILABLE" if missing else "NORMATIVE_BLOCK_MISSING"
        ),
        "missing_context": missing,
        "unfetched_attachment_count": len(unfetched),
        "unfetched_attachments": unfetched,
    }


def _path_for(root: ET.Element, target: ET.Element) -> str:
    parts: list[str] = []
    current = target
    while current is not root:
        parent = next((p for p in root.iter() if current in list(p)), None)
        if parent is None:
            break
        peers = [child for child in list(parent) if child.tag == current.tag]
        parts.append(f"{current.tag}[{peers.index(current) + 1}]")
        current = parent
    return "/" + root.tag + "[1]" + ("/" + "/".join(reversed(parts)) if parts else "")


def _meta(raw: bytes, request: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "raw_xml": raw.decode("utf-8", errors="replace"),
        "raw_bytes_base64": base64.b64encode(raw).decode("ascii"),
        "request": dict(request),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "clock_source": "local",
        "semantic_processing_added": False,
    }


def _unsupported(status: str, api_family: str, binding: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "api_family": api_family,
        "support_eligible": False,
        "version_binding": binding,
        **extra,
    }


def _identity_contract(
    family: str, binding: dict[str, Any], spec: dict[str, Any], id_param: str
) -> tuple[str, str, str] | dict[str, Any]:
    provider_id = str(binding.get("provider_id") or "").strip()
    version_id = str(binding.get("version_id") or "").strip()
    expected_date = binding.get("effective_date", binding.get("expected_effective_date"))
    if not provider_id or not version_id or (not expected_date and family != "institution_rule"):
        return _unsupported("TARGET_VERSION_UNRESOLVED", family, binding)
    if expected_date:
        try:
            expected_date = _date(expected_date, "version_binding.effective_date")
        except ValueError as exc:
            return _unsupported("TARGET_VERSION_UNRESOLVED", family, binding, detail=str(exc))
    else:
        expected_date = ""

    # Stable document identifiers select a document series, not a version.
    if id_param in spec["stable_params"]:
        expected_sequence = str(
            binding.get("expected_provider_sequence")
            or binding.get("resolved_provider_sequence")
            or binding.get("provider_sequence")
            or binding.get("provider_seq")
            or ""
        ).strip()
        if not expected_sequence and version_id.isdigit() and version_id != provider_id:
            expected_sequence = version_id
        if not expected_sequence:
            return _unsupported("TARGET_VERSION_UNRESOLVED", family, binding,
                                detail="stable document ID requires expected provider sequence")
    else:
        expected_sequence = provider_id

    expected_identity = binding.get("expected_identity")
    if isinstance(expected_identity, dict):
        expected_identity = expected_identity.get("provider_id") or expected_identity.get("identity")
    if expected_identity and str(expected_identity) != provider_id:
        return _unsupported("TARGET_VERSION_UNRESOLVED", family, binding,
                            detail="expected_identity does not match provider_id")
    return provider_id, expected_date, expected_sequence


def parse_provider_fragment_xml(
    xml_bytes: bytes | str,
    *,
    api_family: str,
    resolved_identity: str,
    id_param: str,
    provider_target: str,
    request: dict[str, Any] | None = None,
    expected_effective_date: str | None = None,
    expected_sequence: str | None = None,
    version_id: str | None = None,
) -> dict[str, Any]:
    """Parse one provider response, keeping its structure and raw payload."""
    spec = _SPECS.get(api_family)
    if spec is None:
        return _unsupported("FETCH_CAPABILITY_MISSING", api_family, {})
    raw = _raw(xml_bytes)
    _require_xml_payload(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ProviderResponseError("PARSE_ERROR", f"provider response is not well-formed XML: {exc}",
                                    retryable=True, raw_preview=raw[:500].decode("utf-8", "replace")) from exc
    if root.tag.lower() in {"html", "error", "errors", "에러"}:
        raise ProviderResponseError("UNEXPECTED_ROOT", f"unexpected provider root: {root.tag}",
                                    retryable=False, raw_preview=raw[:500].decode("utf-8", "replace"))
    if root.tag not in spec["roots"]:
        raise ProviderResponseError("UNEXPECTED_ROOT", f"unexpected {api_family} provider root: {root.tag}",
                                    retryable=False, raw_preview=raw[:500].decode("utf-8", "replace"))
    if id_param not in spec["id_params"]:
        return _unsupported("IDENTITY_PARAMETER_UNSUPPORTED", api_family, {}, allowed=list(spec["id_params"]))

    provider_identity = _first(root, spec["identity_tags"].get(id_param, ()))
    sequence = _first(root, spec["sequence_tags"])
    title = _first(root, spec["title_tags"])
    blocks, attachments = _blocks(root, spec["content_tags"])
    completeness = _source_completeness(root, blocks, attachments, spec["content_tags"])
    tree = _tree(root, f"/{root.tag}[1]")
    if not provider_identity or not sequence or not title or not blocks:
        return {
            "status": "PROVIDER_STRUCTURE_MISSING", "api_family": api_family,
            "provider_target": provider_target, "support_eligible": False,
            "resolved_identity": str(resolved_identity), "provider_identity": provider_identity,
            "provider_sequence": sequence, "blocks": blocks, "attachments": attachments,
            "attachment_retrieval_candidates": [a for a in completeness["unfetched_attachments"]
                                                 if a["status"] == "ATTACHMENT_CONTENT_UNFETCHED"],
            "source_completeness": completeness,
            "structural_context": {"preserved": True, "provider_root": root.tag, "provider_tree": tree},
            "provider_tree": tree, "meta": _meta(raw, request or {}, ""),
        }
    if provider_identity != str(resolved_identity):
        return {
            "status": "PROVIDER_IDENTITY_MISMATCH", "api_family": api_family,
            "provider_target": provider_target, "support_eligible": False,
            "resolved_identity": str(resolved_identity), "provider_identity": provider_identity,
            "provider_sequence": sequence, "blocks": blocks, "attachments": attachments,
            "attachment_retrieval_candidates": [a for a in completeness["unfetched_attachments"]
                                                 if a["status"] == "ATTACHMENT_CONTENT_UNFETCHED"],
            "source_completeness": completeness,
            "structural_context": {"preserved": True, "provider_root": root.tag, "provider_tree": tree},
            "provider_tree": tree, "meta": _meta(raw, request or {}, ""),
        }
    if expected_sequence and sequence != str(expected_sequence):
        return {
            "status": "PROVIDER_VERSION_MISMATCH", "api_family": api_family,
            "provider_target": provider_target, "support_eligible": False,
            "resolved_identity": str(resolved_identity), "provider_identity": provider_identity,
            "provider_sequence": sequence, "expected_provider_sequence": str(expected_sequence),
            "version_id": version_id, "blocks": blocks, "attachments": attachments,
            "attachment_retrieval_candidates": [a for a in completeness["unfetched_attachments"]
                                                 if a["status"] == "ATTACHMENT_CONTENT_UNFETCHED"],
            "source_completeness": completeness,
            "structural_context": {"preserved": True, "provider_root": root.tag, "provider_tree": tree},
            "provider_tree": tree, "meta": _meta(raw, request or {}, ""),
        }

    effective_raw = _first(root, spec["effective_tags"])
    issued_raw = _first(root, spec["issued_tags"])
    try:
        effective_date = _date(effective_raw, "provider.effective_date")
        issued_date = _date(issued_raw, "provider.issued_date")
    except ValueError as exc:
        raise ProviderResponseError("INVALID_PROVIDER_DATE", str(exc), retryable=False,
                                    raw_preview=raw[:500].decode("utf-8", "replace")) from exc
    try:
        expected = _date(expected_effective_date, "expected_effective_date") if expected_effective_date else ""
    except ValueError as exc:
        raise ProviderResponseError("INVALID_EXPECTED_DATE", str(exc), retryable=False,
                                    raw_preview=raw[:500].decode("utf-8", "replace")) from exc
    full_text = "\n".join(block["text"] for block in blocks)
    date_matches = bool(effective_date and expected and effective_date == expected)
    eligible = date_matches and completeness["complete"]
    status = "OK" if eligible else (
        "EFFECTIVE_DATE_NOT_MACHINE_READABLE" if not effective_date else
        "TARGET_VERSION_UNVERIFIABLE" if not expected else "EFFECTIVE_DATE_MISMATCH"
    )
    if date_matches and not completeness["complete"]:
        status = completeness["status"]
    return {
        "status": status, "api_family": api_family, "provider_target": provider_target,
        "support_eligible": eligible,
        "source": {
            "official_source": "law.go.kr", "resolved_identity": str(resolved_identity),
            "document_id": _first(root, ("행정규칙ID", "자치법규ID")) or sequence,
            "provider_identity": provider_identity, "provider_sequence": sequence,
            "identity_parameter": id_param, "version_id": version_id or "",
            "title": title, "issued_date": issued_date, "effective_date": effective_date,
            "expected_effective_date": expected, "temporal_source_state": (
                "EFFECTIVE_DATE_PRESENT" if effective_date else "EFFECTIVE_DATE_NOT_MACHINE_READABLE"
            ),
        },
        "blocks": blocks, "attachments": attachments,
        "attachment_retrieval_candidates": [a for a in completeness["unfetched_attachments"]
                                             if a["status"] == "ATTACHMENT_CONTENT_UNFETCHED"],
        "source_completeness": completeness,
        "structural_context": {"preserved": True, "provider_root": root.tag, "provider_tree": tree},
        "provider_tree": tree,
        "meta": _meta(raw, request or {}, full_text),
    }


def law_version_status(
    raw: bytes, source: dict[str, Any], binding: dict[str, Any], provider_id: str, expected_date: str,
) -> tuple[str, str, str]:
    """Check one eflaw response against the caller's version binding. Returns (status, MST, 시행일자)."""
    law_key = str(source.get("law_key") or "")
    # The law.go.kr law_key is document ID + promulgation data; it does not
    # encode the requested MST. The parser's mst field is the request value,
    # so it is not independent proof of response identity.
    actual_mst = _first(ET.fromstring(raw), ("MST", "법령일련번호"))
    try:
        actual_date = _date(source.get("effective_date"), "provider.effective_date")
    except ValueError as exc:
        raise ProviderResponseError("INVALID_PROVIDER_DATE", str(exc), retryable=False,
                                    raw_preview=raw[:500].decode("utf-8", "replace")) from exc
    expected_law_id = str(binding.get("law_id") or binding.get("expected_law_id") or "")
    actual_law_id = str(source.get("law_id") or "")
    expected_law_key = str(binding.get("expected_law_key") or "")
    expected_response_hash = str(binding.get("expected_response_sha256") or "")
    actual_response_hash = hashlib.sha256(raw).hexdigest()
    if actual_mst and actual_mst != provider_id:
        status = "PROVIDER_IDENTITY_MISMATCH"
    elif expected_law_id and actual_law_id != expected_law_id:
        status = "PROVIDER_IDENTITY_MISMATCH"
    elif expected_law_key and law_key != expected_law_key:
        status = "PROVIDER_VERSION_MISMATCH"
    elif expected_response_hash and actual_response_hash != expected_response_hash:
        status = "PROVIDER_VERSION_MISMATCH"
    elif not expected_law_key and not expected_response_hash:
        status = "TARGET_VERSION_UNVERIFIABLE"
    elif not actual_date:
        status = "EFFECTIVE_DATE_NOT_MACHINE_READABLE"
    elif actual_date != expected_date:
        status = "EFFECTIVE_DATE_MISMATCH"
    else:
        status = "OK"
    return status, actual_mst, actual_date


def _fetch_law(
    client: Any, binding: dict[str, Any], provider_id: str, expected_date: str,
    observed_identity: str | None, locator: dict[str, Any] | None,
) -> dict[str, Any]:
    loc = dict(locator or {})
    if not loc.get("article"):
        return _unsupported("LOCATOR_REQUIRED", "law", binding, observed_identity=str(observed_identity or ""))
    if set(loc) - {"article", "branch"}:
        return _unsupported("LOCATOR_UNSUPPORTED", "law", binding, locator=loc)
    try:
        jo = encode_jo(loc["article"], loc.get("branch"))
    except (TypeError, ValueError) as exc:
        return _unsupported("LOCATOR_UNSUPPORTED", "law", binding, detail=str(exc), locator=loc)
    params = {"target": "eflaw", "MST": provider_id, "efYd": expected_date, "JO": jo}
    raw = _raw(client._call(SERVICE_URL, params))
    _require_xml_payload(raw)
    try:
        fragment = parse_eflaw_article_xml(raw, mst=provider_id, effective_date=None,
                                           article=loc["article"], branch=loc.get("branch"))
    except ProviderResponseError:
        raise
    source = fragment["source"]
    status, actual_mst, actual_date = law_version_status(raw, source, binding, provider_id, expected_date)
    law_key = str(source.get("law_key") or "")
    text = str(fragment["article"].get("text") or "")
    meta = _meta(raw, params, text)
    meta.update({k: v for k, v in fragment["meta"].items() if k == "text_sha256"})
    return {
        "status": status, "api_family": "law", "support_eligible": status == "OK",
        "observed_identity": str(observed_identity or ""), "version_binding": binding,
        "source": {**source, "mst": actual_mst, "law_key": law_key,
                   "effective_date": actual_date, "expected_effective_date": expected_date,
                   "version_id": str(binding.get("version_id") or "")},
        "blocks": [{"provider_tag": "조문단위", "provider_path": "/법령[1]/조문/조문단위",
                    "text": text, "structure": fragment["article"].get("structure", [])}],
        "source_completeness": {"scope": "provider_inline_body", "complete": True,
                                "inline_normative_complete": True, "status": "COMPLETE",
                                "missing_context": [], "unfetched_attachment_count": 0,
                                "unfetched_attachments": []},
        "structural_context": {"preserved": True, "provider_root": "법령",
                               "structure": fragment["article"].get("structure", [])},
        "fragment": fragment, "meta": meta,
    }


def fetch_source_fragment(
    client: Any,
    *,
    api_family: str,
    observed_identity: str | None,
    version_binding: dict[str, Any],
    locator: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch only after a downstream resolver supplied identity and version."""
    binding = dict(version_binding or {})
    observed_id: str = str(observed_identity or "")
    common: dict[str, Any] = {"api_family": api_family, "observed_identity": observed_id,
                              "version_binding": binding}
    spec = _SPECS.get(api_family)
    if spec is None:
        return _unsupported("FETCH_CAPABILITY_MISSING", api_family, binding,
                            observed_identity=common["observed_identity"])
    if binding.get("status") != "RESOLVED":
        return _unsupported("TARGET_VERSION_UNRESOLVED", api_family, binding,
                            observed_identity=common["observed_identity"])

    if api_family == "institution_rule" and binding.get("provider_target") not in spec["targets"]:
        return _unsupported("TARGET_SUBTYPE_UNRESOLVED", api_family, binding,
                            observed_identity=common["observed_identity"],
                            required_provider_target=list(spec["targets"]))
    id_param = str(binding.get("provider_id_param") or spec["default"])
    if id_param not in spec["id_params"]:
        return _unsupported("IDENTITY_PARAMETER_UNSUPPORTED", api_family, binding,
                            observed_identity=common["observed_identity"], allowed=list(spec["id_params"]))
    contract = _identity_contract(api_family, binding, spec, id_param)
    if isinstance(contract, dict):
        return {**contract, "observed_identity": common["observed_identity"]}
    provider_id, expected_date, expected_sequence = contract

    if api_family == "law":
        return _fetch_law(client, binding, provider_id, expected_date,
                          common["observed_identity"], locator)

    loc = dict(locator or {})
    params = {"target": (binding.get("provider_target") if api_family == "institution_rule" else spec["target"]),
              id_param: provider_id, "type": "XML"}
    raw = client._call(SERVICE_URL, params)
    projected = parse_provider_fragment_xml(
        raw, api_family=api_family, resolved_identity=provider_id, id_param=id_param,
        provider_target=str(params["target"]), request=params,
        expected_effective_date=expected_date, expected_sequence=expected_sequence,
        version_id=str(binding.get("version_id") or ""),
    )
    projected.update(common)
    if loc:
        projected["requested_locator"] = loc
        projected["support_eligible"] = False
        if projected.get("status") == "OK":
            projected["status"] = "LOCATOR_UNSUPPORTED_FULL_DOCUMENT"
    return projected
