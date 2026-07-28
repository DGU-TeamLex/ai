from datetime import date

import pandas as pd


SAMPLE_NEWS = [
    {
        "date": date(2025, 1, 15).isoformat(),
        "title": "인플루엔자 환자 급증으로 호흡기 진료 수요 증가",
        "summary": "한국과 인접한 국가에서 독감 확산세가 이어지며 보건소 대응 물품 수요가 늘고 있다.",
        "source": "sample",
        "country": "Korea",
        "url": "sample://infectious-disease-1",
    },
    {
        "date": date(2025, 2, 10).isoformat(),
        "title": "라텍스 공장 가동 중단으로 의료용 장갑 공급 차질 우려",
        "summary": "말레이시아 주요 라텍스 공장이 폭우로 중단되며 의료용 장갑 조달 지연이 예상된다.",
        "source": "sample",
        "country": "Malaysia",
        "url": "sample://supply-risk-1",
    },
    {
        "date": date(2025, 3, 5).isoformat(),
        "title": "원유 가격 상승으로 플라스틱 원재료 비용 부담 확대",
        "summary": "중동 지정학 리스크로 원유와 플라스틱 계열 원자재 가격 변동성이 확대됐다.",
        "source": "sample",
        "country": "Global",
        "url": "sample://material-price-1",
    },
]


def collect_news() -> pd.DataFrame:
    return pd.DataFrame(SAMPLE_NEWS)


if __name__ == "__main__":
    print(collect_news().to_string(index=False))

