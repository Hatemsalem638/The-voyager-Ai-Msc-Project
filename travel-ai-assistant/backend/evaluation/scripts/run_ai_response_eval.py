"""Replay Voyager AI-response scenarios through the real chat/state pipeline.

The default mode replays mocked live-shaped Llama outputs. ``--live-llama``
uses the real local Ollama router while still stubbing travel providers, so the
report separates model/tool errors from backend state and execution safety.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
EVALUATION_DIR = SCRIPT_DIR.parent if SCRIPT_DIR.name == "scripts" else SCRIPT_DIR
BACKEND_DIR = EVALUATION_DIR.parent
SCENARIOS_DIR = EVALUATION_DIR / "scenarios"
RESULTS_DIR = EVALUATION_DIR / "results"
LATEST_RUN_DIR = RESULTS_DIR / "latest"

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{(Path(tempfile.gettempdir()) / 'voyage_ai_response_eval.db').as_posix()}",
)
os.environ.setdefault("OLLAMA_MODEL", "llama3.1:8b-instruct-q4_0")
os.environ.setdefault("USE_DUFFEL_FLIGHTS", "false")
os.environ.setdefault("USE_LITEAPI_HOTELS", "false")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import Base, SessionLocal, create_db_and_tables, engine  # noqa: E402
from app.main import DEMO_USER_EMAIL, chat  # noqa: E402
from app.memory_store import load_active_trip, save_active_trip, save_default_origin  # noqa: E402
from app.models import ChatSession, User  # noqa: E402
from app.llama_service import (  # noqa: E402
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    TRAVEL_TOOL_ROUTER_PROMPT_VERSION,
    TRAVEL_TOOL_SCHEMA_VERSION,
    set_llama_tool_route_trace_hook,
)
from app.schemas import (  # noqa: E402
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


MOCK_REPORT_JSON = "evaluation_report.json"
MOCK_REPORT_MD = "evaluation_report.md"
MOCK_TRACE_JSONL = "failure_traces.jsonl"
LIVE_REPORT_JSON = "live_evaluation_report.json"
LIVE_REPORT_MD = "live_evaluation_report.md"
LIVE_TRACE_JSONL = "live_failure_traces.jsonl"
PHASE_2B_BASELINE_METRICS = {
    "label": "Before Phase 3A / Phase 3B baseline",
    "tool_accuracy": 0.5098,
    "joint_state_accuracy": 0.4706,
    "repeat_stability_score": 0.2308,
    "failure_category_counts": {
        "TOOL_INTENT": 25,
        "STATE_MERGE": 6,
        "PENDING_ACTION": 3,
    },
    "tool_confusion_matrix": {
        "prepare_trip_request": {
            "prepare_trip_request": 18,
            "search_flights": 9,
            "search_hotels": 9,
        },
        "confirm_booking": {
            "confirm_booking": 4,
            "general_help": 3,
            "prepare_trip_request": 2,
        },
        "search_hotels": {
            "search_hotels": 4,
            "prepare_trip_request": 2,
        },
    },
}

PHASE_3A_BASELINE_METRICS = {
    "label": "Before Phase 3B",
    "tool_accuracy": 0.5098,
    "joint_state_accuracy": 0.4706,
    "repeat_stability_score": 0.2308,
    "failure_category_counts": {
        "TOOL_INTENT": 25,
        "STATE_MERGE": 6,
        "PENDING_ACTION": 3,
    },
}


FAILURE_CATEGORIES = [
    "JSON_PARSE",
    "SCHEMA_ARGS",
    "TOOL_INTENT",
    "SLOT_EXTRACTION",
    "STATE_MERGE",
    "STALE_OVERRIDE",
    "STATE_PRESERVE",
    "PENDING_ACTION",
    "EXECUTION_GATE",
    "RESPONSE_STATE_MISMATCH",
    "MODEL_AVAILABILITY",
]


@dataclass
class ProviderCallRecorder:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def record_flight(self, request: FlightSearchRequest) -> FlightSearchResponse:
        departure = request.itinerary.dates.departure.isoformat()
        return_date = (
            request.itinerary.dates.return_date.isoformat()
            if request.itinerary.dates.return_date
            else None
        )
        origin = request.itinerary.origin.code.upper()
        destination = request.itinerary.destination.code.upper()
        self.calls.append(
            {
                "type": "flights",
                "origin": origin,
                "destination": destination,
                "departureDate": departure,
                "returnDate": return_date,
            }
        )
        flights = [
            NormalisedFlightResult(
                id=f"eval-flight-{origin}-{destination}-{index}",
                offer_id=f"eval-offer-{destination}-{index}",
                display_id=f"Offer {index}",
                option_number=index,
                origin=origin,
                destination=destination,
                airline="Voyage Airways" if index == 1 else "Atlas Air",
                outbound_departure_date=departure,
                outbound_departure_time=f"{7 + index:02d}:30",
                outbound_arrival_date=departure,
                outbound_arrival_time=f"{10 + index:02d}:00",
                return_departure_date=return_date,
                return_departure_time="13:10" if return_date else None,
                return_arrival_date=return_date,
                return_arrival_time="16:20" if return_date else None,
                departure_date=f"{departure}T{7 + index:02d}:30:00",
                return_date=f"{return_date}T13:10:00" if return_date else None,
                stops=0 if index == 1 else 1,
                stops_label="Non-stop" if index == 1 else "1 stop",
                duration_minutes=520 + index * 20,
                duration_label=f"{8 + index}h {index * 5}m",
                cabin_class=request.preferences.cabin_class,
                cabin_class_label=str(request.preferences.cabin_class).replace("_", " ").title(),
                passenger_count=request.passenger_count,
                price=f"{430 + index * 45:.2f}",
                currency="GBP",
                provider="duffel",
            )
            for index in range(1, 4)
        ]
        return FlightSearchResponse(provider_used="duffel", flights=flights)

    def record_hotel(self, request: HotelSearchRequest) -> HotelSearchResponse:
        city = request.destination.city or "Selected city"
        check_in = request.check_in_date.isoformat()
        check_out = request.check_out_date.isoformat()
        nights = max((request.check_out_date - request.check_in_date).days, 1)
        self.calls.append(
            {
                "type": "hotels",
                "city": city,
                "checkInDate": check_in,
                "checkOutDate": check_out,
            }
        )
        hotels = [
            NormalisedHotelResult(
                id=f"eval-hotel-{city.lower()}-{index}",
                option_number=index,
                hotel_name=f"{city} Central Hotel {index}",
                location=city,
                check_in_date=check_in,
                check_out_date=check_out,
                nights=nights,
                guest_count=request.guest_count,
                room_count=request.room_count,
                rating=4.6 - index * 0.2,
                board_type="Breakfast",
                room_name="Standard room",
                refundable=index == 1,
                price=float(125 * nights + index * 35),
                currency="GBP",
                provider="liteapi",
                short_id=f"EHTL{index}",
                amenities=["wifi", "breakfast"],
            )
            for index in range(1, 4)
        ]
        return HotelSearchResponse(provider_used="liteapi", results=hotels)

    def record_destination(
        self,
        request: DestinationDiscoveryRequest,
    ) -> DestinationDiscoveryResponse:
        departure = None
        return_date = None
        if isinstance(request.dates, dict):
            departure = request.dates.get("departure")
            return_date = request.dates.get("return")
        origin_code = request.origin.code if request.origin else None
        self.calls.append(
            {
                "type": "destinations",
                "origin": origin_code,
                "departureDate": departure,
                "returnDate": return_date,
            }
        )
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


def current_ollama_settings() -> tuple[str, str]:
    base_url = os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).strip() or DEFAULT_OLLAMA_BASE_URL
    model = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL
    return base_url, model


def load_ollama_model_digest(base_url: str, model_name: str) -> str | None:
    try:
        response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        if not response.ok:
            return None
        for model in response.json().get("models", []):
            if model.get("name") == model_name:
                digest = model.get("digest")
                return str(digest) if digest else None
    except requests.RequestException:
        return None
    except (TypeError, ValueError):
        return None
    return None


def model_metadata(*, live_llama: bool) -> dict[str, Any]:
    base_url, model = current_ollama_settings()
    return {
        "live_llama": live_llama,
        "model_name": model,
        "ollama_base_url": base_url,
        "ollama_model_digest": load_ollama_model_digest(base_url, model) if live_llama else None,
        "prompt_version": TRAVEL_TOOL_ROUTER_PROMPT_VERSION,
        "schema_version": TRAVEL_TOOL_SCHEMA_VERSION,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


def failure_recommendation(category: str | None) -> str:
    recommendations = {
        "TOOL_INTENT": "Prompt/tool description issue: add clearer tool-choice examples or descriptions.",
        "SCHEMA_ARGS": "Discriminated tool schema issue: tighten argument shape or schema guidance.",
        "SLOT_EXTRACTION": "Prompt examples or schema descriptions: the model missed or distorted travel slots.",
        "STATE_MERGE": "Backend invariant issue: structured fields were not merged into active trip correctly.",
        "STALE_OVERRIDE": "Backend invariant issue: stale state overrode a newer validated value.",
        "PENDING_ACTION": "PendingAction state machine issue: confirmation or queued action was not preserved/executed.",
        "EXECUTION_GATE": "Provider safety gate issue: provider execution did not match expected safe routing.",
        "MODEL_AVAILABILITY": "Ollama/model config issue: local model was unavailable or rejected.",
        "JSON_PARSE": "Prompt/output format issue: model did not return parseable JSON.",
        "RESPONSE_STATE_MISMATCH": "Response formatting issue: text did not match the validated state.",
        "STATE_PRESERVE": "Backend invariant issue: required existing state was not preserved.",
    }
    return recommendations.get(category or "", "No action needed.")


def _important_slots_from_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": get_path(state, "pendingTripRequest.intent"),
        "origin": get_path(state, "origin.airportCode"),
        "destinationRegion": get_path(state, "destinationRegion.name"),
        "selectedDestination": get_path(state, "selectedDestination.airportCode"),
        "month": get_path(state, "month"),
        "departureDate": get_path(state, "dates.departureDate"),
        "returnDate": get_path(state, "dates.returnDate"),
        "nights": get_path(state, "tripDurationNights"),
        "pendingAction": get_path(state, "pendingAction.type"),
        "latestVisibleResultType": get_path(state, "latestVisibleResultType"),
    }


def _important_expected_slots(turn: dict[str, Any]) -> dict[str, Any]:
    combined = dict(turn.get("expected_active_state") or {})
    if turn.get("expected_pendingAction") is None and "expected_pendingAction" in turn:
        combined["pendingAction"] = None
    elif isinstance(turn.get("expected_pendingAction"), dict):
        combined.update(
            {
                f"pendingAction.{key}": value
                for key, value in turn["expected_pendingAction"].items()
            }
        )
    for path, values in (turn.get("expected_list_contains") or {}).items():
        combined[f"{path} includes"] = values
    return combined


def _expected_requested_product(trace: dict[str, Any]) -> str | None:
    expected_slots = trace.get("expected_important_slots")
    expected_intent = (
        expected_slots.get("pendingTripRequest.intent")
        if isinstance(expected_slots, dict)
        else None
    )
    if expected_intent == "package_search":
        return "package"
    if expected_intent == "flight_search":
        return "flight"
    if expected_intent == "hotel_search":
        return "hotel"
    provider_calls = trace.get("expected_provider_calls")
    if isinstance(provider_calls, list):
        provider_types = {
            str(call.get("type"))
            for call in provider_calls
            if isinstance(call, dict)
        }
        if provider_types == {"flights"}:
            return "flight"
        if provider_types == {"hotels"}:
            return "hotel"
        if {"flights", "hotels"}.issubset(provider_types):
            return "package"
    return None


def _actual_requested_product(trace: dict[str, Any]) -> str | None:
    args = trace.get("tool_arguments")
    if not isinstance(args, dict):
        return None
    product = args.get("requested_product") or args.get("requestedProduct") or args.get("product")
    product_text = str(product or "").strip().lower()
    if product_text in {"package", "packages", "trip", "holiday", "flight_hotel", "flight+hotel"}:
        return "package"
    if product_text in {"flight", "flights", "airfare"}:
        return "flight"
    if product_text in {"hotel", "hotels", "stay", "stays", "accommodation"}:
        return "hotel"
    intent = args.get("intent")
    if intent == "package_search":
        return "package"
    if intent == "flight_search":
        return "flight"
    if intent == "hotel_search":
        return "hotel"
    return None


def _pattern_for_failure(trace: dict[str, Any]) -> str:
    expected = set(trace.get("expected_tools") or ([trace.get("expected_tool")] if trace.get("expected_tool") else []))
    actual = trace.get("actual_tool")
    user_message = str(trace.get("user_message") or "").lower()
    before = trace.get("active_state_before") or {}
    after = trace.get("active_state_after") or {}
    expected_intent = get_path(after, "pendingTripRequest.intent") or get_path(before, "pendingTripRequest.intent")
    if "confirm_booking" in expected and actual not in expected:
        return "confirmation wrongly routed to clarification/general_help"
    if "prepare_trip_request" in expected and actual == "search_flights":
        return "package/trip request wrongly routed to flight_search"
    if "prepare_trip_request" in expected and actual == "search_hotels":
        return "package/trip request wrongly routed to search_hotels"
    if get_path(after, "destinationType") == "airport" and get_path(before, "destinationRegion.name"):
        return "broad destination wrongly treated as exact airport"
    if get_path(before, "pendingTripRequest") and actual not in expected:
        return "active-state follow-up ignored"
    if any(slot in " ".join(trace.get("state_checks", {}).get("failures", [])) for slot in ["departureDate", "returnDate", "month"]):
        return "follow-up details wrongly treated as new search"
    if expected_intent == "package_search" and actual not in expected:
        return "package/trip request wrongly routed to another tool"
    if "orlando" in user_message and get_path(after, "selectedDestination.airportCode") == "ISM":
        return "broad destination wrongly treated as exact airport"
    return "other routing/state mismatch"


def _likely_cause_for_trace(trace: dict[str, Any]) -> str:
    pattern = _pattern_for_failure(trace)
    if trace.get("failure_category") == "TOOL_INTENT":
        if pattern == "package/trip request wrongly routed to flight_search":
            return "The router treated a broad package/month request as an executable flight search and converted the month into fixed dates."
        if pattern == "confirmation wrongly routed to clarification/general_help":
            return "The router did not use active_trip.pendingAction/use_active_trip_state for a confirmation turn."
        if pattern == "active-state follow-up ignored":
            return "The router did not preserve the pending trip context while extracting follow-up slots."
        if pattern == "broad destination wrongly treated as exact airport":
            return "The router or merge path lost the broad destination candidate context before resolving the city."
        return "The selected tool does not match the expected dialogue state transition."
    if trace.get("failure_category") in {"STATE_MERGE", "STALE_OVERRIDE"}:
        return "The backend state merge/invariant did not preserve or correct the validated structured slots."
    if trace.get("failure_category") == "PENDING_ACTION":
        return "The pending action was not created, preserved, or executed as expected."
    return failure_recommendation(trace.get("failure_category"))


def reset_database() -> None:
    Base.metadata.drop_all(bind=engine)
    create_db_and_tables()


def load_scenarios(scenarios_dir: Path = SCENARIOS_DIR) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for path in sorted(scenarios_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            scenarios.extend(data)
        elif isinstance(data, dict):
            scenarios.append(data)
    return scenarios


def create_eval_session(initial_state: dict[str, Any] | None = None) -> int:
    initial_state = initial_state or {}
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == DEMO_USER_EMAIL).first()
        if user is None:
            user = User(
                display_name="AI Response Eval User",
                full_name="AI Response Eval User",
                email=DEMO_USER_EMAIL,
                password_hash="",
            )
            db.add(user)
            db.flush()
        session = ChatSession(user_id=user.id, title="AI response eval")
        db.add(session)
        db.flush()
        default_origin = initial_state.get("default_origin") or {
            "city": "East Midlands",
            "country": "United Kingdom",
            "airportCode": "EMA",
            "airportName": "East Midlands Airport",
            "source": "evaluation_default_origin",
        }
        save_default_origin(db, session.id, default_origin)
        if isinstance(initial_state.get("active_trip"), dict):
            save_active_trip(db, session.id, initial_state["active_trip"])
        db.commit()
        return int(session.id)
    finally:
        db.close()


def get_path(value: Any, path: str) -> Any:
    current = value
    if path == "":
        return current
    for part in path.split("."):
        if part.endswith("[]"):
            key = part[:-2]
            if not isinstance(current, dict):
                return None
            current = current.get(key)
            if not isinstance(current, list):
                return None
            return current
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def get_list_path_values(value: Any, path: str) -> list[Any]:
    if "[]." not in path:
        found = get_path(value, path)
        return found if isinstance(found, list) else [found]
    list_path, item_path = path.split("[].", 1)
    items = get_path(value, f"{list_path}[]")
    if not isinstance(items, list):
        return []
    return [get_path(item, item_path) for item in items]


def state_diff(before: Any, after: Any, prefix: str = "") -> dict[str, dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        diff: dict[str, dict[str, Any]] = {}
        for key in sorted(set(before) | set(after)):
            child_prefix = f"{prefix}.{key}" if prefix else key
            diff.update(state_diff(before.get(key), after.get(key), child_prefix))
        return diff
    if before != after:
        return {prefix: {"before": before, "after": after}}
    return {}


def validate_tool_output(raw_output: Any) -> tuple[TravelToolCall | None, list[str]]:
    try:
        payload = json.loads(raw_output) if isinstance(raw_output, str) else raw_output
    except json.JSONDecodeError as exc:
        return None, [f"JSON_PARSE: {exc}"]
    try:
        return TravelToolCall.model_validate(payload), []
    except Exception as exc:  # Pydantic v1/v2 compatible enough for evaluator traces.
        errors = getattr(exc, "errors", lambda: str(exc))()
        return None, [f"SCHEMA_ARGS: {errors}"]


def check_expected_state(
    active_state: dict[str, Any],
    expected: dict[str, Any] | None,
) -> tuple[int, int, list[str]]:
    if not expected:
        return 0, 0, []
    total = 0
    passed = 0
    failures: list[str] = []
    for path, expected_value in expected.items():
        total += 1
        actual_value = get_path(active_state, path)
        if actual_value == expected_value:
            passed += 1
        else:
            failures.append(f"{path}: expected {expected_value!r}, got {actual_value!r}")
    return passed, total, failures


def check_list_contains(
    active_state: dict[str, Any],
    expected: dict[str, list[Any]] | None,
) -> tuple[int, int, list[str]]:
    if not expected:
        return 0, 0, []
    total = 0
    passed = 0
    failures: list[str] = []
    for path, expected_values in expected.items():
        actual_values = get_list_path_values(active_state, path)
        for expected_value in expected_values:
            total += 1
            if expected_value in actual_values:
                passed += 1
            else:
                failures.append(f"{path}: expected to include {expected_value!r}, got {actual_values!r}")
    return passed, total, failures


def check_cleared_fields(
    before: dict[str, Any],
    after: dict[str, Any],
    fields: list[str] | None,
) -> tuple[int, int, list[str]]:
    if not fields:
        return 0, 0, []
    total = 0
    passed = 0
    failures: list[str] = []
    for path in fields:
        total += 1
        after_value = get_path(after, path)
        before_value = get_path(before, path)
        if after_value in (None, [], {}):
            passed += 1
        else:
            failures.append(f"{path}: expected cleared, before {before_value!r}, after {after_value!r}")
    return passed, total, failures


def check_pending_action(
    active_state: dict[str, Any],
    expected: dict[str, Any] | None,
) -> tuple[int, int, list[str]]:
    pending = active_state.get("pendingAction")
    if expected is None:
        return (1, 1, []) if pending is None else (0, 1, [f"pendingAction: expected None, got {pending!r}"])
    return check_expected_state({"pendingAction": pending}, {f"pendingAction.{k}": v for k, v in expected.items()})


def check_forbidden_text(reply: str, forbidden: list[str] | None) -> list[str]:
    if not forbidden:
        return []
    lowered = reply.lower()
    return [text for text in forbidden if text.lower() in lowered]


def check_required_text(reply: str, required: list[str] | None) -> list[str]:
    if not required:
        return []
    lowered = reply.lower()
    return [text for text in required if text.lower() not in lowered]


def check_forbidden_provider_calls(
    calls: list[dict[str, Any]],
    forbidden: list[str] | None,
) -> list[str]:
    if not forbidden:
        return []
    forbidden_set = set(forbidden)
    return [call["type"] for call in calls if call.get("type") in forbidden_set]


def check_expected_provider_calls(
    calls: list[dict[str, Any]],
    expected: list[dict[str, Any]] | None,
) -> list[str]:
    if not expected:
        return []
    failures: list[str] = []
    for expected_call in expected:
        matched = False
        for actual_call in calls:
            if all(actual_call.get(key) == value for key, value in expected_call.items()):
                matched = True
                break
        if not matched:
            failures.append(f"missing provider call {expected_call!r}; actual {calls!r}")
    return failures


def expected_tools_for_turn(turn: dict[str, Any]) -> list[str]:
    if isinstance(turn.get("expected_tools"), list):
        return [str(tool) for tool in turn["expected_tools"]]
    if turn.get("expected_tool"):
        return [str(turn["expected_tool"])]
    return []


def infer_corrections(raw_output: Any, before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    corrections: list[str] = []
    arguments = raw_output.get("arguments", {}) if isinstance(raw_output, dict) else {}
    raw_dates = [
        str(arguments.get(key) or "")
        for key in ("departure_date", "return_date", "check_in_date", "check_out_date")
    ]
    after_departure = get_path(after, "dates.departureDate")
    after_return = get_path(after, "dates.returnDate")
    if any(date_text.startswith("2026-08") for date_text in raw_dates) and (
        str(after_departure).startswith("2026-11") or str(after_return).startswith("2026-11")
    ):
        corrections.append("active_month_corrected_dates")
    if arguments.get("destination_city") == "Orlando" and get_path(after, "selectedDestination.airportCode") == "MCO":
        corrections.append("active_region_candidate_resolved_destination")
    if get_path(before, "selectedDestination.airportCode") == "ISM" and get_path(after, "selectedDestination.airportCode") in (None, "MCO"):
        corrections.append("stale_destination_cleared_or_corrected")
    if str(get_path(before, "dates.departureDate")).startswith("2026-08") and (
        get_path(after, "dates.departureDate") is None
        or str(get_path(after, "dates.departureDate")).startswith("2026-11")
    ):
        corrections.append("stale_dates_cleared_or_corrected")
    return corrections


def classify_failure(
    *,
    validation_errors: list[str],
    tool_ok: bool,
    state_failures: list[str],
    pending_failures: list[str],
    illegal_provider_calls: list[str],
    expected_provider_failures: list[str],
    forbidden_text_hits: list[str],
    missing_text_hits: list[str],
) -> str | None:
    if validation_errors:
        return "JSON_PARSE" if validation_errors[0].startswith("JSON_PARSE") else "SCHEMA_ARGS"
    if not tool_ok:
        return "TOOL_INTENT"
    if illegal_provider_calls or expected_provider_failures:
        return "EXECUTION_GATE"
    if pending_failures:
        return "PENDING_ACTION"
    stale_terms = ("ISM", "2026-08", "August", "15 Aug", "19 Aug")
    if any(any(term in failure for term in stale_terms) for failure in state_failures):
        return "STALE_OVERRIDE"
    if state_failures:
        return "STATE_MERGE"
    if forbidden_text_hits or missing_text_hits:
        return "RESPONSE_STATE_MISMATCH"
    return None


def build_trace_diagnostics(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    expected_pending_action: dict[str, Any] | None,
    state_failures: list[str],
    pending_failures: list[str],
    illegal_provider_calls: list[str],
    expected_provider_failures: list[str],
    forbidden_text_hits: list[str],
    missing_text_hits: list[str],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for failure in state_failures:
        field = failure.split(":", 1)[0]
        before_value = get_path(before, field)
        after_value = get_path(after, field)
        if "expected cleared" in failure:
            code = "STALE_FIELD_NOT_CLEARED"
        elif after_value is None and before_value is not None:
            code = "FIELD_LOST_UNEXPECTEDLY"
        elif after_value is None:
            code = "FIELD_MISSING_AFTER_MERGE"
        else:
            code = "FIELD_MISMATCH_AFTER_MERGE"
        diagnostics.append(
            {
                "code": code,
                "field": field,
                "before": before_value,
                "after": after_value,
                "detail": failure,
            }
        )

    pending_after = after.get("pendingAction")
    if isinstance(expected_pending_action, dict):
        if not isinstance(pending_after, dict):
            diagnostics.append(
                {
                    "code": "PENDING_ACTION_LOST_OR_NOT_CREATED",
                    "expected": expected_pending_action,
                    "actual": pending_after,
                }
            )
        elif pending_after.get("type") == "build_package":
            required_fields = [
                "origin",
                "destination",
                "destinationCity",
                "departureDate",
                "returnDate",
                "passengerCount",
                "requestedProduct",
            ]
            missing_fields = [
                field
                for field in required_fields
                if pending_after.get(field) in (None, "", [])
            ]
            if pending_after.get("tripDurationNights") in (None, "", []):
                missing_fields.append("tripDurationNights")
            if missing_fields:
                diagnostics.append(
                    {
                        "code": "PENDING_ACTION_CREATED_WITH_MISSING_FIELDS",
                        "missingFields": missing_fields,
                        "actual": pending_after,
                    }
                )
    for failure in pending_failures:
        diagnostics.append({"code": "PENDING_ACTION_FIELD_MISMATCH", "detail": failure})
    for call_type in illegal_provider_calls:
        diagnostics.append({"code": "FORBIDDEN_PROVIDER_CALL", "providerCall": call_type})
    for failure in expected_provider_failures:
        diagnostics.append({"code": "EXPECTED_PROVIDER_CALL_MISSING", "detail": failure})
    for text in forbidden_text_hits:
        diagnostics.append({"code": "FORBIDDEN_RESPONSE_TEXT", "text": text})
    for text in missing_text_hits:
        diagnostics.append({"code": "REQUIRED_RESPONSE_TEXT_MISSING", "text": text})
    return diagnostics


def run_turn(
    scenario_id: str,
    turn_index: int,
    session_id: int,
    turn: dict[str, Any],
    recorder: ProviderCallRecorder,
    *,
    live_llama: bool = False,
    repeat_index: int = 1,
) -> dict[str, Any]:
    db = SessionLocal()
    llama_events: list[dict[str, Any]] = []
    try:
        before = load_active_trip(db, session_id)
        pending_before = before.get("pendingAction")
        raw_llama_output = deepcopy(turn.get("mocked_llama_output"))
        tool_call: TravelToolCall | None = None
        validation_errors: list[str] = []
        actual_tool: str | None = None
        selected_llama_event: dict[str, Any] | None = None
        calls_before = len(recorder.calls)

        if live_llama:
            def trace_hook(event: dict[str, Any]) -> None:
                llama_events.append(deepcopy(event))

            set_llama_tool_route_trace_hook(trace_hook)
            try:
                response = chat(
                    ChatRequest(message=turn["user_message"], sessionId=session_id),
                    db=db,
                    current_user=None,
                )
            finally:
                set_llama_tool_route_trace_hook(None)
            valid_events = [event for event in llama_events if event.get("schema_valid")]
            selected_llama_event = valid_events[-1] if valid_events else (llama_events[-1] if llama_events else None)
            if selected_llama_event is not None:
                raw_llama_output = selected_llama_event.get("parsed_json")
                validation_errors = list(selected_llama_event.get("validation_errors") or [])
                actual_tool = selected_llama_event.get("selected_tool")
                if raw_llama_output is not None:
                    tool_call, secondary_errors = validate_tool_output(raw_llama_output)
                    if secondary_errors and not validation_errors:
                        validation_errors = secondary_errors
            elif expected_tools_for_turn(turn):
                validation_errors = ["MODEL_AVAILABILITY: no Llama router trace was captured"]
        else:
            tool_call, validation_errors = validate_tool_output(raw_llama_output)
            actual_tool = tool_call.tool if tool_call else None
            clarification_payload = turn.get("mocked_clarification_answer")
            clarification_answer = (
                ClarificationAnswer.model_validate(clarification_payload)
                if isinstance(clarification_payload, dict)
                else None
            )

            def mocked_router(*_args: Any, **_kwargs: Any) -> TravelToolCall | None:
                return tool_call

            def mocked_clarification(*_args: Any, **_kwargs: Any) -> ClarificationAnswer | None:
                return clarification_answer

            with patch("app.services.chat_service.route_tool_with_llama", side_effect=mocked_router), patch(
                "app.services.chat_service.resolve_clarification_answer_with_llama",
                side_effect=mocked_clarification,
            ):
                response = chat(
                    ChatRequest(message=turn["user_message"], sessionId=session_id),
                    db=db,
                    current_user=None,
                )

        tool_ok = (
            not validation_errors
            and (
                not expected_tools_for_turn(turn)
                or (actual_tool is not None and actual_tool in expected_tools_for_turn(turn))
            )
        )
        db.commit()
        after = load_active_trip(db, session_id)
    finally:
        db.close()

    turn_calls = recorder.calls[calls_before:]
    response_payload = response.model_dump(by_alias=True, mode="json")
    reply = str(response_payload.get("reply") or "")
    state_passed, state_total, state_failures = check_expected_state(
        after,
        turn.get("expected_active_state"),
    )
    list_passed, list_total, list_failures = check_list_contains(
        after,
        turn.get("expected_list_contains"),
    )
    cleared_passed, cleared_total, cleared_failures = check_cleared_fields(
        before,
        after,
        turn.get("expected_cleared_fields"),
    )
    pending_passed, pending_total, pending_failures = check_pending_action(
        after,
        turn.get("expected_pendingAction"),
    )
    forbidden_text_hits = check_forbidden_text(reply, turn.get("forbidden_response_text"))
    missing_text_hits = check_required_text(reply, turn.get("expected_response_contains"))
    illegal_provider_calls = check_forbidden_provider_calls(turn_calls, turn.get("forbidden_provider_calls"))
    expected_provider_failures = check_expected_provider_calls(turn_calls, turn.get("expected_provider_calls"))

    all_state_failures = state_failures + list_failures + cleared_failures
    passed = (
        not validation_errors
        and tool_ok
        and not all_state_failures
        and not pending_failures
        and not forbidden_text_hits
        and not missing_text_hits
        and not illegal_provider_calls
        and not expected_provider_failures
    )
    failure_category = classify_failure(
        validation_errors=validation_errors,
        tool_ok=tool_ok,
        state_failures=all_state_failures,
        pending_failures=pending_failures,
        illegal_provider_calls=illegal_provider_calls,
        expected_provider_failures=expected_provider_failures,
        forbidden_text_hits=forbidden_text_hits,
        missing_text_hits=missing_text_hits,
    )
    if live_llama and selected_llama_event and selected_llama_event.get("failure_category"):
        failure_category = failure_category or selected_llama_event.get("failure_category")
    diagnostics = build_trace_diagnostics(
        before=before,
        after=after,
        expected_pending_action=turn.get("expected_pendingAction"),
        state_failures=all_state_failures,
        pending_failures=pending_failures,
        illegal_provider_calls=illegal_provider_calls,
        expected_provider_failures=expected_provider_failures,
        forbidden_text_hits=forbidden_text_hits,
        missing_text_hits=missing_text_hits,
    )
    trace = {
        "scenario_id": scenario_id,
        "repeat_index": repeat_index,
        "turn_index": turn_index,
        "user_message": turn["user_message"],
        "live_llama": live_llama,
        "safe_context_sent_to_llama": (
            selected_llama_event.get("safe_context_sent_to_llama")
            if selected_llama_event
            else None
        ),
        "raw_llama_response": (
            selected_llama_event.get("raw_llama_response")
            if selected_llama_event
            else None
        ),
        "parsed_llama_json": (
            selected_llama_event.get("parsed_json")
            if selected_llama_event
            else raw_llama_output
        ),
        "tool_arguments": (
            selected_llama_event.get("tool_arguments")
            if selected_llama_event
            else (raw_llama_output.get("arguments") if isinstance(raw_llama_output, dict) else None)
        ),
        "schema_validation_result": {
            "valid": not validation_errors,
            "errors": validation_errors,
        },
        "llama_router_events": llama_events,
        "raw_llama_output": raw_llama_output,
        "validation_errors": validation_errors,
        "expected_tool": turn.get("expected_tool"),
        "expected_tools": expected_tools_for_turn(turn),
        "actual_tool": actual_tool,
        "tool_passed": tool_ok,
        "expected_important_slots": _important_expected_slots(turn),
        "actual_important_slots": _important_slots_from_state(after),
        "active_state_before": before,
        "active_state_after": after,
        "state_diff": state_diff(before, after),
        "corrections_applied": infer_corrections(raw_llama_output, before, after),
        "pendingAction_before": pending_before,
        "pendingAction_after": after.get("pendingAction"),
        "selected_handler": response_payload.get("intent", {}).get("intent_type"),
        "provider_calls_attempted": turn_calls,
        "response_text": reply,
        "state_checks": {
            "passed": state_passed + list_passed + cleared_passed,
            "total": state_total + list_total + cleared_total,
            "failures": all_state_failures,
        },
        "pendingAction_checks": {
            "passed": pending_passed,
            "total": pending_total,
            "failures": pending_failures,
        },
        "forbidden_text_hits": forbidden_text_hits,
        "missing_required_text": missing_text_hits,
        "illegal_provider_calls": illegal_provider_calls,
        "expected_provider_call_failures": expected_provider_failures,
        "diagnostics": diagnostics,
        "pass": passed,
        "failure_category": failure_category,
    }
    if not passed:
        trace["wrong_tool_pattern"] = _pattern_for_failure(trace)
        trace["likely_cause"] = _likely_cause_for_trace(trace)
    else:
        trace["wrong_tool_pattern"] = None
        trace["likely_cause"] = None
    return trace


def run_scenario(
    scenario: dict[str, Any],
    *,
    live_llama: bool = False,
    repeat_index: int = 1,
) -> list[dict[str, Any]]:
    reset_database()
    session_id = create_eval_session(scenario.get("initial_state"))
    recorder = ProviderCallRecorder()
    traces: list[dict[str, Any]] = []
    with patch("app.services.chat_service._search_flights_with_fallback", side_effect=recorder.record_flight), patch(
        "app.services.chat_service._search_hotels_with_fallback",
        side_effect=recorder.record_hotel,
    ), patch(
        "app.services.chat_service.discover_destinations",
        side_effect=recorder.record_destination,
    ), patch("app.services.chat_service.today_utc_date") as mocked_today:
        mocked_today.return_value = datetime(2026, 8, 12).date()
        for index, turn in enumerate(scenario.get("turns", []), start=1):
            traces.append(
                run_turn(
                    scenario["id"],
                    index,
                    session_id,
                    turn,
                    recorder,
                    live_llama=live_llama,
                    repeat_index=repeat_index,
                )
            )
    return traces


def calculate_metrics(scenarios: list[dict[str, Any]], traces: list[dict[str, Any]]) -> dict[str, Any]:
    total_turns = len(traces)
    passed_turns = sum(1 for trace in traces if trace["pass"])
    expected_tool_turns = [trace for trace in traces if trace.get("expected_tools") or trace.get("expected_tool")]
    tool_accuracy = (
        sum(1 for trace in expected_tool_turns if trace["tool_passed"]) / len(expected_tool_turns)
        if expected_tool_turns
        else 0
    )
    state_total = sum(trace["state_checks"]["total"] for trace in traces)
    state_passed = sum(trace["state_checks"]["passed"] for trace in traces)
    joint_state_turns = [trace for trace in traces if trace["state_checks"]["total"]]
    joint_state_accuracy = (
        sum(1 for trace in joint_state_turns if not trace["state_checks"]["failures"]) / len(joint_state_turns)
        if joint_state_turns
        else 0
    )
    pending_total = sum(trace["pendingAction_checks"]["total"] for trace in traces)
    pending_passed = sum(trace["pendingAction_checks"]["passed"] for trace in traces)
    illegal_provider_count = sum(len(trace["illegal_provider_calls"]) for trace in traces)
    provider_gate_turns_passed = sum(
        1
        for trace in traces
        if not trace.get("illegal_provider_calls")
        and not trace.get("expected_provider_call_failures")
    )
    prepare_trip_turns = [trace for trace in traces if trace.get("actual_tool") == "prepare_trip_request"]
    expected_prepare_turns = [
        trace
        for trace in traces
        if "prepare_trip_request" in (trace.get("expected_tools") or [])
        or trace.get("expected_tool") == "prepare_trip_request"
    ]
    requested_product_turns = [
        trace
        for trace in traces
        if _expected_requested_product(trace) is not None
    ]
    requested_product_passed = sum(
        1
        for trace in requested_product_turns
        if _actual_requested_product(trace) == _expected_requested_product(trace)
    )
    confusion: dict[str, dict[str, int]] = {}
    for trace in traces:
        expected_key = "|".join(trace.get("expected_tools") or [trace.get("expected_tool") or "-"])
        actual_key = str(trace.get("actual_tool") or "-")
        confusion.setdefault(expected_key, {})
        confusion[expected_key][actual_key] = confusion[expected_key].get(actual_key, 0) + 1
    category_counts = Counter(
        trace["failure_category"] for trace in traces if trace.get("failure_category")
    )
    wrong_tools = Counter(
        str(trace.get("actual_tool"))
        for trace in traces
        if (trace.get("expected_tools") or trace.get("expected_tool")) and not trace.get("tool_passed")
    )
    wrong_slots = Counter()
    for trace in traces:
        for failure in trace.get("state_checks", {}).get("failures", []):
            wrong_slots.update([failure.split(":", 1)[0]])
    json_parse_turns = sum(
        1
        for trace in traces
        if any(str(error).startswith("JSON_PARSE") for error in trace.get("validation_errors", []))
    )
    schema_valid_turns = sum(1 for trace in traces if not trace.get("validation_errors"))
    per_scenario: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        scenario_traces = [trace for trace in traces if trace["scenario_id"] == scenario["id"]]
        per_scenario[scenario["id"]] = {
            "turns": len(scenario_traces),
            "pass_count": sum(1 for trace in scenario_traces if trace["pass"]),
            "fail_count": sum(1 for trace in scenario_traces if not trace["pass"]),
            "pass_rate": round(
                sum(1 for trace in scenario_traces if trace["pass"]) / len(scenario_traces),
                4,
            )
            if scenario_traces
            else 0,
            "most_common_wrong_tool": Counter(
                str(trace.get("actual_tool"))
                for trace in scenario_traces
                if (trace.get("expected_tools") or trace.get("expected_tool")) and not trace.get("tool_passed")
            ).most_common(1),
            "most_common_wrong_slots": Counter(
                failure.split(":", 1)[0]
                for trace in scenario_traces
                for failure in trace.get("state_checks", {}).get("failures", [])
            ).most_common(5),
        }
    scenario_repeat_pass: dict[str, dict[int, bool]] = {}
    for trace in traces:
        scenario_repeat_pass.setdefault(trace["scenario_id"], {})
        repeat = int(trace.get("repeat_index") or 1)
        scenario_repeat_pass[trace["scenario_id"]].setdefault(repeat, True)
        scenario_repeat_pass[trace["scenario_id"]][repeat] = (
            scenario_repeat_pass[trace["scenario_id"]][repeat] and bool(trace["pass"])
        )
    repeat_outcomes = [
        outcome
        for repeat_map in scenario_repeat_pass.values()
        for outcome in repeat_map.values()
    ]
    repeat_stability_score = (
        sum(1 for outcome in repeat_outcomes if outcome) / len(repeat_outcomes)
        if repeat_outcomes
        else 0
    )
    pattern_counts = Counter(
        _pattern_for_failure(trace)
        for trace in traces
        if not trace.get("pass")
    )
    return {
        "total_scenarios": len(scenarios),
        "total_turns": total_turns,
        "passed_turns": passed_turns,
        "failed_turns": total_turns - passed_turns,
        "json_parse_rate": round((total_turns - json_parse_turns) / total_turns, 4) if total_turns else 0,
        "schema_validity": round(schema_valid_turns / total_turns, 4) if total_turns else 0,
        "tool_accuracy": round(tool_accuracy, 4),
        "slot_state_accuracy": round(state_passed / state_total, 4) if state_total else 0,
        "joint_state_accuracy": round(joint_state_accuracy, 4),
        "prepare_trip_request_usage_rate": round(len(prepare_trip_turns) / total_turns, 4) if total_turns else 0,
        "prepare_trip_request_planner_accuracy": round(
            sum(1 for trace in expected_prepare_turns if trace.get("actual_tool") == "prepare_trip_request")
            / len(expected_prepare_turns),
            4,
        )
        if expected_prepare_turns
        else 0,
        "requested_product_accuracy": round(
            requested_product_passed / len(requested_product_turns),
            4,
        )
        if requested_product_turns
        else 0,
        "execution_decision_accuracy": round(
            sum(1 for trace in traces if trace.get("pass")) / total_turns,
            4,
        )
        if total_turns
        else 0,
        "provider_execution_gate_accuracy": round(
            provider_gate_turns_passed / total_turns,
            4,
        )
        if total_turns
        else 0,
        "pendingAction_accuracy": round(pending_passed / pending_total, 4) if pending_total else 0,
        "stale_override_count": category_counts.get("STALE_OVERRIDE", 0),
        "pendingAction_pass_count": pending_passed,
        "pendingAction_fail_count": pending_total - pending_passed,
        "illegal_provider_call_count": illegal_provider_count,
        "failure_category_counts": dict(category_counts),
        "tool_confusion_matrix": confusion,
        "wrong_tool_pattern_counts": dict(pattern_counts),
        "repeat_stability_score": round(repeat_stability_score, 4),
        "failure_categories": FAILURE_CATEGORIES,
        "most_common_wrong_tools": wrong_tools.most_common(5),
        "most_common_wrong_slots": wrong_slots.most_common(10),
        "per_scenario": per_scenario,
    }


def write_outputs(
    scenarios: list[dict[str, Any]],
    traces: list[dict[str, Any]],
    output_dir: Path = LATEST_RUN_DIR,
    *,
    live_llama: bool = False,
    metadata: dict[str, Any] | None = None,
    comparison_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = calculate_metrics(scenarios, traces)
    metadata = metadata or model_metadata(live_llama=live_llama)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "metadata": metadata,
        "metrics": metrics,
        "phase_2b_baseline_metrics": PHASE_2B_BASELINE_METRICS if live_llama else None,
        "phase_3a_baseline_metrics": PHASE_3A_BASELINE_METRICS if live_llama else None,
        "comparison_metrics": comparison_metrics,
        "scenarios": [
            {
                "id": scenario["id"],
                "description": scenario.get("description"),
                "turn_count": len(scenario.get("turns", [])),
            }
            for scenario in scenarios
        ],
        "traces": traces,
    }
    json_name = LIVE_REPORT_JSON if live_llama else MOCK_REPORT_JSON
    md_name = LIVE_REPORT_MD if live_llama else MOCK_REPORT_MD
    trace_name = LIVE_TRACE_JSONL if live_llama else MOCK_TRACE_JSONL
    (output_dir / json_name).write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    with (output_dir / trace_name).open("w", encoding="utf-8") as file:
        for trace in traces:
            file.write(json.dumps(trace, ensure_ascii=False) + "\n")

    failed_rows = "\n".join(
        f"| {trace['scenario_id']} | {trace['turn_index']} | {trace['failure_category'] or '-'} | "
        f"{'PASS' if trace['pass'] else 'FAIL'} | {trace['response_text'][:160].replace('|', '/')} |"
        for trace in traces
    )
    all_actual_tools = sorted(
        {
            actual
            for row in metrics["tool_confusion_matrix"].values()
            for actual in row
        }
    )
    confusion_header = "| Expected tool | " + " | ".join(all_actual_tools or ["-"]) + " |"
    confusion_separator = "| --- | " + " | ".join("---:" for _ in (all_actual_tools or ["-"])) + " |"
    confusion_rows = "\n".join(
        "| "
        + expected
        + " | "
        + " | ".join(str(actual_counts.get(actual, 0)) for actual in (all_actual_tools or ["-"]))
        + " |"
        for expected, actual_counts in metrics["tool_confusion_matrix"].items()
    )
    pattern_rows = "\n".join(
        f"| {pattern} | {count} |"
        for pattern, count in Counter(metrics["wrong_tool_pattern_counts"]).most_common()
    ) or "| - | 0 |"
    failed_diagnostics_rows = "\n".join(
        f"| {trace['scenario_id']} | {trace['turn_index']} | {trace.get('repeat_index', 1)} | "
        f"{trace['user_message'].replace('|', '/')} | {trace.get('expected_tools') or trace.get('expected_tool')} | "
        f"{trace.get('actual_tool')} | {json.dumps(trace.get('expected_important_slots'), ensure_ascii=False).replace('|', '/')} | "
        f"{json.dumps(trace.get('actual_important_slots'), ensure_ascii=False).replace('|', '/')} | "
        f"{trace.get('failure_category') or '-'} | "
        f"{', '.join(str(item.get('code')) for item in trace.get('diagnostics', [])[:4]) or '-'} | "
        f"{str(trace.get('likely_cause') or '-').replace('|', '/')} |"
        for trace in traces
        if not trace.get("pass")
    ) or "| - | - | - | - | - | - | - | - | - | - | - |"
    scenario_rows = "\n".join(
        f"| {scenario_id} | {data['pass_count']} | {data['fail_count']} | {data['pass_rate'] * 100:.2f}% | "
        f"{data['most_common_wrong_tool'] or '-'} | {data['most_common_wrong_slots'] or '-'} |"
        for scenario_id, data in metrics["per_scenario"].items()
    )
    recommendation_rows = "\n".join(
        f"| {category} | {count} | {failure_recommendation(category)} |"
        for category, count in metrics["failure_category_counts"].items()
    ) or "| - | 0 | No failures. |"
    if comparison_metrics:
        comparison_table = f"""
## Mocked vs Live Comparison

| Mode | Scenarios | Turns | Pass rate | JSON parse rate | Schema validity | Tool accuracy | Slot/state accuracy | Joint state accuracy | Stale overrides | Pending-action failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Mocked live-shaped | {comparison_metrics['total_scenarios']} | {comparison_metrics['total_turns']} | {(comparison_metrics['passed_turns'] / comparison_metrics['total_turns'] * 100) if comparison_metrics['total_turns'] else 0:.2f}% | {comparison_metrics['json_parse_rate'] * 100:.2f}% | {comparison_metrics['schema_validity'] * 100:.2f}% | {comparison_metrics['tool_accuracy'] * 100:.2f}% | {comparison_metrics['slot_state_accuracy'] * 100:.2f}% | {comparison_metrics['joint_state_accuracy'] * 100:.2f}% | {comparison_metrics['stale_override_count']} | {comparison_metrics['pendingAction_fail_count']} |
| Live Llama | {metrics['total_scenarios']} | {metrics['total_turns']} | {(metrics['passed_turns'] / metrics['total_turns'] * 100) if metrics['total_turns'] else 0:.2f}% | {metrics['json_parse_rate'] * 100:.2f}% | {metrics['schema_validity'] * 100:.2f}% | {metrics['tool_accuracy'] * 100:.2f}% | {metrics['slot_state_accuracy'] * 100:.2f}% | {metrics['joint_state_accuracy'] * 100:.2f}% | {metrics['stale_override_count']} | {metrics['pendingAction_fail_count']} |
"""
    else:
        comparison_table = ""
    if live_llama:
        phase_comparison_table = f"""
## Phase 3B Before vs After

| Metric | Before Phase 3B | After Phase 3B |
| --- | ---: | ---: |
| Tool accuracy | {PHASE_3A_BASELINE_METRICS['tool_accuracy'] * 100:.2f}% | {metrics['tool_accuracy'] * 100:.2f}% |
| Joint state accuracy | {PHASE_3A_BASELINE_METRICS['joint_state_accuracy'] * 100:.2f}% | {metrics['joint_state_accuracy'] * 100:.2f}% |
| Repeat stability | {PHASE_3A_BASELINE_METRICS['repeat_stability_score'] * 100:.2f}% | {metrics['repeat_stability_score'] * 100:.2f}% |
| Prepare-trip planner accuracy | - | {metrics['prepare_trip_request_planner_accuracy'] * 100:.2f}% |
| Requested-product accuracy | - | {metrics['requested_product_accuracy'] * 100:.2f}% |
| Provider execution gate accuracy | - | {metrics['provider_execution_gate_accuracy'] * 100:.2f}% |
| Pending-action accuracy | - | {metrics['pendingAction_accuracy'] * 100:.2f}% |

### Failure Category Change

| Category | Before | After |
| --- | ---: | ---: |
| TOOL_INTENT | {PHASE_3A_BASELINE_METRICS['failure_category_counts'].get('TOOL_INTENT', 0)} | {metrics['failure_category_counts'].get('TOOL_INTENT', 0)} |
| STATE_MERGE | {PHASE_3A_BASELINE_METRICS['failure_category_counts'].get('STATE_MERGE', 0)} | {metrics['failure_category_counts'].get('STATE_MERGE', 0)} |
| PENDING_ACTION | {PHASE_3A_BASELINE_METRICS['failure_category_counts'].get('PENDING_ACTION', 0)} | {metrics['failure_category_counts'].get('PENDING_ACTION', 0)} |

The Phase 3B target is prepare_trip_request/planner accuracy >= 80%,
requested_product accuracy >= 75%, joint state accuracy >= 65%, repeat
stability >= 60%, and fewer pendingAction failures.
"""
    else:
        phase_comparison_table = ""
    mode_label = "Live Llama" if live_llama else "Mocked Live-Shaped"
    content = f"""# Voyager AI Response Evaluation Report ({mode_label})

Generated: {report['generated_at']}

## Metadata

| Field | Value |
| --- | --- |
| live_llama | {metadata['live_llama']} |
| model_name | {metadata['model_name']} |
| ollama_base_url | {metadata['ollama_base_url']} |
| ollama_model_digest | {metadata.get('ollama_model_digest') or '-'} |
| prompt_version | {metadata['prompt_version']} |
| schema_version | {metadata['schema_version']} |
| timestamp | {metadata['timestamp']} |

## Metrics

| Metric | Value |
| --- | ---: |
| Total scenarios | {metrics['total_scenarios']} |
| Total turns | {metrics['total_turns']} |
| Passed turns | {metrics['passed_turns']} |
| Failed turns | {metrics['failed_turns']} |
| JSON parse rate | {metrics['json_parse_rate'] * 100:.2f}% |
| Schema validity | {metrics['schema_validity'] * 100:.2f}% |
| Tool accuracy | {metrics['tool_accuracy'] * 100:.2f}% |
| Slot/state accuracy | {metrics['slot_state_accuracy'] * 100:.2f}% |
| Joint state accuracy | {metrics['joint_state_accuracy'] * 100:.2f}% |
| Prepare-trip usage rate | {metrics['prepare_trip_request_usage_rate'] * 100:.2f}% |
| Prepare-trip planner accuracy | {metrics['prepare_trip_request_planner_accuracy'] * 100:.2f}% |
| Requested-product accuracy | {metrics['requested_product_accuracy'] * 100:.2f}% |
| Execution decision accuracy | {metrics['execution_decision_accuracy'] * 100:.2f}% |
| Provider execution gate accuracy | {metrics['provider_execution_gate_accuracy'] * 100:.2f}% |
| Pending-action accuracy | {metrics['pendingAction_accuracy'] * 100:.2f}% |
| Repeat stability score | {metrics['repeat_stability_score'] * 100:.2f}% |
| Stale override count | {metrics['stale_override_count']} |
| Pending-action passed | {metrics['pendingAction_pass_count']} |
| Pending-action failed | {metrics['pendingAction_fail_count']} |
| Illegal provider calls | {metrics['illegal_provider_call_count']} |

{comparison_table}

{phase_comparison_table}

## Failure Categories

```json
{json.dumps(metrics['failure_category_counts'], indent=2)}
```

## Tool Confusion Matrix

{confusion_header}
{confusion_separator}
{confusion_rows}

## Wrong-Tool Pattern Analysis

| Pattern | Count |
| --- | ---: |
{pattern_rows}

## Failed Turn Diagnostics

| Scenario | Turn | Repeat | User message | Expected tool | Actual tool | Expected important slots | Actual slots | Category | Diagnostics | Likely cause |
| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- |
{failed_diagnostics_rows}

## Failure Recommendations

| Category | Count | Recommendation |
| --- | ---: | --- |
{recommendation_rows}

## Scenario Repeats

| Scenario | Pass count | Fail count | Pass rate | Most common wrong tool | Most common wrong slots |
| --- | ---: | ---: | ---: | --- | --- |
{scenario_rows}

## Turn Results

| Scenario | Turn | Category | Result | Response excerpt |
| --- | ---: | --- | --- | --- |
{failed_rows}

## Notes

This evaluator runs the same Voyager chat/state pipeline used by `/chat`.
Mocked mode replays live-shaped tool outputs. Live mode calls the local Ollama
router and stubs travel providers so the report focuses on tool routing, schema
validity, state merge correctness, pending-action handling, provider execution
gates, and response text checks.
"""
    (output_dir / md_name).write_text(content, encoding="utf-8")
    return report


def collect_traces(
    scenarios: list[dict[str, Any]],
    *,
    live_llama: bool = False,
    repeats: int = 1,
) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    for repeat_index in range(1, max(repeats, 1) + 1):
        for scenario in scenarios:
            traces.extend(
                run_scenario(
                    scenario,
                    live_llama=live_llama,
                    repeat_index=repeat_index,
                )
            )
    return traces


def run_ai_response_eval(
    scenarios_dir: Path = SCENARIOS_DIR,
    output_dir: Path = LATEST_RUN_DIR,
    *,
    live_llama: bool = False,
    repeats: int = 1,
) -> dict[str, Any]:
    scenarios = load_scenarios(scenarios_dir)
    traces = collect_traces(scenarios, live_llama=live_llama, repeats=repeats)
    comparison_metrics = None
    if live_llama:
        mocked_traces = collect_traces(scenarios, live_llama=False, repeats=1)
        comparison_metrics = calculate_metrics(scenarios, mocked_traces)
    metadata = model_metadata(live_llama=live_llama)
    metadata["repeats"] = repeats
    return write_outputs(
        scenarios,
        traces,
        output_dir,
        live_llama=live_llama,
        metadata=metadata,
        comparison_metrics=comparison_metrics,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Voyager AI-response evaluation scenarios.")
    parser.add_argument(
        "--live-llama",
        action="store_true",
        help="Call the real local Ollama router instead of scenario mocked outputs.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of times to repeat each scenario. Useful for live Llama stability checks.",
    )
    args = parser.parse_args()
    report = run_ai_response_eval(live_llama=args.live_llama, repeats=max(args.repeats, 1))
    metrics = report["metrics"]
    print(f"Ran {metrics['total_scenarios']} scenario(s), {metrics['total_turns']} turn(s).")
    print(f"Passed turns: {metrics['passed_turns']}/{metrics['total_turns']}")
    print(f"JSON parse rate: {metrics['json_parse_rate'] * 100:.2f}%")
    print(f"Schema validity: {metrics['schema_validity'] * 100:.2f}%")
    print(f"Tool accuracy: {metrics['tool_accuracy'] * 100:.2f}%")
    print(f"Slot/state accuracy: {metrics['slot_state_accuracy'] * 100:.2f}%")
    print(f"Joint state accuracy: {metrics['joint_state_accuracy'] * 100:.2f}%")
    if args.live_llama:
        print(f"Wrote {LATEST_RUN_DIR / LIVE_REPORT_MD}")
        print(f"Wrote {LATEST_RUN_DIR / LIVE_REPORT_JSON}")
        print(f"Wrote {LATEST_RUN_DIR / LIVE_TRACE_JSONL}")
    else:
        print(f"Wrote {LATEST_RUN_DIR / MOCK_REPORT_MD}")
        print(f"Wrote {LATEST_RUN_DIR / MOCK_REPORT_JSON}")
        print(f"Wrote {LATEST_RUN_DIR / MOCK_TRACE_JSONL}")


if __name__ == "__main__":
    main()
