# 외부 데이터 캐시 — 무엇이 들어 있고 왜 레포에 두는가

원장(`raw_stock/`)과 **성격이 다른** 데이터를 모아 둔 곳이다. 여기 있는 것은 전부
**공공 API·공개 아카이브** 에서 받은 것이라 공유에 계약상 제약이 없다.

> ⚠️ `raw_stock/`, `data/handoff/` 는 한국사회보장정보원 제공 자료에서 나온 것이다.
> 이 문서가 다루는 범위가 아니며 공유 전 계약 확인이 필요하다.

## 왜 레포에 두나 — 크기가 아니라 재수집 비용

전부 합쳐 **35MB** 다. 문제는 크기가 아니라 다시 받는 비용이다.

| 데이터 | 재수집 비용 |
|---|---|
| 조달청 납품요구 | **524 요청**, 서비스키당 하루 1,000 쿼터 → 팀원마다 받으면 며칠 |
| GDELT 뉴스 | **수 시간** (15분 단위 아카이브 파일 순회) |
| 관세청 무역 | 서비스키 필요, 증분 수집 |
| 원자재 가격 | Alpha Vantage 키 필요, 분당 호출 제한 |

캐시가 있으면 **API 키 없이도 전 파이프라인이 돌아간다.**

```bash
git clone https://github.com/DGU-TeamLex/ai
cd ai
python -m src.commodity.commodity_risk_scorer    # 키 없이 실행됨
python -m src.module_c.pipeline
```

`.env` 에 `COMMODITY_REFRESH=false`(기본값)면 캐시를 쓴다. `true` 로 두면 원격
재수집을 시도하고, 응답이 비면 상류 실패이므로 `guard_not_empty` 가 막는다.

---

## 1. 조달청 나라장터 납품요구

```
data/processed/procurement_delivery_requests.jsonl        32,346건
data/processed/procurement_collection_progress.json       수집 완료 월 기록
```

| 항목 | 값 |
|---|---|
| 원천 | data.go.kr 「조달청_나라장터 납품요구현황」 |
| 기간 | 2024-01-02 ~ 2025-12-30 (24개월 전수) |
| 범위 | 지역보건의료기관 301곳 (`dminsttNm` 이 보건소/보건지소/보건진료소/보건의료원) |
| 수집기 | `src/procurement/lead_time_collector.py` |

핵심 필드다.

| 필드 | 의미 |
|---|---|
| `dlvrReqRcptDate` | 납품요구 접수일 = **발주 시점** |
| `maxDlvrTmlmtDate` | 납품기한 |
| `rprsntDtilPrdctClsfcNo` | 세부품명번호 (UNSPSC 확장 10자리) |
| `dminsttCd` | 수요기관 코드 |

**리드타임 `L_계약 = maxDlvrTmlmtDate − dlvrReqRcptDate` 의 유일한 원천이다.**

```
유효 31,898건   p25 30  median 30  p75 30  p90 60
```

⚠️ **`L_계약` 은 계약상 납기이지 실제 도착일이 아니다.** 조달 실무에서 납기를
관행적으로 30일로 찍기 때문에 월별 median 이 24개월 내내 표준편차 0.00 이다.
움직이는 것은 평균(35.3~49.8)과 p75 뿐이다. 이 API 에는 실납품일 필드가 없다.

재수집·병합은 이렇게 한다. 샤드는 병합 전 중간물이라 레포에 넣지 않는다.

```bash
python -m src.procurement.lead_time_collector --start 2026-01 --end 2026-03 --shard a --api-key $KEY
python -m src.procurement.lead_time_collector --merge-only
```

---

## 2. GDELT 공급 차질 뉴스

```
data/raw/news/gdelt_supply_disruption_news.csv     7,756건    운영용
data/raw/news/gdelt_archive_history.csv           29,453건    원본 수집분
data/raw/news/_synthetic_pipeline_smoke.csv           96건    ⚠️ 합성, 스모크 전용
```

| 항목 | 값 |
|---|---|
| 원천 | GDELT GKG 2.0 원본 아카이브 (인증 불필요) |
| 기간 | 48개월 |
| 표본율 | 매시 정각 파일만 (1/4) |
| 수집기 | `src/news/gdelt_archive_collector.py` |

⚠️ **`_synthetic_pipeline_smoke.csv` 는 합성 데이터다.** 실데이터로 오인하면
안 된다. #75 에서 이 파일이 운영 점수 경로로 들어가는 것을 차단했다.

### 알려진 한계

* **영어권 편중.** 수입 1위 바레인(수입 비중 49.7%) 관련 기사가 **0건** 이다.
* **업계 전문지 부족.** 4,114건 중 5건뿐이다.
* 그 결과 뉴스 → 리드타임 검정은 24개월로도 **12건 전부 무의미** 하다(#20).

---

## 3. 관세청 수출입 무역통계

```
data/external/trade/kcs_trade_total_monthly.csv      2,024행   HS × 월 전체
data/external/trade/kcs_trade_country_monthly.csv    2,299행   HS × 국가 × 월
```

| 항목 | 값 |
|---|---|
| 원천 | data.go.kr 「관세청_수출입무역통계」 |
| 범위 | HS 34종 × 102개월 |
| 수집기 | `src/trade/trade_collector.py` |

**세 외부신호 중 유일하게 리드타임과의 관계가 재현된 축이다.**

```
trade_risk → lead_p75   lag0 r=+0.432 p=0.0350
                        lag1 r=+0.499 p=0.0153
                        lag2 r=+0.457 p=0.0326
```

국가 가중치(`data/mapping/country_weight.csv`)도 이 실측 수입 비중에서 만든다.

```
바레인 49.70%  중국 27.46%  UAE 11.04%  미국 0.25%
```

⚠️ **HS 커버리지가 30.7% 다.** 원장이 쓰는 원자재 692종 중 HS 매핑이 있는 것이
16종뿐이다. 확장 후보는 `outputs/material_hs_mapping_candidates.csv` 에 있고
전부 `review_status=pending` 이다.

---

## 4. 원자재 가격

```
data/external/market/commodity_prices.csv    9,379행
```

| 항목 | 값 |
|---|---|
| 원천 | Alpha Vantage |
| 기간 | 2015-01-01 ~ 2026-06-30 |
| 품목 | `data/mapping/market_series_registry.csv` 참조 |

⚠️ **기간 설정을 반드시 좁혀서 쓴다.** 캐시는 계속 쌓이는데 전 구간을 쓰면
산출물이 폭증한다. 실측으로 139개월치를 돌렸더니 감사 파일이 **12.9GB** 가 됐다.

```bash
COMMODITY_START_DATE=2023-10-01 COMMODITY_END_DATE=2026-01-31 \
  python -m src.commodity.commodity_risk_scorer
```

---

## 분석 산출물 (`outputs/`)

`outputs/` 전체는 **9.9GB** 라 통째로는 못 넣는다. 대형 중간 산출물(위험점수
원본 2.1GB, 감사 2.5GB, 백테스트 685MB)은 코드로 재현되므로 제외하고,
**결론이 담긴 작은 표와 리포트만** 넣는다.

| 파일 | 내용 |
|---|---|
| `procurement_lead_time_by_item.csv` | 세부품명별 리드타임 분위수 (446종) |
| `quantile_lead_time_recommendation.csv` | 품목별 권고 리드타임·발주량 (102,660) |
| `material_hs_mapping_candidates.csv` | HS 매핑 확장 후보 (pending) |
| `supply_risk_lead_time_validation.json` | 위험→리드타임 검정 18건 |
| `stock_evaluation_report.csv` | 모델 평가 |
| `module_c_run_report.json` | 모듈 C 실행 기록 (preflight 포함) |
| `*.json` 전반 | 각 분석의 결론·파라미터 |

---

## 갱신 주기

| 데이터 | 주기 | 비고 |
|---|---|---|
| 조달청 | 분기 | 쿼터 때문에 여러 키로 샤드 병렬 |
| 관세청 | 월 | 증분 수집 |
| 원자재 | 월 | |
| GDELT | 필요 시 | 오래 걸린다 |

갱신하면 이 문서의 행수·기간도 같이 고친다. **숫자가 문서와 어긋나면 다음
사람이 잘못된 전제로 분석한다.**

---

관련: `data/handoff/README.md`(팀 간 전달 산출물) · #20 · #22 · #39 · #54
