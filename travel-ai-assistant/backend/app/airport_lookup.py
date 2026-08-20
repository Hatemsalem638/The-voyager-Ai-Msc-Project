"""Provider-backed nearest-airport lookup for browser location."""

import logging
import os
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


LOGGER = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_DIR / ".env"
DEFAULT_DUFFEL_BASE_URL = "https://api.duffel.com"
DUFFEL_VERSION = "v2"
REQUEST_TIMEOUT_SECONDS = 12
AIRPORT_SUGGESTION_LIMIT = 10

AIRPORT_PROVIDER_CACHE: list[dict[str, str]] = [
    {"code": "EMA", "name": "East Midlands Airport", "city": "East Midlands", "country": "United Kingdom", "latitude": 52.8311, "longitude": -1.3281},
    {"code": "LHR", "name": "London Heathrow Airport", "city": "London", "country": "United Kingdom"},
    {"code": "LGW", "name": "London Gatwick Airport", "city": "London", "country": "United Kingdom"},
    {"code": "STN", "name": "London Stansted Airport", "city": "London", "country": "United Kingdom"},
    {"code": "LTN", "name": "London Luton Airport", "city": "London", "country": "United Kingdom"},
    {"code": "LCY", "name": "London City Airport", "city": "London", "country": "United Kingdom"},
    {"code": "MAN", "name": "Manchester Airport", "city": "Manchester", "country": "United Kingdom"},
    {"code": "BHX", "name": "Birmingham Airport", "city": "Birmingham", "country": "United Kingdom", "latitude": 52.4539, "longitude": -1.7480},
    {"code": "MCO", "name": "Orlando International Airport", "city": "Orlando", "country": "United States"},
    {"code": "MIA", "name": "Miami International Airport", "city": "Miami", "country": "United States"},
    {"code": "TPA", "name": "Tampa International Airport", "city": "Tampa", "country": "United States"},
    {"code": "FLL", "name": "Fort Lauderdale-Hollywood International Airport", "city": "Fort Lauderdale", "country": "United States"},
    {"code": "JFK", "name": "John F. Kennedy International Airport", "city": "New York", "country": "United States"},
    {"code": "CDG", "name": "Paris Charles de Gaulle Airport", "city": "Paris", "country": "France"},
    {"code": "ORY", "name": "Paris Orly Airport", "city": "Paris", "country": "France"},
    {"code": "CAI", "name": "Cairo International Airport", "city": "Cairo", "country": "Egypt"},
    {"code": "FCO", "name": "Rome Fiumicino Airport", "city": "Rome", "country": "Italy"},
    {"code": "LYS", "name": "Lyon-Saint Exupery Airport", "city": "Lyon", "country": "France"},
    {"code": "GVA", "name": "Geneva Airport", "city": "Geneva", "country": "Switzerland"},
    {"code": "ERZ", "name": "Erzurum Airport", "city": "Erzurum", "country": "Turkey"},
    {"code": "ASR", "name": "Kayseri Erkilet Airport", "city": "Kayseri", "country": "Turkey"},
    {"code": "HRG", "name": "Hurghada International Airport", "city": "Hurghada", "country": "Egypt"},
    {"code": "SSH", "name": "Sharm El Sheikh International Airport", "city": "Sharm El Sheikh", "country": "Egypt"},
]


class AirportLookupError(RuntimeError):
    """Raised when no live provider can resolve a nearest airport."""


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance between two coordinates in kilometres."""

    earth_radius_km = 6371.0
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = (
        sin(d_lat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    )
    return 2 * earth_radius_km * asin(sqrt(a))


def _duffel_settings() -> tuple[str, str]:
    load_dotenv(dotenv_path=ENV_PATH)
    api_key = os.getenv("DUFFEL_API_KEY", "").strip()
    base_url = os.getenv("DUFFEL_BASE_URL", DEFAULT_DUFFEL_BASE_URL).strip()
    if not api_key:
        raise AirportLookupError("Duffel API key is not configured.")
    return base_url.rstrip("/") or DEFAULT_DUFFEL_BASE_URL, api_key


def _coordinate(value: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        raw = value.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _place_coordinates(place: dict[str, Any]) -> tuple[float, float] | None:
    location = place.get("location")
    if isinstance(location, dict):
        lat = _coordinate(location, "lat", "latitude")
        lon = _coordinate(location, "lng", "lon", "longitude")
        if lat is not None and lon is not None:
            return lat, lon

    lat = _coordinate(place, "lat", "latitude")
    lon = _coordinate(place, "lng", "lon", "longitude")
    if lat is not None and lon is not None:
        return lat, lon

    return None


def _airport_from_place(place: dict[str, Any]) -> dict[str, Any] | None:
    code = str(
        place.get("iata_code")
        or place.get("iata_city_code")
        or place.get("code")
        or ""
    ).upper()
    if len(code) != 3:
        return None

    coordinates = _place_coordinates(place)
    if coordinates is None:
        return None

    nested_city = place.get("city")
    city = place.get("city_name")
    if city is None and isinstance(nested_city, dict):
        city = nested_city.get("name")
    return {
        "city": str(city or place.get("name") or code),
        "country": place.get("country_name")
        or place.get("iata_country_code")
        or place.get("country_code"),
        "airportCode": code,
        "airportName": str(place.get("name") or f"{code} airport"),
        "latitude": coordinates[0],
        "longitude": coordinates[1],
    }


def _suggestion_from_place(place: dict[str, Any]) -> dict[str, Any] | None:
    code = str(
        place.get("iata_code")
        or place.get("iata_city_code")
        or place.get("code")
        or ""
    ).upper()
    if len(code) != 3:
        return None

    nested_city = place.get("city")
    city = place.get("city_name")
    if city is None and isinstance(nested_city, dict):
        city = nested_city.get("name")
    city_text = str(city or place.get("name") or code)
    country = str(
        place.get("country_name")
        or place.get("country_code")
        or place.get("iata_country_code")
        or ""
    )
    name = str(place.get("name") or f"{city_text} Airport")
    return {
        "code": code,
        "name": name,
        "city": city_text,
        "country": country,
        "displayName": f"{name} ({code}), {city_text}, {country}".strip(", "),
        "provider": "Duffel",
        "source": "provider_api",
    }


def _airport_suggestions_from_places(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []

    suggestions: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for place in payload["data"]:
        if not isinstance(place, dict):
            continue
        nested_airports = place.get("airports")
        candidates = [place]
        if isinstance(nested_airports, list):
            candidates.extend(nested_airports)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            suggestion = _suggestion_from_place(candidate)
            if suggestion and suggestion["code"] not in seen_codes:
                seen_codes.add(suggestion["code"])
                suggestions.append(suggestion)
    return suggestions


def _search_duffel_airport_suggestions(query: str) -> list[dict[str, Any]]:
    base_url, api_key = _duffel_settings()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Duffel-Version": DUFFEL_VERSION,
        "Accept": "application/json",
    }
    params = {"query": query, "limit": AIRPORT_SUGGESTION_LIMIT}

    try:
        response = requests.get(
            f"{base_url}/places/suggestions",
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        LOGGER.warning("Duffel airport suggestion lookup failed: %s", exc.__class__.__name__)
        raise AirportLookupError("Duffel airport suggestion lookup failed.") from exc
    except ValueError as exc:
        LOGGER.warning("Duffel airport suggestion lookup returned malformed JSON.")
        raise AirportLookupError("Duffel airport suggestion lookup returned malformed JSON.") from exc

    return _airport_suggestions_from_places(payload)


def _matches_airport_query(airport: dict[str, str], query: str) -> bool:
    lowered = query.lower()
    return any(
        lowered in str(airport.get(field, "")).lower()
        for field in ("code", "name", "city", "country")
    )


def _cache_airport_suggestions(query: str) -> list[dict[str, Any]]:
    query_text = query.strip().lower()
    matches = [
        airport
        for airport in AIRPORT_PROVIDER_CACHE
        if _matches_airport_query(airport, query_text)
    ]
    matches.sort(
        key=lambda airport: (
            0 if airport["code"].lower() == query_text else 1,
            0 if airport["city"].lower().startswith(query_text) else 1,
            airport["city"],
            airport["name"],
        )
    )
    return [
        {
            **airport,
            "displayName": f"{airport['name']} ({airport['code']}), {airport['city']}, {airport['country']}",
            "provider": "Duffel",
            "source": "provider_cache",
        }
        for airport in matches[:AIRPORT_SUGGESTION_LIMIT]
    ]


def suggest_airports(query: str) -> list[dict[str, Any]]:
    """Return airport suggestions from Duffel Places with a backend cache fallback."""

    trimmed = query.strip()
    if len(trimmed) < 2:
        return []

    try:
        suggestions = _search_duffel_airport_suggestions(trimmed)
    except AirportLookupError:
        suggestions = []

    if not suggestions:
        suggestions = _cache_airport_suggestions(trimmed)

    suggestions.sort(
        key=lambda airport: (
            0 if airport["code"].lower() == trimmed.lower() else 1,
            0 if airport["city"].lower().startswith(trimmed.lower()) else 1,
            airport["city"],
            airport["name"],
        )
    )
    return suggestions[:AIRPORT_SUGGESTION_LIMIT]


def _airport_candidates_from_places(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []

    candidates: list[dict[str, Any]] = []
    for place in payload["data"]:
        if not isinstance(place, dict):
            continue
        airport = _airport_from_place(place)
        if airport is not None:
            candidates.append(airport)

        nested_airports = place.get("airports")
        if isinstance(nested_airports, list):
            for nested in nested_airports:
                if isinstance(nested, dict):
                    airport = _airport_from_place(nested)
                    if airport is not None:
                        candidates.append(airport)

    return candidates


def _search_duffel_airports_near(
    latitude: float,
    longitude: float,
    radius_metres: int,
) -> list[dict[str, Any]]:
    base_url, api_key = _duffel_settings()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Duffel-Version": DUFFEL_VERSION,
        "Accept": "application/json",
    }
    params = {
        "lat": latitude,
        "lng": longitude,
        "rad": radius_metres,
    }

    try:
        response = requests.get(
            f"{base_url}/places/suggestions",
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        LOGGER.warning("Duffel place lookup failed: %s", exc.__class__.__name__)
        raise AirportLookupError("Duffel place lookup failed.") from exc
    except ValueError as exc:
        LOGGER.warning("Duffel place lookup returned malformed JSON.")
        raise AirportLookupError("Duffel place lookup returned malformed JSON.") from exc

    return _airport_candidates_from_places(payload)


def find_nearest_airport(latitude: float, longitude: float) -> dict[str, Any]:
    """Find the nearest airport from Duffel Places, without storing exact GPS."""

    candidates: list[dict[str, Any]] = []
    for radius in (50000, 150000, 500000, 1500000):
        candidates = _search_duffel_airports_near(latitude, longitude, radius)
        if candidates:
            break

    if not candidates:
        raise AirportLookupError("No airport candidates were returned.")

    airport = min(
        candidates,
        key=lambda candidate: haversine_km(
            latitude,
            longitude,
            float(candidate["latitude"]),
            float(candidate["longitude"]),
        ),
    )
    return {
        "city": airport["city"],
        "country": airport["country"],
        "airportCode": airport["airportCode"],
        "airportName": airport["airportName"],
        "latitude": airport["latitude"],
        "longitude": airport["longitude"],
        "source": "browser_location_duffel_places",
    }
