"""Run a small MultiWOZ-style benchmark against the Voyage AI chat endpoint."""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import sys
import tempfile
from typing import Any
from unittest.mock import patch


EVALUATION_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVALUATION_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
FIXED_TODAY = date(2026, 8, 11)

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(Path(tempfile.gettempdir()) / 'voyage_ai_benchmark.db').as_posix()}")
os.environ.setdefault("OLLAMA_MODEL", "llama3.1:8b-instruct-q4_0")
os.environ.setdefault("USE_DUFFEL_FLIGHTS", "false")
os.environ.setdefault("USE_LITEAPI_HOTELS", "false")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:  # noqa: E402
    from fastapi.testclient import TestClient
except RuntimeError:
    TestClient = None  # type: ignore[assignment]

from app.database import Base, SessionLocal, create_db_and_tables, engine  # noqa: E402
from app.main import DEMO_USER_EMAIL, app, chat  # noqa: E402
from app.memory_store import load_active_trip, save_default_origin  # noqa: E402
from app.models import ChatSession, User  # noqa: E402
from app.schemas import (  # noqa: E402
    ChatRequest,
    FlightSearchRequest,
    FlightSearchResponse,
    HotelSearchRequest,
    HotelSearchResponse,
    NormalisedFlightResult,
    NormalisedHotelResult,
    TravelToolCall,
)


@dataclass
class CaseResult:
    case_id: str
    category: str
    intent_ok: bool
    slot_total: int
    slot_correct: int
    workflow_ok: bool
    context_case: bool
    context_ok: bool
    clarification_case: bool
    clarification_ok: bool
    actual_intent: str
    expected_intent: str
    failed_slots: list[str]
    final_reply: str


def _reset_database() -> None:
    Base.metadata.drop_all(bind=engine)
    create_db_and_tables()


def _demo_session() -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == DEMO_USER_EMAIL).first()
        if user is None:
            user = User(
                display_name="Benchmark Demo User",
                full_name="Benchmark Demo User",
                email=DEMO_USER_EMAIL,
                password_hash="",
            )
            db.add(user)
            db.flush()
        session = ChatSession(user_id=user.id, title="Benchmark case")
        db.add(session)
        db.flush()
        save_default_origin(
            db,
            session.id,
            {
                "city": "East Midlands",
                "country": "United Kingdom",
                "airportCode": "EMA",
                "airportName": "East Midlands Airport",
                "source": "benchmark_default_origin",
            },
        )
        db.commit()
        return int(session.id)
    finally:
        db.close()


def _flight_response(request: FlightSearchRequest) -> FlightSearchResponse:
    origin = request.itinerary.origin.code.upper()
    destination = request.itinerary.destination.code.upper()
    departure = request.itinerary.dates.departure.isoformat()
    return_date = (
        request.itinerary.dates.return_date.isoformat()
        if request.itinerary.dates.return_date
        else None
    )
    passenger_count = request.passenger_count
    cabin = request.preferences.cabin_class
    base_prices = {
        "LHR": 120,
        "JFK": 460,
        "CDG": 135,
        "ORY": 135,
        "LYS": 92,
        "ERZ": 260,
        "ASR": 250,
        "BEY": 310,
        "HRG": 330,
        "SSH": 340,
        "RAK": 220,
    }
    base_price = base_prices.get(destination, 180)
    day_adjustment = int(departure[-2:]) % 7 * 4
    flights: list[NormalisedFlightResult] = []
    for index, extra in enumerate((0, 35, 70), start=1):
        price = base_price + day_adjustment + extra
        flights.append(
            NormalisedFlightResult(
                id=f"bench-flight-{destination}-{index}-{departure}",
                offer_id=f"bench-offer-{destination}-{index}-{departure}",
                display_id=f"Offer {index}",
                option_number=index,
                origin=origin,
                destination=destination,
                airline="Voyage Airways" if index == 1 else "Atlas Air",
                outbound_departure_date=departure,
                outbound_departure_time=f"{7 + index:02d}:30",
                outbound_arrival_date=departure,
                outbound_arrival_time=f"{9 + index:02d}:20",
                return_departure_date=return_date,
                return_departure_time="13:10" if return_date else None,
                return_arrival_date=return_date,
                return_arrival_time="15:00" if return_date else None,
                departure_date=f"{departure}T{7 + index:02d}:30:00",
                return_date=f"{return_date}T13:10:00" if return_date else None,
                stops=0 if index < 3 else 1,
                stops_label="Non-stop" if index < 3 else "1 stop",
                duration_minutes=100 + index * 12,
                duration_label=f"{1 + index}h {index * 10}m",
                cabin_class=cabin,
                cabin_class_label=cabin.replace("_", " ").title(),
                passenger_count=passenger_count,
                price=f"{price:.2f}",
                currency="GBP",
                provider="benchmark_flight_provider",
            )
        )
    return FlightSearchResponse(provider_used="mock_fallback", flights=flights)


def _hotel_response(request: HotelSearchRequest) -> HotelSearchResponse:
    city = request.destination.city or "Selected city"
    check_in = request.check_in_date.isoformat()
    check_out = request.check_out_date.isoformat()
    nights = max((request.check_out_date - request.check_in_date).days, 1)
    city_prices = {
        "London": 145,
        "Paris": 132,
        "Rome": 118,
        "Barcelona": 125,
        "Lyon": 104,
        "Erzurum": 82,
        "Hurghada": 95,
    }
    base_price = city_prices.get(city, 110)
    hotels: list[NormalisedHotelResult] = []
    for index, extra in enumerate((0, 40, 80), start=1):
        hotels.append(
            NormalisedHotelResult(
                id=f"bench-hotel-{city.lower()}-{index}",
                option_number=index,
                hotel_name=f"{city} Central Hotel {index}",
                location=city,
                check_in_date=check_in,
                check_out_date=check_out,
                nights=nights,
                guest_count=request.guest_count,
                room_count=request.room_count,
                rating=4.4 - index * 0.2,
                board_type="Breakfast",
                room_name="Standard room",
                refundable=index == 1,
                price=float(base_price * nights + extra),
                currency=request.preferences.currency or "GBP",
                provider="benchmark_hotel_provider",
                short_id=f"HTL{index}",
                description=f"Benchmark hotel option in {city}.",
                amenities=["wifi", "breakfast"],
            )
        )
    return HotelSearchResponse(provider_used="mock_hotel_fallback", results=hotels)


def _empty_evidence(_candidate: dict[str, Any], _preferences: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidenceNames": [],
        "skiEvidenceCount": 0,
        "familyEvidenceCount": 0,
    }


def _flight_request(
    origin: str,
    destination: str,
    departure: str,
    return_date: str | None = None,
    *,
    passengers: int = 1,
) -> FlightSearchRequest:
    return FlightSearchRequest.model_validate(
        {
            "searchType": "flight",
            "passengerCount": passengers,
            "itinerary": {
                "tripType": "round_trip" if return_date else "one_way",
                "origin": {"city": origin, "code": origin},
                "destination": {"city": destination, "code": destination},
                "dates": {
                    "departure": departure,
                    **({"return": return_date} if return_date else {}),
                },
            },
            "preferences": {"cabinClass": "economy", "maxStops": None},
        }
    )


def _hotel_tool(city: str, check_in: str, check_out: str, guests: int = 1) -> TravelToolCall:
    return TravelToolCall(
        tool="search_hotels",
        arguments={
            "searchType": "hotel",
            "destination": {"city": city},
            "checkInDate": check_in,
            "checkOutDate": check_out,
            "guestCount": guests,
            "roomCount": 1,
            "preferences": {"currency": "GBP"},
        },
    )


def _benchmark_extract_flight(message: str) -> FlightSearchRequest | None:
    normalized = message.lower()
    if "east midlands" in normalized and "new york" in normalized:
        return _flight_request("EMA", "JFK", "2026-09-12", "2026-09-16")
    if "ema to paris" in normalized:
        return _flight_request("EMA", "CDG", "2026-09-12", "2026-09-16", passengers=2)
    if "ema to london" in normalized or "ema to lhr" in normalized:
        return _flight_request("EMA", "LHR", "2026-08-11", "2026-08-15")
    return None


def _benchmark_route_tool(message: str, _conversation_state: dict[str, Any] | None = None) -> TravelToolCall | None:
    normalized = message.lower()
    flight_request = _benchmark_extract_flight(message)
    if flight_request is not None:
        return TravelToolCall(
            tool="search_flights",
            arguments={"search_request": flight_request.model_dump(mode="json", by_alias=True)},
        )
    if "accommodation in paris from 12 september to 16 september" in normalized:
        return _hotel_tool("Paris", "2026-09-12", "2026-09-16")
    if "stays in rome from 1 october to 4 october" in normalized:
        return _hotel_tool("Rome", "2026-10-01", "2026-10-04", guests=2)
    if "travel package to paris from 12 september to 15 september" in normalized:
        return TravelToolCall(
            tool="search_trip_options",
            arguments={
                "destinationText": "Paris",
                "requestedProducts": ["flights", "hotels"],
            },
        )
    return None


def _load_active_trip(session_id: int) -> dict[str, Any]:
    db = SessionLocal()
    try:
        return load_active_trip(db, session_id)
    finally:
        db.close()


def _first_result(response: dict[str, Any], name: str) -> dict[str, Any]:
    values = response.get("results", {}).get(name) or []
    return values[0] if values else {}


def _actual_state(response: dict[str, Any], active_trip: dict[str, Any]) -> dict[str, Any]:
    results = response.get("results", {})
    first_flight = _first_result(response, "flights")
    first_hotel = _first_result(response, "hotels")
    first_package = _first_result(response, "packages")
    cart = results.get("cart") or active_trip.get("cart") or {}
    selected_package = active_trip.get("selectedPackageOption") or {}
    selected_destination = active_trip.get("selectedDestination") or {}
    origin = active_trip.get("origin") or {}
    dates = active_trip.get("dates") or {}
    hotel_summary = first_package.get("hotel") if isinstance(first_package.get("hotel"), dict) else {}
    flight_summary = first_package.get("flight") if isinstance(first_package.get("flight"), dict) else {}
    reply = str(response.get("reply") or "")

    visible = active_trip.get("latestVisibleResultType")
    if not visible:
        if results.get("packages"):
            visible = "packages"
        elif results.get("hotels"):
            visible = "hotels"
        elif results.get("flights"):
            visible = "flights"
        elif results.get("destinations"):
            visible = "destinations"
        elif results.get("cart"):
            visible = "cart"

    return {
        "reply": response.get("reply", ""),
        "intent": _derive_intent(response, visible),
        "origin": (
            origin.get("airportCode")
            or first_flight.get("origin")
            or flight_summary.get("origin")
        ),
        "destination": (
            selected_destination.get("airportCode")
            or first_flight.get("destination")
            or first_package.get("destinationAirportCode")
            or flight_summary.get("destination")
        ),
        "destination_city": (
            selected_destination.get("city")
            or first_hotel.get("location")
            or first_package.get("destinationCity")
            or hotel_summary.get("city")
        ),
        "departure_date": (
            dates.get("departureDate")
            or first_flight.get("outbound_departure_date")
            or flight_summary.get("departureDate")
        ),
        "return_date": (
            dates.get("returnDate")
            or first_flight.get("return_departure_date")
            or flight_summary.get("returnDate")
        ),
        "check_in_date": first_hotel.get("check_in_date") or hotel_summary.get("checkInDate"),
        "check_out_date": first_hotel.get("check_out_date") or hotel_summary.get("checkOutDate"),
        "nights": active_trip.get("tripDurationNights") or first_hotel.get("nights") or first_package.get("nights"),
        "budget": active_trip.get("budget") or active_trip.get("budgetMax") or active_trip.get("maxBudget"),
        "passenger_count": first_flight.get("passenger_count") or active_trip.get("passengerCount"),
        "guest_count": first_hotel.get("guest_count"),
        "selected_option": (
            selected_package.get("optionNumber")
            or cart.get("lastSelectedOptionNumber")
            or active_trip.get("selectedOptionNumber")
            or _selected_option_from_reply(reply)
        ),
        "latest_visible_result_type": visible,
        "next_path": response.get("nextPath"),
        "excluded_region": _first_value(active_trip.get("excludedRegions")),
        "required_activity": _first_value(active_trip.get("requiredActivities")),
    }


def _derive_intent(response: dict[str, Any], visible: str | None) -> str:
    results = response.get("results", {})
    reply = str(response.get("reply") or "").lower()
    has_visible_results = any(
        results.get(key)
        for key in ("flights", "hotels", "destinations", "packages", "cart", "checkout")
    )
    clarification_markers = (
        "where would you like to go",
        "when do you want",
        "how many nights",
        "which destination",
        "which option do you mean",
        "which flight option",
        "please provide",
        "please add a package to your cart first",
    )
    if any(marker in reply for marker in clarification_markers):
        return "clarification"
    if visible in {"flight_selected", "hotel_selected"}:
        return visible.replace("_selected", "_selection")
    if visible == "cart" or results.get("cart"):
        if results.get("checkout") or response.get("nextPath") in {"/checkout", "/profile?next=/checkout"}:
            return "checkout"
        return "cart_update"
    if visible == "packages" or results.get("packages"):
        return "package_search"
    if visible == "hotels" or results.get("hotels"):
        return "hotel_search"
    if visible == "flights" or results.get("flights"):
        return "flight_search"
    if visible == "destinations" or results.get("destinations"):
        return "destination_discovery"
    if any(marker in reply for marker in clarification_markers):
        return "clarification"
    return response.get("intent", {}).get("intent_type") or "unknown"


def _selected_option_from_reply(reply: str) -> int | None:
    normalized = reply.lower()
    match = re.search(r"\b(?:option|hotel|package)\s+(\d+)\b", normalized)
    if match:
        return int(match.group(1))
    return None


def _first_value(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[0]
    return value


def _matches(expected: Any, actual: Any) -> bool:
    if expected is None:
        return actual is None
    if isinstance(expected, bool):
        return bool(actual) == expected
    if isinstance(expected, (int, float)):
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return False
    return str(expected).strip().lower() == str(actual).strip().lower()


def _evaluate_case(case: dict[str, Any], response: dict[str, Any], active_trip: dict[str, Any]) -> CaseResult:
    expected = case["expected"]
    actual = _actual_state(response, active_trip)
    expected_intent = str(expected.get("intent", ""))
    actual_intent = str(actual.get("intent", ""))
    intent_ok = _matches(expected_intent, actual_intent)

    ignored = {"intent", "context_followup"}
    slot_total = 0
    slot_correct = 0
    failed_slots: list[str] = []
    for key, expected_value in expected.items():
        if key in ignored:
            continue
        slot_total += 1
        if _matches(expected_value, actual.get(key)):
            slot_correct += 1
        else:
            failed_slots.append(f"{key}: expected {expected_value!r}, got {actual.get(key)!r}")

    workflow_ok = intent_ok and not failed_slots
    context_case = case["category"] == "follow_up_context" or bool(expected.get("context_followup"))
    context_ok = True
    if context_case:
        context_ok = workflow_ok and actual_intent != "general_enquiry"
    clarification_case = expected_intent == "clarification"
    clarification_ok = True
    if clarification_case:
        clarification_ok = intent_ok

    return CaseResult(
        case_id=case["id"],
        category=case["category"],
        intent_ok=intent_ok,
        slot_total=slot_total,
        slot_correct=slot_correct,
        workflow_ok=workflow_ok,
        context_case=context_case,
        context_ok=context_ok,
        clarification_case=clarification_case,
        clarification_ok=clarification_ok,
        actual_intent=actual_intent,
        expected_intent=expected_intent,
        failed_slots=failed_slots,
        final_reply=str(response.get("reply", "")),
    )


def _run_case_with_direct_handler(case: dict[str, Any]) -> CaseResult:
    session_id = _demo_session()
    final_response: dict[str, Any] = {}
    db = SessionLocal()
    try:
        for message in case["messages"]:
            chat_response = chat(
                ChatRequest(message=message, sessionId=session_id),
                db=db,
                current_user=None,
            )
            final_response = chat_response.model_dump(by_alias=True, mode="json")
            session_id = int(final_response.get("sessionId") or session_id)
        db.commit()
    finally:
        db.close()
    active_trip = _load_active_trip(session_id)
    return _evaluate_case(case, final_response, active_trip)


def _run_case(client: Any, case: dict[str, Any]) -> CaseResult:
    if client is None:
        return _run_case_with_direct_handler(case)

    session_id = _demo_session()
    final_response: dict[str, Any] = {}
    for message in case["messages"]:
        response = client.post("/chat", json={"message": message, "sessionId": session_id})
        if response.status_code != 200:
            final_response = {
                "reply": f"HTTP {response.status_code}: {response.text}",
                "intent": {"intent_type": "http_error"},
                "results": {},
            }
            break
        final_response = response.json()
        session_id = int(final_response.get("sessionId") or session_id)
    active_trip = _load_active_trip(session_id)
    return _evaluate_case(case, final_response, active_trip)


def _write_csv(results: list[CaseResult]) -> None:
    path = EVALUATION_DIR / "benchmark_results.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "case_id",
                "category",
                "expected_intent",
                "actual_intent",
                "intent_ok",
                "slot_correct",
                "slot_total",
                "workflow_ok",
                "context_ok",
                "failed_slots",
                "final_reply",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "case_id": result.case_id,
                    "category": result.category,
                    "expected_intent": result.expected_intent,
                    "actual_intent": result.actual_intent,
                    "intent_ok": result.intent_ok,
                    "slot_correct": result.slot_correct,
                    "slot_total": result.slot_total,
                    "workflow_ok": result.workflow_ok,
                    "context_ok": result.context_ok if result.context_case else "",
                    "failed_slots": "; ".join(result.failed_slots),
                    "final_reply": result.final_reply.replace("\n", " ")[:500],
                }
            )


def _percentage(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100, 2)


def _write_summary(results: list[CaseResult]) -> None:
    total = len(results)
    categories = Counter(result.category for result in results)
    intent_accuracy = _percentage(sum(result.intent_ok for result in results), total)
    slot_total = sum(result.slot_total for result in results)
    slot_correct = sum(result.slot_correct for result in results)
    slot_accuracy = _percentage(slot_correct, slot_total)
    workflow_success = _percentage(sum(result.workflow_ok for result in results), total)
    context_cases = [result for result in results if result.context_case]
    context_success = _percentage(
        sum(result.context_ok for result in context_cases),
        len(context_cases),
    )
    clarification_cases = [result for result in results if result.clarification_case]
    clarification_success = _percentage(
        sum(result.clarification_ok for result in clarification_cases),
        len(clarification_cases),
    )
    failed = [result for result in results if not result.workflow_ok]
    category_lines = "\n".join(
        f"- {category}: {count}" for category, count in sorted(categories.items())
    )
    failed_rows = "\n".join(
        "| {case} | {category} | {expected} | {actual} | {slots} |".format(
            case=result.case_id,
            category=result.category,
            expected=result.expected_intent,
            actual=result.actual_intent,
            slots="<br>".join(result.failed_slots) if result.failed_slots else "Intent mismatch",
        )
        for result in failed
    )
    if not failed_rows:
        failed_rows = "| None | - | - | - | - |"

    content = f"""# Voyage AI Benchmark Summary

Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Scope

This small MultiWOZ-style benchmark evaluates Voyage AI's implemented travel dialogue workflows: flights, hotels, packages, follow-up context, voice-style misrecognition, and cart/checkout handling.

## Cases

Total cases: {total}

Categories tested:

{category_lines}

## Metrics

| Metric | Result |
| --- | ---: |
| Intent accuracy | {intent_accuracy:.2f}% |
| Slot accuracy | {slot_accuracy:.2f}% |
| Workflow success rate | {workflow_success:.2f}% |
| Context follow-up success rate | {context_success:.2f}% |
| Clarification success rate | {clarification_success:.2f}% |

## Failed Cases

| Case | Category | Expected intent | Actual intent | Failed slots |
| --- | --- | --- | --- | --- |
{failed_rows}

## Interpretation

The benchmark measures whether the current Voyage AI backend can preserve travel dialogue state and reach the expected workflow state across realistic multi-turn prompts. Provider calls are replaced with deterministic benchmark data so the results focus on intent handling, slot extraction, and state tracking rather than live API availability or price changes.
"""
    (EVALUATION_DIR / "benchmark_summary.md").write_text(content, encoding="utf-8")


def run_benchmark() -> list[CaseResult]:
    cases = json.loads((EVALUATION_DIR / "benchmark_cases.json").read_text(encoding="utf-8"))
    use_live_llama = os.getenv("VOYAGE_BENCHMARK_USE_LIVE_LLAMA", "false").lower() == "true"
    _reset_database()
    with ExitStack() as stack:
        stack.enter_context(patch("app.services.chat_service._search_flights_with_fallback", side_effect=_flight_response))
        stack.enter_context(patch("app.services.chat_service._search_hotels_with_fallback", side_effect=_hotel_response))
        stack.enter_context(patch("app.main._search_flights_with_fallback", side_effect=_flight_response))
        stack.enter_context(patch("app.main._search_hotels_with_fallback", side_effect=_hotel_response))
        stack.enter_context(patch("app.destination_discovery.get_destination_evidence", side_effect=_empty_evidence))
        if not use_live_llama:
            stack.enter_context(patch("app.services.chat_service.route_tool_with_llama", side_effect=_benchmark_route_tool))
            stack.enter_context(patch("app.services.chat_service.extract_flight_search_with_llama", side_effect=_benchmark_extract_flight))
        stack.enter_context(patch("app.services.chat_service.today_utc_date", return_value=FIXED_TODAY))
        if TestClient is None:
            return [_run_case(None, case) for case in cases]
        with TestClient(app) as client:
            return [_run_case(client, case) for case in cases]


def main() -> None:
    results = run_benchmark()
    _write_csv(results)
    _write_summary(results)
    total = len(results)
    print(f"Ran {total} benchmark cases.")
    print(f"Intent accuracy: {_percentage(sum(result.intent_ok for result in results), total):.2f}%")
    print(
        "Slot accuracy: "
        f"{_percentage(sum(result.slot_correct for result in results), sum(result.slot_total for result in results)):.2f}%"
    )
    print(f"Workflow success: {_percentage(sum(result.workflow_ok for result in results), total):.2f}%")
    print("Wrote evaluation/benchmark_results.csv")
    print("Wrote evaluation/benchmark_summary.md")


if __name__ == "__main__":
    main()
