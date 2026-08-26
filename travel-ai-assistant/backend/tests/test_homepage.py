from datetime import date
import unittest
from unittest.mock import patch

from app.main import (
    _homepage_package_deal,
    homepage_property_types,
    homepage_random_flights,
    homepage_random_packages,
    homepage_trip_ideas,
)
from app.schemas import (
    FlightSearchResponse,
    HotelSearchResponse,
    HomepageFlightDeal,
    NormalisedFlightResult,
    NormalisedHotelResult,
)


EMA = {
    "code": "EMA",
    "name": "East Midlands Airport",
    "city": "East Midlands",
    "country": "United Kingdom",
    "displayName": "East Midlands Airport (EMA), East Midlands, United Kingdom",
    "provider": "Duffel",
    "source": "provider_cache",
}

LYS = {
    "code": "LYS",
    "name": "Lyon-Saint Exupery Airport",
    "city": "Lyon",
    "country": "France",
    "displayName": "Lyon-Saint Exupery Airport (LYS), Lyon, France",
    "provider": "Duffel",
    "source": "provider_cache",
}

LYON_LOCATION = {
    "city": "Lyon",
    "country": "France",
    "displayName": "Lyon, France",
    "providerLocationId": "loc_lyon",
    "latitude": 45.76,
    "longitude": 4.84,
    "provider": "LiteAPI",
    "source": "provider_cache",
}


def _airport_for_query(query: str) -> dict:
    return EMA if query.upper() == "EMA" else LYS


def _location_for_query(_query: str) -> dict:
    return LYON_LOCATION


def _flight_response(price: str = "91.25") -> FlightSearchResponse:
    return FlightSearchResponse(
        provider_used="duffel",
        flights=[
            NormalisedFlightResult(
                id="off_homepage",
                offer_id="off_homepage",
                display_id="Offer home",
                option_number=1,
                origin="EMA",
                destination="LYS",
                airline="Duffel Airways",
                outbound_departure_date="2026-09-01",
                outbound_departure_time="08:30",
                return_departure_date="2026-09-04",
                return_departure_time="12:00",
                stops=0,
                stops_label="Non-stop",
                duration_minutes=108,
                duration_label="1h 48m",
                cabin_class="economy",
                cabin_class_label="Economy",
                passenger_count=1,
                price=price,
                currency="GBP",
                provider="duffel",
            )
        ],
    )


def _hotel_response(price: float = 300.0) -> HotelSearchResponse:
    return HotelSearchResponse(
        provider_used="liteapi",
        results=[
            NormalisedHotelResult(
                id="hotel_homepage",
                option_number=1,
                hotel_name="Voyage Lyon Stay",
                location="Lyon, France",
                check_in_date="2026-09-01",
                check_out_date="2026-09-04",
                nights=3,
                guest_count=1,
                room_count=1,
                rating=4.2,
                board_type="Room only",
                room_name="Standard room",
                refundable=True,
                price=price,
                currency="GBP",
                provider="liteapi",
                short_id="hotel1",
                amenities=[],
            )
        ],
    )


def _unavailable_flight_response() -> FlightSearchResponse:
    return FlightSearchResponse(
        provider_used="unavailable",
        flights=[],
        fallback_reason="No provider-backed flight results.",
    )


def _unavailable_hotel_response() -> HotelSearchResponse:
    return HotelSearchResponse(
        provider_used="unavailable",
        results=[],
        message="No provider-backed hotel results.",
    )


class HomepageEndpointTests(unittest.TestCase):
    def test_random_flights_returns_homepage_shape_and_search_url(self) -> None:
        with (
            patch("app.main._first_airport_for_homepage", side_effect=_airport_for_query),
            patch("app.main._search_flights_with_fallback", return_value=_flight_response()),
        ):
            deals = homepage_random_flights(origin="EMA", limit=1, seed="fixed")

        self.assertEqual(len(deals), 1)
        payload = deals[0].model_dump(by_alias=True)
        self.assertEqual(payload["originAirportCode"], "EMA")
        self.assertEqual(payload["destinationAirportCode"], "LYS")
        self.assertIn("/flights?", payload["searchUrl"])
        self.assertIn("from=EMA", payload["searchUrl"])
        self.assertIn("to=LYS", payload["searchUrl"])

    def test_random_flights_skips_unavailable_provider_results(self) -> None:
        with (
            patch("app.main._first_airport_for_homepage", side_effect=_airport_for_query),
            patch("app.main._search_flights_with_fallback", return_value=_unavailable_flight_response()),
        ):
            deals = homepage_random_flights(origin="EMA", limit=1, seed="fixed")

        self.assertEqual(deals, [])

    def test_random_packages_returns_total_price_from_flight_and_hotel(self) -> None:
        with (
            patch("app.main._search_flights_with_fallback", return_value=_flight_response("91.00")),
            patch("app.main._search_hotels_with_fallback", return_value=_hotel_response(300.0)),
        ):
            package = _homepage_package_deal(
                EMA,
                LYS,
                LYON_LOCATION,
                date(2026, 9, 1),
                date(2026, 9, 4),
            )

        self.assertIsNotNone(package)
        assert package is not None
        payload = package.model_dump(by_alias=True)
        self.assertEqual(payload["flightPrice"], 91.0)
        self.assertEqual(payload["hotelPrice"], 300.0)
        self.assertEqual(payload["totalPrice"], 391.0)
        self.assertIn("/voyager?prompt=", payload["searchUrl"])

    def test_random_package_deal_requires_provider_backed_hotel(self) -> None:
        with (
            patch("app.main._search_flights_with_fallback", return_value=_flight_response("91.00")),
            patch("app.main._search_hotels_with_fallback", return_value=_unavailable_hotel_response()),
        ):
            package = _homepage_package_deal(
                EMA,
                LYS,
                LYON_LOCATION,
                date(2026, 9, 1),
                date(2026, 9, 4),
            )

        self.assertIsNone(package)

    def test_random_packages_endpoint_degrades_safely_when_provider_fails(self) -> None:
        with (
            patch("app.main._first_airport_for_homepage", side_effect=_airport_for_query),
            patch("app.main._first_location_for_homepage", side_effect=_location_for_query),
            patch("app.main._homepage_package_deal", side_effect=RuntimeError("provider down")),
        ):
            deals = homepage_random_packages(origin="EMA", limit=1, seed="fixed")

        self.assertEqual(deals, [])

    def test_property_types_are_backend_driven_and_link_to_hotels(self) -> None:
        types = homepage_property_types()
        payload = [item.model_dump(by_alias=True) for item in types]

        self.assertTrue(any(item["id"] == "hotel" for item in payload))
        self.assertTrue(all(item["searchUrl"].startswith("/hotels?") for item in payload))

    def test_trip_ideas_link_to_voyager_for_theme(self) -> None:
        with (
            patch("app.main._first_location_for_homepage", side_effect=_location_for_query),
            patch("app.main._first_airport_for_homepage", side_effect=_airport_for_query),
        ):
            ideas = homepage_trip_ideas(theme="skiing", limit=1)

        self.assertEqual(len(ideas), 1)
        payload = ideas[0].model_dump(by_alias=True)
        self.assertEqual(payload["theme"], "skiing")
        self.assertIn("skiing", payload["voyagerPrompt"])
        self.assertTrue(payload["searchUrl"].startswith("/voyager?prompt="))


if __name__ == "__main__":
    unittest.main()
