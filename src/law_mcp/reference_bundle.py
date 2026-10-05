"""Whole-document explicit reference bundle over official Korean law sources.

This module is deliberately structural. It combines provider reference
observations with mechanically resolvable article locators found in the exact
law text. It does not promote legal Dependencies, resolve semantic relevance,
or claim nationwide incoming-reference completeness.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import re
import xml.etree.ElementTree as ET
from typing import Any, Iterable

from .card_source import (
    ProviderResponseError,
    SERVICE_URL,
    _article_structure,
    _norm_branch,
    _preview,
    _render_structure,
    _require_xml_payload,
    _text,
    eflaw_source,
    parse_lsdelegated_xml,
    project_references,
)


_POLICY_VERSION = "same_document_explicit_article_v1"
_TEXT_MODES = {"graph_only", "referenced_units", "full_document"}
_DOC_KIND = r"(?:법|법률|영|규칙|조례|대통령령|총리령|부령)"
_QUOTED_DOC_SUFFIX = re.compile(r"「(?P<title>[^」]{1,120})」\s*(?:의\s*)?$")
_THIS_DOC_SUFFIX = re.compile(rf"(?:^|\s)이\s*{_DOC_KIND}\s*$")
_RELATIVE_DOC_SUFFIX = re.compile(rf"(?:^|\s)(?:같은|동)\s*{_DOC_KIND}\s*$")
_OTHER_DOC_SUFFIX = re.compile(rf"(?:^|\s){_DOC_KIND}\s*$")
_DOC_ANCHOR_RE = re.compile(
    rf"(?<![가-힣])(?:(?P<prefix>이|같은|동)\s*)?"
    rf"(?P<kind>{_DOC_KIND})\s*제[1-9]\d{{0,3}}조"
)
_QUOTED_DOC_ANCHOR_RE = re.compile(
    r"「(?P<title>[^」]{1,120})」\s*제[1-9]\d{0,3}조"
)
_RANGE_RE = re.compile(
    r"제(?P<a1>[1-9]\d{0,3})조(?:의(?P<b1>[1-9]\d{0,2}))?"
    r"\s*부터\s*"
    r"제(?P<a2>[1-9]\d{0,3})조(?:의(?P<b2>[1-9]\d{0,2}))?"
    r"(?:까지)?"
)
_LOCATOR_RE = re.compile(
    r"제(?P<article>[1-9]\d{0,3})조(?:의(?P<branch>[1-9]\d{0,2}))?"
    r"(?:제(?P<paragraph>[1-9]\d{0,2})항)?"
    r"(?:제(?P<item>[1-9]\d{0,3})호)?"
    r"(?:(?P<subitem>[가-힣])목)?"
)


def _canonical_date(value: Any, field: str) -> str:
    raw = str(value or "").strip().replace("-", "")
    if not re.fullmatch(r"\d{8}", raw):
        raise ValueError(f"{field} must be YYYYMMDD or YYYY-MM-DD")
    try:
        datetime.strptime(raw, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid calendar date") from exc
    return raw


def _selector(selector: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(selector, dict):
        raise ValueError("selector must be an object")
    mode = str(selector.get("mode") or "")
    if mode == "current":
        allowed = {"mode", "law_id"}
        if set(selector) - allowed:
            raise ValueError("current selector accepts only mode and law_id")
        law_id = str(selector.get("law_id") or "").strip()
        if not law_id.isdigit():
            raise ValueError("current selector law_id must be digits")
        return {"mode": mode, "law_id": law_id}

    if mode == "version":
        allowed = {
            "mode", "mst", "effective_date", "expected_law_key",
            "expected_response_sha256", "expected_law_id",
        }
        if set(selector) - allowed:
            raise ValueError(
                "version selector accepts mst, effective_date and independent version proof fields only"
            )
        mst = str(selector.get("mst") or "").strip()
        if not mst.isdigit():
            raise ValueError("version selector mst must be digits")
        effective_date = _canonical_date(selector.get("effective_date"), "selector.effective_date")
        law_key = str(selector.get("expected_law_key") or "").strip()
        response_hash = str(selector.get("expected_response_sha256") or "").strip().lower()
        if not law_key and not response_hash:
            raise ValueError(
                "version selector requires expected_law_key or expected_response_sha256"
            )
        if response_hash and not re.fullmatch(r"[0-9a-f]{64}", response_hash):
            raise ValueError("expected_response_sha256 must be a 64-character hex digest")
        expected_law_id = str(selector.get("expected_law_id") or "").strip()
        if expected_law_id and not expected_law_id.isdigit():
            raise ValueError("expected_law_id must be digits")
        out = {
            "mode": mode,
            "mst": mst,
            "effective_date": effective_date,
            "expected_law_key": law_key,
            "expected_response_sha256": response_hash,
        }
        if expected_law_id:
            out["expected_law_id"] = expected_law_id
        return out

    raise ValueError("selector.mode must be current or version")


def _same_numeric_id(left: Any, right: Any) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    return bool(a and b and a.isdigit() and b.isdigit() and int(a) == int(b))


def _article_key(article: Any, branch: Any = None) -> str:
    number = str(int(str(article).strip()))
    br = _norm_branch(branch)
    return f"article:{number}" + (f"-{br}" if br else "")


def _parse_document(
    xml_bytes: bytes | str,
    *,
    requested_mst: str = "",
    effective_date: str | None = None,
) -> dict[str, Any]:
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

    articles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for unit in root.findall("./조문/조문단위"):
        if _text(unit, "조문여부") == "전문":
            continue
        number = _text(unit, "조문번호")
        if not number:
            continue
        branch = _norm_branch(_text(unit, "조문가지번호"))
        key = _article_key(number, branch)
        if key in seen:
            raise ProviderResponseError(
                "AMBIGUOUS_ARTICLE",
                f"duplicate article locator in full body: {key}",
                retryable=False,
            )
        seen.add(key)
        structure = _article_structure(unit)
        head_text = _text(unit, "조문내용")
        text_parts = [head_text] if head_text else []
        text_parts.extend(_render_structure(structure))
        article_text = "\n".join(text_parts).strip()
        articles.append({
            "key": str(unit.attrib.get("조문키") or ""),
            "node_key": key,
            "number": str(int(number)),
            "branch": branch,
            "title": _text(unit, "조문제목"),
            "effective_date": _text(unit, "조문시행일자"),
            "amendment_type": _text(unit, "조문제개정유형"),
            "head_text": head_text,
            "text": article_text,
            "reference_note": _text(unit, "조문참고자료"),
            "structure": structure,
        })

    return {
        "source": eflaw_source(
            root, basic, mst=requested_mst, effective_date=effective_date
        ),
        "articles": articles,
        "meta": {
            "article_count": len(articles),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
        },
    }


def _verify_document(
    document: dict[str, Any],
    selector: dict[str, Any],
) -> None:
    source = document["source"]
    raw_hash = str(document["meta"]["response_sha256"])
    actual_law_id = str(source.get("law_id") or "")
    actual_date = str(source.get("effective_date") or "").replace("-", "")
    actual_key = str(source.get("law_key") or "")

    if selector["mode"] == "current":
        if not _same_numeric_id(actual_law_id, selector["law_id"]):
            raise ProviderResponseError(
                "PROVIDER_IDENTITY_MISMATCH",
                "eflaw current response law_id does not match selector",
                retryable=False,
            )
        return

    if selector.get("expected_law_id") and not _same_numeric_id(
        actual_law_id, selector["expected_law_id"]
    ):
        raise ProviderResponseError(
            "PROVIDER_IDENTITY_MISMATCH",
            "eflaw response law_id does not match expected_law_id",
            retryable=False,
        )
    if actual_date != selector["effective_date"]:
        raise ProviderResponseError(
            "EFFECTIVE_DATE_MISMATCH",
            "eflaw response effective date does not match selector",
            retryable=False,
        )
    expected_key = selector.get("expected_law_key") or ""
    if expected_key and actual_key != expected_key:
        raise ProviderResponseError(
            "PROVIDER_VERSION_MISMATCH",
            "eflaw response law_key does not match expected_law_key",
            retryable=False,
        )
    expected_hash = selector.get("expected_response_sha256") or ""
    if expected_hash and raw_hash != expected_hash:
        raise ProviderResponseError(
            "PROVIDER_VERSION_MISMATCH",
            "eflaw response hash does not match expected_response_sha256",
            retryable=False,
        )


def _iter_segments(article: dict[str, Any]) -> Iterable[tuple[str, str]]:
    head = str(article.get("head_text") or "")
    if head:
        yield "head", head

    def walk(nodes: list[dict[str, Any]], parent: str) -> Iterable[tuple[str, str]]:
        for index, node in enumerate(nodes, 1):
            kind = str(node.get("type") or "unit")
            label = str(node.get("label") or index)
            path = f"{parent}/{kind}:{label}"
            text = str(node.get("text") or "")
            if text:
                yield path, text
            yield from walk(list(node.get("children") or []), path)

    yield from walk(list(article.get("structure") or []), "structure")


def _scope_before(text: str, start: int, source_title: str) -> tuple[str, str]:
    lookback = text[max(0, start - 220):start]
    quoted = _QUOTED_DOC_SUFFIX.search(lookback)
    if quoted:
        title = quoted.group("title").strip()
        if re.sub(r"\s+", "", title) == re.sub(r"\s+", "", source_title):
            return "same_document", "SELF_NAMED_DOCUMENT"
        return "not_same_document", "EXTERNAL_NAMED_DOCUMENT"

    tail = lookback[-60:]
    if _THIS_DOC_SUFFIX.search(tail):
        return "same_document", "THIS_DOCUMENT_PREFIX"
    if _RELATIVE_DOC_SUFFIX.search(tail):
        return "unresolved_document", "RELATIVE_DOCUMENT_PREFIX"
    if _OTHER_DOC_SUFFIX.search(tail):
        return "not_same_document", "EXTERNAL_DOCUMENT_PREFIX"

    # One document marker often governs several coordinated locators:
    # 「형법」 제355조 또는 제356조 / 법 제29조 및 제30조.
    # Keep that scope through the current punctuation-bounded clause.
    clause_start = max(
        text.rfind("\n", 0, start),
        text.rfind(".", 0, start),
        text.rfind("。", 0, start),
        text.rfind(";", 0, start),
        text.rfind("!", 0, start),
        text.rfind("?", 0, start),
    ) + 1
    clause = text[clause_start:start]
    anchors: list[tuple[int, str, str]] = []
    for match in _QUOTED_DOC_ANCHOR_RE.finditer(clause):
        title = match.group("title").strip()
        if re.sub(r"\s+", "", title) == re.sub(r"\s+", "", source_title):
            anchors.append((
                match.start(), "same_document", "SELF_NAMED_DOCUMENT_INHERITED"
            ))
        else:
            anchors.append((
                match.start(), "not_same_document", "EXTERNAL_NAMED_DOCUMENT_INHERITED"
            ))
    for match in _DOC_ANCHOR_RE.finditer(clause):
        prefix = str(match.group("prefix") or "")
        if prefix == "이":
            anchors.append((
                match.start(), "same_document", "THIS_DOCUMENT_PREFIX_INHERITED"
            ))
        elif prefix in {"같은", "동"}:
            anchors.append((
                match.start(), "unresolved_document", "RELATIVE_DOCUMENT_PREFIX_INHERITED"
            ))
        else:
            anchors.append((
                match.start(), "not_same_document", "EXTERNAL_DOCUMENT_PREFIX_INHERITED"
            ))
    if anchors:
        _, scope, basis = max(anchors, key=lambda row: row[0])
        return scope, basis

    return "same_document", "BARE_ARTICLE_LOCATOR"


def _local_context(text: str, start: int, end: int, radius: int = 80) -> str:
    return text[max(0, start - radius):min(len(text), end + radius)]


def _expand_range(match: re.Match[str]) -> list[tuple[str, str]]:
    a1, a2 = int(match.group("a1")), int(match.group("a2"))
    b1 = int(match.group("b1")) if match.group("b1") else 0
    b2 = int(match.group("b2")) if match.group("b2") else 0
    if b1 and b2 and a1 == a2 and b1 <= b2 and b2 - b1 <= 100:
        return [(str(a1), str(branch)) for branch in range(b1, b2 + 1)]
    if not b1 and not b2 and a1 <= a2 and a2 - a1 <= 200:
        return [(str(article), "") for article in range(a1, a2 + 1)]
    return []


def extract_same_document_references(
    articles: list[dict[str, Any]],
    *,
    source_title: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract only mechanically resolvable same-document article locators.

    Named external documents and document-relative prefixes such as "법 제N조"
    or "같은 법 제N조" are preserved as unresolved observations instead of
    being misclassified as same-document reverse edges.
    """
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    next_id = 1
    next_unresolved = 1

    for article in articles:
        source = {
            "article": str(article["number"]),
            "branch": str(article.get("branch") or ""),
        }
        for segment_path, text in _iter_segments(article):
            occupied: list[tuple[int, int]] = []
            for match in _RANGE_RE.finditer(text):
                occupied.append(match.span())
                scope, basis = _scope_before(text, match.start(), source_title)
                expanded = _expand_range(match)
                if scope == "same_document" and expanded:
                    for target_article, target_branch in expanded:
                        resolved.append({
                            "observation_id": f"body:{next_id}",
                            "source": {**source, "segment_path": segment_path},
                            "target": {
                                "article": target_article,
                                "branch": target_branch,
                                "paragraph": "",
                                "item": "",
                                "subitem": "",
                            },
                            "raw_text": match.group(0),
                            "span": {"start": match.start(), "end": match.end()},
                            "context": _local_context(text, match.start(), match.end()),
                            "scope_basis": basis,
                            "range_expanded": True,
                        })
                        next_id += 1
                else:
                    unresolved.append({
                        "observation_id": f"body-unresolved:{next_unresolved}",
                        "source": {**source, "segment_path": segment_path},
                        "raw_text": match.group(0),
                        "span": {"start": match.start(), "end": match.end()},
                        "context": _local_context(text, match.start(), match.end()),
                        "reason": basis if scope != "same_document" else "UNSUPPORTED_RANGE_SHAPE",
                    })
                    next_unresolved += 1

            for match in _LOCATOR_RE.finditer(text):
                if any(start <= match.start() < end for start, end in occupied):
                    continue
                target_article = match.group("article")
                target_branch = match.group("branch") or ""
                if (
                    segment_path == "head"
                    and match.start() == 0
                    and target_article == source["article"]
                    and _norm_branch(target_branch) == _norm_branch(source["branch"])
                ):
                    continue
                scope, basis = _scope_before(text, match.start(), source_title)
                if scope != "same_document":
                    unresolved.append({
                        "observation_id": f"body-unresolved:{next_unresolved}",
                        "source": {**source, "segment_path": segment_path},
                        "raw_text": match.group(0),
                        "span": {"start": match.start(), "end": match.end()},
                        "context": _local_context(text, match.start(), match.end()),
                        "reason": basis,
                    })
                    next_unresolved += 1
                    continue
                resolved.append({
                    "observation_id": f"body:{next_id}",
                    "source": {**source, "segment_path": segment_path},
                    "target": {
                        "article": target_article,
                        "branch": _norm_branch(target_branch),
                        "paragraph": match.group("paragraph") or "",
                        "item": match.group("item") or "",
                        "subitem": match.group("subitem") or "",
                    },
                    "raw_text": match.group(0),
                    "span": {"start": match.start(), "end": match.end()},
                    "context": _local_context(text, match.start(), match.end()),
                    "scope_basis": basis,
                    "range_expanded": False,
                })
                next_id += 1

    return resolved, unresolved


def _validate_body_targets(
    observations: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
    *,
    articles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    known = {str(article["node_key"]) for article in articles}
    valid: list[dict[str, Any]] = []
    rejected = list(unresolved)
    next_id = len(rejected) + 1
    for observation in observations:
        target = observation["target"]
        node = _article_key(target["article"], target.get("branch"))
        if node in known:
            valid.append(observation)
            continue
        rejected.append({
            "observation_id": f"body-unresolved:target:{next_id}",
            "source": dict(observation["source"]),
            "raw_text": observation["raw_text"],
            "span": dict(observation["span"]),
            "context": observation["context"],
            "reason": "TARGET_NOT_PRESENT_IN_DOCUMENT_VERSION",
            "candidate_target": dict(target),
        })
        next_id += 1
    return valid, rejected


def _split_csv(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _provider_article_targets(reference: dict[str, Any]) -> list[tuple[str, str]]:
    target = reference.get("target") or {}
    articles = _split_csv(target.get("linked_article"))
    branches = _split_csv(target.get("linked_branch"))
    out: list[tuple[str, str]] = []
    for index, article in enumerate(articles):
        if not article.isdigit():
            continue
        branch = branches[index] if index < len(branches) else ""
        out.append((str(int(article)), _norm_branch(branch)))
    return out


def _normalized_title(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _reference_edges(
    *,
    source_title: str,
    provider_references: list[dict[str, Any]],
    body_observations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    edge_map: dict[tuple[str, ...], dict[str, Any]] = {}
    provider_out: list[dict[str, Any]] = []

    def ensure(
        source_article: str,
        source_branch: str,
        target_title: str,
        target_seq: str,
        target_article: str,
        target_branch: str,
        same_document: bool,
    ) -> dict[str, Any]:
        doc_key = "@self" if same_document else (
            _normalized_title(target_title) + "#" + str(target_seq or "")
        )
        key = (
            source_article, source_branch, doc_key, target_article, target_branch
        )
        if key not in edge_map:
            edge_map[key] = {
                "source": {
                    "article": source_article,
                    "branch": source_branch,
                    "node_key": _article_key(source_article, source_branch),
                },
                "target": {
                    "api_family": "law",
                    "observed_title": source_title if same_document else target_title,
                    "observed_seq": "" if same_document else target_seq,
                    "article": target_article,
                    "branch": target_branch,
                    "node_key": _article_key(target_article, target_branch),
                },
                "same_document": same_document,
                "observed_by": set(),
                "observation_ids": [],
            }
        return edge_map[key]

    for index, reference in enumerate(provider_references, 1):
        item = {**reference, "observation_id": f"provider:{index}"}
        target = item.get("target") or {}
        same_document = (
            target.get("api_family") == "law"
            and bool(target.get("observed_title"))
            and _normalized_title(target.get("observed_title")) == _normalized_title(source_title)
        )
        item["same_document_by_title"] = same_document
        provider_out.append(item)
        source_article = str(item.get("source_article") or "").strip()
        if target.get("api_family") != "law" or not source_article.isdigit():
            continue
        for target_article, target_branch in _provider_article_targets(item):
            edge = ensure(
                str(int(source_article)),
                _norm_branch(item.get("source_branch")),
                str(target.get("observed_title") or ""),
                str(target.get("observed_seq") or ""),
                target_article,
                target_branch,
                same_document,
            )
            edge["observed_by"].add("lsDelegated")
            edge["observation_ids"].append(item["observation_id"])

    for observation in body_observations:
        source = observation["source"]
        target = observation["target"]
        edge = ensure(
            str(source["article"]),
            _norm_branch(source.get("branch")),
            source_title,
            "",
            str(target["article"]),
            _norm_branch(target.get("branch")),
            True,
        )
        edge["observed_by"].add("body_explicit")
        edge["observation_ids"].append(observation["observation_id"])

    edges: list[dict[str, Any]] = []
    for index, key in enumerate(sorted(edge_map), 1):
        edge = edge_map[key]
        edge["edge_id"] = f"ref:{index}"
        edge["observed_by"] = sorted(edge["observed_by"])
        edge["observation_ids"] = sorted(set(edge["observation_ids"]))
        edges.append(edge)
    return edges, provider_out


def _indexes(
    edges: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    outgoing: dict[str, list[str]] = defaultdict(list)
    reverse: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["source"]["node_key"]].append(edge["edge_id"])
        if edge["same_document"]:
            reverse[edge["target"]["node_key"]].append(edge["edge_id"])
    return dict(outgoing), dict(reverse)


def focused_reverse_paths(
    target_node: str,
    *,
    edges: list[dict[str, Any]],
    reverse_index: dict[str, list[str]],
    max_depth: int = 2,
) -> list[dict[str, Any]]:
    if not 1 <= int(max_depth) <= 3:
        raise ValueError("focus.max_depth must be 1..3")
    edge_by_id = {edge["edge_id"]: edge for edge in edges}
    results: list[dict[str, Any]] = []
    frontier: list[tuple[list[str], list[str], str]] = [([target_node], [], target_node)]
    for depth in range(1, int(max_depth) + 1):
        next_frontier: list[tuple[list[str], list[str], str]] = []
        for nodes, edge_ids, node in frontier:
            for edge_id in reverse_index.get(node, []):
                edge = edge_by_id[edge_id]
                source_node = edge["source"]["node_key"]
                if source_node in nodes:
                    continue
                new_nodes = [source_node, *nodes]
                new_edges = [edge_id, *edge_ids]
                results.append({
                    "depth": depth,
                    "nodes": new_nodes,
                    "edge_ids": new_edges,
                })
                next_frontier.append((new_nodes, new_edges, source_node))
        frontier = next_frontier
    return results


def _validate_focus(focus: dict[str, Any] | None) -> dict[str, Any] | None:
    if focus is None:
        return None
    if not isinstance(focus, dict) or set(focus) - {"targets", "max_depth"}:
        raise ValueError("focus accepts only targets and max_depth")
    targets = focus.get("targets")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 20:
        raise ValueError("focus.targets must contain 1..20 article locators")
    max_depth = int(focus.get("max_depth", 2))
    if not 1 <= max_depth <= 3:
        raise ValueError("focus.max_depth must be 1..3")
    normalized: list[dict[str, str]] = []
    for target in targets:
        if not isinstance(target, dict) or set(target) - {"article", "branch"}:
            raise ValueError("each focus target accepts only article and branch")
        if "article" not in target:
            raise ValueError("each focus target requires article")
        article = str(int(str(target["article"])))
        branch = _norm_branch(target.get("branch"))
        normalized.append({"article": article, "branch": branch})
    return {"targets": normalized, "max_depth": max_depth}


def _focus(
    focus: dict[str, Any] | None,
    *,
    edges: list[dict[str, Any]],
    reverse_index: dict[str, list[str]],
) -> list[dict[str, Any]]:
    if focus is None:
        return []
    out: list[dict[str, Any]] = []
    for target in focus["targets"]:
        node = _article_key(target["article"], target.get("branch"))
        out.append({
            "target": {
                "article": target["article"],
                "branch": target.get("branch") or "",
                "node_key": node,
            },
            "paths": focused_reverse_paths(
                node,
                edges=edges,
                reverse_index=reverse_index,
                max_depth=int(focus["max_depth"]),
            ),
        })
    return out


def fetch_law_reference_bundle(
    client: Any,
    *,
    selector: dict[str, Any],
    text_mode: str = "referenced_units",
    focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = _selector(selector)
    if text_mode not in _TEXT_MODES:
        raise ValueError(
            "text_mode must be graph_only, referenced_units, or full_document"
        )
    validated_focus = _validate_focus(focus)

    if selected["mode"] == "current":
        body_request = {"target": "eflaw", "ID": selected["law_id"]}
        relation_request = {"target": "lsDelegated", "ID": selected["law_id"]}
        requested_mst = ""
        effective_date = None
    else:
        body_request = {
            "target": "eflaw",
            "MST": selected["mst"],
            "efYd": selected["effective_date"],
        }
        relation_request = {"target": "lsDelegated", "MST": selected["mst"]}
        requested_mst = selected["mst"]
        effective_date = selected["effective_date"]

    body_raw = client._call(SERVICE_URL, body_request)
    document = _parse_document(
        body_raw, requested_mst=requested_mst, effective_date=effective_date
    )
    _verify_document(document, selected)

    relation_raw = client._call(SERVICE_URL, relation_request)
    parsed_relations = parse_lsdelegated_xml(relation_raw)
    projected = project_references(parsed_relations)
    relation_source = projected.get("source") or {}
    body_source = document["source"]

    if (
        relation_source.get("law_id")
        and body_source.get("law_id")
        and not _same_numeric_id(relation_source["law_id"], body_source["law_id"])
    ):
        raise ProviderResponseError(
            "PROVIDER_IDENTITY_MISMATCH",
            "lsDelegated source law_id does not match eflaw source law_id",
            retryable=False,
        )
    if selected["mode"] == "version":
        relation_seq = str(relation_source.get("seq") or "")
        if relation_seq and relation_seq != selected["mst"]:
            raise ProviderResponseError(
                "PROVIDER_IDENTITY_MISMATCH",
                "lsDelegated source sequence does not match requested MST",
                retryable=False,
            )

    body_observations, unresolved_body = extract_same_document_references(
        document["articles"], source_title=str(body_source.get("law") or "")
    )
    body_observations, unresolved_body = _validate_body_targets(
        body_observations,
        unresolved_body,
        articles=document["articles"],
    )
    edges, provider_observations = _reference_edges(
        source_title=str(body_source.get("law") or ""),
        provider_references=list(projected.get("references") or []),
        body_observations=body_observations,
    )
    outgoing, reverse = _indexes(edges)
    focused_paths = _focus(
        validated_focus, edges=edges, reverse_index=reverse
    )

    referenced_nodes: set[str] = set(outgoing)
    referenced_nodes.update(reverse)
    if text_mode == "graph_only":
        articles: list[dict[str, Any]] = []
    elif text_mode == "full_document":
        articles = document["articles"]
    else:
        articles = [
            article for article in document["articles"]
            if article["node_key"] in referenced_nodes
        ]

    same_document_edges = [edge for edge in edges if edge["same_document"]]
    body_edge_keys = {
        (
            edge["source"]["node_key"],
            edge["target"]["node_key"],
        )
        for edge in same_document_edges
        if "body_explicit" in edge["observed_by"]
    }
    provider_edge_keys = {
        (
            edge["source"]["node_key"],
            edge["target"]["node_key"],
        )
        for edge in same_document_edges
        if "lsDelegated" in edge["observed_by"]
    }

    version: dict[str, Any] = {
        "effective_date": str(body_source.get("effective_date") or ""),
        "law_key": str(body_source.get("law_key") or ""),
    }
    if selected["mode"] == "version":
        version["mst"] = selected["mst"]
        version["selector_mode"] = "version"
    else:
        version["mst_observed_from_lsDelegated"] = str(relation_source.get("seq") or "")
        version["selector_mode"] = "current"

    return {
        "status": "OK",
        "support_eligible": False,
        "semantic_approval": False,
        "source": {
            "official_source": "law.go.kr",
            "law_id": str(body_source.get("law_id") or ""),
            "title": str(body_source.get("law") or ""),
            "provider_document_type": str(body_source.get("provider_document_type") or ""),
            "ministry": str(body_source.get("ministry") or ""),
        },
        "version": version,
        "selector": selected,
        "text_mode": text_mode,
        "articles": articles,
        "reference_edges": edges,
        "provider_observations": provider_observations,
        "body_explicit_observations": body_observations,
        "body_unresolved_observations": unresolved_body,
        "outgoing_index": outgoing,
        "reverse_index_same_document": reverse,
        "focused_paths": focused_paths,
        "coverage": {
            "scope": (
                "single_document_current"
                if selected["mode"] == "current"
                else "single_document_version"
            ),
            "explicit_reference_policy": _POLICY_VERSION,
            "body_scan": "COMPLETE_FOR_POLICY",
            "provider_relation_response": "COMPLETE_RESPONSE",
            "provider_temporal_fidelity": (
                "CURRENT"
                if selected["mode"] == "current"
                else "NOT_GUARANTEED_FOR_VERSION"
            ),
            "reverse_index_same_document": "DERIVED_FROM_PROVIDER_UNION_BODY",
            "external_incoming": "NOT_SEARCHED",
            "semantic_relations": "NOT_EVALUATED",
            "counts": {
                "document_articles": int(document["meta"]["article_count"]),
                "provider_observations": len(provider_observations),
                "body_explicit_observations": len(body_observations),
                "body_unresolved_observations": len(unresolved_body),
                "reference_edges": len(edges),
                "same_document_edges": len(same_document_edges),
                "same_document_body_unique_edges": len(body_edge_keys),
                "same_document_provider_unique_edges": len(provider_edge_keys),
                "same_document_union_unique_edges": len(body_edge_keys | provider_edge_keys),
                "same_document_body_only_edges": len(body_edge_keys - provider_edge_keys),
                "same_document_provider_only_edges": len(provider_edge_keys - body_edge_keys),
            },
        },
        "provenance": {
            "body_request": body_request,
            "relation_request": relation_request,
            "body_response_sha256": str(document["meta"]["response_sha256"]),
            "relation_response_sha256": str(projected.get("meta", {}).get("response_sha256") or ""),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "clock_source": "local",
            "semantic_processing_added": False,
        },
    }
