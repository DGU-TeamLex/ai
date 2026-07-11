import pandas as pd


def _result(
    *,
    event_type: str,
    country: str,
    keyword: str | None,
    material: str | None,
    related_items: list[str],
    risk_direction: str,
    severity: float,
    confidence: float,
    reason: str,
) -> dict:
    return {
        "event_type": event_type,
        "country": country,
        "region": None,
        "keyword": keyword,
        "disease_or_material": material,
        "related_medical_items": related_items,
        "risk_direction": risk_direction,
        "severity": severity,
        "confidence": confidence,
        "reason": reason,
    }


def analyze_news_row(row: pd.Series) -> dict:
    text = f"{row.get('title', '')} {row.get('summary', '')}".lower()
    country = row.get("country") or "Unknown"

    if any(
        keyword in text
        for keyword in [
            "독감",
            "인플루엔자",
            "코로나",
            "covid",
            "폐렴",
            "감염병",
            "호흡기",
            "influenza",
            "pandemic",
            "epidemic",
            "disease outbreak",
            "respiratory disease",
        ]
    ):
        return _result(
            event_type="infectious_disease_outbreak",
            country=country,
            keyword="infectious disease",
            material="respiratory disease",
            related_items=["마스크", "진단키트", "보호복"],
            risk_direction="demand_increase",
            severity=0.70,
            confidence=0.75,
            reason="감염병 또는 호흡기 질환 확산 관련 키워드가 포함됨",
        )

    if any(
        keyword in text
        for keyword in [
            "원유",
            "플라스틱",
            "폴리프로필렌",
            "라텍스",
            "고무",
            "구리",
            "알루미늄",
            "니켈",
            "crude oil",
            "plastic",
            "polypropylene",
            "latex",
            "rubber",
            "nitrile",
            "copper",
            "aluminum",
            "nickel",
        ]
    ):
        material = "latex" if any(keyword in text for keyword in ["라텍스", "고무", "latex", "rubber", "nitrile"]) else "oil_plastic"
        return _result(
            event_type="raw_material_shortage_or_price_spike",
            country=country,
            keyword="raw material risk",
            material=material,
            related_items=["주사기", "카테터", "마스크", "의료용 장갑"],
            risk_direction="supply_decrease",
            severity=0.65,
            confidence=0.70,
            reason="원자재 가격, 부족, 변동성 관련 키워드가 포함됨",
        )

    if any(
        keyword in text
        for keyword in [
            "수출 제한",
            "수출 금지",
            "제재",
            "수입 차질",
            "통관 차질",
            "export restriction",
            "export ban",
            "sanction",
            "import disruption",
            "customs delay",
        ]
    ):
        return _result(
            event_type="export_restriction_or_sanction",
            country=country,
            keyword="export restriction",
            material="general_material",
            related_items=["의료물품", "의료기기"],
            risk_direction="supply_decrease",
            severity=0.85,
            confidence=0.80,
            reason="수출 제한, 제재, 수입 차질 관련 키워드가 포함됨",
        )

    if any(
        keyword in text
        for keyword in [
            "항만",
            "물류",
            "운송",
            "해상",
            "항공화물",
            "파업",
            "배송 지연",
            "port",
            "logistics",
            "shipping",
            "freight",
            "strike",
            "delivery delay",
        ]
    ):
        return _result(
            event_type="port_or_logistics_disruption",
            country=country,
            keyword="logistics disruption",
            material="general_material",
            related_items=["의료물품", "의료기기"],
            risk_direction="supply_decrease",
            severity=0.75,
            confidence=0.75,
            reason="항만, 물류, 운송 지연 관련 키워드가 포함됨",
        )

    if any(
        keyword in text
        for keyword in ["공장", "가동 중단", "생산 중단", "라인 중단", "화재", "factory", "shutdown", "production halt", "plant fire"]
    ):
        material = "latex" if any(keyword in text for keyword in ["라텍스", "latex", "nitrile"]) else "general_material"
        return _result(
            event_type="factory_shutdown",
            country=country,
            keyword="factory shutdown",
            material=material,
            related_items=["의료용 장갑", "의료기기"],
            risk_direction="supply_decrease",
            severity=0.80,
            confidence=0.80,
            reason="공장 폐쇄 또는 생산 중단 관련 키워드가 포함됨",
        )

    if any(
        keyword in text
        for keyword in [
            "전쟁",
            "무력 충돌",
            "군사 충돌",
            "분쟁",
            "중동",
            "우크라이나",
            "war",
            "armed conflict",
            "military conflict",
            "middle east",
            "ukraine",
        ]
    ):
        return _result(
            event_type="war_or_armed_conflict",
            country=country,
            keyword="geopolitical conflict",
            material="general_material",
            related_items=["의료물품", "의료기기"],
            risk_direction="supply_decrease",
            severity=0.85,
            confidence=0.75,
            reason="전쟁, 군사 충돌, 지정학 리스크 관련 키워드가 포함됨",
        )

    if any(keyword in text for keyword in ["정책", "규제", "허가 기준", "통관 기준", "policy", "regulation", "approval criteria", "customs rules"]):
        return _result(
            event_type="policy_regulation_uncertainty",
            country=country,
            keyword="policy uncertainty",
            material="general_material",
            related_items=["의료물품", "의료기기"],
            risk_direction="supply_decrease",
            severity=0.50,
            confidence=0.60,
            reason="정책 또는 규제 불확실성 관련 키워드가 포함됨",
        )

    if any(keyword in text for keyword in ["공급", "수급", "공급난", "재고 부족", "supply", "shortage", "stockout"]):
        return _result(
            event_type="factory_shutdown",
            country=country,
            keyword="supply disruption",
            material="general_material",
            related_items=["의료물품", "의료기기"],
            risk_direction="supply_decrease",
            severity=0.70,
            confidence=0.70,
            reason="공급 차질 또는 수급 불안 관련 키워드가 포함됨",
        )

    if any(keyword in text for keyword in ["경기 침체", "시장 불안", "불확실성", "recession", "market instability", "uncertainty"]):
        return _result(
            event_type="general_economic_uncertainty",
            country=country,
            keyword="economic uncertainty",
            material="general_material",
            related_items=[],
            risk_direction="supply_decrease",
            severity=0.35,
            confidence=0.55,
            reason="일반 경제 불확실성 관련 키워드가 포함됨",
        )

    return _result(
        event_type="none",
        country=country,
        keyword=None,
        material=None,
        related_items=[],
        risk_direction="no_effect",
        severity=0.0,
        confidence=0.0,
        reason="위험 관련 키워드가 충분하지 않음",
    )
