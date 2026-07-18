#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build an auditable parent-child grouping layer without changing item identity."""

import csv
import re
import sys
import unicodedata
from collections import Counter


IN_FILE = sys.argv[1]
OUT_FILE = sys.argv[2]
SUMMARY_CSV = sys.argv[3]
SUMMARY_MD = sys.argv[4]


FAMILY_PARENT_ALIASES = {
    "LANCET": "BLOOD_LANCET",
    "BLOOD_LANCET": "BLOOD_LANCET",
    "HYPODERMIC_NEEDLE": "INJECTION_NEEDLE",
    "INJECTION_NEEDLE": "INJECTION_NEEDLE",
    "FACE_MASK": "MEDICAL_MASK",
    "MEDICAL_MASK": "MEDICAL_MASK",
    "COTTON_GAUZE": "MEDICAL_GAUZE",
    "GAUZE_BANDAGE_DRESSING_GENERIC": "MEDICAL_GAUZE",
    "MEDICAL_GAUZE": "MEDICAL_GAUZE",
    "IV_ADMIN_SET": "INFUSION_SET",
    "IV_ADMINISTRATION_SET": "INFUSION_SET",
    "INFUSION_SET": "INFUSION_SET",
    "IV_CATHETER_ANGIONEEDLE": "ANGIO_CATHETER",
    "ANGIO_CATHETER": "ANGIO_CATHETER",
    "STERILIZATION_PACKAGING_EO": "EO_STERILIZATION_PACKAGING",
    "EO_STERILIZATION_PACKAGING": "EO_STERILIZATION_PACKAGING",
    "DIALYSIS_FLUID_CONTAINER": "DIALYSATE_CONTAINER",
    "DIALYSATE_CONTAINER": "DIALYSATE_CONTAINER",
    "MEDICAL_WASTE_SUPPLY": "MEDICAL_WASTE_CONTAINER",
    "MEDICAL_WASTE_CONTAINER": "MEDICAL_WASTE_CONTAINER",
}

PARENT_NAMES = {
    "BLOOD_LANCET": "채혈침",
    "INJECTION_NEEDLE": "주사침",
    "MEDICAL_MASK": "마스크",
    "MEDICAL_GAUZE": "의료용 거즈",
    "INFUSION_SET": "수액세트",
    "ANGIO_CATHETER": "카테터(angio needle)",
    "EO_STERILIZATION_PACKAGING": "멸균포장재",
    "DIALYSATE_CONTAINER": "혈액투석제통",
    "MEDICAL_WASTE_CONTAINER": "의료폐기물 전용용기",
}

# Only unresolved rows use these rules. Existing structured/name-rule family values
# remain the identity source and are never overwritten by a broad token concept.
CONCEPT_RULES = [
    ("MEDICAL_WASTE_CONTAINER", "의료폐기물 전용용기", re.compile(r"주사(?:침|바늘).*통|needlebox|의료폐기물")),
    ("BLOOD_GLUCOSE_TEST_STRIP", "혈당검사지", re.compile(r"(?:혈당|glucose).*(?:스틱|스트립|검사지|시험지|strip)")),
    ("BLOOD_LANCET", "채혈침", re.compile(r"채혈침|혈당침|란셋|난셋|lancet|lanset")),
    ("DISPOSABLE_SYRINGE", "주사기", re.compile(r"주사기|syringe")),
    ("INJECTION_NEEDLE", "주사침", re.compile(r"주사침|주사바늘|hypodermicneedle")),
    ("INFUSION_SET", "수액세트", re.compile(r"수액.*세트|infusionset|ivset")),
    ("ANGIO_CATHETER", "카테터", re.compile(r"카테터|catheter|angiocath")),
    ("ALCOHOL_SWAB", "알코올스왑", re.compile(r"(?:알코올|알콜|alcohol).*(?:솜|스왑|swab|패드|pad)")),
    ("MEDICAL_GAUZE", "의료용 거즈", re.compile(r"거즈|gauze")),
    ("MEDICAL_MASK", "마스크", re.compile(r"마스크|mask|kf94|kf80")),
    ("MEDICAL_GLOVE", "의료용 장갑", re.compile(r"장갑|glove|글러브|니트릴|라텍스")),
    ("SPECIMEN_CONTAINER", "검체용기", re.compile(r"(?:검체|채변|대변).*(?:용기|통|컵)")),
    ("HYGIENE_TISSUE", "위생티슈", re.compile(r"물티슈|화장지|티슈|키친타올")),
    ("ORAL_HYGIENE_SUPPLY", "구강위생용품", re.compile(r"칫솔|치약|치실|치간칫솔")),
]


def match_key(value):
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def concept_for(name):
    key = match_key(name)
    for concept_id, concept_name, pattern in CONCEPT_RULES:
        if pattern.search(key):
            return concept_id, concept_name, key
    return "UNRESOLVED_PARENT", "미분류", key


def main():
    with open(IN_FILE, encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise ValueError("입력 데이터가 비어 있습니다")

    output = []
    summary = {}
    for row in rows:
        family_id = str(row.get("item_family_id_suggested", "") or "").strip()
        family_name = str(row.get("standard_family_name_suggested", "") or "").strip()
        family_source = str(row.get("family_source", "") or "").strip()
        if family_id and family_id not in {"UNSPECIFIED_ITEM", "MATERIAL_UNSPECIFIED"}:
            parent_id = FAMILY_PARENT_ALIASES.get(family_id, family_id)
            parent_name = PARENT_NAMES.get(parent_id, family_name or parent_id)
            parent_source = (
                "structured_family"
                if family_source != "name_rule"
                else "name_rule_family"
            )
            key = match_key(row.get("representative_name", ""))
        else:
            parent_id, parent_name, key = concept_for(row.get("representative_name", ""))
            parent_source = "concept_dictionary" if parent_id != "UNRESOLVED_PARENT" else "unresolved"

        subtype = str(row.get("item_subtype_id_candidate", "") or "").strip() or "UNSPECIFIED_SUBTYPE"
        specification = str(row.get("normalized_specification_candidate", "") or "").strip() or "UNSPECIFIED_SPEC"
        unit = str(row.get("standard_unit_candidate", "") or "").strip() or "UNSPECIFIED_UNIT"
        grouping_key = "::".join([parent_id, subtype, specification, unit])
        record = {
            "representative_item_id": row.get("representative_item_id", ""),
            "parent_concept_id": parent_id,
            "parent_concept_name": parent_name,
            "parent_concept_source": parent_source,
            "child_original_name": row.get("representative_name", ""),
            "concept_match_key": key,
            "forecast_grouping_key_candidate": grouping_key,
        }
        output.append(record)
        stats = summary.setdefault(parent_id, {
            "parent_concept_name": parent_name,
            "parent_concept_source": parent_source,
            "child_count": 0,
            "occurrence_count": 0.0,
            "usage_sum": 0.0,
        })
        stats["child_count"] += 1
        stats["occurrence_count"] += number(row.get("occurrence_count"))
        stats["usage_sum"] += number(row.get("usage_sum"))

    with open(OUT_FILE, "w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)

    summary_rows = []
    for parent_id, values in sorted(
        summary.items(), key=lambda item: (-item[1]["occurrence_count"], item[0])
    ):
        summary_rows.append({"parent_concept_id": parent_id, **values})
    with open(SUMMARY_CSV, "w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)

    sources = Counter(row["parent_concept_source"] for row in output)
    with open(SUMMARY_MD, "w", encoding="utf-8") as target:
        target.write("# 부모-자식 품목개념 그룹핑 요약\n\n")
        target.write(f"- 대표품목: {len(output):,}\n")
        target.write(f"- 부모개념: {len(summary_rows):,}\n")
        for source, count in sources.most_common():
            target.write(f"- {source}: {count:,}\n")
        target.write("\n| parent_concept_id | 이름 | 자식수 | 발주흔적 | 사용량 |\n")
        target.write("|---|---|---:|---:|---:|\n")
        for row in summary_rows[:50]:
            target.write(
                f"| {row['parent_concept_id']} | {row['parent_concept_name']} | "
                f"{row['child_count']:,} | {row['occurrence_count']:,.0f} | "
                f"{row['usage_sum']:,.0f} |\n"
            )

    print(
        "부모개념 그룹핑:", len(output), "행 /", len(summary_rows),
        "개념 / 미분류", sources.get("unresolved", 0)
    )


if __name__ == "__main__":
    main()
