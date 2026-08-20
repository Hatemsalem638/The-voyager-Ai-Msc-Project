"""Standalone LiteAPI hotel-rates connection test.

This script intentionally does not integrate LiteAPI with the FastAPI app. It
uses the documented POST /hotels/rates endpoint with a minimal one-hotel query
and only prints a compact, redacted response summary.
"""

from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from dotenv import load_dotenv
import requests


BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_BASE_URL = "https://api.liteapi.travel/v3.0"
REQUEST_TIMEOUT_SECONDS = 20


def _redact_and_truncate(value: Any, api_key: str, limit: int = 800) -> str:
    """Produce a compact response preview without leaking credential-shaped data."""

    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    else:
        text = str(value or "")

    if api_key:
        text = text.replace(api_key, "[redacted]")
    text = re.sub(
        r"(?i)((?:x-api-key|authorization|api[_ -]?key))\s*[:=]\s*[^,\s}\]]+",
        r"\1=[redacted]",
        text,
    )
    return text[:limit] + ("..." if len(text) > limit else "")


def _response_summary(payload: Any) -> str:
    if isinstance(payload, list):
        return f"Response list items: {len(payload)}"
    if not isinstance(payload, dict):
        return f"Response type: {type(payload).__name__}"

    keys = ", ".join(sorted(str(key) for key in payload.keys())[:8]) or "none"
    data = payload.get("data")
    if isinstance(data, list):
        return f"Response keys: {keys}; data items: {len(data)}"
    if isinstance(data, dict):
        return f"Response keys: {keys}; data object returned"
    return f"Response keys: {keys}"


def build_test_payload() -> dict[str, Any]:
    """Build the documented minimum rate-search request with future dates."""

    checkin = date.today() + timedelta(days=60)
    checkout = checkin + timedelta(days=2)
    return {
        "hotelIds": ["lp1897"],
        "occupancies": [{"rooms": 1, "adults": 1}],
        "currency": "USD",
        "guestNationality": "US",
        "checkin": checkin.isoformat(),
        "checkout": checkout.isoformat(),
        "timeout": 6,
        "maxRatesPerHotel": 1,
    }


def main() -> int:
    load_dotenv(BACKEND_DIR / ".env")

    api_key = os.getenv("LITEAPI_KEY", "").strip()
    base_url = os.getenv("LITEAPI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    print(f"LiteAPI key configured: {'yes' if api_key else 'no'}")
    print(f"LiteAPI base URL: {base_url}")

    if not api_key:
        print("Missing LITEAPI_KEY in backend/.env")
        return 1

    try:
        response = requests.post(
            f"{base_url}/hotels/rates",
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=build_test_payload(),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        print(f"LiteAPI request timed out after {REQUEST_TIMEOUT_SECONDS} seconds.")
        return 1
    except requests.RequestException as exc:
        print(f"LiteAPI connection error: {exc.__class__.__name__}")
        return 1

    print(f"Status code: {response.status_code}")

    try:
        body: Any = response.json() if response.content else {}
    except ValueError:
        body = response.text

    if response.ok:
        print(f"Safe response summary: {_response_summary(body)}")
        return 0

    print(f"Safe error body: {_redact_and_truncate(body, api_key)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
