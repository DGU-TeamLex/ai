"""약성분 수록 여부로 criticality·is_medical 재판정 후보를 뽑는다 (ai#43).

현행 `criticality` 는 품목명 키워드 휴리스틱(`"mg"`, `"정)"`, `"캡슐"`, `"시럽"` …)으로
판정한다. 2026-07-28 수신한 약성분 데이터는 **기관 시스템의 약품 등록 사실**이므로
품목명 추정보다 근거가 강하다. 두 판정을 대조해 어긋나는 품목을 뽑는다.

  오분류 후보 : criticality = MEDICAL 인데 약성분에 없음
  누락 후보   : criticality ≠ MEDICAL 인데 약성분에 있음

소모품은 성분 정보가 애초에 없으므로 **2갈래 분기**가 필요하다.
약성분에 있으면 그것이 최상위 근거이고, 없으면 기존 키워드·품목군 기준을 유지한다.
이 스크립트는 판정을 DB 에 쓰지 않는다 — 검토용 후보 목록만 만든다.

실행:
    DATABASE_URL=... SSIS_DRUG_FILE=~/Downloads/SSIS_20260728/drug_ingredient.DAT \
    python3 src/loading/compute_is_medical_ingredient_review.py
"""
import csv
import io
import os

import psycopg

csv.field_size_limit(10 ** 9)

DRUG_FILE = os.path.expanduser(
    os.environ.get("SSIS_DRUG_FILE", "~/Downloads/SSIS_20260728/drug_ingredient.DAT")
)
OUT = os.environ.get("OUT", "data/handoff/is_medical_review.csv")


def drug_codes():
    """약성분 파일에 등장하는 약품코드 → 대표 성분코드/용도/약종류."""
    info = {}
    with io.open(DRUG_FILE, "r", encoding="utf-8", newline="") as f:
        r = csv.reader(f, delimiter="|", quotechar='"')
        header = [h.strip() for h in next(r)]
        i = {n: k for k, n in enumerate(header)}
        for row in r:
            if len(row) != len(header):
                continue
            code = row[i["약품코드"]]
            if not code:
                continue
            rec = info.get(code)
            ing = (row[i["성분코드"]] or "").strip()
            if rec is None:
                info[code] = [row[i["용도구분"]], row[i["약종류구분"]], ing]
            elif not rec[2] and ing:
                rec[2] = ing  # 기관 간 교차 보완
    return info


def main():
    info = drug_codes()
    print(f"약성분 약품코드 {len(info):,}종")

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT si.standard_code, si.standard_name, si.criticality, si.item_group_id
               FROM standard_items si ORDER BY si.standard_code"""
        )
        items = cur.fetchall()
    print(f"standard_items {len(items):,}종")

    in_drug = [it for it in items if it[0] in info]
    print(f"약성분에 존재 {len(in_drug):,}종 ({100 * len(in_drug) / len(items):.1f}%)")

    w_items = [it for it in items if it[0].startswith("W")]
    w_in = [it for it in w_items if it[0] in info]
    if w_items:
        print(f"  W품목 {len(w_in):,}/{len(w_items):,} = {100 * len(w_in) / len(w_items):.1f}%")

    mis, miss = [], []
    for code, name, crit, grp in items:
        has = code in info
        if crit == "MEDICAL" and not has:
            mis.append((code, name, crit, grp, "오분류후보", "", "", ""))
        elif crit != "MEDICAL" and has:
            usage, kind, ing = info[code]
            miss.append((code, name, crit, grp, "누락후보", usage, kind, ing))

    print(f"오분류 후보(MEDICAL 인데 약성분 없음) {len(mis):,}종")
    print(f"누락   후보(비MEDICAL 인데 약성분 있음) {len(miss):,}종")

    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["standard_code", "standard_name", "criticality_current",
                     "item_group_id", "verdict", "usage_division", "drug_kind",
                     "ingredient_code"])
        wr.writerows(mis + miss)
    print(f"WROTE {OUT} ({len(mis) + len(miss):,}행)")


if __name__ == "__main__":
    main()
