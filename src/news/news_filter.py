"""스코어링 대상 뉴스 1차 선별.

⚠️ 이 목록은 `news_llm_analyzer` 의 분류 키워드보다 **넓거나 같아야 한다.**
여기서 걸러진 기사는 분류기가 볼 기회조차 없다.

실제로 좁아서 사고가 났다. 수집한 7,756건 중 991건(12.8%)만 통과했고,
탈락한 것 중에 분류기가 `port_or_logistics_disruption` 으로 정확히 잡을 기사가
대량으로 있었다.

    "customs border protection leads long lines airports"   ← customs 미포함
    "us customs computer outage causes delays"              ← outage/delay 미포함

원인은 목록이 한국어 위주로 만들어졌고, 영어는 일부 구(phrase)만 있어
`port` `strike` `sanction` `disruption` 같은 단어가 빠져 있던 것이다.
GDELT 수집분은 전량 영어라 그대로 탈락했다.
"""
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
    "pandemic",
    "epidemic",
    "influenza",
    "disease outbreak",
    "medical supplies",
    "medical device",
    "shortage",
    "supply disruption",
    "export restriction",
    "export ban",
    "factory shutdown",
    "logistics",
    "shipping",
    "latex",
    "nitrile",
    "polypropylene",
    "plastic",
    # ── 아래는 news_llm_analyzer 의 분류 키워드와 맞추기 위해 보강한 것 ──
    # 물류·통관 (port_or_logistics_disruption)
    "port",
    "freight",
    "container",
    "customs",
    "vessel",
    "cargo",
    "shipment",
    "supply chain",
    "delivery delay",
    "backlog",
    "congestion",
    "strike",
    # 수출입 통제 (export_restriction_or_sanction)
    "sanction",
    "embargo",
    "tariff",
    "export control",
    "export curb",
    "import ban",
    "quota",
    # 생산 중단 (factory_shutdown)
    "shutdown",
    "shut down",
    "production halt",
    "plant closure",
    "force majeure",
    "outage",
    "explosion",
    "curtail",
    # 지정학 (war_or_armed_conflict)
    "war",
    "armed conflict",
    "military conflict",
    "middle east",
    "ukraine",
    # 일반 차질
    "disruption",
    "disrupted",
    "bottleneck",
    # 원자재 (raw_material_*)
    "naphtha",
    "crude oil",
    "petrochemical",
    "aluminum",
    "aluminium",
    "alumina",
    "bauxite",
    "polyethylene",
    "pvc",
    "resin",
    "ethylene",
    "cotton",
    "pulp",
    # 의료용품 (medical_supply)
    "respirator",
    "syringe",
    "catheter",
    "surgical mask",
    "vaccine",
]


def filter_relevant_news(news: pd.DataFrame) -> pd.DataFrame:
    text = (news["title"].fillna("") + " " + news["summary"].fillna("")).str.lower()
    mask = text.apply(lambda value: any(keyword.lower() in value for keyword in NEWS_KEYWORDS))
    return news[mask].reset_index(drop=True)
