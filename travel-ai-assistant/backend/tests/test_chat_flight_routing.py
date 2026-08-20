import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.llama_service import (
    DEFAULT_OLLAMA_MODEL,
    extract_flight_search_with_llama,
    extract_travel_intent,
    route_chat_action_with_llama,
    route_tool_with_llama,
    validate_ollama_model,
)
from app.main import (
    FLIGHT_SEARCH_MEMORY,
    PENDING_BOOKINGS,
    _cheapest_flight,
    _create_booking_from_flight,
    _current_displayed_flight,
    _execute_tool_call,
    _execute_llama_route,
    _flight_option,
    _handle_confirmation_message,
    _handle_flight_follow_up_message,
    _looks_like_flight_search_message,
    _select_pending_flight,
    _selected_pending_flight,
    _should_handle_cancellation,
    _search_flights_with_fallback,
    _store_latest_flight_results,
)
from app.memory_store import (
    append_recent_fact,
    load_safe_llama_context,
    load_session_memory,
    load_user_preferences,
    update_user_preferences,
)
from app.models import ChatSession, SessionState, User
from app.providers.duffel_provider import _build_payload
from app.schemas import (
    ChatRequest,
    FlightSearchRequest,
    FlightSearchResponse,
    LlamaChatActionRoute,
    NormalisedFlightResult,
    TravelToolCall,
)


FLIGHT_MESSAGE = (
    "I want to fly from London Heathrow to New York on 12 September 2026 and "
    "return on 19 September 2026. Economy, no stops."
)

DIRECT_FLIGHT_MESSAGE = (
    "Find a direct flight from LHR to JFK from 2026-09-12 to 2026-09-19"
)

VALID_FLIGHT_JSON = {
    "searchType": "flight",
    "passengerCount": 1,
    "itinerary": {
        "tripType": "round_trip",
        "origin": {"city": "London", "code": "LHR"},
        "destination": {"city": "New York", "code": "JFK"},
        "dates": {"departure": "2026-09-12", "return": "2026-09-19"},
    },
    "preferences": {"cabinClass": "economy", "maxStops": 0},
}

BUSINESS_FLIGHT_JSON = {
    **VALID_FLIGHT_JSON,
    "passengerCount": 3,
    "itinerary": {
        **VALID_FLIGHT_JSON["itinerary"],
        "dates": {"departure": "2026-10-12", "return": "2026-10-19"},
    },
    "preferences": {"cabinClass": "business", "maxStops": 0},
}


class ChatFlightRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session_id = 700
        FLIGHT_SEARCH_MEMORY.pop(self.session_id, None)

    def tearDown(self) -> None:
        FLIGHT_SEARCH_MEMORY.pop(self.session_id, None)
        PENDING_BOOKINGS.pop(self.session_id, None)

    def _seed_flight_memory(self) -> None:
        response = FlightSearchResponse(
            provider_used="duffel",
            flights=[
                NormalisedFlightResult(
                    id="off_first",
                    origin="LHR",
                    destination="JFK",
                    airline="Test Air One",
                    departure_date="2026-09-12T08:00:00",
                    return_date="2026-09-19T12:00:00",
                    stops=0,
                    duration_minutes=480,
                    cabin_class="economy",
                    price="500.00",
                    currency="GBP",
                    provider="duffel",
                ),
                NormalisedFlightResult(
                    id="off_second",
                    origin="LHR",
                    destination="JFK",
                    airline="Test Air Two",
                    departure_date="2026-09-12T09:00:00",
                    return_date="2026-09-19T13:00:00",
                    stops=0,
                    duration_minutes=420,
                    cabin_class="economy",
                    price="300.00",
                    currency="GBP",
                    provider="duffel",
                ),
            ],
        )
        _store_latest_flight_results(
            self.session_id,
            {
                "intent_type": "flight_search",
                "destination": "New York",
                "booking_reference": None,
                "departure_city": "London",
                "travelers": 1,
                "budget_level": "medium",
                "message_summary": FLIGHT_MESSAGE,
            },
            response,
            FlightSearchRequest.model_validate(VALID_FLIGHT_JSON),
        )

    def test_natural_language_flight_message_routes_to_flight_search(self) -> None:
        self.assertTrue(_looks_like_flight_search_message(FLIGHT_MESSAGE))
        self.assertEqual(
            extract_travel_intent(FLIGHT_MESSAGE)["intent_type"],
            "flight_search",
        )

    def test_no_only_cancels_with_a_pending_booking(self) -> None:
        self.assertFalse(_should_handle_cancellation("no", session_id=101))

        PENDING_BOOKINGS[101] = {}
        try:
            self.assertTrue(_should_handle_cancellation("no", session_id=101))
        finally:
            PENDING_BOOKINGS.pop(101, None)

    def test_no_stops_is_not_a_cancellation(self) -> None:
        PENDING_BOOKINGS[102] = {}
        try:
            self.assertFalse(_should_handle_cancellation("no stops", session_id=102))
        finally:
            PENDING_BOOKINGS.pop(102, None)

    def test_direct_airport_code_message_routes_to_flight_search(self) -> None:
        self.assertTrue(_looks_like_flight_search_message(DIRECT_FLIGHT_MESSAGE))

    @patch.dict("os.environ", {"OLLAMA_MODEL": "llama3.1:8b-instruct-q4_0"})
    @patch("app.llama_service.requests.post")
    def test_llama_validates_the_expected_flight_schema(self, mock_post: Mock) -> None:
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "message": {"content": json.dumps(VALID_FLIGHT_JSON)}
        }
        mock_post.return_value = mock_response

        result = extract_flight_search_with_llama(FLIGHT_MESSAGE)

        self.assertIsNotNone(result)
        self.assertEqual(result.itinerary.origin.code, "LHR")
        self.assertEqual(result.preferences.max_stops, 0)
        self.assertEqual(mock_post.call_args.kwargs["json"]["model"], "llama3.1:8b-instruct-q4_0")

    @patch.dict("os.environ", {"OLLAMA_MODEL": "llama3.1:8b-instruct-q4_0"})
    @patch("app.llama_service.requests.post")
    def test_llama_extracts_business_class_and_passenger_count(
        self,
        mock_post: Mock,
    ) -> None:
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "message": {"content": json.dumps(BUSINESS_FLIGHT_JSON)}
        }
        mock_post.return_value = mock_response

        result = extract_flight_search_with_llama(
            "I want to fly from London Heathrow to New York on 12 October 2026 "
            "and return on 19 October 2026 for 3 passengers, business, no stops."
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.preferences.cabin_class, "business")
        self.assertEqual(result.passenger_count, 3)

    def test_default_ollama_model_is_q4(self) -> None:
        self.assertEqual(DEFAULT_OLLAMA_MODEL, "llama3.1:8b-instruct-q4_0")

    def test_non_q4_ollama_model_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid OLLAMA_MODEL"):
            validate_ollama_model("llama3.1:8b")

    def test_duffel_payload_creates_one_adult_per_passenger(self) -> None:
        request = FlightSearchRequest.model_validate(BUSINESS_FLIGHT_JSON)

        payload = _build_payload(request)

        self.assertEqual(payload["data"]["cabin_class"], "business")
        self.assertEqual(len(payload["data"]["passengers"]), 3)
        self.assertTrue(
            all(passenger["type"] == "adult" for passenger in payload["data"]["passengers"])
        )

    @patch("app.main.has_duffel_credentials", return_value=True)
    @patch("app.main.search_duffel_flights")
    def test_valid_flight_search_uses_duffel(
        self,
        mock_search_duffel_flights: Mock,
        _mock_has_duffel_credentials: Mock,
    ) -> None:
        mock_search_duffel_flights.return_value = [
            NormalisedFlightResult(
                id="off_test",
                origin="LHR",
                destination="JFK",
                airline="Test Air",
                departure_date="2026-09-12T08:00:00",
                return_date="2026-09-19T12:00:00",
                stops=0,
                cabin_class="economy",
                price="400.00",
                currency="GBP",
                provider="duffel",
            )
        ]

        response = _search_flights_with_fallback(
            FlightSearchRequest.model_validate(VALID_FLIGHT_JSON)
        )

        self.assertEqual(response.provider_used, "duffel")
        self.assertEqual(response.flights[0].provider, "duffel")

    def test_flight_search_then_which_is_the_cheapest(self) -> None:
        self._seed_flight_memory()

        result = _cheapest_flight(self.session_id)

        self.assertIsNotNone(result)
        option_number, flight = result
        self.assertEqual(option_number, 2)
        self.assertEqual(flight["id"], "off_second")

    def test_flight_search_then_show_option_two(self) -> None:
        self._seed_flight_memory()

        result = _flight_option(self.session_id, 2)

        self.assertIsNotNone(result)
        option_number, flight = result
        self.assertEqual(option_number, 2)
        self.assertEqual(flight["id"], "off_second")

    @patch("app.main._booking_to_details")
    @patch("app.main._create_booking_from_flight")
    def test_select_option_two_then_confirm_adds_flight_to_cart(
        self,
        mock_create_booking_from_flight: Mock,
        mock_booking_to_details: Mock,
    ) -> None:
        self._seed_flight_memory()
        _option_number, selected_flight = _flight_option(self.session_id, 2)
        _select_pending_flight(self.session_id, selected_flight)
        mock_create_booking_from_flight.return_value = SimpleNamespace(
            booking_reference="TA123456"
        )
        mock_booking_to_details.return_value = {
            "reference": "TA123456",
            "destination": "JFK",
        }

        response = _handle_confirmation_message(
            Mock(),
            SimpleNamespace(id=1),
            SimpleNamespace(id=self.session_id),
            ChatRequest(message="confirm"),
        )

        mock_create_booking_from_flight.assert_not_called()
        self.assertIsNone(response.results.booking)
        self.assertIsNotNone(response.results.cart)
        self.assertEqual(response.results.cart["items"][0]["itemType"], "flight")
        self.assertEqual(response.next_path, "/cart")
        self.assertIsNone(_selected_pending_flight(self.session_id))

    def test_which_is_cheapest_before_any_search(self) -> None:
        session = SimpleNamespace(id=701)
        response = _handle_flight_follow_up_message(
            Mock(),
            session,
            ChatRequest(message="which is cheapest?"),
        )

        self.assertEqual(response.reply, "Please search for flights first.")

    @patch.dict("os.environ", {"OLLAMA_MODEL": "llama3.1:8b-instruct-q4_0"})
    @patch("app.llama_service.requests.post")
    def test_llama_router_validates_select_current_action(
        self,
        mock_post: Mock,
    ) -> None:
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "message": {
                "content": json.dumps(
                    {
                        "action": "select_current_flight",
                        "option_number": None,
                        "booking_reference": None,
                    }
                )
            }
        }
        mock_post.return_value = mock_response

        route = route_chat_action_with_llama(
            "ok i like it can i book it please?",
            {"has_latest_flight_results": True, "currently_displayed_flight_option": 2},
        )

        self.assertIsNotNone(route)
        self.assertEqual(route.action, "select_current_flight")

    @patch.dict("os.environ", {"OLLAMA_MODEL": "llama3.1:8b-instruct-q4_0"})
    @patch("app.llama_service.requests.post")
    def test_llama_tool_router_validates_current_flight_selection(
        self,
        mock_post: Mock,
    ) -> None:
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "message": {
                "content": json.dumps(
                    {
                        "tool": "select_flight",
                        "arguments": {"selection": "current_displayed"},
                        "confidence": 0.91,
                        "explanation": "The user wants the current flight.",
                    }
                )
            }
        }
        mock_post.return_value = mock_response

        tool_call = route_tool_with_llama(
            "ok i like it can i book it please?",
            {"current_displayed_flight": {"option_number": 2}},
        )

        self.assertIsNotNone(tool_call)
        self.assertEqual(tool_call.tool, "select_flight")
        self.assertEqual(tool_call.arguments["selection"], "current_displayed")
        self.assertEqual(mock_post.call_args.kwargs["json"]["model"], "llama3.1:8b-instruct-q4_0")

    def test_tool_availability_without_context_requests_flight_search(self) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="check_flight_availability"),
            Mock(),
            SimpleNamespace(id=1),
            SimpleNamespace(id=self.session_id),
            ChatRequest(message="is it still available?"),
        )

        self.assertEqual(response.reply, "Please search for flights first.")

    @patch("app.main._search_flights_with_fallback")
    def test_tool_search_uses_typed_request_and_stores_flight_memory(
        self,
        mock_search_flights: Mock,
    ) -> None:
        mock_search_flights.return_value = FlightSearchResponse(
            provider_used="duffel",
            flights=[
                NormalisedFlightResult(
                    id="off_search",
                    origin="LHR",
                    destination="JFK",
                    airline="Test Air",
                    departure_date="2026-09-12T08:00:00",
                    return_date="2026-09-19T12:00:00",
                    stops=0,
                    cabin_class="economy",
                    price="400.00",
                    currency="GBP",
                    provider="duffel",
                )
            ],
        )

        response = _execute_tool_call(
            TravelToolCall(
                tool="search_flights",
                arguments={"search_request": VALID_FLIGHT_JSON},
            ),
            Mock(),
            SimpleNamespace(id=1),
            SimpleNamespace(id=self.session_id),
            ChatRequest(message=FLIGHT_MESSAGE),
        )

        mock_search_flights.assert_called_once()
        self.assertEqual(response.results.flights[0]["provider"], "duffel")
        self.assertEqual(FLIGHT_SEARCH_MEMORY[self.session_id]["search_request"]["searchType"], "flight")

    @patch("app.main.extract_flight_search_with_llama", return_value=None)
    def test_direct_no_stops_without_dates_is_not_a_cancellation(
        self,
        _mock_extract_flight_search: Mock,
    ) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="search_flights"),
            Mock(),
            SimpleNamespace(id=1),
            SimpleNamespace(id=self.session_id),
            ChatRequest(message="I want a direct flight from LHR to JFK with no stops."),
        )

        self.assertIn("departure date", response.reply)
        self.assertNotEqual(response.intent.intent_type, "booking_cancellation")

    def test_tool_ranks_cheapest_and_sets_current_displayed_flight(self) -> None:
        self._seed_flight_memory()

        response = _execute_tool_call(
            TravelToolCall(
                tool="rank_flights",
                arguments={"ranking": "cheapest", "limit": 1},
            ),
            Mock(),
            SimpleNamespace(id=1),
            SimpleNamespace(id=self.session_id),
            ChatRequest(message="Which is the cheapest?"),
        )

        self.assertEqual(response.results.flights[0]["id"], "off_second")
        self.assertEqual(_current_displayed_flight(self.session_id)[0], 2)

    def test_combined_ranking_returns_one_current_flight(self) -> None:
        self._seed_flight_memory()

        response = _execute_tool_call(
            TravelToolCall(
                tool="rank_flights",
                arguments={"ranking": "cheapest_fastest", "limit": 1},
            ),
            Mock(),
            SimpleNamespace(id=1),
            SimpleNamespace(id=self.session_id),
            ChatRequest(message="which is the shortest and cheapest flight?"),
        )

        self.assertIn("best combined option", response.reply)
        self.assertEqual(response.results.flights[0]["id"], "off_second")
        self.assertEqual(_current_displayed_flight(self.session_id)[0], 2)

    def test_cabin_follow_up_describes_current_displayed_flight(self) -> None:
        self._seed_flight_memory()

        response = _execute_tool_call(
            TravelToolCall(
                tool="describe_current_flight",
                arguments={"field": "cabin_class"},
            ),
            Mock(),
            SimpleNamespace(id=1),
            SimpleNamespace(id=self.session_id),
            ChatRequest(message="is it business or economy?"),
        )

        self.assertEqual(response.reply, "The current flight is Economy class.")
        self.assertEqual(response.results.flights[0]["option_number"], 1)

    @patch("app.main._search_flights_with_fallback")
    def test_passenger_follow_up_refreshes_before_selecting(
        self,
        mock_search_flights: Mock,
    ) -> None:
        self._seed_flight_memory()
        mock_search_flights.return_value = FlightSearchResponse(
            provider_used="duffel",
            flights=[
                NormalisedFlightResult(
                    id="off_three_passengers",
                    origin="LHR",
                    destination="JFK",
                    airline="Test Air Two",
                    departure_date="2026-09-12T09:00:00",
                    return_date="2026-09-19T13:00:00",
                    stops=0,
                    duration_minutes=420,
                    cabin_class="economy",
                    passenger_count=3,
                    price="900.00",
                    currency="GBP",
                    provider="duffel",
                )
            ],
        )

        response = _execute_tool_call(
            TravelToolCall(
                tool="update_passenger_count",
                arguments={"passenger_count": 3, "select_after_refresh": True},
            ),
            Mock(),
            SimpleNamespace(id=1),
            SimpleNamespace(id=self.session_id),
            ChatRequest(message="can I book it for 3 passengers?"),
        )

        refreshed_request = mock_search_flights.call_args.args[0]
        self.assertEqual(refreshed_request.passenger_count, 3)
        self.assertFalse(response.requires_confirmation)
        self.assertIsNone(response.pending_action)
        self.assertEqual(response.results.flights[0]["passenger_count"], 3)
        self.assertEqual(_selected_pending_flight(self.session_id)["id"], "off_three_passengers")
        self.assertIn("cart", response.reply.lower())

    @patch("app.main._create_booking_record")
    def test_flight_booking_summary_includes_flight_details(
        self,
        mock_create_booking_record: Mock,
    ) -> None:
        self._seed_flight_memory()
        _option_number, flight = _flight_option(self.session_id, 2)

        _create_booking_from_flight(
            Mock(),
            SimpleNamespace(id=1),
            SimpleNamespace(id=self.session_id),
            flight,
        )

        summary = mock_create_booking_record.call_args.kwargs["summary"]
        self.assertIn("LHR to JFK", summary)
        self.assertIn("Economy", summary)
        self.assertIn("1 passenger", summary)
        self.assertIn("non-stop", summary)
        self.assertIn("2026-09-12", summary)
        self.assertIn("2026-09-19", summary)
        self.assertIn("GBP 300.00", summary)

    @patch("app.main._booking_to_details")
    @patch("app.main._create_booking_from_flight")
    def test_tool_selects_current_flight_then_confirms_booking(
        self,
        mock_create_booking_from_flight: Mock,
        mock_booking_to_details: Mock,
    ) -> None:
        self._seed_flight_memory()
        db = Mock()
        session = SimpleNamespace(id=self.session_id)
        user = SimpleNamespace(id=1)
        _execute_tool_call(
            TravelToolCall(
                tool="rank_flights",
                arguments={"ranking": "cheapest", "limit": 1},
            ),
            db,
            user,
            session,
            ChatRequest(message="Which is cheapest?"),
        )
        selection_response = _execute_tool_call(
            TravelToolCall(
                tool="select_flight",
                arguments={"selection": "current_displayed"},
            ),
            db,
            user,
            session,
            ChatRequest(message="ok i like it can i book it please?"),
        )
        confirmation_response = _execute_tool_call(
            TravelToolCall(tool="confirm_booking", arguments={}),
            db,
            user,
            session,
            ChatRequest(message="yes confirm it"),
        )

        self.assertFalse(selection_response.requires_confirmation)
        self.assertIsNone(selection_response.pending_action)
        mock_create_booking_from_flight.assert_not_called()
        self.assertIsNotNone(confirmation_response.results.cart)
        self.assertEqual(confirmation_response.results.cart["items"][0]["itemType"], "flight")
        self.assertIsNone(_selected_pending_flight(self.session_id))

    @patch("app.main._search_flights_with_fallback")
    def test_tool_refreshes_availability_for_current_flight(
        self,
        mock_search_flights: Mock,
    ) -> None:
        self._seed_flight_memory()
        db = Mock()
        session = SimpleNamespace(id=self.session_id)
        _execute_tool_call(
            TravelToolCall(
                tool="rank_flights",
                arguments={"ranking": "cheapest", "limit": 1},
            ),
            db,
            SimpleNamespace(id=1),
            session,
            ChatRequest(message="Which is cheapest?"),
        )
        mock_search_flights.return_value = FlightSearchResponse(
            provider_used="duffel",
            flights=[
                NormalisedFlightResult(
                    id="off_second",
                    origin="LHR",
                    destination="JFK",
                    airline="Test Air Two",
                    departure_date="2026-09-12T09:00:00",
                    return_date="2026-09-19T13:00:00",
                    stops=0,
                    duration_minutes=420,
                    cabin_class="economy",
                    price="300.00",
                    currency="GBP",
                    provider="duffel",
                )
            ],
        )

        response = _execute_tool_call(
            TravelToolCall(
                tool="check_flight_availability",
                arguments={"selection": "selected_pending"},
            ),
            db,
            SimpleNamespace(id=1),
            session,
            ChatRequest(message="is it still available?"),
        )

        mock_search_flights.assert_called_once()
        self.assertIn("appears available", response.reply)
        self.assertEqual(response.results.flights[0]["id"], "off_second")

    def test_tool_cancels_a_pending_flight_selection(self) -> None:
        self._seed_flight_memory()
        db = Mock()
        session = SimpleNamespace(id=self.session_id)
        _execute_tool_call(
            TravelToolCall(
                tool="select_flight",
                arguments={"selection": "option_number", "option_number": 2},
            ),
            db,
            SimpleNamespace(id=1),
            session,
            ChatRequest(message="select option 2"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="cancel_booking"),
            db,
            SimpleNamespace(id=1),
            session,
            ChatRequest(message="no cancel it"),
        )

        self.assertEqual(response.reply, "No problem. I have not created the booking.")
        self.assertIsNone(_selected_pending_flight(self.session_id))

    @patch("app.main._search_booking_from_database")
    def test_tool_looks_up_a_booking_reference(
        self,
        mock_search_booking: Mock,
    ) -> None:
        mock_search_booking.return_value = {
            "flights": [],
            "hotels": [],
            "packages": [],
            "booking": {
                "reference": "TA123456",
                "status": "Confirmed",
                "destination": "JFK",
            },
            "suggestions": [],
        }

        response = _execute_tool_call(
            TravelToolCall(
                tool="booking_lookup",
                arguments={"booking_reference": "ta123456"},
            ),
            Mock(),
            SimpleNamespace(id=1),
            SimpleNamespace(id=self.session_id),
            ChatRequest(message="Check booking TA123456"),
        )

        mock_search_booking.assert_called_once()
        self.assertEqual(response.results.booking["reference"], "TA123456")

    def test_cheapest_shown_then_select_current_flight(self) -> None:
        self._seed_flight_memory()
        session = SimpleNamespace(id=self.session_id)
        user = SimpleNamespace(id=1)

        cheapest_response = _execute_llama_route(
            LlamaChatActionRoute(action="show_cheapest_flight"),
            Mock(),
            user,
            session,
            ChatRequest(message="which is cheapest?"),
        )
        selected_response = _execute_llama_route(
            LlamaChatActionRoute(action="select_current_flight"),
            Mock(),
            user,
            session,
            ChatRequest(message="ok i like it can i book it please?"),
        )

        self.assertEqual(cheapest_response.results.flights[0]["id"], "off_second")
        self.assertEqual(_current_displayed_flight(self.session_id)[0], 2)
        self.assertFalse(selected_response.requires_confirmation)
        self.assertEqual(_selected_pending_flight(self.session_id)["id"], "off_second")
        self.assertIn("cart", selected_response.reply.lower())

    @patch("app.main._booking_to_details")
    @patch("app.main._create_booking_from_flight")
    def test_router_confirm_selected_flight(
        self,
        mock_create_booking_from_flight: Mock,
        mock_booking_to_details: Mock,
    ) -> None:
        self._seed_flight_memory()
        _option_number, selected_flight = _flight_option(self.session_id, 2)
        _select_pending_flight(self.session_id, selected_flight)
        mock_create_booking_from_flight.return_value = SimpleNamespace(
            booking_reference="TA654321"
        )
        mock_booking_to_details.return_value = {
            "reference": "TA654321",
            "destination": "JFK",
        }

        response = _execute_llama_route(
            LlamaChatActionRoute(action="confirm_booking"),
            Mock(),
            SimpleNamespace(id=1),
            SimpleNamespace(id=self.session_id),
            ChatRequest(message="confirm"),
        )

        mock_create_booking_from_flight.assert_not_called()
        self.assertIsNotNone(response.results.cart)
        self.assertEqual(response.results.cart["items"][0]["itemType"], "flight")
        self.assertEqual(response.next_path, "/cart")

    def test_select_current_without_flight_context_requests_clarification(self) -> None:
        session = SimpleNamespace(id=702)
        response = _execute_llama_route(
            LlamaChatActionRoute(action="select_current_flight"),
            Mock(),
            SimpleNamespace(id=1),
            session,
            ChatRequest(message="can I book it?"),
        )

        self.assertEqual(response.reply, "Please search for flights first.")

class PersistentTravelMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.db = self.session_factory()
        self.user = User(display_name="Persistent Test User", email="persistent@example.com")
        self.db.add(self.user)
        self.db.flush()
        self.chat_session = ChatSession(user_id=self.user.id, title="Persistent test")
        self.db.add(self.chat_session)
        self.db.flush()
        self.session_id = self.chat_session.id
        FLIGHT_SEARCH_MEMORY.pop(self.session_id, None)

    def tearDown(self) -> None:
        FLIGHT_SEARCH_MEMORY.pop(self.session_id, None)
        PENDING_BOOKINGS.pop(self.session_id, None)
        self.db.close()
        self.engine.dispose()

    def _provider_response(self) -> FlightSearchResponse:
        return FlightSearchResponse(
            provider_used="duffel",
            flights=[
                NormalisedFlightResult(
                    id="off_persist_first",
                    offer_id="off_persist_first",
                    display_id="Offer first",
                    option_number=1,
                    origin="LHR",
                    destination="JFK",
                    airline="Persistent Air One",
                    departure_date="2026-09-12T08:00:00",
                    return_date="2026-09-19T12:00:00",
                    outbound_departure_date="2026-09-12",
                    outbound_departure_time="08:00",
                    return_departure_date="2026-09-19",
                    return_departure_time="12:00",
                    stops=0,
                    stops_label="Non-stop",
                    duration_minutes=480,
                    duration_label="8h",
                    cabin_class="economy",
                    cabin_class_label="Economy",
                    passenger_count=1,
                    price="500.00",
                    currency="GBP",
                    provider="duffel",
                ),
                NormalisedFlightResult(
                    id="off_persist_second",
                    offer_id="off_persist_second",
                    display_id="Offer second",
                    option_number=2,
                    origin="LHR",
                    destination="JFK",
                    airline="Persistent Air Two",
                    departure_date="2026-09-12T09:00:00",
                    return_date="2026-09-19T13:00:00",
                    outbound_departure_date="2026-09-12",
                    outbound_departure_time="09:00",
                    return_departure_date="2026-09-19",
                    return_departure_time="13:00",
                    stops=0,
                    stops_label="Non-stop",
                    duration_minutes=420,
                    duration_label="7h",
                    cabin_class="economy",
                    cabin_class_label="Economy",
                    passenger_count=1,
                    price="300.00",
                    currency="GBP",
                    provider="duffel",
                ),
            ],
        )

    def _save_persistent_flight_search(self) -> None:
        _store_latest_flight_results(
            self.session_id,
            {
                "intent_type": "flight_search",
                "destination": "New York",
                "booking_reference": None,
                "departure_city": "London",
                "travelers": 1,
                "budget_level": "medium",
                "message_summary": FLIGHT_MESSAGE,
            },
            self._provider_response(),
            FlightSearchRequest.model_validate(VALID_FLIGHT_JSON),
            self.db,
        )
        self.db.commit()

    def test_flight_search_saves_persistent_session_state(self) -> None:
        self._save_persistent_flight_search()

        state = (
            self.db.query(SessionState)
            .filter(SessionState.session_id == self.session_id)
            .one()
        )
        self.assertIsNotNone(state.latest_flight_search_request_json)
        self.assertIsNotNone(state.latest_flight_results_json)
        memory = load_session_memory(self.db, self.session_id)
        self.assertEqual(len(memory["flights"]), 2)
        self.assertEqual(memory["search_request"]["searchType"], "flight")

    def test_rank_flights_works_after_memory_dictionary_is_cleared(self) -> None:
        self._save_persistent_flight_search()
        FLIGHT_SEARCH_MEMORY.clear()

        response = _execute_tool_call(
            TravelToolCall(
                tool="rank_flights",
                arguments={"ranking": "cheapest", "limit": 1},
            ),
            self.db,
            self.user,
            self.chat_session,
            ChatRequest(message="which is the cheapest?"),
        )

        self.assertEqual(response.results.flights[0]["id"], "off_persist_second")
        memory = load_session_memory(self.db, self.session_id)
        self.assertEqual(memory["current_flight_option"]["option_number"], 2)

    def test_select_and_confirm_booking_works_after_memory_dictionary_is_cleared(self) -> None:
        self._save_persistent_flight_search()
        selection = _execute_tool_call(
            TravelToolCall(
                tool="select_flight",
                arguments={"selection": "option_number", "option_number": 1},
            ),
            self.db,
            self.user,
            self.chat_session,
            ChatRequest(message="select option 1"),
        )
        self.assertFalse(selection.requires_confirmation)
        self.assertIsNotNone(
            load_session_memory(self.db, self.session_id)["pending_flight_selection"]
        )

        FLIGHT_SEARCH_MEMORY.clear()
        response = _execute_tool_call(
            TravelToolCall(tool="confirm_booking"),
            self.db,
            self.user,
            self.chat_session,
            ChatRequest(message="yes"),
        )

        self.assertIsNone(response.results.booking)
        self.assertIsNotNone(response.results.cart)
        self.assertEqual(response.results.cart["items"][0]["itemType"], "flight")
        self.assertEqual(response.next_path, "/cart")
        memory = load_session_memory(self.db, self.session_id)
        self.assertIsNone(memory["pending_flight_selection"])
        self.assertIsNone(memory["pending_action"])

    def test_cancel_clears_persistent_pending_selection(self) -> None:
        self._save_persistent_flight_search()
        _execute_tool_call(
            TravelToolCall(
                tool="select_flight",
                arguments={"selection": "option_number", "option_number": 1},
            ),
            self.db,
            self.user,
            self.chat_session,
            ChatRequest(message="select option 1"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="cancel_booking"),
            self.db,
            self.user,
            self.chat_session,
            ChatRequest(message="cancel"),
        )

        self.assertEqual(response.reply, "No problem. I have not created the booking.")
        memory = load_session_memory(self.db, self.session_id)
        self.assertIsNone(memory["pending_flight_selection"])
        self.assertIsNone(memory["pending_action"])

    def test_user_preferences_can_be_saved_and_loaded(self) -> None:
        update_user_preferences(
            self.db,
            self.user.id,
            preferred_cabin_class="economy",
            max_stops=0,
            budget_max=800,
        )
        self.db.commit()

        preferences = load_user_preferences(self.db, self.user.id)
        self.assertEqual(preferences["preferred_cabin_class"], "economy")
        self.assertEqual(preferences["max_stops"], 0)
        self.assertEqual(preferences["budget_max"], 800)

    def test_safe_llama_context_excludes_sensitive_data(self) -> None:
        self._save_persistent_flight_search()
        append_recent_fact(
            self.db,
            self.session_id,
            {"sender": "user", "text": "token=duffel_test_not_a_real_token"},
        )
        update_user_preferences(
            self.db,
            self.user.id,
            preferred_cabin_class="economy",
            max_stops=0,
        )
        self.db.commit()

        context = load_safe_llama_context(self.db, self.session_id)
        serialised_context = json.dumps(context).lower()
        self.assertEqual(context["latest_route"]["origin"], "LHR")
        self.assertEqual(context["latest_flight_count"], 2)
        for forbidden_value in [
            "duffel_api_key",
            "duffel_test_",
            "api_key",
            "token",
            "raw_provider_payload",
        ]:
            self.assertNotIn(forbidden_value, serialised_context)


if __name__ == "__main__":
    unittest.main()
