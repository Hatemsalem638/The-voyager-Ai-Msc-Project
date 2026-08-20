"""Safe OpenTripMap evidence provider for destination discovery."""

import logging
import os
from pathlib import Path
import re
from typing import Any

import requests
from dotenv import load_dotenv


LOGGER = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BACKEND_DIR / ".env"
DEFAULT_BASE_URL = "https://api.opentripmap.com/0.1/en"
TIMEOUT_SECONDS = 12


def _settings() -> tuple[str, str | None]:
    load_dotenv(dotenv_path=ENV_PATH)
    base_url = os.getenv("OPENTRIPMAP_BASE_URL", DEFAULT_BASE_URL).strip()
    api_key = os.getenv("OPENTRIPMAP_API_KEY")
    return base_url.rstrip("/") or DEFAULT_BASE_URL, api_key.strip() if api_key else None


def search_places_radius(
    lat: float,
    lon: float,
    radius: int = 10000,
    kinds: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return compact place evidence near coordinates, or empty on failure."""

    base_url, api_key = _settings()
    if not api_key:
        return []

    params: dict[str, Any] = {
        "apikey": api_key,
        "lat": lat,
        "lon": lon,
        "radius": radius,
        "limit": max(1, min(limit, 50)),
        "format": "json",
        "rate": 2,
    }
    if kinds:
        params["kinds"] = kinds

    try:
        response = requests.get(
            f"{base_url}/places/radius",
            params=params,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        LOGGER.warning("OpenTripMap evidence request failed: %s", exc.__class__.__name__)
        return []
    except ValueError:
        LOGGER.warning("OpenTripMap evidence response was not valid JSON.")
        return []

    if not isinstance(payload, list):
        return []

    evidence: list[dict[str, Any]] = []
    for item in payload[: max(1, min(limit, 50))]:
        if not isinstance(item, dict):
            continue
        evidence.append(
            {
                "name": str(item.get("name") or "").strip()[:120],
                "kinds": str(item.get("kinds") or "")[:300],
            }
        )
    return evidence


def classify_evidence_for_preferences(
    places: list[dict[str, Any]],
    preferences: dict[str, Any],
) -> dict[str, Any]:
    text = " ".join(
        f"{place.get('name', '')} {place.get('kinds', '')}".lower()
        for place in places
        if isinstance(place, dict)
    )
    beach_terms = ("beach", "sea", "water", "coast", "marine", "aquatic")
    strong_ski_patterns = (
        r"\bski(?:ing)?\b",
        r"\bski resort\b",
        r"\bwinter sports?\b",
        r"\bsnow\b",
        r"\balpine\b",
        r"\bpiste\b",
        r"\bslope\b",
        r"\bcable car\b",
        r"\bchairlift\b",
        r"\bgondola\b",
        r"\bmountain resort\b",
    )
    family_terms = ("amusement", "park", "zoo", "museum", "water", "aquarium")
    tourism_terms = ("tourist", "historic", "museum", "view", "natural", "cultural")
    names = []
    ski_names = []
    for place in places:
        if not isinstance(place, dict):
            continue
        name = str(place.get("name") or "").strip()
        if name:
            names.append(name)
        place_text = f"{place.get('name', '')} {place.get('kinds', '')}".lower()
        if name and any(re.search(pattern, place_text) for pattern in strong_ski_patterns):
            ski_names.append(name)

    return {
        "beachEvidenceCount": sum(term in text for term in beach_terms),
        "skiEvidenceCount": sum(
            1 for pattern in strong_ski_patterns if re.search(pattern, text)
        ),
        "familyEvidenceCount": sum(term in text for term in family_terms),
        "tourismEvidenceCount": sum(term in text for term in tourism_terms),
        "evidenceNames": names[:5],
        "skiEvidenceNames": ski_names[:5],
        "preferencesChecked": preferences,
    }


def get_destination_evidence(
    destination: dict[str, Any],
    preferences: dict[str, Any],
) -> dict[str, Any]:
    places = search_places_radius(
        float(destination["lat"]),
        float(destination["lon"]),
        radius=15000,
        limit=25,
    )
    return classify_evidence_for_preferences(places, preferences)
