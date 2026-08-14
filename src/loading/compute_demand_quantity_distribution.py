"""출고 건당 수량 분포로 포아송 가정을 검증한다 (ai#44).

개선안 1(Robbins 추정량)은 수요가 **포아송 과정**을 따른다고 전제한다. 그 전제의 핵심은
"사건이 한 번에 1단위씩 발생한다"는 것이다. 보건소 물품은 1회에 여러 개를 한꺼번에
출고하는 경우가 많을 것으로 보여, 적용 전에 실측으로 확인해야 한다.

수량이 1 에 몰려 있으면 단순 포아송이 성립하고, 넓게 퍼져 있으면 **복합 포아송
(compound Poisson)** 이 맞다 — 후자면 Robbins 를 그대로 쓸 수 없고 건수(발생 빈도)와
건당 수량(크기)을 분리해 모델링해야 한다.

원장 한 행 = (기관, 부서, 물품, 재고마감일) 하루치이므로, `정상출고량 > 0` 인 행 하나를
**출고 1건**으로 본다.

실행:
    SSIS_STOCK_GLOB='~/Downloads/SSIS_20260728/stock_*.DAT' \
    python3 src/loading/compute_demand_quantity_distribution.py
"""
import collections
import csv
import glob
import io
import os

csv.field_size_limit(10 ** 9)

STOCK_GLOB = os.path.expanduser(
    os.environ.get("SSIS_STOCK_GLOB", "~/Downloads/SSIS_20260728/stock_*.DAT")
)
COL_QTY = "정상출고량"


def quantiles(sorted_vals, total, qs):
    """정렬된 (값, 빈도) 목록에서 분위수를 뽑는다(전량 메모리 적재 회피)."""
    out, seen, i = {}, 0, 0
    targets = [(q, q * total) for q in qs]
    for val, cnt in sorted_vals:
        seen += cnt
        while i < len(targets) and seen >= targets[i][1]:
            out[targets[i][0]] = val
            i += 1
        if i >= len(targets):
            break
    return out


def main():
    files = sorted(glob.glob(STOCK_GLOB))
    if not files:
        raise SystemExit(f"[FATAL] 원본을 찾지 못함: {STOCK_GLOB}")
    print(f"대상 파일 {len(files)}개")

    freq = collections.Counter()   # 출고 수량 -> 건수
    rows = zero = 0
    total_qty = 0.0
    non_integer_events = 0

    for path in files:
        with io.open(path, "r", encoding="utf-8", newline="") as f:
            r = csv.reader(f, delimiter="|", quotechar='"')
            header = [h.strip() for h in next(r)]
            if COL_QTY not in header:
                raise SystemExit(f"[FATAL] {COL_QTY} 컬럼 없음 — {header}")
            qi = header.index(COL_QTY)
            ncol = len(header)
            for row in r:
                if len(row) != ncol:
                    continue
                rows += 1
                raw = row[qi]
                if not raw:
                    zero += 1
                    continue
                try:
                    q = float(raw)
                except ValueError:
                    continue
                if q <= 0:
                    zero += 1
                    continue
                total_qty += q
                if not q.is_integer():
                    non_integer_events += 1
                # 소수 수량도 원본 값 그대로 보존한다. int(q)는 0.5를 0으로 바꾸고
                # 분위수와 "수량=1" 비중을 왜곡한다.
                freq[q] += 1

    events = sum(freq.values())
    print(f"전체 행 {rows:,}  출고 발생 {events:,}건  출고 0/결측 {zero:,}")
    if not events:
        raise SystemExit("출고 건이 없음")

    print(f"출고 총량 {total_qty:,.0f}  건당 평균 {total_qty / events:.2f}")
    print(
        f"소수 수량 {non_integer_events:,}건 "
        f"({100 * non_integer_events / events:.2f}%)"
    )

    ones = freq.get(1, 0)
    le5 = sum(c for q, c in freq.items() if q <= 5)
    le10 = sum(c for q, c in freq.items() if q <= 10)
    print(f"\n건당 수량 = 1  : {ones:,}건 ({100 * ones / events:.1f}%)  ← 포아송 전제의 핵심")
    print(f"건당 수량 ≤ 5  : {le5:,}건 ({100 * le5 / events:.1f}%)")
    print(f"건당 수량 ≤ 10 : {le10:,}건 ({100 * le10 / events:.1f}%)")

    ordered = sorted(freq.items())
    qs = quantiles(ordered, events, [0.25, 0.50, 0.75, 0.90, 0.99])
    print("분위수: " + "  ".join(f"P{int(k * 100)}={v:,}" for k, v in sorted(qs.items())))
    print(f"최대 건당 수량 {max(freq):,}")

    print("\n상위 빈도 수량 (건수 기준)")
    for q, c in freq.most_common(10):
        print(f"  수량 {q:>6,} : {c:>9,}건 ({100 * c / events:4.1f}%)")

    verdict = "성립" if ones / events >= 0.5 else "불성립"
    print(
        f"\n판정: '한 번에 1개씩' 전제 {verdict} "
        f"(수량 1 비중 {100 * ones / events:.1f}%)"
    )
    if verdict == "불성립":
        print("  → 단순 포아송 부적합. 복합 포아송(발생 빈도 × 건당 수량 분리) 필요.")


if __name__ == "__main__":
    main()
