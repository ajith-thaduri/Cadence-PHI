"""Canonical Cadence PHI taxonomy."""
from __future__ import annotations

HIPAA_CATEGORIES: list[str] = [
    "NAME", "RELATIVE_NAME", "PROVIDER_NAME", "FACILITY_NAME", "ADDRESS", "CITY", "STATE",
    "ZIP", "DATE", "DOB", "PHONE", "EMAIL", "MEMBER_ID", "MRN", "ACCOUNT_ID", "NPI", "URL",
    "OTHER_UNIQUE_ID",
]
CONTEXTUAL_CATEGORIES: list[str] = [
    "EMPLOYER", "ORGANIZATION", "LANGUAGE", "GEOGRAPHIC_CONTEXT", "CARE_TEAM_MEMBER",
]
CANONICAL_LABELS: list[str] = HIPAA_CATEGORIES + CONTEXTUAL_CATEGORIES
UNMAPPED = "UNMAPPED"

HIGH_RISK: list[str] = [
    "NAME", "PROVIDER_NAME", "FACILITY_NAME", "DOB", "ADDRESS", "PHONE", "EMAIL",
    "MEMBER_ID", "MRN", "ACCOUNT_ID", "NPI", "DATE",
]
ID_CATEGORIES: list[str] = ["MEMBER_ID", "MRN", "ACCOUNT_ID", "NPI", "OTHER_UNIQUE_ID"]

# Decision-table rows that aggregate several canonical categories.
CATEGORY_GROUPS: dict[str, list[str]] = {
    "ID": ID_CATEGORIES,
    "DATE/DOB": ["DATE", "DOB"],
    "ADDRESS (any location)": ["ADDRESS", "CITY", "STATE", "ZIP"],
}

SUBSETS: list[str] = [
    "CLEAN_CLINICAL", "NOISY_ASR", "TELEPHONE_CONVERSATION", "FACE_TO_FACE_DICTATION", "CONTEXTUAL_PHI", "DATE_FORMS", "NEGATIVE_CONTROL",
    "CARE_PLAN", "MEMBER_SUMMARY", "TIMELINE", "CARE_ASSISTANT", "SPANISH",
    "MIXED_ENGLISH_SPANISH", "LONG_DOCUMENTS", "HARD_IDENTIFIERS",
]
NOISE_LEVELS: list[str] = ["clean", "low", "medium", "high"]


def is_canonical(label: str) -> bool:
    return label in CANONICAL_LABELS
