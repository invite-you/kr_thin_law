"""Administrative-rule articles in the law_article 조→항→호→목 shape."""
from __future__ import annotations

from typing import Any

from .flat_structure import DERIVATION, StructureDerivationError, derive_article
from .source_fragment import fetch_source_fragment


def fetch_admin_rule_articles(
    client: Any, *, version_binding: dict[str, Any],
    article: int | None = None, branch: int | None = None,
) -> dict[str, Any]:
    """Fetch one verified admin-rule version and return its articles with derived structure.

    The version check is the same as source_fragment(admin_rule). Supplementary
    provisions (부칙) and other non-article blocks are returned unchanged.
    """
    result = fetch_source_fragment(client, api_family="admin_rule", observed_identity=None,
                                   version_binding=version_binding)
    if result.get("status") != "OK":
        return result
    articles: list[dict[str, Any]] = []
    headings: list[dict[str, Any]] = []
    other_blocks: list[dict[str, Any]] = []
    for block in result.get("blocks", []):
        if block.get("provider_tag") != "조문내용":
            other_blocks.append({k: block[k] for k in ("provider_tag", "provider_path", "text") if k in block})
            continue
        try:
            derived = derive_article(block["text"])
        except StructureDerivationError as exc:
            return {**_strip(result), "status": "STRUCTURE_DERIVATION_FAILED", "support_eligible": False,
                    "detail": str(exc), "provider_path": block.get("provider_path")}
        derived["provider_path"] = block.get("provider_path")
        (headings if derived["kind"] == "heading" else articles).append(derived)
    if article is not None:
        want = (str(article), str(branch or ""))
        articles = [a for a in articles if (a["number"], a["branch"]) == want]
        if not articles:
            return {**_strip(result), "status": "LOCATOR_NOT_FOUND", "support_eligible": False,
                    "requested_locator": {"article": article, "branch": branch}}
    return {
        **_strip(result),
        "articles": articles,
        "headings": headings if article is None else [],
        "other_blocks": other_blocks if article is None else [],
        "structure_derivation": {
            "method": DERIVATION,
            "reconstruction": "BYTE_EQUAL",
            "note": "제공처가 조문을 통짜 글로 주므로 ①·1.·가. 표시로 나눈 구조입니다. 법적 의미 판단이 아닙니다.",
        },
    }


def _strip(result: dict[str, Any]) -> dict[str, Any]:
    keep = ("status", "api_family", "support_eligible", "observed_identity", "version_binding",
            "source", "source_completeness", "meta")
    return {k: result[k] for k in keep if k in result}
