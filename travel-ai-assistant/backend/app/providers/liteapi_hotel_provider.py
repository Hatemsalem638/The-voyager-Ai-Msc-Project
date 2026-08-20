"""LiteAPI hotel-rates provider with safe normalisation and controlled errors."""

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import requests

from app.schemas import HotelSearchRequest, NormalisedHotelResult


BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_BASE_URL = "https://api.liteapi.travel/v3.0"
REQUEST_TIMEOUT_SECONDS = 20
LOCATION_SUGGESTION_LIMIT = 10
CITY_COUNTRY_CODES = {
    "london": "GB",
    "paris": "FR",
    "new york": "US",
    "rome": "IT",
    "barcelona": "ES",
    "dubai": "AE",
    "cairo": "EG",
    "tokyo": "JP",
}
LOCATION_PROVIDER_CACHE: list[dict[str, Any]] = [
    {"city": "Paris", "country": "France", "latitude": 48.8566, "longitude": 2.3522},
    {"city": "Lyon", "country": "France", "latitude": 45.7640, "longitude": 4.8357},
    {"city": "London", "country": "United Kingdom", "latitude": 51.5072, "longitude": -0.1276},
    {"city": "Manchester", "country": "United Kingdom", "latitude": 53.4808, "longitude": -2.2426},
    {"city": "Nottingham", "country": "United Kingdom", "latitude": 52.9548, "longitude": -1.1581},
    {"city": "Cairo", "country": "Egypt", "latitude": 30.0444, "longitude": 31.2357},
    {"city": "Rome", "country": "Italy", "latitude": 41.9028, "longitude": 12.4964},
    {"city": "Geneva", "country": "Switzerland", "latitude": 46.2044, "longitude": 6.1432},
    {"city": "Hurghada", "country": "Egypt", "latitude": 27.2579, "longitude": 33.8116},
    {"city": "Sharm El Sheikh", "country": "Egypt", "latitude": 27.9158, "longitude": 34.3299},
    {"city": "Istanbul", "country": "Turkey", "latitude": 41.0082, "longitude": 28.9784},
    {"city": "Erzurum", "country": "Turkey", "latitude": 39.9043, "longitude": 41.2679},
    {"city": "Kayseri", "country": "Turkey", "latitude": 38.7205, "longitude": 35.4826},
    {"city": "Marrakesh", "country": "Morocco", "latitude": 31.6295, "longitude": -7.9811},
    {"city": "Beirut", "country": "Lebanon", "latitude": 33.8938, "longitude": 35.5018},
]


class LiteApiHotelProviderError(RuntimeError):
    """Expected LiteAPI failure that should activate the mock fallback."""


def _load_settings() -> tuple[str, str, bool]:
    load_dotenv(BACKEND_DIR / ".env")
    api_key = os.getenv("LITEAPI_KEY", "").strip()
    base_url = os.getenv("LITEAPI_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    enabled = os.getenv("USE_LITEAPI_HOTELS", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return api_key, base_url or DEFAULT_BASE_URL, enabled


def liteapi_hotels_enabled() -> bool:
    api_key, _base_url, enabled = _load_settings()
    return bool(api_key) and enabled


def _location_display(city: str, country: str) -> str:
    return f"{city}, {country}" if country else city


def _normalise_location_result(item: dict[str, Any]) -> dict[str, Any] | None:
    city = (
        item.get("city")
        or item.get("cityName")
        or item.get("name")
        or item.get("placeName")
    )
    country = (
        item.get("country")
        or item.get("countryName")
        or item.get("countryCode")
        or item.get("country_code")
    )
    if not city:
        return None
    city_text = str(city)
    country_text = str(country or "")
    provider_location_id = item.get("id") or item.get("placeId") or item.get("locationId")
    latitude = item.get("latitude") or item.get("lat")
    longitude = item.get("longitude") or item.get("lng") or item.get("lon")
    try:
        latitude_value = float(latitude) if latitude is not None else None
    except (TypeError, ValueError):
        latitude_value = None
    try:
        longitude_value = float(longitude) if longitude is not None else None
    except (TypeError, ValueError):
        longitude_value = None
    return {
        "city": city_text,
        "country": country_text,
        "displayName": _location_display(city_text, country_text),
        "providerLocationId": str(provider_location_id) if provider_location_id else None,
        "latitude": latitude_value,
        "longitude": longitude_value,
        "provider": "LiteAPI",
        "source": "provider_api",
    }


def _extract_location_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "places", "locations", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _search_liteapi_location_suggestions(query: str) -> list[dict[str, Any]]:
    api_key, base_url, enabled = _load_settings()
    if not api_key or not enabled:
        raise LiteApiHotelProviderError("LiteAPI hotel location suggestions are disabled.")

    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    params = {"text": query, "query": query, "limit": LOCATION_SUGGESTION_LIMIT}
    try:
        response = requests.get(
            f"{base_url}/data/places",
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise LiteApiHotelProviderError("LiteAPI location suggestion request failed.") from exc
    except ValueError as exc:
        raise LiteApiHotelProviderError("LiteAPI location suggestion returned malformed JSON.") from exc

    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _extract_location_items(payload):
        suggestion = _normalise_location_result(item)
        if suggestion is None:
            continue
        key = suggestion["displayName"].lower()
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(suggestion)
    return suggestions[:LOCATION_SUGGESTION_LIMIT]


def _matches_location_query(location: dict[str, Any], query: str) -> bool:
    lowered = query.lower()
    return any(
        lowered in str(location.get(field, "")).lower()
        for field in ("city", "country")
    )


def _cache_location_suggestions(query: str) -> list[dict[str, Any]]:
    query_text = query.strip().lower()
    matches = [
        location
        for location in LOCATION_PROVIDER_CACHE
        if _matches_location_query(location, query_text)
    ]
    matches.sort(
        key=lambda location: (
            0 if str(location["city"]).lower().startswith(query_text) else 1,
            str(location["city"]),
        )
    )
    return [
        {
            "city": str(location["city"]),
            "country": str(location["country"]),
            "displayName": _location_display(str(location["city"]), str(location["country"])),
            "providerLocationId": None,
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "provider": "LiteAPI",
            "source": "provider_cache",
        }
        for location in matches[:LOCATION_SUGGESTION_LIMIT]
    ]


def search_liteapi_location_suggestions(query: str) -> list[dict[str, Any]]:
    """Return hotel location suggestions from LiteAPI with backend cache fallback."""

    trimmed = query.strip()
    if len(trimmed) < 2:
        return []

    try:
        suggestions = _search_liteapi_location_suggestions(trimmed)
    except LiteApiHotelProviderError:
        suggestions = []

    if not suggestions:
        suggestions = _cache_location_suggestions(trimmed)

    return suggestions[:LOCATION_SUGGESTION_LIMIT]


def _country_code(country: str | None, currency: str) -> str:
    countries = {
        "united kingdom": "GB",
        "uk": "GB",
        "france": "FR",
        "united states": "US",
        "usa": "US",
        "italy": "IT",
        "spain": "ES",
        "united arab emirates": "AE",
        "egypt": "EG",
        "japan": "JP",
    }
    if country and len(country.strip()) == 2:
        return country.strip().upper()
    if country and country.strip().lower() in countries:
        return countries[country.strip().lower()]
    return "GB" if currency.upper() == "GBP" else "US"


def _build_payload(request: HotelSearchRequest) -> dict[str, Any]:
    destination_country = _country_code(
        request.destination.country,
        request.preferences.currency,
    ) if request.destination.country else CITY_COUNTRY_CODES.get(
        (request.destination.city or "").strip().lower()
    )
    payload: dict[str, Any] = {
        "occupancies": [
            {"rooms": request.room_count, "adults": request.guest_count}
        ],
        "currency": request.preferences.currency.upper(),
        "guestNationality": _country_code(
            request.destination.country,
            request.preferences.currency,
        ),
        "checkin": request.check_in_date.isoformat(),
        "checkout": request.check_out_date.isoformat(),
        "timeout": 6,
        "maxRatesPerHotel": 1,
        "limit": 12,
        "includeHotelData": True,
    }
    if request.destination.city:
        payload["cityName"] = request.destination.city.strip()
    if destination_country:
        payload["countryCode"] = destination_country
    if request.destination.latitude is not None:
        payload["latitude"] = request.destination.latitude
    if request.destination.longitude is not None:
        payload["longitude"] = request.destination.longitude
    if request.preferences.min_rating is not None:
        payload["minRating"] = request.preferences.min_rating
    if request.preferences.board_type:
        payload["boardType"] = request.preferences.board_type
    if request.preferences.amenities:
        payload["roomAmenities"] = request.preferences.amenities
    return payload


def _money(value: Any) -> tuple[float | None, str | None]:
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, dict):
        return None, None
    try:
        amount = float(value.get("amount"))
    except (TypeError, ValueError):
        return None, None
    currency = value.get("currency")
    return amount, str(currency) if currency else None


def _rate_price(rate: dict[str, Any], room_type: dict[str, Any]) -> tuple[float | None, str | None]:
    retail_rate = rate.get("retailRate")
    if isinstance(retail_rate, dict):
        for field in ("total", "suggestedSellingPrice", "initialPrice"):
            amount, currency = _money(retail_rate.get(field))
            if amount is not None:
                return amount, currency
    for field in ("offerRetailRate", "offerInitialPrice", "suggestedSellingPrice"):
        amount, currency = _money(room_type.get(field))
        if amount is not None:
            return amount, currency
    return None, None


def _refundable(rate: dict[str, Any]) -> bool | None:
    policies = rate.get("cancellationPolicies")
    if not isinstance(policies, dict):
        return None
    tag = policies.get("refundableTag")
    if not isinstance(tag, str) or not tag.strip():
        return None
    normalized = tag.lower()
    if any(term in normalized for term in ("non", "not", "no", "nr")):
        return False
    return any(term in normalized for term in ("refund", "free", "yes"))


def _location(hotel_data: dict[str, Any] | None, request: HotelSearchRequest) -> str:
    if isinstance(hotel_data, dict):
        return str(
            hotel_data.get("address")
            or hotel_data.get("city_name")
            or request.destination.city
            or "Hotel location"
        )
    return request.destination.city or "Hotel location"


def _normalise_response(
    payload: Any,
    request: HotelSearchRequest,
) -> list[NormalisedHotelResult]:
    if not isinstance(payload, dict):
        raise LiteApiHotelProviderError("LiteAPI response was not a JSON object.")
    rate_entries = payload.get("data")
    if not isinstance(rate_entries, list):
        raise LiteApiHotelProviderError("LiteAPI response did not include rate data.")

    hotels_by_id = {
        str(hotel.get("id")): hotel
        for hotel in payload.get("hotels", [])
        if isinstance(hotel, dict) and hotel.get("id")
    }
    nights = (request.check_out_date - request.check_in_date).days
    normalised: list[NormalisedHotelResult] = []

    for entry in rate_entries:
        if not isinstance(entry, dict):
            continue
        hotel_id = str(entry.get("hotelId") or "")
        if not hotel_id:
            continue
        hotel_data = hotels_by_id.get(hotel_id, {})
        room_types = entry.get("roomTypes")
        if not isinstance(room_types, list):
            continue
        for room_type in room_types:
            if not isinstance(room_type, dict):
                continue
            rates = room_type.get("rates")
            if not isinstance(rates, list):
                continue
            for rate in rates:
                if not isinstance(rate, dict):
                    continue
                price, currency = _rate_price(rate, room_type)
                if price is None:
                    continue
                if (
                    request.preferences.max_price is not None
                    and price > request.preferences.max_price
                ):
                    continue
                rating_value = hotel_data.get("rating")
                try:
                    rating = float(rating_value) if rating_value is not None else None
                except (TypeError, ValueError):
                    rating = None
                if (
                    request.preferences.min_rating is not None
                    and (rating is None or rating < request.preferences.min_rating)
                ):
                    continue

                provider_id = str(
                    room_type.get("offerId")
                    or rate.get("rateId")
                    or f"{hotel_id}-{room_type.get('roomTypeId') or 'room'}"
                )
                perks = rate.get("perks")
                amenities = [
                    str(perk)[:80]
                    for perk in perks
                    if isinstance(perk, (str, int, float))
                ][:8] if isinstance(perks, list) else []
                normalised.append(
                    NormalisedHotelResult(
                        id=provider_id,
                        option_number=1,
                        hotel_name=str(
                            hotel_data.get("name") or f"Hotel {hotel_id[-8:]}"
                        ),
                        location=_location(hotel_data, request),
                        check_in_date=request.check_in_date.isoformat(),
                        check_out_date=request.check_out_date.isoformat(),
                        nights=nights,
                        guest_count=request.guest_count,
                        room_count=request.room_count,
                        rating=rating,
                        board_type=rate.get("boardName") or rate.get("boardType"),
                        room_name=rate.get("name") or room_type.get("roomTypeId"),
                        refundable=_refundable(rate),
                        price=round(price, 2),
                        currency=currency or request.preferences.currency.upper(),
                        provider="liteapi",
                        short_id=f"Hotel {hotel_id[-6:]}",
                        amenities=amenities,
                        latitude=hotel_data.get("latitude"),
                        longitude=hotel_data.get("longitude"),
                    )
                )

    normalised.sort(key=lambda hotel: hotel.price)
    return [
        hotel.model_copy(update={"option_number": index})
        for index, hotel in enumerate(normalised, start=1)
    ]


def search_liteapi_hotels(request: HotelSearchRequest) -> list[NormalisedHotelResult]:
    """Search live LiteAPI rates and return only safe normalised hotel cards."""

    api_key, base_url, enabled = _load_settings()
    if not enabled:
        raise LiteApiHotelProviderError("LiteAPI hotel search is disabled.")
    if not api_key:
        raise LiteApiHotelProviderError("LiteAPI credentials are missing.")

    try:
        response = requests.post(
            f"{base_url}/hotels/rates",
            headers={"X-API-Key": api_key, "Accept": "application/json"},
            json=_build_payload(request),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise LiteApiHotelProviderError("LiteAPI hotel request timed out.") from exc
    except requests.RequestException as exc:
        raise LiteApiHotelProviderError("LiteAPI hotel request failed.") from exc

    if not response.ok:
        raise LiteApiHotelProviderError(
            f"LiteAPI hotel request returned HTTP {response.status_code}."
        )
    try:
        results = _normalise_response(response.json(), request)
    except ValueError as exc:
        raise LiteApiHotelProviderError("LiteAPI returned invalid JSON.") from exc
    if not results:
        raise LiteApiHotelProviderError("LiteAPI returned no usable hotel rates.")
    return results
