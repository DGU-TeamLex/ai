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
    material_meta_codes: list[str] | None = None,
    demand_risk_meta_codes: list[str] | None = None,
    external_event_codes: list[str] | None = None,
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
        "material_meta_codes": material_meta_codes or [],
        "demand_risk_meta_codes": demand_risk_meta_codes or [],
        "external_event_codes": external_event_codes or [],
    }


def analyze_news_row(row: pd.Series) -> dict:
    title = str(row.get("title", "") or "").lower()
    text = f"{title} {row.get('summary', '')}".lower()
    country = row.get("country") or "Unknown"

    infectious_keywords = [
        "독감",
        "인플루엔자",
        "코로나",
        "covid",
        "coronavirus",
        "폐렴",
        "감염병",
        "호흡기",
        "influenza",
        "pandemic",
        "epidemic",
        "disease outbreak",
        "respiratory disease",
        "mpox",
        "hiv",
        "measles",
        "avian flu",
        "bird flu",
    ]
    has_infectious_keyword = any(
        keyword in text for keyword in infectious_keywords
    )
    if has_infectious_keyword and any(
        phrase in title
        for phrase in [
            "no confirmed",
            "not confirmed",
            "no outbreak",
            "outbreak ruled out",
            "확진 없음",
            "유행 아님",
        ]
    ):
        return _result(
            event_type="none",
            country=country,
            keyword="infectious disease negation",
            material="respiratory disease",
            related_items=[],
            risk_direction="no_effect",
            severity=0.0,
            confidence=0.85,
            reason="제목에서 감염병 발생 또는 확진을 부정함",
        )

    if has_infectious_keyword:
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
            demand_risk_meta_codes=["INFECTIOUS_DISEASE_OUTBREAK"],
        )

    has_raw_material_keyword = any(
        keyword in text
        for keyword in [
            "원유",
            "나프타",
            "플라스틱",
            "폴리프로필렌",
            "폴리에틸렌",
            "폴리염화비닐",
            "라텍스",
            "고무",
            "구리",
            "알루미늄",
            "니켈",
            "crude oil",
            "brent",
            "wti",
            "naphtha",
            "plastic",
            "polypropylene",
            "polyethylene",
            "pvc",
            "latex",
            "rubber",
            "nitrile",
            "copper",
            "aluminum",
            "nickel",
        ]
    )
    if has_raw_material_keyword:
        if any(keyword in text for keyword in ["니트릴", "nitrile"]):
            material = "nitrile"
            material_meta_codes = ["SYNTHETIC_NITRILE_RUBBER"]
        elif any(keyword in text for keyword in ["라텍스", "고무", "latex", "rubber"]):
            material = "latex"
            material_meta_codes = ["NATURAL_RUBBER_LATEX"]
        elif any(keyword in text for keyword in ["알루미늄", "aluminum"]):
            material = "aluminum"
            material_meta_codes = ["ALUMINUM"]
        elif any(keyword in text for keyword in ["폴리프로필렌", "polypropylene"]):
            material = "polypropylene"
            material_meta_codes = ["POLYPROPYLENE_PP"]
        elif any(keyword in text for keyword in ["폴리에틸렌", "polyethylene"]):
            material = "polyethylene"
            material_meta_codes = ["POLYETHYLENE_PE"]
        elif any(keyword in text for keyword in ["폴리염화비닐", "pvc"]):
            material = "pvc"
            material_meta_codes = ["POLYVINYL_CHLORIDE_PVC"]
        else:
            material = "oil_plastic"
            material_meta_codes = [
                "CRUDE_OIL_REFINED",
                "POLYPROPYLENE_PP",
                "POLYETHYLENE_PE",
                "POLYVINYL_CHLORIDE_PVC",
            ]
        middle_east = any(
            keyword in text
            for keyword in ["중동", "middle east", "gulf", "호르무즈", "hormuz"]
        )
        naphtha = any(keyword in text for keyword in ["나프타", "naphtha"])
        external_event_codes = (
            ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK"]
            if middle_east and naphtha
            else []
        )
        if any(
            keyword in text
            for keyword in [
                "price fall",
                "prices fall",
                "price decline",
                "prices decline",
                "price drop",
                "prices drop",
                "prices tumble",
                "price tumbles",
                "crude slips",
                "oil slips",
                "easing oil",
                "crude fell",
                "oil fell",
                "crude plunged",
                "oil plunged",
                "surplus",
                "가격 하락",
                "가격 안정",
                "공급 완화",
            ]
        ):
            return _result(
                event_type="raw_material_price_relief",
                country=country,
                keyword="raw material price relief",
                material=material,
                related_items=["주사기", "카테터", "마스크", "의료용 장갑"],
                risk_direction="supply_increase",
                severity=0.0,
                confidence=0.75,
                reason="원자재 가격 하락 또는 공급 완화 표현이 포함됨",
                material_meta_codes=material_meta_codes,
                external_event_codes=external_event_codes,
            )
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
            material_meta_codes=material_meta_codes,
            external_event_codes=external_event_codes,
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
