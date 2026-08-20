"""Small helpers for active-trip state."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.memory_store import load_active_trip, save_active_trip


def _normalise_trip_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalised: list[str] = []
    for value in values:
        item = str(value or "").strip().lower().replace(" ", "_")
        if item and item not in normalised:
            normalised.append(item)
    return normalised


def _title_trip_list(values: list[str]) -> list[str]:
    titled: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        display = item.replace("_", " ")
        if display.lower() == "europe":
            display = "Europe"
        if item not in titled and display not in titled:
            titled.append(display if display == "Europe" else item)
    return titled


def _set_latest_visible_result_type(
    db: Session,
    session_id: int,
    result_type: str,
) -> None:
    active_trip = load_active_trip(db, session_id)
    active_trip["latestVisibleResultType"] = result_type
    save_active_trip(db, session_id, active_trip)
