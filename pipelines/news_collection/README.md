# 뉴스(GDELT) 수집 러너

뉴스 위험 신호를 학습·검증 구간 전체에 대해 수집하고, 그 결과를 기기 간에
이어받을 수 있게 하는 도구다.

## 왜 별도 러너가 필요한가

`src.news.news_risk_scorer` 를 그냥 돌리면 세 가지에서 막힌다.

**1. GDELT 는 IP 단위로 요청을 강하게 제한한다.**
실측하면 4~5요청이 통과한 뒤 긴 429 벽이 온다. 24개월 x 3분할 = 72요청을 다
받는 데 8시간 이상 걸린다. 스코어러 한 번 호출로는 중간에 죽는다.

```
17:07  17:08  17:09  17:10   ← 4개 통과
17:11~ 429 지속 (백오프 60→120→240→480→900초)
```

**2. `GDELT_QUERY_MODE=combined` 은 쓸 수 없다.**
요청을 72 → 24 로 줄이려고 만들어진 선택지지만, 질의가 261자라 GDELT 가
길이를 이유로 거부한다. `split` 은 최대 172자라 통과한다. 상한은 그 사이에 있다.

```
Your query was too short or too long.
```

재시도해도 결과가 같으므로 이 경로는 항상 실패한다. 자세한 내용과 수정
방향은 이슈 #22 에 남겼다.

**3. 수집 결과가 `.gitignore` 된 곳에만 남는다.**
`data/external/` 은 무시 대상이라 기기를 옮기면 8시간을 다시 써야 한다.
같은 입력으로 같은 WAPE 가 나오는지 확인하려면 기사 원본이 고정돼야 한다는
점에서도 곤란하다.

## 구성

| 파일 | 역할 |
|---|---|
| `collect_full_window.py` | 429 를 견디며 전 구간이 찰 때까지 재시도하는 수집 드라이버 |
| `sync_checkpoints.py` | 체크포인트 ↔ `data/handoff/news_gdelt_articles.parquet` 왕복 |

체크포인트는 `(월 x 카테고리)` 단위로 즉시 저장되고 다음 호출에서 건너뛴다.
그래서 죽어도 재실행하면 이어받는다.

## 쓰는 법

### 진행률 확인

```bash
python -m pipelines.news_collection.sync_checkpoints status
```

### 다른 기기에서 이어받기

레포에 올라온 handoff parquet 을 체크포인트로 되돌린다. 이미 있는 파일은
건드리지 않는다.

```bash
python -m pipelines.news_collection.sync_checkpoints restore
```

### 수집

```bash
export SSL_CERT_FILE="$(python3 -c 'import certifi;print(certifi.where())')"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"

export GDELT_REQUEST_DELAY_SECONDS=30      # 429 를 덜 유발한다
export GDELT_RATE_LIMIT_BACKOFF_SECONDS=60
export GDELT_MAX_BACKOFF_SECONDS=900
export GDELT_MAX_RETRIES=10

python -m pipelines.news_collection.collect_full_window
```

기간을 좁히려면 `NEWS_START_DATE` / `NEWS_END_DATE` 를 준다. 모델 평가만
목적이라면 검증(2025 Q1/Q2)·홀드아웃(2025-10~12)이 모두 2025년이므로
2025년만 받아도 된다 — 36요청이라 시간이 절반이다.

```bash
NEWS_START_DATE=2025-01-01 NEWS_END_DATE=2025-12-31 \
  python -m pipelines.news_collection.collect_full_window
```

### 수집 결과를 레포에 올리기

```bash
python -m pipelines.news_collection.sync_checkpoints export
git add data/handoff/news_gdelt_articles.parquet
```

`data/handoff/` 는 "재생성 비용이 크고 정의 고정이 필요한" 산출물을 두는
자리이므로 여기에 넣는다. 기사 메타데이터(날짜·제목·출처·국가·URL)만 담기며
원장 데이터는 들어가지 않는다.

### 스코어링 → 파이프라인

수집이 끝나면 요청 없이 캐시만 읽어 점수를 만든다.

```bash
export NEWS_PROVIDER=gdelt
export NEWS_START_DATE=2024-01-01
export NEWS_END_DATE=2025-12-31
export NEWS_DATA_PATH=data/external/news_gdelt_cache.csv
export NEWS_REFRESH=false
export NEWS_ARTICLE_SCORE_FORMAT=parquet     # 아래 "디스크" 참고

python -m src.news.news_risk_scorer
python -m src.module_c.pipeline
python -m src.feature_engineering
python -m src.modeling.training
python -m src.modeling.prediction
```

## 디스크

기사별 감사 산출물이 크다. 기사 1건당 감사행이 3천 개 남짓(기사 x 매칭 품목)
이라 수집 기간에 정비례한다.

| 기간 | 기사 | `stock_news_article_scores` CSV | parquet+zstd |
|---|---:|---:|---:|
| 1개월 | 745건 | 1,220 MB | **8.2 MB** |
| 24개월(추정) | ~18,000건 | ~28.8 GB | **~0.19 GB** |

`NEWS_ARTICLE_SCORE_FORMAT=parquet` 을 주면 후자로 쓴다(왕복 무손실 확인).
기본값은 `csv` 라 기존 산출물 정의는 그대로다.

모듈 C 도 감사용 CSV 를 1.9GB 가량 쏟아낸다. 단계 사이에 지우지 않으면
디스크가 찬다.

## 알려진 제약

- **combined 모드 불가** — 위 2번. 이슈 #22
- **원자재가격 축** — `COMMODITY_PROVIDER` 기본값이 `disabled` 이고 FRED /
  Alpha Vantage 키가 필요하다. 키가 없으면 `commodity_risk` 가 0으로 남는다
- **수출입 축** — `관세청_HS부호_20260101.xlsx` 가 있어야 한다
- 외부 신호가 전부 0이면 `training.py` 가 model B/C/D 를 건너뛰고, 앙상블이
  `stock_model_d_module_c_pred` 를 요구해 **예측 산출 자체가 실패**한다.
  뉴스는 성능 옵션이 아니라 구조적 필수 입력이다
