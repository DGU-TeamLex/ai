#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
item_family_candidate_suggestions.csv 전체 1,000건에 빈칸 없이 '공급 카테고리
클러스터'를 붙인다. 보건소가 실제로 자주 다루는 의료용품/의약품은 최대한 큰
묶음으로 모으고, 의료와 무관하거나 확정할 수 없는 건 전부 '기타'로 보낸다.

이 클러스터는 item_family_id_suggested(정밀 단위, 예: ACETAMINOPHEN)보다 굵은
2단계 그룹이다 — 정밀 family는 그대로 남겨두고, 그 위에 실무에서 바로 쓸 수
있는 큰 카테고리를 얹는 것. 정밀도가 필요하면 item_family_id, 운영 편의가
필요하면 이 클러스터를 쓰면 된다.
"""
import csv
import sys

if len(sys.argv) != 4:
    raise SystemExit(
        "usage: build_supply_clusters.py <input.csv> <output.csv> <summary.md>"
    )

IN_FILE = sys.argv[1]
OUT_FILE = sys.argv[2]
SUMMARY_FILE = sys.argv[3]

OTHER = ("OTHER", "기타")

# cluster_id, cluster_name_kr -> family_id 목록
CLUSTERS = [
    ("PAIN_NSAID", "해열진통소염제(경구·외용)", [
        "ACETAMINOPHEN", "ASPIRIN", "IBUPROFEN", "NAPROXEN", "KETOPROFEN", "PIROXICAM",
        "METHYL_SALICYLATE", "TOPICAL_PATCH_UNSPECIFIED", "TOPICAL_NSAID_UNCONFIRMED", "나프록센",
        "TRAMADOL_ACETAMINOPHEN", "CELECOXIB", "ACECLOFENAC", "LOXOPROFEN", "TALNIFLUMATE",
        "DICLOFENAC", "CELECOXIB_HERBAL", "NAPROXEN_ESOMEPRAZOLE",
        "PROPIONIC_ACID_NSAID",
    ]),
    ("ANTIHISTAMINE_ALLERGY", "항히스타민·알레르기약", [
        "CHLORPHENIRAMINE", "LEVOCETIRIZINE", "COLD_COMBO_ANTIHISTAMINE_DECONGESTANT",
        "ANTIHISTAMINE_MOTION_SICKNESS", "디멘히드리네이트", "DIMENHYDRINATE", "CETIRIZINE",
        "AZELASTINE", "PIPRINHYDRINATE", "PSEUDOEPHEDRINE", "EBASTINE",
        "ANTIHISTAMINE",
    ]),
    ("GI_DIGESTIVE", "소화기계약(제산·소화효소·지사·완하)", [
        "ALMAGATE", "MAGNESIUM_HYDROXIDE", "ALUMINUM_MAGNESIUM_ANTACID", "DIGESTIVE_ENZYME",
        "LOPERAMIDE", "BISACODYL", "SENNA_LAXATIVE", "CIMETIDINE", "FAMOTIDINE",
        "HYOSCINE_BUTYLBROMIDE", "MUGWORT_EXTRACT", "DIGESTIVE_UNCONFIRMED", "수산화마그네슘", "무당",
        "URSODEOXYCHOLIC_ACID", "NIZATIDINE", "DOMPERIDONE", "METOCLOPRAMIDE", "MOSAPRIDE",
        "ESOMEPRAZOLE", "OMEPRAZOLE", "LANSOPRAZOLE", "REBAMIPIDE", "PINAVERIUM", "ANTACID",
        "ANTIDIARRHEAL_COMBO", "TRIMEBUTINE", "LEVOSULPIRIDE",
        "PROTON_PUMP_INHIBITOR",
    ]),
    ("RESPIRATORY", "호흡기·진해거담약", [
        "CARBOCISTEINE", "BROMHEXINE", "AMBROXOL", "AMBROXOL_CLENBUTEROL", "IPRATROPIUM",
        "AMINOPHYLLINE", "COUGH_COLD_COMBO", "MUCOLYTIC_AGENT", "레보드로프로피진", "ERDOSTEINE",
        "ACETYLCYSTEINE", "IVY_LEAF_EXTRACT", "LEVODROPROPIZINE", "DOXOFYLLINE",
        "ACEBROPHYLLINE", "TULOBUTEROL", "PROCATEROL", "MONTELUKAST", "PRANLUKAST",
    ]),
    ("ANTIBIOTIC", "항생제", [
        "AMOXICILLIN_CLAVULANATE", "CO_TRIMOXAZOLE", "FUSIDIC_ACID", "토브라마이신",
        "AMOXICILLIN", "METRONIDAZOLE", "TOBRAMYCIN", "CLARITHROMYCIN", "AZITHROMYCIN",
        "CEFACLOR", "CEFIXIME", "CEFDINIR", "CEFUROXIME_AXETIL", "LEVOFLOXACIN",
        "CIPROFLOXACIN", "MOXIFLOXACIN", "OFLOXACIN", "CLINDAMYCIN", "ROXITHROMYCIN",
        "DOXYCYCLINE", "MINOCYCLINE",
        "CEPHALOSPORIN", "FLUOROQUINOLONE",
    ]),
    ("ANTITUBERCULAR", "항결핵제", [
        "ISONIAZID", "RIFAMPIN", "ETHAMBUTOL", "RIFAMPICIN", "RIFAMPICIN_ISONIAZID", "ISONIAZID_RIFAMPICIN",
    ]),
    ("VITAMIN_NUTRITION", "비타민·영양보충제", [
        "VITAMIN_B_COMPLEX", "VITAMIN_B_COMPLEX_C", "VITAMIN_C", "FOLIC_ACID", "IRON_SUPPLEMENT",
        "IRON_FMOA", "PROBIOTICS", "MULTIVITAMIN_MINERAL", "NUTRITIONAL_SUPPLEMENT_GENERIC",
        "MEDICAL_NUTRITION_FORMULA", "THIAMINE_B1", "PYRIDOXINE_B6", "PREGNANCY_SUPPLEMENT",
        "VITAMIN_UNSPECIFIED", "CALCIUM_VITAMIN_D", "OMEGA3_FATTY_ACID", "VITAMIN_D",
        "CHOLINE_ALFOSCERATE", "GRAPE_SEED_EXTRACT", "ST_JOHNS_WORT_EXTRACT",
    ]),
    ("CARDIOVASCULAR_METABOLIC", "순환기·대사질환약(혈압·혈당·이뇨)", [
        "AMLODIPINE", "INSULIN_GLARGINE", "HYDROCHLOROTHIAZIDE", "로사르탄칼륨", "아토르바스타틴칼슘수화물",
        "METFORMIN", "TELMISARTAN_AMLODIPINE", "VALSARTAN_AMLODIPINE", "LOSARTAN_AMLODIPINE", "LOSARTAN",
        "LINAGLIPTIN_METFORMIN", "DPP4_METFORMIN_COMBO_GENERIC", "ROSUVASTATIN_EZETIMIBE", "GLIMEPIRIDE",
        "ARB_AMLODIPINE_COMBO_UNCONFIRMED", "VALSARTAN_HCTZ", "TELMISARTAN", "TELMISARTAN_HCTZ",
        "LOSARTAN_HCTZ", "CANDESARTAN_AMLODIPINE", "ROSUVASTATIN", "ATORVASTATIN_EZETIMIBE", "ATORVASTATIN",
        "EMPAGLIFLOZIN_METFORMIN", "TENELIGLIPTIN_METFORMIN", "GEMIGLIPTIN_METFORMIN", "SITAGLIPTIN",
        "METOPROLOL", "ATENOLOL", "GINKGO_LEAF_EXTRACT", "TAMSULOSIN", "VITAMIN_D", "KALLIKREIN",
        "TELMISARTAN_HCTZ", "ATORVASTATIN_EZETIMIBE", "CANDESARTAN_HCTZ", "PIOGLITAZONE",
        "GLIMEPIRIDE_METFORMIN", "OLMESARTAN", "OLMESARTAN_AMLODIPINE", "OLMESARTAN_AMLODIPINE_HCTZ",
        "VALSARTAN", "SAXAGLIPTIN_METFORMIN", "SITAGLIPTIN_METFORMIN", "EMPAGLIFLOZIN_LINAGLIPTIN",
        "FIMASARTAN_AMLODIPINE", "VILDAGLIPTIN", "BISOPROLOL", "FELODIPINE", "ENALAPRIL",
        "TERAZOSIN", "FINASTERIDE", "AMLODIPINE_ATORVASTATIN", "EZETIMIBE_FENOFIBRATE",
        "PRAVASTATIN_FENOFIBRATE", "SOLIFENACIN",
        "ANGIOTENSIN_RECEPTOR_BLOCKER", "DIHYDROPYRIDINE_CCB", "STATIN", "DPP4_INHIBITOR",
    ]),
    ("DERM_TOPICAL", "피부외용제(항진균·스테로이드·창상연고)", [
        "KETOCONAZOLE", "CLOTRIMAZOLE", "CENTELLA_EXTRACT", "BETAMETHASONE_CLOTRIMAZOLE_GENTAMICIN",
        "CALAMINE", "TRIAMCINOLONE_ORAL", "DEXAMETHASONE", "INSECT_BITE_TOPICAL_UNCONFIRMED",
        "TOPICAL_UNCONFIRMED", "센텔라정량추출물", "무피로신", "테르비나핀염산염", "프레드니카르베이트",
        "클로트리마졸", "ACYCLOVIR", "SKINCARE_COSMETIC_TOPICAL", "우레아",
        "METHYLPREDNISOLONE", "BETAMETHASONE", "BETAMETHASONE_GENTAMICIN", "UREA_TOPICAL",
        "GENTAMICIN", "ITRACONAZOLE", "CENTELLA_ASIATICA_EXTRACT", "MUPIROCIN", "TERBINAFINE",
        "PREDNICARBATE", "UREA", "CLOTRIMAZOLE", "FLUCONAZOLE", "AMOROLFINE", "CLOBETASOL_PROPIONATE",
        "MOMETASONE_FUROATE", "HYDROCORTISONE", "DESONIDE", "DESOXIMETASONE", "PREDNISOLONE",
        "ISOTRETINOIN", "TACROLIMUS", "PIMECROLIMUS", "CICLOPIROX", "SILVER_SULFADIAZINE",
    ]),
    ("ANESTHETIC_MUSCLE", "마취·근이완제", [
        "LIDOCAINE", "LIDOCAINE_TOPICAL", "EPERISONE", "클로르페네신카르바메이트", "BENZYDAMINE",
        "CHLORPHENESIN_CARBAMATE", "PRIDINOL", "BACLOFEN", "ANTIINFLAMMATORY_ENZYME", "BROMELAIN",
    ]),
    ("ANTISEPTIC_DISINFECT", "소독·멸균제", [
        "CHLORHEXIDINE", "POVIDONE_IODINE", "ALCOHOL_SWAB", "ANTISEPTIC_SWAB", "HYDROGEN_PEROXIDE",
        "MOUTHWASH_ANTISEPTIC", "ALCOHOL_ETHANOL_CHEMICAL",
    ]),
    ("IV_FLUID", "수액·생리식염수", [
        "IV_FLUID_SALINE", "IV_FLUID_RINGERS_LACTATE", "SODIUM_ALGINATE", "염화나트륨",
        "OGTT_GLUCOSE_SOLUTION", "IV_ADMIN_SET", "DIALYSIS_FLUID_CONTAINER",
        "SODIUM_CHLORIDE", "NORMAL_SALINE", "GLUCOSE", "WATER_FOR_INJECTION", "POTASSIUM_CHLORIDE",
        "INFUSION_SET", "DIALYSATE_CONTAINER", "IV_FLUID_CONTAINER",
    ]),
    ("VACCINE", "백신", ["HEPATITIS_B_VACCINE", "INFLUENZA_VACCINE"]),
    ("LAB_DIAGNOSTIC", "검사·진단시약/기구", [
        "BLOOD_GLUCOSE_TEST_STRIP", "BLOOD_GLUCOSE_LIPID_TEST_STRIP", "LIPID_TEST_STRIP",
        "HEMOGLOBIN_TEST_STRIP", "CLINICAL_CHEMISTRY_TEST", "COTININE_TEST_KIT", "CULTURE_MEDIUM",
        "LAB_CUVETTE", "LAB_PIPETTE_TIP", "BLOOD_COLLECTION_TUBE", "SPECIMEN_CONTAINER",
        "PREGNANCY_TEST_KIT", "THERMOMETER_DEVICE", "GLUCOSE_METER_DEVICE",
        "PREGNANCY_TEST", "BLOOD_GLUCOSE_TESTING_SET", "BLOOD_GLUCOSE_METER_KIT",
    ]),
    ("INJECTION_PHLEBOTOMY", "주사·채혈 소모품(금속/플라스틱)", [
        "DISPOSABLE_SYRINGE", "LANCET", "ACUPUNCTURE_NEEDLE", "TONGUE_DEPRESSOR",
        "IV_CATHETER_ANGIONEEDLE",
        "BLOOD_LANCET", "INJECTION_NEEDLE", "ANGIO_CATHETER",
    ]),
    ("WOUND_CARE", "창상피복재·드레싱류", [
        "WOUND_DRESSING_HYDROCOLLOID", "WOUND_DRESSING_POLYURETHANE_FOAM", "WOUND_DRESSING_FILM",
        "WOUND_DRESSING_NONWOVEN", "WOUND_CLOSURE_STRIP", "ADHESIVE_BANDAGE", "SELF_ADHERENT_WRAP",
        "KINESIOLOGY_TAPE", "COTTON_GAUZE", "GAUZE_BANDAGE_DRESSING_GENERIC", "SUCTION_TIP_MEDICAL_SUPPLY",
        "STERILIZATION_PACKAGING_EO",
        "MEDICAL_GAUZE", "EO_STERILIZATION_PACKAGING",
    ]),
    ("GLOVE_PPE", "장갑·개인보호구", ["MEDICAL_GLOVE", "FACE_MASK", "MEDICAL_MASK"]),
    ("ORAL_DENTAL", "구강위생·치과용품", [
        "DENTAL_HYGIENE_CONSUMER", "FLUORIDE_DENTAL", "DENTURE_CLEANSER", "DENTURE_ADHESIVE",
    ]),
    ("SMOKING_CESSATION", "금연 보조용품", ["NICOTINE_REPLACEMENT", "SMOKING_CESSATION_DEVICE"]),
    ("HANBANG", "한방제제", ["HANBANG_MIXED_HERBAL_EXTRACT", "HOMEOPATHIC_PLANT_COMPLEX", "일체형"]),
    ("PEST_CONTROL", "방역·해충관리", ["INSECT_REPELLENT", "DIFLUBENZURON_IGR"]),
    ("HYGIENE_CONSUMABLE", "위생 소모품(물티슈 등)", ["HYGIENE_TISSUE"]),
    ("FAMILY_PLANNING", "가족계획용품", ["CONDOM"]),
    ("ANTHELMINTIC", "구충제", ["ANTHELMINTIC_GENERIC"]),
    ("WASTE_MANAGEMENT", "의료폐기물 처리용품", [
        "MEDICAL_WASTE_SUPPLY", "MEDICAL_WASTE_CONTAINER", "MEDICAL_WASTE_RFID_TAG"
    ]),
    ("FUEL_ENERGY", "유류", ["FUEL"]),
    ("URINARY_DRAINAGE_SUPPLY", "도뇨·배액 소모품", ["URINE_BAG"]),
]

FAMILY_TO_CLUSTER = {}
for cid, cname, fams in CLUSTERS:
    for fam in fams:
        FAMILY_TO_CLUSTER[fam] = (cid, cname)


def main():
    with open(IN_FILE, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    out_rows = []
    cluster_counts = {}
    for r in rows:
        fid = r["item_family_id_suggested"]
        cid, cname = FAMILY_TO_CLUSTER.get(fid, OTHER)
        r2 = dict(r)
        r2["supply_cluster_id"] = cid
        r2["supply_cluster_name"] = cname
        out_rows.append(r2)
        cluster_counts[(cid, cname)] = cluster_counts.get((cid, cname), 0) + 1

    fieldnames = list(out_rows[0].keys())
    with open(OUT_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    total = len(out_rows)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(f"# 공급 카테고리 클러스터 요약 (전체 {total:,}건, 빈칸 없음)\n\n")
        f.write("정밀 단위는 `item_family_id_suggested`, 운영/원자재 리스크 관리용 큰 묶음은 이 클러스터를 쓴다.\n")
        f.write("의료와 무관하거나 성분/재질을 확정할 수 없는 건 전부 `기타`로 모았다.\n\n")
        f.write("| 클러스터 | 건수 | 비중 |\n|---|---:|---:|\n")
        for (cid, cname), n in sorted(cluster_counts.items(), key=lambda kv: -kv[1]):
            f.write(f"| {cname} ({cid}) | {n} | {n/total*100:.1f}% |\n")

    print("총 처리:", len(out_rows))
    print("클러스터 수(기타 제외):", len([c for c in cluster_counts if c[0] != 'OTHER']))
    print()
    for (cid, cname), n in sorted(cluster_counts.items(), key=lambda kv: -kv[1]):
        print(f"{cname:35s} ({cid:26s}) {n:4d}건")
    print("\n저장:", OUT_FILE)
    print("저장:", SUMMARY_FILE)


if __name__ == "__main__":
    main()
