"""MCP entry point. Thin tools never decide legal meaning or resolve versions."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Callable

from mcp.server import MCPServer

from .card_source import ProviderResponseError, fetch_references
from .client import FixtureClient, OfficialClient
from .context_packet import validate_packet
from .admin_article import fetch_admin_rule_articles
from .context_producer import ContextProducer
from .decision import fetch_decision
from .law_supplement import fetch_law_supplements
from .search import search_documents
from .source_fragment import fetch_source_fragment


def create_server(client: Any) -> MCPServer:
    server = MCPServer(
        "legal-thin-mcp", version="4.8.0",
        instructions=(
            "공식 원문과 판본이 확인된 문맥을 조회합니다. 법적 의미, 필요한 참조의 선택, "
            "과거 판본 해석과 최종 의미 판단은 호출자가 수행합니다. READY는 원문 전달 상태입니다."
        ),
    )

    def guarded(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            result = operation()
            result.setdefault("execution_mode", "FIXTURE_REPLAY" if isinstance(client, FixtureClient) else "LIVE")
            return result
        except ProviderResponseError as exc:
            return {
                "status": "PROVIDER_ERROR", "error_code": exc.error_code,
                "detail": exc.detail, "retryable": exc.retryable,
                "candidates": exc.candidates, "support_eligible": False,
            }
        except ValueError as exc:
            return {"status": "INVALID_INPUT", "detail": str(exc), "support_eligible": False}

    @server.tool()
    def law_article(
        mst: str, effective_date: str, article: int, expected_law_key: str,
        branch: int | None = None, expected_law_id: str | None = None,
    ) -> dict[str, Any]:
        """정확한 판본의 조항호목을 조회합니다. expected_law_key는 판본 확인용 공식 원천값입니다."""
        binding: dict[str, Any] = {
            "status": "RESOLVED", "provider_id": mst, "version_id": mst,
            "effective_date": effective_date, "expected_law_key": expected_law_key,
        }
        if expected_law_id:
            binding["expected_law_id"] = expected_law_id
        return guarded(lambda: fetch_source_fragment(
            client, api_family="law", observed_identity=None, version_binding=binding,
            locator={"article": article, "branch": branch},
        ))

    @server.tool()
    def law_references(
        mst: str, article: int | None = None, branch: int | None = None,
    ) -> dict[str, Any]:
        """공식 참조 관측값을 순서대로 조회합니다. 참조 번호는 확정된 대상 판본이나 법적 관계가 아닙니다."""
        def retrieve() -> dict[str, Any]:
            result = fetch_references(client, mst=mst, article=article, branch=branch)
            actual = str(result.get("source", {}).get("seq") or "")
            result["status"] = "OK" if actual == mst else "PROVIDER_IDENTITY_MISMATCH"
            result["support_eligible"] = False
            return result
        return guarded(retrieve)

    @server.tool()
    def source_fragment(
        api_family: str, version_binding: dict[str, Any],
        observed_identity: str | None = None, locator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """law/admin_rule/ordinance/treaty/institution_rule 원문을 검증된 판본 입력으로 조회합니다."""
        return guarded(lambda: fetch_source_fragment(
            client, api_family=api_family, observed_identity=observed_identity,
            version_binding=version_binding, locator=locator,
        ))

    @server.tool()
    def law_search(
        target: str, query: str, include_history: bool = False, display: int = 100, page: int = 1,
        search_body: bool = False,
    ) -> dict[str, Any]:
        """공식 검색 목록을 조회합니다. target=eflaw(법령, 시행일별 판본), admrul(행정규칙), ppc(개인정보보호위원회 의결서), prec(법원 판례). search_body=True면 본문 검색. 후보 목록일 뿐 판본·문서를 고르지 않습니다."""
        return guarded(lambda: search_documents(
            client, target=target, query=query, include_history=include_history,
            display=display, page=page, search_body=search_body,
        ))

    @server.tool()
    def decision_document(target: str, decision_id: str) -> dict[str, Any]:
        """개인정보보호위원회 의결서(ppc) 또는 법원 판례(prec) 한 건을 제공처 칸 그대로 돌려줍니다. 요청한 일련번호와 응답을 대조합니다. 위반 조항·금액을 뽑거나 해석하지 않습니다."""
        return guarded(lambda: fetch_decision(client, target=target, decision_id=decision_id))

    @server.tool()
    def admin_rule_article(
        version_binding: dict[str, Any], article: int | None = None, branch: int | None = None,
    ) -> dict[str, Any]:
        """판본이 확인된 행정규칙의 조문을 law_article과 같은 조·항·호·목 구조로 돌려줍니다. 구조는 원문 표시(①·1.·가.)로 나눈 것이며 원문과 바이트 단위로 재조립 검사를 거칩니다."""
        return guarded(lambda: fetch_admin_rule_articles(
            client, version_binding=version_binding, article=article, branch=branch,
        ))

    @server.tool()
    def law_supplements(
        mst: str, effective_date: str, expected_law_key: str, expected_law_id: str | None = None,
    ) -> dict[str, Any]:
        """정확한 판본의 부칙(시행일·적용례·경과조치)과 별표를 원문 그대로 돌려줍니다. 부칙은 조·항·호·목 구조를 덧붙이며 바이트 재조립 검사를 거칩니다."""
        binding: dict[str, Any] = {
            "status": "RESOLVED", "provider_id": mst, "version_id": mst,
            "effective_date": effective_date, "expected_law_key": expected_law_key,
        }
        if expected_law_id:
            binding["expected_law_id"] = expected_law_id
        return guarded(lambda: fetch_law_supplements(client, version_binding=binding))

    @server.tool()
    def context_packet(
        root_need: dict[str, Any], selected_needs: list[dict[str, Any]] | None = None,
        mode: str = "CANONICAL_BUILD", as_of: str | None = None,
        coverage: dict[str, Any] | None = None,
        transform_decisions: list[dict[str, Any]] | None = None,
        temporal_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """호출자가 선택한 근거만 별도 조립기로 가져옵니다. 자료 완전성 검사이며 법적 해결 판정이 아닙니다."""
        producer = ContextProducer(lambda **kwargs: fetch_source_fragment(client, **kwargs))
        return guarded(lambda: producer.produce(
            root_need, selected_needs=selected_needs or (), mode=mode, as_of=as_of,
            coverage=coverage, transform_decisions=transform_decisions or (),
            temporal_binding=temporal_binding,
        ))

    @server.tool()
    def validate_context_packet(packet: dict[str, Any]) -> dict[str, Any]:
        """문맥 묶음의 식별값, 해시, 근거 역할, 누락과 시간 결박을 검사합니다. 법적 의미를 승인하지 않습니다."""
        errors = validate_packet(packet)
        return {"status": "PASS" if not errors else "FAIL", "errors": errors, "semantic_approval": False}

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Version-bound Korean legal thin MCP over stdio")
    parser.add_argument("--fixture-manifest", type=Path, help="Explicit offline replay, never a live fallback")
    parser.add_argument("--fixture-root", type=Path, help="Explicit directory allowed for offline raw files")
    parser.add_argument("--capture-dir", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()
    if args.fixture_manifest:
        client: Any = FixtureClient(args.fixture_manifest, allowed_root=args.fixture_root)
    else:
        capture_dir = args.capture_dir or Path(os.environ.get(
            "LAW_MCP_CAPTURE_DIR", str(Path.home() / ".legal-thin-mcp" / "captures")
        ))
        client = OfficialClient(
            capture_dir=capture_dir, timeout=args.timeout, max_attempts=args.max_attempts
        )
    create_server(client).run(transport="stdio")


if __name__ == "__main__":
    main()
