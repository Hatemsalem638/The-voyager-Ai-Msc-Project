from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.date_utils import today_utc_date


def validate_future_iso(value: Any, *, today: date | None = None) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    reference = today or today_utc_date()
    if parsed < reference:
        return None
    return parsed.isoformat()


def clear_last_clarification(active_trip: dict[str, Any]) -> None:
    active_trip.pop("lastClarification", None)


def record_clarification(
    active_trip: dict[str, Any],
    awaiting: list[str],
    question_text: str,
) -> bool:
    awaiting_sorted = sorted(str(field) for field in awaiting if field)
    previous = active_trip.get("lastClarification")
    previous_awaiting = (
        sorted(str(field) for field in previous.get("awaiting", []) if field)
        if isinstance(previous, dict)
        else []
    )
    same_awaiting = awaiting_sorted == previous_awaiting
    turn_count = (
        int(previous.get("turnCount") or 0) + 1
        if same_awaiting and isinstance(previous, dict)
        else 1
    )
    active_trip["lastClarification"] = {
        "awaiting": awaiting_sorted,
        "askedText": question_text,
        "turnCount": turn_count,
    }
    return same_awaiting and turn_count > 1


def concrete_clarification_options(
    awaiting: list[str],
    *,
    today: date | None = None,
) -> list[str]:
    reference = today or today_utc_date()
    if any(field in {"departureDate", "departure_date", "checkInDate"} for field in awaiting):
        options = [
            reference + timedelta(days=1),
            reference + timedelta(days=7),
            reference + timedelta(days=14),
        ]
        return [option.strftime("%d %b %Y") for option in options]
    if any(field in {"returnDate", "return_date", "nights"} for field in awaiting):
        return ["3 nights", "4 nights", "7 nights"]
    return []


def disambiguating_clarification_text(
    question_text: str,
    awaiting: list[str],
    *,
    today: date | None = None,
) -> str:
    options = concrete_clarification_options(awaiting, today=today)
    if options:
        return (
            f"{question_text} To keep this moving, you can choose one of these: "
            f"{', '.join(options)}."
        )
    return f"{question_text} Please give the missing detail in one message."
