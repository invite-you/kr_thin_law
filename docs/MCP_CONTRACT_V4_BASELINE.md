# MCP Contract v4 — Thin Official-Source Adapter

## 1. 경계

이 MCP는 법률 의미 데이터 구조를 완성하지 않는다. 국가법령정보 API가 이미 제공하는 **정체성, 판본, 공식 구조, 시간 원천값, 참조 관측값**을 다음 단계가 다시 XML/법문을 파싱하지 않아도 되도록 전달한다.

### MCP 안에서 허용되는 가공

- `제10조의2 -> JO=001002` 같은 공식 locator의 기계적 인코딩
- 요청 조문 1건 선택
- XML의 명시적 `조→항→호→목` 구조 보존
- 번호/본문이 모두 없는 provider wrapper 제거(자식은 그대로 승격)
- `lsDelegated` 반복 레코드의 순서/target header 상태 보존
- API field family를 `law/admin_rule/ordinance/institution_rule/treaty`로 표시
- 동일 provider 문맥에서 쪼개진 링크 조각에 compact grouping key와 순서 부여
- 조문/가지 번호의 공백 제거·숫자 정규화(`제10조의2 -> JO=001002` 인코딩 포함)
- 빈 공식 값의 빈 값 보존(앞/뒤 형제 레코드 값으로 채우지 않음) 및 응답 provenance hash(`response_sha256`) 계산

### 반드시 다음 단계에 남기는 것

- 영구 `Document/DocumentVersion/SourceUnit/SourceRef` ID 정책
- `ReferenceObservation` 최종 저장 정책
- target의 역사 판본 resolution
- 상대참조/범위참조 해석
- Fact/Dependency 생성 및 효과 판정
- selective semantic closure
- TemporalRule 해석
- LLM 검증/감사

## 2. `law_article`

입력:

```text
mst, effective_date, article, branch?
```

호출은 공식 `eflaw` 본문 API의 `MST + efYd + JO`를 사용한다.

### 선택 전제조건과 오류

`article + branch`는 비전문(전문 제외) 조문을 정확히 1건 식별해야 한다.

- 0건: `ARTICLE_NOT_FOUND`
- 다건: `AMBIGUOUS_ARTICLE` — `candidates`에 각 조문의 `key/number/branch/effective_date/head_text`를 담아 호출자가 구분할 수 있게 한다. 선택 결과는 항상 단건이다.

응답 이상 전체는 §6 에러 계약을 따른다.

출력 예시:

```json
{
  "source": {
    "official_source": "law.go.kr",
    "law": "개인정보 보호법 시행령",
    "law_id": "011468",
    "law_key": "...",
    "mst": "289537",
    "provider_document_type": "대통령령",
    "provider_document_type_code": "A0007",
    "ministry": "개인정보보호위원회",
    "promulgation_date": "20260910",
    "promulgation_number": "36671",
    "effective_date": "20260911",
    "revision_type": "일부개정",
    "article_effective_date_text": "...",
    "annex_effective_date_text": "..."
  },
  "article": {
    "key": "0004021",
    "number": "4",
    "branch": "2",
    "title": "...",
    "effective_date": "...",
    "head_text": "...",
    "text": "...",
    "structure": [
      {
        "type": "item",
        "provider_tag": "호",
        "label": "1.",
        "text": "...",
        "children": []
      }
    ]
  },
  "meta": {
    "response_sha256": "...",
    "text_sha256": "...",
    "clock_source": "local",
    "requested_effective_date": "...",
    "request": {"target":"eflaw","MST":"...","efYd":"...","JO":"..."},
    "retrieved_at": "..."
  }
}
```

### 구조 원칙

공식 API는 항/호/목을 별도 필드와 중첩 요소로 제공한다. 따라서 MCP가 이를 flat text만으로 축소하면 다음 단계가 법문을 재파싱해야 한다. v4는 provider 구조를 그대로 보존한다.

단, `<항>`이 번호도 본문도 없이 `호`만 감싸는 경우는 법적 paragraph를 새로 만들지 않고 자식을 article 바로 아래에 둔다. 이것은 의미 추론이 아니라 **빈 provider wrapper를 canonical structural node로 오인하지 않기 위한 lossless normalization**이다.

## 3. `law_references`

입력:

```text
mst, article?, branch?
```

출력 예시:

```json
{
  "references": [{
    "provider_group": "3:1:2",
    "provider_order": 2,
    "source_article": "4",
    "source_branch": "2",
    "source_title": "...",
    "kind": "인용법령",
    "at": "제4조의2",
    "text": "제1항",
    "context": "법 제7조제1항",
    "target": {
      "api_family": "law",
      "family_ambiguous": false,
      "observed_seq": "213857",
      "observed_title": "개인정보 보호법",
      "linked_article": "7",
      "linked_branch": "",
      "linked_article_title": "개인정보 보호위원회"
    }
  }]
}
```

### `provider_group`의 의미

법적 relation ID가 아니다. 같은 `lsDelegated` provider 문맥이 여러 링크 child로 쪼개진 경우를 다시 찾기 위한 **응답-local transport key**일 뿐이다.

형식:

```text
<source-group-index>:<delegate-index>:<fragment-group-index>
```

`provider_order`는 해당 `<위임정보>` 안의 원래 record 순서다.

다음 단계는 같은 group의 `text` 조각을 임의로 문자열 결합해 법적 target을 확정할 필요가 없다. `context`, `at`, 각 target fragment를 함께 보존한 뒤 ReferenceObservation/target resolution 단계에서 사용하면 된다.

## 4. 안전한 명명

- `api_family`: provider field family. 내부 법률 ontology의 document type 판정이 아님. 분류는 기계적 우선순위로만 한다: **레코드 태그(`위임규정조문정보` 등) > 레코드 자체의 family 필드 > target segment header 필드**. 공공기관규정이 `위임법령*` 필드로 제공되는 경우 `api_family`는 필드 출처를 나타내므로 `"law"`일 수 있으며, 법적 종류 재분류는 다음 단계의 일이다.
- `family_ambiguous`: `api_family`가 provider 레코드 태그가 아니라 필드/헤더 추론으로만 판정된 경우 `true`. 실측 lsDelegated는 전 레코드가 레코드 태그 판정이라 `false`이며, `true`는 레코드 태그 없는 인라인 레코드에서만 노출된다(C-3 잔여 표시).
- `observed_seq`: API 응답에서 관측된 target seq. source의 역사 시점 target version 확정값이 아님.
- `observed_seq/observed_title`의 세그먼트 헤더 상속: 레코드 자체에 target 필드가 없으면 **같은 target segment header(`위임구분`/target 필드)의 선언값을 상속**한다. 이는 provider 선언 상속이며, 앞/뒤 형제 레코드 값을 채우는 것이 아니다. family가 바뀌거나 seq가 다르면 새 segment를 시작해 경계를 보존하고, family 간 교차 상속은 하지 않는다.
- `kind`: `위임구분` 원문. Dependency effect가 아님.
- `article_effective_date_text`: provider 원문. TemporalRule이 아님.

## 5. 의도적으로 없는 기능

```text
historical target resolver
relative/range reference resolver
Fact generation
Dependency promotion
semantic closure
TemporalRule parser
LLM
vector/reranker
DB/cache
```

이 경계를 넘기 시작하면 MCP가 다음 단계와 중복된다.

## 6. 에러 계약

응답 이상은 `ProviderResponseError(ValueError)`로 표현한다. 기존 호출자의 `except ValueError` 호환을 위해 ValueError를 상속한다. 필드: `error_code`, `retryable`, `detail`, `raw_preview`(응답 선두 500바이트), `candidates`(`AMBIGUOUS_ARTICLE` 전용).

| error_code | 조건 | retryable |
|---|---|---|
| `EMPTY_RESPONSE` | 응답 본문 0바이트 | true |
| `HTML_RESPONSE` | HTML 페이지(게이트웨이/오류 페이지) 회신 | true |
| `PARSE_ERROR` | XML이 well-formed 아님 | true |
| `UNEXPECTED_ROOT` | 루트 또는 `법령` 노드 불일치 | false |
| `MISSING_STRUCTURE` | 필수 구조 노드 부재(`기본정보` 등) | false |
| `ARTICLE_NOT_FOUND` | 요청 조문 0건 | false |
| `AMBIGUOUS_ARTICLE` | 비전문 조문 다건 (`candidates` 제공) | false |

`retryable=true`는 전송 계층 일시 오류 성격으로, 호출자가 재시도해도 안전함을 뜻한다. HTTP 상태 코드 계층의 오류 분류는 `UPSTREAM_INTEGRATION.md`의 클라이언트 계층에서 담당한다.

## 7. provenance 메타 (v4.3)

- `response_sha256`: raw 응답 bytes의 hash — 전송 계층 증거. `version_manifest_hash`가 아니다.
- `text_sha256`(`law_article`): `article.text` 본문만의 hash. raw 메타(공포일자 등)만 변하면 변하지 않고, 조문 본문이 변하면 항상 변한다. 본문은 raw 안에 있으므로 본문 변경 시 `response_sha256`도 함께 변한다.
- `clock_source`: `retrieved_at`의 출처 표기. 현재 `"local"`(호스트 UTC 시계)이며 신뢰시계 도입 시 값이 바뀐다.
- 캡처 원장(`src/law_mcp/capture_ledger.py`, `validation/capture_ledger.jsonl`): payload별 `first_seen`을 불변으로 기록한다. 동일 `response_sha256` 재수신 시 `first_seen` 유지, 신규 payload만 새 행을 받는다. 배치 수집(`validation/batch_ingest.py`)은 고유 요청당 1회만 다운로드한다.
