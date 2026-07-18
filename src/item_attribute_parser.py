import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlparse

import pandas as pd

from .config import MAPPING_DATA_DIR, PROCESSED_DATA_DIR, PROJECT_ROOT, SAMPLE_DATA_DIR


ATTRIBUTE_PARSER_VERSION = "item-attribute-parser-v1.1"
DICTIONARY_VERSION = "item-attribute-evidence-v1.0"

DEFAULT_ALIAS_PATH = PROCESSED_DATA_DIR / "item_alias_candidates_v0.3.parquet"
DEFAULT_WORKLIST_PATH = PROCESSED_DATA_DIR / "item_product_worklist_v1.parquet"
DEFAULT_SUGGESTION_PATH = (
    PROCESSED_DATA_DIR / "item_material_pipeline" / "item_family_candidate_suggestions_full.csv"
)
DEFAULT_OFFICIAL_NEDRUG_PATH = (
    PROJECT_ROOT / "data" / "external" / "official" / "mfds_nedrug_web.parquet"
)
DEFAULT_INGREDIENT_PATH = (
    PROJECT_ROOT / "pipelines" / "item_material" / "data" / "ingredient_ko_en.tsv"
)
DEFAULT_DICTIONARY_PATH = MAPPING_DATA_DIR / "item_attribute_evidence_dictionary_v1.csv"
DEFAULT_ALIAS_OUTPUT_PATH = PROCESSED_DATA_DIR / "item_alias_attributes_v1.parquet"
DEFAULT_REPRESENTATIVE_OUTPUT_PATH = (
    PROCESSED_DATA_DIR / "item_representative_attributes_v1.parquet"
)
DEFAULT_EXTERNAL_CANDIDATE_PATH = (
    PROCESSED_DATA_DIR / "item_external_match_candidates_v1.csv"
)
DEFAULT_SAMPLE_PATH = SAMPLE_DATA_DIR / "item_attribute_review_sample_1000.csv"
DEFAULT_REPORT_PATH = PROCESSED_DATA_DIR / "item_attribute_parser_v1_report.json"

MEDICATION_GROUPS = {
    "MED_ORAL",
    "MED_INJECT",
    "MED_TOPICAL",
    "KM_EXTRACT",
    "KM_HERB",
    "SUPPLEMENT",
}
NEEDLE_FAMILIES = {
    "DISPOSABLE_SYRINGE",
    "INJECTION_NEEDLE",
    "BLOOD_LANCET",
    "CATHETER",
    "ANGIO_CATHETER",
}

NEEDLE_CONTEXT_PATTERN = re.compile(
    r"주사\s*기|주사\s*(?:침|바늘)|채혈\s*침|란셋|난셋|lancet|lanset|safelan|"
    r"니들|needle|유침|카테터|카데터|캐뉼라|cannula|cath|angio|나비\s*바늘|"
    r"butterfly|메디컷|젤코|jelco|혈관내\s*튜브",
    re.IGNORECASE,
)
GAUGE_PATTERN = re.compile(
    r"(?<!\d)(?P<value>\d{1,2})\s*\(?\s*"
    r"(?:G(?=\s*(?:[x*×]\s*\d|[^A-Za-z]|$))|게이지)\s*\)?"
    r"(?:\s*[x*×](?=\s*\d))?",
    re.IGNORECASE,
)
VOLUME_PATTERN = re.compile(
    r"(?<!\d)(?P<value>\d+(?:[,.]\d+)?)\s*"
    r"(?P<unit>밀리리터|리터|씨씨|cc|mL|ml|L)\s*(?P<qualifier>이하|초과)?",
    re.IGNORECASE,
)
SPECIAL_PACKAGE_VOLUME_PATTERN = re.compile(
    r"(?P<value>\d+(?:[,.]\d+)?)\s*\(\s*(?P<count>\d+)\s*\)\s*"
    r"(?P<unit>밀리리터|리터|씨씨|cc|mL|ml|L)\s*/\s*"
    r"(?P<pack>병|백|팩|통)",
    re.IGNORECASE,
)
PER_PACKAGE_MEASURE_PATTERN = re.compile(
    r"(?P<value>\d+(?:[,.]\d+)?)\s*"
    r"(?P<measure_unit>mL|ml|cc|L|밀리리터|리터|mg|g|그램|그람)\s*/\s*"
    r"(?P<pack>병|포|관|개|백|팩|통|튜브)",
    re.IGNORECASE,
)
DIMENSION_PATTERN = re.compile(
    r"(?<!\d)(?P<first>\d+(?:\.\d+)?)\s*(?P<first_unit>mm|cm|m)\s*"
    r"[x*×]\s*(?P<second>\d+(?:\.\d+)?)\s*(?P<second_unit>mm|cm|m)",
    re.IGNORECASE,
)
NEEDLE_LENGTH_PATTERN = re.compile(
    r"(?<!\d)(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|inch|인치|\")",
    re.IGNORECASE,
)
STRENGTH_PATTERN = re.compile(
    r"(?<!\d)(?P<value>\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)*?)\s*"
    r"(?P<unit>밀리그람|밀리그램|마이크로그람|마이크로그램|그램|그람|"
    r"mg|mcg|ug|μg|㎍|g|IU|단위|%)(?![A-Za-z])",
    re.IGNORECASE,
)
CONCENTRATION_PATTERN = re.compile(
    r"(?<!\d)(?P<numerator>\d+(?:\.\d+)?)\s*"
    r"(?P<numerator_unit>mg|mcg|ug|μg|㎍|g|IU|밀리그람|밀리그램|그램|그람)\s*/\s*"
    r"(?P<denominator>\d+(?:[,.]\d+)?)\s*"
    r"(?P<denominator_unit>mL|ml|cc|L|밀리리터|리터|"
    r"mg|mcg|ug|μg|㎍|g|밀리그람|밀리그램|그램|그람|"
    r"정|캡슐|캅셀|포|병|바이알|앰플)",
    re.IGNORECASE,
)
PACK_PATTERN = re.compile(
    r"(?<!\d)(?P<count>\d+)\s*(?P<unit>개입|개|정|캡슐|캅셀|캡|매|"
    r"병|바이알|앰플|포|통|롤|roll|T|EA)(?![A-Za-z])",
    re.IGNORECASE,
)
MODEL_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]{3,}(?![A-Za-z0-9])")
URL_PATTERN = re.compile(r"https?://[^\s;]+")
NEDRUG_ITEM_SEQ_PATTERN = re.compile(r"itemSeq=(\d+)")

COMPANY_MARKER_PATTERN = re.compile(
    r"제약|약품|파마|바이오|메디텍|메디칼|메디컬|헬스케어|양행|"
    r"의료기|산업|상사|팜|유한양행|녹십자|종근당|셀트리온|"
    r"한독|유유|일화|서흥|익수|아이큐어|동아|일동|신일|한미|대웅|"
    r"보령|유한|휴온스|광동|동화|태극|삼남|삼일|영진|풍림|화일|조아|"
    r"알리코|셀티스|코리아|^한국",
    re.IGNORECASE,
)
CORPORATE_FORM_PATTERN = re.compile(r"\(주\)|주식회사|㈜|\(유\)|유한회사", re.IGNORECASE)
NON_COMPANY_DETAIL_PATTERN = re.compile(
    r"급여|비급여|보훈|방문|무료|유료|남녀|성인|소아|사용량|기준|"
    r"수출명|샘플|홍보|대여|재활|본소|지소|정주용|근주용|둥근머리|"
    r"소형|중형|대형|색상",
    re.IGNORECASE,
)
OPERATIONAL_PATTERN = re.compile(
    r"방문|홍보|판촉|무료|유료|비급여|급여|보훈|대여|재활|본소|지소|내당",
    re.IGNORECASE,
)
INGREDIENT_LIKE_PATTERN = re.compile(
    r"염산염|브롬화물|황산염|인산염|질산염|칼슘|나트륨|칼륨|수화물|"
    r"무수물|아세테이트|시트르산|베실산염|메실산염|말레산염|푸마르산염|"
    r"추출물|추출액|건조엑스|농축액|백신|톡소이드",
    re.IGNORECASE,
)

PACK_UNIT_CODES = {
    "개입": "EA",
    "개": "EA",
    "EA": "EA",
    "정": "TABLET",
    "T": "TABLET",
    "캡슐": "CAPSULE",
    "캅셀": "CAPSULE",
    "캡": "CAPSULE",
    "매": "SHEET",
    "병": "BOTTLE",
    "바이알": "VIAL",
    "앰플": "AMPULE",
    "포": "SACHET",
    "통": "CONTAINER",
    "롤": "ROLL",
    "ROLL": "ROLL",
    "백": "BAG",
    "팩": "PACK",
    "관": "TUBE",
    "튜브": "TUBE",
}
DOSAGE_FORM_UNITS = {
    "정제": "TABLET",
    "캡슐": "CAPSULE",
    "환": "TABLET",
    "과립": "SACHET",
    "시럽": "BOTTLE",
    "현탁액": "BOTTLE",
    "내용액": "BOTTLE",
    "주사제": "VIAL_OR_AMPULE",
    "점안제": "BOTTLE",
    "점이제": "BOTTLE",
    "연고": "TUBE",
    "크림": "TUBE",
    "패치/파스": "SHEET",
}
OFFICIAL_REGULATOR_DOMAINS = {
    "nedrug.mfds.go.kr",
    "mfds.go.kr",
    "www.mfds.go.kr",
    "data.go.kr",
    "www.data.go.kr",
    "law.go.kr",
    "www.law.go.kr",
    "accessdata.fda.gov",
    "www.fda.gov",
    "who.int",
    "www.who.int",
    "extranet.who.int",
}
OFFICIAL_MANUFACTURER_DOMAINS = {
    "celltrionph.com",
    "www.celltrionph.com",
    "yuhan.co.kr",
    "www.yuhan.co.kr",
    "samnam51.com",
    "www.samnam51.com",
    "nelsonpharm.co.kr",
    "www.nelsonpharm.co.kr",
}
OFFICIAL_PUBLIC_AGENCY_DOMAINS = {"hira.or.kr", "www.hira.or.kr"}

DICTIONARY_COLUMNS = [
    "dictionary_id",
    "entry_type",
    "match_key",
    "canonical_id",
    "canonical_value",
    "applicable_context",
    "source_name",
    "source_record_id",
    "source_url",
    "source_tier",
    "verification_status",
    "confidence",
    "retrieved_at",
    "evidence_note",
    "dictionary_version",
]


@dataclass(frozen=True)
class ParsedToken:
    token_type: str
    raw: str
    normalized: str
    start: int
    end: int
    confidence: float
    source: str
    role: str = ""


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _clean(value: object) -> str:
    text = unicodedata.normalize("NFKC", _text(value))
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def _compact(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", _clean(value).lower())


def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("||".join(values).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _normalize_number(value: str) -> str:
    normalized = value.replace(",", "")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


def _normalize_volume(value: str, unit: str, qualifier: str = "") -> tuple[str, str, str]:
    normalized_unit = "mL" if unit.lower() in {"ml", "cc", "밀리리터", "씨씨"} else "L"
    normalized_value = _normalize_number(value)
    prefix = "<=" if qualifier == "이하" else ">" if qualifier == "초과" else ""
    return normalized_value, normalized_unit, f"{prefix}{normalized_value}{normalized_unit}"


def _normalize_strength(value: str, unit: str) -> str:
    unit_map = {
        "밀리그람": "mg",
        "밀리그램": "mg",
        "마이크로그람": "mcg",
        "마이크로그램": "mcg",
        "ug": "mcg",
        "μg": "mcg",
        "㎍": "mcg",
        "g": "g",
        "mg": "mg",
        "mcg": "mcg",
        "그램": "g",
        "그람": "g",
        "단위": "IU",
    }
    normalized_unit = unit_map.get(unit.lower(), unit)
    if normalized_unit.lower() == "iu":
        normalized_unit = "IU"
    values = "/".join(_normalize_number(part.strip()) for part in value.split("/"))
    return f"{values}{normalized_unit}"


def _overlaps(start: int, end: int, token: ParsedToken) -> bool:
    return start < token.end and token.start < end


def _add_token(tokens: list[ParsedToken], token: ParsedToken) -> bool:
    if token.start >= 0 and any(
        existing.start >= 0 and _overlaps(token.start, token.end, existing)
        for existing in tokens
    ):
        return False
    tokens.append(token)
    return True


def _top_level_parentheticals(text: str) -> list[tuple[int, int, str]]:
    pairs = {"(": ")", "[": "]"}
    stack: list[tuple[str, int]] = []
    output = []
    for index, character in enumerate(text):
        if character in pairs:
            stack.append((character, index))
        elif character in pairs.values() and stack:
            opener, start = stack[-1]
            if pairs[opener] != character:
                continue
            stack.pop()
            if not stack:
                output.append((start, index + 1, text[start + 1 : index]))
    return output


def _canonical_company(value: str) -> str:
    company = CORPORATE_FORM_PATTERN.sub("", value)
    company = company.replace("(", "").replace(")", "")
    return re.sub(r"\s+", "", company).strip("-_/.,")


def _dictionary_base_name(value: str) -> str:
    name = _clean(value)
    previous = None
    while previous != name:
        previous = name
        name = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", "", name)
    name = STRENGTH_PATTERN.sub("", name)
    name = re.sub(r"[-_/ ]*\d+\s*(?:정|캡슐|캅셀|포|병|개|매|앰플|바이알)\s*$", "", name)
    return re.sub(r"[\s_\-/()\[\]]+", "", name)


def _static_dictionary_rows(retrieved_at: str) -> list[dict[str, object]]:
    fda_url = (
        "https://www.fda.gov/regulatory-information/search-fda-guidance-documents/"
        "guidance-content-premarket-notification-510k-submissions-piston-syringes"
    )
    fda_standard_url = (
        "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfstandards/"
        "detail.cfm?standard__identification_no=34431"
    )
    safelan_fda_url = "https://www.accessdata.fda.gov/cdrh_docs/pdf23/K230759.pdf"
    celltrion_url = (
        "https://www.celltrionph.com/ko-kr/product/introducedetail?modify_key=18"
    )
    common = {"retrieved_at": retrieved_at, "dictionary_version": DICTIONARY_VERSION}
    rows = [
        {
            **common,
            "dictionary_id": "DICT_NEEDLE_GAUGE_FDA_GUIDANCE",
            "entry_type": "unit_semantics",
            "match_key": "G",
            "canonical_id": "NEEDLE_GAUGE",
            "canonical_value": "needle gauge",
            "applicable_context": "needle_device",
            "source_name": "FDA piston syringe 510(k) guidance",
            "source_record_id": "physical_specifications_hypodermic_needle",
            "source_url": fda_url,
            "source_tier": "official_regulator",
            "verification_status": "verified_official",
            "confidence": 1.0,
            "evidence_note": "FDA lists syringe size, needle gauge, needle length, and quantity as separate label attributes.",
        },
        {
            **common,
            "dictionary_id": "DICT_NEEDLE_GAUGE_ISO6009_FDA",
            "entry_type": "unit_semantics",
            "match_key": "G",
            "canonical_id": "NEEDLE_GAUGE",
            "canonical_value": "needle gauge",
            "applicable_context": "needle_device",
            "source_name": "FDA recognized consensus standard ISO 6009:2016",
            "source_record_id": "FDA_FR_6-381",
            "source_url": fda_standard_url,
            "source_tier": "official_regulator",
            "verification_status": "verified_multi_source",
            "confidence": 1.0,
            "evidence_note": "The recognized standard identifies hypodermic needles by designated metric and Gauge.",
        },
        {
            **common,
            "dictionary_id": "DICT_FDA_PRODUCT_SAFELAN",
            "entry_type": "product_alias",
            "match_key": "SafeLan",
            "canonical_id": "FDA_DEVICE::K230759::SAFELAN",
            "canonical_value": "SafeLan blood lancet",
            "applicable_context": "MED_SUPPLY",
            "source_name": "FDA 510(k) K230759",
            "source_record_id": "K230759",
            "source_url": safelan_fda_url,
            "source_tier": "official_regulator",
            "verification_status": "verified_official",
            "confidence": 0.99,
            "evidence_note": "FDA identifies SafeLan 26G and 30G as single-use blood lancet models.",
        },
        {
            **common,
            "dictionary_id": "DICT_FDA_FAMILY_SAFELAN",
            "entry_type": "product_family",
            "match_key": "SafeLan",
            "canonical_id": "BLOOD_LANCET",
            "canonical_value": "채혈침",
            "applicable_context": "MED_SUPPLY",
            "source_name": "FDA 510(k) K230759",
            "source_record_id": "K230759",
            "source_url": safelan_fda_url,
            "source_tier": "official_regulator",
            "verification_status": "verified_official",
            "confidence": 0.99,
            "evidence_note": "FDA common name and classification identify SafeLan as a blood lancet.",
        },
        {
            **common,
            "dictionary_id": "DICT_FDA_MANUFACTURER_SAFELAN",
            "entry_type": "product_manufacturer",
            "match_key": "SafeLan",
            "canonical_id": "MANUFACTURER::BOSUNGMEDITECH",
            "canonical_value": "BOSUNGMEDITECH CO., LTD.",
            "applicable_context": "MED_SUPPLY",
            "source_name": "FDA 510(k) K230759",
            "source_record_id": "K230759",
            "source_url": safelan_fda_url,
            "source_tier": "official_regulator",
            "verification_status": "verified_official",
            "confidence": 0.99,
            "evidence_note": "FDA filing lists BOSUNGMEDITECH CO., LTD. as the applicant for SafeLan.",
        },
        {
            **common,
            "dictionary_id": "DICT_PRODUCT_GODEX_CAPSULE",
            "entry_type": "product_alias",
            "match_key": "고덱스캡슐",
            "canonical_id": "MANUFACTURER_PRODUCT::CELLTRIONPH::GODEX_CAPSULE",
            "canonical_value": "고덱스캡슐",
            "applicable_context": "MED_ORAL",
            "source_name": "셀트리온제약 제품소개",
            "source_record_id": "modify_key=18",
            "source_url": celltrion_url,
            "source_tier": "official_manufacturer",
            "verification_status": "verified_official",
            "confidence": 0.98,
            "evidence_note": "Official manufacturer product page identifies the product, ingredients, strength, and package units.",
        },
        {
            **common,
            "dictionary_id": "DICT_PRODUCT_INGREDIENT_GODEX_CAPSULE",
            "entry_type": "product_ingredient",
            "match_key": "고덱스캡슐",
            "canonical_id": (
                "CARNITINE_OROTATE;ANTITOXIC_LIVER_EXTRACT;ADENINE_HYDROCHLORIDE;"
                "PYRIDOXINE_HYDROCHLORIDE;RIBOFLAVIN;CYANOCOBALAMIN;"
                "BIPHENYL_DIMETHYL_DICARBOXYLATE"
            ),
            "canonical_value": (
                "오로트산카르니틴;항독성간장엑스;아데닌염산염;피리독신염산염;"
                "리보플라빈;시아노코발라민;비페닐디메틸디카르복실레이트"
            ),
            "applicable_context": "MED_ORAL",
            "source_name": "셀트리온제약 제품소개",
            "source_record_id": "modify_key=18",
            "source_url": celltrion_url,
            "source_tier": "official_manufacturer",
            "verification_status": "verified_official",
            "confidence": 0.98,
            "evidence_note": "Ingredients are listed on the official manufacturer product page.",
        },
        {
            **common,
            "dictionary_id": "DICT_PRODUCT_MANUFACTURER_GODEX_CAPSULE",
            "entry_type": "product_manufacturer",
            "match_key": "고덱스캡슐",
            "canonical_id": "MANUFACTURER::CELLTRION_PHARM",
            "canonical_value": "셀트리온제약",
            "applicable_context": "MED_ORAL",
            "source_name": "셀트리온제약 제품소개",
            "source_record_id": "modify_key=18",
            "source_url": celltrion_url,
            "source_tier": "official_manufacturer",
            "verification_status": "verified_official",
            "confidence": 0.98,
            "evidence_note": "Product is published in the official Celltrion Pharm product catalog.",
        },
    ]

    trusted_products = [
        {
            "prefix": "YUHAN_PENIRAMIN",
            "match_key": "페니라민정",
            "product_id": "MANUFACTURER_PRODUCT::YUHAN::PENIRAMIN_TABLET",
            "product_name": "페니라민정",
            "ingredient_id": "CHLORPHENIRAMINE_MALEATE",
            "ingredient_name": "클로르페니라민말레산염",
            "manufacturer_id": "MANUFACTURER::YUHAN",
            "manufacturer_name": "유한양행",
            "strength": "2mg",
            "source_name": "유한양행 제품정보",
            "source_record_id": "YPRD_IDX=1865",
            "source_url": "https://www.yuhan.co.kr/Products/List/?Category=315&YPRD_IDX=1865&cid=177&mode=view&p=1&sf=YPRD_SORT&sm=-1",
            "source_tier": "official_manufacturer",
            "verification_status": "verified_official",
            "confidence": 0.99,
            "evidence_note": "Official manufacturer page lists the product, chlorpheniramine maleate 2mg per tablet, and package unit.",
        },
        {
            "prefix": "SAMNAM_ACETAMINOPHEN_500",
            "match_key": "삼남아세트아미노펜정",
            "product_id": "MANUFACTURER_PRODUCT::SAMNAM::ACETAMINOPHEN_500_TABLET",
            "product_name": "삼남아세트아미노펜정500mg",
            "ingredient_id": "ACETAMINOPHEN",
            "ingredient_name": "아세트아미노펜",
            "manufacturer_id": "MANUFACTURER::SAMNAM_PHARM",
            "manufacturer_name": "삼남제약",
            "strength": "500mg",
            "source_name": "삼남제약 제품정보",
            "source_record_id": "com_board_id=18&com_board_idx=9",
            "source_url": "https://www.samnam51.com/bizdemo39879/mp3/mp3_sub15.php?com_board_basic=read_form&com_board_id=18&com_board_idx=9&sub=01",
            "source_tier": "official_manufacturer",
            "verification_status": "verified_official",
            "confidence": 0.99,
            "evidence_note": "Official manufacturer page lists acetaminophen as the active ingredient, 500mg product strength, and package units.",
        },
        {
            "prefix": "SINIL_THIAMINE_10",
            "match_key": "신일티아민염산염정",
            "product_id": "HIRA_DRUG::653801460",
            "product_name": "신일티아민염산염정10밀리그램",
            "ingredient_id": "THIAMINE_HYDROCHLORIDE",
            "ingredient_name": "티아민염산염",
            "manufacturer_id": "MANUFACTURER::SINIL_PHARM",
            "manufacturer_name": "신일제약",
            "strength": "10mg",
            "source_name": "건강보험심사평가원 + 약학정보원",
            "source_record_id": "653801460",
            "source_url": "https://www.hira.or.kr/ra/medi/getHistoryList.do?artcNm=653801460&isActivity=Y&isDown=N&pageIndex=1&pgmid=HIRAA030035020000&srchCnd=%5B653801460%5D",
            "source_tier": "official_public_agency",
            "verification_status": "verified_multi_source",
            "confidence": 0.97,
            "evidence_note": "HIRA verifies the exact 10mg product code; Korea Pharmaceutical Information Center independently lists thiamine hydrochloride 10mg and Sinil Pharm (https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11A2070B0018).",
        },
        {
            "prefix": "NELSON_IBUPROFEN_200",
            "match_key": "한국넬슨이부프로펜정",
            "product_id": "MANUFACTURER_PRODUCT::NELSON::IBUPROFEN_200_TABLET",
            "product_name": "넬슨이부프로펜정200밀리그램(이부프로펜)",
            "ingredient_id": "IBUPROFEN",
            "ingredient_name": "이부프로펜",
            "manufacturer_id": "MANUFACTURER::NELSON_PHARM",
            "manufacturer_name": "한국넬슨제약",
            "strength": "200mg",
            "source_name": "한국넬슨제약 제품목록 + 대한의사협회 Drug DB",
            "source_record_id": "it_id=1724822145",
            "source_url": "https://nelsonpharm.co.kr/product/list.php?ca_id=10",
            "source_tier": "official_manufacturer",
            "verification_status": "verified_multi_source",
            "confidence": 0.97,
            "evidence_note": "Official manufacturer catalog lists the 200mg ibuprofen tablet; KMA Drug DB independently matches the legacy raw name, ingredient, strength, manufacturer, and tablet form (https://www.snubi.org/~kma/new_kma/contents/search/?CodeProd=A18400091&_p=ProdInfo1).",
        },
    ]
    for product in trusted_products:
        product_common = {
            **common,
            "match_key": product["match_key"],
            "applicable_context": "MED_ORAL",
            "source_name": product["source_name"],
            "source_record_id": product["source_record_id"],
            "source_url": product["source_url"],
            "source_tier": product["source_tier"],
            "verification_status": product["verification_status"],
            "confidence": product["confidence"],
            "evidence_note": product["evidence_note"],
        }
        rows.extend(
            [
                {
                    **product_common,
                    "dictionary_id": f"DICT_PRODUCT_{product['prefix']}",
                    "entry_type": "product_alias",
                    "canonical_id": product["product_id"],
                    "canonical_value": product["product_name"],
                },
                {
                    **product_common,
                    "dictionary_id": f"DICT_PRODUCT_INGREDIENT_{product['prefix']}",
                    "entry_type": "product_ingredient",
                    "canonical_id": product["ingredient_id"],
                    "canonical_value": product["ingredient_name"],
                },
                {
                    **product_common,
                    "dictionary_id": f"DICT_PRODUCT_MANUFACTURER_{product['prefix']}",
                    "entry_type": "product_manufacturer",
                    "canonical_id": product["manufacturer_id"],
                    "canonical_value": product["manufacturer_name"],
                },
                {
                    **product_common,
                    "dictionary_id": f"DICT_PRODUCT_STRENGTH_{product['prefix']}",
                    "entry_type": "product_strength",
                    "canonical_id": f"{product['product_id']}::STRENGTH",
                    "canonical_value": product["strength"],
                },
            ]
        )
    return rows


def build_verified_evidence_dictionary(
    official_nedrug_path: Path = DEFAULT_OFFICIAL_NEDRUG_PATH,
    output_path: Path = DEFAULT_DICTIONARY_PATH,
) -> pd.DataFrame:
    retrieved_at = datetime.now(timezone.utc).isoformat()
    rows = _static_dictionary_rows(retrieved_at)
    if official_nedrug_path.exists():
        official = pd.read_parquet(official_nedrug_path)
        for record in official.to_dict("records"):
            if int(record.get("http_status", 0)) != 200:
                continue
            item_seq = _text(record.get("item_seq"))
            source_name = _text(record.get("source_item_name"))
            match_key = _dictionary_base_name(source_name)
            source_url = _text(record.get("source_url"))
            common = {
                "applicable_context": "medication",
                "source_name": "식품의약품안전처 의약품안전나라",
                "source_record_id": item_seq,
                "source_url": source_url,
                "source_tier": "official_regulator",
                "verification_status": "verified_official",
                "confidence": 0.99,
                "retrieved_at": _text(record.get("retrieved_at")) or retrieved_at,
                "dictionary_version": DICTIONARY_VERSION,
            }
            rows.append(
                {
                    **common,
                    "dictionary_id": f"DICT_MFDS_PRODUCT_{item_seq}",
                    "entry_type": "product_alias",
                    "match_key": match_key,
                    "canonical_id": f"MFDS_DRUG::{item_seq}",
                    "canonical_value": source_name,
                    "evidence_note": "Official NEDrug page fetched with HTTP 200 and response SHA256 recorded.",
                }
            )
            family_ids = [value for value in _text(record.get("verified_family_ids")).split(";") if value]
            family_names = [value for value in _text(record.get("verified_family_tokens")).split(";") if value]
            if family_ids and family_names:
                rows.append(
                    {
                        **common,
                        "dictionary_id": f"DICT_MFDS_INGREDIENT_{item_seq}",
                        "entry_type": "product_ingredient",
                        "match_key": match_key,
                        "canonical_id": ";".join(family_ids),
                        "canonical_value": ";".join(family_names),
                        "evidence_note": "Ingredient token was found on the official NEDrug detail page.",
                    }
                )
            strengths = [value for value in _text(record.get("verified_strength_tokens")).split(";") if value]
            if strengths:
                rows.append(
                    {
                        **common,
                        "dictionary_id": f"DICT_MFDS_STRENGTH_{item_seq}",
                        "entry_type": "product_strength",
                        "match_key": match_key,
                        "canonical_id": f"MFDS_DRUG_STRENGTH::{item_seq}",
                        "canonical_value": ";".join(strengths),
                        "evidence_note": "Strength token was found on the official NEDrug detail page.",
                    }
                )

    dictionary = pd.DataFrame(rows, columns=DICTIONARY_COLUMNS)
    dictionary = dictionary.drop_duplicates("dictionary_id", keep="last").sort_values(
        ["entry_type", "match_key", "dictionary_id"], kind="stable"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dictionary.to_csv(output_path, index=False, encoding="utf-8-sig")
    return dictionary.reset_index(drop=True)


def load_verified_dictionary(path: Path = DEFAULT_DICTIONARY_PATH) -> pd.DataFrame:
    dictionary = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = set(DICTIONARY_COLUMNS) - set(dictionary.columns)
    if missing:
        raise ValueError(f"Evidence dictionary is missing columns: {sorted(missing)}")
    allowed_status = {"verified_official", "verified_multi_source"}
    allowed_tiers = {
        "official_regulator",
        "official_manufacturer",
        "official_public_agency",
    }
    verified = dictionary[
        dictionary["verification_status"].isin(allowed_status)
        & dictionary["source_tier"].isin(allowed_tiers)
    ].copy()
    return verified.reset_index(drop=True)


def load_ingredient_aliases(path: Path = DEFAULT_INGREDIENT_PATH) -> dict[str, str]:
    frame = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    aliases = {}
    for row in frame.to_dict("records"):
        alias = _clean(row.get("korean"))
        canonical = _clean(row.get("english"))
        if not alias or not canonical:
            continue
        if canonical.startswith(("NON_", "PROMO_", "MANUFACTURER_", "APPAREL_")):
            continue
        if canonical in {"LANCET", "WOUND_DRESSING", "CERVICAL_COLLAR"}:
            continue
        aliases[alias] = canonical
    return aliases


class ItemAttributeParser:
    def __init__(
        self,
        ingredient_aliases: dict[str, str] | None = None,
        verified_dictionary: pd.DataFrame | None = None,
    ) -> None:
        self.ingredient_aliases = ingredient_aliases or {}
        aliases = sorted(self.ingredient_aliases, key=lambda value: (-len(value), value))
        self.ingredient_pattern = (
            re.compile("|".join(re.escape(value) for value in aliases), re.IGNORECASE)
            if aliases
            else None
        )
        self.dictionary_by_type: dict[str, dict[str, list[dict[str, str]]]] = {}
        if verified_dictionary is not None:
            for record in verified_dictionary.to_dict("records"):
                entry_type = _text(record.get("entry_type"))
                match_key = _compact(record.get("match_key"))
                self.dictionary_by_type.setdefault(entry_type, {}).setdefault(match_key, []).append(record)

    def _dictionary_records(self, entry_type: str, base_name: str) -> list[dict[str, str]]:
        lookup = self.dictionary_by_type.get(entry_type, {})
        compact = _compact(base_name)
        if compact in lookup:
            return lookup[compact]
        candidates = []
        for key, records in lookup.items():
            if len(key) >= 4 and (compact.startswith(key) or key.startswith(compact)):
                candidates.extend(records)
        return candidates

    def parse(
        self,
        name: object,
        *,
        item_group_id: object = "",
        item_family_id: object = "",
        item_subtype_id: object = "",
        dosage_form: object = "",
        standard_unit: object = "",
        operational_tags: object = "",
        local_item_key: object = "",
    ) -> dict[str, object]:
        text = _clean(name)
        group_id = _text(item_group_id)
        family_id = _text(item_family_id)
        subtype_id = _text(item_subtype_id)
        dosage = _text(dosage_form)
        needle_context = family_id in NEEDLE_FAMILIES or bool(NEEDLE_CONTEXT_PATTERN.search(text))
        medication_context = group_id in MEDICATION_GROUPS
        tokens: list[ParsedToken] = []
        parentheticals = _top_level_parentheticals(text)
        concentrations = []
        concentration_ranges: list[tuple[tuple[int, int], tuple[int, int]]] = []
        for match in CONCENTRATION_PATTERN.finditer(text):
            numerator = _normalize_strength(
                match.group("numerator"), match.group("numerator_unit")
            )
            denominator_unit = match.group("denominator_unit")
            if denominator_unit.lower() in {
                "ml",
                "cc",
                "l",
                "밀리리터",
                "리터",
            }:
                _, _, denominator = _normalize_volume(
                    match.group("denominator"), denominator_unit
                )
            elif denominator_unit in PACK_UNIT_CODES:
                denominator = (
                    f"{_normalize_number(match.group('denominator'))}"
                    f"{PACK_UNIT_CODES[denominator_unit]}"
                )
                _add_token(
                    tokens,
                    ParsedToken(
                        "dose_basis",
                        text[match.start("denominator") : match.end("denominator_unit")],
                        denominator,
                        match.start("denominator"),
                        match.end("denominator_unit"),
                        0.98,
                        "name_literal:concentration_denominator",
                        "active_strength_per_dose_unit_not_package_count",
                    ),
                )
            else:
                denominator = _normalize_strength(
                    match.group("denominator"), denominator_unit
                )
            concentrations.append(f"{numerator}/{denominator}")
            concentration_ranges.append(
                (
                    (match.start("numerator"), match.end("numerator_unit")),
                    (match.start("denominator"), match.end("denominator_unit")),
                )
            )

        gauges = []
        if needle_context:
            for match in GAUGE_PATTERN.finditer(text):
                normalized = f"{match.group('value')}G"
                token = ParsedToken(
                    "needle_gauge",
                    match.group(0),
                    normalized,
                    match.start(),
                    match.end(),
                    1.0,
                    "context_rule:needle_device+verified_unit_dictionary",
                    "needle_outer_diameter_gauge",
                )
                if _add_token(tokens, token):
                    gauges.append(normalized)

        dimensions = []
        for match in DIMENSION_PATTERN.finditer(text):
            normalized = (
                f"{_normalize_number(match.group('first'))}{match.group('first_unit').lower()} x "
                f"{_normalize_number(match.group('second'))}{match.group('second_unit').lower()}"
            )
            token = ParsedToken(
                "dimension",
                match.group(0),
                normalized,
                match.start(),
                match.end(),
                0.98,
                "name_literal",
                "product_dimension",
            )
            if _add_token(tokens, token):
                dimensions.append(normalized)

        needle_lengths = []
        if needle_context:
            for match in NEEDLE_LENGTH_PATTERN.finditer(text):
                value = _normalize_number(match.group("value"))
                unit = match.group("unit").lower()
                unit = "inch" if unit in {'"', "인치"} else unit
                normalized = f"{value}{unit}"
                token = ParsedToken(
                    "needle_length",
                    match.group(0),
                    normalized,
                    match.start(),
                    match.end(),
                    0.96,
                    "context_rule:needle_device",
                    "needle_length",
                )
                if _add_token(tokens, token):
                    needle_lengths.append(normalized)

        capacities = []
        capacity_role = (
            "syringe_capacity"
            if family_id == "DISPOSABLE_SYRINGE"
            else "device_container_capacity"
            if group_id in {"MED_SUPPLY", "WASTE"}
            else "package_volume"
        )
        for match in SPECIAL_PACKAGE_VOLUME_PATTERN.finditer(text):
            value, unit, normalized = _normalize_volume(match.group("value"), match.group("unit"))
            match_role = "syringe_capacity" if family_id == "DISPOSABLE_SYRINGE" else "package_volume"
            token = ParsedToken(
                "capacity",
                match.group(0)[: match.group(0).lower().rfind("/")],
                normalized,
                match.start(),
                match.start() + match.group(0).lower().rfind("/"),
                0.98,
                "name_literal:special_package_volume",
                match_role,
            )
            if _add_token(tokens, token):
                capacities.append((value, unit, normalized, match_role))
        for match in VOLUME_PATTERN.finditer(text):
            value, unit, normalized = _normalize_volume(
                match.group("value"), match.group("unit"), match.group("qualifier") or ""
            )
            match_role = capacity_role
            prefix = text[max(0, match.start() - 2) : match.start()].rstrip()
            if medication_context and prefix.endswith("/"):
                match_role = "concentration_denominator"
            token = ParsedToken(
                "capacity",
                match.group(0),
                normalized,
                match.start(),
                match.end(),
                0.98,
                "name_literal",
                match_role,
            )
            if _add_token(tokens, token):
                capacities.append((value, unit, normalized, match_role))

        strengths = []
        net_weights = []
        for match in STRENGTH_PATTERN.finditer(text):
            if any(_overlaps(match.start(), match.end(), token) for token in tokens):
                continue
            normalized = _normalize_strength(match.group("value"), match.group("unit"))
            unit = match.group("unit").lower()
            is_mass = unit in {"g", "그램", "그람", "mg", "밀리그램", "밀리그람"}
            prefix = text[max(0, match.start() - 2) : match.start()].rstrip()
            suffix = text[match.end() :]
            concentration_position = next(
                (
                    "numerator"
                    if numerator_start <= match.start() and match.end() <= numerator_end
                    else "denominator"
                    if denominator_start <= match.start() and match.end() <= denominator_end
                    else ""
                    for (numerator_start, numerator_end),
                    (denominator_start, denominator_end) in concentration_ranges
                    if (
                        numerator_start <= match.start() and match.end() <= numerator_end
                    )
                    or (
                        denominator_start <= match.start() and match.end() <= denominator_end
                    )
                ),
                "",
            )
            per_package_mass = bool(
                re.match(r"\s*/\s*(?:병|포|관|개|백|팩|통|튜브)\b", suffix)
            )
            trailing_package_mass = bool(
                group_id in {"MED_TOPICAL", "KM_EXTRACT", "KM_HERB"}
                and prefix.endswith("-")
                and re.fullmatch(
                    r"\s*(?:\(\s*\d+\s*(?:EA|개)\s*\))?\s*",
                    suffix,
                    re.IGNORECASE,
                )
            )
            if concentration_position == "denominator":
                token_type = "concentration_denominator"
                role = "concentration_basis_not_active_strength"
            elif medication_context and not (is_mass and (per_package_mass or trailing_package_mass)):
                token_type = "active_strength"
                role = "ingredient_strength_or_concentration_numerator"
                strengths.append(normalized)
            elif is_mass:
                token_type = "net_weight"
                role = "product_or_package_mass"
                net_weights.append(normalized)
            else:
                token_type = "strength_or_concentration"
                role = "non_drug_concentration"
                strengths.append(normalized)
            _add_token(
                tokens,
                ParsedToken(
                    token_type,
                    match.group(0),
                    normalized,
                    match.start(),
                    match.end(),
                    0.94 if medication_context else 0.82,
                    "group_context+name_literal",
                    role,
                ),
            )

        pack_candidates: list[tuple[int, int, str, str, ParsedToken]] = []
        for match in PACK_PATTERN.finditer(text):
            if any(
                denominator_start <= match.start() and match.end() <= denominator_end
                for _, (denominator_start, denominator_end) in concentration_ranges
            ):
                continue
            if any(_overlaps(match.start(), match.end(), token) for token in tokens):
                continue
            raw_unit = match.group("unit")
            unit_key = raw_unit.upper() if raw_unit.lower() in {"ea", "t", "roll"} else raw_unit
            normalized_unit = PACK_UNIT_CODES.get(unit_key, unit_key)
            count = match.group("count")
            prefix = text[max(0, match.start() - 3) : match.start()]
            suffix = text[match.end() :]
            score = 20
            if raw_unit == "개입":
                score += 100
            if "-" in prefix:
                score += 80
            if not suffix.strip(" )]_/.-"):
                score += 60
            if "/" in prefix:
                score -= 15
            token = ParsedToken(
                "pack_count",
                match.group(0),
                f"{count} {normalized_unit}",
                match.start(),
                match.end(),
                0.95 if score >= 80 else 0.76,
                "name_literal:pack_pattern",
                "package_count",
            )
            if _add_token(tokens, token):
                pack_candidates.append((score, match.start(), count, normalized_unit, token))

        for match in SPECIAL_PACKAGE_VOLUME_PATTERN.finditer(text):
            count = match.group("count")
            raw_unit = match.group("pack")
            normalized_unit = PACK_UNIT_CODES.get(raw_unit, raw_unit)
            slash = match.group(0).rfind("/")
            start = match.start() + slash + 1
            token = ParsedToken(
                "pack_count",
                text[start : match.end()],
                f"{count} {normalized_unit}",
                start,
                match.end(),
                0.96,
                "name_literal:special_package_volume",
                "package_count",
            )
            if _add_token(tokens, token):
                pack_candidates.append((120, start, count, normalized_unit, token))

        for match in PER_PACKAGE_MEASURE_PATTERN.finditer(text):
            raw_unit = match.group("pack")
            normalized_unit = PACK_UNIT_CODES.get(raw_unit, raw_unit)
            slash = match.group(0).rfind("/")
            start = match.start() + slash + 1
            token = ParsedToken(
                "pack_count",
                text[start : match.end()],
                f"1 {normalized_unit}",
                start,
                match.end(),
                0.90,
                "name_literal:per_package_measure",
                "inferred_single_package_unit",
            )
            if _add_token(tokens, token):
                pack_candidates.append((110, start, "1", normalized_unit, token))

        pack_candidates.sort(key=lambda value: (value[0], value[1]), reverse=True)
        pack_quantity = pack_candidates[0][2] if pack_candidates else ""
        pack_unit = pack_candidates[0][3] if pack_candidates else ""

        ingredient_ids = []
        ingredient_names = []
        ingredient_sources = []
        ingredient_ranges = []
        if medication_context and self.ingredient_pattern is not None:
            ingredient_matches = list(self.ingredient_pattern.finditer(text))
            has_explicit_parenthetical_ingredient = any(
                (
                    any(
                        start < match.start() and match.end() < end
                        for start, end, _ in parentheticals
                    )
                )
                for match in ingredient_matches
            ) or any(
                bool(INGREDIENT_LIKE_PATTERN.search(_clean(content)))
                and not COMPANY_MARKER_PATTERN.search(_clean(content))
                and not CORPORATE_FORM_PATTERN.search(_clean(content))
                for _, _, content in parentheticals
            )
            for match in ingredient_matches:
                if any(_overlaps(match.start(), match.end(), token) for token in tokens):
                    continue
                raw = match.group(0)
                canonical = self.ingredient_aliases.get(raw)
                if canonical is None:
                    canonical = self.ingredient_aliases.get(next(
                        (key for key in self.ingredient_aliases if key.lower() == raw.lower()),
                        "",
                    ))
                if not canonical:
                    continue
                in_parenthetical = any(
                    start < match.start() and match.end() < end
                    for start, end, _ in parentheticals
                )
                if has_explicit_parenthetical_ingredient and not in_parenthetical:
                    continue
                confidence = 0.93 if in_parenthetical else 0.78
                source = "name_literal_parenthetical" if in_parenthetical else "name_literal_substring"
                token = ParsedToken(
                    "ingredient",
                    raw,
                    canonical,
                    match.start(),
                    match.end(),
                    confidence,
                    source,
                    "active_ingredient_candidate",
                )
                if _add_token(tokens, token):
                    ingredient_ids.append(canonical)
                    ingredient_names.append(raw)
                    ingredient_sources.append(source)
                    ingredient_ranges.append((match.start(), match.end()))

        manufacturer = ""
        manufacturer_source = ""
        manufacturer_confidence = 0.0
        manufacturer_block: tuple[int, int] | None = None
        for index, (start, end, content) in enumerate(parentheticals):
            compact_content = _clean(content)
            if not compact_content or re.search(r"\d", compact_content):
                continue
            if NON_COMPANY_DETAIL_PATTERN.search(compact_content):
                continue
            if any(start < ingredient_start and ingredient_end < end for ingredient_start, ingredient_end in ingredient_ranges):
                continue
            explicit = bool(
                COMPANY_MARKER_PATTERN.search(compact_content)
                or CORPORATE_FORM_PATTERN.search(compact_content)
            )
            if not explicit:
                continue
            candidate = _canonical_company(compact_content)
            if len(candidate) < 2:
                continue
            confidence = 0.92
            if confidence > manufacturer_confidence:
                manufacturer = candidate
                manufacturer_source = "name_literal_company_marker"
                manufacturer_confidence = confidence
                manufacturer_block = (start, end)

        if manufacturer_block:
            start, end = manufacturer_block
            _add_token(
                tokens,
                ParsedToken(
                    "manufacturer",
                    text[start:end],
                    manufacturer,
                    start,
                    end,
                    manufacturer_confidence,
                    manufacturer_source,
                    "manufacturer_or_supplier_candidate",
                ),
            )

        if medication_context:
            for start, end, content in parentheticals:
                if any(
                    start < token.end and token.start < end
                    for token in tokens
                    if token.start >= 0
                ):
                    continue
                cleaned_content = _clean(content).strip("()[]")
                if not cleaned_content or re.search(r"\d", cleaned_content):
                    continue
                if (
                    COMPANY_MARKER_PATTERN.search(cleaned_content)
                    or CORPORATE_FORM_PATTERN.search(cleaned_content)
                    or NON_COMPANY_DETAIL_PATTERN.search(cleaned_content)
                    or ":" in cleaned_content
                ):
                    continue
                following = text[end:].lstrip()
                likely_literal_ingredient = bool(
                    INGREDIENT_LIKE_PATTERN.search(cleaned_content)
                    or following.startswith("_(")
                    or following.startswith("(")
                )
                if not likely_literal_ingredient:
                    continue
                canonical = "UNMAPPED::" + _compact(cleaned_content).upper()
                token = ParsedToken(
                    "ingredient",
                    text[start:end],
                    canonical,
                    start,
                    end,
                    0.76,
                    "name_literal_unmapped",
                    "active_ingredient_requires_canonical_verification",
                )
                if _add_token(tokens, token):
                    ingredient_ids.append(canonical)
                    ingredient_names.append(cleaned_content)
                    ingredient_sources.append("name_literal_unmapped")
                    ingredient_ranges.append((start, end))

        mask_ranges = []
        for token in tokens:
            if token.start < 0:
                continue
            if token.token_type == "ingredient":
                if token.source == "name_literal_substring":
                    continue
                containing = next(
                    ((start, end) for start, end, _ in parentheticals if start < token.start and token.end < end),
                    None,
                )
                if containing:
                    mask_ranges.append(containing)
                else:
                    mask_ranges.append((token.start, token.end))
                continue
            mask_ranges.append((token.start, token.end))
        characters = list(text)
        for start, end in mask_ranges:
            for index in range(max(0, start), min(len(characters), end)):
                characters[index] = " "
        base_name = "".join(characters)
        base_name = re.sub(r"[\s_\-/,:;*×]+", " ", base_name)
        base_name = re.sub(r"[()\[\]{}]+", " ", base_name)
        base_name = " ".join(base_name.split()).strip(" .")
        base_name = re.sub(r"^(?:방문|비급여|급여|홍보|재활)\s*", "", base_name).strip()
        if not base_name:
            base_name = text

        canonical_product_id = ""
        canonical_product_name = ""
        verified_item_family_id = ""
        verified_standard_family_name = ""
        verified_item_group_id = ""
        verified_dictionary_ids = []
        verified_ingredient_dictionary_ids = []
        product_records = self._dictionary_records("product_alias", base_name)
        if product_records:
            record = product_records[0]
            canonical_product_id = _text(record.get("canonical_id"))
            canonical_product_name = _text(record.get("canonical_value"))
            verified_dictionary_ids.append(_text(record.get("dictionary_id")))

        for record in self._dictionary_records("product_ingredient", base_name):
            record_ids = [value for value in _text(record.get("canonical_id")).split(";") if value]
            record_names = [value for value in _text(record.get("canonical_value")).split(";") if value]
            ingredient_ids.extend(record_ids)
            ingredient_names.extend(record_names)
            ingredient_sources.append("verified_dictionary")
            dictionary_id = _text(record.get("dictionary_id"))
            verified_dictionary_ids.append(dictionary_id)
            verified_ingredient_dictionary_ids.append(dictionary_id)

        for record in self._dictionary_records("product_manufacturer", base_name):
            manufacturer = _text(record.get("canonical_value")) or manufacturer
            manufacturer_source = "verified_dictionary"
            manufacturer_confidence = float(record.get("confidence") or 0.98)
            verified_dictionary_ids.append(_text(record.get("dictionary_id")))

        for record in self._dictionary_records("product_family", base_name):
            verified_item_family_id = _text(record.get("canonical_id"))
            verified_standard_family_name = _text(record.get("canonical_value"))
            verified_item_group_id = _text(record.get("applicable_context"))
            verified_dictionary_ids.append(_text(record.get("dictionary_id")))

        ingredient_ids = list(dict.fromkeys(value for value in ingredient_ids if value))
        ingredient_names = list(dict.fromkeys(value for value in ingredient_names if value))
        verified_dictionary_ids = list(dict.fromkeys(value for value in verified_dictionary_ids if value))

        unresolved = []
        qualifiers = []
        recognized_tokens = [token for token in tokens if token.start >= 0]
        for start, end, content in parentheticals:
            if any(start < token.end and token.start < end for token in recognized_tokens):
                continue
            cleaned_content = _clean(content).strip("()[]")
            if not cleaned_content:
                continue
            if OPERATIONAL_PATTERN.search(cleaned_content) or NON_COMPANY_DETAIL_PATTERN.search(cleaned_content):
                qualifiers.append(cleaned_content)
            else:
                unresolved.append(cleaned_content)

        model_tokens = []
        for match in MODEL_PATTERN.finditer(text):
            if any(_overlaps(match.start(), match.end(), token) for token in recognized_tokens):
                continue
            model_tokens.append(match.group(0))

        capacity = ("", "", "", "")
        if capacities:
            capacity = next(
                (
                    candidate
                    for candidate in reversed(capacities)
                    if candidate[3] != "concentration_denominator"
                ),
                capacity,
            )
        gauge = gauges[0] if gauges else ""
        needle_length = needle_lengths[0] if needle_lengths else ""
        dimension = dimensions[0] if dimensions else ""
        inventory_unit = _text(standard_unit) or pack_unit or DOSAGE_FORM_UNITS.get(dosage, "")
        if not inventory_unit and family_id in NEEDLE_FAMILIES:
            inventory_unit = "EA"

        external_reasons = []
        resolved_family_id = verified_item_family_id or family_id
        resolved_group_id = verified_item_group_id or group_id
        if not resolved_family_id and not medication_context:
            external_reasons.append("missing_item_family")
        if resolved_group_id == "UNCLASSIFIED" or not resolved_group_id:
            external_reasons.append("unclassified_item_group")
        if medication_context and not ingredient_ids:
            external_reasons.append("medication_ingredient_unresolved")
        if any(value.startswith("UNMAPPED::") for value in ingredient_ids):
            external_reasons.append("ingredient_requires_canonical_verification")
        elif (
            medication_context
            and ingredient_ids
            and not verified_ingredient_dictionary_ids
            and any(source.startswith("name_literal_") for source in ingredient_sources)
        ):
            external_reasons.append("ingredient_mapping_requires_external_verification")
        if manufacturer_source == "name_position_candidate":
            external_reasons.append("manufacturer_requires_verification")
        if unresolved:
            external_reasons.append("unresolved_parenthetical_tokens")
        if model_tokens:
            external_reasons.append("model_token_requires_catalog_match")

        confidence = 0.45
        confidence += 0.10 if base_name else 0
        confidence += 0.10 if family_id else 0
        confidence += 0.10 if tokens else 0
        confidence += 0.15 if verified_dictionary_ids else 0
        confidence -= min(0.20, len(unresolved) * 0.05)
        confidence = round(max(0.0, min(0.99, confidence)), 3)
        if verified_dictionary_ids and not unresolved:
            parse_status = "verified_external_dictionary"
        elif external_reasons:
            parse_status = "needs_external_match"
        else:
            parse_status = "parsed_candidate"

        exact_variant_parts = [
            resolved_family_id or resolved_group_id or "UNCLASSIFIED",
            subtype_id or dosage or "UNSPECIFIED_SUBTYPE",
            capacity[2],
            gauge,
            needle_length,
            dimension,
            pack_quantity,
            pack_unit,
            inventory_unit,
        ]
        material_parts = [
            resolved_family_id or resolved_group_id or "UNCLASSIFIED",
            subtype_id,
            ";".join(ingredient_ids),
            capacity[2],
            gauge,
            needle_length,
            dimension,
        ]
        forecast_series_key = _text(local_item_key)
        product_variant_key = "|".join(value or "-" for value in exact_variant_parts)
        material_match_key = "|".join(value or "-" for value in material_parts)
        material_readiness = (
            "verified_ingredient_ready"
            if (
                verified_ingredient_dictionary_ids
                and ingredient_ids
                and not any(value.startswith("UNMAPPED::") for value in ingredient_ids)
            )
            else "ingredient_candidate_review"
            if ingredient_ids
            else "family_only"
            if resolved_family_id
            else "needs_external_match"
        )

        tokens.sort(key=lambda token: (token.start if token.start >= 0 else 10**9, token.end, token.token_type))
        return {
            "base_item_name_candidate": base_name,
            "canonical_product_id": canonical_product_id,
            "canonical_product_name": canonical_product_name,
            "verified_item_group_id": verified_item_group_id,
            "verified_item_family_id": verified_item_family_id,
            "verified_standard_family_name": verified_standard_family_name,
            "manufacturer_candidate": manufacturer,
            "manufacturer_source": manufacturer_source,
            "manufacturer_confidence": round(manufacturer_confidence, 3),
            "ingredient_ids": ";".join(ingredient_ids),
            "ingredient_names": ";".join(ingredient_names),
            "ingredient_source": ";".join(dict.fromkeys(ingredient_sources)),
            "dosage_form": dosage,
            "capacity_value": capacity[0],
            "capacity_unit": capacity[1],
            "capacity_normalized": capacity[2],
            "capacity_role": capacity[3] if capacity[2] else "",
            "all_capacities": ";".join(dict.fromkeys(value[2] for value in capacities)),
            "active_strengths": ";".join(dict.fromkeys(strengths)),
            "concentrations": ";".join(dict.fromkeys(concentrations)),
            "net_weight": ";".join(dict.fromkeys(net_weights)),
            "needle_gauge": gauge,
            "needle_gauge_value": gauge[:-1] if gauge else "",
            "needle_length": needle_length,
            "dimensions": ";".join(dict.fromkeys(dimensions)),
            "pack_quantity": pack_quantity,
            "pack_unit": pack_unit,
            "inventory_unit": inventory_unit,
            "model_tokens": ";".join(dict.fromkeys(model_tokens)),
            "operational_tokens": ";".join(
                dict.fromkeys(value for value in [_text(operational_tags), *qualifiers] if value)
            ),
            "unresolved_tokens": ";".join(dict.fromkeys(unresolved)),
            "parsed_tokens_json": json.dumps(
                [asdict(token) for token in tokens], ensure_ascii=False, separators=(",", ":")
            ),
            "forecast_series_key": forecast_series_key,
            "normalized_inventory_key": product_variant_key,
            "material_match_key": material_match_key,
            "material_match_readiness": material_readiness,
            "attribute_confidence": confidence,
            "attribute_parse_status": parse_status,
            "external_match_needed_reasons": ";".join(dict.fromkeys(external_reasons)),
            "verified_dictionary_ids": ";".join(verified_dictionary_ids),
            "attribute_parser_version": ATTRIBUTE_PARSER_VERSION,
        }


def _write_alias_attributes(
    aliases: pd.DataFrame,
    parser: ItemAttributeParser,
    output_path: Path,
    limit: int | None = None,
    chunk_size: int = 25_000,
) -> tuple[int, dict[str, int]]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    if limit is not None:
        aliases = aliases.head(limit)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    writer = None
    status_counts: dict[str, int] = {}
    rows_written = 0
    identity_columns = [
        "institution_id",
        "local_item_code",
        "local_item_key",
        "raw_item_name",
        "product_name_candidate",
        "item_group_id_candidate",
        "item_family_id_candidate",
        "item_subtype_id_candidate",
        "occurrence_count",
        "usage_sum",
    ]
    try:
        for start in range(0, len(aliases), chunk_size):
            chunk = aliases.iloc[start : start + chunk_size]
            records = []
            for row in chunk.itertuples(index=False):
                values = row._asdict()
                parsed = parser.parse(
                    values.get("product_name_candidate") or values.get("raw_item_name"),
                    item_group_id=values.get("item_group_id_candidate"),
                    item_family_id=values.get("item_family_id_candidate"),
                    item_subtype_id=values.get("item_subtype_id_candidate"),
                    dosage_form=values.get("dosage_form_candidate"),
                    standard_unit=values.get("standard_unit_candidate"),
                    operational_tags=values.get("operational_tags"),
                    local_item_key=values.get("local_item_key"),
                )
                record = {column: values.get(column, "") for column in identity_columns}
                record.update(parsed)
                records.append(record)
                status = parsed["attribute_parse_status"]
                status_counts[status] = status_counts.get(status, 0) + 1
            table = pa.Table.from_pylist(records)
            if writer is None:
                writer = pq.ParquetWriter(temporary_path, table.schema, compression="zstd")
            writer.write_table(table)
            rows_written += len(records)
    finally:
        if writer is not None:
            writer.close()
    if rows_written == 0:
        raise ValueError("No alias attributes were generated")
    temporary_path.replace(output_path)
    return rows_written, dict(sorted(status_counts.items()))


def _build_representative_attributes(
    worklist: pd.DataFrame,
    parser: ItemAttributeParser,
    output_path: Path,
    limit: int | None = None,
) -> pd.DataFrame:
    if limit is not None:
        worklist = worklist.head(limit)
    records = []
    identity_columns = [
        "representative_item_id",
        "representative_name",
        "item_group_id_candidate",
        "item_family_id_candidate",
        "item_subtype_id_candidate",
        "occurrence_count",
        "usage_sum",
        "institution_count",
        "raw_name_examples",
        "local_code_examples",
    ]
    for row in worklist.itertuples(index=False):
        values = row._asdict()
        parsed = parser.parse(
            values.get("representative_name"),
            item_group_id=values.get("item_group_id_candidate"),
            item_family_id=values.get("item_family_id_candidate"),
            item_subtype_id=values.get("item_subtype_id_candidate"),
            dosage_form=values.get("dosage_form_candidate"),
            standard_unit=values.get("standard_unit_candidate"),
        )
        record = {column: values.get(column, "") for column in identity_columns}
        record.update(parsed)
        records.append(record)
    frame = pd.DataFrame.from_records(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output_path, index=False, compression="zstd")
    return frame


def _source_tier(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if domain in OFFICIAL_REGULATOR_DOMAINS:
        return "official_regulator"
    if domain in OFFICIAL_MANUFACTURER_DOMAINS:
        return "official_manufacturer"
    if domain in OFFICIAL_PUBLIC_AGENCY_DOMAINS:
        return "official_public_agency"
    return "secondary_reference" if domain else "not_searched"


def _external_candidates(
    representatives: pd.DataFrame,
    suggestions: pd.DataFrame,
    dictionary: pd.DataFrame,
    official_nedrug: pd.DataFrame,
    output_path: Path,
) -> pd.DataFrame:
    suggestion_columns = [
        "representative_item_id",
        "item_family_id_suggested",
        "standard_family_name_suggested",
        "family_basis",
        "evidence_note",
    ]
    available = [column for column in suggestion_columns if column in suggestions.columns]
    merged = representatives.merge(
        suggestions[available], on="representative_item_id", how="left", validate="one_to_one"
    )
    official_ids = set(official_nedrug.get("item_seq", pd.Series(dtype=str)).astype(str))
    verified_urls = set(dictionary.loc[
        dictionary["verification_status"].isin({"verified_official", "verified_multi_source"}),
        "source_url",
    ].astype(str))
    records = []
    for row in merged.to_dict("records"):
        if _text(row.get("attribute_parse_status")) != "needs_external_match":
            continue
        evidence_note = _text(row.get("evidence_note"))
        url_match = URL_PATTERN.search(evidence_note)
        url = url_match.group(0).rstrip(".,)") if url_match else ""
        tier = _source_tier(url)
        item_seq_match = NEDRUG_ITEM_SEQ_PATTERN.search(url)
        is_cached_official = bool(item_seq_match and item_seq_match.group(1) in official_ids)
        if url in verified_urls or is_cached_official:
            verification_status = "verified_official"
            candidate_status = "verified_dictionary_ready"
            confidence = 0.98
        elif url:
            verification_status = "candidate_unverified"
            candidate_status = "web_candidate_needs_review"
            confidence = 0.65 if tier.startswith("official_") else 0.45
        else:
            verification_status = "not_searched"
            candidate_status = "search_required"
            confidence = 0.0
        representative_name = _text(row.get("representative_name"))
        records.append(
            {
                "representative_item_id": _text(row.get("representative_item_id")),
                "representative_name": representative_name,
                "base_item_name_candidate": _text(row.get("base_item_name_candidate")),
                "item_group_id_candidate": _text(row.get("item_group_id_candidate")),
                "item_family_id_candidate": _text(row.get("item_family_id_candidate")),
                "ingredient_ids_candidate": _text(row.get("ingredient_ids")),
                "manufacturer_candidate": _text(row.get("manufacturer_candidate")),
                "capacity_candidate": _text(row.get("capacity_normalized")),
                "needle_gauge_candidate": _text(row.get("needle_gauge")),
                "pack_quantity_candidate": _text(row.get("pack_quantity")),
                "pack_unit_candidate": _text(row.get("pack_unit")),
                "unresolved_tokens": _text(row.get("unresolved_tokens")),
                "external_match_needed_reasons": _text(row.get("external_match_needed_reasons")),
                "search_query": f'"{representative_name}" 식약처 제조사 성분 규격',
                "suggested_family_id": _text(row.get("item_family_id_suggested")),
                "suggested_family_name": _text(row.get("standard_family_name_suggested")),
                "candidate_basis": _text(row.get("family_basis")),
                "evidence_note": evidence_note,
                "evidence_url": url,
                "source_domain": urlparse(url).netloc.lower(),
                "source_tier": tier,
                "verification_status": verification_status,
                "candidate_status": candidate_status,
                "candidate_confidence": confidence,
                "occurrence_count": row.get("occurrence_count", 0),
                "usage_sum": row.get("usage_sum", 0.0),
                "review_status": "needs_review" if candidate_status != "verified_dictionary_ready" else "dictionary_ready",
                "attribute_parser_version": ATTRIBUTE_PARSER_VERSION,
            }
        )
    frame = pd.DataFrame.from_records(records)
    if not frame.empty:
        frame = frame.sort_values(
            ["candidate_status", "usage_sum", "occurrence_count"],
            ascending=[True, False, False],
            kind="stable",
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False, encoding="utf-8-sig")
    return frame.reset_index(drop=True)


def _review_sample(representatives: pd.DataFrame, output_path: Path, sample_size: int) -> pd.DataFrame:
    frame = representatives.copy()
    frame["sample_has_gauge"] = frame["needle_gauge"].astype(str).ne("")
    frame["sample_has_pack"] = frame["pack_quantity"].astype(str).ne("")
    frame["sample_needs_external"] = frame["attribute_parse_status"].eq("needs_external_match")
    frame["sample_has_unresolved"] = frame["unresolved_tokens"].astype(str).ne("")
    buckets = [
        frame[frame["sample_has_gauge"]].sort_values("usage_sum", ascending=False).head(250),
        frame[frame["sample_has_unresolved"]].sort_values("usage_sum", ascending=False).head(250),
        frame[frame["sample_needs_external"]].sort_values("usage_sum", ascending=False).head(500),
        frame[frame["sample_has_pack"]].sort_values("usage_sum", ascending=False).head(250),
        frame.sort_values("usage_sum", ascending=False),
    ]
    sample = pd.concat(buckets, ignore_index=True).drop_duplicates("representative_item_id").head(sample_size)
    output_columns = [
        "representative_item_id",
        "representative_name",
        "base_item_name_candidate",
        "manufacturer_candidate",
        "ingredient_names",
        "ingredient_ids",
        "dosage_form",
        "capacity_normalized",
        "capacity_role",
        "all_capacities",
        "active_strengths",
        "concentrations",
        "net_weight",
        "needle_gauge",
        "needle_length",
        "dimensions",
        "pack_quantity",
        "pack_unit",
        "inventory_unit",
        "unresolved_tokens",
        "normalized_inventory_key",
        "material_match_key",
        "material_match_readiness",
        "attribute_parse_status",
        "external_match_needed_reasons",
        "verified_dictionary_ids",
        "occurrence_count",
        "usage_sum",
        "raw_name_examples",
        "parsed_tokens_json",
    ]
    sample = sample[[column for column in output_columns if column in sample.columns]]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sample.to_csv(output_path, index=False, encoding="utf-8-sig")
    return sample


def run_attribute_pipeline(
    alias_path: Path = DEFAULT_ALIAS_PATH,
    worklist_path: Path = DEFAULT_WORKLIST_PATH,
    suggestion_path: Path = DEFAULT_SUGGESTION_PATH,
    official_nedrug_path: Path = DEFAULT_OFFICIAL_NEDRUG_PATH,
    ingredient_path: Path = DEFAULT_INGREDIENT_PATH,
    dictionary_path: Path = DEFAULT_DICTIONARY_PATH,
    alias_output_path: Path = DEFAULT_ALIAS_OUTPUT_PATH,
    representative_output_path: Path = DEFAULT_REPRESENTATIVE_OUTPUT_PATH,
    external_candidate_path: Path = DEFAULT_EXTERNAL_CANDIDATE_PATH,
    sample_path: Path = DEFAULT_SAMPLE_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    sample_size: int = 1000,
    limit: int | None = None,
) -> dict[str, object]:
    dictionary = build_verified_evidence_dictionary(official_nedrug_path, dictionary_path)
    verified_dictionary = load_verified_dictionary(dictionary_path)
    parser = ItemAttributeParser(load_ingredient_aliases(ingredient_path), verified_dictionary)
    aliases = pd.read_parquet(alias_path)
    worklist = pd.read_parquet(worklist_path)
    suggestions = pd.read_csv(suggestion_path, dtype=str, keep_default_na=False)
    official_nedrug = (
        pd.read_parquet(official_nedrug_path)
        if official_nedrug_path.exists()
        else pd.DataFrame()
    )
    alias_rows, alias_status_counts = _write_alias_attributes(
        aliases, parser, alias_output_path, limit=limit
    )
    representatives = _build_representative_attributes(
        worklist, parser, representative_output_path, limit=limit
    )
    external = _external_candidates(
        representatives, suggestions, dictionary, official_nedrug, external_candidate_path
    )
    sample = _review_sample(representatives, sample_path, min(sample_size, len(representatives)))

    def has_overlapping_token_types(value: object, left: str, right: str) -> bool:
        parsed = json.loads(_text(value) or "[]")
        left_tokens = [token for token in parsed if token["token_type"] == left]
        right_tokens = [token for token in parsed if token["token_type"] == right]
        return any(
            left_token["start"] < right_token["end"]
            and right_token["start"] < left_token["end"]
            for left_token in left_tokens
            for right_token in right_tokens
        )

    gauge_strength_conflicts = int(
        representatives["parsed_tokens_json"]
        .map(
            lambda value: has_overlapping_token_types(
                value, "needle_gauge", "active_strength"
            )
        )
        .sum()
    )
    dose_basis_pack_conflicts = int(
        representatives["parsed_tokens_json"]
        .map(lambda value: has_overlapping_token_types(value, "dose_basis", "pack_count"))
        .sum()
    )
    expected_alias_rows = min(len(aliases), limit) if limit is not None else len(aliases)
    expected_representative_rows = (
        min(len(worklist), limit) if limit is not None else len(worklist)
    )
    quality_gates = {
        "alias_rows_preserved": alias_rows == expected_alias_rows,
        "representative_rows_preserved": len(representatives) == expected_representative_rows,
        "representative_ids_unique": not representatives["representative_item_id"].duplicated().any(),
        "external_candidate_ids_unique": not external.get(
            "representative_item_id", pd.Series(dtype=str)
        ).duplicated().any(),
        "external_queue_covers_all_needs_external": len(external)
        == int(representatives["attribute_parse_status"].eq("needs_external_match").sum()),
        "review_sample_ids_unique": not sample["representative_item_id"].duplicated().any(),
        "gauge_strength_positions_disjoint": gauge_strength_conflicts == 0,
        "dose_basis_pack_positions_disjoint": dose_basis_pack_conflicts == 0,
        "dictionary_loader_excludes_untrusted_rows": set(
            verified_dictionary["verification_status"]
        ).issubset({"verified_official", "verified_multi_source"})
        and set(verified_dictionary["source_tier"]).issubset(
            {"official_regulator", "official_manufacturer", "official_public_agency"}
        ),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "attribute_parser_version": ATTRIBUTE_PARSER_VERSION,
        "dictionary_version": DICTIONARY_VERSION,
        "source_alias_rows": int(len(aliases)),
        "alias_rows_written": alias_rows,
        "representative_rows_written": int(len(representatives)),
        "dictionary_rows": int(len(dictionary)),
        "verified_dictionary_rows_loaded": int(len(verified_dictionary)),
        "external_candidate_rows": int(len(external)),
        "external_candidate_status_counts": external.get(
            "candidate_status", pd.Series(dtype=str)
        ).value_counts().to_dict(),
        "alias_parse_status_counts": alias_status_counts,
        "representative_parse_status_counts": representatives[
            "attribute_parse_status"
        ].value_counts().to_dict(),
        "representatives_with_gauge": int(representatives["needle_gauge"].astype(str).ne("").sum()),
        "representatives_with_capacity": int(
            representatives["capacity_normalized"].astype(str).ne("").sum()
        ),
        "representatives_with_pack_count": int(
            representatives["pack_quantity"].astype(str).ne("").sum()
        ),
        "representatives_with_ingredient": int(
            representatives["ingredient_ids"].astype(str).ne("").sum()
        ),
        "representatives_with_concentration": int(
            representatives["concentrations"].astype(str).ne("").sum()
        ),
        "material_match_readiness_counts": representatives[
            "material_match_readiness"
        ].value_counts().to_dict(),
        "gauge_strength_conflicts": gauge_strength_conflicts,
        "dose_basis_pack_conflicts": dose_basis_pack_conflicts,
        "quality_gates": quality_gates,
        "sample_rows": int(len(sample)),
        "outputs": {
            "dictionary": str(dictionary_path),
            "alias_attributes": str(alias_output_path),
            "representative_attributes": str(representative_output_path),
            "external_candidates": str(external_candidate_path),
            "review_sample": str(sample_path),
        },
    }
    failed_quality_gates = [name for name, passed in quality_gates.items() if not passed]
    if failed_quality_gates:
        raise ValueError(f"Attribute quality gates failed: {failed_quality_gates}")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse raw_stock item names into structured attributes")
    parser.add_argument("--alias-path", type=Path, default=DEFAULT_ALIAS_PATH)
    parser.add_argument("--worklist-path", type=Path, default=DEFAULT_WORKLIST_PATH)
    parser.add_argument("--suggestion-path", type=Path, default=DEFAULT_SUGGESTION_PATH)
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    report = run_attribute_pipeline(
        alias_path=args.alias_path,
        worklist_path=args.worklist_path,
        suggestion_path=args.suggestion_path,
        sample_size=args.sample_size,
        limit=args.limit,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
