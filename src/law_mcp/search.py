"""Official search listings. Lists candidate documents and versions; never selects one."""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from typing import Any

from .card_source import SEARCH_URL, ProviderResponseError, _require_xml_payload

# target -> (item tag in the listing, how history is requested)
_TARGETS = {
    "eflaw": ("law", None),        # 법령: 시행일 기준 목록. 연혁·현행·시행예정을 함께 돌려준다.
    "admrul": ("admrul", "nw"),    # 행정규칙: nw=1 현행만, nw=2 연혁 포함
    "ppc": ("ppc", None),          # 개인정보보호위원회 결정문(의결서). 안건명이 빈 결정이 많아 본문 검색 권장
    "prec": ("prec", None),        # 법원 판례
}


def search_documents(
    client: Any, *, target: str, query: str, include_history: bool = False,
    display: int = 100, page: int = 1, search_body: bool = False,
) -> dict[str, Any]:
    if target not in _TARGETS:
        raise ValueError(f"target must be one of {sorted(_TARGETS)}")
    if not query.strip():
        raise ValueError("query is required")
    if not 1 <= display <= 100 or page < 1:
        raise ValueError("display must be 1..100 and page >= 1")
    item_tag, history_param = _TARGETS[target]
    params: dict[str, Any] = {"target": target, "query": query, "display": str(display), "page": str(page)}
    if history_param:
        params[history_param] = "2" if include_history else "1"
    if search_body:
        params["search"] = "2"  # 제공처 규약: 1=제목(기본), 2=본문
    raw = client._call(SEARCH_URL, params)
    _require_xml_payload(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ProviderResponseError("PARSE_ERROR", f"search listing is not well-formed XML: {exc}",
                                    retryable=True) from exc
    rows = [{child.tag: (child.text or "").strip() for child in item}
            for item in root if item.tag == item_tag]
    total = int((root.findtext("totalCnt") or "0").strip() or 0)
    return {
        "status": "OK", "target": target, "query": query, "include_history": include_history,
        "search_body": search_body, "total_count": total, "page": page, "display": display,
        "truncated": total > page * display, "rows": rows,
        "support_eligible": False,
        "note": "검색 목록은 후보일 뿐입니다. 판본 선택과 판본 확인은 호출자가 합니다.",
        "meta": {"request": params, "response_sha256": hashlib.sha256(raw).hexdigest()},
    }
