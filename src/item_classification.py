import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import logging
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen

import pandas as pd

from .config import MAPPING_DATA_DIR, PROCESSED_DATA_DIR, SAMPLE_DATA_DIR
from .item_enrichment import (
    DEFAULT_ALIAS_LINK_PATH,
    DEFAULT_WORKLIST_PATH,
    normalize_match_name,
)
from .item_normalization import FORECASTABLE_BY_GROUP


LOGGER = logging.getLogger(__name__)
CLASSIFICATION_VERSION = "classification-v1.0"
TAXONOMY_VERSION = "v1.0"

FAMILY_SUGGESTION_PATH = (
    PROCESSED_DATA_DIR
    / "item_material_pipeline"
    / "item_family_candidate_suggestions_full.csv"
)
OFFICIAL_NEDRUG_PATH = (
    PROCESSED_DATA_DIR.parent / "external" / "official" / "mfds_nedrug_web.parquet"
)
OFFICIAL_NEDRUG_REPORT_PATH = (
    PROCESSED_DATA_DIR.parent / "external" / "official" / "mfds_nedrug_web_report.json"
)
REPRESENTATIVE_OUTPUT_PATH = PROCESSED_DATA_DIR / "item_classification_candidates_v1.parquet"
LOCAL_OUTPUT_PATH = PROCESSED_DATA_DIR / "item_local_classification_candidates_v1.parquet"
REVIEW_OUTPUT_PATH = PROCESSED_DATA_DIR / "item_classification_review_queue_v1.csv"
SAMPLE_OUTPUT_PATH = SAMPLE_DATA_DIR / "item_classification_review_sample_1000.csv"
REPORT_OUTPUT_PATH = PROCESSED_DATA_DIR / "item_classification_v1_report.json"
MANUAL_DECISION_PATH = MAPPING_DATA_DIR / "item_manual_standardization_decisions.csv"
TAXONOMY_PATH = MAPPING_DATA_DIR / "item_family_taxonomy.csv"
APPROVED_CLASSIFICATION_PATH = MAPPING_DATA_DIR / "item_forecast_classification_approved.csv"

MFDS_DEVICE_CLASSIFICATION_URL = (
    "https://www.law.go.kr/LSW/admRulLsInfoP.do?admRulSeq=2100000275656"
)
MFDS_UDI_PRODUCT_URL = "https://www.data.go.kr/data/15073875/openapi.do"
MFDS_DRUG_PERMIT_URL = "https://www.data.go.kr/data/15095677/openapi.do"
MEDICAL_WASTE_RULE_URL = (
    "https://www.law.go.kr/flDownload.do?bylClsCd=110201&flSeq=162812925&gubun="
)

NEDRUG_URL_PATTERN = re.compile(
    r"https://nedrug\.mfds\.go\.kr/pbp/CCBBB01/getItemDetail\?itemSeq=(\d+)"
)
VOLUME_SPEC_PATTERN = re.compile(r"^(?:<=|>)?\d+(?:\.\d+)?(?:mL|L)$")
GAUGE_SPEC_PATTERN = re.compile(r"^\d{1,2}G(?: \((?:성인용|소아용)\))?$")


@dataclass(frozen=True)
class FamilyApprovalRule:
    group_id: str
    evidence_id: str
    evidence_url: str


FAMILY_APPROVAL_RULES = {
    "DISPOSABLE_SYRINGE": FamilyApprovalRule(
        "MED_SUPPLY", "MFDS_DEVICE_CLASSIFICATION_2026_18", MFDS_DEVICE_CLASSIFICATION_URL
    ),
    "INJECTION_NEEDLE": FamilyApprovalRule(
        "MED_SUPPLY", "MFDS_DEVICE_CLASSIFICATION_2026_18", MFDS_DEVICE_CLASSIFICATION_URL
    ),
    "BLOOD_LANCET": FamilyApprovalRule(
        "MED_SUPPLY", "MFDS_DEVICE_CLASSIFICATION_2026_18", MFDS_DEVICE_CLASSIFICATION_URL
    ),
    "INFUSION_SET": FamilyApprovalRule(
        "MED_SUPPLY", "MFDS_DEVICE_CLASSIFICATION_2026_18", MFDS_DEVICE_CLASSIFICATION_URL
    ),
    "ANGIO_CATHETER": FamilyApprovalRule(
        "MED_SUPPLY", "MFDS_DEVICE_CLASSIFICATION_2026_18", MFDS_DEVICE_CLASSIFICATION_URL
    ),
    "URINE_BAG": FamilyApprovalRule(
        "MED_SUPPLY", "MFDS_DEVICE_CLASSIFICATION_2026_18", MFDS_DEVICE_CLASSIFICATION_URL
    ),
    "MEDICAL_WASTE_CONTAINER": FamilyApprovalRule(
        "WASTE", "WASTE_RULE_APPENDIX_5_2026_03_26", MEDICAL_WASTE_RULE_URL
    ),
}

UNIT_NAMES = {
    "EA": "개수",
    "ROLL": "Roll",
    "TABLET": "정",
    "CAPSULE": "캡슐",
    "SACHET": "포",
    "BOTTLE": "병",
    "AMPULE": "앰플",
    "VIAL": "바이알",
    "SHEET": "매",
    "CONTAINER": "통",
}

PACK_UNIT_CODES = {
    "정": "TABLET",
    "T": "TABLET",
    "캡슐": "CAPSULE",
    "캅셀": "CAPSULE",
    "캡": "CAPSULE",
    "포": "SACHET",
    "병": "BOTTLE",
    "앰플": "AMPULE",
    "바이알": "VIAL",
    "매": "SHEET",
    "개": "EA",
    "EA": "EA",
    "통": "CONTAINER",
}

DOSAGE_FORM_SUBTYPES = {
    "정제": ("DRUG_TABLET", "정제"),
    "캡슐": ("DRUG_CAPSULE", "캡슐"),
    "캡슐/캅셀": ("DRUG_CAPSULE", "캡슐"),
    "시럽": ("DRUG_SYRUP", "시럽"),
    "과립": ("DRUG_GRANULE", "과립"),
    "현탁액": ("DRUG_SUSPENSION", "현탁액"),
    "내용액": ("DRUG_ORAL_LIQUID", "내용액"),
    "환": ("DRUG_PILL", "환"),
    "주사제": ("DRUG_INJECTION", "주사제"),
    "점안제": ("DRUG_EYE_DROP", "점안제"),
    "점이제": ("DRUG_EAR_DROP", "점이제"),
    "연고": ("DRUG_OINTMENT", "연고"),
    "크림": ("DRUG_CREAM", "크림"),
    "패치/파스": ("DRUG_PATCH", "패치/파스"),
    "로션": ("DRUG_LOTION", "로션"),
    "겔": ("DRUG_GEL", "겔"),
    "외용제": ("DRUG_TOPICAL_OTHER", "외용제"),
}

TAXONOMY_COLUMNS = [
    "source_item_name",
    "source_subtype_name",
    "source_specification",
    "item_family_id",
    "standard_family_name",
    "item_subtype_id",
    "standard_subtype_name",
    "item_group_id",
    "is_forecastable",
    "normalized_specification",
    "unit_code",
    "unit_name",
    "material_candidate",
    "material_mapping_status",
    "review_status",
    "taxonomy_version",
]

DECISION_COLUMNS = [
    "decision_id",
    "representative_item_id",
    "decision_action",
    "canonical_item_id",
    "verified_item_name",
    "verified_item_group_id",
    "item_family_id",
    "verified_family_name",
    "item_subtype_id",
    "verified_subtype_name",
    "verified_specification",
    "verified_unit",
    "verified_material",
    "evidence_source",
    "evidence_record_id",
    "evidence_url",
    "evidence_field",
    "retrieved_at",
    "verification_status",
    "review_status",
    "reviewer",
    "reviewed_at",
    "review_note",
    "decision_version",
]


def _text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _join_unique(values: pd.Series) -> str:
    return ";".join(sorted({_text(value) for value in values if _text(value)}))


def _single_or_blank(values: pd.Series) -> str:
    unique = sorted({_text(value) for value in values if _text(value)})
    return unique[0] if len(unique) == 1 else ""


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._heading_depth = 0
        self.text_parts: list[str] = []
        self.headings: list[str] = []
        self._heading_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "h1" and self._skip_depth == 0:
            self._heading_depth += 1
            self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "h1" and self._heading_depth:
            heading = " ".join(self._heading_parts).strip()
            if heading:
                self.headings.append(heading)
            self._heading_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        self.text_parts.append(value)
        if self._heading_depth:
            self._heading_parts.append(value)


def parse_nedrug_html(html: bytes, item_seq: str) -> dict[str, str]:
    parser = _VisibleTextParser()
    parser.feed(html.decode("utf-8", errors="replace"))
    headings = [
        heading
        for heading in parser.headings
        if "의약(외)품상세정보" not in heading and "상세정보" != heading
    ]
    source_item_name = max(headings, key=len) if headings else ""
    visible_text = " ".join(parser.text_parts)
    if not source_item_name:
        raise ValueError(f"NEDrug item {item_seq} has no product heading")
    if item_seq not in visible_text:
        raise ValueError(f"NEDrug item {item_seq} does not contain its product code")
    return {
        "source_item_name": source_item_name,
        "visible_text_normalized": normalize_match_name(visible_text),
    }


def _extract_nedrug_item_seq(value: object) -> str:
    match = NEDRUG_URL_PATTERN.search(_text(value))
    return match.group(1) if match else ""


def _family_match_token(value: object) -> str:
    name = re.sub(r"\([^)]*\)", "", _text(value)).strip()
    return normalize_match_name(name)


def collect_nedrug_targets(
    worklist: pd.DataFrame,
    suggestions: pd.DataFrame,
) -> pd.DataFrame:
    fields = [
        "representative_item_id",
        "item_family_id_suggested",
        "standard_family_name_suggested",
        "family_basis",
        "evidence_note",
    ]
    targets = worklist[
        ["representative_item_id", "representative_name", "match_name_core", "strength_candidate"]
    ].merge(suggestions[fields], on="representative_item_id", how="left", validate="one_to_one")
    targets["item_seq"] = targets["evidence_note"].map(_extract_nedrug_item_seq)
    targets = targets[
        targets["family_basis"].eq("web_search_2026_07_15")
        & targets["item_seq"].ne("")
    ].copy()
    targets["source_url"] = targets["item_seq"].map(
        lambda value: (
            "https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=" + value
        )
    )
    targets["family_match_token"] = targets["standard_family_name_suggested"].map(
        _family_match_token
    )
    targets["strength_match_tokens"] = targets["strength_candidate"].map(
        lambda value: ";".join(
            normalize_match_name(part)
            for part in _text(value).split(";")
            if normalize_match_name(part)
        )
    )
    return targets.reset_index(drop=True)


def fetch_nedrug_web_evidence(
    worklist_path: Path = DEFAULT_WORKLIST_PATH,
    suggestion_path: Path = FAMILY_SUGGESTION_PATH,
    output_path: Path = OFFICIAL_NEDRUG_PATH,
    report_path: Path = OFFICIAL_NEDRUG_REPORT_PATH,
    refresh: bool = False,
    limit: int | None = None,
    request_delay_seconds: float = 0.15,
) -> dict[str, object]:
    worklist = pd.read_parquet(worklist_path)
    suggestions = pd.read_csv(suggestion_path, dtype=str, keep_default_na=False)
    targets = collect_nedrug_targets(worklist, suggestions)
    target_groups = list(targets.groupby("item_seq", sort=True))
    if limit is not None:
        target_groups = target_groups[:limit]

    existing = pd.DataFrame()
    if output_path.exists() and not refresh:
        existing = pd.read_parquet(output_path)
    existing_ids = set(existing.get("item_seq", pd.Series(dtype=str)).astype(str))
    records = []
    errors = []
    retrieved_at = datetime.now(timezone.utc).isoformat()

    for index, (item_seq, group) in enumerate(target_groups, start=1):
        if item_seq in existing_ids:
            continue
        source_url = group.iloc[0]["source_url"]
        try:
            request = Request(
                source_url,
                headers={"User-Agent": "teamlex-evidence-classifier/1.0"},
            )
            with urlopen(request, timeout=45.0) as response:
                html = response.read()
                final_url = response.geturl()
                status = response.status
            if status != 200:
                raise ValueError(f"HTTP status {status}")
            parsed = parse_nedrug_html(html, item_seq)
            visible = parsed.pop("visible_text_normalized")
            family_ids = []
            family_tokens = []
            strength_tokens = []
            for row in group.to_dict("records"):
                family_token = _text(row["family_match_token"])
                if family_token and family_token in visible:
                    family_ids.append(_text(row["item_family_id_suggested"]))
                    family_tokens.append(family_token)
                expected_strengths = [
                    token
                    for token in _text(row["strength_match_tokens"]).split(";")
                    if token
                ]
                if expected_strengths and all(token in visible for token in expected_strengths):
                    strength_tokens.extend(expected_strengths)
            records.append(
                {
                    "source_id": "mfds_nedrug_web",
                    "item_seq": item_seq,
                    "source_item_name": parsed["source_item_name"],
                    "source_url": source_url,
                    "final_url": final_url,
                    "verified_family_ids": ";".join(sorted(set(family_ids))),
                    "verified_family_tokens": ";".join(sorted(set(family_tokens))),
                    "verified_strength_tokens": ";".join(sorted(set(strength_tokens))),
                    "retrieved_at": retrieved_at,
                    "response_sha256": hashlib.sha256(html).hexdigest(),
                    "http_status": status,
                }
            )
        except Exception as error:  # network and source format failures are recorded for review
            errors.append({"item_seq": item_seq, "url": source_url, "error": str(error)})
        if index % 25 == 0:
            LOGGER.info("Checked %s/%s NEDrug records", index, len(target_groups))
        if request_delay_seconds:
            time.sleep(request_delay_seconds)

    fetched = pd.DataFrame.from_records(records)
    combined = pd.concat([existing, fetched], ignore_index=True)
    if not combined.empty:
        combined = combined.drop_duplicates("item_seq", keep="last").sort_values("item_seq")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(output_path, index=False, compression="zstd")
    report = {
        "generated_at": retrieved_at,
        "source": "mfds_nedrug_web",
        "candidate_representative_items": int(len(targets)),
        "candidate_official_records": int(targets["item_seq"].nunique()),
        "records_available": int(len(combined)),
        "records_fetched": int(len(fetched)),
        "fetch_errors": errors,
        "output_path": str(output_path),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if target_groups and combined.empty:
        raise RuntimeError("No NEDrug evidence could be fetched")
    return report


def _derive_drug_detail(row: pd.Series) -> tuple[str, str, str, str]:
    dosage_form = _text(row.get("dosage_form_candidate")) or _text(
        row.get("dosage_form_suggested")
    )
    subtype_id, subtype_name = DOSAGE_FORM_SUBTYPES.get(dosage_form, ("", ""))
    specification = _text(row.get("strength_candidate"))
    unit_code = PACK_UNIT_CODES.get(_text(row.get("pack_unit_candidate")), "")
    return subtype_id, subtype_name, specification, unit_code


def _valid_family_specification(family_id: str, subtype_id: str, specification: str) -> bool:
    if family_id == "DISPOSABLE_SYRINGE":
        return bool(VOLUME_SPEC_PATTERN.fullmatch(specification))
    if family_id in {"INJECTION_NEEDLE", "BLOOD_LANCET"}:
        return bool(GAUGE_SPEC_PATTERN.fullmatch(specification))
    if family_id == "ANGIO_CATHETER":
        if subtype_id == "BUTTERFLY_NEEDLE":
            return specification == "나비 바늘"
        return bool(GAUGE_SPEC_PATTERN.fullmatch(specification))
    if family_id == "INFUSION_SET":
        return specification == "수액세트"
    if family_id == "URINE_BAG":
        return specification == "Urine bag" or bool(
            VOLUME_SPEC_PATTERN.fullmatch(specification)
        )
    if family_id == "MEDICAL_WASTE_CONTAINER":
        return subtype_id in {
            "RIGID_NEEDLE_BOX",
            "MEDICAL_WASTE_PE_BAG",
            "MEDICAL_WASTE_SYNTHETIC_BAG",
            "MEDICAL_WASTE_CARDBOARD_BOX",
        } and bool(VOLUME_SPEC_PATTERN.fullmatch(specification))
    return False


def _contains_semicolon_value(values: object, expected: str) -> bool:
    return expected in {_text(value) for value in _text(values).split(";") if _text(value)}


def _all_strengths_verified(values: object, specification: str) -> bool:
    verified = {_text(value) for value in _text(values).split(";") if _text(value)}
    expected = {
        normalize_match_name(value)
        for value in specification.split(";")
        if normalize_match_name(value)
    }
    return bool(expected) and expected.issubset(verified)


def build_representative_classifications(
    worklist: pd.DataFrame,
    suggestions: pd.DataFrame,
    official_nedrug: pd.DataFrame | None = None,
    reviewed_at: str | None = None,
) -> pd.DataFrame:
    suggestion_columns = [
        "representative_item_id",
        "item_family_id_suggested",
        "standard_family_name_suggested",
        "family_basis",
        "evidence_note",
        "dosage_form_suggested",
        "retrieved_at",
        "family_review_status",
    ]
    suggestion_frame = suggestions[suggestion_columns].rename(
        columns={"retrieved_at": "family_retrieved_at"}
    )
    frame = worklist.merge(
        suggestion_frame,
        on="representative_item_id",
        how="left",
        validate="one_to_one",
    ).copy()
    for column in frame.columns:
        if frame[column].dtype == object:
            frame[column] = frame[column].fillna("")

    has_existing_family = frame["item_family_id_candidate"].astype(str).str.strip().ne("")
    suggested_family = frame["item_family_id_suggested"].where(
        ~frame["item_family_id_suggested"].isin({"", "UNSPECIFIED_ITEM"}),
        "",
    )
    frame["selected_item_family_id"] = frame["item_family_id_candidate"].where(
        has_existing_family, suggested_family
    )
    frame["selected_standard_family_name"] = frame[
        "standard_family_name_candidate"
    ].where(has_existing_family, frame["standard_family_name_suggested"])
    frame["selected_item_subtype_id"] = frame["item_subtype_id_candidate"]
    frame["selected_standard_subtype_name"] = frame["standard_subtype_name_candidate"]
    frame["selected_specification"] = frame["normalized_specification_candidate"]
    frame["selected_unit_code"] = frame["standard_unit_candidate"]
    frame["classification_basis"] = "local_explicit_family_rule"
    frame.loc[~has_existing_family, "classification_basis"] = (
        "family_suggestion:" + frame.loc[~has_existing_family, "family_basis"].astype(str)
    )

    for index, row in frame.loc[~has_existing_family].iterrows():
        if not _text(row["selected_item_family_id"]):
            continue
        subtype_id, subtype_name, specification, unit_code = _derive_drug_detail(row)
        if subtype_id:
            frame.at[index, "selected_item_subtype_id"] = subtype_id
            frame.at[index, "selected_standard_subtype_name"] = subtype_name
            frame.at[index, "selected_specification"] = specification
            frame.at[index, "selected_unit_code"] = unit_code

    frame["nedrug_item_seq"] = frame["evidence_note"].map(_extract_nedrug_item_seq)
    if official_nedrug is None or official_nedrug.empty:
        official_nedrug = pd.DataFrame(
            columns=[
                "item_seq",
                "source_item_name",
                "source_url",
                "retrieved_at",
                "verified_family_ids",
                "verified_strength_tokens",
            ]
        )
    official = official_nedrug.rename(
        columns={
            "source_item_name": "official_item_name",
            "source_url": "official_source_url",
            "retrieved_at": "official_retrieved_at",
        }
    )
    frame = frame.merge(
        official[
            [
                "item_seq",
                "official_item_name",
                "official_source_url",
                "official_retrieved_at",
                "verified_family_ids",
                "verified_strength_tokens",
            ]
        ],
        left_on="nedrug_item_seq",
        right_on="item_seq",
        how="left",
        validate="many_to_one",
    )
    for column in [
        "official_item_name",
        "official_source_url",
        "official_retrieved_at",
        "verified_family_ids",
        "verified_strength_tokens",
    ]:
        frame[column] = frame[column].fillna("")

    frame["classification_status"] = "candidate_family"
    frame["review_status"] = "needs_taxonomy_review"
    frame["review_reason"] = "family_or_detail_requires_review"
    frame["decision_action"] = ""
    frame["verification_status"] = "candidate_classification"
    frame["classification_confidence"] = 0.60
    frame["canonical_item_id"] = ""
    frame["verified_item_name"] = ""
    frame["evidence_source"] = frame["family_basis"].where(
        frame["family_basis"].ne(""), "local_normalization_rule"
    )
    frame["evidence_record_id"] = ""
    frame["evidence_url"] = frame["evidence_note"].map(
        lambda value: (re.search(r"https?://\S+", _text(value)) or [""])[0]
        if re.search(r"https?://\S+", _text(value))
        else ""
    )
    frame["evidence_field"] = "representative_name"

    conflict = frame["candidate_status"].eq("candidate_conflict") | frame[
        "item_group_id_candidate"
    ].eq("")
    unresolved = frame["selected_item_family_id"].eq("")
    group_only = unresolved & ~frame["item_group_id_candidate"].isin({"", "UNCLASSIFIED"})
    frame.loc[conflict, ["classification_status", "review_status", "review_reason"]] = [
        "conflict",
        "needs_conflict_review",
        "local_group_or_family_conflict",
    ]
    frame.loc[unresolved & ~group_only & ~conflict, [
        "classification_status",
        "review_status",
        "review_reason",
    ]] = ["unresolved", "needs_external_evidence", "family_not_identified"]
    frame.loc[group_only & ~conflict, [
        "classification_status",
        "review_status",
        "review_reason",
    ]] = ["group_only", "needs_family_review", "only_item_group_identified"]

    details_complete = (
        frame["selected_item_subtype_id"].ne("")
        & frame["selected_specification"].ne("")
        & frame["selected_unit_code"].ne("")
    )
    frame.loc[
        details_complete & ~conflict & ~unresolved,
        "classification_status",
    ] = "candidate_complete"

    official_core = frame["official_item_name"].map(
        lambda value: normalize_match_name(
            value,
            remove_parenthetical=True,
            remove_trailing_pack=True,
        )
    )
    official_name_match = official_core.ne("") & official_core.eq(frame["match_name_core"])
    official_family_match = frame.apply(
        lambda row: _contains_semicolon_value(
            row["verified_family_ids"], row["selected_item_family_id"]
        ),
        axis=1,
    )
    official_strength_match = frame.apply(
        lambda row: _all_strengths_verified(
            row["verified_strength_tokens"], row["selected_specification"]
        ),
        axis=1,
    )
    approved_drug = (
        frame["nedrug_item_seq"].ne("")
        & official_name_match
        & official_family_match
        & official_strength_match
        & details_complete
        & ~conflict
        & frame["item_group_id_candidate"].isin(
            {"MED_ORAL", "MED_INJECT", "MED_TOPICAL", "KM_EXTRACT"}
        )
    )

    family_rule = frame["selected_item_family_id"].map(FAMILY_APPROVAL_RULES)
    expected_group = family_rule.map(
        lambda value: value.group_id if isinstance(value, FamilyApprovalRule) else ""
    )
    valid_family_spec = frame.apply(
        lambda row: _valid_family_specification(
            row["selected_item_family_id"],
            row["selected_item_subtype_id"],
            row["selected_specification"],
        ),
        axis=1,
    )
    approved_family = (
        has_existing_family
        & family_rule.notna()
        & expected_group.eq(frame["item_group_id_candidate"])
        & frame["candidate_status"].eq("candidate_consistent")
        & details_complete
        & valid_family_spec
        & frame["selected_unit_code"].eq("EA")
    )

    reviewed_at = reviewed_at or datetime.now(timezone.utc).isoformat()
    frame["reviewed_at"] = ""
    frame["reviewer"] = ""
    frame.loc[approved_family, [
        "classification_status",
        "review_status",
        "review_reason",
        "decision_action",
        "verification_status",
        "classification_confidence",
        "reviewer",
        "reviewed_at",
    ]] = [
        "approved_external_family",
        "approved",
        "explicit_family_spec_unit_supported_by_official_taxonomy",
        "APPROVE_FAMILY",
        "verified_family",
        0.97,
        "codex_external_evidence_pipeline",
        reviewed_at,
    ]
    for index in frame.index[approved_family]:
        rule = FAMILY_APPROVAL_RULES[frame.at[index, "selected_item_family_id"]]
        frame.at[index, "evidence_source"] = rule.evidence_id
        frame.at[index, "evidence_record_id"] = (
            rule.evidence_id + "::" + frame.at[index, "selected_item_family_id"]
        )
        frame.at[index, "evidence_url"] = rule.evidence_url
        frame.at[index, "evidence_field"] = (
            "representative_name;item_family_id;item_subtype_id;normalized_specification;unit_code"
        )

    frame.loc[approved_drug, [
        "classification_status",
        "review_status",
        "review_reason",
        "decision_action",
        "verification_status",
        "classification_confidence",
        "reviewer",
        "reviewed_at",
        "evidence_source",
        "evidence_record_id",
        "evidence_field",
    ]] = [
        "approved_external_item",
        "approved",
        "official_name_code_ingredient_strength_match",
        "APPROVE_ITEM",
        "verified_identity",
        1.0,
        "codex_external_evidence_pipeline",
        reviewed_at,
        "mfds_nedrug_web",
        "",
        "품목기준코드;제품명;원료약품및분량;제형",
    ]
    frame.loc[approved_drug, "evidence_record_id"] = frame.loc[
        approved_drug, "nedrug_item_seq"
    ]
    frame.loc[approved_drug, "evidence_url"] = frame.loc[
        approved_drug, "official_source_url"
    ]
    frame.loc[approved_drug, "canonical_item_id"] = (
        "mfds_drug::" + frame.loc[approved_drug, "nedrug_item_seq"]
    )
    frame.loc[approved_drug, "verified_item_name"] = frame.loc[
        approved_drug, "official_item_name"
    ]
    frame.loc[approved_drug, "official_retrieved_at"] = frame.loc[
        approved_drug, "official_retrieved_at"
    ].where(frame.loc[approved_drug, "official_retrieved_at"].ne(""), reviewed_at)

    frame["retrieved_at_effective"] = frame["official_retrieved_at"].where(
        frame["official_retrieved_at"].ne(""), frame["family_retrieved_at"]
    )
    frame["retrieved_at_effective"] = frame["retrieved_at_effective"].where(
        frame["retrieved_at_effective"].ne(""), reviewed_at
    )
    frame["is_forecastable"] = frame["item_group_id_candidate"].map(
        FORECASTABLE_BY_GROUP
    )
    frame["classification_version"] = CLASSIFICATION_VERSION
    return frame


def build_manual_decisions(classifications: pd.DataFrame) -> pd.DataFrame:
    approved = classifications[classifications["review_status"].eq("approved")].copy()
    records = []
    for row in approved.to_dict("records"):
        representative_id = _text(row["representative_item_id"])
        records.append(
            {
                "decision_id": f"DECISION::{CLASSIFICATION_VERSION}::{representative_id}",
                "representative_item_id": representative_id,
                "decision_action": row["decision_action"],
                "canonical_item_id": row["canonical_item_id"],
                "verified_item_name": row["verified_item_name"],
                "verified_item_group_id": row["item_group_id_candidate"],
                "item_family_id": row["selected_item_family_id"],
                "verified_family_name": row["selected_standard_family_name"],
                "item_subtype_id": row["selected_item_subtype_id"],
                "verified_subtype_name": row["selected_standard_subtype_name"],
                "verified_specification": row["selected_specification"],
                "verified_unit": row["selected_unit_code"],
                "verified_material": "",
                "evidence_source": row["evidence_source"],
                "evidence_record_id": row["evidence_record_id"],
                "evidence_url": row["evidence_url"],
                "evidence_field": row["evidence_field"],
                "retrieved_at": row["retrieved_at_effective"],
                "verification_status": row["verification_status"],
                "review_status": "approved",
                "reviewer": row["reviewer"],
                "reviewed_at": row["reviewed_at"],
                "review_note": row["review_reason"],
                "decision_version": CLASSIFICATION_VERSION,
            }
        )
    return pd.DataFrame.from_records(records, columns=DECISION_COLUMNS)


def build_approved_taxonomy(
    decisions: pd.DataFrame,
    existing_path: Path = TAXONOMY_PATH,
) -> pd.DataFrame:
    if existing_path.exists():
        existing = pd.read_csv(existing_path, dtype=str, keep_default_na=False)
    else:
        existing = pd.DataFrame(columns=TAXONOMY_COLUMNS)
    missing = [column for column in TAXONOMY_COLUMNS if column not in existing.columns]
    if missing:
        raise ValueError(f"Existing taxonomy is missing columns: {missing}")

    approved = decisions[decisions["review_status"].eq("approved")].copy()
    generated = approved[
        [
            "verified_item_group_id",
            "item_family_id",
            "verified_family_name",
            "item_subtype_id",
            "verified_subtype_name",
            "verified_specification",
            "verified_unit",
        ]
    ].drop_duplicates()
    generated = generated.rename(
        columns={
            "verified_item_group_id": "item_group_id",
            "verified_family_name": "standard_family_name",
            "verified_subtype_name": "standard_subtype_name",
            "verified_specification": "normalized_specification",
            "verified_unit": "unit_code",
        }
    )
    generated["source_item_name"] = generated["standard_family_name"]
    generated["source_subtype_name"] = generated["standard_subtype_name"]
    generated["source_specification"] = generated["normalized_specification"]
    generated["is_forecastable"] = generated["item_group_id"].map(FORECASTABLE_BY_GROUP)
    generated["unit_name"] = generated["unit_code"].map(UNIT_NAMES).fillna("")
    generated["material_candidate"] = ""
    generated["material_mapping_status"] = "separate_approved_mapping_required"
    generated["review_status"] = "approved"
    generated["taxonomy_version"] = TAXONOMY_VERSION
    generated = generated[TAXONOMY_COLUMNS]
    if generated["unit_name"].eq("").any():
        values = sorted(generated.loc[generated["unit_name"].eq(""), "unit_code"].unique())
        raise ValueError(f"Approved taxonomy has unknown unit codes: {values}")

    generated_marker = (
        existing["taxonomy_version"].eq(TAXONOMY_VERSION)
        & existing["material_mapping_status"].eq("separate_approved_mapping_required")
    )
    # v1.0 rows with this marker are owned by this pipeline. Rebuild them from
    # current decisions so withdrawn automatic approvals cannot survive reruns.
    retained = existing.loc[~generated_marker, TAXONOMY_COLUMNS]
    output = pd.concat([retained, generated], ignore_index=True)
    output = output.drop_duplicates(
        [
            "item_family_id",
            "item_subtype_id",
            "normalized_specification",
            "unit_code",
            "taxonomy_version",
        ],
        keep="last",
    )
    return output.sort_values(
        ["review_status", "item_group_id", "item_family_id", "item_subtype_id", "normalized_specification"],
        ascending=[True, True, True, True, True],
        kind="stable",
    ).reset_index(drop=True)


def build_local_classifications(
    links: pd.DataFrame,
    classifications: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    representative = classifications[
        [
            "representative_item_id",
            "classification_status",
            "review_status",
            "item_group_id_candidate",
            "selected_item_family_id",
            "selected_standard_family_name",
            "selected_item_subtype_id",
            "selected_standard_subtype_name",
            "selected_specification",
            "selected_unit_code",
            "decision_action",
            "reviewer",
            "reviewed_at",
            "evidence_source",
            "evidence_record_id",
            "evidence_url",
        ]
    ].copy()
    representative["decision_id"] = (
        "DECISION::"
        + CLASSIFICATION_VERSION
        + "::"
        + representative["representative_item_id"].astype(str)
    )
    joined = links.merge(
        representative,
        on="representative_item_id",
        how="left",
        validate="many_to_one",
    ).drop_duplicates(["local_item_key", "representative_item_id"])
    if joined["classification_status"].isna().any():
        raise ValueError("Alias links contain representative IDs missing from classification output")

    taxonomy_fields = [
        "item_group_id_candidate",
        "selected_item_family_id",
        "selected_item_subtype_id",
        "selected_specification",
        "selected_unit_code",
    ]
    joined["taxonomy_tuple"] = joined[taxonomy_fields].astype(str).agg("::".join, axis=1)
    joined["is_approved_rep"] = joined["review_status"].eq("approved")

    grouped = joined.groupby("local_item_key", observed=True, sort=False)
    local = grouped.agg(
        institution_code=("institution_id", "first"),
        item_code=("local_item_code", "first"),
        representative_item_count=("representative_item_id", "nunique"),
        approved_representative_count=("is_approved_rep", "sum"),
        taxonomy_tuple_count=("taxonomy_tuple", "nunique"),
        item_group_id=("item_group_id_candidate", _single_or_blank),
        item_family_id=("selected_item_family_id", _single_or_blank),
        standard_family_name=("selected_standard_family_name", _single_or_blank),
        item_subtype_id=("selected_item_subtype_id", _single_or_blank),
        standard_subtype_name=("selected_standard_subtype_name", _single_or_blank),
        normalized_specification=("selected_specification", _single_or_blank),
        unit_code=("selected_unit_code", _single_or_blank),
        representative_statuses=("classification_status", _join_unique),
        evidence_references=("decision_id", _join_unique),
    ).reset_index()
    local["all_representatives_approved"] = local["approved_representative_count"].eq(
        local["representative_item_count"]
    )
    local["local_review_status"] = "needs_review"
    local.loc[
        local["all_representatives_approved"] & local["taxonomy_tuple_count"].eq(1),
        "local_review_status",
    ] = "approved"

    approved_keys = set(
        local.loc[local["local_review_status"].eq("approved"), "local_item_key"]
    )
    approved_joined = joined[joined["local_item_key"].isin(approved_keys)].copy()
    approved = (
        approved_joined.groupby("local_item_key", observed=True, sort=False)
        .agg(
            institution_code=("institution_id", "first"),
            item_code=("local_item_code", "first"),
            item_family_id=("selected_item_family_id", "first"),
            item_subtype_id=("selected_item_subtype_id", "first"),
            normalized_specification=("selected_specification", "first"),
            unit_code=("selected_unit_code", "first"),
            reviewer=("reviewer", _join_unique),
            reviewed_at=("reviewed_at", "max"),
            evidence_reference=("decision_id", _join_unique),
        )
        .reset_index()
    )
    approved["taxonomy_version"] = TAXONOMY_VERSION
    approved["review_status"] = "approved"
    approved["classification_version"] = CLASSIFICATION_VERSION
    approved = approved[
        [
            "institution_code",
            "item_code",
            "local_item_key",
            "item_family_id",
            "item_subtype_id",
            "normalized_specification",
            "unit_code",
            "taxonomy_version",
            "review_status",
            "reviewer",
            "reviewed_at",
            "evidence_reference",
            "classification_version",
        ]
    ]
    if approved["local_item_key"].duplicated().any():
        raise ValueError("Approved local classification contains duplicate local_item_key values")
    return local, approved


def select_review_sample(review: pd.DataFrame, sample_size: int = 1000) -> pd.DataFrame:
    if len(review) <= sample_size:
        return review.copy()
    frame = review.copy()
    priority = {
        "conflict": 0,
        "candidate_complete": 1,
        "candidate_family": 2,
        "group_only": 3,
        "unresolved": 4,
    }
    frame["_priority"] = frame["classification_status"].map(priority).fillna(9)
    frame["_hash"] = frame["representative_item_id"].map(
        lambda value: hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    )
    frame = frame.sort_values(
        ["_priority", "usage_sum", "occurrence_count", "_hash"],
        ascending=[True, False, False, True],
        kind="stable",
    )
    strata_columns = ["classification_status", "item_group_id_candidate"]
    first_per_stratum = frame.groupby(strata_columns, dropna=False, sort=False).head(1)
    conflict_rows = frame[frame["classification_status"].eq("conflict")]
    high_priority = frame.head(sample_size // 2)
    selected = pd.concat([first_per_stratum, conflict_rows, high_priority]).drop_duplicates(
        "representative_item_id"
    )
    if len(selected) < sample_size:
        fill = frame[~frame["representative_item_id"].isin(selected["representative_item_id"])]
        selected = pd.concat([selected, fill.head(sample_size - len(selected))])
    return selected.head(sample_size).drop(columns=["_priority", "_hash"])


def run_item_classification(
    worklist_path: Path = DEFAULT_WORKLIST_PATH,
    suggestion_path: Path = FAMILY_SUGGESTION_PATH,
    alias_link_path: Path = DEFAULT_ALIAS_LINK_PATH,
    official_path: Path = OFFICIAL_NEDRUG_PATH,
    sample_size: int = 1000,
) -> dict[str, object]:
    worklist = pd.read_parquet(worklist_path)
    suggestions = pd.read_csv(suggestion_path, dtype=str, keep_default_na=False)
    links = pd.read_parquet(alias_link_path)
    official = pd.read_parquet(official_path) if official_path.exists() else pd.DataFrame()
    reviewed_at = datetime.now(timezone.utc).isoformat()

    classifications = build_representative_classifications(
        worklist,
        suggestions,
        official,
        reviewed_at=reviewed_at,
    )
    if len(classifications) != len(worklist):
        raise ValueError("Representative classification output does not preserve all worklist rows")
    if classifications["representative_item_id"].duplicated().any():
        raise ValueError("Representative classification output has duplicate IDs")

    decisions = build_manual_decisions(classifications)
    taxonomy = build_approved_taxonomy(decisions)
    local, approved_local = build_local_classifications(links, classifications)

    approved_taxonomy_keys = set(
        taxonomy.loc[taxonomy["review_status"].eq("approved")]
        .apply(
            lambda row: (
                row["item_family_id"],
                row["item_subtype_id"],
                row["normalized_specification"],
                row["unit_code"],
                row["taxonomy_version"],
            ),
            axis=1,
        )
        .tolist()
    )
    approved_mapping_keys = set(
        approved_local.apply(
            lambda row: (
                row["item_family_id"],
                row["item_subtype_id"],
                row["normalized_specification"],
                row["unit_code"],
                row["taxonomy_version"],
            ),
            axis=1,
        ).tolist()
    )
    missing_taxonomy = approved_mapping_keys - approved_taxonomy_keys
    if missing_taxonomy:
        raise ValueError(f"Approved local mappings reference missing taxonomy rows: {missing_taxonomy}")

    review_columns = [
        "representative_item_id",
        "representative_name",
        "raw_name_examples",
        "local_code_examples",
        "institution_count",
        "occurrence_count",
        "usage_sum",
        "item_group_id_candidate",
        "item_group_candidates",
        "selected_item_family_id",
        "selected_standard_family_name",
        "selected_item_subtype_id",
        "selected_standard_subtype_name",
        "selected_specification",
        "selected_unit_code",
        "classification_status",
        "classification_basis",
        "family_basis",
        "evidence_note",
        "evidence_url",
        "review_status",
        "review_reason",
    ]
    review = classifications.loc[
        ~classifications["review_status"].eq("approved"), review_columns
    ].sort_values(
        ["usage_sum", "occurrence_count", "representative_name"],
        ascending=[False, False, True],
        kind="stable",
    )
    sample = select_review_sample(review, sample_size)

    for path in [
        REPRESENTATIVE_OUTPUT_PATH,
        LOCAL_OUTPUT_PATH,
        REVIEW_OUTPUT_PATH,
        SAMPLE_OUTPUT_PATH,
        MANUAL_DECISION_PATH,
        TAXONOMY_PATH,
        APPROVED_CLASSIFICATION_PATH,
        REPORT_OUTPUT_PATH,
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
    classifications.to_parquet(REPRESENTATIVE_OUTPUT_PATH, index=False, compression="zstd")
    local.to_parquet(LOCAL_OUTPUT_PATH, index=False, compression="zstd")
    review.to_csv(REVIEW_OUTPUT_PATH, index=False, encoding="utf-8-sig")
    sample.to_csv(SAMPLE_OUTPUT_PATH, index=False, encoding="utf-8-sig")
    decisions.to_csv(MANUAL_DECISION_PATH, index=False, encoding="utf-8-sig")
    taxonomy.to_csv(TAXONOMY_PATH, index=False)
    approved_local.to_csv(APPROVED_CLASSIFICATION_PATH, index=False)

    report = {
        "classification_version": CLASSIFICATION_VERSION,
        "generated_at": reviewed_at,
        "input": {
            "representative_items": int(len(worklist)),
            "alias_links": int(len(links)),
            "official_nedrug_records": int(len(official)),
        },
        "representative_classification_status_counts": classifications[
            "classification_status"
        ].value_counts().to_dict(),
        "representative_group_counts": classifications[
            "item_group_id_candidate"
        ].value_counts(dropna=False).to_dict(),
        "approved_representative_items": int(classifications["review_status"].eq("approved").sum()),
        "approved_external_items": int(
            classifications["classification_status"].eq("approved_external_item").sum()
        ),
        "approved_external_families": int(
            classifications["classification_status"].eq("approved_external_family").sum()
        ),
        "review_rows": int(len(review)),
        "sample_rows": int(len(sample)),
        "local_item_keys": int(len(local)),
        "approved_local_item_keys": int(len(approved_local)),
        "approved_taxonomy_rows": int(taxonomy["review_status"].eq("approved").sum()),
        "quality_gates": {
            "representative_rows_preserved": len(classifications) == len(worklist),
            "representative_ids_unique": not classifications[
                "representative_item_id"
            ].duplicated().any(),
            "approved_local_keys_unique": not approved_local["local_item_key"].duplicated().any(),
            "approved_local_taxonomy_references_complete": not missing_taxonomy,
            "unapproved_representatives_excluded_from_approved_local_mapping": bool(
                local.loc[local["local_review_status"].eq("approved"), "all_representatives_approved"].all()
            ),
        },
        "outputs": {
            "representative_candidates": str(REPRESENTATIVE_OUTPUT_PATH),
            "local_candidates": str(LOCAL_OUTPUT_PATH),
            "review_queue": str(REVIEW_OUTPUT_PATH),
            "review_sample": str(SAMPLE_OUTPUT_PATH),
            "manual_decisions": str(MANUAL_DECISION_PATH),
            "taxonomy": str(TAXONOMY_PATH),
            "approved_local_classification": str(APPROVED_CLASSIFICATION_PATH),
        },
        "external_evidence_sources": {
            "mfds_drug_permit": MFDS_DRUG_PERMIT_URL,
            "mfds_nedrug": "https://nedrug.mfds.go.kr/",
            "mfds_device_classification": MFDS_DEVICE_CLASSIFICATION_URL,
            "mfds_udi_product": MFDS_UDI_PRODUCT_URL,
            "medical_waste_rule": MEDICAL_WASTE_RULE_URL,
        },
    }
    REPORT_OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evidence-gated item classification")
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch_parser = subparsers.add_parser("fetch-official-web")
    fetch_parser.add_argument("--refresh", action="store_true")
    fetch_parser.add_argument("--limit", type=int)
    fetch_parser.add_argument("--delay", type=float, default=0.15)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--sample-size", type=int, default=1000)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--refresh", action="store_true")
    run_parser.add_argument("--limit", type=int)
    run_parser.add_argument("--delay", type=float, default=0.15)
    run_parser.add_argument("--sample-size", type=int, default=1000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.command in {"fetch-official-web", "run"}:
        fetch_report = fetch_nedrug_web_evidence(
            refresh=args.refresh,
            limit=args.limit,
            request_delay_seconds=args.delay,
        )
        print(json.dumps(fetch_report, ensure_ascii=False, indent=2))
    if args.command in {"build", "run"}:
        report = run_item_classification(sample_size=args.sample_size)
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
