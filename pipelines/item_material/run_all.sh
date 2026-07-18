#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 물품 원재료·리스크 메타코드 파이프라인 일괄 실행기
#
# 사용법:
#   ./run_all.sh <입력파일(.parquet 또는 .csv)> [출력폴더]
#
# 예:
#   ./run_all.sh ./output/_integrated_input.parquet ./output_full
#
# 권장 진입점은 worklist와 구조화 속성을 먼저 병합하는
#   python -m src.item_integrated_pipeline
#
# 입력이 .parquet 이면 자동으로 .csv 로 변환한다(pandas 필요).
# ---------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export PIPE_DATA_DIR="$HERE/data"          # brand_dict_extra.tsv, ingredient_ko_en.tsv 위치
PYTHON="${PYTHON:-python3}"

IN="${1:?입력파일(.parquet 또는 .csv)을 첫 인자로 주세요}"
OUTDIR="${2:-$HERE/output_full}"
mkdir -p "$OUTDIR"

# 1) parquet -> csv (필요 시)
if [[ "$IN" == *.parquet ]]; then
  CSV="$OUTDIR/_input.csv"
  echo "[0/5] parquet -> csv 변환..."
  "$PYTHON" -c 'import pandas as pd,sys; pd.read_parquet(sys.argv[1]).to_csv(sys.argv[2],index=False,encoding="utf-8-sig")' "$IN" "$CSV"
else
  CSV="$IN"
fi

echo "[1/5] family 분류(구조화 결과 우선 + 이름 규칙 감사 후보 생성)..."
"$PYTHON" "$HERE/scripts/build_family_candidates.py" "$CSV" \
  "$OUTDIR/item_family_candidate_suggestions_full.csv" \
  "$OUTDIR/unresolved_priority_queue_full.csv"

echo "[2/5] 공급 클러스터 부착..."
"$PYTHON" "$HERE/scripts/build_supply_clusters.py" \
  "$OUTDIR/item_family_candidate_suggestions_full.csv" \
  "$OUTDIR/item_supply_clusters_full.csv" \
  "$OUTDIR/supply_cluster_summary_full.md"

echo "[3/5] 원재료·리스크·수요 메타코드 매핑..."
"$PYTHON" "$HERE/scripts/build_material_events.py" \
  "$OUTDIR/item_supply_clusters_full.csv" \
  "$OUTDIR/item_material_event_mapping_full.csv" \
  "$OUTDIR/meta_code_glossary_full.csv" \
  "$OUTDIR/raw_material_to_items_index_full.md"

if [[ "${PIPE_SKIP_EXCEL:-0}" == "1" ]]; then
  echo "[4/5] PIPE_SKIP_EXCEL=1: 선택 산출물인 xlsx 생성을 건너뜁니다."
elif "$PYTHON" -c "import openpyxl" 2>/dev/null; then
  echo "[4/5] 메타코드 사전 엑셀 생성..."
  "$PYTHON" "$HERE/scripts/build_meta_code_excel.py" \
    "$OUTDIR/meta_code_glossary_full.csv" \
    "$OUTDIR/item_material_event_mapping_full.csv" \
    "$OUTDIR/meta_code_glossary_full.xlsx"
else
  echo "[4/5] openpyxl 미설치: 선택 산출물인 xlsx 생성을 건너뜁니다. CSV는 생성되었습니다."
fi

echo "[5/5] 예측용 상위 품목개념 후보 생성(선택 family는 변경하지 않음)..."
"$PYTHON" "$HERE/scripts/build_parent_concepts.py" \
  "$OUTDIR/item_material_event_mapping_full.csv" \
  "$OUTDIR/item_parent_concept_grouping_full.csv" \
  "$OUTDIR/parent_concept_summary_full.csv" \
  "$OUTDIR/parent_concept_summary_full.md"

echo
echo "완료. 산출물: $OUTDIR"
echo "  - item_material_event_mapping_full.csv  (최종 통합본)"
echo "  - meta_code_glossary_full.csv            (메타코드 사전)"
echo "  - unresolved_priority_queue_full.csv     (다음 확장 대상 큐)"
echo "  - item_parent_concept_grouping_full.csv  (예측 집계용 상위개념 후보)"
