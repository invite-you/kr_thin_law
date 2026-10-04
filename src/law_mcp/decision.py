"""Decisions (개인정보보호위원회 의결서 `ppc`, 법원 판례 `prec`) as delivered by the provider.

Transport only: every provider field is returned verbatim under its own tag. Citations,
violated provisions and amounts are NOT extracted here (the provider places them inside
free text in different fields, e.g. a 의결서 may put the 과태료 amount under 신청인).
The requested serial is checked against the response; nothing is selected or interpreted.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from typing import Any

from .card_source import SERVICE_URL, ProviderResponseError, _require_xml_payload

_SPECS = {
    "ppc": {"root": "PpcService", "id_tags": ("결정문일련번호",), "date_tags": ("의결일자",)},
    "prec": {"root": "PrecService", "id_tags": ("판례정보일련번호", "판례일련번호"), "date_tags": ("선고일자",)},
}


def fetch_decision(client: Any, *, target: str, decision_id: str) -> dict[str, Any]:
    if target not in _SPECS:
        raise ValueError(f"target must be one of {sorted(_SPECS)}")
    decision_id = str(decision_id).strip()
    if not decision_id.isdigit():
        raise ValueError("decision_id must be the provider serial number (digits)")
    spec = _SPECS[target]
    params = {"target": target, "ID": decision_id}
    raw = client._call(SERVICE_URL, params)
    _require_xml_payload(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ProviderResponseError("PARSE_ERROR", f"decision response is not well-formed XML: {exc}",
                                    retryable=True) from exc
    fields = [{"tag": el.tag, "text": el.text or ""} for el in root.iter() if el is not root and len(el) == 0]
    by_tag = {f["tag"]: f["text"].strip() for f in fields}
    actual_id = next((by_tag[t] for t in spec["id_tags"] if by_tag.get(t)), "")
    if root.tag != spec["root"] or not fields:
        status = "DECISION_NOT_FOUND"
    elif actual_id != decision_id:
        status = "PROVIDER_IDENTITY_MISMATCH"
    else:
        status = "OK"
    return {
        "status": status, "target": target, "decision_id": decision_id, "provider_root": root.tag,
        "decision_date": next((by_tag[t] for t in spec["date_tags"] if by_tag.get(t)), ""),
        "fields": fields if status == "OK" else [],
        "support_eligible": status == "OK",
        "note": "제공처 칸을 그대로 돌려줍니다. 위반 조항·금액 등은 본문 글 안에 있으며 이 도구는 뽑지 않습니다.",
        "meta": {"request": params, "response_sha256": hashlib.sha256(raw).hexdigest()},
    }
