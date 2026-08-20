import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from pydantic import ValidationError

from app.schemas import FlightSearchRequest


BACKEND_DIR = Path(__file__).resolve().parent
ENV_PATH = BACKEND_DIR / ".env"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b-instruct-q4_0"
REQUIRED_OLLAMA_MODEL = "llama3.1:8b-instruct-q4_0"
REQUEST_TIMEOUT_SECONDS = 90

TEST_MESSAGE = (
    "I want to fly from London Heathrow to New York on 12 September 2026 and "
    "return on 19 September 2026. I want economy and no stops."
)

EXPECTED_SCHEMA = {
    "searchType": "flight",
    "passengerCount": 1,
    "itinerary": {
        "tripType": "round_trip",
        "origin": {
            "city": "London",
            "code": "LHR",
        },
        "destination": {
            "city": "New York",
            "code": "JFK",
        },
        "dates": {
            "departure": "2026-09-12",
            "return": "2026-09-19",
        },
    },
    "preferences": {
        "cabinClass": "economy",
        "maxStops": 0,
    },
}

JSON_FORMAT_SCHEMA = {
    "type": "object",
    "properties": {
        "searchType": {"type": "string", "const": "flight"},
        "passengerCount": {"type": "integer", "minimum": 1},
        "itinerary": {
            "type": "object",
            "properties": {
                "tripType": {"type": "string", "enum": ["one_way", "round_trip"]},
                "origin": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "code": {"type": "string"},
                    },
                    "required": ["city", "code"],
                },
                "destination": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "code": {"type": "string"},
                    },
                    "required": ["city", "code"],
                },
                "dates": {
                    "type": "object",
                    "properties": {
                        "departure": {"type": "string"},
                        "return": {"type": "string"},
                    },
                    "required": ["departure", "return"],
                },
            },
            "required": ["tripType", "origin", "destination", "dates"],
        },
        "preferences": {
            "type": "object",
            "properties": {
                "cabinClass": {
                    "type": "string",
                    "enum": ["economy", "premium_economy", "business", "first"],
                },
                "maxStops": {"type": "integer", "minimum": 0},
            },
            "required": ["cabinClass", "maxStops"],
        },
    },
    "required": ["searchType", "passengerCount", "itinerary", "preferences"],
}


def _load_settings() -> tuple[str, str]:
    load_dotenv(dotenv_path=ENV_PATH)
    base_url = os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).strip()
    model = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip()
    selected_model = model or DEFAULT_OLLAMA_MODEL
    if selected_model != REQUIRED_OLLAMA_MODEL:
        raise ValueError(
            "Invalid OLLAMA_MODEL. This project is configured to use "
            "llama3.1:8b-instruct-q4_0. Please update backend/.env."
        )
    return base_url or DEFAULT_OLLAMA_BASE_URL, selected_model


def _build_payload(model: str) -> dict[str, Any]:
    expected_json = json.dumps(EXPECTED_SCHEMA, indent=2)
    return {
        "model": model,
        "stream": False,
        "format": JSON_FORMAT_SCHEMA,
        "options": {
            "temperature": 0,
        },
        "messages": [
            {
                "role": "system",
                "content": (
                    "You convert travel requests into structured flight search "
                    "JSON. Return only valid JSON. Do not include markdown, "
                    "comments, explanations, or extra keys. The JSON must match "
                    f"this schema and field naming exactly:\n{expected_json}"
                ),
            },
            {
                "role": "user",
                "content": TEST_MESSAGE,
            },
        ],
    }


def _extract_message_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Ollama response was not a JSON object.")

    message = payload.get("message")
    if not isinstance(message, dict):
        raise ValueError("Ollama response did not include message content.")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Ollama message content was empty.")

    return content


def _strip_json_fence(value: str) -> str:
    stripped = value.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _print_failure_hint() -> None:
    print(
        "Ollama is not reachable. Start Ollama and run: "
        "ollama pull llama3.1:8b-instruct-q4_0"
    )


def main() -> int:
    base_url, model = _load_settings()
    url = f"{base_url.rstrip('/')}/api/chat"

    print(f"Ollama base URL: {base_url}")
    print(f"Model: {model}")

    try:
        response = requests.post(
            url,
            json=_build_payload(model),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.ConnectionError:
        _print_failure_hint()
        return 1
    except requests.Timeout:
        print("Ollama request timed out.")
        print("If the model is still downloading or loading, wait and try again.")
        return 1
    except requests.RequestException as exc:
        print(f"Ollama request failed: {exc}")
        return 1

    if not response.ok:
        print(f"Ollama HTTP status: {response.status_code}")
        try:
            error_payload = response.json()
        except ValueError:
            print(response.text[:500])
        else:
            error_message = error_payload.get("error") if isinstance(
                error_payload,
                dict,
            ) else None
            print(f"Ollama error: {error_message or error_payload}")
            if response.status_code == 404:
                print("Check that the model is installed: ollama pull llama3.1:8b-instruct-q4_0")
        return 1

    try:
        response_payload = response.json()
        content = _extract_message_content(response_payload)
        parsed_json = json.loads(_strip_json_fence(content))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        print("JSON parsing succeeded: false")
        print("Schema validation succeeded: false")
        print(f"Parsing error: {exc}")
        return 1

    print("JSON parsing succeeded: true")

    try:
        validated_request = FlightSearchRequest.model_validate(parsed_json)
    except ValidationError as exc:
        print("Schema validation succeeded: false")
        print(exc)
        print("Final parsed JSON:")
        print(json.dumps(parsed_json, indent=2))
        return 1

    final_json = validated_request.model_dump(by_alias=True, mode="json")
    print("Schema validation succeeded: true")
    print("Final parsed JSON:")
    print(json.dumps(final_json, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
