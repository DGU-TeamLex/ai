import argparse
from collections import Counter
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import heapq
import json
import logging
import math
from pathlib import Path
import re
import unicodedata

from .config import RAW_STOCK_DIR, RAW_STOCK_FILE_PATTERN, SAMPLE_DATA_DIR
from .data_loader import RAW_STOCK_COLUMNS, discover_raw_stock_files


LOGGER = logging.getLogger(__name__)
NORMALIZATION_VERSION = "item-normalization-v0.5"
DEFAULT_OUTPUT_PATH = SAMPLE_DATA_DIR / "raw_stock_item_normalization_sample_1000.csv"
DEFAULT_ALIAS_OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "processed" / "item_alias_candidates_v0.3.parquet"
DEFAULT_STOCK_OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "processed" / "stock_with_item_normalization_v0.3.parquet"
DEFAULT_REPORT_OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "processed" / "item_normalization_v0.3_report.json"

FACT_NORMALIZATION_COLUMNS = [
    "local_item_key",
    "cleaned_name",
    "product_name_candidate",
    "unresolved_product_name_candidate",
    "operational_tags",
    "operational_class_candidate",
    "standard_family_name_candidate",
    "item_family_id_candidate",
    "standard_subtype_name_candidate",
    "item_subtype_id_candidate",
    "canonical_item_id_candidate",
    "intrinsic_item_group_id_candidate",
    "item_group_id_candidate",
    "is_forecastable_default",
    "is_forecastable_override",
    "effective_is_forecastable",
    "dosage_form_candidate",
    "strength_candidate",
    "pack_quantity_candidate",
    "pack_unit_candidate",
    "normalized_specification_candidate",
    "standard_unit_candidate",
    "material_candidate",
    "material_mapping_status",
    "parenthetical_details",
    "classification_method",
    "classification_confidence",
    "intrinsic_classification_method",
    "intrinsic_classification_confidence",
    "normalization_status",
    "review_status",
    "normalization_version",
]

FORECASTABLE_BY_GROUP = {
    "MED_ORAL": "t",
    "MED_INJECT": "t",
    "MED_TOPICAL": "t",
    "LAB_REAGENT": "t",
    "DISINFECT": "t",
    "MED_SUPPLY": "t",
    "KM_EXTRACT": "t",
    "KM_HERB": "t",
    "SUPPLEMENT": "t",
    "PROMO": "f",
    "FUEL": "t",
    "WASTE": "f",
    "RENTAL": "f",
    "UNCLASSIFIED": "",
}

OPERATIONAL_TAG_PATTERNS = [
    ("홍보", re.compile(r"홍보(?:물품|물)?|판촉(?:물품)?|기념품|증정품", re.IGNORECASE)),
    ("방문", re.compile(r"방문(?:건강관리(?:사업)?)?", re.IGNORECASE)),
    ("재활", re.compile(r"재활", re.IGNORECASE)),
    ("무료", re.compile(r"무료", re.IGNORECASE)),
    ("유료", re.compile(r"유료", re.IGNORECASE)),
    ("비급여", re.compile(r"비급여(?:용)?", re.IGNORECASE)),
    ("택배용", re.compile(r"택배용|택배", re.IGNORECASE)),
    ("대여", re.compile(r"대여(?:용|사업|품)?|임대", re.IGNORECASE)),
    ("본소", re.compile(r"본소", re.IGNORECASE)),
    ("지소", re.compile(r"지소", re.IGNORECASE)),
    ("내당", re.compile(r"내당", re.IGNORECASE)),
]

LEADING_NUMBER_PATTERN = re.compile(r"^\s*(?:\d+(?:-\d+)?|[가-하])\s*[.):_\-]\s*")
WRAPPED_OPERATIONAL_PATTERN = re.compile(
    r"^\s*[\[(]?\s*[★*]?\s*"
    r"(방문(?:건강관리(?:사업)?)?|방|재활|내당|본소|지소|무료|유료|비급여(?:용)?|택배용|대여(?:용)?)"
    r"\s*[\])]?\s*[:._\-]*\s*",
    re.IGNORECASE,
)
TRAILING_OPERATIONAL_PATTERN = re.compile(
    r"\s*[\[(]\s*(방문(?:건강관리(?:사업)?)?|재활|내당|본소|지소|무료|유료|비급여(?:용)?|택배용|대여(?:용)?)"
    r"\s*[\])]\s*$",
    re.IGNORECASE,
)
BARE_TRAILING_OPERATIONAL_PATTERN = re.compile(
    r"\s*[-_:]?\s*(?:대여사업|대여용|대여|임대)\s*$",
    re.IGNORECASE,
)

@dataclass(frozen=True)
class FamilyRule:
    pattern: re.Pattern
    family_id: str
    family_name: str
    subtype_id: str
    subtype_name: str
    group_id: str
    standard_unit: str = ""


FAMILY_RULES = [
    FamilyRule(
        re.compile(
            r"주사\s*(?:침|바늘)\s*(?:폐기|수거)?\s*통|(?:needle|니들)\s*(?:box|통)",
            re.IGNORECASE,
        ),
        "MEDICAL_WASTE_CONTAINER",
        "의료폐기물 전용용기",
        "RIGID_NEEDLE_BOX",
        "합성수지형 용기(needle box)",
        "WASTE",
        "EA",
    ),
    FamilyRule(
        re.compile(
            r"(?=.*폐기물)(?=.*(?:용기|통|박스|상자|봉투|비닐))"
            r"(?=.*(?:의료|감염성|손상성|격리|병리계|조직물류|적출물|주사|침통|바늘))|"
            r"needle\s*box",
            re.IGNORECASE,
        ),
        "MEDICAL_WASTE_CONTAINER",
        "의료폐기물 전용용기",
        "MEDICAL_WASTE_CONTAINER_GENERIC",
        "의료폐기물 전용용기",
        "WASTE",
        "EA",
    ),
    FamilyRule(
        re.compile(r"(?:EO\s*가스.*)?멸균\s*(?:포장재|롤|파우치)", re.IGNORECASE),
        "EO_STERILIZATION_PACKAGING",
        "멸균포장재",
        "EO_STERILIZATION_ROLL",
        "EO가스 소독용 포장재",
        "DISINFECT",
        "ROLL",
    ),
    FamilyRule(
        re.compile(r"(?:주사액|생리\s*식염수|수액제).*?(?:통|용기)|수액제\s*(?:통|용기)", re.IGNORECASE),
        "IV_FLUID_CONTAINER",
        "수액제통",
        "IV_FLUID_CONTAINER",
        "주사액·생리식염수 등 수액제통",
        "MED_SUPPLY",
        "EA",
    ),
    FamilyRule(
        re.compile(r"수액\s*(?:세트|셋트|set)", re.IGNORECASE),
        "INFUSION_SET",
        "수액세트",
        "INFUSION_SET",
        "수액세트",
        "MED_SUPPLY",
        "EA",
    ),
    FamilyRule(
        re.compile(r"혈액\s*투석제?\s*(?:통|용기)", re.IGNORECASE),
        "DIALYSATE_CONTAINER",
        "혈액투석제통",
        "DIALYSATE_CONTAINER",
        "혈액투석제통",
        "MED_SUPPLY",
        "EA",
    ),
    FamilyRule(
        re.compile(
            r"angio\s*(?:needle|cath(?:eter)?)|angiocath|"
            r"(?:안지오|엔지오)\s*니들|나비\s*바늘|"
            r"I\.?\s*V\.?\s*카테터|"
            r"(?:\d{1,2}\s*\(?\s*(?:G|게이지)\s*\)?.*카테터|"
            r"카테터.*\d{1,2}\s*\(?\s*(?:G|게이지)\s*\)?)",
            re.IGNORECASE,
        ),
        "ANGIO_CATHETER",
        "카테터",
        "ANGIO_NEEDLE",
        "카테터(angio needle)",
        "MED_SUPPLY",
        "EA",
    ),
    FamilyRule(
        re.compile(r"urine\s*bag|유린\s*백|소변\s*(?:백|주머니)", re.IGNORECASE),
        "URINE_BAG",
        "Urine bag",
        "URINE_BAG",
        "Urine bag",
        "MED_SUPPLY",
        "EA",
    ),
    FamilyRule(
        re.compile(r"(?:혈당|당뇨)\s*(?:측정|검사)?\s*(?:스틱|스트립|검사지)", re.IGNORECASE),
        "BLOOD_GLUCOSE_TEST_STRIP",
        "혈당검사지",
        "BLOOD_GLUCOSE_TEST_STRIP",
        "혈당검사지",
        "LAB_REAGENT",
    ),
    FamilyRule(re.compile(r"(?:일회용\s*)?주사기", re.IGNORECASE), "DISPOSABLE_SYRINGE", "주사기", "SYRINGE_USAGE_BASED", "주사기(사용량 기준)", "MED_SUPPLY", "EA"),
    FamilyRule(re.compile(r"주사(?:침|바늘)", re.IGNORECASE), "INJECTION_NEEDLE", "주사침", "INJECTION_NEEDLE", "주사침", "MED_SUPPLY", "EA"),
    FamilyRule(
        re.compile(r"채혈(?:침|핀)|란셋|난셋|\b(?:lancet|lanset|safelan)\b", re.IGNORECASE),
        "BLOOD_LANCET",
        "채혈침",
        "BLOOD_LANCET",
        "채혈침",
        "MED_SUPPLY",
        "EA",
    ),
    FamilyRule(re.compile(r"알코올\s*(?:스?왑|솜)|알콜\s*(?:스?왑|솜)", re.IGNORECASE), "ALCOHOL_SWAB", "알코올스왑", "ALCOHOL_SWAB", "알코올스왑", "MED_SUPPLY", "EA"),
    FamilyRule(re.compile(r"거즈", re.IGNORECASE), "MEDICAL_GAUZE", "의료용 거즈", "MEDICAL_GAUZE", "의료용 거즈", "MED_SUPPLY", "EA"),
    FamilyRule(re.compile(r"카테터|카데터", re.IGNORECASE), "CATHETER", "카테터", "CATHETER_GENERIC", "카테터", "MED_SUPPLY", "EA"),
    FamilyRule(re.compile(r"(?:의료용\s*)?마스크|KF\s*\d{2}", re.IGNORECASE), "MEDICAL_MASK", "마스크", "MEDICAL_MASK", "마스크", "MED_SUPPLY", "EA"),
    FamilyRule(re.compile(r"(?:의료용\s*)?(?:(?:라텍스|니트릴|폴리)?\s*장갑|poly\s*glove|폴리\s*글러브)", re.IGNORECASE), "MEDICAL_GLOVE", "의료용 장갑", "MEDICAL_GLOVE", "의료용 장갑", "MED_SUPPLY", "EA"),
    FamilyRule(re.compile(r"임신\s*(?:진단|테스트|검사)\s*(?:키트|시약|기)", re.IGNORECASE), "PREGNANCY_TEST", "임신진단키트", "PREGNANCY_TEST", "임신진단키트", "LAB_REAGENT", "EA"),
]

PROMO_PATTERN = re.compile(r"홍보(?:물품|물)?|판촉(?:물품)?|기념품|증정품", re.IGNORECASE)
RENTAL_CONTEXT_PATTERN = re.compile(r"대여(?:용|사업|품)?|임대|(?:^|[\s([_\-])렌탈", re.IGNORECASE)
RENTAL_ASSET_PATTERN = re.compile(
    r"(?:유축기|혈당\s*측정기|혈당기|혈압계|염도계|휠체어|기기|장비).*?(?:대여|임대|렌탈)|"
    r"(?:대여|임대|렌탈).*?(?:유축기|혈당\s*측정기|혈당기|혈압계|염도계|휠체어|기기|장비)",
    re.IGNORECASE,
)
RENTAL_CONSUMABLE_PATTERN = re.compile(
    r"가방|저장\s*팩|소모품|스틱|스트립|검사지|란셋|알코올|알콜|솜|주사기|마스크|장갑|커프|수첩",
    re.IGNORECASE,
)
FUEL_PATTERN = re.compile(r"휘발유|경유|등유|LPG|가솔린|디젤|유류", re.IGNORECASE)
WASTE_PATTERN = re.compile(
    r"폐기물|쓰레기\s*봉투|폐기용|"
    r"주사\s*(?:침|바늘)\s*(?:폐기|수거)?\s*통|(?:needle|니들)\s*(?:box|통)",
    re.IGNORECASE,
)
DISINFECT_PATTERN = re.compile(
    r"소독제|살균제|멸균제|소독용\s*(?:에탄올|알코올|알콜)|"
    r"유한\s*락스|차아염소산(?:나트륨)?|염소계\s*소독",
    re.IGNORECASE,
)
LAB_PATTERN = re.compile(
    r"검사\s*시약|진단\s*시약|진단\s*키트|검사\s*키트|검사지|테스트\s*스트립|reagent|"
    r"(?:혈당|당화\s*혈색소|콜레스테롤|고지혈증|빈혈|니코틴).*?(?:측정|검사).*?(?:스틱|스트립|시험지|카트리지|큐벳)",
    re.IGNORECASE,
)
CULTURE_MEDIA_PATTERN = re.compile(
    r"수송\s*배지|배양\s*배지|생\s*배지|평판\s*배지|분말\s*배지|제조\s*배지|"
    r"검사(?:용)?\s*배지|(?:agar|broth).*?배지|"
    r"(?:TCBS|EMB|KIA|TSB|TSA|SS|SIM|XLD|BAP|APW|VTM|UTM|MSA|CIN|mEC)\s*배지",
    re.IGNORECASE,
)
NON_LAB_BADGE_PATTERN = re.compile(r"임산부|엠블럼|주차|차량|배려|명찰|뱃지", re.IGNORECASE)
MED_SUPPLY_PATTERN = re.compile(
    r"주사기|주사침|주사바늘|채혈침|란셋|거즈|붕대|밴드|카테터|카데터|"
    r"수액\s*세트|튜브|마스크|장갑|면봉|드레싱|봉합|캐뉼라|필터|의료용\s*테이프",
    re.IGNORECASE,
)
SUPPLEMENT_PATTERN = re.compile(
    r"건강\s*기능\s*식품|건기식|영양제",
    re.IGNORECASE,
)
TOPICAL_PATTERN = re.compile(
    r"연고|크림|로션|패치|패취|파스|플라스타|카타플라스마|습포제|"
    r"점안(?:액|제)?|점이(?:액|제)?|외용(?:액|제)?|겔(?:\s|$)|스프레이",
    re.IGNORECASE,
)
INJECTION_EXPLICIT_PATTERN = re.compile(
    r"주사(?:액|제)?|약침|백신|바이알|앰플|프리필드",
    re.IGNORECASE,
)
INJECTION_SUFFIX_PATTERN = re.compile(
    r"[가-힣A-Za-z0-9]+주(?=$|[\s(_\d/-])",
    re.IGNORECASE,
)
ORAL_PATTERN = re.compile(
    r"캡슐|캅셀|시럽|과립|현탁액|내용액|경구용|"
    r"[가-힣A-Za-z0-9]+정(?=$|[\s(_\d/-])|[가-힣A-Za-z0-9]+환(?=$|[\s(_\d/-])|"
    r"(?:^|[\s(])정(?:$|[\s)])|(?:^|[\s(])환(?:$|[\s)])",
    re.IGNORECASE,
)
KM_EXTRACT_PATTERN = re.compile(r"연조\s*엑스|엑스(?:제|산|과립)|혼합\s*단미\s*엑스|한방\s*과립", re.IGNORECASE)
KM_HERB_PATTERN = re.compile(r"한약재|약초|절편", re.IGNORECASE)
NON_HERB_CONTEXT_PATTERN = re.compile(r"만들기|마사지|체험|키트|세트", re.IGNORECASE)
NON_MEDICAL_TOPICAL_PATTERN = re.compile(
    r"핸드\s*크림|선\s*크림|보습\s*크림|수분\s*크림|아이\s*크림|바디\s*크림|풋\s*크림|"
    r"아이스크림|슈크림|크림빵|화장품",
    re.IGNORECASE,
)
NON_MEDICAL_SUPPLY_PATTERN = re.compile(
    r"스트레칭\s*밴드|운동\s*밴드|고무\s*밴드|머리\s*밴드|헤어\s*밴드",
    re.IGNORECASE,
)
NON_ORAL_USE_CONTEXT_PATTERN = re.compile(
    r"측정|행정|설정|조정|교정|결정|고정|인정|정리",
    re.IGNORECASE,
)

STRENGTH_PATTERN = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*(mg|mcg|ug|μg|g|IU|%)(?![A-Za-z])", re.IGNORECASE)
PACK_PATTERN = re.compile(r"(?<!\d)(\d+)\s*(정|캡슐|캡|매|개|병|바이알|앰플|포|통|T|EA)(?![A-Za-z])", re.IGNORECASE)
PARENTHETICAL_PATTERN = re.compile(r"(?<=[(\[])[^)\]]+(?=[)\]])")
DIMENSION_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(cm|mm)\s*[x*×]\s*(\d+(?:\.\d+)?)\s*(m|cm|mm)",
    re.IGNORECASE,
)
VOLUME_PATTERN = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(밀리리터|리터|cc|mL|ml|L)\s*(이하|초과)?",
    re.IGNORECASE,
)
GAUGE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2})\s*\(?\s*"
    r"(?:G(?=\s*(?:[x*×]\s*\d|[^A-Za-z]|$))|게이지)\s*\)?"
    r"(?:\s*[x*×](?=\s*\d))?",
    re.IGNORECASE,
)

GAUGE_FAMILY_IDS = {
    "DISPOSABLE_SYRINGE",
    "INJECTION_NEEDLE",
    "BLOOD_LANCET",
    "CATHETER",
    "ANGIO_CATHETER",
}


@dataclass
class AliasStats:
    institution_id: str
    local_item_code: str
    raw_item_name: str
    example_department: str
    occurrence_count: int
    usage_sum: float
    first_seen_date: str
    last_seen_date: str


def clean_item_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return " ".join(normalized.replace("\r", " ").replace("\n", " ").split())


def _remove_operational_wrappers(value: str) -> str:
    result = value
    while True:
        changed = LEADING_NUMBER_PATTERN.sub("", result, count=1)
        changed = WRAPPED_OPERATIONAL_PATTERN.sub("", changed, count=1)
        changed = TRAILING_OPERATIONAL_PATTERN.sub("", changed, count=1)
        changed = BARE_TRAILING_OPERATIONAL_PATTERN.sub("", changed, count=1)
        if changed == result:
            break
        result = changed
    return result.strip(" -_:./")


def extract_operational_tags(cleaned_name: str) -> tuple[str, list[str]]:
    tags = []
    for tag, pattern in OPERATIONAL_TAG_PATTERNS:
        if pattern.search(cleaned_name):
            tags.append(tag)
    product_name = _remove_operational_wrappers(cleaned_name)
    return product_name or cleaned_name, tags


def match_family(product_name: str) -> FamilyRule | None:
    for rule in FAMILY_RULES:
        if rule.pattern.search(product_name):
            return rule
    return None


def classify_intrinsic_item(product_name: str, item_code: str) -> tuple[str, str, float]:
    if FUEL_PATTERN.search(product_name):
        return "FUEL", "pattern:fuel", 0.98
    if WASTE_PATTERN.search(product_name):
        return "WASTE", "pattern:waste", 0.95

    family = match_family(product_name)
    if family:
        return family.group_id, f"family:{family.family_id}", 0.95
    if DISINFECT_PATTERN.search(product_name):
        return "DISINFECT", "pattern:disinfect", 0.92
    if LAB_PATTERN.search(product_name) or (
        CULTURE_MEDIA_PATTERN.search(product_name) and not NON_LAB_BADGE_PATTERN.search(product_name)
    ):
        return "LAB_REAGENT", "pattern:lab_reagent", 0.90
    if MED_SUPPLY_PATTERN.search(product_name) and not NON_MEDICAL_SUPPLY_PATTERN.search(product_name):
        return "MED_SUPPLY", "pattern:medical_supply", 0.88
    is_korean_medicine_code = bool(re.match(r"^(?:OOM|OMD|O)", item_code, re.IGNORECASE))
    if is_korean_medicine_code and KM_EXTRACT_PATTERN.search(product_name):
        return "KM_EXTRACT", "pattern:km_extract", 0.90
    if KM_HERB_PATTERN.search(product_name) and not NON_HERB_CONTEXT_PATTERN.search(product_name):
        return "KM_HERB", "pattern:km_herb", 0.88
    if SUPPLEMENT_PATTERN.search(product_name):
        return "SUPPLEMENT", "pattern:supplement", 0.90
    if INJECTION_EXPLICIT_PATTERN.search(product_name):
        return "MED_INJECT", "dosage_form:injection", 0.86
    if TOPICAL_PATTERN.search(product_name) and not NON_MEDICAL_TOPICAL_PATTERN.search(product_name):
        return "MED_TOPICAL", "dosage_form:topical", 0.86
    if ORAL_PATTERN.search(product_name) and not (
        item_code.upper().startswith("USE") and NON_ORAL_USE_CONTEXT_PATTERN.search(product_name)
    ):
        return "MED_ORAL", "dosage_form:oral", 0.86
    if not item_code.upper().startswith("USE") and INJECTION_SUFFIX_PATTERN.search(product_name):
        return "MED_INJECT", "dosage_form:official_code_injection_suffix", 0.82
    if is_korean_medicine_code:
        return "UNCLASSIFIED", "code_hint:korean_medicine_unresolved", 0.20
    return "UNCLASSIFIED", "no_rule", 0.0


def detect_operational_class(raw_item_name: str) -> str:
    if PROMO_PATTERN.search(raw_item_name):
        return "PROMOTIONAL_USE"
    if RENTAL_CONTEXT_PATTERN.search(raw_item_name):
        return "RENTAL_PROGRAM"
    if WASTE_PATTERN.search(raw_item_name):
        return "WASTE_HANDLING"
    if FUEL_PATTERN.search(raw_item_name):
        return "FUEL_USE"
    return ""


def is_rental_asset(raw_item_name: str) -> bool:
    return bool(RENTAL_ASSET_PATTERN.search(raw_item_name)) and not bool(
        RENTAL_CONSUMABLE_PATTERN.search(raw_item_name)
    )


def classify_item(product_name: str, raw_item_name: str, item_code: str) -> tuple[str, str, float]:
    intrinsic_group, method, confidence = classify_intrinsic_item(product_name, item_code)
    operational_class = detect_operational_class(raw_item_name)
    if operational_class == "PROMOTIONAL_USE":
        return "PROMO", "operational:promo", 0.99
    if operational_class == "RENTAL_PROGRAM" and is_rental_asset(raw_item_name):
        return "RENTAL", "operational:rental_asset", 0.98
    return intrinsic_group, method, confidence


def extract_dosage_form(product_name: str, group_id: str) -> str:
    if group_id == "MED_INJECT":
        return "주사제"
    if group_id == "MED_TOPICAL":
        for pattern, name in [
            (r"점안", "점안제"),
            (r"점이", "점이제"),
            (r"연고", "연고"),
            (r"크림", "크림"),
            (r"패치|패취|파스|플라스타|카타플라스마|습포제", "패치/파스"),
            (r"로션", "로션"),
            (r"겔", "겔"),
        ]:
            if re.search(pattern, product_name, re.IGNORECASE):
                return name
        return "외용제"
    if group_id == "MED_ORAL":
        for pattern, name in [
            (r"캡슐|캅셀", "캡슐"),
            (r"시럽", "시럽"),
            (r"과립", "과립"),
            (r"현탁액", "현탁액"),
            (r"내용액", "내용액"),
            (r"환(?=$|[\s(_\d/-])", "환"),
            (r"정(?=$|[\s(_\d/-])", "정제"),
        ]:
            if re.search(pattern, product_name, re.IGNORECASE):
                return name
        return "경구제"
    return ""


def derive_subtype(family: FamilyRule | None, product_name: str) -> tuple[str, str]:
    if family is None:
        return "", ""
    if family.family_id == "MEDICAL_WASTE_CONTAINER":
        explicit_pe = bool(re.search(r"\bPE\b|폴리에틸렌", product_name, re.IGNORECASE))
        bag = bool(re.search(r"봉투|비닐", product_name, re.IGNORECASE)) or explicit_pe
        cardboard = bool(re.search(r"골판지|종이", product_name, re.IGNORECASE))
        rigid = bool(
            re.search(
                r"needle\s*box|니들|주사\s*(?:침|바늘).*통|침\s*통|바늘\s*통|"
                r"손상성|격리|액상|sharp|합성\s*수지|PVC|플라스틱",
                product_name,
                re.IGNORECASE,
            )
        )
        if bag and re.search(r"박스|상자", product_name, re.IGNORECASE):
            return "", ""
        if sum((bag, cardboard, rigid)) > 1:
            return "", ""
        if cardboard:
            return "MEDICAL_WASTE_CARDBOARD_BOX", "골판지류 상자형 용기"
        if explicit_pe:
            return "MEDICAL_WASTE_PE_BAG", "봉투형 용기(PE)"
        if bag:
            return "MEDICAL_WASTE_SYNTHETIC_BAG", "봉투형 용기(합성수지류)"
        if rigid:
            return "RIGID_NEEDLE_BOX", "합성수지형 용기(needle box)"
        # A generic medical-waste container can be a bag, cardboard box, or
        # synthetic-resin box. Keep the subtype unresolved without name evidence.
        return "", ""
    if family.family_id == "ANGIO_CATHETER" and re.search(r"나비\s*바늘", product_name):
        return "BUTTERFLY_NEEDLE", "나비 바늘"
    return family.subtype_id, family.subtype_name


def extract_specification(product_name: str, family: FamilyRule | None) -> str:
    if family is None:
        return ""
    normalized_name = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", product_name)
    family_id = family.family_id
    if family_id == "ANGIO_CATHETER" and re.search(r"나비\s*바늘", normalized_name):
        return "나비 바늘"

    if family_id == "INFUSION_SET":
        return "수액세트"

    if family_id in {"EO_STERILIZATION_PACKAGING", "MEDICAL_GAUZE"}:
        dimension = DIMENSION_PATTERN.search(normalized_name)
        if dimension:
            return (
                f"{dimension.group(1)}{dimension.group(2).lower()} x "
                f"{dimension.group(3)}{dimension.group(4).lower()}"
            )
        return ""

    if family_id in {
        "DISPOSABLE_SYRINGE",
        "IV_FLUID_CONTAINER",
        "DIALYSATE_CONTAINER",
        "MEDICAL_WASTE_CONTAINER",
        "URINE_BAG",
    }:
        volume = VOLUME_PATTERN.search(normalized_name)
        if volume:
            value, unit, qualifier = volume.groups()
            normalized_unit = "mL" if unit.lower() in {"cc", "ml", "밀리리터"} else "L"
            prefix = "<=" if qualifier == "이하" else ">" if qualifier == "초과" else ""
            return f"{prefix}{value}{normalized_unit}"
        return "Urine bag" if family_id == "URINE_BAG" else ""

    if family_id in {
        "INJECTION_NEEDLE",
        "BLOOD_LANCET",
        "CATHETER",
        "ANGIO_CATHETER",
    }:
        gauge = GAUGE_PATTERN.search(normalized_name)
        if gauge:
            age = (
                "성인용"
                if "성인" in normalized_name
                else "소아용"
                if "소아" in normalized_name
                else ""
            )
            return f"{gauge.group(1)}G" + (f" ({age})" if age else "")
    return ""


def material_candidate(family: FamilyRule | None, subtype_id: str) -> tuple[str, str]:
    if family and family.family_id == "MEDICAL_WASTE_CONTAINER":
        if subtype_id == "MEDICAL_WASTE_PE_BAG":
            return "PE", "source_example_candidate"
        if subtype_id in {"MEDICAL_WASTE_SYNTHETIC_BAG", "RIGID_NEEDLE_BOX"}:
            return "SYNTHETIC_RESIN", "needs_evidence"
        if subtype_id == "MEDICAL_WASTE_CARDBOARD_BOX":
            return "PAPERBOARD", "needs_evidence"
        return "", "needs_evidence"
    return "", "needs_evidence"


def extract_strengths(
    product_name: str,
    family: FamilyRule | None,
) -> list[tuple[str, str]]:
    """Extract drug strengths without treating a needle gauge as grams."""
    gauge_spans: list[tuple[int, int]] = []
    if family and family.family_id in GAUGE_FAMILY_IDS:
        gauge_spans = [match.span() for match in GAUGE_PATTERN.finditer(product_name)]

    strengths = []
    for match in STRENGTH_PATTERN.finditer(product_name):
        if any(match.start() < end and start < match.end() for start, end in gauge_spans):
            continue
        strengths.append((match.group(1), match.group(2)))
    return strengths


def normalize_alias(stats: AliasStats) -> dict[str, object]:
    cleaned_name = clean_item_name(stats.raw_item_name)
    product_name, operational_tags = extract_operational_tags(cleaned_name)
    family = match_family(product_name)
    intrinsic_group, intrinsic_method, intrinsic_confidence = classify_intrinsic_item(
        product_name, stats.local_item_code
    )
    if family and family.group_id != intrinsic_group:
        family = None
    operational_class = detect_operational_class(stats.raw_item_name)
    group_id = intrinsic_group
    classification_method = intrinsic_method
    confidence = intrinsic_confidence
    forecast_override = ""
    if operational_class == "PROMOTIONAL_USE":
        group_id, classification_method, confidence = "PROMO", "operational:promo", 0.99
        forecast_override = "f"
    elif operational_class == "RENTAL_PROGRAM" and is_rental_asset(stats.raw_item_name):
        group_id, classification_method, confidence = "RENTAL", "operational:rental_asset", 0.98
        forecast_override = "f"
    strength_matches = extract_strengths(product_name, family)
    pack_matches = PACK_PATTERN.findall(product_name)
    parenthetical_details = [
        value.strip()
        for value in PARENTHETICAL_PATTERN.findall(product_name)
        if value.strip() and not any(tag in value for tag in operational_tags)
    ]
    family_id = family.family_id if family else ""
    family_name = family.family_name if family else ""
    subtype_id, subtype_name = derive_subtype(family, product_name)
    normalized_specification = extract_specification(product_name, family)
    candidate_material, material_status = material_candidate(family, subtype_id)
    forecast_default = FORECASTABLE_BY_GROUP[intrinsic_group]
    effective_forecast = forecast_override or FORECASTABLE_BY_GROUP[group_id]
    normalization_status = (
        "family_candidate"
        if family
        else "group_candidate"
        if intrinsic_group != "UNCLASSIFIED"
        else "unresolved"
    )
    return {
        "institution_id": stats.institution_id,
        "example_department": stats.example_department,
        "local_item_code": stats.local_item_code,
        "local_item_key": f"{stats.institution_id}::{stats.local_item_code}",
        "raw_item_name": stats.raw_item_name,
        "cleaned_name": cleaned_name,
        "product_name_candidate": product_name,
        "unresolved_product_name_candidate": product_name if not family else "",
        "operational_tags": ";".join(dict.fromkeys(operational_tags)),
        "operational_class_candidate": operational_class,
        "standard_family_name_candidate": family_name,
        "item_family_id_candidate": family_id,
        "standard_subtype_name_candidate": subtype_name,
        "item_subtype_id_candidate": subtype_id,
        "canonical_item_id_candidate": "",
        "intrinsic_item_group_id_candidate": intrinsic_group,
        "item_group_id_candidate": group_id,
        "is_forecastable_default": forecast_default,
        "is_forecastable_override": forecast_override,
        "effective_is_forecastable": effective_forecast,
        "is_forecastable_candidate": effective_forecast,
        "dosage_form_candidate": extract_dosage_form(product_name, intrinsic_group),
        "strength_candidate": ";".join(f"{value}{unit}" for value, unit in strength_matches),
        "pack_quantity_candidate": pack_matches[-1][0] if pack_matches else "",
        "pack_unit_candidate": pack_matches[-1][1] if pack_matches else "",
        "normalized_specification_candidate": normalized_specification,
        "standard_unit_candidate": family.standard_unit if family else "",
        "material_candidate": candidate_material,
        "material_mapping_status": material_status,
        "parenthetical_details": ";".join(parenthetical_details),
        "occurrence_count": stats.occurrence_count,
        "usage_sum": round(stats.usage_sum, 6),
        "first_seen_date": stats.first_seen_date,
        "last_seen_date": stats.last_seen_date,
        "classification_method": classification_method,
        "classification_confidence": confidence,
        "intrinsic_classification_method": intrinsic_method,
        "intrinsic_classification_confidence": intrinsic_confidence,
        "normalization_status": normalization_status,
        "review_status": "needs_review",
        "normalization_version": NORMALIZATION_VERSION,
    }


def extract_alias_stats(files: list[Path]) -> dict[tuple[str, str, str], AliasStats]:
    aliases: dict[tuple[str, str, str], AliasStats] = {}
    processed_rows = 0
    for path in files:
        LOGGER.info("Reading aliases from %s", path)
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.reader(file, delimiter="|", quotechar='"')
            header = next(reader, None)
            if header != RAW_STOCK_COLUMNS:
                raise ValueError(f"Unexpected raw_stock header in {path}: {header}")
            for row in reader:
                if len(row) != len(RAW_STOCK_COLUMNS):
                    raise ValueError(f"Malformed raw_stock record in {path} near logical row {reader.line_num}")
                department, item_code, item_name, closing_date = row[0], row[1], row[2], row[3]
                institution_id = row[17].strip()
                key = (institution_id, item_code.strip(), clean_item_name(item_name))
                try:
                    usage = float(row[12]) if row[12] else 0.0
                except ValueError:
                    usage = 0.0
                current = aliases.get(key)
                if current is None:
                    aliases[key] = AliasStats(
                        institution_id=institution_id,
                        local_item_code=item_code.strip(),
                        raw_item_name=clean_item_name(item_name),
                        example_department=department.strip(),
                        occurrence_count=1,
                        usage_sum=usage,
                        first_seen_date=closing_date,
                        last_seen_date=closing_date,
                    )
                else:
                    current.occurrence_count += 1
                    current.usage_sum += usage
                    current.first_seen_date = min(current.first_seen_date, closing_date)
                    current.last_seen_date = max(current.last_seen_date, closing_date)
                processed_rows += 1
                if processed_rows % 1_000_000 == 0:
                    LOGGER.info("Processed %s raw rows (%s aliases)", f"{processed_rows:,}", f"{len(aliases):,}")
    LOGGER.info("Extracted %s aliases from %s rows", f"{len(aliases):,}", f"{processed_rows:,}")
    return aliases


def _allocate_group_quotas(group_counts: dict[str, int], sample_size: int) -> dict[str, int]:
    available_total = sum(group_counts.values())
    target = min(sample_size, available_total)
    nonempty = [group for group, count in group_counts.items() if count]
    minimum = min(20, target // len(nonempty)) if nonempty else 0
    quotas = {group: min(count, minimum) for group, count in group_counts.items()}
    remaining = target - sum(quotas.values())
    while remaining > 0:
        candidates = [group for group in nonempty if quotas[group] < group_counts[group]]
        if not candidates:
            break
        capacity_total = sum(group_counts[group] - quotas[group] for group in candidates)
        additions = {}
        for group in candidates:
            capacity = group_counts[group] - quotas[group]
            additions[group] = min(capacity, max(1, math.floor(remaining * capacity / capacity_total)))
        added = 0
        for group in sorted(candidates, key=lambda value: (-group_counts[value], value)):
            if added >= remaining:
                break
            increment = min(additions[group], remaining - added)
            quotas[group] += increment
            added += increment
        remaining -= added
    return quotas


def _stable_hash(record: dict[str, object]) -> int:
    value = f"{record['local_item_key']}::{record['raw_item_name']}"
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:8], "big")


def select_stratified_sample(aliases: dict[tuple[str, str, str], AliasStats], sample_size: int) -> list[dict[str, object]]:
    group_counts = {group: 0 for group in FORECASTABLE_BY_GROUP}
    for stats in aliases.values():
        group_id = normalize_alias(stats)["item_group_id_candidate"]
        group_counts[str(group_id)] += 1
    quotas = _allocate_group_quotas(group_counts, sample_size)

    usage_heaps: dict[str, list[tuple[float, int, dict[str, object]]]] = {group: [] for group in quotas}
    hash_heaps: dict[str, list[tuple[int, int, dict[str, object]]]] = {group: [] for group in quotas}
    sequence = 0
    for stats in aliases.values():
        record = normalize_alias(stats)
        group = str(record["item_group_id_candidate"])
        quota = quotas[group]
        if quota == 0:
            continue
        high_usage_slots = max(1, quota // 2)
        usage_entry = (abs(float(record["usage_sum"])), sequence, record)
        if len(usage_heaps[group]) < high_usage_slots:
            heapq.heappush(usage_heaps[group], usage_entry)
        elif usage_entry[:2] > usage_heaps[group][0][:2]:
            heapq.heapreplace(usage_heaps[group], usage_entry)

        hash_value = _stable_hash(record)
        hash_entry = (-hash_value, sequence, record)
        if len(hash_heaps[group]) < quota:
            heapq.heappush(hash_heaps[group], hash_entry)
        elif hash_value < -hash_heaps[group][0][0]:
            heapq.heapreplace(hash_heaps[group], hash_entry)
        sequence += 1

    selected = []
    for group in sorted(quotas):
        seen = set()
        group_records = []
        for _, _, record in sorted(usage_heaps[group], key=lambda value: (-value[0], value[1])):
            key = (record["local_item_key"], record["raw_item_name"])
            if key not in seen:
                record["sampling_reason"] = "high_usage"
                group_records.append(record)
                seen.add(key)
        for _, _, record in sorted(hash_heaps[group], key=lambda value: (-value[0], value[1])):
            if len(group_records) >= quotas[group]:
                break
            key = (record["local_item_key"], record["raw_item_name"])
            if key not in seen:
                record["sampling_reason"] = "deterministic_diversity"
                group_records.append(record)
                seen.add(key)
        selected.extend(group_records)
    if len(selected) != min(sample_size, len(aliases)):
        raise RuntimeError(f"Expected {min(sample_size, len(aliases))} samples, selected {len(selected)}")
    return selected


def write_sample(records: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["sample_number", *records[0].keys()]
    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for index, record in enumerate(records, start=1):
            writer.writerow({"sample_number": index, **record})


def validate_normalized_record(record: dict[str, object]) -> list[str]:
    errors = []
    group_id = str(record["item_group_id_candidate"])
    intrinsic_group = str(record["intrinsic_item_group_id_candidate"])
    raw_name = str(record["raw_item_name"])
    product_name = str(record["product_name_candidate"])
    family_id = str(record["item_family_id_candidate"])
    family_name = str(record["standard_family_name_candidate"])

    if group_id not in FORECASTABLE_BY_GROUP or intrinsic_group not in FORECASTABLE_BY_GROUP:
        errors.append("unknown_item_group")
    if not record["institution_id"] or not record["local_item_code"] or not raw_name or not product_name:
        errors.append("missing_required_key")
    if bool(family_id) != bool(family_name):
        errors.append("family_id_name_inconsistent")
    if not family_id and family_name:
        errors.append("unresolved_exposed_as_standard_name")
    if group_id == "RENTAL" and "트렌탈" in raw_name:
        errors.append("trental_substring_collision")
    if group_id == "RENTAL" and RENTAL_CONSUMABLE_PATTERN.search(raw_name):
        errors.append("rental_consumable_as_asset")
    if group_id == "MED_INJECT" and "행주" in raw_name:
        errors.append("dishcloth_as_injection")
    if group_id == "SUPPLEMENT" and "오메가3산에틸에스테르" in raw_name:
        errors.append("prescription_omega3_as_supplement")
    if group_id == "KM_EXTRACT" and str(record["local_item_code"]).upper().startswith("W"):
        errors.append("conventional_drug_as_korean_medicine")
    if (
        group_id == "LAB_REAGENT"
        and re.search(r"배지|뱃지", raw_name, re.IGNORECASE)
        and NON_LAB_BADGE_PATTERN.search(raw_name)
    ):
        errors.append("badge_as_culture_media")
    if (
        group_id == "MED_ORAL"
        and str(record["local_item_code"]).upper().startswith("USE")
        and NON_ORAL_USE_CONTEXT_PATTERN.search(product_name)
    ):
        errors.append("non_dosage_korean_word_as_oral")
    if group_id == "MED_SUPPLY" and NON_MEDICAL_SUPPLY_PATTERN.search(product_name):
        errors.append("nonmedical_band_as_supply")
    if group_id == "MED_TOPICAL" and NON_MEDICAL_TOPICAL_PATTERN.search(product_name):
        errors.append("nonmedical_cream_as_topical")
    if product_name.startswith("용]"):
        errors.append("broken_operational_wrapper")
    expected_forecast = str(record["is_forecastable_override"]) or FORECASTABLE_BY_GROUP[group_id]
    if str(record["effective_is_forecastable"]) != expected_forecast:
        errors.append("forecast_policy_inconsistent")
    if record["normalization_status"] == "unresolved" and intrinsic_group != "UNCLASSIFIED":
        errors.append("unresolved_status_inconsistent")
    return errors


def write_all_alias_candidates(
    aliases: dict[tuple[str, str, str], AliasStats],
    output_path: Path,
    batch_size: int = 50_000,
) -> tuple[dict[tuple[str, str, str], tuple[object, ...]], dict[str, object]]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.name}.tmp")
    temporary_path.unlink(missing_ok=True)
    writer = None
    batch = []
    normalized_by_key: dict[tuple[str, str, str], tuple[object, ...]] = {}
    group_counts = Counter()
    status_counts = Counter()
    validation_counts = Counter()
    validation_examples = []

    def flush() -> None:
        nonlocal writer, batch
        if not batch:
            return
        table = pa.Table.from_pylist(batch)
        if writer is None:
            writer = pq.ParquetWriter(temporary_path, table.schema, compression="zstd", use_dictionary=True)
        writer.write_table(table)
        batch = []

    try:
        for index, (key, stats) in enumerate(aliases.items(), start=1):
            record = normalize_alias(stats)
            errors = validate_normalized_record(record)
            for error in errors:
                validation_counts[error] += 1
            if errors and len(validation_examples) < 100:
                validation_examples.append({"key": list(key), "errors": errors, "raw_item_name": stats.raw_item_name})
            group_counts[str(record["item_group_id_candidate"])] += 1
            status_counts[str(record["normalization_status"])] += 1
            normalized_by_key[key] = tuple(record[column] for column in FACT_NORMALIZATION_COLUMNS)
            batch.append(record)
            if len(batch) >= batch_size:
                flush()
            if index % 100_000 == 0:
                LOGGER.info("Normalized %s/%s aliases", f"{index:,}", f"{len(aliases):,}")
        flush()
    finally:
        if writer is not None:
            writer.close()

    if validation_counts:
        temporary_path.unlink(missing_ok=True)
        raise ValueError(
            "Full alias normalization failed quality gates: "
            + json.dumps(
                {"counts": dict(validation_counts), "examples": validation_examples},
                ensure_ascii=False,
            )
        )
    parquet_rows = pq.ParquetFile(temporary_path).metadata.num_rows
    if parquet_rows != len(aliases):
        temporary_path.unlink(missing_ok=True)
        raise ValueError(f"Alias parquet row mismatch: expected {len(aliases)}, got {parquet_rows}")
    temporary_path.replace(output_path)
    metrics = {
        "alias_rows": len(aliases),
        "group_counts": dict(sorted(group_counts.items())),
        "normalization_status_counts": dict(sorted(status_counts.items())),
        "quality_gate_error_counts": {},
    }
    return normalized_by_key, metrics


def write_normalized_stock_fact(
    files: list[Path],
    normalized_by_key: dict[tuple[str, str, str], tuple[object, ...]],
    output_path: Path,
    expected_rows: int,
    batch_size: int = 100_000,
) -> dict[str, object]:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.name}.tmp")
    temporary_path.unlink(missing_ok=True)
    writer = None
    processed_rows = 0
    missing_joins = 0
    group_counts = Counter()
    status_counts = Counter()
    mapping_columns: dict[str, list[object]] = {
        "__institution_id": [],
        "__local_item_code": [],
        "__alias_cleaned_name": [],
        **{column: [] for column in FACT_NORMALIZATION_COLUMNS},
    }
    for key, normalized in normalized_by_key.items():
        mapping_columns["__institution_id"].append(key[0])
        mapping_columns["__local_item_code"].append(key[1])
        mapping_columns["__alias_cleaned_name"].append(key[2])
        for column, value in zip(FACT_NORMALIZATION_COLUMNS, normalized):
            mapping_columns[column].append(value)
    mapping = pd.DataFrame(mapping_columns)

    try:
        for path in files:
            LOGGER.info("Joining normalization onto %s", path)
            file_row_offset = 0
            chunks = pd.read_csv(
                path,
                sep="|",
                quotechar='"',
                dtype=str,
                keep_default_na=False,
                encoding="utf-8-sig",
                chunksize=batch_size,
                on_bad_lines="error",
            )
            for chunk in chunks:
                if chunk.columns.tolist() != RAW_STOCK_COLUMNS:
                    raise ValueError(f"Unexpected raw_stock header in {path}: {chunk.columns.tolist()}")
                chunk["__institution_id"] = chunk["보건기관코드_en"].str.strip()
                chunk["__local_item_code"] = chunk["물품코드"].str.strip()
                chunk["__alias_cleaned_name"] = chunk["물품명"].map(clean_item_name)
                chunk["source_file"] = path.name
                chunk["source_logical_row"] = range(file_row_offset + 2, file_row_offset + len(chunk) + 2)
                file_row_offset += len(chunk)
                merged = chunk.merge(
                    mapping,
                    how="left",
                    on=["__institution_id", "__local_item_code", "__alias_cleaned_name"],
                    validate="many_to_one",
                    sort=False,
                )
                chunk_missing = int(merged["item_group_id_candidate"].isna().sum())
                missing_joins += chunk_missing
                if chunk_missing:
                    examples = merged.loc[
                        merged["item_group_id_candidate"].isna(),
                        ["보건기관코드_en", "물품코드", "물품명"],
                    ].head(10)
                    raise ValueError(f"Missing {chunk_missing} normalization joins in {path}: {examples.to_dict('records')}")
                group_counts.update(merged["item_group_id_candidate"].value_counts().to_dict())
                status_counts.update(merged["normalization_status"].value_counts().to_dict())
                merged = merged.drop(columns=["__institution_id", "__local_item_code", "__alias_cleaned_name"])
                table = pa.Table.from_pandas(merged, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(
                        temporary_path,
                        table.schema,
                        compression="zstd",
                        use_dictionary=True,
                    )
                writer.write_table(table)
                processed_rows += len(merged)
                if processed_rows % 1_000_000 < batch_size:
                    LOGGER.info("Wrote %s normalized stock rows", f"{processed_rows:,}")
    finally:
        if writer is not None:
            writer.close()

    if missing_joins or processed_rows != expected_rows:
        temporary_path.unlink(missing_ok=True)
        raise ValueError(
            f"Normalized stock join failed: rows={processed_rows}, expected={expected_rows}, missing={missing_joins}"
        )
    parquet_rows = pq.ParquetFile(temporary_path).metadata.num_rows
    if parquet_rows != expected_rows:
        temporary_path.unlink(missing_ok=True)
        raise ValueError(f"Stock parquet row mismatch: expected {expected_rows}, got {parquet_rows}")
    temporary_path.replace(output_path)
    return {
        "stock_rows": processed_rows,
        "missing_alias_joins": missing_joins,
        "group_counts": dict(sorted(group_counts.items())),
        "normalization_status_counts": dict(sorted(status_counts.items())),
        "raw_values_preserved_as_strings": True,
    }


def generate_full_normalization(
    raw_dir: Path = RAW_STOCK_DIR,
    pattern: str = RAW_STOCK_FILE_PATTERN,
    sample_output_path: Path = DEFAULT_OUTPUT_PATH,
    alias_output_path: Path = DEFAULT_ALIAS_OUTPUT_PATH,
    stock_output_path: Path = DEFAULT_STOCK_OUTPUT_PATH,
    report_output_path: Path = DEFAULT_REPORT_OUTPUT_PATH,
    sample_size: int = 1000,
) -> dict[str, object]:
    files = discover_raw_stock_files(raw_dir, pattern)
    if not files:
        raise FileNotFoundError(f"No raw_stock files found under {raw_dir} with pattern {pattern}")
    aliases = extract_alias_stats(files)
    expected_rows = sum(stats.occurrence_count for stats in aliases.values())
    sample = select_stratified_sample(aliases, sample_size)
    write_sample(sample, sample_output_path)
    normalized_by_key, alias_metrics = write_all_alias_candidates(aliases, alias_output_path)
    del aliases
    stock_metrics = write_normalized_stock_fact(files, normalized_by_key, stock_output_path, expected_rows)
    report = {
        "normalization_version": NORMALIZATION_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_files": [path.name for path in files],
        "sample_rows": len(sample),
        "sample_output": str(sample_output_path),
        "alias_output": str(alias_output_path),
        "stock_output": str(stock_output_path),
        "alias_metrics": alias_metrics,
        "stock_metrics": stock_metrics,
    }
    report_output_path.parent.mkdir(parents=True, exist_ok=True)
    with report_output_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    LOGGER.info("Saved full normalization report to %s", report_output_path)
    return report


def generate_normalization_sample(
    raw_dir: Path = RAW_STOCK_DIR,
    pattern: str = RAW_STOCK_FILE_PATTERN,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    sample_size: int = 1000,
) -> Path:
    files = discover_raw_stock_files(raw_dir, pattern)
    if not files:
        raise FileNotFoundError(f"No raw_stock files found under {raw_dir} with pattern {pattern}")
    aliases = extract_alias_stats(files)
    records = select_stratified_sample(aliases, sample_size)
    write_sample(records, output_path)
    LOGGER.info("Saved %s normalization samples to %s", len(records), output_path)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate raw_stock item-normalization candidates")
    parser.add_argument("--raw-dir", type=Path, default=RAW_STOCK_DIR)
    parser.add_argument("--pattern", default=RAW_STOCK_FILE_PATTERN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--full", action="store_true", help="Write all aliases and the normalized stock fact")
    parser.add_argument("--alias-output", type=Path, default=DEFAULT_ALIAS_OUTPUT_PATH)
    parser.add_argument("--stock-output", type=Path, default=DEFAULT_STOCK_OUTPUT_PATH)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT_OUTPUT_PATH)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.full:
        generate_full_normalization(
            raw_dir=args.raw_dir,
            pattern=args.pattern,
            sample_output_path=args.output,
            alias_output_path=args.alias_output,
            stock_output_path=args.stock_output,
            report_output_path=args.report_output,
            sample_size=args.sample_size,
        )
    else:
        generate_normalization_sample(args.raw_dir, args.pattern, args.output, args.sample_size)


if __name__ == "__main__":
    main()
