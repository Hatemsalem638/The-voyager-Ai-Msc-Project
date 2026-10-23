import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.main import (
    FLIGHT_SEARCH_MEMORY,
    HOTEL_SEARCH_MEMORY,
    _build_package_options,
    _execute_tool_call,
    _extract_budget_amount,
    _extract_trip_duration_components,
    chat,
)
from app.memory_store import load_active_trip, save_active_trip, save_default_origin
from app.models import Booking, ChatSession, User
from app.schemas import (
    ChatRequest,
    FlightSearchRequest,
    FlightSearchResponse,
    HotelSearchRequest,
    HotelSearchResponse,
    NormalisedFlightResult,
    NormalisedHotelResult,
    PackageBuildRequest,
    TravelToolCall,
)


class PackageBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.SessionLocal()
        self.user = User(
            display_name="Package Test User",
            email="package-test@example.com",
            password_hash="",
        )
        self.db.add(self.user)
        self.db.flush()
        self.session = ChatSession(user_id=self.user.id, title="Package test")
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

    def _seed_lyon_trip(self, *, with_dates: bool = True) -> None:
        save_active_trip(
            self.db,
            self.session.id,
            {
                "revision": 9,
                "origin": {
                    "city": "East Midlands",
                    "country": "United Kingdom",
                    "airportCode": "EMA",
                    "airportName": "East Midlands Airport",
                    "source": "browser_location_duffel_places",
                },
                "selectedDestination": {
                    "city": "Lyon",
                    "country": "France",
                    "airportCode": "LYS",
                    "airportName": "Lyon-Saint Exupery Airport",
                    "source": "selected_recommendation",
                },
                "dates": {
                    "departureDate": "2026-09-01" if with_dates else None,
                    "returnDate": "2026-09-04" if with_dates else None,
                    "source": "selected_date_followup" if with_dates else "date_later",
                },
                "tripDurationNights": 3,
                "passengerCount": 1,
                "cabinClass": "economy",
                "status": "ready_for_package_or_checkout" if with_dates else "destination_selected",
            },
        )
        self.db.commit()

    def _seed_september_comparison(self) -> None:
        self._seed_lyon_trip(with_dates=False)
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["latestDatePriceComparison"] = {
            "rangeType": "month",
            "rangeLabel": "September 2026",
            "startDate": "2026-09-01",
            "endDate": "2026-09-30",
            "originAirportCode": "EMA",
            "destinationAirportCode": "LYS",
            "tripDurationNights": 3,
            "results": [
                {
                    "departureDate": "2026-09-01",
                    "returnDate": "2026-09-04",
                    "cheapestPrice": 91,
                    "currency": "GBP",
                    "offerSummary": {
                        "id": "flight-1",
                        "offer_id": "offer-1",
                        "origin": "EMA",
                        "destination": "LYS",
                        "airline": "Test Airways",
                        "outbound_departure_date": "2026-09-01",
                        "return_departure_date": "2026-09-04",
                        "cabin_class": "economy",
                        "passenger_count": 1,
                        "price": "91",
                        "currency": "GBP",
                        "provider": "duffel",
                    },
                }
            ],
            "cheapestResult": {
                "departureDate": "2026-09-13",
                "returnDate": "2026-09-16",
                "cheapestPrice": 80,
                "currency": "GBP",
                "offerSummary": None,
            },
        }
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

    @staticmethod
    def _mock_package_flights(request: FlightSearchRequest) -> FlightSearchResponse:
        return FlightSearchResponse(
            provider_used="duffel",
            flights=[
                NormalisedFlightResult(
                    id="flight-package-1",
                    offer_id="offer-package-1",
                    display_id="Offer package",
                    option_number=1,
                    origin=request.itinerary.origin.code,
                    destination=request.itinerary.destination.code,
                    airline="Package Airways",
                    outbound_departure_date=request.itinerary.dates.departure.isoformat(),
                    outbound_departure_time="08:30",
                    return_departure_date=(
                        request.itinerary.dates.return_date.isoformat()
                        if request.itinerary.dates.return_date
                        else None
                    ),
                    return_departure_time="14:20",
                    stops=0,
                    stops_label="Non-stop",
                    duration_minutes=108,
                    duration_label="1h 48m",
                    cabin_class=request.preferences.cabin_class,
                    cabin_class_label=request.preferences.cabin_class.title(),
                    passenger_count=request.passenger_count,
                    price="91",
                    currency="GBP",
                    provider="duffel",
                )
            ],
        )

    @staticmethod
    def _mock_package_hotels(request: HotelSearchRequest) -> HotelSearchResponse:
        return HotelSearchResponse(
            provider_used="liteapi",
            results=[
                NormalisedHotelResult(
                    id="hotel-package-1",
                    option_number=1,
                    hotel_name="Lyon Central Hotel",
                    location=request.destination.city or "Lyon",
                    check_in_date=request.check_in_date.isoformat(),
                    check_out_date=request.check_out_date.isoformat(),
                    nights=3,
                    guest_count=request.guest_count,
                    room_count=request.room_count,
                    rating=4.2,
                    board_type="Room only",
                    room_name="Standard room",
                    refundable=True,
                    price=300,
                    currency="GBP",
                    provider="liteapi",
                    short_id="Hotel package",
                )
            ],
        )

    @staticmethod
    def _package_request_with_budget(max_budget: float | None = None) -> PackageBuildRequest:
        return PackageBuildRequest.model_validate(
            {
                "originAirportCode": "MAN",
                "destinationAirportCode": "BCN",
                "destinationCity": "Barcelona",
                "destinationCountry": "Spain",
                "departureDate": "2026-09-18",
                "returnDate": "2026-09-23",
                "passengerCount": 1,
                "rooms": 1,
                "adults": 1,
                "cabin": "economy",
                "maxBudget": max_budget,
                "maxPackages": 5,
            }
        )

    @staticmethod
    def _multi_package_flights(request: FlightSearchRequest) -> FlightSearchResponse:
        flights = []
        for index, (offer_id, price) in enumerate(
            [
                ("offer-low", 520),
                ("offer-mid", 704),
                ("offer-high", 777),
            ],
            start=1,
        ):
            flights.append(
                NormalisedFlightResult(
                    id=f"flight-package-{index}",
                    offer_id=offer_id,
                    display_id=f"Offer package {index}",
                    option_number=index,
                    origin=request.itinerary.origin.code,
                    destination=request.itinerary.destination.code,
                    airline="Vueling",
                    outbound_departure_date=request.itinerary.dates.departure.isoformat(),
                    return_departure_date=request.itinerary.dates.return_date.isoformat()
                    if request.itinerary.dates.return_date
                    else None,
                    stops=0,
                    stops_label="Non-stop",
                    cabin_class=request.preferences.cabin_class,
                    cabin_class_label=request.preferences.cabin_class.title(),
                    passenger_count=request.passenger_count,
                    price=str(price),
                    currency="GBP",
                    provider="duffel",
                )
            )
        return FlightSearchResponse(provider_used="duffel", flights=flights)

    @staticmethod
    def _multi_package_hotels(request: HotelSearchRequest) -> HotelSearchResponse:
        hotels = []
        for index, (hotel_id, hotel_name, price, rating) in enumerate(
            [
                ("hotel-low", "Barcelona Princess", 220, 4.4),
                ("hotel-mid", "Acta Voraport", 220, 4.1),
                ("hotel-high", "Central Barcelona Hotel", 420, 4.7),
            ],
            start=1,
        ):
            hotels.append(
                NormalisedHotelResult(
                    id=hotel_id,
                    option_number=index,
                    hotel_name=hotel_name,
                    location=request.destination.city or "Barcelona",
                    check_in_date=request.check_in_date.isoformat(),
                    check_out_date=request.check_out_date.isoformat(),
                    nights=5,
                    guest_count=request.guest_count,
                    room_count=request.room_count,
                    rating=rating,
                    board_type="Room only",
                    room_name="Standard room",
                    refundable=True,
                    price=price,
                    currency="GBP",
                    provider="liteapi",
                    short_id=hotel_name,
                )
            )
        return HotelSearchResponse(provider_used="liteapi", results=hotels)

    def _seed_barcelona_package_results(self) -> None:
        package_request = self._package_request_with_budget()
        flight_request = FlightSearchRequest.model_validate(
            {
                "searchType": "flight",
                "passengerCount": 1,
                "itinerary": {
                    "tripType": "round_trip",
                    "origin": {"city": "Manchester", "code": "MAN"},
                    "destination": {"city": "Barcelona", "code": "BCN"},
                    "dates": {"departure": "2026-09-18", "return": "2026-09-23"},
                },
                "preferences": {"cabinClass": "economy", "maxStops": None},
            }
        )
        hotel_request = HotelSearchRequest.model_validate(
            {
                "searchType": "hotel",
                "destination": {"city": "Barcelona"},
                "checkInDate": "2026-09-18",
                "checkOutDate": "2026-09-23",
                "guestCount": 1,
                "roomCount": 1,
            }
        )
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip.update(
            {
                "revision": 12,
                "origin": {
                    "city": "Manchester",
                    "country": "United Kingdom",
                    "airportCode": "MAN",
                    "airportName": "Manchester Airport",
                    "source": "explicit_origin",
                },
                "selectedDestination": {
                    "city": "Barcelona",
                    "country": "Spain",
                    "airportCode": "BCN",
                    "airportName": "Barcelona-El Prat Josep Tarradellas Airport",
                    "source": "explicit_destination",
                    "confirmed": True,
                },
                "destinationRegion": None,
                "destinationCandidates": [],
                "dates": {
                    "departureDate": "2026-09-18",
                    "returnDate": "2026-09-23",
                    "source": "llama_structured_trip_request",
                },
                "month": "2026-09",
                "tripDurationNights": 5,
                "passengerCount": 1,
                "requestedProducts": ["packages"],
                "pendingTripRequest": {
                    "intent": "package_search",
                    "selectedDestination": {
                        "city": "Barcelona",
                        "country": "Spain",
                        "airportCode": "BCN",
                        "airportName": "Barcelona-El Prat Josep Tarradellas Airport",
                        "source": "explicit_destination",
                        "confirmed": True,
                    },
                    "dates": {"departureDate": "2026-09-18", "returnDate": "2026-09-23"},
                    "month": "2026-09",
                    "missingFields": [],
                },
                "latestPackageRequest": package_request.model_dump(by_alias=True, mode="json"),
                "latestPackageOptions": _build_package_options(
                    package_request,
                    self._multi_package_flights(flight_request),
                    self._multi_package_hotels(hotel_request),
                    active_trip_revision=12,
                ),
                "latestVisibleResultType": "packages",
                "status": "package_options_ready",
            }
        )
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

    def test_date_selection_followup_sets_active_trip_dates(self) -> None:
        self._seed_september_comparison()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="okay i will go on the 1st"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-09-01")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-09-04")
        self.assertEqual(active_trip["status"], "ready_for_package_or_checkout")
        self.assertEqual(active_trip["selectedFlightCandidate"]["origin"], "EMA")
        self.assertIn("1 Sep 2026 to 4 Sep 2026", response.reply)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)

    @patch("app.main._search_hotels_with_fallback")
    @patch("app.main._search_flights_with_fallback")
    def test_package_request_uses_active_trip_flight_and_hotel_dates(
        self,
        mock_flights,
        mock_hotels,
    ) -> None:
        mock_flights.side_effect = self._mock_package_flights
        mock_hotels.side_effect = self._mock_package_hotels
        self._seed_lyon_trip()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="find me a full package with hotel and total price"),
        )

        flight_request = mock_flights.call_args.args[0]
        hotel_request = mock_hotels.call_args.args[0]
        self.assertEqual(flight_request.itinerary.origin.code, "EMA")
        self.assertEqual(flight_request.itinerary.destination.code, "LYS")
        self.assertEqual(flight_request.itinerary.dates.departure.isoformat(), "2026-09-01")
        self.assertEqual(flight_request.itinerary.dates.return_date.isoformat(), "2026-09-04")
        self.assertEqual(hotel_request.destination.city, "Lyon")
        self.assertEqual(hotel_request.check_in_date.isoformat(), "2026-09-01")
        self.assertEqual(hotel_request.check_out_date.isoformat(), "2026-09-04")
        self.assertTrue(response.results.packages)
        self.assertEqual(response.results.flights, [])
        self.assertEqual(response.results.hotels, [])
        self.assertEqual(response.results.packages[0]["totalPrice"], 391)
        self.assertIn("cheapest package", response.reply)
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["latestVisibleResultType"], "packages")
        self.assertTrue(active_trip["latestPackageOptions"])

    @patch("app.main._search_hotels_with_fallback")
    @patch("app.main._search_flights_with_fallback")
    def test_budget_only_followup_preserves_confirmed_package_trip(
        self,
        mock_flights,
        mock_hotels,
    ) -> None:
        self._seed_barcelona_package_results()

        response = _execute_tool_call(
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "requested_product": "package",
                    "use_active_trip_state": True,
                    "destination_text": "Barcelona",
                    "destination_type": "region",
                    "destination_candidates": ["BCN"],
                    "month": "2026-09",
                    "budget_amount": 800,
                    "budget_currency": "GBP",
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="but my budget is 800 pounds"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["origin"]["airportCode"], "MAN")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "BCN")
        self.assertIsNone(active_trip.get("destinationRegion"))
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-09-18")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-09-23")
        self.assertEqual(active_trip["budget"], 800)
        self.assertIsNone(active_trip.get("pendingAction"))
        self.assertTrue(response.results.packages)
        self.assertNotIn("several main airport choices", response.reply)
        self.assertNotIn("Please provide your exact departure and return dates", response.reply)
        mock_flights.assert_not_called()
        mock_hotels.assert_not_called()

    @patch("app.main._search_hotels_with_fallback")
    @patch("app.main._search_flights_with_fallback")
    def test_passenger_only_followup_preserves_confirmed_package_trip(
        self,
        mock_flights,
        mock_hotels,
    ) -> None:
        mock_flights.side_effect = self._multi_package_flights
        mock_hotels.side_effect = self._multi_package_hotels
        self._seed_barcelona_package_results()

        response = _execute_tool_call(
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "requested_product": "package",
                    "use_active_trip_state": True,
                    "passenger_count": 2,
                },
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="make it for 2 people"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "BCN")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-09-18")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-09-23")
        self.assertEqual(active_trip["passengerCount"], 2)
        self.assertTrue(response.results.packages)
        self.assertNotIn("Please provide your exact departure and return dates", response.reply)
        flight_request = mock_flights.call_args.args[0]
        hotel_request = mock_hotels.call_args.args[0]
        self.assertEqual(flight_request.passenger_count, 2)
        self.assertEqual(hotel_request.guest_count, 2)

    def test_budgeted_package_options_only_show_in_budget_results(self) -> None:
        package_request = self._package_request_with_budget(800)
        flight_request = FlightSearchRequest.model_validate(
            {
                "searchType": "flight",
                "passengerCount": 1,
                "itinerary": {
                    "tripType": "round_trip",
                    "origin": {"city": "Manchester", "code": "MAN"},
                    "destination": {"city": "Barcelona", "code": "BCN"},
                    "dates": {"departure": "2026-09-18", "return": "2026-09-23"},
                },
                "preferences": {"cabinClass": "economy", "maxStops": None},
            }
        )
        hotel_request = HotelSearchRequest.model_validate(
            {
                "searchType": "hotel",
                "destination": {"city": "Barcelona"},
                "checkInDate": "2026-09-18",
                "checkOutDate": "2026-09-23",
                "guestCount": 1,
                "roomCount": 1,
            }
        )

        packages = _build_package_options(
            package_request,
            self._multi_package_flights(flight_request),
            self._multi_package_hotels(hotel_request),
            active_trip_revision=1,
        )

        self.assertTrue(packages)
        self.assertTrue(all(package["totalPrice"] <= 800 for package in packages))
        self.assertEqual([package["optionNumber"] for package in packages], list(range(1, len(packages) + 1)))

    @patch("app.main._search_hotels_with_fallback")
    @patch("app.main._search_flights_with_fallback")
    def test_package_reply_says_no_in_budget_and_shows_closest_option(
        self,
        mock_flights,
        mock_hotels,
    ) -> None:
        mock_flights.side_effect = self._multi_package_flights
        mock_hotels.side_effect = self._multi_package_hotels
        self._seed_barcelona_package_results()
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["budget"] = 700
        save_active_trip(self.db, self.session.id, active_trip)
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="find me a full package"),
        )

        self.assertEqual(len(response.results.packages), 1)
        self.assertGreater(response.results.packages[0]["totalPrice"], 700)
        self.assertIn("I could not find a package within your GBP 700 budget", response.reply)
        self.assertIn("closest option", response.reply)

    def test_package_builder_deduplicates_and_renumbers_options(self) -> None:
        package_request = self._package_request_with_budget()
        flight_request = FlightSearchRequest.model_validate(
            {
                "searchType": "flight",
                "passengerCount": 1,
                "itinerary": {
                    "tripType": "round_trip",
                    "origin": {"city": "Manchester", "code": "MAN"},
                    "destination": {"city": "Barcelona", "code": "BCN"},
                    "dates": {"departure": "2026-09-18", "return": "2026-09-23"},
                },
                "preferences": {"cabinClass": "economy", "maxStops": None},
            }
        )
        hotel_request = HotelSearchRequest.model_validate(
            {
                "searchType": "hotel",
                "destination": {"city": "Barcelona"},
                "checkInDate": "2026-09-18",
                "checkOutDate": "2026-09-23",
                "guestCount": 1,
                "roomCount": 1,
            }
        )
        flights = self._multi_package_flights(flight_request)
        duplicate_flight = flights.flights[0].model_copy(update={"id": "duplicate-flight"})
        flights.flights.append(duplicate_flight)
        hotels = self._multi_package_hotels(hotel_request)
        duplicate_hotel = hotels.results[0].model_copy(update={"option_number": 99})
        hotels.results.append(duplicate_hotel)

        packages = _build_package_options(
            package_request,
            flights,
            hotels,
            active_trip_revision=1,
        )

        keys = {
            (
                package["flight"]["offerId"],
                package["hotel"]["hotelId"],
                package["flight"]["departureDate"],
                package["flight"].get("returnDate"),
                package["hotel"]["checkInDate"],
                package["hotel"]["checkOutDate"],
                package["totalPrice"],
            )
            for package in packages
        }
        self.assertEqual(len(keys), len(packages))
        self.assertEqual([package["optionNumber"] for package in packages], list(range(1, len(packages) + 1)))

    def test_package_request_requires_dates(self) -> None:
        self._seed_lyon_trip(with_dates=False)

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="find me a full package"),
        )

        self.assertIn("When do you want to depart", response.reply)

    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_london_trip_sentence_stores_next_month_duration_and_budget(
        self,
        _today,
        mock_flights,
    ) -> None:
        mock_flights.side_effect = self._mock_package_flights

        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(
                message=(
                    "I'm traveling to London for three days and my budget is "
                    "a thousand pounds what is the my date would be next month "
                    "on the same day today"
                ),
            ),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "LHR")
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-09-09")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-09-11")
        self.assertEqual(active_trip["tripDurationDays"], 3)
        self.assertEqual(active_trip["tripDurationNights"], 2)
        self.assertEqual(active_trip["budget"], 1000)

    def test_duration_parser_reads_days_and_nights(self) -> None:
        self.assertEqual(
            _extract_trip_duration_components("for 3 days and two nights"),
            (3, 2),
        )
        self.assertEqual(
            _extract_trip_duration_components("I will be staying for three days"),
            (3, 2),
        )
        self.assertEqual(_extract_trip_duration_components("two nights"), (None, 2))

    def test_budget_parser_reads_thousand_pounds(self) -> None:
        self.assertEqual(_extract_budget_amount("my budget is a thousand pounds"), 1000)
        self.assertEqual(_extract_budget_amount("under £1000"), 1000)
        self.assertEqual(_extract_budget_amount("within my budget of 1000"), 1000)

    @patch("app.main._search_hotels_with_fallback")
    @patch("app.main._search_flights_with_fallback")
    def test_pending_package_duration_days_and_nights_builds_package(
        self,
        mock_flights,
        mock_hotels,
    ) -> None:
        mock_flights.side_effect = self._mock_package_flights
        mock_hotels.side_effect = self._mock_package_hotels
        save_active_trip(
            self.db,
            self.session.id,
            {
                "revision": 4,
                "origin": {
                    "city": "East Midlands",
                    "country": "United Kingdom",
                    "airportCode": "EMA",
                    "airportName": "East Midlands Airport",
                    "source": "browser_location_duffel_places",
                },
                "selectedDestination": {
                    "city": "London",
                    "country": "United Kingdom",
                    "airportCode": "LHR",
                    "airportName": "London Heathrow Airport",
                    "source": "explicit_destination",
                },
                "dates": {
                    "departureDate": "2026-09-09",
                    "returnDate": None,
                    "source": "package_clarification",
                },
                "budget": 1000,
                "pendingPackageRequest": {
                    "intent": "package_search",
                    "origin": {
                        "city": "East Midlands",
                        "country": "United Kingdom",
                        "airportCode": "EMA",
                        "airportName": "East Midlands Airport",
                        "source": "browser_location_duffel_places",
                    },
                    "destination": {
                        "city": "London",
                        "country": "United Kingdom",
                        "airportCode": "LHR",
                        "airportName": "London Heathrow Airport",
                        "source": "explicit_destination",
                    },
                    "departureDate": "2026-09-09",
                    "returnDate": None,
                    "maxBudget": 1000,
                    "passengerCount": 1,
                    "rooms": 1,
                    "missingFields": ["returnDate"],
                },
            },
        )
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="for 3 days and two nights"),
        )

        flight_request = mock_flights.call_args.args[0]
        hotel_request = mock_hotels.call_args.args[0]
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-09-11")
        self.assertEqual(active_trip["tripDurationDays"], 3)
        self.assertEqual(active_trip["tripDurationNights"], 2)
        self.assertIsNone(active_trip["pendingPackageRequest"])
        self.assertEqual(flight_request.itinerary.origin.code, "EMA")
        self.assertEqual(flight_request.itinerary.destination.code, "LHR")
        self.assertEqual(hotel_request.destination.city, "London")
        self.assertNotIn("How many nights will you stay", response.reply)
        self.assertTrue(response.results.packages)

    def test_package_deal_followup_after_flights_creates_pending_from_active_trip(self) -> None:
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
                "selectedDestination": {
                    "city": "London",
                    "country": "United Kingdom",
                    "airportCode": "LHR",
                    "airportName": "London Heathrow Airport",
                    "source": "explicit_destination",
                },
                "dates": {
                    "departureDate": "2026-09-09",
                    "returnDate": None,
                    "source": "explicit_message",
                },
                "budget": 1000,
                "latestVisibleResultType": "flights",
            },
        )
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="is there any other packages that are deals that are available"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        pending = active_trip["pendingPackageRequest"]
        self.assertEqual(pending["origin"]["airportCode"], "EMA")
        self.assertEqual(pending["destination"]["airportCode"], "LHR")
        self.assertEqual(pending["departureDate"], "2026-09-09")
        self.assertEqual(pending["maxBudget"], 1000)
        self.assertEqual(pending["missingFields"], ["returnDate"])
        self.assertIn("How many nights will you stay", response.reply)

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_package_request_to_london_stores_pending_state(self, _today) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="Build me a flight and hotel package to London, United Kingdom"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        pending = active_trip["pendingPackageRequest"]
        self.assertIsInstance(pending, dict)
        self.assertEqual(pending["destination"]["airportCode"], "LHR")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "LHR")
        self.assertIn("departureDate", pending["missingFields"])
        self.assertIn("returnDate", pending["missingFields"])
        self.assertIn("When do you want to depart", response.reply)

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_package_clarification_merges_date_then_route_without_losing_destination(
        self,
        _today,
    ) -> None:
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="Build me a flight and hotel package to London, United Kingdom"),
        )

        date_response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="today"),
        )
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["pendingPackageRequest"]["departureDate"], "2026-08-09")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "LHR")
        self.assertIn("How many nights", date_response.reply)

        route_response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="Cairo to London"),
        )
        active_trip = load_active_trip(self.db, self.session.id)
        pending = active_trip["pendingPackageRequest"]
        self.assertEqual(pending["origin"]["airportCode"], "CAI")
        self.assertEqual(pending["destination"]["airportCode"], "LHR")
        self.assertEqual(pending["departureDate"], "2026-08-09")
        self.assertEqual(pending["missingFields"], ["returnDate"])
        self.assertIn("Cairo (CAI) to London (LHR)", route_response.reply)
        self.assertIn("How many nights", route_response.reply)

    @patch("app.main._search_hotels_with_fallback")
    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_package_clarification_three_nights_builds_cairo_to_london_package(
        self,
        _today,
        mock_flights,
        mock_hotels,
    ) -> None:
        mock_flights.side_effect = self._mock_package_flights
        mock_hotels.side_effect = self._mock_package_hotels
        for message in [
            "Build me a flight and hotel package to London, United Kingdom",
            "today",
            "Cairo to London",
        ]:
            _execute_tool_call(
                TravelToolCall(tool="general_help", arguments={}),
                self.db,
                self.user,
                self.session,
                ChatRequest(message=message),
            )

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="3 nights"),
        )

        flight_request = mock_flights.call_args.args[0]
        hotel_request = mock_hotels.call_args.args[0]
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(flight_request.itinerary.origin.code, "CAI")
        self.assertEqual(flight_request.itinerary.destination.code, "LHR")
        self.assertEqual(flight_request.itinerary.dates.departure.isoformat(), "2026-08-09")
        self.assertEqual(flight_request.itinerary.dates.return_date.isoformat(), "2026-08-12")
        self.assertEqual(hotel_request.destination.city, "London")
        self.assertEqual(hotel_request.check_in_date.isoformat(), "2026-08-09")
        self.assertEqual(hotel_request.check_out_date.isoformat(), "2026-08-12")
        self.assertIsNone(active_trip["pendingPackageRequest"])
        self.assertEqual(active_trip["origin"]["airportCode"], "CAI")
        self.assertEqual(active_trip["selectedDestination"]["airportCode"], "LHR")
        self.assertTrue(response.results.packages)
        self.assertIn("Cairo", response.reply)

    @patch("app.main._search_hotels_with_fallback")
    @patch("app.main._search_flights_with_fallback")
    def test_stale_route_is_not_used_for_package(
        self,
        mock_flights,
        mock_hotels,
    ) -> None:
        mock_flights.side_effect = self._mock_package_flights
        mock_hotels.side_effect = self._mock_package_hotels
        self._seed_lyon_trip()
        active_trip = load_active_trip(self.db, self.session.id)
        active_trip["latestFlightRequest"] = {
            "itinerary": {
                "origin": {"code": "LHR"},
                "destination": {"code": "JFK"},
                "dates": {"departure": "2026-10-01"},
            }
        }
        save_active_trip(self.db, self.session.id, active_trip)

        _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="find package"),
        )

        flight_request = mock_flights.call_args.args[0]
        self.assertEqual(flight_request.itinerary.origin.code, "EMA")
        self.assertEqual(flight_request.itinerary.destination.code, "LYS")

    @patch("app.main._search_hotels_with_fallback")
    @patch("app.main._search_flights_with_fallback")
    def test_package_selection_adds_to_cart_and_checkout_hands_off_to_checkout_page(
        self,
        mock_flights,
        mock_hotels,
    ) -> None:
        mock_flights.side_effect = self._mock_package_flights
        mock_hotels.side_effect = self._mock_package_hotels
        self._seed_lyon_trip()
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="find me a full package"),
        )
        flight_call_count = mock_flights.call_count
        hotel_call_count = mock_hotels.call_count

        selection_response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="select package 1"),
        )
        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["selectedPackageOption"]["optionNumber"], 1)
        self.assertEqual(active_trip["cart"]["status"], "active")
        self.assertEqual(active_trip["cartStatus"], "active")
        self.assertEqual(active_trip["latestCartId"], active_trip["cart"]["cartId"])
        self.assertEqual(active_trip["latestVisibleResultType"], "cart")
        self.assertIn("added package option 1", selection_response.reply.lower())
        self.assertEqual(selection_response.results.flights, [])
        self.assertEqual(selection_response.results.hotels, [])
        self.assertEqual(mock_flights.call_count, flight_call_count)
        self.assertEqual(mock_hotels.call_count, hotel_call_count)

        checkout_response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="checkout"),
        )
        active_trip = load_active_trip(self.db, self.session.id)
        booking = self.db.query(Booking).filter(Booking.booking_type == "package_checkout").first()
        self.assertIsNone(booking)
        self.assertEqual(active_trip["cart"]["status"], "active")
        self.assertEqual(active_trip["latestVisibleResultType"], "cart")
        self.assertEqual(checkout_response.next_action, "navigate")
        self.assertEqual(checkout_response.next_path, "/checkout")
        self.assertEqual(checkout_response.redirect_to, "/checkout")
        self.assertIn("take you to checkout", checkout_response.reply)
        self.assertIsNotNone(checkout_response.results.cart)

    @patch("app.main._search_hotels_with_fallback")
    @patch("app.main._search_flights_with_fallback")
    def test_llama_add_to_cart_intent_returns_cart_redirect(
        self,
        mock_flights,
        mock_hotels,
    ) -> None:
        mock_flights.side_effect = self._mock_package_flights
        mock_hotels.side_effect = self._mock_package_hotels
        self._seed_lyon_trip()
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="find me a full package"),
        )

        response = _execute_tool_call(
            TravelToolCall(
                tool="add_to_cart",
                arguments={"item_type": "package", "option_number": 1},
            ),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="i like package 1"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["cart"]["status"], "active")
        self.assertEqual(active_trip["selectedPackageOption"]["optionNumber"], 1)
        self.assertEqual(response.next_path, "/cart")
        self.assertEqual(response.redirect_to, "/cart")
        self.assertIn("added package option 1", response.reply.lower())

    @patch("app.main.route_tool_with_llama")
    @patch("app.main._search_hotels_with_fallback")
    @patch("app.main._search_flights_with_fallback")
    def test_chat_uses_llama_add_to_cart_before_keyword_package_selection(
        self,
        mock_flights,
        mock_hotels,
        mock_router,
    ) -> None:
        mock_flights.side_effect = self._mock_package_flights
        mock_hotels.side_effect = self._mock_package_hotels
        self._seed_lyon_trip()
        _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="find me a full package"),
        )
        mock_router.return_value = TravelToolCall(
            tool="add_to_cart",
            arguments={"item_type": "package", "option_number": 1},
        )

        response = chat(
            ChatRequest(message="i like package 1", sessionId=self.session.id),
            db=self.db,
            current_user=self.user,
        )

        self.assertEqual(response.redirect_to, "/cart")
        self.assertEqual(response.next_path, "/cart")
        mock_router.assert_called_once()

    def test_llama_checkout_typo_intent_returns_checkout_redirect(self) -> None:
        self._seed_lyon_trip()
        active_trip = load_active_trip(self.db, self.session.id)
        package = {
            "packageId": "pkg-1",
            "optionNumber": 1,
            "destinationCity": "Lyon",
            "destinationAirportCode": "LYS",
            "totalPrice": 391,
            "currency": "GBP",
            "flight": {"airline": "Package Airways", "origin": "EMA", "destination": "LYS"},
            "hotel": {"hotelName": "Lyon Central Hotel", "city": "Lyon"},
        }
        active_trip["selectedPackageOption"] = package
        active_trip["cart"] = {
            "cartId": "cart-test",
            "items": [{"cartItemId": "cart-item-1", "itemType": "package", "packageOption": package}],
            "totalPrice": 391,
            "currency": "GBP",
            "status": "active",
        }
        active_trip["latestVisibleResultType"] = "cart"
        save_active_trip(self.db, self.session.id, active_trip)

        response = _execute_tool_call(
            TravelToolCall(tool="checkout", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="chekout"),
        )

        self.assertEqual(response.redirect_to, "/checkout")
        self.assertEqual(response.next_path, "/checkout")
        self.assertNotIn("Hello, I'm The Voyager", response.reply)

    def test_llama_checkout_empty_cart_does_not_redirect(self) -> None:
        self._seed_lyon_trip()

        response = _execute_tool_call(
            TravelToolCall(tool="checkout", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="chekout"),
        )

        self.assertIsNone(response.redirect_to)
        self.assertIn("Please add a package", response.reply)

    def test_checkout_requires_cart(self) -> None:
        self._seed_lyon_trip()

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="checkout"),
        )

        self.assertIn("Please add a package", response.reply)

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_checkout_check_out_command_without_cart_does_not_general_help(self, _today) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="check out"),
        )

        self.assertIn("Please add a package", response.reply)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_date_budget_first_collects_fields_and_asks_destination(self, _today) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(
                message=(
                    "I want to go to the 11th and come back on the 15th "
                    "my budget is a thousand pounds"
                ),
            ),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-08-11")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-08-15")
        self.assertEqual(active_trip["budget"], 1000)
        self.assertIn("Where would you like to go", response.reply)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 9))
    def test_leave_back_date_followup_collects_dates(self, _today) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I'll leave on the 11th back on the 15th"),
        )

        active_trip = load_active_trip(self.db, self.session.id)
        self.assertEqual(active_trip["dates"]["departureDate"], "2026-08-11")
        self.assertEqual(active_trip["dates"]["returnDate"], "2026-08-15")
        self.assertIn("Where would you like to go", response.reply)
        self.assertNotIn("Hello, I'm The Voyager", response.reply)


if __name__ == "__main__":
    unittest.main()
