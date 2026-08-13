import json
import logging
from pathlib import Path

import pandas as pd


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def ensure_dirs(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class EmptyOutputRefused(RuntimeError):
    """산출이 비어 기존 파일 덮어쓰기를 거부했다."""


def guard_not_empty(frame: pd.DataFrame, path: Path, label: str = "") -> pd.DataFrame:
    """빈 산출로 기존 파일을 덮어쓰는 것을 막는다.

    실측 사고(2026-08-13): 원자재 위험 재점수화에서 상류 가격 수집이 0행을
    반환했는데 `to_csv` 가 그대로 실행되어 1.5GB 감사 파일이 헤더만 남고
    날아갔다. 계산은 예외 없이 끝났고 로그도 "Saved ... (0 rows)" 였다.

    빈 산출은 정상 결과가 아니라 상류 실패의 징후다. 기존 파일이 있는데
    새 산출이 비면 **쓰지 않고 중단** 한다. 파일이 아직 없으면 최초 생성이므로
    빈 산출도 허용한다(스키마만 있는 파일이 필요한 경우가 있다).

    합성 뉴스 차단(ai#22)과 같은 fail-closed 원칙이다.
    """
    if not frame.empty or not path.exists():
        return frame
    raise EmptyOutputRefused(
        f"{label or path.name}: 산출이 0행인데 기존 파일이 있다({path}). "
        "덮어쓰지 않고 중단한다. 상류 입력이 비지 않았는지 확인하라."
    )


def read_year_month(series: pd.Series) -> pd.Series:
    values = series.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    return pd.to_datetime(values, format="%Y%m").dt.to_period("M").dt.to_timestamp()


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.replace(0, pd.NA)
    return (numerator / denominator).astype("float64").fillna(0.0)

