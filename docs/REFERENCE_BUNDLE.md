# law_reference_bundle 계약

## 목적

한 법령의 한 판본에 대해 공식 전체 본문과 `lsDelegated`를 각각 한 번 조회한 뒤, 다음 호출자가 같은 비싼 원천 조회를 반복하지 않도록 구조적 참조 자료를 함께 반환합니다.

이 도구는 법적 의미 관계를 만들지 않습니다. 반환 edge는 **공식 제공처 관측 또는 법문에 명시된 조문 locator의 기계적 관측**입니다.

## MCP wire validation

`selector`와 `focus`는 MCP wire에서 단순 JSON object로 노출합니다. current/version을 `oneOf` 또는 discriminator로 나누지 않습니다. 실제 허용 필드와 값 검증은 bundle core의 strict validator가 하나의 진실원으로 수행하며, 잘못된 입력은 provider I/O 전에 거부됩니다.

## 입력

### 현행

```json
{
  "selector": {
    "mode": "current",
    "law_id": "011357"
  },
  "text_mode": "referenced_units",
  "focus": {
    "targets": [{"article": 29}],
    "max_depth": 2
  }
}
```

현행 모드는 공식 `eflaw(ID=...)`와 `lsDelegated(ID=...)`를 사용합니다. ID 조회에서 `efYd`를 몰래 채우지 않습니다.

### 특정 판본

```json
{
  "selector": {
    "mode": "version",
    "mst": "283839",
    "effective_date": "20260911",
    "expected_law_key": "0113572026031021445",
    "expected_law_id": "011357"
  },
  "text_mode": "graph_only"
}
```

특정 판본은 `MST + effective_date`만으로 확정 근거로 취급하지 않습니다. 다음 중 하나의 독립 판본 확인값이 반드시 필요합니다.

- `expected_law_key`
- `expected_response_sha256`

`expected_law_id`는 선택적 추가 identity guard입니다.

## text_mode

- `graph_only`: 참조관측·edge·index·coverage만 반환합니다.
- `referenced_units`: 참조 edge가 닿는 이 법령의 조문만 구조와 원문을 중복 없이 반환합니다. 기본값입니다.
- `full_document`: 전체 조문 구조와 원문을 반환합니다.

## 본문 명시참조 정책

정책 ID: `same_document_explicit_article_v1`.

자동 same-document edge로 만들 수 있는 것은 문서 정체성을 추측하지 않아도 되는 경우뿐입니다.

자동 처리 예:

- `제29조`
- `제29조제1항`
- `제66조부터 제68조까지`
- `제43조의2부터 제43조의4까지`
- `이 법 제29조`
- 현재 문서명과 정확히 일치하는 `「문서명」 제29조`

자동 same-document edge로 만들지 않는 예:

- `「형법」 제355조` — 이름이 다른 문서
- `법 제29조` — 시행령·규칙에서는 상위 법률을 뜻할 수 있음
- `같은 법 제29조` — 선행 문맥을 따라야 문서가 정해짐

이들은 `body_unresolved_observations`에 원문 위치와 이유를 남깁니다. 이는 누락이 아니라 의도적 fail-closed입니다.

## 출력 핵심

- `articles`: text_mode에 따른 공식 조문 원문과 조→항→호→목 구조
- `provider_observations`: `lsDelegated` 관측을 순서·원문 필드와 함께 보존
- `body_explicit_observations`: 본문에서 기계적으로 확정된 same-document locator
- `body_unresolved_observations`: 외부/상대 문서 표현 또는 안전하게 확장할 수 없는 범위
- `reference_edges`: 관측을 조문→조문 단위로 중복 제거한 구조 edge
- `outgoing_index`: source 조문에서 edge ID 목록
- `reverse_index_same_document`: 같은 문서 target 조문에서 들어오는 edge ID 목록
- `focused_paths`: focus가 요청됐을 때만 계산하는 최대 1~3 hop 역방향 경로
- `coverage`: 조사 범위와 하지 않은 조사를 분리한 상태
- `provenance`: 실제 upstream request와 응답 SHA-256

`observed_by=["body_explicit","lsDelegated"]`는 두 발견 채널에서 관측됐다는 뜻일 뿐, 법적 의미가 독립적으로 교차검증됐다는 뜻이 아닙니다.

## coverage 해석

```json
{
  "scope": "single_document_version",
  "explicit_reference_policy": "same_document_explicit_article_v1",
  "body_scan": "COMPLETE_FOR_POLICY",
  "provider_relation_response": "COMPLETE_RESPONSE",
  "provider_temporal_fidelity": "NOT_GUARANTEED_FOR_VERSION",
  "reverse_index_same_document": "DERIVED_FROM_PROVIDER_UNION_BODY",
  "external_incoming": "NOT_SEARCHED",
  "semantic_relations": "NOT_EVALUATED"
}
```

`COMPLETE_FOR_POLICY`는 위 정책으로 전체 본문을 훑었다는 뜻입니다. 모든 법적 의미관계가 완전하다는 뜻이 아닙니다.

특정 과거 판본에서 `lsDelegated`가 그 시점의 관계를 완전히 재현한다고 가정하지 않습니다. 따라서 version 모드는 항상 `provider_temporal_fidelity=NOT_GUARANTEED_FOR_VERSION`을 반환합니다.

## 경계

이 도구가 하지 않는 것:

- historical target resolver
- 상대 문서 지시어의 의미 해소
- Fact 생성
- Dependency 승격
- APPLY/DEFINE/EXCLUDE/OVERRIDE 판정
- semantic closure
- 전국 모든 법령의 incoming 역색인
- LLM 분석

같은 문서 reverse index는 별도 법률 ontology가 아니라 관측된 source→target edge를 뒤집은 파생 자료입니다.
