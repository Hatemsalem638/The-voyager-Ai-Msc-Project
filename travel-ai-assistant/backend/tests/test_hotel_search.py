import unittest
import json
import inspect
from datetime import date
from unittest.mock import Mock, patch

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.main import (
    chat,
    _execute_hotel_search_request,
    _execute_tool_call,
    _extract_budget_amount,
    _hotel_city_from_message,
    _known_destination_from_text,
    _search_hotels_with_fallback,
    _store_latest_hotel_results,
)
from app.llama_service import route_tool_with_llama
from app.memory_store import (
    load_active_trip,
    load_safe_llama_context,
    load_session_memory,
    save_active_trip,
    save_default_origin,
)
from app.models import Booking, ChatSession, SessionState, User
from app.providers.liteapi_hotel_provider import (
    LiteApiHotelProviderError,
    _build_payload,
    search_liteapi_hotels,
)
from app.providers.mock_hotel_provider import search_mock_hotels
from app.schemas import (
    ChatRequest,
    HotelSearchRequest,
    HotelSearchResponse,
    NormalisedHotelResult,
    TravelToolCall,
)


HOTEL_REQUEST_JSON = {
    "searchType": "hotel",
    "destination": {"city": "Paris", "country": "France"},
    "checkInDate": "2026-09-12",
    "checkOutDate": "2026-09-16",
    "guestCount": 2,
    "roomCount": 1,
    "preferences": {"currency": "GBP"},
}


class HotelSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.session_factory()
        self.user = User(
            display_name="Hotel Test User",
            email="hotel-test@example.com",
            password_hash="",
        )
        self.db.add(self.user)
        self.db.flush()
        self.session = ChatSession(user_id=self.user.id, title="Hotel test session")
        self.db.add(self.session)
        self.db.flush()
        save_default_origin(
            self.db,
            self.session.id,
            {
                "city": "East Midlands",
                "country": "United Kingdom",
                "airportCode": "EMA",
                "airportName": "East Midlands Airport",
                "source": "test_default_origin",
            },
        )
        self.db.commit()
        self.request = HotelSearchRequest.model_validate(HOTEL_REQUEST_JSON)

    def test_hotel_city_extraction_is_generic_for_valencia(self) -> None:
        self.assertEqual(
            _hotel_city_from_message("hotel in valencia for 3 nights"),
            "Valencia",
        )
        source = inspect.getsource(_hotel_city_from_message)
        self.assertNotIn("city_names", source)

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _mock_provider_response(self) -> HotelSearchResponse:
        results = [
            result.model_copy(update={"provider": "liteapi"})
            for result in search_mock_hotels(self.request)
        ]
        return HotelSearchResponse(
            provider_used="liteapi",
            results=results,
            message="LiteAPI test response.",
        )

    def _search_and_store(self) -> None:
        with patch(
            "app.main._search_hotels_with_fallback",
            return_value=self._mock_provider_response(),
        ):
            _execute_hotel_search_request(
                self.db,
                self.session,
                ChatRequest(message="Find me a hotel in Paris from 2026-09-12 to 2026-09-16 for 2 adults"),
                self.request,
            )

    def _store_five_london_hotels(self) -> None:
        hotel_request = HotelSearchRequest.model_validate(
            {
                "searchType": "hotel",
                "destination": {"city": "London", "country": "United Kingdom"},
                "checkInDate": "2026-08-15",
                "checkOutDate": "2026-08-17",
                "guestCount": 1,
                "roomCount": 1,
                "preferences": {"currency": "GBP"},
            }
        )
        hotels = [
            NormalisedHotelResult(
                id=f"hotel-{index}",
                option_number=index,
                hotel_name=f"London Hotel {index}",
                location="London",
                check_in_date="2026-08-15",
                check_out_date="2026-08-17",
                nights=2,
                guest_count=1,
                room_count=1,
                rating=8.0 + index / 10,
                room_name="Standard Room",
                board_type="RO",
                refundable=True,
                price=100 + index,
                currency="GBP",
                provider="liteapi",
                short_id=f"Hotel {index}",
            )
            for index in range(1, 6)
        ]
        response = HotelSearchResponse(
            provider_used="liteapi",
            results=hotels,
        )
        _store_latest_hotel_results(
            self.session.id,
            {"intent_type": "hotel_search", "message_summary": "seeded hotels"},
            response,
            hotel_request,
            self.db,
        )

    def _hotel_provider_for_request(self, request: HotelSearchRequest) -> HotelSearchResponse:
        results = [
            result.model_copy(update={"provider": "liteapi"})
            for result in search_mock_hotels(request)
        ]
        return HotelSearchResponse(
            provider_used="liteapi",
            results=results,
            message="LiteAPI test response.",
        )

    @patch("app.main.liteapi_hotels_enabled", return_value=False)
    def test_hotel_search_returns_unavailable_when_liteapi_is_disabled(
        self,
        _mock_enabled: Mock,
    ) -> None:
        response = _search_hotels_with_fallback(self.request)

        self.assertEqual(response.provider_used, "unavailable")
        self.assertEqual(response.results, [])
        self.assertIn("provider-backed hotel results", response.message or "")

    def test_mock_hotel_results_are_normalised_and_priced(self) -> None:
        results = search_mock_hotels(self.request)

        self.assertGreaterEqual(len(results), 2)
        self.assertTrue(all(result.price > 0 for result in results))
        self.assertTrue(all(result.nights == 4 for result in results))
        self.assertTrue(all(result.provider == "mock_hotel_fallback" for result in results))

    def test_liteapi_payload_infers_country_for_supported_city(self) -> None:
        payload = _build_payload(self.request)

        self.assertEqual(payload["cityName"], "Paris")
        self.assertEqual(payload["countryCode"], "FR")

    def test_hotel_search_saves_persistent_hotel_memory(self) -> None:
        self._search_and_store()

        state = self.db.query(SessionState).filter(SessionState.session_id == self.session.id).one()
        memory = load_session_memory(self.db, self.session.id)
        self.assertIsNotNone(state.latest_hotel_search_request_json)
        self.assertGreater(len(memory["hotels"]), 0)
        self.assertEqual(memory["current_hotel_option"]["option_number"], 1)

    def test_cheapest_hotel_ranks_stored_results(self) -> None:
        self._search_and_store()

        response = _execute_tool_call(
            TravelToolCall(tool="rank_hotels", arguments={"ranking": "cheapest"}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="Which is the cheapest hotel?"),
        )

        self.assertIn("cheapest hotel", response.reply)
        self.assertEqual(len(response.results.hotels), 1)

    def test_cheapest_hotel_followup_uses_visible_hotel_results(self) -> None:
        self._store_five_london_hotels()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="which is the cheapest hotel?"),
        )

        self.assertIn("The cheapest hotel is option 1", response.reply)
        self.assertIn("London Hotel 1", response.reply)
        self.assertNotIn("Please provide a city", response.reply)
        self.assertEqual(response.results.hotels[0]["hotel_name"], "London Hotel 1")

    def test_cheapest_hotel_followup_mentions_budget_when_available(self) -> None:
        self._store_five_london_hotels()
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["budget"] = 1000
        active_trip["selectedFlightOption"] = {
            "optionNumber": 1,
            "id": "flight-london",
            "offer_id": "flight-london",
            "origin": "EMA",
            "destination": "LHR",
            "airline": "Test Air",
            "price": "41.31",
            "currency": "GBP",
        }
        save_active_trip(self.db, self.session.id, active_trip)

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="which is the cheapest hotel?"),
        )

        self.assertIn("within your £1,000.00 budget", response.reply)
        self.assertIn("£142.31", response.reply)

    def test_show_hotel_option_two_uses_hotel_context(self) -> None:
        self._search_and_store()

        response = _execute_tool_call(
            TravelToolCall(tool="show_hotel_option", arguments={"option_number": 2}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="Show option 2"),
        )

        self.assertEqual(response.results.hotels[0]["option_number"], 2)

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_hotel_only_request_creates_pending_hotel_request(self, _today: Mock) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i want a hotel in london central london near big ben"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        pending = active_trip["pendingHotelRequest"]
        self.assertEqual(pending["intent"], "hotel")
        self.assertEqual(pending["city"], "London")
        self.assertIn("Big Ben", pending["areaOrLandmark"])
        self.assertIsNone(active_trip.get("pendingPackageRequest"))
        self.assertNotEqual((active_trip.get("selectedDestination") or {}).get("airportCode"), "YOJ")
        self.assertIn("check in", response.reply.lower())
        self.assertNotIn("depart", response.reply.lower())

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_hotel_booking_hyde_park_is_hotel_only_in_chat_route(self, _today: Mock) -> None:
        response = chat(
            ChatRequest(
                message="I want to book a hotel in London Hyde Park preferably",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        pending = active_trip["pendingHotelRequest"]
        self.assertEqual(pending["intent"], "hotel")
        self.assertEqual(pending["city"], "London")
        self.assertEqual(pending["areaOrLandmark"], "Hyde Park")
        self.assertEqual(pending["locationLabel"], "near Hyde Park")
        self.assertIsNone(active_trip.get("pendingFlightRequest"))
        self.assertIsNone(active_trip.get("selectedDestination"))
        self.assertIn("London near Hyde Park", response.reply)
        self.assertIn("check in", response.reply.lower())
        self.assertNotIn("East Midlands", response.reply)
        self.assertNotIn("LHR", response.reply)
        self.assertNotIn("departure date", response.reply.lower())

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_flight_wording_blocks_hotel_only_detector(self, _today: Mock) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I wanna fly to London for three days and two nights"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertIsNone(active_trip.get("pendingHotelRequest"))
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "LHR")
        self.assertIn("When do you want to depart", response.reply)
        self.assertNotIn("check in", response.reply.lower())

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_hotel_date_followup_uses_leave_as_check_in_not_high_level(self, _today: Mock) -> None:
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i want a hotel in london central london near big ben"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i want to leave on the 11th"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        pending = active_trip["pendingHotelRequest"]
        self.assertEqual(pending["city"], "London")
        self.assertEqual(pending["checkInDate"], "2026-08-11")
        self.assertIsNone(pending.get("maxBudget"))
        self.assertNotEqual((active_trip.get("selectedDestination") or {}).get("airportCode"), "YOJ")
        self.assertIn("check-in date", response.reply.lower())
        self.assertIn("how many nights", response.reply.lower())
        self.assertNotIn("depart", response.reply.lower())

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_hotel_date_followup_with_live_back_searches_london_hotel(self, _today: Mock) -> None:
        chat(
            ChatRequest(
                message="I want to book a hotel in London Hyde Park preferably",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        with patch("app.main._search_hotels_with_fallback", side_effect=self._hotel_provider_for_request) as hotels:
            response = chat(
                ChatRequest(
                    message="I want to live on the 11th and come back on the 15th",
                    sessionId=self.session.id,
                ),
                db=self.db,
                current_user=self.user,
            )

        hotel_request = hotels.call_args.args[0]
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertIsNone(active_trip.get("pendingHotelRequest"))
        self.assertIsNone(active_trip.get("pendingFlightRequest"))
        self.assertEqual(hotel_request.destination.city, "London")
        self.assertEqual(hotel_request.check_in_date.isoformat(), "2026-08-11")
        self.assertEqual(hotel_request.check_out_date.isoformat(), "2026-08-15")
        self.assertEqual(hotel_request.guest_count, 1)
        self.assertTrue(response.results.hotels)
        self.assertIn("London near Hyde Park", response.reply)
        self.assertNotIn("EMA", response.reply)
        self.assertNotIn("LPL", response.reply)
        self.assertNotIn("flight", response.reply.lower())

    def test_ordinal_date_is_not_budget_and_leave_is_not_high_level(self) -> None:
        self.assertIsNone(_extract_budget_amount("i want to leave on the 11th"))
        self.assertIsNone(_extract_budget_amount("from 11th to 15th"))
        self.assertIsNone(_known_destination_from_text("leave"))
        self.assertIsNone(_known_destination_from_text("level"))

    def test_london_known_destination_never_resolves_to_liverpool(self) -> None:
        london = _known_destination_from_text("London")

        self.assertIsNotNone(london)
        self.assertEqual(london["city"], "London")
        self.assertEqual(london["airportCode"], "LHR")
        self.assertNotEqual(london["airportCode"], "LPL")

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_hotel_nights_trigger_london_hotel_search_not_package(self, _today: Mock) -> None:
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i want a hotel in london central london near big ben"),
        )
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i want to leave on the 11th"),
        )

        with patch("app.main._search_hotels_with_fallback", side_effect=self._hotel_provider_for_request) as hotels:
            response = _execute_tool_call(
                TravelToolCall(tool="general_help", arguments={}),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="3 nights"),
            )

        hotel_request = hotels.call_args.args[0]
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertIsNone(active_trip.get("pendingHotelRequest"))
        self.assertEqual(hotel_request.destination.city, "London")
        self.assertEqual(hotel_request.check_in_date.isoformat(), "2026-08-11")
        self.assertEqual(hotel_request.check_out_date.isoformat(), "2026-08-14")
        self.assertTrue(response.results.hotels)
        self.assertFalse(response.results.packages)
        self.assertIn("London", response.reply)

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_package_only_when_explicitly_requested_after_hotel(self, _today: Mock) -> None:
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i want a hotel in london near big ben"),
        )
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertIsNone(active_trip.get("pendingPackageRequest"))

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="make it a full package"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertIsNotNone(active_trip.get("pendingPackageRequest"))
        self.assertIn("depart", response.reply.lower())

    def test_selecting_hotel_stores_selected_option_without_booking(self) -> None:
        self._search_and_store()

        response = _execute_tool_call(
            TravelToolCall(tool="select_hotel", arguments={"selection": "option_number", "option_number": 1}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I like option 1, book it"),
        )
        active_trip = load_active_trip(self.db, self.session.id)
        memory = load_session_memory(self.db, self.session.id)

        self.assertFalse(response.requires_confirmation)
        self.assertIsNone(response.pending_action)
        self.assertEqual(active_trip["latestVisibleResultType"], "hotel_selected")
        self.assertEqual(active_trip["selectedHotelOption"]["optionNumber"], 1)
        self.assertIsNone(memory["pending_hotel_selection"])
        self.assertIn("add it to your cart", response.reply.lower())

    def test_ambiguous_option_selection_uses_visible_hotel_context(self) -> None:
        self._store_five_london_hotels()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I like option 5"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        memory = load_session_memory(self.db, self.session.id)
        self.assertEqual(active_trip["latestVisibleResultType"], "hotel_selected")
        self.assertEqual(active_trip["selectedHotelOption"]["optionNumber"], 5)
        self.assertIsNone(memory["pending_hotel_selection"])
        self.assertFalse(response.requires_confirmation)
        self.assertIn("london hotel 5", response.reply.lower())

    def test_go_for_option_one_after_hotel_results_selects_hotel(self) -> None:
        self._store_five_london_hotels()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i will go for option 1"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["latestVisibleResultType"], "hotel_selected")
        self.assertEqual(active_trip["selectedHotelOption"]["optionNumber"], 1)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)
        self.assertIn("add it to your cart", response.reply.lower())

    def test_take_first_one_after_hotel_results_selects_hotel(self) -> None:
        self._store_five_london_hotels()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i'll take the first one"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["latestVisibleResultType"], "hotel_selected")
        self.assertEqual(active_trip["selectedHotelOption"]["optionNumber"], 1)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)

    def test_continue_with_option_one_after_hotel_results_selects_hotel(self) -> None:
        self._store_five_london_hotels()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="continue with this option option one"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        memory = load_session_memory(self.db, self.session.id)
        self.assertEqual(active_trip["latestVisibleResultType"], "hotel_selected")
        self.assertEqual(active_trip["selectedHotelOption"]["optionNumber"], 1)
        self.assertIsNone(memory["pending_hotel_selection"])
        self.assertFalse(response.requires_confirmation)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)
        self.assertIn("london hotel 1", response.reply.lower())

    def test_yes_book_it_after_selected_hotel_adds_hotel_to_cart(self) -> None:
        self._store_five_london_hotels()
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I like option 5"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="confirm this hotel"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(self.db.query(Booking).count(), 0)
        self.assertIsNone(response.results.booking)
        self.assertEqual(active_trip["latestVisibleResultType"], "cart")
        self.assertEqual(active_trip["cart"]["items"][0]["itemType"], "hotel")
        self.assertEqual(active_trip["cart"]["items"][0]["hotelOption"]["id"], "hotel-5")
        self.assertEqual(response.next_action, "navigate")
        self.assertEqual(response.next_path, "/cart")
        self.assertIn("added", response.reply.lower())
        self.assertIn("cart", response.reply.lower())
        self.assertIsNone(load_session_memory(self.db, self.session.id)["pending_hotel_selection"])
        self.assertNotIn("provide a city", response.reply.lower())
        self.assertNotIn("check-in date", response.reply.lower())

    def test_repeated_yes_book_it_after_hotel_cart_does_not_search_again(self) -> None:
        self._search_and_store()
        _execute_tool_call(
            TravelToolCall(tool="select_hotel", arguments={"selection": "option_number", "option_number": 1}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="Select option 1"),
        )
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="yes book it"),
        )
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="yes book it"),
        )
        active_trip = load_active_trip(self.db, self.session.id)

        self.assertEqual(self.db.query(Booking).count(), 0)
        self.assertEqual(active_trip["latestVisibleResultType"], "cart")
        self.assertEqual(len(active_trip["cart"]["items"]), 1)
        self.assertIn("already in your cart", response.reply.lower())
        self.assertEqual(response.next_path, "/cart")

    def test_cancel_clears_pending_hotel_selection(self) -> None:
        self._search_and_store()
        _execute_tool_call(
            TravelToolCall(tool="select_hotel", arguments={"selection": "cheapest"}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="Book the cheapest hotel"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="cancel_booking"),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="cancel"),
        )

        self.assertIn("not created", response.reply)
        self.assertIsNone(load_session_memory(self.db, self.session.id)["pending_hotel_selection"])

    @patch("app.providers.liteapi_hotel_provider._load_settings", return_value=("local-key", "https://example.test", True))
    @patch("app.providers.liteapi_hotel_provider.requests.post")
    def test_liteapi_provider_handles_http_timeout_and_malformed_response(
        self,
        mock_post: Mock,
        _mock_settings: Mock,
    ) -> None:
        http_error = Mock()
        http_error.ok = False
        http_error.status_code = 503
        mock_post.return_value = http_error
        with self.assertRaises(LiteApiHotelProviderError):
            search_liteapi_hotels(self.request)

        mock_post.side_effect = requests.Timeout()
        with self.assertRaises(LiteApiHotelProviderError):
            search_liteapi_hotels(self.request)

        malformed = Mock()
        malformed.ok = True
        malformed.json.side_effect = ValueError("invalid JSON")
        mock_post.side_effect = None
        mock_post.return_value = malformed
        with self.assertRaises(LiteApiHotelProviderError):
            search_liteapi_hotels(self.request)

    def test_safe_hotel_context_and_chat_response_do_not_contain_liteapi_key(self) -> None:
        self._search_and_store()
        context = load_safe_llama_context(self.db, self.session.id)

        self.assertNotIn("LITEAPI_KEY", str(context))
        self.assertNotIn("sand_", str(context))
        self.assertNotIn("api_key", str(context).lower())

    @patch.dict("os.environ", {"OLLAMA_MODEL": "llama3.1:8b-instruct-q4_0"})
    @patch("app.llama_service.requests.post")
    def test_llama_router_accepts_structured_hotel_search_tool(
        self,
        mock_post: Mock,
    ) -> None:
        ollama_response = Mock()
        ollama_response.ok = True
        ollama_response.json.return_value = {
            "message": {
                "content": json.dumps(
                    {
                        "tool": "search_hotels",
                        "arguments": HOTEL_REQUEST_JSON,
                        "confidence": 0.94,
                        "explanation": "The user requested a dated hotel search.",
                    }
                )
            }
        }
        mock_post.return_value = ollama_response

        tool_call = route_tool_with_llama(
            "Find me a hotel in Paris from 2026-09-12 to 2026-09-16 for 2 adults.",
            {"has_latest_hotel_results": False},
        )

        self.assertIsNotNone(tool_call)
        self.assertEqual(tool_call.tool, "search_hotels")
        self.assertEqual(tool_call.arguments["destination"]["city"], "Paris")


if __name__ == "__main__":
    unittest.main()
