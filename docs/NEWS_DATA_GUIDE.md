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

GDELT DOC API는 API 키 없이 사용할 수 있습니다. 수집기는 긴 기간을 월 단위로 나누고 감염병, 의료물품 공급, 원자재 카테고리를 각각 조회합니다. 결과는 `NEWS_DATA_PATH`에 캐시되며 `NEWS_REFRESH=true`일 때만 다시 수집합니다. 호출 제한이 발생하면 자동으로 대기 후 재시도하고, 월·카테고리별 체크포인트에서 이어서 수집합니다.

```text
NEWS_PROVIDER=gdelt
NEWS_DATA_PATH=data/raw/news/gdelt_history.csv
NEWS_START_DATE=2018-01-01
NEWS_END_DATE=2025-12-31
NEWS_REFRESH=false
GDELT_MAX_RECORDS=250
```

기사 목록은 한 요청에 최대 250건이므로 캐시는 전체 뉴스 모집단이 아니라 위험 기사 후보군입니다. 전체 기사 대비 보도량 정규화 데이터는 별도 단계에서 GDELT timeline volume으로 수집합니다.
