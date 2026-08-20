"""Mock travel provider.

Future work:
- Replace this file with real travel provider integrations.
- Expedia API integration can be introduced behind the same search interface.
- Add provider error handling, caching, rate limits, and result normalization.
"""

from typing import Any

from app.schemas import FlightSearchRequest, NormalisedFlightResult

MOCK_FLIGHTS = [
    {
        "id": "FL-1001",
        "destination": "Paris",
        "airline": "Demo Air",
        "price": 320,
        "currency": "USD",
    },
    {
        "id": "FL-1006",
        "destination": "Paris",
        "airline": "EuroFly",
        "price": 260,
        "currency": "USD",
    },
    {
        "id": "FL-1007",
        "destination": "Paris",
        "airline": "Sky Europe",
        "price": 510,
        "currency": "USD",
    },
    {
        "id": "FL-1002",
        "destination": "London",
        "airline": "Sample Airways",
        "price": 280,
        "currency": "USD",
    },
    {
        "id": "FL-1003",
        "destination": "Tokyo",
        "airline": "Pacific Skies",
        "price": 760,
        "currency": "USD",
    },
    {
        "id": "FL-1004",
        "destination": "Cairo",
        "airline": "Mock Wings",
        "price": 180,
        "currency": "USD",
    },
    {
        "id": "FL-1005",
        "destination": "Rome",
        "airline": "Demo Air",
        "price": 350,
        "currency": "USD",
    },
    {
        "id": "FL-1008",
        "destination": "Rome",
        "airline": "EuroFly",
        "price": 270,
        "currency": "USD",
    },
    {
        "id": "FL-1009",
        "destination": "Rome",
        "airline": "Sky Europe",
        "price": 540,
        "currency": "USD",
    },
]

MOCK_HOTELS = [
    {
        "id": "HT-2001",
        "destination": "Paris",
        "name": "Placeholder Paris Hotel",
        "nightly_price": 140,
        "currency": "USD",
    },
    {
        "id": "HT-2006",
        "destination": "Paris",
        "name": "Paris City Hotel",
        "nightly_price": 125,
        "currency": "USD",
    },
    {
        "id": "HT-2007",
        "destination": "Paris",
        "name": "Grand Paris Palace",
        "nightly_price": 210,
        "currency": "USD",
    },
    {
        "id": "HT-2002",
        "destination": "London",
        "name": "Mock London Stay",
        "nightly_price": 130,
        "currency": "USD",
    },
    {
        "id": "HT-2003",
        "destination": "Tokyo",
        "name": "Tokyo Bay Inn",
        "nightly_price": 170,
        "currency": "USD",
    },
    {
        "id": "HT-2004",
        "destination": "Cairo",
        "name": "Sample Cairo Suites",
        "nightly_price": 90,
        "currency": "USD",
    },
    {
        "id": "HT-2005",
        "destination": "Rome",
        "name": "Mock Rome Residence",
        "nightly_price": 155,
        "currency": "USD",
    },
    {
        "id": "HT-2008",
        "destination": "Rome",
        "name": "Rome City Hotel",
        "nightly_price": 140,
        "currency": "USD",
    },
    {
        "id": "HT-2009",
        "destination": "Rome",
        "name": "Grand Rome Palace",
        "nightly_price": 170,
        "currency": "USD",
    },
]


def _filter_by_destination(
    items: list[dict[str, str | int]], destination: str | None
) -> list[dict[str, str | int]]:
    if not destination:
        return []

    return [item for item in items if item["destination"] == destination]


def _build_package_options(
    flights: list[dict[str, str | int]],
    hotels: list[dict[str, str | int]],
    destination: str | None,
) -> list[dict[str, str | int]]:
    if not destination or not flights or not hotels:
        return []

    packages = []
    for index, (flight, hotel) in enumerate(zip(flights[:3], hotels[:3]), start=1):
        packages.append(
            {
                "id": f"PKG-{destination.upper()}-{index:03d}",
                "option_number": index,
                "destination": destination,
                "flight_id": str(flight["id"]),
                "flight_airline": str(flight["airline"]),
                "hotel_id": str(hotel["id"]),
                "hotel_name": str(hotel["name"]),
                "summary": (
                    f"Option {index}: {flight['airline']} flight with "
                    f"{hotel['name']}"
                ),
                "estimated_price": int(flight["price"])
                + int(hotel["nightly_price"]) * 3,
                "currency": "USD",
            }
        )

    return packages


def search_mock_travel(intent: dict[str, Any]) -> dict[str, Any]:
    """Return mock travel data based on the detected intent."""
    intent_type = intent.get("intent_type", "general_enquiry")
    destination = intent.get("destination")
    matching_flights = _filter_by_destination(MOCK_FLIGHTS, destination)
    matching_hotels = _filter_by_destination(MOCK_HOTELS, destination)

    empty_results = {
        "flights": [],
        "hotels": [],
        "packages": [],
        "booking": None,
        "suggestions": [],
    }

    if intent_type == "booking_retrieval":
        return empty_results

    if intent_type == "flight_search":
        return {
            **empty_results,
            "flights": matching_flights,
        }

    if intent_type == "hotel_search":
        return {
            **empty_results,
            "hotels": matching_hotels,
        }

    if intent_type == "package_search":
        return {
            **empty_results,
            "flights": matching_flights,
            "hotels": matching_hotels,
            "packages": _build_package_options(
                matching_flights,
                matching_hotels,
                destination,
            ),
        }

    return {
        **empty_results,
        "suggestions": [
            "Try asking for flights to London.",
            "Try asking for a hotel in Paris.",
            "Try asking to check booking ABC123.",
        ],
    }


def search_mock_flight_results(
    request: FlightSearchRequest,
) -> list[NormalisedFlightResult]:
    """Return normalised mock flights for the standalone flight search endpoint."""
    origin_code = request.itinerary.origin.code.upper()
    destination_code = request.itinerary.destination.code.upper()
    destination_city = request.itinerary.destination.city
    matching_flights = _filter_by_destination(MOCK_FLIGHTS, destination_city)
    template_flights = matching_flights or MOCK_FLIGHTS[:3]
    return_date = request.itinerary.dates.return_date

    results: list[NormalisedFlightResult] = []
    for index, flight in enumerate(template_flights[:5], start=1):
        stops = 0 if request.preferences.max_stops == 0 else min(index - 1, 1)
        result_id = f"MOCK-{origin_code}-{destination_code}-{index:03d}"
        duration_minutes = 420 + (index - 1) * 45
        duration_hours, duration_remainder = divmod(duration_minutes, 60)
        duration_label = (
            f"{duration_hours}h {duration_remainder}m"
            if duration_remainder
            else f"{duration_hours}h"
        )
        outbound_departure_minutes = (7 + index) * 60 + 30
        outbound_arrival_minutes = outbound_departure_minutes + duration_minutes
        return_departure_minutes = (11 + index) * 60 + 15
        return_arrival_minutes = return_departure_minutes + duration_minutes
        outbound_departure_time = (
            f"{outbound_departure_minutes // 60:02d}:{outbound_departure_minutes % 60:02d}"
        )
        outbound_arrival_time = (
            f"{outbound_arrival_minutes // 60:02d}:{outbound_arrival_minutes % 60:02d}"
        )
        return_departure_time = (
            f"{return_departure_minutes // 60:02d}:{return_departure_minutes % 60:02d}"
        )
        return_arrival_time = (
            f"{return_arrival_minutes // 60:02d}:{return_arrival_minutes % 60:02d}"
        )
        cabin_class_label = {
            "economy": "Economy",
            "premium_economy": "Premium Economy",
            "business": "Business",
            "first": "First",
        }[request.preferences.cabin_class]

        results.append(
            NormalisedFlightResult(
                id=result_id,
                offer_id=result_id,
                display_id=f"Offer {origin_code}-{destination_code}-{index:03d}",
                option_number=index,
                origin=origin_code,
                destination=destination_code,
                airline=str(flight["airline"]),
                outbound_departure_date=request.itinerary.dates.departure.isoformat(),
                outbound_departure_time=outbound_departure_time,
                outbound_arrival_date=request.itinerary.dates.departure.isoformat(),
                outbound_arrival_time=outbound_arrival_time,
                return_departure_date=return_date.isoformat() if return_date else None,
                return_departure_time=return_departure_time if return_date else None,
                return_arrival_date=return_date.isoformat() if return_date else None,
                return_arrival_time=return_arrival_time if return_date else None,
                departure_date=request.itinerary.dates.departure.isoformat(),
                return_date=return_date.isoformat() if return_date else None,
                stops=stops,
                stops_label="Non-stop" if stops == 0 else f"{stops} stop",
                duration_minutes=duration_minutes,
                duration_label=duration_label,
                cabin_class=request.preferences.cabin_class,
                cabin_class_label=cabin_class_label,
                passenger_count=request.passenger_count,
                price=str(flight["price"]),
                currency=str(flight["currency"]),
                provider="mock",
            )
        )

    return results
