import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .config import EXTERNAL_MASTER_DIR, PROCESSED_DATA_DIR, SAMPLE_DATA_DIR


ENRICHMENT_VERSION = "item-enrichment-v1.0"
DEFAULT_ALIAS_PATH = PROCESSED_DATA_DIR / "item_alias_candidates_v0.3.parquet"
DEFAULT_WORKLIST_PATH = PROCESSED_DATA_DIR / "item_product_worklist_v1.parquet"
DEFAULT_ALIAS_LINK_PATH = PROCESSED_DATA_DIR / "item_alias_to_product_v1.parquet"
DEFAULT_GROUPED_PATH = PROCESSED_DATA_DIR / "item_grouped_verified_v1.parquet"
DEFAULT_REVIEW_PATH = PROCESSED_DATA_DIR / "item_enrichment_review_queue_v1.csv"
DEFAULT_SAMPLE_PATH = SAMPLE_DATA_DIR / "item_grouping_review_sample_1000.csv"
DEFAULT_REPORT_PATH = PROCESSED_DATA_DIR / "item_enrichment_v1_report.json"


@dataclass(frozen=True)
class SourceProfile:
    source_id: str
    title: str
    dataset_url: str
    endpoint: str
    item_name_fields: tuple[str, ...]
    record_id_fields: tuple[str, ...]
    code_fields: tuple[str, ...] = ()
    company_fields: tuple[str, ...] = ()
    group_scope: tuple[str, ...] = ()


SOURCE_PROFILES = {
    "mfds_drug_permit": SourceProfile(
        source_id="mfds_drug_permit",
        title="식품의약품안전처 의약품 제품 허가정보",
        dataset_url="https://www.data.go.kr/data/15095677/openapi.do",
        endpoint=(
            "https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService07/"
            "getDrugPrdtPrmsnInq07"
        ),
        item_name_fields=("ITEM_NAME", "item_name"),
        record_id_fields=("ITEM_SEQ", "PRDLST_STDR_CODE", "item_seq"),
        code_fields=("EDI_CODE", "BAR_CODE", "PRDLST_STDR_CODE"),
        company_fields=("ENTP_NAME", "entp_name"),
        group_scope=("MED_ORAL", "MED_INJECT", "MED_TOPICAL", "KM_EXTRACT"),
    ),
    "mfds_device_permit": SourceProfile(
        source_id="mfds_device_permit",
        title="식품의약품안전처 의료기기 품목허가 정보",
        dataset_url="https://www.data.go.kr/data/15057456/openapi.do",
        endpoint=(
            "https://apis.data.go.kr/1471000/MdlpPrdlstPrmisnInfoService05/"
            "getMdlpPrdlstPrmisnList04"
        ),
        item_name_fields=("PRDUCT", "PRDLST_NM", "prduct"),
        record_id_fields=("PRDUCT_PRMISN_NO", "PRDLST_SN", "prductPrmisnNo"),
        company_fields=("ENTRPS", "entrps", "MNFTURER_NM"),
        group_scope=("MED_SUPPLY", "LAB_REAGENT", "DISINFECT"),
    ),
    "mfds_device_udi_product": SourceProfile(
        source_id="mfds_device_udi_product",
        title="식품의약품안전처 의료기기 표준코드별 제품정보",
        dataset_url="https://www.data.go.kr/data/15073875/openapi.do",
        endpoint=(
            "https://apis.data.go.kr/1471000/MdeqStdCdPrdtInfoService03/"
            "getMdeqStdCdPrdtInfoInq03"
        ),
        item_name_fields=("PRDCT_NM", "PRDLST_NM", "MODEL_NM", "PRODUCT_NAME"),
        record_id_fields=("UDIDI_CD", "PRDUCT_PRMISN_NO"),
        code_fields=("UDIDI_CD", "EDI_CD"),
        company_fields=("BSSH_NM", "ENTRPS_NM"),
        group_scope=("MED_SUPPLY", "LAB_REAGENT", "DISINFECT"),
    ),
    "mfds_device_udi_attributes": SourceProfile(
        source_id="mfds_device_udi_attributes",
        title="식품의약품안전처 의료기기 표준코드별 통합정보",
        dataset_url="https://www.data.go.kr/data/15073863/openapi.do",
        endpoint=(
            "https://apis.data.go.kr/1471000/MdeqStdCdUnityInfoService01/"
            "getMdeqStdCdUnityInfoInq01"
        ),
        item_name_fields=("PRDCT_NM", "PRDLST_NM"),
        record_id_fields=("UDIDI_CD",),
        code_fields=("UDIDI_CD",),
        company_fields=("BSSH_NM",),
        group_scope=("MED_SUPPLY", "LAB_REAGENT", "DISINFECT"),
    ),
    "mfds_quasi_drug_permit": SourceProfile(
        source_id="mfds_quasi_drug_permit",
        title="식품의약품안전처 의약외품 제품 허가 정보",
        dataset_url="https://www.data.go.kr/data/15095679/openapi.do",
        endpoint=(
            "https://apis.data.go.kr/1471000/QdrgPrdtPrmsnInfoService03/"
            "getQdrgPrdtPrmsnInfoInq03"
        ),
        item_name_fields=("ITEM_NAME", "PRDLST_NM"),
        record_id_fields=("ITEM_SEQ", "PRDLST_STDR_CODE", "PRDUCT_PRMISN_NO"),
        company_fields=("ENTP_NAME", "BSSH_NM"),
        group_scope=("DISINFECT", "MED_SUPPLY", "MED_TOPICAL"),
    ),
    "mfds_health_functional_food": SourceProfile(
        source_id="mfds_health_functional_food",
        title="식품의약품안전처 건강기능식품정보",
        dataset_url="https://www.data.go.kr/data/15056760/openapi.do",
        endpoint="https://apis.data.go.kr/1471000/HtfsInfoService03/getHtfsList01",
        item_name_fields=("PRDLST_NM", "PRDUCT", "ITEM_NAME"),
        record_id_fields=("PRDLST_REPORT_NO", "PRDLST_MNF_MANAGE_NO"),
        company_fields=("BSSH_NM", "ENTP_NAME"),
        group_scope=("SUPPLEMENT",),
    ),
    "pps_item_list": SourceProfile(
        source_id="pps_item_list",
        title="조달청 물품목록정보서비스",
        dataset_url="https://www.data.go.kr/data/15129417/openapi.do",
        endpoint=(
            "https://apis.data.go.kr/1230000/ao/ThngListInfoService02/"
            "getThngPrdnmLocplcAccotListInfoInfoPrdlstSearch02"
        ),
        item_name_fields=("prdctNm", "prdctKoreanNm", "prdlstNm", "prdctName"),
        record_id_fields=("prdctIdntNo", "prdctIdntfcNo"),
        code_fields=("prdctIdntNo", "prdctClsfcNo", "dtilPrdctClsfcNo"),
        company_fields=("mnfcturCmpnyNm", "entrpsNm"),
    ),
}


TRAILING_LOCAL_PACK_PATTERN = re.compile(
    r"[-_/ ]*\d+(?:\.\d+)?\s*(?:정|캡슐|캅셀|포|병|개|매|앰플|바이알|통|팩|ea|t)\s*$",
    re.IGNORECASE,
)
PARENTHETICAL_BLOCK_PATTERN = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
NON_WORD_PATTERN = re.compile(r"[^0-9a-z가-힣%]+", re.IGNORECASE)


def normalize_match_name(
    value: object,
    *,
    remove_parenthetical: bool = False,
    remove_trailing_pack: bool = False,
) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    if remove_trailing_pack:
        text = TRAILING_LOCAL_PACK_PATTERN.sub("", text)
    if remove_parenthetical:
        while PARENTHETICAL_BLOCK_PATTERN.search(text):
            text = PARENTHETICAL_BLOCK_PATTERN.sub("", text)
    replacements = (
        (r"밀리그람|밀리그램", "mg"),
        (r"마이크로그람|마이크로그램|μg|㎍", "mcg"),
        (r"그람|그램", "g"),
        (r"밀리리터|씨씨|cc", "ml"),
        (r"리터", "l"),
        (r"캅셀", "캡슐"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)\s*[*x×]\s*(?=\d)", "x", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)\.(?=\d)", "p", text)
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    return NON_WORD_PATTERN.sub("", text)


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _join_unique(values, limit: int = 20) -> str:
    unique = sorted({str(value).strip() for value in values if str(value).strip()})
    return ";".join(unique[:limit])


def _single_or_blank(values) -> str:
    unique = sorted({str(value).strip() for value in values if str(value).strip()})
    return unique[0] if len(unique) == 1 else ""


def _allocate_sample_quotas(group_counts: dict[str, int], sample_size: int) -> dict[str, int]:
    target = min(sample_size, sum(group_counts.values()))
    nonempty = {group: count for group, count in group_counts.items() if count > 0}
    if not nonempty or target == 0:
        return {group: 0 for group in group_counts}
    quotas = {group: min(count, 1) for group, count in nonempty.items()}
    remaining = target - sum(quotas.values())
    while remaining > 0:
        candidates = [group for group, count in nonempty.items() if quotas[group] < count]
        if not candidates:
            break
        group = max(
            candidates,
            key=lambda value: (
                (nonempty[value] - quotas[value]) / nonempty[value],
                nonempty[value],
                value,
            ),
        )
        quotas[group] += 1
        remaining -= 1
    return {group: quotas.get(group, 0) for group in group_counts}


def build_product_worklist(
    alias_path: Path = DEFAULT_ALIAS_PATH,
    worklist_path: Path = DEFAULT_WORKLIST_PATH,
    alias_link_path: Path = DEFAULT_ALIAS_LINK_PATH,
) -> dict[str, object]:
    import pandas as pd

    aliases = pd.read_parquet(alias_path)
    required = {
        "institution_id",
        "local_item_code",
        "local_item_key",
        "raw_item_name",
        "product_name_candidate",
        "item_group_id_candidate",
        "item_family_id_candidate",
        "standard_family_name_candidate",
        "item_subtype_id_candidate",
        "standard_subtype_name_candidate",
        "normalized_specification_candidate",
        "standard_unit_candidate",
        "dosage_form_candidate",
        "strength_candidate",
        "pack_quantity_candidate",
        "pack_unit_candidate",
        "occurrence_count",
        "usage_sum",
    }
    missing = sorted(required - set(aliases.columns))
    if missing:
        raise ValueError(f"Alias input is missing required columns: {missing}")
    if aliases["product_name_candidate"].fillna("").eq("").any():
        raise ValueError("Alias input contains an empty product_name_candidate")

    aliases = aliases.copy()
    aliases["match_name_strict"] = aliases["product_name_candidate"].map(normalize_match_name)
    aliases["match_name_core"] = aliases["product_name_candidate"].map(
        lambda value: normalize_match_name(
            value,
            remove_parenthetical=True,
            remove_trailing_pack=True,
        )
    )
    aliases["representative_item_id"] = aliases["match_name_strict"].map(
        lambda value: _stable_id("ITEM", value)
    )

    records = []
    for representative_item_id, group in aliases.groupby("representative_item_id", sort=False):
        group_ids = sorted({value for value in group["item_group_id_candidate"] if value})
        family_ids = sorted({value for value in group["item_family_id_candidate"] if value})
        subtype_ids = sorted({value for value in group["item_subtype_id_candidate"] if value})
        product_names = sorted(set(group["product_name_candidate"]))
        representative_name = max(
            product_names,
            key=lambda value: (
                int(group.loc[group["product_name_candidate"] == value, "occurrence_count"].sum()),
                -len(value),
                value,
            ),
        )
        current_status = "candidate_consistent"
        review_reason = "external_evidence_required"
        if len(group_ids) > 1 or len(family_ids) > 1 or len(subtype_ids) > 1:
            current_status = "candidate_conflict"
            review_reason = "local_candidate_conflict;external_evidence_required"
        elif not family_ids:
            current_status = "candidate_incomplete"

        records.append(
            {
                "representative_item_id": representative_item_id,
                "representative_name": representative_name,
                "match_name_strict": normalize_match_name(representative_name),
                "match_name_core": normalize_match_name(
                    representative_name,
                    remove_parenthetical=True,
                    remove_trailing_pack=True,
                ),
                "item_group_id_candidate": group_ids[0] if len(group_ids) == 1 else "",
                "item_group_candidates": ";".join(group_ids),
                "item_family_id_candidate": family_ids[0] if len(family_ids) == 1 else "",
                "standard_family_name_candidate": _single_or_blank(
                    group["standard_family_name_candidate"]
                ),
                "item_subtype_id_candidate": subtype_ids[0] if len(subtype_ids) == 1 else "",
                "standard_subtype_name_candidate": _single_or_blank(
                    group["standard_subtype_name_candidate"]
                ),
                "normalized_specification_candidate": _single_or_blank(
                    group["normalized_specification_candidate"]
                ),
                "standard_unit_candidate": _single_or_blank(group["standard_unit_candidate"]),
                "dosage_form_candidate": _single_or_blank(group["dosage_form_candidate"]),
                "strength_candidate": _single_or_blank(group["strength_candidate"]),
                "pack_quantity_candidate": _single_or_blank(group["pack_quantity_candidate"]),
                "pack_unit_candidate": _single_or_blank(group["pack_unit_candidate"]),
                "alias_row_count": int(len(group)),
                "raw_name_variant_count": int(group["raw_item_name"].nunique()),
                "institution_count": int(group["institution_id"].nunique()),
                "local_item_key_count": int(group["local_item_key"].nunique()),
                "local_code_count": int(group["local_item_code"].nunique()),
                "occurrence_count": int(group["occurrence_count"].sum()),
                "usage_sum": round(float(group["usage_sum"].sum()), 6),
                "raw_name_examples": _join_unique(group["raw_item_name"]),
                "local_code_examples": _join_unique(group["local_item_code"]),
                "local_codes": _join_unique(group["local_item_code"], limit=100_000),
                "candidate_status": current_status,
                "canonical_item_id_candidate": "",
                "canonical_item_id": "",
                "matched_source_item_name": "",
                "verified_item_name": "",
                "verified_item_group_id": "",
                "verified_family_name": "",
                "verified_subtype_name": "",
                "verified_specification": "",
                "verified_unit": "",
                "verified_material": "",
                "evidence_source": "",
                "evidence_record_id": "",
                "evidence_url": "",
                "retrieved_at": "",
                "match_method": "",
                "match_score": 0.0,
                "verification_status": "not_verified",
                "review_status": "needs_external_evidence",
                "review_reason": review_reason,
                "enrichment_version": ENRICHMENT_VERSION,
            }
        )

    worklist = pd.DataFrame.from_records(records).sort_values(
        ["occurrence_count", "representative_name"], ascending=[False, True]
    )
    links = aliases[
        [
            "institution_id",
            "local_item_code",
            "local_item_key",
            "raw_item_name",
            "product_name_candidate",
            "representative_item_id",
        ]
    ].copy()

    if len(worklist) != aliases["representative_item_id"].nunique():
        raise ValueError("Representative worklist row count does not match the unique item count")
    if len(links) != len(aliases) or links["representative_item_id"].isna().any():
        raise ValueError("Alias-to-product link output is incomplete")

    worklist_path.parent.mkdir(parents=True, exist_ok=True)
    alias_link_path.parent.mkdir(parents=True, exist_ok=True)
    worklist.to_parquet(worklist_path, index=False, compression="zstd")
    links.to_parquet(alias_link_path, index=False, compression="zstd")
    return {
        "alias_rows": int(len(aliases)),
        "representative_items": int(len(worklist)),
        "candidate_status_counts": worklist["candidate_status"].value_counts().to_dict(),
        "worklist_path": str(worklist_path),
        "alias_link_path": str(alias_link_path),
    }


def _case_insensitive_record(record: dict[str, object]) -> dict[str, object]:
    return {str(key).lower(): value for key, value in record.items()}


def _field(record: dict[str, object], names: tuple[str, ...]) -> str:
    lowered = _case_insensitive_record(record)
    for name in names:
        value = lowered.get(name.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def parse_data_go_response(response_body: bytes, content_type: str = "") -> tuple[list[dict], int]:
    if "json" in content_type.lower() or response_body.lstrip().startswith((b"{", b"[")):
        payload = json.loads(response_body.decode("utf-8-sig"))
        root = payload.get("response", payload)
        header = root.get("header", {}) if isinstance(root, dict) else {}
        result_code = str(header.get("resultCode", header.get("result_code", "00")))
        if result_code not in {"00", "0", "NORMAL_CODE"}:
            raise ValueError(f"Public API returned resultCode={result_code}: {header}")
        body = root.get("body", root) if isinstance(root, dict) else {}
        items = body.get("items", []) if isinstance(body, dict) else []
        if isinstance(items, dict):
            items = items.get("item", items)
        if isinstance(items, dict):
            items = [items]
        if items is None:
            items = []
        total = int(body.get("totalCount", len(items))) if isinstance(body, dict) else len(items)
        return [dict(item) for item in items], total

    root = ElementTree.fromstring(response_body)
    result_code = root.findtext(".//resultCode", default="00")
    if result_code not in {"00", "0", "NORMAL_CODE"}:
        message = root.findtext(".//resultMsg", default="")
        raise ValueError(f"Public API returned resultCode={result_code}: {message}")
    items = []
    for item in root.findall(".//item"):
        items.append({child.tag: child.text or "" for child in item})
    total = int(root.findtext(".//totalCount", default=str(len(items))))
    return items, total


def fetch_official_master(
    source_id: str,
    output_path: Path | None = None,
    service_key: str | None = None,
    page_size: int = 100,
    max_pages: int | None = None,
) -> dict[str, object]:
    import pandas as pd

    if source_id not in SOURCE_PROFILES:
        raise ValueError(f"Unknown source: {source_id}. Choose from {sorted(SOURCE_PROFILES)}")
    profile = SOURCE_PROFILES[source_id]
    key = unquote(service_key or os.getenv("DATA_GO_KR_SERVICE_KEY", "")).strip()
    if not key:
        raise RuntimeError("DATA_GO_KR_SERVICE_KEY is required to fetch official API data")
    output_path = output_path or EXTERNAL_MASTER_DIR / f"{source_id}.parquet"
    retrieved_at = datetime.now(timezone.utc).isoformat()
    records = []

    page = 1
    total_count = None
    while total_count is None or (page - 1) * page_size < total_count:
        if max_pages is not None and page > max_pages:
            break
        query = urlencode(
            {
                "serviceKey": key,
                "pageNo": page,
                "numOfRows": page_size,
                "type": "json",
            }
        )
        request = Request(
            f"{profile.endpoint}?{query}",
            headers={"User-Agent": "teamlex-item-enrichment/1.0"},
        )
        with urlopen(request, timeout=60.0) as response:
            response_body = response.read()
            content_type = response.headers.get("content-type", "")
        page_items, reported_total = parse_data_go_response(response_body, content_type)
        if total_count is None:
            total_count = reported_total
        if not page_items:
            break
        for item in page_items:
            source_name = _field(item, profile.item_name_fields)
            record_id = _field(item, profile.record_id_fields)
            records.append(
                {
                    "source_id": profile.source_id,
                    "source_title": profile.title,
                    "source_record_id": record_id,
                    "source_item_name": source_name,
                    "source_company": _field(item, profile.company_fields),
                    "source_code": _field(item, profile.code_fields),
                    "match_name_strict": normalize_match_name(source_name),
                    "match_name_core": normalize_match_name(
                        source_name,
                        remove_parenthetical=True,
                        remove_trailing_pack=True,
                    ),
                    "group_scope": ";".join(profile.group_scope),
                    "evidence_url": profile.dataset_url,
                    "retrieved_at": retrieved_at,
                    "source_payload_json": json.dumps(item, ensure_ascii=False, sort_keys=True),
                }
            )
        page += 1

    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        raise ValueError(f"Official source {source_id} returned no records")
    missing_identity = frame["source_record_id"].eq("") & frame["source_item_name"].eq("")
    if missing_identity.any():
        raise ValueError(
            f"Official source {source_id} has {int(missing_identity.sum())} records without ID or name"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output_path, index=False, compression="zstd")
    return {
        "source_id": source_id,
        "records": int(len(frame)),
        "reported_total_count": int(total_count or 0),
        "pages_fetched": page - 1,
        "partial": bool(total_count and len(frame) < total_count),
        "output_path": str(output_path),
        "retrieved_at": retrieved_at,
    }


def validate_official_csv(
    path: Path,
    required_columns: set[str],
    minimum_rows: int = 1,
) -> dict[str, object]:
    import pandas as pd

    signature = path.read_bytes()[:16]
    if signature.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"PK\x03\x04")):
        raise ValueError(f"Expected CSV but received a binary file: {path}")
    last_error = None
    frame = None
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            frame = pd.read_csv(path, dtype=str, encoding=encoding, keep_default_na=False)
            break
        except (UnicodeDecodeError, pd.errors.ParserError) as error:
            last_error = error
    if frame is None:
        raise ValueError(f"Could not parse official CSV {path}: {last_error}")
    missing = sorted(required_columns - set(frame.columns))
    if missing:
        raise ValueError(f"Official CSV is missing required columns: {missing}")
    if len(frame) < minimum_rows:
        raise ValueError(f"Official CSV has {len(frame)} rows; expected at least {minimum_rows}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path),
        "rows": int(len(frame)),
        "columns": frame.columns.tolist(),
        "sha256": digest,
    }


def discover_official_master_paths(directory: Path = EXTERNAL_MASTER_DIR) -> list[Path]:
    """Return only API masters produced by ``fetch_official_master``.

    The directory also contains source-specific evidence caches with different
    schemas, such as NEDrug HTML snapshots. Arbitrary Parquet discovery would
    incorrectly treat those caches as consolidated match masters.
    """
    return [
        path
        for source_id in sorted(SOURCE_PROFILES)
        if (path := directory / f"{source_id}.parquet").exists()
    ]


def match_official_masters(
    worklist_path: Path = DEFAULT_WORKLIST_PATH,
    master_paths: list[Path] | None = None,
    grouped_path: Path = DEFAULT_GROUPED_PATH,
    review_path: Path = DEFAULT_REVIEW_PATH,
    sample_path: Path = DEFAULT_SAMPLE_PATH,
    sample_size: int = 1000,
) -> dict[str, object]:
    import pandas as pd

    worklist = pd.read_parquet(worklist_path).copy()
    master_paths = master_paths or discover_official_master_paths()
    if not master_paths:
        masters = pd.DataFrame(
            columns=[
                "source_id",
                "source_title",
                "source_record_id",
                "source_item_name",
                "source_company",
                "source_code",
                "match_name_strict",
                "match_name_core",
                "group_scope",
                "evidence_url",
                "retrieved_at",
            ]
        )
    else:
        masters = pd.concat([pd.read_parquet(path) for path in master_paths], ignore_index=True)
    required_master_columns = {
        "source_id",
        "source_record_id",
        "source_item_name",
        "match_name_strict",
        "match_name_core",
        "evidence_url",
        "retrieved_at",
    }
    missing = sorted(required_master_columns - set(masters.columns))
    if missing:
        raise ValueError(f"Official master data is missing columns: {missing}")

    strict_counts = masters["match_name_strict"].value_counts()
    core_counts = masters["match_name_core"].value_counts()
    masters["match_source_code"] = masters["source_code"].map(normalize_match_name)
    code_counts = masters["match_source_code"].value_counts()
    code_unique = masters[
        masters["match_source_code"].map(code_counts).eq(1)
        & masters["match_source_code"].ne("")
    ].set_index("match_source_code", drop=False)
    strict_unique = masters[
        masters["match_name_strict"].map(strict_counts).eq(1)
        & masters["match_name_strict"].ne("")
    ].set_index("match_name_strict", drop=False)
    core_unique = masters[
        masters["match_name_core"].map(core_counts).eq(1) & masters["match_name_core"].ne("")
    ].set_index("match_name_core", drop=False)

    output_records = []
    for row in worklist.to_dict("records"):
        match = None
        method = ""
        score = 0.0
        verification_status = "not_verified"
        review_status = "needs_external_evidence"
        strict_key = row["match_name_strict"]
        core_key = row["match_name_core"]
        local_codes = {
            normalize_match_name(value)
            for value in str(row.get("local_codes", row.get("local_code_examples", ""))).split(";")
            if value.strip()
        }
        code_matches = [code_unique.loc[code] for code in local_codes if code in code_unique.index]
        if len(code_matches) == 1:
            match = code_matches[0]
            method = "official_code_exact_unique"
            score = 1.0
            verification_status = "verified_identity"
            review_status = "taxonomy_review_required"
        elif len(code_matches) > 1:
            method = "official_code_exact_conflict"
            verification_status = "ambiguous"
            review_status = "identity_review_required"
        elif strict_key in strict_unique.index:
            match = strict_unique.loc[strict_key]
            method = "official_name_exact_unique"
            score = 0.95
            verification_status = "candidate_identity"
            review_status = "identity_review_required"
        elif core_key in core_unique.index:
            match = core_unique.loc[core_key]
            method = "official_name_core_unique"
            score = 0.90
            verification_status = "candidate_identity"
            review_status = "identity_review_required"
        elif strict_key and strict_counts.get(strict_key, 0) > 1:
            method = "official_name_exact_ambiguous"
            verification_status = "ambiguous"
            review_status = "identity_review_required"

        if match is not None:
            source_id = str(match["source_id"])
            record_id = str(match["source_record_id"])
            candidate_id = f"{source_id}::{record_id}"
            row["canonical_item_id_candidate"] = candidate_id
            row["matched_source_item_name"] = str(match["source_item_name"])
            if verification_status == "verified_identity":
                row["canonical_item_id"] = candidate_id
                row["verified_item_name"] = str(match["source_item_name"])
            row["evidence_source"] = source_id
            row["evidence_record_id"] = record_id
            row["evidence_url"] = str(match["evidence_url"])
            row["retrieved_at"] = str(match["retrieved_at"])
        row["match_method"] = method
        row["match_score"] = score
        row["verification_status"] = verification_status
        row["review_status"] = review_status
        output_records.append(row)

    grouped = pd.DataFrame.from_records(output_records)
    if len(grouped) != len(worklist):
        raise ValueError("Grouped output does not preserve every representative item")
    if grouped["representative_item_id"].duplicated().any():
        raise ValueError("Grouped output has duplicate representative item IDs")

    grouped_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    grouped.to_parquet(grouped_path, index=False, compression="zstd")
    review = grouped[grouped["review_status"] != "approved"].sort_values(
        ["occurrence_count", "representative_name"], ascending=[False, True]
    )
    review.to_csv(review_path, index=False, encoding="utf-8-sig")
    if len(review) <= sample_size:
        sample = review
    else:
        group_counts = review["item_group_id_candidate"].value_counts(dropna=False).to_dict()
        quotas = _allocate_sample_quotas(group_counts, sample_size)
        sample_parts = []
        for group, frame in review.groupby("item_group_id_candidate", dropna=False):
            quota = quotas[group]
            sample_parts.append(frame.nlargest(quota, "occurrence_count"))
        sample = pd.concat(sample_parts).drop_duplicates("representative_item_id")
        if len(sample) != sample_size:
            raise ValueError(f"Expected {sample_size} review samples, selected {len(sample)}")
    sample.to_csv(sample_path, index=False, encoding="utf-8-sig")
    return {
        "representative_items": int(len(grouped)),
        "official_master_records": int(len(masters)),
        "verification_status_counts": grouped["verification_status"].value_counts().to_dict(),
        "review_rows": int(len(review)),
        "grouped_path": str(grouped_path),
        "review_path": str(review_path),
        "sample_path": str(sample_path),
    }


def write_report(payload: dict[str, object], path: Path = DEFAULT_REPORT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "enrichment_version": ENRICHMENT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evidence-based item enrichment")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build-worklist")
    build_parser.add_argument("--alias-path", type=Path, default=DEFAULT_ALIAS_PATH)
    build_parser.add_argument("--worklist-path", type=Path, default=DEFAULT_WORKLIST_PATH)
    build_parser.add_argument("--alias-link-path", type=Path, default=DEFAULT_ALIAS_LINK_PATH)

    fetch_parser = subparsers.add_parser("fetch")
    fetch_parser.add_argument("--source", choices=sorted(SOURCE_PROFILES), required=True)
    fetch_parser.add_argument("--output", type=Path)
    fetch_parser.add_argument("--page-size", type=int, default=100)
    fetch_parser.add_argument("--max-pages", type=int)

    match_parser = subparsers.add_parser("match")
    match_parser.add_argument("--worklist-path", type=Path, default=DEFAULT_WORKLIST_PATH)
    match_parser.add_argument("--master", type=Path, action="append")
    match_parser.add_argument("--sample-size", type=int, default=1000)

    args = parser.parse_args()
    try:
        if args.command == "build-worklist":
            result = build_product_worklist(
                alias_path=args.alias_path,
                worklist_path=args.worklist_path,
                alias_link_path=args.alias_link_path,
            )
        elif args.command == "fetch":
            result = fetch_official_master(
                source_id=args.source,
                output_path=args.output,
                page_size=args.page_size,
                max_pages=args.max_pages,
            )
        else:
            result = match_official_masters(
                worklist_path=args.worklist_path,
                master_paths=args.master,
                sample_size=args.sample_size,
            )
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))
    write_report(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
