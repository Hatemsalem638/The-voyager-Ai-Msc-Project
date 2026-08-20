import unittest
from datetime import date
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.date_utils import (
    normalise_hotel_dates,
    normalise_trip_dates,
    resolve_future_date,
    resolve_relative_travel_date,
)
from app.main import (
    _execute_tool_call,
    _search_hotels_with_fallback,
    _search_flights_with_fallback,
    search_flights,
    search_hotels,
)
from app.models import ChatSession, User
from app.mock_provider import search_mock_flight_results
from app.providers.mock_hotel_provider import search_mock_hotels
from app.schemas import (
    ChatRequest,
    FlightSearchRequest,
    FlightSearchResponse,
    HotelSearchRequest,
    HotelSearchResponse,
    TravelToolCall,
)


FUTURE_TODAY = date(2026, 6, 1)
CAIRO_TO_LONDON_PAST_TOOL = {
    "search_request": {
        "searchType": "flight",
        "passengerCount": 1,
        "itinerary": {
            "tripType": "one_way",
            "origin": {"city": "Cairo", "code": "CAI"},
            "destination": {"city": "London", "code": "LHR"},
            "dates": {"departure": "2023-10-05"},
        },
        "preferences": {"cabinClass": "economy", "maxStops": None},
    }
}


class DateHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.session_factory()
        self.user = User(
            display_name="Date Test User",
            email="date-test@example.com",
            password_hash="",
        )
        self.db.add(self.user)
        self.db.flush()
        self.session = ChatSession(user_id=self.user.id, title="Date test session")
        self.db.add(self.session)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_missing_year_october_resolves_to_future_date(self) -> None:
        self.assertEqual(
            resolve_future_date("5th of October", today=FUTURE_TODAY),
            "2026-10-05",
        )
        self.assertEqual(
            resolve_future_date("5 October", today=date(2026, 10, 6)),
            "2027-10-05",
        )

    def test_same_day_next_month_with_today_resolves_next_month(self) -> None:
        self.assertEqual(
            resolve_relative_travel_date(
                "next month on the same day today",
                today=date(2026, 8, 9),
            ),
            "2026-09-09",
        )
        self.assertEqual(
            resolve_relative_travel_date(
                "same day next month",
                today=date(2026, 1, 31),
            ),
            "2026-02-28",
        )

    def test_no_past_flight_dates_from_chat(self) -> None:
        def mock_search(request: FlightSearchRequest) -> FlightSearchResponse:
            return FlightSearchResponse(
                provider_used="mock_fallback",
                flights=search_mock_flight_results(request),
                fallback_reason="Provider unavailable.",
            )

        with (
            patch("app.main.today_utc_date", return_value=FUTURE_TODAY),
            patch("app.main._search_flights_with_fallback", side_effect=mock_search),
        ):
            response = _execute_tool_call(
                TravelToolCall(tool="search_flights", arguments=CAIRO_TO_LONDON_PAST_TOOL),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="can i fly from cairo to london on the 5th of october"),
            )

        self.assertIsNotNone(response)
        self.assertEqual(response.results.flights[0]["outbound_departure_date"], "2026-10-05")

    def test_direct_flight_search_rejects_explicit_past_date(self) -> None:
        request = FlightSearchRequest.model_validate(
            {
                **CAIRO_TO_LONDON_PAST_TOOL["search_request"],
                "itinerary": {
                    **CAIRO_TO_LONDON_PAST_TOOL["search_request"]["itinerary"],
                    "dates": {"departure": "2023-10-05"},
                },
            }
        )

        with self.assertRaises(HTTPException) as context:
            search_flights(request)

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("future departure", str(context.exception.detail))

    def test_mock_flight_results_use_corrected_future_date_and_clean_offer_id(self) -> None:
        request = FlightSearchRequest.model_validate(
            {
                **CAIRO_TO_LONDON_PAST_TOOL["search_request"],
                "itinerary": {
                    **CAIRO_TO_LONDON_PAST_TOOL["search_request"]["itinerary"],
                    "dates": {"departure": "2026-10-05"},
                },
            }
        )
        results = search_mock_flight_results(request)

        self.assertEqual(results[0].outbound_departure_date, "2026-10-05")
        self.assertEqual(results[0].display_id, "Offer CAI-LHR-001")
        self.assertNotIn("Offer -", results[0].display_id or "")

    def test_return_date_must_be_after_departure(self) -> None:
        request = FlightSearchRequest.model_validate(
            {
                "searchType": "flight",
                "passengerCount": 1,
                "itinerary": {
                    "tripType": "round_trip",
                    "origin": {"code": "CAI"},
                    "destination": {"code": "LHR"},
                    "dates": {"departure": "2026-10-10", "return": "2026-10-05"},
                },
                "preferences": {"cabinClass": "economy", "maxStops": None},
            }
        )

        with self.assertRaises(HTTPException) as context:
            search_flights(request)

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("Return date must be after", str(context.exception.detail))

    def test_hotel_missing_year_dates_resolve_future(self) -> None:
        past_hotel_tool = {
            "searchType": "hotel",
            "destination": {"city": "Paris"},
            "checkInDate": "2023-09-12",
            "checkOutDate": "2023-09-16",
            "guestCount": 2,
            "roomCount": 1,
            "preferences": {"currency": "GBP"},
        }

        def mock_search(request: HotelSearchRequest) -> HotelSearchResponse:
            return HotelSearchResponse(
                provider_used="mock_hotel_fallback",
                results=search_mock_hotels(request),
                message="Provider unavailable.",
            )

        with (
            patch("app.main.today_utc_date", return_value=FUTURE_TODAY),
            patch("app.main._search_hotels_with_fallback", side_effect=mock_search),
        ):
            response = _execute_tool_call(
                TravelToolCall(tool="search_hotels", arguments=past_hotel_tool),
                self.db,
                self.user,
                self.session,
                ChatRequest(
                    message="hotel in Paris from 12 September to 16 September for 2 adults and 1 room"
                ),
            )

        self.assertIsNotNone(response)
        self.assertEqual(response.results.hotels[0]["check_in_date"], "2026-09-12")
        self.assertEqual(response.results.hotels[0]["check_out_date"], "2026-09-16")

    def test_direct_hotel_search_rejects_past_checkin(self) -> None:
        request = HotelSearchRequest.model_validate(
            {
                "searchType": "hotel",
                "destination": {"city": "Paris"},
                "checkInDate": "2023-09-12",
                "checkOutDate": "2023-09-16",
                "guestCount": 2,
                "roomCount": 1,
                "preferences": {"currency": "GBP"},
            }
        )

        with self.assertRaises(HTTPException) as context:
            search_hotels(request)

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("Hotel check-in must", str(context.exception.detail))

    def test_general_help_mentions_hotels_and_voyage_ai(self) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="hello"),
        )

        self.assertIsNotNone(response)
        self.assertIn("flights", response.reply.lower())
        self.assertIn("hotels", response.reply.lower())
        self.assertIn("the voyager", response.reply.lower())
        self.assertIn("voyage ai", response.reply.lower())

    def test_hotel_dates_reject_same_day_checkout(self) -> None:
        with self.assertRaises(ValueError):
            normalise_hotel_dates(
                "2026-09-12",
                "2026-09-12",
                today=FUTURE_TODAY,
            )

    def test_one_way_trip_keeps_no_return_date(self) -> None:
        departure, return_date = normalise_trip_dates(
            "5 October",
            today=FUTURE_TODAY,
        )
        self.assertEqual(departure, "2026-10-05")
        self.assertIsNone(return_date)
