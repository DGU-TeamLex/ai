#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
item_supply_clusters_final.csv 의 24개 클러스터(+기타 세부)에 실제 인터넷 검색으로
확인한 원자재·공급측 이벤트유형·수요측 이벤트유형을 매핑한다.

원자재는 "성분명"이 아니라 "뉴스에 나올 법한 공급망 리스크 단위"로 잡았다
(예: 아세트아미노펜 -> 원료의약품 수입 의존, 이거로 원자재 매핑 대상은 아니지만
파스/겔류 등 소모품은 석유화학 원자재까지). 공급 이벤트와 수요 이벤트는 서로
다른 인과경로이므로 분리했다(ai#12 원칙과 동일).

근거는 대부분 2026-07-15 세션 검색 결과. 특히 2026-04 나프타 쇼크(중동 정세發
플라스틱 원자재 가격 급등, 국내 주사기·수술장갑 가격 30~75% 상승 실사례)와
원료의약품 중국·인도 수입의존도 50%+ 통계가 핵심 근거.
"""
import csv
import sys

if len(sys.argv) != 5:
    raise SystemExit(
        "usage: build_material_events.py <input.csv> <output.csv> "
        "<glossary.csv> <reverse_index.md>"
    )

IN_FILE = sys.argv[1]
OUT_FILE = sys.argv[2]
GLOSSARY_FILE = sys.argv[3]
REVERSE_INDEX_FILE = sys.argv[4]
TODAY = "2026-07-18"
PIPELINE_VERSION = "combined-material-v2.1"

# cluster_id -> (원자재, 근거, 공급측 이벤트유형, 수요측 이벤트유형)
CLUSTER_MATERIAL_EVENTS = {
    "PAIN_NSAID": (
        "원료의약품(합성 API, 수입 의존) + 파스/겔 포장재(PP·알루미늄)",
        "원료의약품 자급률 11.9~25.6%, 중국·인도 비중 50%+ — https://www.newsis.com/view/NISX20251015_0003363610 ; 코로나 시기 아세트아미노펜 품귀·PTP포장재 수급난 — 데일리팜",
        "중국/인도 원료의약품 수출규제·공장가동중단; 나프타發 플라스틱 포장재(PTP) 가격급등",
        "감염병 유행(발열환자 급증); 겨울철 감기유행; 폭염(온열질환)",
    ),
    "ANTIHISTAMINE_ALLERGY": (
        "원료의약품(수입 의존)",
        "원료의약품 중국·인도 의존도 50%+ — https://www.newsis.com/view/NISX20251015_0003363610",
        "중국/인도 원료의약품 수출규제·공장가동중단",
        "황사·미세먼지 계절, 꽃가루 알레르기 시즌, 감염병 유행",
    ),
    "GI_DIGESTIVE": (
        "원료의약품(합성 API, 수입 의존) — 제산제(알루미늄/마그네슘 광물)·소화효소제(동물/미생물유래)는 원료가 다름, 하단 개별 family 표 참고",
        "게루삼/마그밀 성분 확인(광물성 무기화합물) — 2026-07-15 검색",
        "광물 원자재 가격변동; 효소 생산공장 이슈; 원료의약품 수입 차질",
        "명절 연휴(과식); 여름철 식중독 유행",
    ),
    "RESPIRATORY": (
        "원료의약품(수입 의존)",
        "원료의약품 중국·인도 의존도 50%+",
        "중국/인도 원료의약품 수출규제·공장가동중단",
        "독감·코로나 등 호흡기 감염병 유행(가장 직접적 트리거); 미세먼지·황사 시즌",
    ),
    "ANTIBIOTIC": (
        "원료의약품(특히 마크로라이드계는 90% 중국·인도산)",
        "클래리트로마이신 원료 중국·인도 90% — https://www.dailypharm.com/user/news/339863 ; 국내 등록 원료의약품 545개 중 72%가 중국·인도산",
        "중국/인도 수출규제; 원료 불순물 이슈로 인한 공급중단(실제 크라비트정 등 장기품절 사례)",
        "세균감염 유행; 감염병 유행 후 이차감염 증가",
    ),
    "ANTITUBERCULAR": (
        "원료의약품(오래된 제네릭, 수입 의존)",
        "원료의약품 전반 중국·인도 의존 구조와 동일 패턴으로 추정",
        "중국/인도 원료의약품 수출규제",
        "결핵관리 프로그램 등록자 수(정책 요인이 뉴스 요인보다 큼); 집단감염 발생 뉴스",
    ),
    "VITAMIN_NUTRITION": (
        "비타민 원료(합성비타민이 전체의 80%, 중국 기업 집중 생산) + 철분(광물, 철염화합물)",
        "합성비타민 비중 80%, 화위안셩우·신허청 등 중국기업이 주요 생산 — 2026-07-15 검색(KOTRA 등)",
        "중국 비타민 원료공장 환경규제·가동중단(반복적으로 있었던 패턴)",
        "계절/캠페인성(면역력 강조 시즌); 임산부 지원사업 정책 확대",
    ),
    "CARDIOVASCULAR_METABOLIC": (
        "원료의약품(암로디핀 등 제네릭, 수입 의존)",
        "원료의약품 중국·인도 의존도 50%+",
        "중국/인도 원료의약품 수출규제",
        "급성 뉴스 트리거 약함 — 인구고령화에 따른 구조적 수요 증가가 주된 요인",
    ),
    "DERM_TOPICAL": (
        "원료의약품 + 연고기제(바세린/파라핀 등 석유화학 유래)",
        "2026-04 나프타 쇼크 — https://www.ebn.co.kr/news/articleView.html?idxno=1704225",
        "나프타/원유 가격급등(중동 정세)",
        "계절성 피부질환(여름 무좀·습진, 겨울 건조증); 수족구 등 감염병 유행",
    ),
    "ANESTHETIC_MUSCLE": (
        "원료의약품(수입 의존)",
        "원료의약품 중국·인도 의존도 50%+",
        "중국/인도 원료의약품 수출규제",
        "특별한 뉴스 트리거 약함 — 시술/처치 건수에 연동",
    ),
    "ANTISEPTIC_DISINFECT": (
        "에탄올(곡물/사탕수수 발효 또는 석유화학) — 포비돈요오드/클로르헥시딘/과산화수소는 원료가 전혀 다름, 하단 개별 family 표 참고",
        "에탄올 생산방식별 원자재 — https://ko.wikipedia.org/wiki/에탄올 ; 곡물가격 급등 시 바이오에탄올 원가 상승",
        "곡물(옥수수 등) 가격급등; 나프타 가격급등(석유화학 에탄올 경로)",
        "감염병 대유행 뉴스 = 손소독제 수요 폭증의 대표 사례(코로나 시기 품귀 반복)",
    ),
    "IV_FLUID": (
        "염화나트륨(소금) + 포도당(옥수수전분 유래) — 국내 제조 자체가 2개사 과점(JW중외 50%+대한약품 30%=80%)이라 이게 최대 리스크",
        "국내 수액제 시장 JW중외제약 약50%, 대한약품 약30% 과점 — 2026-07-15 검색(메디칼업저버 등)",
        "곡물가격 변동; **국내 2개사 중 한 곳이라도 가동중단되면 전체 공급의 절반 이상 타격**(원자재보다 제조사 집중도 자체가 리스크)",
        "대형 재해·사고(다수 부상자); 감염병 유행(수분보충 수요)",
    ),
    "VACCINE": (
        "생물학적 원료(세포주 배양, mRNA는 지질나노입자 등 특수원료) — 국내는 완제 충전·포장 중심, 원액은 해외 의존",
        "삼성바이오로직스 모더나 위탁생산은 원액 아닌 충전·포장 단계만 담당 — https://www.korea.kr/news/policyNewsView.do?newsId=148887725",
        "해외 원액 생산국 상황; 국제 물류(콜드체인)",
        "신종 감염병 발생 뉴스 = 백신 수요의 가장 직접적 트리거",
    ),
    "LAB_DIAGNOSTIC": (
        "혈당 등 전기화학식 검사스트립은 탄소/그래파이트 작동전극+은/염화은 기준전극이 저가형 주류(일부 고급형은 금·백금 도금) — 면역진단키트·배지·용기·기기류는 원료가 전혀 다름, 하단 개별 family 표 참고",
        "바이오센서 전극 재질(금/백금/은/은-염화은/탄소/그래파이트/ITO/팔라듐 등) — 국내 특허문헌 다수, 2026-07-15 검색 https://patents.google.com/patent/WO2013180528A1/ko ; 상용 저가 스트립은 스크린프린팅 탄소 작동전극+Ag/AgCl 기준전극 조합이 원가상 주류",
        "귀금속(은 등) 가격변동; 정밀부품 공급망 이슈(반도체류와 유사)",
        "당뇨 등 만성질환 관리 추세; 신종 감염병 진단키트 수요 급증(코로나 사례)",
    ),
    "INJECTION_PHLEBOTOMY": (
        "폴리프로필렌(주사기 배럴) + 스테인리스강(바늘)",
        "2026-04 나프타 쇼크로 3cc 주사기 100개 상자가 5천원대→8,900원까지 급등 — https://www.mt.co.kr/thebio/2026/04/25/2026042421113895803",
        "나프타/원유 가격급등(중동 정세) — 실제 최근(2026-04) 국내 가격 급등 사례 확인됨",
        "예방접종 캠페인; 감염병 유행 시 채혈·시술 증가",
    ),
    "WOUND_CARE": (
        "부직포(폴리프로필렌 계열) + 폴리우레탄 + 하이드로콜로이드 + 아크릴점착제 — 전부 석유화학 유래",
        "2026-04 나프타 쇼크, PP 46%·PVC 27%·포장지 22% 인상 — https://www.mt.co.kr/thebio/2026/04/24/2026042310490892913",
        "나프타/원유 가격급등(중동 정세)",
        "대형 재해·사고(다수 부상자); 계절성은 낮음",
    ),
    "GLOVE_PPE": (
        "천연고무 라텍스(태국·인도네시아·말레이시아 생산 집중) 또는 니트릴 합성고무(부타디엔+아크릴로니트릴, 석유화학) + 마스크는 PP 부직포(멜트블로운)",
        "말레이시아 천연라텍스 생산·수급 구조 — KOTRA ; 2026-04 나프타 쇼크로 수술장갑 상자당 27,000원→35,000~38,000원(30~40%) 급등 — https://www.mt.co.kr/thebio/2026/04/25/2026042421113895803",
        "동남아 천연고무 생산국 기후/정세; 나프타/원유 가격급등(니트릴·마스크)",
        "감염병 대유행 뉴스 = 장갑·마스크 수요 폭증의 대표 사례(코로나 마스크 대란과 동일 패턴)",
    ),
    "ORAL_DENTAL": (
        "치약: 실리카·탄산칼슘(연마제, 광물) + SLS계열 계면활성제(석유화학) / 칫솔: PP+나일론모 / 불소화합물(광물)",
        "치약 3대 성분(불소·연마제·계면활성제) 확인, 연마제는 광물(실리카·탄산칼슘), 계면활성제는 SLS 등 석유화학 — 2026-07-15 검색",
        "광물(실리카 등) 가격; 나프타 가격(SLS·칫솔 플라스틱)",
        "특별한 뉴스 트리거 약함; 학교 구강보건사업 시즌",
    ),
    "SMOKING_CESSATION": (
        "니코틴 — 담뱃잎 추출보다 합성이 더 경제적이라 합성 비중 확대 추세, 중국산 니코틴대체물질 수입 사례 있음",
        "합성이 담뱃잎 추출보다 경제적, 중국 제약사 개발 니코틴대체물질 국내 수입 사례 — https://www.khan.co.kr/article/202501140600011",
        "담뱃잎 작황(추출형) 또는 화학원료 수급(합성형); 중국發 원료 수입 이슈",
        "금연 캠페인 정책, 금연클리닉 등록 시즌(뉴스보다 정책 이벤트)",
    ),
    "HANBANG": (
        "한약재 — 품목별 원산지 상이(감초는 전량수입/중국·몽골, 녹용은 러시아, 육두구는 인도네시아 등), 전반적으로 중국 의존도 심화 추세",
        "감초 전량수입(중국 등 한랭지역), 녹용 러시아산, 육두구 인도네시아산 — 2026-07-15 검색(헬스경향 등)",
        "중국 약재 수출입 이슈; 러시아·인도네시아 등 원산지국 정세; 기후에 따른 작황 변화",
        "환절기 보약 수요; 명절",
    ),
    "PEST_CONTROL": (
        "피레스로이드계 화학 살충성분(제충국 성분을 모방한 합성 화학물질, 석유화학 유래) — 특정 생산국 집중도는 확인 안 됨",
        "피레스로이드 시장은 북미가 주도(생산국 특정보다 범용 화학원료 성격) — 2026-07-15 검색, 생산국 집중도 불명확",
        "나프타/원유 가격급등(일반 화학원료 경로)",
        "감염병 매개체(모기 등) 방역 이슈; 여름철",
    ),
    "HYGIENE_CONSUMABLE": (
        "부직포(폴리에스터 또는 목재펄프 유래 레이온) + 정제수/방부제",
        "펄프 가격 6개월새 25% 급등(러시아 공급차질+중동 정세發 해상운임 상승) — https://www.ktappi.or.kr/boards/notice/33562",
        "펄프/섬유 원자재 가격급등; 나프타 가격(폴리에스터 경로)",
        "감염병 유행 시 위생용품 수요 증가",
    ),
    "FAMILY_PLANNING": (
        "천연고무 라텍스(장갑과 동일 공급망)",
        "말레이시아 라텍스 생산 구조 — KOTRA",
        "동남아 천연고무 생산국 기후/정세",
        "특별한 뉴스 트리거 약함",
    ),
    "ANTHELMINTIC": (
        "원료의약품(수입 의존)",
        "원료의약품 전반 구조와 동일 패턴으로 추정",
        "중국/인도 원료의약품 수출규제",
        "낮은 우선순위",
    ),
    "WASTE_MANAGEMENT": (
        "PVC/PE(폐기물 용기·봉투) — 석유화학 유래",
        "2026-04 나프타 쇼크로 PVC 27% 인상 확인",
        "나프타/원유 가격급등(중동 정세)",
        "낮은 우선순위",
    ),
    "URINARY_DRAINAGE_SUPPLY": (
        "폴리염화비닐(PVC) 백 + 실리콘/라텍스 튜빙",
        "일반적인 의료용 배액백 구성 — 나프타 쇼크와 동일 석유화학 경로로 추정",
        "나프타/원유 가격급등(중동 정세)",
        "요로감염·비뇨기계 시술 건수 증가; 특별한 뉴스 트리거는 약함",
    ),
    "FUEL_ENERGY": (
        "원유(정제유)",
        "나프타/원유가 자체가 이 클러스터의 원자재 — 2026-04 나프타 쇼크 근거 동일",
        "중동 정세, OPEC 감산, 정제시설 가동중단",
        "재해 시 비상발전 수요, 계절(동절기 난방)",
    ),
}

# 기타(OTHER) 세부 — family_id 기준으로 별도 매핑
OTHER_FAMILY_MATERIAL_EVENTS = {
    "PRINTED_MATERIAL": (
        "펄프/종이",
        "펄프 가격 6개월새 25% 급등 — 러시아 침엽수펄프 공급차질(EU 제재) + 인도네시아 수마트라 홍수 + 중동 정세發 호르무즈해협 물류비 상승 — https://www.ktappi.or.kr/boards/notice/33562",
        "러시아發 펄프 공급차질; 중동 정세發 해상운임 급등",
        "낮은 우선순위(비의료)",
    ),
    "APPAREL_TEXTILE": (
        "면(원면) 또는 폴리에스터(석유화학)",
        "국제 면화가격 저점 대비 55% 폭등 — https://www.ktnews.com/news/articleView.html?idxno=120494",
        "주요 면화 생산국(미국·브라질·인도) 작황; 나프타 가격(폴리에스터)",
        "낮은 우선순위(비의료)",
    ),
    "PHARMACY_DISPENSING_SUPPLY": (
        "비닐 약봉투=폴리에틸렌(PE), 종이 약봉투=펄프 — 품목별 상이",
        "나프타 쇼크(PE) + 펄프가격 급등(종이) — 위 근거 동일",
        "나프타/원유 가격; 펄프 가격",
        "낮은 우선순위(비의료)",
    ),
    "ORAL_CANDY_GUM": (
        "정제설탕(정제당) + 검베이스(합성수지) — 기관/제조사가 실제 구매하는 형태는 정제당이지만, 가격급등 신호는 그보다 상류인 원당/사탕수수 국제무역 단계에서 먼저 발생(공급 파이프라인상 시차 존재)",
        "인도 상공부(DGFT), 2026-05-14부로 원당·백설탕·정제설탕(HS 1701 14 90, 1701 99 90) 전 품목 수출을 2026-09-30까지 금지(EU/US TRQ 등 일부 예외) — https://ddnews.gov.in/en/india-bans-sugar-exports-with-immediate-effect-until-september-2026/ ; 브라질 사탕수수의 에탄올 전환 확대까지 겹쳐 2026/27년 글로벌 설탕 150만톤 적자 전망 — https://www.ebn.co.kr/news/articleView.html?idxno=1715889 ; 이번 금지는 원당·백설탕·정제당을 모두 포함해 상류~하류 전 단계에 동시 적용된 예외적 사례",
        "인도·브라질 등 주요 생산국 수출규제/작황(2026년 현재진행형, 원당 단계에서 시작해 정제당 가격으로 전이); 나프타 가격(검베이스) — 손소독제 에탄올 원료(사탕수수)와 용도 경쟁 관계",
        "낮은 우선순위(비의료)",
    ),
    "SKINCARE_COSMETIC_TOPICAL": (
        "석유화학 유래(바세린=파라핀) + 식물성 오일 등 혼합",
        "나프타 쇼크 — 위 근거 동일(파라핀은 석유 정제 부산물)",
        "나프타/원유 가격",
        "낮은 우선순위(비의료)",
    ),
    "INFANT_FEEDING_SUPPLY": (
        "실리콘(모래·석영 등 규소 광물 추출) 또는 폴리프로필렌(PP)",
        "실리콘은 모래/석영(차돌) 추출물이 원료 — 2026-07-15 검색; PP는 나프타 쇼크 근거 동일",
        "규소 광물 가격; 나프타/원유 가격(PP)",
        "낮은 우선순위(비의료)",
    ),
    "PROMO_MATERIAL": (
        "품목별 재질 상이(종이/플라스틱/섬유 등 혼재)",
        "단일 원자재로 특정 부적절 — 원자재 매핑 실익 낮음",
        "해당 없음",
        "낮은 우선순위(비의료)",
    ),
    "MATERNAL_PROGRAM_PROMO": (
        "품목별 재질 상이(스티커·뱃지 등)",
        "단일 원자재로 특정 부적절",
        "해당 없음",
        "낮은 우선순위(비의료)",
    ),
    "UNSPECIFIED_ITEM": (
        "특정 불가",
        "브랜드/성분 미확인으로 원자재 특정 불가",
        "해당 없음",
        "해당 없음",
    ),
}


# ---------------------------------------------------------------------------
# raw_material_evidence / demand_risk_events 를 메타코드로 정형화.
# 24개 클러스터 + 기타 세부를 훑어서 반복되는 "수출입/유통 리스크 유형"과
# "수요 트리거 유형"을 추출해 SNAKE_CASE 코드로 묶었다. 한 클러스터가 여러
# 리스크를 동시에 갖는 경우 세미콜론으로 여러 코드를 붙인다.
# ---------------------------------------------------------------------------
RAW_MATERIAL_RISK_META = {
    "PAIN_NSAID": ["API_IMPORT_DEPENDENCY_CN_IN", "MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
    "ANTIHISTAMINE_ALLERGY": ["API_IMPORT_DEPENDENCY_CN_IN"],
    "GI_DIGESTIVE": ["MINERAL_RAWMATERIAL_GENERIC", "API_IMPORT_DEPENDENCY_CN_IN"],
    "RESPIRATORY": ["API_IMPORT_DEPENDENCY_CN_IN"],
    "ANTIBIOTIC": ["API_IMPORT_DEPENDENCY_CN_IN"],
    "ANTITUBERCULAR": ["API_IMPORT_DEPENDENCY_CN_IN"],
    "VITAMIN_NUTRITION": ["CHINA_VITAMIN_RAWMATERIAL_CONCENTRATION"],
    "CARDIOVASCULAR_METABOLIC": ["API_IMPORT_DEPENDENCY_CN_IN"],
    "DERM_TOPICAL": ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK", "API_IMPORT_DEPENDENCY_CN_IN"],
    "ANESTHETIC_MUSCLE": ["API_IMPORT_DEPENDENCY_CN_IN"],
    "ANTISEPTIC_DISINFECT": ["AGRI_COMMODITY_PRICE_VOLATILITY", "MIDEAST_NAPHTHA_PETROCHEM_SHOCK", "RARE_ELEMENT_ORIGIN_CONCENTRATION"],
    "IV_FLUID": ["DOMESTIC_OLIGOPOLY_CONCENTRATION"],
    "VACCINE": ["BIOLOGICS_FOREIGN_ORIGIN_DEPENDENCY"],
    "LAB_DIAGNOSTIC": ["PRECIOUS_METAL_COMPONENT_DEPENDENCY"],
    "INJECTION_PHLEBOTOMY": ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
    "WOUND_CARE": ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
    "GLOVE_PPE": ["SEA_NATURAL_RUBBER_ORIGIN_CONCENTRATION", "MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
    "ORAL_DENTAL": ["MINERAL_RAWMATERIAL_GENERIC", "MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
    "SMOKING_CESSATION": ["CHINA_SPECIALTY_CHEM_IMPORT"],
    "HANBANG": ["HANBANG_MULTISOURCE_ORIGIN_RISK"],
    "PEST_CONTROL": ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
    "HYGIENE_CONSUMABLE": ["PULP_TIMBER_SUPPLY_DISRUPTION", "MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
    "FAMILY_PLANNING": ["SEA_NATURAL_RUBBER_ORIGIN_CONCENTRATION"],
    "ANTHELMINTIC": ["API_IMPORT_DEPENDENCY_CN_IN"],
    "WASTE_MANAGEMENT": ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
    "URINARY_DRAINAGE_SUPPLY": ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
    "FUEL_ENERGY": ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
}
RAW_MATERIAL_RISK_META_OTHER = {
    "PRINTED_MATERIAL": ["PULP_TIMBER_SUPPLY_DISRUPTION"],
    "APPAREL_TEXTILE": ["COTTON_COMMODITY_PRICE_VOLATILITY"],
    "PHARMACY_DISPENSING_SUPPLY": ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK", "PULP_TIMBER_SUPPLY_DISRUPTION"],
    "ORAL_CANDY_GUM": ["AGRI_COMMODITY_PRICE_VOLATILITY"],
    "SKINCARE_COSMETIC_TOPICAL": ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
    "INFANT_FEEDING_SUPPLY": ["MINERAL_RAWMATERIAL_GENERIC", "MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
    "PROMO_MATERIAL": ["NOT_APPLICABLE"],
    "MATERNAL_PROGRAM_PROMO": ["NOT_APPLICABLE"],
    "UNSPECIFIED_ITEM": ["UNCLASSIFIED_MATERIAL_RISK"],
}

DEMAND_RISK_META = {
    "PAIN_NSAID": ["INFECTIOUS_DISEASE_OUTBREAK", "SEASONAL_CLIMATE_PATTERN"],
    "ANTIHISTAMINE_ALLERGY": ["SEASONAL_CLIMATE_PATTERN", "INFECTIOUS_DISEASE_OUTBREAK"],
    "GI_DIGESTIVE": ["LIFESTYLE_CALENDAR_EVENT", "SEASONAL_CLIMATE_PATTERN"],
    "RESPIRATORY": ["INFECTIOUS_DISEASE_OUTBREAK", "SEASONAL_CLIMATE_PATTERN"],
    "ANTIBIOTIC": ["INFECTIOUS_DISEASE_OUTBREAK"],
    "ANTITUBERCULAR": ["GOV_POLICY_PROGRAM_EXPANSION", "INFECTIOUS_DISEASE_OUTBREAK"],
    "VITAMIN_NUTRITION": ["SEASONAL_CLIMATE_PATTERN", "GOV_POLICY_PROGRAM_EXPANSION"],
    "CARDIOVASCULAR_METABOLIC": ["CHRONIC_DISEASE_STRUCTURAL_TREND"],
    "DERM_TOPICAL": ["SEASONAL_CLIMATE_PATTERN", "INFECTIOUS_DISEASE_OUTBREAK"],
    "ANESTHETIC_MUSCLE": ["LOW_PRIORITY_NO_TRIGGER"],
    "ANTISEPTIC_DISINFECT": ["INFECTIOUS_DISEASE_OUTBREAK"],
    "IV_FLUID": ["MASS_CASUALTY_DISASTER_EVENT", "INFECTIOUS_DISEASE_OUTBREAK"],
    "VACCINE": ["INFECTIOUS_DISEASE_OUTBREAK"],
    "LAB_DIAGNOSTIC": ["CHRONIC_DISEASE_STRUCTURAL_TREND", "INFECTIOUS_DISEASE_OUTBREAK"],
    "INJECTION_PHLEBOTOMY": ["GOV_POLICY_PROGRAM_EXPANSION", "INFECTIOUS_DISEASE_OUTBREAK"],
    "WOUND_CARE": ["MASS_CASUALTY_DISASTER_EVENT"],
    "GLOVE_PPE": ["INFECTIOUS_DISEASE_OUTBREAK"],
    "ORAL_DENTAL": ["LOW_PRIORITY_NO_TRIGGER", "GOV_POLICY_PROGRAM_EXPANSION"],
    "SMOKING_CESSATION": ["GOV_POLICY_PROGRAM_EXPANSION"],
    "HANBANG": ["SEASONAL_CLIMATE_PATTERN", "LIFESTYLE_CALENDAR_EVENT"],
    "PEST_CONTROL": ["SEASONAL_CLIMATE_PATTERN", "INFECTIOUS_DISEASE_OUTBREAK"],
    "HYGIENE_CONSUMABLE": ["INFECTIOUS_DISEASE_OUTBREAK"],
    "FAMILY_PLANNING": ["LOW_PRIORITY_NO_TRIGGER"],
    "ANTHELMINTIC": ["LOW_PRIORITY_NO_TRIGGER"],
    "WASTE_MANAGEMENT": ["LOW_PRIORITY_NO_TRIGGER"],
    "URINARY_DRAINAGE_SUPPLY": ["CHRONIC_DISEASE_STRUCTURAL_TREND", "LOW_PRIORITY_NO_TRIGGER"],
    "FUEL_ENERGY": ["MASS_CASUALTY_DISASTER_EVENT", "SEASONAL_CLIMATE_PATTERN"],
}
DEMAND_RISK_META_OTHER_DEFAULT = ["NOT_APPLICABLE"]

# 메타코드 사전(설명) — 팀 공유용
META_CODE_GLOSSARY = {
    "API_IMPORT_DEPENDENCY_CN_IN": "원료의약품 중국·인도 수입의존(자급률 11.9~25.6%, 중국·인도 비중 50%+)",
    "MIDEAST_NAPHTHA_PETROCHEM_SHOCK": "중동 정세發 나프타/석유화학 원자재 가격급등(2026-04 실사례)",
    "DOMESTIC_OLIGOPOLY_CONCENTRATION": "국내 소수 제조사 과점(예: 수액제 2개사 80%)",
    "SEA_NATURAL_RUBBER_ORIGIN_CONCENTRATION": "동남아(태국·인니·말레이) 천연고무 원산지 집중",
    "AGRI_COMMODITY_PRICE_VOLATILITY": "곡물·설탕 등 농산물 국제시세 변동",
    "PULP_TIMBER_SUPPLY_DISRUPTION": "펄프/원목 공급망 차질(러시아 제재, 중동發 해상운임 등)",
    "COTTON_COMMODITY_PRICE_VOLATILITY": "면화(원면) 국제시세 변동",
    "BIOLOGICS_FOREIGN_ORIGIN_DEPENDENCY": "백신 등 생물학적제제 원액 해외생산 의존(국내는 충전·포장만)",
    "PRECIOUS_METAL_COMPONENT_DEPENDENCY": "검사기기 전극 등 귀금속 부품 의존",
    "CHINA_VITAMIN_RAWMATERIAL_CONCENTRATION": "비타민 원료 중국기업 생산 집중",
    "HANBANG_MULTISOURCE_ORIGIN_RISK": "한약재 다국적 원산지 의존(중국/러시아/인도네시아 등 품목별 상이)",
    "CHINA_SPECIALTY_CHEM_IMPORT": "중국산 특수화학원료 수입(니코틴대체물질 등)",
    "RARE_ELEMENT_ORIGIN_CONCENTRATION": "요오드 등 희귀원소 소수생산국 집중",
    "MINERAL_RAWMATERIAL_GENERIC": "광물자원 일반(리스크 낮음)",
    "NOT_APPLICABLE": "원자재/이벤트 매핑 대상 아님(비의료)",
    "UNCLASSIFIED_MATERIAL_RISK": "성분/재질 미확인으로 리스크 특정 불가",
    "INFECTIOUS_DISEASE_OUTBREAK": "감염병 대유행(가장 강력한 단일 수요 트리거)",
    "SEASONAL_CLIMATE_PATTERN": "계절/기후(황사·폭염·한파·꽃가루·독감철 등)",
    "GOV_POLICY_PROGRAM_EXPANSION": "정부 정책/캠페인/보건사업 확대",
    "MASS_CASUALTY_DISASTER_EVENT": "대형 재해·사고(다수 부상자)",
    "CHRONIC_DISEASE_STRUCTURAL_TREND": "만성질환·고령화 등 구조적 추세(뉴스 트리거 약함)",
    "LIFESTYLE_CALENDAR_EVENT": "명절·연휴 등 생활패턴 이벤트",
    "LOW_PRIORITY_NO_TRIGGER": "특별한 뉴스 트리거 약함, 낮은 우선순위",
}


# ---------------------------------------------------------------------------
# raw_material_suggested(자유텍스트, 예: "천연고무 라텍스... 또는 니트릴 합성고무...")를
# 실제 원재료 단위 메타코드로 쪼갠다. raw_material_risk_meta_code(왜 위험한가,
# 카테고리)와는 다른 축 — 이건 "무엇으로 만들어졌는가"(물질 자체)를 코드화한 것.
# 한 클러스터가 재질을 여러 개 쓰면(예: 주사기 = PP배럴+스테인리스강 바늘)
# 전부 나열한다.
# ---------------------------------------------------------------------------
RAW_MATERIAL_CODE = {
    "PAIN_NSAID": ["PHARMA_API_GENERIC", "POLYPROPYLENE_PP", "ALUMINUM"],
    "ANTIHISTAMINE_ALLERGY": ["PHARMA_API_GENERIC"],
    "GI_DIGESTIVE": ["PHARMA_API_GENERIC"],
    "RESPIRATORY": ["PHARMA_API_GENERIC"],
    "ANTIBIOTIC": ["PHARMA_API_GENERIC"],
    "ANTITUBERCULAR": ["PHARMA_API_GENERIC"],
    "VITAMIN_NUTRITION": ["VITAMIN_SYNTHETIC_RAWMATERIAL"],
    "CARDIOVASCULAR_METABOLIC": ["PHARMA_API_GENERIC"],
    "DERM_TOPICAL": ["PHARMA_API_GENERIC", "PARAFFIN_PETROLEUM"],
    "ANESTHETIC_MUSCLE": ["PHARMA_API_GENERIC"],
    "ANTISEPTIC_DISINFECT": ["ETHANOL"],
    "IV_FLUID": ["SODIUM_CHLORIDE", "GLUCOSE_CORNSTARCH"],
    "VACCINE": ["BIOLOGICS_CELL_CULTURE", "LIPID_NANOPARTICLE"],
    "LAB_DIAGNOSTIC": ["MATERIAL_UNSPECIFIED"],
    "INJECTION_PHLEBOTOMY": ["POLYPROPYLENE_PP", "STAINLESS_STEEL"],
    "WOUND_CARE": ["POLYPROPYLENE_PP", "POLYURETHANE_PU", "HYDROCOLLOID_GEL", "ACRYLIC_ADHESIVE"],
    "GLOVE_PPE": ["NATURAL_RUBBER_LATEX", "SYNTHETIC_NITRILE_RUBBER", "POLYPROPYLENE_PP"],
    "ORAL_DENTAL": ["SILICA_ABRASIVE_MINERAL", "CALCIUM_CARBONATE_MINERAL", "SURFACTANT_SLS", "POLYPROPYLENE_PP", "NYLON", "FLUORIDE_COMPOUND"],
    "SMOKING_CESSATION": ["TOBACCO_LEAF_NICOTINE", "SYNTHETIC_NICOTINE_CHEMICAL"],
    "HANBANG": ["HERBAL_MATERIAL_MULTISOURCE"],
    "PEST_CONTROL": ["PYRETHROID_CHEMICAL"],
    "HYGIENE_CONSUMABLE": ["POLYESTER_FIBER", "RAYON_PULP"],
    "FAMILY_PLANNING": ["NATURAL_RUBBER_LATEX"],
    "ANTHELMINTIC": ["PHARMA_API_GENERIC"],
    "WASTE_MANAGEMENT": ["POLYVINYL_CHLORIDE_PVC", "POLYETHYLENE_PE"],
    "URINARY_DRAINAGE_SUPPLY": ["POLYVINYL_CHLORIDE_PVC", "SILICONE", "NATURAL_RUBBER_LATEX"],
    "FUEL_ENERGY": ["CRUDE_OIL_REFINED"],
}
RAW_MATERIAL_CODE_OTHER = {
    "PRINTED_MATERIAL": ["PULP_PAPER"],
    "APPAREL_TEXTILE": ["COTTON_FIBER", "POLYESTER_FIBER"],
    "PHARMACY_DISPENSING_SUPPLY": ["POLYETHYLENE_PE", "PULP_PAPER"],
    "ORAL_CANDY_GUM": ["REFINED_SUGAR", "SYNTHETIC_GUM_BASE"],
    "SKINCARE_COSMETIC_TOPICAL": ["PARAFFIN_PETROLEUM", "PLANT_OIL"],
    "INFANT_FEEDING_SUPPLY": ["SILICONE", "POLYPROPYLENE_PP"],
    "PROMO_MATERIAL": ["MIXED_MATERIAL_NOT_SINGLE"],
    "MATERNAL_PROGRAM_PROMO": ["MIXED_MATERIAL_NOT_SINGLE"],
    "UNSPECIFIED_ITEM": ["MATERIAL_UNSPECIFIED"],
}

MATERIAL_CODE_GLOSSARY = {
    "PHARMA_API_GENERIC": "원료의약품(합성 API) 일반",
    "POLYPROPYLENE_PP": "폴리프로필렌(PP)",
    "ALUMINUM": "알루미늄(포장재 등)",
    "VITAMIN_SYNTHETIC_RAWMATERIAL": "합성비타민 원료",
    "IRON_MINERAL_COMPOUND": "철염화합물(광물)",
    "PARAFFIN_PETROLEUM": "파라핀(석유정제 유래)",
    "ETHANOL": "에탄올",
    "IODINE": "요오드",
    "SODIUM_CHLORIDE": "염화나트륨(소금)",
    "GLUCOSE_CORNSTARCH": "포도당(옥수수전분 유래)",
    "BIOLOGICS_CELL_CULTURE": "생물학적 세포배양 원료",
    "LIPID_NANOPARTICLE": "지질나노입자(mRNA 백신용)",
    "ENZYME_REAGENT": "효소시약(임상화학용)",
    "STAINLESS_STEEL": "스테인리스강",
    "POLYURETHANE_PU": "폴리우레탄",
    "HYDROCOLLOID_GEL": "하이드로콜로이드겔",
    "ACRYLIC_ADHESIVE": "아크릴점착제",
    "NATURAL_RUBBER_LATEX": "천연고무 라텍스",
    "SYNTHETIC_NITRILE_RUBBER": "니트릴 합성고무",
    "SURFACTANT_SLS": "SLS계열 계면활성제",
    "NYLON": "나일론",
    "FLUORIDE_COMPOUND": "불소화합물(광물)",
    "HERBAL_MATERIAL_MULTISOURCE": "한약재(다원산지 — 감초=중국/몽골, 녹용=러시아, 육두구=인도네시아 등 품목별 원산지 상이)",
    "PYRETHROID_CHEMICAL": "피레스로이드계 살충화학물질",
    "POLYESTER_FIBER": "폴리에스터 섬유",
    "RAYON_PULP": "레이온(목재펄프 유래)",
    "POLYVINYL_CHLORIDE_PVC": "폴리염화비닐(PVC)",
    "POLYETHYLENE_PE": "폴리에틸렌(PE)",
    "SILICONE": "실리콘(모래·석영 유래)",
    "CRUDE_OIL_REFINED": "원유(정제유)",
    "PULP_PAPER": "펄프/종이",
    "COTTON_FIBER": "면(원면)",
    "SYNTHETIC_GUM_BASE": "검베이스(합성수지)",
    "PLANT_OIL": "식물성 오일",
    "MIXED_MATERIAL_NOT_SINGLE": "혼합재질(단일 원재료로 특정 부적절)",
    "MATERIAL_UNSPECIFIED": "재질 미상",
    # --- 대분류 폴백용 개략 코드(성분 미특정, group_coarse) ---
    "LAB_REAGENT_GENERIC": "검사·진단 시약 일반(성분 미특정, 대분류 기반)",
    "DISINFECTANT_CHEMICAL_GENERIC": "소독·살균 화학물질 일반(성분 미특정, 대분류 기반)",
    "MEDICAL_SUPPLY_PLASTIC_GENERIC": "의료소모품 플라스틱 일반(재질 미특정, PP 추정, 대분류 기반)",
    # --- 2026-07-15 2차 정밀화: 뭉뚱그려진 코드를 실제 물질 단위로 분리 ---
    "CARBON_GRAPHITE_ELECTRODE": "탄소/그래파이트 작동전극(스크린프린팅, 저가 검사스트립 주류 소재)",
    "SILVER_SILVER_CHLORIDE_ELECTRODE": "은/염화은(Ag/AgCl) 기준전극(검사스트립 사실상 표준 구성요소)",
    "IMMUNOASSAY_REAGENT_LATEX": "면역진단 시약(항체-금나노입자/라텍스 컨쥬게이트, 신속진단키트류)",
    "BIOLOGICAL_GROWTH_MEDIUM": "미생물 배양배지(한천·펩톤 등 생물유래 성분)",
    "ELECTRONIC_DEVICE_COMPONENT": "전자부품(PCB·배터리·디스플레이 — 소모품이 아닌 기기)",
    "RFID_ELECTRONIC_TAG_COMPONENT": "RFID 전자태그(실리콘칩+구리/알루미늄 안테나)",
    "ALUMINUM_HYDROXIDE_MINERAL": "수산화알루미늄(보크사이트 유래 광물, 제산제 원료)",
    "MAGNESIUM_HYDROXIDE_MINERAL": "수산화마그네슘(마그네사이트/해수 유래 광물, 제산제 원료)",
    "PANCREATIN_ANIMAL_ENZYME": "판크레아틴(돼지 등 동물 췌장유래 소화효소)",
    "MICROBIAL_FUNGAL_ENZYME": "미생물·진균유래 소화효소(발효생산)",
    "SILICA_ABRASIVE_MINERAL": "실리카(연마제, 규사 광물)",
    "CALCIUM_CARBONATE_MINERAL": "탄산칼슘(연마제, 석회석 광물)",
    "CHLORHEXIDINE_GLUCONATE_CHEMICAL": "클로르헥시딘글루콘산염(합성 화학물질)",
    "HYDROGEN_PEROXIDE_CHEMICAL": "과산화수소(합성 화학물질)",
    "TOBACCO_LEAF_NICOTINE": "담뱃잎 추출 니코틴",
    "SYNTHETIC_NICOTINE_CHEMICAL": "합성 니코틴(화학합성, 중국산 대체물질 수입 사례 있음)",
    "NONWOVEN_FABRIC_SPUNBOND": "부직포(스펀본드/멜트블로운)",
    "WOOD": "목재(설압자 등)",
    "REFINED_SUGAR": "정제설탕(정제당) — 실제 구매 형태, 원물은 사탕수수/사탕무",
}

# ---------------------------------------------------------------------------
# 원재료 공급 파이프라인 단계. "설탕이 사탕수수/사탕무를 들여오는 건지 정제설탕을
# 들여오는 건지"처럼, 코드 하나가 대표하는 물질이 공급망의 어느 단계(원물/광물채굴/
# 정제중간재/합성화학/생물유래/제조부품)에 있는지와, 그 상류에 어떤 원물이 있는지를
# 분리해서 표시한다. 기관이 실제로 구매하는 형태(예: 정제당)와 뉴스에서 가격급등이
# 먼저 터지는 단계(예: 원당/사탕수수 수출규제)가 다를 수 있어, 이벤트-가격 전이에
# 시차가 생길 수 있음을 note에 남긴다.
# ---------------------------------------------------------------------------
STAGE_RAW = "원물(농축수산물)"
STAGE_MINED = "원광(광물채굴)"
STAGE_REFINED = "정제/가공 중간재"
STAGE_SYNTH = "합성화학물질"
STAGE_BIO = "생물유래물질"
STAGE_COMPONENT = "제조부품(전자/기계)"
STAGE_NA = "해당없음"

MATERIAL_SUPPLY_STAGE = {
    "PHARMA_API_GENERIC": (STAGE_SYNTH, ""),
    "POLYPROPYLENE_PP": (STAGE_REFINED, "상류 원물은 원유(나프타 분해)"),
    "ALUMINUM": (STAGE_REFINED, "상류 원물은 보크사이트"),
    "VITAMIN_SYNTHETIC_RAWMATERIAL": (STAGE_SYNTH, ""),
    "IRON_MINERAL_COMPOUND": (STAGE_MINED, ""),
    "PARAFFIN_PETROLEUM": (STAGE_REFINED, "상류 원물은 원유"),
    "ETHANOL": (STAGE_REFINED, "상류 원물은 곡물/사탕수수 또는 원유(생산방식에 따라 상이)"),
    "IODINE": (STAGE_MINED, ""),
    "SODIUM_CHLORIDE": (STAGE_MINED, ""),
    "GLUCOSE_CORNSTARCH": (STAGE_REFINED, "상류 원물은 옥수수"),
    "BIOLOGICS_CELL_CULTURE": (STAGE_BIO, ""),
    "LIPID_NANOPARTICLE": (STAGE_SYNTH, ""),
    "ENZYME_REAGENT": (STAGE_BIO, ""),
    "STAINLESS_STEEL": (STAGE_REFINED, "상류 원물은 철광석+크롬/니켈"),
    "POLYURETHANE_PU": (STAGE_REFINED, "상류 원물은 원유"),
    "HYDROCOLLOID_GEL": (STAGE_REFINED, ""),
    "ACRYLIC_ADHESIVE": (STAGE_REFINED, "상류 원물은 원유"),
    "NATURAL_RUBBER_LATEX": (STAGE_RAW, "고무나무 수액을 채취해 최소가공만 거침"),
    "SYNTHETIC_NITRILE_RUBBER": (STAGE_REFINED, "상류 원물은 원유(부타디엔+아크릴로나이트릴)"),
    "SURFACTANT_SLS": (STAGE_SYNTH, ""),
    "NYLON": (STAGE_REFINED, "상류 원물은 원유"),
    "FLUORIDE_COMPOUND": (STAGE_MINED, ""),
    "HERBAL_MATERIAL_MULTISOURCE": (STAGE_RAW, "품목별 원산지 상이"),
    "PYRETHROID_CHEMICAL": (STAGE_SYNTH, ""),
    "POLYESTER_FIBER": (STAGE_REFINED, "상류 원물은 원유"),
    "RAYON_PULP": (STAGE_REFINED, "상류 원물은 원목"),
    "POLYVINYL_CHLORIDE_PVC": (STAGE_REFINED, "상류 원물은 원유"),
    "POLYETHYLENE_PE": (STAGE_REFINED, "상류 원물은 원유"),
    "SILICONE": (STAGE_REFINED, "상류 원물은 규사(모래/석영)"),
    "CRUDE_OIL_REFINED": (STAGE_MINED, "원유 채굴 자체 — 여기가 나프타/석유화학 전체 원자재의 최상류"),
    "PULP_PAPER": (STAGE_REFINED, "상류 원물은 원목"),
    "COTTON_FIBER": (STAGE_RAW, ""),
    "SYNTHETIC_GUM_BASE": (STAGE_REFINED, "상류 원물은 원유"),
    "PLANT_OIL": (STAGE_RAW, ""),
    "MIXED_MATERIAL_NOT_SINGLE": (STAGE_NA, ""),
    "MATERIAL_UNSPECIFIED": (STAGE_NA, ""),
    "LAB_REAGENT_GENERIC": (STAGE_SYNTH, "대분류 기반 개략(성분 미특정)"),
    "DISINFECTANT_CHEMICAL_GENERIC": (STAGE_SYNTH, "대분류 기반 개략(성분 미특정)"),
    "MEDICAL_SUPPLY_PLASTIC_GENERIC": (STAGE_REFINED, "대분류 기반 개략, 상류 원물은 원유(재질 미특정)"),
    "CARBON_GRAPHITE_ELECTRODE": (STAGE_MINED, ""),
    "SILVER_SILVER_CHLORIDE_ELECTRODE": (STAGE_MINED, "귀금속(은) 시세에 연동"),
    "IMMUNOASSAY_REAGENT_LATEX": (STAGE_BIO, "항체는 생물유래, 금나노입자 라벨은 광물(금) 연동"),
    "BIOLOGICAL_GROWTH_MEDIUM": (STAGE_BIO, ""),
    "ELECTRONIC_DEVICE_COMPONENT": (STAGE_COMPONENT, "반도체·배터리 공급망 성격, 원자재보다 부품 수급이 리스크"),
    "RFID_ELECTRONIC_TAG_COMPONENT": (STAGE_COMPONENT, ""),
    "ALUMINUM_HYDROXIDE_MINERAL": (STAGE_MINED, "상류 원물은 보크사이트"),
    "MAGNESIUM_HYDROXIDE_MINERAL": (STAGE_MINED, "마그네사이트 또는 해수 유래"),
    "PANCREATIN_ANIMAL_ENZYME": (STAGE_BIO, "축산 질병(ASF 등) 발생 시 원료 수급 리스크"),
    "MICROBIAL_FUNGAL_ENZYME": (STAGE_BIO, "발효공장 가동 이슈가 리스크"),
    "SILICA_ABRASIVE_MINERAL": (STAGE_MINED, ""),
    "CALCIUM_CARBONATE_MINERAL": (STAGE_MINED, ""),
    "CHLORHEXIDINE_GLUCONATE_CHEMICAL": (STAGE_SYNTH, ""),
    "HYDROGEN_PEROXIDE_CHEMICAL": (STAGE_SYNTH, ""),
    "TOBACCO_LEAF_NICOTINE": (STAGE_RAW, ""),
    "SYNTHETIC_NICOTINE_CHEMICAL": (STAGE_SYNTH, "중국산 대체물질 수입 사례"),
    "NONWOVEN_FABRIC_SPUNBOND": (STAGE_REFINED, "상류 원물은 원유(PP 원사)"),
    "WOOD": (STAGE_RAW, ""),
    "REFINED_SUGAR": (
        STAGE_REFINED,
        "실제 구매 형태는 정제당이지만 가격급등 신호는 대부분 상류(원당/사탕수수 국제무역)에서 먼저 발생 — "
        "단 2026년 인도 수출금지처럼 원당·백설탕·정제당을 동시에 묶는 조치는 전이 시차 없이 즉시 반영될 수 있음",
    ),
}


# ---------------------------------------------------------------------------
# family 단위 원재료 오버라이드. 위 RAW_MATERIAL_CODE/RAW_MATERIAL_CODE_OTHER는
# 클러스터(또는 기타 family) 단위라 같은 클러스터 안에 재질이 전혀 다른 품목이
# 섞여 있으면(예: LAB_DIAGNOSTIC 안에 혈당스트립과 배양배지가 같이 있음, GLOVE_PPE
# 안에 고무장갑과 PP마스크가 같이 있음) 부정확해진다. item_family_id_suggested가
# 이 오버라이드에 있으면 그걸 우선 적용해 원재료 코드와 raw_material_suggested 설명
# 문구를 모두 더 정밀한 값으로 교체한다. (material_codes, 설명문구) 튜플.
# ---------------------------------------------------------------------------
FAMILY_MATERIAL_OVERRIDE = {
    # GI_DIGESTIVE — 제산제/소화효소제만 예외 처리, 나머지는 클러스터 기본값(PHARMA_API_GENERIC)
    "ALMAGATE": (["ALUMINUM_HYDROXIDE_MINERAL", "MAGNESIUM_HYDROXIDE_MINERAL"], "알마게이트 — 알루미늄·마그네슘 복합 수산화물(광물성 무기화합물)"),
    "MAGNESIUM_HYDROXIDE": (["MAGNESIUM_HYDROXIDE_MINERAL"], "수산화마그네슘(마그네사이트/해수 유래 광물)"),
    "ALUMINUM_MAGNESIUM_ANTACID": (["ALUMINUM_HYDROXIDE_MINERAL", "MAGNESIUM_HYDROXIDE_MINERAL"], "알루미늄·마그네슘 복합 제산제"),
    "수산화마그네슘": (["MAGNESIUM_HYDROXIDE_MINERAL"], "수산화마그네슘"),
    "DIGESTIVE_ENZYME": (["PANCREATIN_ANIMAL_ENZYME", "MICROBIAL_FUNGAL_ENZYME"], "훼스탈/베아제류 — 동물(돼지 췌장) 유래 판크레아틴 + 미생물(진균) 발효효소 복합"),
    "MUGWORT_EXTRACT": (["HERBAL_MATERIAL_MULTISOURCE"], "쑥 등 생약 추출물"),
    "무당": (["HERBAL_MATERIAL_MULTISOURCE"], "생약 성분 추정"),
    # ANTISEPTIC_DISINFECT — 알코올 외 성분만 예외 처리
    "POVIDONE_IODINE": (["IODINE"], "포비돈요오드 — 요오드 원소(칠레·일본 등 소수생산국 집중)"),
    "CHLORHEXIDINE": (["CHLORHEXIDINE_GLUCONATE_CHEMICAL"], "클로르헥시딘글루콘산염 — 에탄올·요오드와 무관한 별도 합성화학물질"),
    "HYDROGEN_PEROXIDE": (["HYDROGEN_PEROXIDE_CHEMICAL"], "과산화수소 — 합성 화학물질"),
    "ALCOHOL_SWAB": (["ETHANOL", "NONWOVEN_FABRIC_SPUNBOND"], "부직포 기재 + 에탄올 함침"),
    "ANTISEPTIC_SWAB": (["ETHANOL", "NONWOVEN_FABRIC_SPUNBOND"], "부직포 기재 + 소독액 함침"),
    # ORAL_DENTAL — 불소도포제만 예외(치약/칫솔 묶음은 클러스터 기본값 유지)
    "FLUORIDE_DENTAL": (["FLUORIDE_COMPOUND"], "불소도포제 — 불소화합물 단일 원료"),
    # WASTE_MANAGEMENT — RFID 태그만 예외
    "MEDICAL_WASTE_RFID_TAG": (["RFID_ELECTRONIC_TAG_COMPONENT"], "RFID 태그 — 실리콘칩+금속 안테나, 폐기물 용기 본체(PVC/PE)와 원료가 다름"),
    # GLOVE_PPE — 장갑과 마스크를 분리
    "MEDICAL_GLOVE": (["NATURAL_RUBBER_LATEX", "SYNTHETIC_NITRILE_RUBBER"], "라텍스 또는 니트릴 고무 장갑 — 마스크(PP)와 원료가 다름"),
    "FACE_MASK": (["POLYPROPYLENE_PP"], "보건용 마스크 — 겉감/필터(멜트블로운)/안감 전부 PP 부직포"),
    "MEDICAL_MASK": (["POLYPROPYLENE_PP"], "의료용 마스크 — PP 부직포 기반 재질 후보(제품별 구성 검토 필요)"),
    # LAB_DIAGNOSTIC — 전기화학 스트립 / 면역진단 / 배지 / 용기·소모품 / 기기를 각각 분리
    "BLOOD_GLUCOSE_LIPID_TEST_STRIP": (["CARBON_GRAPHITE_ELECTRODE", "SILVER_SILVER_CHLORIDE_ELECTRODE", "ENZYME_REAGENT"], "전기화학 검사스트립 — 저가형은 탄소/그래파이트 작동전극+은/염화은 기준전극이 주류(일부 고급형은 금·백금 도금)"),
    "BLOOD_GLUCOSE_TEST_STRIP": (["CARBON_GRAPHITE_ELECTRODE", "SILVER_SILVER_CHLORIDE_ELECTRODE", "ENZYME_REAGENT"], "전기화학 검사스트립 — 저가형은 탄소/그래파이트 작동전극+은/염화은 기준전극이 주류(일부 고급형은 금·백금 도금)"),
    "HEMOGLOBIN_TEST_STRIP": (["CARBON_GRAPHITE_ELECTRODE", "SILVER_SILVER_CHLORIDE_ELECTRODE", "ENZYME_REAGENT"], "빈혈검사지 — 전기화학식 스트립과 동일 전극 구성으로 추정"),
    "LIPID_TEST_STRIP": (["CARBON_GRAPHITE_ELECTRODE", "SILVER_SILVER_CHLORIDE_ELECTRODE", "ENZYME_REAGENT"], "콜레스테롤 등 지질검사지 — 전기화학식 스트립과 동일 전극 구성으로 추정"),
    "CLINICAL_CHEMISTRY_TEST": (["IMMUNOASSAY_REAGENT_LATEX"], "HBs Ag 등 면역진단시약 — 항체-금나노입자/라텍스 컨쥬게이트, 전극형 검사스트립과 원료가 다름"),
    "PREGNANCY_TEST_KIT": (["IMMUNOASSAY_REAGENT_LATEX"], "신속 면역진단(래피드 테스트) — 금나노입자 컨쥬게이트 라인 방식이 일반적"),
    "PREGNANCY_TEST": (["IMMUNOASSAY_REAGENT_LATEX"], "임신진단키트 — 면역진단 시약 기반 재질 후보"),
    "COTININE_TEST_KIT": (["IMMUNOASSAY_REAGENT_LATEX"], "코티닌 신속진단키트 — 면역진단시약과 동일 계열"),
    "CULTURE_MEDIUM": (["BIOLOGICAL_GROWTH_MEDIUM"], "수송배지/배양배지 — 한천·펩톤 등 생물유래 배양성분, 전극 스트립과 무관"),
    "SPECIMEN_CONTAINER": (["POLYPROPYLENE_PP"], "검체용기 — PP 플라스틱"),
    "LAB_CUVETTE": (["POLYPROPYLENE_PP"], "큐벳 — 통상 PP/PS 플라스틱"),
    "LAB_PIPETTE_TIP": (["POLYPROPYLENE_PP"], "피펫팁 — PP"),
    "BLOOD_COLLECTION_TUBE": (["POLYPROPYLENE_PP"], "채혈관 — 튜브 본체 PP(유리 제품도 있으나 소모품은 PP가 일반적)"),
    "THERMOMETER_DEVICE": (["ELECTRONIC_DEVICE_COMPONENT"], "체온계 — 전자부품(PCB/배터리/디스플레이), 소모품이 아닌 기기라 원자재 리스크 성격이 다름"),
    "GLUCOSE_METER_DEVICE": (["ELECTRONIC_DEVICE_COMPONENT"], "혈당측정기 본체 — 전자기기, 스트립 소모품과 원자재 리스크 성격이 다름"),
    # WOUND_CARE — 품목별 재질을 세분화
    "WOUND_DRESSING_HYDROCOLLOID": (["HYDROCOLLOID_GEL", "POLYURETHANE_PU"], "듀오덤류 — 하이드로콜로이드 겔층+폴리우레탄 필름 백킹"),
    "WOUND_DRESSING_POLYURETHANE_FOAM": (["POLYURETHANE_PU"], "메디폼류 — 폴리우레탄 폼/필름"),
    "WOUND_DRESSING_FILM": (["POLYURETHANE_PU"], "테가덤류 — 투명 폴리우레탄 필름"),
    "WOUND_DRESSING_NONWOVEN": (["NONWOVEN_FABRIC_SPUNBOND", "ACRYLIC_ADHESIVE"], "슈퍼포아류 — 부직포+아크릴 점착코팅"),
    "WOUND_CLOSURE_STRIP": (["NONWOVEN_FABRIC_SPUNBOND", "ACRYLIC_ADHESIVE"], "스테리스트립류 — 부직포 스트립+점착제"),
    "ADHESIVE_BANDAGE": (["COTTON_FIBER", "ACRYLIC_ADHESIVE"], "대일밴드류 — 면패드+점착 백킹"),
    "SELF_ADHERENT_WRAP": (["NONWOVEN_FABRIC_SPUNBOND", "NATURAL_RUBBER_LATEX"], "코반류 — 부직포+탄성섬유(천연고무 라텍스 함유)"),
    "KINESIOLOGY_TAPE": (["COTTON_FIBER", "ACRYLIC_ADHESIVE"], "키네시오테이프 — 면직물+아크릴 점착제"),
    "COTTON_GAUZE": (["COTTON_FIBER"], "거즈/면봉 — 면"),
    "GAUZE_BANDAGE_DRESSING_GENERIC": (["COTTON_FIBER"], "붕대류 — 면"),
    "MEDICAL_GAUZE": (["COTTON_FIBER"], "의료용 거즈 — 면 섬유 후보"),
    "SUCTION_TIP_MEDICAL_SUPPLY": (["POLYVINYL_CHLORIDE_PVC"], "흡인관 — PVC 튜빙"),
    "STERILIZATION_PACKAGING_EO": (["PULP_PAPER", "POLYETHYLENE_PE"], "EO멸균포장재 — 종이+플라스틱(PE) 적층 파우치"),
    "EO_STERILIZATION_PACKAGING": (["PULP_PAPER", "POLYETHYLENE_PE"], "EO멸균포장재 — 종이+플라스틱 적층 구조 후보"),
    # INJECTION_PHLEBOTOMY — 설압자(목재) 등 예외 처리
    "DISPOSABLE_SYRINGE": (["POLYPROPYLENE_PP", "NATURAL_RUBBER_LATEX"], "배럴 PP + 플런저팁 고무(제조사별 천연/합성 상이)"),
    "LANCET": (["STAINLESS_STEEL", "POLYPROPYLENE_PP"], "채혈침 스테인리스강 + 하우징 PP"),
    "BLOOD_LANCET": (["STAINLESS_STEEL", "POLYPROPYLENE_PP"], "채혈침 — 스테인리스강 침 + PP 하우징 후보"),
    "INJECTION_NEEDLE": (["STAINLESS_STEEL", "POLYPROPYLENE_PP"], "주사침 — 스테인리스강 바늘 + 플라스틱 허브 후보"),
    "ANGIO_CATHETER": (["STAINLESS_STEEL", "POLYURETHANE_PU", "POLYPROPYLENE_PP"], "말초혈관 카테터 — 삽입침·카테터·허브 복합재질 후보"),
    "INFUSION_SET": (["POLYVINYL_CHLORIDE_PVC", "POLYPROPYLENE_PP"], "수액세트 — 튜브와 연결부 복합 플라스틱 재질 후보"),
    "DIALYSATE_CONTAINER": (["POLYETHYLENE_PE"], "혈액투석제통 — PE계 용기 후보(제품 문서 검토 필요)"),
    "IV_FLUID_CONTAINER": (["POLYVINYL_CHLORIDE_PVC", "POLYPROPYLENE_PP"], "수액제통 — PVC/폴리올레핀계 용기 후보(제품별 상이)"),
    "ACUPUNCTURE_NEEDLE": (["STAINLESS_STEEL"], "침 — 스테인리스강"),
    "TONGUE_DEPRESSOR": (["WOOD"], "설압자 — 목재(자작나무 등)"),
    # SMOKING_CESSATION — 니코틴 제품과 비니코틴 기기 분리
    "NICOTINE_REPLACEMENT": (["TOBACCO_LEAF_NICOTINE", "SYNTHETIC_NICOTINE_CHEMICAL"], "니코틴패치/껌 — 담뱃잎 추출 또는 합성 니코틴(합성 비중 확대 추세)"),
    "SMOKING_CESSATION_DEVICE": (["POLYPROPYLENE_PP"], "마우스피스 등 — 니코틴 미함유, PP 플라스틱"),
    # VITAMIN_NUTRITION — 철분제만 광물화합물, 나머지는 비타민별로 개별 승격(promote_family_material)
    "IRON_SUPPLEMENT": (["IRON_MINERAL_COMPOUND"], "철염화합물(광물) — 다른 비타민류와 원료가 다름"),
    "IRON_FMOA": (["IRON_MINERAL_COMPOUND"], "철염화합물(광물, FMOA 제형)"),
}


# 성분이 아닌 sentinel family_id는 원재료 코드로 승격하지 않는다.
SENTINEL_FIDS = {"NON_INGREDIENT_SPEC", "MANUFACTURER_NAME_NOISE", "UNKNOWN_INGREDIENT"}

# ---------------------------------------------------------------------------
# "PHARMA_API_GENERIC"/"VITAMIN_SYNTHETIC_RAWMATERIAL" 같은 뭉뚱그린 코드는
# item_family_id_suggested 자체가 이미 구체적 성분명(예: ACETAMINOPHEN, 케토프로펜,
# 비타민C)인 경우가 대부분이라, family_id를 그대로 원재료코드로 승격시킨다 —
# 새 사전을 손으로 만드는 대신 이미 확보된 family 식별 결과를 재사용하는 규칙.
# 승격하지 않는 경우:
#  - family_basis가 "이 물질이 뭔지 모른다"는 뜻인 경우(unresolved) 또는 애초에
#    의약품 성분이 아닌 것을 잘못 걸친 경우(functional_keyword, non_material_category)
#  - 이름은 있어도 "복합/미상" 성격이라 승격시켜봐야 의미 없는 family(SKIP 목록)
#  - 실제로는 완전히 다른 물질군이라 별도 고정 코드로 바꿔야 하는 family(NON_SUBSTANCE 목록)
# ---------------------------------------------------------------------------
PROMOTABLE_GENERIC_CODES = {"PHARMA_API_GENERIC", "VITAMIN_SYNTHETIC_RAWMATERIAL"}
NON_PROMOTABLE_BASIS = {
    "functional_keyword",
    "non_material_category",
    "naming_pattern_unverified",
    "unresolved",
}
SKIP_PROMOTION_FIDS = {
    # 이미 다른 코드(IRON_MINERAL_COMPOUND)로 정확히 표현되어 중복 승격할 필요 없음
    "IRON_SUPPLEMENT", "IRON_FMOA",
    # 여러 비타민/영양소가 섞인 복합제라 단일 성분으로 승격 불가
    "VITAMIN_UNSPECIFIED", "NUTRITIONAL_SUPPLEMENT_GENERIC", "MULTIVITAMIN_MINERAL", "PREGNANCY_SUPPLEMENT",
}
NON_SUBSTANCE_FID_OVERRIDE = {
    # 이름은 VITAMIN_NUTRITION 클러스터에 있지만 실제론 합성비타민이 아닌 별도 물질군
    "PROBIOTICS": ("PROBIOTIC_BACTERIAL_CULTURE", "유산균 등 생균 — 발효 배양생산, 합성비타민과 원료·공급망이 전혀 다름", "생물유래물질"),
    "MEDICAL_NUTRITION_FORMULA": ("MEDICAL_NUTRITION_FORMULA_MIXED", "환자용 영양식/영양죽 — 여러 영양소를 섞은 가공식품, 단일 비타민 원료가 아님", "제조부품(전자/기계)"),
}

SUBTYPE_MATERIAL_OVERRIDE = {
    "MEDICAL_WASTE_PE_BAG": (
        ["POLYETHYLENE_PE"],
        "의료폐기물 PE 봉투형 용기 — 명칭에 PE가 명시된 재질 후보",
    ),
    "MEDICAL_WASTE_SYNTHETIC_BAG": (
        ["MEDICAL_SUPPLY_PLASTIC_GENERIC"],
        "의료폐기물 합성수지 봉투형 용기 — 수지 종류는 제품 문서 확인 필요",
    ),
    "RIGID_NEEDLE_BOX": (
        ["POLYPROPYLENE_PP"],
        "손상성 의료폐기물용 경질 needle box — PP계 재질 후보",
    ),
    "MEDICAL_WASTE_CARDBOARD_BOX": (
        ["PULP_PAPER"],
        "의료폐기물 골판지류 상자형 용기 — 펄프·종이계 재질 후보",
    ),
}

# 여러 제품이 한 재고 단위로 묶인 키트는 개별 구성품의 BOM과 수량비가 확인되기 전
# 단일 원자재로 환원하지 않는다. 구성품 family 분리는 가능하지만 원자재는 보류한다.
COMPOSITE_SET_FIDS = {
    "BLOOD_GLUCOSE_TESTING_SET",
    "BLOOD_GLUCOSE_METER_KIT",
}


# ---------------------------------------------------------------------------
# 대분류(item_group_id_candidate) 기반 폴백. 성분(브랜드)을 특정 못 해도 원본
# AI팀이 준 대분류(MED_ORAL 등)는 있으므로, '재질미상'으로 버리는 대신 대분류
# 수준의 개략 원재료를 배정한다. 예: 어떤 경구약인지 몰라도 경구약인 건 알면
# PHARMA_API_GENERIC(원료의약품 일반)이 정직한 값이고, 공급망 리스크(중국·인도
# 수입의존)도 그게 맞다. material_confidence='group_coarse'로 표시해 정밀 식별과
# 구분한다. UNCLASSIFIED/RENTAL 등 대분류 자체가 무의미한 건 여전히 재질미상.
# (mat_codes, verified, evidence, supply, demand, risk_meta, demand_meta)
# ---------------------------------------------------------------------------
GROUP_FALLBACK = {
    "MED_ORAL": (["PHARMA_API_GENERIC", "POLYPROPYLENE_PP", "ALUMINUM"],
        "경구약(성분 미특정) — 대분류 기반 개략: 합성 원료의약품 + PTP/블리스터 포장재",
        "원본 대분류 MED_ORAL. 성분 미식별이나 경구 의약품인 것은 확인됨 → 원료의약품 일반으로 배정",
        "중국/인도 원료의약품 수입 차질(성분 미상이나 합성 API 공통 리스크); 포장재 나프타",
        "성분 미상 — 개별 확인 필요",
        ["API_IMPORT_DEPENDENCY_CN_IN"], ["LOW_PRIORITY_NO_TRIGGER"]),
    "MED_INJECT": (["PHARMA_API_GENERIC"],
        "주사약(성분 미특정) — 대분류 기반 개략: 합성/생물학적 원료의약품",
        "원본 대분류 MED_INJECT. 성분 미식별이나 주사 의약품인 것은 확인됨",
        "중국/인도 원료의약품 수입 차질; 일부 생물학적제제는 해외 원액 의존",
        "성분 미상 — 개별 확인 필요",
        ["API_IMPORT_DEPENDENCY_CN_IN"], ["LOW_PRIORITY_NO_TRIGGER"]),
    "MED_TOPICAL": (["PHARMA_API_GENERIC", "PARAFFIN_PETROLEUM"],
        "외용약(성분 미특정) — 대분류 기반 개략: 원료의약품 + 연고기제(파라핀 등 석유화학)",
        "원본 대분류 MED_TOPICAL. 성분 미식별이나 외용 의약품인 것은 확인됨",
        "중국/인도 원료의약품 수입 차질; 연고기제 나프타 가격급등",
        "계절성 피부질환 등(성분 미상, 개략)",
        ["API_IMPORT_DEPENDENCY_CN_IN", "MIDEAST_NAPHTHA_PETROCHEM_SHOCK"], ["SEASONAL_CLIMATE_PATTERN"]),
    "LAB_REAGENT": (["LAB_REAGENT_GENERIC"],
        "검사·진단 시약(성분 미특정) — 대분류 기반 개략",
        "원본 대분류 LAB_REAGENT. 검사·진단용 시약인 것은 확인됨",
        "특수화학·정밀부품 수입 의존(반도체류와 유사); 일부 귀금속·효소 원료",
        "만성질환 관리·감염병 진단 수요(성분 미상, 개략)",
        ["CHINA_SPECIALTY_CHEM_IMPORT"], ["CHRONIC_DISEASE_STRUCTURAL_TREND"]),
    "DISINFECT": (["DISINFECTANT_CHEMICAL_GENERIC"],
        "소독·살균제(성분 미특정) — 대분류 기반 개략: 에탄올/요오드/염소계 등",
        "원본 대분류 DISINFECT. 소독·살균 용도인 것은 확인됨",
        "곡물(에탄올) 가격급등; 나프타(석유화학 소독성분)",
        "감염병 대유행 시 소독제 수요 폭증",
        ["AGRI_COMMODITY_PRICE_VOLATILITY", "MIDEAST_NAPHTHA_PETROCHEM_SHOCK"], ["INFECTIOUS_DISEASE_OUTBREAK"]),
    "MED_SUPPLY": (["MEDICAL_SUPPLY_PLASTIC_GENERIC"],
        "의료소모품(재질 미특정) — 대분류 기반 개략: 플라스틱(PP 등) 추정",
        "원본 대분류 MED_SUPPLY. 의료소모품인 것은 확인됨(재질은 품목별 상이, PP 추정)",
        "나프타/원유 가격급등(플라스틱 소모품 공통)",
        "대형 재해·감염병 시 소모품 수요 증가(개략)",
        ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK"], ["MASS_CASUALTY_DISASTER_EVENT"]),
    "KM_EXTRACT": (["HERBAL_MATERIAL_MULTISOURCE"],
        "한약 엑스제(성분 미특정) — 대분류 기반 개략: 다원산지 한약재",
        "원본 대분류 KM_EXTRACT. 한방 엑스제인 것은 확인됨",
        "중국 약재 수출입 이슈; 원산지국 정세·작황",
        "환절기 보약 수요; 명절(개략)",
        ["HANBANG_MULTISOURCE_ORIGIN_RISK"], ["SEASONAL_CLIMATE_PATTERN"]),
    "KM_HERB": (["HERBAL_MATERIAL_MULTISOURCE"],
        "한약재(성분 미특정) — 대분류 기반 개략: 다원산지 생약",
        "원본 대분류 KM_HERB. 한약재인 것은 확인됨",
        "중국 약재 수출입 이슈; 원산지국 정세·작황",
        "환절기 보약 수요; 명절(개략)",
        ["HANBANG_MULTISOURCE_ORIGIN_RISK"], ["SEASONAL_CLIMATE_PATTERN"]),
    "SUPPLEMENT": (["VITAMIN_SYNTHETIC_RAWMATERIAL"],
        "건강기능식품/영양보충제(성분 미특정) — 대분류 기반 개략",
        "원본 대분류 SUPPLEMENT. 영양보충제인 것은 확인됨",
        "중국 비타민·영양원료 생산 집중",
        "면역력 강조 시즌·캠페인(개략)",
        ["CHINA_VITAMIN_RAWMATERIAL_CONCENTRATION"], ["SEASONAL_CLIMATE_PATTERN"]),
    "PROMO": (["MIXED_MATERIAL_NOT_SINGLE"],
        "홍보/판촉물(재질 미특정) — 대분류 기반 개략",
        "원본 대분류 PROMO. 비의료 판촉물",
        "해당 없음", "낮은 우선순위(비의료)",
        ["NOT_APPLICABLE"], ["NOT_APPLICABLE"]),
    "FUEL": (["CRUDE_OIL_REFINED"],
        "유류(성분 미특정) — 대분류 기반 개략: 정제유",
        "원본 대분류 FUEL. 유류",
        "중동 정세·OPEC 감산·정제시설 가동중단",
        "재해 시 비상발전; 동절기 난방(개략)",
        ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK"], ["SEASONAL_CLIMATE_PATTERN"]),
    "WASTE": (["POLYVINYL_CHLORIDE_PVC", "POLYETHYLENE_PE"],
        "폐기물 처리용품(재질 미특정) — 대분류 기반 개략: PVC/PE",
        "원본 대분류 WASTE. 폐기물 처리용품",
        "나프타/원유 가격급등(석유화학 용기·봉투)",
        "낮은 우선순위",
        ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK"], ["LOW_PRIORITY_NO_TRIGGER"]),
}


def promote_family_material(fid, basis, standard_name, mat_code_list):
    """PHARMA_API_GENERIC/VITAMIN_SYNTHETIC_RAWMATERIAL 자리를 family_id 기반의
    더 정밀한 코드로 교체할 수 있으면 교체한다. (new_mat_code_list, glossary_entry_or_None)"""
    if fid in NON_SUBSTANCE_FID_OVERRIDE:
        new_code, desc, stage = NON_SUBSTANCE_FID_OVERRIDE[fid]
        if not any(c in PROMOTABLE_GENERIC_CODES for c in mat_code_list):
            return mat_code_list, None
        new_list = [new_code if c in PROMOTABLE_GENERIC_CODES else c for c in mat_code_list]
        return new_list, (new_code, desc, stage, "")
    if fid in SKIP_PROMOTION_FIDS or basis in NON_PROMOTABLE_BASIS:
        return mat_code_list, None
    if not any(c in PROMOTABLE_GENERIC_CODES for c in mat_code_list):
        return mat_code_list, None
    new_list = [fid if c in PROMOTABLE_GENERIC_CODES else c for c in mat_code_list]
    note = "미검증 일반지식 기반 — 개별 확인 권장" if basis == "general_knowledge_unverified" else ""
    return new_list, (fid, standard_name, STAGE_SYNTH, note)


def main():
    with open(IN_FILE, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError("입력 데이터가 비어 있습니다")

    out_rows = []
    no_map = 0
    override_count = 0
    promoted_count = 0
    literal_recovered = 0
    group_recovered = 0
    dynamic_glossary = {}
    for r in rows:
        cid = r["supply_cluster_id"]
        fid = r["item_family_id_suggested"]
        group = r.get("item_group_id_candidate", "")
        mat_conf = "identified"
        mapping_basis = "cluster_default"
        if r.get("family_basis") == "unresolved" and group in GROUP_FALLBACK:
            # 성분 미특정이지만 원본 대분류가 있으면 개략 원재료 배정(재질미상 회피)
            mat_code, mat, ev, sup, dem, rm_meta, dm_meta = GROUP_FALLBACK[group]
            mat_conf = "group_coarse"
            mapping_basis = "group_fallback"
            group_recovered += 1
        elif cid == "OTHER" and fid in OTHER_FAMILY_MATERIAL_EVENTS:
            mat, ev, sup, dem = OTHER_FAMILY_MATERIAL_EVENTS[fid]
            rm_meta = RAW_MATERIAL_RISK_META_OTHER.get(fid, ["UNCLASSIFIED_MATERIAL_RISK"])
            dm_meta = DEMAND_RISK_META_OTHER_DEFAULT
            mat_code = RAW_MATERIAL_CODE_OTHER.get(fid, ["MATERIAL_UNSPECIFIED"])
            if mat_code == ["MATERIAL_UNSPECIFIED"]:
                mat_conf = "unspecified"
                mapping_basis = "unmapped"
        elif cid in CLUSTER_MATERIAL_EVENTS:
            mat, ev, sup, dem = CLUSTER_MATERIAL_EVENTS[cid]
            rm_meta = RAW_MATERIAL_RISK_META.get(cid, ["UNCLASSIFIED_MATERIAL_RISK"])
            dm_meta = DEMAND_RISK_META.get(cid, ["LOW_PRIORITY_NO_TRIGGER"])
            mat_code = RAW_MATERIAL_CODE.get(cid, ["MATERIAL_UNSPECIFIED"])
        elif fid not in SENTINEL_FIDS and r.get("family_basis") in (
                "name_literal_parenthetical", "name_literal_substring",
                "general_knowledge_unverified", "web_search_2026_07_15",
                "verified_external_dictionary", "verified_ingredient_dictionary"):
            # 성분/약물은 특정됐지만(상품명 추출·성분사전·브랜드사전·웹검색) 아직
            # 어느 클러스터에도 안 엮인 경우 — family_id 자체가 실제 원료(대개 합성
            # API)다. '재질미상'으로 버리지 않고 성분코드로 살린다. 덕분에 브랜드사전에
            # 항목만 추가하면(클러스터 등록 없이도) 재질미상에서 자동으로 빠진다.
            disp = r["standard_family_name_suggested"]
            mat = f"{disp} — 성분 특정됨(원료의약품 등, 세부 클러스터 미등록)"
            ev = "성분/브랜드 식별 완료 → family_id를 원료코드로 승격(공급단계 합성 가정)"
            sup = "합성 원료의약품 다수 → 중국/인도 원료 수입 차질 가능(성분별 상이, 개별 확인 권장)"
            dem = "성분별 상이 — 개별 확인 필요"
            rm_meta = ["API_IMPORT_DEPENDENCY_CN_IN"]
            dm_meta = ["LOW_PRIORITY_NO_TRIGGER"]
            mat_code = [fid]
            mapping_basis = "family_as_ingredient_candidate"
            literal_recovered += 1
            if fid not in dynamic_glossary and fid not in MATERIAL_CODE_GLOSSARY:
                dynamic_glossary[fid] = (fid, disp, STAGE_SYNTH, "성분 특정됨, 공급단계는 합성 가정(광물·생물유래 성분은 개별 확인)")
        else:
            mat, ev, sup, dem = ("재질 상이(품목별 개별 확인 필요)", "클러스터 매핑표에 없음", "해당 없음", "낮은 우선순위(비의료)")
            rm_meta = ["UNCLASSIFIED_MATERIAL_RISK"]
            dm_meta = ["NOT_APPLICABLE"]
            mat_code = ["MATERIAL_UNSPECIFIED"]
            mat_conf = "unspecified"
            mapping_basis = "unmapped"
            no_map += 1

        # family 단위 오버라이드 — 클러스터 단위로 뭉뚱그려진 원재료를 정밀화
        if fid in FAMILY_MATERIAL_OVERRIDE:
            mat_code, mat = FAMILY_MATERIAL_OVERRIDE[fid]
            mapping_basis = "family_rule_candidate"
            mat_conf = "identified"
            override_count += 1

        subtype = r.get("item_subtype_id_candidate", "")
        if subtype in SUBTYPE_MATERIAL_OVERRIDE:
            mat_code, mat = SUBTYPE_MATERIAL_OVERRIDE[subtype]
            mapping_basis = "subtype_rule_candidate"
            mat_conf = "identified"
            override_count += 1

        # PHARMA_API_GENERIC/VITAMIN_SYNTHETIC_RAWMATERIAL -> family_id 기반 성분코드로 승격
        mat_code, glossary_entry = promote_family_material(
            fid, r["family_basis"], r["standard_family_name_suggested"], mat_code
        )
        if glossary_entry:
            promoted_count += 1
            code = glossary_entry[0]
            if code not in dynamic_glossary and code not in MATERIAL_CODE_GLOSSARY:
                dynamic_glossary[code] = glossary_entry

        if fid in COMPOSITE_SET_FIDS:
            mat_code = ["MATERIAL_UNSPECIFIED"]
            mat = "복합 혈당측정 세트 — 구성품별 BOM·수량비 확인 필요"
            ev = "검사지·란셋·알코올솜·측정기 중 둘 이상이 한 재고단위에 포함되어 단일 원자재로 환원하지 않음"
            rm_meta = ["UNCLASSIFIED_MATERIAL_RISK"]
            mat_conf = "unspecified"
            mapping_basis = "composite_set_requires_bom"

        if fid in SENTINEL_FIDS or any(code in SENTINEL_FIDS for code in mat_code):
            mat_code = ["MATERIAL_UNSPECIFIED"]
            mat = "성분 아님/미상(규격·색상·용기 등 비성분 표기) — 재질미상"
            ev = "비성분 sentinel은 원재료 코드로 승격하지 않음"
            mat_conf = "unspecified"
            mapping_basis = "sentinel_blocked"

        # 활동성 scope — 발주흔적 기준. usage_sum 은 활성 품목도 결손이 많아
        # occurrence_count/institution_count 로 판정한다. one_off(1회·단일기관)는
        # 예측대상서 제외 후보.
        def _n(k):
            try: return float(r.get(k) or 0)
            except ValueError: return 0.0
        _occ, _inst = _n("occurrence_count"), _n("institution_count")
        if _occ <= 1 and _inst <= 1:
            activity_scope = "one_off"
        elif _occ >= 20 or _inst >= 5:
            activity_scope = "active_high"
        else:
            activity_scope = "active_low"

        r2 = dict(r)
        # This pipeline proposes family/cluster-level material mappings. It does
        # not have enough product evidence to mark a material as verified.
        r2["raw_material_suggested"] = mat
        r2["raw_material_evidence"] = ev
        r2["raw_material_meta_code"] = ";".join(mat_code)
        r2["material_confidence"] = mat_conf
        r2["material_evidence_tier"] = mapping_basis
        r2["material_source_family_id"] = fid
        r2["material_source_subtype_id"] = subtype
        r2["activity_scope"] = activity_scope
        r2["raw_material_risk_meta_code"] = ";".join(rm_meta)
        r2["supply_risk_events"] = sup
        r2["demand_risk_events"] = dem
        r2["demand_risk_meta_code"] = ";".join(dm_meta)
        r2["material_event_retrieved_at"] = TODAY
        r2["material_review_status"] = "needs_review"
        r2["material_pipeline_version"] = PIPELINE_VERSION
        out_rows.append(r2)

    fieldnames = list(out_rows[0].keys())
    with open(OUT_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    with open(GLOSSARY_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "meta_code", "category", "description", "supply_stage",
            "supply_stage_note", "stage_confidence"
        ])
        raw_risk_codes = (
            set().union(*[set(v) for v in RAW_MATERIAL_RISK_META.values()])
            | set().union(*[set(v) for v in RAW_MATERIAL_RISK_META_OTHER.values()])
        )
        demand_risk_codes = (
            set().union(*[set(v) for v in DEMAND_RISK_META.values()])
            | set(DEMAND_RISK_META_OTHER_DEFAULT)
        )
        for code, desc in META_CODE_GLOSSARY.items():
            if code in raw_risk_codes:
                writer.writerow([code, "raw_material_risk", desc, "", "", "n/a"])
            if code in demand_risk_codes:
                writer.writerow([code, "demand_risk", desc, "", "", "n/a"])
        for code, desc in MATERIAL_CODE_GLOSSARY.items():
            stage, note = MATERIAL_SUPPLY_STAGE.get(code, ("", ""))
            writer.writerow([code, "raw_material", desc, stage, note, "confirmed"])
        for code, (_code, desc, stage, note) in sorted(dynamic_glossary.items()):
            if code not in MATERIAL_CODE_GLOSSARY:
                writer.writerow([code, "raw_material", desc, stage, note, "assumed_synth"])

    # 원재료코드 -> 실제 품목(대표품목명) 역인덱스
    material_to_items = {}
    for r in out_rows:
        for code in r["raw_material_meta_code"].split(";"):
            material_to_items.setdefault(code, []).append(
                (r["representative_name"], float(r["usage_sum"] or 0))
            )

    with open(REVERSE_INDEX_FILE, "w", encoding="utf-8") as f:
        f.write("# 원재료 메타코드 -> 실제 품목 역인덱스\n\n")
        f.write(f"작성일: {TODAY}\n\n")
        for code, items in sorted(material_to_items.items(), key=lambda kv: -len(kv[1])):
            desc = MATERIAL_CODE_GLOSSARY.get(code) or (dynamic_glossary[code][1] if code in dynamic_glossary else "")
            items_sorted = sorted(items, key=lambda x: -x[1])
            total_usage = sum(u for _, u in items)
            f.write(f"## {code} — {desc}\n\n")
            f.write(f"품목 {len(items)}건, 누적사용량 {total_usage:,.0f}\n\n")
            f.write("사용량 상위 10개: " + ", ".join(n for n, _ in items_sorted[:10]) + "\n\n")

    blanks = sum(1 for r in out_rows if not r["raw_material_suggested"].strip())
    blanks_mat = sum(1 for r in out_rows if not r["raw_material_meta_code"].strip())
    print("총 처리:", len(out_rows))
    print("raw_material_suggested 빈칸:", blanks)
    print("raw_material_meta_code 빈칸:", blanks_mat)
    print("고유 원재료코드 수:", len(material_to_items))
    print("클러스터 매핑표 없어서 기본값 처리된 행:", no_map)
    print("family 단위 오버라이드로 정밀화된 행:", override_count)
    print("PHARMA_API_GENERIC/VITAMIN_SYNTHETIC_RAWMATERIAL -> 성분코드로 승격된 행:", promoted_count)
    print("상품명 성분추출됐으나 클러스터 미등록 -> 성분코드로 복구된 행:", literal_recovered)
    print("대분류 폴백(group_coarse)으로 재질미상 회피된 행:", group_recovered)
    print("신규 승격 코드 수:", len(dynamic_glossary))
    print("저장:", OUT_FILE)
    print("저장:", GLOSSARY_FILE)
    print("저장:", REVERSE_INDEX_FILE)


if __name__ == "__main__":
    main()
