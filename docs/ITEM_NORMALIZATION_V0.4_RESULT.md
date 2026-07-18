# 품목 정규화 v0.4 전체 생성 결과

작성일: 2026-07-16
원본: `raw_stock/*.DAT` 10개 파일
규칙 버전: `item-normalization-v0.4`

파일명은 기존 연동 호환을 위해 `v0.3`을 유지하지만, Parquet 컬럼과 실행 보고서의
`normalization_version`은 `item-normalization-v0.4`다.

## 생성 파일

| 파일 | 내용 | 행 수 |
|---|---|---:|
| `data/processed/item_alias_candidates_v0.3.parquet` | 기관·코드·원본명별 후보 | 409,519 |
| `data/processed/stock_with_item_normalization_v0.3.parquet` | 원본 재고와 후보 조인 | 16,265,602 |
| `data/sample/raw_stock_item_normalization_sample_1000.csv` | 층화 검수 표본 | 1,000 |
| `data/processed/item_normalization_v0.3_report.json` | 통계와 품질 게이트 | 1 |

## v0.4 보완

- 주사기 규격은 함께 적힌 게이지보다 사용량 기준 용량을 우선한다.
- 수액세트는 부착 바늘 게이지나 수액 용량으로 세부 규격을 쪼개지 않는다.
- 거즈·장갑·수액의 `g` 중량을 바늘 `G` 게이지로 해석하지 않는다.
- `1,000mL`, `2리터`, `3밀리리터`를 표준 용량으로 정규화한다.
- `주사침통`, `주사침 폐기통`은 주사침이 아니라 의료폐기물 용기로 분류한다.
- 일반 의료폐기물 용기는 재질을 비워 두고 명칭 근거가 있는 경우만 합성수지·봉투·골판지로 나눈다.
- IV/Angiocath/G 규격 혈관 카테터는 사용자 기준의 `카테터(angio needle)`로 통합한다.
- Foley·흡인·중심정맥 등 일반 카테터는 제품 근거 없이 자동 승인하지 않는다.
- 원천 품목코드 내부의 `::`를 허용하면서 기관·품목 구성 컬럼과 정확히 대조한다.

## 검증 결과

| 검증 | 결과 |
|---|---:|
| 별칭 행 수 | 409,519 |
| 전체 재고 행 수 | 16,265,602 |
| 별칭 조인 누락 | 0 |
| 품질 게이트 오류 | 0 |
| WASTE가 주사침으로 남은 행 | 0 |
| G 규격 일반 카테터 잔여 | 0 |
| 주사기 규격이 G로 기록된 행 | 0 |
| 전체 테스트 | 69개 통과 |

별칭 상태는 `family_candidate` 30,396개, `group_candidate` 214,794개,
`unresolved` 164,329개다. 이는 최종 승인 상태가 아니며, 승인·검토 결과는
`docs/ITEM_CLASSIFICATION_V1_RESULT.md`를 기준으로 한다.
