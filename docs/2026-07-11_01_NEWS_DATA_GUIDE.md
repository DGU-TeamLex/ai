# 뉴스 데이터 준비 가이드

WeP-Stock 뉴스 리스크 학습에는 모델 학습 및 검증 기간과 겹치는 과거 뉴스가 필요합니다.
현재 샘플 뉴스는 배치 동작 확인용이며 모델 성능 검증에는 사용하지 않습니다.

## 사용자 준비 항목

1. 2018-01-01부터 2025-12-31까지 사용 가능한 뉴스 원천을 확정합니다.
2. 아래 CSV 계약으로 기사 메타데이터를 준비합니다.
3. 실제 품목명, 품목군, 원자재 관계를 확인할 담당자를 정합니다.
4. 자동 분석된 기사 중 이벤트 유형별 표본을 검수합니다.

API 키와 비밀값은 저장소 파일이나 대화에 입력하지 않고 로컬 `.env`에서 관리합니다.

## CSV 계약

필수 컬럼:

```text
date,title
```

권장 전체 컬럼:

```text
date,title,summary,source,country,url
```

- `date`: 기사 발행일, `YYYY-MM-DD` 권장
- `title`: 기사 제목
- `summary`: 기사 요약 또는 본문 일부
- `source`: 언론사 또는 공식기관 이름
- `country`: 사건 영향 국가. 모르면 `Unknown`
- `url`: 원문 URL. 중복 제거 식별자로 사용

같은 URL은 한 건만 남깁니다. URL이 없으면 `date + source + title` 조합으로 중복을 제거합니다.

## 실행

`.env`를 사용하는 실행 환경에서 다음 값을 설정합니다.

```text
NEWS_PROVIDER=csv
NEWS_DATA_PATH=data/raw/news/news_history.csv
```

현재 애플리케이션은 환경 변수를 직접 읽습니다. 셸에서 실행할 때도 동일한 이름을 지정할 수 있습니다.

```bash
NEWS_PROVIDER=csv NEWS_DATA_PATH=data/raw/news/news_history.csv \
python -m src.news.news_risk_scorer
```

결과:

```text
outputs/news_risk_scores.csv
outputs/news_article_scores.csv
```

`news_article_scores.csv`에는 최종 점수뿐 아니라 사건 유형, 중복 수, 출처·심각도·연관성·시간 감쇠 등 계산 근거가 기록됩니다.

## 수집원 선정 기준

- 과거 기사 접근 범위가 2018년부터 이어지는가
- 전체 기사량 또는 월별 모수가 제공되는가
- 원문 URL, 발행일, 출처, 제목을 안정적으로 제공하는가
- 한국어 기사 또는 번역 검색을 지원하는가
- 연구·서비스에서 재사용 가능한 라이선스인가

GDELT는 글로벌 기사 검색과 전체 기사 대비 보도량 정규화에 유용하지만, 기사 목록 API의 반환량 제한이 있으므로 단독 원천으로 간주하지 않습니다.

## GDELT 수집

GDELT DOC API는 API 키 없이 사용할 수 있습니다. 수집기는 긴 기간을 월 단위로
나누며, **`split` 모드만 지원합니다** — 감염병·의료물품·원자재 세 카테고리를 각각
조회한 뒤 로컬 분석기가 사건 유형을 분류합니다. 결과는 `NEWS_DATA_PATH`에
캐시되며 `NEWS_REFRESH=true`일 때만 다시 수집합니다. 호출 제한이 발생하면 자동으로
대기 후 재시도하고, 월·쿼리별 체크포인트에서 이어서 수집합니다.

```text
NEWS_PROVIDER=gdelt
NEWS_DATA_PATH=data/raw/news/gdelt_history.csv
NEWS_START_DATE=2018-01-01
NEWS_END_DATE=2025-12-31
NEWS_REFRESH=false
GDELT_MAX_RECORDS=250
GDELT_QUERY_MODE=split
GDELT_REQUEST_DELAY_SECONDS=5.0
GDELT_MAX_RETRIES=8
GDELT_RATE_LIMIT_BACKOFF_SECONDS=30.0
GDELT_MAX_BACKOFF_SECONDS=300.0
```

기사 목록은 한 요청에 최대 250건이므로 캐시는 전체 뉴스 모집단이 아니라 위험 기사
후보군입니다. 전체 기사 대비 보도량 정규화 데이터는 별도 단계에서 GDELT timeline
volume으로 수집합니다.

### `combined` 모드는 제거되었습니다 (ai#22)

요청 수를 72 → 24로 줄이려고 세 카테고리를 하나의 OR 질의로 합쳤으나, GDELT가
**첫 요청부터** 질의 길이 초과로 거부합니다.

```
Your query was too short or too long.

combined  risk_candidate   261자  → 거부
split     raw_material     172자  → 정상
```

rate limit이 아니라 질의 구성 문제라 재시도해도 풀리지 않습니다. 도달은 하지만
항상 실패하는 경로였으므로 삭제했고, `GDELT_QUERY_MODE=combined`를 지정하면
조용히 `split`으로 넘어가지 않고 **명시적으로 `ValueError`가 발생합니다**.

키워드를 임의로 쳐내 길이를 줄이는 방식은 택하지 않았습니다. recall 평가 없이
줄이면 수집 대상이 조용히 작아집니다. 고정 2분할도 최종 규칙으로 두지 않습니다.
후속 과제는 감염병·의료용품·원자재라는 **감사 가능한 의미 축은 유지한 채**, 각
카테고리 내부의 OR 검색어를 검증 가능한 크기의 여러 `query_id`로 **동적 분할**하는
것입니다.

질의 길이 거부는 `GDELTPermanentQueryError`로 분리되어 **재시도하지 않습니다**.
종전에는 `GDELTTransientResponseError`로 잡혀 재시도 횟수를 그대로 소진했습니다.
429·5xx·timeout만 backoff 대상입니다.

> GDELT 공식 문서에 정확한 문자 수 상한이 명시되어 있지 않습니다. 위 261/172는
> 재현된 관측이지 시스템 계약이 아닙니다. URL 인코딩 길이와 연산자 구성도
> 영향을 줍니다. 참고: <https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/>

기존 DOC API가 지속적으로 `429`를 반환하면 `gdelt_ngram` 공급자를 사용합니다. 이
공급자는 GDELT가 공개한 최신 Web NGrams 파일과 기사 TOC를 내려받고, 주제어와 위험
문맥어가 모두 확인된 문서만 제목·URL 기반 뉴스 후보로 저장합니다. 이 방식은 최신
위험 감시용이며 과거 기간 기사 검색을 대체하지는 않습니다.

```text
NEWS_PROVIDER=gdelt_ngram
NEWS_DATA_PATH=data/raw/news/gdelt_ngram_latest.csv
NEWS_REFRESH=true
GDELT_NGRAM_BATCH_COUNT=4
GDELT_NGRAM_LOOKBACK_MINUTES=45
GDELT_NGRAM_LANGUAGES=en,ko
GDELT_NGRAM_APPEND_CACHE=true
```

`GDELT_NGRAM_APPEND_CACHE=true`는 새 배치를 기존 캐시에 누적합니다. 필터 규칙 변경 후
원본 배치를 재처리할 때는 일시적으로 `false`로 설정해 이전 판정 결과를 교체합니다.
