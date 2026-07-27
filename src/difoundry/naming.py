from __future__ import annotations

import re
from difflib import SequenceMatcher

_SYNONYM_GROUPS = [
    {"id", "identifier", "key", "uuid", "guid", "externalid", "externalkey"},
    {"name", "title", "displayname", "legalname", "fullname", "companyname", "organizationname", "label"},
    {"email", "emailaddress", "mail"},
    {"phone", "phonenumber", "telephone", "mobile"},
    {"createdat", "createdon", "creationdate", "datecreated"},
    {"updatedat", "updatedon", "modifiedat", "modifiedon", "lastmodified"},
    {"status", "state", "lifecycle"},
    {"description", "details", "summary", "notes"},
    {"customer", "client", "account", "organization", "company"},
    {"user", "person", "contact", "member"},
    {"amount", "total", "value", "balance"},
]


def slugify(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return value or "unnamed"


def singularize(value: str) -> str:
    value = slugify(value)
    if value.endswith("ies") and len(value) > 3:
        return value[:-3] + "y"
    if value.endswith("sses"):
        return value[:-2]
    if value.endswith("s") and not value.endswith("ss") and len(value) > 3:
        return value[:-1]
    return value


def tokens(value: str) -> set[str]:
    return {part for part in slugify(value).split("_") if part}


def canonical_token(value: str) -> str:
    compact = slugify(value).replace("_", "")
    for group in _SYNONYM_GROUPS:
        if compact in group:
            return sorted(group)[0]
    return compact


def lexical_similarity(left: str, right: str) -> float:
    left_slug = slugify(left)
    right_slug = slugify(right)
    if left_slug == right_slug:
        return 1.0
    if canonical_token(left_slug) == canonical_token(right_slug):
        return 0.96
    left_tokens = tokens(left_slug)
    right_tokens = tokens(right_slug)
    jaccard = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    sequence = SequenceMatcher(None, left_slug, right_slug).ratio()
    return max(jaccard, sequence * 0.9)
