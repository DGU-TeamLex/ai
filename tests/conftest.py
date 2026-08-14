"""테스트가 개발자 로컬 `.env` 에 오염되지 않게 한다.

## 무엇이 문제였나

`src/config.py` 는 모듈 최상위에서 `.env` 를 읽어 `os.environ` 을 직접 고친다.

    DOTENV_LOADED_KEYS = load_dotenv()      # import 시점에 실행
    ...
    os.environ[key] = value                 # 프로세스 전역 상태 변경

즉 `import src.config` 한 줄로 개발자의 `.env` 값이 프로세스에 주입된다.
테스트가 그 아래 어떤 모듈이든 import 하면 같은 일이 일어난다.

실제로 `test_csv_provider_requires_path` 가 이것 때문에 실패했다.

    기대  ValueError("NEWS_DATA_PATH is required when NEWS_PROVIDER=csv")
    실제  FileNotFoundError — .env 의 NEWS_DATA_PATH 가 이미 들어와 있어
          경로가 있는 것으로 보였다

## 왜 그냥 두면 안 되나

* **같은 커밋이 사람마다 통과/실패한다.** `.env` 를 가진 개발자와 없는 개발자,
  `.env` 가 없는 CI 가 서로 다른 결과를 본다.
* import 순서에 따라 값이 있을 수도 없을 수도 있어 재현이 안 된다.
* 아이러니하게도 이 자동 로드는 "설정이 조용히 안 먹는" 문제를 막으려고
  넣은 것인데(ai#71), 이번엔 "설정이 조용히 먹어서" 테스트가 거짓 결과를 냈다.

## 어떻게 막나

conftest 는 테스트 모듈보다 먼저 import 된다. 여기서 `src.config` 를 한 번
불러 `.env` 로드를 **일으킨 뒤 그 키들을 지운다.** load_dotenv 는 최초 import
때 한 번만 돌므로, 이후 어떤 모듈을 import 해도 다시 주입되지 않는다.

`.env` 값이 꼭 필요한 테스트는 `patch.dict` 로 직접 넣으면 된다. 그 편이
무엇에 의존하는지 코드에 드러나므로 낫다.
"""
import os

import pytest


def _purge_dotenv_injected_keys() -> list[str]:
    """`.env` 가 주입한 키만 골라 지운다. 쉘이 준 값은 건드리지 않는다."""
    from src.config import DOTENV_LOADED_KEYS

    removed = []
    for key in DOTENV_LOADED_KEYS:
        if key in os.environ:
            del os.environ[key]
            removed.append(key)
    return removed


_PURGED = _purge_dotenv_injected_keys()


@pytest.fixture(autouse=True)
def _isolate_dotenv():
    """테스트 중에 누가 다시 로드해도 끝나면 원상복구한다.

    `load_dotenv()` 를 직접 부르는 테스트가 있을 수 있으므로 사후에도 정리한다.
    """
    yield
    _purge_dotenv_injected_keys()


def pytest_report_header(config):
    if _PURGED:
        return f"dotenv 격리: .env 주입 키 {len(_PURGED)}개 제거 ({', '.join(sorted(_PURGED)[:4])} ...)"
    return "dotenv 격리: 제거할 키 없음 (.env 없음)"
