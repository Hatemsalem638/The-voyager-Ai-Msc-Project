import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from app.llama_service import (
    LLAMA_PLANNER_TOOL_CALL_JSON_SCHEMA,
    TRAVEL_TOOL_ROUTER_PROMPT,
)
from evaluation.run_ai_response_eval import (
    LIVE_REPORT_JSON,
    LIVE_REPORT_MD,
    LIVE_TRACE_JSONL,
    build_trace_diagnostics,
    calculate_metrics,
    check_expected_provider_calls,
    check_expected_state,
    check_forbidden_provider_calls,
    check_forbidden_text,
    expected_tools_for_turn,
    load_scenarios,
    run_ai_response_eval,
)


class AIResponseEvaluationTests(unittest.TestCase):
    def test_scenario_files_load_correctly(self) -> None:
        scenarios = load_scenarios()
        scenario_ids = {scenario["id"] for scenario in scenarios}

        self.assertIn("florida_birmingham_november_fresh", scenario_ids)
        self.assertIn("florida_birmingham_november_polluted", scenario_ids)
        self.assertIn("flexible_clear_flight_birmingham_paris_dates", scenario_ids)
        self.assertIn("flexible_visible_package_cheapest_and_second_cart", scenario_ids)
        self.assertTrue(all(scenario.get("turns") for scenario in scenarios))

    def test_expected_state_checks_nested_paths(self) -> None:
        active_state = {
            "origin": {"airportCode": "BHX"},
            "dates": {"departureDate": "2026-11-15"},
            "pendingTripRequest": {"intent": "package_search"},
        }

        passed, total, failures = check_expected_state(
            active_state,
            {
                "origin.airportCode": "BHX",
                "dates.departureDate": "2026-11-15",
                "pendingTripRequest.intent": "package_search",
            },
        )

        self.assertEqual((passed, total, failures), (3, 3, []))

    def test_trace_diagnostics_classify_pending_and_lost_fields(self) -> None:
        diagnostics = build_trace_diagnostics(
            before={
                "dates": {"departureDate": "2026-11-15"},
                "pendingAction": {
                    "type": "build_package",
                    "origin": "BHX",
                    "destination": "MCO",
                },
            },
            after={"dates": {"departureDate": None}, "pendingAction": None},
            expected_pending_action={
                "type": "build_package",
                "origin": "BHX",
                "destination": "MCO",
            },
            state_failures=["dates.departureDate: expected '2026-11-15', got None"],
            pending_failures=["pendingAction.type: expected 'build_package', got None"],
            illegal_provider_calls=[],
            expected_provider_failures=[],
            forbidden_text_hits=[],
            missing_text_hits=[],
        )

        codes = [diagnostic["code"] for diagnostic in diagnostics]
        self.assertIn("FIELD_LOST_UNEXPECTEDLY", codes)
        self.assertIn("PENDING_ACTION_LOST_OR_NOT_CREATED", codes)
        self.assertIn("PENDING_ACTION_FIELD_MISMATCH", codes)

    def test_forbidden_text_checks_are_case_insensitive(self) -> None:
        failures = check_forbidden_text(
            "I have Orlando International Airport (MCO) for 15 Nov 2026.",
            ["ISM", "15 Aug 2026"],
        )

        self.assertEqual(failures, [])
        self.assertEqual(check_forbidden_text("Route uses ISM.", ["ism"]), ["ism"])

    def test_forbidden_provider_call_checks(self) -> None:
        calls = [{"type": "flights"}, {"type": "hotels"}]

        self.assertEqual(
            check_forbidden_provider_calls(calls, ["flights"]),
            ["flights"],
        )
        self.assertEqual(check_forbidden_provider_calls(calls, ["packages"]), [])

    def test_expected_provider_call_checks(self) -> None:
        calls = [
            {
                "type": "flights",
                "origin": "BHX",
                "destination": "MCO",
                "departureDate": "2026-11-15",
                "returnDate": "2026-11-19",
            }
        ]

        self.assertEqual(
            check_expected_provider_calls(
                calls,
                [
                    {
                        "type": "flights",
                        "origin": "BHX",
                        "destination": "MCO",
                    }
                ],
            ),
            [],
        )
        self.assertTrue(
            check_expected_provider_calls(
                calls,
                [{"type": "flights", "origin": "BHX", "destination": "ISM"}],
            )
        )

    def test_phase3b_prompt_uses_prepare_trip_request_as_planner(self) -> None:
        prompt = TRAVEL_TOOL_ROUTER_PROMPT

        self.assertIn("prepare_trip_request is the default planner/state-update tool", prompt)
        self.assertIn('"requested_product":"flight"', prompt)
        self.assertIn('"requested_product":"hotel"', prompt)
        self.assertIn('"requested_product":"package"', prompt)
        self.assertIn("Direct flight/hotel provider search tools are not available to you", prompt)
        self.assertIn("pendingAction.type build_package", prompt)
        self.assertIn("handle_visible_results", prompt)
        self.assertIn("latestVisibleResultType packages", prompt)
        self.assertIn("i want go on a trip to florida from birmingham in november", prompt)
        self.assertIn('"tool":"prepare_trip_request"', prompt)
        self.assertIn('"tool":"handle_visible_results"', prompt)
        self.assertIn('"tool":"confirm_booking"', prompt)

    def test_phase3b_planner_schema_hides_direct_provider_search_tools(self) -> None:
        tool_enum = LLAMA_PLANNER_TOOL_CALL_JSON_SCHEMA["properties"]["tool"]["enum"]

        self.assertIn("prepare_trip_request", tool_enum)
        self.assertIn("handle_visible_results", tool_enum)
        self.assertIn("confirm_booking", tool_enum)
        self.assertNotIn("search_flights", tool_enum)
        self.assertNotIn("search_hotels", tool_enum)

    def test_expected_tools_and_confusion_metrics(self) -> None:
        self.assertEqual(
            expected_tools_for_turn({"expected_tools": ["prepare_trip_request", "search_hotels"]}),
            ["prepare_trip_request", "search_hotels"],
        )
        self.assertEqual(
            expected_tools_for_turn({"expected_tool": "confirm_booking"}),
            ["confirm_booking"],
        )

        traces = [
            {
                "scenario_id": "s1",
                "repeat_index": 1,
                "pass": False,
                "expected_tool": "prepare_trip_request",
                "expected_tools": ["prepare_trip_request"],
                "actual_tool": "search_flights",
                "tool_arguments": {"requested_product": "flight", "intent": "flight_search"},
                "tool_passed": False,
                "validation_errors": [],
                "state_checks": {
                    "passed": 0,
                    "total": 2,
                    "failures": ["month: expected '2026-11', got None"],
                },
                "expected_important_slots": {
                    "pendingTripRequest.intent": "package_search",
                },
                "pendingAction_checks": {"passed": 0, "total": 0, "failures": []},
                "illegal_provider_calls": [],
                "failure_category": "TOOL_INTENT",
            },
            {
                "scenario_id": "s1",
                "repeat_index": 2,
                "pass": True,
                "expected_tool": "prepare_trip_request",
                "expected_tools": ["prepare_trip_request"],
                "actual_tool": "prepare_trip_request",
                "tool_arguments": {"requested_product": "package", "intent": "package_search"},
                "tool_passed": True,
                "validation_errors": [],
                "state_checks": {"passed": 1, "total": 1, "failures": []},
                "expected_important_slots": {
                    "pendingTripRequest.intent": "package_search",
                },
                "pendingAction_checks": {"passed": 0, "total": 0, "failures": []},
                "illegal_provider_calls": [],
                "failure_category": None,
            },
        ]

        metrics = calculate_metrics(
            [{"id": "s1", "turns": [{"user_message": "x"}]}],
            traces,
        )

        self.assertEqual(
            metrics["tool_confusion_matrix"]["prepare_trip_request"]["search_flights"],
            1,
        )
        self.assertEqual(metrics["most_common_wrong_slots"][0][0], "month")
        self.assertEqual(metrics["repeat_stability_score"], 0.5)
        self.assertEqual(metrics["prepare_trip_request_planner_accuracy"], 0.5)
        self.assertEqual(metrics["requested_product_accuracy"], 0.5)
        self.assertIn("provider_execution_gate_accuracy", metrics)

    def test_florida_live_shaped_bad_output_is_corrected_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report = run_ai_response_eval(output_dir=Path(tmpdir))

        traces = report["traces"]
        florida_turn_two = next(
            trace
            for trace in traces
            if trace["scenario_id"] == "florida_birmingham_november_fresh"
            and trace["turn_index"] == 2
        )

        self.assertTrue(florida_turn_two["pass"])
        self.assertEqual(
            florida_turn_two["active_state_after"]["selectedDestination"]["airportCode"],
            "MCO",
        )
        self.assertEqual(
            florida_turn_two["active_state_after"]["dates"]["departureDate"],
            "2026-11-15",
        )
        self.assertIn(
            "active_month_corrected_dates",
            florida_turn_two["corrections_applied"],
        )
        self.assertIn(
            "active_region_candidate_resolved_destination",
            florida_turn_two["corrections_applied"],
        )

    def test_polluted_session_catches_stale_august_ism_contamination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report = run_ai_response_eval(output_dir=Path(tmpdir))

        polluted_first = next(
            trace
            for trace in report["traces"]
            if trace["scenario_id"] == "florida_birmingham_november_polluted"
            and trace["turn_index"] == 1
        )
        polluted_second = next(
            trace
            for trace in report["traces"]
            if trace["scenario_id"] == "florida_birmingham_november_polluted"
            and trace["turn_index"] == 2
        )

        self.assertTrue(polluted_first["pass"])
        self.assertIn(
            "stale_destination_cleared_or_corrected",
            polluted_first["corrections_applied"],
        )
        self.assertIn(
            "stale_dates_cleared_or_corrected",
            polluted_first["corrections_applied"],
        )
        self.assertTrue(polluted_second["pass"])
        self.assertEqual(
            polluted_second["active_state_after"]["selectedDestination"]["airportCode"],
            "MCO",
        )
        self.assertEqual(
            polluted_second["active_state_after"]["dates"]["departureDate"],
            "2026-11-15",
        )

    def test_live_llama_mode_uses_real_router_path_with_mocked_ollama(self) -> None:
        class FakeOllamaResponse:
            ok = True
            status_code = 200

            def json(self) -> dict:
                return {
                    "message": {
                        "content": json.dumps(
                            {
                                "tool": "prepare_trip_request",
                                "arguments": {
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
                                    "missing_fields": [
                                        "exact_dates",
                                        "destination_airport_choice",
                                    ],
                                },
                            }
                        )
                    }
                }

        scenario = [
            {
                "id": "single_live_router_turn",
                "description": "Small live-mode smoke test with mocked Ollama HTTP.",
                "initial_state": {
                    "default_origin": {
                        "city": "East Midlands",
                        "country": "United Kingdom",
                        "airportCode": "EMA",
                        "airportName": "East Midlands Airport",
                        "source": "evaluation_default_origin",
                    }
                },
                "turns": [
                    {
                        "user_message": "i want go on a trip to florida from birmingham in november",
                        "expected_tool": "prepare_trip_request",
                        "expected_active_state": {
                            "origin.airportCode": "BHX",
                            "destinationRegion.name": "Florida",
                            "month": "2026-11",
                        },
                        "expected_list_contains": {
                            "destinationCandidates[].airportCode": ["MCO", "MIA"],
                        },
                        "forbidden_provider_calls": ["flights", "hotels"],
                    }
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as scenario_dir, tempfile.TemporaryDirectory() as output_dir:
            scenario_path = Path(scenario_dir) / "live_smoke.json"
            scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
            with patch("app.llama_service.requests.post", return_value=FakeOllamaResponse()), patch(
                "evaluation.run_ai_response_eval.load_ollama_model_digest",
                return_value="fake-digest",
            ):
                report = run_ai_response_eval(
                    scenarios_dir=Path(scenario_dir),
                    output_dir=Path(output_dir),
                    live_llama=True,
                    repeats=2,
                )

            self.assertEqual(report["metadata"]["live_llama"], True)
            self.assertEqual(report["metadata"]["ollama_model_digest"], "fake-digest")
            self.assertEqual(report["metrics"]["total_turns"], 2)
            self.assertEqual(report["metrics"]["passed_turns"], 2)
            self.assertEqual(report["metrics"]["schema_validity"], 1.0)
            self.assertEqual(report["traces"][0]["raw_llama_response"].startswith("{"), True)
            self.assertEqual(report["traces"][0]["actual_tool"], "prepare_trip_request")
            self.assertIsNotNone(report["traces"][0]["safe_context_sent_to_llama"])
            self.assertTrue((Path(output_dir) / LIVE_REPORT_MD).exists())
            self.assertTrue((Path(output_dir) / LIVE_REPORT_JSON).exists())
            self.assertTrue((Path(output_dir) / LIVE_TRACE_JSONL).exists())


if __name__ == "__main__":
    unittest.main()
