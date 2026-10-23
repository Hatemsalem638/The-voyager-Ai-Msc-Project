import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.destination_discovery import discover_destinations
from app.main import (
    FLIGHT_SEARCH_MEMORY,
    HOTEL_SEARCH_MEMORY,
    _execute_tool_call,
    _store_latest_flight_results,
    _store_latest_hotel_results,
    chat,
)
from app.memory_store import (
    load_active_trip,
    load_session_memory,
    save_active_trip,
    save_default_origin,
    save_last_tool,
    save_latest_destination_discovery,
    save_latest_flight_search,
    save_pending_destination_discovery as save_persistent_pending_destination_discovery,
)
from app.models import Booking, ChatSession, User
from app.schemas import (
    ChatRequest,
    ClarificationAnswer,
    DestinationDiscoveryRequest,
    DestinationDiscoveryResponse,
    DestinationRecommendation,
    FlightSearchRequest,
    FlightSearchResponse,
    HotelSearchRequest,
    HotelSearchResponse,
    NormalisedFlightResult,
    NormalisedHotelResult,
    TravelToolCall,
)


TODAY = date(2026, 8, 4)
CURRENT_DATE = date(2026, 8, 7)
AUGUST_8 = date(2026, 8, 8)


def _wrong_stale_route() -> dict:
    return {
        "searchType": "flight",
        "passengerCount": 1,
        "itinerary": {
            "tripType": "one_way",
            "origin": {"city": "Erzurum", "code": "ERZ"},
            "destination": {"city": "London", "code": "LHR"},
            "dates": {"departure": "2026-08-05"},
        },
        "preferences": {"cabinClass": "economy", "maxStops": None},
    }


class ActiveTripStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.SessionLocal()
        self.user = User(
            display_name="Active Trip Test User",
            email="active-trip@example.com",
            password_hash="",
        )
        self.db.add(self.user)
        self.db.flush()
        self.session = ChatSession(user_id=self.user.id, title="Active trip test")
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
                "latitude": 52.8311,
                "longitude": -1.3281,
                "source": "browser_location_duffel_places",
            },
        )
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
            flights=[
                NormalisedFlightResult(
                    id=f"offer_{request.itinerary.origin.code}_{request.itinerary.destination.code}",
                    offer_id="mock_offer",
                    display_id="Offer mock",
                    option_number=1,
                    origin=request.itinerary.origin.code,
                    destination=request.itinerary.destination.code,
                    airline="Mock Airways",
                    outbound_departure_date=request.itinerary.dates.departure.isoformat(),
                    return_departure_date=(
                        request.itinerary.dates.return_date.isoformat()
                        if request.itinerary.dates.return_date
                        else None
                    ),
                    stops=0,
                    stops_label="Non-stop",
                    cabin_class=request.preferences.cabin_class,
                    cabin_class_label=request.preferences.cabin_class.title(),
                    passenger_count=request.passenger_count,
                    price="144",
                    currency="GBP",
                    provider="duffel",
                )
            ],
            fallback_reason="Test provider.",
        )

    @staticmethod
    def _mock_hotel_search(request: HotelSearchRequest) -> HotelSearchResponse:
        return HotelSearchResponse(
            provider_used="liteapi",
            results=[
                NormalisedHotelResult(
                    id=f"hotel_{request.destination.city}",
                    option_number=1,
                    hotel_name=f"{request.destination.city} Beach Hotel",
                    location=request.destination.city or "",
                    check_in_date=request.check_in_date.isoformat(),
                    check_out_date=request.check_out_date.isoformat(),
                    nights=3,
                    guest_count=request.guest_count,
                    room_count=request.room_count,
                    rating=4.5,
                    room_name="Standard room",
                    refundable=True,
                    price=300,
                    currency="GBP",
                    provider="liteapi",
                    short_id="Hotel mock",
                )
            ],
            message="Test provider.",
        )

    def _flight_request(self) -> FlightSearchRequest:
        return FlightSearchRequest.model_validate(
            {
                "searchType": "flight",
                "passengerCount": 1,
                "itinerary": {
                    "tripType": "round_trip",
                    "origin": {"city": "East Midlands", "code": "EMA"},
                    "destination": {"city": "Erzurum", "code": "ERZ"},
                    "dates": {"departure": "2026-09-01", "return": "2026-09-04"},
                },
                "preferences": {"cabinClass": "economy", "maxStops": 0},
            }
        )

    def _seed_flight_results(self, prices: list[str]) -> None:
        request = self._flight_request()
        response = FlightSearchResponse(
            provider_used="duffel",
            flights=[
                NormalisedFlightResult(
                    id=f"flight_{index}",
                    offer_id=f"offer_{index}",
                    display_id=f"Offer {index}",
                    option_number=index,
                    origin=request.itinerary.origin.code,
                    destination=request.itinerary.destination.code,
                    airline=f"Flight Test Air {index}",
                    outbound_departure_date=request.itinerary.dates.departure.isoformat(),
                    return_departure_date=request.itinerary.dates.return_date.isoformat(),
                    stops=0,
                    stops_label="Non-stop",
                    cabin_class="economy",
                    cabin_class_label="Economy",
                    passenger_count=1,
                    price=price,
                    currency="GBP",
                    provider="duffel",
                )
                for index, price in enumerate(prices, start=1)
            ],
        )
        _store_latest_flight_results(
            self.session.id,
            {
                "intent_type": "flight_search",
                "destination": "Erzurum",
                "booking_reference": None,
                "departure_city": "East Midlands",
                "travelers": 1,
                "budget_level": "medium",
                "message_summary": "Seeded flight results",
            },
            response,
            request,
            self.db,
        )
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["latestVisibleResultType"] = "flights"
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

    @staticmethod
    def _empty_flight_search(request: FlightSearchRequest) -> FlightSearchResponse:
        return FlightSearchResponse(
            provider_used="duffel",
            flights=[],
            fallback_reason="No test results.",
        )

    @staticmethod
    def _airport_suggestions(query: str) -> list[dict]:
        airports = {
            "MAN": {
                "code": "MAN",
                "name": "Manchester Airport",
                "city": "Manchester",
                "country": "United Kingdom",
            },
            "BHX": {
                "code": "BHX",
                "name": "Birmingham Airport",
                "city": "Birmingham",
                "country": "United Kingdom",
            },
            "MCO": {
                "code": "MCO",
                "name": "Orlando International Airport",
                "city": "Orlando",
                "country": "United States",
            },
            "MIA": {
                "code": "MIA",
                "name": "Miami International Airport",
                "city": "Miami",
                "country": "United States",
            },
            "TPA": {
                "code": "TPA",
                "name": "Tampa International Airport",
                "city": "Tampa",
                "country": "United States",
            },
            "FLL": {
                "code": "FLL",
                "name": "Fort Lauderdale-Hollywood International Airport",
                "city": "Fort Lauderdale",
                "country": "United States",
            },
            "MAD": {
                "code": "MAD",
                "name": "Adolfo Suarez Madrid-Barajas Airport",
                "city": "Madrid",
                "country": "Spain",
            },
            "BCN": {
                "code": "BCN",
                "name": "Barcelona-El Prat Airport",
                "city": "Barcelona",
                "country": "Spain",
            },
            "VLC": {
                "code": "VLC",
                "name": "Valencia Airport",
                "city": "Valencia",
                "country": "Spain",
            },
            "LCG": {
                "code": "LCG",
                "name": "A Coruna Airport",
                "city": "A Coruna",
                "country": "Spain",
            },
            "BAR": {
                "code": "BAR",
                "name": "Qionghai Boao Airport",
                "city": "Qionghai",
                "country": "China",
            },
            "FCO": {
                "code": "FCO",
                "name": "Rome Fiumicino Airport",
                "city": "Rome",
                "country": "Italy",
            },
            "MXP": {
                "code": "MXP",
                "name": "Milan Malpensa Airport",
                "city": "Milan",
                "country": "Italy",
            },
            "NAP": {
                "code": "NAP",
                "name": "Naples International Airport",
                "city": "Naples",
                "country": "Italy",
            },
        }
        normalized = str(query or "").strip().upper()
        if normalized in airports:
            return [airports[normalized]]
        country_lookup = {
            "SPAIN": ["MAD", "BCN", "VLC"],
            "ITALY": ["FCO", "MXP", "NAP"],
            "FLORIDA": ["MCO", "MIA", "TPA", "FLL"],
        }
        return [airports[code] for code in country_lookup.get(normalized, [])]

    def test_structured_florida_request_explicit_origin_overrides_detected_location(self) -> None:
        response = _execute_tool_call(
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "flight_search",
                    "origin_text": "Birmingham",
                    "origin_code": "BHX",
                    "destination_text": "Florida",
                    "destination_type": "region",
                    "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                    "missing_fields": ["departure_date", "return_date", "destination_airport_choice"],
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i want to fly to florida from birmingham"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertNotEqual(active_trip["origin"]["airportCode"], "EMA")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Florida")
        self.assertEqual(active_trip["destinationType"], "region")
        candidate_codes = [
            candidate["airportCode"] for candidate in active_trip["destinationCandidates"]
        ]
        self.assertEqual(candidate_codes, ["MCO", "MIA", "TPA", "FLL"])
        self.assertIn("Birmingham Airport (BHX)", response.reply)
        self.assertIn("Orlando", response.reply)
        self.assertNotIn("Fly To Florida", response.reply)
        self.assertNotIn("0 mock flight", response.reply.lower())

    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_uses_structured_florida_slots_from_llama(self, mock_router) -> None:
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "intent": "flight_search",
                "origin_text": "Birmingham",
                "origin_code": "BHX",
                "destination_text": "Florida",
                "destination_type": "region",
                "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
            },
        )

        response = chat(
            ChatRequest(
                message="i want to fly to florida from birmingham",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Florida")
        self.assertIn("Florida has several main airports", response.reply)
        mock_router.assert_called_once()

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.suggest_airports")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_country_region_spain_asks_for_airport_choice_without_guessing(
        self,
        mock_router,
        mock_suggest,
        mock_flights,
    ) -> None:
        mock_suggest.side_effect = self._airport_suggestions
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "intent": "package_search",
                "origin_text": "Manchester",
                "origin_code": "MAN",
                "destination_text": "Spain",
                "destination_code": "LCG",
                "destination_region": "Spain",
                "destination_type": "country",
                "destination_candidates": ["MAD", "BCN", "BAR", "VLC"],
                "date_text": "in october",
                "month": "2026-10",
                "missing_fields": ["exact_dates", "destination_airport_choice"],
            },
        )

        response = chat(
            ChatRequest(
                message="i want go on a trip to spain from manchester in october",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "MAN")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Spain")
        self.assertIsNone(active_trip["selectedDestination"])
        self.assertEqual(
            [candidate["airportCode"] for candidate in active_trip["destinationCandidates"]],
            ["MAD", "BCN", "VLC"],
        )
        self.assertIn("Spain has several main airport choices", response.reply)
        self.assertIn("Madrid", response.reply)
        self.assertIn("Barcelona", response.reply)
        self.assertIn("Valencia", response.reply)
        self.assertNotIn("A Coru", response.reply)
        self.assertNotIn("Qionghai", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.suggest_airports")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_country_region_italy_uses_generic_candidate_clarification(
        self,
        mock_router,
        mock_suggest,
        mock_flights,
    ) -> None:
        mock_suggest.side_effect = self._airport_suggestions
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "intent": "package_search",
                "origin_text": "Birmingham",
                "origin_code": "BHX",
                "destination_text": "Italy",
                "destination_region": "Italy",
                "destination_type": "country",
                "destination_candidates": ["FCO", "MXP", "NAP"],
                "date_text": "in november",
                "month": "2026-11",
                "missing_fields": ["exact_dates", "destination_airport_choice"],
            },
        )

        response = chat(
            ChatRequest(
                message="trip to italy from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Italy")
        self.assertIsNone(active_trip["selectedDestination"])
        self.assertEqual(
            [candidate["airportCode"] for candidate in active_trip["destinationCandidates"]],
            ["FCO", "MXP", "NAP"],
        )
        self.assertIn("Italy has several main airport choices", response.reply)
        self.assertIn("Rome", response.reply)
        self.assertIn("Milan", response.reply)
        self.assertIn("Naples", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.suggest_airports")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_broad_destination_missing_type_still_preserves_region_context(
        self,
        mock_router,
        mock_suggest,
        mock_flights,
    ) -> None:
        mock_suggest.side_effect = self._airport_suggestions
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "intent": "package_search",
                "origin_text": "Manchester",
                "origin_code": "MAN",
                "destination_text": "Spain",
                "date_text": "in october",
                "month": "2026-10",
                "missing_fields": ["exact_dates", "destination_airport_choice"],
            },
        )

        response = chat(
            ChatRequest(
                message="i want go on a trip to spain from manchester in october",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "MAN")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Spain")
        self.assertIsNone(active_trip["selectedDestination"])
        self.assertIn("city or airport in Spain", response.reply)
        self.assertNotIn("A Coru", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.suggest_airports")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_broad_destination_invalid_candidate_code_is_rejected(
        self,
        mock_router,
        mock_suggest,
        mock_flights,
    ) -> None:
        mock_suggest.side_effect = self._airport_suggestions
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "intent": "package_search",
                "origin_text": "Birmingham",
                "origin_code": "BHX",
                "destination_text": "Atlantis",
                "destination_region": "Atlantis",
                "destination_type": "region",
                "destination_candidates": ["XXX"],
                "date_text": "in november",
                "month": "2026-11",
                "missing_fields": ["exact_dates", "destination_airport_choice"],
            },
        )

        response = chat(
            ChatRequest(
                message="trip to atlantis from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["destinationRegion"]["name"], "Atlantis")
        self.assertEqual(active_trip["destinationCandidates"], [])
        self.assertIsNone(active_trip["selectedDestination"])
        self.assertIn("city or airport", response.reply)
        self.assertNotIn("XXX", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.suggest_airports")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_broad_destination_without_valid_candidates_asks_for_city_or_airport(
        self,
        mock_router,
        mock_suggest,
        mock_flights,
    ) -> None:
        mock_suggest.side_effect = self._airport_suggestions
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "intent": "package_search",
                "origin_text": "Birmingham",
                "origin_code": "BHX",
                "destination_text": "Unknown Region",
                "destination_region": "Unknown Region",
                "destination_type": "region",
                "destination_candidates": [],
                "date_text": "in november",
                "month": "2026-11",
                "missing_fields": ["exact_dates", "destination_airport_choice"],
            },
        )

        response = chat(
            ChatRequest(
                message="trip to unknown region from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["destinationRegion"]["name"], "Unknown Region")
        self.assertEqual(active_trip["destinationCandidates"], [])
        self.assertIsNone(active_trip["selectedDestination"])
        self.assertIn("city or airport", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_florida_package_month_uses_structured_clarification(
        self,
        mock_router,
    ) -> None:
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["dates"] = {
            "departureDate": "2026-08-19",
            "returnDate": "2026-08-22",
            "source": "old_test_state",
        }
        save_active_trip(self.db, self.session.id, active_trip)

        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "intent": "package_search",
                "origin_text": "Birmingham",
                "origin_code": "BHX",
                "destination_text": "Florida",
                "destination_type": "region",
                "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                "date_text": "in november",
                "month": "2026-11",
                "missing_fields": ["exact_dates", "destination_airport_choice"],
            },
        )

        response = chat(
            ChatRequest(
                message="i want go on a trip to florida from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Florida")
        self.assertEqual(active_trip["month"], "2026-11")
        self.assertIsNone(active_trip["dates"]["departureDate"])
        self.assertIsNone(active_trip["dates"]["returnDate"])
        self.assertIn("Birmingham Airport (BHX)", response.reply)
        self.assertIn("Florida has several main airport choices", response.reply)
        self.assertIn("exact departure and return dates", response.reply)
        self.assertNotIn("19 Aug 2026", response.reply)
        self.assertNotIn("22 Aug 2026", response.reply)
        self.assertNotIn("complete package", response.reply.lower())
        self.assertNotIn("mock package", response.reply.lower())
        self.assertNotIn("Fly To Florida", response.reply)

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_unwraps_nested_prepare_trip_request_from_llama(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        mock_router.return_value = TravelToolCall(
            tool="search_flights",
            arguments={
                "prepare_trip_request": {
                    "intent": "package_search",
                    "origin_text": "Birmingham",
                    "origin_code": "BHX",
                    "destination_text": "Florida",
                    "destination_region": "Florida",
                    "destination_type": "region",
                    "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                    "departure_date": None,
                    "return_date": None,
                    "date_text": "in November",
                    "month": "2026-11",
                    "missing_fields": ["exact_dates", "destination_airport_choice"],
                    "use_active_trip_state": False,
                    "search_candidates": False,
                }
            },
        )

        response = chat(
            ChatRequest(
                message="i want go on a trip to florida from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Florida")
        self.assertEqual(active_trip["month"], "2026-11")
        self.assertEqual(active_trip["pendingTripRequest"]["intent"], "package_search")
        self.assertIsNone(active_trip["dates"]["departureDate"])
        self.assertIsNone(active_trip["dates"]["returnDate"])
        self.assertIn("exact departure and return dates", response.reply)
        self.assertNotIn("I found real economy flight options", response.reply)
        self.assertNotIn("BHX to MIA", response.reply)
        self.assertNotIn("01 Nov 2026", response.reply)
        self.assertNotIn("Return Not available", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_coerces_broad_search_request_wrapper_from_llama(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        mock_router.return_value = TravelToolCall(
            tool="search_flights",
            arguments={
                "search_request": {
                    "origin_text": "Birmingham",
                    "destination_text": "Florida",
                    "destination_region": "Florida",
                    "destination_type": "region",
                    "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                    "departure_date": None,
                    "return_date": None,
                    "date_text": "in November",
                    "month": "2026-11",
                }
            },
        )

        response = chat(
            ChatRequest(
                message="i want go on a trip to florida from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Florida")
        self.assertEqual(active_trip["pendingTripRequest"]["intent"], "package_search")
        self.assertIsNone(active_trip["dates"]["departureDate"])
        self.assertIsNone(active_trip["dates"]["returnDate"])
        self.assertIn("Florida package", response.reply)
        self.assertIn("exact departure and return dates", response.reply)
        self.assertNotIn("BHX to MIA", response.reply)
        self.assertNotIn("01 Nov 2026", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_to_florida_from_birmingham_does_not_use_fly_destination(
        self,
        mock_router,
    ) -> None:
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "intent": "flight_search",
                "origin_text": "Birmingham",
                "origin_code": "BHX",
                "destination_text": "Florida",
                "destination_type": "region",
                "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                "missing_fields": ["departure_date", "return_date", "destination_airport_choice"],
            },
        )

        for message in (
            "i want to fly to florida from birmingham",
            "i want to fly from birmingham to florida",
        ):
            response = chat(
                ChatRequest(message=message, sessionId=self.session.id),
                db=self.db,
                current_user=self.user,
            )
            active_trip = load_active_trip(self.db, self.session.id)
            self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
            self.assertEqual(active_trip["destinationRegion"]["name"], "Florida")
            self.assertIn("Florida has several main airports", response.reply)
            self.assertNotIn("Fly To Florida", response.reply)
            self.assertNotIn("suitable flights for Fly", response.reply)
            self.assertNotIn("0 mock", response.reply.lower())

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_orlando_day_range_inherits_november_and_package_intent(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        mock_router.side_effect = [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "origin_text": "Birmingham",
                    "origin_code": "BHX",
                    "destination_text": "Florida",
                    "destination_type": "region",
                    "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                    "date_text": "in november",
                    "month": "2026-11",
                    "missing_fields": ["exact_dates", "destination_airport_choice"],
                },
            ),
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "use_active_trip_state": True,
                    "destination_text": "Orlando",
                    "destination_code": "MCO",
                    "destination_type": "airport",
                    "departure_day": 15,
                    "return_day": 20,
                    "month": "2026-11",
                },
            ),
        ]

        chat(
            ChatRequest(
                message="i want go on a trip to florida from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        response = chat(
            ChatRequest(
                message="i can go to Orlando but from the 15th till the 20th",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "MCO")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-11-15")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-11-20")
        self.assertEqual(active_trip["requestedProducts"], ["packages"])
        self.assertIn("Birmingham Airport (BHX)", response.reply)
        self.assertIn("Orlando International Airport (MCO)", response.reply)
        self.assertIn("15 Nov 2026", response.reply)
        self.assertIn("20 Nov 2026", response.reply)
        self.assertIn("flight and hotel package", response.reply)
        self.assertNotIn("LHR to MCO", response.reply)
        self.assertNotIn("Return Not available", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service._search_hotels_with_fallback")
    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_orlando_day_and_budget_followups_preserve_package_trip(
        self,
        mock_router,
        mock_flights,
        mock_hotels,
    ) -> None:
        flight_requests: list[FlightSearchRequest] = []
        hotel_requests: list[HotelSearchRequest] = []

        def capture_flights(request: FlightSearchRequest) -> FlightSearchResponse:
            flight_requests.append(request)
            return self._mock_flight_search(request)

        def capture_hotels(request: HotelSearchRequest) -> HotelSearchResponse:
            hotel_requests.append(request)
            return self._mock_hotel_search(request)

        mock_flights.side_effect = capture_flights
        mock_hotels.side_effect = capture_hotels
        mock_router.side_effect = [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "origin_text": "Birmingham",
                    "origin_code": "BHX",
                    "destination_text": "Florida",
                    "destination_type": "region",
                    "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                    "date_text": "in november",
                    "month": "2026-11",
                    "missing_fields": ["exact_dates", "destination_airport_choice"],
                },
            ),
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "use_active_trip_state": True,
                    "destination_text": "Orlando",
                    "destination_code": "MCO",
                    "destination_type": "airport",
                    "departure_day": 15,
                    "nights": 4,
                },
            ),
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "use_active_trip_state": True,
                    "budget_amount": 1000,
                    "budget_currency": "GBP",
                },
            ),
        ]

        first = chat(
            ChatRequest(
                message="i want go on a trip to florida from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        second = chat(
            ChatRequest(
                message="i would like to go to Orlando on the 15th and stay there for 4 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        third = chat(
            ChatRequest(
                message="can u find me full package my budget is 1000 pounds",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "MCO")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-11-15")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-11-19")
        self.assertEqual(active_trip["tripDurationNights"], 4)
        self.assertEqual(active_trip["pendingTripRequest"]["intent"], "package_search")
        self.assertEqual(active_trip["latestVisibleResultType"], "trip_clarification")
        self.assertEqual(active_trip["pendingAction"]["type"], "build_package")
        self.assertEqual(active_trip["pendingAction"]["origin"], "BHX")
        self.assertEqual(active_trip["pendingAction"]["destination"], "MCO")
        self.assertEqual(active_trip["pendingAction"]["departureDate"], "2026-11-15")
        self.assertEqual(active_trip["pendingAction"]["returnDate"], "2026-11-19")
        self.assertEqual(active_trip["pendingAction"]["budget"], 1000)
        self.assertEqual(active_trip["budget"], 1000)
        self.assertEqual(active_trip["budgetCurrency"], "GBP")
        self.assertIn("exact departure", first.reply)
        self.assertNotIn("When do you want to depart?", second.reply)
        self.assertNotIn("15 Aug 2026", second.reply)
        self.assertNotIn("19 Aug 2026", second.reply)
        self.assertNotIn("Where would you like to go?", third.reply)
        self.assertNotIn("15 Aug 2026", third.reply)
        self.assertNotIn("19 Aug 2026", third.reply)
        self.assertFalse(flight_requests)
        self.assertFalse(hotel_requests)

    @patch("app.services.chat_service._search_hotels_with_fallback")
    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_live_shaped_orlando_followup_and_yes_builds_package(
        self,
        mock_router,
        mock_flights,
        mock_hotels,
    ) -> None:
        flight_requests: list[FlightSearchRequest] = []
        hotel_requests: list[HotelSearchRequest] = []

        def capture_flights(request: FlightSearchRequest) -> FlightSearchResponse:
            flight_requests.append(request)
            return self._mock_flight_search(request)

        def capture_hotels(request: HotelSearchRequest) -> HotelSearchResponse:
            hotel_requests.append(request)
            return self._mock_hotel_search(request)

        mock_flights.side_effect = capture_flights
        mock_hotels.side_effect = capture_hotels
        mock_router.side_effect = [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "origin_text": "Birmingham",
                    "origin_code": "BHX",
                    "destination_text": "Florida",
                    "destination_type": "region",
                    "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                    "date_text": "in november",
                    "month": "2026-11",
                    "missing_fields": ["exact_dates", "destination_airport_choice"],
                },
            ),
            TravelToolCall(
                tool="search_hotels",
                arguments={
                    "destination_city": "Orlando",
                    "check_in_date": "2026-08-15",
                    "check_out_date": "2026-08-19",
                    "nights": 4,
                },
            ),
            TravelToolCall(
                tool="confirm_booking",
                arguments={"confirmed": True, "use_active_trip_state": True},
            ),
        ]

        first = chat(
            ChatRequest(
                message="i want go on a trip to florida from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        after_first = load_active_trip(self.db, self.session.id)
        self.assertEqual(after_first["pendingTripRequest"]["intent"], "package_search")
        self.assertEqual(after_first["destinationRegion"]["name"], "Florida")
        self.assertEqual(after_first["month"], "2026-11")
        self.assertIn(
            "MCO",
            [candidate["airportCode"] for candidate in after_first["destinationCandidates"]],
        )

        second = chat(
            ChatRequest(
                message="i would like to go to Orlando on the 15th and stay there for 4 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        after_second = load_active_trip(self.db, self.session.id)
        self.assertEqual(after_second["selectedDestination"]["airportCode"], "MCO")
        self.assertEqual(after_second["dates"]["departureDate"], "2026-11-15")
        self.assertEqual(after_second["dates"]["returnDate"], "2026-11-19")
        self.assertEqual(after_second["tripDurationNights"], 4)
        self.assertEqual(after_second["pendingTripRequest"]["intent"], "package_search")
        self.assertEqual(after_second["pendingAction"]["type"], "build_package")
        self.assertIn("Orlando International Airport (MCO)", second.reply)
        self.assertNotIn("ISM", second.reply)
        self.assertNotIn("15 Aug 2026", second.reply)
        self.assertNotIn("19 Aug 2026", second.reply)

        third = chat(
            ChatRequest(message="yes please", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )
        after_third = load_active_trip(self.db, self.session.id)
        self.assertEqual(after_third["selectedDestination"]["airportCode"], "MCO")
        self.assertEqual(after_third["dates"]["departureDate"], "2026-11-15")
        self.assertEqual(after_third["dates"]["returnDate"], "2026-11-19")
        self.assertEqual(after_third["latestVisibleResultType"], "packages")
        self.assertIsNone(after_third.get("pendingAction"))
        self.assertTrue(flight_requests)
        self.assertTrue(hotel_requests)
        self.assertEqual(flight_requests[-1].itinerary.origin.code, "BHX")
        self.assertEqual(flight_requests[-1].itinerary.destination.code, "MCO")
        self.assertEqual(flight_requests[-1].itinerary.dates.departure.isoformat(), "2026-11-15")
        self.assertEqual(flight_requests[-1].itinerary.dates.return_date.isoformat(), "2026-11-19")
        self.assertEqual(hotel_requests[-1].destination.city, "Orlando")
        self.assertEqual(hotel_requests[-1].check_in_date.isoformat(), "2026-11-15")
        self.assertEqual(hotel_requests[-1].check_out_date.isoformat(), "2026-11-19")
        self.assertIn("I built package options", third.reply)
        self.assertNotIn("Florida has several main airport choices", third.reply)

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_day_only_followup_uses_pending_florida_month_not_current_month(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        mock_router.side_effect = [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "origin_text": "Birmingham",
                    "origin_code": "BHX",
                    "destination_text": "Florida",
                    "destination_type": "region",
                    "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                    "date_text": "in november",
                    "month": "2026-11",
                    "missing_fields": ["exact_dates", "destination_airport_choice"],
                },
            ),
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "use_active_trip_state": True,
                    "departure_day": 15,
                },
            ),
        ]

        chat(
            ChatRequest(
                message="i want go on a trip to florida from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        response = chat(
            ChatRequest(message="on the 15th", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-11-15")
        self.assertIsNone(active_trip["dates"]["returnDate"])
        self.assertEqual(active_trip["requestedProducts"], ["packages"])
        self.assertNotIn("15 Aug 2026", response.reply)
        self.assertIn("15 Nov 2026", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_new_florida_month_request_clears_polluted_august_state(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        save_active_trip(
            self.db,
            self.session.id,
            {
                "origin": {
                    "city": "Birmingham",
                    "airportCode": "BHX",
                    "airportName": "Birmingham Airport",
                    "country": "United Kingdom",
                },
                "selectedDestination": {
                    "city": "Orlando",
                    "airportCode": "MCO",
                    "airportName": "Orlando International Airport",
                    "country": "United States",
                },
                "dates": {
                    "departureDate": "2026-08-15",
                    "returnDate": "2026-08-19",
                    "source": "stale_test_state",
                },
                "tripDurationNights": 4,
                "month": "2026-08",
                "pendingTripRequest": {"intent": "package_search"},
                "requestedProducts": ["packages"],
                "latestPackageOptions": [{"optionNumber": 1, "destinationAirportCode": "MCO"}],
            },
        )
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "intent": "package_search",
                "origin_text": "Birmingham",
                "origin_code": "BHX",
                "destination_text": "Florida",
                "destination_type": "region",
                "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                "date_text": "in november",
                "month": "2026-11",
                "departure_date": None,
                "return_date": None,
            },
        )

        response = chat(
            ChatRequest(
                message="i want go on a trip to florida from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Florida")
        self.assertIsNone(active_trip["selectedDestination"])
        self.assertEqual(active_trip["month"], "2026-11")
        self.assertIsNone(active_trip["dates"]["departureDate"])
        self.assertIsNone(active_trip["dates"]["returnDate"])
        self.assertIsNone(active_trip["tripDurationNights"])
        self.assertFalse(active_trip.get("latestPackageOptions"))
        self.assertIn("November is a broad date range", response.reply)
        self.assertNotIn("15 Aug 2026", response.reply)
        self.assertNotIn("19 Aug 2026", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_coerces_live_top_level_florida_search_flights_shape(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        mock_router.return_value = TravelToolCall(
            tool="search_flights",
            arguments={
                "origin": "Birmingham",
                "destination": "Florida",
                "departure_date": "2026-11",
                "return_date": None,
                "passenger_count": 1,
            },
        )

        response = chat(
            ChatRequest(
                message="i want go on a trip to florida from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Florida")
        self.assertEqual(active_trip["month"], "2026-11")
        self.assertIsNone(active_trip["dates"]["departureDate"])
        self.assertIsNone(active_trip["dates"]["returnDate"])
        self.assertEqual(active_trip["pendingTripRequest"]["intent"], "package_search")
        self.assertIn("November is a broad date range", response.reply)
        self.assertNotIn("MIA", response.reply.split("from")[0])
        mock_flights.assert_not_called()

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_coerces_live_search_hotels_orlando_followup_to_trip_state(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        save_active_trip(
            self.db,
            self.session.id,
            {
                "origin": {
                    "city": "Birmingham",
                    "airportCode": "BHX",
                    "airportName": "Birmingham Airport",
                    "country": "United Kingdom",
                },
                "destinationRegion": {
                    "name": "Florida",
                    "type": "region",
                    "candidateCodes": ["MCO", "MIA", "TPA", "FLL"],
                },
                "destinationCandidates": [
                    {
                        "city": "Orlando",
                        "airportCode": "MCO",
                        "airportName": "Orlando International Airport",
                        "country": "United States",
                    },
                    {"city": "Miami", "airportCode": "MIA", "country": "United States"},
                ],
                "dates": {"departureDate": None, "returnDate": None},
                "month": "2026-11",
                "pendingTripRequest": {"intent": "package_search"},
                "requestedProducts": ["packages"],
            },
        )
        mock_router.return_value = TravelToolCall(
            tool="search_hotels",
            arguments={
                "destination_city": "Orlando",
                "check_in_date": "2026-08-15",
                "check_out_date": "2026-08-19",
            },
        )

        response = chat(
            ChatRequest(
                message="i would like to go to Orlando on the 15th and stay there for 4 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "MCO")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-11-15")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-11-19")
        self.assertEqual(active_trip["tripDurationNights"], 4)
        self.assertEqual(active_trip["pendingTripRequest"]["intent"], "package_search")
        self.assertIn("Orlando International Airport (MCO)", response.reply)
        self.assertNotIn("Kissimmee", response.reply)
        self.assertNotIn("15 Aug 2026", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_day_only_uses_active_month_and_existing_nights(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        save_active_trip(
            self.db,
            self.session.id,
            {
                "origin": {
                    "city": "Birmingham",
                    "airportCode": "BHX",
                    "airportName": "Birmingham Airport",
                    "country": "United Kingdom",
                },
                "selectedDestination": {
                    "city": "Orlando",
                    "airportCode": "MCO",
                    "airportName": "Orlando International Airport",
                    "country": "United States",
                },
                "dates": {"departureDate": None, "returnDate": None},
                "tripDurationNights": 4,
                "month": "2026-11",
                "pendingTripRequest": {"intent": "package_search"},
                "requestedProducts": ["packages"],
            },
        )
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "intent": "package_search",
                "use_active_trip_state": True,
                "departure_day": 15,
            },
        )

        response = chat(
            ChatRequest(message="15th", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-11-15")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-11-19")
        self.assertEqual(active_trip["pendingTripRequest"]["intent"], "package_search")
        self.assertNotIn("15 Aug 2026", response.reply)
        self.assertIn("15 Nov 2026", response.reply)
        self.assertIn("19 Nov 2026", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_invalid_structured_flight_request_does_not_use_stale_search(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        save_active_trip(
            self.db,
            self.session.id,
            {
                "origin": {
                    "city": "Birmingham",
                    "airportCode": "BHX",
                    "airportName": "Birmingham Airport",
                    "country": "United Kingdom",
                },
                "selectedDestination": {
                    "city": "Orlando",
                    "airportCode": "MCO",
                    "airportName": "Orlando International Airport",
                    "country": "United States",
                },
                "dates": {"departureDate": None, "returnDate": None},
                "tripDurationNights": 4,
                "month": "2026-11",
                "pendingTripRequest": {"intent": "package_search"},
                "requestedProducts": ["packages"],
            },
        )
        save_latest_flight_search(
            self.db,
            self.session.id,
            _wrong_stale_route(),
            [{"origin": "ERZ", "destination": "LHR", "price": "100"}],
        )
        mock_router.return_value = TravelToolCall(
            tool="search_flights",
            arguments={
                "search_request": {
                    "origin": {
                        "airportCode": "BHX",
                        "airportName": "Birmingham Airport",
                        "city": "Birmingham",
                    },
                    "destination": {
                        "airportCode": "MCO",
                        "city": "Orlando",
                        "airportName": "Orlando International Airport",
                    },
                    "departureDate": "2026-08-15",
                    "returnDate": None,
                    "maxStops": 0,
                }
            },
        )

        response = chat(
            ChatRequest(
                message="i would like to go to Orlando on the 15th and stay there for 4 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        self.assertNotIn("15 Aug 2026", response.reply)
        self.assertNotIn("ERZ to LHR", response.reply)
        self.assertNotIn("BHX to MCO", response.reply)
        mock_flights.assert_not_called()

    @patch("app.services.chat_service._search_hotels_with_fallback")
    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_coerces_live_budget_search_flights_shape_to_package_build(
        self,
        mock_router,
        mock_flights,
        mock_hotels,
    ) -> None:
        flight_requests: list[FlightSearchRequest] = []
        hotel_requests: list[HotelSearchRequest] = []

        def capture_flights(request: FlightSearchRequest) -> FlightSearchResponse:
            flight_requests.append(request)
            return self._mock_flight_search(request)

        def capture_hotels(request: HotelSearchRequest) -> HotelSearchResponse:
            hotel_requests.append(request)
            return self._mock_hotel_search(request)

        mock_flights.side_effect = capture_flights
        mock_hotels.side_effect = capture_hotels
        save_active_trip(
            self.db,
            self.session.id,
            {
                "origin": {
                    "city": "Birmingham",
                    "airportCode": "BHX",
                    "airportName": "Birmingham Airport",
                    "country": "United Kingdom",
                },
                "selectedDestination": {
                    "city": "Orlando",
                    "airportCode": "MCO",
                    "airportName": "Orlando International Airport",
                    "country": "United States",
                },
                "dates": {
                    "departureDate": "2026-11-15",
                    "returnDate": "2026-11-19",
                },
                "tripDurationNights": 4,
                "month": "2026-11",
                "pendingTripRequest": {"intent": "package_search"},
                "requestedProducts": ["packages"],
            },
        )
        mock_router.return_value = TravelToolCall(
            tool="search_flights",
            arguments={"budget_min": 1000, "currency": "GBP"},
        )

        response = chat(
            ChatRequest(
                message="can u find me full package my budget is 1000 pounds",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["budget"], 1000)
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "MCO")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-11-15")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-11-19")
        self.assertEqual(active_trip["latestVisibleResultType"], "trip_clarification")
        self.assertEqual(active_trip["pendingAction"]["type"], "build_package")
        self.assertEqual(active_trip["pendingAction"]["budget"], 1000)
        self.assertNotIn("BHX to MCO", response.reply)
        self.assertNotIn("Where would you like to go?", response.reply)
        self.assertFalse(flight_requests)
        self.assertFalse(hotel_requests)

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_bhx_no_results_suggests_alternatives_without_lhr_search(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "intent": "flight_search",
                "origin_text": "Birmingham",
                "origin_code": "BHX",
                "destination_text": "Florida",
                "destination_type": "region",
                "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                "departure_date": "2026-11-15",
                "return_date": "2026-11-20",
                "search_candidates": True,
            },
        )
        mock_flights.side_effect = self._empty_flight_search

        response = chat(
            ChatRequest(
                message="search the florida airports from birmingham",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["pendingAlternativeOriginSearch"]["original_origin"], "BHX")
        self.assertIn("alternative UK departure airports", response.reply)
        for call in mock_flights.call_args_list:
            self.assertEqual(call.args[0].itinerary.origin.code, "BHX")
            self.assertNotEqual(call.args[0].itinerary.origin.code, "LHR")

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_chat_endpoint_alternative_origins_only_after_structured_confirmation(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip.update(
            {
                "origin": {
                    "city": "Birmingham",
                    "country": "United Kingdom",
                    "airportCode": "BHX",
                    "airportName": "Birmingham Airport",
                    "source": "explicit_origin",
                },
                "destinationRegion": {"name": "Florida", "type": "region"},
                "destinationCandidates": [
                    {"airportCode": "MCO", "city": "Orlando", "airportName": "Orlando International Airport"},
                ],
                "dates": {"departureDate": "2026-11-15", "returnDate": "2026-11-20"},
                "pendingAlternativeOriginSearch": {
                    "pending_action": "search_alternative_origins",
                    "original_origin": "BHX",
                    "alternative_origin_candidates": ["LHR", "LGW", "MAN"],
                    "destination_region": "Florida",
                    "destination_candidates": ["MCO"],
                    "dates": {"departureDate": "2026-11-15", "returnDate": "2026-11-20"},
                },
            }
        )
        save_active_trip(self.db, self.session.id, active_trip)
        mock_router.return_value = TravelToolCall(
            tool="search_alternative_origins",
            arguments={"confirmed": True, "use_active_trip_state": True},
        )
        mock_flights.side_effect = self._mock_flight_search

        response = chat(
            ChatRequest(message="yes check those", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        self.assertIn("alternative UK departure airports", response.reply)
        searched_origins = {call.args[0].itinerary.origin.code for call in mock_flights.call_args_list}
        self.assertIn("LHR", searched_origins)
        self.assertNotIn("BHX", searched_origins)

    @patch("app.services.chat_service.route_tool_with_llama")
    def test_full_package_followup_cannot_add_stale_london_package_to_cart(
        self,
        mock_router,
    ) -> None:
        stale_package = {
            "packageId": "PKG-LHR-STALE",
            "optionNumber": 1,
            "destinationCity": "London",
            "destinationAirportCode": "LHR",
            "flight": {
                "offerId": "old-flight",
                "airline": "Old Airways",
                "origin": "EMA",
                "destination": "LHR",
                "departureDate": "2026-08-13",
                "returnDate": "2026-08-16",
                "departureTime": "10:00",
                "returnTime": "12:00",
                "cabin": "economy",
                "stops": 0,
                "duration": "1h",
                "price": 100,
                "currency": "GBP",
                "provider": "old",
            },
            "hotel": {
                "hotelId": "strand",
                "hotelName": "Strand Palace",
                "city": "London",
                "checkInDate": "2026-08-13",
                "checkOutDate": "2026-08-16",
                "nights": 3,
                "rating": 4,
                "address": None,
                "boardType": None,
                "roomName": "Double room",
                "price": 300,
                "currency": "GBP",
                "provider": "old",
            },
            "totalPrice": 400,
            "currency": "GBP",
            "nights": 3,
            "passengers": 1,
            "rooms": 1,
            "recommendationScore": 80,
            "reasons": [],
            "warnings": [],
            "providerSummary": "old",
            "activeTripRevision": 1,
        }
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip.update(
            {
                "origin": {"airportCode": "EMA", "city": "East Midlands", "airportName": "East Midlands Airport"},
                "selectedDestination": {"airportCode": "LHR", "city": "London", "airportName": "London Heathrow Airport"},
                "dates": {"departureDate": "2026-08-13", "returnDate": "2026-08-16"},
                "latestPackageOptions": [stale_package],
                "selectedPackageOption": stale_package,
                "cart": {"cartId": "old", "items": []},
                "latestVisibleResultType": "packages",
            }
        )
        save_active_trip(self.db, self.session.id, active_trip)
        mock_router.side_effect = [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "origin_text": "Birmingham",
                    "origin_code": "BHX",
                    "destination_text": "Florida",
                    "destination_type": "region",
                    "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                    "month": "2026-11",
                    "date_text": "in November",
                    "missing_fields": ["exact_dates", "destination_airport_choice"],
                },
            ),
            TravelToolCall(
                tool="add_to_cart",
                arguments={"item_type": "package", "option_number": 1},
            ),
        ]

        chat(
            ChatRequest(
                message="i want go on a trip to florida from birmingham in november",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        response = chat(
            ChatRequest(message="i said i need a full package", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Florida")
        self.assertFalse(active_trip.get("latestPackageOptions"))
        self.assertIsNone(active_trip.get("cart"))
        self.assertIn("build a package first", response.reply)
        for forbidden in ["London", "EMA to LHR", "Strand Palace", "2026-08-13", "2026-08-16"]:
            self.assertNotIn(forbidden, response.reply)

    def test_structured_florida_date_followup_merges_with_incomplete_route(self) -> None:
        _execute_tool_call(
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "flight_search",
                    "destination_text": "Florida",
                    "destination_type": "region",
                    "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i want to fly to florida"),
        )

        response = _execute_tool_call(
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "flight_search",
                    "use_active_trip_state": True,
                    "departure_date": "2026-08-19",
                    "return_date": "2026-08-22",
                    "nights": 3,
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="same day next week for 3 nights"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "EMA")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Florida")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-08-19")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-08-22")
        self.assertEqual(active_trip["tripDurationNights"], 3)
        self.assertNotIn("Where are you flying from and to?", response.reply)
        self.assertIn("Would you like me to search", response.reply)

    def test_structured_package_month_request_asks_for_exact_dates_not_mock_failure(self) -> None:
        response = _execute_tool_call(
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "origin_text": "Birmingham",
                    "origin_code": "BHX",
                    "destination_text": "Florida",
                    "destination_type": "region",
                    "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                    "date_text": "in november",
                    "month": "2026-11",
                    "missing_fields": ["exact_dates", "destination_airport_choice"],
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i want go on a trip to florida from birmingham in november"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["destinationRegion"]["name"], "Florida")
        self.assertEqual(active_trip["month"], "2026-11")
        self.assertIn("exact departure and return dates", response.reply)
        self.assertNotIn("complete mock package", response.reply.lower())

    @patch("app.main._search_flights_with_fallback")
    def test_no_results_for_florida_candidates_suggests_alternative_origins(
        self,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._empty_flight_search

        response = _execute_tool_call(
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "flight_search",
                    "origin_text": "Birmingham",
                    "origin_code": "BHX",
                    "destination_text": "Florida",
                    "destination_type": "region",
                    "destination_candidates": ["MCO", "MIA", "TPA", "FLL"],
                    "departure_date": "2026-08-19",
                    "return_date": "2026-08-22",
                    "search_candidates": True,
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="search those florida airports"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        pending = active_trip["pendingAlternativeOriginSearch"]
        self.assertEqual(pending["original_origin"], "BHX")
        self.assertEqual(pending["destination_candidates"], ["MCO", "MIA", "TPA", "FLL"])
        self.assertEqual(pending["dates"]["departureDate"], "2026-08-19")
        self.assertIn("alternative UK departure airports", response.reply)
        self.assertNotIn("0 mock flight", response.reply.lower())
        for call in mock_flights.call_args_list:
            self.assertEqual(call.args[0].itinerary.origin.code, "BHX")

    @patch("app.main._search_flights_with_fallback")
    def test_structured_yes_to_alternatives_uses_saved_active_trip_state(
        self,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip.update(
            {
                "origin": {
                    "city": "Birmingham",
                    "country": "United Kingdom",
                    "airportCode": "BHX",
                    "airportName": "Birmingham Airport",
                    "source": "explicit_origin",
                },
                "destinationRegion": {"name": "Florida", "type": "region"},
                "destinationCandidates": [
                    {"airportCode": "MCO", "city": "Orlando", "airportName": "Orlando International Airport"},
                    {"airportCode": "MIA", "city": "Miami", "airportName": "Miami International Airport"},
                ],
                "dates": {"departureDate": "2026-08-19", "returnDate": "2026-08-22"},
                "pendingAlternativeOriginSearch": {
                    "pending_action": "search_alternative_origins",
                    "original_origin": "BHX",
                    "alternative_origin_candidates": ["LHR", "LGW", "MAN"],
                    "destination_region": "Florida",
                    "destination_candidates": ["MCO", "MIA"],
                    "dates": {"departureDate": "2026-08-19", "returnDate": "2026-08-22"},
                },
            }
        )
        save_active_trip(self.db, self.session.id, active_trip)

        response = _execute_tool_call(
            TravelToolCall(
                tool="search_alternative_origins",
                arguments={"confirmed": True, "use_active_trip_state": True},
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="yes"),
        )

        self.assertIn("alternative UK departure airports", response.reply)
        used_origins = {call.args[0].itinerary.origin.code for call in mock_flights.call_args_list}
        self.assertEqual(used_origins, {"LHR", "LGW", "MAN"})
        used_destinations = {
            call.args[0].itinerary.destination.code for call in mock_flights.call_args_list
        }
        self.assertEqual(used_destinations, {"MCO", "MIA"})

    @patch("app.main._search_flights_with_fallback")
    def test_structured_try_london_alternative_resolves_london_airport(
        self,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["pendingAlternativeOriginSearch"] = {
            "pending_action": "search_alternative_origins",
            "original_origin": "BHX",
            "alternative_origin_candidates": ["LHR", "LGW", "MAN"],
            "destination_region": "Florida",
            "destination_candidates": ["MCO"],
            "dates": {"departureDate": "2026-08-19", "returnDate": "2026-08-22"},
        }
        save_active_trip(self.db, self.session.id, active_trip)

        _execute_tool_call(
            TravelToolCall(
                tool="search_alternative_origins",
                arguments={"origin_text": "London", "use_active_trip_state": True},
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="try London"),
        )

        self.assertEqual(mock_flights.call_args.args[0].itinerary.origin.code, "LHR")
        self.assertEqual(mock_flights.call_args.args[0].itinerary.destination.code, "MCO")

    def _seed_hotel_results(self, prices: list[float]) -> None:
        request = HotelSearchRequest.model_validate(
            {
                "searchType": "hotel",
                "destination": {"city": "Lyon"},
                "checkInDate": "2026-09-01",
                "checkOutDate": "2026-09-04",
                "guestCount": 1,
                "roomCount": 1,
                "preferences": {"currency": "GBP"},
            }
        )
        response = HotelSearchResponse(
            provider_used="liteapi",
            results=[
                NormalisedHotelResult(
                    id=f"hotel_{index}",
                    option_number=index,
                    hotel_name=f"Lyon Hotel {index}",
                    location="Lyon",
                    check_in_date="2026-09-01",
                    check_out_date="2026-09-04",
                    nights=3,
                    guest_count=1,
                    room_count=1,
                    rating=4.0,
                    room_name="Standard room",
                    refundable=True,
                    price=price,
                    currency="GBP",
                    provider="liteapi",
                    short_id=f"Hotel {index}",
                )
                for index, price in enumerate(prices, start=1)
            ],
        )
        _store_latest_hotel_results(
            self.session.id,
            {
                "intent_type": "hotel_search",
                "destination": "Lyon",
                "booking_reference": None,
                "departure_city": None,
                "travelers": 1,
                "budget_level": "medium",
                "message_summary": "Seeded hotel results",
            },
            response,
            request,
            self.db,
        )
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["latestVisibleResultType"] = "hotels"
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

    def _seed_package_results(self) -> None:
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["latestVisibleResultType"] = "packages"
        active_trip["latestPackageOptions"] = [
            {
                "packageId": "PKG-1",
                "optionNumber": 1,
                "destinationCity": "Lyon",
                "destinationAirportCode": "LYS",
                "totalPrice": 500,
                "currency": "GBP",
                "nights": 3,
                "passengers": 1,
                "rooms": 1,
                "flight": {
                    "offerId": "flight-package-1",
                    "airline": "Package Air",
                    "origin": "EMA",
                    "destination": "LYS",
                    "departureDate": "2026-09-01",
                    "returnDate": "2026-09-04",
                    "departureTime": "08:30",
                    "returnTime": "10:45",
                    "cabin": "economy",
                    "stops": 0,
                    "duration": "1h 45m",
                    "price": 200,
                    "currency": "GBP",
                    "provider": "duffel",
                },
                "hotel": {
                    "hotelId": "hotel-package-1",
                    "hotelName": "Lyon Hotel",
                    "city": "Lyon",
                    "checkInDate": "2026-09-01",
                    "checkOutDate": "2026-09-04",
                    "nights": 3,
                    "rating": 4.0,
                    "address": "Lyon",
                    "boardType": "Room only",
                    "roomName": "Standard room",
                    "price": 300,
                    "currency": "GBP",
                    "provider": "liteapi",
                },
                "reasons": [],
                "warnings": [],
                "providerSummary": "Flight and hotel",
            },
            {
                "packageId": "PKG-2",
                "optionNumber": 2,
                "destinationCity": "Lyon",
                "destinationAirportCode": "LYS",
                "totalPrice": 420,
                "currency": "GBP",
                "nights": 3,
                "passengers": 1,
                "rooms": 1,
                "flight": {
                    "offerId": "flight-package-2",
                    "airline": "Package Air",
                    "origin": "EMA",
                    "destination": "LYS",
                    "departureDate": "2026-09-01",
                    "returnDate": "2026-09-04",
                    "departureTime": "09:30",
                    "returnTime": "11:45",
                    "cabin": "economy",
                    "stops": 0,
                    "duration": "1h 45m",
                    "price": 180,
                    "currency": "GBP",
                    "provider": "duffel",
                },
                "hotel": {
                    "hotelId": "hotel-package-2",
                    "hotelName": "Budget Lyon Hotel",
                    "city": "Lyon",
                    "checkInDate": "2026-09-01",
                    "checkOutDate": "2026-09-04",
                    "nights": 3,
                    "rating": 3.8,
                    "address": "Lyon",
                    "boardType": "Room only",
                    "roomName": "Standard room",
                    "price": 240,
                    "currency": "GBP",
                    "provider": "liteapi",
                },
                "reasons": [],
                "warnings": [],
                "providerSummary": "Flight and hotel",
            },
        ]
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

    def _seed_selected_hurghada_trip(self) -> None:
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip.update(
            {
                "revision": 8,
                "origin": {
                    "city": "Birmingham",
                    "country": "United Kingdom",
                    "airportCode": "BHX",
                    "airportName": "Birmingham Airport",
                    "source": "explicit_origin",
                },
                "selectedDestination": {
                    "city": "Hurghada",
                    "country": "Egypt",
                    "airportCode": "HRG",
                    "airportName": "Hurghada International Airport",
                    "source": "selected_recommendation",
                    "recommendationId": "HRG:1",
                    "confirmed": True,
                },
                "destinationRegion": None,
                "destinationCandidates": [],
                "dates": {
                    "departureDate": None,
                    "returnDate": None,
                    "source": "destination_selected",
                },
                "requestedProducts": ["flights"],
                "pendingTripRequest": {
                    "intent": "flight_search",
                    "origin": {
                        "city": "Birmingham",
                        "country": "United Kingdom",
                        "airportCode": "BHX",
                        "airportName": "Birmingham Airport",
                        "source": "explicit_origin",
                    },
                    "selectedDestination": {
                        "city": "Hurghada",
                        "country": "Egypt",
                        "airportCode": "HRG",
                        "airportName": "Hurghada International Airport",
                        "source": "selected_recommendation",
                        "recommendationId": "HRG:1",
                        "confirmed": True,
                    },
                    "dates": {
                        "departureDate": None,
                        "returnDate": None,
                        "source": "destination_selected",
                    },
                    "missingFields": ["departure_date", "return_date"],
                },
                "latestVisibleResultType": "trip_clarification",
                "status": "destination_selected",
            }
        )
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

    @staticmethod
    def _mock_destination_comparison_flight_search(
        request: FlightSearchRequest,
    ) -> FlightSearchResponse:
        destination = request.itinerary.destination.code.upper()
        prices = {
            "ERZ": "310",
            "GVA": "120",
            "LYS": "180",
            "RAK": "220",
            "BEY": "260",
        }
        return FlightSearchResponse(
            provider_used="duffel",
            flights=[
                NormalisedFlightResult(
                    id=f"offer_{request.itinerary.origin.code}_{destination}",
                    offer_id=f"mock_offer_{destination}",
                    display_id=f"Offer {destination}",
                    option_number=1,
                    origin=request.itinerary.origin.code,
                    destination=destination,
                    airline="Comparison Airways",
                    outbound_departure_date=request.itinerary.dates.departure.isoformat(),
                    return_departure_date=(
                        request.itinerary.dates.return_date.isoformat()
                        if request.itinerary.dates.return_date
                        else None
                    ),
                    stops=0,
                    stops_label="Non-stop",
                    cabin_class=request.preferences.cabin_class,
                    cabin_class_label=request.preferences.cabin_class.title(),
                    passenger_count=request.passenger_count,
                    price=prices.get(destination, "999"),
                    currency="GBP",
                    provider="duffel",
                )
            ],
            fallback_reason="Test provider.",
        )

    @staticmethod
    def _mock_cheapest_day_flight_search(
        request: FlightSearchRequest,
    ) -> FlightSearchResponse:
        departure = request.itinerary.dates.departure.isoformat()
        prices = {
            "2026-08-10": "300",
            "2026-08-11": "280",
            "2026-08-12": "270",
            "2026-08-13": "230",
            "2026-08-14": "250",
            "2026-08-15": "260",
            "2026-08-16": "290",
        }
        if departure.startswith("2026-09-"):
            day = int(departure[-2:])
            prices[departure] = str(180 - day if day <= 13 else 190 + day)
        if departure.startswith("2027-09-"):
            day = int(departure[-2:])
            prices[departure] = str(220 - day if day <= 9 else 250 + day)
        destination = request.itinerary.destination.code.upper()
        return FlightSearchResponse(
            provider_used="duffel",
            flights=[
                NormalisedFlightResult(
                    id=f"offer_{destination}_{departure}",
                    offer_id=f"mock_offer_{destination}_{departure}",
                    display_id=f"Offer {destination} {departure}",
                    option_number=1,
                    origin=request.itinerary.origin.code,
                    destination=destination,
                    airline="Flexible Date Airways",
                    outbound_departure_date=departure,
                    return_departure_date=(
                        request.itinerary.dates.return_date.isoformat()
                        if request.itinerary.dates.return_date
                        else None
                    ),
                    stops=0,
                    stops_label="Non-stop",
                    duration_minutes=290,
                    duration_label="4h 50m",
                    cabin_class=request.preferences.cabin_class,
                    cabin_class_label=request.preferences.cabin_class.title(),
                    passenger_count=request.passenger_count,
                    price=prices.get(departure, "999"),
                    currency="GBP",
                    provider="duffel",
                )
            ],
            fallback_reason="Test provider.",
        )

    def _seed_kayseri_destination_recommendations(self) -> None:
        request_payload = {
            "origin": {
                "city": "East Midlands",
                "code": "EMA",
                "airportName": "East Midlands Airport",
            },
            "activityPreferences": ["skiing"],
            "requiredActivities": ["skiing"],
            "tripDurationNights": 3,
        }
        recommendations = [
            {
                "city": "Erzurum",
                "country": "Turkey",
                "airportCode": "ERZ",
                "airportName": "Erzurum Airport",
                "region": "Asia",
                "score": 56,
                "matchReasons": ["Trusted fallback skiing knowledge matches your request."],
                "warnings": [],
                "evidenceSummary": "Activity evidence: Trusted fallback.",
                "estimatedFlightMinutes": 310,
            },
            {
                "city": "Geneva",
                "country": "Switzerland",
                "airportCode": "GVA",
                "airportName": "Geneva Airport",
                "region": "Europe",
                "score": 52,
                "matchReasons": ["Trusted fallback skiing knowledge matches your request."],
                "warnings": [],
                "evidenceSummary": "Activity evidence: Trusted fallback.",
                "estimatedFlightMinutes": 103,
            },
            {
                "city": "Lyon",
                "country": "France",
                "airportCode": "LYS",
                "airportName": "Lyon-Saint Exupery Airport",
                "region": "Europe",
                "score": 52,
                "matchReasons": ["Trusted fallback skiing knowledge matches your request."],
                "warnings": [],
                "evidenceSummary": "Activity evidence: Trusted fallback.",
                "estimatedFlightMinutes": 120,
            },
            {
                "city": "Marrakesh",
                "country": "Morocco",
                "airportCode": "RAK",
                "airportName": "Marrakesh Menara Airport",
                "region": "Africa",
                "score": 52,
                "matchReasons": ["Trusted fallback skiing knowledge matches your request."],
                "warnings": [],
                "evidenceSummary": "Activity evidence: Trusted fallback.",
                "estimatedFlightMinutes": 224,
            },
            {
                "city": "Beirut",
                "country": "Lebanon",
                "airportCode": "BEY",
                "airportName": "Beirut-Rafic Hariri International Airport",
                "region": "Middle East",
                "score": 52,
                "matchReasons": ["Trusted fallback skiing knowledge matches your request."],
                "warnings": [],
                "evidenceSummary": "Activity evidence: Trusted fallback.",
                "estimatedFlightMinutes": 317,
            },
            {
                "city": "Kayseri",
                "country": "Turkey",
                "airportCode": "ASR",
                "airportName": "Kayseri Erkilet Airport",
                "region": "Asia",
                "score": 52,
                "matchReasons": ["Trusted fallback skiing knowledge matches your request."],
                "warnings": [],
                "evidenceSummary": "Activity evidence: Trusted fallback.",
                "estimatedFlightMinutes": 286,
            },
        ]
        save_latest_destination_discovery(
            self.db,
            self.session.id,
            request_payload,
            recommendations,
        )
        save_active_trip(
            self.db,
            self.session.id,
            {
                "revision": 3,
                "origin": {
                    "city": "East Midlands",
                    "country": "United Kingdom",
                    "airportCode": "EMA",
                    "airportName": "East Midlands Airport",
                    "source": "browser_location_duffel_places",
                },
                "selectedDestination": None,
                "dates": {
                    "departureDate": None,
                    "returnDate": None,
                    "source": "date_later",
                },
                "tripDurationNights": 3,
                "requiredActivities": ["skiing"],
                "activityPreferences": ["skiing"],
                "requestedProducts": [],
                "status": "discovering_without_dates",
            },
        )
        self.db.commit()

    def _start_skiing_discovery(self) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(
                message=(
                    "i want to fly to somewhere that i can do skiing and i want "
                    "to go for 3 nights"
                )
            ),
        )
        self.assertIn("When do you want to depart", response.reply)

    def _complete_outside_europe_skiing_discovery(self) -> None:
        _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i dont want it in europe and maybe same day next month"),
        )

    def _complete_outside_europe_skiing_discovery_next_week(self) -> None:
        _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i dont want it in europe and maybe same day next week"),
        )

    def test_cheaper_after_flight_results_routes_to_flights(self) -> None:
        self._seed_flight_results(["300", "250", "220"])

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i want something cheaper"),
        )

        self.assertNotIn("package options", response.reply.lower())
        self.assertIn("cheaper flight", response.reply.lower())
        self.assertEqual(
            [float(flight["price"]) for flight in response.results.flights],
            [220.0, 250.0],
        )

    def test_cheaper_after_single_flight_says_current_is_cheapest(self) -> None:
        self._seed_flight_results(["263.22"])

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="something cheaper"),
        )

        self.assertNotIn("package options", response.reply.lower())
        self.assertIn("cheapest flight option", response.reply.lower())
        self.assertEqual(len(response.results.flights), 1)

    def test_cheaper_after_hotel_results_routes_to_hotels(self) -> None:
        self._seed_hotel_results([350, 275])

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="show me something cheaper"),
        )

        self.assertNotIn("package options", response.reply.lower())
        self.assertIn("hotel", response.reply.lower())
        self.assertEqual(len(response.results.hotels), 1)
        self.assertEqual(response.results.hotels[0]["price"], 275.0)

    def test_cheaper_after_package_results_still_routes_to_packages(self) -> None:
        self._seed_package_results()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="something cheaper"),
        )

        self.assertIn("package", response.reply.lower())
        self.assertEqual(response.results.packages[0]["totalPrice"], 420)

    def test_cheapest_package_question_uses_visible_package_results(self) -> None:
        self._seed_package_results()

        response = _execute_tool_call(
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "package",
                    "intent": "package_search",
                    "missing_fields": ["exact_dates"],
                    "use_active_trip_state": True,
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="which is the cheapest package?"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["latestVisibleResultType"], "packages")
        self.assertEqual(response.results.packages[0]["optionNumber"], 2)
        self.assertEqual(response.results.packages[0]["totalPrice"], 420)
        self.assertIn("cheapest package", response.reply.lower())
        self.assertIn("package 2", response.reply.lower())
        self.assertIn("Budget Lyon Hotel", response.reply)
        self.assertNotIn("provide your exact departure", response.reply.lower())

    def test_visible_results_tool_selects_cheapest_package(self) -> None:
        self._seed_package_results()

        response = _execute_tool_call(
            TravelToolCall(
                tool="handle_visible_results",
                arguments={
                    "action": "select_cheapest",
                    "result_type": "package",
                    "use_visible_results": True,
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="which is the cheapest?"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["latestVisibleResultType"], "packages")
        self.assertEqual(active_trip["currentPackageOption"]["optionNumber"], 2)
        self.assertEqual(response.results.packages[0]["optionNumber"], 2)
        self.assertIn("cheapest option", response.reply.lower())
        self.assertNotIn("provide your exact departure", response.reply.lower())

    def test_visible_results_tool_adds_current_package_to_cart(self) -> None:
        self._seed_package_results()
        _execute_tool_call(
            TravelToolCall(
                tool="handle_visible_results",
                arguments={
                    "action": "select_cheapest",
                    "result_type": "package",
                    "use_visible_results": True,
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="which is the cheapest?"),
        )

        response = _execute_tool_call(
            TravelToolCall(
                tool="handle_visible_results",
                arguments={
                    "action": "add_selected_to_cart",
                    "result_type": "package",
                    "use_current_selection": True,
                    "use_visible_results": True,
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="okay book it"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["latestVisibleResultType"], "cart")
        self.assertEqual(active_trip["selectedPackageOption"]["optionNumber"], 2)
        self.assertEqual(active_trip["cart"]["items"][0]["packageOption"]["optionNumber"], 2)
        self.assertIn("added package option 2", response.reply.lower())

    def test_visible_results_tool_compares_and_shows_package_option(self) -> None:
        self._seed_package_results()

        compare_response = _execute_tool_call(
            TravelToolCall(
                tool="handle_visible_results",
                arguments={
                    "action": "compare_results",
                    "result_type": "package",
                    "use_visible_results": True,
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="compare them"),
        )
        self.assertEqual(
            [package["totalPrice"] for package in compare_response.results.packages],
            [420, 500],
        )

        show_response = _execute_tool_call(
            TravelToolCall(
                tool="handle_visible_results",
                arguments={
                    "action": "show_option",
                    "result_type": "package",
                    "option_number": 1,
                    "use_visible_results": True,
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="show me the first one"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["currentPackageOption"]["optionNumber"], 1)
        self.assertEqual(show_response.results.packages[0]["optionNumber"], 1)

    def test_visible_results_tool_requires_package_results_for_package_actions(self) -> None:
        response = _execute_tool_call(
            TravelToolCall(
                tool="handle_visible_results",
                arguments={
                    "action": "select_cheapest",
                    "result_type": "package",
                    "use_visible_results": True,
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="which is cheapest?"),
        )

        self.assertIn("package options", response.reply.lower())
        self.assertFalse(response.results.packages)

    def test_visible_results_tool_handles_flight_context(self) -> None:
        self._seed_flight_results(["300", "250"])

        response = _execute_tool_call(
            TravelToolCall(
                tool="handle_visible_results",
                arguments={
                    "action": "select_cheapest",
                    "result_type": "flight",
                    "use_visible_results": True,
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="which is cheapest?"),
        )

        self.assertIn("cheapest flight", response.reply.lower())
        self.assertEqual(response.results.flights[0]["price"], "250")

    def test_visible_results_tool_handles_hotel_context(self) -> None:
        self._seed_hotel_results([350, 275])

        response = _execute_tool_call(
            TravelToolCall(
                tool="handle_visible_results",
                arguments={
                    "action": "select_cheapest",
                    "result_type": "hotel",
                    "use_visible_results": True,
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="which is cheapest?"),
        )

        self.assertIn("cheapest hotel", response.reply.lower())
        self.assertEqual(response.results.hotels[0]["price"], 275.0)

    def test_cheapest_package_variants_use_visible_package_results(self) -> None:
        for message in [
            "which is the cheapest?",
            "which one is cheapest?",
            "cheapest?",
            "what is the cheapest?",
            "lowest?",
            "which is lowest?",
            "which one is the best price?",
            "best price?",
            "cheapest package",
            "show me the cheapest one",
            "something cheaper",
        ]:
            with self.subTest(message=message):
                self._seed_package_results()

                response = _execute_tool_call(
                    TravelToolCall(tool="prepare_trip_request", arguments={"requested_product": "package"}),
                    self.db,
                    self.user,
                    self.session,
                    ChatRequest(message=message),
                )

                self.assertEqual(response.results.packages[0]["optionNumber"], 2)
                self.assertEqual(response.results.packages[0]["totalPrice"], 420)
                self.assertNotIn("provide your exact departure", response.reply.lower())

    def test_compare_packages_uses_existing_results_without_replanning(self) -> None:
        self._seed_package_results()

        response = _execute_tool_call(
            TravelToolCall(tool="prepare_trip_request", arguments={"requested_product": "package"}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="compare the packages"),
        )

        self.assertEqual(
            [package["totalPrice"] for package in response.results.packages],
            [420, 500],
        )
        self.assertIn("sorted by total price", response.reply.lower())
        self.assertNotIn("provide your exact departure", response.reply.lower())

    def test_show_package_one_uses_existing_results(self) -> None:
        self._seed_package_results()

        response = _execute_tool_call(
            TravelToolCall(tool="prepare_trip_request", arguments={"requested_product": "package"}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="show package 1"),
        )

        self.assertEqual(len(response.results.packages), 1)
        self.assertEqual(response.results.packages[0]["optionNumber"], 1)
        self.assertIn("package 1", response.reply.lower())
        self.assertIn("Lyon Hotel", response.reply)
        self.assertNotIn("provide your exact departure", response.reply.lower())

    def test_add_package_one_to_cart_uses_existing_results(self) -> None:
        self._seed_package_results()

        response = _execute_tool_call(
            TravelToolCall(tool="prepare_trip_request", arguments={"requested_product": "package"}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="add package 1 to cart"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["latestVisibleResultType"], "cart")
        self.assertEqual(active_trip["selectedPackageOption"]["optionNumber"], 1)
        self.assertEqual(active_trip["cart"]["items"][0]["packageOption"]["optionNumber"], 1)
        self.assertIn("added package option 1", response.reply.lower())

    def test_cheaper_without_visible_result_asks_for_context(self) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="something cheaper"),
        )

        self.assertIn("Do you want cheaper flights, hotels, or packages?", response.reply)

    def test_continue_with_option_one_after_flight_results_selects_flight(self) -> None:
        self._seed_flight_results(["263.22", "310.00"])

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="continue with option one"),
        )

        memory = load_session_memory(self.db, self.session.id)
        self.assertEqual(memory["pending_flight_selection"]["option_number"], 1)
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["selectedFlightOption"]["optionNumber"], 1)
        self.assertEqual(active_trip["latestVisibleResultType"], "flight_selected")
        self.assertFalse(response.requires_confirmation)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)
        self.assertIn("flight option 1", response.reply.lower())
        self.assertIn("cart", response.reply.lower())

    def test_continue_with_option_one_after_package_results_adds_package_to_cart(self) -> None:
        self._seed_package_results()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="continue with option one"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["latestVisibleResultType"], "cart")
        self.assertEqual(active_trip["selectedPackageOption"]["optionNumber"], 1)
        self.assertEqual(active_trip["cart"]["items"][0]["packageOption"]["optionNumber"], 1)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)
        self.assertIn("added package option 1", response.reply.lower())

    def test_multi_city_request_creates_draft_without_general_help(self) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(
                message=(
                    "i want to go to new york and then spain and stay in every "
                    "country 3 nights"
                )
            ),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        draft = active_trip.get("multiCityDraft")
        self.assertIsInstance(draft, dict)
        self.assertIn("multi-city", response.reply)
        self.assertIn("one destination package at a time", response.reply)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)

    def test_multi_city_request_stores_destinations_and_nights(self) -> None:
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(
                message=(
                    "i want to go to new york and then spain and stay in every "
                    "country 3 nights"
                )
            ),
        )

        draft = load_active_trip(self.db, self.session.id)["multiCityDraft"]
        destination_names = [
            destination.get("city") or destination.get("country")
            for destination in draft["destinations"]
        ]
        self.assertEqual(destination_names[:2], ["New York", "Spain"])
        self.assertEqual(draft["nightsPerDestination"], 3)

    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_multi_city_date_followup_starts_first_destination(
        self,
        _mock_today,
    ) -> None:
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(
                message=(
                    "i want to go to new york and then spain and stay in every "
                    "country 3 nights"
                )
            ),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="today and after 8 days"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)
        self.assertIn("New York first for 3 nights", response.reply)
        self.assertEqual(active_trip["selectedDestination"]["city"], "New York")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "JFK")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-08-08")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-08-11")
        self.assertEqual(active_trip["multiCityDraft"]["status"], "first_destination_ready")

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_single_destination_request_does_not_create_multi_city_draft(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i want to go to Miami today and return after two days"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertIsNone(active_trip.get("multiCityDraft"))
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "MIA")
        self.assertTrue(response.results.flights)

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main.today_utc_date", return_value=TODAY)
    def test_pending_skiing_discovery_stores_active_origin_and_duration(
        self,
        _mock_today,
        _mock_evidence,
    ) -> None:
        self._start_skiing_discovery()

        active_trip = load_active_trip(self.db, self.session.id)

        self.assertEqual(active_trip["origin"]["airportCode"], "EMA")
        self.assertEqual(active_trip["tripDurationNights"], 3)
        self.assertIn("skiing", active_trip["requiredActivities"])
        self.assertEqual(active_trip["status"], "awaiting_date")

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main.today_utc_date", return_value=CURRENT_DATE)
    def test_new_top_level_discovery_strips_stale_date_and_europe(
        self,
        _mock_today,
        _mock_evidence,
    ) -> None:
        save_active_trip(
            self.db,
            self.session.id,
            {
                "revision": 5,
                "origin": {
                    "city": "East Midlands",
                    "airportCode": "EMA",
                    "airportName": "East Midlands Airport",
                    "source": "browser_location_duffel_places",
                },
                "selectedDestination": {
                    "city": "Beirut",
                    "country": "Lebanon",
                    "airportCode": "BEY",
                    "source": "selected_recommendation",
                },
                "dates": {
                    "departureDate": "2026-08-14",
                    "returnDate": "2026-08-17",
                    "source": "old_trip",
                },
                "requiredActivities": ["skiing"],
                "excludedRegions": ["Europe"],
                "requestedProducts": ["flights"],
                "status": "ready_for_search",
            },
        )
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(
                tool="discover_destinations",
                arguments={
                    "origin": {
                        "city": "East Midlands",
                        "code": "EMA",
                        "airportName": "East Midlands Airport",
                    },
                    "tripPurpose": "family_holiday",
                    "travellers": {"adults": 2, "children": 2},
                    "activityPreferences": ["skiing"],
                    "requiredActivities": ["skiing"],
                    "excludedRegions": ["Europe"],
                    "regionPreferences": ["Europe"],
                    "shortFlightPreference": True,
                    "dates": {"departure": "2026-08-14", "return": "2026-08-17"},
                    "tripDurationNights": 3,
                    "missingFields": [],
                    "softPreferences": ["region:europe", "short_flight"],
                    "hardFilters": ["region:europe"],
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(
                message=(
                    "i want to fly to somewhere that i can do skiing and i want "
                    "to go for 3 nights"
                )
            ),
        )

        memory = load_session_memory(self.db, self.session.id)
        active_trip = load_active_trip(self.db, self.session.id)
        pending = memory["pending_destination_discovery"]

        self.assertIn("When do you want to depart", response.reply)
        self.assertEqual(response.results.destinations, [])
        self.assertIsNone(active_trip["selectedDestination"])
        self.assertIsNone(active_trip["dates"]["departureDate"])
        self.assertIsNone(active_trip["dates"]["returnDate"])
        self.assertEqual(active_trip["excludedRegions"], [])
        self.assertEqual(active_trip["regionPreferences"], [])
        self.assertEqual(active_trip["requestedProducts"], [])
        self.assertEqual(pending["dates"], {})
        self.assertEqual(pending["excludedRegions"], [])
        self.assertIn("departureDate", pending["missingFields"])
        self.assertEqual(pending["travellers"]["children"], 0)

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_dateless_destination_discovery_shows_places_without_asking_date_again(
        self,
        _mock_today,
        _mock_evidence,
    ) -> None:
        self._start_skiing_discovery()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="lets choose the place first then we will chose the date"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        memory = load_session_memory(self.db, self.session.id)
        self.assertNotIn("When do you want to depart", response.reply)
        self.assertIn("Flight prices and availability will be checked once you choose a date", response.reply)
        self.assertTrue(response.results.destinations)
        self.assertIsNone(active_trip["dates"]["departureDate"])
        self.assertIsNone(active_trip["dates"]["returnDate"])
        self.assertEqual(active_trip["status"], "discovering_without_dates")
        self.assertIsNone(memory["pending_destination_discovery"])
        for destination in response.results.destinations:
            self.assertNotIn("Suitable for family travel.", destination["matchReasons"])

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_cheapest_day_without_selected_destination_asks_which_destination(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        self._seed_kayseri_destination_recommendations()

        response = _execute_tool_call(
            TravelToolCall(tool="rank_flights", arguments={"ranking": "cheapest"}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="cheapest day next week"),
        )

        mock_flights.assert_not_called()
        self.assertIn("First choose one of the destination ideas", response.reply)

    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_fly_to_london_with_duration_is_not_hotel_only(self, _mock_today) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I wanna fly to London for three days and two nights"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertIsNone(active_trip.get("pendingHotelRequest"))
        self.assertEqual(active_trip["selectedDestination"]["city"], "London")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "LHR")
        self.assertEqual(active_trip["tripDurationDays"], 3)
        self.assertEqual(active_trip["tripDurationNights"], 2)
        self.assertIn("When do you want to depart", response.reply)
        self.assertNotIn("search hotels", response.reply.lower())
        self.assertNotIn("check in", response.reply.lower())
        pending = active_trip["pendingFlightRequest"]
        self.assertEqual(pending["origin"]["airportCode"], "EMA")
        self.assertEqual(pending["destination"]["airportCode"], "LHR")
        self.assertEqual(pending["tripDurationDays"], 3)
        self.assertEqual(pending["tripDurationNights"], 2)
        self.assertIn("departureDate", pending["missingFields"])

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_pending_flight_depart_on_fifteenth_runs_search(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I wanna fly to London for three days and two nights"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I want to depart on the 15"),
        )

        flight_request = mock_flights.call_args.args[0]
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(flight_request.itinerary.origin.code, "EMA")
        self.assertEqual(flight_request.itinerary.destination.code, "LHR")
        self.assertEqual(flight_request.itinerary.dates.departure.isoformat(), "2026-08-15")
        self.assertEqual(flight_request.itinerary.dates.return_date.isoformat(), "2026-08-17")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-08-15")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-08-17")
        self.assertIsNone(active_trip["pendingFlightRequest"])
        self.assertTrue(response.results.flights)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_pending_flight_day_only_followup_runs_search(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I wanna fly to London for three days and two nights"),
        )

        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="on the 15"),
        )

        flight_request = mock_flights.call_args.args[0]
        self.assertEqual(flight_request.itinerary.origin.code, "EMA")
        self.assertEqual(flight_request.itinerary.destination.code, "LHR")
        self.assertEqual(flight_request.itinerary.dates.departure.isoformat(), "2026-08-15")
        self.assertEqual(flight_request.itinerary.dates.return_date.isoformat(), "2026-08-17")

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_pending_flight_today_followup_uses_current_date(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I wanna fly to London for three days and two nights"),
        )

        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="today"),
        )

        flight_request = mock_flights.call_args.args[0]
        self.assertEqual(flight_request.itinerary.origin.code, "EMA")
        self.assertEqual(flight_request.itinerary.destination.code, "LHR")
        self.assertEqual(flight_request.itinerary.dates.departure.isoformat(), "2026-08-09")
        self.assertEqual(flight_request.itinerary.dates.return_date.isoformat(), "2026-08-11")

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_cheapest_day_uses_active_london_destination_and_asks_for_range(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["selectedDestination"] = {
            "city": "London",
            "country": "United Kingdom",
            "airportCode": "LHR",
            "airportName": "London Heathrow Airport",
            "source": "explicit_destination",
        }
        active_trip["tripDurationDays"] = 3
        active_trip["tripDurationNights"] = 2
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="when is the cheapest day"),
        )

        mock_flights.assert_not_called()
        self.assertIn("Which month or week should I check", response.reply)
        self.assertIn("London", response.reply)
        self.assertNotIn("Please tell me which destination", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_cheapest_day_message_with_destination_does_not_ask_for_destination(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(
                message=(
                    "I want to go to London for three days and two nights and "
                    "search for the cheapest day maximum"
                )
            ),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        mock_flights.assert_not_called()
        self.assertEqual(active_trip["selectedDestination"]["city"], "London")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "LHR")
        self.assertEqual(active_trip["tripDurationDays"], 3)
        self.assertEqual(active_trip["tripDurationNights"], 2)
        self.assertIn("Which month or week should I check", response.reply)
        self.assertIn("London", response.reply)
        self.assertNotIn("Please tell me which destination", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_kayseri_cheapest_day_compares_next_week_dates(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_cheapest_day_flight_search
        self._seed_kayseri_destination_recommendations()

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="okay Kayseri looks nice when is the cheapest day to go for it next week ?"),
        )

        requests = [call.args[0] for call in mock_flights.call_args_list]
        departures = [item.itinerary.dates.departure.isoformat() for item in requests]
        returns = [item.itinerary.dates.return_date.isoformat() for item in requests]
        self.assertEqual(
            departures,
            [
                "2026-08-10",
                "2026-08-11",
                "2026-08-12",
                "2026-08-13",
                "2026-08-14",
                "2026-08-15",
                "2026-08-16",
            ],
        )
        self.assertEqual(
            returns,
            [
                "2026-08-13",
                "2026-08-14",
                "2026-08-15",
                "2026-08-16",
                "2026-08-17",
                "2026-08-18",
                "2026-08-19",
            ],
        )
        self.assertTrue(all(item.itinerary.origin.code == "EMA" for item in requests))
        self.assertTrue(all(item.itinerary.destination.code == "ASR" for item in requests))
        self.assertIn("The cheapest departure day is 13 Aug 2026", response.reply)
        self.assertEqual(response.results.flights[0]["outbound_departure_date"], "2026-08-13")

        active_trip = load_active_trip(self.db, self.session.id)
        comparison = active_trip["latestDatePriceComparison"]
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "ASR")
        self.assertEqual(comparison["destinationAirportCode"], "ASR")
        self.assertEqual(comparison["checkedDateRange"]["start"], "2026-08-10")
        self.assertEqual(comparison["checkedDateRange"]["end"], "2026-08-16")
        self.assertEqual(comparison["cheapestResult"]["departureDate"], "2026-08-13")
        self.assertEqual(comparison["rangeType"], "week")
        self.assertEqual(comparison["rangeLabel"], "next week")

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_lyon_cheapest_day_in_september_uses_month_range(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_cheapest_day_flight_search
        self._seed_kayseri_destination_recommendations()

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="lyon looks nice when is the cheapest day in September?"),
        )

        requests = [call.args[0] for call in mock_flights.call_args_list]
        departures = [item.itinerary.dates.departure.isoformat() for item in requests]
        self.assertEqual(len(requests), 30)
        self.assertEqual(departures[0], "2026-09-01")
        self.assertEqual(departures[-1], "2026-09-30")
        self.assertTrue(all(date_value.startswith("2026-09-") for date_value in departures))
        self.assertFalse(any(date_value.startswith("2026-08-") for date_value in departures))
        self.assertTrue(all(item.itinerary.origin.code == "EMA" for item in requests))
        self.assertTrue(all(item.itinerary.destination.code == "LYS" for item in requests))
        self.assertIn("September 2026", response.reply)
        self.assertNotIn("next week", response.reply)

        active_trip = load_active_trip(self.db, self.session.id)
        comparison = active_trip["latestDatePriceComparison"]
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "LYS")
        self.assertEqual(comparison["rangeType"], "month")
        self.assertEqual(comparison["rangeLabel"], "September 2026")
        self.assertEqual(comparison["startDate"], "2026-09-01")
        self.assertEqual(comparison["endDate"], "2026-09-30")
        self.assertEqual(comparison["checkedDateRange"]["start"], "2026-09-01")
        self.assertEqual(comparison["checkedDateRange"]["end"], "2026-09-30")
        self.assertEqual(comparison["cheapestResult"]["departureDate"], "2026-09-13")

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_september_followup_routes_to_active_trip_month_comparison(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_cheapest_day_flight_search
        self._seed_kayseri_destination_recommendations()
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["selectedDestination"] = {
            "city": "Lyon",
            "country": "France",
            "airportCode": "LYS",
            "airportName": "Lyon-Saint Exupery Airport",
            "source": "selected_recommendation",
        }
        active_trip["status"] = "date_price_compared"
        active_trip["latestDatePriceComparison"] = {
            "rangeType": "week",
            "rangeLabel": "next week",
            "startDate": "2026-08-10",
            "endDate": "2026-08-16",
            "originAirportCode": "EMA",
            "destinationAirportCode": "LYS",
            "tripDurationNights": 3,
            "results": [],
            "cheapestResult": None,
        }
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="is there anything in september ?"),
        )

        requests = [call.args[0] for call in mock_flights.call_args_list]
        departures = [item.itinerary.dates.departure.isoformat() for item in requests]
        self.assertEqual(len(requests), 30)
        self.assertTrue(all(date_value.startswith("2026-09-") for date_value in departures))
        self.assertIn("September 2026", response.reply)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)
        comparison = load_active_trip(self.db, self.session.id)["latestDatePriceComparison"]
        self.assertEqual(comparison["rangeType"], "month")
        self.assertEqual(comparison["startDate"], "2026-09-01")
        self.assertEqual(comparison["endDate"], "2026-09-30")

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_day_only_followup_uses_latest_comparison_month(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_cheapest_day_flight_search
        self._seed_kayseri_destination_recommendations()
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["selectedDestination"] = {
            "city": "Lyon",
            "country": "France",
            "airportCode": "LYS",
            "airportName": "Lyon-Saint Exupery Airport",
            "source": "selected_recommendation",
        }
        active_trip["dates"] = {
            "departureDate": "2026-09-13",
            "returnDate": "2026-09-16",
            "source": "date_price_comparison",
        }
        active_trip["latestDatePriceComparison"] = {
            "rangeType": "month",
            "rangeLabel": "September 2026",
            "startDate": "2026-09-01",
            "endDate": "2026-09-30",
            "originAirportCode": "EMA",
            "destinationAirportCode": "LYS",
            "tripDurationNights": 3,
            "results": [],
            "cheapestResult": None,
        }
        save_active_trip(self.db, self.session.id, active_trip)
        save_latest_flight_search(
            self.db,
            self.session.id,
            _wrong_stale_route(),
            FlightSearchResponse(provider_used="duffel", flights=[]),
        )
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={"search_request": _wrong_stale_route()}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="what about the 19th"),
        )

        request_used = mock_flights.call_args.args[0]
        self.assertEqual(mock_flights.call_count, 1)
        self.assertEqual(request_used.itinerary.origin.code, "EMA")
        self.assertEqual(request_used.itinerary.destination.code, "LYS")
        self.assertEqual(request_used.itinerary.dates.departure.isoformat(), "2026-09-19")
        self.assertEqual(request_used.itinerary.dates.return_date.isoformat(), "2026-09-22")
        self.assertIn("19 Sep 2026 to 22 Sep 2026", response.reply)
        self.assertNotIn("19 Aug 2026", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_explicit_month_overrides_latest_comparison_month(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_cheapest_day_flight_search
        self._seed_kayseri_destination_recommendations()
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["selectedDestination"] = {
            "city": "Lyon",
            "country": "France",
            "airportCode": "LYS",
            "airportName": "Lyon-Saint Exupery Airport",
            "source": "selected_recommendation",
        }
        active_trip["latestDatePriceComparison"] = {
            "rangeType": "month",
            "rangeLabel": "September 2026",
            "startDate": "2026-09-01",
            "endDate": "2026-09-30",
            "originAirportCode": "EMA",
            "destinationAirportCode": "LYS",
            "tripDurationNights": 3,
            "results": [],
            "cheapestResult": None,
        }
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="what about 19 October"),
        )

        request_used = mock_flights.call_args.args[0]
        self.assertEqual(request_used.itinerary.dates.departure.isoformat(), "2026-10-19")
        self.assertEqual(request_used.itinerary.dates.return_date.isoformat(), "2026-10-22")
        self.assertIn("19 Oct 2026 to 22 Oct 2026", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_day_only_followup_uses_october_comparison_month(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_cheapest_day_flight_search
        self._seed_kayseri_destination_recommendations()
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["selectedDestination"] = {
            "city": "Lyon",
            "country": "France",
            "airportCode": "LYS",
            "airportName": "Lyon-Saint Exupery Airport",
            "source": "selected_recommendation",
        }
        active_trip["latestDatePriceComparison"] = {
            "rangeType": "month",
            "rangeLabel": "October 2026",
            "startDate": "2026-10-01",
            "endDate": "2026-10-31",
            "originAirportCode": "EMA",
            "destinationAirportCode": "LYS",
            "tripDurationNights": 3,
            "results": [],
            "cheapestResult": None,
        }
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="what about 5th"),
        )

        request_used = mock_flights.call_args.args[0]
        self.assertEqual(request_used.itinerary.dates.departure.isoformat(), "2026-10-05")
        self.assertEqual(request_used.itinerary.dates.return_date.isoformat(), "2026-10-08")
        self.assertIn("5 Oct 2026 to 8 Oct 2026", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_day_only_without_comparison_uses_current_month(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_cheapest_day_flight_search
        self._seed_kayseri_destination_recommendations()
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["selectedDestination"] = {
            "city": "Lyon",
            "country": "France",
            "airportCode": "LYS",
            "airportName": "Lyon-Saint Exupery Airport",
            "source": "selected_recommendation",
        }
        active_trip["latestDatePriceComparison"] = None
        active_trip["dates"] = {
            "departureDate": None,
            "returnDate": None,
            "source": "date_later",
        }
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="what about the 19th"),
        )

        request_used = mock_flights.call_args.args[0]
        self.assertEqual(request_used.itinerary.origin.code, "EMA")
        self.assertEqual(request_used.itinerary.destination.code, "LYS")
        self.assertEqual(request_used.itinerary.dates.departure.isoformat(), "2026-08-19")
        self.assertEqual(request_used.itinerary.dates.return_date.isoformat(), "2026-08-22")
        self.assertIn("19 Aug 2026 to 22 Aug 2026", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_month_abbreviation_sept_uses_september_range(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_cheapest_day_flight_search
        self._seed_kayseri_destination_recommendations()

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="lyon cheapest day in Sept"),
        )

        departures = [
            call.args[0].itinerary.dates.departure.isoformat()
            for call in mock_flights.call_args_list
        ]
        self.assertEqual(departures[0], "2026-09-01")
        self.assertEqual(departures[-1], "2026-09-30")
        self.assertIn("September 2026", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=date(2026, 11, 10))
    def test_passed_month_rolls_to_next_year(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_cheapest_day_flight_search
        self._seed_kayseri_destination_recommendations()
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["selectedDestination"] = {
            "city": "Lyon",
            "country": "France",
            "airportCode": "LYS",
            "airportName": "Lyon-Saint Exupery Airport",
            "source": "selected_recommendation",
        }
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="cheapest day in September"),
        )

        departures = [
            call.args[0].itinerary.dates.departure.isoformat()
            for call in mock_flights.call_args_list
        ]
        self.assertEqual(departures[0], "2027-09-01")
        self.assertEqual(departures[-1], "2027-09-30")
        self.assertIn("September 2027", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.MAX_FLEXIBLE_DATE_CHECKS", 5)
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_month_range_max_guard_asks_to_narrow_range(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        self._seed_kayseri_destination_recommendations()
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["selectedDestination"] = {
            "city": "Lyon",
            "country": "France",
            "airportCode": "LYS",
            "airportName": "Lyon-Saint Exupery Airport",
            "source": "selected_recommendation",
        }
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="cheapest day in September"),
        )

        mock_flights.assert_not_called()
        self.assertIn("Please narrow it to a shorter date range", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_cheaper_day_followup_uses_stored_comparison_without_rerunning_provider(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_cheapest_day_flight_search
        self._seed_kayseri_destination_recommendations()
        _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="okay Kayseri looks nice when is the cheapest day to go for it next week ?"),
        )
        self.assertEqual(mock_flights.call_count, 7)

        response = _execute_tool_call(
            TravelToolCall(tool="rank_flights", arguments={"ranking": "cheapest"}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="is there a cheaper day?"),
        )

        self.assertEqual(mock_flights.call_count, 7)
        self.assertIn("I already compared the checked dates", response.reply)
        self.assertIn("13 Aug 2026", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_explicit_thirteenth_override_uses_active_destination_not_stale_route(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_cheapest_day_flight_search
        self._seed_kayseri_destination_recommendations()
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["selectedDestination"] = {
            "city": "Kayseri",
            "country": "Turkey",
            "airportCode": "ASR",
            "airportName": "Kayseri Erkilet Airport",
            "source": "selected_recommendation",
        }
        active_trip["dates"] = {
            "departureDate": "2026-08-14",
            "returnDate": "2026-08-17",
            "source": "old_search",
        }
        save_active_trip(self.db, self.session.id, active_trip)
        save_latest_flight_search(
            self.db,
            self.session.id,
            _wrong_stale_route(),
            FlightSearchResponse(provider_used="duffel", flights=[]),
        )
        save_last_tool(self.db, self.session.id, "search_flights")
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={"search_request": _wrong_stale_route()}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="what about 13th"),
        )

        request_used = mock_flights.call_args.args[0]
        self.assertEqual(mock_flights.call_count, 1)
        self.assertEqual(request_used.itinerary.origin.code, "EMA")
        self.assertEqual(request_used.itinerary.destination.code, "ASR")
        self.assertEqual(request_used.itinerary.dates.departure.isoformat(), "2026-08-13")
        self.assertEqual(request_used.itinerary.dates.return_date.isoformat(), "2026-08-16")
        self.assertIn("13 Aug 2026 to 16 Aug 2026", response.reply)
        memory = load_session_memory(self.db, self.session.id)
        self.assertEqual(memory["search_request"]["itinerary"]["origin"]["code"], "EMA")
        self.assertEqual(memory["search_request"]["itinerary"]["destination"]["code"], "ASR")

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main.today_utc_date", return_value=TODAY)
    def test_date_followup_preserves_skiing_and_excludes_europe(
        self,
        _mock_today,
        _mock_evidence,
    ) -> None:
        self._start_skiing_discovery()
        self._complete_outside_europe_skiing_discovery()

        active_trip = load_active_trip(self.db, self.session.id)

        self.assertEqual(active_trip["dates"]["departureDate"], "2026-09-04")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-09-07")
        self.assertIn("Europe", active_trip["excludedRegions"])
        self.assertIn("skiing", active_trip["requiredActivities"])

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=TODAY)
    def test_select_erzurum_searches_ema_to_erz_not_stale_reverse_route(
        self,
        _mock_today,
        mock_flights,
        _mock_evidence,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        self._start_skiing_discovery()
        self._complete_outside_europe_skiing_discovery()

        _execute_tool_call(
            TravelToolCall(
                tool="search_flights",
                arguments={"search_request": _wrong_stale_route()},
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i could go for Erzurum"),
        )

        request_used = mock_flights.call_args.args[0]
        self.assertEqual(request_used.itinerary.origin.code, "EMA")
        self.assertEqual(request_used.itinerary.destination.code, "ERZ")
        self.assertEqual(request_used.itinerary.dates.departure.isoformat(), "2026-09-04")
        self.assertEqual(request_used.itinerary.dates.return_date.isoformat(), "2026-09-07")
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "ERZ")

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=CURRENT_DATE)
    def test_select_beirut_preserves_ema_origin_return_date_and_duration(
        self,
        _mock_today,
        mock_flights,
        _mock_evidence,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        self._start_skiing_discovery()
        self._complete_outside_europe_skiing_discovery_next_week()

        _execute_tool_call(
            TravelToolCall(
                tool="search_flights",
                arguments={"search_request": _wrong_stale_route()},
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="okay i like Beirut"),
        )

        request_used = mock_flights.call_args.args[0]
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(request_used.itinerary.origin.code, "EMA")
        self.assertEqual(request_used.itinerary.destination.code, "BEY")
        self.assertEqual(request_used.itinerary.dates.departure.isoformat(), "2026-08-14")
        self.assertEqual(request_used.itinerary.dates.return_date.isoformat(), "2026-08-17")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "BEY")
        self.assertEqual(active_trip["tripDurationNights"], 3)

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=CURRENT_DATE)
    def test_pick_cheapest_of_destination_recommendations_compares_valid_routes(
        self,
        _mock_today,
        mock_flights,
        _mock_evidence,
    ) -> None:
        mock_flights.side_effect = self._mock_destination_comparison_flight_search
        self._start_skiing_discovery()
        self._complete_outside_europe_skiing_discovery_next_week()
        save_latest_flight_search(
            self.db,
            self.session.id,
            _wrong_stale_route(),
            FlightSearchResponse(
                provider_used="duffel",
                flights=[
                    NormalisedFlightResult(
                        id="old_lhr_jfk",
                        offer_id="old_lhr_jfk",
                        display_id="Old",
                        option_number=1,
                        origin="LHR",
                        destination="JFK",
                        airline="Old Airways",
                        outbound_departure_date="2026-08-14",
                        return_departure_date="2026-08-17",
                        stops=0,
                        stops_label="Non-stop",
                        cabin_class="economy",
                        cabin_class_label="Economy",
                        passenger_count=1,
                        price="50",
                        currency="GBP",
                        provider="duffel",
                    )
                ],
                fallback_reason="Old stale route.",
            ),
        )
        save_last_tool(self.db, self.session.id, "discover_destinations")
        self.db.commit()
        expected_codes = {
            item["airportCode"]
            for item in load_session_memory(self.db, self.session.id)["destination_recommendations"]
        }

        response = _execute_tool_call(
            TravelToolCall(
                tool="rank_flights",
                arguments={"ranking": "cheapest", "limit": 1},
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="pick the cheapest of them on the 17th of this month"),
        )

        requests = [call.args[0] for call in mock_flights.call_args_list]
        destinations = {item.itinerary.destination.code for item in requests}

        self.assertEqual(len(requests), len(expected_codes))
        self.assertEqual(destinations, expected_codes)
        self.assertTrue(all(item.itinerary.origin.code == "EMA" for item in requests))
        self.assertTrue(
            all(item.itinerary.dates.departure.isoformat() == "2026-08-17" for item in requests)
        )
        self.assertTrue(
            all(item.itinerary.dates.return_date.isoformat() == "2026-08-20" for item in requests)
        )
        self.assertFalse(
            any(
                item.itinerary.origin.code == "LHR"
                and item.itinerary.destination.code == "JFK"
                for item in requests
            )
        )
        self.assertIn("I compared flights from East Midlands Airport (EMA)", response.reply)
        self.assertIn("17 Aug 2026", response.reply)
        cheapest_destination = response.results.flights[0]["destination"]
        self.assertIn(cheapest_destination, expected_codes)
        self.assertEqual(response.results.flights[0]["origin"], "EMA")

        memory = load_session_memory(self.db, self.session.id)
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(memory["flights"][0]["destination"], cheapest_destination)
        self.assertNotEqual(memory["search_request"]["itinerary"]["origin"]["code"], "LHR")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-08-17")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-08-20")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], cheapest_destination)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=CURRENT_DATE)
    def test_pick_cheapest_of_them_without_destinations_asks_for_context(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        response = _execute_tool_call(
            TravelToolCall(
                tool="rank_flights",
                arguments={"ranking": "cheapest", "limit": 1},
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="pick the cheapest of them on the 17th of this month"),
        )

        mock_flights.assert_not_called()
        self.assertIn("Please choose or request destination ideas first", response.reply)

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=TODAY)
    def test_beach_shift_clears_stale_flight_state_and_scores_beach(
        self,
        _mock_today,
        mock_flights,
        _mock_evidence,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        self._start_skiing_discovery()
        self._complete_outside_europe_skiing_discovery()
        _execute_tool_call(
            TravelToolCall(
                tool="search_flights",
                arguments={"search_request": _wrong_stale_route()},
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i could go for Erzurum"),
        )
        self.assertTrue(mock_flights.called)

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(
                message=(
                    "you know what i dont want skiing i want somewhere where "
                    "there is a beach and beach activies"
                )
            ),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        memory = load_session_memory(self.db, self.session.id)
        self.assertIsNone(active_trip["selectedDestination"])
        self.assertNotIn("skiing", active_trip["requiredActivities"])
        self.assertIn("beach", active_trip["requiredActivities"])
        self.assertIn("beach_activities", active_trip["activityPreferences"])
        self.assertEqual(memory["flights"], [])
        self.assertIn(response.results.destinations[0]["airportCode"], {"HRG", "SSH"})
        self.assertTrue(
            any(
                "beach" in reason.lower() or "water" in reason.lower()
                for reason in response.results.destinations[0]["matchReasons"]
            )
        )

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main._search_hotels_with_fallback")
    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=TODAY)
    def test_select_hurghada_and_request_flights_and_hotels_uses_active_trip(
        self,
        _mock_today,
        mock_flights,
        mock_hotels,
        _mock_evidence,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        mock_hotels.side_effect = self._mock_hotel_search
        self._start_skiing_discovery()
        self._complete_outside_europe_skiing_discovery()
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(
                message=(
                    "you know what i dont want skiing i want somewhere where "
                    "there is a beach and beach activies"
                )
            ),
        )

        response = _execute_tool_call(
            TravelToolCall(
                tool="search_flights",
                arguments={"search_request": _wrong_stale_route()},
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="okay i like Hurghada what about the flights and hotels"),
        )

        flight_request = mock_flights.call_args.args[0]
        hotel_request = mock_hotels.call_args.args[0]
        self.assertEqual(flight_request.itinerary.origin.code, "EMA")
        self.assertEqual(flight_request.itinerary.destination.code, "HRG")
        self.assertEqual(flight_request.itinerary.dates.departure.isoformat(), "2026-09-04")
        self.assertEqual(flight_request.itinerary.dates.return_date.isoformat(), "2026-09-07")
        self.assertEqual(hotel_request.destination.city, "Hurghada")
        self.assertEqual(hotel_request.check_in_date.isoformat(), "2026-09-04")
        self.assertEqual(hotel_request.check_out_date.isoformat(), "2026-09-07")
        self.assertTrue(response.results.packages)
        self.assertEqual(response.results.flights, [])
        self.assertEqual(response.results.hotels, [])
        self.assertNotIn("ERZ to LHR", response.reply)

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_confirmed_destination_is_not_widened_by_date_only_region_merge(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        self._seed_selected_hurghada_trip()
        mock_flights.side_effect = self._mock_flight_search
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "requested_product": "flight",
                "intent": "flight_search",
                "destination_text": "Egypt",
                "destination_region": "Egypt",
                "destination_type": "country",
                "destination_candidates": ["HRG", "SSH"],
                "departure_date": "2026-09-18",
                "return_date": "2026-09-25",
                "missing_fields": [],
                "use_active_trip_state": False,
            },
        )

        response = chat(
            ChatRequest(
                message="18th of september and return 25th of september",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "HRG")
        self.assertIsNone(active_trip.get("destinationRegion"))
        flight_request = mock_flights.call_args.args[0]
        self.assertEqual(flight_request.itinerary.origin.code, "BHX")
        self.assertEqual(flight_request.itinerary.destination.code, "HRG")
        self.assertEqual(flight_request.itinerary.dates.departure.isoformat(), "2026-09-18")
        self.assertEqual(flight_request.itinerary.dates.return_date.isoformat(), "2026-09-25")
        self.assertNotIn(" to Egypt", response.reply)

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_confirmed_destination_date_and_nights_followup_proceeds(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        self._seed_selected_hurghada_trip()
        mock_flights.side_effect = self._mock_flight_search
        mock_router.return_value = TravelToolCall(tool="general_help", arguments={})

        response = chat(
            ChatRequest(
                message="18th of september and stay for 5 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        flight_request = mock_flights.call_args.args[0]
        self.assertEqual(flight_request.itinerary.destination.code, "HRG")
        self.assertEqual(flight_request.itinerary.dates.departure.isoformat(), "2026-09-18")
        self.assertEqual(flight_request.itinerary.dates.return_date.isoformat(), "2026-09-23")
        self.assertNotIn("Please provide your exact departure and return dates", response.reply)

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_confirmed_destination_date_and_days_followup_uses_days_as_stay_length(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        self._seed_selected_hurghada_trip()
        mock_flights.side_effect = self._mock_flight_search
        mock_router.return_value = TravelToolCall(tool="general_help", arguments={})

        chat(
            ChatRequest(
                message="18th of september and for 5 days",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        flight_request = mock_flights.call_args.args[0]
        self.assertEqual(flight_request.itinerary.origin.code, "BHX")
        self.assertEqual(flight_request.itinerary.destination.code, "HRG")
        self.assertEqual(flight_request.itinerary.dates.departure.isoformat(), "2026-09-18")
        self.assertEqual(flight_request.itinerary.dates.return_date.isoformat(), "2026-09-23")

    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_confirmed_destination_can_change_to_new_specific_city(
        self,
        mock_router,
        mock_flights,
    ) -> None:
        self._seed_selected_hurghada_trip()
        mock_flights.side_effect = self._mock_flight_search
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "requested_product": "flight",
                "intent": "flight_search",
                "destination_text": "Sharm El Sheikh",
                "destination_code": "SSH",
                "destination_type": "city",
                "departure_date": "2026-09-18",
                "return_date": "2026-09-23",
                "missing_fields": [],
                "use_active_trip_state": False,
            },
        )

        chat(
            ChatRequest(
                message="actually make it Sharm El Sheikh from 18th of september for 5 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "SSH")
        flight_request = mock_flights.call_args.args[0]
        self.assertEqual(flight_request.itinerary.destination.code, "SSH")

    @patch("app.services.chat_service.discover_destinations")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_yes_after_destination_discovery_offer_executes_discovery_not_flight_selection(
        self,
        mock_router,
        mock_discovery,
    ) -> None:
        mock_discovery.return_value = self._mock_destination_discovery_response()
        mock_router.side_effect = [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "flight",
                    "intent": "destination_discovery",
                    "destination_text": None,
                    "destination_type": "unknown",
                    "origin_text": "East Midlands Airport",
                    "origin_code": "EMA",
                    "requiredActivities": ["beach"],
                    "environmentPreferences": ["beach"],
                    "departure_date": "2026-09-18",
                    "missing_fields": [],
                    "use_active_trip_state": True,
                },
            ),
            TravelToolCall(
                tool="select_flight",
                arguments={"selection": "current", "use_active_trip_state": True},
            ),
        ]

        offered = chat(
            ChatRequest(message="18th of september from my location", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )
        self.assertIn("I can search beach destinations", offered.reply)
        self.assertIn("departing 18 Sep 2026", offered.reply)

        confirmed = chat(
            ChatRequest(message="yes please", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        mock_discovery.assert_called_once()
        self.assertIn("destination ideas", confirmed.reply)
        self.assertNotIn("Please choose a flight option first", confirmed.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_explicit_miami_destination_beats_destination_discovery(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        save_active_trip(
            self.db,
            self.session.id,
            {
                "revision": 5,
                "origin": {
                    "city": "East Midlands",
                    "airportCode": "EMA",
                    "airportName": "East Midlands Airport",
                    "source": "browser_location_duffel_places",
                },
                "selectedDestination": {
                    "city": "Lyon",
                    "country": "France",
                    "airportCode": "LYS",
                    "source": "old_trip",
                },
                "dates": {
                    "departureDate": "2026-08-19",
                    "returnDate": "2026-08-22",
                    "source": "old_trip",
                },
                "tripDurationNights": 3,
                "requiredActivities": ["skiing"],
                "activityPreferences": ["skiing"],
                "latestDatePriceComparison": {
                    "rangeType": "month",
                    "startDate": "2026-08-01",
                    "endDate": "2026-08-31",
                },
                "status": "ready_for_search",
            },
        )
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="discover_destinations", arguments={"environmentPreferences": ["beach"]}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i want to go to the beach in miami today and return after two days"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        flight_request = mock_flights.call_args.args[0]
        self.assertEqual(active_trip["selectedDestination"]["city"], "Miami")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "MIA")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-08-08")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-08-10")
        self.assertEqual(active_trip["tripDurationNights"], 2)
        self.assertIn("beach", active_trip["requiredActivities"])
        self.assertNotIn("skiing", active_trip["requiredActivities"])
        self.assertNotIn("skiing", active_trip["activityPreferences"])
        self.assertIsNone(active_trip["latestDatePriceComparison"])
        self.assertEqual(response.results.destinations, [])
        self.assertTrue(response.results.flights)
        self.assertNotIn("Hurghada", response.reply)
        self.assertNotIn("Sharm", response.reply)
        self.assertNotIn("skiing", response.reply.lower())
        self.assertEqual(flight_request.itinerary.origin.code, "EMA")
        self.assertEqual(flight_request.itinerary.destination.code, "MIA")
        self.assertEqual(flight_request.itinerary.dates.departure.isoformat(), "2026-08-08")
        self.assertEqual(flight_request.itinerary.dates.return_date.isoformat(), "2026-08-10")

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=AUGUST_8)
    def test_short_miami_followup_sets_destination_without_general_help(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        save_active_trip(
            self.db,
            self.session.id,
            {
                "revision": 3,
                "origin": {
                    "city": "East Midlands",
                    "airportCode": "EMA",
                    "airportName": "East Midlands Airport",
                    "source": "browser_location_duffel_places",
                },
                "dates": {
                    "departureDate": "2026-08-08",
                    "returnDate": "2026-08-10",
                    "source": "previous_request",
                },
                "tripDurationNights": 2,
                "environmentPreferences": ["beach"],
                "requiredActivities": ["beach"],
                "status": "discovering",
            },
        )
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="in miami"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        flight_request = mock_flights.call_args.args[0]
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "MIA")
        self.assertNotIn("Hello, I'm The Voyager", response.reply)
        self.assertEqual(flight_request.itinerary.origin.code, "EMA")
        self.assertEqual(flight_request.itinerary.destination.code, "MIA")
        self.assertEqual(flight_request.itinerary.dates.departure.isoformat(), "2026-08-08")
        self.assertEqual(flight_request.itinerary.dates.return_date.isoformat(), "2026-08-10")

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_london_trip_with_budget_stores_lhr_not_lpl(self, _mock_today) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I'd like to plan a trip to London and my budget is a thousand pounds"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["selectedDestination"]["city"], "London")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "LHR")
        self.assertNotEqual(active_trip["selectedDestination"]["airportCode"], "LPL")
        self.assertEqual(active_trip["budget"], 1000.0)
        self.assertIn("London (LHR)", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_live_on_eleventh_and_fifteen_keeps_london_pending_flight(
        self,
        _mock_today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I'd like to plan a trip to London and my budget is a thousand pounds"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I want to live on the 11th and 15"),
        )

        flight_request = mock_flights.call_args.args[0]
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(flight_request.itinerary.origin.code, "EMA")
        self.assertEqual(flight_request.itinerary.destination.code, "LHR")
        self.assertNotEqual(flight_request.itinerary.destination.code, "LPL")
        self.assertEqual(flight_request.itinerary.dates.departure.isoformat(), "2026-08-11")
        self.assertEqual(flight_request.itinerary.dates.return_date.isoformat(), "2026-08-15")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "LHR")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-08-11")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-08-15")
        self.assertTrue(response.results.flights)

    def test_confirm_selected_flight_adds_cart_without_booking_reference(self) -> None:
        self._seed_flight_results(["263.22", "310.00"])
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="choose option one"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="confirm_booking", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="confirm this booking and put it on checkout"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(self.db.query(Booking).count(), 0)
        self.assertEqual(active_trip["latestVisibleResultType"], "cart")
        self.assertEqual(active_trip["cart"]["items"][0]["itemType"], "flight")
        self.assertEqual(response.results.cart["items"][0]["itemType"], "flight")
        self.assertEqual(response.next_action, "navigate")
        self.assertEqual(response.next_path, "/cart")
        self.assertNotIn("reference", response.reply.lower())

    def test_repeated_flight_confirmation_does_not_duplicate_cart_or_booking(self) -> None:
        self._seed_flight_results(["263.22", "310.00"])
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="choose option one"),
        )
        _execute_tool_call(
            TravelToolCall(tool="confirm_booking", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="confirm this booking and put it on checkout"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="confirm_booking", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="confirm this booking"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(self.db.query(Booking).count(), 0)
        self.assertEqual(len(active_trip["cart"]["items"]), 1)
        self.assertIn("already in your cart", response.reply)
        self.assertEqual(response.next_path, "/cart")

    def test_thank_you_with_active_cart_is_cart_aware(self) -> None:
        self._seed_flight_results(["263.22"])
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="choose option one"),
        )
        _execute_tool_call(
            TravelToolCall(tool="confirm_booking", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="book it"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="thank you"),
        )

        self.assertIn("You're welcome", response.reply)
        self.assertIn("cart", response.reply.lower())
        self.assertNotIn("Hello, I'm The Voyager", response.reply)

    def test_confirm_this_flight_with_one_visible_flight_adds_to_cart(self) -> None:
        self._seed_flight_results(["263.22"])

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="confirm this flight"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["latestVisibleResultType"], "cart")
        self.assertEqual(active_trip["cart"]["items"][0]["itemType"], "flight")
        self.assertEqual(response.next_path, "/cart")
        self.assertNotIn("pending booking", response.reply.lower())

    def test_i_like_this_flight_with_one_visible_flight_selects_without_search(self) -> None:
        self._seed_flight_results(["263.22"])

        with patch("app.main._search_flights_with_fallback") as mock_search:
            response = _execute_tool_call(
                TravelToolCall(tool="general_help", arguments={}),
                self.db,
                self.user,
                self.session,
                ChatRequest(message="i like this flight"),
            )

        active_trip = load_active_trip(self.db, self.session.id)
        mock_search.assert_not_called()
        self.assertEqual(active_trip["selectedFlightOption"]["optionNumber"], 1)
        self.assertEqual(active_trip["latestVisibleResultType"], "flight_selected")
        self.assertIn("flight option 1", response.reply.lower())
        self.assertIn("cart", response.reply.lower())
        self.assertNotIn("0 mock flight", response.reply.lower())

    def test_this_flight_with_multiple_visible_flights_asks_for_option(self) -> None:
        self._seed_flight_results(["263.22", "310.00"])

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i like this flight"),
        )

        self.assertIn("Which flight option", response.reply)
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertIsNone(active_trip.get("selectedFlightOption"))

    @patch("app.services.chat_service.route_tool_with_llama")
    def test_structured_date_text_day_creates_pending_package_action(self, mock_router) -> None:
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "requested_product": "package",
                "intent": "package_search",
                "origin_text": "Birmingham",
                "origin_code": "BHX",
                "destination_text": "Orlando",
                "destination_type": "city",
                "destination_code": "MCO",
                "date_text": "on the 15th of November",
                "month": "2026-11",
                "nights": 4,
                "missing_fields": [],
            },
        )

        response = chat(
            ChatRequest(
                message="Plan a 4 night trip to Orlando from Birmingham on the 15th of November",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "MCO")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-11-15")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-11-19")
        self.assertEqual(active_trip["tripDurationNights"], 4)
        self.assertEqual(active_trip["pendingAction"]["type"], "build_package")
        self.assertEqual(active_trip["pendingAction"]["origin"], "BHX")
        self.assertEqual(active_trip["pendingAction"]["destination"], "MCO")
        self.assertEqual(active_trip["pendingAction"]["destinationCity"], "Orlando")
        self.assertEqual(active_trip["pendingAction"]["passengerCount"], 1)
        self.assertEqual(active_trip["pendingAction"]["requestedProduct"], "package")
        self.assertIn("15 Nov 2026", response.reply)
        self.assertIn("19 Nov 2026", response.reply)

    @patch("app.services.chat_service.route_tool_with_llama")
    def test_structured_trip_single_destination_candidate_prefers_candidate_airport(
        self,
        mock_router,
    ) -> None:
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "requested_product": "package",
                "intent": "package_search",
                "origin_text": "Birmingham",
                "origin_code": "BHX",
                "destination_text": "Paris",
                "destination_type": "city",
                "destination_candidates": ["CDG"],
                "departure_date": "2026-09-12",
                "return_date": "2026-09-16",
                "missing_fields": [],
            },
        )

        chat(
            ChatRequest(
                message="I want a package from Birmingham to Paris from 12 September to 16 September",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "CDG")
        self.assertEqual(active_trip["pendingAction"]["destination"], "CDG")

    @patch("app.services.chat_service.route_tool_with_llama")
    def test_new_broad_activity_request_clears_stale_orlando_package_state(
        self,
        mock_router,
    ) -> None:
        save_active_trip(
            self.db,
            self.session.id,
            {
                "origin": {
                    "city": "Birmingham",
                    "airportCode": "BHX",
                    "airportName": "Birmingham Airport",
                    "country": "United Kingdom",
                    "source": "explicit_origin",
                },
                "selectedDestination": {
                    "city": "Orlando",
                    "country": "United States",
                    "airportCode": "MCO",
                    "airportName": "Orlando International Airport",
                    "source": "selected_region_candidate",
                },
                "dates": {
                    "departureDate": "2026-11-15",
                    "returnDate": "2026-11-19",
                    "source": "old_package_flow",
                },
                "tripDurationNights": 4,
                "pendingTripRequest": {"intent": "package_search"},
                "requestedProducts": ["packages"],
                "latestVisibleResultType": "packages",
                "status": "package_options_ready",
                "latestPackageOptions": [
                    {
                        "optionNumber": 1,
                        "destinationAirportCode": "MCO",
                        "destinationCity": "Orlando",
                        "totalPrice": 714.08,
                        "currency": "GBP",
                    }
                ],
                "currentPackageOption": {
                    "optionNumber": 1,
                    "destinationAirportCode": "MCO",
                },
                "selectedPackageOption": {
                    "optionNumber": 1,
                    "destinationAirportCode": "MCO",
                },
                "cart": {"items": [{"itemType": "package"}]},
            },
        )
        self.db.commit()
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "requested_product": "flight",
                "intent": "destination_discovery",
                "destination_text": None,
                "destination_type": "unknown",
                "destination_code": None,
                "requiredActivities": ["skiing"],
                "activityPreferences": ["skiing"],
                "tripDurationNights": 3,
                "missing_fields": ["departure_date", "destination"],
                "use_active_trip_state": False,
            },
        )

        response = chat(
            ChatRequest(
                message=(
                    "i want to fly to somewhere that i can do skiing and i want "
                    "to go for 3 nights"
                ),
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "BHX")
        self.assertIsNone(active_trip["selectedDestination"])
        self.assertIsNone(active_trip.get("destinationRegion"))
        self.assertEqual(active_trip.get("destinationCandidates"), [])
        self.assertEqual(active_trip["tripDurationNights"], 3)
        self.assertIn("skiing", [value.lower() for value in active_trip["requiredActivities"]])
        self.assertFalse(active_trip.get("latestPackageOptions"))
        self.assertIsNone(active_trip.get("currentPackageOption"))
        self.assertIsNone(active_trip.get("selectedPackageOption"))
        self.assertEqual(active_trip["cart"]["items"][0]["itemType"], "package")
        self.assertNotEqual(active_trip.get("latestVisibleResultType"), "packages")
        self.assertNotIn("Orlando", response.reply)
        self.assertNotIn("MCO", response.reply)
        self.assertIn("skiing", response.reply.lower())
        self.assertIn("3-night", response.reply)

    @patch("app.services.chat_service.discover_destinations")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_broad_activity_frame_preserves_skiing_across_origin_and_date_followups(
        self,
        mock_router,
        mock_discover,
    ) -> None:
        save_active_trip(
            self.db,
            self.session.id,
            {
                "origin": {
                    "city": "Birmingham",
                    "airportCode": "BHX",
                    "airportName": "Birmingham Airport",
                    "country": "United Kingdom",
                    "source": "explicit_origin",
                },
                "selectedDestination": {
                    "city": "Orlando",
                    "country": "United States",
                    "airportCode": "MCO",
                    "airportName": "Orlando International Airport",
                    "source": "selected_region_candidate",
                },
                "dates": {
                    "departureDate": "2026-11-15",
                    "returnDate": "2026-11-19",
                    "source": "old_package_flow",
                },
                "tripDurationNights": 4,
                "pendingTripRequest": {"intent": "package_search"},
                "requestedProducts": ["packages"],
                "latestVisibleResultType": "packages",
                "status": "package_options_ready",
                "latestPackageOptions": [
                    {
                        "optionNumber": 1,
                        "destinationAirportCode": "MCO",
                        "destinationCity": "Orlando",
                        "totalPrice": 714.08,
                        "currency": "GBP",
                    }
                ],
                "currentPackageOption": {"optionNumber": 1, "destinationAirportCode": "MCO"},
                "selectedPackageOption": {"optionNumber": 1, "destinationAirportCode": "MCO"},
                "cart": {"items": [{"itemType": "package"}]},
            },
        )
        self.db.commit()
        mock_router.side_effect = [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "flight",
                    "intent": "destination_discovery",
                    "destination_text": None,
                    "destination_type": "unknown",
                    "destination_code": None,
                    "requiredActivities": ["skiing"],
                    "activityPreferences": ["skiing"],
                    "tripDurationNights": 3,
                    "missing_fields": ["departure_date"],
                    "use_active_trip_state": False,
                },
            ),
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "flight",
                    "intent": "destination_discovery",
                    "origin_text": "East Midlands Airport",
                    "origin_code": "EMA",
                    "destination_text": None,
                    "destination_type": "unknown",
                    "destination_code": None,
                    "month": "2026-09",
                    "date_text": "next month",
                    "requiredActivities": ["skiing"],
                    "activityPreferences": ["skiing"],
                    "missing_fields": ["departure_date"],
                    "use_active_trip_state": True,
                },
            ),
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "flight",
                    "intent": "destination_discovery",
                    "destination_text": None,
                    "destination_type": "unknown",
                    "destination_code": None,
                    "departure_day": 15,
                    "requiredActivities": ["skiing"],
                    "activityPreferences": ["skiing"],
                    "missing_fields": [],
                    "use_active_trip_state": True,
                },
            ),
        ]
        mock_discover.return_value = DestinationDiscoveryResponse(
            provider_used="fallback_tags",
            recommendations=[
                DestinationRecommendation(
                    city="Geneva",
                    country="Switzerland",
                    airportCode="GVA",
                    airportName="Geneva Airport",
                    region="Europe",
                    score=72,
                    matchReasons=["Trusted fallback skiing knowledge matches your request."],
                    warnings=[],
                    evidenceSummary="Activity evidence: Alpine ski areas near Geneva.",
                    estimatedFlightMinutes=103,
                )
            ],
        )

        first = chat(
            ChatRequest(
                message="i want to fly to somewhere that i can do skiing and i want to go for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        first_state = load_active_trip(self.db, self.session.id)
        self.assertEqual(first_state["tripFrame"], "broad_activity_trip")
        self.assertIsNone(first_state["selectedDestination"])
        self.assertFalse(first_state.get("latestPackageOptions"))
        self.assertIn("skiing", [value.lower() for value in first_state["requiredActivities"]])
        self.assertEqual(first_state["tripDurationNights"], 3)
        self.assertNotIn("Florida", first.reply)
        self.assertNotIn("Orlando", first.reply)
        self.assertNotIn("MCO", first.reply)

        second = chat(
            ChatRequest(
                message="maybe next month and from east midlands airport",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        second_state = load_active_trip(self.db, self.session.id)
        self.assertEqual(second_state["origin"]["airportCode"], "EMA")
        self.assertEqual(second_state["month"], "2026-09")
        self.assertEqual(second_state["tripFrame"], "broad_activity_trip")
        self.assertEqual(second_state["tripDurationNights"], 3)
        self.assertIsNone(second_state["selectedDestination"])
        self.assertIn("skiing", [value.lower() for value in second_state["requiredActivities"]])
        self.assertNotIn("Florida", second.reply)
        self.assertNotIn("Orlando", second.reply)
        self.assertNotIn("MCO", second.reply)
        self.assertIn("September", second.reply)

        third = chat(
            ChatRequest(message="on the 15th", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )
        third_state = load_active_trip(self.db, self.session.id)
        self.assertEqual(third_state["origin"]["airportCode"], "EMA")
        self.assertEqual(third_state["dates"]["departureDate"], "2026-09-15")
        self.assertEqual(third_state["dates"]["returnDate"], "2026-09-18")
        self.assertIn("skiing", [value.lower() for value in third_state["requiredActivities"]])
        self.assertNotIn("Florida", third.reply)
        self.assertNotIn("Orlando", third.reply)
        self.assertNotIn("MCO", third.reply)
        self.assertTrue(mock_discover.called)
        discovery_request = mock_discover.call_args.args[0]
        self.assertEqual(discovery_request.origin.code, "EMA")
        self.assertEqual(discovery_request.dates["departure"], "2026-09-15")
        self.assertEqual(discovery_request.dates["return"], "2026-09-18")
        self.assertIn("skiing", [value.lower() for value in discovery_request.required_activities])

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.discover_destinations")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_broad_activity_date_text_this_month_executes_discovery_without_stale_month(
        self,
        mock_router,
        mock_discover,
        _mock_today,
    ) -> None:
        save_active_trip(
            self.db,
            self.session.id,
            {
                "origin": {
                    "city": "Birmingham",
                    "airportCode": "BHX",
                    "airportName": "Birmingham Airport",
                    "source": "old_package_flow",
                },
                "selectedDestination": {
                    "city": "Orlando",
                    "airportCode": "MCO",
                    "airportName": "Orlando International Airport",
                    "source": "old_package_flow",
                },
                "month": "2026-11",
                "dateText": "in november",
                "dates": {"departureDate": "2026-11-15", "returnDate": "2026-11-19"},
                "latestVisibleResultType": "packages",
                "latestPackageOptions": [{"optionNumber": 1, "destinationAirportCode": "MCO"}],
                "requestedProducts": ["packages"],
            },
        )
        self.db.commit()
        mock_router.side_effect = [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "flight",
                    "intent": "destination_discovery",
                    "destination_text": None,
                    "destination_type": "unknown",
                    "destination_code": None,
                    "requiredActivities": ["skiing"],
                    "activityPreferences": ["skiing"],
                    "tripDurationNights": 3,
                    "missing_fields": ["departure_date"],
                    "use_active_trip_state": False,
                },
            ),
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "flight",
                    "intent": "destination_discovery",
                    "destination_text": None,
                    "destination_type": "unknown",
                    "destination_code": None,
                    "date_text": "19th of this month",
                    "departure_day": 19,
                    "requiredActivities": ["skiing"],
                    "activityPreferences": ["skiing"],
                    "missing_fields": [],
                    "use_active_trip_state": True,
                },
            ),
        ]
        mock_discover.return_value = DestinationDiscoveryResponse(
            provider_used="fallback_tags",
            recommendations=[
                DestinationRecommendation(
                    city="Geneva",
                    country="Switzerland",
                    airportCode="GVA",
                    airportName="Geneva Airport",
                    region="Europe",
                    score=72,
                    matchReasons=["Trusted fallback skiing knowledge matches your request."],
                    warnings=[],
                    evidenceSummary="Activity evidence: Alpine ski areas near Geneva.",
                    estimatedFlightMinutes=103,
                )
            ],
        )

        first = chat(
            ChatRequest(
                message="i want to fly to somewhere that i can do skiing and i want to go for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        first_state = load_active_trip(self.db, self.session.id)
        self.assertEqual(first_state["tripFrame"], "broad_activity_trip")
        self.assertIsNone(first_state.get("month"))
        self.assertIsNone(first_state.get("selectedDestination"))
        self.assertEqual(first_state["pendingAction"]["type"], "suggest_destinations")
        self.assertNotIn("November", first.reply)
        self.assertNotIn("Orlando", first.reply)
        self.assertNotIn("MCO", first.reply)

        second = chat(
            ChatRequest(message="19th of this month", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )
        state = load_active_trip(self.db, self.session.id)
        self.assertEqual(state["dates"]["departureDate"], "2026-08-19")
        self.assertEqual(state["dates"]["returnDate"], "2026-08-22")
        self.assertEqual(state["tripDurationNights"], 3)
        self.assertIsNone(state.get("selectedDestination"))
        self.assertIn("skiing", [value.lower() for value in state["requiredActivities"]])
        self.assertNotIn("Please choose a flight option first", second.reply)
        self.assertNotIn("November", second.reply)
        self.assertNotIn("Orlando", second.reply)
        self.assertNotIn("MCO", second.reply)
        discovery_request = mock_discover.call_args.args[0]
        self.assertEqual(discovery_request.origin.code, "BHX")
        self.assertEqual(discovery_request.dates["departure"], "2026-08-19")
        self.assertEqual(discovery_request.dates["return"], "2026-08-22")

    @patch("app.services.chat_service.discover_destinations")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_pending_destination_suggestion_confirmation_runs_discovery_not_flight_selection(
        self,
        mock_router,
        mock_discover,
    ) -> None:
        save_active_trip(
            self.db,
            self.session.id,
            {
                "origin": {
                    "city": "East Midlands",
                    "airportCode": "EMA",
                    "airportName": "East Midlands Airport",
                    "source": "default_origin",
                },
                "tripFrame": "broad_activity_trip",
                "requiredActivities": ["Skiing"],
                "activityPreferences": ["Skiing"],
                "tripDurationNights": 3,
                "selectedDestination": None,
                "dates": {"departureDate": None, "returnDate": None},
                "pendingAction": {
                    "type": "suggest_destinations",
                    "tripFrame": "broad_activity_trip",
                    "origin": "EMA",
                    "requiredActivities": ["Skiing"],
                    "tripDurationNights": 3,
                    "request": {
                        "origin": {
                            "city": "East Midlands",
                            "code": "EMA",
                            "airportName": "East Midlands Airport",
                        },
                        "requiredActivities": ["Skiing"],
                        "activityPreferences": ["Skiing"],
                        "tripDurationNights": 3,
                        "dates": {},
                    },
                },
                "latestVisibleResultType": "destination_discovery_pending",
            },
        )
        self.db.commit()
        mock_router.return_value = TravelToolCall(
            tool="confirm_booking",
            arguments={"confirmed": True, "use_active_trip_state": True},
        )
        mock_discover.return_value = DestinationDiscoveryResponse(
            provider_used="fallback_tags",
            recommendations=[
                DestinationRecommendation(
                    city="Geneva",
                    country="Switzerland",
                    airportCode="GVA",
                    airportName="Geneva Airport",
                    region="Europe",
                    score=72,
                    matchReasons=["Trusted fallback skiing knowledge matches your request."],
                    warnings=[],
                    evidenceSummary="Activity evidence: Alpine ski areas near Geneva.",
                    estimatedFlightMinutes=103,
                )
            ],
        )

        response = chat(
            ChatRequest(message="yes please", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        self.assertTrue(mock_discover.called)
        self.assertNotIn("Please choose a flight option first", response.reply)
        self.assertIn("destination", response.reply.lower())

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service._search_hotels_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_pending_hotel_structured_date_followup_executes_search(
        self,
        mock_router,
        mock_hotels,
        _mock_today,
    ) -> None:
        hotel_requests: list[HotelSearchRequest] = []

        def capture_hotels(request: HotelSearchRequest) -> HotelSearchResponse:
            hotel_requests.append(request)
            return self._mock_hotel_search(request)

        mock_hotels.side_effect = capture_hotels
        mock_router.side_effect = [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "hotel",
                    "intent": "hotel_search",
                    "destination_text": "London",
                    "destination_type": "city",
                    "budget_amount": 600,
                    "budget_currency": "GBP",
                    "missing_fields": ["check_in_date", "nights"],
                    "use_active_trip_state": False,
                },
            ),
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "hotel",
                    "intent": "hotel_search",
                    "destination_text": "London",
                    "destination_type": "city",
                    "date_text": "18th of next month",
                    "departure_day": 18,
                    "nights": 3,
                    "budget_amount": 600,
                    "budget_currency": "GBP",
                    "missing_fields": [],
                    "use_active_trip_state": True,
                },
            ),
        ]

        first = chat(
            ChatRequest(
                message="i want to book an hotel in london and my budget is 600 pounds",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        self.assertIn("When do you want to check in", first.reply)

        second = chat(
            ChatRequest(
                message="the 18th of next month and i want to stay for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        self.assertTrue(hotel_requests)
        hotel_request = hotel_requests[0]
        self.assertEqual(hotel_request.destination.city, "London")
        self.assertEqual(hotel_request.check_in_date.isoformat(), "2026-09-18")
        self.assertEqual(hotel_request.check_out_date.isoformat(), "2026-09-21")
        self.assertNotIn("When do you want to check in", second.reply)
        self.assertIn("hotel option", second.reply.lower())

    @patch("app.services.chat_service.route_tool_with_llama")
    def test_find_flights_to_london_keeps_default_origin_when_llama_returns_same_airport(
        self,
        mock_router,
    ) -> None:
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "requested_product": "flight",
                "intent": "flight_search",
                "origin_text": "London",
                "origin_code": "LHR",
                "destination_text": "London",
                "destination_type": "city",
                "destination_code": "LHR",
                "missing_fields": ["departure_date", "return_date"],
            },
        )

        response = chat(
            ChatRequest(message="find flights to london", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "EMA")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "LHR")
        self.assertNotEqual(
            active_trip["origin"]["airportCode"],
            active_trip["selectedDestination"]["airportCode"],
        )
        self.assertIn("East Midlands Airport (EMA)", response.reply)
        self.assertIn("London Heathrow Airport (LHR)", response.reply)

    @patch("app.services.chat_service.route_tool_with_llama")
    def test_structured_trip_egypt_country_populates_broad_candidates(self, mock_router) -> None:
        mock_router.return_value = TravelToolCall(
            tool="prepare_trip_request",
            arguments={
                "requested_product": "package",
                "intent": "package_search",
                "origin_text": "Birmingham",
                "origin_code": "BHX",
                "destination_text": "Egypt",
                "destination_region": "Egypt",
                "destination_type": "country",
                "destination_candidates": ["HRG", "SSH", "CAI"],
                "month": "2026-12",
                "departure_date": None,
                "return_date": None,
                "missing_fields": ["exact_dates", "destination_airport_choice"],
            },
        )

        response = chat(
            ChatRequest(
                message="I want a package to Egypt from Birmingham in December",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["destinationRegion"]["name"], "Egypt")
        candidate_codes = [
            candidate["airportCode"]
            for candidate in active_trip["destinationCandidates"]
        ]
        self.assertEqual(candidate_codes, ["HRG", "SSH", "CAI"])
        self.assertIn("Egypt", response.reply)
        self.assertNotIn("Florida has", response.reply)

    @patch("app.services.chat_service._search_hotels_with_fallback")
    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_pending_package_budget_passenger_updates_then_confirmation_executes(
        self,
        mock_router,
        mock_flights,
        mock_hotels,
    ) -> None:
        flight_requests: list[FlightSearchRequest] = []
        hotel_requests: list[HotelSearchRequest] = []

        def capture_flights(request: FlightSearchRequest) -> FlightSearchResponse:
            flight_requests.append(request)
            return self._mock_flight_search(request)

        def capture_hotels(request: HotelSearchRequest) -> HotelSearchResponse:
            hotel_requests.append(request)
            return self._mock_hotel_search(request)

        mock_flights.side_effect = capture_flights
        mock_hotels.side_effect = capture_hotels
        mock_router.side_effect = [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "package",
                    "intent": "package_search",
                    "origin_text": "Birmingham",
                    "origin_code": "BHX",
                    "destination_text": "Orlando",
                    "destination_type": "city",
                    "destination_code": "MCO",
                    "departure_date": "2026-11-15",
                    "return_date": "2026-11-19",
                    "nights": 4,
                    "missing_fields": [],
                },
            ),
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "package",
                    "intent": "package_search",
                    "use_active_trip_state": True,
                    "budget_amount": 1000,
                    "budget_currency": "GBP",
                    "passenger_count": 2,
                    "missing_fields": [],
                },
            ),
            TravelToolCall(
                tool="confirm_booking",
                arguments={"confirmed": True, "use_active_trip_state": True},
            ),
        ]

        chat(
            ChatRequest(
                message="Plan a package to Orlando from Birmingham from 15 November to 19 November",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        first_state = load_active_trip(self.db, self.session.id)
        self.assertEqual(first_state["pendingAction"]["destination"], "MCO")

        budget_response = chat(
            ChatRequest(
                message="my budget is 1000 pounds and make it for 2 people",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        budget_state = load_active_trip(self.db, self.session.id)
        self.assertEqual(budget_state["pendingAction"]["type"], "build_package")
        self.assertEqual(budget_state["pendingAction"]["origin"], "BHX")
        self.assertEqual(budget_state["pendingAction"]["destination"], "MCO")
        self.assertEqual(budget_state["pendingAction"]["departureDate"], "2026-11-15")
        self.assertEqual(budget_state["pendingAction"]["returnDate"], "2026-11-19")
        self.assertEqual(budget_state["pendingAction"]["budget"], 1000)
        self.assertEqual(budget_state["pendingAction"]["passengerCount"], 2)
        self.assertFalse(flight_requests)
        self.assertFalse(hotel_requests)
        self.assertIn("Would you like me to build", budget_response.reply)

        response = chat(
            ChatRequest(message="yes please", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertIsNone(active_trip.get("pendingAction"))
        self.assertEqual(active_trip["latestVisibleResultType"], "packages")
        self.assertTrue(flight_requests)
        self.assertTrue(hotel_requests)
        self.assertEqual(flight_requests[-1].itinerary.origin.code, "BHX")
        self.assertEqual(flight_requests[-1].itinerary.destination.code, "MCO")
        self.assertEqual(flight_requests[-1].passenger_count, 2)
        self.assertEqual(hotel_requests[-1].destination.city, "Orlando")
        self.assertIn("I built package options", response.reply)

    def _mock_destination_discovery_response(self) -> DestinationDiscoveryResponse:
        return DestinationDiscoveryResponse(
            provider_used="fallback_tags",
            recommendations=[
                DestinationRecommendation(
                    city="Erzurum",
                    country="Turkey",
                    airportCode="ERZ",
                    airportName="Erzurum Airport",
                    region="Asia",
                    score=70,
                    matchReasons=["Trusted fallback skiing knowledge matches your request."],
                    evidenceSummary="Trusted fallback: Palandoken ski area near Erzurum.",
                    estimatedFlightMinutes=310,
                )
            ],
        )

    def _mock_skiing_destination_set(self) -> DestinationDiscoveryResponse:
        return DestinationDiscoveryResponse(
            provider_used="fallback_tags",
            recommendations=[
                DestinationRecommendation(
                    city="Erzurum",
                    country="Turkey",
                    airportCode="ERZ",
                    airportName="Erzurum Airport",
                    region="Asia",
                    score=56,
                    matchReasons=["Trusted fallback skiing knowledge matches your request."],
                    evidenceSummary="Trusted fallback: Palandoken ski area near Erzurum.",
                    estimatedFlightMinutes=310,
                ),
                DestinationRecommendation(
                    city="Geneva",
                    country="Switzerland",
                    airportCode="GVA",
                    airportName="Geneva Airport",
                    region="Europe",
                    score=52,
                    matchReasons=["Trusted fallback skiing knowledge matches your request."],
                    evidenceSummary="Trusted fallback: Alpine ski areas near Geneva.",
                    estimatedFlightMinutes=103,
                ),
            ],
        )

    def _pending_skiing_router_calls(self) -> list[TravelToolCall]:
        return [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "flight",
                    "intent": "destination_discovery",
                    "destination_text": None,
                    "destination_type": "unknown",
                    "requiredActivities": ["skiing"],
                    "activityPreferences": ["skiing"],
                    "tripDurationNights": 3,
                    "missing_fields": ["departure_date"],
                    "use_active_trip_state": False,
                },
            ),
            TravelToolCall(tool="general_help", arguments={}),
            TravelToolCall(tool="general_help", arguments={}),
        ]

    def _run_birmingham_december_discovery(
        self,
        mock_router,
        mock_discovery,
    ) -> None:
        mock_router.side_effect = [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "flight",
                    "intent": "destination_discovery",
                    "destination_text": None,
                    "destination_type": "unknown",
                    "requiredActivities": ["skiing"],
                    "activityPreferences": ["skiing"],
                    "tripDurationNights": 3,
                    "missing_fields": ["departure_date"],
                    "use_active_trip_state": False,
                },
            ),
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "flight",
                    "intent": "destination_discovery",
                    "origin_code": "BHX",
                    "origin_text": "Birmingham",
                    "destination_text": None,
                    "destination_type": "unknown",
                    "departure_date": "2026-12-15",
                    "nights": 3,
                    "requiredActivities": ["skiing"],
                    "activityPreferences": ["skiing"],
                    "missing_fields": [],
                    "use_active_trip_state": True,
                },
            ),
        ]
        mock_discovery.return_value = self._mock_skiing_destination_set()
        chat(
            ChatRequest(
                message="i want to fly to somewhere that i can do skiing and i want to go for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        chat(
            ChatRequest(
                message="15th of december from birmingham",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_destination_discovery_persists_recommendations_and_dates_to_database(
        self,
        mock_discovery,
        mock_router,
        _mock_today,
    ) -> None:
        self._run_birmingham_december_discovery(mock_router, mock_discovery)

        memory = load_session_memory(self.db, self.session.id)
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertTrue(memory["destination_recommendations"])
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-12-15")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-12-18")
        self.assertEqual(active_trip["tripDurationNights"], 3)
        self.assertEqual(active_trip["latestVisibleResultType"], "destinations")

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service._search_flights_with_fallback")
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_destination_selection_after_discovery_preserves_geneva_and_dates(
        self,
        mock_discovery,
        mock_router,
        mock_flights,
        _mock_today,
    ) -> None:
        mock_flights.side_effect = self._mock_flight_search
        self._run_birmingham_december_discovery(mock_router, mock_discovery)
        mock_router.side_effect = None
        mock_router.return_value = TravelToolCall(tool="general_help", arguments={})

        response = chat(
            ChatRequest(message="i like Geneva", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "GVA")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-12-15")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-12-18")
        self.assertNotIn("Please tell me the exact departure date", response.reply)

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.resolve_clarification_answer_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_two_slot_clarification_date_only_reasks_only_origin(
        self,
        mock_discovery,
        mock_clarification,
        mock_router,
        _mock_today,
    ) -> None:
        mock_router.return_value = TravelToolCall(tool="general_help", arguments={})
        mock_clarification.return_value = ClarificationAnswer(
            resolved=True,
            departure_date="2026-08-21",
        )
        mock_discovery.return_value = self._mock_destination_discovery_response()
        pending_request = DestinationDiscoveryRequest(
            requiredActivities=["skiing"],
            activityPreferences=["skiing"],
            tripDurationNights=3,
            missingFields=["origin", "departureDate"],
        )
        save_persistent_pending_destination_discovery(
            self.db,
            self.session.id,
            {
                **pending_request.model_dump(by_alias=True, mode="json"),
                "askedQuestion": "When do you want to depart, and which airport would you like to fly from?",
            },
        )
        save_active_trip(
            self.db,
            self.session.id,
            {
                "tripFrame": "broad_activity_trip",
                "requiredActivities": ["skiing"],
                "activityPreferences": ["skiing"],
                "tripDurationNights": 3,
                "dates": {"departureDate": None, "returnDate": None},
            },
        )
        self.db.commit()

        response = chat(
            ChatRequest(message="same day next week", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        mock_discovery.assert_not_called()
        self.assertIn("Which airport", response.reply)
        self.assertNotIn("When do you want to depart", response.reply)

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.resolve_clarification_answer_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_two_slot_clarification_default_origin_executes_discovery(
        self,
        mock_discovery,
        mock_clarification,
        mock_router,
        _mock_today,
    ) -> None:
        mock_router.return_value = TravelToolCall(tool="general_help", arguments={})
        mock_clarification.return_value = ClarificationAnswer(
            resolved=True,
            departure_date="2026-08-21",
            origin_text="nearest airport",
        )
        mock_discovery.return_value = self._mock_destination_discovery_response()
        pending_request = DestinationDiscoveryRequest(
            requiredActivities=["skiing"],
            activityPreferences=["skiing"],
            tripDurationNights=3,
            missingFields=["origin", "departureDate"],
        )
        save_persistent_pending_destination_discovery(
            self.db,
            self.session.id,
            {
                **pending_request.model_dump(by_alias=True, mode="json"),
                "askedQuestion": "When do you want to depart, and which airport would you like to fly from?",
            },
        )
        save_active_trip(
            self.db,
            self.session.id,
            {
                "tripFrame": "broad_activity_trip",
                "requiredActivities": ["skiing"],
                "activityPreferences": ["skiing"],
                "tripDurationNights": 3,
                "dates": {"departureDate": None, "returnDate": None},
            },
        )
        self.db.commit()

        response = chat(
            ChatRequest(
                message="same day next week from the nearest airport",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        mock_discovery.assert_called_once()
        discovery_request = mock_discovery.call_args.args[0]
        self.assertEqual(discovery_request.origin.code, "EMA")
        self.assertEqual(discovery_request.dates["departure"], "2026-08-21")
        self.assertEqual(discovery_request.dates["return"], "2026-08-24")
        self.assertIn("destination ideas", response.reply)

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    def test_final_loop_guard_blocks_byte_identical_assistant_reply(
        self,
        mock_router,
        _mock_today,
    ) -> None:
        mock_router.return_value = TravelToolCall(tool="general_help", arguments={})

        first = chat(
            ChatRequest(message="hello", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )
        with self.assertLogs("app.services.chat_service", level="WARNING") as logs:
            second = chat(
                ChatRequest(message="hello again", sessionId=self.session.id),
                db=self.db,
                current_user=self.user,
            )

        self.assertNotEqual(first.reply, second.reply)
        self.assertTrue(
            any("Duplicate assistant reply blocked" in line for line in logs.output)
        )

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_mid_conversation_birmingham_origin_keeps_estimated_flight_minutes(
        self,
        mock_discovery,
        mock_router,
        _mock_today,
    ) -> None:
        self._run_birmingham_december_discovery(mock_router, mock_discovery)

        discovery_request = mock_discovery.call_args.args[0]
        self.assertEqual(discovery_request.origin.code, "BHX")
        self.assertIsNotNone(discovery_request.origin.latitude)
        self.assertIsNotNone(discovery_request.origin.longitude)
        memory = load_session_memory(self.db, self.session.id)
        self.assertTrue(
            all(
                item.get("estimatedFlightMinutes") is not None
                for item in memory["destination_recommendations"]
            )
        )

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.resolve_clarification_answer_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_pending_destination_date_answer_this_month_executes_discovery(
        self,
        mock_discovery,
        mock_clarification,
        mock_router,
        _mock_today,
    ) -> None:
        mock_router.side_effect = self._pending_skiing_router_calls()
        mock_clarification.return_value = ClarificationAnswer(
            resolved=True,
            departure_date="2026-08-19",
        )
        mock_discovery.return_value = self._mock_destination_discovery_response()

        first = chat(
            ChatRequest(
                message="i want to fly to somewhere that i can do skiing and i want to go for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        self.assertIn("When do you want to depart", first.reply)
        self.assertNotIn("choose one of these", first.reply)

        second = chat(
            ChatRequest(message="19th of this month", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        mock_discovery.assert_called_once()
        discovery_request = mock_discovery.call_args.args[0]
        self.assertEqual(discovery_request.dates["departure"], "2026-08-19")
        self.assertEqual(discovery_request.dates["return"], "2026-08-22")
        self.assertIn("destination ideas", second.reply)

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.resolve_clarification_answer_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_pending_destination_explicit_return_beats_stored_duration(
        self,
        mock_discovery,
        mock_clarification,
        mock_router,
        _mock_today,
    ) -> None:
        mock_router.side_effect = self._pending_skiing_router_calls()
        mock_clarification.return_value = ClarificationAnswer(resolved=False)
        mock_discovery.return_value = self._mock_destination_discovery_response()

        chat(
            ChatRequest(
                message="i want to fly to somewhere that i can do skiing and i want to go for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        response = chat(
            ChatRequest(message="19th and back on the 25th", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        mock_discovery.assert_called_once()
        discovery_request = mock_discovery.call_args.args[0]
        self.assertEqual(discovery_request.dates["departure"], "2026-08-19")
        self.assertEqual(discovery_request.dates["return"], "2026-08-25")
        self.assertEqual(discovery_request.trip_duration_nights, 6)
        self.assertIn("destination ideas", response.reply)

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.resolve_clarification_answer_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_pending_destination_date_answer_day_only_executes_discovery(
        self,
        mock_discovery,
        mock_clarification,
        mock_router,
        _mock_today,
    ) -> None:
        mock_router.side_effect = self._pending_skiing_router_calls()
        mock_clarification.return_value = ClarificationAnswer(
            resolved=True,
            departure_date="2026-08-15",
        )
        mock_discovery.return_value = self._mock_destination_discovery_response()

        chat(
            ChatRequest(
                message="i want to fly to somewhere that i can do skiing and i want to go for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        chat(
            ChatRequest(message="on the 15th", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        mock_discovery.assert_called_once()
        discovery_request = mock_discovery.call_args.args[0]
        self.assertEqual(discovery_request.dates["departure"], "2026-08-15")
        self.assertEqual(discovery_request.dates["return"], "2026-08-18")

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.resolve_clarification_answer_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_pending_destination_date_and_new_nights_overrides_stored_duration(
        self,
        mock_discovery,
        mock_clarification,
        mock_router,
        _mock_today,
    ) -> None:
        mock_router.side_effect = self._pending_skiing_router_calls()
        mock_clarification.return_value = ClarificationAnswer(resolved=False)
        mock_discovery.return_value = self._mock_destination_discovery_response()

        chat(
            ChatRequest(
                message="i want to fly to somewhere that i can do skiing and i want to go for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        chat(
            ChatRequest(
                message="18th of september and stay for 5 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        discovery_request = mock_discovery.call_args.args[0]
        self.assertEqual(discovery_request.dates["departure"], "2026-09-18")
        self.assertEqual(discovery_request.dates["return"], "2026-09-23")
        self.assertEqual(discovery_request.trip_duration_nights, 5)

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.resolve_clarification_answer_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_pending_destination_date_and_days_uses_current_stay_length(
        self,
        mock_discovery,
        mock_clarification,
        mock_router,
        _mock_today,
    ) -> None:
        mock_router.side_effect = self._pending_skiing_router_calls()
        mock_clarification.return_value = ClarificationAnswer(resolved=False)
        mock_discovery.return_value = self._mock_destination_discovery_response()

        chat(
            ChatRequest(
                message="i want to fly to somewhere that i can do skiing and i want to go for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        chat(
            ChatRequest(
                message="18th of september and for 5 days",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        discovery_request = mock_discovery.call_args.args[0]
        self.assertEqual(discovery_request.dates["departure"], "2026-09-18")
        self.assertEqual(discovery_request.dates["return"], "2026-09-23")
        self.assertEqual(discovery_request.trip_duration_nights, 5)

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.resolve_clarification_answer_with_llama")
    @patch("app.services.chat_service._search_hotels_with_fallback")
    def test_pending_hotel_date_answer_executes_search(
        self,
        mock_hotel_search,
        mock_clarification,
        mock_router,
        _mock_today,
    ) -> None:
        mock_router.side_effect = [
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "requested_product": "hotel",
                    "intent": "hotel_search",
                    "destination_text": "London",
                    "destination_type": "city",
                    "missing_fields": ["check_in_date", "nights"],
                    "use_active_trip_state": False,
                },
            ),
            TravelToolCall(tool="general_help", arguments={}),
        ]
        mock_clarification.return_value = ClarificationAnswer(
            resolved=True,
            departure_date="2026-09-18",
            nights=3,
        )
        mock_hotel_search.side_effect = self._mock_hotel_search

        first = chat(
            ChatRequest(message="i want to book a hotel in London", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )
        self.assertIn("When do you want to check in", first.reply)

        response = chat(
            ChatRequest(
                message="the 18th of next month and i want to stay for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )

        mock_hotel_search.assert_called_once()
        hotel_request = mock_hotel_search.call_args.args[0]
        self.assertEqual(hotel_request.check_in_date.isoformat(), "2026-09-18")
        self.assertEqual(hotel_request.check_out_date.isoformat(), "2026-09-21")
        self.assertIn("hotel", response.reply.lower())

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.resolve_clarification_answer_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_clarification_loop_guard_avoids_byte_identical_repeat(
        self,
        mock_discovery,
        mock_clarification,
        mock_router,
        _mock_today,
    ) -> None:
        mock_router.side_effect = self._pending_skiing_router_calls()
        mock_clarification.return_value = ClarificationAnswer(resolved=False)

        first = chat(
            ChatRequest(
                message="i want to fly to somewhere that i can do skiing and i want to go for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        second = chat(
            ChatRequest(message="not sure", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        mock_discovery.assert_not_called()
        self.assertNotEqual(first.reply, second.reply)
        self.assertIn("choose one of these", second.reply)

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.resolve_clarification_answer_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_past_clarification_date_is_rejected_without_provider_call(
        self,
        mock_discovery,
        mock_clarification,
        mock_router,
        _mock_today,
    ) -> None:
        mock_router.side_effect = self._pending_skiing_router_calls()
        mock_clarification.return_value = ClarificationAnswer(
            resolved=True,
            departure_date="2026-08-01",
        )

        chat(
            ChatRequest(
                message="i want to fly to somewhere that i can do skiing and i want to go for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        response = chat(
            ChatRequest(message="that date", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        mock_discovery.assert_not_called()
        self.assertIn("When do you want to depart", response.reply)

    @patch("app.services.chat_service.today_utc_date", return_value=date(2026, 8, 14))
    @patch("app.services.chat_service.route_tool_with_llama")
    @patch("app.services.chat_service.resolve_clarification_answer_with_llama")
    @patch("app.services.chat_service.discover_destinations")
    def test_malformed_clarification_date_is_rejected_without_crash(
        self,
        mock_discovery,
        mock_clarification,
        mock_router,
        _mock_today,
    ) -> None:
        mock_router.side_effect = self._pending_skiing_router_calls()
        mock_clarification.return_value = ClarificationAnswer(
            resolved=True,
            departure_date="18/09/2026",
        )

        chat(
            ChatRequest(
                message="i want to fly to somewhere that i can do skiing and i want to go for 3 nights",
                sessionId=self.session.id,
            ),
            db=self.db,
            current_user=self.user,
        )
        response = chat(
            ChatRequest(message="the eighteenth", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        mock_discovery.assert_not_called()
        self.assertIn("When do you want to depart", response.reply)


    @patch("app.services.chat_service.route_tool_with_llama")
    def test_null_session_id_creates_fresh_user_session_instead_of_oldest(
        self,
        mock_router,
    ) -> None:
        save_active_trip(
            self.db,
            self.session.id,
            {
                "origin": {
                    "city": "East Midlands",
                    "airportCode": "EMA",
                    "airportName": "East Midlands Airport",
                    "source": "old_session",
                },
                "selectedDestination": {
                    "city": "Erzurum",
                    "airportCode": "ERZ",
                    "airportName": "Erzurum Airport",
                    "source": "old_session",
                },
                "requiredActivities": ["skiing"],
                "tripDurationNights": 3,
                "dates": {"departureDate": "2026-08-19", "returnDate": "2026-08-22"},
                "lastClarification": {
                    "awaiting": ["departureDate"],
                    "askedText": "When do you want to depart?",
                    "turnCount": 1,
                },
            },
        )
        self.db.commit()
        mock_router.return_value = TravelToolCall(tool="general_help", arguments={})

        response = chat(
            ChatRequest(message="hello", sessionId=None),
            db=self.db,
            current_user=self.user,
        )

        self.assertIsNotNone(response.session_id)
        self.assertNotEqual(response.session_id, self.session.id)
        fresh_state = load_active_trip(self.db, int(response.session_id))
        self.assertIsNone(fresh_state.get("selectedDestination"))
        self.assertFalse(fresh_state.get("requiredActivities"))
        self.assertIsNone(fresh_state.get("lastClarification"))
        old_state = load_active_trip(self.db, self.session.id)
        self.assertEqual(old_state["selectedDestination"]["airportCode"], "ERZ")

    @patch("app.services.chat_service.route_tool_with_llama")
    def test_null_session_id_creates_fresh_demo_session_instead_of_oldest(
        self,
        mock_router,
    ) -> None:
        demo_user = User(
            display_name="Demo User",
            full_name="Demo User",
            email="demo@example.com",
            password_hash="",
        )
        self.db.add(demo_user)
        self.db.flush()
        old_demo_session = ChatSession(user_id=demo_user.id, title="Old demo chat")
        self.db.add(old_demo_session)
        self.db.flush()
        save_active_trip(
            self.db,
            old_demo_session.id,
            {
                "selectedDestination": {
                    "city": "Orlando",
                    "airportCode": "MCO",
                    "airportName": "Orlando International Airport",
                    "source": "old_demo_session",
                },
                "dates": {"departureDate": "2026-11-15", "returnDate": "2026-11-19"},
            },
        )
        self.db.commit()
        mock_router.return_value = TravelToolCall(tool="general_help", arguments={})

        response = chat(
            ChatRequest(message="hello", sessionId=None),
            db=self.db,
            current_user=None,
        )

        self.assertIsNotNone(response.session_id)
        self.assertNotEqual(response.session_id, old_demo_session.id)
        fresh_state = load_active_trip(self.db, int(response.session_id))
        self.assertIsNone(fresh_state.get("selectedDestination"))
        old_state = load_active_trip(self.db, old_demo_session.id)
        self.assertEqual(old_state["selectedDestination"]["airportCode"], "MCO")


if __name__ == "__main__":
    unittest.main()
