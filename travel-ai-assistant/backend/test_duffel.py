import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parent
ENV_PATH = BACKEND_DIR / ".env"
DEFAULT_BASE_URL = "https://api.duffel.com"


def summarize_json(payload: Any) -> None:
    if not isinstance(payload, dict):
        print(f"Response JSON type: {type(payload).__name__}")
        return

    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        print(f"Errors returned: {len(errors)}")
        for error in errors[:3]:
            if not isinstance(error, dict):
                continue
            code = error.get("code", "unknown_code")
            title = error.get("title", "Unknown error")
            detail = error.get("detail")
            if detail:
                print(f"- {code}: {title} - {detail}")
            else:
                print(f"- {code}: {title}")
        return

    data = payload.get("data")
    if isinstance(data, list):
        print(f"Airports returned: {len(data)}")
        for airport in data[:5]:
            if not isinstance(airport, dict):
                continue
            iata_code = airport.get("iata_code") or airport.get("iata_city_code") or "N/A"
            name = airport.get("name", "Unknown airport")
            city_name = airport.get("city_name", "Unknown city")
            country_code = airport.get("country_code", "N/A")
            print(f"- {iata_code}: {name} ({city_name}, {country_code})")
        return

    print(f"Response keys: {', '.join(sorted(payload.keys())) or 'none'}")


def main() -> int:
    if ENV_PATH.exists() and not ENV_PATH.is_file():
        print("Missing DUFFEL_API_KEY in backend/.env")
        print("backend/.env exists but is not a file.")
        return 1

    load_dotenv(dotenv_path=ENV_PATH)

    api_key = os.getenv("DUFFEL_API_KEY", "").strip()
    if not api_key:
        print("Missing DUFFEL_API_KEY in backend/.env")
        return 1

    if not api_key.startswith("duffel_test_"):
        print("Warning: this does not look like a test token.")
        print("Stopping before making a Duffel request.")
        return 1

    base_url = os.getenv("DUFFEL_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    url = f"{base_url.rstrip('/')}/air/airports?limit=5"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Duffel-Version": "v2",
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        print(f"Duffel request failed: {exc}")
        return 1

    print(f"Status code: {response.status_code}")

    try:
        payload = response.json()
    except ValueError:
        print(f"Response was not JSON. Body length: {len(response.text)} bytes")
        return 1

    summarize_json(payload)
    return 0 if response.ok else 1


if __name__ == "__main__":
    sys.exit(main())
