"""Budget parsing helpers for natural-language travel requests."""

from __future__ import annotations

import re

from app.utils.option_parsing import _parse_small_number


NUMBER_TOKEN_PATTERN = r"\d{1,5}|one|two|three|four|five|six|seven|eight|nine|ten|a"


def _normalize_control_message(message: str) -> str:
    normalized = message.lower().replace("'", "")
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _parse_duration_number(value: str | None) -> int | None:
    text = str(value or "").strip().lower()
    if text == "a":
        return 1
    return _parse_small_number(text)


def _extract_budget_amount(message: str) -> float | None:
    raw = str(message or "").lower().replace(",", "")
    normalized = _normalize_control_message(raw)
    if not any(
        phrase in normalized
        for phrase in [
            "budget",
            "under",
            "less than",
            "within my budget",
            "pound",
            "gbp",
        ]
    ) and "Â£" not in raw:
        return None

    if re.search(r"\b(?:a|one)\s+thousand\b", normalized):
        return 1000.0
    thousand_match = re.search(rf"\b({NUMBER_TOKEN_PATTERN})\s+thousand\b", normalized)
    if thousand_match:
        value = _parse_duration_number(thousand_match.group(1))
        if value:
            return float(value * 1000)
    money_match = re.search(
        r"(?:Â£|gbp\s*)?(\d{2,6})(?:\s*(?:pounds?|gbp))?",
        raw,
        flags=re.IGNORECASE,
    )
    if money_match:
        return float(money_match.group(1))
    return None
