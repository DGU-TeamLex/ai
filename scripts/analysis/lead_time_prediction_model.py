"""리드타임을 분위수 조회가 아니라 **모형으로 예측** 할 수 있는가 (ai#20).

## 지금 방식의 한계

현행 정책은 전체 리드타임 분포에서 분위수를 뽑아 쓴다. 품목이 무엇이든,
기관이 어디든, 언제든 같은 분포를 본다. 위험 점수만이 분위수를 움직인다.

그런데 실측 분포가 이렇다.

    p50 30   p60 30   p70 30   p75 30   p80 31   p90 60   p95 94

**하위 80% 에 변동이 아예 없다.** 계약 납기를 관행적으로 30일로 찍기 때문이다.
정보가 꼬리에만 있으므로 어떤 단조 매핑도 그 구간에서는 평평하다.

## 그래서 묻는 것

"리드타임이 30일을 넘을 것인가" 를 **품목·기관·시점 특성으로 예측할 수 있는가.**

분위수 조회는 조건부 정보를 안 쓴다. 특정 품목군이나 특정 기관이 체계적으로
오래 걸린다면, 그것을 알고 있는 편이 전체 분포를 보는 것보다 낫다.

## 판정 기준 (작업 시작 전 고정)

    ① 이항 분류(L > 30) AUC 가 0.60 이상       — 무작위(0.5)보다 의미 있게 나은가
    ② 분위수 회귀 pinball loss 가 무조건부 분위수보다 낮은가
    ③ 어떤 특성이 기여하는지 해석 가능한가

셋 다 실패하면 "리드타임은 조건부로 예측되지 않는다" 가 결론이고, 현행 분위수
조회를 유지하는 근거가 된다. 실패도 결과다.

## 설계

* 표본은 조달청 납품요구 24개월 전수. 유효 리드타임 31,898건.
* 시간 분할한다. 학습 2024-01~2025-06, 검증 2025-07~2025-12.
  무작위 분할하면 같은 계약의 앞뒤가 갈려 누수가 된다.
* 특성은 발주 시점에 **알 수 있는 것만** 쓴다. 납기는 타깃이므로 제외한다.

근거: Koenker & Bassett (1978) 분위수 회귀; 시계열 분할은 Bergmeir & Benítez
(2012) *Information Sciences* 191:192-213.

실행:
    python scripts/analysis/lead_time_prediction_model.py
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

RAW_GLOB = "procurement_delivery_requests*.jsonl"
OUT_PATH = ROOT / "outputs" / "lead_time_prediction_report.json"
TRAIN_END = "2025-06"
LEAD_MIN, LEAD_MAX = 0, 365
THRESHOLD_DAYS = 30
MIN_AUC = 0.60


def _load() -> pd.DataFrame:
    rows = []
    for path in (ROOT / "data" / "processed").glob(RAW_GLOB):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rows.append(record)
    frame = pd.DataFrame(rows).drop_duplicates("dlvrReqNo")
    frame["rcpt"] = pd.to_datetime(frame["dlvrReqRcptDate"], errors="coerce")
    frame["due"] = pd.to_datetime(frame["maxDlvrTmlmtDate"], errors="coerce")
    frame["lead_days"] = (frame["due"] - frame["rcpt"]).dt.days
    frame = frame.dropna(subset=["rcpt", "lead_days"])
    return frame[frame["lead_days"].between(LEAD_MIN, LEAD_MAX)].copy()


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    """발주 시점에 알 수 있는 것만 쓴다. 납기는 타깃이라 제외한다."""
    out = pd.DataFrame(index=frame.index)
    out["month"] = frame["rcpt"].dt.month
    out["quarter"] = frame["rcpt"].dt.quarter
    out["day_of_month"] = frame["rcpt"].dt.day
    # 연말 집중 발주는 납기가 촉박할 수 있다.
    out["is_year_end"] = frame["rcpt"].dt.month.isin([11, 12]).astype(int)
    qty = pd.to_numeric(frame.get("dlvrReqQty"), errors="coerce").fillna(0)
    amount = pd.to_numeric(frame.get("dlvrReqAmt"), errors="coerce").fillna(0)
    out["log_qty"] = np.log1p(qty.clip(lower=0))
    out["log_amount"] = np.log1p(amount.clip(lower=0))
    out["unit_price"] = np.log1p((amount / qty.replace(0, np.nan)).fillna(0).clip(lower=0))
    # 최종 납품요구 여부 — 분할 납품이면 납기 구조가 다를 수 있다.
    out["is_final"] = (frame.get("fnlDlvrReqYn", "").astype(str) == "Y").astype(int)
    return out


def _target_encode(
    train: pd.Series,
    target: pd.Series,
    apply_to: pd.Series,
    prior_weight: float = 20.0,
) -> pd.Series:
    """범주형을 평균 인코딩한다. 표본이 적은 범주는 전체 평균으로 수축한다.

    Bühlmann-Straub 신뢰도와 같은 형태다. 학습 구간 통계만 쓰므로 검증 구간
    정보가 새지 않는다.
    """
    grand = float(target.mean())
    stats = target.groupby(train).agg(["mean", "size"])
    shrunk = (stats["mean"] * stats["size"] + grand * prior_weight) / (
        stats["size"] + prior_weight
    )
    return apply_to.map(shrunk).fillna(grand)


def main() -> None:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    frame = _load()
    month = frame["rcpt"].dt.strftime("%Y-%m")
    is_train = month <= TRAIN_END
    print(f"표본 {len(frame):,}건  학습 {int(is_train.sum()):,} / 검증 {int((~is_train).sum()):,}")
    print(f"리드타임 median {frame['lead_days'].median():.0f}일  "
          f"> {THRESHOLD_DAYS}일 비율 {frame['lead_days'].gt(THRESHOLD_DAYS).mean():.1%}\n")

    y = frame["lead_days"].gt(THRESHOLD_DAYS).astype(int)
    X = _features(frame)
    # 범주형 2종을 학습 구간 통계로 인코딩한다.
    for column, source in (("item_te", "rprsntDtilPrdctClsfcNo"), ("inst_te", "dminsttCd")):
        key = frame[source].astype(str)
        X[column] = _target_encode(key[is_train], y[is_train], key)

    model = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.06, max_leaf_nodes=31,
        min_samples_leaf=50, l2_regularization=1.0, random_state=42,
    )
    model.fit(X[is_train], y[is_train])
    proba = model.predict_proba(X[~is_train])[:, 1]
    auc = float(roc_auc_score(y[~is_train], proba))

    print("=== ① 이항 분류 (L > 30일) ===")
    print(f"  검증 AUC = {auc:.4f}   (기준 {MIN_AUC}, 무작위 0.5)")
    base_rate = float(y[~is_train].mean())
    print(f"  검증 구간 양성률 {base_rate:.1%}")

    # 특성 하나씩 빼서 기여를 본다. permutation 은 표본이 커 시간이 걸리므로
    # 단일 특성 모델의 AUC 로 대신한다 — 해석이 목적이다.
    print("\n=== ③ 특성별 단독 AUC ===")
    singles = {}
    for column in X.columns:
        single = HistGradientBoostingClassifier(
            max_iter=120, learning_rate=0.1, min_samples_leaf=50, random_state=42,
        )
        single.fit(X.loc[is_train, [column]], y[is_train])
        singles[column] = float(
            roc_auc_score(y[~is_train], single.predict_proba(X.loc[~is_train, [column]])[:, 1])
        )
    for column, value in sorted(singles.items(), key=lambda kv: -kv[1]):
        print(f"  {column:<16}{value:.4f}")

    # ② 분위수 회귀 — 무조건부 분위수 대비 pinball loss
    print("\n=== ② 분위수 회귀 vs 무조건부 분위수 ===")
    from sklearn.ensemble import HistGradientBoostingRegressor

    target = frame["lead_days"].astype(float)
    results = {}
    for tau in (0.5, 0.75, 0.9):
        regressor = HistGradientBoostingRegressor(
            loss="quantile", quantile=tau, max_iter=300, learning_rate=0.06,
            min_samples_leaf=50, random_state=42,
        )
        regressor.fit(X[is_train], target[is_train])
        predicted = regressor.predict(X[~is_train])
        constant = float(target[is_train].quantile(tau))

        def pinball(actual, forecast):
            delta = actual - forecast
            return float(np.mean(np.maximum(tau * delta, (tau - 1) * delta)))

        model_loss = pinball(target[~is_train].to_numpy(), predicted)
        base_loss = pinball(target[~is_train].to_numpy(), np.full(len(predicted), constant))
        gain = (base_loss - model_loss) / base_loss
        results[f"q{int(tau*100)}"] = {
            "model_pinball": model_loss, "constant_pinball": base_loss,
            "constant_value": constant, "improvement": gain,
        }
        print(f"  q{int(tau*100)}  모형 {model_loss:.4f}  무조건부 {base_loss:.4f}"
              f"  개선 {gain:+.2%}   (상수 {constant:.0f}일)")

    verdict = {
        "auc_pass": auc >= MIN_AUC,
        "pinball_pass": any(v["improvement"] > 0.01 for v in results.values()),
    }
    print("\n=== 판정 ===")
    print(f"  ① AUC ≥ {MIN_AUC}          {'통과' if verdict['auc_pass'] else '실패'}  ({auc:.4f})")
    print(f"  ② pinball 1% 이상 개선   {'통과' if verdict['pinball_pass'] else '실패'}")
    if not any(verdict.values()):
        print("\n  리드타임은 조건부로 예측되지 않는다. 현행 분위수 조회를 유지하는 근거다.")
    else:
        print("\n  조건부 정보가 있다. 정책에 반영할 여지가 있다.")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(
            {
                "samples": int(len(frame)), "train": int(is_train.sum()),
                "valid": int((~is_train).sum()), "threshold_days": THRESHOLD_DAYS,
                "auc": auc, "base_rate": base_rate,
                "single_feature_auc": singles, "quantile_regression": results,
                "verdict": verdict,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
