"""Supplementary provisions (부칙) and annexes (별표) of one verified law version.

law_article returns one 조문 only. 부칙 (시행일·적용례·경과조치) and 별표 (예: 과태료 부과기준)
live in the same ``target=eflaw`` response, so this module fetches the whole version once,
checks it with the same version binding as law_article, and returns those blocks unchanged.

Transport only: no legal meaning is decided. A 부칙 is additionally split into its 조
(``제N조(...)`` at line start) and each 조 gets the law_article 조→항→호→목 shape via
flat_structure; the slices are checked to rebuild the 부칙 text byte-for-byte.
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from .card_source import ProviderResponseError, SERVICE_URL, _require_xml_payload, eflaw_source
from .flat_structure import DERIVATION, StructureDerivationError, derive_article
from .source_fragment import _SPECS, _identity_contract, _unsupported, law_version_status

_ADDENDUM_ARTICLE = re.compile(r"^제\d+조(?:의\d+)?\s*\(", re.MULTILINE)
_ANNEX_FILE_TAGS = ("별표서식파일링크", "별표서식PDF파일링크", "별표HWP파일명", "별표PDF파일명")


def _all_text(el: ET.Element | None) -> str:
    return "".join(el.itertext()) if el is not None else ""


def split_addendum(text: str) -> dict[str, Any]:
    """Split one 부칙 text into its leading line(s) and 조 with law_article structure."""
    starts = [m.start() for m in _ADDENDUM_ARTICLE.finditer(text)]
    if not starts:
        return {"head_text": text.strip(), "head_span": [0, len(text)], "articles": [],
                "structure_derivation": "NO_ARTICLE_MARKER"}
    bounds = starts + [len(text)]
    articles = []
    for start, end in zip(bounds, bounds[1:]):
        derived = derive_article(text[start:end])
        derived["span"] = [start, end]
        articles.append(derived)
    if text[:starts[0]] + "".join(text[a["span"][0]:a["span"][1]] for a in articles) != text:
        raise StructureDerivationError("addendum slices do not rebuild the provider text")
    return {"head_text": text[:starts[0]].strip(), "head_span": [0, starts[0]], "articles": articles,
            "structure_derivation": DERIVATION}


def fetch_law_supplements(client: Any, *, version_binding: dict[str, Any]) -> dict[str, Any]:
    """Fetch one verified law version and return its 부칙 and 별표 blocks."""
    binding = dict(version_binding or {})
    if binding.get("status") != "RESOLVED":
        return _unsupported("TARGET_VERSION_UNRESOLVED", "law", binding)
    contract = _identity_contract("law", binding, _SPECS["law"], "MST")
    if isinstance(contract, dict):
        return contract
    provider_id, expected_date, _ = contract
    params = {"target": "eflaw", "MST": provider_id, "efYd": expected_date}
    raw = client._call(SERVICE_URL, params)
    _require_xml_payload(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ProviderResponseError("PARSE_ERROR", "Provider XML is malformed", retryable=True) from exc
    basic = root.find("./기본정보")
    if root.tag != "법령" or basic is None:
        raise ProviderResponseError("UNEXPECTED_ROOT", "eflaw response must be 법령 with 기본정보",
                                    retryable=False)
    source = eflaw_source(root, basic, mst=provider_id, effective_date=None)
    status, actual_mst, actual_date = law_version_status(raw, source, binding, provider_id, expected_date)
    result: dict[str, Any] = {
        "status": status, "api_family": "law", "support_eligible": status == "OK",
        "version_binding": binding,
        "source": {**source, "mst": actual_mst, "effective_date": actual_date,
                   "expected_effective_date": expected_date},
        "meta": {"response_sha256": hashlib.sha256(raw).hexdigest(), "request": params,
                 "retrieved_at": datetime.now(timezone.utc).isoformat(), "clock_source": "local",
                 "semantic_processing_added": False},
    }
    if status != "OK":
        return result

    addenda = []
    for unit in root.findall("./부칙/부칙단위"):
        text = _all_text(unit.find("./부칙내용"))
        entry: dict[str, Any] = {
            "key": str(unit.attrib.get("부칙키") or ""),
            "promulgation_date": (unit.findtext("부칙공포일자") or "").strip(),
            "promulgation_number": (unit.findtext("부칙공포번호") or "").strip(),
            "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        try:
            entry.update(split_addendum(text))
        except StructureDerivationError as exc:
            # The 부칙 text itself is still delivered; only the convenience structure is withheld.
            entry.update({"articles": None, "structure_derivation": "FAILED", "detail": str(exc)})
        addenda.append(entry)

    annexes = []
    for unit in root.iter("별표단위"):
        text = _all_text(unit.find("./별표내용"))
        annexes.append({
            "key": str(unit.attrib.get("별표키") or ""),
            "number": (unit.findtext("별표번호") or "").strip(),
            "branch": (unit.findtext("별표가지번호") or "").strip(),
            "kind": (unit.findtext("별표구분") or "").strip(),
            "title": (unit.findtext("별표제목") or "").strip(),
            "effective_date": (unit.findtext("별표시행일자") or "").strip(),
            "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "files": {tag: [(e.text or "").strip() for e in unit.findall(tag)]
                      for tag in _ANNEX_FILE_TAGS if unit.find(tag) is not None},
        })
    result.update({
        "addenda": addenda,
        "annexes": annexes,
        "structure_derivation": {
            "method": DERIVATION, "reconstruction": "BYTE_EQUAL",
            "note": "부칙은 줄 머리의 '제N조(' 표시로 조를 나누고, 조 안은 ①·1.·가. 표시로 나눈 구조입니다. "
                    "타법개정 부칙 안에 인용된 개정문도 같은 표시로 나뉠 수 있습니다. 법적 의미 판단이 아닙니다. "
                    "별표는 제공처 글 그대로이며 표를 칸으로 나누지 않습니다.",
        },
    })
    return result
