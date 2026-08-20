import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.airport_lookup import AirportLookupError
from app.main import suggest_airport_endpoint, suggest_location_endpoint
from app.providers.liteapi_hotel_provider import LiteApiHotelProviderError


class SuggestionEndpointTests(unittest.TestCase):
    def test_airport_suggest_by_airport_code(self) -> None:
        with patch(
            "app.airport_lookup._search_duffel_airport_suggestions",
            side_effect=AirportLookupError("offline"),
        ):
            results = suggest_airport_endpoint("cai")

        self.assertTrue(
            any(
                airport.code == "CAI"
                and "Cairo International Airport" in airport.display_name
                for airport in results
            )
        )

    def test_airport_suggest_by_city(self) -> None:
        with patch(
            "app.airport_lookup._search_duffel_airport_suggestions",
            side_effect=AirportLookupError("offline"),
        ):
            results = suggest_airport_endpoint("cairo")

        self.assertTrue(any(airport.code == "CAI" for airport in results))

    def test_airport_suggest_by_partial_city(self) -> None:
        with patch(
            "app.airport_lookup._search_duffel_airport_suggestions",
            side_effect=AirportLookupError("offline"),
        ):
            results = suggest_airport_endpoint("lon")
        codes = {airport.code for airport in results}

        self.assertTrue({"LHR", "LGW"}.issubset(codes))

    def test_airport_suggest_minimum_length(self) -> None:
        with self.assertRaises(HTTPException) as context:
            suggest_airport_endpoint("c")

        self.assertEqual(context.exception.status_code, 400)

    def test_hotel_location_suggest_by_city(self) -> None:
        with patch(
            "app.providers.liteapi_hotel_provider._search_liteapi_location_suggestions",
            side_effect=LiteApiHotelProviderError("offline"),
        ):
            results = suggest_location_endpoint("cairo", type="hotel")

        self.assertTrue(
            any(
                location.city == "Cairo"
                and location.country == "Egypt"
                and location.display_name == "Cairo, Egypt"
                for location in results
            )
        )

    def test_hotel_location_suggest_partial(self) -> None:
        with patch(
            "app.providers.liteapi_hotel_provider._search_liteapi_location_suggestions",
            side_effect=LiteApiHotelProviderError("offline"),
        ):
            results = suggest_location_endpoint("par", type="hotel")

        self.assertTrue(any(location.display_name == "Paris, France" for location in results))

    def test_hotel_location_suggest_minimum_length(self) -> None:
        with self.assertRaises(HTTPException) as context:
            suggest_location_endpoint("p", type="hotel")

        self.assertEqual(context.exception.status_code, 400)

    def test_hotel_location_suggest_rejects_non_hotel_type(self) -> None:
        with self.assertRaises(HTTPException) as context:
            suggest_location_endpoint("par", type="restaurant")

        self.assertEqual(context.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
