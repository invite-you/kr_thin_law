from __future__ import annotations

from typing import Any
from datetime import datetime, timezone
import hashlib
import xml.etree.ElementTree as ET

SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"


def encode_jo(article: int | str, branch: int | str | None = None) -> str:
    """Encode 제N조/제N조의M into the official six-digit JO format.

    This is a mechanical locator conversion, not legal interpretation.
    """
    a = int(str(article).strip())
    b = int(str(branch).strip()) if branch not in (None, "", 0, "0") else 0
    if a < 0 or a > 9999 or b < 0 or b > 99:
        raise ValueError("article must be 0..9999 and branch 0..99")
    return f"{a:04d}{b:02d}"


def _norm_branch(value: Any) -> str:
    s = str(value or "").strip()
    if s in ("", "0", "00", "000000"):
        return ""
    return str(int(s)) if s.isdigit() else s


class ProviderResponseError(ValueError):
    """Structured upstream/parsing failure with machine-readable transport metadata."""

    def __init__(
        self,
        error_code: str,
        detail: str,
        *,
        retryable: bool,
        raw_preview: str = "",
        candidates: list[dict[str, Any]] | None = None,
        http_status: int | None = None,
        provider_response: str = "",
    ) -> None:
        message = f"[{error_code}] {detail}"
        if provider_response:
            message += "\nUPSTREAM_RESPONSE:\n" + provider_response
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.raw_preview = raw_preview
        self.detail = detail
        self.candidates = candidates or []
        self.http_status = http_status
        self.provider_response = provider_response


def _preview(raw: bytes, limit: int = 500) -> str:
    return raw[:limit].decode("utf-8", errors="replace")


def _is_provider_error_xml(raw: bytes) -> bool:
    """Detect explicit provider failure envelopes without interpreting their meaning.

    The original provider payload remains the canonical error detail. This only
    distinguishes failure from a legitimate success payload, including a normal
    zero-result search.
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return False

    def first(tag: str) -> str:
        node = root.find(f".//{tag}")
        return (node.text or "").strip() if node is not None and node.text else ""

    result_code = first("resultCode")
    if result_code and result_code not in {"0", "00"}:
        return True
    if first("resultMsg").lower() in {"fail", "failed", "failure", "error"}:
        return True
    if root.tag in {"Response", "OpenAPI_ServiceResponse", "Error", "Errors", "에러"}:
        return True
    return root.find(".//cmmMsgHeader") is not None


def _require_xml_payload(raw: bytes) -> None:
    """Reject transport/provider failure payloads before domain parsing."""
    if not raw:
        raise ProviderResponseError(
            "EMPTY_RESPONSE",
            "upstream returned an empty body",
            retryable=True,
        )
    head = raw.lstrip()[:200].lower()
    if head.startswith(b"<html") or head.startswith(b"<!doctype html"):
        raise ProviderResponseError(
            "HTML_RESPONSE",
            "upstream returned an HTML page instead of an XML payload",
            retryable=True,
            raw_preview=_preview(raw),
            provider_response=raw.decode("utf-8", errors="replace"),
        )
    if _is_provider_error_xml(raw):
        raise ProviderResponseError(
            "PROVIDER_DECLARED_ERROR",
            "official provider returned an error response",
            retryable=False,
            raw_preview=_preview(raw),
            provider_response=raw.decode("utf-8", errors="replace"),
        )


def _select_article(
    content: dict[str, Any],
    article: int | str,
    branch: int | str | None = None,
) -> dict[str, Any]:
    """Select one already-parsed article without rewriting its text."""
    want_num = str(int(str(article).strip()))
    want_branch = _norm_branch(branch)
    hits = []
    for item in content.get("articles") or []:
        num = str(item.get("번호") or "").strip()
        br = _norm_branch(item.get("가지번호"))
        if num == want_num and br == want_branch and item.get("구분") != "전문":
            hits.append(item)
    if not hits:
        raise ProviderResponseError(
            "ARTICLE_NOT_FOUND",
            f"expected exactly one article 제{want_num}조"
            f"{('의'+want_branch) if want_branch else ''}, got 0",
            retryable=False,
        )
    if len(hits) > 1:
        candidates = [
            {
                "key": str(item.get("조문키") or ""),
                "number": str(item.get("번호") or ""),
                "branch": _norm_branch(item.get("가지번호")),
                "effective_date": str(item.get("시행일") or ""),
                "head_text": str(item.get("제목") or "")[:80],
            }
            for item in hits
        ]
        raise ProviderResponseError(
            "AMBIGUOUS_ARTICLE",
            f"expected exactly one article 제{want_num}조"
            f"{('의'+want_branch) if want_branch else ''}, got {len(hits)}",
            retryable=False,
            candidates=candidates,
        )
    return hits[0]


def project_article(
    content: dict[str, Any],
    meta: dict[str, Any],
    *,
    mst: str,
    effective_date: str | None,
    article: int | str,
    branch: int | str | None = None,
) -> dict[str, Any]:
    """Backward-compatible projection from an already parsed body.

    New code should prefer :func:`parse_eflaw_article_xml`, which can preserve
    the official article/paragraph/item/subitem tree and raw-response hash.
    This function remains for callers that already use the upstream generic
    body parser. It never invents semantic relations.
    """
    art = _select_article(content, article, branch)
    fields = content.get("fields") or {}
    dates = content.get("dates_normalized") or {}

    source = {
        "law": fields.get("법령명_한글") or fields.get("법령명한글") or "",
        "law_id": str(fields.get("법령ID") or ""),
        "mst": str(mst),
        "provider_document_type": str(fields.get("법종구분") or ""),
        "provider_document_type_code": str(fields.get("법종구분코드") or ""),
        "promulgation_date": str(fields.get("공포일자") or ""),
        "promulgation_number": str(fields.get("공포번호") or ""),
        "effective_date": str(
            fields.get("시행일자")
            or dates.get("시행일자")
            or ""
        ),
        "article_effective_date_text": str(
            fields.get("조문시행일자문자열") or ""
        ),
        "annex_effective_date_text": str(
            fields.get("별표시행일자문자열") or ""
        ),
    }
    article_out: dict[str, Any] = {
        "key": str(art.get("조문키") or ""),
        "number": str(art.get("번호") or ""),
        "branch": _norm_branch(art.get("가지번호")),
        "title": str(art.get("제목") or ""),
        "effective_date": str(art.get("시행일") or ""),
        "text": str(art.get("내용") or ""),
    }
    if "구조" in art:
        article_out["structure"] = art.get("구조") or []

    return {
        "source": source,
        "article": article_out,
        "meta": {
            "parser_article_count": meta.get("article_count"),
            "requested_effective_date": str(effective_date or ""),
        },
    }


_STRUCT_SPECS = {
    "항": ("paragraph", "항번호", "항내용", "호"),
    "호": ("item", "호번호", "호내용", "목"),
    "목": ("subitem", "목번호", "목내용", None),
}


def _parse_structure_node(el: ET.Element) -> list[dict[str, Any]]:
    """Convert one official 항/호/목 node to transport-only structure.

    The law.go.kr XML sometimes inserts a bare <항> wrapper around directly
    enumerated 호 even though there is no numbered paragraph (for example
    개인정보 보호법 제2조). A wrapper with neither label nor text is therefore
    transparent: its children are lifted instead of inventing a paragraph.
    """
    kind, num_tag, text_tag, child_tag = _STRUCT_SPECS[el.tag]
    label = _text(el, num_tag)
    text = _text(el, text_tag)
    children: list[dict[str, Any]] = []
    if child_tag:
        for child in el.findall(f"./{child_tag}"):
            children.extend(_parse_structure_node(child))

    if not label and not text:
        return children
    return [{
        "type": kind,
        "provider_tag": el.tag,
        "label": label,
        "text": text,
        "children": children,
    }]


def _article_structure(article_el: ET.Element) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for hang in article_el.findall("./항"):
        out.extend(_parse_structure_node(hang))
    return out


def _render_structure(nodes: list[dict[str, Any]], depth: int = 0) -> list[str]:
    lines: list[str] = []
    for node in nodes:
        text = str(node.get("text") or "").strip()
        if text:
            lines.append("  " * (depth + 1) + text)
        lines.extend(_render_structure(node.get("children") or [], depth + 1))
    return lines


def parse_eflaw_article_xml(
    xml_bytes: bytes | str,
    *,
    mst: str,
    effective_date: str | None,
    article: int | str,
    branch: int | str | None = None,
) -> dict[str, Any]:
    """Parse one official ``target=eflaw`` response without legal analysis.

    Only provider-owned identity, version/time metadata and the explicit
    조→항→호→목 structure are normalized. Fact/Dependency/TemporalRule
    interpretation remains a downstream responsibility.
    """
    raw = xml_bytes.encode("utf-8") if isinstance(xml_bytes, str) else bytes(xml_bytes)
    _require_xml_payload(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ProviderResponseError(
            "PARSE_ERROR",
            f"eflaw response is not well-formed XML: {exc}",
            retryable=True,
            raw_preview=_preview(raw),
        ) from exc
    if root.tag != "법령":
        raise ProviderResponseError(
            "UNEXPECTED_ROOT",
            f"eflaw response root must be 법령, got {root.tag}",
            retryable=False,
            raw_preview=_preview(raw),
        )

    basic = root.find("./기본정보")
    if basic is None:
        raise ProviderResponseError(
            "MISSING_STRUCTURE",
            "eflaw response has no 기본정보",
            retryable=False,
            raw_preview=_preview(raw),
        )

    want_num = str(int(str(article).strip()))
    want_branch = _norm_branch(branch)
    hits: list[ET.Element] = []
    for unit in root.findall("./조문/조문단위"):
        num = _text(unit, "조문번호")
        br = _norm_branch(_text(unit, "조문가지번호"))
        if num == want_num and br == want_branch and _text(unit, "조문여부") != "전문":
            hits.append(unit)
    if not hits:
        raise ProviderResponseError(
            "ARTICLE_NOT_FOUND",
            f"expected exactly one official article 제{want_num}조"
            f"{('의'+want_branch) if want_branch else ''}, got 0",
            retryable=False,
            raw_preview=_preview(raw),
        )
    if len(hits) > 1:
        candidates = [
            {
                "key": str(unit.attrib.get("조문키") or ""),
                "number": _text(unit, "조문번호"),
                "branch": _norm_branch(_text(unit, "조문가지번호")),
                "effective_date": _text(unit, "조문시행일자"),
                "head_text": _text(unit, "조문내용")[:80],
            }
            for unit in hits
        ]
        raise ProviderResponseError(
            "AMBIGUOUS_ARTICLE",
            f"expected exactly one official article 제{want_num}조"
            f"{('의'+want_branch) if want_branch else ''}, got {len(hits)}",
            retryable=False,
            raw_preview=_preview(raw),
            candidates=candidates,
        )
    unit = hits[0]

    structure = _article_structure(unit)
    head_text = _text(unit, "조문내용")
    text_parts = [head_text] if head_text else []
    text_parts.extend(_render_structure(structure))
    article_text = "\n".join(text_parts).strip()

    return {
        "source": eflaw_source(root, basic, mst=mst, effective_date=effective_date),
        "article": {
            "key": str(unit.attrib.get("조문키") or ""),
            "number": _text(unit, "조문번호"),
            "branch": _norm_branch(_text(unit, "조문가지번호")),
            "title": _text(unit, "조문제목"),
            "effective_date": _text(unit, "조문시행일자"),
            "amendment_type": _text(unit, "조문제개정유형"),
            "move_before": _text(unit, "조문이동이전"),
            "move_after": _text(unit, "조문이동이후"),
            "changed": _text(unit, "조문변경여부"),
            "head_text": head_text,
            "text": article_text,
            "reference_note": _text(unit, "조문참고자료"),
            "structure": structure,
        },
        "meta": {
            "requested_effective_date": str(effective_date or ""),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            # P1-D content hash: article text only. Raw-meta-only changes move
            # response_sha256 but not text_sha256; article text changes always
            # move both (the text lives inside the raw payload).
            "text_sha256": hashlib.sha256(article_text.encode("utf-8")).hexdigest(),
        },
    }


def eflaw_source(root: ET.Element, basic: ET.Element, *, mst: str, effective_date: str | None) -> dict[str, Any]:
    """Provider identity and version metadata of one ``target=eflaw`` response (기본정보)."""
    law_type = basic.find("./법종구분")
    ministry = basic.find("./소관부처")
    return {
        "official_source": "law.go.kr",
        "law": _text(basic, "법령명_한글") or _text(basic, "법령명한글"),
        "law_id": _text(basic, "법령ID"),
        "law_key": str(root.attrib.get("법령키") or ""),
        "mst": str(mst),
        "provider_document_type": _node_text(law_type),
        "provider_document_type_code": str(
            (law_type.attrib.get("법종구분코드") if law_type is not None else "") or ""
        ),
        "ministry": _node_text(ministry),
        "ministry_code": str(
            (ministry.attrib.get("소관부처코드") if ministry is not None else "") or ""
        ),
        "promulgation_date": _text(basic, "공포일자"),
        "promulgation_number": _text(basic, "공포번호"),
        "effective_date": _text(basic, "시행일자"),
        "revision_type": _text(basic, "제개정구분"),
        "article_effective_date_text": _text(basic, "조문시행일자문자열"),
        "annex_effective_date_text": _text(basic, "별표시행일자문자열"),
    }


def fetch_article(
    client: Any,
    *,
    mst: str,
    effective_date: str,
    article: int | str,
    branch: int | str | None = None,
) -> dict[str, Any]:
    """Fetch one article directly from official ``eflaw`` XML.

    This intentionally bypasses the upstream generic body renderer because that
    renderer flattens 항/호/목 into display text. The dedicated adapter keeps the
    provider hierarchy so the next stage does not have to parse legal text again.
    """
    jo = encode_jo(article, branch)
    params = {
        "target": "eflaw",
        "MST": str(mst),
        "efYd": str(effective_date).replace("-", ""),
        "JO": jo,
    }
    body = client._call(SERVICE_URL, params)
    out = parse_eflaw_article_xml(
        body,
        mst=str(mst),
        effective_date=str(effective_date),
        article=article,
        branch=branch,
    )
    out["meta"]["request"] = params
    out["meta"]["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    out["meta"]["clock_source"] = "local"
    return out


def _text(el: ET.Element | None, tag: str) -> str:
    if el is None:
        return ""
    node = el.find(tag)
    return (node.text or "").strip() if node is not None and node.text else ""


def _node_text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None and el.text else ""


def _direct_leaf_map(el: ET.Element) -> dict[str, str]:
    """Read only direct leaf children and preserve explicit empty values.

    Recursive flattening is intentionally avoided because lsDelegated may contain
    repeated target headers/records inside one <위임정보>.
    """
    out: dict[str, str] = {}
    for node in list(el):
        if len(node) == 0 and node.tag:
            out[node.tag] = _node_text(node)
    return out


_FAMILY_SPECS: dict[str, dict[str, str]] = {
    "law": {
        "record_tag": "위임법령조문정보",
        "seq": "위임법령일련번호",
        "title": "위임법령제목",
        "article": "위임법령조문번호",
        "branch": "위임법령조문가지번호",
        "article_title": "위임법령조문제목",
    },
    "admin_rule": {
        "record_tag": "위임행정규칙조문정보",
        "seq": "위임행정규칙일련번호",
        "title": "위임행정규칙제목",
    },
    "ordinance": {
        "record_tag": "위임자치법규조문정보",
        "seq": "위임자치법규일련번호",
        "title": "위임자치법규제목",
    },
    "institution_rule": {
        "record_tag": "위임규정조문정보",
        "seq": "위임규정일련번호",
        "title": "위임규정제목",
    },
    "treaty": {
        # Official guide exposes treaty seq/title fields. No treaty live fixture
        # was available in this handoff, so direct inline fields remain supported
        # even if a dedicated child tag is not observed.
        "record_tag": "조약조문정보",
        "seq": "조약일련번호",
        "title": "조약제목",
    },
}

_RECORD_TAG_TO_FAMILY = {
    spec["record_tag"]: family for family, spec in _FAMILY_SPECS.items()
}
_TARGET_FIELD_TO_FAMILY: dict[str, str] = {}
for _family, _spec in _FAMILY_SPECS.items():
    _TARGET_FIELD_TO_FAMILY[_spec["seq"]] = _family
    _TARGET_FIELD_TO_FAMILY[_spec["title"]] = _family

_INLINE_RECORD_FIELDS = {
    "링크텍스트",
    "라인텍스트",
    "조항호목",
    "위임법령조문번호",
    "위임법령조문가지번호",
    "위임법령조문제목",
}


def parse_lsdelegated_xml(xml_bytes: bytes | str) -> dict[str, Any]:
    """Parse target=lsDelegated as lossless reference observations.

    The parser does not decide legal meaning or historical target version.
    It only preserves official source identity, ordered target groups and each
    provider relation record.
    """
    raw = xml_bytes.encode("utf-8") if isinstance(xml_bytes, str) else bytes(xml_bytes)
    _require_xml_payload(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ProviderResponseError(
            "PARSE_ERROR",
            f"lsDelegated response is not well-formed XML: {exc}",
            retryable=True,
            raw_preview=_preview(raw),
        ) from exc
    law_el = root.find(".//법령") if root.tag != "법령" else root
    if law_el is None:
        raise ProviderResponseError(
            "UNEXPECTED_ROOT",
            "lsDelegated response has no 법령 node",
            retryable=False,
            raw_preview=_preview(raw),
        )

    info = law_el.find("./법령정보")
    source = {
        "seq": _text(info, "법령일련번호"),
        "law_id": _text(info, "법령ID"),
        "title": _text(info, "법령명"),
        "promulgation_date": _text(info, "공포일자"),
        "promulgation_number": _text(info, "공포번호"),
        "effective_date": _text(info, "시행일자"),
    }

    known = {
        "법령일련번호", "법령ID", "법령명", "시행일자", "공포일자", "공포번호",
        "소관부처", "소관부처코드", "전화번호",
        "조문번호", "조문제목", "조문가지번호", "위임구분",
        "위임법령일련번호", "위임법령제목", "위임법령조문번호",
        "위임법령조문가지번호", "위임법령조문제목", "위임법령조문정보",
        "위임행정규칙일련번호", "위임행정규칙제목", "위임행정규칙조문정보",
        "위임자치법규일련번호", "위임자치법규제목", "위임자치법규조문정보",
        "위임규정일련번호", "위임규정제목", "위임규정조문정보",
        "조약일련번호", "조약제목", "조약조문정보",
        "링크텍스트", "라인텍스트", "조항호목",
    }
    unknown: set[str] = set()
    raw_refs: list[dict[str, Any]] = []

    for source_group_index, group in enumerate(law_el.findall("./위임조문정보"), 1):
        src = group.find("./조정보")
        src_article = _text(src, "조문번호")
        src_branch = _text(src, "조문가지번호")
        src_title = _text(src, "조문제목")

        for delegate_index, delegate in enumerate(group.findall("./위임정보"), 1):
            state = {
                "kind": "",
                "family": "",
                "seq": "",
                "title": "",
            }
            inline: dict[str, str] = {}
            target_segment_index = 0
            record_index = 0
            fragment_group_index = 0
            last_fragment_key: tuple[str, str, str, str, str] | None = None
            target_segment_needs_start = True

            def emit(fields: dict[str, str], family: str | None = None) -> None:
                nonlocal target_segment_index, record_index, fragment_group_index
                nonlocal last_fragment_key, target_segment_needs_start
                # Mechanical family precedence: explicit record tag, then the
                # record's own family-specific target fields, then the segment
                # header family. Own fields beat the header so a child record
                # carrying its own target fields is never mislabeled by a
                # broader header field family (e.g. 위임법령*).
                fam = family
                family_basis = "record_tag" if fam else ""
                if not fam:
                    for key in fields:
                        if key in _TARGET_FIELD_TO_FAMILY:
                            fam = _TARGET_FIELD_TO_FAMILY[key]
                            family_basis = "own_fields"
                            break
                if not fam:
                    if state["family"]:
                        fam = state["family"]
                        family_basis = "segment_header"
                    else:
                        fam = "unknown"
                        family_basis = "unknown"
                spec = _FAMILY_SPECS.get(fam, {})

                seq_key = spec.get("seq", "")
                title_key = spec.get("title", "")
                observed_seq = fields.get(seq_key, "") if seq_key else ""
                observed_title = fields.get(title_key, "") if title_key else ""
                if fam == state["family"]:
                    observed_seq = observed_seq or state["seq"]
                    observed_title = observed_title or state["title"]

                if target_segment_needs_start or target_segment_index == 0:
                    target_segment_index += 1
                    target_segment_needs_start = False
                    last_fragment_key = None

                record_index += 1
                # One visual/provider reference may be split into several child
                # records (e.g. "법" + "제18조" + "제2항").  Group only by
                # exact provider-owned context/locator values; do not infer legal
                # meaning or resolve the target here.
                fragment_key = (
                    fam, observed_seq, observed_title,
                    fields.get("조항호목", ""), fields.get("라인텍스트", ""),
                )
                if fragment_key != last_fragment_key:
                    fragment_group_index += 1
                    last_fragment_key = fragment_key

                raw_refs.append({
                    "provider_source_group_index": source_group_index,
                    "provider_delegate_index": delegate_index,
                    "provider_target_segment_index": target_segment_index,
                    "provider_fragment_group_index": fragment_group_index,
                    "provider_record_index": record_index,
                    "kind": state["kind"],
                    "source_article": src_article,
                    "source_branch": src_branch,
                    "source_title": src_title,
                    "at": fields.get("조항호목", ""),
                    "text": fields.get("링크텍스트", ""),
                    "line": fields.get("라인텍스트", ""),
                    # Mechanical provider family, not a downstream document-type judgment.
                    "api_family": fam,
                    # True when api_family was NOT provider-declared by a record
                    # tag (field/header inference only) — C-3 residual exposure.
                    "family_ambiguous": family_basis != "record_tag",
                    # lsDelegated observations are not guaranteed to be version-bound
                    # to the source historical MST. The next stage resolves that.
                    "observed_target_seq": observed_seq,
                    "observed_target_title": observed_title,
                    "linked_article": (
                        fields.get(spec.get("article", ""), "")
                        if fam == "law" else ""
                    ),
                    "linked_branch": (
                        fields.get(spec.get("branch", ""), "")
                        if fam == "law" else ""
                    ),
                    "linked_article_title": (
                        fields.get(spec.get("article_title", ""), "")
                        if fam == "law" else ""
                    ),
                })

            def flush_inline() -> None:
                nonlocal inline
                if inline and any(k in inline for k in _INLINE_RECORD_FIELDS):
                    emit(inline)
                inline = {}

            for child in list(delegate):
                if child.tag not in known and len(child) == 0:
                    unknown.add(child.tag)

                if child.tag == "위임구분" and len(child) == 0:
                    flush_inline()
                    target_segment_needs_start = True
                    state = {
                        "kind": _node_text(child),
                        "family": "",
                        "seq": "",
                        "title": "",
                    }
                    continue

                if child.tag in _TARGET_FIELD_TO_FAMILY and len(child) == 0:
                    family = _TARGET_FIELD_TO_FAMILY[child.tag]
                    spec = _FAMILY_SPECS[family]
                    if state["family"] and family != state["family"]:
                        flush_inline()
                        target_segment_needs_start = True
                        state["family"] = family
                        state["seq"] = ""
                        state["title"] = ""
                    elif not state["family"]:
                        state["family"] = family

                    value = _node_text(child)
                    if child.tag == spec["seq"]:
                        # A repeated seq after record payload starts a new observed
                        # target group even if the provider did not create a new
                        # <위임정보> wrapper.
                        if state["seq"] and value != state["seq"]:
                            flush_inline()
                            target_segment_needs_start = True
                            state["seq"] = ""
                            state["title"] = ""
                        state["seq"] = value
                    else:
                        state["title"] = value
                    continue

                if child.tag in _RECORD_TAG_TO_FAMILY:
                    flush_inline()
                    family = _RECORD_TAG_TO_FAMILY[child.tag]
                    fields = _direct_leaf_map(child)
                    unknown.update(k for k in fields if k not in known)
                    emit(fields, family)
                    continue

                if len(child) == 0:
                    # Supports direct-inline families seen in older/synthetic
                    # fixtures without recursively mixing sibling records.
                    inline[child.tag] = _node_text(child)
                else:
                    for leaf in child.iter():
                        if len(leaf) == 0 and leaf.tag not in known:
                            unknown.add(leaf.tag)

            flush_inline()

    return {
        "source": source,
        "references": raw_refs,
        "meta": {
            "reference_count": len(raw_refs),
            "unknown_leaf_fields": sorted(unknown),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
        },
    }


def project_references(
    parsed: dict[str, Any],
    *,
    article: int | str | None = None,
    branch: int | str | None = None,
) -> dict[str, Any]:
    """Project official reference observations without semantic interpretation."""
    refs = parsed.get("references") or []
    if article is not None:
        a = str(int(str(article).strip()))
        refs = [
            r for r in refs
            if str(r.get("source_article") or "").strip() == a
        ]
    if branch is not None:
        b = _norm_branch(branch)
        refs = [
            r for r in refs
            if _norm_branch(r.get("source_branch")) == b
        ]

    compact = []
    for ref in refs:
        family = ref.get("api_family") or "unknown"
        target = {
            "api_family": family,
            "family_ambiguous": bool(ref.get("family_ambiguous")),
            "observed_seq": ref.get("observed_target_seq") or "",
            "observed_title": ref.get("observed_target_title") or "",
        }
        if family == "law":
            # Preserve explicit blank values; absence and empty official values
            # must not be filled from siblings.
            target["linked_article"] = ref.get("linked_article") or ""
            target["linked_branch"] = ref.get("linked_branch") or ""
            target["linked_article_title"] = ref.get("linked_article_title") or ""

        # Compact provider-only grouping.  This lets the next stage reconstruct
        # a provider reference that was split into multiple link fragments,
        # without exposing XML implementation details as legal semantics.
        provider_group = ":".join(str(x or 0) for x in (
            ref.get("provider_source_group_index"),
            ref.get("provider_delegate_index"),
            ref.get("provider_fragment_group_index"),
        ))
        item = {
            "provider_group": provider_group,
            "provider_order": ref.get("provider_record_index"),
            "source_article": ref.get("source_article") or "",
            "source_branch": _norm_branch(ref.get("source_branch")),
            "source_title": ref.get("source_title") or "",
            "kind": ref.get("kind") or "",
            "at": ref.get("at") or "",
            "text": ref.get("text") or "",
            "target": target,
        }
        # 라인텍스트 is an official provider field and is often the only useful
        # local context for a generic link text such as "대통령령". Keeping it
        # is transport, not semantic interpretation.
        if "line" in ref:
            item["context"] = ref.get("line") or ""
        compact.append(item)

    out: dict[str, Any] = {
        "source": {
            "official_source": "law.go.kr",
            "seq": parsed["source"].get("seq") or "",
            "law_id": parsed["source"].get("law_id") or "",
            "title": parsed["source"].get("title") or "",
            "promulgation_date": parsed["source"].get("promulgation_date") or "",
            "promulgation_number": parsed["source"].get("promulgation_number") or "",
            "effective_date": parsed["source"].get("effective_date") or "",
        },
        "references": compact,
        "meta": {
            "reference_count": len(compact),
            "response_sha256": parsed.get("meta", {}).get("response_sha256", ""),
        },
    }
    if parsed.get("meta", {}).get("unknown_leaf_fields"):
        out["schema_warning"] = "upstream lsDelegated response contains unmapped fields"
        out["meta"]["unknown_leaf_fields"] = parsed["meta"]["unknown_leaf_fields"]
    return out


def fetch_references(
    client: Any,
    *,
    mst: str,
    article: int | str | None = None,
    branch: int | str | None = None,
) -> dict[str, Any]:
    """Fetch official reference/delegation observations directly from lsDelegated."""
    params = {"target": "lsDelegated", "MST": str(mst)}
    body = client._call(SERVICE_URL, params)
    parsed = parse_lsdelegated_xml(body)
    out = project_references(parsed, article=article, branch=branch)
    out["meta"]["request"] = params
    out["meta"]["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    out["meta"]["clock_source"] = "local"
    return out
