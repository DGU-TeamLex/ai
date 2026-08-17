"""정보원 제출용 Markdown 문서의 구조·각주·용어를 정적 검사한다.

데이터나 모델 산출물은 수정하지 않는다. 기본 대상은 2026-08-17 보고서, 사례 부록,
결과표 양식, GitHub 이슈 감사표다.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOCUMENTS = (
    ROOT / "docs" / "2026-08-17_03_GITHUB_ISSUE_COMMENT_ALIGNMENT.md",
    ROOT / "docs" / "2026-08-17_04_SSIS_EXPERIMENT_RESULT_REPORT.md",
    ROOT / "docs" / "2026-08-17_05_DATA_CONNECTION_AND_EXCLUSION_CASES.md",
    ROOT / "docs" / "2026-08-17_06_SSIS_RESULT_TABLE_TEMPLATE.md",
)

FOOTNOTE_REFERENCE = re.compile(r"\[\^([^\]]+)\]")
FOOTNOTE_DEFINITION = re.compile(r"^\[\^([^\]]+)\]:", re.MULTILINE)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
SECRET_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD))\s*=\s*[^\s`]+"
)

REQUIRED_HEADINGS = {
    "2026-08-17_03_GITHUB_ISSUE_COMMENT_ALIGNMENT.md": (
        "상태를 읽는 법",
        "최신 코멘트 결정표",
        "심사·제출 전에 반드시 남겨야 하는 미완료",
        "Git 반영 원칙",
    ),
    "2026-08-17_04_SSIS_EXPERIMENT_RESULT_REPORT.md": (
        "먼저 읽는 결론",
        "용어와 공식을 먼저 이해하기",
        "어떤 데이터를 사용했고, 무엇을 연결하지 못했는가",
        "문제를 해결한 흐름",
        "예측모형은 어떻게 구성했는가",
        "예측에서 제안발주량까지",
        "외부위험 병렬 실험",
        "결과 판정과 모형 선택",
        "정보원 제공 항목",
        "최종 결론",
        "각주와 참고문헌",
    ),
    "2026-08-17_05_DATA_CONNECTION_AND_EXCLUSION_CASES.md": (
        "한눈에 보는 처리 사유",
        "음수 거래는 어떻게 처리했는가",
        "과거품목 연결에서 제외한 사례",
        "외부위험이 빠졌던 이유와 현재 재실행 사례",
        "제출용 제외사유 코드 제안",
        "각주",
    ),
    "2026-08-17_06_SSIS_RESULT_TABLE_TEMPLATE.md": tuple(
        f"표 {index}." for index in range(1, 11)
    ),
}

REPORT_REQUIRED_TERMS = (
    "단순 기준예측",
    "조정 전 기준모형",
    "검증 조정모형",
    "병렬 시험값",
    "자동 권고 차단 규칙",
    "재고정책 검증 실험",
    "재사용 평가구간",
)

STALE_REPORT_FRAGMENTS = (
    "WAPE 38.12%를 35.68%",
    "동일 시험행 605,437",
    "BIAS% -9.42%",
)


def check_document(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return [f"{path}: 파일이 없습니다"]
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(ROOT)

    headings = [title.strip("` ") for _, title in HEADING.findall(text)]
    duplicate_headings = [
        title for title, count in Counter(headings).items() if count > 1
    ]
    if duplicate_headings:
        errors.append(f"{relative}: 중복 제목 {duplicate_headings}")

    for required in REQUIRED_HEADINGS.get(path.name, ()):
        if not any(required in heading for heading in headings):
            errors.append(f"{relative}: 필수 제목 누락: {required}")

    definitions = FOOTNOTE_DEFINITION.findall(text)
    references = FOOTNOTE_REFERENCE.findall(text)
    reference_set = set(references) - set(definitions)
    definition_set = set(definitions)
    undefined = sorted(reference_set - definition_set)
    unused = sorted(definition_set - set(references))
    duplicate_definitions = sorted(
        key for key, count in Counter(definitions).items() if count > 1
    )
    if undefined:
        errors.append(f"{relative}: 정의되지 않은 각주 {undefined}")
    if unused:
        errors.append(f"{relative}: 사용되지 않은 각주 {unused}")
    if duplicate_definitions:
        errors.append(f"{relative}: 중복 각주 정의 {duplicate_definitions}")

    for target in MARKDOWN_LINK.findall(text):
        clean = target.strip().split("#", 1)[0]
        if not clean or re.match(r"^(?:https?://|mailto:)", clean):
            continue
        candidate = (path.parent / clean).resolve()
        if not candidate.exists():
            errors.append(f"{relative}: 깨진 로컬 링크: {target}")

    if SECRET_ASSIGNMENT.search(text):
        errors.append(f"{relative}: 비밀값처럼 보이는 환경변수 대입 발견")

    if path.name == "2026-08-17_04_SSIS_EXPERIMENT_RESULT_REPORT.md":
        for term in REPORT_REQUIRED_TERMS:
            if term not in text:
                errors.append(f"{relative}: 통일 용어 누락: {term}")
        for fragment in STALE_REPORT_FRAGMENTS:
            if fragment in text:
                errors.append(f"{relative}: 오래된 성능문구 잔류: {fragment}")
        if "음수 only" in text:
            errors.append(f"{relative}: 혼합 용어 '음수 only' 사용")
        if "최종 독립 테스트" in text:
            errors.append(
                f"{relative}: 이미 본 평가기간을 최종 독립 테스트로 오해할 표현"
            )

    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    paths = [
        path if path.is_absolute() else ROOT / path
        for path in (args.paths or list(DEFAULT_DOCUMENTS))
    ]
    errors = [error for path in paths for error in check_document(path)]
    if errors:
        print("DOCUMENT CHECK FAILED")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print(f"DOCUMENT CHECK PASSED: {len(paths)} files")


if __name__ == "__main__":
    main()
