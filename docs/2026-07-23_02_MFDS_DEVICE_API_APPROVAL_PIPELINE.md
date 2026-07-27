# 의료기기 공공 API 기반 식별·원자재 승인 파이프라인

- 작성일: 2026-07-23 KST
- 구현 버전: `item-enrichment-v1.1`
- 대상 입력: `raw_stock` 기반 대표품목
- 금지 입력: 기존 `device/` 폴더

## 1. 결론

공공데이터포털 API 호출 성공만으로 모든 매핑을 승인하지 않는다. 공식 API 결과와
`raw_stock` 품목이 제품 고유코드로 일치하고, 해당 제품의 구조화 필드에 원자재 포함이
명시된 경우에만 제품 단위 원자재 주장을 자동 승인한다.

승인 단계는 다음과 같이 분리한다.

| 단계 | 의미 | 자동 승인 조건 |
|---|---|---|
| API 수집 | 공식 데이터를 정상적으로 저장 | 정상 응답, 식별자 존재, 요청 범위 기록 |
| 제품 식별 | raw_stock 품목과 공식 제품이 동일 | UDI-DI 또는 공식 코드가 유일하게 정확히 일치 |
| 원자재 주장 | 해당 제품이 특정 물질을 포함 | 제품 식별 승인 + 공식 구조화 필드가 `예` |
| 재고 가중치 | 원자재 가격·수급 위험을 재고에 반영 | 원자재 주장 승인 + 시장경로 + 노출가중치 별도 승인 |

## 2. 발견한 기존 오류

1. 의료기기 품목허가와 UDI 통합정보 JSON은 `items[].item` 구조였으나 기존 파서는
   바깥 객체만 읽었다. 그 결과 정상 응답도 품목명·식별자 없는 행으로 거절됐다.
2. 품목허가 API의 검색 파라미터는 `prduct`, `prductPrmisnNo`처럼 소문자 기반인데
   응답 필드명과 같은 대문자 이름을 사용하면 조건이 적용되지 않았다.
3. UDI 제품정보의 실제 제품명·업체명 필드인 `PRDT_NM_INFO`,
   `MNFT_IPRT_ENTP_NM`가 프로필에 빠져 있었다.
4. UDI 제품정보와 통합정보가 각각 약 266만 건인데 조건 없이 전체 수집할 수 있었다.
5. UDI 통합정보 행은 제품명 없이 UDI-DI와 속성만 제공되는데, 제품정보와 결합하지
   않고 별도 제품처럼 매칭하려 했다.
6. API 마스터를 수집해도 제품 식별·재질 승인이 모듈 C 후보에 연결되지 않았다.

## 3. 수정한 처리 방식

### 3.1 수집

- 중첩된 `item` 객체를 재귀적으로 한 단계씩 평탄화한다.
- UDI 대용량 API는 `--query` 또는 `--max-pages`가 없으면 실행을 거절한다.
- 페이지 크기는 서버 계약인 최대 500건으로 제한한다.
- 조건조회 0건은 장애가 아니라 정상적인 `근거 없음`으로 저장한다.
- 허용되지 않은 검색 필드는 API 호출 전에 거절한다.

예시는 다음과 같다.

```bash
set -a
source .env
set +a

conda run -n teamlex python -m src.item_enrichment fetch \
  --source mfds_device_permit \
  --query prduct=주사기 \
  --page-size 500

conda run -n teamlex python -m src.item_enrichment fetch \
  --source mfds_device_udi_product \
  --query PRDLST_NM=주사기 \
  --page-size 500
```

### 3.2 UDI 결합

`mfds_device_udi_product`와 `mfds_device_udi_attributes`를 UDI-DI로 결합한다. 제품명이
없는 통합정보 행은 독립적인 제품 식별 후보로 사용하지 않는다.

### 3.3 승인 규칙

현재 공식 구조화 원자재 필드는 다음 두 개만 사용한다.

| 공식 필드 | 승인 코드 | 조건 |
|---|---|---|
| `LATEX_ICLS_YN` | `NATURAL_RUBBER_LATEX` | 값이 `예`인 경우 |
| `PHTHLT_ICLS_YN` | `PHTHALATE_PLASTICIZER` | 값이 `예`인 경우 |

`아니오`, 빈 값, 제품명 유사 매칭은 원자재 승인 근거로 사용하지 않는다. 추가설명 자유
텍스트도 현재 자동 승인에 사용하지 않는다.

제품명만 유일하게 일치하면 `candidate_identity`, UDI-DI가 유일하게 정확히 일치하면
`verified_identity`와 `identity_review_status=approved`를 부여한다. 원자재 승인은 후자의
경우에만 가능하다.

## 4. 실제 API 검증 결과

| 검증 항목 | 결과 |
|---|---:|
| 허가번호 조건조회 | 요청 1건, 응답 1건 |
| 주사기 품목허가 마스터 | 1,269건 |
| 주사기 UDI 제품 마스터 | 18,696건 |
| 장갑 UDI 제품 검증 표본 | 100건 |
| 라텍스 명시 제품 UDI 조회 | 12건 |
| 통합정보 행 존재 | 10건 |
| `LATEX_ICLS_YN=예` | 10건 |
| 실제 UDI 제품·속성 결합 테스트 | 제품 식별·라텍스 승인 통과 |

공식 출처:

- 식약처 의료기기 품목허가 정보: <https://www.data.go.kr/data/15057456/openapi.do>
- 식약처 의료기기 표준코드별 제품정보: <https://www.data.go.kr/data/15073875/openapi.do>
- 식약처 의료기기 표준코드별 통합정보: <https://www.data.go.kr/data/15073863/openapi.do>

## 5. 현재 전체 데이터 적용 결과

전체 대표품목 101,546개를 공식 마스터 20,068건과 다시 대조했다. 현재 raw_stock의
로컬 품목코드는 `USE...`, `MTR...` 등 기관 내부 코드이며 UDI-DI가 아니다. 따라서 공식
재질 레코드가 있어도 동일 제품임을 증명할 조인 키가 없다.

| 결과 | 건수 |
|---|---:|
| 정확한 공식 코드 일치 | 0 |
| 이름 중복 후보 | 4 |
| 제품 식별 자동 승인 | 0 |
| 제품 단위 원자재 자동 승인 | 0 |

0건은 API 미작동 결과가 아니라 잘못된 제품으로 원자재를 전파하지 않은 품질 게이트
결과다. 예를 들어 특정 UDI 장갑이 라텍스를 포함한다는 사실을 `라텍스장갑`이라는 이름의
모든 raw_stock 행에 곧바로 적용하지 않는다.

## 6. 모듈 C 연결

승인된 제품 단위 원자재 주장은 다음 파일로 저장한다.

- `data/processed/official_device_material_claims_v1.csv`

모듈 C 후보는 `representative_item_id + raw_material_meta_code`가 모두 일치할 때만 공식
주장을 결합한다. 결합된 행은 다음처럼 바뀐다.

```text
material_review_status = approved
material_confidence = verified
material_evidence_tier = official_product_material
official_material_claim_approved = true
```

다만 원자재-시장가격 경로가 없거나 노출가중치가 승인되지 않으면 최종 재고 조정에는
들어가지 않는다. UDI 통합정보는 물질 포함 여부를 증명하지만 제품 원가에서 해당 물질이
차지하는 비율은 제공하지 않기 때문이다.

## 7. 다음 필수 작업

자동 승인 건수를 늘리려면 병원 물품 마스터 또는 구매·입고 데이터에 다음 중 하나가
필요하다.

1. UDI-DI 또는 바코드
2. 식약처 허가번호
3. 제조사 + 모델명 + 제품명 조합

이 식별자가 확보되기 전에는 공식 API를 품목군 후보 검증에는 사용할 수 있지만, 특정
제품의 원자재를 raw_stock 전체에 자동 전파해서는 안 된다.
