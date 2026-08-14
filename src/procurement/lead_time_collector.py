"""조달청 나라장터 종합쇼핑몰 납품요구 수집 — 실측 리드타임 산출용.

## 왜 필요한가

재고 정책의 리드타임 L 은 "발주 시점부터 입고까지의 총 대기시간"이다
(클라이언트 확인 정의). 그런데 원장에는 발주일 필드가 없다. 컬럼은
재고마감일 하나뿐이라, 원장으로 잴 수 있는 건

    M = 입고일 − 직전 거래일   (품절 상태에서의 입고)

이고 발주 시점은 그 구간 안 어딘가다. 즉 M ≥ L 인 상한일 뿐이다.
`supply_risk_level_policy.json` 의 method="stockout_duration_p25" 도 M 의
p25 를 L 의 대용으로 쓰는 heuristic이지 L 을 식별한 게 아니다.

조달청 납품요구 데이터에는 두 날짜가 모두 있어 L 을 직접 얻는다.

    L_계약 = maxDlvrTmlmtDate(납품기한) − dlvrReqRcptDate(납품요구접수일)

## 조인 제약

원장의 기관코드(`보건기관코드_en`)는 비식별화되어 있어(예: `P;485`, `R4<4<`)
수요기관코드와 매칭할 수 없다. 반면 응답의 `dminsttNm`(수요기관명)은 평문이라
**모집단을 보건소로 좁히는 것**은 가능하다. 따라서 발주 건별 짝짓기는 포기하고
세부품명(`rprsntDtilPrdctClsfcNo`) 단위 분포로 집계한다. 우리가 정책에 넣을
값도 품목별 L 이므로 이 단위로 충분하다.

## 한계 (결과 해석 시 반드시 같이 읽을 것)

L_계약은 **계약상 납기**이지 실제 도착일이 아니다. 납기를 넘겨 도착하면
과소추정이 된다. 원장과 기관 조인이 불가능하므로 이 차이는 여기서 검증할 수
없다. 결과는 "계약 기준 L" 로 못박아 보고하고, 정책 반영 시 지연 마진을
별도로 얹어야 한다.

실행:
    python -m src.procurement.lead_time_collector --start 2024-01 --end 2025-06
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from ..config import OUTPUT_DIR, PROCESSED_DATA_DIR
from ..utils import ensure_dirs

LOGGER = logging.getLogger(__name__)

ENDPOINT = (
    "https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService/getDlvrReqInfoList"
)
# 관세청 수집기와 같은 이유로 요청 예산을 강제한다. 전국 납품요구는 5일에
# 약 27,000건이라 무제한으로 돌리면 일 할당량을 태운다.
DEFAULT_MAX_REQUESTS = 3000
PAGE_SIZE = 999
# data.go.kr 공통 트래픽 제한. 0.2초 간격 + 백오프 1·2·4초로는 429 를 못 넘겼다
# (2024-08 page=22 에서 중단). 간격을 늘리고 429 는 별도로 길게 기다린다.
REQUEST_INTERVAL_SEC = 0.6
MAX_RETRIES = 6
THROTTLE_BACKOFF_SEC = 30

RAW_PATH = PROCESSED_DATA_DIR / "procurement_delivery_requests.jsonl"
PROGRESS_PATH = PROCESSED_DATA_DIR / "procurement_collection_progress.json"
SUMMARY_PATH = OUTPUT_DIR / "procurement_lead_time_by_item.csv"

# 지역보건의료기관. `dminsttNm` 이 평문이라 이름으로 거른다.
# 보건지소·보건진료소까지 포함해야 원장 모집단(3,598곳)과 층이 맞는다.
HEALTH_CENTER_TOKENS = ("보건소", "보건지소", "보건진료소", "보건의료원")

KEEP_FIELDS = [
    "dlvrReqNo",
    "dlvrReqRcptDate",
    "maxDlvrTmlmtDate",
    "dlvrReqNm",
    "dlvrReqQty",
    "dlvrReqAmt",
    "dminsttCd",
    "dminsttNm",
    "dmndInsttDivNm",
    "dminsttRgnNm",
    "corpNm",
    "rprsntPrdctClsfcNo",
    "rprsntPrdctClsfcNoNm",
    "rprsntDtilPrdctClsfcNo",
    "rprsntDtilPrdctClsfcNoNm",
    "cntrctNo",
    "fnlDlvrReqYn",
]


class DailyQuotaExhausted(RuntimeError):
    """일일 할당량 소진. 기다려서 풀리지 않으므로 즉시 중단한다.

    처음엔 429 를 전부 레이트리밋으로 보고 30~180초씩 백오프했는데, 응답 헤더에
    `X-RateLimit-Remaining: 0` 이 이미 찍혀 있었다. 할당량 소진은 자정(KST)
    리셋까지 풀리지 않으므로 대기는 순수 낭비다. 두 경우를 구분한다.
    """


class RequestBudgetExceeded(RuntimeError):
    """예산을 넘기면 조용히 자르지 않고 실패시킨다.

    관세청 수집기에서 캐시 조기반환이 신규 코드를 조용히 건너뛰어
    외부 신호가 전부 0 이 된 적이 있다(ai#71). 부분 수집을 완전한 수집처럼
    보이게 하는 실패 방식은 반복하지 않는다.
    """


def _service_key() -> str:
    import os

    key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "DATA_GO_KR_SERVICE_KEY 가 없다. .env 에 설정하고, 해당 API 에 "
            "활용신청이 승인되었는지 확인할 것 "
            "(미승인 시 403 SERVICE_KEY_IS_NOT_REGISTERED_ERROR)."
        )
    return key


def _month_windows(start: str, end: str) -> list[tuple[str, str]]:
    """[start, end] 월 구간을 월 단위 조회창으로 자른다."""
    begin = date.fromisoformat(f"{start}-01")
    stop = date.fromisoformat(f"{end}-01")
    windows = []
    cursor = begin
    while cursor <= stop:
        if cursor.month == 12:
            nxt = date(cursor.year + 1, 1, 1)
        else:
            nxt = date(cursor.year, cursor.month + 1, 1)
        windows.append((cursor.strftime("%Y%m%d"), (nxt - timedelta(days=1)).strftime("%Y%m%d")))
        cursor = nxt
    return windows


def _fetch_page(key: str, begin: str, end: str, page: int) -> dict:
    params = urllib.parse.urlencode(
        {
            "serviceKey": key,
            "pageNo": page,
            "numOfRows": PAGE_SIZE,
            "type": "json",
            "inqryDiv": 1,  # 1 = 납품요구접수일자 기준 조회
            "inqryBgnDate": begin,
            "inqryEndDate": end,
        },
        safe="%",
    )
    url = f"{ENDPOINT}?{params}"
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429 and exc.headers.get("X-RateLimit-Remaining") == "0":
                raise DailyQuotaExhausted(
                    f"일일 할당량 소진 (한도 {exc.headers.get('X-RateLimit-Limit')}). "
                    f"{begin}~{end} page={page} 에서 중단. "
                    f"자정(KST) 리셋 후 같은 명령으로 재실행하면 이어받는다."
                ) from exc
            if exc.code == 429:
                # 레이트리밋은 짧은 지수 백오프로 안 풀린다. 창을 길게 잡는다.
                wait = THROTTLE_BACKOFF_SEC * (attempt + 1)
                LOGGER.warning(
                    "429 레이트리밋 %s~%s page=%s — %s초 대기 (재시도 %s/%s)",
                    begin, end, page, wait, attempt + 1, MAX_RETRIES,
                )
                time.sleep(wait)
            else:
                time.sleep(2**attempt)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            last_error = exc
            time.sleep(2**attempt)
    else:
        raise RuntimeError(f"조회 실패 {begin}~{end} page={page}: {last_error}")

    header = payload.get("response", {}).get("header", {})
    if header.get("resultCode") not in {"00", "0"}:
        # 정상이 아닌 응답을 빈 결과로 흘려보내면 "수집됐는데 0건" 이 된다.
        raise RuntimeError(
            f"API 오류 {begin}~{end} page={page}: "
            f"{header.get('resultCode')} {header.get('resultMsg')}"
        )
    return payload["response"]["body"]


def _is_health_center(name: str) -> bool:
    return any(token in name for token in HEALTH_CENTER_TOKENS)


def _completed_months() -> set[str]:
    """수집을 끝까지 마친 달(YYYY-MM). 할당량 소진 뒤 이어받기 위한 것.

    "마지막 달을 무조건 미완료로 본다" 는 방식은 쓰지 않는다. 일 단위로 나눠
    받는 운용에서는 재실행마다 정상 완료된 달까지 버려서, 할당량을 쓰고도
    진도가 안 나간다(실제로 7월치 1,054건이 그렇게 날아갔다).

    대신 달을 마칠 때마다 진행 파일에 기록하고 그것을 신뢰한다.
    """
    if not PROGRESS_PATH.exists():
        return set()
    try:
        return set(json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))["completed_months"])
    except (json.JSONDecodeError, KeyError):
        return set()


def _mark_month_complete(month: str) -> None:
    done = _completed_months()
    done.add(month)
    PROGRESS_PATH.write_text(
        json.dumps({"completed_months": sorted(done)}, indent=2),
        encoding="utf-8",
    )


def collect(start: str, end: str, max_requests: int, resume: bool = True) -> Path:
    key = _service_key()
    ensure_dirs(PROCESSED_DATA_DIR)
    requests_used = 0
    kept = 0
    scanned = 0

    done = _completed_months() if resume else set()
    if done and RAW_PATH.exists():
        LOGGER.info("이어받기: 완료된 %s개월 건너뜀 (%s ~ %s)",
                    len(done), min(done), max(done))
        # 완료 기록이 없는 달 = 중단된 달의 부분 수집분. 그대로 두고 append 하면
        # 그 달이 중복 계상되어 분포가 왜곡되므로 버린다.
        kept_lines = [
            line
            for line in RAW_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
            if (json.loads(line).get("dlvrReqRcptDate") or "")[:7] in done
        ]
        dropped = len(RAW_PATH.read_text(encoding="utf-8").splitlines()) - len(kept_lines)
        RAW_PATH.write_text("".join(kept_lines), encoding="utf-8")
        kept = len(kept_lines)
        LOGGER.info("기존 %s건 유지, 미완료 달 부분수집분 %s건 폐기",
                    f"{kept:,}", f"{dropped:,}")

    mode = "a" if (resume and done) else "w"
    with RAW_PATH.open(mode, encoding="utf-8") as sink:
        for begin, finish in _month_windows(start, end):
            if f"{begin[:4]}-{begin[4:6]}" in done:
                continue
            page = 1
            while True:
                if requests_used >= max_requests:
                    raise RequestBudgetExceeded(
                        f"요청 예산 {max_requests} 초과 ({begin} 처리 중). "
                        f"--max-requests 를 올리거나 구간을 줄일 것. "
                        f"현재까지 스캔 {scanned:,}건 / 보건기관 {kept:,}건."
                    )
                body = _fetch_page(key, begin, finish, page)
                requests_used += 1
                total = int(body.get("totalCount", 0))
                items = body.get("items") or []
                if isinstance(items, dict):
                    items = [items]
                for row in items:
                    scanned += 1
                    if not _is_health_center(str(row.get("dminsttNm", ""))):
                        continue
                    sink.write(
                        json.dumps(
                            {field: row.get(field) for field in KEEP_FIELDS},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    kept += 1
                if page * PAGE_SIZE >= total or not items:
                    # 마지막 페이지까지 받은 시점에만 완료로 기록한다.
                    # 중간에 할당량이 끊기면 기록되지 않아 다음 실행이 재수집한다.
                    sink.flush()
                    _mark_month_complete(f"{begin[:4]}-{begin[4:6]}")
                    LOGGER.info(
                        "%s~%s: total=%s pages=%s 누적 보건기관 %s건",
                        begin, finish, f"{total:,}", page, f"{kept:,}",
                    )
                    break
                page += 1
                time.sleep(REQUEST_INTERVAL_SEC)

    LOGGER.info(
        "수집 완료: 요청 %s회 / 스캔 %s건 / 보건기관 %s건 → %s",
        requests_used, f"{scanned:,}", f"{kept:,}", RAW_PATH,
    )
    return RAW_PATH


def summarise() -> Path:
    import pandas as pd

    if not RAW_PATH.exists():
        raise FileNotFoundError(f"수집 결과가 없다: {RAW_PATH}. 먼저 collect 를 실행할 것.")
    frame = pd.read_json(RAW_PATH, lines=True, dtype={"rprsntDtilPrdctClsfcNo": "string"})
    if frame.empty:
        raise ValueError("보건기관 납품요구가 0건이다. 수집 구간과 기관명 필터를 확인할 것.")

    # 응답 날짜는 조회 파라미터(YYYYMMDD)와 달리 하이픈 형식(2024-01-02)이다.
    # 결측은 '--' 로 온다(납품기한 미기재 건). 그것만 결측 처리하고,
    # 그 밖의 파싱 실패는 형식 변경 신호이므로 조용히 넘기지 않는다.
    missing_marker = frame[["dlvrReqRcptDate", "maxDlvrTmlmtDate"]].isin(["--", "", None])
    for column in ("dlvrReqRcptDate", "maxDlvrTmlmtDate"):
        frame.loc[missing_marker[column], column] = None
        parsed = pd.to_datetime(frame[column], format="%Y-%m-%d", errors="coerce")
        unparsed = parsed.isna() & frame[column].notna()
        if unparsed.any():
            # 조용히 NaT 로 흘리면 "수집은 됐는데 유효 0건" 이 된다. 드러내고 멈춘다.
            raise ValueError(
                f"{column} 파싱 실패 {int(unparsed.sum()):,}/{len(frame):,}건. "
                f"예시: {frame.loc[unparsed, column].head(3).tolist()}"
            )
        frame[column] = parsed
    frame["lead_time_days"] = (
        frame["maxDlvrTmlmtDate"] - frame["dlvrReqRcptDate"]
    ).dt.days

    before = len(frame)
    # 음수는 데이터 오류, 365 초과는 단가계약성 장기건이라 단발 조달 리드타임이 아니다.
    frame = frame[frame["lead_time_days"].between(0, 365)]
    LOGGER.info("리드타임 유효 %s/%s건", f"{len(frame):,}", f"{before:,}")

    grouped = frame.groupby(["rprsntDtilPrdctClsfcNo", "rprsntDtilPrdctClsfcNoNm"])[
        "lead_time_days"
    ]
    summary = pd.DataFrame(
        {
            "n": grouped.size(),
            "mean": grouped.mean().round(1),
            "p25": grouped.quantile(0.25),
            "median": grouped.median(),
            "p75": grouped.quantile(0.75),
            "p90": grouped.quantile(0.90),
        }
    ).reset_index().sort_values("n", ascending=False)

    ensure_dirs(OUTPUT_DIR)
    summary.to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig")

    overall = frame["lead_time_days"]
    LOGGER.info(
        "전체 L_계약: n=%s p25=%.0f median=%.0f p75=%.0f p90=%.0f (현행 fallback 15일)",
        f"{len(overall):,}",
        overall.quantile(0.25), overall.median(),
        overall.quantile(0.75), overall.quantile(0.90),
    )
    LOGGER.info("저장: %s (%s개 세부품명)", SUMMARY_PATH, f"{len(summary):,}")
    return SUMMARY_PATH


def main() -> None:
    parser = argparse.ArgumentParser(
        description="조달청 납품요구에서 실측 리드타임(L_계약) 산출",
    )
    parser.add_argument("--start", default="2024-01", help="시작 월 YYYY-MM")
    parser.add_argument("--end", default="2025-06", help="종료 월 YYYY-MM")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument(
        "--summarise-only",
        action="store_true",
        help="수집을 건너뛰고 기존 원본에서 집계만 다시 한다",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not args.summarise_only:
        try:
            collect(args.start, args.end, args.max_requests)
        except DailyQuotaExhausted as exc:
            # 실패가 아니라 예정된 중단이다. 일 단위로 나눠 받는 운용에서
            # 이걸 exit!=0 으로 내면 스케줄러가 매일 경보를 울린다.
            LOGGER.warning("%s", exc)
            LOGGER.warning("여기까지 받은 분량으로 집계만 갱신한다.")
    summarise()


if __name__ == "__main__":
    main()
