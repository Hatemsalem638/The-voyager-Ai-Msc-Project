"""Duffel flight search provider for the standalone flight search endpoint."""

import os
from pathlib import Path
import re
from typing import Any

import requests
from dotenv import load_dotenv

from app.schemas import FlightSearchRequest, NormalisedFlightResult

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BACKEND_DIR / ".env"
DEFAULT_BASE_URL = "https://api.duffel.com"
DUFFEL_VERSION = "v2"
REQUEST_TIMEOUT_SECONDS = 30
ISO_DURATION_PATTERN = re.compile(
    r"^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?$"
)

load_dotenv(dotenv_path=ENV_PATH)


class DuffelProviderError(Exception):
    """Raised when Duffel cannot return usable flight search results."""


def has_duffel_credentials() -> bool:
    return bool(os.getenv("DUFFEL_API_KEY", "").strip())


def _build_slices(request: FlightSearchRequest) -> list[dict[str, str]]:
    origin_code = request.itinerary.origin.code.upper()
    destination_code = request.itinerary.destination.code.upper()
    slices = [
        {
            "origin": origin_code,
            "destination": destination_code,
            "departure_date": request.itinerary.dates.departure.isoformat(),
        }
    ]

    return_date = request.itinerary.dates.return_date
    if request.itinerary.trip_type == "round_trip" and return_date:
        slices.append(
            {
                "origin": destination_code,
                "destination": origin_code,
                "departure_date": return_date.isoformat(),
            }
        )

    return slices


def _build_payload(request: FlightSearchRequest) -> dict[str, Any]:
    data: dict[str, Any] = {
        "slices": _build_slices(request),
        "passengers": [
            {"type": "adult"} for _ in range(request.passenger_count)
        ],
        "cabin_class": request.preferences.cabin_class,
    }

    if request.preferences.max_stops is not None:
        data["max_connections"] = request.preferences.max_stops

    return {"data": data}


def _safe_duffel_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"Duffel HTTP {response.status_code}"

    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not isinstance(errors, list) or not errors:
        return f"Duffel HTTP {response.status_code}"

    first_error = errors[0]
    if not isinstance(first_error, dict):
        return f"Duffel HTTP {response.status_code}"

    code = first_error.get("code", "unknown_error")
    title = first_error.get("title", "Duffel request failed")
    return f"Duffel HTTP {response.status_code}: {code} - {title}"


def _location_code(value: Any) -> str | None:
    if isinstance(value, str):
        return value

    if not isinstance(value, dict):
        return None

    return (
        value.get("iata_code")
        or value.get("iata_city_code")
        or value.get("code")
    )


def _first_departure(slice_data: dict[str, Any] | None) -> str | None:
    if not isinstance(slice_data, dict):
        return None

    segments = slice_data.get("segments")
    if isinstance(segments, list) and segments:
        first_segment = segments[0]
        if isinstance(first_segment, dict):
            return (
                first_segment.get("departing_at")
                or first_segment.get("departure_datetime")
                or first_segment.get("departure_time")
            )

    return slice_data.get("departure_date")


def _last_arrival(slice_data: dict[str, Any] | None) -> str | None:
    if not isinstance(slice_data, dict):
        return None

    segments = slice_data.get("segments")
    if isinstance(segments, list) and segments:
        last_segment = segments[-1]
        if isinstance(last_segment, dict):
            return (
                last_segment.get("arriving_at")
                or last_segment.get("arrival_datetime")
                or last_segment.get("arrival_time")
            )

    return slice_data.get("arrival_date")


def _date_time_parts(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None

    date_part, separator, time_part = value.partition("T")
    if not separator:
        return date_part, None

    return date_part, time_part[:5] or None


def _stop_count(slice_data: dict[str, Any] | None) -> int | None:
    if not isinstance(slice_data, dict):
        return None

    segments = slice_data.get("segments")
    if not isinstance(segments, list) or not segments:
        return None

    return max(len(segments) - 1, 0)


def _duration_minutes(slice_data: dict[str, Any] | None) -> int | None:
    if not isinstance(slice_data, dict):
        return None

    duration = slice_data.get("duration")
    if not isinstance(duration, str):
        return None

    match = ISO_DURATION_PATTERN.match(duration)
    if not match:
        return None

    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    return hours * 60 + minutes


def _duration_label(duration_minutes: int | None) -> str | None:
    if duration_minutes is None:
        return None

    hours, minutes = divmod(duration_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def _stops_label(stops: int | None) -> str | None:
    if stops is None:
        return None
    if stops == 0:
        return "Non-stop"
    if stops == 1:
        return "1 stop"
    return f"{stops} stops"


def _cabin_class_label(cabin_class: str) -> str:
    return {
        "economy": "Economy",
        "premium_economy": "Premium Economy",
        "business": "Business",
        "first": "First",
    }.get(cabin_class, cabin_class.replace("_", " ").title())


def _airline_name(offer: dict[str, Any]) -> str | None:
    owner = offer.get("owner")
    if isinstance(owner, dict) and owner.get("name"):
        return str(owner["name"])

    slices = offer.get("slices")
    if not isinstance(slices, list):
        return None

    for slice_data in slices:
        if not isinstance(slice_data, dict):
            continue
        segments = slice_data.get("segments")
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            carrier = segment.get("operating_carrier") or segment.get("marketing_carrier")
            if isinstance(carrier, dict) and carrier.get("name"):
                return str(carrier["name"])

    return None


def _normalise_offer(
    offer: dict[str, Any],
    request: FlightSearchRequest,
) -> NormalisedFlightResult | None:
    offer_id = offer.get("id")
    if not offer_id:
        return None

    slices = offer.get("slices")
    outbound_slice = slices[0] if isinstance(slices, list) and slices else None
    return_slice = slices[1] if isinstance(slices, list) and len(slices) > 1 else None

    origin = None
    destination = None
    if isinstance(outbound_slice, dict):
        origin = _location_code(outbound_slice.get("origin"))
        destination = _location_code(outbound_slice.get("destination"))

    outbound_stops = _stop_count(outbound_slice)
    return_stops = _stop_count(return_slice)
    known_stops = [
        stops for stops in [outbound_stops, return_stops] if stops is not None
    ]
    stops = max(known_stops) if known_stops else None
    outbound_departure = _first_departure(outbound_slice)
    outbound_arrival = _last_arrival(outbound_slice)
    return_departure = _first_departure(return_slice)
    return_arrival = _last_arrival(return_slice)
    outbound_departure_date, outbound_departure_time = _date_time_parts(
        outbound_departure
    )
    outbound_arrival_date, outbound_arrival_time = _date_time_parts(outbound_arrival)
    return_departure_date, return_departure_time = _date_time_parts(return_departure)
    return_arrival_date, return_arrival_time = _date_time_parts(return_arrival)
    duration_minutes = _duration_minutes(outbound_slice)
    normalised_offer_id = str(offer_id)

    return NormalisedFlightResult(
        id=normalised_offer_id,
        offer_id=normalised_offer_id,
        display_id=f"Offer {normalised_offer_id[-8:]}",
        origin=(origin or request.itinerary.origin.code).upper(),
        destination=(destination or request.itinerary.destination.code).upper(),
        airline=_airline_name(offer),
        outbound_departure_date=outbound_departure_date,
        outbound_departure_time=outbound_departure_time,
        outbound_arrival_date=outbound_arrival_date,
        outbound_arrival_time=outbound_arrival_time,
        return_departure_date=return_departure_date,
        return_departure_time=return_departure_time,
        return_arrival_date=return_arrival_date,
        return_arrival_time=return_arrival_time,
        departure_date=outbound_departure,
        return_date=return_departure,
        stops=stops,
        stops_label=_stops_label(stops),
        duration_minutes=duration_minutes,
        duration_label=_duration_label(duration_minutes),
        cabin_class=request.preferences.cabin_class,
        cabin_class_label=_cabin_class_label(request.preferences.cabin_class),
        passenger_count=request.passenger_count,
        price=str(offer.get("total_amount")) if offer.get("total_amount") else None,
        currency=(
            str(offer.get("total_currency"))
            if offer.get("total_currency")
            else None
        ),
        provider="duffel",
    )


def _filter_by_max_stops(
    results: list[NormalisedFlightResult],
    max_stops: int | None,
) -> list[NormalisedFlightResult]:
    if max_stops is None:
        return results

    known_results = [result for result in results if result.stops is not None]
    if not known_results:
        return results

    return [result for result in known_results if result.stops <= max_stops]


def search_duffel_flights(
    flight_search_request: FlightSearchRequest,
) -> list[NormalisedFlightResult]:
    api_key = os.getenv("DUFFEL_API_KEY", "").strip()
    if not api_key:
        raise DuffelProviderError("Missing DUFFEL_API_KEY; Duffel provider is unavailable.")

    if not api_key.startswith("duffel_test_"):
        raise DuffelProviderError("Duffel credentials are not valid for this environment.")

    base_url = os.getenv("DUFFEL_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    url = f"{base_url.rstrip('/')}/air/offer_requests"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Duffel-Version": DUFFEL_VERSION,
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=_build_payload(flight_search_request),
            params={"return_offers": "true"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise DuffelProviderError("Duffel request timed out.") from exc
    except requests.RequestException as exc:
        raise DuffelProviderError("Duffel request failed.") from exc

    if not response.ok:
        raise DuffelProviderError(_safe_duffel_error(response))

    try:
        payload = response.json()
    except ValueError as exc:
        raise DuffelProviderError("Duffel returned malformed JSON.") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise DuffelProviderError("Duffel returned a malformed response.")

    offers = payload["data"].get("offers")
    if not isinstance(offers, list):
        raise DuffelProviderError("Duffel response did not include offers.")

    results = [
        result
        for offer in offers
        if isinstance(offer, dict)
        for result in [_normalise_offer(offer, flight_search_request)]
        if result is not None
    ]
    results = _filter_by_max_stops(
        results,
        flight_search_request.preferences.max_stops,
    )

    if not results:
        raise DuffelProviderError("Duffel returned zero matching offers.")

    return [
        result.model_copy(update={"option_number": index})
        for index, result in enumerate(results, start=1)
    ]
