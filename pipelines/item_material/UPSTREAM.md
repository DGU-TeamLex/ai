# Upstream Integration Record

## Sources Reviewed

| role | repository | commit | status |
|---|---|---|---|
| retired source | `DGU-TeamLex/wep-stock-data-normalization` | `938fa3e6a0af82bdeb1fd9c3e4ecb96a9db41499` | canonical repository로 이동 안내 |
| canonical source | `DGU-TeamLex/wep-stock-item-material-pipeline` | `74d398204000d855e86da084afa5b671a63d50fe` | 2026-07-18 비교 실행 |
| original local integration | `wep-stock-data-normalization/add-item-material-pipeline` | `94b5663833b4d50f37d6e82260e7eb59f35e185b` | 기존 27개 공급 클러스터·리스크 축의 기반 |

현재 로컬 버전은 `combined-family-v2.1`, `combined-material-v2.1`이다.

## Integration Policy

원격 구현을 통째로 교체하지 않고 실제 전체 데이터 재실행과 충돌 감사를 거쳐 다음만
선별 적용했다.

### Adopted

1. canonical 저장소의 상위 품목개념(parent concept) 계층을 별도 산출물로 추가했다.
2. 의약품 대분류 안에서만 제네릭 접미사 규칙을 사용하고
   `naming_pattern_unverified`로 격리했다.
3. 비성분 sentinel을 원자재 코드로 승격하지 않는 규칙을 강화했다.
4. 기존 27개 공급 클러스터와 원재료·공급위험·수요트리거 3축 구조는 유지했다.
5. `worklist`와 구조화 속성을 일대일 병합한 뒤 family 규칙을 실행한다.

### Not Adopted Wholesale

canonical 저장소의 이름-only family 결과와 확대 사전은 구조화 결과를 직접 덮어쓰지
않는다. 전체 101,546건 비교 실행에서 다음 교차 도메인 오분류가 확인됐기 때문이다.

- 산소마스크를 `FUEL`로 분류
- 채혈침을 검사스트립 또는 `NON_INGREDIENT_SPEC`으로 분류
- 알코올솜을 의약품 성분으로 분류
- 주사침 폐기통을 주사침 또는 한방침으로 분류
- `Lanset`을 `LANSOPRAZOLE`로 분류

원격 이름 규칙 결과는 `name_rule_*` 컬럼에 감사 후보로 보존한다.

## Resolution Order

1. 검증된 구조화 family
2. 검증된 단일 유효성분
3. 사용자 공식 기준표의 정확 규칙 또는 고신뢰 복합 문맥 규칙
4. 로컬 구조화 family·subtype·specification
5. 이름·브랜드·접미사 규칙

선택값과 이름 규칙이 다르면 `family_conflict_flag=true`로 남기며 어느 값도 승인으로
간주하지 않는다. 일반 Foley·흡인 카테터는 angio needle로 합치지 않고, `24G` 같은
게이지는 수량이 아니라 규격으로 보존한다.

## Safety Changes

1. `raw_material_verified` 대신 `raw_material_suggested`를 사용한다.
2. 모든 원자재 결과는 `material_review_status=needs_review`다.
3. 복합 혈당측정 세트는 BOM 확인 전 `MATERIAL_UNSPECIFIED`로 둔다.
4. `raw_material_verified` 컬럼, sentinel 원자재 누출, 대표품목 행 손실을 품질 게이트로
   차단한다.
5. 운영 뉴스·가격 점수는 별도 승인된
   `data/mapping/stock_item_material_mapping.csv`만 읽는다.
6. `consolidate_research.py`는 기존 사전을 보존하면서 신규 조사 결과를 병합한다.
7. glossary는 사용된 모든 코드와 축을 포함하고 `(category, meta_code)` 중복을 금지한다.

실행 결과와 비교 수치는
`docs/ITEM_INTEGRATED_PIPELINE_V2_1_RESULT.md`를 참고한다.
