"""SQLite-backed safe conversation memory and user preference helpers."""

from copy import deepcopy
from datetime import date, datetime
import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models import ChatSession, SessionState, UserPreference
from app.schemas import (
    ActiveTripState,
    DestinationDiscoveryRequest,
    DestinationRecommendation,
    NormalisedFlightResult,
    NormalisedHotelResult,
)


LOGGER = logging.getLogger(__name__)
PREFERENCE_FIELDS = {
    "preferred_cabin_class",
    "max_stops",
    "preferred_departure_time",
    "budget_min",
    "budget_max",
    "preferred_hotel_rating",
    "hotel_location_preference",
    "accessibility_needs_json",
    "sustainability_preference",
}


def _safe_fact_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(
        r"duffel_(?:test|live)_[A-Za-z0-9_\-]+",
        "[redacted-secret]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?i)\b(?:api[_ -]?key|token)\s*[:=]\s*\S+",
        "[redacted-secret]",
        text,
    )
    return text[:240]


def _json_compatible(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        try:
            return _json_compatible(value.model_dump(by_alias=True, mode="json"))
        except TypeError:
            return _json_compatible(value.model_dump())
    if hasattr(value, "dict"):
        return _json_compatible(value.dict())
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return str(value)


def json_dumps_safe(value: Any) -> str | None:
    """Serialise Pydantic values and standard Python data without raising."""

    if value is None:
        return None
    try:
        return json.dumps(_json_compatible(value), separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        LOGGER.warning("Unable to serialise persistent memory value: %s", exc.__class__.__name__)
        return None


def json_loads_safe(value: str | None, default: Any) -> Any:
    """Load JSON with a safe independent default when storage is empty or invalid."""

    if not value:
        return deepcopy(default)
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        LOGGER.warning("Ignoring malformed persistent memory JSON.")
        return deepcopy(default)


def get_or_create_session_state(db: Session, session_id: int) -> SessionState:
    state = (
        db.query(SessionState)
        .filter(SessionState.session_id == session_id)
        .first()
    )
    if state is None:
        state = SessionState(session_id=session_id)
        db.add(state)
        db.flush()
    return state


def _normalise_active_trip(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    try:
        return ActiveTripState.model_validate(value).model_dump(
            by_alias=True,
            mode="json",
        )
    except (TypeError, ValueError):
        return ActiveTripState().model_dump(by_alias=True, mode="json")


def load_active_trip(db: Session, session_id: int) -> dict[str, Any]:
    """Load the canonical active trip state for a session."""

    state = (
        db.query(SessionState)
        .filter(SessionState.session_id == session_id)
        .first()
    )
    if state is None:
        return _normalise_active_trip(None)
    return _normalise_active_trip(json_loads_safe(state.active_trip_json, {}))


def save_active_trip(db: Session, session_id: int, active_trip: Any) -> dict[str, Any]:
    """Persist canonical trip state and return the normalised representation."""

    state = get_or_create_session_state(db, session_id)
    safe_trip = _normalise_active_trip(active_trip)
    state.active_trip_json = json_dumps_safe(safe_trip)
    db.flush()
    return safe_trip


def clear_dependent_travel_state(
    db: Session,
    session_id: int,
    *,
    clear_flights: bool = True,
    clear_hotels: bool = True,
    clear_destinations: bool = False,
    clear_pending: bool = True,
) -> None:
    """Clear state that depends on a stale active-trip destination or date."""

    state = get_or_create_session_state(db, session_id)
    if clear_flights:
        state.latest_flight_search_request_json = None
        state.latest_flight_results_json = None
        state.current_flight_option_json = None
        state.pending_flight_selection_json = None
    if clear_hotels:
        state.latest_hotel_search_request_json = None
        state.latest_hotel_results_json = None
        state.current_hotel_option_json = None
        state.pending_hotel_selection_json = None
    if clear_destinations:
        state.latest_destination_discovery_request_json = None
        state.latest_destination_discovery_results_json = None
    if clear_pending:
        state.pending_action = None
    db.flush()


def _normalise_flight(value: Any) -> dict[str, Any] | None:
    try:
        return NormalisedFlightResult.model_validate(value).model_dump(mode="json")
    except (TypeError, ValueError):
        return None


def _normalise_results(value: Any) -> list[dict[str, Any]]:
    candidates = getattr(value, "flights", value)
    if not isinstance(candidates, list):
        return []
    return [
        normalised
        for item in candidates
        for normalised in [_normalise_flight(item)]
        if normalised is not None
    ]


def _normalise_hotel(value: Any) -> dict[str, Any] | None:
    try:
        return NormalisedHotelResult.model_validate(value).model_dump(mode="json")
    except (TypeError, ValueError):
        return None


def _normalise_hotel_results(value: Any) -> list[dict[str, Any]]:
    candidates = getattr(value, "results", value)
    if not isinstance(candidates, list):
        return []
    return [
        normalised
        for item in candidates
        for normalised in [_normalise_hotel(item)]
        if normalised is not None
    ]


def _normalise_destination(value: Any) -> dict[str, Any] | None:
    try:
        return DestinationRecommendation.model_validate(value).model_dump(mode="json")
    except (TypeError, ValueError):
        return None


def _normalise_destination_results(value: Any) -> list[dict[str, Any]]:
    candidates = getattr(value, "recommendations", value)
    if not isinstance(candidates, list):
        return []
    return [
        normalised
        for item in candidates
        for normalised in [_normalise_destination(item)]
        if normalised is not None
    ]


def load_session_memory(db: Session, session_id: int) -> dict[str, Any]:
    state = (
        db.query(SessionState)
        .filter(SessionState.session_id == session_id)
        .first()
    )
    if state is None:
        return {
            "search_request": None,
            "flights": [],
            "current_flight_option": None,
            "pending_flight_selection": None,
            "hotel_search_request": None,
            "hotels": [],
            "current_hotel_option": None,
            "pending_hotel_selection": None,
            "destination_discovery_request": None,
            "destination_recommendations": [],
            "pending_destination_discovery": None,
            "active_trip": _normalise_active_trip(None),
            "default_origin": None,
            "pending_action": None,
            "last_tool": None,
            "recent_facts": [],
        }

    return {
        "search_request": json_loads_safe(
            state.latest_flight_search_request_json,
            None,
        ),
        "flights": json_loads_safe(state.latest_flight_results_json, []),
        "current_flight_option": json_loads_safe(
            state.current_flight_option_json,
            None,
        ),
        "pending_flight_selection": json_loads_safe(
            state.pending_flight_selection_json,
            None,
        ),
        "hotel_search_request": json_loads_safe(
            state.latest_hotel_search_request_json,
            None,
        ),
        "hotels": json_loads_safe(state.latest_hotel_results_json, []),
        "current_hotel_option": json_loads_safe(
            state.current_hotel_option_json,
            None,
        ),
        "pending_hotel_selection": json_loads_safe(
            state.pending_hotel_selection_json,
            None,
        ),
        "destination_discovery_request": json_loads_safe(
            state.latest_destination_discovery_request_json,
            None,
        ),
        "destination_recommendations": json_loads_safe(
            state.latest_destination_discovery_results_json,
            [],
        ),
        "pending_destination_discovery": json_loads_safe(
            state.pending_destination_discovery_json,
            None,
        ),
        "active_trip": _normalise_active_trip(
            json_loads_safe(state.active_trip_json, {})
        ),
        "default_origin": json_loads_safe(state.default_origin_json, None),
        "pending_action": state.pending_action,
        "last_tool": state.last_tool,
        "recent_facts": json_loads_safe(state.recent_facts_json, []),
    }


def save_latest_flight_search(
    db: Session,
    session_id: int,
    search_request: Any,
    results: Any,
) -> None:
    """Save only the validated request and normalised flight records."""

    state = get_or_create_session_state(db, session_id)
    safe_results = _normalise_results(results)
    state.latest_flight_search_request_json = json_dumps_safe(search_request)
    state.latest_flight_results_json = json_dumps_safe(safe_results)
    state.current_flight_option_json = json_dumps_safe(
        {"option_number": 1, "flight": safe_results[0]}
        if safe_results
        else None
    )
    state.pending_flight_selection_json = None
    state.pending_hotel_selection_json = None
    state.pending_destination_discovery_json = None
    state.pending_action = None
    state.last_tool = "search_flights"
    db.flush()


def save_current_flight_option(
    db: Session,
    session_id: int,
    option_number: int | None,
    flight: Any = None,
) -> None:
    state = get_or_create_session_state(db, session_id)
    if option_number is None:
        state.current_flight_option_json = None
    else:
        state.current_flight_option_json = json_dumps_safe(
            {
                "option_number": option_number,
                "flight": _normalise_flight(flight) if flight is not None else None,
            }
        )
    db.flush()


def save_pending_flight_selection(
    db: Session,
    session_id: int,
    option_number: int | None,
    flight: Any,
    pending_action: str = "confirm_flight_booking",
) -> None:
    state = get_or_create_session_state(db, session_id)
    safe_flight = _normalise_flight(flight)
    if safe_flight is None:
        raise ValueError("Pending flight selection must be a normalised flight result.")
    state.pending_flight_selection_json = json_dumps_safe(
        {"option_number": option_number, "flight": safe_flight}
    )
    state.pending_hotel_selection_json = None
    state.pending_action = pending_action
    db.flush()


def clear_pending_flight_selection(db: Session, session_id: int) -> None:
    state = get_or_create_session_state(db, session_id)
    state.pending_flight_selection_json = None
    state.pending_action = None
    db.flush()


def save_latest_hotel_search(
    db: Session,
    session_id: int,
    search_request: Any,
    results: Any,
) -> None:
    """Persist only validated request data and normalised hotel cards."""

    state = get_or_create_session_state(db, session_id)
    safe_results = _normalise_hotel_results(results)
    state.latest_hotel_search_request_json = json_dumps_safe(search_request)
    state.latest_hotel_results_json = json_dumps_safe(safe_results)
    state.current_hotel_option_json = json_dumps_safe(
        {"option_number": 1, "hotel": safe_results[0]} if safe_results else None
    )
    state.pending_hotel_selection_json = None
    state.pending_flight_selection_json = None
    state.pending_destination_discovery_json = None
    state.pending_action = None
    state.last_tool = "search_hotels"
    db.flush()


def save_current_hotel_option(
    db: Session,
    session_id: int,
    option_number: int | None,
    hotel: Any = None,
) -> None:
    state = get_or_create_session_state(db, session_id)
    state.current_hotel_option_json = (
        json_dumps_safe(
            {
                "option_number": option_number,
                "hotel": _normalise_hotel(hotel) if hotel is not None else None,
            }
        )
        if option_number is not None
        else None
    )
    db.flush()


def save_pending_hotel_selection(
    db: Session,
    session_id: int,
    option_number: int | None,
    hotel: Any,
    pending_action: str = "confirm_hotel_booking",
) -> None:
    state = get_or_create_session_state(db, session_id)
    safe_hotel = _normalise_hotel(hotel)
    if safe_hotel is None:
        raise ValueError("Pending hotel selection must be a normalised hotel result.")
    state.pending_hotel_selection_json = json_dumps_safe(
        {"option_number": option_number, "hotel": safe_hotel}
    )
    state.pending_flight_selection_json = None
    state.pending_action = pending_action
    db.flush()


def clear_pending_hotel_selection(db: Session, session_id: int) -> None:
    state = get_or_create_session_state(db, session_id)
    state.pending_hotel_selection_json = None
    state.pending_action = None
    db.flush()


def save_latest_destination_discovery(
    db: Session,
    session_id: int,
    discovery_request: Any,
    recommendations: Any,
) -> None:
    state = get_or_create_session_state(db, session_id)
    state.latest_destination_discovery_request_json = json_dumps_safe(discovery_request)
    state.latest_destination_discovery_results_json = json_dumps_safe(
        _normalise_destination_results(recommendations)
    )
    state.pending_destination_discovery_json = None
    state.last_tool = "discover_destinations"
    db.flush()


def _normalise_destination_request(value: Any) -> dict[str, Any] | None:
    try:
        return DestinationDiscoveryRequest.model_validate(value).model_dump(
            by_alias=True,
            mode="json",
        )
    except (TypeError, ValueError):
        return None


def save_pending_destination_discovery(
    db: Session,
    session_id: int,
    discovery_request: Any,
) -> None:
    state = get_or_create_session_state(db, session_id)
    safe_request = _normalise_destination_request(discovery_request)
    state.pending_destination_discovery_json = json_dumps_safe(safe_request)
    state.last_tool = "pending_destination_discovery"
    db.flush()


def clear_pending_destination_discovery(db: Session, session_id: int) -> None:
    state = get_or_create_session_state(db, session_id)
    state.pending_destination_discovery_json = None
    db.flush()


def save_default_origin(db: Session, session_id: int, default_origin: Any) -> None:
    state = get_or_create_session_state(db, session_id)
    state.default_origin_json = json_dumps_safe(default_origin)
    db.flush()


def save_last_tool(db: Session, session_id: int, tool_name: str) -> None:
    state = get_or_create_session_state(db, session_id)
    state.last_tool = tool_name[:80]
    db.flush()


def append_recent_fact(
    db: Session,
    session_id: int,
    fact: Any,
    max_facts: int = 10,
) -> None:
    state = get_or_create_session_state(db, session_id)
    facts = json_loads_safe(state.recent_facts_json, [])
    if not isinstance(facts, list):
        facts = []
    if isinstance(fact, dict):
        safe_fact = {
            "sender": str(fact.get("sender") or "assistant")[:20],
            "text": _safe_fact_text(fact.get("text")),
        }
    else:
        safe_fact = _safe_fact_text(fact)
    facts.append(safe_fact)
    state.recent_facts_json = json_dumps_safe(facts[-max(1, max_facts):])
    db.flush()


def get_or_create_user_preferences(db: Session, user_id: int) -> UserPreference:
    preferences = (
        db.query(UserPreference)
        .filter(UserPreference.user_id == user_id)
        .first()
    )
    if preferences is None:
        preferences = UserPreference(user_id=user_id)
        db.add(preferences)
        db.flush()
    return preferences


def update_user_preferences(
    db: Session,
    user_id: int,
    **kwargs: Any,
) -> UserPreference:
    preferences = get_or_create_user_preferences(db, user_id)
    for field, value in kwargs.items():
        if field not in PREFERENCE_FIELDS:
            continue
        if field == "accessibility_needs_json":
            value = json_dumps_safe(value) if value is not None else None
        setattr(preferences, field, value)
    db.flush()
    return preferences


def load_user_preferences(db: Session, user_id: int) -> dict[str, Any]:
    preferences = get_or_create_user_preferences(db, user_id)
    return {
        "preferred_cabin_class": preferences.preferred_cabin_class,
        "max_stops": preferences.max_stops,
        "preferred_departure_time": preferences.preferred_departure_time,
        "budget_min": preferences.budget_min,
        "budget_max": preferences.budget_max,
        "preferred_hotel_rating": preferences.preferred_hotel_rating,
        "hotel_location_preference": preferences.hotel_location_preference,
        "accessibility_needs": json_loads_safe(
            preferences.accessibility_needs_json,
            None,
        ),
        "sustainability_preference": preferences.sustainability_preference,
    }


def _flight_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    flight = value.get("flight") if isinstance(value.get("flight"), dict) else value
    return {
        "option_number": value.get("option_number") or flight.get("option_number"),
        "airline": flight.get("airline"),
        "origin": flight.get("origin"),
        "destination": flight.get("destination"),
        "outbound_departure_date": flight.get("outbound_departure_date"),
        "outbound_departure_time": flight.get("outbound_departure_time"),
        "return_departure_date": flight.get("return_departure_date"),
        "return_departure_time": flight.get("return_departure_time"),
        "cabin_class": flight.get("cabin_class"),
        "passenger_count": flight.get("passenger_count"),
        "stops": flight.get("stops"),
        "price": flight.get("price"),
        "currency": flight.get("currency"),
    }


def _hotel_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    hotel = value.get("hotel") if isinstance(value.get("hotel"), dict) else value
    return {
        "option_number": value.get("option_number") or hotel.get("option_number"),
        "hotel_name": hotel.get("hotel_name"),
        "location": hotel.get("location"),
        "check_in_date": hotel.get("check_in_date"),
        "check_out_date": hotel.get("check_out_date"),
        "guest_count": hotel.get("guest_count"),
        "room_count": hotel.get("room_count"),
        "rating": hotel.get("rating"),
        "price": hotel.get("price"),
        "currency": hotel.get("currency"),
        "provider": hotel.get("provider"),
    }


def _safe_destination_origin(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "city": value.get("city"),
        "code": value.get("code"),
        "airportName": value.get("airportName"),
    }


def _active_context_type(last_tool: Any) -> str | None:
    tool_name = str(last_tool or "")
    if "destination" in tool_name:
        return "destination"
    if "hotel" in tool_name:
        return "hotel"
    if "flight" in tool_name:
        return "flight"
    if "checkout" in tool_name:
        return "checkout"
    return None


def load_safe_llama_context(db: Session, session_id: int) -> dict[str, Any]:
    """Return compact state appropriate for Llama routing, without secrets."""

    memory = load_session_memory(db, session_id)
    search_request = memory["search_request"]
    flights = memory["flights"] if isinstance(memory["flights"], list) else []
    current = _flight_summary(memory["current_flight_option"])
    pending = _flight_summary(memory["pending_flight_selection"])
    hotel_search_request = memory["hotel_search_request"]
    hotels = memory["hotels"] if isinstance(memory["hotels"], list) else []
    current_hotel = _hotel_summary(memory["current_hotel_option"])
    pending_hotel = _hotel_summary(memory["pending_hotel_selection"])
    destination_request = memory["destination_discovery_request"]
    pending_destination_request = memory["pending_destination_discovery"]
    destination_recommendations = (
        memory["destination_recommendations"]
        if isinstance(memory["destination_recommendations"], list)
        else []
    )
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    preferences = load_user_preferences(db, session.user_id) if session else {}

    route = {"origin": None, "destination": None}
    flight_route_details = {
        "origin": {"city": None, "code": None},
        "destination": {"city": None, "code": None},
    }
    dates = {"departure": None, "return": None}
    flight_preferences = {"passenger_count": None, "cabin_class": None, "max_stops": None}
    if isinstance(search_request, dict):
        itinerary = search_request.get("itinerary")
        if isinstance(itinerary, dict):
            origin = itinerary.get("origin")
            destination = itinerary.get("destination")
            date_values = itinerary.get("dates")
            route = {
                "origin": origin.get("code") if isinstance(origin, dict) else None,
                "destination": destination.get("code")
                if isinstance(destination, dict)
                else None,
            }
            flight_route_details = {
                "origin": {
                    "city": origin.get("city") if isinstance(origin, dict) else None,
                    "code": origin.get("code") if isinstance(origin, dict) else None,
                },
                "destination": {
                    "city": destination.get("city") if isinstance(destination, dict) else None,
                    "code": destination.get("code") if isinstance(destination, dict) else None,
                },
            }
            dates = {
                "departure": date_values.get("departure")
                if isinstance(date_values, dict)
                else None,
                "return": date_values.get("return")
                if isinstance(date_values, dict)
                else None,
            }
        preferences = search_request.get("preferences")
        flight_preferences = {
            "passenger_count": search_request.get("passengerCount"),
            "cabin_class": preferences.get("cabinClass")
            if isinstance(preferences, dict)
            else None,
            "max_stops": preferences.get("maxStops")
            if isinstance(preferences, dict)
            else None,
        }

    hotel_destination = None
    hotel_dates = {"check_in": None, "check_out": None}
    hotel_preferences = {"guest_count": None, "room_count": None}
    if isinstance(hotel_search_request, dict):
        destination = hotel_search_request.get("destination")
        if isinstance(destination, dict):
            hotel_destination = destination.get("city") or destination.get("country")
        hotel_dates = {
            "check_in": hotel_search_request.get("checkInDate"),
            "check_out": hotel_search_request.get("checkOutDate"),
        }
        hotel_preferences = {
            "guest_count": hotel_search_request.get("guestCount"),
            "room_count": hotel_search_request.get("roomCount"),
        }

    active_context_type = _active_context_type(memory["last_tool"])
    default_origin = memory.get("default_origin")
    if not isinstance(default_origin, dict):
        default_origin = None
    else:
        default_origin = {
            "city": default_origin.get("city"),
            "country": default_origin.get("country"),
            "airportCode": default_origin.get("airportCode"),
            "airportName": default_origin.get("airportName"),
            "source": default_origin.get("source"),
        }
    active_trip = memory.get("active_trip")
    if not isinstance(active_trip, dict):
        active_trip = _normalise_active_trip(None)
    selected_destination = active_trip.get("selectedDestination")
    if not isinstance(selected_destination, dict):
        selected_destination = None
    active_origin = active_trip.get("origin")
    if not isinstance(active_origin, dict):
        active_origin = None
    active_dates = active_trip.get("dates")
    if not isinstance(active_dates, dict):
        active_dates = {}
    safe_active_trip = {
        "revision": active_trip.get("revision", 0),
        "origin": {
            "city": active_origin.get("city") if active_origin else None,
            "airportCode": active_origin.get("airportCode") if active_origin else None,
            "airportName": active_origin.get("airportName") if active_origin else None,
            "source": active_origin.get("source") if active_origin else None,
        },
        "selectedDestination": {
            "city": selected_destination.get("city") if selected_destination else None,
            "country": selected_destination.get("country") if selected_destination else None,
            "airportCode": selected_destination.get("airportCode")
            if selected_destination
            else None,
            "airportName": selected_destination.get("airportName")
            if selected_destination
            else None,
            "source": selected_destination.get("source") if selected_destination else None,
        }
        if selected_destination
        else None,
        "destinationRegion": active_trip.get("destinationRegion"),
        "destinationType": active_trip.get("destinationType"),
        "tripFrame": active_trip.get("tripFrame"),
        "destinationCandidates": [
            {
                "city": candidate.get("city"),
                "airportCode": candidate.get("airportCode"),
                "airportName": candidate.get("airportName"),
            }
            for candidate in active_trip.get("destinationCandidates", [])
            if isinstance(candidate, dict)
        ][:8],
        "dates": {
            "departureDate": active_dates.get("departureDate"),
            "returnDate": active_dates.get("returnDate"),
            "source": active_dates.get("source"),
        },
        "dateText": active_trip.get("dateText"),
        "month": active_trip.get("month"),
        "pendingTripRequest": active_trip.get("pendingTripRequest"),
        "pendingAlternativeOriginSearch": active_trip.get(
            "pendingAlternativeOriginSearch"
        ),
        "pendingAction": active_trip.get("pendingAction"),
        "tripDurationNights": active_trip.get("tripDurationNights"),
        "requiredActivities": active_trip.get("requiredActivities", []),
        "activityPreferences": active_trip.get("activityPreferences", []),
        "environmentPreferences": active_trip.get("environmentPreferences", []),
        "regionPreferences": active_trip.get("regionPreferences", []),
        "excludedRegions": active_trip.get("excludedRegions", []),
        "requestedProducts": active_trip.get("requestedProducts", []),
        "status": active_trip.get("status"),
        "passengerCount": active_trip.get("passengerCount", 1),
        "cabinClass": active_trip.get("cabinClass", "economy"),
    }

    return {
        "has_latest_flight_results": bool(flights),
        "latest_flight_count": len(flights),
        "latest_route": route,
        "latest_dates": dates,
        "latest_flight_context": {
            "route": flight_route_details,
            "dates": dates,
            **flight_preferences,
            "result_count": len(flights),
            "current_displayed_option": current.get("option_number") if current else None,
        },
        "latest_search_request_exists": isinstance(search_request, dict),
        "current_displayed_flight": current,
        "selected_pending_flight": pending,
        "has_latest_hotel_results": bool(hotels),
        "latest_hotel_count": len(hotels),
        "latest_hotel_destination": hotel_destination,
        "latest_hotel_dates": hotel_dates,
        "latest_hotel_context": {
            "destination_city": hotel_destination,
            "dates": hotel_dates,
            **hotel_preferences,
            "result_count": len(hotels),
            "current_displayed_option": current_hotel.get("option_number")
            if current_hotel
            else None,
        },
        "latest_hotel_search_request_exists": isinstance(hotel_search_request, dict),
        "current_displayed_hotel": current_hotel,
        "selected_pending_hotel": pending_hotel,
        "has_latest_destination_recommendations": bool(destination_recommendations),
        "latest_destination_recommendation_count": len(destination_recommendations),
        "latest_destination_discovery_request_exists": isinstance(
            destination_request,
            dict,
        ),
        "pending_destination_discovery_exists": isinstance(
            pending_destination_request,
            dict,
        ),
        "pending_destination_discovery": (
            {
                "origin": _safe_destination_origin(
                    pending_destination_request.get("origin")
                ),
                "activityPreferences": pending_destination_request.get(
                    "activityPreferences",
                    [],
                ),
                "requiredActivities": pending_destination_request.get(
                    "requiredActivities",
                    [],
                ),
                "tripDurationNights": pending_destination_request.get(
                    "tripDurationNights"
                ),
                "dates": pending_destination_request.get("dates"),
                "missingFields": pending_destination_request.get("missingFields", []),
            }
            if isinstance(pending_destination_request, dict)
            else None
        ),
        "latest_destination_context": {
            "origin": (
                _safe_destination_origin(destination_request.get("origin"))
                if isinstance(destination_request, dict)
                else None
            ),
            "tripPurpose": (
                destination_request.get("tripPurpose")
                if isinstance(destination_request, dict)
                else None
            ),
            "environmentPreferences": (
                destination_request.get("environmentPreferences", [])
                if isinstance(destination_request, dict)
                else []
            ),
            "activityPreferences": (
                destination_request.get("activityPreferences", [])
                if isinstance(destination_request, dict)
                else []
            ),
            "requiredActivities": (
                destination_request.get("requiredActivities", [])
                if isinstance(destination_request, dict)
                else []
            ),
            "regionPreferences": (
                destination_request.get("regionPreferences", [])
                if isinstance(destination_request, dict)
                else []
            ),
            "excludedRegions": (
                destination_request.get("excludedRegions", [])
                if isinstance(destination_request, dict)
                else []
            ),
            "maxFlightDurationMinutes": (
                destination_request.get("maxFlightDurationMinutes")
                if isinstance(destination_request, dict)
                else None
            ),
            "shortFlightPreference": (
                destination_request.get("shortFlightPreference")
                if isinstance(destination_request, dict)
                else None
            ),
            "softPreferences": (
                destination_request.get("softPreferences", [])
                if isinstance(destination_request, dict)
                else []
            ),
            "hardFilters": (
                destination_request.get("hardFilters", [])
                if isinstance(destination_request, dict)
                else []
            ),
        },
        "pending_action": memory["pending_action"],
        "last_tool_used": memory["last_tool"],
        "active_context_type": active_context_type,
        "active_trip": safe_active_trip,
        "default_origin": default_origin,
        "recent_facts": memory["recent_facts"][-10:],
        "user_preferences": preferences,
    }
