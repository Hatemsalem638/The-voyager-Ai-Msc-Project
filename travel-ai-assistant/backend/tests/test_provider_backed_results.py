import unittest
from unittest.mock import patch

from app.main import _search_flights_with_fallback, _search_hotels_with_fallback
from app.schemas import FlightSearchRequest, HotelSearchRequest


class ProviderBackedResultSafetyTests(unittest.TestCase):
    def test_flight_search_without_duffel_returns_unavailable_no_cards(self) -> None:
        request = FlightSearchRequest.model_validate(
            {
                "searchType": "flight",
                "passengerCount": 1,
                "itinerary": {
                    "tripType": "round_trip",
                    "origin": {"code": "EMA", "city": "East Midlands"},
                    "destination": {"code": "MCO", "city": "Orlando"},
                    "dates": {"departure": "2026-11-15", "return": "2026-11-19"},
                },
                "preferences": {"cabinClass": "economy", "maxStops": None},
            }
        )

        with patch("app.main.has_duffel_credentials", return_value=False):
            response = _search_flights_with_fallback(request)

        self.assertEqual(response.provider_used, "unavailable")
        self.assertEqual(response.flights, [])
        self.assertIn("provider-backed flight results", response.fallback_reason or "")

    def test_hotel_search_without_liteapi_returns_unavailable_no_cards(self) -> None:
        request = HotelSearchRequest.model_validate(
            {
                "searchType": "hotel",
                "destination": {"city": "Orlando", "country": "United States"},
                "checkInDate": "2026-11-15",
                "checkOutDate": "2026-11-19",
                "guestCount": 1,
                "roomCount": 1,
                "preferences": {"currency": "GBP"},
            }
        )

        with patch("app.main.liteapi_hotels_enabled", return_value=False):
            response = _search_hotels_with_fallback(request)

        self.assertEqual(response.provider_used, "unavailable")
        self.assertEqual(response.results, [])
        self.assertIn("provider-backed hotel results", response.message or "")


if __name__ == "__main__":
    unittest.main()
