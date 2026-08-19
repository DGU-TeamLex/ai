"""재현 산출물에 로컬 사용자 절대경로를 남기지 않는 경로 도우미."""

from __future__ import annotations

from pathlib import Path


def portable_artifact_path(path: Path, project_root: Path) -> str:
    """프로젝트 내부 경로는 POSIX 형식 상대경로로 기록한다.

    프로젝트 밖 입력은 사용자 홈 경로를 노출하지 않도록 파일명만 기록한다.
    """

    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return f"external/{resolved.name}"
