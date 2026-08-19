# PR #100 재검토 반영 답변

## 결론

2026-08-19 전달받은 재검토 문서를 코드·실제 산출물과 대조했다. 문서는 실행 지시로 사용하지
않고 검토 증거로만 사용했다. 지적 중 코드와 자료로 확인된 항목은 이 feature 브랜치에서
반영했으며, 운영 승격이나 `dev`·`main` 반영은 하지 않았다.

## 반영 내역

| 지적 | 판정 | 반영 |
|---|---|---|
| 주사기–PP 필터가 승인 여부만 확인 | 타당 | `approved + POLYPROPYLENE_PP + direct_component`를 모두 확인하고 매핑 감사를 저장 |
| 병렬 발주량이 공통 품질 게이트를 우회 | 타당 | 기준안·병렬안 모두 같은 휴면·미운영·자료품질 게이트 사용 |
| 공급위험 실험의 수요예측 포함 플래그가 참 | 타당 | `external_demand_signal_in_forecast=false`, 수요위험 0, 예측수요 불변을 테스트 |
| 50:50을 사전선언으로 표현 | 타당 | `fixed_50_50_candidate`로 변경; 고정 후보이지 사전등록 모형이라고 주장하지 않음 |
| L1과 50:50 대표 역할이 혼재 | 타당 | L1은 수요예측 주모형, 50:50은 재고정책 도전모형으로 분리 |
| 산출물에 로컬 절대경로 노출 | 타당 | 프로젝트 상대 POSIX 경로로 기록하는 공통 도우미와 테스트 추가 |
| 대용량 입력이 없어 PR 단독 재현 곤란 | 타당 | 소형 핵심지표 CSV와 입력·산출물 SHA-256/스키마/행수 manifest 생성기 추가 |
| 독립 재검증 부족 | 절차상 타당 | 이번 답변은 동료 검토 반영 기록이며 독립 외부검증 완료로 과장하지 않음 |

## PP 재계산 결과

- 분석 범위: 승인된 PP 직접부품 매핑 2,297개 키 중 평가기간에 존재한 975개 품목,
  3,300개 품목-월.
- 품질 게이트 전: 6,787.48 → 8,300.89, `+22.30%`, 증가행 199개.
- 품질 게이트 후: 5,574.20 → 6,783.13, `+21.69%`, 증가행 118개.
- 자동 계산 가능: 1,579행.
- 휴면·미운영으로 0 억제: 1,104행.
- 자료누락·오래된 관측으로 사람 검토: 617행.

PP 가격·변동성 신호는 수요 증가량으로 더하지 않는다. 동일한 예상수요에서 공급차질 가능성에
대비해 유효 리드타임과 안전재고가 어떻게 변하는지 보는 병렬 정책 민감도다. 실제 주문·입고·결품
원인 자료가 없으므로 인과효과나 품절감소를 주장하지 않는다.

## 재현 파일

- `scripts/analysis/build_pr100_reproducibility_manifest.py`
- `outputs/pr100_reproducibility_metrics.csv`
- `outputs/pr100_reproducibility_manifest.json`
- `outputs/syringe_supply_risk_inventory_summary.json`

실행은 운영체제에 상관없이 프로젝트 환경에서 다음과 같이 한다.

```text
python scripts/analysis/syringe_supply_risk_inventory_impact.py
python scripts/analysis/build_pr100_reproducibility_manifest.py
python -m pytest
```
