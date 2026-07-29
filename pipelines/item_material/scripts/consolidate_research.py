#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6개 리서치 배치(research_batch_*.txt)를 취합해 brand_dict_extra.tsv 로 만든다.
- 신뢰도 SEARCHED->web_search_2026_07_15 / GENERAL->general_knowledge_unverified / UNRESOLVED->unresolved
- 매칭키워드 끝의 숫자(중복회피용 접미)를 떼고 dedupe
- 같은 키워드가 여러 배치에 있으면 SEARCHED 를 우선, 그다음 먼저 나온 것
"""
import csv, glob, re, os
from pathlib import Path

BASIS = {"SEARCHED": "web_search_2026_07_15",
         "GENERAL": "general_knowledge_unverified",
         "UNRESOLVED": "unresolved"}
RANK = {"web_search_2026_07_15": 0, "general_knowledge_unverified": 1, "unresolved": 2}

bundle_dir = Path(__file__).resolve().parents[1]
rows = {}  # keyword -> (fid, disp, basis, ev)
DIR = os.environ.get("PIPE_RESEARCH_DIR", str(bundle_dir / "research"))
OUT_DIR = os.environ.get("PIPE_DATA_DIR", str(bundle_dir / "data"))
OUT = os.path.join(OUT_DIR, "brand_dict_extra.tsv")

# Preserve the upstream dictionary and merge research into it. Rebuilding from
# research batches alone would silently discard all existing brand mappings.
if os.path.exists(OUT):
    with open(OUT, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            kw = row.get("keyword", "").strip()
            if kw:
                rows[kw] = (
                    row.get("family_id", "").strip(),
                    row.get("display", "").strip(),
                    row.get("basis", "general_knowledge_unverified").strip(),
                    row.get("evidence", "").strip(),
                )

initial_count = len(rows)
for path in sorted(glob.glob(os.path.join(DIR, "research_batch_*.txt"))):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 5:
                continue
            kw, fid, disp, conf, ev = parts[0], parts[1], parts[2], parts[3], parts[4]
            kw = re.sub(r"\d+$", "", kw).strip()  # 끝 숫자 접미 제거
            if len(kw) < 2:
                continue
            basis = BASIS.get(conf.upper(), "general_knowledge_unverified")
            cand = (fid, disp, basis, ev)
            if kw not in rows or RANK[basis] < RANK[rows[kw][2]]:
                rows[kw] = cand

# 길이 내림차순(더 구체적인 키워드 우선 매칭)
out = sorted(rows.items(), key=lambda kv: -len(kv[0]))
os.makedirs(OUT_DIR, exist_ok=True)
with open(OUT, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["keyword", "family_id", "display", "basis", "evidence"])
    for kw, (fid, disp, basis, ev) in out:
        w.writerow([kw, fid, disp, basis, ev])

# 통계
from collections import Counter
bc = Counter(v[2] for v in rows.values())
fc = Counter(v[0] for v in rows.values())
print("고유 키워드:", len(rows))
print("기존 사전 보존:", initial_count)
print("순증 키워드:", len(rows) - initial_count)
print("basis별:", dict(bc))
print("고유 family_id:", len(fc))
print("저장:", OUT)
# 자주 나온 family_id (클러스터 등록 검토용)
print("\n상위 family_id:")
for fid, n in fc.most_common(40):
    print(f"  {fid:45s} {n}")
