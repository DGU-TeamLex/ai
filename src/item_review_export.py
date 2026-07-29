import argparse
import json
from pathlib import Path

import pandas as pd

from .config import (
    ITEM_MATERIAL_EVENT_CANDIDATE_PATH,
    PROCESSED_DATA_DIR,
    SAMPLE_DATA_DIR,
)


DEFAULT_CLASSIFICATION_PATH = (
    PROCESSED_DATA_DIR / "item_classification_candidates_v1.parquet"
)
DEFAULT_OUTPUT_PATH = SAMPLE_DATA_DIR / "item_classification_noteworthy_1000.csv"

CATEGORY_WEIGHTS = {
    "classification_conflict": 0.12,
    "official_drug_evidence_review": 0.12,
    "medical_waste_boundary": 0.16,
    "catheter_boundary": 0.18,
    "approved_family_audit": 0.05,
    "candidate_complete_unapproved": 0.12,
    "material_evidence_review": 0.06,
    "candidate_family_high_usage": 0.05,
    "group_only_high_usage": 0.04,
    "unresolved_high_usage": 0.07,
}

CATEGORY_GUIDANCE = {
    "classification_conflict": (
        "품목군 또는 가족 후보가 서로 충돌함",
        "원본명, 기관 코드, 공식 제품 ID를 확인해 하나의 taxonomy로 확정",
    ),
    "official_drug_evidence_review": (
        "식약처 NEDrug 페이지가 연결됐지만 제품 단위 승인 조건을 재확인해야 함",
        "공식 제품명, 품목코드, 성분, 함량, 제형을 원본명과 대조",
    ),
    "medical_waste_boundary": (
        "용기 형태와 재질에 따라 폐기물 세부유형과 원자재가 달라짐",
        "손상성, 침통, 봉투, PE, 종이 근거를 확인하고 일반 박스의 재질은 추정하지 않음",
    ),
    "catheter_boundary": (
        "angio 카테터와 Foley, 흡인, 중심정맥 등 일반 카테터의 경계 항목",
        "G/게이지, IV, Angiocath, 사용목적을 확인해 카테터 세부유형 확정",
    ),
    "approved_family_audit": (
        "가족·규격·단위 자동승인 규칙의 정확도 감사 표본",
        "승인 결과가 실제 물품과 일치하는지 확인하고 오승인이면 규칙 수정",
    ),
    "candidate_complete_unapproved": (
        "세부유형·규격·단위는 완결됐지만 공식 승인 근거가 부족함",
        "공식 마스터 또는 제조사 문서 근거를 추가한 뒤 승인 여부 결정",
    ),
    "material_evidence_review": (
        "원자재 후보 근거가 일반지식 미검증 또는 미식별 상태임",
        "UDI, IFU, SDS 또는 제조사 문서로 재질을 확인하고 별도 매핑 승인",
    ),
    "candidate_family_high_usage": (
        "사용량이 큰 가족 후보지만 세부정보 또는 승인 근거가 부족함",
        "세부유형, 규격, 단위와 공식 근거를 우선 보완",
    ),
    "group_only_high_usage": (
        "사용량이 크지만 DB item_group만 판정됨",
        "표준 가족과 제품 신원을 우선 조사",
    ),
    "unresolved_high_usage": (
        "사용량이 크지만 안전하게 판정할 가족 근거가 없음",
        "원본 코드 체계와 공식 제품 마스터를 먼저 확인",
    ),
    "priority_fill": (
        "주요 구간 표본 이후 사용량과 상태 우선순위로 추가된 검토 항목",
        "현재 상태의 review_reason을 기준으로 검토",
    ),
}

OUTPUT_COLUMNS = [
    "attention_rank",
    "attention_category",
    "attention_reason_ko",
    "recommended_review_action_ko",
    "representative_item_id",
    "representative_name",
    "classification_status",
    "review_status",
    "review_reason",
    "candidate_status",
    "classification_confidence",
    "item_group_id_candidate",
    "item_group_candidates",
    "item_family_id_candidate",
    "standard_family_name_candidate",
    "item_subtype_id_candidate",
    "standard_subtype_name_candidate",
    "normalized_specification_candidate",
    "standard_unit_candidate",
    "selected_item_family_id",
    "selected_standard_family_name",
    "selected_item_subtype_id",
    "selected_standard_subtype_name",
    "selected_specification",
    "selected_unit_code",
    "classification_basis",
    "is_forecastable",
    "canonical_item_id",
    "nedrug_item_seq",
    "official_item_name",
    "verified_family_ids",
    "verified_strength_tokens",
    "evidence_source",
    "evidence_record_id",
    "evidence_url",
    "family_basis",
    "evidence_note",
    "raw_material_suggested",
    "raw_material_evidence",
    "raw_material_meta_code",
    "material_confidence",
    "material_review_status",
    "supply_cluster_id",
    "supply_cluster_name",
    "usage_sum",
    "occurrence_count",
    "institution_count",
    "local_item_key_count",
    "raw_name_variant_count",
    "raw_name_examples",
    "local_code_examples",
]


def _text(series: pd.Series) -> pd.Series:
    return series.astype("string").fillna("").str.strip()


def _prepare_frame(
    classifications: pd.DataFrame,
    materials: pd.DataFrame,
) -> pd.DataFrame:
    required = {
        "representative_item_id",
        "classification_status",
        "item_family_id_candidate",
        "selected_item_family_id",
        "nedrug_item_seq",
        "usage_sum",
        "occurrence_count",
    }
    missing = sorted(required - set(classifications.columns))
    if missing:
        raise ValueError(f"Classification data is missing columns: {missing}")
    if "representative_item_id" not in materials.columns:
        raise ValueError("Material data is missing representative_item_id")

    material_columns = [
        "representative_item_id",
        "raw_material_suggested",
        "raw_material_evidence",
        "raw_material_meta_code",
        "material_confidence",
        "material_review_status",
        "supply_cluster_id",
        "supply_cluster_name",
    ]
    missing_material = [column for column in material_columns if column not in materials.columns]
    if missing_material:
        raise ValueError(f"Material data is missing columns: {missing_material}")
    if materials["representative_item_id"].duplicated().any():
        raise ValueError("Material data must have one row per representative_item_id")

    frame = classifications.merge(
        materials[material_columns],
        on="representative_item_id",
        how="left",
        validate="one_to_one",
    )
    for column in {
        "item_group_id_candidate",
        "selected_item_subtype_id",
        "family_basis",
    }:
        if column not in frame.columns:
            frame[column] = ""
    for column in frame.columns:
        if frame[column].dtype == object or isinstance(frame[column].dtype, pd.StringDtype):
            frame[column] = _text(frame[column])
    frame["usage_sum"] = pd.to_numeric(frame["usage_sum"], errors="coerce").fillna(0.0)
    frame["occurrence_count"] = pd.to_numeric(
        frame["occurrence_count"], errors="coerce"
    ).fillna(0)
    status_priority = {
        "conflict": 0,
        "candidate_complete": 1,
        "candidate_family": 2,
        "group_only": 3,
        "unresolved": 4,
        "approved_external_item": 5,
        "approved_external_family": 6,
    }
    frame["_status_priority"] = frame["classification_status"].map(status_priority).fillna(9)
    return frame


def _sorted(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values(
        ["_status_priority", "usage_sum", "occurrence_count", "representative_item_id"],
        ascending=[True, False, False, True],
        kind="stable",
    )


def _take(
    pool: pd.DataFrame,
    quota: int,
    selected_ids: set[str],
    category: str,
    strata: str | None = None,
) -> pd.DataFrame:
    if quota <= 0:
        return pool.iloc[0:0].copy()
    available = _sorted(pool[~pool["representative_item_id"].isin(selected_ids)])
    if available.empty:
        return available
    if strata and strata in available.columns:
        groups = {
            str(key): group.reset_index(drop=True)
            for key, group in available.groupby(strata, sort=True, dropna=False)
        }
        ordered_ids = []
        depth = 0
        while len(ordered_ids) < quota:
            added = False
            for key in sorted(groups):
                group = groups[key]
                if depth < len(group):
                    ordered_ids.append(group.iloc[depth]["representative_item_id"])
                    added = True
                    if len(ordered_ids) == quota:
                        break
            if not added:
                break
            depth += 1
        chosen = available.set_index("representative_item_id").loc[ordered_ids].reset_index()
    else:
        chosen = available.head(quota).copy()
    chosen["attention_category"] = category
    reason, action = CATEGORY_GUIDANCE[category]
    chosen["attention_reason_ko"] = reason
    chosen["recommended_review_action_ko"] = action
    selected_ids.update(chosen["representative_item_id"].astype(str))
    return chosen


def build_noteworthy_sample(
    classifications: pd.DataFrame,
    materials: pd.DataFrame,
    sample_size: int = 1000,
) -> pd.DataFrame:
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    frame = _prepare_frame(classifications, materials)
    if len(frame) < sample_size:
        raise ValueError(
            f"Not enough representative items for sample: rows={len(frame)}, requested={sample_size}"
        )

    quotas = {
        category: max(1, round(sample_size * weight))
        for category, weight in CATEGORY_WEIGHTS.items()
    }
    family_candidate = frame["item_family_id_candidate"]
    selected_family = frame["selected_item_family_id"]
    selected_ids: set[str] = set()
    parts = []

    rules = [
        (
            "classification_conflict",
            frame["classification_status"].eq("conflict"),
            None,
        ),
        (
            "official_drug_evidence_review",
            frame["nedrug_item_seq"].ne(""),
            None,
        ),
        (
            "medical_waste_boundary",
            family_candidate.eq("MEDICAL_WASTE_CONTAINER")
            | selected_family.eq("MEDICAL_WASTE_CONTAINER"),
            "selected_item_subtype_id",
        ),
        (
            "catheter_boundary",
            family_candidate.isin({"CATHETER", "ANGIO_CATHETER"})
            | selected_family.isin({"CATHETER", "ANGIO_CATHETER"}),
            "item_family_id_candidate",
        ),
        (
            "approved_family_audit",
            frame["classification_status"].eq("approved_external_family"),
            "selected_item_family_id",
        ),
        (
            "candidate_complete_unapproved",
            frame["classification_status"].eq("candidate_complete"),
            "item_group_id_candidate",
        ),
        (
            "material_evidence_review",
            frame["material_review_status"].eq("needs_review")
            & frame["family_basis"].isin({"unresolved", "general_knowledge_unverified"}),
            "family_basis",
        ),
        (
            "candidate_family_high_usage",
            frame["classification_status"].eq("candidate_family"),
            "item_group_id_candidate",
        ),
        (
            "group_only_high_usage",
            frame["classification_status"].eq("group_only"),
            "item_group_id_candidate",
        ),
        (
            "unresolved_high_usage",
            frame["classification_status"].eq("unresolved"),
            "item_group_id_candidate",
        ),
    ]
    for category, mask, strata in rules:
        chosen = _take(
            frame[mask],
            quotas[category],
            selected_ids,
            category,
            strata=strata,
        )
        if not chosen.empty:
            parts.append(chosen)

    selected_count = sum(len(part) for part in parts)
    if selected_count < sample_size:
        parts.append(
            _take(
                frame,
                sample_size - selected_count,
                selected_ids,
                "priority_fill",
            )
        )

    output = pd.concat(parts, ignore_index=True).head(sample_size).copy()
    if len(output) != sample_size:
        raise ValueError(f"Could not build requested sample: {len(output)} != {sample_size}")
    if output["representative_item_id"].duplicated().any():
        raise ValueError("Noteworthy sample contains duplicate representative_item_id values")
    output.insert(0, "attention_rank", range(1, len(output) + 1))
    for column in OUTPUT_COLUMNS:
        if column not in output.columns:
            output[column] = ""
    return output[OUTPUT_COLUMNS]


def export_noteworthy_sample(
    classification_path: Path = DEFAULT_CLASSIFICATION_PATH,
    material_path: Path = ITEM_MATERIAL_EVENT_CANDIDATE_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    sample_size: int = 1000,
) -> dict[str, object]:
    classifications = pd.read_parquet(classification_path)
    materials = pd.read_csv(material_path, low_memory=False)
    sample = build_noteworthy_sample(classifications, materials, sample_size=sample_size)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sample.to_csv(output_path, index=False, encoding="utf-8-sig")
    return {
        "output_path": str(output_path),
        "rows": int(len(sample)),
        "unique_representative_items": int(sample["representative_item_id"].nunique()),
        "category_counts": {
            str(key): int(value)
            for key, value in sample["attention_category"].value_counts().items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export noteworthy item review sample")
    parser.add_argument("--classification-path", type=Path, default=DEFAULT_CLASSIFICATION_PATH)
    parser.add_argument("--material-path", type=Path, default=ITEM_MATERIAL_EVENT_CANDIDATE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--sample-size", type=int, default=1000)
    args = parser.parse_args()
    report = export_noteworthy_sample(
        classification_path=args.classification_path,
        material_path=args.material_path,
        output_path=args.output,
        sample_size=args.sample_size,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
