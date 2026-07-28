"""약성분 원본 → 성분 마스터 + 3계층 동일군 배정 (ai#45, ai#43 선행 산출물).

2026-07-28 수신한 약성분 데이터셋(`...데이터셋(약성분)_수정.DAT`, 8컬럼 3,576,320행)을
읽어 두 산출물을 만든다.

  1. `drug_ingredient_master.csv` — 약품코드별 canonical 성분 매핑
     원본은 (기관 × 약품) 이라 같은 약품이라도 기관에 따라 성분코드가 비어 있다.
     행 단위 결측은 12.75% 지만, **기관 간 교차 보완**하면 약품코드 기준 87.49%까지
     외부 데이터 없이 자체 복구된다. 이 복구를 여기서 수행한다.

  2. `ingredient_tiers.csv` — 3계층 폴백 동일군 배정
     개선안 2(TSB-HB)의 µ̂0,g 와 #44 의 SS 풀링 단위로 **함께** 쓸 계층 정의다.
       tier1 성분군      : 같은 성분코드 = 실제 대체 가능. 단 1종짜리 군은 축소추정 불가라 제외.
       tier2 용도구분×약종류: 진료약/경구, 진료약/주사 …
       tier3 품목군        : 소모품 및 위 두 계층 미해당. 현행 item_groups 를 그대로 쓴다.
                            DB 접근이 없으면 미배정(UNASSIGNED)으로 남기고 건수만 보고한다.

원본 파서 주의: 물품명·약품명에 줄바꿈이 포함된 행이 큰따옴표로 인용돼 있어
`split('|')` 로는 행이 쪼개진다. 반드시 quotechar 를 지정한 csv.reader 를 쓴다
(backend#66 과 동일 원인).

실행:
    SSIS_DRUG_FILE=~/Downloads/SSIS_20260728/drug_ingredient.DAT \
    python3 src/loading/build_ingredient_tiers.py

    # 품목군(tier3)까지 채우려면 standard_code,item_group_id 2컬럼 CSV 를 준다
    ITEM_GROUP_CSV=data/handoff/item_groups.csv ...
"""
import collections
import csv
import io
import os

csv.field_size_limit(10 ** 9)

DRUG_FILE = os.path.expanduser(
    os.environ.get("SSIS_DRUG_FILE", "~/Downloads/SSIS_20260728/drug_ingredient.DAT")
)
ITEM_GROUP_CSV = os.environ.get("ITEM_GROUP_CSV", "")
OUT_DIR = os.environ.get("OUT_DIR", "data/handoff")

# 성분군이 동일군 역할을 하려면 최소 2종이어야 한다(1종이면 축소추정 불가).
MIN_GROUP_SIZE = int(os.environ.get("MIN_GROUP_SIZE", "2"))


def read_drug_file(path: str):
    """(기관, 약품코드) 원본을 스트리밍으로 읽는다."""
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="|", quotechar='"')
        header = [h.strip() for h in next(reader)]
        idx = {name: i for i, name in enumerate(header)}
        required = ("약품코드", "성분코드", "보건기관코드_en")
        missing = [c for c in required if c not in idx]
        if missing:
            raise SystemExit(f"[FATAL] 필수 컬럼 누락 {missing} — 실제 헤더 {header}")
        for row in reader:
            if len(row) != len(header):
                continue
            yield {name: row[i] for name, i in idx.items()}


def build_master():
    """약품코드별 canonical 성분 매핑 — 기관 간 교차 보완."""
    ing_votes = collections.defaultdict(collections.Counter)  # drug -> {ingredient_code: 기관수}
    ing_name = {}
    attrs = {}          # drug -> (약품명, 용도구분, 약종류구분, 약품단위)
    rows = 0

    for r in read_drug_file(DRUG_FILE):
        rows += 1
        drug = r["약품코드"]
        if not drug:
            continue
        if drug not in attrs:
            attrs[drug] = (
                r.get("약품명1", ""), r.get("용도구분", ""),
                r.get("약종류구분", ""), r.get("약품단위1", ""),
            )
        code = (r.get("성분코드") or "").strip()
        if code:
            ing_votes[drug][code] += 1
            if code not in ing_name:
                ing_name[code] = (r.get("성분명") or "").strip()

    master = {}
    for drug, (name, usage, kind, unit) in attrs.items():
        votes = ing_votes.get(drug)
        if votes:
            # 최다 기관이 보고한 성분코드를 대표로 삼는다(동률이면 코드 오름차순으로 고정).
            top_code, top_n = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        else:
            top_code, top_n = "", 0
        master[drug] = {
            "drug_code": drug,
            "drug_name": name,
            "usage_division": usage,
            "drug_kind": kind,
            "drug_unit": unit,
            "ingredient_code": top_code,
            "ingredient_name": ing_name.get(top_code, ""),
            "has_multiple_ingredients": "true" if votes and len(votes) > 1 else "false",
            "source_institution_count": top_n,
        }
    return master, rows


def assign_tiers(master: dict, item_group: dict):
    """3계층 폴백 배정. tier1 성분군 → tier2 용도×약종류 → tier3 품목군."""
    size = collections.Counter(
        m["ingredient_code"] for m in master.values() if m["ingredient_code"]
    )
    out = {}
    for drug, m in master.items():
        code = m["ingredient_code"]
        if code and size[code] >= MIN_GROUP_SIZE:
            tier, gid = 1, f"ING:{code}"
        elif m["usage_division"] or m["drug_kind"]:
            tier, gid = 2, f"USE:{m['usage_division']}/{m['drug_kind']}"
        else:
            gid = item_group.get(drug, "")
            tier, gid = (3, f"GRP:{gid}") if gid else (0, "UNASSIGNED")
        out[drug] = (tier, gid)
    return out, size


def main():
    print(f"원본: {DRUG_FILE}")
    master, rows = build_master()
    print(f"읽은 행수 {rows:,} / 약품코드 {len(master):,}")

    with_code = sum(1 for m in master.values() if m["ingredient_code"])
    multi = sum(1 for m in master.values() if m["has_multiple_ingredients"] == "true")
    print(
        f"성분코드 확보(교차 보완 후) {with_code:,} / {len(master):,} "
        f"= {100 * with_code / len(master):.2f}%   복수성분 약품 {multi:,}종"
    )

    item_group = {}
    if ITEM_GROUP_CSV and os.path.exists(ITEM_GROUP_CSV):
        with io.open(ITEM_GROUP_CSV, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                item_group[r["standard_code"]] = r.get("item_group_id", "")
        print(f"품목군 참조 {len(item_group):,}건 로드 ({ITEM_GROUP_CSV})")
    else:
        print("품목군 참조 없음 — tier3 는 UNASSIGNED 로 남긴다 (ITEM_GROUP_CSV 미지정)")

    tiers, group_size = assign_tiers(master, item_group)

    singles = sum(1 for c, n in group_size.items() if n < MIN_GROUP_SIZE)
    usable = len(group_size) - singles
    covered = sum(1 for t, _ in tiers.values() if t == 1)
    print(
        f"성분군 {len(group_size):,}개 (사용가능 {usable:,} / 1종짜리 {singles:,} "
        f"= {100 * singles / max(1, len(group_size)):.1f}%)"
    )
    print(f"군당 평균 약품수 {with_code / max(1, len(group_size)):.2f}종")
    dist = collections.Counter(t for t, _ in tiers.values())
    for t in (1, 2, 3, 0):
        if dist.get(t):
            label = {1: "tier1 성분군", 2: "tier2 용도×약종류", 3: "tier3 품목군", 0: "미배정"}[t]
            print(f"  {label:18s} {dist[t]:>7,}종 ({100 * dist[t] / len(tiers):5.1f}%)")
    print(f"tier1 커버 약품 {covered:,}종")

    os.makedirs(OUT_DIR, exist_ok=True)
    p1 = os.path.join(OUT_DIR, "drug_ingredient_master.csv")
    with io.open(p1, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "drug_code", "drug_name", "usage_division", "drug_kind", "drug_unit",
            "ingredient_code", "ingredient_name", "has_multiple_ingredients",
            "source_institution_count",
        ])
        w.writeheader()
        for drug in sorted(master):
            w.writerow(master[drug])
    print(f"WROTE {p1} ({len(master):,}행)")

    p2 = os.path.join(OUT_DIR, "ingredient_tiers.csv")
    with io.open(p2, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["drug_code", "tier", "group_id", "ingredient_code",
                    "usage_division", "drug_kind"])
        for drug in sorted(tiers):
            tier, gid = tiers[drug]
            m = master[drug]
            w.writerow([drug, tier, gid, m["ingredient_code"],
                        m["usage_division"], m["drug_kind"]])
    print(f"WROTE {p2} ({len(tiers):,}행)")


if __name__ == "__main__":
    main()
