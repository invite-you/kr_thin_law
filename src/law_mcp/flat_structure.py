"""Derive the law_article 조→항→호→목 shape from flat provider article text.

Official law XML carries <항>/<호>/<목> elements, but administrative-rule (and other
non-law) responses deliver each article as one flat 조문내용 string. This module
splits that string on the drafting markers only (①, "1.", "가.") so callers receive
the same node shape as law_article: {type, label, text, children}.

Transport only: no legal meaning is decided. Every node also carries `span`, the
[start, end) offsets of its own slice in the original text, and the slices are
checked to rebuild the original text byte-for-byte; otherwise derivation fails.
"""
from __future__ import annotations

import re
from typing import Any

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_HEADING = re.compile(r"제(\d+)조(?:의(\d+))?\s*\(([^)]*)\)")
_CHAPTER = re.compile(r"제\d+(?:장|절|관|편)\s")
_LEVELS = (
    ("paragraph", re.compile(rf"[ \t]*([{CIRCLED}])")),
    ("item", re.compile(r"[ \t]*(\d+(?:의\d+)?\.)\s")),
    ("subitem", re.compile(r"[ \t]*([가-힣]\.)\s")),
)
_DEPTH = {"paragraph": 0, "item": 1, "subitem": 2}
DERIVATION = "TEXT_MARKER_SPLIT"


class StructureDerivationError(ValueError):
    pass


def _cuts(text: str, heading_end: int) -> list[tuple[int, str, str]]:
    cuts: list[tuple[int, str, str]] = []
    first = re.compile(rf"[ \t]+([{CIRCLED}])").match(text, heading_end)
    if first:
        cuts.append((first.start(), "paragraph", first.group(1)))
    for newline in re.finditer(r"\n", text):
        for kind, pattern in _LEVELS:
            match = pattern.match(text, newline.end())
            if match:
                cuts.append((newline.start(), kind, match.group(1)))
                break
    return cuts


def derive_article(text: str) -> dict[str, Any]:
    """Return a law_article-shaped article dict for one flat article string."""
    if _CHAPTER.match(text + " ") and not _HEADING.match(text):
        return {"kind": "heading", "text": text.strip(), "span": [0, len(text)]}
    heading = _HEADING.match(text)
    if not heading:
        raise StructureDerivationError("article heading not found")
    cuts = _cuts(text, heading.end())
    bounds = [0] + [c[0] for c in cuts] + [len(text)]
    head_slice = (bounds[0], bounds[1])
    root: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    for index, (start, kind, label) in enumerate(cuts):
        end = bounds[index + 2]
        node = {"type": kind, "provider_tag": "조문내용", "label": label,
                "text": text[start:end].strip(), "children": [], "span": [start, end]}
        depth = _DEPTH[kind]
        while stack and _DEPTH[stack[-1]["type"]] >= depth:
            stack.pop()
        (stack[-1]["children"] if stack else root).append(node)
        stack.append(node)

    slices = [text[head_slice[0]:head_slice[1]]]

    def collect(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            slices.append(text[node["span"][0]:node["span"][1]])
            collect(node["children"])

    collect(root)
    if "".join(slices) != text:
        raise StructureDerivationError("derived slices do not rebuild the provider text")
    return {
        "kind": "article",
        "number": heading.group(1),
        "branch": heading.group(2) or "",
        "title": heading.group(3).strip(),
        "head_text": text[head_slice[0]:head_slice[1]].strip(),
        "head_span": list(head_slice),
        "text": text,
        "structure": root,
        "structure_derivation": DERIVATION,
    }
