"""Measure response times for key Voyage AI chat workflows."""

from __future__ import annotations

import csv
import os
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import statistics
import sys
import time
from typing import Any
from unittest.mock import patch


EVALUATION_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVALUATION_DIR.parent

os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{(Path(tempfile.gettempdir()) / 'voyage_ai_response_time_tests.db').as_posix()}",
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


ITERATIONS = 3


TEST_CASES = [
    {
        "id": "simple_help",
        "messages": ["hello can you help me plan a trip"],
    },
    {
        "id": "flight_search",
        "messages": ["I want to fly to London on the 11th and come back on the 15th"],
    },
    {
        "id": "hotel_search",
        "messages": ["I want to book a hotel in London Hyde Park from the 11th to the 15th"],
    },
    {
        "id": "package_search",
        "messages": ["I want a full flight and hotel package to London from the 11th to the 15th under 1000 pounds"],
    },
    {
        "id": "destination_discovery",
        "messages": ["I want to go skiing for 3 nights"],
    },
    {
        "id": "cart_selection_flow",
        "messages": [
            "I want to fly to London on the 11th and come back on the 15th",
            "option one",
            "yes add it to cart",
        ],
    },
    {
        "id": "invalid_request",
        "messages": ["I want to travel yesterday"],
    },
    {
        "id": "booking_lookup_or_profile_route",
        "messages": ["show my bookings"],
    },
]


@dataclass
class TimingRow:
    case_id: str
    iteration: int
    elapsed_ms: float
    success: bool
    final_reply: str


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
            return {"reply": response.text, "statusCode": response.status_code}
        final_response = response.json()
        session_id = int(final_response.get("sessionId") or session_id)
    return final_response


def _run_once(client: Any, messages: list[str]) -> tuple[float, bool, str]:
    start = time.perf_counter()
    if client is None:
        response = _send_direct(messages)
    else:
        response = _send_test_client(client, messages)
    elapsed_ms = (time.perf_counter() - start) * 1000
    success = not response.get("statusCode") and bool(response.get("reply"))
    return elapsed_ms, success, str(response.get("reply") or "")[:500]


def _patches() -> ExitStack:
    stack = ExitStack()
    stack.enter_context(patch("app.services.chat_service._search_flights_with_fallback", side_effect=_flight_response))
    stack.enter_context(patch("app.services.chat_service._search_hotels_with_fallback", side_effect=_hotel_response))
    stack.enter_context(patch("app.main._search_flights_with_fallback", side_effect=_flight_response))
    stack.enter_context(patch("app.main._search_hotels_with_fallback", side_effect=_hotel_response))
    stack.enter_context(patch("app.destination_discovery.get_destination_evidence", side_effect=_empty_evidence))
    stack.enter_context(patch("app.services.chat_service.route_tool_with_llama", side_effect=_benchmark_route_tool))
    stack.enter_context(patch("app.services.chat_service.extract_flight_search_with_llama", side_effect=_benchmark_extract_flight))
    stack.enter_context(patch("app.services.chat_service.today_utc_date", return_value=FIXED_TODAY))
    return stack


def run_tests() -> list[TimingRow]:
    _reset_database()
    rows: list[TimingRow] = []
    with _patches():
        if TestClient is None:
            client_context = None
        else:
            client_context = TestClient(app)
        if client_context is None:
            for case in TEST_CASES:
                for iteration in range(1, ITERATIONS + 1):
                    elapsed, success, reply = _run_once(None, case["messages"])
                    rows.append(TimingRow(case["id"], iteration, elapsed, success, reply))
        else:
            with client_context as client:
                for case in TEST_CASES:
                    for iteration in range(1, ITERATIONS + 1):
                        elapsed, success, reply = _run_once(client, case["messages"])
                        rows.append(TimingRow(case["id"], iteration, elapsed, success, reply))
    return rows


def _write_csv(rows: list[TimingRow]) -> None:
    with (EVALUATION_DIR / "response_time_results.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["case_id", "iteration", "elapsed_ms", "success", "final_reply"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "case_id": row.case_id,
                    "iteration": row.iteration,
                    "elapsed_ms": f"{row.elapsed_ms:.2f}",
                    "success": row.success,
                    "final_reply": row.final_reply.replace("\n", " "),
                }
            )


def _summary_for_case(rows: list[TimingRow]) -> dict[str, Any]:
    values = [row.elapsed_ms for row in rows]
    success = all(row.success for row in rows)
    return {
        "average": statistics.mean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "result": "Pass" if success else "Fail",
    }


def _write_summary(rows: list[TimingRow]) -> None:
    grouped: dict[str, list[TimingRow]] = {}
    for row in rows:
        grouped.setdefault(row.case_id, []).append(row)

    table_rows = []
    for case_id, case_rows in grouped.items():
        summary = _summary_for_case(case_rows)
        table_rows.append(
            "| {case} | {avg:.2f} ms | {median:.2f} ms | {min:.2f} ms | {max:.2f} ms | {result} |".format(
                case=case_id,
                avg=summary["average"],
                median=summary["median"],
                min=summary["min"],
                max=summary["max"],
                result=summary["result"],
            )
        )

    content = f"""# Voyage AI Response-Time Summary

Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

Each test case was run {ITERATIONS} times using deterministic provider and router stubs.

| Test case | Average time | Median time | Min | Max | Result |
| --- | ---: | ---: | ---: | ---: | --- |
{chr(10).join(table_rows)}

## Thesis Interpretation Notes

- The response-time evaluation measures the backend chat workflow under controlled provider conditions.
- The timings focus on application orchestration, routing, validation and state handling rather than live supplier latency.
- Live provider and live local Llama timings may differ from these controlled measurements.
"""
    (EVALUATION_DIR / "response_time_summary.md").write_text(content, encoding="utf-8")


def main() -> None:
    rows = run_tests()
    _write_csv(rows)
    _write_summary(rows)
    print(f"Ran {len(TEST_CASES)} response-time cases with {ITERATIONS} iterations each.")
    print("Wrote evaluation/response_time_results.csv")
    print("Wrote evaluation/response_time_summary.md")


if __name__ == "__main__":
    main()
