"""Deterministic normalised hotel results for offline/demo fallback."""

from typing import Any

from app.schemas import HotelSearchRequest, NormalisedHotelResult


CITY_HOTELS: dict[str, list[dict[str, Any]]] = {
    "london": [
        {"name": "Thames View Hotel", "rating": 8.4, "nightly": 165, "board": "BB"},
        {"name": "Bloomsbury City Stay", "rating": 8.0, "nightly": 132, "board": "RO"},
        {"name": "Kensington Garden House", "rating": 8.8, "nightly": 214, "board": "BB"},
    ],
    "paris": [
        {"name": "Left Bank Residence", "rating": 8.6, "nightly": 154, "board": "BB"},
        {"name": "Montmartre City Hotel", "rating": 7.9, "nightly": 118, "board": "RO"},
        {"name": "Seine Grand Hotel", "rating": 9.0, "nightly": 226, "board": "HB"},
    ],
    "new york": [
        {"name": "Manhattan Midtown Hotel", "rating": 8.3, "nightly": 238, "board": "RO"},
        {"name": "Hudson Square Stay", "rating": 7.8, "nightly": 194, "board": "BB"},
        {"name": "Park Avenue Suites", "rating": 8.9, "nightly": 315, "board": "BB"},
    ],
    "rome": [
        {"name": "Trastevere Courtyard Hotel", "rating": 8.5, "nightly": 129, "board": "BB"},
        {"name": "Roma Centro Stay", "rating": 7.7, "nightly": 96, "board": "RO"},
        {"name": "Pantheon Grand Residence", "rating": 9.1, "nightly": 188, "board": "HB"},
    ],
    "barcelona": [
        {"name": "Gothic Quarter Hotel", "rating": 8.4, "nightly": 122, "board": "BB"},
        {"name": "Eixample Urban Stay", "rating": 7.9, "nightly": 101, "board": "RO"},
        {"name": "Mediterranean Terrace Hotel", "rating": 8.9, "nightly": 176, "board": "HB"},
    ],
    "dubai": [
        {"name": "Marina View Hotel", "rating": 8.7, "nightly": 178, "board": "BB"},
        {"name": "Downtown Dubai Stay", "rating": 8.1, "nightly": 142, "board": "RO"},
        {"name": "Palm Grand Resort", "rating": 9.0, "nightly": 265, "board": "HB"},
    ],
    "cairo": [
        {"name": "Nile View Hotel", "rating": 8.2, "nightly": 86, "board": "BB"},
        {"name": "Downtown Cairo Stay", "rating": 7.6, "nightly": 61, "board": "RO"},
        {"name": "Giza Grand Residence", "rating": 8.8, "nightly": 132, "board": "HB"},
    ],
    "tokyo": [
        {"name": "Shinjuku City Hotel", "rating": 8.5, "nightly": 154, "board": "RO"},
        {"name": "Asakusa Garden Stay", "rating": 8.0, "nightly": 118, "board": "BB"},
        {"name": "Tokyo Bay Grand Hotel", "rating": 9.0, "nightly": 228, "board": "HB"},
    ],
}

CURRENCY_MULTIPLIERS = {"GBP": 1.0, "USD": 1.27, "EUR": 1.17, "AED": 4.67}


def search_mock_hotels(request: HotelSearchRequest) -> list[NormalisedHotelResult]:
    """Return deterministic hotel cards with pricing based on nights and rooms."""

    city = (request.destination.city or "Travel").strip() or "Travel"
    templates = CITY_HOTELS.get(city.lower())
    if templates is None:
        templates = [
            {"name": f"{city} Central Hotel", "rating": 8.1, "nightly": 120, "board": "BB"},
            {"name": f"{city} Urban Stay", "rating": 7.7, "nightly": 96, "board": "RO"},
            {"name": f"{city} Grand Residence", "rating": 8.8, "nightly": 175, "board": "HB"},
        ]

    currency = request.preferences.currency.upper()
    multiplier = CURRENCY_MULTIPLIERS.get(currency, 1.0)
    nights = (request.check_out_date - request.check_in_date).days
    results: list[NormalisedHotelResult] = []

    for template_index, template in enumerate(templates, start=1):
        rating = float(template["rating"])
        board_type = str(template["board"])
        total_price = round(
            float(template["nightly"]) * nights * request.room_count * multiplier,
            2,
        )
        if (
            request.preferences.min_rating is not None
            and rating < request.preferences.min_rating
        ):
            continue
        if (
            request.preferences.max_price is not None
            and total_price > request.preferences.max_price
        ):
            continue
        if (
            request.preferences.board_type
            and board_type.lower() != request.preferences.board_type.lower()
        ):
            continue

        result_id = f"MOCK-HOTEL-{city.upper().replace(' ', '-')[:16]}-{template_index:02d}"
        results.append(
            NormalisedHotelResult(
                id=result_id,
                option_number=len(results) + 1,
                hotel_name=str(template["name"]),
                location=city,
                check_in_date=request.check_in_date.isoformat(),
                check_out_date=request.check_out_date.isoformat(),
                nights=nights,
                guest_count=request.guest_count,
                room_count=request.room_count,
                rating=rating,
                board_type=board_type,
                room_name="Standard Double Room",
                refundable=template_index != 2,
                price=total_price,
                currency=currency,
                provider="mock_hotel_fallback",
                short_id=f"Hotel {template_index}",
                description="Deterministic hotel result.",
                amenities=["Wi-Fi", "Reception"],
            )
        )

    return sorted(results, key=lambda hotel: hotel.price)
