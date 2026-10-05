# Legal Thin MCP 4.9.0

공식 한국 법령 자료를 정확한 판본과 원문 구조로 전달하는 실행 가능한 프로젝트입니다. MCP는 모델이 외부 조회 도구를 호출하는 연결 규약입니다. 법적 의미와 필요한 참조의 선택은 이 프로젝트를 사용하는 별도 분석기가 맡습니다.

현재 실행 기준은 이 폴더의 `src/`, `tests/`, `pyproject.toml`입니다. 이 저장소는 4.8.0에서 공개를 시작했고 4.9.0에서 문서 단위 참조 묶음을 추가했습니다. 과거 커밋과 인계 ZIP은 원래 로컬 프로젝트에 보존했습니다.

## 설치와 실행

Python 3.10 이상에서 프로젝트 폴더를 작업 위치로 사용합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
$env:LAW_API_OC = '발급받은 국가법령정보 API 값'
.\.venv\Scripts\python.exe -X utf8 -m law_mcp
```

서버는 표준 입출력으로 MCP 메시지를 교환하므로 직접 실행하면 연결 요청을 기다립니다. 연결 설정 예시는 `samples/mcp_config.json`입니다. 기존 Codex 설정을 자동 변경하지 않습니다.

실행 중 원문과 실패 응답은 기본적으로 사용자 폴더의 `.legal-thin-mcp/captures`에 보존합니다. `LAW_MCP_CAPTURE_DIR` 또는 `--capture-dir`로 위치를 지정할 수 있습니다. API 값은 요청에만 넣고 기록에는 넣지 않습니다. 저장 응답은 재현 근거이며 응답 캐시로 사용하지 않습니다.

## 제공 도구

| 도구 | 전달하는 자료 |
|---|---|
| `law_article` | 지정 법령 판본의 조·항·호·목과 공식 식별값 |
| `law_references` | 공식 참조 관측값과 제공 순서; 법적 관계의 승인 결과는 아님 |
| `law_reference_bundle` | 법령 한 판본의 전체 본문 명시 조문참조와 `lsDelegated` 관측을 합쳐 outgoing·같은 문서 reverse index를 반환. `focus`로 1~3 hop 경로 선택 가능 |
| `source_fragment` | 법령·행정규칙·자치법규·조약·기관규칙의 판본 입력에 따른 공식 원문 |
| `context_packet` | 호출자가 선택한 원문만 별도 문맥 조립기로 수집한 묶음 |
| `validate_context_packet` | 문맥 묶음의 식별값·내용 해시·역할·누락·시간 입력 검사 |
| `law_search` | 공식 검색 목록. `eflaw`(법령, 시행일별 판본: 연혁·현행·시행예정) 또는 `admrul`(행정규칙, `include_history`로 연혁 포함). 후보 목록일 뿐 판본을 고르지 않음 |
| `admin_rule_article` | 판본 확인된 행정규칙의 조문을 `law_article`과 같은 조·항·호·목 구조로 반환. 제공처가 통짜 글로 주는 조문을 ①·1.·가. 표시로 나누고 원문과 바이트 단위 재조립을 검사함 |
| `law_supplements` | 판본 확인된 법령의 부칙(시행일·적용례·경과조치)과 별표를 원문 그대로 반환. 판본 확인은 `law_article`과 같음. 부칙은 줄 머리 '제N조(' 표시로 조를 나누고 조 안을 조·항·호·목 구조로 덧붙이며 바이트 재조립을 검사함. 별표는 표를 칸으로 나누지 않음 |
| `decision_document` | 개인정보보호위원회 의결서(`ppc`) 또는 법원 판례(`prec`) 한 건을 제공처 칸 그대로 반환. 요청 일련번호와 응답 대조. 위반 조항·금액은 본문 글 안에 있으며 뽑지 않음. 검색은 `law_search`(target=ppc/prec, `search_body`로 본문 검색) |

`source_fragment`는 `version_binding`을 받습니다. 이는 별도 판본 확인기가 정한 공식 조회 번호와 판본 확인값을 묶은 입력입니다. `observed_identity`는 원래 참조의 관측값으로 보존하며 조회할 판본으로 자동 사용하지 않습니다.

법령 XML의 `법령키`는 MST 조회 일련번호와 다른 값입니다. `law_article`의 `expected_law_key`는 별도 확인된 공식 판본 값이어야 합니다. 응답의 시행일과 판본 확인값이 일치해야 근거 사용 가능 상태를 반환합니다. 비법률 자료의 ID/LID도 가족별 공식 식별값과 판본 일련번호를 대조합니다. 발령일·공포일·서명일을 시행일로 바꾸지 않습니다.

자료가 없거나 판본·시행일·세부 종류·요청 위치가 확인되지 않으면 상태값을 반환하고 `support_eligible=false`로 둡니다. 이 값은 해당 자료를 확정 근거로 사용하면 안 된다는 뜻입니다. 부칙·별표·표의 원문과 구조는 보존하며, 첨부파일 링크만 있고 원문이 없으면 누락을 표시합니다.

문맥 조립기는 의미상 필요성을 판단하지 않습니다. 호출자가 `selected_needs`, 근거 역할, 탐색 완료 여부와 시간 확인 결과를 전달합니다. `READY`는 이 입력을 바탕으로 한 원문 전달 상태이며 법적 결론이나 `RESOLVED` 승인이 아닙니다. 같은 요청은 한 번의 조립 안에서만 합치고 원래 관측 기록은 모두 남깁니다.


### 문서 단위 참조 묶음

`law_reference_bundle`은 비싼 전체 본문 조회와 참조 조회를 한 번씩 실행한 뒤 그 결과를 한 묶음으로 재사용하기 위한 도구입니다.

현행본은 안정 법령 ID만 지정합니다.

```json
{
  "selector": {"mode": "current", "law_id": "011357"},
  "text_mode": "referenced_units",
  "focus": {"targets": [{"article": 29}], "max_depth": 2}
}
```

특정 과거·미래 판본은 제공처 조회 일련번호(MST), 시행일과 함께 독립 판본 확인값을 넣어야 합니다.

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

`text_mode`은 `graph_only`, `referenced_units`(기본), `full_document` 중 하나입니다. 전체 경로를 미리 펼치지 않고 양방향 index를 항상 돌려주며, `focus`가 있을 때만 1~3 hop 경로를 계산합니다.

본문 보충 추출은 같은 문서의 **기계적으로 확정 가능한 명시 조문 locator**만 대상으로 합니다. `「형법」 제355조`, `법 제29조`, `같은 법 제29조`처럼 다른 문서 또는 선행 문맥이 필요한 표현은 같은 문서 edge로 추측하지 않고 unresolved 관측으로 남깁니다. `coverage.external_incoming=NOT_SEARCHED`이면 전국 역인용을 모두 찾았다는 뜻이 아닙니다. 특정 MST의 `lsDelegated`가 역사 시점에 완전하다는 보장도 하지 않습니다.

세부 계약과 제한은 [참조 묶음 계약](docs/REFERENCE_BUNDLE.md) 및 [알려진 제한](KNOWN_LIMITATIONS.md)을 봅니다.

## 원문 재생과 검사

```powershell
.\.venv\Scripts\python.exe -X utf8 -m law_mcp --fixture-manifest samples/replay_manifest.json --fixture-root .
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -X utf8 -m mypy src/law_mcp
.\.venv\Scripts\python.exe -X utf8 -m ruff check src/law_mcp
```

`--fixture-manifest`는 저장한 원문을 요청·해시와 대조해 다시 읽는 명시적 재생 모드입니다. 기본적으로 manifest 폴더 안의 파일만 읽으며, 예제처럼 원문이 옆 폴더에 있으면 `--fixture-root`로 허용할 루트 폴더를 지정합니다. 실제 조회 실패 시 이 모드로 자동 전환하지 않습니다.

## 기준 자료

시험용 공식 원문은 `tests/fixtures/`에 포함합니다. 원문 재생 예제에 필요한 10개 파일은 `tests/fixtures/replay/`에 있으며 `samples/replay_manifest.json`의 내용 해시와 대조합니다. 과거 검증 보고서와 인계 패키지는 원래 로컬 프로젝트에 보존하며 이 저장소에 포함하지 않습니다. 저장 원문을 이용한 시험 성공은 현재 공식 서비스 조회나 법적 의미 정확성의 승인을 뜻하지 않습니다.

SDK 사용 근거: [공식 Python SDK 시작 문서](https://py.sdk.modelcontextprotocol.io/get-started/first-steps/), [실행 문서](https://py.sdk.modelcontextprotocol.io/run/). 제공기관 필드 근거: [국가법령정보 공식 행정규칙 조회 안내](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=admrulInfoGuide).
