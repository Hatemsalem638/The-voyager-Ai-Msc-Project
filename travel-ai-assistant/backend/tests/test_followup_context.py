import unittest
from datetime import date
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.llama_service import extract_travel_intent
from app.main import (
    FLIGHT_SEARCH_MEMORY,
    HOTEL_SEARCH_MEMORY,
    _execute_tool_call,
    _rule_based_chat_fallback,
)
from app.memory_store import load_safe_llama_context
from app.models import ChatSession, User
from app.mock_provider import search_mock_flight_results
from app.providers.mock_hotel_provider import search_mock_hotels
from app.services.chat_service import _ambiguous_destination_followup
from app.schemas import (
    ChatRequest,
    FlightSearchRequest,
    FlightSearchResponse,
    HotelSearchRequest,
    HotelSearchResponse,
    TravelToolCall,
)


TODAY = date(2026, 6, 20)


def flight_request(
    origin_code: str = "CAI",
    origin_city: str = "Cairo",
    destination_code: str = "LHR",
    destination_city: str = "London",
    departure: str = "2026-07-10",
    passenger_count: int = 1,
) -> dict:
    return {
        "searchType": "flight",
        "passengerCount": passenger_count,
        "itinerary": {
            "tripType": "one_way",
            "origin": {"city": origin_city, "code": origin_code},
            "destination": {"city": destination_city, "code": destination_code},
            "dates": {"departure": departure},
        },
        "preferences": {"cabinClass": "economy", "maxStops": None},
    }


DEFAULT_EXAMPLE_ROUTE = flight_request(
    origin_code="LHR",
    origin_city="London",
    destination_code="JFK",
    destination_city="New York",
    departure="2026-09-12",
)


class FollowUpContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.session_factory()
        self.user = User(
            display_name="Follow-up Test User",
            email="followup-test@example.com",
            password_hash="",
        )
        self.db.add(self.user)
        self.db.flush()
        self.session = ChatSession(user_id=self.user.id, title="Follow-up test")
        self.db.add(self.session)
        self.db.commit()
        FLIGHT_SEARCH_MEMORY.pop(self.session.id, None)
        HOTEL_SEARCH_MEMORY.pop(self.session.id, None)

    def tearDown(self) -> None:
        FLIGHT_SEARCH_MEMORY.pop(self.session.id, None)
        HOTEL_SEARCH_MEMORY.pop(self.session.id, None)
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def _mock_flight_search(request: FlightSearchRequest) -> FlightSearchResponse:
        return FlightSearchResponse(
            provider_used="duffel",
            flights=search_mock_flight_results(request),
            fallback_reason="Provider unavailable.",
        )

    @staticmethod
    def _mock_hotel_search(request: HotelSearchRequest) -> HotelSearchResponse:
        return HotelSearchResponse(
            provider_used="liteapi",
            results=search_mock_hotels(request),
            message="Provider unavailable.",
        )

    def _search_initial_flight(self, request_data: dict | None = None) -> None:
        with (
            patch("app.main.today_utc_date", return_value=TODAY),
            patch("app.main._search_flights_with_fallback", side_effect=self._mock_flight_search),
        ):
            _execute_tool_call(
                TravelToolCall(
                    tool="search_flights",
                    arguments={"search_request": request_data or flight_request()},
                ),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="Find flights from Cairo to London on 2026-07-10"),
            )

    def test_flight_followup_tomorrow_reuses_latest_route(self) -> None:
        self._search_initial_flight()
        with (
            patch("app.main.today_utc_date", return_value=TODAY),
            patch("app.main._search_flights_with_fallback", side_effect=self._mock_flight_search) as search,
        ):
            response = _execute_tool_call(
                TravelToolCall(
                    tool="search_flights",
                    arguments={"search_request": DEFAULT_EXAMPLE_ROUTE},
                ),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="what about tomorrow?"),
            )

        refreshed_request = search.call_args.args[0]
        self.assertEqual(refreshed_request.itinerary.origin.code, "CAI")
        self.assertEqual(refreshed_request.itinerary.destination.code, "LHR")
        self.assertEqual(refreshed_request.itinerary.dates.departure.isoformat(), "2026-06-21")
        self.assertEqual(response.results.flights[0]["origin"], "CAI")
        self.assertEqual(response.results.flights[0]["destination"], "LHR")

    def test_initial_tomorrow_search_uses_tomorrow_date(self) -> None:
        stale_tool_request = flight_request(departure="2023-10-05")
        with (
            patch("app.main.today_utc_date", return_value=TODAY),
            patch("app.main._search_flights_with_fallback", side_effect=self._mock_flight_search) as search,
        ):
            _execute_tool_call(
                TravelToolCall(
                    tool="search_flights",
                    arguments={"search_request": stale_tool_request},
                ),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="is there flights from cairo to london tomorrow?"),
            )

        request_used = search.call_args.args[0]
        self.assertEqual(request_used.itinerary.origin.code, "CAI")
        self.assertEqual(request_used.itinerary.destination.code, "LHR")
        self.assertEqual(request_used.itinerary.dates.departure.isoformat(), "2026-06-21")

    def test_partial_followup_without_context_asks_for_route(self) -> None:
        search = Mock(side_effect=self._mock_flight_search)
        with (
            patch("app.main.today_utc_date", return_value=TODAY),
            patch("app.main._search_flights_with_fallback", search),
        ):
            response = _execute_tool_call(
                TravelToolCall(
                    tool="search_flights",
                    arguments={"search_request": DEFAULT_EXAMPLE_ROUTE},
                ),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="what about tomorrow?"),
            )

        search.assert_not_called()
        self.assertIn("need the route first", response.reply.lower())
        self.assertEqual(response.results.flights, [])

    def test_followup_next_week_reuses_latest_route(self) -> None:
        self._search_initial_flight()
        with (
            patch("app.main.today_utc_date", return_value=TODAY),
            patch("app.main._search_flights_with_fallback", side_effect=self._mock_flight_search) as search,
        ):
            _execute_tool_call(
                TravelToolCall(
                    tool="search_flights",
                    arguments={"search_request": DEFAULT_EXAMPLE_ROUTE},
                ),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="what about next week?"),
            )

        refreshed_request = search.call_args.args[0]
        self.assertEqual(refreshed_request.itinerary.origin.code, "CAI")
        self.assertEqual(refreshed_request.itinerary.destination.code, "LHR")
        self.assertEqual(refreshed_request.itinerary.dates.departure.isoformat(), "2026-06-27")

    def test_passenger_followup_reuses_latest_route(self) -> None:
        self._search_initial_flight()
        with patch(
            "app.main._search_flights_with_fallback",
            side_effect=self._mock_flight_search,
        ) as search:
            _execute_tool_call(
                TravelToolCall(
                    tool="update_passenger_count",
                    arguments={"passenger_count": 2, "select_after_refresh": False},
                ),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="make it 2 passengers"),
            )

        refreshed_request = search.call_args.args[0]
        self.assertEqual(refreshed_request.itinerary.origin.code, "CAI")
        self.assertEqual(refreshed_request.itinerary.destination.code, "LHR")
        self.assertEqual(refreshed_request.passenger_count, 2)

    def test_prompt_example_route_does_not_leak(self) -> None:
        self._search_initial_flight()
        with (
            patch("app.main.today_utc_date", return_value=TODAY),
            patch("app.main._search_flights_with_fallback", side_effect=self._mock_flight_search),
        ):
            response = _execute_tool_call(
                TravelToolCall(
                    tool="search_flights",
                    arguments={"search_request": DEFAULT_EXAMPLE_ROUTE},
                ),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="what about tomorrow?"),
            )

        self.assertNotEqual(response.results.flights[0]["origin"], "LHR")
        self.assertNotEqual(response.results.flights[0]["destination"], "JFK")

    def test_hotel_followup_uses_hotel_context(self) -> None:
        hotel_request = {
            "searchType": "hotel",
            "destination": {"city": "Paris"},
            "checkInDate": "2026-07-10",
            "checkOutDate": "2026-07-13",
            "guestCount": 2,
            "roomCount": 1,
            "preferences": {"currency": "GBP"},
        }
        with patch(
            "app.main._search_hotels_with_fallback",
            side_effect=self._mock_hotel_search,
        ), patch("app.main.today_utc_date", return_value=TODAY):
            _execute_tool_call(
                TravelToolCall(tool="search_hotels", arguments=hotel_request),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="Find a hotel in Paris from 2026-07-10 to 2026-07-13"),
            )

        with (
            patch("app.main.today_utc_date", return_value=TODAY),
            patch("app.main._search_hotels_with_fallback", side_effect=self._mock_hotel_search) as hotels,
            patch("app.main._search_flights_with_fallback") as flights,
        ):
            response = _execute_tool_call(
                TravelToolCall(
                    tool="search_flights",
                    arguments={"search_request": DEFAULT_EXAMPLE_ROUTE},
                ),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="what about tomorrow?"),
            )

        flights.assert_not_called()
        refreshed_request = hotels.call_args.args[0]
        self.assertEqual(refreshed_request.destination.city, "Paris")
        self.assertEqual(response.results.hotels[0]["location"], "Paris")

    def test_latest_context_updates_after_new_route(self) -> None:
        self._search_initial_flight()
        second_route = flight_request(
            origin_code="CDG",
            origin_city="Paris",
            destination_code="FCO",
            destination_city="Rome",
            departure="2026-08-10",
        )
        self._search_initial_flight(second_route)

        with (
            patch("app.main.today_utc_date", return_value=TODAY),
            patch("app.main._search_flights_with_fallback", side_effect=self._mock_flight_search) as search,
        ):
            _execute_tool_call(
                TravelToolCall(
                    tool="search_flights",
                    arguments={"search_request": DEFAULT_EXAMPLE_ROUTE},
                ),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="what about tomorrow?"),
            )

        refreshed_request = search.call_args.args[0]
        self.assertEqual(refreshed_request.itinerary.origin.code, "CDG")
        self.assertEqual(refreshed_request.itinerary.destination.code, "FCO")

    def test_safe_context_contains_active_flight_request_details(self) -> None:
        self._search_initial_flight()
        context = load_safe_llama_context(self.db, self.session.id)

        self.assertEqual(context["active_context_type"], "flight")
        self.assertEqual(context["latest_flight_context"]["route"]["origin"]["code"], "CAI")
        self.assertEqual(context["latest_flight_context"]["route"]["destination"]["city"], "London")
        self.assertEqual(context["latest_flight_context"]["passenger_count"], 1)

    def test_rule_based_fallback_reuses_latest_route(self) -> None:
        self._search_initial_flight()
        with (
            patch("app.main.today_utc_date", return_value=TODAY),
            patch("app.main._search_flights_with_fallback", side_effect=self._mock_flight_search) as search,
        ):
            response = _rule_based_chat_fallback(
                self.db,
                self.user,
                self.session,
                ChatRequest(message="what about tomorrow?"),
            )

        refreshed_request = search.call_args.args[0]
        self.assertEqual(refreshed_request.itinerary.origin.code, "CAI")
        self.assertEqual(refreshed_request.itinerary.destination.code, "LHR")
        self.assertEqual(response.results.flights[0]["origin"], "CAI")

    def test_fallback_destination_extraction_is_not_limited_to_old_city_list(self) -> None:
        old_list_city = extract_travel_intent("Find me a hotel in London for three nights")
        new_city = extract_travel_intent("Find me a hotel in Valencia for three nights")

        self.assertEqual(old_list_city["destination"], "London")
        self.assertEqual(new_city["destination"], "Valencia")

    @patch("app.services.chat_service.suggest_airports")
    def test_ambiguous_destination_followup_uses_validated_lookup_not_old_city_list(
        self,
        mock_suggest,
    ) -> None:
        def suggestions(query: str) -> list[dict[str, str]]:
            airports = {
                "Paris": {
                    "code": "CDG",
                    "name": "Paris Charles de Gaulle Airport",
                    "city": "Paris",
                    "country": "France",
                },
                "Porto": {
                    "code": "OPO",
                    "name": "Porto Airport",
                    "city": "Porto",
                    "country": "Portugal",
                },
            }
            return [airports[query]] if query in airports else []

        mock_suggest.side_effect = suggestions
        latest_request = FlightSearchRequest.model_validate(flight_request())

        self.assertTrue(_ambiguous_destination_followup("what about Paris?", latest_request))
        self.assertTrue(_ambiguous_destination_followup("what about Porto?", latest_request))
