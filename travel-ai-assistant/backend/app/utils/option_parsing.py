"""Option-number parsing helpers for chat follow-up messages."""

from __future__ import annotations

import re


NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
}


def _normalize_control_message(message: str) -> str:
    normalized = message.lower().replace("'", "")
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _parse_small_number(value: str | None) -> int | None:
    text = str(value or "").strip().lower()
    if text.isdigit():
        return int(text)
    return NUMBER_WORDS.get(text) or ORDINAL_WORDS.get(text)


def _extract_option_number(message: str) -> int | None:
    normalized = _normalize_control_message(message)
    word_pattern = "|".join([*NUMBER_WORDS.keys(), *ORDINAL_WORDS.keys()])
    match = re.search(
        rf"\b(?:option|package)\s+(?:the\s+)?(\d+|{word_pattern})\b",
        normalized,
    )
    if not match:
        match = re.search(
            rf"\b(?:option|package)\s+(?:number\s+)?(\d+|{word_pattern})\b",
            normalized,
        )
    if not match:
        match = re.search(
            rf"\b(?:the\s+)?({word_pattern})\s+one\b",
            normalized,
        )
    if not match:
        return None

    value = _parse_small_number(match.group(1))
    return value if value is not None else None
