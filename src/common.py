"""Shared normalization and filtering rules used by both implementations."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Mapping

ANALYST_ROLES = (
    "Аналитик",
    "Системный аналитик",
    "Бизнес-аналитик",
    "BI-аналитик, аналитик данных",
    "Продуктовый аналитик",
    "Руководитель отдела аналитики",
)

_SPACE_RE = re.compile(r"\s+")

def normalize_skill(value: str | None) -> str:
    """Normalize a skill label without merging semantically different labels."""
    return _SPACE_RE.sub(" ", (value or "").strip()).lower()

@lru_cache(maxsize=50_000)
def normalize_skill_cached(value: str) -> str:
    return normalize_skill(value)

def parse_skills(value: str | None, use_cache: bool = True) -> list[str]:
    """Return unique, normalized skills in deterministic alphabetical order."""
    result: set[str] = set()
    for raw in (value or "").split(";"):
        if not raw.strip():
            continue
        skill = normalize_skill_cached(raw) if use_cache else normalize_skill(raw)
        if skill:
            result.add(skill)
    return sorted(result)

def is_analyst(row: Mapping[str, Any]) -> bool:
    return str(row.get("role", "") or "").strip() in ANALYST_ROLES

def _number(value: Any) -> float | None:
    text = str(value or "").strip().replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None

def salary_estimate(row: Mapping[str, Any]) -> float | None:
    """Estimate a comparable salary from RUR records only.

    If both range bounds are present, use their midpoint; otherwise use the
    available bound. Restrict the analysis to 10,000–1,000,000 RUR to avoid
    obvious malformed values. This is an analytical filter, not a correction
    of the source data.
    """
    if str(row.get("currency", "") or "").strip().upper() != "RUR":
        return None
    low = _number(row.get("salary_from"))
    high = _number(row.get("salary_to"))
    if low is not None and high is not None:
        value = (low + high) / 2.0
    else:
        value = low if low is not None else high
    if value is None or not 10_000 <= value <= 1_000_000:
        return None
    return value