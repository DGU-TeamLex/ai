import pandas as pd


def analyze_news_row(row: pd.Series) -> dict:
    text = f"{row.get('title', '')} {row.get('summary', '')}".lower()
    country = row.get("country") or "Unknown"

    if any(keyword in text for keyword in ["독감", "인플루엔자", "코로나", "폐렴", "감염병", "호흡기"]):
        return {
            "event_type": "infectious_disease",
            "country": country,
            "region": None,
            "keyword": "infectious disease",
            "disease_or_material": "respiratory disease",
            "related_medical_items": ["마스크", "진단키트", "보호복"],
            "risk_direction": "demand_increase",
            "severity": 0.70,
            "confidence": 0.75,
            "reason": "감염병 또는 호흡기 질환 확산 관련 키워드가 포함됨",
        }
    if any(keyword in text for keyword in ["공급", "수급", "공장", "물류", "수출 제한", "수입 차질"]):
        material = "latex" if "라텍스" in text else "general_material"
        return {
            "event_type": "supply_risk",
            "country": country,
            "region": None,
            "keyword": "supply disruption",
            "disease_or_material": material,
            "related_medical_items": ["의료용 장갑"],
            "risk_direction": "supply_decrease",
            "severity": 0.80,
            "confidence": 0.80,
            "reason": "공급 차질 또는 생산 중단 관련 키워드가 포함됨",
        }
    if any(keyword in text for keyword in ["원유", "플라스틱", "폴리프로필렌", "라텍스", "고무", "구리", "알루미늄", "니켈"]):
        return {
            "event_type": "material_price",
            "country": country,
            "region": None,
            "keyword": "material price",
            "disease_or_material": "oil_plastic",
            "related_medical_items": ["주사기", "카테터", "마스크"],
            "risk_direction": "supply_decrease",
            "severity": 0.65,
            "confidence": 0.70,
            "reason": "원자재 가격 또는 변동성 관련 키워드가 포함됨",
        }
    return {
        "event_type": "none",
        "country": country,
        "region": None,
        "keyword": None,
        "disease_or_material": None,
        "related_medical_items": [],
        "risk_direction": "no_effect",
        "severity": 0.0,
        "confidence": 0.0,
        "reason": "위험 관련 키워드가 충분하지 않음",
    }

