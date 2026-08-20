"""Evaluate fallback and error-handling behaviour for Voyage AI."""

from __future__ import annotations

import csv
import os
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
from typing import Any
from unittest.mock import patch


EVALUATION_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVALUATION_DIR.parent

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{(Path(tempfile.gettempdir()) / 'voyage_ai_fallback_error_tests.db').as_posix()}",
)
os.environ.setdefault("OLLAMA_MODEL", "llama3.1:8b-instruct-q4_0")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(EVALUATION_DIR) not in sys.path:
    sys.path.insert(0, str(EVALUATION_DIR))

from app.database import SessionLocal  # noqa: E402
from app.main import app, chat  # noqa: E402
from app.schemas import ChatRequest  # noqa: E402
from run_benchmark import (  # noqa: E402
    FIXED_TODAY,
    TestClient,
    _benchmark_extract_flight,
    _benchmark_route_tool,
    _demo_session,
    _empty_evidence,
    _flight_response,
    _hotel_response,
    _reset_database,
)


TEST_CASES = [
    {
        "id": "duffel_unavailable",
        "messages": ["Find me a flight from EMA to London on 11 August and back on 15 August"],
        "expected": "fallback_or_flights",
    },
    {
        "id": "liteapi_unavailable",
        "messages": ["I want to book a hotel in London Hyde Park from the 11th to the 15th"],
        "expected": "fallback_or_hotels",
    },
    {
        "id": "invalid_city",
        "messages": ["I want to fly to asdfghjkl on the 11th"],
        "expected": "safe_no_crash",
    },
    {
        "id": "invalid_past_date",
        "messages": ["I want to travel yesterday"],
        "expected": "safe_no_crash",
    },
    {
        "id": "missing_destination",
        "messages": ["I want a hotel for 3 nights"],
        "expected": "asks_destination",
    },
    {
        "id": "missing_date",
        "messages": ["I want to fly to London"],
        "expected": "asks_date",
    },
    {
        "id": "impossible_budget",
        "messages": ["I want a package to London under 5 pounds"],
        "expected": "safe_no_successful_booking",
    },
    {
        "id": "invalid_option_number",
        "messages": [
            "I want to fly to London on the 11th and come back on the 15th",
            "option 99",
        ],
        "expected": "invalid_option_message",
    },
    {
        "id": "empty_cart_checkout",
        "messages": ["checkout"],
        "expected": "no_booking_without_cart",
    },
    {
        "id": "repeated_add_to_cart",
        "messages": [
            "I want to fly to London on the 11th and come back on the 15th",
            "option one",
            "yes add it to cart",
            "yes add it to cart",
        ],
        "expected": "no_crash_no_duplicate_or_message",
    },
]


@dataclass
class ErrorCaseResult:
    case_id: str
    passed: bool
    expected: str
    final_reply: str
    result_type: str
    reason: str


def _send_direct(messages: list[str]) -> dict[str, Any]:
    session_id = _demo_session()
    final_response: dict[str, Any] = {}
    db = SessionLocal()
    try:
        for message in messages:
            response = chat(
                ChatRequest(message=message, sessionId=session_id),
                db=db,
                current_user=None,
            )
            final_response = response.model_dump(by_alias=True, mode="json")
            session_id = int(final_response.get("sessionId") or session_id)
        db.commit()
        return final_response
    finally:
        db.close()


def _send_test_client(client: Any, messages: list[str]) -> dict[str, Any]:
    session_id = _demo_session()
    final_response: dict[str, Any] = {}
    for message in messages:
        response = client.post("/chat", json={"message": message, "sessionId": session_id})
        if response.status_code != 200:
            return {"reply": response.text, "statusCode": response.status_code, "results": {}}
        final_response = response.json()
        session_id = int(final_response.get("sessionId") or session_id)
    return final_response


def _patches() -> ExitStack:
    stack = ExitStack()
    stack.enter_context(patch("app.services.chat_service.has_duffel_credentials", return_value=False))
    stack.enter_context(patch("app.services.chat_service.liteapi_hotels_enabled", return_value=False))
    stack.enter_context(patch("app.services.chat_service._search_flights_with_fallback", side_effect=_flight_response))
    stack.enter_context(patch("app.services.chat_service._search_hotels_with_fallback", side_effect=_hotel_response))
    stack.enter_context(patch("app.main._search_flights_with_fallback", side_effect=_flight_response))
    stack.enter_context(patch("app.main._search_hotels_with_fallback", side_effect=_hotel_response))
    stack.enter_context(patch("app.destination_discovery.get_destination_evidence", side_effect=_empty_evidence))
    stack.enter_context(patch("app.services.chat_service.route_tool_with_llama", side_effect=_benchmark_route_tool))
    stack.enter_context(patch("app.services.chat_service.extract_flight_search_with_llama", side_effect=_benchmark_extract_flight))
    stack.enter_context(patch("app.services.chat_service.today_utc_date", return_value=FIXED_TODAY))
    return stack


def _result_type(response: dict[str, Any]) -> str:
    results = response.get("results") or {}
    if results.get("flights"):
        return "flights"
    if results.get("hotels"):
        return "hotels"
    if results.get("packages"):
        return "packages"
    if results.get("cart"):
        return "cart"
    if results.get("booking"):
        return "booking"
    return str(response.get("intent", {}).get("intent_type") or "message")


def _evaluate(case: dict[str, Any], response: dict[str, Any]) -> ErrorCaseResult:
    reply = str(response.get("reply") or "")
    lowered = reply.lower()
    result_type = _result_type(response)
    no_crash = not response.get("statusCode") and bool(reply)
    expected = case["expected"]
    passed = False
    reason = ""

    if expected == "fallback_or_flights":
        passed = no_crash and result_type == "flights"
        reason = "Expected flight fallback results or a safe flight response."
    elif expected == "fallback_or_hotels":
        passed = no_crash and result_type == "hotels"
        reason = "Expected hotel fallback results or a safe hotel response."
    elif expected == "safe_no_crash":
        passed = no_crash
        reason = "Expected any safe non-crashing response."
    elif expected == "asks_destination":
        passed = no_crash and (
            "which city" in lowered
            or "provide a city" in lowered
            or "where" in lowered
            or "destination" in lowered
        )
        reason = "Expected the assistant to ask for a missing destination."
    elif expected == "asks_date":
        passed = no_crash and ("when" in lowered or "date" in lowered or "depart" in lowered)
        reason = "Expected the assistant to ask for missing date details."
    elif expected == "safe_no_successful_booking":
        passed = no_crash and "booking reference" not in lowered
        reason = "Expected no booking reference for impossible budget."
    elif expected == "invalid_option_message":
        passed = no_crash and ("option" in lowered or "choose" in lowered or "could not find" in lowered)
        reason = "Expected a valid-option clarification."
    elif expected == "no_booking_without_cart":
        passed = no_crash and "booking reference" not in lowered
        reason = "Expected no booking reference from empty cart checkout."
    elif expected == "no_crash_no_duplicate_or_message":
        passed = no_crash
        reason = "Expected no crash and a safe duplicate/add-to-cart response."

    return ErrorCaseResult(case["id"], passed, expected, reply[:500], result_type, reason)


def run_tests() -> list[ErrorCaseResult]:
    _reset_database()
    results: list[ErrorCaseResult] = []
    with _patches():
        if TestClient is None:
            for case in TEST_CASES:
                results.append(_evaluate(case, _send_direct(case["messages"])))
        else:
            with TestClient(app) as client:
                for case in TEST_CASES:
                    results.append(_evaluate(case, _send_test_client(client, case["messages"])))
    return results


def _write_csv(results: list[ErrorCaseResult]) -> None:
    with (EVALUATION_DIR / "fallback_and_error_results.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["case_id", "passed", "expected", "result_type", "reason", "final_reply"],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "case_id": result.case_id,
                    "passed": result.passed,
                    "expected": result.expected,
                    "result_type": result.result_type,
                    "reason": result.reason,
                    "final_reply": result.final_reply.replace("\n", " "),
                }
            )


def _write_summary(results: list[ErrorCaseResult]) -> None:
    total = len(results)
    passed = sum(result.passed for result in results)
    failed = total - passed
    failure_rows = [
        f"| {result.case_id} | {result.expected} | {result.result_type} | {result.final_reply} |"
        for result in results
        if not result.passed
    ]
    failure_table = "\n".join(failure_rows) if failure_rows else "| None | - | - | - |"
    content = f"""# Voyage AI Fallback and Error-Handling Summary

Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

Total cases: {total}

Passed cases: {passed}

Failed cases: {failed}

| Failed case | Expected behaviour | Actual result type | Final reply |
| --- | --- | --- | --- |
{failure_table}

## Interpretation

The fallback and error-handling tests check that provider outages, missing information, invalid input, empty carts and repeated actions do not crash the assistant or create unsafe booking state.

## Thesis Interpretation Notes

- The evaluation shows whether the system degrades safely when supplier data or user input is incomplete.
- Failed cases should be treated as limitations or future improvement points rather than hidden.
- The tests focus on safety and graceful handling, not on live supplier success rates.
"""
    (EVALUATION_DIR / "fallback_and_error_summary.md").write_text(content, encoding="utf-8")


def main() -> None:
    results = run_tests()
    _write_csv(results)
    _write_summary(results)
    print(f"Ran {len(results)} fallback/error cases.")
    print(f"Passed: {sum(result.passed for result in results)}")
    print(f"Failed: {sum(not result.passed for result in results)}")
    print("Wrote evaluation/fallback_and_error_results.csv")
    print("Wrote evaluation/fallback_and_error_summary.md")


if __name__ == "__main__":
    main()
