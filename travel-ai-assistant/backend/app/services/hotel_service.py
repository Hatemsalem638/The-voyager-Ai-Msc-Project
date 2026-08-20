"""Hotel pricing and ranking helpers."""

from __future__ import annotations

from typing import Any


def _hotel_price(hotel: dict[str, Any]) -> float | None:
    try:
        return float(hotel.get("price"))
    except (TypeError, ValueError):
        return None


def _cheapest_hotel_from_memory(
    memory: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]] | None:
    if memory is None:
        return None
    options = [
        (index, hotel, _hotel_price(hotel))
        for index, hotel in enumerate(memory["hotels"], start=1)
    ]
    priced = [option for option in options if option[2] is not None]
    if not priced:
        return None
    option_number, hotel, _price = min(priced, key=lambda item: item[2])
    return option_number, hotel
