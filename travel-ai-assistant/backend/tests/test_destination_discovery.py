import unittest
from datetime import date
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.airport_lookup import find_nearest_airport
from app.database import Base
from app.destination_discovery import discover_destinations
from app.main import (
    _execute_destination_discovery_tool,
    _execute_flight_search_request,
    _execute_tool_call,
    _get_or_create_demo_context,
    _merge_destination_refinement,
    _resolve_destination_followup_departure,
)
from app.memory_store import load_safe_llama_context, load_session_memory, save_default_origin
from app.models import ChatSession, User
from app.providers.opentripmap_provider import classify_evidence_for_preferences
from app.schemas import (
    ChatRequest,
    DestinationDiscoveryRequest,
    DestinationDiscoveryResponse,
    DestinationRecommendation,
    FlightAirport,
    FlightDates,
    FlightItinerary,
    FlightPreferences,
    FlightSearchRequest,
    TravelToolCall,
)


class DestinationDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        self.db = self.SessionLocal()
        self.user, self.session = _get_or_create_demo_context(self.db)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    @patch.dict(
        "os.environ",
        {
            "DUFFEL_API_KEY": "duffel_test_mock",
            "DUFFEL_BASE_URL": "https://api.duffel.com",
        },
    )
    @patch("app.airport_lookup.requests.get")
    def test_current_location_nearest_airport_cairo(self, mock_get: Mock) -> None:
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [
                {
                    "type": "airport",
                    "iata_code": "CAI",
                    "name": "Cairo International Airport",
                    "city_name": "Cairo",
                    "iata_country_code": "EG",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                }
            ]
        }
        mock_get.return_value = mock_response

        nearest = find_nearest_airport(30.0444, 31.2357)

        self.assertEqual(nearest["airportCode"], "CAI")
        self.assertEqual(nearest["source"], "browser_location_duffel_places")
        self.assertEqual(mock_get.call_args.kwargs["params"]["lat"], 30.0444)

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    def test_beach_request_scores_red_sea_destinations_highly(self, _mock_evidence) -> None:
        request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "environmentPreferences": ["beach"],
                "travellers": {"adults": 2, "children": 0},
            }
        )

        response = discover_destinations(request)

        self.assertEqual(response.provider_used, "fallback_tags")
        self.assertIn(response.recommendations[0].airportCode, {"HRG", "SSH"})

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    def test_skiing_request_does_not_rank_red_sea_destinations_highly(self, _mock_evidence) -> None:
        request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "activityPreferences": ["skiing"],
                "travellers": {"adults": 2, "children": 0},
            }
        )

        response = discover_destinations(request)
        first_codes = [item.airportCode for item in response.recommendations[:2]]

        self.assertNotIn("HRG", first_codes)
        self.assertNotIn("SSH", first_codes)

    def test_weak_places_do_not_count_as_skiing_evidence(self) -> None:
        evidence = classify_evidence_for_preferences(
            [
                {"name": "Mountain Museum", "kinds": "museums,cultural"},
                {"name": "Old Church", "kinds": "churches,historic"},
                {"name": "Aquarium", "kinds": "aquariums"},
                {"name": "National Park", "kinds": "national_parks,natural"},
            ],
            {"activity": ["skiing"]},
        )

        self.assertEqual(evidence["skiEvidenceCount"], 0)

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    def test_max_flight_duration_penalises_far_destinations(self, _mock_evidence) -> None:
        request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "activityPreferences": ["skiing"],
                "maxFlightDurationMinutes": 120,
                "travellers": {"adults": 2, "children": 2},
            }
        )

        response = discover_destinations(request)
        far = next(item for item in response.recommendations if item.airportCode in {"GVA", "LYS"})

        self.assertTrue(far.warnings)

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    def test_europe_short_flight_keeps_skiing_as_required_activity(
        self,
        _mock_evidence,
    ) -> None:
        request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "activityPreferences": ["skiing"],
                "regionPreferences": ["Europe"],
                "shortFlightPreference": True,
                "travellers": {"adults": 2, "children": 2},
            }
        )

        response = discover_destinations(request)
        codes = [item.airportCode for item in response.recommendations]

        self.assertNotIn("IST", codes)
        self.assertNotIn("ATH", codes)
        self.assertIn(response.recommendations[0].airportCode, {"GVA", "LYS"})
        self.assertTrue(
            any(
                "Trade-off" in warning
                for item in response.recommendations
                for warning in item.warnings
            )
        )

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    def test_non_skiing_destinations_are_excluded_when_skiing_required(
        self,
        _mock_evidence,
    ) -> None:
        request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "regionPreferences": ["Europe"],
                "shortFlightPreference": True,
            }
        )

        response = discover_destinations(request)
        codes = [item.airportCode for item in response.recommendations]

        self.assertEqual(set(codes), {"GVA", "LYS"})

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    def test_family_reason_not_added_when_family_not_requested(
        self,
        _mock_evidence,
    ) -> None:
        request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "travellers": {"adults": 1, "children": 0},
            }
        )

        response = discover_destinations(request)

        for destination in response.recommendations:
            self.assertNotIn("Suitable for family travel.", destination.matchReasons)

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    def test_fallback_only_skiing_cannot_score_perfectly(
        self,
        _mock_evidence,
    ) -> None:
        request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "East Midlands",
                    "code": "EMA",
                    "latitude": 52.8311,
                    "longitude": -1.3281,
                },
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "travellers": {"adults": 1, "children": 0},
            }
        )

        response = discover_destinations(request)

        self.assertTrue(response.recommendations)
        self.assertTrue(all(item.score <= 80 for item in response.recommendations))
        self.assertTrue(all(item.score < 100 for item in response.recommendations))

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    def test_august_skiing_warning_and_score_cap_for_fallback_matches(
        self,
        _mock_evidence,
    ) -> None:
        request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "East Midlands",
                    "code": "EMA",
                    "latitude": 52.8311,
                    "longitude": -1.3281,
                },
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "dates": {"departure": "2026-08-14", "return": "2026-08-17"},
                "travellers": {"adults": 1, "children": 0},
            }
        )

        response = discover_destinations(request)

        self.assertTrue(response.recommendations)
        self.assertTrue(all(item.score <= 65 for item in response.recommendations))
        self.assertTrue(
            any(
                "Outdoor skiing may not be available in August" in warning
                for item in response.recommendations
                for warning in item.warnings
            )
        )

    @patch(
        "app.destination_discovery.get_destination_evidence",
        return_value={
            "evidenceNames": ["Menara Bassin", "Menara gardens", "Battle of Marrakech"],
            "skiEvidenceCount": 0,
            "skiEvidenceNames": [],
        },
    )
    def test_skiing_evidence_summary_separates_activity_from_general_attractions(
        self,
        _mock_evidence,
    ) -> None:
        request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "East Midlands",
                    "code": "EMA",
                    "latitude": 52.8311,
                    "longitude": -1.3281,
                },
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "excludedRegions": ["Europe"],
                "travellers": {"adults": 1, "children": 0},
            }
        )

        response = discover_destinations(request)
        marrakesh = next(item for item in response.recommendations if item.airportCode == "RAK")

        self.assertIn("Activity evidence: Trusted fallback: Oukaimeden", marrakesh.evidenceSummary)
        self.assertIn("General attractions: Menara Bassin", marrakesh.evidenceSummary)
        self.assertNotIn("Live skiing or winter-sports evidence", marrakesh.matchReasons)

    @patch("app.destination_discovery.get_destination_evidence", side_effect=Exception("provider unavailable"))
    def test_opentripmap_provider_failure_does_not_crash_when_mocked(self, _mock_evidence) -> None:
        request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {"city": "Cairo", "code": "CAI"},
                "environmentPreferences": ["beach"],
                "travellers": {"adults": 1, "children": 0},
            }
        )

        response = discover_destinations(request)

        self.assertEqual(response.provider_used, "fallback_tags")
        self.assertTrue(response.recommendations)

    def test_safe_llama_context_includes_default_origin_without_gps(self) -> None:
        save_default_origin(
            self.db,
            self.session.id,
            {
                "city": "Cairo",
                "country": "Egypt",
                "airportCode": "CAI",
                "airportName": "Cairo International Airport",
                "latitude": 30.1219,
                "longitude": 31.4056,
                "source": "browser_location",
            },
        )
        self.db.commit()

        context = load_safe_llama_context(self.db, self.session.id)

        self.assertEqual(context["default_origin"]["airportCode"], "CAI")
        self.assertNotIn("latitude", context["default_origin"])
        self.assertNotIn("longitude", context["default_origin"])

    @patch("app.main.discover_destinations")
    def test_discover_destination_tool_uses_default_origin(self, mock_discover) -> None:
        save_default_origin(
            self.db,
            self.session.id,
            {
                "city": "Cairo",
                "country": "Egypt",
                "airportCode": "CAI",
                "airportName": "Cairo International Airport",
                "latitude": 30.1219,
                "longitude": 31.4056,
                "source": "browser_location",
            },
        )
        mock_discover.return_value = DestinationDiscoveryResponse(
            provider_used="fallback_tags",
            recommendations=[
                DestinationRecommendation(
                    city="Hurghada",
                    country="Egypt",
                    airportCode="HRG",
                    airportName="Hurghada International Airport",
                    region="Africa",
                    score=90,
                    matchReasons=["Matches your beach preference."],
                    warnings=[],
                    evidenceSummary="Fallback tags were used.",
                    estimatedFlightMinutes=62,
                )
            ],
            message="Mocked destination response.",
        )

        response = _execute_destination_discovery_tool(
            TravelToolCall(
                tool="discover_destinations",
                arguments={"environmentPreferences": ["beach"]},
            ),
            self.db,
            self.session,
            ChatRequest(message="I want somewhere near the beach"),
        )

        self.assertTrue(response.results.destinations)
        called_request = mock_discover.call_args.args[0]
        self.assertEqual(called_request.origin.code, "CAI")
        self.assertEqual(called_request.origin.latitude, 30.1219)

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main._search_flights_with_fallback")
    def test_europe_short_flight_followup_refines_destination_discovery_not_flights(
        self,
        mock_flights,
        _mock_evidence,
    ) -> None:
        initial_request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "activityPreferences": ["skiing"],
                "travellers": {"adults": 2, "children": 2},
            }
        )
        _execute_destination_discovery_tool(
            TravelToolCall(
                tool="discover_destinations",
                arguments=initial_request.model_dump(by_alias=True, mode="json"),
            ),
            self.db,
            self.session,
            ChatRequest(message="I have 2 children and want a skiing holiday"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="keep it in europe and i dont like long flights"),
        )

        mock_flights.assert_not_called()
        self.assertTrue(response.results.destinations)
        refined_request = load_session_memory(self.db, self.session.id)[
            "destination_discovery_request"
        ]
        self.assertIn("skiing", refined_request["requiredActivities"])
        self.assertNotIn(
            "HRG",
            [destination["airportCode"] for destination in response.results.destinations],
        )
        self.assertNotIn(
            "SSH",
            [destination["airportCode"] for destination in response.results.destinations],
        )
        self.assertIn("longer flights", response.reply.lower())
        self.assertIn("could not find a short-flight european skiing destination", response.reply.lower())

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main._search_flights_with_fallback")
    def test_relaxed_followup_removes_europe_short_flight_and_skiing_requirement(
        self,
        mock_flights,
        _mock_evidence,
    ) -> None:
        initial_request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "activityPreferences": ["skiing"],
                "regionPreferences": ["Europe"],
                "shortFlightPreference": True,
                "softPreferences": ["region:europe", "short_flight"],
                "hardFilters": ["region:europe"],
            }
        )
        _execute_destination_discovery_tool(
            TravelToolCall(
                tool="discover_destinations",
                arguments=initial_request.model_dump(by_alias=True, mode="json"),
            ),
            self.db,
            self.session,
            ChatRequest(message="I have 2 children and want a skiing holiday"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(
                message=(
                    "it doesnt have to be in europe and i dint care about long "
                    "flights ii just need a place with other places to enjoy"
                )
            ),
        )

        mock_flights.assert_not_called()
        refined_request = load_session_memory(self.db, self.session.id)[
            "destination_discovery_request"
        ]
        self.assertEqual(refined_request["regionPreferences"], [])
        self.assertFalse(refined_request["shortFlightPreference"])
        self.assertIsNone(refined_request["maxFlightDurationMinutes"])
        self.assertNotIn("skiing", refined_request["requiredActivities"])
        self.assertNotIn("skiing", refined_request["activityPreferences"])
        self.assertIn("attractions", refined_request["activityPreferences"])
        self.assertIn("sightseeing", refined_request["activityPreferences"])
        self.assertIn("activities", refined_request["activityPreferences"])
        self.assertIn("removed the Europe, short-flight preference", response.reply)
        self.assertIn("attractions and things to do", response.reply)
        self.assertNotIn("region and flight-duration preferences", response.reply)
        self.assertTrue(response.results.destinations)
        self.assertNotEqual(
            [item["airportCode"] for item in response.results.destinations[:2]],
            ["GVA", "LYS"],
        )

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    def test_i_dont_want_skiing_removes_required_skiing(
        self,
        _mock_evidence,
    ) -> None:
        initial_request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "regionPreferences": ["Europe"],
            }
        )
        _execute_destination_discovery_tool(
            TravelToolCall(
                tool="discover_destinations",
                arguments=initial_request.model_dump(by_alias=True, mode="json"),
            ),
            self.db,
            self.session,
            ChatRequest(message="I want a skiing holiday"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I don't want skiing"),
        )

        refined_request = load_session_memory(self.db, self.session.id)[
            "destination_discovery_request"
        ]
        self.assertNotIn("skiing", refined_request["requiredActivities"])
        self.assertNotIn("skiing", refined_request["activityPreferences"])
        self.assertNotEqual(
            [item["airportCode"] for item in response.results.destinations],
            ["GVA", "LYS"],
        )

    def test_long_flight_removal_clears_short_flight_constraints(self) -> None:
        initial_request = DestinationDiscoveryRequest.model_validate(
            {
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "regionPreferences": ["Europe"],
                "maxFlightDurationMinutes": 120,
                "shortFlightPreference": True,
                "softPreferences": ["region:europe", "short_flight"],
                "hardFilters": ["region:europe", "max_flight_minutes:120"],
            }
        )

        refined_request = _merge_destination_refinement(
            initial_request,
            "I don't care about long flights",
        )

        self.assertFalse(refined_request.short_flight_preference)
        self.assertIsNone(refined_request.max_flight_duration_minutes)
        self.assertNotIn("short_flight", refined_request.soft_preferences)
        self.assertNotIn("max_flight_minutes:120", refined_request.hard_filters)
        self.assertIn("Europe", refined_request.region_preferences)

    def test_europe_removal_clears_region_constraints(self) -> None:
        initial_request = DestinationDiscoveryRequest.model_validate(
            {
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "regionPreferences": ["Europe"],
                "shortFlightPreference": True,
                "softPreferences": ["region:europe", "short_flight"],
                "hardFilters": ["region:europe"],
            }
        )

        refined_request = _merge_destination_refinement(
            initial_request,
            "It doesn't have to be in Europe",
        )

        self.assertEqual(refined_request.region_preferences, [])
        self.assertEqual(refined_request.excluded_regions, [])
        self.assertNotIn("region:europe", refined_request.soft_preferences)
        self.assertNotIn("region:europe", refined_request.hard_filters)
        self.assertTrue(refined_request.short_flight_preference)

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main._search_flights_with_fallback")
    def test_no_i_dont_want_it_in_europe_excludes_europe_but_preserves_skiing(
        self,
        mock_flights,
        _mock_evidence,
    ) -> None:
        initial_request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "regionPreferences": ["Europe"],
                "softPreferences": ["region:europe"],
            }
        )
        _execute_destination_discovery_tool(
            TravelToolCall(
                tool="discover_destinations",
                arguments=initial_request.model_dump(by_alias=True, mode="json"),
            ),
            self.db,
            self.session,
            ChatRequest(message="I want skiing in Europe"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="no i dont want it in europe"),
        )

        mock_flights.assert_not_called()
        refined_request = load_session_memory(self.db, self.session.id)[
            "destination_discovery_request"
        ]
        self.assertEqual(refined_request["regionPreferences"], [])
        self.assertEqual(refined_request["excludedRegions"], ["Europe"])
        self.assertIn("skiing", refined_request["requiredActivities"])
        result_codes = [item["airportCode"] for item in response.results.destinations]
        self.assertNotIn("GVA", result_codes)
        self.assertNotIn("LYS", result_codes)
        self.assertTrue(result_codes)
        self.assertIn("excluded Europe", response.reply)
        self.assertIn("kept skiing as the main requirement", response.reply)
        self.assertNotIn("using your region preference", response.reply)
        for destination in response.results.destinations:
            self.assertNotEqual(destination["region"], "Europe")
            self.assertFalse(
                any(
                    "Europe region preference" in reason
                    for reason in destination.get("matchReasons", [])
                )
            )

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=date(2026, 8, 4))
    def test_date_followup_continues_pending_destination_discovery_from_ema(
        self,
        _mock_today,
        mock_flights,
        _mock_evidence,
    ) -> None:
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

        initial_response = _execute_tool_call(
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

        self.assertIn("When do you want to depart", initial_response.reply)
        self.assertIn("EMA", initial_response.reply)
        pending_request = load_session_memory(self.db, self.session.id)[
            "pending_destination_discovery"
        ]
        self.assertEqual(pending_request["origin"]["code"], "EMA")
        self.assertEqual(pending_request["origin"]["airportName"], "East Midlands Airport")
        self.assertEqual(pending_request["tripDurationNights"], 3)
        self.assertIn("departureDate", pending_request["missingFields"])
        self.assertIn("skiing", pending_request["requiredActivities"])

        bad_llama_flight_tool = TravelToolCall(
            tool="search_flights",
            arguments={
                "search_request": {
                    "searchType": "flight",
                    "passengerCount": 1,
                    "itinerary": {
                        "tripType": "one_way",
                        "origin": {"city": "Cairo", "code": "CAI"},
                        "destination": {"city": "London", "code": "LHR"},
                        "dates": {"departure": "2026-08-11"},
                    },
                    "preferences": {"cabinClass": "economy", "maxStops": 0},
                }
            },
        )

        followup_response = _execute_tool_call(
            bad_llama_flight_tool,
            self.db,
            self.user,
            self.session,
            ChatRequest(message="maybe same day next week"),
        )

        mock_flights.assert_not_called()
        memory = load_session_memory(self.db, self.session.id)
        discovery_request = memory["destination_discovery_request"]
        self.assertIsNone(memory["pending_destination_discovery"])
        self.assertIsNone(memory["search_request"])
        self.assertEqual(discovery_request["origin"]["code"], "EMA")
        self.assertEqual(discovery_request["origin"]["airportName"], "East Midlands Airport")
        self.assertEqual(discovery_request["dates"]["departure"], "2026-08-11")
        self.assertEqual(discovery_request["dates"]["return"], "2026-08-14")
        self.assertEqual(discovery_request["tripDurationNights"], 3)
        self.assertIn("skiing", discovery_request["requiredActivities"])
        self.assertTrue(followup_response.results.destinations)
        self.assertFalse(followup_response.results.flights)
        self.assertIn("East Midlands Airport (EMA)", followup_response.reply)
        self.assertIn("11 Aug 2026", followup_response.reply)
        self.assertIn("14 Aug 2026", followup_response.reply)
        self.assertIn("skiing", followup_response.reply.lower())
        self.assertIn("3 nights", followup_response.reply)
        self.assertNotIn("CAI", followup_response.reply)
        self.assertNotIn("LHR", followup_response.reply)

    @patch("app.main.today_utc_date", return_value=date(2026, 8, 4))
    def test_same_day_next_week_resolves_from_current_date(self, _mock_today) -> None:
        self.assertEqual(
            _resolve_destination_followup_departure("maybe same day next week"),
            "2026-08-11",
        )

    @patch("app.main._search_flights_with_fallback")
    def test_search_flights_refuses_missing_destination_without_lhr_default(
        self,
        mock_flights,
    ) -> None:
        incomplete_request = FlightSearchRequest.model_construct(
            search_type="flight",
            passenger_count=1,
            itinerary=FlightItinerary.model_construct(
                trip_type="one_way",
                origin=FlightAirport.model_construct(city="East Midlands", code="EMA"),
                destination=FlightAirport.model_construct(city=None, code=""),
                dates=FlightDates.model_construct(
                    departure=date(2026, 8, 11),
                    return_date=None,
                ),
            ),
            preferences=FlightPreferences.model_construct(
                cabin_class="economy",
                max_stops=None,
            ),
        )

        response = _execute_flight_search_request(
            self.db,
            self.session,
            ChatRequest(message="maybe same day next week"),
            incomplete_request,
        )

        mock_flights.assert_not_called()
        self.assertEqual(response.reply, "Where would you like to travel to?")
        self.assertFalse(response.results.flights)
        self.assertNotIn("LHR", response.reply)

    @patch("app.main._search_flights_with_fallback")
    def test_date_only_followup_without_pending_context_does_not_default_to_lhr(
        self,
        mock_flights,
    ) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="maybe same day next week"),
        )

        mock_flights.assert_not_called()
        self.assertFalse(response.results.flights)
        self.assertNotIn("LHR", response.reply)
        self.assertIn("route", response.reply.lower())

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch("app.main._search_flights_with_fallback")
    @patch("app.main.today_utc_date", return_value=date(2026, 8, 4))
    def test_new_ema_session_does_not_reuse_old_cai_lhr_memory(
        self,
        _mock_today,
        mock_flights,
        _mock_evidence,
    ) -> None:
        old_user = User(
            display_name="Old User",
            email="old-session@example.com",
            password_hash="x",
        )
        self.db.add(old_user)
        self.db.flush()
        old_session = ChatSession(user_id=old_user.id, title="Old CAI session")
        self.db.add(old_session)
        self.db.flush()
        save_default_origin(
            self.db,
            old_session.id,
            {
                "city": "Cairo",
                "country": "Egypt",
                "airportCode": "CAI",
                "airportName": "Cairo International Airport",
                "source": "browser_location_duffel_places",
            },
        )
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

        _execute_tool_call(
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
        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="maybe same day next week"),
        )

        mock_flights.assert_not_called()
        discovery_request = load_session_memory(self.db, self.session.id)[
            "destination_discovery_request"
        ]
        self.assertEqual(discovery_request["origin"]["code"], "EMA")
        self.assertNotEqual(discovery_request["origin"]["code"], "CAI")
        self.assertFalse(response.results.flights)

    def test_negative_europe_followup_adds_excluded_regions(self) -> None:
        initial_request = DestinationDiscoveryRequest.model_validate(
            {
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "regionPreferences": ["Europe"],
                "softPreferences": ["region:europe"],
                "hardFilters": ["region:europe"],
            }
        )

        refined_request = _merge_destination_refinement(
            initial_request,
            "not in Europe please",
        )

        self.assertEqual(refined_request.region_preferences, [])
        self.assertEqual(refined_request.excluded_regions, ["Europe"])
        self.assertIn("skiing", refined_request.required_activities)
        self.assertNotIn("region:europe", refined_request.soft_preferences)
        self.assertNotIn("region:europe", refined_request.hard_filters)

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    def test_europe_exclusion_filters_europe_destinations_from_discovery(
        self,
        _mock_evidence,
    ) -> None:
        request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "excludedRegions": ["Europe"],
            }
        )

        response = discover_destinations(request)
        codes = [item.airportCode for item in response.recommendations]

        self.assertNotIn("GVA", codes)
        self.assertNotIn("LYS", codes)
        self.assertTrue(codes)
        self.assertTrue(
            any(item.region != "Europe" for item in response.recommendations)
        )

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    @patch(
        "app.destination_discovery.DESTINATION_CANDIDATES",
        [
            {
                "city": "Geneva",
                "country": "Switzerland",
                "airportCode": "GVA",
                "airportName": "Geneva Airport",
                "region": "Europe",
                "lat": 46.2381,
                "lon": 6.1089,
                "fallbackTags": ["skiing_nearby", "mountains", "winter", "family"],
            },
            {
                "city": "Lyon",
                "country": "France",
                "airportCode": "LYS",
                "airportName": "Lyon-Saint Exupery Airport",
                "region": "Europe",
                "lat": 45.7256,
                "lon": 5.0811,
                "fallbackTags": ["skiing_nearby", "mountains", "culture", "food"],
            },
        ],
    )
    def test_europe_exclusion_without_outside_skiing_candidates_explains_gap(
        self,
        _mock_evidence,
    ) -> None:
        initial_request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "regionPreferences": ["Europe"],
            }
        )
        _execute_destination_discovery_tool(
            TravelToolCall(
                tool="discover_destinations",
                arguments=initial_request.model_dump(by_alias=True, mode="json"),
            ),
            self.db,
            self.session,
            ChatRequest(message="I want skiing in Europe"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="no i dont want it in europe"),
        )

        self.assertEqual(response.results.destinations, [])
        self.assertIn("could not find a strong non-Europe skiing match", response.reply)
        self.assertIn("expand the candidate list or allow Europe", response.reply)

    @patch("app.destination_discovery.get_destination_evidence", return_value={"evidenceNames": []})
    def test_i_dont_want_skiing_in_europe_does_not_keep_skiing_europe_matches(
        self,
        _mock_evidence,
    ) -> None:
        initial_request = DestinationDiscoveryRequest.model_validate(
            {
                "origin": {
                    "city": "Cairo",
                    "code": "CAI",
                    "latitude": 30.1219,
                    "longitude": 31.4056,
                },
                "activityPreferences": ["skiing"],
                "requiredActivities": ["skiing"],
                "regionPreferences": ["Europe"],
                "shortFlightPreference": True,
            }
        )
        _execute_destination_discovery_tool(
            TravelToolCall(
                tool="discover_destinations",
                arguments=initial_request.model_dump(by_alias=True, mode="json"),
            ),
            self.db,
            self.session,
            ChatRequest(message="I want skiing in Europe"),
        )

        response = _execute_tool_call(
            TravelToolCall(tool="general_help", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I don't want skiing in Europe"),
        )

        refined_request = load_session_memory(self.db, self.session.id)[
            "destination_discovery_request"
        ]
        self.assertNotIn("skiing", refined_request["requiredActivities"])
        self.assertEqual(refined_request["regionPreferences"], [])
        first_codes = [item["airportCode"] for item in response.results.destinations[:2]]
        self.assertNotEqual(first_codes, ["GVA", "LYS"])

    def test_destination_only_without_default_origin_asks_for_origin(self) -> None:
        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I want to go to London"),
        )

        self.assertIn("Where are you flying from", response.reply)

    def test_destination_only_with_default_origin_mentions_cai(self) -> None:
        save_default_origin(
            self.db,
            self.session.id,
            {
                "city": "Cairo",
                "country": "Egypt",
                "airportCode": "CAI",
                "airportName": "Cairo International Airport",
                "source": "browser_location",
            },
        )
        self.db.commit()

        response = _execute_tool_call(
            TravelToolCall(tool="search_flights", arguments={}),
            self.db,
            self.user,
            self.session,
            ChatRequest(message="I want to go to London"),
        )

        self.assertIn("CAI", response.reply)


if __name__ == "__main__":
    unittest.main()
