"""Flight ranking and pricing helpers."""

from __future__ import annotations

from typing import Any


def _flight_price(flight: dict[str, Any]) -> float | None:
    try:
        return float(flight.get("price"))
    except (TypeError, ValueError):
        return None


def _cheapest_flight_from_memory(
    memory: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]] | None:
    if memory is None:
        return None

    priced_flights = [
        (index, flight, _flight_price(flight))
        for index, flight in enumerate(memory["flights"], start=1)
    ]
    priced_flights = [item for item in priced_flights if item[2] is not None]
    if not priced_flights:
        return None

    option_number, flight, _price = min(priced_flights, key=lambda item: item[2])
    return option_number, flight


def _fastest_flight_from_memory(
    memory: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]] | None:
    if memory is None:
        return None

    candidates = []
    for index, flight in enumerate(memory["flights"], start=1):
        duration = flight.get("duration_minutes")
        if isinstance(duration, int):
            candidates.append((index, flight, duration))

    if candidates:
        option_number, flight, _duration = min(candidates, key=lambda item: item[2])
        return option_number, flight

    return min(
        enumerate(memory["flights"], start=1),
        key=lambda item: (item[1].get("stops") or 0, _flight_price(item[1]) or float("inf")),
    )


def _rank_flights_from_memory(
    memory: dict[str, Any] | None,
    ranking: str,
    limit: int = 1,
) -> list[tuple[int, dict[str, Any]]]:
    if memory is None:
        return []

    indexed_flights = list(enumerate(memory["flights"], start=1))
    if ranking == "cheapest":
        indexed_flights.sort(key=lambda item: _flight_price(item[1]) or float("inf"))
    elif ranking == "fastest":
        indexed_flights.sort(
            key=lambda item: (
                item[1].get("duration_minutes") or float("inf"),
                item[1].get("stops") or 0,
            )
        )
    elif ranking == "earliest":
        indexed_flights.sort(key=lambda item: item[1].get("departure_date") or "")
    elif ranking == "latest":
        indexed_flights.sort(
            key=lambda item: item[1].get("departure_date") or "",
            reverse=True,
        )
    elif ranking == "cheapest_fastest":
        priced_flights = [
            item for item in indexed_flights if _flight_price(item[1]) is not None
        ]
        if priced_flights:
            priced_flights.sort(key=lambda item: _flight_price(item[1]) or float("inf"))
            cheapest_group_size = max(1, (len(priced_flights) + 9) // 10)
            cheapest_group = priced_flights[:cheapest_group_size]
            cheapest_group.sort(
                key=lambda item: (
                    item[1].get("duration_minutes") or float("inf"),
                    _flight_price(item[1]) or float("inf"),
                    item[1].get("stops") or 0,
                )
            )
            remaining = priced_flights[cheapest_group_size:]
            unpriced = [
                item for item in indexed_flights if _flight_price(item[1]) is None
            ]
            indexed_flights = cheapest_group + remaining + unpriced
        else:
            indexed_flights.sort(
                key=lambda item: (
                    item[1].get("duration_minutes") or float("inf"),
                    item[1].get("stops") or 0,
                )
            )
    else:
        priced = [_flight_price(flight) for _index, flight in indexed_flights]
        known_prices = [price for price in priced if price is not None]
        durations = [
            flight.get("duration_minutes")
            for _index, flight in indexed_flights
            if isinstance(flight.get("duration_minutes"), int)
        ]
        minimum_price = min(known_prices) if known_prices else None
        price_range = (
            max(known_prices) - minimum_price
            if minimum_price is not None and known_prices
            else 0
        )
        minimum_duration = min(durations) if durations else None
        duration_range = (
            max(durations) - minimum_duration
            if minimum_duration is not None and durations
            else 0
        )

        def best_value_key(item: tuple[int, dict[str, Any]]) -> tuple[float, int]:
            _index, flight = item
            price = _flight_price(flight)
            duration = flight.get("duration_minutes")
            price_score = (
                1.0
                if price is None or minimum_price is None
                else (price - minimum_price) / price_range
                if price_range
                else 0.0
            )
            duration_score = (
                0.5
                if not isinstance(duration, int) or minimum_duration is None
                else (duration - minimum_duration) / duration_range
                if duration_range
                else 0.0
            )
            stops = flight.get("stops")
            stop_penalty = 0.0 if stops == 0 else 0.2 * (stops or 1)
            return price_score + duration_score + stop_penalty, _index

        indexed_flights.sort(key=best_value_key)

    return indexed_flights[: max(1, min(limit, 10))]
