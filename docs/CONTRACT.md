# 원문 조회와 문맥 계약

이 계약은 원문 전달의 완전성을 검사합니다. 법적 의미의 적절성을 승인하지 않습니다. 의미 판단은 이 도구를 호출하는 별도 분석기가 수행합니다.

`source_fragment` 입력은 `api_family`, `observed_identity`, `version_binding`, `locator`입니다. 판본 결박, 즉 요청한 문서와 특정 판본의 대응 확인은 `version_binding`으로 전달합니다.

| 자료 종류 | 공식 조회 대상 | 조회 식별값 | 추가 확인 |
|---|---|---|---|
| law | eflaw | MST | expected_law_key 또는 expected_response_sha256, 원문 시행일 |
| admin_rule | admrul | ID / LID | LID는 응답 판본 일련번호까지 별도로 확인 |
| ordinance | ordin | MST / ID | ID는 응답 판본 일련번호까지 별도로 확인 |
| treaty | trty | ID | 서명일과 발효일을 구별 |
| institution_rule | school / public / pi | ID / LID | provider_target 필수, 발령일로 시행일을 추정하지 않음 |

공통 `version_binding`에는 `status="RESOLVED"`, `provider_id`, `version_id`가 필요합니다. 기관규칙 외에는 확인할 `effective_date`도 필요합니다. 안정 문서 번호 LID/일부 ID는 여러 판본이 공유하므로 `expected_provider_sequence` 등 별도의 판본 일련번호 확인값을 받습니다. `version_id`는 호출자가 사용하는 판본의 이름이고 실제 조회는 `provider_id` 및 검증된 공식 확인값으로 결박합니다. 기관규칙은 시행일 입력이 없어도 원문을 확보할 수 있으나 시행일 확인 없이 근거로 사용 가능한 상태가 되지 않습니다.

law의 위치 입력은 `article`과 선택적인 `branch`입니다. 비법률 자료는 공식 XML 본문 전체를 구조와 함께 제공합니다. 세부 위치를 처리할 수 없으면 `LOCATOR_UNSUPPORTED_FULL_DOCUMENT`로 표시하고 근거 사용을 막습니다. 다른 자료 종류나 판본으로 대체하지 않습니다.

조회 결과의 `meta`는 원 응답과 본문 내용의 SHA-256 해시(내용이 같은지 비교하는 지문), 요청, 수집 시각, 로컬 시계 표시와 원문을 보존합니다. `source_completeness.scope="provider_inline_body"`는 제공기관 응답 안의 본문 범위만 설명합니다. 일반 첨부 링크는 아직 읽지 않은 후보로 전달합니다. 본문 없이 링크만 있는 별표·별지는 필요한 구조 내용의 누락으로 표시합니다. 첨부의 법적 필요성이나 의미를 이 모듈이 추정하지 않습니다.

문맥 묶음은 `dps-context-v0.3`를 사용합니다. `ROOT_NORMATIVE`, `GOVERNING_NORMATIVE`, `TARGET_NORMATIVE`, `TEMPORAL_NORMATIVE`는 판본이 결박된 규범 원문의 역할입니다. `EXTERNAL_AUDIT`, `EXPLANATORY`, `RETRIEVAL_CANDIDATE`는 직접적인 확정 근거로 사용할 수 없습니다. 원문은 `context_blocks`에 두며 첨부 링크와 메타데이터를 규범 원문으로 승격하지 않습니다.

`ContextProducer`는 다음 기능만 수행합니다.

1. 호출자가 지정한 자료를 조회하고 같은 대상·판본·위치의 통신을 한 번으로 합칩니다.
2. 각 관측의 원래 식별값, 역할, 요청과 해시를 보존합니다. 같은 원문에 서로 다른 역할이 지정되면 역할을 따로 유지합니다.
3. 부모 문맥과 조·항·호·목·표·별표의 제공 구조를 보존합니다. 내용과 메타데이터를 구별합니다.
4. 실패·누락·판본 불일치를 노출하고 기계적 검사에 따라 `READY` 또는 `PARTIAL`을 반환합니다.

호출자가 제공하는 탐색 완료·잔여 탐색·변환 검토·시간 확인 값은 외부 입력입니다. 기계적 검사 통과나 `READY`를 법적 해결, 의미적 완전성, `RESOLVED` 승인으로 해석하면 안 됩니다. 서로 다른 시간의 법령 상태를 한 의미로 합치는 판단도 별도 분석기의 책임입니다.
