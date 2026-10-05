# 4.9.2 — 2026-10-06

- `law_reference_bundle`의 MCP wire selector를 중첩 `oneOf/discriminator` union에서 단순 JSON object로 변경하고, 엄격 검증을 기존 core `_selector()` 하나로 통일했습니다.
- 4.9.1을 대상으로 실제 MCP `Client.call_tool` 회귀를 먼저 추가해 유효한 current/version selector가 둘 다 `string_pattern_mismatch`로 provider I/O 전에 차단되는 문제를 재현했습니다. 수정 뒤 동일 E2E가 provider까지 도달합니다.
- 유효 version selector에서 `expected_law_key`/응답 hash가 빠지거나 알 수 없는 selector 필드가 들어오면 provider 호출 수 0인 상태에서 `INVALID_INPUT`으로 닫는 회귀를 추가했습니다.
- public smoke를 내부 Python 함수 직접 호출에서 실제 MCP wire 호출로 변경했습니다. 공개 `OC=test`로 자동차관리법 current bundle(198개 조문, 1,237 reference edges, upstream 2회)을 통과했습니다.
- 별도 live probe에서 같은 current 응답의 공식 MST/시행일/법령키를 사용해 exact version selector를 MCP wire로 다시 호출했고 MST `290737`, 시행일 `20261002`, 법령키 `0017472026080421857`을 동일하게 결박하며 upstream 2회로 성공했습니다.
- capture redaction에서 URL-encoded `OC%3D<credential>`이 남는 결함을 회귀로 재현하고, literal 및 URL-encoded/double-encoded OC echo를 capture/error 구성 전에 정확한 known-value 방식으로 마스킹하도록 수정했습니다.
- Python 3.10/3.12에서 231개 회귀, ruff, mypy, package build를 통과했습니다.

# 4.9.1 — 2026-10-06

- 국가법령정보 Open API의 실패 응답을 정상 빈 데이터로 보이게 만들 수 있던 오류 처리를 수정했습니다.
- HTTP 200이어도 제공처가 명시한 실패(`resultCode != 00`, `resultMsg=fail`, 오류용 `Response` envelope)는 `ProviderResponseError`로 처리합니다.
- MCP 계층에서는 이 오류를 성공 객체로 감싸지 않고 SDK의 `ToolError`로 전달하여 `is_error=true`가 유지됩니다.
- 오류 원인은 MCP가 별도 분류·재작성하지 않습니다. 인증값 `OC`만 가린 뒤 제공처의 원문 오류 XML을 `UPSTREAM_RESPONSE`로 그대로 전달합니다.
- 검색 응답은 예상 root와 `totalCnt`를 검증하므로 malformed/error XML이 `status=OK, rows=[]`로 축약되지 않습니다. 정상 `totalCnt=0` 검색은 계속 성공입니다.
- `context_packet` 내부에서도 provider transport 오류를 PARTIAL/EMPTY로 삼키지 않고 상위 MCP tool error까지 전파합니다.
- 실제 공개 API에서 잘못된 인증값을 사용해 `<Response><result>사용자 정보 검증에 실패...</result><msg>...</msg></Response>` 형상을 재현하고 회귀시험으로 고정했습니다.

# 4.9.0 — 2026-10-05

- `law_reference_bundle`을 추가했습니다. 법령 전체 `eflaw` 본문과 `lsDelegated`를 문서당 한 번씩 조회하고, 본문 명시 조문참조와 제공처 관측의 합집합으로 outgoing 및 같은 문서 reverse index를 만듭니다.
- PIPA 현행 fixture에서 `lsDelegated`만으로는 보이지 않던 제35조의2→제29조 명시참조를 본문 채널이 보완하는 회귀테스트를 추가했습니다.
- `법 제N조`, `같은 법 제N조`, 다른 법령명이 붙은 조문은 같은 문서 edge로 추측하지 않고 unresolved 관측으로 보존합니다.
- current selector는 `law_id`만 받고, version selector는 `MST + 시행일 + 독립 판본 증명값`을 요구해 현재/과거 조회 의미가 섞이지 않게 했습니다.
- `graph_only / referenced_units / full_document` 반환 모드를 추가하고, 전체 multi-hop 경로는 미리 펼치지 않으며 `focus`가 있을 때만 최대 3 hop을 계산합니다.
- coverage에 nationwide incoming 미검색, semantic 미평가, 과거 `lsDelegated` 시간 정합성 미보장을 명시합니다.
- GitHub Actions에서 Python 3.10/3.12 offline 회귀, ruff, mypy, build를 실행하고 공식 `OC=test` public sample live smoke를 별도 관측합니다.

# 4.8.0 — 2026-10-01

기존 도구의 동작과 출력은 바꾸지 않고, 검색 대상 2개와 도구 1개를 더했습니다.

- `law_search`: target에 `ppc`(개인정보보호위원회 의결서)와 `prec`(법원 판례)를 더했습니다. `search_body=True`로 본문 검색을 합니다(제공처 `search=2`). 의결서는 안건명이 비어 있는 결정이 많아 제목 검색으로는 거의 찾히지 않습니다(실측).
- `decision_document`: 의결서·판례 한 건을 제공처 칸 그대로 돌려줍니다. 위반 조항·금액은 제공처가 칸을 고르지 않게 본문 글에 넣어 두므로 뽑지 않습니다(실측: 과태료 금액이 '신청인' 칸에 들어간 의결서).

# 4.7.0 — 2026-10-01

기존 도구 7개의 동작과 출력은 바꾸지 않고 도구 1개를 추가했습니다.

- `law_supplements`: `law_article`은 조문 하나만 돌려주므로, 같은 판본의 부칙과 별표를 따로 돌려줍니다. 판본 확인(일련번호·법령ID·법령키·시행일)은 `law_article`과 같은 검사를 공유합니다(`law_version_status`로 분리).
- 부칙은 줄 머리의 `제N조(` 표시로 조를 나누고 조 안은 `admin_rule_article`과 같은 분해기로 조·항·호·목 구조를 덧붙입니다. 나눈 조각이 원문을 재조립하지 못하면 그 부칙은 구조 없이 원문만 돌려줍니다.
- 별표는 제공처 글과 파일 링크를 그대로 돌려줍니다. 표를 칸으로 나누지 않습니다.

# 4.6.0 — 2026-10-01

기존 도구 5개의 동작과 출력은 바꾸지 않고 도구 2개를 추가했습니다.

- `law_search`: 공식 검색 목록(법령 시행일별 판본, 행정규칙 현행·연혁)을 그대로 돌려줍니다. 판본 선택은 호출자 몫입니다.
- `admin_rule_article`: 행정규칙 조문을 법령과 같은 조·항·호·목 구조로 돌려줍니다. 공식 응답이 행정규칙 조문을 통짜 글로 주므로 ①·1.·가. 표시로 나누고, 나눈 조각이 원문을 바이트 단위로 재조립하지 못하면 실패로 반환합니다. 같은 분해기를 법령 조문에 적용하면 공식 항·호 구조와 일치함을 시험으로 고정했습니다.
- 공식 검색 응답은 상세 링크에 API 이용값을 그대로 담아 돌려줍니다. 수신 직후 이용값을 가려 기록·반환합니다.

# 4.5.0 — 2026-09-28

기존 v4.4 조회와 인계 ZIP의 최소 확장을 하나의 실행 프로젝트로 통합했습니다.

- 자료 종류별 공식 조회, 문서·판본·시행일 확인, 기관규칙 세부 종류 미확정 시 호출 차단을 추가했습니다.
- 법령키와 조회 일련번호를 구별하며 판본 확인값 누락·불일치 시 근거 사용을 막습니다.
- 비법률 자료의 본문, 부칙, 별표, 원문 구조와 미조회 첨부 후보를 보존합니다. 번호·발령일·링크를 규범 본문으로 승격하지 않습니다.
- 문맥 조립기를 별도 모듈로 구현했습니다. 호출자는 필요한 대상·역할·탐색·시간 확인 결과를 입력하고 조립기는 원문 전달 상태만 반환합니다.
- 같은 원문 요청의 통신은 한 번으로 합치며 원래 관측과 서로 다른 근거 역할을 보존합니다.
- 공식 SDK 2.2.0 기반 표준 입출력 서버, 설치 패키지, 명시적 원문 재생과 실패 응답을 보존하는 제한 재시도를 제공합니다.
- 공식 API 신규 응답, 원문 재생·실제 MCP 연결·독립 설치 결과를 검증 기록으로 남깁니다.

이전 폴더·ZIP과 초기 인계 실패 기록은 보존했습니다. 법적 의미 판단과 최종 의미 검토의 승인은 별도 분석기의 책임입니다.
