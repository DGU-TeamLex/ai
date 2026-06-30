import pandas as pd


NEWS_KEYWORDS = [
    "감염병",
    "독감",
    "인플루엔자",
    "코로나",
    "COVID",
    "폐렴",
    "호흡기",
    "홍역",
    "조류독감",
    "공급난",
    "수급",
    "원자재",
    "수출 제한",
    "공장",
    "물류",
    "항만",
    "재고",
    "수입",
    "원유",
    "플라스틱",
    "폴리프로필렌",
    "라텍스",
    "고무",
    "구리",
    "알루미늄",
    "니켈",
    "반도체",
    "부직포",
    "면화",
]


def filter_relevant_news(news: pd.DataFrame) -> pd.DataFrame:
    text = (news["title"].fillna("") + " " + news["summary"].fillna("")).str.lower()
    mask = text.apply(lambda value: any(keyword.lower() in value for keyword in NEWS_KEYWORDS))
    return news[mask].reset_index(drop=True)

