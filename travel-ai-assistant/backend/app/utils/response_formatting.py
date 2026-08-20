"""Small response-formatting helpers shared by chat service flows."""

from __future__ import annotations

from typing import Any


def _split_flight_datetime(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None

    date_part, separator, time_part = value.partition("T")
    if not separator:
        return date_part, None
    return date_part, time_part[:5] or None


def _cabin_class_label(cabin_class: str | None) -> str:
    return {
        "economy": "Economy",
        "premium_economy": "Premium Economy",
        "business": "Business",
        "first": "First",
    }.get(cabin_class or "", (cabin_class or "Unknown").replace("_", " ").title())


def _stops_label(stops: int | None) -> str:
    if stops is None:
        return "Stops unavailable"
    if stops == 0:
        return "Non-stop"
    if stops == 1:
        return "1 stop"
    return f"{stops} stops"


def _duration_label(duration_minutes: Any) -> str | None:
    if not isinstance(duration_minutes, int) or duration_minutes < 0:
        return None

    hours, minutes = divmod(duration_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
