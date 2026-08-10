# 파이프라인 실행 런북

새 기기에서 원장부터 예측까지 처음 돌릴 때, 또는 중단한 작업을 다른 기기에서
이어받을 때 보는 문서다. README 의 Batch Run 은 명령 나열이고, 여기에는
**빠뜨리면 조용히 잘못된 결과가 나오는 지점**과 그 확인법을 적는다.

마지막 갱신: 2026-08-10

## 0. 가장 많이 놓치는 것 두 가지

### 일괄승인을 돌리지 않으면 커버리지가 0.94% 로 주저앉는다

`src/config.py` 에 경로 전환 스위치가 있다.

```python
if _bulk_approval_is_active():        # 마커 + bulk parquet 3종이 모두 있어야 True
    ITEM_FAMILY_TAXONOMY_PATH = ITEM_BULK_TAXONOMY_PATH
    APPROVED_ITEM_CLASSIFICATION_PATH = ITEM_BULK_CLASSIFICATION_PATH
    STOCK_MATERIAL_MAPPING_PATH = ITEM_BULK_MATERIAL_MAPPING_PATH
else:
    ...  # seed 로 떨어진다
```

seed 택소노미는 **78행(승인 60조합·family 10개)** 이다. 여기로 떨어지면
`inventory_status` 의 분류 커버리지가 **0.94%** 가 되고, 긴급부족 판정도
753건에서 10건으로 줄어든다. **에러가 나지 않아서 알아채기 어렵다.**

```bash
python -m src.item_bulk_approval --apply
```

`--apply` 가 없으면 parquet 은 쓰지만 마커를 만들지 않아 전환되지 않는다.
돌린 뒤 반드시 확인한다.

```bash
python -c "
from src.config import ITEM_FAMILY_TAXONOMY_PATH as T, ITEM_BULK_APPROVAL_MARKER_PATH as M
print('marker', M.exists(), '| taxonomy ->', T.name)"
# 기대: marker True | taxonomy -> item_family_taxonomy_bulk_approved.parquet
```

| | seed | bulk |
|---|---:|---:|
| 택소노미 | 78행 | 438,446행 |
| 승인 로컬품목 | 4,346 | 505,669 |
| 분류 커버리지 | 0.94% | 100% |
| 원자재 매핑 | 없음 | 634,655행 |

일괄승인은 **후보 수용이지 사실 검증이 아니다.** 자동발주는 외부근거 승인분
4,346건에만 열려 있고 이 값은 일괄승인 후에도 바뀌지 않는다.

주의할 점이 둘 더 있다.

**반드시 별도 프로세스로 먼저 돌려야 한다.** 위 전환 코드는 `src/config.py`
모듈 최상단에 있어 **import 시점에 한 번만 평가된다.** 같은 파이썬 프로세스
안에서 일괄승인을 돌리고 이어서 다른 단계를 호출하면, 이미 import 된 config
는 여전히 seed 경로를 들고 있다.

**`python -m src.main` 에는 이 단계가 없다.** 현재 배치는
`preprocessing → inventory_status → news → ...` 순이라 표준화·일괄승인을
거치지 않는다. 배치만 돌리면 seed 경로로 동작한다.

### 외부 신호가 전부 0이면 예측 산출 자체가 실패한다

뉴스·원자재가격·Module C 가 모두 0이면 `training.py` 가 model B/C/D 를
건너뛰고, 앙상블 정책이 `stock_model_d_module_c_pred` 를 요구해서 이렇게 죽는다.

```
Skipping stock_model_b_news: news risk features contain no non-zero observations
ValueError: Forecast ensemble model columns are missing: ['stock_model_d_module_c_pred']
```

뉴스는 성능 옵션이 아니라 **구조적 필수 입력**이다. 4절을 먼저 끝내야 한다.

## 1. 준비

```bash
git clone https://github.com/DGU-TeamLex/ai.git && cd ai
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**pandas 3.x 에서는 `item_normalization` 이 죽는다**(`make_na_array` AssertionError).
`requirements.txt` 가 상한을 두지 않으므로 설치 후 확인한다.

```bash
python -c "import pandas; print(pandas.__version__)"   # 2.x 여야 한다
```

macOS 의 python.org 빌드는 시스템 인증서를 읽지 않아 외부 수집이 전부
`CERTIFICATE_VERIFY_FAILED` 로 실패한다. 인증키 문제가 아니다.

```bash
export SSL_CERT_FILE="$(python -c 'import certifi;print(certifi.where())')"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
```

## 2. 원장 배치

원장 원본은 레포에 없다(`data/raw/`, `raw_stock/` 은 gitignore). 팀 보관처에서
받아 `raw_stock/` 에 둔다. 2018~19 보조 원장도 함께 쓰면 학습 구간이 넓어진다.

```bash
python -m src.preprocessing
python -c "
import pandas as pd
d = pd.read_parquet('data/processed/stock_monthly.parquet', columns=['stock_item_key'])
print(f'{len(d):,}행  고유 시리즈 {d.stock_item_key.nunique():,}')"
```

## 3. 품목 표준화 → 일괄승인

```bash
python -m src.item_integrated_pipeline --with-excel --sample-size 1000
python -m src.item_bulk_approval --apply          # 0절 참고 — 빠뜨리면 안 된다
```

## 4. 외부 신호

### 뉴스 (필수)

전용 런북이 있다: [`pipelines/news_collection/README.md`](../pipelines/news_collection/README.md)

요약하면 이렇다.

```bash
# 이미 받아둔 수집분을 복원한다 (네트워크 요청 0회)
python -m pipelines.news_collection.sync_checkpoints restore
python -m pipelines.news_collection.sync_checkpoints status

# 나머지를 받는다. 429 를 견디며 이어받는다
export GDELT_REQUEST_DELAY_SECONDS=30
export GDELT_RATE_LIMIT_BACKOFF_SECONDS=60
export GDELT_MAX_BACKOFF_SECONDS=900
python -m pipelines.news_collection.collect_full_window

# 받은 만큼 레포에 되돌려 다음 기기가 이어받게 한다
python -m pipelines.news_collection.sync_checkpoints export
```

**GDELT 제한은 IP 단위다.** 4~5요청이 통과한 뒤 긴 429 벽이 오고, 시간당
7~8개꼴이라 24개월 x 3분할 = 72요청에 8시간 이상 걸린다. 기기를 바꾸면
제한이 새로 시작하므로, 한 기기에서 막히면 다른 기기에서 이어받는 편이 빠르다.

모델 평가만 목적이라면 검증(2025 Q1/Q2)·홀드아웃(2025-10~12)이 전부
2025년이므로 2025년만 받아도 된다 — 36요청이라 절반이다.

```bash
NEWS_START_DATE=2025-01-01 NEWS_END_DATE=2025-12-31 \
  python -m pipelines.news_collection.collect_full_window
```

스코어링은 요청 없이 캐시만 읽는다.

```bash
export NEWS_PROVIDER=gdelt NEWS_REFRESH=false
export NEWS_START_DATE=2024-01-01 NEWS_END_DATE=2025-12-31
export NEWS_DATA_PATH=data/external/news_gdelt_cache.csv
export NEWS_ARTICLE_SCORE_FORMAT=parquet          # 6절 디스크 참고

python -m src.news.news_risk_scorer
```

### 원자재가격 (선택 — 키 필요)

`COMMODITY_PROVIDER` 기본값이 `disabled` 라 켜지 않으면 `commodity_risk` 가
0으로 남는다. FRED / Alpha Vantage 무료 키가 필요하다.

```bash
export COMMODITY_PROVIDER=fred        # 또는 alpha_vantage
python -m src.commodity.commodity_risk_scorer
```

### 수출입 (선택 — 참조표 필요)

`data/external/trade/reference/관세청_HS부호_20260101.xlsx` 가 있어야 한다.
없으면 `blocked_missing_hsk_reference` 로 0행이 나온다.

```bash
python -m src.trade.hsk_reference
python -m src.trade.trade_risk_scorer
```

## 5. 모듈 C → 학습 → 예측

```bash
python -m src.module_c.pipeline
python -m src.feature_engineering
```

여기서 외부 신호가 실제로 들어왔는지 확인하고 넘어간다. 0이면 학습이 실패한다.

```bash
python -c "
import pandas as pd
cols = ['disease_news_risk','supply_news_risk','material_news_risk',
        'total_news_risk','commodity_risk','module_c_total_risk']
d = pd.read_parquet('outputs/stock_feature_table.parquet', columns=cols)
for c in cols:
    nz = (d[c] != 0).sum()
    print(f'  {c:<24} 비0 {nz:>10,} ({nz/len(d)*100:6.2f}%)')"
```

```bash
python -m src.modeling.historical_weight_tuning --apply
python -m src.modeling.training
python -m src.modeling.prediction
python -m src.modeling.temporal_ensemble_tuning --apply
python -m src.modeling.prediction          # 적용된 혼합 가중치로 재생성
python -m src.modeling.classified_prediction
python -m src.modeling.evaluation
python -m src.modeling.inventory_status
```

## 6. 디스크

두 곳에서 크게 쓴다. 여유가 4GB 미만이면 단계 사이에 지운다.

**기사별 감사 산출물** — 기사 1건당 감사행이 3천 개 남짓(기사 x 매칭 품목)이라
수집 기간에 정비례한다.

| 기간 | 기사 | CSV | parquet+zstd |
|---|---:|---:|---:|
| 1개월 | 745건 | 1,220 MB | **8.2 MB** |
| 24개월(추정) | ~18,000건 | ~28.8 GB | **~0.19 GB** |

`NEWS_ARTICLE_SCORE_FORMAT=parquet` 을 주면 후자로 쓴다. 기본값은 `csv` 다.

**모듈 C 감사 CSV** — 약 1.9GB. 전부 재생성 가능하다.

```bash
rm -f outputs/module_c_material_exposure_candidates.csv \
      outputs/module_c_supply_risk_level_audit.csv \
      outputs/module_c_supply_risk_quality_{classified,issues,quarantine,review}.csv
```

## 7. 마지막 실행 결과 (2026-08-10)

일괄승인을 적용하고 뉴스 2024-01 한 달치를 넣은 상태의 값이다. 재현 시 비교용.

| 산출물 | 행수 |
|---|---:|
| `stock_predictions.csv` | 163,229 |
| `stock_backtest_predictions.csv` | 605,437 |
| `stock_news_risk_scores.csv` | 326,429 |
| `stock_module_c_risk_scores.csv` | 326,428 |
| `stock_inventory_status.csv` | 416,128 |

백테스트 WAPE 다.

| 모델 | WAPE | BIAS_PCT |
|---|---:|---:|
| temporal_ensemble | 36.974 | −5.01 |
| stock_model_b_news | 37.276 | −1.89 |
| stock_model_a_usage_tweedie | 37.297 | −1.85 |
| stock_model_d_module_c | 37.310 | −1.79 |
| stock_model_a_usage_only | 38.040 | −12.46 |
| baseline_rolling_mean_3 | 40.021 | −6.04 |

**이 수치로 뉴스 효과를 결론지으면 안 된다.** 뉴스가 24개월 중 1개월치
(피처 기준 2.35%)뿐이다. 뉴스 모델과 tweedie 변형의 차이가 0.02%p 인데,
usage-only 대비 0.76%p 개선분은 대부분 목적함수에서 온 것으로 보인다.
전 구간 수집 후 다시 재야 한다.

리드타임은 모델 입력 피처가 아니므로(`get_model_feature_columns()` 에 없다)
예측 정확도와 무관하다. 재고정책(결품률·재고수준)에만 작용한다.

## 8. 알려진 제약

- **`GDELT_QUERY_MODE=combined` 은 항상 실패한다** — 질의 261자를 GDELT 가
  거부한다(`split` 은 최대 172자). 이슈 #22
- `requirements.txt` 에 pandas 상한이 없다 — 1절
- macOS python.org 빌드 SSL — 1절
- `item_alias_to_product_v1.parquet` 에 대표품목이 서로 다른 중복 49키가 있다.
  흡수하면 제품이 사라지므로 quarantine 설계가 필요하다
