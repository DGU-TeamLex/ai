#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
item_grouping_review_sample_1000.csv 의 대표품목 1,000건에 대해
item_family_id / standard_family_name '후보'를 제안한다. (v2 — 괄호 패턴 확장 + 사전 대폭 보강)

이 스크립트가 만드는 값은 전부 미검증 후보다. ITEM_STANDARDIZATION_TEAM_GUIDE.md
규칙에 따라 verified_* 필드나 canonical_item_id 는 절대 채우지 않고, 별도 파일로만
출력한다. family_basis 컬럼에 근거 종류를 명시해서 검토자가 신뢰도를 바로 판단할
수 있게 한다.
"""
import csv
import os
import re
import sys

# 보조 데이터(brand_dict_extra.tsv, ingredient_ko_en.tsv)가 있는 폴더.
# 환경변수 PIPE_DATA_DIR 로 재정의 가능하며 기본값은 파이프라인의 data 폴더다.
PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("PIPE_DATA_DIR", os.path.join(PIPELINE_DIR, "data"))

if len(sys.argv) != 4:
    raise SystemExit(
        "usage: build_family_candidates.py <input.csv> <output.csv> <unresolved_queue.csv>"
    )

IN_FILE = sys.argv[1]
OUT_FILE = sys.argv[2]
QUEUE_FILE = sys.argv[3]
TODAY = "2026-07-18"
SEARCHED_TODAY = "web_search_2026_07_15"
GENERAL = "general_knowledge_unverified"
PIPELINE_VERSION = "combined-family-v2.1"

# ---------------------------------------------------------------------------
# 1) 상품명에 이미 성분명이 괄호로 명시된 구조 파싱
#    "브랜드명(성분명)_(함량)(제조사)" 또는 "브랜드명(성분명)(제조사)" 둘 다 인정.
#    첫 괄호 내용이 순한글(숫자 없음, 2~30자)이고 바로 뒤(또는 밑줄 뒤)에 괄호가
#    하나 더 이어질 때만 성분으로 인정 — "브랜드(단일 제조사)-1정"처럼 괄호가
#    하나뿐인 경우는 대상에서 제외한다(그 괄호는 대개 제조사).
# ---------------------------------------------------------------------------
PAREN_INGREDIENT_RE = re.compile(r'^[^()]+\(([가-힣·,/\s]{2,30})\)_?\(')


def extract_literal_ingredient(name: str):
    m = PAREN_INGREDIENT_RE.match(name)
    if m:
        return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# 0) 물품분류 기준표.xlsx(공식 조사양식) 8개 품목 — 최우선 판정.
#    이 표에 정의된 세부유형(규격)을 그대로 쓴다. 표 범위 밖 규격이면 그 사실을
#    명시해서 검토자가 알 수 있게 한다.
# ---------------------------------------------------------------------------
OFFICIAL_TABLE_BASIS = "official_standard_table"
OFFICIAL_TABLE_SOURCE = "물품분류 기준표.xlsx(사용자 제공 공식 조사양식)"


def official_standard_classify(name: str):
    lname = name.lower().replace(" ", "")

    # 1) 주사기(사용량기준): 3cc/5cc/10cc — 주차침 규격은 구분하지 않는다(기준표 명시)
    is_syringe = "주사기" in name or "syringe" in lname
    is_needle_or_catheter_set = any(
        token in lname
        for token in [
            "카테터",
            "catheter",
            "나비바늘",
            "scalpvein",
            "주사기바늘",
            "주사기침",
        ]
    )
    if is_syringe and not is_needle_or_catheter_set:
        for token, label in [("10cc", "10cc"), ("10ml", "10cc"), ("5cc", "5cc"),
                              ("5ml", "5cc"), ("3cc", "3cc"), ("3ml", "3cc")]:
            if token in lname:
                return ("DISPOSABLE_SYRINGE", f"주사기(사용량기준 {label})", OFFICIAL_TABLE_BASIS,
                        f"{OFFICIAL_TABLE_SOURCE} — 주사기는 cc별로만 구분(주사침 규격 무관)")
        return ("DISPOSABLE_SYRINGE", "주사기(규격 미상 — 기준표 3/5/10cc 범위 확인 필요)", OFFICIAL_TABLE_BASIS,
                f"{OFFICIAL_TABLE_SOURCE} — 표준 조사 규격(3/5/10cc) 밖이거나 규격 미표기")

    # 2) 의료폐기물 전용용기: 합성수지형(니들박스, PP) vs 봉투형(PE)
    if "폐기물" in name and ("용기" in name or "박스" in name or "L" in name.upper()):
        if "전자태그" in name or "rfid" in lname:
            return ("MEDICAL_WASTE_RFID_TAG", "의료폐기물 전자태그(용기 아님)", "functional_keyword",
                     "물리적 용기가 아니라 전자식별표라 기준표 대상 외로 별도 분류")
        is_synthetic = any(k in lname for k in ["pvc", "니들", "합성수지"])
        is_bag = any(k in lname for k in ["pe", "봉투"])
        m = re.search(r"(\d+)\s*l", lname)
        vol = int(m.group(1)) if m else None
        if is_synthetic:
            std_vol = "1L" if vol == 1 else "2L" if vol == 2 else f"{vol}L(표준 규격 1L/2L 밖)" if vol else "용량 미상"
            return ("MEDICAL_WASTE_SUPPLY", f"의료폐기물 처리용품(합성수지형 용기·니들박스, {std_vol})", OFFICIAL_TABLE_BASIS,
                     f"{OFFICIAL_TABLE_SOURCE} — 합성수지형(PP, 니들박스) 표준 규격 1L/2L")
        if is_bag:
            std_vol = "4L" if vol == 4 else "5L" if vol == 5 else f"{vol}L(표준 규격 4L/5L 밖)" if vol else "용량 미상"
            return ("MEDICAL_WASTE_SUPPLY", f"의료폐기물 처리용품(봉투형 용기 PE, {std_vol})", OFFICIAL_TABLE_BASIS,
                     f"{OFFICIAL_TABLE_SOURCE} — 봉투형(PE) 표준 규격 4L/5L")
        return ("MEDICAL_WASTE_SUPPLY", "의료폐기물 처리용품(형태 미상 — 합성수지형/봉투형 확인 필요)", OFFICIAL_TABLE_BASIS,
                 f"{OFFICIAL_TABLE_SOURCE} — 재질(합성수지형/봉투형PE) 확인 안 됨, 30L 등은 골판지류일 가능성(기준표 조사대상 아님)")

    # 3) 멸균포장재(EO가스 소독용): 15cm*200M / 20cm*200M, Roll 단위
    is_eo_packaging = bool(
        "멸균포장재" in name
        or re.search(r"(?:\beo(?:\s*gas)?\b|이오\s*가스).{0,8}(?:포장|롤|roll|파우치)", name, re.I)
        or re.search(r"멸균(?:용)?(?:포장)?(?:롤|파우치|포장지|포장재)", name, re.I)
    )
    is_packaging_equipment = bool(re.search(r"접착기|실링기|sealer", name, re.I))
    if is_eo_packaging and not is_packaging_equipment:
        size = "15cm*200M" if "15" in name else "20cm*200M" if "20" in name else "규격 미상"
        return ("STERILIZATION_PACKAGING_EO", f"멸균포장재(EO가스 소독용, {size})", OFFICIAL_TABLE_BASIS,
                 f"{OFFICIAL_TABLE_SOURCE} — 15*200m/20*200m 기준, Roll 단위")

    # 4) 주사액/생리식염수 등 수액제통: 500cc 이하 / 1000cc — 성분 구분 없이 전체 백형태
    if any(k in name for k in ["수액제통"]) or ("생리식염수" in name and ("500" in name or "1000" in name)):
        vol = "1000cc" if "1000" in name else "500cc 이하" if "500" in name else "용량 미상"
        return ("IV_FLUID_CONTAINER", f"주사액·생리식염수 등 수액제통({vol})", OFFICIAL_TABLE_BASIS,
                 f"{OFFICIAL_TABLE_SOURCE} — 성분 구분 없이 백형태 수액제 전체")

    # 5) 수액세트(유량조절기/필터 포함)
    if "수액세트" in name:
        return ("IV_ADMIN_SET", "수액세트(투여기구)", OFFICIAL_TABLE_BASIS,
                 f"{OFFICIAL_TABLE_SOURCE} — 유량조절기·필터형 및 일반 수액세트 포함")

    # 6) 혈액투석제통: 5L 이하 / 5L 초과
    if "혈액투석" in name or "투석액" in name:
        m = re.search(r"(\d+)\s*l", lname)
        vol = int(m.group(1)) if m else None
        cat = "5L 이하" if (vol is not None and vol <= 5) else "5L 초과" if vol else "용량 미상"
        return ("DIALYSIS_FLUID_CONTAINER", f"혈액투석제통({cat})", OFFICIAL_TABLE_BASIS,
                 f"{OFFICIAL_TABLE_SOURCE} — 5L 이하/초과로 구분")

    # 7) 카테터(angio needle): 일반 Foley/흡인/중심정맥 카테터는 제외한다.
    # 사용자 기준표의 angio needle과 G 규격이 명시된 말초혈관 카테터만 대상으로 한다.
    is_angio_catheter = bool(
        re.search(r"angio|안지오|엔지오|angiocath", lname, re.I)
        or (
            ("카테터" in name or "catheter" in lname)
            and re.search(r"\d{1,2}\s*g", lname, re.I)
        )
    )
    if is_angio_catheter:
        if "나비" in name:
            sub = "나비 바늘"
        else:
            m = re.search(r"(\d+)\s*g", lname)
            gauge = int(m.group(1)) if m else None
            sub = "22G(성인용)" if (gauge is not None and gauge <= 22) else "24G(소아용)" if (gauge is not None and gauge >= 24) else "규격 미상"
        return ("IV_CATHETER_ANGIONEEDLE", f"카테터(angio needle, {sub})", OFFICIAL_TABLE_BASIS,
                 f"{OFFICIAL_TABLE_SOURCE} — 말초혈관 삽입용, 성인용(22G이하)/소아용(24G이상)/나비바늘")

    # 8) Urine bag (일반용 + Hourly urine bag 포함)
    if "urine" in lname or "유린백" in name or "소변주머니" in name or "유치도뇨" in name:
        return ("URINE_BAG", "Urine bag(소변주머니)", OFFICIAL_TABLE_BASIS,
                 f"{OFFICIAL_TABLE_SOURCE} — 일반용·Hourly urine bag 모두 포함")

    return None


# ---------------------------------------------------------------------------
# 0-1) 제형(약명칭 접미사) 판정 — 성분을 몰라도 최소한 제형은 상품명에서 뽑아낼 수 있다.
#    family 를 대체하지 않고 별도 컬럼(dosage_form_suggested)으로 붙인다.
# ---------------------------------------------------------------------------
DOSAGE_FORM_SUFFIX = [
    ("TABLET", "정제", ["정)", "정(", "정1", "정2", "정3", "정4", "정5", "정경", "정 ", "정-"]),
    ("CAPSULE", "캡슐/캅셀", ["캡슐", "캅셀"]),
    ("SYRUP", "시럽", ["시럽"]),
    ("SUSPENSION", "현탁액", ["현탁"]),
    ("GEL", "겔/젤", ["겔", "젤"]),
    ("CREAM", "크림", ["크림"]),
    ("OINTMENT", "연고", ["연고"]),
    ("PATCH", "패치/파스/플라스타", ["패치", "파스", "플라스타", "카타플라즈마"]),
    ("SUPPOSITORY", "좌제", ["좌제"]),
    ("GRANULE", "과립", ["과립"]),
    ("POWDER", "산제/분말", ["산제", "분말"]),
    ("SPRAY", "스프레이/분무", ["스프레이", "분무"]),
    ("INHALATION", "흡입제", ["흡입액", "흡입기"]),
    ("SOLUTION_LIQUID", "액제", ["액)", "액(", "액-", "액 ", "액/"]),
]


def dosage_form_for(name: str):
    for fid, disp, subs in DOSAGE_FORM_SUFFIX:
        for s in subs:
            if s in name:
                return (fid, disp)
    if name.rstrip().endswith("정"):
        return ("TABLET", "정제")
    if name.rstrip().endswith("액"):
        return ("SOLUTION_LIQUID", "액제")
    return ("", "")


# ---------------------------------------------------------------------------
# 2) 상품명에 성분명이 직접 부분문자열로 포함된 경우 (일반명 브랜드)
# ---------------------------------------------------------------------------
SUBSTRING_INGREDIENTS = [
    ("ACETAMINOPHEN", "아세트아미노펜", ["아세트아미노펜"]),
    ("ASPIRIN", "아스피린", ["아스피린"]),
    ("IBUPROFEN", "이부프로펜", ["이부프로펜"]),
    ("NAPROXEN", "나프록센", ["낙센", "나프록센"]),
    ("KETOPROFEN", "케토프로펜", ["케토프로펜"]),
    ("PIROXICAM", "피록시캄", ["피록시캄", "피로시캄"]),
    ("DICLOFENAC", "디클로페낙", ["디클로페낙"]),
    ("CHLORHEXIDINE", "클로르헥시딘", ["클로르헥시딘"]),
    ("KETOCONAZOLE", "케토코나졸", ["케토코나졸"]),
    ("LIDOCAINE", "리도카인", ["리도카인"]),
    ("CLOTRIMAZOLE", "클로트리마졸", ["클로트리마졸"]),
    ("ACYCLOVIR", "아시클로버(항바이러스 외용제)", ["아시클로버"]),
    ("MAGNESIUM_HYDROXIDE", "수산화마그네슘", ["수산화마그네슘"]),
    ("SODIUM_ALGINATE", "알긴산나트륨", ["알긴산나트륨"]),
    ("THIAMINE_B1", "티아민(비타민 B1)", ["티아민염산염", "벤포티아민"]),
    ("PYRIDOXINE_B6", "피리독신(비타민 B6)", ["피리독신"]),
    ("FOLIC_ACID", "엽산", ["엽산", "폴산"]),
    ("IPRATROPIUM", "이프라트로피움", ["이프라트로피움"]),
    ("BENZYDAMINE", "벤지다민", ["벤지다민"]),
    ("FUSIDIC_ACID", "퓨시드산", ["퓨시드산"]),
    ("DIQUAFOSOL", "디쿠아포솔나트륨", ["디쿠아포솔"]),
    ("INSULIN_GLARGINE", "인슐린글라진", ["인슐린글라진"]),
    ("AMOXICILLIN_CLAVULANATE", "아목시실린-클라불란산칼륨", ["아목시실린"]),
    ("DEXAMETHASONE", "덱사메타손", ["덱사메타손"]),
    ("CHLORPHENIRAMINE", "클로르페니라민(항히스타민)", ["페니라민"]),
    ("HYDROCHLOROTHIAZIDE", "히드로클로로티아지드(이뇨제)", ["히드로클로로티아지드"]),
    ("DIMENHYDRINATE", "디멘히드리네이트(항히스타민, 멀미약)", ["디멘히드리네이트"]),
    ("CARBOCISTEINE", "카르보시스테인(거담제)", ["카르보시스테인"]),
    ("BROMHEXINE", "브롬헥신(거담제)", ["브롬헥신"]),
    ("AMBROXOL", "암브록솔(거담제)", ["암브록솔", "암부록솔"]),
    ("CIMETIDINE", "시메티딘(H2차단제)", ["시메티딘"]),
    ("FAMOTIDINE", "파모티딘(H2차단제)", ["파모티딘"]),
    ("ALMAGATE", "알마게이트(제산제)", ["알마게이트"]),
    ("AMLODIPINE", "암로디핀(혈압강하제)", ["암로디핀"]),
    ("EPERISONE", "에페리손(근이완제)", ["에페리손"]),
    ("LOPERAMIDE", "로페라마이드(지사제)", ["로페라마이드"]),
    ("LEVOCETIRIZINE", "레보세티리진(항히스타민)", ["레보세티리진"]),
    ("RIFAMPIN", "리팜핀(항결핵제)", ["RMP", "리팜핀"]),
    ("ISONIAZID", "이소니아지드(항결핵제)", ["INH", "이소니아지드"]),
    ("ETHAMBUTOL", "에탐부톨(항결핵제)", ["EMB", "에탐부톨"]),
    ("VITAMIN_C", "비타민C", ["비타민씨", "비타민C", "비타민 C"]),
    ("AMINOPHYLLINE", "아미노필린(기관지확장제)", ["아미노필린"]),
    ("NIZATIDINE", "니자티딘(H2차단제)", ["니자티딘"]),
    ("ACETYLCYSTEINE", "아세틸시스테인(거담제)", ["아세틸시스테인"]),
    ("GINKGO_LEAF_EXTRACT", "은행엽엑스(말초혈액순환개선제)", ["은행엽"]),
    ("METFORMIN", "메트포르민(당뇨병 치료제)", ["메트포르민"]),
    ("TELMISARTAN_AMLODIPINE", "텔미사르탄+암로디핀(혈압강하 복합제)", ["텔미사르탄"]),
    ("MUGWORT_EXTRACT", "애엽 추출물(위점막보호제)", ["애엽"]),
    ("HANBANG_MIXED_HERBAL_EXTRACT", "한방 혼합단미엑스제(연조엑스, 단일성분 아님)", ["연조엑스"]),
]

BRAND_DICT = [
    ("아목틴", "AMOXICILLIN_CLAVULANATE", "아목시실린-클라불란산칼륨", GENERAL,
     "새 정본 1a522eb의 부분문자열 충돌 수정 반영: '목틴'보다 '아목틴'을 우선"),
    ("삐콤씨", "VITAMIN_B_COMPLEX_C", "비타민B복합제+비타민C", GENERAL, "유한양행 삐콤씨 — 일반적으로 알려진 비타민B군+C 복합제"),
    ("삐콤", "VITAMIN_B_COMPLEX", "비타민B복합제", SEARCHED_TODAY,
     "티아민6mg/리보플라빈6mg/나이아신25mgNE/판토텐산5mg/B6 1mg/B12 1μg/비타민C50mg — https://www.bosa.co.kr/news/articleView.html?idxno=2037335"),
    ("바로코민", "VITAMIN_B_COMPLEX", "비타민B복합제", SEARCHED_TODAY,
     "리보플라빈/피리독신/아스코르빈산/토코페롤/시아노코발라민/푸르설티아민 — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11A0030A0329"),
    ("비맥스", "VITAMIN_B_COMPLEX", "비타민B복합제(+D,아연)", SEARCHED_TODAY,
     "GC녹십자 비맥스 — 비타민B군 8종+D+아연 — https://gcbmax.co.kr/"),
    ("아로나민골드", "VITAMIN_B_COMPLEX", "비타민B복합제", GENERAL, "일동제약 아로나민골드 — 비타민B군 복합제로 널리 알려짐"),
    ("임팩타민", "VITAMIN_B_COMPLEX", "비타민B복합제", GENERAL, "고함량 비타민B군 제품으로 널리 알려짐"),
    ("벤포벨", "THIAMINE_B1", "벤포티아민(비타민B1 유도체)", GENERAL, "벤포티아민 제제로 널리 알려짐"),
    ("비오라민", "VITAMIN_B_COMPLEX", "비타민B복합제", GENERAL, "비타민B군 제품으로 추정 — 개별 검색 권장"),
    ("원스타민", "VITAMIN_B_COMPLEX", "비타민B복합제", GENERAL, "종합비타민 제품으로 추정 — 개별 검색 권장"),
    ("센트룸", "MULTIVITAMIN_MINERAL", "종합비타민·미네랄", GENERAL, "Centrum — 종합비타민 브랜드로 널리 알려짐"),
    ("고려은단비타민씨", "VITAMIN_C", "비타민C", GENERAL, "고려은단 비타민C 제품"),
    ("레모나유산균", "PROBIOTICS", "유산균", GENERAL, "레모나 브랜드의 유산균 라인"),
    ("레모나", "VITAMIN_C", "비타민C(발포/과립)", GENERAL, "경남제약 레모나 — 비타민C 제품으로 널리 알려짐"),
    ("훼리너프", "IRON_FMOA", "철 만니톨 난단백(FMOA)", SEARCHED_TODAY,
     "철-만니톨-난단백(FMOA)+시아노코발라민+엽산 — https://www.medifonews.com/news/article_print.html?no=15709"),
    ("헤모포스", "IRON_SUPPLEMENT", "철분제", GENERAL, "철분 보충제로 널리 알려짐, 개별 검색 권장"),
    ("광동헤모비앤씨", "IRON_SUPPLEMENT", "철분제", GENERAL, "광동제약 철분제로 추정 — 개별 검색 권장"),
    ("마이니 철분", "IRON_SUPPLEMENT", "철분제", GENERAL, "제품명에 철분 명시"),
    ("다크토솔루션 철분", "IRON_SUPPLEMENT", "철분제", GENERAL, "제품명에 철분 명시"),
    ("닥터솔루션 철분", "IRON_SUPPLEMENT", "철분제", GENERAL, "제품명에 철분 명시"),
    ("아임마미 철분", "IRON_SUPPLEMENT", "철분제(브랜드 미확인)", "unresolved", "브랜드 검색 결과 불충분 — 개별 확인 필요"),
    ("아임마미 엽산", "FOLIC_ACID", "엽산(브랜드 미확인)", "unresolved", "브랜드 검색 결과 불충분 — 개별 확인 필요"),
    ("마이니레몬유래엽산", "FOLIC_ACID", "엽산", GENERAL, "제품명에 엽산 명시"),
    ("마미센스", "IRON_SUPPLEMENT", "철분제(임산부용)", GENERAL, "신일제약 마미센스 — 헤모글로빈 수치 개선 효과 보고, 철분제 계열로 추정 — https://www.pillyze.com/ranking/health-condition/PREGNANCY"),
    ("케토크린", "KETOPROFEN", "케토프로펜(파스/겔)", SEARCHED_TODAY,
     "케토프로펜 30mg/매 — https://doctornow.co.kr/medicine-dictionary/9be66e5635ba4be29f89f312d33bdaa2"),
    ("케토톱", "KETOPROFEN", "케토프로펜(파스/겔)", GENERAL, "한독 케토톱 — 케토프로펜 파스로 널리 알려짐"),
    ("케펜텍", "KETOPROFEN", "케토프로펜(파스/겔)", SEARCHED_TODAY,
     "케토프로펜 30mg/매 — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11ACCCCC0229"),
    ("디펜쿨", "TOPICAL_NSAID_UNCONFIRMED", "국소소염진통제(성분 미확인)", "unresolved", "개별 검색 필요"),
    ("리도아가아제", "LIDOCAINE", "리도카인", SEARCHED_TODAY,
     "리도카인 58mg/g + 아크리놀 5.8mg/g(습윤드레싱형) — https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=199100535"),
    ("리도가아제", "LIDOCAINE", "리도카인", SEARCHED_TODAY,
     "'리도아가아제' 표기 차이 — 리도카인 58mg/g + 아크리놀 5.8mg/g(습윤드레싱형)"),
    ("베아제", "DIGESTIVE_ENZYME", "복합소화효소제", GENERAL, "대웅제약 베아제 — 복합 소화효소제로 널리 알려짐"),
    ("베스타제", "DIGESTIVE_ENZYME", "복합소화효소제", SEARCHED_TODAY,
     "동아제약 베스타제당의정 — 비오디아스타제500 75mg+리파제AP6 2.8mg+셀룰라제AP3 25mg — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11A0150A0156"),
    ("베데스타크림", "TOPICAL_UNCONFIRMED", "외용크림(성분 미확인)", "unresolved", "개별 확인 필요"),
    ("리나치올", "CARBOCISTEINE", "카르보시스테인(거담제)", SEARCHED_TODAY,
     "현대약품 리나치올 — L-카르보시스테인 — https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=197800466"),
    ("다이크로짇", "HYDROCHLOROTHIAZIDE", "히드로클로로티아지드(이뇨제)", SEARCHED_TODAY,
     "유한양행 다이크로짇정 — 히드로클로로티아지드 25mg — https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=196000008"),
    ("성광칼라민로오션", "CALAMINE", "칼라민로션", GENERAL, "제품명에 칼라민 명시"),
    ("후시딘", "FUSIDIC_ACID", "퓨시드산(항생연고)", GENERAL, "동화약품 후시딘 — 퓨시드산 성분으로 널리 알려짐"),
    ("마데카솔", "CENTELLA_EXTRACT", "병풀추출물(창상치료)", GENERAL, "동국제약 마데카솔 — 병풀(센텔라)추출물로 널리 알려짐"),
    ("안티푸라민", "METHYL_SALICYLATE", "메틸살리실레이트(소염진통연고)", GENERAL, "유한양행 안티푸라민 — 살리실산메틸 계열로 널리 알려짐"),
    ("맨소래담", "METHYL_SALICYLATE", "메틸살리실레이트(소염진통연고)", GENERAL, "멘소래담류 제품으로 널리 알려짐"),
    ("버물리", "INSECT_BITE_TOPICAL_UNCONFIRMED", "충교상 외용제(성분 미확인)", "unresolved", "제형 유사(안티푸라민류)로 추정되나 개별 확인 필요"),
    ("모스쿨액", "INSECT_BITE_TOPICAL_UNCONFIRMED", "충교상 외용액(성분 미확인)", "unresolved", "개별 확인 필요"),
    ("타이레놀", "ACETAMINOPHEN", "아세트아미노펜", GENERAL, "한국얀센 타이레놀 — 아세트아미노펜으로 널리 알려짐"),
    ("오구멘틴", "AMOXICILLIN_CLAVULANATE", "아목시실린-클라불란산칼륨", GENERAL, "GSK 오구멘틴 — 국내 최다처방 항생제 중 하나"),
    ("아루펜", "IBUPROFEN", "이부프로펜", SEARCHED_TODAY,
     "아루펜정400mg 이부프로펜 함유 — https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=199901375"),
    ("이부펜", "IBUPROFEN", "이부프로펜", SEARCHED_TODAY,
     "이부펜정400mg — 이부프로펜 400mg — https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetailCache?cacheSeq=198800776"),
    ("액티피드", "COLD_COMBO_ANTIHISTAMINE_DECONGESTANT", "항히스타민+비충혈제거 복합감기약", SEARCHED_TODAY,
     "슈도에페드린염산염60mg+트리프롤리딘염산염수화물2.5mg — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11A0500A0068"),
    ("마그밀", "MAGNESIUM_HYDROXIDE", "수산화마그네슘", SEARCHED_TODAY,
     "삼남제약 마그밀정 — 수산화마그네슘 500mg — https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=197400246"),
    ("게루삼", "ALUMINUM_MAGNESIUM_ANTACID", "알루미늄/마그네슘 복합 제산제", SEARCHED_TODAY,
     "건조수산화알루미늄겔200mg+침강탄산칼슘100mg+탄산마그네슘50mg+탄산수소나트륨50mg — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11ABBBBB2281"),
    ("알마겔", "ALMAGATE", "알마게이트(제산제)", SEARCHED_TODAY,
     "유한양행 알마겔정 — 알마게이트 — https://www.munyak.co.kr/product/detail/198700430"),
    ("파티겔", "ALMAGATE", "알마게이트(제산제)", "name_literal_parenthetical", "제품명에 (알마게이트) 명시"),
    ("코푸정", "COUGH_COLD_COMBO", "진해거담 복합제", SEARCHED_TODAY,
     "덱스트로메토르판/디히드로코데인+메틸에페드린+클로르페니라민 등 복합(제형별 상이) — https://health.kr/searchDrug/result_drug.asp?drug_cd=A11AGGGGA3916"),
    ("판크론", "DIGESTIVE_ENZYME", "복합소화효소제", GENERAL, "판크레아틴 계열 소화효소제로 추정 — 개별 확인 권장"),
    ("셉트린", "CO_TRIMOXAZOLE", "설파메톡사졸-트리메토프림(항생제)", SEARCHED_TODAY,
     "삼일제약 셉트린정 — 트리메토프림80mg+설파메톡사졸400mg — https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=197000049"),
    ("보나링", "DIMENHYDRINATE", "디멘히드리네이트(항히스타민, 멀미약)", SEARCHED_TODAY,
     "일양약품 보나링에이정 — 디멘히드리네이트 50mg — https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=197000076"),
    ("뮤코졸", "BROMHEXINE", "브롬헥신(거담제)", SEARCHED_TODAY,
     "부광약품 뮤코졸정 — 브롬헥신염산염 8.0mg — https://health.kr/searchDrug/result_drug.asp?drug_cd=A11A1310A0083"),
    ("뮤코론", "MUCOLYTIC_AGENT", "점액용해제(성분 미특정)", "unresolved", "개별 확인 필요 — 뮤코졸과 달리 검색으로 확인 안 됨"),
    ("다이제스토", "DIGESTIVE_ENZYME", "복합소화효소제", GENERAL, "'다이제틴' 계열과 유사 소화제로 추정 — 개별 확인 권장"),
    ("다이아제타산제", "DIGESTIVE_UNCONFIRMED", "소화제(정확한 제품 미확인)", "unresolved", "웹 검색으로 정확한 제품 특정 실패 — 개별 확인 필요"),
    ("로프민", "LOPERAMIDE", "로페라마이드(지사제)", SEARCHED_TODAY,
     "영일제약 로프민캅셀 — 로페라마이드염산염 2mg — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11A1660A0057"),
    ("부스코판", "HYOSCINE_BUTYLBROMIDE", "부틸스코폴라민(진경제)", SEARCHED_TODAY,
     "부틸스코폴라민브롬화물 10mg — https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=200901181"),
    ("아락실", "SENNA_LAXATIVE", "센나(완하제)", GENERAL, "아락실 — 센나 성분 완하제로 널리 알려짐"),
    ("둘코락스", "BISACODYL", "비사코딜(완하제)", SEARCHED_TODAY,
     "둘코락스에스장용정 — 비사코딜5mg+도큐세이트나트륨16.75mg — https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=200906478"),
    ("카네스텐", "CLOTRIMAZOLE", "클로트리마졸(항진균제)", GENERAL, "바이엘 카네스텐 — 항진균크림으로 널리 알려짐"),
    ("파나덤크림", "BETAMETHASONE_CLOTRIMAZOLE_GENTAMICIN", "베타메타손+클로트리마졸+겐타마이신(복합)", SEARCHED_TODAY,
     "스테로이드+항진균+항생 복합크림 — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11A2190B0010"),
    ("나미야지크림", "TOPICAL_UNCONFIRMED", "외용크림(성분 미확인, 수출명 BECLOGEN)", "unresolved", "수출명이 베클로메타손 계열을 시사하나 미확인 — 개별 확인 필요"),
    ("반질올크림", "TOPICAL_UNCONFIRMED", "외용크림(성분 미확인)", "unresolved", "개별 확인 필요"),
    ("오라메디", "TRIAMCINOLONE_ORAL", "트리암시놀론(구강궤양 연고)", GENERAL, "오라메디 — 구강궤양 치료연고로 널리 알려짐"),
    ("씨잘", "LEVOCETIRIZINE", "레보세티리진(항히스타민)", SEARCHED_TODAY,
     "한국유씨비 씨잘정5mg — 레보세티리진염산염 5mg — https://health.kr/searchDrug/result_drug.asp?drug_cd=A11AOOOOO3892"),
    ("레티라진", "LEVOCETIRIZINE", "레보세티리진(항히스타민)", SEARCHED_TODAY,
     "레보세티리진염산염 5mg — https://www.munyak.co.kr/product/detail/200807989"),
    ("로이솔", "AMBROXOL", "암브록솔(거담제)", SEARCHED_TODAY,
     "암브록솔염산염 30mg — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11A0030A0513"),
    ("아세르정", "AMBROXOL", "암브록솔(거담제)", SEARCHED_TODAY,
     "삼남제약 아세르정 암브록솔염산염 30mg — https://doctornow.co.kr/medicine-dictionary/48469da3845c4aa784703c472c40bb57"),
    ("암브로콜", "AMBROXOL_CLENBUTEROL", "암브록솔+클렌부테롤(진해거담 복합)", SEARCHED_TODAY,
     "암브록솔염산염 30mg + 클렌부테롤염산염 0.02mg — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11A2140A0163"),
    ("마로비벤", "HOMEOPATHIC_PLANT_COMPLEX", "동종요법 식물복합제(단일 API 아님)", SEARCHED_TODAY,
     "12종 식물틴크+1종 미네랄 복합 동종요법 제제, 원자재 매핑 시 단일 성분으로 취급 불가 — http://www.whosaeng.com/24560"),
    ("피고셉", "UNCONFIRMED", "성분 미확인", "unresolved", "정확한 제품 특정 실패 — 개별 확인 필요"),
    ("스테린정", "UNCONFIRMED", "성분 미확인", "unresolved", "'스티렌'과 혼동 가능 — 개별 확인 필요"),
    ("솔로젠정", "UNCONFIRMED", "성분 미확인", "unresolved", "개별 확인 필요"),
    ("프리나정", "UNCONFIRMED", "성분 미확인(벤조디아제핀계 추정)", "unresolved", "검색 결과 불충분 — 개별 확인 필요"),
    ("실버웰", "UNCONFIRMED", "성분 미확인", "unresolved", "개별 확인 필요"),
    ("스티렌", "MUGWORT_EXTRACT", "애엽(쑥) 추출물(위점막보호제)", GENERAL, "동아에스티 스티렌 — 애엽 추출물 위염치료제로 널리 알려짐"),
    ("리스테린", "MOUTHWASH_ANTISEPTIC", "구강청결제", GENERAL, "존슨앤드존슨 리스테린 — 구강청결제로 널리 알려짐"),
    ("가그린", "MOUTHWASH_ANTISEPTIC", "구강청결제", GENERAL, "동아제약 가그린 — 구강청결제로 널리 알려짐"),
    ("오스템쿨가글", "MOUTHWASH_ANTISEPTIC", "구강청결제", GENERAL, "구강청결제로 추정"),
    ("니코틴엘", "NICOTINE_REPLACEMENT", "니코틴 대체제(껌/패치)", GENERAL, "노바티스 니코틴엘 — 금연보조 니코틴 껌/패치로 널리 알려짐"),
    ("니코레트", "NICOTINE_REPLACEMENT", "니코틴 대체제(껌)", GENERAL, "니코레트 — 금연보조 니코틴 껌으로 널리 알려짐"),
    ("니코파인드", "NICOTINE_REPLACEMENT", "니코틴 대체제", "unresolved", "브랜드 확인 필요"),
    ("니코싸인", "NICOTINE_REPLACEMENT", "니코틴 대체제(추정)", "unresolved", "브랜드 확인 필요"),
    ("니코에이", "NICOTINE_REPLACEMENT", "니코틴 대체제(추정)", "unresolved", "브랜드 확인 필요"),
    ("제네디아코티닌테스트", "COTININE_TEST_KIT", "코티닌 검사키트(금연클리닉 흡연확인용)", GENERAL, "제품명에 코티닌 검사 명시"),
    ("벅스존벤주론타블렛", "DIFLUBENZURON_IGR", "디플루벤주론(곤충성장억제제, 방역용)", SEARCHED_TODAY,
     "IGR 특성 곤충성장억제 정제, 디플루벤주론 함유 — https://bugszone.com/"),
    ("유박스", "HEPATITIS_B_VACCINE", "B형간염 백신", GENERAL, "LG화학/녹십자 유박스 — B형간염 백신으로 널리 알려짐"),
    ("인플루엔자", "INFLUENZA_VACCINE", "인플루엔자 백신", GENERAL, "제품명에 인플루엔자 명시"),
    ("글루오렌지", "OGTT_GLUCOSE_SOLUTION", "경구포도당부하검사용 포도당액", SEARCHED_TODAY,
     "OGTT용 포도당 용액 — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11AKP08F0547"),
    ("듀오덤", "WOUND_DRESSING_HYDROCOLLOID", "하이드로콜로이드 창상피복재", GENERAL, "ConvaTec 듀오덤 — 하이드로콜로이드 드레싱으로 널리 알려짐"),
    ("메디폼", "WOUND_DRESSING_POLYURETHANE_FOAM", "폴리우레탄 폼 창상피복재", SEARCHED_TODAY,
     "반투과성 폴리우레탄 필름+폼 — https://www.medifoam.co.kr/introduction/productDetail.php?type=type6"),
    ("슈퍼포아", "WOUND_DRESSING_NONWOVEN", "부직포+아크릴점착 창상피복재", SEARCHED_TODAY,
     "부직포+아크릴점착코팅 재질 — https://3sjeju.com/m/shop/item.php?it_id=A170105"),
    ("수퍼포아", "WOUND_DRESSING_NONWOVEN", "부직포+아크릴점착 창상피복재", SEARCHED_TODAY, "슈퍼포아와 동일 제품(표기 차이) 추정"),
    ("테가덤", "WOUND_DRESSING_FILM", "투명필름 창상피복재", GENERAL, "3M 테가덤 — 투명 필름 드레싱으로 널리 알려짐"),
    ("스테리스트립", "WOUND_CLOSURE_STRIP", "창상봉합용 스트립", GENERAL, "3M 스테리스트립 — 봉합대체 테이프로 널리 알려짐"),
    ("대일밴드", "ADHESIVE_BANDAGE", "접착 밴드(반창고)", GENERAL, "대일밴드 — Band-Aid류 접착밴드 브랜드로 널리 알려짐"),
    ("코반", "SELF_ADHERENT_WRAP", "자착성 압박붕대", GENERAL, "3M 코반 — 자착성 붕대로 널리 알려짐"),
    ("키네시오테이프", "KINESIOLOGY_TAPE", "키네시오 테이프", GENERAL, "제품명 자체가 일반명"),
    ("코드프리", "BLOOD_GLUCOSE_TEST_STRIP", "혈당검사지", GENERAL, "SD바이오센서 코드프리 — 혈당측정기 브랜드로 널리 알려짐"),
    ("케어센스", "BLOOD_GLUCOSE_TEST_STRIP", "혈당검사지", GENERAL, "아이센스 케어센스 — 혈당측정기 브랜드로 널리 알려짐"),
    ("아큐첵", "BLOOD_GLUCOSE_TEST_STRIP", "혈당검사지", GENERAL, "로슈 Accu-Chek — 혈당측정기 브랜드로 널리 알려짐"),
    ("accu chek", "BLOOD_GLUCOSE_TEST_STRIP", "혈당검사지", GENERAL, "로슈 Accu-Chek — 혈당측정기 브랜드로 널리 알려짐"),
    ("accu check", "BLOOD_GLUCOSE_TEST_STRIP", "혈당검사지", GENERAL, "로슈 Accu-Chek — 혈당측정기 브랜드로 널리 알려짐"),
    ("리피도케어스트립", "LIPID_TEST_STRIP", "지질(콜레스테롤) 검사지", GENERAL, "제품명에 리피드(지질) 명시"),
    ("듀오락베이비", "PROBIOTICS", "유산균(영유아용)", GENERAL, "셀바이오텍 듀오락 — 유산균 브랜드로 널리 알려짐"),
    ("덴트픽스", "DENTURE_ADHESIVE", "틀니 접착제", GENERAL, "제품명으로 미루어 틀니 접착제 추정"),
    ("프랙타주", "HOMEOPATHIC_PLANT_COMPLEX", "동종요법 식물복합제(단일 API 아님)", SEARCHED_TODAY,
     "이연제약 프랙타주 — 12종 이상 식물틴크+미네랄 복합 동종요법제 — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11AOOOOO8294"),
    ("라벤다크림", "BETAMETHASONE_CLOTRIMAZOLE_GENTAMICIN", "베타메타손+클로트리마졸+겐타마이신(복합)", SEARCHED_TODAY,
     "베타메타손디프로피오네이트+클로트리마졸+겐타마이신황산염 복합크림 — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11A0770A0224"),
    ("메가트루파워", "VITAMIN_B_COMPLEX", "비타민B복합제", SEARCHED_TODAY,
     "유한양행 메가트루파워 — 활성형 비타민B군+D+항산화 미네랄 — https://megatrue.co.kr/"),
    ("메가트루", "VITAMIN_B_COMPLEX", "비타민B복합제", SEARCHED_TODAY, "유한양행 메가트루 — 비타민B군 복합제"),
    ("글루코닥터탑", "BLOOD_GLUCOSE_TEST_STRIP", "혈당검사지", SEARCHED_TODAY,
     "올메디쿠스 글루코닥터탑 — 혈당측정기/시험지 브랜드 — https://prod.danawa.com/info/?pcode=3314174"),
    ("미션울트라", "BLOOD_GLUCOSE_TEST_STRIP", "혈당검사지(브랜드 미확인)", "unresolved", "혈당측정 문맥으로 추정되나 브랜드 특정 실패"),
    ("콜레스틱", "LIPID_TEST_STRIP", "지질(콜레스테롤) 검사지", GENERAL, "제품명 패턴('콜레스'+'스틱')으로 콜레스테롤 검사지 추정"),
    ("케논엘플라스타", "KETOPROFEN", "케토프로펜(파스/겔)", GENERAL, "'케논엘플라스타'는 케토프로펜 계열 파스로 추정(케펜텍/케토톱류와 동일 계열명 패턴)"),
    ("케논엘", "AMOXICILLIN_CLAVULANATE", "아목시실린-클라불란산칼륨", SEARCHED_TODAY,
     "종근당 케논엘 — 아목시실린수화물+클라불란산칼륨 — https://www.ckdpharm.com/product/productView.do?prodCode=CKD0000088"),
    ("신신파프", "METHYL_SALICYLATE", "메틸살리실레이트(소염진통연고)", SEARCHED_TODAY,
     "살리실산메틸 300mg+디펜히드라민+티몰+살리실산글리콜 복합 — https://www.sinsinpas.net/product/view.php?board_id=15"),
    ("케토그린", "KETOPROFEN", "케토프로펜(파스/겔)", GENERAL, "'케토' 접두 브랜드 패턴으로 케토프로펜 계열 추정 — 개별 확인 권장"),
    ("혜민고", "HANBANG_MIXED_HERBAL_EXTRACT", "한방 혼합 외용 고제(단일 성분 아님)", GENERAL, "한방 외용 고제로 추정 — 개별 확인 권장"),
    ("바로한방고", "HANBANG_MIXED_HERBAL_EXTRACT", "한방 혼합 외용 고제(단일 성분 아님)", GENERAL, "한방 외용 고제로 추정 — 개별 확인 권장"),
    ("훼스탈", "DIGESTIVE_ENZYME", "복합소화효소제", GENERAL, "한독 훼스탈 — 복합 소화효소제로 널리 알려짐"),
    ("물린디액", "INSECT_BITE_TOPICAL_UNCONFIRMED", "충교상 외용제(성분 미확인)", "unresolved", "신신제약 물린디액 — 개별 확인 필요"),
    # --- 2026-07-15 3차: 전체 101,546건 확장 시 사용량 상위 unresolved에서 발견된 갭 ---
    ("다이아벡스", "METFORMIN", "메트포르민(당뇨병 치료제)", SEARCHED_TODAY,
     "대웅제약 다이아벡스정 — 메트포르민염산염(구 제품명 굴루코파정) — https://www.munyak.co.kr/product/detail/198500321"),
    ("글루코젠", "METFORMIN", "메트포르민(당뇨병 치료제)", GENERAL, "뉴젠팜 글루코젠정 — 제품명 패턴(글루코+젠)으로 메트포르민 제제로 추정, 개별 확인 권장"),
    ("메트파민", "METFORMIN", "메트포르민(당뇨병 치료제)", GENERAL, "삼남제약 메트파민정 — 제품명 자체가 메트포르민 축약형, 개별 확인 권장"),
    ("트윈스타", "TELMISARTAN_AMLODIPINE", "텔미사르탄+암로디핀(혈압강하 복합제)", SEARCHED_TODAY,
     "한국베링거인겔하임 트윈스타정 — 텔미사르탄+암로디핀베실산염 — https://health.kr/searchDrug/result_drug.asp?drug_cd=2010082000008"),
    ("투탑스", "TELMISARTAN_AMLODIPINE", "텔미사르탄+암로디핀(혈압강하 복합제)", SEARCHED_TODAY,
     "일동제약 투탑스정 — 텔미사르탄+암로디핀 제네릭 복합제 — https://mobile.ildong.com/kor/product/view.id?prdSeq=2743"),
    ("셀미스타", "TELMISARTAN_AMLODIPINE", "텔미사르탄+암로디핀(혈압강하 복합제)", GENERAL, "셀트리온제약 — 트윈스타/투탑스와 동일 계열명 패턴(용량표기 40/5, 80/5)으로 추정"),
    ("텔미르핀", "TELMISARTAN_AMLODIPINE", "텔미사르탄+암로디핀(혈압강하 복합제)", GENERAL, "화일약품 — '텔미' 접두로 텔미사르탄 계열 복합제 추정"),
    ("노바스크", "AMLODIPINE", "암로디핀(혈압강하제)", GENERAL, "한국화이자 노바스크 — 암로디핀베실산염 오리지널로 널리 알려짐"),
    ("바로디핀", "AMLODIPINE", "암로디핀(혈압강하제)", GENERAL, "신풍제약 — '~디핀' 계열 암로디핀 제네릭 명명 패턴으로 추정, 개별 확인 권장"),
    # --- 2026-07-15 4차: 큐 상위 대량 확장(고혈압/당뇨/이상지질혈증 복합제 위주) ---
    ("엑스포지", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY,
     "노바티스 엑스포지정(오리지널) — 암로디핀+발사르탄 — https://www.novartis.com/kr-ko/sites/novartis_kr/files/exforge.pdf"),
    ("엑스포스", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY,
     "익수제약 엑스포스정 — 엑스포지 제네릭, 암로디핀+발사르탄 — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=2016041900007"),
    ("엑스듀오", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "익수제약 발사르탄 복합제 패밀리('엑스' 접두) 명명 패턴으로 추정"),
    ("엑스발사텍", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "'발사텍'에 발사르탄 명시, 익수제약 패밀리 패턴"),
    ("엑스로탄", "ARB_AMLODIPINE_COMBO_UNCONFIRMED", "ARB+암로디핀 복합제(정확한 ARB 성분 미확인)", "unresolved", "'엑스' 계열 패턴이나 '로탄'이 로사르탄을 시사할 수도 있어 개별 확인 필요"),
    ("엑스페라", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "익수제약 발사르탄 복합제 패밀리('엑스' 접두) 명명 패턴으로 추정"),
    ("아모잘탄", "LOSARTAN_AMLODIPINE", "암로디핀+로사르탄(혈압강하 복합제)", SEARCHED_TODAY,
     "한미약품 아모잘탄정 — 암로디핀캄실산염+로사르탄칼륨, 2020년 원외처방 2위 고혈압약 — https://www.hanmi.co.kr/business/product/finder/detail-703.hm?prodSeq=703"),
    ("암로잘", "LOSARTAN_AMLODIPINE", "암로디핀+로사르탄(혈압강하 복합제)", GENERAL, "'암로'+'잘탄' 조합으로 아모잘탄 계열 제네릭 추정"),
    ("로자살탄", "LOSARTAN", "로사르탄칼륨(혈압강하제)", GENERAL, "'로자'+'살탄' 조합으로 로사르탄 단일제 추정"),
    ("카자탄", "LOSARTAN", "로사르탄칼륨(혈압강하제)", GENERAL, "'~자탄' 접미로 로사르탄 계열 제네릭 추정, 개별 확인 권장"),
    ("오잘탄", "LOSARTAN", "로사르탄칼륨(혈압강하제)", GENERAL, "'~잘탄' 접미로 로사르탄 계열 제네릭 추정, 개별 확인 권장"),
    ("코자살탄", "LOSARTAN", "로사르탄칼륨(혈압강하제)", GENERAL, "'코자'(오리지널 Cozaar 브랜드 유래)+'살탄' 조합으로 로사르탄 제네릭 추정"),
    ("로자케이", "LOSARTAN", "로사르탄칼륨(혈압강하제)", "name_literal_parenthetical", "제품명에 (로자탄칼륨) 명시 — '로사르탄칼륨'의 표기 변형으로 판단"),
    ("트라젠타듀오", "LINAGLIPTIN_METFORMIN", "리나글립틴+메트포르민(당뇨병 복합제)", SEARCHED_TODAY,
     "베링거인겔하임 트라젠타듀오정 — DPP-4억제제(리나글립틴)+메트포르민, 2012년 국내 허가 — https://doctorsnews.co.kr/news/articleView.html?idxno=111749"),
    ("슈가메트", "DPP4_METFORMIN_COMBO_GENERIC", "DPP-4억제제+메트포르민(당뇨병 복합제, 성분 미특정)", SEARCHED_TODAY,
     "DPP-4 억제제 계열 복합제로 확인되나 정확한 DPP-4 성분(자체 함량 조합)은 개별 확인 필요 — https://www.medicopharma.co.kr/news/articleView.html?idxno=62336"),
    ("로수바미브", "ROSUVASTATIN_EZETIMIBE", "로수바스타틴+에제티미브(이상지질혈증 복합제)", SEARCHED_TODAY,
     "유한양행 로수바미브정 — 저용량 로수바스타틴+에제티미브 — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=2016012600015"),
    ("로수젯", "ROSUVASTATIN_EZETIMIBE", "로수바스타틴+에제티미브(이상지질혈증 복합제)", GENERAL, "한미약품 로수젯정 — 로수바미브와 시장 빅2를 이루는 동일 계열 오리지널 복합신약으로 널리 알려짐"),
    ("우루사", "URSODEOXYCHOLIC_ACID", "우르소데옥시콜산(간장약)", GENERAL, "대웅제약 우루사 — UDCA 성분으로 널리 알려짐"),
    ("지르텍", "CETIRIZINE", "세티리진(항히스타민)", GENERAL, "한국유씨비 지르텍 — 세티리진 성분으로 널리 알려짐"),
    ("부루펜", "IBUPROFEN", "이부프로펜", GENERAL, "삼일제약 부루펜 — 이부프로펜으로 널리 알려짐"),
    ("엘도스", "ERDOSTEINE", "에르도스테인(거담제)", GENERAL, "대웅제약 엘도스캅셀 — 에르도스테인으로 널리 알려짐"),
    ("가스터", "FAMOTIDINE", "파모티딘(H2차단제)", GENERAL, "동아제약 가스터정 — 파모티딘으로 널리 알려짐"),
    ("아마릴", "GLIMEPIRIDE", "글리메피리드(당뇨병 치료제)", SEARCHED_TODAY,
     "한독 아마릴정 — 글리메피리드, 1998년 발매 이후 경구혈당강하제 시장 점유율 1위 — https://www.handok.co.kr/product/detail?idx=46"),
    ("아모디핀", "AMLODIPINE", "암로디핀(혈압강하제)", GENERAL, "'아모디핀' 표기로 암로디핀 제네릭 추정"),
    ("오로디핀", "AMLODIPINE", "암로디핀(혈압강하제)", GENERAL, "동아제약 — '~디핀' 계열 암로디핀 제네릭 명명 패턴으로 추정"),
    ("베스디핀", "AMLODIPINE", "암로디핀(혈압강하제)", GENERAL, "진양제약 — '~디핀' 계열 암로디핀 제네릭 명명 패턴으로 추정"),
    ("암로핀", "AMLODIPINE", "암로디핀(혈압강하제)", GENERAL, "유한양행 — '암로'+'핀' 조합으로 암로디핀 제네릭 추정"),
    ("암로텐", "AMLODIPINE", "암로디핀(혈압강하제)", GENERAL, "일동제약 — '암로' 접두로 암로디핀 제네릭 추정"),
    # --- 2026-07-15 5차: ARB/CCB/DPP-4 복합제 대량 확장(큐 상위 300 조사) ---
    # 발사르탄+암로디핀(엑스포지 제네릭) — 명단 확인: 데일리팜/약업신문
    ("엑스포르테", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY,
     "한국휴텍스 — 엑스포지 제네릭 — https://www.yakup.com/news/index.html?nid=109758&mode=view"),
    ("엑스콤비", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY, "대원제약 — 엑스포지 제네릭"),
    ("노바스크브이", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY, "한국화이자 — 엑스포지 제네릭(자사 노바스크 브랜드 확장)"),
    ("발사포스", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY, "JW중외제약 — 엑스포지 제네릭"),
    ("하이포지", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY, "한국콜마 — 엑스포지 제네릭"),
    ("바르디핀", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY, "우리들제약 — 엑스포지 제네릭"),
    ("제이포지", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY, "제일약품 — 엑스포지 제네릭"),
    ("맥스포지", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY, "셀트리온제약 — 엑스포지 제네릭"),
    ("바이포지", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY, "일동제약 — 엑스포지 제네릭"),
    ("엑스디핀", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY, "프라임제약 — 엑스포지 제네릭"),
    ("암로발탄", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", SEARCHED_TODAY, "신풍제약 — 엑스포지 제네릭"),
    ("듀크포지", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "'포지'(Exforge 유래) 접미 패턴으로 엑스포지 계열 제네릭 추정"),
    ("더블포지", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "'포지' 접미 패턴으로 엑스포지 계열 제네릭 추정"),
    ("비스포지", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "'포지' 접미 패턴으로 엑스포지 계열 제네릭 추정"),
    ("셀트포지", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "셀트리온제약 — '포지' 접미 패턴으로 엑스포지 계열 제네릭 추정"),
    ("디스포지", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "'포지' 접미 패턴으로 엑스포지 계열 제네릭 추정"),
    ("카덴자", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "용량표기(5/80mg) 패턴이 엑스포지 계열과 동일해 추정"),
    ("아모스타", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "'아모'+'스타' 조합, 용량표기(40/5mg)로 발사르탄+암로디핀 계열 추정"),
    ("발사핀", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "'발사'(발사르탄)+'핀'(암로디핀) 조합 명명 패턴"),
    ("트윈플러스", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "위더스제약 — 트윈스타류와 유사 계열명이나 용량표기(40/5mg)상 발사르탄 계열 추정"),
    ("트윈에코", "VALSARTAN_AMLODIPINE", "암로디핀+발사르탄(혈압강하 복합제)", GENERAL, "시어스제약 — 용량표기(40/5mg)상 발사르탄+암로디핀 계열 추정"),
    # 발사르탄+HCTZ(코디오반 제네릭)
    ("코디오르반", "VALSARTAN_HCTZ", "발사르탄+히드로클로로티아지드(혈압강하 복합제)", SEARCHED_TODAY,
     "대원제약 코디오르탄 계열 — 노바티스 코디오반(발사르탄/HCTZ) 제네릭 — https://mdtoday.co.kr/news/view/179584233784298"),
    ("코디오살탄", "VALSARTAN_HCTZ", "발사르탄+히드로클로로티아지드(혈압강하 복합제)", GENERAL, "코디오반 계열 제네릭 명명 패턴"),
    ("코디오르탄", "VALSARTAN_HCTZ", "발사르탄+히드로클로로티아지드(혈압강하 복합제)", GENERAL, "코디오반 계열 제네릭 명명 패턴"),
    # 텔미사르탄 단일제
    ("미카르디스", "TELMISARTAN", "텔미사르탄(혈압강하제, 단일제)", SEARCHED_TODAY,
     "한국베링거인겔하임 미카르디스정 — 텔미사르탄 오리지널 단일제, 1999년 허가 — https://www.boehringer-ingelheim.com/kr/human-health/products/micardis"),
    ("텔미누보", "TELMISARTAN_AMLODIPINE", "텔미사르탄+암로디핀(혈압강하 복합제, 저용량)", SEARCHED_TODAY,
     "종근당 텔미누보 — 텔미사르탄 저용량 공략 제품, 용량표기(40/2.5mg)상 암로디핀 복합 추정 — https://www.dailypharm.com/Users/News/NewsView.html?ID=315936"),
    ("텔미탄플러스", "TELMISARTAN_HCTZ", "텔미사르탄+히드로클로로티아지드(혈압강하 복합제, 추정)", GENERAL, "'텔미'+'플러스' 명명 패턴, 정확한 이뇨제 성분은 개별 확인 필요"),
    ("텔미로탄플러스", "TELMISARTAN_HCTZ", "텔미사르탄+히드로클로로티아지드(혈압강하 복합제, 추정)", GENERAL, "'텔미'+'플러스' 명명 패턴"),
    ("씨르텔미플러스", "TELMISARTAN_HCTZ", "텔미사르탄+히드로클로로티아지드(혈압강하 복합제, 추정)", GENERAL, "'텔미'+'플러스' 명명 패턴"),
    # 로사르탄+HCTZ(하이자정 제네릭, 코자플러스류)
    ("코자플러스", "LOSARTAN_HCTZ", "로사르탄+히드로클로로티아지드(혈압강하 복합제)", GENERAL, "'코자'(Cozaar 유래)+'플러스'(이뇨제 추가) 명명 패턴"),
    ("코자르탄플러스", "LOSARTAN_HCTZ", "로사르탄+히드로클로로티아지드(혈압강하 복합제)", GENERAL, "'코자르탄'+'플러스' 명명 패턴"),
    ("코자", "LOSARTAN", "로사르탄칼륨(혈압강하제, 단일제)", GENERAL, "한국MSD 코자 — 로사르탄칼륨 오리지널로 널리 알려짐"),
    ("코자르탄", "LOSARTAN", "로사르탄칼륨(혈압강하제)", GENERAL, "'코자'(Cozaar) 유래 제네릭 명명 패턴"),
    # 칸데사르탄+암로디핀
    ("칸데암로", "CANDESARTAN_AMLODIPINE", "칸데사르탄+암로디핀(혈압강하 복합제)", SEARCHED_TODAY,
     "신풍제약 칸데암로 — 칸데사르탄/암로디핀 개량신약, 8/16/5/10mg 용량 — http://www.monews.co.kr/news/articleView.html?idxno=93256"),
    # 로수바스타틴+에제티미브 추가 브랜드
    ("로바젯", "ROSUVASTATIN_EZETIMIBE", "로수바스타틴+에제티미브(이상지질혈증 복합제)", GENERAL, "'로바'(로수바스타틴)+'젯'(에제티미브) 명명 패턴"),
    ("로수브젯", "ROSUVASTATIN_EZETIMIBE", "로수바스타틴+에제티미브(이상지질혈증 복합제)", GENERAL, "'로수브'+'젯' 명명 패턴"),
    ("로수엠젯", "ROSUVASTATIN_EZETIMIBE", "로수바스타틴+에제티미브(이상지질혈증 복합제)", GENERAL, "'로수'+'젯' 명명 패턴"),
    ("진토젯", "ROSUVASTATIN_EZETIMIBE", "로수바스타틴+에제티미브(이상지질혈증 복합제)", GENERAL, "'젯'(에제티미브) 접미 패턴"),
    ("아토바미브", "ROSUVASTATIN_EZETIMIBE", "로수바스타틴+에제티미브(이상지질혈증 복합제)", GENERAL, "'바미브'(에제티미브 계열 명명, 로수바미브류) 패턴"),
    ("아르젯", "ROSUVASTATIN_EZETIMIBE", "로수바스타틴+에제티미브(이상지질혈증 복합제)", GENERAL, "'젯' 접미 패턴"),
    ("아로젯", "ROSUVASTATIN_EZETIMIBE", "로수바스타틴+에제티미브(이상지질혈증 복합제)", GENERAL, "'젯' 접미 패턴"),
    ("에슈바", "ROSUVASTATIN_EZETIMIBE", "로수바스타틴+에제티미브(이상지질혈증 복합제)", GENERAL, "용량표기(10/10mg) 패턴상 동일계열 추정"),
    ("에브젯", "ROSUVASTATIN_EZETIMIBE", "로수바스타틴+에제티미브(이상지질혈증 복합제)", GENERAL, "'젯' 접미 패턴"),
    ("로슈타", "ROSUVASTATIN", "로수바스타틴칼슘(이상지질혈증 치료제, 단일제)", "name_literal_parenthetical", "제품명에 (로수바스타틴칼슘) 명시(공백 포함 괄호라 정규식 미매칭, 브랜드사전으로 보완)"),
    # 아토르바스타틴
    ("리피토플러스", "ATORVASTATIN_EZETIMIBE", "아토르바스타틴+에제티미브(이상지질혈증 복합제)", GENERAL, "화이자 리피토 브랜드 확장, 용량표기(10/10mg)상 에제티미브 복합 추정"),
    ("리피토", "ATORVASTATIN", "아토르바스타틴칼슘(이상지질혈증 치료제, 단일제)", GENERAL, "한국화이자 리피토 — 아토르바스타틴 오리지널로 널리 알려짐"),
    ("리피논", "ATORVASTATIN", "아토르바스타틴칼슘(이상지질혈증 치료제)", GENERAL, "동아제약 — 아토르바스타틴 제네릭으로 추정"),
    # DPP-4 억제제+메트포르민 복합제
    ("자디앙듀오", "EMPAGLIFLOZIN_METFORMIN", "엠파글리플로진+메트포르민(당뇨병 복합제)", SEARCHED_TODAY,
     "한국베링거인겔하임·한국릴리 자디앙듀오 — SGLT-2억제제+메트포르민 — https://www.boehringer-ingelheim.com/kr/human-health/products/jardiance-duo"),
    ("테네글립엠", "TENELIGLIPTIN_METFORMIN", "테네리글립틴+메트포르민(당뇨병 복합제)", SEARCHED_TODAY, "한독 테넬리아엠 제네릭 계열 — 테네리글립틴+메트포르민"),
    ("테네글리엠", "TENELIGLIPTIN_METFORMIN", "테네리글립틴+메트포르민(당뇨병 복합제)", GENERAL, "테네글립엠과 동일계열 표기 변형"),
    ("테네글엠", "TENELIGLIPTIN_METFORMIN", "테네리글립틴+메트포르민(당뇨병 복합제)", GENERAL, "테네글립엠과 동일계열 표기 변형"),
    ("제미메트", "GEMIGLIPTIN_METFORMIN", "제미글립틴+메트포르민(당뇨병 복합제)", SEARCHED_TODAY,
     "LG화학/LG생명과학 제미메트 — 제미글립틴(자체개발 DPP-4억제제)+메트포르민, 2013년 허가"),
    ("자누비아", "SITAGLIPTIN", "시타글립틴(당뇨병 치료제, DPP-4억제제)", GENERAL, "한국MSD 자누비아 — 시타글립틴 오리지널로 널리 알려짐"),
    ("글루코닐", "METFORMIN", "메트포르민(당뇨병 치료제)", GENERAL, "셀트리온제약 — '글루코' 접두 패턴으로 메트포르민 제제 추정"),
    # 기타 순환기/소화기 브랜드
    ("돔페린", "DOMPERIDONE", "돔페리돈(위장관운동촉진제)", GENERAL, "'돔페' 접두로 돔페리돈 제네릭 추정"),
    ("돔펠엠", "DOMPERIDONE", "돔페리돈(위장관운동촉진제)", GENERAL, "'돔페' 접두로 돔페리돈 제네릭 추정"),
    ("돔필", "DOMPERIDONE", "돔페리돈(위장관운동촉진제)", GENERAL, "'돔' 접두로 돔페리돈 제네릭 추정"),
    ("맥페란", "METOCLOPRAMIDE", "메토클로프라미드(위장관운동촉진제)", GENERAL, "동화약품 맥페란 — 메토클로프라미드로 널리 알려짐"),
    ("가스모틴", "MOSAPRIDE", "모사프리드(위장관운동촉진제)", GENERAL, "대웅제약 가스모틴 — 모사프리드로 널리 알려짐"),
    ("모티리톤", "HANBANG_MIXED_HERBAL_EXTRACT", "한방 혼합단미엑스제(현호색·견우자 연조엑스)", GENERAL, "동아제약 모티리톤 — 현호색·견우자 등 생약복합제로 널리 알려짐"),
    ("탐스로", "TAMSULOSIN", "탐수로신(전립선비대증 치료제)", GENERAL, "'탐스로' 접두로 탐수로신 제네릭 추정"),
    ("메토폴", "METOPROLOL", "메토프롤롤(베타차단제)", GENERAL, "'메토폴' 표기로 메토프롤롤 제네릭 추정"),
    ("테놀민", "ATENOLOL", "아테놀롤(베타차단제)", GENERAL, "현대약품 테놀민 — '테놀'(아테놀롤) 명명 패턴"),
    ("아테날", "ATENOLOL", "아테놀롤(베타차단제)", GENERAL, "'테날'(아테놀롤) 명명 패턴 추정"),
    ("엘도신", "ERDOSTEINE", "에르도스테인(거담제)", GENERAL, "엘도스와 유사 계열명, 에르도스테인 제네릭 추정"),
    ("훼로바", "IRON_SUPPLEMENT", "철분제", GENERAL, "'훼로'(ferrous, 철) 접두로 철분제 추정"),
    ("칼시롤", "VITAMIN_D", "콜레칼시페롤(비타민D)", GENERAL, "'칼시롤'(칼시페롤) 명명 패턴으로 비타민D 추정"),
    ("코푸시럽", "COUGH_COLD_COMBO", "진해거담 복합제", GENERAL, "유한양행 코푸시럽 — 진해거담 복합 시럽으로 널리 알려짐"),
    ("코푸투스", "COUGH_COLD_COMBO", "진해거담 복합제", GENERAL, "'코푸' 계열 진해거담제 명명 패턴"),
    ("코대원", "COUGH_COLD_COMBO", "진해거담 복합제", GENERAL, "대원제약 — '코' 접두 진해거담 시럽 명명 패턴"),
    ("코프원", "COUGH_COLD_COMBO", "진해거담 복합제", GENERAL, "현대약품 — '코' 접두 진해거담 시럽 명명 패턴"),
    ("시네츄라", "IVY_LEAF_EXTRACT", "아이비엽 추출물(진해거담제)", GENERAL, "안국약품 시네츄라 — 아이비엽 추출물로 널리 알려짐"),
    ("레보투스", "레보드로프로피진", "레보드로프로피진(거담제)", GENERAL, "'레보투스' 브랜드명, 레보드로프로피진 제제 추정"),
    # --- 2026-07-15 6차: 큐 최상위 재조사 ---
    ("토바스틴", "ATORVASTATIN", "아토르바스타틴칼슘(이상지질혈증 치료제)", SEARCHED_TODAY,
     "셀트리온제약 토바스틴정 — 아토르바스타틴칼슘삼수화물 — https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetailCache?cacheSeq=200706537aupdateTs2025-01-03+23:21:42.0b"),
    ("뉴본", "KALLIKREIN", "칼리크레인(말초순환장애 치료제)", SEARCHED_TODAY,
     "메디카코리아 뉴본정 — 칼리크레인1, 말초순환장애·메니에르증후군 등에 사용 — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11A1000A0181"),
    ("영풍칼리크레인", "KALLIKREIN", "칼리크레인(말초순환장애 치료제)", GENERAL, "영풍제약 — 제품명에 칼리크레인 명시"),
    ("에페리젠", "EPERISONE", "에페리손(근이완제)", SEARCHED_TODAY,
     "뉴젠팜 에페리젠정 — 에페리손염산염 — https://www.health.kr/searchDrug/result_drug.asp?drug_cd=A11AOOOOO3224"),
    ("헤모퀸골드", "IRON_SUPPLEMENT", "철분제(폴리사카리드철착염 복합)", SEARCHED_TODAY,
     "경남제약 헤모퀸골드엠캡슐 — 폴리사카리드철착염+시아노코발라민+폴산 — https://www.munyak.co.kr/product/detail/201400746"),
]

FUNCTIONAL_KEYWORDS = [
    (["주사기"], "DISPOSABLE_SYRINGE", "일회용 주사기"),
    (["란셋", "란셑", "난셋", "안전난셋", "채혈침", "사혈침"], "LANCET", "채혈용 란셋"),
    (["설압자"], "TONGUE_DEPRESSOR", "설압자"),
    (["장갑", "글러브", "글로브", "라텍스", "폴리글로브", "러버글러브"], "MEDICAL_GLOVE", "의료용 장갑(라텍스/니트릴/폴리)"),
    (["포비돈", "포타딘", "그린포비돈"], "POVIDONE_IODINE", "포비돈요오드 소독제/스왑"),
    (["알콜솜", "알콜스왑", "알콜스폰지", "에탄올스왑", "알코올솜", "알코올스왑"], "ALCOHOL_SWAB", "알코올 소독솜/스왑"),
    (["소독솜", "코튼볼", "써지코튼", "스킨코튼"], "COTTON_GAUZE", "소독솜/거즈"),
    (["케어스왑", "뉴클린스왑", "크린셉스틱스왑", "성광포스틱스왑", "포스틱스왑"], "ANTISEPTIC_SWAB", "소독스왑(성분 미특정)"),
    (["침(", "동방침", "한방침", "이침", "지압침", "호침", "침 0", "침0", "침"], "ACUPUNCTURE_NEEDLE", "침(한방/이침)"),
    (["생리식염수", "포도당가생리식염", "멸균증류수", "증류수", "크린조"], "IV_FLUID_SALINE", "생리식염수/수액류"),
    (["하트만"], "IV_FLUID_RINGERS_LACTATE", "하트만액(젖산링거액)"),
    (["과산화수소"], "HYDROGEN_PEROXIDE", "과산화수소 소독액"),
    (["불소"], "FLUORIDE_DENTAL", "불소 도포/양치 용액"),
    (["당화혈색소", "hba1c", "혈당", "콜레스테롤", "당뇨스틱"], "BLOOD_GLUCOSE_LIPID_TEST_STRIP", "혈당/지질 검사지·카트리지(브랜드 미특정)"),
    (["ss agar", "agar", "수송배지", "배양배지", "이동배지", "운반배지"], "CULTURE_MEDIUM", "미생물 배양·수송배지"),
    (["큐벳"], "LAB_CUVETTE", "검사용 큐벳"),
    (["yellow tip"], "LAB_PIPETTE_TIP", "피펫 팁"),
    (["cbc bottle", "sst tube"], "BLOOD_COLLECTION_TUBE", "채혈관"),
    (["gpt", "got", "bun", "creatinine", "t-bilirubin", "t-cholesterol",
      "hdl-cholesterol", "uric acid", "alp", "ggt", "r-gtp", "rpr", "ppd",
      "hbs ag", "hbs ab", "tg"], "CLINICAL_CHEMISTRY_TEST", "임상화학 검사 항목(시약 아님, 검사명)"),
    (["콘돔"], "CONDOM", "콘돔"),
    (["기피제"], "INSECT_REPELLENT", "해충기피제"),
    (["마스크"], "FACE_MASK", "마스크(보건용/일회용)"),
    (["틀니세정제", "의치세정제"], "DENTURE_CLEANSER", "틀니(의치) 세정제"),
    (["유산균"], "PROBIOTICS", "유산균(브랜드 미특정)"),
    (["파스"], "TOPICAL_PATCH_UNSPECIFIED", "파스(성분 미특정 — 개별 확인 필요)"),
    (["경방", "정우", "작약감초탕", "오적산", "쌍화탕"], "HANBANG_MIXED_HERBAL_EXTRACT", "한방 혼합단미엑스제(단일 성분 아님)"),
    (["영양죽", "뉴케어", "영양식", "일반환자식"], "MEDICAL_NUTRITION_FORMULA", "환자용 영양식/영양죽"),
    (["영양제", "결핵영양제"], "NUTRITIONAL_SUPPLEMENT_GENERIC", "영양제(성분 미특정)"),
    (["철분제", "임산부철분"], "IRON_SUPPLEMENT", "철분제(브랜드 미특정)"),
    (["빈혈스틱", "빈혈스트립", "빈혈지"], "HEMOGLOBIN_TEST_STRIP", "빈혈(헤모글로빈) 검사지"),
    (["구충제"], "ANTHELMINTIC_GENERIC", "구충제(성분 미특정)"),
    (["일회용밴드", "반창고", "종이반창고"], "ADHESIVE_BANDAGE", "접착 밴드/반창고"),
    (["핫팩"], "PROMO_MATERIAL", "홍보/판촉/비품"),
    (["거즈", "네오드레싱", "드레싱세트", "드레싱키트", "드레싱밴드", "탑드레싱키트",
      "일회용드레싱", "1회용드레싱"], "GAUZE_BANDAGE_DRESSING_GENERIC", "거즈/드레싱(브랜드·성분 미특정)"),
    (["붕대", "밴드", "세라밴드", "아쿠아밴드", "모아랩밴드", "지혈밴드", "수밴드"],
     "GAUZE_BANDAGE_DRESSING_GENERIC", "붕대/밴드(브랜드·성분 미특정)"),
    (["메딕스"], "WOUND_DRESSING_NONWOVEN", "부직포+아크릴점착 창상피복재(브랜드 미확인)"),
    (["수액세트"], "IV_ADMIN_SET", "수액세트(투여기구)"),
    (["멸균면봉", "면봉"], "COTTON_GAUZE", "소독솜/거즈"),
    (["lancet"], "LANCET", "채혈용 란셋"),
    (["latex glove", "glove"], "MEDICAL_GLOVE", "의료용 장갑(라텍스/니트릴/폴리)"),
    (["syringe"], "DISPOSABLE_SYRINGE", "일회용 주사기"),
    (["일회용석션팁", "석션팁", "메디컷"], "SUCTION_TIP_MEDICAL_SUPPLY", "흡인팁/의료용 절개보조기구"),
    (["손소독제", "소독용에탄올", "소독용알코올", "살균소독제", "소독용알콜"],
     "ALCOHOL_ETHANOL_CHEMICAL", "에탄올 소독제"),
    (["휘발유", "경유"], "FUEL", "유류"),
    (["임신테스트기", "임신진단"], "PREGNANCY_TEST_KIT", "임신테스트기"),
    (["폐기물"], "MEDICAL_WASTE_SUPPLY", "의료폐기물 처리용품(용기/봉투/태그)"),
    (["포도당생리식염", "포도당가생리식염"], "IV_FLUID_SALINE", "생리식염수/수액류"),
    (["포비딘"], "POVIDONE_IODINE", "포비돈요오드 소독제/스왑"),
    (["소화제"], "DIGESTIVE_ENZYME", "복합소화효소제(브랜드 미특정)"),
    (["프로바이오틱스", "프리바이오틱스"], "PROBIOTICS", "유산균(브랜드 미특정)"),
    (["한방파프", "제일쿨파프"], "HANBANG_MIXED_HERBAL_EXTRACT", "한방 혼합 외용 파스(단일 성분 아님)"),
    (["빈혈제"], "IRON_SUPPLEMENT", "철분제"),
    (["빈혈검사지", "빈혈지"], "HEMOGLOBIN_TEST_STRIP", "빈혈(헤모글로빈) 검사지"),
    (["pt10 lipid test", "lipid test"], "LIPID_TEST_STRIP", "지질(콜레스테롤) 검사지"),
]

NONMATERIAL_KEYWORDS = [
    (["약봉투", "약포지", "약분포지", "투약병", "투약컵", "약포장지", "비닐약봉투",
      "자동약포장지", "비닐봉투", "약비닐봉투", "종이약봉투", "약봉지", "투약 봉투",
      "비닐가방", "비닐약봉지"], "PHARMACY_DISPENSING_SUPPLY", "조제/투약 포장용품"),
    (["엠블럼", "뱃지", "배지", "주차증", "주차표지", "차량증", "차량스티커",
      "가방고리", "스티커", "쿠폰", "임산부 안전벨트"], "MATERNAL_PROGRAM_PROMO", "임산부지원사업 홍보/식별용품"),
    (["수첩", "소책자", "리플렛", "리플릿", "약달력", "등록카드"], "PRINTED_MATERIAL", "인쇄물(수첩/안내책자)"),
    (["부채", "텀블러", "볼펜", "컬러링북", "장바구니", "에코백", "종이가방",
      "쇼핑백", "종이백", "타포린백", "타포린가방", "부직포가방", "리유저블백",
      "온습도계", "체중계", "활동량계", "손톱깎이", "효자손", "구급함", "마더박스",
      "건강관리키트", "위생용품세트", "건전지", "물병", "종이컵", "지팡이",
      "마사지", "지압", "악력기", "스트레칭", "우산", "담요", "자석스티커",
      "건강관리수첩", "건강수첩"], "PROMO_MATERIAL", "홍보/판촉/비품"),
    (["양말", "손싸개", "속싸개", "배냇저고리", "신생아내의", "오가닉 내의",
      "턱받이", "손수건", "가제손수건", "토시", "무릎보호대", "손목보호대",
      "허리보호대", "요실금팬티", "팬티형기저귀", "기저귀", "위생매트", "에이프런",
      "덧신"], "APPAREL_TEXTILE", "섬유/직물 용품"),
    (["물티슈", "티슈", "손소독티슈", "살균소독티슈", "소독티슈", "손세정제",
      "핸드워시", "비누", "핸드타올", "주방타올", "행주", "수세미", "손톱깍기", "물휴지"],
     "HYGIENE_TISSUE", "위생/청결 소모품"),
    (["칫솔", "치약", "치실", "혀클리너", "혀크리너", "치간칫솔", "치간치솔", "치솔"], "DENTAL_HYGIENE_CONSUMER", "구강위생 소비재"),
    (["이유식", "빨대컵", "젖병", "유축기", "모유저장팩", "수유패드", "치발기", "유축세트"], "INFANT_FEEDING_SUPPLY", "영유아 수유용품"),
    (["금연파이프", "아로마금연파이프", "아로팝", "향파이프", "파이프", "마우스피스",
      "co마우스피스"], "SMOKING_CESSATION_DEVICE", "금연보조 기구(니코틴 아님)"),
    (["자일리톨", "니코틴사탕", "은단", "이클립스", "호올스", "홀스", "임팩트",
      "엠오이칼", "민트캔디", "비빌캔디", "레모비타플러스", "니코틴껌", "금연껌"],
     "ORAL_CANDY_GUM", "구강용 캔디/껌류"),
    (["채변통", "객담통"], "SPECIMEN_CONTAINER", "검체용기"),
    (["체온계"], "THERMOMETER_DEVICE", "체온계(장비)"),
    (["혈당계"], "GLUCOSE_METER_DEVICE", "혈당측정기(장비)"),
    (["엠블럼", "앰블럼"], "MATERNAL_PROGRAM_PROMO", "임산부지원사업 홍보/식별용품"),
    (["가방", "홍보용"], "PROMO_MATERIAL", "홍보/판촉/비품"),
    (["수건", "손수건"], "APPAREL_TEXTILE", "섬유/직물 용품"),
    (["연고곽", "연고통", "약분포지", "틀니통"], "PHARMACY_DISPENSING_SUPPLY", "조제/투약 포장용품(용기)"),
    (["튼살크림", "바디로션", "풋크림", "보습제", "바세린", "바셀린", "핸드크림"],
     "SKINCARE_COSMETIC_TOPICAL", "화장품형 외용 스킨케어(의약품 아님)"),
    (["임신테스트기"], "PREGNANCY_TEST_KIT", "임신테스트기"),
    (["보냉백", "보냉가방", "보온보냉백", "보온보냉가방", "크린백", "위생팩",
      "지퍼백", "종이백", "가방(종이)", "장바구니", "쓰레기봉투", "종량제봉투",
      "쿨키스", "수매트", "미끄럼방지매트", "에어프런", "주방용품세트",
      "일회용베개커버", "일회용 미러", "우양산", "케어웰"], "PROMO_MATERIAL", "홍보/판촉/비품"),
    (["임신축하", "임신부 위생용품", "임신부 건강관리용품", "아장아장 위생용품",
      "임신부 위생용품 세트"], "PROMO_MATERIAL", "임산부지원사업 홍보/식별용품"),
    (["틀니용품세트"], "DENTURE_CLEANSER", "틀니(의치) 관리용품"),
    (["색연필"], "PROMO_MATERIAL", "홍보/판촉/비품"),
    (["비타민", "비타c", "비타 c"], "VITAMIN_UNSPECIFIED", "비타민(성분 미특정)"),
    (["물통", "홍보"], "PROMO_MATERIAL", "홍보/판촉/비품"),
    (["임산부", "산모"], "MATERNAL_PROGRAM_PROMO", "임산부지원사업 홍보/식별용품"),
    (["스트립"], "CLINICAL_CHEMISTRY_TEST", "검사스트립(항목 미특정)"),
    (["알코올솜", "알코올"], "ALCOHOL_ETHANOL_CHEMICAL", "에탄올 소독제"),
]


# ---------------------------------------------------------------------------
# 0-2) local_codes 안에 EXDG*/MTR* 접두사 코드가 있으면 전국 표준코드로 추정.
#    (2026-07-15 보건의료정보부 미팅 — "MTR, EXD 접두사 코드 정리 자료 확인 후
#    공유 예정"이라는 액션아이템에 대응해, 원본 데이터에서 직접 패턴을 확인함.
#    같은 대표품목 샘플 내에서 EXDG/MTR 코드가 전부 1:1로만 매칭돼 있어서
#    —기관마다 제각각인 USE코드와 달리— 전국 공통 표준코드일 가능성이 높다고
#    판단. 정확한 코드체계 의미는 보건의료정보부 확인 회신을 받아야 확정된다.)
# ---------------------------------------------------------------------------
NATIONAL_CODE_PREFIXES = {
    "EXDG": "체외진단(EXDG) 표준코드로 추정 — 보건의료정보부 확인 대기",
    "MTR": "자재(Material/MTR) 표준코드로 추정 — 보건의료정보부 확인 대기",
}


def national_standard_code_for(local_codes: str):
    if not local_codes:
        return ("", "")
    for code in local_codes.split(";"):
        code = code.strip()
        for prefix, note in NATIONAL_CODE_PREFIXES.items():
            if code.startswith(prefix):
                return (code, note)
    return ("", "")


def to_snake(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9가-힣]+", "_", s).strip("_").upper()


# ---------------------------------------------------------------------------
# 외부 브랜드사전(brand_dict_extra.tsv) 병합 — 큐 상위 미상 품목을 서브에이전트
# 리서치로 대량 확장한 결과다. 소스 리터럴을 비대하게 만들지 않으려고 런타임에
# TSV 로 읽어 BRAND_DICT 에 합친다. 같은 키워드면 외부(최신 리서치)가 우선하고,
# 매칭은 키워드 길이 내림차순으로 해서 더 구체적인 브랜드가 먼저 잡히게 한다.
# ---------------------------------------------------------------------------
def _load_brand_dict_all():
    extra_path = os.path.join(DATA_DIR, "brand_dict_extra.tsv")
    by_kw = {}
    for kw, fid, disp, basis, ev in BRAND_DICT:
        by_kw[kw] = (kw, fid, disp, basis, ev)
    try:
        with open(extra_path, encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for r in reader:
                kw = r["keyword"].strip()
                if not kw:
                    continue
                by_kw[kw] = (kw, r["family_id"].strip(), r["display"].strip(),
                             r["basis"].strip(), r["evidence"].strip())
    except FileNotFoundError:
        pass
    # 정렬: (1) 성분이 특정된 항목(basis!=unresolved)을 먼저 시도 — 예전 하드코딩된
    # 'UNCONFIRMED/unresolved' 플레이스홀더가 더 긴 키워드라는 이유로 새로 리서치된
    # 짧은 키워드를 가리는 것을 방지. (2) 같은 그룹 내에선 키워드 길이 내림차순.
    return sorted(by_kw.values(), key=lambda e: (e[3] == "unresolved", -len(e[0])))


BRAND_DICT_ALL = _load_brand_dict_all()


# ---------------------------------------------------------------------------
# family_id 한글->영어 정규화. 상품명 괄호에서 추출된 성분명(레보드로프로피진 등)이나
# 사전의 한글 family_id 가 메타코드로 그대로 나가면 코드 체계가 한/영 혼재된다.
# ingredient_ko_en.tsv(서브에이전트가 만든 한글성분명->영어 INN 매핑)로 전부 영어화한다.
# 매핑에 없으면 원본 유지(다음 라운드에 TSV 에 추가).
# ---------------------------------------------------------------------------
def _load_ko_en():
    path = os.path.join(DATA_DIR, "ingredient_ko_en.tsv")
    m = {}
    try:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                ko, en = r["korean"].strip(), r["english"].strip()
                if ko and en:
                    m[ko] = en
    except FileNotFoundError:
        pass
    return m


KO_EN = _load_ko_en()
_HANGUL = re.compile(r"[가-힣]")

# 규격·색상·용기·제조사 표기를 성분으로 승격하지 않는다.
NON_INGREDIENT_KO = {
    ko for ko, en in KO_EN.items()
    if en in ("NON_INGREDIENT_SPEC", "MANUFACTURER_NAME_NOISE")
}


# 이름 전체의 문맥이 짧은 브랜드/성분 부분문자열보다 명백한 경우 먼저 적용한다.
# 이 규칙은 어제 원격 실데이터 감사에서 확인한 교차 도메인 오분류를 막는다.
CONTEXT_FAMILY_RULES = [
    (
        re.compile(r"주사(?:기)?\s*(?:침|바늘)\s*(?:(?:폐기|수거)(?:물)?)?\s*통|(?:needle|니들)\s*(?:box|통)|의료폐기물", re.I),
        "MEDICAL_WASTE_CONTAINER",
        "의료폐기물 전용용기",
        "폐기·수거 용기 문맥이므로 주사침/한방침으로 분류하지 않음",
    ),
    (
        re.compile(r"산소.{0,12}마스크|마스크.{0,8}산소|oxygen\s*mask", re.I),
        "MEDICAL_MASK",
        "산소마스크",
        "마스크 문맥이므로 의료용 산소가스/FUEL로 분류하지 않음",
    ),
    (
        re.compile(
            r"(?:알코올|알콜).{0,14}(?:솜|스?왑|패드|스폰지|코튼)"
            r"|alcohol.{0,14}(?:swab|pad|sponge|cotton)",
            re.I,
        ),
        "ALCOHOL_SWAB",
        "알코올스왑",
        "알코올 솜·스왑 문맥을 제품 브랜드보다 우선",
    ),
    (
        re.compile(
            r"채혈(?:침|핀)|혈당(?:검사)?침|란셋|난셋|랜싯|"
            r"(?:auto\s*)?lancet(?:te)?s?|lansets?|safelan|worldlet",
            re.I,
        ),
        "BLOOD_LANCET",
        "채혈침",
        "채혈침/란셋 문맥을 혈당검사지 또는 의약품 브랜드보다 우선",
    ),
    (
        re.compile(r"(?:혈당|당뇨).*?(?:스틱|스트립|검사지|시험지)|blood\s*glucose.*strip", re.I),
        "BLOOD_GLUCOSE_TEST_STRIP",
        "혈당검사지",
        "검사지·스트립 형태가 명시된 혈당검사 소모품",
    ),
    (
        re.compile(r"주사(?:기)?\s*(?:침|바늘)|hypodermic\s*needle", re.I),
        "INJECTION_NEEDLE",
        "주사침",
        "주사용 바늘 문맥이므로 한방침과 분리",
    ),
]


GENERIC_SUFFIX_RULES = [
    (re.compile(r"사르탄"), "ANGIOTENSIN_RECEPTOR_BLOCKER", "ARB(사르탄계)"),
    (re.compile(r"디핀"), "DIHYDROPYRIDINE_CCB", "디하이드로피리딘 CCB(디핀계)"),
    (re.compile(r"스타틴"), "STATIN", "스타틴계"),
    (re.compile(r"프라졸"), "PROTON_PUMP_INHIBITOR", "PPI(프라졸계)"),
    (re.compile(r"글립틴"), "DPP4_INHIBITOR", "DPP-4 억제제(글립틴계)"),
    (re.compile(r"플록사신"), "FLUOROQUINOLONE", "퀴놀론계(플록사신)"),
    (re.compile(r"세파|세프(?![트티])"), "CEPHALOSPORIN", "세팔로스포린계"),
    (re.compile(r"프로펜"), "PROPIONIC_ACID_NSAID", "프로피온산 NSAID(프로펜계)"),
]
MEDICATION_GROUPS = {"MED_ORAL", "MED_INJECT", "MED_TOPICAL"}


FAMILY_EQUIVALENCE = {
    "LANCET": "BLOOD_LANCET",
    "BLOOD_LANCET": "BLOOD_LANCET",
    "FACE_MASK": "MEDICAL_MASK",
    "MEDICAL_MASK": "MEDICAL_MASK",
    "COTTON_GAUZE": "MEDICAL_GAUZE",
    "GAUZE_BANDAGE_DRESSING_GENERIC": "MEDICAL_GAUZE",
    "MEDICAL_GAUZE": "MEDICAL_GAUZE",
    "HYPODERMIC_NEEDLE": "INJECTION_NEEDLE",
    "INJECTION_NEEDLE": "INJECTION_NEEDLE",
    "IV_ADMIN_SET": "INFUSION_SET",
    "IV_ADMINISTRATION_SET": "INFUSION_SET",
    "INFUSION_SET": "INFUSION_SET",
    "IV_CATHETER_ANGIONEEDLE": "ANGIO_CATHETER",
    "ANGIO_CATHETER": "ANGIO_CATHETER",
    "STERILIZATION_PACKAGING_EO": "EO_STERILIZATION_PACKAGING",
    "EO_STERILIZATION_PACKAGING": "EO_STERILIZATION_PACKAGING",
    "DIALYSIS_FLUID_CONTAINER": "DIALYSATE_CONTAINER",
    "DIALYSATE_CONTAINER": "DIALYSATE_CONTAINER",
    "MEDICAL_WASTE_SUPPLY": "MEDICAL_WASTE_CONTAINER",
    "MEDICAL_WASTE_CONTAINER": "MEDICAL_WASTE_CONTAINER",
}


def normalize_fid(fid: str) -> str:
    if fid in KO_EN:
        fid = KO_EN[fid]
    fid = FAMILY_EQUIVALENCE.get(fid, fid)
    # 매핑에 없지만 한글이 남아있으면 표시만(다음 라운드 큐로), 코드값은 유지
    return fid


def context_family_classify(name: str):
    normalized = re.sub(r"\s+", "", name.lower())
    if "혈당" in normalized or "당뇨" in normalized or "glucose" in normalized:
        components = [
            bool(re.search(r"스틱|스트립|검사지|시험지|strip", normalized, re.I)),
            bool(re.search(r"란셋|난셋|랜싯|lancet|lanset", normalized, re.I)),
            bool(re.search(r"알코올|알콜|alcohol", normalized, re.I)),
        ]
        if sum(components) >= 2:
            if "혈당계" in normalized or "glucosemeter" in normalized:
                return (
                    "BLOOD_GLUCOSE_METER_KIT",
                    "혈당측정기·소모품 세트",
                    "context_explicit_rule",
                    "혈당계와 둘 이상의 소모품(검사지·란셋·알코올솜)이 함께 명시된 복합 세트",
                )
            return (
                "BLOOD_GLUCOSE_TESTING_SET",
                "혈당측정 소모품 세트",
                "context_explicit_rule",
                "둘 이상의 혈당측정 소모품(검사지·란셋·알코올솜)이 함께 명시된 복합 세트",
            )
    for pattern, fid, display, note in CONTEXT_FAMILY_RULES:
        if pattern.search(name):
            return fid, display, "context_explicit_rule", note
    return None


def classify(name: str, item_group_id: str = ""):
    lname = name.lower()

    official = official_standard_classify(name)
    if official:
        return official

    contextual = context_family_classify(name)
    if contextual:
        return contextual

    lit = extract_literal_ingredient(name)
    if lit and lit in NON_INGREDIENT_KO:
        lit = None
    if lit:
        for fid, disp, subs in SUBSTRING_INGREDIENTS:
            for s in subs:
                if s in lit:
                    return (fid, disp, "name_literal_parenthetical", f"원본에 (${lit}) 명시, '{s}' 로 정규화")
        return (to_snake(lit), lit, "name_literal_parenthetical", "원본 상품명에 이미 명시된 성분명 그대로 사용")

    for fid, disp, subs in SUBSTRING_INGREDIENTS:
        for s in subs:
            if s in name:
                return (fid, disp, "name_literal_substring", f"상품명에 '{s}' 직접 포함")

    for kw, fid, disp, basis, evidence in BRAND_DICT_ALL:
        if kw.lower() in lname:
            return (fid, disp, basis, evidence)

    # 새 정본의 제네릭 접미사 규칙은 약품 대분류에서만 사용한다. 정확 성분이 아니라
    # 치료계열 후보이므로 별도 basis로 남겨 원재료 코드로 승격되지 않게 한다.
    if item_group_id in MEDICATION_GROUPS:
        for pattern, fid, disp in GENERIC_SUFFIX_RULES:
            if pattern.search(name):
                return (
                    fid,
                    disp,
                    "naming_pattern_unverified",
                    f"제네릭 접미사 '{pattern.pattern}' 기반 치료계열 후보; 성분 확정 아님",
                )

    for kws, fid, disp in FUNCTIONAL_KEYWORDS:
        for k in kws:
            if k.lower() in lname:
                return (fid, disp, "functional_keyword", f"'{k}' 키워드로 기능 분류")

    for kws, fid, disp in NONMATERIAL_KEYWORDS:
        for k in kws:
            if k.lower() in lname:
                return (fid, disp, "non_material_category", f"'{k}' 키워드로 비우선 분류")

    return ("UNSPECIFIED_ITEM", "품목 미상 — 개별 확인 필요", "unresolved", "규칙/사전으로 판단 불가, 검색으로도 특정 실패")


def _text(row, key):
    return str(row.get(key, "") or "").strip()


def _single_code(value):
    codes = [code.strip() for code in str(value or "").split(";") if code.strip()]
    codes = [code for code in codes if not code.startswith("UNMAPPED::")]
    return codes[0] if len(set(codes)) == 1 else ""


def _canonical_family(fid):
    return FAMILY_EQUIVALENCE.get(fid, fid)


def resolve_family(row, rule_result):
    rule_fid, rule_name, rule_basis, rule_evidence = rule_result
    verified_fid = _text(row, "verified_item_family_id")
    verified_name = _text(row, "verified_standard_family_name")
    local_fid = _text(row, "item_family_id_candidate")
    local_name = _text(row, "standard_family_name_candidate")
    candidate_status = _text(row, "candidate_status")
    verified_ingredient = ""
    if _text(row, "material_match_readiness") == "verified_ingredient_ready":
        verified_ingredient = _single_code(_text(row, "ingredient_ids"))

    source = "name_rule"
    selected_fid, selected_name = rule_fid, rule_name
    basis, evidence = rule_basis, rule_evidence
    review_status = "needs_review" if rule_basis != "unresolved" else "unresolved_needs_research"

    authoritative_name_rule = rule_basis in {
        OFFICIAL_TABLE_BASIS,
        "context_explicit_rule",
    }

    if verified_fid:
        source = "verified_structured_family"
        selected_fid = verified_fid
        selected_name = verified_name or local_name or verified_fid
        basis = "verified_external_dictionary"
        evidence = "검증된 외부 근거 사전: " + (_text(row, "verified_dictionary_ids") or "record available")
        review_status = "verified_family_evidence"
    elif verified_ingredient:
        source = "verified_ingredient_dictionary"
        selected_fid = verified_ingredient
        selected_name = _text(row, "ingredient_names") or verified_ingredient
        basis = "verified_ingredient_dictionary"
        evidence = "공식 근거 사전으로 검증된 단일 유효성분"
        review_status = "verified_family_evidence"
    elif authoritative_name_rule:
        source = (
            "official_standard_rule"
            if rule_basis == OFFICIAL_TABLE_BASIS
            else "context_explicit_rule"
        )
    elif local_fid and candidate_status != "candidate_conflict":
        source = "local_structured_family"
        selected_fid = local_fid
        selected_name = local_name or local_fid
        basis = "local_structured_family"
        evidence = "raw_stock 명칭의 품목·세부유형·규격 구조화 규칙에서 생성된 family 후보"

    rule_is_unknown = rule_fid in {"", "UNSPECIFIED_ITEM"} or rule_basis == "unresolved"
    conflict = False
    structured_override = (
        source in {"official_standard_rule", "context_explicit_rule"}
        and bool(local_fid)
        and _canonical_family(local_fid) != _canonical_family(selected_fid)
    )
    if structured_override:
        conflict = True
        resolution = f"family_conflict_{source}_preferred"
    elif source == "name_rule":
        resolution = "unresolved" if rule_is_unknown else "name_rule_only"
    elif rule_is_unknown:
        resolution = f"{source}_only"
    elif _canonical_family(selected_fid) == _canonical_family(rule_fid):
        resolution = f"{source}_name_rule_agree"
    else:
        conflict = True
        resolution = f"family_conflict_{source}_preferred"

    conflict_reason = ""
    if structured_override:
        conflict_reason = (
            f"structured={local_fid}; explicit_context={selected_fid}; "
            "복합세트·명시 물품 문맥을 우선하고 기존 구조화 결과는 검토 후보로 보존"
        )
    elif conflict:
        conflict_reason = (
            f"structured={selected_fid}; name_rule={rule_fid}; "
            "구조화 family를 유지하고 이름 규칙 결과는 검토 후보로만 보존"
        )

    # 구조화 결과가 최종 선택되더라도 공식 웹 근거가 있는 이름 규칙은 버리지 않는다.
    # item_classification 단계가 evidence_note에서 NEDrug 품목기준코드를 수집하므로,
    # 보조 근거를 함께 남겨 후속 검증이 계속 작동하게 한다.
    if source != "name_rule" and rule_basis == SEARCHED_TODAY and rule_evidence:
        evidence = f"{evidence}; 보조 이름 규칙 근거: {rule_evidence}"

    return {
        "item_family_id_suggested": selected_fid,
        "standard_family_name_suggested": selected_name,
        "family_basis": basis,
        "evidence_note": evidence,
        "family_source": source,
        "family_resolution_status": resolution,
        "family_conflict_flag": "true" if conflict else "false",
        "family_conflict_reason": conflict_reason,
        "name_rule_item_family_id": rule_fid,
        "name_rule_standard_family_name": rule_name,
        "name_rule_family_basis": rule_basis,
        "name_rule_evidence_note": rule_evidence,
        "family_review_status": review_status,
    }


# 같은 family_id 인데 소스별로 표시명이 갈린 것들을 하나로 통일한다.
# (가장 먼저 등록된 표기가 대개 최다 사용례라 그걸 기준으로 삼았다)
NAME_CANON = {
    "NICOTINE_REPLACEMENT": "니코틴 대체제(껌/패치)",
    "IRON_SUPPLEMENT": "철분제",
    "PROBIOTICS": "유산균",
    "KETOPROFEN": "케토프로펜(파스/겔)",
    "CLOTRIMAZOLE": "클로트리마졸",
    "FUSIDIC_ACID": "퓨시드산",
    "MAGNESIUM_HYDROXIDE": "수산화마그네슘",
    "INSECT_BITE_TOPICAL_UNCONFIRMED": "충교상 외용제(성분 미확인)",
    "ADHESIVE_BANDAGE": "접착 밴드/반창고",
    "PROMO_MATERIAL": "홍보/판촉/비품",
    "BLOOD_GLUCOSE_TEST_STRIP": "혈당검사지",
    "BLOOD_LANCET": "채혈침",
    "INJECTION_NEEDLE": "주사침",
    "MEDICAL_MASK": "마스크",
    "MEDICAL_GAUZE": "의료용 거즈",
    "MEDICAL_WASTE_CONTAINER": "의료폐기물 전용용기",
    "INFUSION_SET": "수액세트",
    "ANGIO_CATHETER": "카테터",
    "EO_STERILIZATION_PACKAGING": "멸균포장재",
    "BLOOD_GLUCOSE_TESTING_SET": "혈당측정 소모품 세트",
    "BLOOD_GLUCOSE_METER_KIT": "혈당측정기·소모품 세트",
}


def main():
    with open(IN_FILE, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError("입력 데이터가 비어 있습니다")

    out_rows = []
    stats = {}
    for r in rows:
        name = r["representative_name"]
        rule_fid, rule_disp, rule_basis, rule_evidence = classify(
            name, _text(r, "item_group_id_candidate")
        )
        rule_fid = normalize_fid(rule_fid)
        rule_disp = NAME_CANON.get(rule_fid, rule_disp)
        resolved = resolve_family(
            r, (rule_fid, rule_disp, rule_basis, rule_evidence)
        )
        fid = normalize_fid(resolved["item_family_id_suggested"])
        resolved["item_family_id_suggested"] = fid
        resolved["standard_family_name_suggested"] = NAME_CANON.get(
            fid, resolved["standard_family_name_suggested"]
        )
        basis = resolved["family_basis"]
        dosage_id, dosage_name = dosage_form_for(name)
        nat_code, nat_note = national_standard_code_for(r.get("local_codes", ""))
        stats[basis] = stats.get(basis, 0) + 1
        output = dict(r)
        output.update(resolved)
        output.update({
            "dosage_form_suggested": dosage_name,
            "national_standard_code": nat_code,
            "national_standard_code_note": nat_note,
            "retrieved_at": TODAY if rule_basis == SEARCHED_TODAY else _text(r, "retrieved_at"),
            "family_retrieved_at": TODAY if rule_basis == SEARCHED_TODAY else "",
            "family_pipeline_version": PIPELINE_VERSION,
        })
        out_rows.append(output)

    fieldnames = list(out_rows[0].keys())
    with open(OUT_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    # ------------------------------------------------------------------
    # unresolved 우선순위 큐. 정렬키를 usage_sum -> occurrence_count(발주/거래 흔적)로
    # 바꿨다: 이 데이터셋의 usage_sum 은 활성 품목에서도 결손이 많아(예: 마우스피스
    # occ 5767·60개기관인데 usage 0), 사용량으로 정렬하면 진짜 자주 쓰는 미식별
    # 품목이 큐 바닥에 깔린다. occurrence_count(정규화 등장·거래 흔적)와
    # institution_count(사용 기관 수)가 훨씬 신뢰할 만한 활동성 지표다.
    # 1회 등장·단일기관(occ==1 & inst<=1) 은 일회성 노이즈로 보고 scope=one_off 로
    # 분리 — 예측대상서 제외 후보라 큐 하단으로 내린다.
    # ------------------------------------------------------------------
    def numf(row, k):
        try:
            return float(row.get(k) or 0)
        except ValueError:
            return 0.0

    def scope_of(row):
        occ, inst = numf(row, "occurrence_count"), numf(row, "institution_count")
        return "one_off" if (occ <= 1 and inst <= 1) else "active"

    unresolved_rows = [r for r, o in zip(rows, out_rows) if o["family_basis"] == "unresolved"]
    # 활성 먼저, 그 안에서 발주흔적(occurrence_count) 큰 순
    unresolved_rows.sort(key=lambda r: (scope_of(r) == "active", numf(r, "occurrence_count"),
                                        numf(r, "institution_count")), reverse=True)
    queue_rows = []
    for r in unresolved_rows:
        queue_rows.append({
            "scope": scope_of(r),
            "occurrence_count": r.get("occurrence_count", ""),
            "institution_count": r.get("institution_count", ""),
            "usage_sum": r.get("usage_sum", ""),
            "representative_item_id": r.get("representative_item_id", ""),
            "representative_name": r.get("representative_name", ""),
            "item_group_id_candidate": r.get("item_group_id_candidate", ""),
        })
    with open(QUEUE_FILE, "w", encoding="utf-8-sig", newline="") as f:
        cols = ["scope", "occurrence_count", "institution_count", "usage_sum",
                "representative_item_id", "representative_name", "item_group_id_candidate"]
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(queue_rows)
    n_active = sum(1 for q in queue_rows if q["scope"] == "active")
    print(f"\n미식별 큐: 활성 {n_active}건 / 일회성노이즈 {len(queue_rows)-n_active}건")

    print("총 처리:", len(out_rows))
    print("\n=== family_basis 분포 ===")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {k:32s} {v:4d}")
    n_unresolved = stats.get("unresolved", 0)
    print(f"\nUNRESOLVED: {n_unresolved} / {len(out_rows)} ({n_unresolved/len(out_rows)*100:.1f}%)")
    print("저장:", OUT_FILE)
    print("저장(우선순위 큐):", QUEUE_FILE)


if __name__ == "__main__":
    main()
