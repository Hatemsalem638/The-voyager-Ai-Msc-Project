from contextlib import asynccontextmanager
from calendar import month_name, monthrange
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
import json
import logging
import random
import re
from secrets import randbelow
from typing import Any, Callable
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    get_current_user,
    get_optional_current_user,
    hash_password,
    verify_password,
)
from app.airport_lookup import (
    AIRPORT_PROVIDER_CACHE,
    AirportLookupError,
    find_nearest_airport,
    suggest_airports,
)
from app.database import create_db_and_tables, get_db
from app.destination_candidates import DESTINATION_CANDIDATES
from app.destination_discovery import discover_destinations
from app.date_utils import (
    TravelDateValidationError,
    extract_yearless_month_days,
    has_explicit_year,
    normalise_hotel_dates,
    normalise_trip_dates,
    resolve_future_date,
    resolve_relative_travel_date,
    today_utc_date,
)
from app.llama_service import (
    extract_flight_search_with_llama,
    extract_travel_intent,
    resolve_clarification_answer_with_llama,
    route_tool_with_llama,
)
from app.memory_store import (
    append_recent_fact,
    clear_dependent_travel_state,
    clear_pending_flight_selection as clear_persistent_pending_flight_selection,
    clear_pending_hotel_selection as clear_persistent_pending_hotel_selection,
    load_active_trip,
    load_safe_llama_context,
    load_session_memory,
    save_active_trip,
    save_current_flight_option as save_persistent_current_flight_option,
    save_current_hotel_option as save_persistent_current_hotel_option,
    save_last_tool as save_persistent_last_tool,
    save_latest_destination_discovery,
    save_latest_flight_search,
    save_latest_hotel_search,
    save_pending_destination_discovery as save_persistent_pending_destination_discovery,
    save_pending_flight_selection as save_persistent_pending_flight_selection,
    save_pending_hotel_selection as save_persistent_pending_hotel_selection,
    save_default_origin,
    get_or_create_user_preferences,
    load_user_preferences,
    update_user_preferences,
)
from app.mock_provider import search_mock_flight_results, search_mock_travel
from app.models import Booking, ChatSession, Message, User
from app.providers.duffel_provider import (
    DuffelProviderError,
    has_duffel_credentials,
    search_duffel_flights,
)
from app.providers.liteapi_hotel_provider import (
    LiteApiHotelProviderError,
    liteapi_hotels_enabled,
    search_liteapi_location_suggestions,
    search_liteapi_hotels,
)
from app.providers.mock_hotel_provider import search_mock_hotels
from app.services.clarification import (
    clear_last_clarification,
    disambiguating_clarification_text,
    record_clarification,
    validate_future_iso,
)
from app.schemas import (
    AirportSuggestion,
    BookingCreateRequest,
    BookingDetails,
    BookingListResponse,
    CheckoutRequest,
    CheckoutResponse,
    ChatRequest,
    ChatResponse,
    ClarificationAnswer,
    FlightSearchRequest,
    FlightSearchResponse,
    HotelSearchRequest,
    HotelSearchResponse,
    HotelLocationSuggestion,
    HomepageDestination,
    HomepageFlightDeal,
    HomepagePackageDeal,
    HomepagePropertyType,
    HomepageTripIdea,
    CurrentLocationRequest,
    CurrentLocationResponse,
    DefaultOrigin,
    DestinationDiscoveryRequest,
    DestinationDiscoveryResponse,
    LlamaChatActionRoute,
    NormalisedFlightResult,
    PackageBuildRequest,
    PackageCartItem,
    PackageCartSummary,
    PackageFlightSummary,
    PackageHotelSummary,
    PackageOption,
    FlightTicket,
    HotelVoucher,
    PreferenceUpdate,
    TravelToolCall,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserProfileResponse,
    UserProfileUpdate,
    UserSummary,
)
from app.services.cart_service import (
    _cart_contains_flight,
    _cart_contains_hotel,
    _flight_cart_summary,
    _hotel_cart_summary,
    _package_cart_summary,
)
from app.services.checkout_service import (
    _checkout_package_summary,
    _checkout_totals,
    _safe_checkout_item_details,
    _travel_documents_for_checkout_items,
)
from app.services.flight_service import (
    _cheapest_flight_from_memory,
    _fastest_flight_from_memory,
    _flight_price,
    _rank_flights_from_memory,
)
from app.services.hotel_service import _cheapest_hotel_from_memory, _hotel_price
from app.services.package_service import (
    _flight_summary_for_package,
    _hotel_summary_for_package,
    _package_results_for_chat,
    _sort_package_options_for_budget,
)
from app.state.active_trip_state import (
    _normalise_trip_list,
    _set_latest_visible_result_type,
    _title_trip_list,
)
from app.utils.budget_parsing import _extract_budget_amount
from app.utils.option_parsing import _extract_option_number, _parse_small_number
from app.utils.response_formatting import (
    _cabin_class_label,
    _duration_label,
    _split_flight_datetime,
    _stops_label,
)

DEMO_USER_EMAIL = "demo@example.com"
LOGGER = logging.getLogger(__name__)
PENDING_BOOKINGS: dict[int, dict[str, Any]] = {}
FLIGHT_SEARCH_MEMORY: dict[int, dict[str, Any]] = {}
HOTEL_SEARCH_MEMORY: dict[int, dict[str, Any]] = {}
PENDING_SELECTION_ACTION = "select_package_option"
PENDING_CONFIRMATION_ACTION = "confirm_booking"
PENDING_FLIGHT_CONFIRMATION_ACTION = "confirm_flight_booking"
PENDING_HOTEL_CONFIRMATION_ACTION = "confirm_hotel_booking"
CHAT_FLIGHT_CARD_LIMIT = 12
CHAT_HOTEL_CARD_LIMIT = 12
MAX_FLEXIBLE_DATE_CHECKS = 31
HOMEPAGE_DESTINATION_SEED_TERMS = [
    "lyon",
    "paris",
    "cairo",
    "geneva",
    "hurghada",
    "rome",
    "kayseri",
    "beirut",
    "marrakesh",
]
HOMEPAGE_THEME_SEED_TERMS = {
    "skiing": ["geneva", "lyon", "erzurum", "kayseri", "beirut"],
    "beach": ["hurghada", "sharm", "rome", "marrakesh"],
    "city_break": ["paris", "london", "rome", "lyon"],
    "family": ["paris", "london", "hurghada", "cairo"],
    "food": ["rome", "paris", "istanbul", "beirut"],
    "culture": ["cairo", "rome", "paris", "beirut"],
    "budget": ["lyon", "cairo", "kayseri", "marrakesh"],
}
HOMEPAGE_PROPERTY_TYPES = [
    ("hotel", "Hotel", "Classic stays for city breaks and longer trips."),
    ("apartment", "Apartment", "More space for families, groups, and flexible stays."),
    ("resort", "Resort", "Beach, pool, and activity-focused stays."),
    ("villa", "Villa", "Private stays for special journeys and groups."),
    ("cabin", "Cabin", "Nature stays for quiet escapes."),
    ("cottage", "Cottage", "Cosy countryside-style breaks."),
    ("aparthotel", "Aparthotel", "Apartment comfort with hotel-style services."),
    ("hostel", "Hostel", "Budget-friendly stays for simple trips."),
]

MONTH_NAME_LOOKUP = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

CONFIRMATION_MESSAGES = {
    "yes",
    "yeah",
    "yep",
    "confirm",
    "confirm this",
    "confirm it",
    "confirm this hotel",
    "confirm the hotel",
    "confrim",
    "confirmed",
    "book it",
    "yes book it",
    "confirm booking",
    "proceed",
    "go ahead",
    "sounds good",
    "okay book it",
    "sure",
}

CANCELLATION_MESSAGES = {
    "no",
    "cancel",
    "never mind",
    "nevermind",
    "do not book",
    "don't book",
    "stop booking",
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_db_and_tables()
    yield

app = FastAPI(
    title="AI-Powered Travel Booking Assistant API",
    description="Backend API for Voyage AI travel planning and booking.",
    version="0.1.0",
    lifespan=lifespan,
)

# Open CORS is for local Vite testing. Restrict it before deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/airports/suggest", response_model=list[AirportSuggestion])
def suggest_airport_endpoint(q: str) -> list[AirportSuggestion]:
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(
            status_code=400,
            detail="Airport search query must be at least 2 characters.",
        )
    return [
        AirportSuggestion.model_validate(suggestion)
        for suggestion in suggest_airports(query)
    ]


@app.get("/locations/suggest", response_model=list[HotelLocationSuggestion])
def suggest_location_endpoint(q: str, type: str = "hotel") -> list[HotelLocationSuggestion]:
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(
            status_code=400,
            detail="Location search query must be at least 2 characters.",
        )
    if type.strip().lower() != "hotel":
        raise HTTPException(
            status_code=400,
            detail="Only hotel location suggestions are supported.",
        )
    return [
        HotelLocationSuggestion.model_validate(suggestion)
        for suggestion in search_liteapi_location_suggestions(query)
    ]


def _normalise_email(value: str) -> str:
    email = value.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="Please provide a valid email address.")
    return email


def _user_summary(user: User) -> UserSummary:
    return UserSummary(id=user.id, email=user.email, full_name=user.full_name)


def _user_profile(db: Session, user: User) -> UserProfileResponse:
    return UserProfileResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        created_at=user.created_at,
        preferences=load_user_preferences(db, user.id),
    )


@app.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register_user(payload: UserCreate, db: Session = Depends(get_db)) -> TokenResponse:
    """Create a local account and its first safe preference/session records."""

    email = _normalise_email(payload.email)
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    full_name = payload.full_name.strip() if payload.full_name else None
    user = User(
        display_name=full_name or email.split("@", 1)[0],
        full_name=full_name,
        email=email,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()
    get_or_create_user_preferences(db, user.id)
    _get_or_create_user_session(db, user)
    db.commit()
    db.refresh(user)

    return TokenResponse(access_token=create_access_token(user), user=_user_summary(user))


@app.post("/auth/login", response_model=TokenResponse)
def login_user(payload: UserLogin, db: Session = Depends(get_db)) -> TokenResponse:
    email = _normalise_email(payload.email)
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    return TokenResponse(access_token=create_access_token(user), user=_user_summary(user))


@app.get("/auth/me", response_model=UserProfileResponse)
def get_auth_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserProfileResponse:
    return _user_profile(db, current_user)


@app.patch("/auth/me", response_model=UserProfileResponse)
def update_auth_me(
    payload: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserProfileResponse:
    if "full_name" in payload.model_fields_set:
        full_name = payload.full_name.strip() if payload.full_name else None
        current_user.full_name = full_name
        current_user.display_name = full_name or current_user.email.split("@", 1)[0]
        db.commit()
        db.refresh(current_user)
    return _user_profile(db, current_user)


@app.get("/auth/preferences")
def get_auth_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return load_user_preferences(db, current_user.id)


@app.patch("/auth/preferences")
def update_auth_preferences(
    payload: PreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if "accessibility_needs" in updates:
        updates["accessibility_needs_json"] = updates.pop("accessibility_needs")
    update_user_preferences(db, current_user.id, **updates)
    db.commit()
    return load_user_preferences(db, current_user.id)


def _search_flights_with_fallback(request: FlightSearchRequest) -> FlightSearchResponse:
    fallback_reason: str | None = None

    if has_duffel_credentials():
        try:
            flights = search_duffel_flights(request)
        except DuffelProviderError as exc:
            fallback_reason = str(exc)
        else:
            return FlightSearchResponse(
                provider_used="duffel",
                flights=flights,
                fallback_reason=None,
            )
    else:
        fallback_reason = "Missing DUFFEL_API_KEY; using mock fallback."

    return FlightSearchResponse(
        provider_used="mock_fallback",
        flights=search_mock_flight_results(request),
        fallback_reason=fallback_reason,
    )


def _search_hotels_with_fallback(request: HotelSearchRequest) -> HotelSearchResponse:
    """Use LiteAPI when configured, otherwise return stable mock hotel cards."""

    fallback_message: str | None = None
    if liteapi_hotels_enabled():
        try:
            results = search_liteapi_hotels(request)
        except LiteApiHotelProviderError as exc:
            fallback_message = str(exc)
        else:
            return HotelSearchResponse(
                provider_used="liteapi",
                results=results,
                message=None,
            )
    else:
        fallback_message = "LiteAPI hotel search is disabled or credentials are missing."

    return HotelSearchResponse(
        provider_used="mock_hotel_fallback",
        results=search_mock_hotels(request),
        message=fallback_message,
    )


def _homepage_rng(seed: str | None) -> random.Random:
    return random.Random(seed or str(randbelow(10_000_000)))


def _float_price(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _homepage_future_dates(rng: random.Random, nights: int = 3) -> tuple[date, date]:
    base = today_utc_date() + timedelta(days=rng.randint(45, 170))
    return base, base + timedelta(days=nights)


def _first_airport_for_homepage(query: str) -> dict[str, Any] | None:
    suggestions = suggest_airports(query)
    return suggestions[0] if suggestions else None


def _first_location_for_homepage(query: str) -> dict[str, Any] | None:
    suggestions = search_liteapi_location_suggestions(query)
    return suggestions[0] if suggestions else None


def _flight_search_url(
    origin: dict[str, Any],
    destination: dict[str, Any],
    departure_date: str,
    return_date: str | None,
) -> str:
    parts = [
        f"from={quote(str(origin['code']))}",
        f"to={quote(str(destination['code']))}",
        f"fromLabel={quote(str(origin['displayName']))}",
        f"toLabel={quote(str(destination['displayName']))}",
        f"fromCity={quote(str(origin['city']))}",
        f"toCity={quote(str(destination['city']))}",
        f"departureDate={quote(departure_date)}",
    ]
    if return_date:
        parts.append(f"returnDate={quote(return_date)}")
    return "/flights?" + "&".join(parts)


def _hotel_search_url(location: dict[str, Any], property_type: str | None = None) -> str:
    parts = [
        f"destination={quote(str(location['displayName']))}",
        f"city={quote(str(location['city']))}",
        f"country={quote(str(location['country']))}",
    ]
    if location.get("providerLocationId"):
        parts.append(f"providerLocationId={quote(str(location['providerLocationId']))}")
    if location.get("latitude") is not None:
        parts.append(f"latitude={quote(str(location['latitude']))}")
    if location.get("longitude") is not None:
        parts.append(f"longitude={quote(str(location['longitude']))}")
    if property_type:
        parts.append(f"propertyType={quote(property_type)}")
    return "/hotels?" + "&".join(parts)


def _source_from_provider(provider_used: str, *records: dict[str, Any]) -> str:
    if provider_used in {"duffel", "liteapi"} and all(
        record.get("source") == "provider_api" for record in records if record
    ):
        return "provider_api"
    return "provider_cache"


def _homepage_flight_deal(
    origin: dict[str, Any],
    destination: dict[str, Any],
    departure: date,
    return_date: date,
) -> HomepageFlightDeal | None:
    request = FlightSearchRequest.model_validate(
        {
            "searchType": "flight",
            "passengerCount": 1,
            "itinerary": {
                "tripType": "round_trip",
                "origin": {"code": origin["code"], "city": origin["city"]},
                "destination": {
                    "code": destination["code"],
                    "city": destination["city"],
                },
                "dates": {
                    "departure": departure.isoformat(),
                    "return": return_date.isoformat(),
                },
            },
            "preferences": {"cabinClass": "economy", "maxStops": None},
        }
    )
    response = _search_flights_with_fallback(request)
    if not response.flights:
        return None
    flight = response.flights[0]
    price = _float_price(flight.price)
    return HomepageFlightDeal.model_validate(
        {
            "id": f"{origin['code']}-{destination['code']}-{departure.isoformat()}",
            "originAirportCode": origin["code"],
            "originCity": origin["city"],
            "originDisplayName": origin["displayName"],
            "destinationAirportCode": destination["code"],
            "destinationCity": destination["city"],
            "destinationDisplayName": destination["displayName"],
            "departureDate": departure.isoformat(),
            "returnDate": return_date.isoformat(),
            "airline": flight.airline,
            "price": price,
            "currency": flight.currency,
            "duration": flight.duration_label,
            "stops": flight.stops_label,
            "provider": response.provider_used,
            "source": _source_from_provider(response.provider_used, origin, destination),
            "searchUrl": _flight_search_url(
                origin,
                destination,
                departure.isoformat(),
                return_date.isoformat(),
            ),
        }
    )


def _homepage_package_deal(
    origin: dict[str, Any],
    airport: dict[str, Any],
    location: dict[str, Any],
    departure: date,
    return_date: date,
) -> HomepagePackageDeal | None:
    nights = max((return_date - departure).days, 1)
    flight_deal = _homepage_flight_deal(origin, airport, departure, return_date)
    if flight_deal is None or flight_deal.price is None or not flight_deal.currency:
        return None
    hotel_request = HotelSearchRequest.model_validate(
        {
            "searchType": "hotel",
            "destination": {
                "city": location["city"],
                "country": location["country"],
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
            },
            "checkInDate": departure.isoformat(),
            "checkOutDate": return_date.isoformat(),
            "guestCount": 1,
            "roomCount": 1,
            "preferences": {"currency": flight_deal.currency},
        }
    )
    hotel_response = _search_hotels_with_fallback(hotel_request)
    matching_hotels = [
        hotel
        for hotel in hotel_response.results
        if hotel.currency.upper() == flight_deal.currency.upper()
    ]
    if not matching_hotels:
        return None
    hotel = matching_hotels[0]
    hotel_price = round(float(hotel.price), 2)
    total = round(flight_deal.price + hotel_price, 2)
    source = "provider_api" if (
        flight_deal.source == "provider_api"
        and hotel_response.provider_used == "liteapi"
        and location.get("source") == "provider_api"
    ) else "provider_cache"
    return HomepagePackageDeal.model_validate(
        {
            "id": f"pkg-{origin['code']}-{airport['code']}-{departure.isoformat()}",
            "originAirportCode": origin["code"],
            "originCity": origin["city"],
            "destinationAirportCode": airport["code"],
            "destinationCity": location["city"],
            "destinationCountry": location["country"],
            "departureDate": departure.isoformat(),
            "returnDate": return_date.isoformat(),
            "nights": nights,
            "flightPrice": flight_deal.price,
            "hotelPrice": hotel_price,
            "totalPrice": total,
            "currency": flight_deal.currency,
            "hotelName": hotel.hotel_name,
            "hotelRating": hotel.rating,
            "providerSummary": f"{flight_deal.provider} flight + {hotel_response.provider_used} stay",
            "source": source,
            "searchUrl": (
                "/voyager?prompt="
                + quote(
                    f"Show me a {nights}-night package to {location['city']} "
                    f"from {origin['code']} on {departure.isoformat()}"
                )
            ),
        }
    )


@app.get("/homepage/property-types", response_model=list[HomepagePropertyType])
def homepage_property_types() -> list[HomepagePropertyType]:
    return [
        HomepagePropertyType.model_validate(
            {
                "id": key,
                "label": label,
                "description": description,
                "searchUrl": f"/hotels?propertyType={quote(key)}",
            }
        )
        for key, label, description in HOMEPAGE_PROPERTY_TYPES
    ]


@app.get("/homepage/trending-destinations", response_model=list[HomepageDestination])
def homepage_trending_destinations(limit: int = 8) -> list[HomepageDestination]:
    destinations: list[HomepageDestination] = []
    seen: set[str] = set()
    for term in HOMEPAGE_DESTINATION_SEED_TERMS:
        if len(destinations) >= max(1, min(limit, 12)):
            break
        location = _first_location_for_homepage(term)
        if not location:
            continue
        key = str(location["displayName"]).lower()
        if key in seen:
            continue
        seen.add(key)
        airport = _first_airport_for_homepage(str(location["city"]))
        destinations.append(
            HomepageDestination.model_validate(
                {
                    "id": f"dest-{key.replace(' ', '-')}",
                    "city": location["city"],
                    "country": location["country"],
                    "displayName": location["displayName"],
                    "airportCode": airport.get("code") if airport else None,
                    "providerLocationId": location.get("providerLocationId"),
                    "tags": ["trending", "stays", "packages"],
                    "source": location["source"],
                    "searchUrl": _hotel_search_url(location),
                }
            )
        )
    return destinations


@app.get("/homepage/trip-ideas", response_model=list[HomepageTripIdea])
def homepage_trip_ideas(theme: str = "city_break", limit: int = 6) -> list[HomepageTripIdea]:
    normalised_theme = theme.strip().lower().replace("-", "_")
    terms = HOMEPAGE_THEME_SEED_TERMS.get(
        normalised_theme,
        HOMEPAGE_THEME_SEED_TERMS["city_break"],
    )
    ideas: list[HomepageTripIdea] = []
    for term in terms:
        if len(ideas) >= max(1, min(limit, 10)):
            break
        location = _first_location_for_homepage(term)
        if not location:
            continue
        airport = _first_airport_for_homepage(str(location["city"]))
        prompt_theme = normalised_theme.replace("_", " ")
        ideas.append(
            HomepageTripIdea.model_validate(
                {
                    "id": f"idea-{normalised_theme}-{location['city']}",
                    "theme": normalised_theme,
                    "city": location["city"],
                    "country": location["country"],
                    "displayName": location["displayName"],
                    "airportCode": airport.get("code") if airport else None,
                    "estimatedFlightPrice": None,
                    "estimatedPackagePrice": None,
                    "tags": [prompt_theme, "AI idea"],
                    "source": location["source"],
                    "voyagerPrompt": f"I want a {prompt_theme} trip to {location['displayName']}",
                    "searchUrl": "/voyager?prompt="
                    + quote(f"I want a {prompt_theme} trip to {location['displayName']}"),
                }
            )
        )
    return ideas


@app.get("/homepage/random-flights", response_model=list[HomepageFlightDeal])
def homepage_random_flights(
    origin: str = "EMA",
    limit: int = 6,
    seed: str | None = None,
) -> list[HomepageFlightDeal]:
    rng = _homepage_rng(seed)
    origin_airport = _first_airport_for_homepage(origin) or _first_airport_for_homepage("EMA")
    if not origin_airport:
        return []
    terms = HOMEPAGE_DESTINATION_SEED_TERMS[:]
    rng.shuffle(terms)
    deals: list[HomepageFlightDeal] = []
    for term in terms:
        if len(deals) >= max(1, min(limit, 6)):
            break
        destination = _first_airport_for_homepage(term)
        if not destination or destination["code"] == origin_airport["code"]:
            continue
        departure, return_date = _homepage_future_dates(rng)
        try:
            deal = _homepage_flight_deal(origin_airport, destination, departure, return_date)
        except Exception as exc:  # Keep the homepage loading if a provider fails.
            LOGGER.warning("Homepage flight deal failed: %s", exc.__class__.__name__)
            continue
        if deal is not None:
            deals.append(deal)
    return deals


@app.get("/homepage/random-packages", response_model=list[HomepagePackageDeal])
def homepage_random_packages(
    origin: str = "EMA",
    limit: int = 4,
    seed: str | None = None,
) -> list[HomepagePackageDeal]:
    rng = _homepage_rng(seed)
    origin_airport = _first_airport_for_homepage(origin) or _first_airport_for_homepage("EMA")
    if not origin_airport:
        return []
    terms = HOMEPAGE_DESTINATION_SEED_TERMS[:]
    rng.shuffle(terms)
    deals: list[HomepagePackageDeal] = []
    for term in terms:
        if len(deals) >= max(1, min(limit, 5)):
            break
        airport = _first_airport_for_homepage(term)
        location = _first_location_for_homepage(term)
        if not airport or not location or airport["code"] == origin_airport["code"]:
            continue
        departure, return_date = _homepage_future_dates(rng)
        try:
            deal = _homepage_package_deal(
                origin_airport,
                airport,
                location,
                departure,
                return_date,
            )
        except Exception as exc:
            LOGGER.warning("Homepage package deal failed: %s", exc.__class__.__name__)
            continue
        if deal is not None:
            deals.append(deal)
    return deals


@app.post("/flights/search", response_model=FlightSearchResponse)
def search_flights(request: FlightSearchRequest) -> FlightSearchResponse:
    _validate_direct_flight_dates(request)
    return _search_flights_with_fallback(request)


@app.post("/hotels/search", response_model=HotelSearchResponse)
def search_hotels(request: HotelSearchRequest) -> HotelSearchResponse:
    """Direct typed hotel search using LiteAPI first and mock fallback."""

    _validate_direct_hotel_dates(request)
    return _search_hotels_with_fallback(request)


@app.post("/location/current", response_model=CurrentLocationResponse)
def save_current_location(
    request: CurrentLocationRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
) -> CurrentLocationResponse:
    if current_user is None:
        _user, session = _get_or_create_demo_context(db)
    else:
        session = _get_or_create_user_session(db, current_user)

    try:
        nearest_airport = find_nearest_airport(request.latitude, request.longitude)
    except AirportLookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="I could not detect the nearest airport right now. Please type your origin manually.",
        ) from exc
    default_origin = DefaultOrigin.model_validate(nearest_airport)
    save_default_origin(db, session.id, nearest_airport)
    active_trip = _active_trip_for_session(session.id, db)
    active_trip["origin"] = {
        "city": default_origin.city,
        "country": default_origin.country,
        "airportCode": default_origin.airportCode,
        "airportName": default_origin.airportName,
        "source": default_origin.source,
    }
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    db.commit()

    return CurrentLocationResponse(
        defaultOrigin=default_origin,
        message=(
            f"I set {default_origin.airportName} ({default_origin.airportCode}) "
            "as your default origin."
        ),
    )


def _validate_direct_flight_dates(request: FlightSearchRequest) -> None:
    """Reject explicit past dates from the structured API instead of guessing."""

    try:
        normalise_trip_dates(
            request.itinerary.dates.departure,
            request.itinerary.dates.return_date,
            today=today_utc_date(),
        )
    except TravelDateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_direct_hotel_dates(request: HotelSearchRequest) -> None:
    """Reject explicit past hotel stays from the structured API."""

    try:
        normalise_hotel_dates(
            request.check_in_date,
            request.check_out_date,
            today=today_utc_date(),
        )
    except TravelDateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _empty_travel_results() -> dict[str, Any]:
    return {
        "flights": [],
        "hotels": [],
        "destinations": [],
        "packages": [],
        "cart": None,
        "checkout": None,
        "flightTicket": None,
        "hotelVoucher": None,
        "booking": None,
        "suggestions": [],
    }


def _flight_results_for_chat(
    flights: list[dict[str, Any]],
    limit: int | None = None,
) -> dict[str, Any]:
    return {
        **_empty_travel_results(),
        "flights": flights[:limit] if limit is not None else flights,
    }


def _hotel_results_for_chat(
    hotels: list[dict[str, Any]],
    limit: int | None = None,
) -> dict[str, Any]:
    return {
        **_empty_travel_results(),
        "hotels": hotels[:limit] if limit is not None else hotels,
    }


def _destination_results_for_chat(
    response: DestinationDiscoveryResponse,
) -> dict[str, Any]:
    return {
        **_empty_travel_results(),
        "destinations": [
            item.model_dump(mode="json") for item in response.recommendations
        ],
    }


def _normalised_flight_dicts(
    provider_response: FlightSearchResponse,
) -> list[dict[str, Any]]:
    flights: list[dict[str, Any]] = []
    for option_number, result in enumerate(provider_response.flights, start=1):
        flight = result.model_dump(mode="json")
        flight_id = str(flight.get("id") or "")
        outbound_date, outbound_time = _split_flight_datetime(
            flight.get("departure_date")
        )
        return_date, return_time = _split_flight_datetime(flight.get("return_date"))
        flight["offer_id"] = flight.get("offer_id") or flight_id
        flight["display_id"] = flight.get("display_id") or f"Offer {flight_id[-8:]}"
        flight["option_number"] = option_number
        flight["outbound_departure_date"] = (
            flight.get("outbound_departure_date") or outbound_date
        )
        flight["outbound_departure_time"] = (
            flight.get("outbound_departure_time") or outbound_time
        )
        flight["return_departure_date"] = (
            flight.get("return_departure_date") or return_date
        )
        flight["return_departure_time"] = (
            flight.get("return_departure_time") or return_time
        )
        flight["stops_label"] = flight.get("stops_label") or _stops_label(
            flight.get("stops")
        )
        flight["duration_label"] = flight.get("duration_label") or _duration_label(
            flight.get("duration_minutes")
        )
        flight["cabin_class_label"] = flight.get(
            "cabin_class_label"
        ) or _cabin_class_label(flight.get("cabin_class"))
        flight["passenger_count"] = flight.get("passenger_count") or 1
        flights.append(flight)

    return flights


def _store_latest_flight_results(
    session_id: int,
    intent: dict[str, Any],
    provider_response: FlightSearchResponse,
    flight_search_request: FlightSearchRequest | None = None,
    db: Session | None = None,
) -> None:
    flights = _normalised_flight_dicts(provider_response)
    FLIGHT_SEARCH_MEMORY[session_id] = {
        "intent": deepcopy(intent),
        "flights": flights,
        "search_request": (
            flight_search_request.model_dump(by_alias=True, mode="json")
            if flight_search_request
            else None
        ),
        "provider_used": provider_response.provider_used,
        "current_flight_option": 1 if flights else None,
        "selected_flight_id": None,
        "pending_action": None,
        "last_tool_used": "search_flights",
    }
    if isinstance(db, Session):
        save_latest_flight_search(
            db,
            session_id,
            (
                flight_search_request.model_dump(by_alias=True, mode="json")
                if flight_search_request
                else None
            ),
            flights,
        )
        _set_latest_visible_result_type(db, session_id, "flights")


def _persistent_flight_intent(memory: dict[str, Any]) -> dict[str, Any]:
    search_request = memory.get("search_request")
    if not isinstance(search_request, dict):
        return _build_control_intent("", "flight_search")

    itinerary = search_request.get("itinerary")
    origin = itinerary.get("origin", {}) if isinstance(itinerary, dict) else {}
    destination = itinerary.get("destination", {}) if isinstance(itinerary, dict) else {}
    return {
        "intent_type": "flight_search",
        "destination": destination.get("city") or destination.get("code"),
        "booking_reference": None,
        "departure_city": origin.get("city") or origin.get("code"),
        "travelers": search_request.get("passengerCount", 1),
        "budget_level": "medium",
        "message_summary": "Persisted flight search",
    }


def _persistent_memory_as_legacy_shape(
    db: Session,
    session_id: int,
) -> dict[str, Any] | None:
    persisted = load_session_memory(db, session_id)
    flights = persisted.get("flights")
    if not isinstance(flights, list) or not flights:
        return None

    current_data = persisted.get("current_flight_option")
    pending_data = persisted.get("pending_flight_selection")
    current_option = (
        current_data.get("option_number")
        if isinstance(current_data, dict)
        else None
    )
    pending_flight = (
        pending_data.get("flight") if isinstance(pending_data, dict) else None
    )
    return {
        "intent": _persistent_flight_intent(persisted),
        "flights": flights,
        "search_request": persisted.get("search_request"),
        "provider_used": None,
        "current_flight_option": current_option,
        "selected_flight_id": (
            pending_flight.get("id") if isinstance(pending_flight, dict) else None
        ),
        "pending_action": persisted.get("pending_action"),
        "last_tool_used": persisted.get("last_tool"),
    }


def _latest_flight_memory(
    session_id: int,
    db: Session | None = None,
) -> dict[str, Any] | None:
    if isinstance(db, Session):
        return _persistent_memory_as_legacy_shape(db, session_id)

    memory = FLIGHT_SEARCH_MEMORY.get(session_id)
    if not memory or not memory.get("flights"):
        return None
    return memory


def _flight_option(
    session_id: int,
    option_number: int | None,
    db: Session | None = None,
) -> tuple[int, dict[str, Any]] | None:
    memory = _latest_flight_memory(session_id, db)
    if memory is None or option_number is None or option_number < 1:
        return None

    flights = memory["flights"]
    if option_number > len(flights):
        return None

    return option_number, flights[option_number - 1]


def _cheapest_flight(
    session_id: int,
    db: Session | None = None,
) -> tuple[int, dict[str, Any]] | None:
    return _cheapest_flight_from_memory(_latest_flight_memory(session_id, db))


def _fastest_flight(
    session_id: int,
    db: Session | None = None,
) -> tuple[int, dict[str, Any]] | None:
    return _fastest_flight_from_memory(_latest_flight_memory(session_id, db))


def _rank_flights(
    session_id: int,
    ranking: str,
    limit: int = 1,
    db: Session | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    return _rank_flights_from_memory(
        _latest_flight_memory(session_id, db),
        ranking,
        limit,
    )


def _resolve_flight_selection(
    session_id: int,
    selection: str,
    option_number: int | None = None,
    db: Session | None = None,
) -> tuple[int, dict[str, Any]] | None:
    if selection == "current_displayed":
        return _current_displayed_flight(session_id, db)
    if selection == "cheapest":
        return _cheapest_flight(session_id, db)
    if selection == "fastest":
        return _fastest_flight(session_id, db)
    if selection == "option_number":
        return _flight_option(session_id, option_number, db)
    return None


def _select_pending_flight(
    session_id: int,
    flight: dict[str, Any],
    db: Session | None = None,
) -> None:
    _clear_pending_hotel_selection(session_id, db)
    memory = _latest_flight_memory(session_id, db)
    option_number: int | None = None
    if memory is not None:
        memory["selected_flight_id"] = flight.get("id")
        memory["pending_action"] = "confirm_flight_booking"
        option_number = _option_number_for_flight(session_id, flight.get("id"), db)
        if isinstance(db, Session):
            save_persistent_pending_flight_selection(
                db,
                session_id,
                option_number,
                flight,
            )
    if isinstance(db, Session):
        active_trip = _active_trip_for_session(session_id, db)
        active_trip["selectedFlightOption"] = {
            "optionNumber": option_number or flight.get("option_number") or 1,
            **deepcopy(flight),
        }
        active_trip["selectedHotelOption"] = None
        active_trip["latestVisibleResultType"] = "flight_selected"
        active_trip["status"] = "flight_selected"
        active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
        save_active_trip(db, session_id, active_trip)


def _set_current_flight_option(
    session_id: int,
    option_number: int,
    db: Session | None = None,
) -> None:
    memory = _latest_flight_memory(session_id, db)
    option = _flight_option(session_id, option_number, db)
    if memory is not None and option is not None:
        memory["current_flight_option"] = option_number
        if isinstance(db, Session):
            save_persistent_current_flight_option(
                db,
                session_id,
                option_number,
                option[1],
            )


def _current_displayed_flight(
    session_id: int,
    db: Session | None = None,
) -> tuple[int, dict[str, Any]] | None:
    memory = _latest_flight_memory(session_id, db)
    if memory is None:
        return None

    current_option = memory.get("current_flight_option")
    return _flight_option(session_id, current_option, db)


def _selected_pending_flight(
    session_id: int,
    db: Session | None = None,
) -> dict[str, Any] | None:
    if isinstance(db, Session):
        persisted = load_session_memory(db, session_id)
        pending = persisted.get("pending_flight_selection")
        if isinstance(pending, dict) and isinstance(pending.get("flight"), dict):
            return pending["flight"]

    memory = _latest_flight_memory(session_id, db)
    if memory is None:
        return None

    selected_flight_id = memory.get("selected_flight_id")
    if not selected_flight_id:
        return None

    return next(
        (
            flight
            for flight in memory["flights"]
            if flight.get("id") == selected_flight_id
        ),
        None,
    )


def _clear_pending_flight_selection(
    session_id: int,
    db: Session | None = None,
) -> None:
    memory = FLIGHT_SEARCH_MEMORY.get(session_id)
    if memory is not None:
        memory["selected_flight_id"] = None
        memory["pending_action"] = None
    if isinstance(db, Session):
        clear_persistent_pending_flight_selection(db, session_id)
        active_trip = load_active_trip(db, session_id)
        if active_trip.get("selectedFlightOption") is not None:
            active_trip["selectedFlightOption"] = None
            if active_trip.get("latestVisibleResultType") == "flight_selected":
                active_trip["latestVisibleResultType"] = "flights"
            active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
            save_active_trip(db, session_id, active_trip)


def _clear_current_flight_option(
    session_id: int,
    db: Session | None = None,
) -> None:
    memory = FLIGHT_SEARCH_MEMORY.get(session_id)
    if memory is not None:
        memory["current_flight_option"] = None
    if isinstance(db, Session):
        save_persistent_current_flight_option(db, session_id, None)


def _set_last_tool_used(
    session_id: int,
    tool_name: str,
    db: Session | None = None,
) -> None:
    memory = FLIGHT_SEARCH_MEMORY.get(session_id)
    if memory is not None:
        memory["last_tool_used"] = tool_name
    hotel_memory = HOTEL_SEARCH_MEMORY.get(session_id)
    if hotel_memory is not None:
        hotel_memory["last_tool_used"] = tool_name
    if isinstance(db, Session):
        save_persistent_last_tool(db, session_id, tool_name)


def _flight_state_summary(
    option_number: int | None,
    flight: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if option_number is None or flight is None:
        return None

    return {
        "option_number": option_number,
        "id": flight.get("id"),
        "airline": flight.get("airline"),
        "origin": flight.get("origin"),
        "destination": flight.get("destination"),
        "departure_date": flight.get("departure_date"),
        "outbound_departure_date": flight.get("outbound_departure_date"),
        "outbound_departure_time": flight.get("outbound_departure_time"),
        "return_date": flight.get("return_date"),
        "return_departure_date": flight.get("return_departure_date"),
        "return_departure_time": flight.get("return_departure_time"),
        "cabin_class": flight.get("cabin_class"),
        "cabin_class_label": flight.get("cabin_class_label"),
        "passenger_count": flight.get("passenger_count"),
        "stops": flight.get("stops"),
        "stops_label": flight.get("stops_label"),
        "duration_label": flight.get("duration_label"),
        "price": flight.get("price"),
        "currency": flight.get("currency"),
    }


def _option_number_for_flight(
    session_id: int,
    flight_id: str | None,
    db: Session | None = None,
) -> int | None:
    if not flight_id:
        return None
    memory = _latest_flight_memory(session_id, db)
    if memory is None:
        return None
    for index, flight in enumerate(memory["flights"], start=1):
        if flight.get("id") == flight_id:
            return index
    return None


def _normalised_hotel_dicts(
    provider_response: HotelSearchResponse,
) -> list[dict[str, Any]]:
    hotels: list[dict[str, Any]] = []
    for option_number, result in enumerate(provider_response.results, start=1):
        hotel = result.model_dump(mode="json")
        hotel["option_number"] = option_number
        hotel["short_id"] = hotel.get("short_id") or f"Hotel {option_number}"
        hotels.append(hotel)
    return hotels


def _build_hotel_intent(
    message: str,
    hotel_search: HotelSearchRequest,
) -> dict[str, Any]:
    return {
        "intent_type": "hotel_search",
        "destination": hotel_search.destination.city or hotel_search.destination.country,
        "booking_reference": None,
        "departure_city": None,
        "travelers": hotel_search.guest_count,
        "budget_level": "medium",
        "message_summary": message[:120],
    }


def _store_latest_hotel_results(
    session_id: int,
    intent: dict[str, Any],
    provider_response: HotelSearchResponse,
    hotel_search_request: HotelSearchRequest,
    db: Session | None = None,
) -> None:
    hotels = _normalised_hotel_dicts(provider_response)
    HOTEL_SEARCH_MEMORY[session_id] = {
        "intent": deepcopy(intent),
        "hotels": hotels,
        "search_request": hotel_search_request.model_dump(by_alias=True, mode="json"),
        "provider_used": provider_response.provider_used,
        "current_hotel_option": 1 if hotels else None,
        "selected_hotel_id": None,
        "pending_action": None,
        "last_tool_used": "search_hotels",
    }
    if isinstance(db, Session):
        save_latest_hotel_search(
            db,
            session_id,
            hotel_search_request.model_dump(by_alias=True, mode="json"),
            hotels,
        )
        _set_latest_visible_result_type(db, session_id, "hotels")


def _persistent_hotel_intent(memory: dict[str, Any]) -> dict[str, Any]:
    search_request = memory.get("hotel_search_request")
    if not isinstance(search_request, dict):
        return _build_control_intent("", "hotel_search")
    destination = search_request.get("destination")
    destination_value = (
        destination.get("city") or destination.get("country")
        if isinstance(destination, dict)
        else None
    )
    return {
        "intent_type": "hotel_search",
        "destination": destination_value,
        "booking_reference": None,
        "departure_city": None,
        "travelers": search_request.get("guestCount", 1),
        "budget_level": "medium",
        "message_summary": "Persisted hotel search",
    }


def _persistent_hotel_memory_as_legacy_shape(
    db: Session,
    session_id: int,
) -> dict[str, Any] | None:
    persisted = load_session_memory(db, session_id)
    hotels = persisted.get("hotels")
    if not isinstance(hotels, list) or not hotels:
        return None
    current_data = persisted.get("current_hotel_option")
    pending_data = persisted.get("pending_hotel_selection")
    current_option = (
        current_data.get("option_number") if isinstance(current_data, dict) else None
    )
    pending_hotel = pending_data.get("hotel") if isinstance(pending_data, dict) else None
    return {
        "intent": _persistent_hotel_intent(persisted),
        "hotels": hotels,
        "search_request": persisted.get("hotel_search_request"),
        "provider_used": None,
        "current_hotel_option": current_option,
        "selected_hotel_id": pending_hotel.get("id") if isinstance(pending_hotel, dict) else None,
        "pending_action": persisted.get("pending_action"),
        "last_tool_used": persisted.get("last_tool"),
    }


def _latest_hotel_memory(
    session_id: int,
    db: Session | None = None,
) -> dict[str, Any] | None:
    if isinstance(db, Session):
        return _persistent_hotel_memory_as_legacy_shape(db, session_id)
    memory = HOTEL_SEARCH_MEMORY.get(session_id)
    return memory if memory and memory.get("hotels") else None


def _hotel_option(
    session_id: int,
    option_number: int | None,
    db: Session | None = None,
) -> tuple[int, dict[str, Any]] | None:
    memory = _latest_hotel_memory(session_id, db)
    if memory is None or option_number is None or option_number < 1:
        return None
    hotels = memory["hotels"]
    return (option_number, hotels[option_number - 1]) if option_number <= len(hotels) else None


def _cheapest_hotel(
    session_id: int,
    db: Session | None = None,
) -> tuple[int, dict[str, Any]] | None:
    return _cheapest_hotel_from_memory(_latest_hotel_memory(session_id, db))


def _set_current_hotel_option(
    session_id: int,
    option_number: int,
    db: Session | None = None,
) -> None:
    memory = _latest_hotel_memory(session_id, db)
    option = _hotel_option(session_id, option_number, db)
    if memory is not None and option is not None:
        memory["current_hotel_option"] = option_number
        if isinstance(db, Session):
            save_persistent_current_hotel_option(db, session_id, option_number, option[1])


def _current_displayed_hotel(
    session_id: int,
    db: Session | None = None,
) -> tuple[int, dict[str, Any]] | None:
    memory = _latest_hotel_memory(session_id, db)
    return _hotel_option(session_id, memory.get("current_hotel_option"), db) if memory else None


def _option_number_for_hotel(
    session_id: int,
    hotel_id: str | None,
    db: Session | None = None,
) -> int | None:
    memory = _latest_hotel_memory(session_id, db)
    if memory is None or not hotel_id:
        return None
    return next(
        (index for index, hotel in enumerate(memory["hotels"], start=1) if hotel.get("id") == hotel_id),
        None,
    )


def _select_pending_hotel(
    session_id: int,
    hotel: dict[str, Any],
    db: Session | None = None,
) -> None:
    memory = _latest_hotel_memory(session_id, db)
    if memory is not None:
        memory["selected_hotel_id"] = hotel.get("id")
        memory["pending_action"] = PENDING_HOTEL_CONFIRMATION_ACTION
        if isinstance(db, Session):
            save_persistent_pending_hotel_selection(
                db,
                session_id,
                _option_number_for_hotel(session_id, hotel.get("id"), db),
                hotel,
            )


def _selected_pending_hotel(
    session_id: int,
    db: Session | None = None,
) -> dict[str, Any] | None:
    if isinstance(db, Session):
        pending = load_session_memory(db, session_id).get("pending_hotel_selection")
        if isinstance(pending, dict) and isinstance(pending.get("hotel"), dict):
            return pending["hotel"]
    memory = _latest_hotel_memory(session_id, db)
    if memory is None or not memory.get("selected_hotel_id"):
        return None
    return next(
        (hotel for hotel in memory["hotels"] if hotel.get("id") == memory["selected_hotel_id"]),
        None,
    )


def _clear_pending_hotel_selection(
    session_id: int,
    db: Session | None = None,
) -> None:
    memory = HOTEL_SEARCH_MEMORY.get(session_id)
    if memory is not None:
        memory["selected_hotel_id"] = None
        memory["pending_action"] = None
    if isinstance(db, Session):
        clear_persistent_pending_hotel_selection(db, session_id)


def _clear_current_hotel_option(
    session_id: int,
    db: Session | None = None,
) -> None:
    memory = HOTEL_SEARCH_MEMORY.get(session_id)
    if memory is not None:
        memory["current_hotel_option"] = None
    if isinstance(db, Session):
        save_persistent_current_hotel_option(db, session_id, None)


def _hotel_follow_up_intent(
    session_id: int,
    message: str,
    db: Session | None = None,
) -> dict[str, Any]:
    memory = _latest_hotel_memory(session_id, db)
    if memory is None:
        return _build_control_intent(message, "hotel_search")
    intent = dict(memory["intent"])
    intent["message_summary"] = message[:120]
    return intent


def _hotel_context_is_current(session_id: int, db: Session | None = None) -> bool:
    memory = _latest_hotel_memory(session_id, db)
    if memory is None:
        return False
    last_tool = memory.get("last_tool_used")
    return isinstance(last_tool, str) and (
        last_tool.startswith("search_hotels")
        or last_tool.startswith("rank_hotels")
        or last_tool.startswith("show_hotel")
        or last_tool.startswith("select_hotel")
    )


def _recent_conversation_facts(
    db: Session | None,
    session_id: int,
) -> dict[str, list[str]]:
    if db is None:
        return {"user": [], "assistant": []}

    messages = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.id.desc())
        .limit(12)
        .all()
    )
    messages.reverse()
    facts = {"user": [], "assistant": []}
    for message in messages:
        if message.sender not in facts:
            continue
        facts[message.sender].append(message.content[:180])

    return {
        "user": facts["user"][-3:],
        "assistant": facts["assistant"][-3:],
    }


def _conversation_state_for_llama(
    session_id: int,
    db: Session | None = None,
) -> dict[str, Any]:
    if isinstance(db, Session):
        context = load_safe_llama_context(db, session_id)
        context["current_date"] = today_utc_date().isoformat()
        pending_package = PENDING_BOOKINGS.get(session_id)
        selected_package_option = (
            pending_package.get("selected_option_number") if pending_package else None
        )
        if selected_package_option is not None and not context["pending_action"]:
            context["pending_action"] = PENDING_CONFIRMATION_ACTION
        elif pending_package is not None and not context["pending_action"]:
            context["pending_action"] = PENDING_SELECTION_ACTION
        context["selected_pending_package_option"] = selected_package_option
        return context

    flight_memory = _latest_flight_memory(session_id)
    pending_package = PENDING_BOOKINGS.get(session_id)
    selected_flight = _selected_pending_flight(session_id)

    latest_intent = flight_memory.get("intent") if flight_memory else {}
    latest_flight = flight_memory["flights"][0] if flight_memory else {}
    current_option = flight_memory.get("current_flight_option") if flight_memory else None
    current_match = _current_displayed_flight(session_id)
    current_flight = current_match[1] if current_match else None
    selected_option = _option_number_for_flight(
        session_id,
        selected_flight.get("id") if selected_flight else None,
    )
    selected_package_option = (
        pending_package.get("selected_option_number") if pending_package else None
    )

    if flight_memory and flight_memory.get("pending_action"):
        pending_action = flight_memory["pending_action"]
    elif selected_flight is not None or selected_package_option is not None:
        pending_action = PENDING_CONFIRMATION_ACTION
    elif pending_package is not None:
        pending_action = PENDING_SELECTION_ACTION
    else:
        pending_action = None

    return {
        "current_date": today_utc_date().isoformat(),
        "has_latest_flight_results": flight_memory is not None,
        "latest_flight_count": len(flight_memory["flights"])
        if flight_memory
        else 0,
        "latest_route": {
            "origin": latest_flight.get("origin") or latest_intent.get("departure_city"),
            "destination": latest_flight.get("destination")
            or latest_intent.get("destination"),
        },
        "latest_search_request_exists": bool(
            flight_memory and flight_memory.get("search_request")
        ),
        "current_displayed_flight": _flight_state_summary(
            current_option,
            current_flight,
        ),
        "selected_pending_flight": _flight_state_summary(
            selected_option,
            selected_flight,
        ),
        "selected_pending_package_option": selected_package_option,
        "pending_action": pending_action,
        "last_tool_used": flight_memory.get("last_tool_used") if flight_memory else None,
        "recent_conversation": _recent_conversation_facts(db, session_id),
    }


def _normalize_control_message(message: str) -> str:
    normalized = message.lower().replace("'", "")
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _word_count(value: str) -> int:
    return len(value.split())


def _has_phrase(normalized_message: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", normalized_message) is not None


def _is_close_match(value: str, phrase: str) -> bool:
    threshold = 0.8 if len(phrase) <= 6 else 0.84
    return SequenceMatcher(None, value, phrase).ratio() >= threshold


def _matches_control_phrase(message: str, phrases: set[str]) -> bool:
    normalized = _normalize_control_message(message)
    if not normalized:
        return False

    if normalized in phrases:
        return True

    tokens = normalized.split()
    for phrase in phrases:
        phrase_tokens = phrase.split()
        phrase_length = len(phrase_tokens)

        if phrase_length > 1 and _has_phrase(normalized, phrase):
            return True

        if phrase_length == 1:
            if any(_is_close_match(token, phrase) for token in tokens):
                return True
            continue

        for start_index in range(0, len(tokens) - phrase_length + 1):
            token_window = " ".join(tokens[start_index : start_index + phrase_length])
            if _is_close_match(token_window, phrase):
                return True

        if _word_count(normalized) <= phrase_length + 1 and _is_close_match(
            normalized,
            phrase,
        ):
            return True

    return False


def _is_confirmation_message(message: str) -> bool:
    return _matches_control_phrase(message, CONFIRMATION_MESSAGES)


def _is_cancellation_message(message: str) -> bool:
    normalized_message = _normalize_control_message(message)
    normalized_phrases = {
        _normalize_control_message(phrase) for phrase in CANCELLATION_MESSAGES
    }
    return normalized_message in normalized_phrases


def _should_handle_cancellation(
    message: str,
    session_id: int,
    db: Session | None = None,
) -> bool:
    has_pending_booking = session_id in PENDING_BOOKINGS
    has_pending_flight = _selected_pending_flight(session_id, db) is not None
    has_pending_hotel = _selected_pending_hotel(session_id, db) is not None
    active_trip = load_active_trip(db, session_id) if isinstance(db, Session) else {}
    has_selected_hotel = isinstance(active_trip.get("selectedHotelOption"), dict)
    return (
        has_pending_booking
        or has_pending_flight
        or has_pending_hotel
        or has_selected_hotel
    ) and _is_cancellation_message(
        message
    )


def _looks_like_flight_search_message(message: str) -> bool:
    normalized_message = message.lower()
    has_flight_term = any(
        phrase in normalized_message
        for phrase in ["fly", "flight", "flights", "direct", "no stops"]
    )
    has_known_airport_code = re.search(r"\b(?:lhr|jfk)\b", message, re.IGNORECASE)
    has_route_terms = "from" in normalized_message and "to" in normalized_message
    return has_flight_term or bool(has_known_airport_code) or has_route_terms


def _looks_like_flight_or_travel_request(message: str) -> bool:
    normalized = _normalize_control_message(message)
    if not normalized:
        return False
    if _looks_like_package_request(message):
        return True
    return any(
        phrase in normalized
        for phrase in [
            "fly",
            "flight",
            "flights",
            "go to",
            "travel to",
            "trip to",
            "cheapest day",
            "cheap day",
            "when is cheapest",
            "when is the cheapest",
            "search for the cheapest day",
            "deal package",
        ]
    )


def _is_flight_follow_up_message(message: str) -> bool:
    normalized_message = _normalize_control_message(message)
    option_number = _extract_option_number(message)
    return (
        "cheapest" in normalized_message
        or "lowest price" in normalized_message
        or "fastest" in normalized_message
        or "book the cheapest" in normalized_message
        or (
            option_number is not None
            and any(
                phrase in normalized_message
                for phrase in ["show", "select", "choose", "i like", "i want", "continue"]
            )
        )
    )


def _is_current_flight_reference(message: str) -> bool:
    normalized_message = _normalize_control_message(message)
    return any(
        phrase in normalized_message
        for phrase in [
            "i like it",
            "can i book it",
            "book it",
            "book this one",
            "ill take that",
            "take that",
        ]
    )


def _looks_like_this_flight_reference(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "this flight",
            "like this flight",
            "i like this flight",
            "i want this flight",
            "this option is good",
            "this is good",
            "confirm this flight",
            "book this flight",
            "add this flight",
            "continue with this flight",
        ]
    )


def _looks_like_this_hotel_reference(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "this hotel",
            "like this hotel",
            "i like this hotel",
            "i want this hotel",
            "confirm this hotel",
            "book this hotel",
            "add this hotel",
            "continue with this hotel",
        ]
    )


def _looks_like_direct_add_or_confirm(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return (
        _is_confirmation_message(message)
        or _looks_like_package_checkout(message)
        or any(
            phrase in normalized
            for phrase in [
                "confirm",
                "book",
                "add",
                "checkout",
                "cart",
            ]
        )
    )


def _is_select_option_message(message: str) -> bool:
    normalized = _normalize_control_message(message)
    if _extract_option_number(message) is None:
        return False
    if re.fullmatch(
        r"(?:the\s+)?(?:option|package)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)",
        normalized,
    ):
        return True

    return any(
        phrase in normalized
        for phrase in [
            "select",
            "choose",
            "i like",
            "i want",
            "take",
            "go with",
            "go for",
            "book option",
            "book package",
            "continue",
            "continue with",
            "continue option",
        ]
    )


def _is_show_option_message(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return _extract_option_number(message) is not None and any(
        phrase in normalized
        for phrase in ["show", "view", "see", "details"]
    )


def _is_cheaper_options_message(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return "cheaper" in normalized or "lowest price" in normalized


def _is_another_option_message(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "another option",
            "other option",
            "other options",
            "more options",
            "next option",
        ]
    )


def _is_package_option_message(message: str) -> bool:
    return (
        _is_select_option_message(message)
        or _is_show_option_message(message)
        or _is_cheaper_options_message(message)
        or _is_another_option_message(message)
    )


def _looks_like_destination_only_flight_request(message: str) -> bool:
    normalized = _normalize_control_message(message)
    if not normalized:
        return False
    if " from " in f" {normalized} ":
        return False
    destination_pattern = re.search(
        r"\b(?:go|fly|travel|trip)\s+to\s+[a-z]{3,}(?:\s+[a-z]{3,})?\b",
        normalized,
    )
    return bool(destination_pattern)


def _looks_like_destination_discovery_message(message: str) -> bool:
    normalized = _normalize_control_message(message)
    preference_words = {
        "beach",
        "sea",
        "summer",
        "relaxing",
        "family",
        "children",
        "kids",
        "skiing",
        "mountains",
        "city break",
        "nightlife",
        "culture",
        "short flight",
    }
    discovery_words = {"somewhere", "destination", "recommend", "holiday", "vacation"}
    return any(word in normalized for word in preference_words) and any(
        word in normalized for word in discovery_words
    )


def _fallback_destination_discovery_tool(message: str) -> TravelToolCall:
    normalized = _normalize_control_message(message)
    environment: list[str] = []
    activity: list[str] = []
    if any(word in normalized for word in ["beach", "sea", "summer"]):
        environment.append("beach")
    if any(word in normalized for word in ["skiing", "ski", "mountain"]):
        activity.append("skiing")
    required_activities = list(activity)
    regions = ["Europe"] if "europe" in normalized else []
    children_match = re.search(r"\b(\d+)\s+(?:children|kids)\b", normalized)
    children = int(children_match.group(1)) if children_match else 0
    duration_match = re.search(r"(?:less than|under|max(?:imum)?)\s+(\d+)\s+hours?", normalized)
    max_minutes = int(duration_match.group(1)) * 60 if duration_match else None
    trip_duration_nights = _extract_trip_duration_nights(message)
    short_flight = any(
        phrase in normalized
        for phrase in [
            "not long flights",
            "dont like long flights",
            "short flights",
            "shorter flights",
            "closer",
            "nearby",
        ]
    )
    return TravelToolCall(
        tool="discover_destinations",
        arguments={
            "tripPurpose": "family_holiday" if children else "holiday",
            "travellers": {"adults": 2 if children else 1, "children": children},
            "environmentPreferences": environment,
            "activityPreferences": activity,
            "requiredActivities": required_activities,
            "regionPreferences": regions,
            "maxFlightDurationMinutes": max_minutes,
            "shortFlightPreference": short_flight,
            "tripDurationNights": trip_duration_nights,
            "softPreferences": ["short_flight"] if short_flight else [],
            "hardFilters": [],
        },
    )


def _extract_trip_duration_nights(message: str) -> int | None:
    _days, nights = _extract_trip_duration_components(message)
    return nights


NUMBER_TOKEN_PATTERN = r"\d{1,5}|one|two|three|four|five|six|seven|eight|nine|ten|a"


def _parse_duration_number(value: str | None) -> int | None:
    text = str(value or "").strip().lower()
    if text == "a":
        return 1
    return _parse_small_number(text)


def _extract_trip_duration_components(message: str) -> tuple[int | None, int | None]:
    normalized = _normalize_control_message(message)
    day_matches = [
        _parse_duration_number(match.group(1))
        for match in re.finditer(rf"\b({NUMBER_TOKEN_PATTERN})\s+days?\b", normalized)
    ]
    night_matches = [
        _parse_duration_number(match.group(1))
        for match in re.finditer(rf"\b({NUMBER_TOKEN_PATTERN})\s+nights?\b", normalized)
    ]
    days = next((value for value in day_matches if value and value > 0), None)
    nights = next((value for value in night_matches if value and value > 0), None)
    if nights is None and days is not None:
        nights = max(days - 1, 1)
    return days, nights


def _extract_return_after_days(message: str) -> int | None:
    normalized = _normalize_control_message(message)
    match = re.search(
        r"\breturn(?:ing)?\s+(?:after|in)\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+days?\b",
        normalized,
    )
    if not match:
        return None
    days = _parse_small_number(match.group(1))
    return days if days and days > 0 else None


def _extract_departure_return_day_numbers(message: str) -> tuple[int | None, int | None]:
    normalized = _normalize_control_message(message)
    departure_patterns = [
        r"\b(?:leave|leaving|live|depart|departing|travel|travelling|go|going)\s+(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b",
        r"\b(?:go|going)\s+to\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b",
        r"\bfrom\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b",
    ]
    return_patterns = [
        r"\b(?:come\s+back|coming\s+back|back|return|returning)\s+(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b",
        r"\bto\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b",
    ]
    departure_day: int | None = None
    return_day: int | None = None
    for pattern in departure_patterns:
        match = re.search(pattern, normalized)
        if match:
            departure_day = int(match.group(1))
            break
    for pattern in return_patterns:
        match = re.search(pattern, normalized)
        if match:
            return_day = int(match.group(1))
            break
    if departure_day is not None and return_day is None:
        trailing_match = re.search(
            rf"\b(?:leave|leaving|live|depart|departing|travel|travelling|go|going)\s+"
            rf"(?:on\s+)?(?:the\s+)?{departure_day}(?:st|nd|rd|th)?\s+"
            r"(?:and|to)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\b",
            normalized,
        )
        if trailing_match:
            return_day = int(trailing_match.group(1))
    return departure_day, return_day


def _active_trip_reference_month(active_trip: dict[str, Any]) -> date | None:
    comparison = active_trip.get("latestDatePriceComparison")
    if isinstance(comparison, dict) and comparison.get("rangeType") == "month":
        start_date = comparison.get("startDate")
        if isinstance(start_date, str):
            try:
                return date.fromisoformat(start_date)
            except ValueError:
                return None
    dates = active_trip.get("dates") if isinstance(active_trip.get("dates"), dict) else {}
    departure = dates.get("departureDate")
    if isinstance(departure, str):
        try:
            return date.fromisoformat(departure)
        except ValueError:
            return None
    return None


def _resolve_day_in_month(day: int, reference: date) -> date | None:
    try:
        return date(reference.year, reference.month, day)
    except ValueError:
        return None


def _extract_departure_return_dates_from_message(
    message: str,
    active_trip: dict[str, Any],
) -> tuple[str | None, str | None]:
    departure_day, return_day = _extract_departure_return_day_numbers(message)
    departure_iso = (
        resolve_relative_travel_date(message, today=today_utc_date())
        or resolve_future_date(message, today=today_utc_date())
    )
    reference_month = _active_trip_reference_month(active_trip)
    if departure_day is not None:
        departure_iso = _resolve_day_only_followup(
            f"the {departure_day}th",
            base_date=reference_month,
        )

    return_iso: str | None = None
    if return_day is not None:
        reference = (
            date.fromisoformat(departure_iso)
            if departure_iso
            else reference_month or today_utc_date()
        )
        return_date = _resolve_day_in_month(return_day, reference)
        if return_date and departure_iso and return_date <= date.fromisoformat(departure_iso):
            next_month = 1 if reference.month == 12 else reference.month + 1
            next_year = reference.year + 1 if reference.month == 12 else reference.year
            return_date = _resolve_day_in_month(return_day, date(next_year, next_month, 1))
        return_iso = return_date.isoformat() if return_date else None
    return departure_iso, return_iso


def _destination_dates(discovery_request: DestinationDiscoveryRequest) -> dict[str, Any]:
    return discovery_request.dates if isinstance(discovery_request.dates, dict) else {}


def _destination_departure_date(discovery_request: DestinationDiscoveryRequest) -> str | None:
    value = _destination_dates(discovery_request).get("departure")
    return str(value) if value else None


def _destination_return_date(discovery_request: DestinationDiscoveryRequest) -> str | None:
    value = _destination_dates(discovery_request).get("return")
    return str(value) if value else None


def _resolve_day_only_followup(
    message: str,
    base_date: date | None = None,
) -> str | None:
    text = str(message or "").lower()
    match = re.search(r"\b(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", text)
    if not match:
        match = re.search(r"\bon\s+(?:the\s+)?(\d{1,2})\b", text)
    if not match:
        return None
    day = int(match.group(1))
    if base_date is not None:
        try:
            return date(base_date.year, base_date.month, day).isoformat()
        except ValueError:
            return None
    reference = today_utc_date()
    year = reference.year
    month = reference.month
    try:
        candidate = date(year, month, day)
    except ValueError:
        return None
    if candidate < reference:
        month = 1 if reference.month == 12 else reference.month + 1
        year = reference.year + 1 if reference.month == 12 else reference.year
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
    return candidate.isoformat()


def _resolve_destination_followup_departure(message: str) -> str | None:
    reference = today_utc_date()
    return (
        resolve_relative_travel_date(message, today=reference)
        or resolve_future_date(message, today=reference)
        or _resolve_day_only_followup(message)
    )


def _next_week_departure_dates(reference: date | None = None) -> list[str]:
    today = reference or today_utc_date()
    days_until_next_monday = (7 - today.weekday()) % 7
    if days_until_next_monday == 0:
        days_until_next_monday = 7
    monday = today + timedelta(days=days_until_next_monday)
    return [(monday + timedelta(days=offset)).isoformat() for offset in range(7)]


def _resolve_month_range_from_message(
    message: str,
    reference: date | None = None,
) -> dict[str, Any] | None:
    normalized = _normalize_control_message(message)
    if not normalized:
        return None
    for month_text, month_number in sorted(
        MONTH_NAME_LOOKUP.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if not re.search(rf"\b{re.escape(month_text)}\b", normalized):
            continue
        today = reference or today_utc_date()
        year_match = re.search(r"\b(20\d{2}|19\d{2})\b", normalized)
        year = int(year_match.group(1)) if year_match else today.year
        last_day = monthrange(year, month_number)[1]
        if not year_match and date(year, month_number, last_day) < today:
            year += 1
            last_day = monthrange(year, month_number)[1]
        start = date(year, month_number, 1)
        end = date(year, month_number, last_day)
        label = f"{month_name[month_number]} {year}"
        return {
            "rangeType": "month",
            "rangeLabel": label,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "departureDates": [
                (start + timedelta(days=offset)).isoformat()
                for offset in range((end - start).days + 1)
            ],
        }
    return None


def _resolve_next_month_range_from_message(
    message: str,
    reference: date | None = None,
) -> dict[str, Any] | None:
    normalized = _normalize_control_message(message)
    if "next month" not in normalized:
        return None
    today = reference or today_utc_date()
    year = today.year + (1 if today.month == 12 else 0)
    month_number = 1 if today.month == 12 else today.month + 1
    start = date(year, month_number, 1)
    end = date(year, month_number, monthrange(year, month_number)[1])
    label = f"{month_name[month_number]} {year}"
    return {
        "rangeType": "month",
        "rangeLabel": label,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "departureDates": [
            (start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)
        ],
    }


def _resolve_named_month_date_from_message(
    message: str,
    reference: date | None = None,
) -> str | None:
    normalized = _normalize_control_message(message)
    today = reference or today_utc_date()
    month_pattern = "|".join(
        re.escape(value)
        for value in sorted(MONTH_NAME_LOOKUP, key=len, reverse=True)
    )
    patterns = [
        rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+(?P<month>{month_pattern})(?:\s+(?P<year>20\d{{2}}|19\d{{2}}))?\b",
        rf"\b(?P<month>{month_pattern})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<year>20\d{{2}}|19\d{{2}}))?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        day = int(match.group("day"))
        month_number = MONTH_NAME_LOOKUP[match.group("month")]
        year_text = match.group("year")
        year = int(year_text) if year_text else today.year
        try:
            candidate = date(year, month_number, day)
        except ValueError:
            return None
        if not year_text and candidate < today:
            try:
                candidate = date(year + 1, month_number, day)
            except ValueError:
                return None
        return candidate.isoformat()
    return None


def _date_comparison_from_active_trip(active_trip: dict[str, Any]) -> dict[str, Any] | None:
    comparison = active_trip.get("latestDatePriceComparison")
    return comparison if isinstance(comparison, dict) else None


def _active_trip_day_only_base_date(active_trip: dict[str, Any]) -> date | None:
    comparison = _date_comparison_from_active_trip(active_trip)
    if comparison and comparison.get("rangeType") == "month":
        start_date = comparison.get("startDate") or (
            comparison.get("checkedDateRange", {}).get("start")
            if isinstance(comparison.get("checkedDateRange"), dict)
            else None
        )
        if isinstance(start_date, str):
            try:
                return date.fromisoformat(start_date)
            except ValueError:
                pass

    dates = _active_trip_dates(active_trip)
    departure = dates.get("departureDate")
    if isinstance(departure, str):
        try:
            return date.fromisoformat(departure)
        except ValueError:
            return None
    return None


def _display_date_label(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return value
    return f"{parsed.day} {parsed.strftime('%b %Y')}"


def _month_display_label(month: Any) -> str | None:
    month_text = _normalise_structured_month(month)
    if not month_text:
        return None
    try:
        parsed = date.fromisoformat(f"{month_text}-01")
    except ValueError:
        return None
    return parsed.strftime("%B")


def _package_option_label(package: dict[str, Any]) -> str:
    option_number = package.get("optionNumber") or package.get("option_number")
    return f"Package {option_number}" if option_number else "This package"


def _package_money_label(package: dict[str, Any]) -> str:
    return _format_money_for_reply(
        _package_price(package),
        str(package.get("currency") or "GBP"),
    )


def _format_money_for_reply(price: float | None, currency: str | None = "GBP") -> str:
    if price is None:
        return "the available price"
    code = str(currency or "GBP").upper()
    if code == "GBP":
        return f"£{price:,.2f}"
    return f"{code} {price:,.2f}"


def _format_price_for_reply(flight: dict[str, Any]) -> str:
    price = flight.get("price")
    currency = str(flight.get("currency") or "").upper()
    try:
        amount = float(price)
        price_text = f"{amount:,.0f}" if amount.is_integer() else f"{amount:,.2f}"
    except (TypeError, ValueError):
        price_text = str(price or "an unavailable price")
    if currency == "GBP":
        return f"£{price_text}"
    return f"{price_text} {currency}".strip()


def _format_comparison_price(result: dict[str, Any]) -> str:
    flight = result.get("offerSummary")
    if isinstance(flight, dict):
        return _format_price_for_reply(flight)
    price = result.get("cheapestPrice")
    currency = str(result.get("currency") or "").upper()
    try:
        amount = float(price)
        price_text = f"{amount:,.0f}" if amount.is_integer() else f"{amount:,.2f}"
    except (TypeError, ValueError):
        price_text = str(price or "an unavailable price")
    if currency == "GBP":
        return f"£{price_text}"
    return f"{price_text} {currency}".strip()




def _log_route_trace(
    route: str,
    tool: str,
    origin: str | None = None,
    missing_before: list[str] | None = None,
    missing_after: list[str] | None = None,
) -> None:
    LOGGER.info(
        "Routing trace: %s",
        {
            "route": route,
            "tool": tool,
            "origin": origin,
            "missingFieldsBefore": missing_before or [],
            "missingFieldsAfter": missing_after or [],
        },
    )


def _clarification_reply_text(
    db: Session,
    session_id: int,
    active_trip: dict[str, Any],
    missing_fields: list[str],
    question_text: str,
) -> str:
    repeated = record_clarification(active_trip, missing_fields, question_text)
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session_id, active_trip)
    if repeated:
        return disambiguating_clarification_text(question_text, missing_fields)
    return question_text


def _pending_destination_question_text(
    pending_request: DestinationDiscoveryRequest,
) -> str:
    if pending_request.origin is None:
        dates = _destination_dates(pending_request)
        if dates.get("departure"):
            return "Which airport would you like to fly from?"
        return "When do you want to depart, and which airport would you like to fly from?"
    origin_note = (
        f"I'll use {_origin_message({'city': pending_request.origin.city, 'airportCode': pending_request.origin.code, 'airportName': pending_request.origin.airport_name})} as your origin. "
        if pending_request.origin and pending_request.origin.code
        else ""
    )
    return f"{origin_note}When do you want to depart?"


def _pending_destination_discovery_request(
    session_id: int,
    db: Session,
) -> DestinationDiscoveryRequest | None:
    pending_data = load_session_memory(db, session_id).get(
        "pending_destination_discovery"
    )
    if not isinstance(pending_data, dict):
        return None
    try:
        return DestinationDiscoveryRequest.model_validate(pending_data)
    except Exception as exc:
        LOGGER.warning(
            "Stored pending destination discovery validation failed: %s",
            exc.__class__.__name__,
        )
        return None


def _merge_pending_destination_discovery_dates(
    pending_request: DestinationDiscoveryRequest,
    message: str,
    asked_question: str | None = None,
    default_origin: dict[str, Any] | None = None,
) -> DestinationDiscoveryRequest | None:
    existing_departure = _destination_departure_date(pending_request)
    departure_iso = existing_departure or _resolve_destination_followup_departure(message)
    nights = pending_request.trip_duration_nights or _extract_trip_duration_nights(message)
    return_iso: str | None = None
    origin_payload: dict[str, Any] | None = None
    if departure_iso:
        _, explicit_return_iso = _extract_departure_return_dates_from_message(
            message,
            {"dates": {"departureDate": departure_iso}},
        )
        if explicit_return_iso:
            return_iso = explicit_return_iso
    origin_missing = pending_request.origin is None
    answer: ClarificationAnswer | None = None
    if departure_iso is None or origin_missing:
        awaiting = []
        if departure_iso is None:
            awaiting.append("departure_date")
            if not nights:
                awaiting.append("nights")
        if origin_missing:
            awaiting.append("origin")
        answer = resolve_clarification_answer_with_llama(
            asked_question=asked_question or _pending_destination_question_text(pending_request),
            user_reply=message,
            awaiting=awaiting,
        )
        if answer and answer.resolved:
            if departure_iso is None and answer.departure_date:
                departure_iso = validate_future_iso(answer.departure_date)
            nights = nights or answer.nights
            if answer.return_date:
                return_iso = validate_future_iso(answer.return_date)
                if return_iso and departure_iso:
                    try:
                        if date.fromisoformat(return_iso) <= date.fromisoformat(departure_iso):
                            return_iso = None
                    except ValueError:
                        return_iso = None
            if origin_missing and (answer.origin_code or answer.origin_text):
                origin_payload = _airport_payload_from_structured_slot(
                    code=answer.origin_code,
                    text=answer.origin_text,
                    source="clarification_answer",
                )
                if origin_payload is None and default_origin:
                    origin_payload = deepcopy(default_origin)
                    origin_payload["source"] = "default_origin_clarification"
    if departure_iso is None and origin_payload is None:
        return None
    payload = pending_request.model_dump(by_alias=True, mode="json")
    if origin_payload:
        payload["origin"] = {
            "city": origin_payload.get("city"),
            "code": origin_payload.get("airportCode"),
            "airportName": origin_payload.get("airportName"),
            "latitude": origin_payload.get("latitude"),
            "longitude": origin_payload.get("longitude"),
        }
    dates = dict(payload.get("dates") or {})
    if departure_iso:
        dates["departure"] = departure_iso
    if return_iso:
        dates["return"] = return_iso
        try:
            payload["tripDurationNights"] = max(
                1,
                (date.fromisoformat(return_iso) - date.fromisoformat(departure_iso)).days,
            )
        except ValueError:
            if nights:
                payload["tripDurationNights"] = nights
    elif nights and departure_iso:
        dates["return"] = (
            date.fromisoformat(departure_iso) + timedelta(days=nights)
        ).isoformat()
        payload["tripDurationNights"] = nights
    payload["dates"] = dates
    missing = []
    if not payload.get("origin"):
        missing.append("origin")
    if not dates.get("departure"):
        missing.append("departureDate")
    payload["missingFields"] = missing
    merged_request = DestinationDiscoveryRequest.model_validate(payload)
    if _looks_like_destination_refinement_message(message):
        return _merge_destination_refinement(merged_request, message)
    return merged_request


def _destination_discovery_intro(discovery_request: DestinationDiscoveryRequest) -> str | None:
    departure_iso = _destination_departure_date(discovery_request)
    return_iso = _destination_return_date(discovery_request)
    nights = discovery_request.trip_duration_nights
    if not departure_iso or not return_iso or not nights:
        return None
    origin_text = (
        f"{discovery_request.origin.airport_name or discovery_request.origin.city or 'your saved origin'} "
        f"({discovery_request.origin.code})"
        if discovery_request.origin and discovery_request.origin.code
        else "your saved origin"
    )
    activity_text = (
        "skiing "
        if "skiing"
        in {str(value).lower().replace(" ", "_") for value in discovery_request.required_activities}
        else ""
    )
    return (
        f"I'll use {origin_text} as your origin and search for "
        f"{activity_text}destination ideas for {nights} nights, departing "
        f"{_display_date_label(departure_iso)} and returning "
        f"{_display_date_label(return_iso)}."
    )


def _store_pending_destination_discovery_and_ask_date(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    discovery_request: DestinationDiscoveryRequest,
    question_text: str | None = None,
) -> ChatResponse:
    payload = discovery_request.model_dump(by_alias=True, mode="json")
    missing_fields = set(payload.get("missingFields") or [])
    if not payload.get("origin"):
        missing_fields.add("origin")
    payload["missingFields"] = sorted(
        missing_fields | {"departureDate"}
    )
    payload["createdAt"] = payload.get("createdAt") or today_utc_date().isoformat()
    pending_request = DestinationDiscoveryRequest.model_validate(payload)
    question_text = question_text or _pending_destination_question_text(pending_request)
    _sync_active_trip_from_destination_request(
        db,
        session.id,
        pending_request,
        status="awaiting_date",
    )
    pending_payload = pending_request.model_dump(by_alias=True, mode="json")
    pending_payload["askedQuestion"] = question_text
    save_persistent_pending_destination_discovery(db, session.id, pending_payload)
    active_trip = _active_trip_for_session(session.id, db)
    if _has_broad_activity_trip_frame(active_trip) and active_trip.get("pendingAction") is None:
        _store_pending_destination_suggestion_action(active_trip)
        active_trip["status"] = "awaiting_date"
    guarded_question = _clarification_reply_text(
        db,
        session.id,
        active_trip,
        list(pending_payload.get("missingFields") or ["departureDate"]),
        question_text,
    )
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "general_enquiry"),
        guarded_question,
    )


def _wants_dateless_destination_discovery(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "choose the place first",
            "chose the place first",
            "choose place first",
            "choose destination first",
            "destination first",
            "place first",
            "show places first",
            "show destinations first",
            "date later",
            "dates later",
            "choose the date later",
            "chose the date later",
            "we will choose the date",
            "we will chose the date",
            "pick the place first",
        ]
    )


def _execute_dateless_destination_discovery_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    if not _wants_dateless_destination_discovery(request.message):
        return None

    discovery_request = (
        _pending_destination_discovery_request(session.id, db)
        or _latest_destination_discovery_request(session.id, db)
    )
    if discovery_request is None:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "general_enquiry"),
            "Tell me what kind of place you want, and I can show destination ideas first.",
        )

    payload = discovery_request.model_dump(by_alias=True, mode="json")
    payload["dates"] = {}
    payload["missingFields"] = []
    discovery_request = DestinationDiscoveryRequest.model_validate(payload)
    active_trip = _sync_active_trip_from_destination_request(
        db,
        session.id,
        discovery_request,
        status="discovering_without_dates",
    )
    active_trip["dates"] = {
        "departureDate": None,
        "returnDate": None,
        "source": "date_later",
    }
    active_trip["status"] = "discovering_without_dates"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)

    provider_response = discover_destinations(discovery_request)
    save_latest_destination_discovery(
        db,
        session.id,
        discovery_request.model_dump(by_alias=True, mode="json"),
        provider_response.recommendations,
    )
    _set_last_tool_used(session.id, "discover_destinations", db)
    results = _destination_results_for_chat(provider_response)
    if provider_response.recommendations:
        top = provider_response.recommendations[0]
        reply = (
            "I can show destination ideas first. Flight prices and availability "
            "will be checked once you choose a date. "
            f"The strongest match is {top.city}, {top.country} "
            f"({top.airportCode}) with a score of {top.score}/100."
        )
    else:
        reply = (
            "I can show destination ideas first, but I could not find a strong "
            "match yet. Try adding a preference such as beach, skiing, culture, "
            "or short flight."
        )
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "general_enquiry"),
        reply,
        results=results,
    )


def _default_origin_payload(default_origin: dict[str, Any]) -> dict[str, Any]:
    return {
        "city": default_origin.get("city"),
        "code": default_origin.get("airportCode"),
        "airportName": default_origin.get("airportName"),
        "latitude": default_origin.get("latitude"),
        "longitude": default_origin.get("longitude"),
    }


def _destination_origin_was_explicit(
    message: str,
    discovery_request: DestinationDiscoveryRequest,
) -> bool:
    origin = discovery_request.origin
    if origin is None:
        return False
    return _message_mentions_location(message, origin.city) or _message_mentions_location(
        message,
        origin.code,
    )


def _apply_destination_origin_precedence(
    discovery_request: DestinationDiscoveryRequest,
    message: str,
    session_id: int,
    db: Session,
) -> DestinationDiscoveryRequest:
    if _destination_origin_was_explicit(message, discovery_request):
        return discovery_request
    default_origin = _default_origin_for_session(session_id, db)
    if not default_origin:
        return discovery_request
    payload = discovery_request.model_dump(by_alias=True, mode="json")
    payload["origin"] = _default_origin_payload(default_origin)
    return DestinationDiscoveryRequest.model_validate(payload)


def _build_control_intent(message: str, intent_type: str) -> dict[str, Any]:
    return {
        "intent_type": intent_type,
        "destination": None,
        "booking_reference": None,
        "departure_city": None,
        "travelers": 1,
        "budget_level": "medium",
        "message_summary": message[:120],
    }


def _default_origin_for_session(session_id: int, db: Session) -> dict[str, Any] | None:
    default_origin = load_session_memory(db, session_id).get("default_origin")
    return default_origin if isinstance(default_origin, dict) else None


def _origin_message(default_origin: dict[str, Any]) -> str:
    airport_name = default_origin.get("airportName") or default_origin.get("city")
    airport_code = default_origin.get("airportCode")
    if airport_name and airport_code:
        return f"{airport_name} ({airport_code})"
    return str(default_origin.get("city") or "your saved origin")


def _active_trip_for_session(session_id: int, db: Session) -> dict[str, Any]:
    active_trip = load_active_trip(db, session_id)
    if isinstance(active_trip.get("origin"), dict):
        return active_trip

    default_origin = _default_origin_for_session(session_id, db)
    if default_origin:
        active_trip["origin"] = {
            "city": default_origin.get("city"),
            "country": default_origin.get("country"),
            "airportCode": default_origin.get("airportCode"),
            "airportName": default_origin.get("airportName"),
            "source": default_origin.get("source") or "default_origin",
        }
        active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
        active_trip = save_active_trip(db, session_id, active_trip)
    return active_trip


def _invalidate_dependent_state(
    db: Session,
    session_id: int,
    reason: str,
    *,
    clear_flights: bool = True,
    clear_hotels: bool = True,
    clear_destinations: bool = False,
) -> None:
    LOGGER.info(
        "Invalidating dependent travel state: %s",
        {
            "session_id": session_id,
            "reason": reason,
            "clearFlights": clear_flights,
            "clearHotels": clear_hotels,
            "clearDestinations": clear_destinations,
        },
    )
    if clear_flights:
        FLIGHT_SEARCH_MEMORY.pop(session_id, None)
    if clear_hotels:
        HOTEL_SEARCH_MEMORY.pop(session_id, None)
    clear_dependent_travel_state(
        db,
        session_id,
        clear_flights=clear_flights,
        clear_hotels=clear_hotels,
        clear_destinations=clear_destinations,
    )


def _clear_active_trip_package_state(active_trip: dict[str, Any]) -> None:
    for key in [
        "latestPackageRequest",
        "pendingPackageRequest",
        "pendingAction",
        "latestPackageOptions",
        "currentPackageOption",
        "selectedPackageOption",
        "cart",
        "checkout",
        "flightTicket",
        "hotelVoucher",
    ]:
        active_trip[key] = [] if key == "latestPackageOptions" else None
    if active_trip.get("latestVisibleResultType") in {
        "packages",
        "cart",
        "checkout",
        "tickets",
    }:
        active_trip["latestVisibleResultType"] = None


def _clear_active_trip_route_dependent_state(active_trip: dict[str, Any]) -> None:
    _clear_active_trip_package_state(active_trip)
    for key in [
        "latestDatePriceComparison",
        "selectedFlightCandidate",
        "pendingFlightRequest",
        "pendingHotelRequest",
        "selectedFlightOption",
        "selectedHotelOption",
    ]:
        active_trip[key] = None


def _active_trip_debug_summary(active_trip: dict[str, Any]) -> dict[str, Any]:
    origin = active_trip.get("origin") if isinstance(active_trip.get("origin"), dict) else {}
    destination = (
        active_trip.get("selectedDestination")
        if isinstance(active_trip.get("selectedDestination"), dict)
        else {}
    )
    region = (
        active_trip.get("destinationRegion")
        if isinstance(active_trip.get("destinationRegion"), dict)
        else {}
    )
    dates = active_trip.get("dates") if isinstance(active_trip.get("dates"), dict) else {}
    pending = (
        active_trip.get("pendingTripRequest")
        if isinstance(active_trip.get("pendingTripRequest"), dict)
        else {}
    )
    pending_action = (
        active_trip.get("pendingAction")
        if isinstance(active_trip.get("pendingAction"), dict)
        else {}
    )
    return {
        "origin": origin.get("airportCode"),
        "destination": destination.get("airportCode"),
        "destinationRegion": region.get("name"),
        "destinationCandidates": [
            candidate.get("airportCode")
            for candidate in active_trip.get("destinationCandidates", [])
            if isinstance(candidate, dict) and candidate.get("airportCode")
        ],
        "month": active_trip.get("month"),
        "departureDate": dates.get("departureDate"),
        "returnDate": dates.get("returnDate"),
        "nights": active_trip.get("tripDurationNights"),
        "intent": pending.get("intent"),
        "pendingAction": pending_action.get("type"),
        "requestedProducts": active_trip.get("requestedProducts"),
        "latestVisibleResultType": active_trip.get("latestVisibleResultType"),
    }


def _destination_request_origin_for_active_trip(
    discovery_request: DestinationDiscoveryRequest,
) -> dict[str, Any] | None:
    if discovery_request.origin is None:
        return None
    origin = discovery_request.origin
    return {
        "city": origin.city,
        "airportCode": origin.code,
        "airportName": origin.airport_name,
        "latitude": origin.latitude,
        "longitude": origin.longitude,
        "source": "destination_discovery",
    }


def _sync_active_trip_from_destination_request(
    db: Session,
    session_id: int,
    discovery_request: DestinationDiscoveryRequest,
    *,
    status: str,
) -> dict[str, Any]:
    active_trip = _active_trip_for_session(session_id, db)
    old_selected = active_trip.get("selectedDestination")
    old_required = _normalise_trip_list(active_trip.get("requiredActivities"))
    old_activity = _normalise_trip_list(active_trip.get("activityPreferences"))
    old_environment = _normalise_trip_list(active_trip.get("environmentPreferences"))

    new_required = _normalise_trip_list(discovery_request.required_activities)
    new_activity = _normalise_trip_list(discovery_request.activity_preferences)
    new_environment = _normalise_trip_list(discovery_request.environment_preferences)
    preferences_changed = (
        old_required != new_required
        or old_activity != new_activity
        or old_environment != new_environment
    )

    if isinstance(old_selected, dict) and preferences_changed:
        active_trip["selectedDestination"] = None
        _clear_active_trip_package_state(active_trip)
        _invalidate_dependent_state(
            db,
            session_id,
            "destination_preferences_changed",
        )

    origin = _destination_request_origin_for_active_trip(discovery_request)
    if origin and origin.get("airportCode"):
        active_trip["origin"] = origin

    dates = _destination_dates(discovery_request)
    departure = dates.get("departure")
    return_date = dates.get("return")
    active_trip["dates"] = {
        "departureDate": str(departure) if departure else None,
        "returnDate": str(return_date) if return_date else None,
        "source": "destination_discovery",
    }
    active_trip["tripDurationNights"] = discovery_request.trip_duration_nights
    active_trip["requiredActivities"] = _title_trip_list(new_required)
    active_trip["activityPreferences"] = _title_trip_list(new_activity)
    active_trip["environmentPreferences"] = _title_trip_list(new_environment)
    active_trip["regionPreferences"] = list(discovery_request.region_preferences)
    active_trip["excludedRegions"] = list(discovery_request.excluded_regions)
    active_trip["status"] = status
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    return save_active_trip(db, session_id, active_trip)


ORDINAL_SELECTIONS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
}


def _destination_option_number_from_message(message: str) -> int | None:
    explicit = _extract_option_number(message)
    if explicit is not None:
        return explicit
    normalized = _normalize_control_message(message)
    for word, number in ORDINAL_SELECTIONS.items():
        if _has_phrase(normalized, word) or _has_phrase(normalized, f"the {word} one"):
            return number
    match = re.search(r"\bnumber\s+(\d+)\b", normalized)
    return int(match.group(1)) if match else None


def resolve_destination_selection(
    message: str,
    latest_recommendations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve a user selection against the latest destination recommendations."""

    if not latest_recommendations:
        return None

    option_number = _destination_option_number_from_message(message)
    if option_number is not None and 1 <= option_number <= len(latest_recommendations):
        selected = dict(latest_recommendations[option_number - 1])
        selected["_selected_option_number"] = option_number
        return selected

    normalized = _normalize_control_message(message)
    for index, recommendation in enumerate(latest_recommendations, start=1):
        city = _normalize_control_message(str(recommendation.get("city") or ""))
        code = str(recommendation.get("airportCode") or "").lower()
        airport_name = _normalize_control_message(
            str(recommendation.get("airportName") or "")
        )
        if city and _has_phrase(normalized, city):
            selected = dict(recommendation)
            selected["_selected_option_number"] = index
            return selected
        if code and re.search(rf"\b{re.escape(code)}\b", normalized):
            selected = dict(recommendation)
            selected["_selected_option_number"] = index
            return selected
        if airport_name and _has_phrase(normalized, airport_name):
            selected = dict(recommendation)
            selected["_selected_option_number"] = index
            return selected
    return None


def _requested_products_from_message(
    message: str,
    *,
    default_when_selected: bool = True,
) -> list[str]:
    normalized = _normalize_control_message(message)
    products: list[str] = []
    if any(
        phrase in normalized
        for phrase in ["flight", "flights", "fly", "airfare", "plane", "ticket"]
    ):
        products.append("flights")
    if any(
        phrase in normalized
        for phrase in ["hotel", "hotels", "stay", "stays", "accommodation", "room"]
    ):
        products.append("hotels")
    if not products and default_when_selected:
        products.append("flights")
    return products


def _destination_to_active_trip_destination(
    destination: dict[str, Any],
) -> dict[str, Any]:
    option_number = destination.get("_selected_option_number")
    airport_code = destination.get("airportCode")
    return {
        "city": destination.get("city"),
        "country": destination.get("country"),
        "airportCode": airport_code,
        "airportName": destination.get("airportName"),
        "source": "selected_recommendation",
        "recommendationId": (
            f"{airport_code}:{option_number}" if airport_code and option_number else airport_code
        ),
    }


def _known_destination_from_text(text: str) -> dict[str, Any] | None:
    query = str(text or "").strip(" .,!?")
    if not query:
        return None
    if _normalize_control_message(query) in {
        "today",
        "tomorrow",
        "live",
        "leave",
        "leaving",
        "depart",
        "departing",
        "go",
        "going",
        "come",
        "return",
        "back",
        "level",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
    }:
        return None
    suggestions = suggest_airports(query)
    if suggestions:
        normalized_query = _normalize_control_message(query)
        for airport in AIRPORT_PROVIDER_CACHE:
            if (
                _normalize_control_message(str(airport.get("city") or ""))
                == normalized_query
            ):
                code = str(airport.get("code") or "").upper()
                return {
                    "city": airport.get("city"),
                    "country": airport.get("country"),
                    "airportCode": code,
                    "airportName": airport.get("name"),
                    "source": "explicit_destination",
                    "recommendationId": code,
                }
        exact = next(
            (
                suggestion
                for suggestion in suggestions
                if _normalize_control_message(str(suggestion.get("city") or ""))
                == normalized_query
                or str(suggestion.get("code") or "").lower() == query.lower()
                or normalized_query
                in _normalize_control_message(
                    str(suggestion.get("name") or suggestion.get("airportName") or "")
                )
            ),
            None,
        )
        if exact is None:
            return None
        code = str(exact.get("code") or exact.get("airportCode") or "").upper()
        for airport in AIRPORT_PROVIDER_CACHE:
            if str(airport.get("code") or "").upper() == code:
                return {
                    "city": airport.get("city"),
                    "country": airport.get("country"),
                    "airportCode": code,
                    "airportName": airport.get("name"),
                    "source": "explicit_destination",
                    "recommendationId": code,
                }
        return {
            "city": exact.get("city"),
            "country": exact.get("country"),
            "airportCode": code,
            "airportName": exact.get("name") or exact.get("airportName"),
            "source": "explicit_destination",
            "recommendationId": code,
        }
    return None


def _known_destination_from_message_text(message: str) -> dict[str, Any] | None:
    normalized = _normalize_control_message(message)
    if " from " in f" {normalized} " and " to " in f" {normalized} ":
        return None
    entries: list[dict[str, Any]] = []
    for airport in AIRPORT_PROVIDER_CACHE:
        entries.append(
            {
                "city": airport.get("city"),
                "country": airport.get("country"),
                "airportCode": airport.get("code"),
                "airportName": airport.get("name"),
            }
        )
    for destination in DESTINATION_CANDIDATES:
        entries.append(
            {
                "city": destination.get("city"),
                "country": destination.get("country"),
                "airportCode": destination.get("airportCode"),
                "airportName": destination.get("airportName"),
            }
        )

    matches: list[tuple[int, dict[str, Any]]] = []
    seen_codes: set[str] = set()
    is_short_correction = bool(
        re.fullmatch(
            r"(?:no\s+)?(?:(?:i\s+mean|make\s+it|change\s+it\s+to|in|to)\s+)?[a-z][a-z\s]{1,35}",
            normalized,
        )
    )
    for entry in entries:
        code = str(entry.get("airportCode") or "").upper()
        if not code or code in seen_codes:
            continue
        city = _normalize_control_message(str(entry.get("city") or ""))
        airport_name = _normalize_control_message(str(entry.get("airportName") or ""))
        city_as_destination = bool(
            city
            and (
                re.search(rf"\b(?:to|in|for)\b.*\b{re.escape(city)}\b", normalized)
                or (is_short_correction and _has_phrase(normalized, city))
            )
        )
        code_as_destination = bool(
            code
            and (
                re.search(rf"\b(?:to|in|for)\s+{re.escape(code.lower())}\b", normalized)
                or (is_short_correction and re.search(rf"\b{re.escape(code.lower())}\b", normalized))
            )
        )
        airport_as_destination = bool(
            airport_name
            and (
                re.search(rf"\b(?:to|in|for)\b.*\b{re.escape(airport_name)}\b", normalized)
                or (is_short_correction and _has_phrase(normalized, airport_name))
            )
        )
        if city_as_destination:
            matches.append((len(city), entry))
            seen_codes.add(code)
        elif code_as_destination:
            matches.append((10, entry))
            seen_codes.add(code)
        elif airport_as_destination:
            matches.append((len(airport_name), entry))
            seen_codes.add(code)

    if not matches:
        return None
    _length, match = sorted(matches, key=lambda item: item[0], reverse=True)[0]
    return {
        "city": match.get("city"),
        "country": match.get("country"),
        "airportCode": match.get("airportCode"),
        "airportName": match.get("airportName"),
        "source": "explicit_destination",
        "recommendationId": match.get("airportCode"),
    }


def _explicit_destination_phrases(message: str) -> list[str]:
    normalized = _normalize_control_message(message)
    phrases: list[str] = []
    stop = (
        r"today|tomorrow|return|after|from|with|and|on|next|this|"
        r"for\s+\d|for\s+one|for\s+two|for\s+three|for\s+four|"
        r"for\s+five|for\s+six|for\s+seven"
    )
    for match in re.finditer(
        rf"\b(?:to|in|for)\s+([a-z][a-z\s]{{1,45}}?)(?=\s+(?:{stop})\b|$)",
        normalized,
    ):
        phrase = match.group(1).strip()
        if phrase:
            phrases.append(phrase)

    correction_match = re.fullmatch(
        r"(?:no\s+)?(?:(?:i\s+mean|make\s+it|change\s+it\s+to|in|to)\s+)?([a-z][a-z\s]{1,35})",
        normalized,
    )
    if correction_match:
        phrases.append(correction_match.group(1).strip())

    cleaned: list[str] = []
    filler = re.compile(
        r"\b(?:the|a|an|go|travel|fly|want|beach|sea|skiing|ski|holiday|vacation|trip|place|destination|somewhere)\b",
        re.IGNORECASE,
    )
    for phrase in phrases:
        candidate = re.sub(filler, " ", phrase)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if candidate and candidate not in cleaned:
            cleaned.append(candidate)
    return cleaned


def _resolve_explicit_destination_from_message(message: str) -> dict[str, Any] | None:
    normalized = _normalize_control_message(message)
    if " from " in f" {normalized} " and " to " in f" {normalized} ":
        return None
    known = _known_destination_from_message_text(message)
    if known is not None:
        return known
    if not re.search(r"\b(?:to|in|for|make it|i mean|change it to)\b", normalized):
        return None
    for phrase in reversed(_explicit_destination_phrases(message)):
        destination = _known_destination_from_text(phrase)
        if destination and destination.get("airportCode"):
            return destination
    return None


def _looks_like_multi_city_request(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "and then",
            "then go to",
            "after that",
            "every country",
            "each country",
            "multi city",
            "multi-city",
            "several countries",
            "more than one country",
        ]
    )


def _multi_city_destination_entries(message: str) -> list[dict[str, Any]]:
    normalized = _normalize_control_message(message)
    entries: list[dict[str, Any]] = []

    def add_entry(entry: dict[str, Any] | None) -> None:
        if not entry:
            return
        key = str(entry.get("airportCode") or entry.get("country") or entry.get("city") or "").lower()
        if not key:
            return
        existing = {
            str(item.get("airportCode") or item.get("country") or item.get("city") or "").lower()
            for item in entries
        }
        if key not in existing:
            entries.append(entry)

    for airport in AIRPORT_PROVIDER_CACHE:
        city = _normalize_control_message(str(airport.get("city") or ""))
        country = _normalize_control_message(str(airport.get("country") or ""))
        code = str(airport.get("code") or "").lower()
        if (
            city and _has_phrase(normalized, city)
            or code and re.search(rf"\b{re.escape(code)}\b", normalized)
        ):
            add_entry(
                {
                    "city": airport.get("city"),
                    "country": airport.get("country"),
                    "airportCode": airport.get("code"),
                    "airportName": airport.get("name"),
                    "source": "explicit_destination",
                    "displayName": airport.get("city"),
                }
            )
    ordered: list[dict[str, Any]] = []
    for match in re.finditer(
        r"\b(?:and then|then go to|after that)\s+([a-z][a-z\s]{1,45})",
        normalized,
    ):
        phrase = re.split(
            r"\b(?:and\s+stay|stay|for|from|on|with|after that|and then|then go to)\b",
            match.group(1),
            maxsplit=1,
        )[0].strip()
        if not phrase:
            continue
        known = _known_destination_from_text(phrase)
        if known is not None and (
            _normalize_control_message(str(known.get("city") or ""))
            == _normalize_control_message(phrase)
            or str(known.get("airportCode") or "").lower() == phrase.lower()
        ):
            add_entry(known)
            continue
        add_entry(
            {
                "city": None,
                "country": phrase.title(),
                "airportCode": None,
                "airportName": None,
                "source": "unresolved_multi_city_destination",
                "displayName": phrase.title(),
            }
        )

    for entry in entries:
        names = [
            str(entry.get("city") or ""),
            str(entry.get("country") or ""),
            str(entry.get("airportCode") or ""),
        ]
        positions = [
            normalized.find(_normalize_control_message(name))
            for name in names
            if name and normalized.find(_normalize_control_message(name)) >= 0
        ]
        ordered.append({**entry, "_position": min(positions) if positions else 10_000})
    ordered.sort(key=lambda item: item.get("_position", 10_000))
    for entry in ordered:
        entry.pop("_position", None)
    return ordered


def _destination_display_name(destination: dict[str, Any]) -> str:
    return str(
        destination.get("displayName")
        or destination.get("city")
        or destination.get("country")
        or destination.get("airportCode")
        or "that destination"
    )


def _create_multi_city_draft(
    db: Session,
    session_id: int,
    message: str,
) -> dict[str, Any] | None:
    if not _looks_like_multi_city_request(message):
        return None
    destinations = _multi_city_destination_entries(message)
    if len(destinations) < 2:
        return None

    active_trip = _active_trip_for_session(session_id, db)
    _invalidate_dependent_state(
        db,
        session_id,
        "multi_city_draft_started",
        clear_destinations=True,
    )
    active_trip = _active_trip_for_session(session_id, db)
    nights = _extract_trip_duration_nights(message)
    first_destination = destinations[0]
    active_trip["multiCityDraft"] = {
        "destinations": destinations,
        "nightsPerDestination": nights,
        "status": "awaiting_departure_date",
        "currentDestinationIndex": 0,
    }
    active_trip["selectedDestination"] = None
    active_trip["dates"] = {
        "departureDate": None,
        "returnDate": None,
        "source": "multi_city_draft",
    }
    if nights:
        active_trip["tripDurationNights"] = nights
    active_trip["requestedProducts"] = []
    active_trip["latestVisibleResultType"] = "multi_city_draft"
    active_trip["status"] = "multi_city_awaiting_date"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session_id, active_trip)
    return {
        "active_trip": active_trip,
        "first_destination": first_destination,
        "destinations": destinations,
        "nights": nights,
    }


def _execute_multi_city_request_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    draft_result = _create_multi_city_draft(db, session.id, request.message)
    if draft_result is None:
        return None
    first_destination = draft_result["first_destination"]
    destinations = draft_result["destinations"]
    nights = draft_result.get("nights") or 3
    first_name = _destination_display_name(first_destination)
    remaining = [
        _destination_display_name(destination)
        for destination in destinations[1:]
    ]
    remainder = f", then we can add {' and '.join(remaining)} after that" if remaining else ""
    reply = (
        "I can help plan that as a multi-city trip. This version builds one "
        f"destination package at a time, so I'll start with {first_name} for "
        f"{nights} nights{remainder}. When do you want to depart?"
    )
    _set_last_tool_used(session.id, "multi_city_draft", db)
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "package_search"),
        reply,
    )


def _execute_multi_city_date_followup_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    draft = active_trip.get("multiCityDraft")
    if not isinstance(draft, dict):
        return None
    destinations = draft.get("destinations")
    if not isinstance(destinations, list) or not destinations:
        return None

    departure = resolve_relative_travel_date(
        request.message,
        today=today_utc_date(),
    ) or resolve_future_date(request.message, today=today_utc_date())
    if departure is None:
        return None

    first_destination = destinations[0]
    first_code = str(first_destination.get("airportCode") or "").upper()
    nights = int(draft.get("nightsPerDestination") or active_trip.get("tripDurationNights") or 3)
    return_date = (date.fromisoformat(departure) + timedelta(days=nights)).isoformat()
    active_trip["selectedDestination"] = {
        "city": first_destination.get("city"),
        "country": first_destination.get("country"),
        "airportCode": first_code or None,
        "airportName": first_destination.get("airportName"),
        "source": "multi_city_first_destination",
        "recommendationId": first_code or first_destination.get("country"),
    }
    active_trip["dates"] = {
        "departureDate": departure,
        "returnDate": return_date,
        "source": "multi_city_date_followup",
    }
    active_trip["tripDurationNights"] = nights
    active_trip["latestVisibleResultType"] = "multi_city_draft"
    active_trip["status"] = "multi_city_first_destination_ready"
    draft["status"] = "first_destination_ready"
    draft["currentDestinationIndex"] = 0
    active_trip["multiCityDraft"] = draft
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    _set_last_tool_used(session.id, "multi_city_date_followup", db)

    first_name = _destination_display_name(first_destination)
    reply = (
        f"Great - I'll start the multi-city plan from {departure}. I'll plan "
        f"{first_name} first for {nights} nights, then we can add the next "
        "destination after that. Would you like flights only, or a full package "
        "with hotel?"
    )
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "package_search"),
        reply,
    )


def _looks_like_new_explicit_trip_request(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "i want to go",
            "i want to travel",
            "i want to fly",
            "find me a trip",
            "book me a trip",
            "plan a trip",
            "go to",
            "travel to",
            "fly to",
        ]
    )


def _preferences_from_explicit_destination_message(message: str) -> tuple[list[str], list[str], list[str]]:
    normalized = _normalize_control_message(message)
    required: list[str] = []
    activity: list[str] = []
    environment: list[str] = []
    if any(word in normalized for word in ["beach", "sea"]):
        required.append("beach")
        environment.append("beach")
        if any(word in normalized for word in ["activities", "activity", "water", "swim", "snorkel", "diving"]):
            activity.append("beach_activities")
    if any(word in normalized for word in ["skiing", "ski"]):
        required.append("skiing")
        activity.append("skiing")
    return (
        _title_trip_list(_normalise_trip_list(required)),
        _title_trip_list(_normalise_trip_list(activity)),
        _title_trip_list(_normalise_trip_list(environment)),
    )


def _dates_from_explicit_destination_message(
    message: str,
    active_trip: dict[str, Any],
) -> tuple[str | None, str | None, int | None, str]:
    reference = today_utc_date()
    departure = resolve_relative_travel_date(message, today=reference) or resolve_future_date(
        message,
        today=reference,
    )
    new_top_level = _looks_like_new_explicit_trip_request(message)
    if departure is None and not new_top_level:
        active_dates = _active_trip_dates(active_trip)
        departure = active_dates.get("departureDate")
        return_date = active_dates.get("returnDate")
        nights = active_trip.get("tripDurationNights")
        if departure and return_date:
            try:
                nights = (date.fromisoformat(return_date) - date.fromisoformat(departure)).days
            except ValueError:
                pass
        return departure, return_date, int(nights) if nights else None, "active_trip"

    return_after_days = _extract_return_after_days(message)
    nights = return_after_days or _extract_trip_duration_nights(message)
    return_date = None
    if departure and nights:
        return_date = (date.fromisoformat(departure) + timedelta(days=nights)).isoformat()
    return departure, return_date, nights, "explicit_message"


def _set_explicit_destination_active_trip(
    db: Session,
    session_id: int,
    destination: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    active_trip = _active_trip_for_session(session_id, db)
    new_top_level = _looks_like_new_explicit_trip_request(message)
    old_destination = _active_trip_destination(active_trip)
    old_code = str((old_destination or {}).get("airportCode") or "").upper()
    new_code = str(destination.get("airportCode") or "").upper()
    if new_top_level or (old_code and new_code and old_code != new_code):
        _invalidate_dependent_state(
            db,
            session_id,
            "explicit_destination_changed",
            clear_destinations=True,
        )
        _clear_active_trip_package_state(active_trip)
        active_trip["latestDatePriceComparison"] = None
        active_trip["selectedFlightCandidate"] = None

    required, activity, environment = _preferences_from_explicit_destination_message(message)
    departure, return_date, nights, date_source = _dates_from_explicit_destination_message(
        message,
        active_trip,
    )
    duration_days, duration_nights = _extract_trip_duration_components(message)
    if duration_nights and departure and _extract_return_after_days(message) is None:
        nights = duration_nights
        return_date = (date.fromisoformat(departure) + timedelta(days=nights)).isoformat()
    budget = _extract_budget_amount(message)

    active_trip["selectedDestination"] = {
        "city": destination.get("city"),
        "country": destination.get("country"),
        "airportCode": new_code,
        "airportName": destination.get("airportName"),
        "source": "explicit_destination",
        "recommendationId": new_code,
    }
    active_trip["dates"] = {
        "departureDate": departure,
        "returnDate": return_date,
        "source": date_source,
    }
    if nights:
        active_trip["tripDurationNights"] = nights
    elif new_top_level:
        active_trip["tripDurationNights"] = None
    if duration_days:
        active_trip["tripDurationDays"] = duration_days
    elif new_top_level:
        active_trip["tripDurationDays"] = None
    if budget is not None:
        active_trip["budget"] = budget
    if required or new_top_level:
        active_trip["requiredActivities"] = required
    if activity or new_top_level:
        active_trip["activityPreferences"] = activity
    if environment or new_top_level:
        active_trip["environmentPreferences"] = environment
    if new_top_level:
        active_trip["regionPreferences"] = []
        active_trip["excludedRegions"] = []
        active_trip["requestedProducts"] = []
    active_trip["status"] = "ready_for_search" if departure else "destination_selected"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    return save_active_trip(db, session_id, active_trip)


def _pending_flight_missing_fields(pending: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    origin = pending.get("origin") if isinstance(pending.get("origin"), dict) else {}
    destination = (
        pending.get("destination")
        if isinstance(pending.get("destination"), dict)
        else {}
    )
    if not origin.get("airportCode"):
        missing.append("origin")
    if not destination.get("airportCode"):
        missing.append("destination")
    if not pending.get("departureDate"):
        missing.append("departureDate")
    return missing


def _pending_flight_request(active_trip: dict[str, Any]) -> dict[str, Any] | None:
    pending = active_trip.get("pendingFlightRequest")
    return pending if isinstance(pending, dict) and pending.get("intent") == "flight" else None


def _build_pending_flight_request_from_active_trip(
    active_trip: dict[str, Any],
) -> dict[str, Any] | None:
    destination = _active_trip_destination(active_trip)
    if not destination or not destination.get("airportCode"):
        return None
    dates = _active_trip_dates(active_trip)
    pending = {
        "intent": "flight",
        "origin": deepcopy(_active_trip_origin(active_trip)),
        "destination": deepcopy(destination),
        "departureDate": dates.get("departureDate"),
        "returnDate": dates.get("returnDate"),
        "tripDurationDays": active_trip.get("tripDurationDays"),
        "tripDurationNights": active_trip.get("tripDurationNights"),
        "passengerCount": active_trip.get("passengerCount") or 1,
        "cabin": active_trip.get("cabinClass") or "economy",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "lastUpdatedAt": datetime.now(timezone.utc).isoformat(),
    }
    pending["missingFields"] = _pending_flight_missing_fields(pending)
    return pending


def _save_pending_flight_request(
    db: Session,
    session_id: int,
    active_trip: dict[str, Any],
    pending: dict[str, Any],
) -> dict[str, Any]:
    active_trip["pendingFlightRequest"] = pending
    active_trip["latestVisibleResultType"] = "flight_clarification"
    active_trip["status"] = "flight_clarification"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    return save_active_trip(db, session_id, active_trip)


def _pending_flight_question(pending: dict[str, Any]) -> str:
    missing = _pending_flight_missing_fields(pending)
    if "origin" in missing:
        return "Where are you flying from?"
    if "destination" in missing:
        return "Where would you like to fly to?"
    return "When do you want to depart?"


def _resolve_pending_flight_departure_date(
    message: str,
    active_trip: dict[str, Any],
) -> str | None:
    return (
        _extract_departure_return_dates_from_message(message, active_trip)[0]
        or _resolve_day_only_followup(message, base_date=_active_trip_reference_month(active_trip))
        or resolve_relative_travel_date(message, today=today_utc_date())
        or resolve_future_date(message, today=today_utc_date())
    )


def _merge_pending_flight_request(
    pending: dict[str, Any],
    message: str,
    active_trip: dict[str, Any],
) -> dict[str, Any]:
    pending = deepcopy(pending)
    origin = _origin_from_package_message(message)
    destination = _destination_from_package_message(message)
    route = _route_from_package_message(message)
    if route:
        origin, destination = route
    if origin:
        pending["origin"] = origin
    elif not isinstance(pending.get("origin"), dict) or not pending["origin"].get("airportCode"):
        active_origin = _active_trip_origin(active_trip)
        if active_origin:
            pending["origin"] = deepcopy(active_origin)
    if destination:
        pending["destination"] = destination

    departure_iso, explicit_return_iso = _extract_departure_return_dates_from_message(
        message,
        active_trip,
    )
    if departure_iso is None:
        departure_iso = _resolve_pending_flight_departure_date(message, active_trip)
    if departure_iso:
        pending["departureDate"] = departure_iso
    if explicit_return_iso:
        pending["returnDate"] = explicit_return_iso

    duration_days, duration_nights = _extract_trip_duration_components(message)
    if duration_days:
        pending["tripDurationDays"] = duration_days
    if duration_nights:
        pending["tripDurationNights"] = duration_nights
    nights = pending.get("tripDurationNights")
    if pending.get("departureDate") and nights and not explicit_return_iso:
        pending["returnDate"] = (
            date.fromisoformat(str(pending["departureDate"])) + timedelta(days=int(nights))
        ).isoformat()

    pending["lastUpdatedAt"] = datetime.now(timezone.utc).isoformat()
    pending["missingFields"] = _pending_flight_missing_fields(pending)
    return pending


def _flight_search_from_pending_request(pending: dict[str, Any]) -> FlightSearchRequest:
    origin = pending.get("origin") if isinstance(pending.get("origin"), dict) else {}
    destination = (
        pending.get("destination")
        if isinstance(pending.get("destination"), dict)
        else {}
    )
    return_date = pending.get("returnDate")
    payload = {
        "searchType": "flight",
        "passengerCount": int(pending.get("passengerCount") or 1),
        "itinerary": {
            "tripType": "round_trip" if return_date else "one_way",
            "origin": {
                "city": origin.get("city"),
                "code": str(origin.get("airportCode") or "").upper(),
            },
            "destination": {
                "city": destination.get("city"),
                "code": str(destination.get("airportCode") or "").upper(),
            },
            "dates": {"departure": pending.get("departureDate")},
        },
        "preferences": {
            "cabinClass": pending.get("cabin") or "economy",
            "maxStops": None,
        },
    }
    if return_date:
        payload["itinerary"]["dates"]["return"] = return_date
    return FlightSearchRequest.model_validate(payload)


def _apply_pending_flight_to_active_trip(
    active_trip: dict[str, Any],
    pending: dict[str, Any],
) -> None:
    origin = pending.get("origin") if isinstance(pending.get("origin"), dict) else None
    destination = (
        pending.get("destination")
        if isinstance(pending.get("destination"), dict)
        else None
    )
    if origin:
        active_trip["origin"] = deepcopy(origin)
    if destination:
        active_trip["selectedDestination"] = deepcopy(destination)
    active_trip["dates"] = {
        "departureDate": pending.get("departureDate"),
        "returnDate": pending.get("returnDate"),
        "source": "pending_flight_request",
    }
    if pending.get("tripDurationDays"):
        active_trip["tripDurationDays"] = int(pending["tripDurationDays"])
    if pending.get("tripDurationNights"):
        active_trip["tripDurationNights"] = int(pending["tripDurationNights"])
    active_trip["passengerCount"] = int(pending.get("passengerCount") or 1)
    active_trip["cabinClass"] = pending.get("cabin") or "economy"
    active_trip["requestedProducts"] = ["flights"]


def _execute_pending_flight_clarification(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    pending = _pending_flight_request(active_trip)
    if pending is None:
        return None
    if _looks_like_hotel_only_request(request.message) or _looks_like_package_request(request.message):
        return None

    pending = _merge_pending_flight_request(pending, request.message, active_trip)
    _apply_pending_flight_to_active_trip(active_trip, pending)
    if _pending_flight_missing_fields(pending):
        active_trip = _save_pending_flight_request(db, session.id, active_trip, pending)
        _set_last_tool_used(session.id, "flight_clarification", db)
        question_text = _pending_flight_question(pending)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            _clarification_reply_text(
                db,
                session.id,
                active_trip,
                _pending_flight_missing_fields(pending),
                question_text,
            ),
        )

    active_trip["pendingFlightRequest"] = None
    clear_last_clarification(active_trip)
    active_trip["status"] = "ready_for_search"
    active_trip["latestVisibleResultType"] = "flight_clarification"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    flight_request = _flight_search_from_pending_request(pending)
    return _execute_flight_search_request(db, session, request, flight_request)


def _execute_explicit_destination_trip_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    if _looks_like_cheapest_day_message(request.message) or _looks_like_destination_set_flight_comparison(request.message):
        return None
    destination = _resolve_explicit_destination_from_message(request.message)
    if destination is None:
        return None
    active_trip = _set_explicit_destination_active_trip(
        db,
        session.id,
        destination,
        request.message,
    )
    products = _requested_products_from_message(request.message)
    active_trip["requestedProducts"] = products
    save_active_trip(db, session.id, active_trip)
    dates = _active_trip_dates(active_trip)
    origin = _active_trip_origin(active_trip) or {}
    destination_state = _active_trip_destination(active_trip) or {}

    if not dates.get("departureDate"):
        pending = _build_pending_flight_request_from_active_trip(active_trip)
        if pending:
            active_trip = _save_pending_flight_request(db, session.id, active_trip, pending)
        if not origin.get("airportCode"):
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "flight_search"),
                "Where are you flying from?",
            )
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            (
                f"I can use {_origin_message(origin)} as your origin and "
                f"{destination_state.get('city')} ({destination_state.get('airportCode')}) "
                "as your destination. When do you want to depart?"
                if active_trip.get("tripDurationNights")
                else (
                    f"I can use {_origin_message(origin)} as your origin and "
                    f"{destination_state.get('city')} ({destination_state.get('airportCode')}) "
                    "as your destination. Please provide your departure date and return date."
                )
            ),
        )

    LOGGER.info(
        "Routing trace: %s",
        {
            "route": "explicit_destination_trip_search",
            "selectedDestination": destination_state.get("airportCode"),
            "origin": origin.get("airportCode"),
            "departureDate": dates.get("departureDate"),
            "returnDate": dates.get("returnDate"),
            "requestedProducts": products,
            "activeTripRevision": active_trip.get("revision"),
        },
    )
    return _execute_active_trip_product_search(db, session, request, products)


def _select_destination_in_active_trip(
    db: Session,
    session_id: int,
    selected_destination: dict[str, Any],
    requested_products: list[str],
) -> dict[str, Any]:
    active_trip = _active_trip_for_session(session_id, db)
    new_destination = _destination_to_active_trip_destination(selected_destination)
    old_destination = active_trip.get("selectedDestination")
    old_code = (
        old_destination.get("airportCode")
        if isinstance(old_destination, dict)
        else None
    )
    new_code = new_destination.get("airportCode")
    if old_code and new_code and old_code != new_code:
        _invalidate_dependent_state(db, session_id, "selected_destination_changed")
        _clear_active_trip_package_state(active_trip)
    active_trip["selectedDestination"] = new_destination
    active_trip["requestedProducts"] = requested_products
    active_trip["status"] = "destination_selected"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    return save_active_trip(db, session_id, active_trip)


def _active_trip_origin(active_trip: dict[str, Any]) -> dict[str, Any] | None:
    origin = active_trip.get("origin")
    return origin if isinstance(origin, dict) else None


def _active_trip_destination(active_trip: dict[str, Any]) -> dict[str, Any] | None:
    destination = active_trip.get("selectedDestination")
    return destination if isinstance(destination, dict) else None


def _active_trip_dates(active_trip: dict[str, Any]) -> dict[str, Any]:
    dates = active_trip.get("dates")
    return dates if isinstance(dates, dict) else {}


def build_flight_request_from_active_trip(
    active_trip: dict[str, Any],
) -> tuple[FlightSearchRequest | None, str | None]:
    origin = _active_trip_origin(active_trip)
    destination = _active_trip_destination(active_trip)
    dates = _active_trip_dates(active_trip)
    origin_code = origin.get("airportCode") if origin else None
    destination_code = destination.get("airportCode") if destination else None
    departure = dates.get("departureDate")
    return_date = dates.get("returnDate")

    if not origin_code:
        return None, "Where are you travelling from?"
    if not destination_code:
        return None, "Please choose a destination first."
    if not departure:
        return None, "When do you want to depart?"

    payload = {
        "searchType": "flight",
        "passengerCount": active_trip.get("passengerCount") or 1,
        "itinerary": {
            "tripType": "round_trip" if return_date else "one_way",
            "origin": {
                "city": origin.get("city"),
                "code": str(origin_code).upper(),
            },
            "destination": {
                "city": destination.get("city"),
                "code": str(destination_code).upper(),
            },
            "dates": {"departure": departure},
        },
        "preferences": {
            "cabinClass": active_trip.get("cabinClass") or "economy",
            "maxStops": None,
        },
    }
    if return_date:
        payload["itinerary"]["dates"]["return"] = return_date
    try:
        return FlightSearchRequest.model_validate(payload), None
    except Exception as exc:
        LOGGER.warning("Active trip flight request validation failed: %s", exc)
        return None, "I need a valid origin, destination, and travel date before searching flights."


def build_hotel_request_from_active_trip(
    active_trip: dict[str, Any],
) -> tuple[HotelSearchRequest | None, str | None]:
    destination = _active_trip_destination(active_trip)
    dates = _active_trip_dates(active_trip)
    city = destination.get("city") if destination else None
    departure = dates.get("departureDate")
    return_date = dates.get("returnDate")
    if not city:
        return None, "Please choose a destination first."
    if not departure or not return_date:
        return None, "Please provide your stay length or check-out date before I search hotels."

    payload = {
        "searchType": "hotel",
        "destination": {
            "city": city,
            "country": destination.get("country") if destination else None,
        },
        "checkInDate": departure,
        "checkOutDate": return_date,
        "guestCount": active_trip.get("passengerCount") or 1,
        "roomCount": 1,
        "preferences": {"currency": "GBP"},
    }
    try:
        return HotelSearchRequest.model_validate(payload), None
    except Exception as exc:
        LOGGER.warning("Active trip hotel request validation failed: %s", exc)
        return None, "I need valid hotel dates before searching stays."


def build_package_request_from_active_trip(
    active_trip: dict[str, Any],
) -> tuple[PackageBuildRequest | None, str | None]:
    origin = _active_trip_origin(active_trip)
    destination = _active_trip_destination(active_trip)
    dates = _active_trip_dates(active_trip)
    origin_code = str((origin or {}).get("airportCode") or "").upper()
    destination_code = str((destination or {}).get("airportCode") or "").upper()
    destination_city = (destination or {}).get("city")
    departure = dates.get("departureDate")
    return_date = dates.get("returnDate")

    if not origin_code:
        return None, "Where are you travelling from?"
    if not destination_code or not destination_city:
        return None, "Please choose a destination first."
    if not departure:
        return None, "When do you want to depart?"
    if not return_date:
        return None, "Please provide your stay length or return date before I build a package."

    passenger_count = int(active_trip.get("passengerCount") or 1)
    payload = {
        "originAirportCode": origin_code,
        "destinationAirportCode": destination_code,
        "destinationCity": destination_city,
        "destinationCountry": (destination or {}).get("country"),
        "departureDate": departure,
        "returnDate": return_date,
        "passengerCount": passenger_count,
        "rooms": int(active_trip.get("rooms") or 1),
        "adults": max(1, int(active_trip.get("adults") or passenger_count)),
        "children": int(active_trip.get("children") or 0),
        "cabin": active_trip.get("cabinClass") or "economy",
        "maxBudget": active_trip.get("budget"),
        "maxPackages": 5,
    }
    try:
        return PackageBuildRequest.model_validate(payload), None
    except Exception as exc:
        LOGGER.warning("Active trip package request validation failed: %s", exc)
        return None, "I need a valid origin, destination, and trip dates before building a package."


def _flight_request_from_package_request(
    package_request: PackageBuildRequest,
) -> FlightSearchRequest:
    return FlightSearchRequest.model_validate(
        {
            "searchType": "flight",
            "passengerCount": package_request.passenger_count,
            "itinerary": {
                "tripType": "round_trip",
                "origin": {"city": None, "code": package_request.origin_airport_code},
                "destination": {
                    "city": package_request.destination_city,
                    "code": package_request.destination_airport_code,
                },
                "dates": {
                    "departure": package_request.departure_date.isoformat(),
                    "return": package_request.return_date.isoformat(),
                },
            },
            "preferences": {
                "cabinClass": package_request.cabin,
                "maxStops": None,
            },
        }
    )


def _hotel_request_from_package_request(
    package_request: PackageBuildRequest,
) -> HotelSearchRequest:
    return HotelSearchRequest.model_validate(
        {
            "searchType": "hotel",
            "destination": {
                "city": package_request.destination_city,
                "country": package_request.destination_country,
            },
            "checkInDate": package_request.departure_date.isoformat(),
            "checkOutDate": package_request.return_date.isoformat(),
            "guestCount": package_request.passenger_count,
            "roomCount": package_request.rooms,
            "preferences": {
                "currency": "GBP",
                "minRating": package_request.hotel_star_rating,
            },
        }
    )


def _package_request_matches_active_trip(
    package_request: PackageBuildRequest,
    active_trip: dict[str, Any],
) -> bool:
    origin = _active_trip_origin(active_trip) or {}
    destination = _active_trip_destination(active_trip) or {}
    dates = _active_trip_dates(active_trip)
    return (
        package_request.origin_airport_code.upper()
        == str(origin.get("airportCode") or "").upper()
        and package_request.destination_airport_code.upper()
        == str(destination.get("airportCode") or "").upper()
        and package_request.destination_city == destination.get("city")
        and package_request.departure_date.isoformat() == dates.get("departureDate")
        and package_request.return_date.isoformat() == dates.get("returnDate")
    )


def _priced_flights(provider_response: FlightSearchResponse) -> list[dict[str, Any]]:
    flights = [
        flight
        for flight in _normalised_flight_dicts(provider_response)
        if _flight_price(flight) is not None
    ]
    return sorted(flights, key=lambda flight: _flight_price(flight) or float("inf"))


def _priced_hotels(provider_response: HotelSearchResponse) -> list[dict[str, Any]]:
    hotels = [
        hotel
        for hotel in _normalised_hotel_dicts(provider_response)
        if _hotel_price(hotel) is not None
    ]
    return sorted(
        hotels,
        key=lambda hotel: (
            _hotel_price(hotel) or float("inf"),
            -(float(hotel.get("rating") or 0)),
        ),
    )


def _build_package_options(
    package_request: PackageBuildRequest,
    flight_response: FlightSearchResponse,
    hotel_response: HotelSearchResponse,
    active_trip_revision: int,
) -> list[dict[str, Any]]:
    flights = _priced_flights(flight_response)[:3]
    hotels = _priced_hotels(hotel_response)[:3]
    packages: list[dict[str, Any]] = []
    nights = (package_request.return_date - package_request.departure_date).days
    option_number = 1
    for flight in flights:
        flight_summary = _flight_summary_for_package(flight, _flight_price)
        for hotel in hotels:
            hotel_summary = _hotel_summary_for_package(hotel, _hotel_price)
            if flight_summary.currency != hotel_summary.currency:
                continue
            total = round(flight_summary.price + hotel_summary.price, 2)
            package = PackageOption(
                packageId=(
                    f"PKG-{package_request.destination_airport_code}-"
                    f"{option_number}-{flight_summary.offerId[-6:] or 'FLT'}-"
                    f"{(hotel_summary.hotelId or 'HOT')[-6:]}"
                ),
                optionNumber=option_number,
                destinationCity=package_request.destination_city,
                destinationAirportCode=package_request.destination_airport_code,
                flight=flight_summary,
                hotel=hotel_summary,
                totalPrice=total,
                currency=flight_summary.currency,
                nights=nights,
                passengers=package_request.passenger_count,
                rooms=package_request.rooms,
                recommendationScore=max(50, min(95, 95 - ((option_number - 1) * 5))),
                reasons=[
                    "Includes return flight and hotel stay",
                    f"Uses selected {nights}-night trip dates",
                    (
                        "Flight route matches active trip "
                        f"{package_request.origin_airport_code} to "
                        f"{package_request.destination_airport_code}"
                    ),
                    f"Hotel price covers {nights} nights",
                ],
                warnings=[],
                providerSummary=(
                    f"Flight: {flight_summary.provider}; hotel: {hotel_summary.provider}"
                ),
                activeTripRevision=active_trip_revision,
            )
            packages.append(package.model_dump(mode="json"))
            option_number += 1
            if len(packages) >= package_request.max_packages:
                return _sort_package_options_for_budget(packages, package_request.max_budget)
    return _sort_package_options_for_budget(packages, package_request.max_budget)


def _looks_like_package_request(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "package",
            "full package",
            "flight and hotel",
            "flights and hotels",
            "hotel and flight",
            "with hotel",
            "include hotel",
            "total price",
            "full trip price",
            "how much all together",
            "find me flights with hotel",
            "give me the total",
            "package deals",
            "available packages",
            "any deals",
            "deals available",
        ]
    )


def _looks_like_package_selection(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "select package",
            "add package",
            "confirm package",
            "add it to cart",
            "add this to cart",
            "choose package",
            "i like package",
            "i want package",
            "continue with option",
            "continue option",
            "go with option",
            "go for option",
            "take the first one",
            "take first one",
        ]
    ) or bool(
        re.fullmatch(
            r"(?:the\s+)?(?:option|package)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)",
            normalized,
        )
    ) or (
        bool(
            re.search(
                r"\b(?:choose|select|want|like|continue|go with)\s+(?:this\s+)?(?:option\s+)?(?:\d+|one|two|three|four|five)\b",
                normalized,
            )
        )
        and "package" in normalized
    )


def _looks_like_package_checkout(message: str) -> bool:
    normalized = _normalize_control_message(message)
    if any(
        phrase in normalized
        for phrase in [
            "checkout",
            "check out",
            "proceed to checkout",
            "go to checkout",
            "take me to checkout",
            "continue to checkout",
            "finish checkout",
            "confirm checkout",
            "complete checkout",
            "yes checkout",
            "pay now",
        ]
    ):
        return True
    return normalized in {"checkout", "check out"}


def _active_trip_cart_has_items(active_trip: dict[str, Any]) -> bool:
    cart = active_trip.get("cart") if isinstance(active_trip.get("cart"), dict) else None
    items = cart.get("items") if isinstance(cart, dict) else None
    return isinstance(items, list) and len(items) > 0


def _looks_like_contextual_checkout_request(
    message: str,
    active_trip: dict[str, Any],
) -> bool:
    if _looks_like_package_checkout(message):
        return True
    normalized = _normalize_control_message(message)
    if _active_trip_cart_has_items(active_trip) and any(
        phrase in normalized
        for phrase in ["book it", "confirm booking", "complete booking", "continue", "next step"]
    ):
        return True
    if normalized in {"continue", "next step"}:
        return _active_trip_cart_has_items(active_trip) or active_trip.get("latestVisibleResultType") in {
            "cart",
            "package_added",
        }
    return False


def _execute_flexible_trip_field_collection_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    if _resolve_explicit_destination_from_message(request.message) is not None:
        return None
    departure_iso, return_iso = _extract_departure_return_dates_from_message(
        request.message,
        active_trip,
    )
    departure_day, return_day = _extract_departure_return_day_numbers(request.message)
    budget = _extract_budget_amount(request.message)
    duration_days, duration_nights = _extract_trip_duration_components(request.message)
    has_explicit_date_range = departure_day is not None and return_day is not None
    if not (budget is not None or has_explicit_date_range):
        return None

    dates = active_trip.get("dates") if isinstance(active_trip.get("dates"), dict) else {}
    if departure_iso:
        dates["departureDate"] = departure_iso
    if return_iso:
        dates["returnDate"] = return_iso
    elif departure_iso and duration_nights:
        dates["returnDate"] = (
            date.fromisoformat(departure_iso) + timedelta(days=duration_nights)
        ).isoformat()
    dates["source"] = "field_collection"
    active_trip["dates"] = dates
    if duration_days:
        active_trip["tripDurationDays"] = duration_days
    if duration_nights:
        active_trip["tripDurationNights"] = duration_nights
    if budget is not None:
        active_trip["budget"] = budget
    origin = active_trip.get("origin") if isinstance(active_trip.get("origin"), dict) else None
    if not origin:
        default_origin = _default_origin_for_session(session.id, db)
        if default_origin:
            active_trip["origin"] = deepcopy(default_origin)
    active_trip["status"] = "collecting_trip_fields"
    active_trip["latestVisibleResultType"] = "trip_clarification"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    _set_last_tool_used(session.id, "collect_trip_fields", db)

    details: list[str] = []
    if dates.get("departureDate") and dates.get("returnDate"):
        details.append(
            f"{_display_date_label(str(dates['departureDate'])) or dates['departureDate']} "
            f"to {_display_date_label(str(dates['returnDate'])) or dates['returnDate']}"
        )
    elif dates.get("departureDate"):
        details.append(
            f"departing {_display_date_label(str(dates['departureDate'])) or dates['departureDate']}"
        )
    if budget is not None:
        details.append(f"a budget of GBP {budget:,.0f}")
    if duration_nights:
        details.append(f"{duration_nights} night{'s' if duration_nights != 1 else ''}")
    detail_text = ", ".join(details)
    reply = (
        f"Got it - I'll use {detail_text}. Where would you like to go?"
        if detail_text
        else "Got it. Where would you like to go?"
    )
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "package_search"),
        reply,
    )


def _looks_like_hotel_only_request(message: str) -> bool:
    normalized = _normalize_control_message(message)
    if _looks_like_flight_or_travel_request(message):
        return False
    has_explicit_hotel_language = any(
        phrase in normalized
        for phrase in [
            "hotel",
            "hotels",
            "stays",
            "accommodation",
            "room",
            "rooms",
            "place to stay",
            "places to stay",
            "stay near",
            "stay in central",
        ]
    )
    if not has_explicit_hotel_language:
        return False
    return True


def _hotel_city_from_message(message: str) -> str | None:
    normalized = _normalize_control_message(message)
    match = re.search(r"\b(?:hotel|hotels|stay|accommodation)\s+(?:in|near)\s+([a-z][a-z\s]{1,40})", normalized)
    if match:
        candidate = match.group(1).strip()
        candidate = re.split(
            r"\b(?:near|from|on|for|with|and|central|downtown)\b",
            candidate,
            maxsplit=1,
        )[0].strip()
        if candidate:
            return candidate.title()
    return None


def _hotel_area_or_landmark_from_message(message: str) -> tuple[str | None, str | None]:
    normalized = _normalize_control_message(message)
    landmark: str | None = None
    area_parts: list[str] = []
    near_match = re.search(r"\bnear\s+([a-z][a-z\s]{1,45})", normalized)
    if near_match and landmark is None:
        near_value = re.split(r"\b(?:on|for|from|with|and)\b", near_match.group(1), maxsplit=1)[0].strip()
        if near_value:
            landmark = near_value.title()
    if landmark:
        area_parts.append(f"near {landmark}")
    label = " ".join(area_parts) if area_parts else None
    return landmark, label


def _pending_hotel_missing_fields(pending: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not pending.get("city"):
        missing.append("city")
    if not pending.get("checkInDate"):
        missing.append("checkInDate")
    if not pending.get("checkOutDate") and not pending.get("nights"):
        missing.append("nights")
    return missing


def _new_pending_hotel_request(message: str) -> dict[str, Any] | None:
    if not _looks_like_hotel_only_request(message):
        return None
    city = _hotel_city_from_message(message)
    landmark, location_label = _hotel_area_or_landmark_from_message(message)
    if not city and not landmark and not location_label:
        return None
    duration_days, duration_nights = _extract_trip_duration_components(message)
    nights = duration_nights or (max(duration_days - 1, 1) if duration_days else None)
    month_days = extract_yearless_month_days(message)
    if len(month_days) >= 2:
        check_in = resolve_future_date(
            f"{month_days[0][1]} {month_name[month_days[0][0]]}",
            today=today_utc_date(),
        )
        check_out = resolve_future_date(
            f"{month_days[1][1]} {month_name[month_days[1][0]]}",
            today=today_utc_date(),
        )
    else:
        check_in, check_out = _extract_departure_return_dates_from_message(message, {})
    if check_in and not check_out and nights:
        check_out = (date.fromisoformat(check_in) + timedelta(days=int(nights))).isoformat()
    if check_in and check_out and not nights:
        try:
            nights = max(1, (date.fromisoformat(check_out) - date.fromisoformat(check_in)).days)
        except ValueError:
            nights = None
    guest_match = re.search(
        r"\b(?:for\s+)?(\d+)\s+(?:guest|guests|adult|adults|people|travellers|travelers)\b",
        _normalize_control_message(message),
    )
    guests = int(guest_match.group(1)) if guest_match else 1
    pending = {
        "intent": "hotel",
        "city": city,
        "country": None,
        "areaOrLandmark": landmark or location_label,
        "locationLabel": location_label or city,
        "checkInDate": check_in,
        "checkOutDate": check_out,
        "nights": nights,
        "guests": guests,
        "rooms": 1,
        "maxBudget": _extract_budget_amount(message),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "lastUpdatedAt": datetime.now(timezone.utc).isoformat(),
    }
    pending["missingFields"] = _pending_hotel_missing_fields(pending)
    return pending


def _pending_hotel_request(active_trip: dict[str, Any]) -> dict[str, Any] | None:
    pending = active_trip.get("pendingHotelRequest")
    return pending if isinstance(pending, dict) and pending.get("intent") == "hotel" else None


def _message_should_leave_pending_hotel_context(message: str) -> bool:
    normalized = _normalize_control_message(message)
    if not normalized:
        return False
    if _looks_like_hotel_only_request(message):
        return False
    if _looks_like_flight_or_travel_request(message):
        return True
    return _resolve_explicit_destination_from_message(message) is not None and any(
        phrase in normalized for phrase in ["go to", "travel to", "fly to", "trip to"]
    )


def _hotel_question(pending: dict[str, Any], *, just_set_check_in: bool = False) -> str:
    city = pending.get("city") or "your chosen city"
    label = pending.get("locationLabel") or city
    if (
        pending.get("city")
        and isinstance(label, str)
        and label.lower().startswith("near ")
    ):
        label = f"{city} {label}"
    if "city" in _pending_hotel_missing_fields(pending):
        return "Which city would you like to stay in?"
    if "checkInDate" in _pending_hotel_missing_fields(pending):
        return f"Sure - I can search hotels in {label}. When do you want to check in, and how many nights will you stay?"
    if "nights" in _pending_hotel_missing_fields(pending):
        prefix = (
            f"Great - I'll use {_display_date_label(pending.get('checkInDate')) or pending.get('checkInDate')} "
            "as your check-in date. "
            if just_set_check_in and pending.get("checkInDate")
            else ""
        )
        return f"{prefix}How many nights will you stay?"
    return f"I have enough details to search hotels in {city}."


def _save_pending_hotel_request(
    db: Session,
    session_id: int,
    active_trip: dict[str, Any],
    pending: dict[str, Any],
) -> dict[str, Any]:
    active_trip["pendingHotelRequest"] = pending
    active_trip["latestVisibleResultType"] = "hotel_clarification"
    active_trip["status"] = "hotel_clarification"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    return save_active_trip(db, session_id, active_trip)


def _merge_pending_hotel_request(
    pending: dict[str, Any],
    message: str,
    active_trip: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    pending = deepcopy(pending)
    just_set_check_in = False
    city = _hotel_city_from_message(message)
    if city:
        pending["city"] = city
    landmark, location_label = _hotel_area_or_landmark_from_message(message)
    if landmark or location_label:
        pending["areaOrLandmark"] = landmark or location_label
        pending["locationLabel"] = location_label or pending.get("city")

    check_in, explicit_check_out = _extract_departure_return_dates_from_message(message, active_trip)
    model_nights: int | None = None
    if check_in:
        pending["checkInDate"] = check_in
        just_set_check_in = True
    if explicit_check_out:
        pending["checkOutDate"] = explicit_check_out
        try:
            pending["nights"] = max(
                1,
                (date.fromisoformat(explicit_check_out) - date.fromisoformat(str(pending["checkInDate"]))).days,
            )
        except (TypeError, ValueError):
            pass
    if not check_in and not pending.get("checkInDate"):
        answer = resolve_clarification_answer_with_llama(
            asked_question=_hotel_question(pending),
            user_reply=message,
            awaiting=["departure_date", "nights"],
        )
        if answer and answer.resolved and answer.departure_date:
            validated_check_in = validate_future_iso(answer.departure_date)
            if validated_check_in:
                pending["checkInDate"] = validated_check_in
                just_set_check_in = True
                model_nights = answer.nights
                if answer.return_date:
                    validated_check_out = validate_future_iso(answer.return_date)
                    if validated_check_out:
                        try:
                            if date.fromisoformat(validated_check_out) > date.fromisoformat(validated_check_in):
                                pending["checkOutDate"] = validated_check_out
                                pending["nights"] = (
                                    date.fromisoformat(validated_check_out)
                                    - date.fromisoformat(validated_check_in)
                                ).days
                        except ValueError:
                            pass

    duration_days, duration_nights = _extract_trip_duration_components(message)
    if duration_nights:
        pending["nights"] = duration_nights
    elif model_nights:
        pending["nights"] = model_nights
    elif duration_days:
        pending["nights"] = max(duration_days - 1, 1)
    if pending.get("checkInDate") and pending.get("nights") and not pending.get("checkOutDate"):
        pending["checkOutDate"] = (
            date.fromisoformat(str(pending["checkInDate"])) + timedelta(days=int(pending["nights"]))
        ).isoformat()
    budget = _extract_budget_amount(message)
    if budget is not None:
        pending["maxBudget"] = budget
    pending["missingFields"] = _pending_hotel_missing_fields(pending)
    pending["lastUpdatedAt"] = datetime.now(timezone.utc).isoformat()
    return pending, just_set_check_in


def _structured_hotel_date_value(arguments: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in arguments and arguments.get(key) is not None:
            return arguments.get(key)
    return None


def _merge_pending_hotel_request_from_structured_arguments(
    pending: dict[str, Any],
    arguments: dict[str, Any],
    active_trip: dict[str, Any],
) -> dict[str, Any]:
    arguments = _canonicalise_structured_trip_arguments(arguments)
    pending = deepcopy(pending)
    city = (
        arguments.get("destination_text")
        or arguments.get("destinationText")
        or arguments.get("destination_city")
        or arguments.get("destinationCity")
    )
    if city:
        pending["city"] = str(city).strip().title()
        pending["locationLabel"] = pending.get("locationLabel") or pending["city"]

    month = _normalise_structured_month(arguments.get("month")) or _normalise_structured_month(
        active_trip.get("month")
    )
    date_text = arguments.get("date_text") or arguments.get("dateText")
    departure_day = _normalise_structured_day(
        arguments.get("departure_day")
        or arguments.get("departureDay")
        or arguments.get("check_in_day")
        or arguments.get("checkInDay")
    ) or _normalise_structured_day_from_text(date_text)
    check_in = _normalise_structured_date(
        _structured_hotel_date_value(
            arguments,
            "check_in_date",
            "checkInDate",
            "departure_date",
            "departureDate",
        )
    ) or _date_from_structured_month_day(month, departure_day)
    if check_in is None and departure_day is not None:
        base_month = None
        if month:
            try:
                base_month = date.fromisoformat(f"{month}-01")
            except ValueError:
                base_month = None
        check_in = _resolve_day_only_followup(f"the {departure_day}th", base_date=base_month)
    if check_in is None and date_text:
        check_in = (
            resolve_relative_travel_date(date_text, today=today_utc_date())
            or resolve_future_date(date_text, today=today_utc_date())
            or _resolve_named_month_date_from_message(str(date_text), reference=today_utc_date())
        )
    if check_in:
        pending["checkInDate"] = check_in

    nights = arguments.get("nights") or arguments.get("tripDurationNights")
    try:
        nights_int = int(nights) if nights is not None else None
    except (TypeError, ValueError):
        nights_int = None
    if nights_int:
        pending["nights"] = nights_int

    return_day = _normalise_structured_day(
        arguments.get("return_day")
        or arguments.get("returnDay")
        or arguments.get("check_out_day")
        or arguments.get("checkOutDay")
    )
    check_out = _normalise_structured_date(
        _structured_hotel_date_value(
            arguments,
            "check_out_date",
            "checkOutDate",
            "return_date",
            "returnDate",
        )
    ) or _date_from_structured_month_day(month, return_day)
    if not check_out and pending.get("checkInDate") and pending.get("nights"):
        check_out = (
            date.fromisoformat(str(pending["checkInDate"])) + timedelta(days=int(pending["nights"]))
        ).isoformat()
    if check_out:
        pending["checkOutDate"] = check_out

    budget = (
        arguments.get("budget_amount")
        or arguments.get("budgetAmount")
        or arguments.get("budget")
        or arguments.get("maxBudget")
        or arguments.get("max_budget")
    )
    try:
        pending["maxBudget"] = float(budget) if budget is not None else pending.get("maxBudget")
    except (TypeError, ValueError):
        pass
    pending["missingFields"] = _pending_hotel_missing_fields(pending)
    pending["lastUpdatedAt"] = datetime.now(timezone.utc).isoformat()
    return pending


def _execute_pending_hotel_structured_clarification(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    pending = _pending_hotel_request(active_trip)
    if pending is None or tool_call.tool not in {"prepare_trip_request", "search_hotels"}:
        return None
    merged = _merge_pending_hotel_request_from_structured_arguments(
        pending,
        tool_call.arguments,
        active_trip,
    )
    active_trip = _save_pending_hotel_request(db, session.id, active_trip, merged)
    if _pending_hotel_missing_fields(merged):
        _set_last_tool_used(session.id, "hotel_clarification", db)
        question_text = _hotel_question(merged)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "hotel_search"),
            _clarification_reply_text(
                db,
                session.id,
                active_trip,
                _pending_hotel_missing_fields(merged),
                question_text,
            ),
        )

    hotel_search = _hotel_search_from_pending_request(merged)
    active_trip["pendingHotelRequest"] = None
    clear_last_clarification(active_trip)
    active_trip["status"] = "ready_for_hotel_search"
    active_trip["latestVisibleResultType"] = "hotel_clarification"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    response = _execute_hotel_search_request(db, session, request, hotel_search)
    label = merged.get("locationLabel")
    if label and response.results.hotels:
        city = merged.get("city") or "your chosen city"
        if str(label).lower().startswith("near "):
            response.reply = f"I searched hotels in {city} {label}. {response.reply}"
        else:
            response.reply = f"I searched hotels in {city} around {label}. {response.reply}"
    return response


def _hotel_search_from_pending_request(pending: dict[str, Any]) -> HotelSearchRequest:
    preferences: dict[str, Any] = {"currency": "GBP"}
    if pending.get("maxBudget") is not None:
        preferences["maxPrice"] = pending["maxBudget"]
    return HotelSearchRequest.model_validate(
        {
            "searchType": "hotel",
            "destination": {
                "city": pending.get("city"),
                "country": pending.get("country"),
            },
            "checkInDate": pending.get("checkInDate"),
            "checkOutDate": pending.get("checkOutDate"),
            "guestCount": int(pending.get("guests") or 1),
            "roomCount": int(pending.get("rooms") or 1),
            "preferences": preferences,
        }
    )


def _pending_hotel_from_structured_trip(
    active_trip: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    destination = _active_trip_destination(active_trip) or {}
    dates = _active_trip_dates(active_trip)
    city = (
        arguments.get("destination_text")
        or arguments.get("destinationText")
        or destination.get("city")
    )
    landmark = (
        arguments.get("area_or_landmark")
        or arguments.get("areaOrLandmark")
        or arguments.get("landmark")
    )
    location_label = (
        arguments.get("location_label")
        or arguments.get("locationLabel")
        or (f"near {landmark}" if landmark else city)
    )
    pending = {
        "intent": "hotel",
        "city": str(city).strip().title() if city else None,
        "country": destination.get("country"),
        "areaOrLandmark": str(landmark).strip() if landmark else None,
        "locationLabel": str(location_label).strip() if location_label else None,
        "checkInDate": dates.get("departureDate"),
        "checkOutDate": dates.get("returnDate"),
        "nights": active_trip.get("tripDurationNights"),
        "guests": int(active_trip.get("passengerCount") or 1),
        "rooms": int(active_trip.get("rooms") or 1),
        "maxBudget": active_trip.get("budget"),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "lastUpdatedAt": datetime.now(timezone.utc).isoformat(),
    }
    pending["missingFields"] = _pending_hotel_missing_fields(pending)
    return pending


def _execute_hotel_only_request_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    pending = _new_pending_hotel_request(request.message)
    if pending is None:
        return None
    active_trip = _active_trip_for_session(session.id, db)
    _clear_active_trip_package_state(active_trip)
    active_trip["selectedDestination"] = None
    active_trip["requestedProducts"] = ["hotels"]
    active_trip = _save_pending_hotel_request(db, session.id, active_trip, pending)
    if not _pending_hotel_missing_fields(pending):
        active_trip["pendingHotelRequest"] = None
        active_trip["status"] = "ready_for_hotel_search"
        active_trip["latestVisibleResultType"] = "hotel_clarification"
        active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
        save_active_trip(db, session.id, active_trip)
        return _execute_hotel_search_request(
            db,
            session,
            request,
            _hotel_search_from_pending_request(pending),
        )
    _set_last_tool_used(session.id, "hotel_clarification", db)
    question_text = _hotel_question(pending)
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "hotel_search"),
        _clarification_reply_text(
            db,
            session.id,
            active_trip,
            _pending_hotel_missing_fields(pending),
            question_text,
        ),
    )


def _execute_pending_hotel_clarification(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    pending = _pending_hotel_request(active_trip)
    if pending is None:
        return None
    if _looks_like_package_request(request.message):
        city = str(pending.get("city") or "").strip()
        destination = _airport_from_package_location_text(city) if city else None
        if destination:
            active_trip["selectedDestination"] = _package_airport_payload(
                destination,
                "hotel_to_package",
            )
        if pending.get("checkInDate") or pending.get("checkOutDate"):
            active_trip["dates"] = {
                "departureDate": pending.get("checkInDate"),
                "returnDate": pending.get("checkOutDate"),
                "source": "hotel_to_package",
            }
        if pending.get("nights"):
            active_trip["tripDurationNights"] = int(pending["nights"])
        if pending.get("maxBudget") is not None:
            active_trip["budget"] = float(pending["maxBudget"])
        active_trip["pendingHotelRequest"] = None
        active_trip["requestedProducts"] = ["flights", "hotels"]
        save_active_trip(db, session.id, active_trip)
        return _execute_package_request_if_possible(
            db,
            session,
            request,
            force=True,
        )
    if _message_should_leave_pending_hotel_context(request.message):
        active_trip["pendingHotelRequest"] = None
        if active_trip.get("latestVisibleResultType") == "hotel_clarification":
            active_trip["latestVisibleResultType"] = None
        active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
        save_active_trip(db, session.id, active_trip)
        return None
    pending, just_set_check_in = _merge_pending_hotel_request(pending, request.message, active_trip)
    active_trip = _save_pending_hotel_request(db, session.id, active_trip, pending)
    if _pending_hotel_missing_fields(pending):
        _set_last_tool_used(session.id, "hotel_clarification", db)
        question_text = _hotel_question(pending, just_set_check_in=just_set_check_in)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "hotel_search"),
            _clarification_reply_text(
                db,
                session.id,
                active_trip,
                _pending_hotel_missing_fields(pending),
                question_text,
            ),
        )

    hotel_search = _hotel_search_from_pending_request(pending)
    active_trip["pendingHotelRequest"] = None
    clear_last_clarification(active_trip)
    active_trip["status"] = "ready_for_hotel_search"
    active_trip["latestVisibleResultType"] = "hotel_clarification"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    response = _execute_hotel_search_request(db, session, request, hotel_search)
    label = pending.get("locationLabel")
    if label and response.results.hotels:
        city = pending.get("city") or "your chosen city"
        if str(label).lower().startswith("near "):
            response.reply = f"I searched hotels in {city} {label}. {response.reply}"
        else:
            response.reply = f"I searched hotels in {city} around {label}. {response.reply}"
    return response


def _package_airport_payload(value: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "city": value.get("city"),
        "country": value.get("country"),
        "airportCode": str(value.get("airportCode") or "").upper(),
        "airportName": value.get("airportName"),
        "latitude": value.get("latitude"),
        "longitude": value.get("longitude"),
        "source": source,
        "recommendationId": value.get("recommendationId") or value.get("airportCode"),
    }


def _airport_from_package_location_text(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip(" .,!?")
    if not raw:
        return None
    if _normalize_control_message(raw) in {
        "live",
        "leave",
        "leaving",
        "depart",
        "departing",
        "go",
        "going",
        "come",
        "return",
        "back",
        "level",
    }:
        return None
    candidates = [raw]
    for candidate in candidates:
        airport = _known_destination_from_text(candidate)
        if airport and airport.get("airportCode"):
            return airport
    return None


DEFAULT_UK_ALTERNATIVE_ORIGIN_CODES = ["LHR", "LGW", "MAN"]


def _airport_payload_from_lookup(value: dict[str, Any], source: str) -> dict[str, Any]:
    code = str(value.get("airportCode") or value.get("code") or "").upper()
    return {
        "city": value.get("city"),
        "country": value.get("country"),
        "airportCode": code,
        "airportName": value.get("airportName") or value.get("name"),
        "latitude": value.get("latitude"),
        "longitude": value.get("longitude"),
        "source": source,
        "recommendationId": code,
    }


def _airport_payload_from_code(code: str, source: str) -> dict[str, Any] | None:
    code_text = str(code or "").strip().upper()
    if len(code_text) != 3:
        return None
    try:
        suggestions = suggest_airports(code_text)
    except Exception as exc:
        LOGGER.warning("Airport code validation lookup failed: %s", exc.__class__.__name__)
        suggestions = []
    for suggestion in suggestions:
        suggestion_code = str(
            suggestion.get("airportCode") or suggestion.get("code") or ""
        ).upper()
        if suggestion_code == code_text:
            for airport in AIRPORT_PROVIDER_CACHE:
                if str(airport.get("code") or "").upper() == code_text:
                    return {
                        "city": airport.get("city"),
                        "country": airport.get("country"),
                        "airportCode": code_text,
                        "airportName": airport.get("name"),
                        "latitude": airport.get("latitude"),
                        "longitude": airport.get("longitude"),
                        "source": source,
                        "recommendationId": code_text,
                    }
            return _airport_payload_from_lookup(suggestion, source)
    for airport in AIRPORT_PROVIDER_CACHE:
        if str(airport.get("code") or "").upper() == code_text:
            return {
                "city": airport.get("city"),
                "country": airport.get("country"),
                "airportCode": code_text,
                "airportName": airport.get("name"),
                "latitude": airport.get("latitude"),
                "longitude": airport.get("longitude"),
                "source": source,
                "recommendationId": code_text,
            }
    return None


def _airport_payload_from_structured_slot(
    *,
    code: Any = None,
    text: Any = None,
    source: str,
    require_text_match_for_code: bool = False,
) -> dict[str, Any] | None:
    if text:
        text_airport = _airport_from_package_location_text(str(text))
        code_airport = _airport_payload_from_code(str(code), source) if code else None
        if text_airport:
            text_payload = _package_airport_payload(text_airport, source)
            if code_airport:
                text_city = _normalize_control_message(str(text_payload.get("city") or ""))
                code_city = _normalize_control_message(str(code_airport.get("city") or ""))
                if text_city and code_city and text_city != code_city:
                    return text_payload
                return code_airport
            return text_payload
        if code_airport and require_text_match_for_code:
            return None
    if code:
        airport = _airport_payload_from_code(str(code), source)
        if airport:
            return airport
    return None


def _destination_region_from_structured_slots(
    destination_text: Any,
    destination_type: Any,
    destination_region: Any = None,
    candidate_codes: list[str] | None = None,
) -> dict[str, Any] | None:
    region_value = destination_region
    if isinstance(region_value, dict):
        region_value = region_value.get("name") or region_value.get("displayName")
    text_value = str(region_value or destination_text or "").strip()
    text = _normalize_control_message(text_value)
    type_text = _normalize_control_message(str(destination_type or ""))
    if type_text not in {"region", "country"} and not region_value:
        return None
    if not text:
        return None
    display_name = text_value.strip()
    region_type = "country" if type_text == "country" else "region"
    return {
        "name": display_name,
        "displayName": display_name,
        "country": display_name if region_type == "country" else None,
        "type": region_type,
        "candidateCodes": list(candidate_codes or []),
    }


def _candidate_code_from_value(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("airportCode") or value.get("code")
    code = str(value or "").strip().upper()
    return code if len(code) == 3 else None


def _destination_candidates_from_structured_slots(
    raw_candidates: Any,
) -> list[dict[str, Any]]:
    candidate_codes: list[str] = []
    if isinstance(raw_candidates, list):
        candidate_codes.extend(
            code for candidate in raw_candidates if (code := _candidate_code_from_value(candidate))
        )

    candidates: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for code in candidate_codes:
        airport = _airport_payload_from_code(code, "broad_destination_candidate")
        if airport and airport["airportCode"] not in seen_codes:
            seen_codes.add(airport["airportCode"])
            candidates.append(airport)
    return candidates


def _candidate_city_list(candidates: list[dict[str, Any]]) -> str:
    candidate_city_names = [
        str(candidate.get("city")) for candidate in candidates if candidate.get("city")
    ]
    if not candidate_city_names:
        return "the available destination airports"
    if len(candidate_city_names) == 1:
        return candidate_city_names[0]
    return ", ".join(candidate_city_names[:-1]) + f" and {candidate_city_names[-1]}"


def _filter_region_candidate_outliers(
    candidates: list[dict[str, Any]],
    region: dict[str, Any],
) -> list[dict[str, Any]]:
    if not candidates:
        return candidates

    region_country = _normalize_control_message(str(region.get("country") or ""))
    if region_country:
        exact_matches = [
            candidate
            for candidate in candidates
            if _normalize_control_message(str(candidate.get("country") or "")) == region_country
        ]
        if exact_matches:
            return exact_matches

    country_values = [
        str(candidate.get("country") or "").strip().upper()
        for candidate in candidates
        if str(candidate.get("country") or "").strip()
    ]
    if len(country_values) < 3:
        return candidates

    counts = Counter(country_values)
    most_common = counts.most_common(2)
    if not most_common:
        return candidates
    top_country, top_count = most_common[0]
    runner_up_count = most_common[1][1] if len(most_common) > 1 else 0
    if top_count >= 2 and top_count > runner_up_count:
        return [
            candidate
            for candidate in candidates
            if str(candidate.get("country") or "").strip().upper() == top_country
        ]
    return candidates


def _destination_region_display_name(active_trip: dict[str, Any]) -> str:
    region = active_trip.get("destinationRegion")
    if isinstance(region, dict):
        value = region.get("displayName") or region.get("name")
        if value:
            return str(value)
    return "your selected destination"




def _normalise_structured_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _normalise_structured_month(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        month_value = value.get("month") or value.get("name")
        year_value = value.get("year")
        month = _normalise_structured_month(month_value)
        if month and year_value is not None:
            try:
                return f"{int(year_value):04d}-{int(month.split('-', 1)[1]):02d}"
            except (TypeError, ValueError):
                return month
        return month
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return text
    normalized = _normalize_control_message(text)
    month_lookup = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    month_number = month_lookup.get(normalized)
    if month_number is None:
        return None
    today = today_utc_date()
    year = today.year if month_number >= today.month else today.year + 1
    return f"{year:04d}-{month_number:02d}"


def _normalise_structured_day(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("day", "date", "dayOfMonth"):
            day = _normalise_structured_day(value.get(key))
            if day is not None:
                return day
        return None
    try:
        day = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return day if 1 <= day <= 31 else None


def _normalise_structured_day_from_text(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return None
    match = re.search(
        r"\b([0-3]?\d)(?:st|nd|rd|th)?\s+of\s+(?:this|next)\s+month\b",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return _normalise_structured_day(match.group(1))
    match = re.search(
        r"\b(?:on|from|depart(?:ing)?|leave|leaving|the)\s+(?:the\s+)?([0-3]?\d)(?:st|nd|rd|th)?\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"\b([0-3]?\d)(?:st|nd|rd|th)?\s+(?:of\s+)?"
            r"(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|"
            r"aug|august|sep|sept|september|oct|october|nov|november|dec|december)\b",
            text,
            flags=re.IGNORECASE,
        )
    if not match:
        return None
    return _normalise_structured_day(match.group(1))


def _structured_date_text_month(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _normalize_control_message(value)
    if "this month" in normalized:
        today = today_utc_date()
        return f"{today.year:04d}-{today.month:02d}"
    if "next month" in normalized:
        next_month_range = _resolve_next_month_range_from_message(normalized)
        if next_month_range:
            start_date = str(next_month_range.get("startDate") or "")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date):
                return start_date[:7]
    return _normalise_structured_month(value)


def _structured_list_argument(arguments: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, list):
            return [
                str(item).strip()
                for item in value
                if str(item).strip()
            ]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
    return []


def _has_broad_activity_trip_frame(active_trip: dict[str, Any]) -> bool:
    required = _normalise_trip_list(active_trip.get("requiredActivities"))
    activity = _normalise_trip_list(active_trip.get("activityPreferences"))
    environment = _normalise_trip_list(active_trip.get("environmentPreferences"))
    has_activity = bool(required or activity or environment)
    return bool(
        has_activity
        and not active_trip.get("selectedDestination")
        and not active_trip.get("destinationRegion")
    )


def _date_from_structured_month_day(month: str | None, day: int | None) -> str | None:
    if not month or day is None:
        return None
    try:
        year_text, month_text = month.split("-", 1)
        return date(int(year_text), int(month_text), day).isoformat()
    except ValueError:
        return None


def _canonicalise_structured_trip_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    canonical = dict(arguments)
    origin = canonical.get("origin")
    if isinstance(origin, dict):
        canonical.setdefault("origin_text", origin.get("city") or origin.get("text") or origin.get("name"))
        canonical.setdefault("origin_code", origin.get("code") or origin.get("airportCode"))
    destination = canonical.get("destination")
    if isinstance(destination, dict):
        canonical.setdefault(
            "destination_text",
            destination.get("city") or destination.get("text") or destination.get("name"),
        )
        canonical.setdefault("destination_code", destination.get("code") or destination.get("airportCode"))
    raw_destination_candidates = canonical.get("destination_candidates") or canonical.get(
        "destinationCandidates"
    )
    destination_type_text = _normalize_control_message(
        str(canonical.get("destination_type") or canonical.get("destinationType") or "")
    )
    if not (canonical.get("destination_code") or canonical.get("destinationCode")):
        if (
            destination_type_text in {"airport", "city", "exact_destination"}
            and isinstance(raw_destination_candidates, list)
            and len(raw_destination_candidates) == 1
        ):
            candidate_code = _candidate_code_from_value(raw_destination_candidates[0])
            if candidate_code:
                canonical.setdefault("destination_code", candidate_code)
    destination_region = canonical.get("destination_region") or canonical.get("destinationRegion")
    if isinstance(destination_region, dict):
        canonical.setdefault(
            "destination_region",
            destination_region.get("name") or destination_region.get("displayName"),
        )
        canonical.setdefault("destination_text", destination_region.get("name") or destination_region.get("displayName"))
        canonical.setdefault("destination_type", "region")
        canonical.setdefault(
            "destination_candidates",
            destination_region.get("candidateCodes") or destination_region.get("destination_candidates"),
        )
    elif destination_region:
        canonical.setdefault("destination_text", destination_region)
        canonical.setdefault("destination_type", "region")
    date_text = canonical.get("date_text") or canonical.get("dateText")
    if date_text and not canonical.get("month"):
        month = _structured_date_text_month(date_text)
        if month:
            canonical.setdefault("month", month)
    if date_text and not (
        canonical.get("departure_date")
        or canonical.get("departureDate")
        or canonical.get("departure_day")
        or canonical.get("departureDay")
    ):
        relative_departure = resolve_relative_travel_date(date_text)
        if relative_departure:
            canonical.setdefault("departure_date", relative_departure)
    departure_value = canonical.get("departure_date") or canonical.get("departureDate")
    if isinstance(departure_value, dict):
        month = _normalise_structured_month(departure_value)
        if month:
            canonical.setdefault("month", month)
            canonical.setdefault("date_text", f"in {month}")
        day = _normalise_structured_day(departure_value)
        if day is not None:
            canonical.setdefault("departure_day", day)
        canonical.pop("departure_date", None)
        canonical.pop("departureDate", None)
    return_value = canonical.get("return_date") or canonical.get("returnDate")
    if isinstance(return_value, dict):
        day = _normalise_structured_day(return_value)
        if day is not None:
            canonical.setdefault("return_day", day)
        canonical.pop("return_date", None)
        canonical.pop("returnDate", None)
    if canonical.get("trip_duration_nights") is not None:
        canonical.setdefault("tripDurationNights", canonical.get("trip_duration_nights"))
    if canonical.get("passengers") is not None:
        canonical.setdefault("passenger_count", canonical.get("passengers"))
    return canonical


def _broad_trip_missing_fields(active_trip: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    dates = _active_trip_dates(active_trip)
    if not active_trip.get("origin"):
        missing.append("origin")
    if active_trip.get("destinationRegion") and not active_trip.get("selectedDestination"):
        missing.append("destination_airport_choice")
    elif not active_trip.get("selectedDestination"):
        missing.append("destination")
    if not dates.get("departureDate"):
        missing.append("departure_date")
    if active_trip.get("dateText") and not dates.get("departureDate"):
        missing.append("exact_dates")
    return list(dict.fromkeys(missing))


def _store_structured_trip_request(
    db: Session,
    session_id: int,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    arguments = _canonicalise_structured_trip_arguments(arguments)
    active_trip = _active_trip_for_session(session_id, db)
    previous_pending_action = deepcopy(active_trip.get("pendingAction"))
    LOGGER.info(
        "Structured trip merge start: %s",
        {
            "session_id": session_id,
            "arguments": {
                key: arguments.get(key)
                for key in [
                    "intent",
                    "origin_code",
                    "originCode",
                    "origin_text",
                    "originText",
                    "destination_code",
                    "destinationCode",
                    "destination_text",
                    "destinationText",
                    "destination_type",
                    "destinationType",
                    "departure_date",
                    "departureDate",
                    "return_date",
                    "returnDate",
                    "departure_day",
                    "departureDay",
                    "return_day",
                    "returnDay",
                    "month",
                    "nights",
                    "tripDurationNights",
                    "budget_amount",
                    "budgetAmount",
                    "budget",
                    "use_active_trip_state",
                ]
                if key in arguments
            },
            "before": _active_trip_debug_summary(active_trip),
        },
    )
    existing_pending_trip_request = (
        active_trip.get("pendingTripRequest")
        if isinstance(active_trip.get("pendingTripRequest"), dict)
        else {}
    )
    existing_broad_activity_frame = _has_broad_activity_trip_frame(active_trip)
    has_explicit_origin = bool(
        arguments.get("origin_code")
        or arguments.get("originCode")
        or arguments.get("origin_text")
        or arguments.get("originText")
    )
    has_explicit_destination = bool(
        arguments.get("destination_code")
        or arguments.get("destinationCode")
        or arguments.get("destination_text")
        or arguments.get("destinationText")
    )
    has_structured_date_hint = bool(
        arguments.get("departure_date")
        or arguments.get("departureDate")
        or arguments.get("return_date")
        or arguments.get("returnDate")
        or arguments.get("departure_day")
        or arguments.get("departureDay")
        or arguments.get("return_day")
        or arguments.get("returnDay")
        or arguments.get("month")
        or arguments.get("date_text")
        or arguments.get("dateText")
        or arguments.get("nights")
        or arguments.get("tripDurationNights")
    )
    has_structured_budget_hint = bool(
        arguments.get("budget_amount")
        or arguments.get("budgetAmount")
        or arguments.get("budget")
        or arguments.get("maxBudget")
        or arguments.get("max_budget")
    )
    has_structured_passenger_hint = bool(
        arguments.get("passenger_count")
        or arguments.get("passengerCount")
        or arguments.get("passengers")
        or arguments.get("adults")
    )
    incoming_required = _normalise_trip_list(
        _structured_list_argument(arguments, "requiredActivities", "required_activities")
    )
    incoming_activity = _normalise_trip_list(
        _structured_list_argument(arguments, "activityPreferences", "activity_preferences")
    )
    incoming_environment = _normalise_trip_list(
        _structured_list_argument(arguments, "environmentPreferences", "environment_preferences")
    )
    has_structured_activity_hint = bool(
        incoming_required or incoming_activity or incoming_environment
    )
    existing_required = _normalise_trip_list(active_trip.get("requiredActivities"))
    existing_activity = _normalise_trip_list(active_trip.get("activityPreferences"))
    existing_environment = _normalise_trip_list(active_trip.get("environmentPreferences"))
    activity_frame_changed = has_structured_activity_hint and (
        incoming_required != existing_required
        or incoming_activity != existing_activity
        or incoming_environment != existing_environment
    )
    existing_region = (
        active_trip.get("destinationRegion")
        if isinstance(active_trip.get("destinationRegion"), dict)
        else {}
    )
    existing_candidate_codes = {
        str(candidate.get("airportCode") or "").upper()
        for candidate in active_trip.get("destinationCandidates", [])
        if isinstance(candidate, dict)
    }
    explicit_destination_code = str(
        arguments.get("destination_code")
        or arguments.get("destinationCode")
        or ""
    ).upper()
    destination_chooses_existing_region = bool(existing_region) and (
        not explicit_destination_code
        or explicit_destination_code in existing_candidate_codes
        or explicit_destination_code in {
            str(code).upper() for code in existing_region.get("candidateCodes", [])
        }
    )
    use_existing = bool(arguments.get("use_active_trip_state")) or (
        bool(existing_pending_trip_request)
        and not has_explicit_origin
        and (
            (has_explicit_destination and destination_chooses_existing_region)
            or has_structured_date_hint
            or has_structured_budget_hint
            or not (has_explicit_destination or has_structured_date_hint)
        )
    ) or (
        existing_broad_activity_frame
        and not has_explicit_destination
        and (
            has_explicit_origin
            or has_structured_date_hint
            or has_structured_budget_hint
            or has_structured_passenger_hint
            or not has_structured_activity_hint
        )
    )

    origin = _airport_payload_from_structured_slot(
        code=arguments.get("origin_code") or arguments.get("originCode"),
        text=arguments.get("origin_text") or arguments.get("originText"),
        source="explicit_origin",
    )
    if origin is None and (use_existing or has_structured_activity_hint):
        origin = _active_trip_origin(active_trip)
    if origin is None:
        origin = _default_origin_for_session(session_id, db)
    if origin is not None:
        active_trip["origin"] = deepcopy(origin)

    destination_code_arg = arguments.get("destination_code") or arguments.get("destinationCode")
    destination_text_arg = arguments.get("destination_text") or arguments.get("destinationText")
    destination = None
    if use_existing and isinstance(active_trip.get("destinationCandidates"), list):
        normalized_destination_text = _normalize_control_message(str(destination_text_arg or ""))
        normalized_destination_code = str(destination_code_arg or "").upper()
        for candidate in active_trip.get("destinationCandidates", []):
            if not isinstance(candidate, dict):
                continue
            candidate_code = str(candidate.get("airportCode") or "").upper()
            candidate_city = _normalize_control_message(str(candidate.get("city") or ""))
            if (
                normalized_destination_code
                and normalized_destination_code == candidate_code
            ) or (
                normalized_destination_text
                and normalized_destination_text == candidate_city
            ):
                destination = {
                    **candidate,
                    "source": "selected_region_candidate",
                    "recommendationId": candidate_code or candidate.get("recommendationId"),
                }
                break
    if destination is None:
        destination = _airport_payload_from_structured_slot(
            code=destination_code_arg,
            text=destination_text_arg,
            source="explicit_destination",
            require_text_match_for_code=True,
        )
    LOGGER.info(
        "Structured destination resolver: %s",
        {
            "session_id": session_id,
            "input": {
                "destinationText": destination_text_arg,
                "destinationCode": destination_code_arg,
                "activeRegion": (active_trip.get("destinationRegion") or {}).get("name")
                if isinstance(active_trip.get("destinationRegion"), dict)
                else None,
                "activeCandidates": [
                    candidate.get("airportCode")
                    for candidate in active_trip.get("destinationCandidates", [])
                    if isinstance(candidate, dict) and candidate.get("airportCode")
                ],
            },
            "output": {
                "airportCode": (destination or {}).get("airportCode"),
                "city": (destination or {}).get("city"),
                "source": (destination or {}).get("source"),
            },
        },
    )
    raw_destination_candidates = arguments.get("destination_candidates") or arguments.get("destinationCandidates")
    candidates = _destination_candidates_from_structured_slots(
        raw_destination_candidates,
    )
    candidate_codes = [
        str(candidate.get("airportCode") or "").upper()
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("airportCode")
    ]
    region = _destination_region_from_structured_slots(
        destination_text_arg,
        arguments.get("destination_type") or arguments.get("destinationType"),
        arguments.get("destination_region") or arguments.get("destinationRegion"),
        candidate_codes,
    )
    structured_missing_fields = {
        str(field)
        for field in (
            arguments.get("missing_fields")
            or arguments.get("missingFields")
            or []
        )
        if field is not None
    }
    if (
        region is None
        and destination is None
        and destination_text_arg
        and (
            destination_code_arg
            or "destination_airport_choice" in structured_missing_fields
        )
    ):
        region = _destination_region_from_structured_slots(
            destination_text_arg,
            "region",
            destination_text_arg,
            candidate_codes,
        )
    if region:
        candidates = _filter_region_candidate_outliers(candidates, region)
        region["candidateCodes"] = [
            str(candidate.get("airportCode") or "").upper()
            for candidate in candidates
            if candidate.get("airportCode")
        ]
    if region:
        if use_existing and active_trip.get("selectedDestination") and not (
            arguments.get("destination_code") or arguments.get("destinationCode")
        ):
            active_trip["destinationCandidates"] = candidates or active_trip.get("destinationCandidates") or []
        else:
            active_trip["destinationRegion"] = region
            active_trip["destinationType"] = "region"
            active_trip["destinationCandidates"] = candidates
            active_trip["selectedDestination"] = None
    elif destination:
        active_trip["selectedDestination"] = destination
        active_trip["destinationRegion"] = None
        active_trip["destinationType"] = "airport"
        active_trip["destinationCandidates"] = []

    active_origin = _active_trip_origin(active_trip)
    active_destination = _active_trip_destination(active_trip)
    if (
        active_origin
        and active_destination
        and str(active_origin.get("airportCode") or "").upper()
        == str(active_destination.get("airportCode") or "").upper()
    ):
        default_origin = _default_origin_for_session(session_id, db)
        if (
            default_origin
            and str(default_origin.get("airportCode") or "").upper()
            != str(active_destination.get("airportCode") or "").upper()
        ):
            active_trip["origin"] = deepcopy(default_origin)
            origin = deepcopy(default_origin)

    incoming_month = _normalise_structured_month(arguments.get("month"))
    broad_activity_request = (
        activity_frame_changed
        and not region
        and not destination
    )
    if broad_activity_request:
        preserved_cart = active_trip.get("cart")
        if not has_explicit_origin and not _active_trip_origin(active_trip):
            default_origin = _default_origin_for_session(session_id, db)
            if default_origin:
                active_trip["origin"] = deepcopy(default_origin)
        active_trip["selectedDestination"] = None
        active_trip["destinationRegion"] = None
        active_trip["destinationType"] = None
        active_trip["destinationCandidates"] = []
        active_trip["tripFrame"] = "broad_activity_trip"
        if incoming_month is None and not (
            arguments.get("date_text") or arguments.get("dateText")
        ):
            active_trip["month"] = None
            active_trip["dateText"] = None
        _clear_active_trip_route_dependent_state(active_trip)
        if preserved_cart:
            active_trip["cart"] = preserved_cart
        _invalidate_dependent_state(
            db,
            session_id,
            reason="new_broad_activity_trip_request",
            clear_flights=True,
            clear_hotels=True,
            clear_destinations=True,
        )
    if has_structured_activity_hint:
        active_trip["requiredActivities"] = _title_trip_list(incoming_required)
        active_trip["activityPreferences"] = _title_trip_list(incoming_activity)
        active_trip["environmentPreferences"] = _title_trip_list(incoming_environment)
    if _has_broad_activity_trip_frame(active_trip):
        active_trip["tripFrame"] = "broad_activity_trip"

    new_explicit_trip_frame = (
        not use_existing
        and (
            has_explicit_origin
            or has_explicit_destination
            or incoming_month is not None
            or has_structured_activity_hint
        )
    )
    if new_explicit_trip_frame:
        _clear_active_trip_route_dependent_state(active_trip)
        if incoming_month is not None and not (
            arguments.get("departure_date")
            or arguments.get("departureDate")
            or arguments.get("return_date")
            or arguments.get("returnDate")
            or arguments.get("departure_day")
            or arguments.get("departureDay")
            or arguments.get("return_day")
            or arguments.get("returnDay")
        ):
            active_trip["dates"] = {
                "departureDate": None,
                "returnDate": None,
                "source": "new_month_only_trip_frame",
            }
            if not _has_broad_activity_trip_frame(active_trip):
                active_trip["tripDurationNights"] = None
                active_trip["tripDurationDays"] = None
        _invalidate_dependent_state(
            db,
            session_id,
            reason="new_structured_trip_frame",
            clear_flights=True,
            clear_hotels=True,
            clear_destinations=False,
        )

    month = incoming_month
    if month is None and use_existing:
        month = _normalise_structured_month(active_trip.get("month"))
    date_text = arguments.get("date_text") or arguments.get("dateText")
    departure_day = _normalise_structured_day(
        arguments.get("departure_day") or arguments.get("departureDay")
    ) or _normalise_structured_day_from_text(date_text)
    return_day = _normalise_structured_day(
        arguments.get("return_day") or arguments.get("returnDay")
    )
    departure = _normalise_structured_date(
        arguments.get("departure_date") or arguments.get("departureDate")
    ) or _date_from_structured_month_day(month, departure_day)
    if departure is None and departure_day is not None:
        base_month = None
        if month:
            try:
                base_month = date.fromisoformat(f"{month}-01")
            except ValueError:
                base_month = None
        departure = _resolve_day_only_followup(
            f"the {departure_day}th",
            base_date=base_month,
        )
    return_date = _normalise_structured_date(
        arguments.get("return_date") or arguments.get("returnDate")
    ) or _date_from_structured_month_day(month, return_day)
    if use_existing and month:
        try:
            month_year, month_number = month.split("-", 1)
            active_year = int(month_year)
            active_month = int(month_number)
        except (AttributeError, TypeError, ValueError):
            active_year = None
            active_month = None
        if departure and active_year and active_month:
            parsed_departure = date.fromisoformat(departure)
            if parsed_departure.year != active_year or parsed_departure.month != active_month:
                departure = date(active_year, active_month, parsed_departure.day).isoformat()
        if return_date and active_year and active_month:
            parsed_return = date.fromisoformat(return_date)
            if parsed_return.year != active_year or parsed_return.month != active_month:
                return_date = date(active_year, active_month, parsed_return.day).isoformat()
    nights = arguments.get("nights") or arguments.get("tripDurationNights")
    try:
        nights_int = int(nights) if nights is not None else None
    except (TypeError, ValueError):
        nights_int = None
    inherited_nights_int: int | None = None
    if use_existing and nights_int is None:
        try:
            inherited_nights_int = int(active_trip.get("tripDurationNights"))
        except (TypeError, ValueError):
            inherited_nights_int = None
    effective_nights_int = nights_int or inherited_nights_int
    if departure and effective_nights_int and not return_date:
        return_date = (date.fromisoformat(departure) + timedelta(days=effective_nights_int)).isoformat()
    if departure and return_date and nights_int is None:
        try:
            calculated_nights = (
                date.fromisoformat(return_date) - date.fromisoformat(departure)
            ).days
            if calculated_nights > 0:
                nights_int = calculated_nights
                effective_nights_int = calculated_nights
        except ValueError:
            pass
    LOGGER.info(
        "Structured date normaliser: %s",
        {
            "session_id": session_id,
            "input": {
                "month": month,
                "departureDate": arguments.get("departure_date") or arguments.get("departureDate"),
                "returnDate": arguments.get("return_date") or arguments.get("returnDate"),
                "departureDay": arguments.get("departure_day") or arguments.get("departureDay"),
                "returnDay": arguments.get("return_day") or arguments.get("returnDay"),
                "nights": arguments.get("nights") or arguments.get("tripDurationNights"),
            },
            "output": {
                "departureDate": departure,
                "returnDate": return_date,
                "nights": nights_int or effective_nights_int,
            },
            "activeMonth": active_trip.get("month"),
        },
    )
    dates = _active_trip_dates(active_trip)
    should_inherit_dates = (
        use_existing
        and not has_structured_date_hint
        and not departure
        and not return_date
        and not has_explicit_origin
        and not has_explicit_destination
    )
    active_trip["dates"] = {
        "departureDate": departure or (dates.get("departureDate") if should_inherit_dates else None),
        "returnDate": return_date or (dates.get("returnDate") if should_inherit_dates else None),
        "source": "llama_structured_trip_request",
    }
    if nights_int:
        active_trip["tripDurationNights"] = nights_int
    budget_value = (
        arguments.get("budget_amount")
        or arguments.get("budgetAmount")
        or arguments.get("budget")
        or arguments.get("maxBudget")
        or arguments.get("max_budget")
    )
    try:
        budget_float = float(budget_value) if budget_value is not None else None
    except (TypeError, ValueError):
        budget_float = None
    if budget_float is not None:
        active_trip["budget"] = budget_float
        budget_currency = (
            arguments.get("budget_currency")
            or arguments.get("budgetCurrency")
            or arguments.get("currency")
            or "GBP"
        )
        active_trip["budgetCurrency"] = str(budget_currency).upper()
    if isinstance(date_text, str) and date_text.strip():
        active_trip["dateText"] = date_text.strip()
    if month:
        active_trip["month"] = month
    passenger_value = (
        arguments.get("passenger_count")
        or arguments.get("passengerCount")
        or arguments.get("passengers")
        or arguments.get("adults")
    )
    try:
        passenger_count = int(passenger_value) if passenger_value is not None else None
    except (TypeError, ValueError):
        passenger_count = None
    if passenger_count and passenger_count > 0:
        active_trip["passengerCount"] = passenger_count

    pending_trip_request = (
        active_trip.get("pendingTripRequest")
        if isinstance(active_trip.get("pendingTripRequest"), dict)
        else {}
    )
    intent_name = str(
        arguments.get("intent")
        or (pending_trip_request or {}).get("intent")
        or "flight_search"
    )
    requested_product = (
        arguments.get("requested_product")
        or arguments.get("requestedProduct")
        or arguments.get("product")
    )
    requested_product_text = str(requested_product or "").strip().lower()
    if requested_product_text in {"package", "packages", "flight_hotel", "flight+hotel", "trip", "holiday"}:
        requested_products = ["packages"]
        intent_name = "package_search"
    elif requested_product_text in {"hotel", "hotels", "stay", "stays", "accommodation"}:
        requested_products = ["hotels"]
        intent_name = "hotel_search"
    elif requested_product_text in {"flight", "flights", "airfare"}:
        requested_products = ["flights"]
        if intent_name not in {"destination_discovery", "discover_destinations"}:
            intent_name = "flight_search"
    elif intent_name == "package_search":
        requested_products = ["packages"]
    elif intent_name == "hotel_search":
        requested_products = ["hotels"]
    else:
        requested_products = ["flights"]
    if _has_broad_activity_trip_frame(active_trip):
        active_trip["tripFrame"] = "broad_activity_trip"
        if intent_name in {"flight_search", "unknown", ""}:
            intent_name = "destination_discovery"
    active_trip["requestedProducts"] = requested_products
    active_trip["pendingTripRequest"] = {
        "intent": intent_name,
        "tripFrame": active_trip.get("tripFrame"),
        "origin": active_trip.get("origin"),
        "destinationRegion": active_trip.get("destinationRegion"),
        "destinationCandidates": active_trip.get("destinationCandidates") or [],
        "selectedDestination": active_trip.get("selectedDestination"),
        "dates": active_trip.get("dates"),
        "dateText": active_trip.get("dateText"),
        "month": active_trip.get("month"),
        "tripDurationNights": active_trip.get("tripDurationNights"),
        "budget": active_trip.get("budget"),
        "budgetCurrency": active_trip.get("budgetCurrency"),
        "missingFields": [],
    }
    active_trip["pendingTripRequest"]["missingFields"] = _broad_trip_missing_fields(active_trip)
    if "packages" in requested_products:
        route_or_date_changed = any(
            [
                has_explicit_origin,
                has_explicit_destination,
                has_structured_date_hint,
            ]
        )
        pending_payload = _package_pending_action_payload(active_trip)
        if pending_payload is not None:
            active_trip["pendingAction"] = pending_payload
            active_trip["status"] = "awaiting_package_build_confirmation"
        elif (
            isinstance(previous_pending_action, dict)
            and previous_pending_action.get("type") == "build_package"
            and not route_or_date_changed
            and (has_structured_budget_hint or has_structured_passenger_hint)
        ):
            active_trip["pendingAction"] = previous_pending_action
            _update_pending_package_action_from_active_trip(active_trip)
    if _has_broad_activity_trip_frame(active_trip):
        _store_pending_destination_suggestion_action(active_trip)
    if active_trip.get("latestVisibleResultType") != "destination_discovery_pending":
        active_trip["latestVisibleResultType"] = "trip_clarification"
    if active_trip.get("status") not in {
        "awaiting_package_build_confirmation",
        "destination_discovery_pending",
    }:
        active_trip["status"] = "trip_clarification"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    saved_trip = save_active_trip(db, session_id, active_trip)
    LOGGER.info(
        "Structured trip merge complete: %s",
        {
            "session_id": session_id,
            "after": _active_trip_debug_summary(saved_trip),
        },
    )
    return saved_trip


def _structured_trip_reply(active_trip: dict[str, Any]) -> str:
    origin = _active_trip_origin(active_trip)
    region = active_trip.get("destinationRegion")
    candidates = (
        active_trip.get("destinationCandidates")
        if isinstance(active_trip.get("destinationCandidates"), list)
        else []
    )
    dates = _active_trip_dates(active_trip)
    origin_label = _origin_message(origin) if origin else "your chosen origin"
    required_activities = [
        str(value).replace("_", " ")
        for value in active_trip.get("requiredActivities", [])
        if str(value).strip()
    ]
    products = active_trip.get("requestedProducts", [])
    product = (
        "package"
        if "packages" in products
        else "hotel"
        if "hotels" in products
        else "flights"
    )
    selected_destination = _active_trip_destination(active_trip)
    if isinstance(region, dict):
        region_name = str(
            region.get("displayName")
            or region.get("name")
            or "that region"
        )
        month_label = _month_display_label(active_trip.get("month")) or "that month"
        candidate_label = _candidate_city_list(candidates)
        if not candidates:
            if active_trip.get("month") and not dates.get("departureDate"):
                return (
                    f"I can help build a {region_name} {product} from {origin_label}. "
                    f"{month_label} is a broad date range, and I need a city or airport "
                    f"in {region_name} before searching. Please tell me your exact "
                    "departure and return dates, or how many nights you want to stay, "
                    f"and which {region_name} city or airport you prefer."
                )
            return (
                f"I can search {product} from {origin_label} to {region_name}. "
                f"Please tell me which city or airport in {region_name} you prefer."
            )
        if active_trip.get("month") and not dates.get("departureDate"):
            return (
                f"I can help build a {region_name} {product} from {origin_label}. "
                f"{region_name} has several main airport choices, including {candidate_label}. "
                f"{month_label} is a broad date range, so please tell me your exact "
                "departure and return dates, or how many nights you want to stay."
            )
        if dates.get("departureDate"):
            date_label = _display_date_label(dates.get("departureDate"))
            return_label = _display_date_label(dates.get("returnDate"))
            date_phrase = (
                f"from {date_label} to {return_label}"
                if return_label
                else f"departing {date_label}"
            )
            return (
                f"I have your trip as {origin_label} to {region_name} {date_phrase}. "
                f"{region_name} has several airports, including {candidate_label}. "
                "Would you like me to search those airports and compare the best options?"
            )
        return (
            f"I can search {product} from {origin_label} to {region_name}. "
            f"{region_name} has several main airports, including {candidate_label}. "
            "Please provide your departure and return dates, or tell me which "
            f"{region_name} airport you prefer."
        )
    if selected_destination and origin:
        destination_label = _origin_message(selected_destination)
        date_label = _display_date_label(dates.get("departureDate"))
        return_label = _display_date_label(dates.get("returnDate"))
        if date_label and return_label:
            if "packages" in active_trip.get("requestedProducts", []):
                return (
                    f"I have your destination as {destination_label} from {origin_label}, "
                    f"travelling from {date_label} to {return_label}. "
                    "Would you like me to build a flight and hotel package for those dates?"
                )
            if "hotels" in active_trip.get("requestedProducts", []):
                return (
                    f"I have your hotel search as {destination_label}, "
                    f"checking in {date_label} and checking out {return_label}. "
                    "Would you like me to search stays for those dates?"
                )
            return (
                f"I have your flight search as {origin_label} to {destination_label}, "
                f"travelling from {date_label} to {return_label}. "
                "Would you like me to search those flights?"
            )
        return (
            f"I have your destination as {destination_label} from {origin_label}. "
            "Please provide your exact departure and return dates."
        )
    if required_activities:
        activity_label = " and ".join(required_activities)
        nights = active_trip.get("tripDurationNights")
        nights_phrase = (
            f" for a {nights}-night trip"
            if isinstance(nights, int) and nights > 0
            else ""
        )
        month_label = _month_display_label(active_trip.get("month"))
        if month_label and not dates.get("departureDate"):
            return (
                f"I can search {activity_label} destinations{nights_phrase} "
                f"from {origin_label} in {month_label}. Please tell me the exact "
                "departure date, or I can suggest suitable destinations first."
            )
        if dates.get("departureDate"):
            date_label = _display_date_label(dates.get("departureDate"))
            return_label = _display_date_label(dates.get("returnDate"))
            date_phrase = (
                f"from {date_label} to {return_label}"
                if return_label
                else f"departing {date_label}"
            )
            return (
                f"I can search {activity_label} destinations{nights_phrase} "
                f"from {origin_label}, {date_phrase}."
            )
        origin_phrase = (
            f" I'll use {origin_label} as your departure airport."
            if origin
            else " Which airport would you like to fly from?"
        )
        return (
            f"I can help you find {activity_label} destinations{nights_phrase}. "
            f"When do you want to depart?{origin_phrase}"
        )
    if not origin:
        return "Where are you travelling from?"
    return "Please choose a destination and exact travel dates."


def _package_pending_action_payload(active_trip: dict[str, Any]) -> dict[str, Any] | None:
    origin = _active_trip_origin(active_trip) or {}
    destination = _active_trip_destination(active_trip) or {}
    dates = _active_trip_dates(active_trip)
    origin_code = origin.get("airportCode")
    destination_code = destination.get("airportCode")
    departure = dates.get("departureDate")
    return_date = dates.get("returnDate")
    if not (origin_code and destination_code and departure and return_date):
        return None
    passenger_count = int(active_trip.get("passengerCount") or 1)
    payload = {
        "type": "build_package",
        "origin": origin_code,
        "originCity": origin.get("city"),
        "destination": destination_code,
        "destinationCity": destination.get("city"),
        "destinationCountry": destination.get("country"),
        "departureDate": departure,
        "returnDate": return_date,
        "tripDurationNights": active_trip.get("tripDurationNights"),
        "nights": active_trip.get("tripDurationNights"),
        "passengerCount": passenger_count,
        "requestedProduct": "package",
    }
    if active_trip.get("budget") is not None:
        payload["budget"] = active_trip.get("budget")
        payload["budgetCurrency"] = active_trip.get("budgetCurrency") or "GBP"
    return payload


def _destination_discovery_request_from_active_trip(
    active_trip: dict[str, Any],
    *,
    require_dates: bool = True,
) -> DestinationDiscoveryRequest | None:
    if not _has_broad_activity_trip_frame(active_trip):
        return None
    origin = _active_trip_origin(active_trip)
    dates = _active_trip_dates(active_trip)
    departure = dates.get("departureDate")
    return_date = dates.get("returnDate")
    required = _normalise_trip_list(active_trip.get("requiredActivities"))
    activity = _normalise_trip_list(active_trip.get("activityPreferences")) or required
    if not (origin and origin.get("airportCode") and required):
        return None
    if require_dates and not (departure and return_date):
        return None
    payload = {
        "origin": {
            "city": origin.get("city"),
            "code": origin.get("airportCode"),
            "airportName": origin.get("airportName"),
            "latitude": origin.get("latitude"),
            "longitude": origin.get("longitude"),
        },
        "requiredActivities": _title_trip_list(required),
        "activityPreferences": _title_trip_list(activity),
        "environmentPreferences": _title_trip_list(
            _normalise_trip_list(active_trip.get("environmentPreferences"))
        ),
        "regionPreferences": list(active_trip.get("regionPreferences") or []),
        "excludedRegions": list(active_trip.get("excludedRegions") or []),
        "tripDurationNights": active_trip.get("tripDurationNights"),
        "dates": {},
    }
    if departure:
        payload["dates"]["departure"] = departure
    if return_date:
        payload["dates"]["return"] = return_date
    return DestinationDiscoveryRequest.model_validate(payload)


def _destination_suggestion_pending_action_payload(
    active_trip: dict[str, Any],
) -> dict[str, Any] | None:
    discovery_request = _destination_discovery_request_from_active_trip(
        active_trip,
        require_dates=False,
    )
    if discovery_request is None:
        return None
    dates = _active_trip_dates(active_trip)
    return {
        "type": "suggest_destinations",
        "tripFrame": "broad_activity_trip",
        "origin": (_active_trip_origin(active_trip) or {}).get("airportCode"),
        "requiredActivities": list(active_trip.get("requiredActivities") or []),
        "activityPreferences": list(active_trip.get("activityPreferences") or []),
        "tripDurationNights": active_trip.get("tripDurationNights"),
        "month": active_trip.get("month"),
        "dateText": active_trip.get("dateText"),
        "departureDate": dates.get("departureDate"),
        "returnDate": dates.get("returnDate"),
        "request": discovery_request.model_dump(by_alias=True, mode="json"),
    }


def _pending_destination_suggestion_action(
    active_trip: dict[str, Any],
) -> dict[str, Any] | None:
    pending_action = active_trip.get("pendingAction")
    if isinstance(pending_action, dict) and pending_action.get("type") == "suggest_destinations":
        return pending_action
    return None


def _store_pending_destination_suggestion_action(
    active_trip: dict[str, Any],
) -> None:
    pending_payload = _destination_suggestion_pending_action_payload(active_trip)
    if pending_payload is None:
        return
    active_trip["pendingAction"] = pending_payload
    active_trip["latestVisibleResultType"] = "destination_discovery_pending"
    if active_trip.get("status") != "awaiting_package_build_confirmation":
        active_trip["status"] = "destination_discovery_pending"


def _execute_pending_destination_suggestion_confirmation(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    pending_action = _pending_destination_suggestion_action(active_trip)
    if pending_action is None:
        return None
    discovery_request = _destination_discovery_request_from_active_trip(
        active_trip,
        require_dates=False,
    )
    if discovery_request is None:
        request_payload = pending_action.get("request")
        if isinstance(request_payload, dict):
            try:
                discovery_request = DestinationDiscoveryRequest.model_validate(request_payload)
            except Exception:
                discovery_request = None
    if discovery_request is None:
        active_trip["pendingAction"] = None
        active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
        save_active_trip(db, session.id, active_trip)
        return None
    active_trip["pendingAction"] = None
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    active_trip = save_active_trip(db, session.id, active_trip)
    dates = _destination_dates(discovery_request)
    if not dates.get("departure"):
        payload = discovery_request.model_dump(by_alias=True, mode="json")
        payload["dates"] = {}
        payload["missingFields"] = []
        discovery_request = DestinationDiscoveryRequest.model_validate(payload)
        active_trip = _sync_active_trip_from_destination_request(
            db,
            session.id,
            discovery_request,
            status="discovering_without_dates",
        )
        active_trip["dates"] = {
            "departureDate": None,
            "returnDate": None,
            "source": "date_later",
        }
        active_trip["pendingAction"] = None
        active_trip["status"] = "discovering_without_dates"
        active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
        save_active_trip(db, session.id, active_trip)
        provider_response = discover_destinations(discovery_request)
        save_latest_destination_discovery(
            db,
            session.id,
            discovery_request.model_dump(by_alias=True, mode="json"),
            provider_response.recommendations,
        )
        _set_last_tool_used(session.id, "discover_destinations", db)
        results = _destination_results_for_chat(provider_response)
        if provider_response.recommendations:
            top = provider_response.recommendations[0]
            reply = (
                "I can show destination ideas first. Flight prices and availability "
                "will be checked once you choose a date. "
                f"The strongest match is {top.city}, {top.country} "
                f"({top.airportCode}) with a score of {top.score}/100."
            )
        else:
            reply = "I can show destination ideas first, but I could not find a strong match yet."
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "general_enquiry"),
            reply,
            results=results,
        )
    return _execute_destination_discovery_tool(
        TravelToolCall(
            tool="discover_destinations",
            arguments=discovery_request.model_dump(by_alias=True, mode="json"),
        ),
        db,
        session,
        request,
    )


def _update_pending_package_action_from_active_trip(active_trip: dict[str, Any]) -> None:
    pending_action = active_trip.get("pendingAction")
    if not isinstance(pending_action, dict) or pending_action.get("type") != "build_package":
        return
    refreshed = _package_pending_action_payload(active_trip)
    if refreshed is None:
        if active_trip.get("budget") is not None:
            pending_action["budget"] = active_trip.get("budget")
            pending_action["budgetCurrency"] = active_trip.get("budgetCurrency") or "GBP"
        if active_trip.get("passengerCount"):
            pending_action["passengerCount"] = int(active_trip.get("passengerCount") or 1)
        return
    pending_action.update(refreshed)
    active_trip["pendingAction"] = pending_action


def _apply_pending_package_action_to_active_trip(
    active_trip: dict[str, Any],
    pending_action: dict[str, Any],
) -> dict[str, Any]:
    origin = _airport_payload_from_code(str(pending_action.get("origin") or ""), "pending_package_action")
    destination = _airport_payload_from_code(
        str(pending_action.get("destination") or ""),
        "pending_package_action",
    )
    if origin:
        active_trip["origin"] = origin
    if destination:
        active_trip["selectedDestination"] = destination
        active_trip["destinationRegion"] = None
        active_trip["destinationType"] = "airport"
        active_trip["destinationCandidates"] = []
    departure = _normalise_structured_date(pending_action.get("departureDate"))
    return_date = _normalise_structured_date(pending_action.get("returnDate"))
    if departure or return_date:
        active_trip["dates"] = {
            "departureDate": departure,
            "returnDate": return_date,
            "source": "pending_package_action",
        }
    try:
        nights = int(
            pending_action.get("tripDurationNights")
            or pending_action.get("nights")
            or active_trip.get("tripDurationNights")
            or 0
        )
    except (TypeError, ValueError):
        nights = 0
    if nights > 0:
        active_trip["tripDurationNights"] = nights
    try:
        passenger_count = int(pending_action.get("passengerCount") or active_trip.get("passengerCount") or 1)
    except (TypeError, ValueError):
        passenger_count = 1
    active_trip["passengerCount"] = max(1, passenger_count)
    if pending_action.get("budget") is not None:
        active_trip["budget"] = pending_action.get("budget")
        active_trip["budgetCurrency"] = pending_action.get("budgetCurrency") or "GBP"
    active_trip["requestedProducts"] = ["packages"]
    return active_trip


def _store_pending_package_build_action(
    db: Session,
    session_id: int,
    active_trip: dict[str, Any],
) -> dict[str, Any]:
    pending_payload = _package_pending_action_payload(active_trip)
    if pending_payload is None:
        return save_active_trip(db, session_id, active_trip)
    active_trip["pendingAction"] = pending_payload
    active_trip["status"] = "awaiting_package_build_confirmation"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    saved_trip = save_active_trip(db, session_id, active_trip)
    LOGGER.info(
        "Stored pending package build action: %s",
        {
            "session_id": session_id,
            "activeTrip": _active_trip_debug_summary(saved_trip),
        },
    )
    return saved_trip


def _pending_package_build_action(active_trip: dict[str, Any]) -> dict[str, Any] | None:
    pending_action = active_trip.get("pendingAction")
    if isinstance(pending_action, dict) and pending_action.get("type") == "build_package":
        return pending_action
    return None


def _execute_pending_package_build_confirmation(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    pending_action = _pending_package_build_action(active_trip)
    if pending_action is None:
        return None
    active_trip = _apply_pending_package_action_to_active_trip(active_trip, pending_action)
    package_request, error = build_package_request_from_active_trip(active_trip)
    if package_request is None:
        active_trip["pendingAction"] = None
        active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
        save_active_trip(db, session.id, active_trip)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_search"),
            error or "Please choose a destination and exact travel dates before I build a package.",
        )

    LOGGER.info(
        "Executing pending package build action: %s",
        {
            "session_id": session.id,
            "pendingAction": pending_action,
            "activeTrip": _active_trip_debug_summary(active_trip),
        },
    )
    active_trip["pendingAction"] = None
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    return _execute_package_request_if_possible(db, session, request, force=True)


def _flight_search_for_candidate(
    origin: dict[str, Any],
    destination: dict[str, Any],
    departure: str,
    return_date: str | None,
    passenger_count: int = 1,
) -> FlightSearchRequest:
    payload = {
        "searchType": "flight",
        "passengerCount": passenger_count,
        "itinerary": {
            "tripType": "round_trip" if return_date else "one_way",
            "origin": {"city": origin.get("city"), "code": origin.get("airportCode")},
            "destination": {
                "city": destination.get("city"),
                "code": destination.get("airportCode"),
            },
            "dates": {"departure": departure},
        },
        "preferences": {"cabinClass": "economy", "maxStops": None},
    }
    if return_date:
        payload["itinerary"]["dates"]["return"] = return_date
    return FlightSearchRequest.model_validate(payload)


def _store_pending_alternative_origin_search(
    db: Session,
    session_id: int,
    active_trip: dict[str, Any],
) -> dict[str, Any]:
    active_trip["pendingAlternativeOriginSearch"] = {
        "pending_action": "search_alternative_origins",
        "original_origin": (_active_trip_origin(active_trip) or {}).get("airportCode"),
        "alternative_origin_candidates": DEFAULT_UK_ALTERNATIVE_ORIGIN_CODES,
        "destination_region": (active_trip.get("destinationRegion") or {}).get("name"),
        "destination_candidates": [
            candidate.get("airportCode")
            for candidate in active_trip.get("destinationCandidates", [])
            if isinstance(candidate, dict) and candidate.get("airportCode")
        ],
        "dates": _active_trip_dates(active_trip),
    }
    active_trip["latestVisibleResultType"] = "alternative_origin_prompt"
    active_trip["status"] = "awaiting_alternative_origin_confirmation"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    return save_active_trip(db, session_id, active_trip)


def _execute_broad_destination_candidate_search(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    active_trip: dict[str, Any],
) -> ChatResponse | None:
    origin = _active_trip_origin(active_trip)
    dates = _active_trip_dates(active_trip)
    candidates = active_trip.get("destinationCandidates")
    if not origin or not isinstance(candidates, list) or not dates.get("departureDate"):
        return None
    all_flights: list[dict[str, Any]] = []
    for destination in candidates:
        if not isinstance(destination, dict) or not destination.get("airportCode"):
            continue
        flight_request = _flight_search_for_candidate(
            origin,
            destination,
            str(dates["departureDate"]),
            dates.get("returnDate"),
            int(active_trip.get("passengerCount") or 1),
        )
        provider_response = _search_flights_with_fallback(flight_request)
        for flight in _normalised_flight_dicts(provider_response):
            all_flights.append(flight)

    if all_flights:
        all_flights.sort(key=_flight_price)
        region_name = _destination_region_display_name(active_trip)
        reply = (
            f"I searched {region_name} airport options from {_origin_message(origin)} "
            "and found flight choices across the selected candidate airports."
        )
        active_trip["latestVisibleResultType"] = "flights"
        save_active_trip(db, session.id, active_trip)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            reply,
            _flight_results_for_chat(all_flights, CHAT_FLIGHT_CARD_LIMIT),
        )

    active_trip = _store_pending_alternative_origin_search(db, session.id, active_trip)
    region_name = _destination_region_display_name(active_trip)
    reply = (
        f"I could not find suitable flights from {_origin_message(origin)} to {region_name} "
        "for those dates. Would you like me to also check alternative UK departure "
        "airports such as London Heathrow, London Gatwick or Manchester?"
    )
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "flight_search"),
        reply,
    )


def _execute_prepare_trip_request_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    active_trip = _store_structured_trip_request(db, session.id, tool_call.arguments)
    if _has_broad_activity_trip_frame(active_trip):
        discovery_request = _destination_discovery_request_from_active_trip(active_trip)
        if discovery_request is not None:
            if _pending_destination_suggestion_action(active_trip) is not None:
                active_trip["pendingAction"] = None
                active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
                save_active_trip(db, session.id, active_trip)
            return _execute_destination_discovery_tool(
                TravelToolCall(
                    tool="discover_destinations",
                    arguments=discovery_request.model_dump(by_alias=True, mode="json"),
                ),
                db,
                session,
                request,
                preserve_request_origin=True,
            )
        pending_discovery_request = _destination_discovery_request_from_active_trip(
            active_trip,
            require_dates=False,
        )
        if (
            pending_discovery_request is not None
            and pending_discovery_request.trip_duration_nights
            and not _destination_departure_date(pending_discovery_request)
        ):
            question_text = _structured_trip_reply(active_trip)
            return _store_pending_destination_discovery_and_ask_date(
                db,
                session,
                request,
                pending_discovery_request,
                question_text=question_text,
            )
    if tool_call.arguments.get("search_candidates"):
        search_response = _execute_broad_destination_candidate_search(
            db,
            session,
            request,
            active_trip,
        )
        if search_response is not None:
            return search_response
    if "hotels" in active_trip.get("requestedProducts", []):
        hotel_request, _ = build_hotel_request_from_active_trip(active_trip)
        if hotel_request is not None:
            return _execute_hotel_search_request(db, session, request, hotel_request)
        pending_hotel = _pending_hotel_from_structured_trip(active_trip, tool_call.arguments)
        active_trip["selectedDestination"] = None
        active_trip = _save_pending_hotel_request(db, session.id, active_trip, pending_hotel)
        _set_last_tool_used(session.id, "hotel_clarification", db)
        question_text = _hotel_question(pending_hotel)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "hotel_search"),
            _clarification_reply_text(
                db,
                session.id,
                active_trip,
                _pending_hotel_missing_fields(pending_hotel),
                question_text,
            ),
        )
    if "flights" in active_trip.get("requestedProducts", []):
        flight_request, _ = build_flight_request_from_active_trip(active_trip)
        if flight_request is not None:
            flight_request = validate_flight_search_against_active_trip(
                flight_request,
                active_trip,
                request.message,
            )
            return _execute_flight_search_request(db, session, request, flight_request)
    if "packages" in active_trip.get("requestedProducts", []):
        package_request, _ = build_package_request_from_active_trip(active_trip)
        if package_request is not None:
            active_trip = _store_pending_package_build_action(db, session.id, active_trip)
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(
            request.message,
            "package_search"
            if "packages" in active_trip.get("requestedProducts", [])
            else "hotel_search"
            if "hotels" in active_trip.get("requestedProducts", [])
            else "flight_search",
        ),
        _structured_trip_reply(active_trip),
    )


def _execute_search_alternative_origins_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    active_trip = _active_trip_for_session(session.id, db)
    pending = active_trip.get("pendingAlternativeOriginSearch")
    if not isinstance(pending, dict):
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            "Please choose a route and travel dates before I check alternative airports.",
        )
    origin_codes = tool_call.arguments.get("origin_codes") or tool_call.arguments.get("originCodes")
    if not isinstance(origin_codes, list) or not origin_codes:
        origin_text = tool_call.arguments.get("origin_text") or tool_call.arguments.get("originText")
        if origin_text:
            origin = _airport_payload_from_structured_slot(text=origin_text, source="alternative_origin")
            origin_codes = [origin["airportCode"]] if origin else []
        else:
            origin_codes = (
                pending.get("alternative_origin_candidates")
                or pending.get("alternativeOriginCandidates")
                or DEFAULT_UK_ALTERNATIVE_ORIGIN_CODES
            )
    destination_codes = (
        pending.get("destination_candidates")
        or pending.get("destinationCandidates")
        or []
    )
    dates = pending.get("dates") if isinstance(pending.get("dates"), dict) else {}
    flights: list[dict[str, Any]] = []
    for origin_code in origin_codes:
        origin = _airport_payload_from_code(str(origin_code), "alternative_origin")
        if not origin:
            continue
        for destination_code in destination_codes:
            destination = _airport_payload_from_code(str(destination_code), "broad_destination_candidate")
            if not destination:
                continue
            flight_request = _flight_search_for_candidate(
                origin,
                destination,
                str(dates.get("departureDate")),
                dates.get("returnDate"),
                int(active_trip.get("passengerCount") or 1),
            )
            provider_response = _search_flights_with_fallback(flight_request)
            flights.extend(_normalised_flight_dicts(provider_response))
    if not flights:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            "I could not find suitable alternative-airport flights for those dates.",
        )
    flights.sort(key=_flight_price)
    active_trip["pendingAlternativeOriginSearch"] = None
    active_trip["latestVisibleResultType"] = "flights"
    save_active_trip(db, session.id, active_trip)
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "flight_search"),
        "I checked alternative UK departure airports and found flight options.",
        _flight_results_for_chat(flights, CHAT_FLIGHT_CARD_LIMIT),
    )


def _coerce_nested_structured_tool_call(
    tool_call: TravelToolCall | None,
) -> TravelToolCall | None:
    if tool_call is None:
        return None
    LOGGER.info(
        "Llama structured tool output: %s",
        {
            "tool": tool_call.tool,
            "argumentKeys": sorted(str(key) for key in tool_call.arguments.keys()),
        },
    )
    nested_prepare = tool_call.arguments.get("prepare_trip_request")
    if isinstance(nested_prepare, dict):
        return TravelToolCall(
            tool="prepare_trip_request",
            arguments=nested_prepare,
            confidence=tool_call.confidence,
            explanation=tool_call.explanation,
        )
    if tool_call.tool == "search_trip_options":
        destination_text = (
            tool_call.arguments.get("destinationText")
            or tool_call.arguments.get("destination_text")
        )
        requested_products = tool_call.arguments.get("requestedProducts") or tool_call.arguments.get(
            "requested_products"
        )
        region = _destination_region_from_structured_slots(destination_text, "region")
        if region:
            products = requested_products if isinstance(requested_products, list) else []
            intent_name = (
                "package_search"
                if any(str(product).lower() in {"hotels", "hotel", "packages", "package"} for product in products)
                else "flight_search"
            )
            return TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": intent_name,
                    "destination_text": region["name"],
                    "destination_type": "region",
                    "destination_candidates": region["candidateCodes"],
                    "use_active_trip_state": False,
                },
                confidence=tool_call.confidence,
                explanation=tool_call.explanation,
            )
    if tool_call.tool == "search_flights" and not isinstance(tool_call.arguments.get("search_request"), dict):
        origin_text = tool_call.arguments.get("origin") or tool_call.arguments.get("origin_text")
        destination_text = tool_call.arguments.get("destination") or tool_call.arguments.get("destination_text")
        departure_value = tool_call.arguments.get("departure_date") or tool_call.arguments.get("departureDate")
        month_value = tool_call.arguments.get("month")
        if month_value is None and isinstance(departure_value, str) and re.fullmatch(r"\d{4}-\d{2}", departure_value):
            month_value = departure_value
            departure_value = None
        region = _destination_region_from_structured_slots(destination_text, "region")
        if region or month_value:
            return TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": "package_search",
                    "origin_text": origin_text,
                    "destination_text": region["name"] if region else destination_text,
                    "destination_type": "region" if region else None,
                    "destination_candidates": region["candidateCodes"] if region else [],
                    "departure_date": departure_value,
                    "return_date": tool_call.arguments.get("return_date")
                    or tool_call.arguments.get("returnDate"),
                    "month": month_value,
                    "date_text": f"in {month_value}" if month_value else None,
                },
                confidence=tool_call.confidence,
                explanation=tool_call.explanation,
            )
    nested_search_request = tool_call.arguments.get("search_request")
    if (
        tool_call.tool == "search_flights"
        and isinstance(nested_search_request, dict)
        and (
            nested_search_request.get("destination_type")
            or nested_search_request.get("destinationType")
            or nested_search_request.get("destination_candidates")
            or nested_search_request.get("destinationCandidates")
            or nested_search_request.get("month")
            or nested_search_request.get("date_text")
            or nested_search_request.get("dateText")
        )
        and not nested_search_request.get("searchType")
    ):
        if not nested_search_request.get("intent") and (
            nested_search_request.get("month")
            or nested_search_request.get("date_text")
            or nested_search_request.get("dateText")
        ):
            nested_search_request = {**nested_search_request, "intent": "package_search"}
        return TravelToolCall(
            tool="prepare_trip_request",
            arguments=nested_search_request,
            confidence=tool_call.confidence,
            explanation=tool_call.explanation,
        )
    nested_alternatives = tool_call.arguments.get("search_alternative_origins")
    if isinstance(nested_alternatives, dict):
        return TravelToolCall(
            tool="search_alternative_origins",
            arguments=nested_alternatives,
            confidence=tool_call.confidence,
            explanation=tool_call.explanation,
        )
    return tool_call


def _coerce_tool_call_for_active_trip(
    tool_call: TravelToolCall | None,
    active_trip: dict[str, Any],
) -> TravelToolCall | None:
    if tool_call is None:
        return None
    pending_trip = (
        active_trip.get("pendingTripRequest")
        if isinstance(active_trip.get("pendingTripRequest"), dict)
        else {}
    )
    if not pending_trip:
        return tool_call
    pending_intent = pending_trip.get("intent") or "package_search"
    if tool_call.tool == "search_hotels":
        dates = tool_call.arguments.get("dates") if isinstance(tool_call.arguments.get("dates"), dict) else {}
        destination_payload = (
            tool_call.arguments.get("destination")
            if isinstance(tool_call.arguments.get("destination"), dict)
            else {}
        )
        destination_text = (
            tool_call.arguments.get("destination_city")
            or tool_call.arguments.get("destinationCity")
            or destination_payload.get("city")
        )
        if destination_text or dates:
            return TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": pending_intent,
                    "use_active_trip_state": True,
                    "destination_text": destination_text,
                    "departure_date": (
                        dates.get("check_in")
                        or dates.get("checkInDate")
                        or tool_call.arguments.get("check_in_date")
                        or tool_call.arguments.get("checkInDate")
                    ),
                    "return_date": (
                        dates.get("check_out")
                        or dates.get("checkOutDate")
                        or tool_call.arguments.get("check_out_date")
                        or tool_call.arguments.get("checkOutDate")
                    ),
                },
                confidence=tool_call.confidence,
                explanation=tool_call.explanation,
            )
    if tool_call.tool == "search_flights":
        raw_search_request = tool_call.arguments.get("search_request")
        if isinstance(raw_search_request, dict):
            origin = raw_search_request.get("origin") if isinstance(raw_search_request.get("origin"), dict) else {}
            destination = (
                raw_search_request.get("destination")
                if isinstance(raw_search_request.get("destination"), dict)
                else {}
            )
            dates = raw_search_request.get("dates") if isinstance(raw_search_request.get("dates"), dict) else {}
            return TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": pending_intent,
                    "use_active_trip_state": True,
                    "origin_code": origin.get("code") or origin.get("airportCode"),
                    "origin_text": origin.get("city") or origin.get("airportName"),
                    "destination_code": destination.get("code") or destination.get("airportCode"),
                    "destination_text": destination.get("city") or destination.get("airportName"),
                    "departure_date": raw_search_request.get("departureDate") or dates.get("departure"),
                    "return_date": raw_search_request.get("returnDate") or dates.get("return"),
                },
                confidence=tool_call.confidence,
                explanation=tool_call.explanation,
            )
        budget_value = (
            tool_call.arguments.get("budget_amount")
            or tool_call.arguments.get("budgetAmount")
            or tool_call.arguments.get("budget")
            or tool_call.arguments.get("budget_max")
            or tool_call.arguments.get("budgetMax")
            or tool_call.arguments.get("budget_min")
            or tool_call.arguments.get("budgetMin")
        )
        if budget_value is not None:
            return TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": pending_intent,
                    "use_active_trip_state": True,
                    "budget_amount": budget_value,
                    "budget_currency": tool_call.arguments.get("currency") or "GBP",
                },
                confidence=tool_call.confidence,
                explanation=tool_call.explanation,
            )
    return tool_call


def _route_from_package_message(message: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    normalized = _normalize_control_message(message)
    route_match = re.search(
        r"\bfrom\s+(.+?)\s+to\s+(.+?)(?=\s+(?:today|tomorrow|depart|leav|on|for|return|and\s+return)\b|$)",
        normalized,
    )
    if route_match is None and " to " in f" {normalized} ":
        tokens = normalized.split()
        if len(tokens) <= 5:
            route_match = re.search(r"^(.+?)\s+to\s+(.+)$", normalized)
    if route_match is None:
        return None

    origin = _airport_from_package_location_text(route_match.group(1))
    destination = _airport_from_package_location_text(route_match.group(2))
    if not origin or not destination:
        return None
    return (
        _package_airport_payload(origin, "explicit_route"),
        _package_airport_payload(destination, "explicit_route"),
    )


def _origin_from_package_message(message: str) -> dict[str, Any] | None:
    normalized = _normalize_control_message(message)
    if re.search(r"\bfrom\b.+\bto\b", normalized):
        return None
    match = re.search(
        r"\bfrom\s+(.+?)(?=\s+(?:today|tomorrow|depart|leav|on|for|return|and)\b|$)",
        normalized,
    )
    if match is None:
        return None
    origin = _airport_from_package_location_text(match.group(1))
    return _package_airport_payload(origin, "explicit_origin") if origin else None


def _destination_from_package_message(message: str) -> dict[str, Any] | None:
    route = _route_from_package_message(message)
    if route is not None:
        return route[1]
    destination = _resolve_explicit_destination_from_message(message)
    if destination is None:
        normalized = _normalize_control_message(message)
        match = re.search(
            r"\bto\s+(.+?)(?=\s+(?:today|tomorrow|depart|leav|on|for|return|with|and)\b|$)",
            normalized,
        )
        if match is not None:
            destination = _airport_from_package_location_text(match.group(1))
    return _package_airport_payload(destination, "explicit_destination") if destination else None


def _after_days_from_message(message: str) -> int | None:
    normalized = _normalize_control_message(message)
    match = re.search(
        r"\bafter\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+days?\b",
        normalized,
    )
    if not match:
        return None
    days = _parse_small_number(match.group(1))
    return days if days and days > 0 else None


def _new_pending_package_request(active_trip: dict[str, Any]) -> dict[str, Any]:
    origin = _active_trip_origin(active_trip)
    destination = _active_trip_destination(active_trip)
    dates = _active_trip_dates(active_trip)
    return {
        "intent": "package_search",
        "origin": deepcopy(origin) if origin else None,
        "destination": deepcopy(destination) if destination else None,
        "departureDate": dates.get("departureDate"),
        "returnDate": dates.get("returnDate"),
        "tripDurationDays": active_trip.get("tripDurationDays"),
        "tripDurationNights": active_trip.get("tripDurationNights"),
        "maxBudget": active_trip.get("budget"),
        "passengerCount": active_trip.get("passengerCount") or 1,
        "rooms": active_trip.get("rooms") or 1,
        "missingFields": [],
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }


def _pending_package_request(active_trip: dict[str, Any]) -> dict[str, Any] | None:
    pending = active_trip.get("pendingPackageRequest")
    return pending if isinstance(pending, dict) else None


def _pending_package_missing_fields(pending: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not isinstance(pending.get("origin"), dict) or not pending["origin"].get("airportCode"):
        missing.append("origin")
    if not isinstance(pending.get("destination"), dict) or not pending["destination"].get("airportCode"):
        missing.append("destination")
    if not pending.get("departureDate"):
        missing.append("departureDate")
    if not pending.get("returnDate"):
        missing.append("returnDate")
    return missing


def _pending_package_question(pending: dict[str, Any]) -> str:
    missing = _pending_package_missing_fields(pending)
    origin = pending.get("origin") if isinstance(pending.get("origin"), dict) else {}
    destination = (
        pending.get("destination") if isinstance(pending.get("destination"), dict) else {}
    )
    departure = _display_date_label(pending.get("departureDate"))

    if missing == ["departureDate", "returnDate"]:
        return "When do you want to depart?"
    if missing == ["returnDate"]:
        if origin.get("airportCode") and destination.get("airportCode"):
            route_text = (
                f" {origin.get('city') or origin.get('airportCode')} "
                f"({origin.get('airportCode')}) to "
                f"{destination.get('city') or destination.get('airportCode')} "
                f"({destination.get('airportCode')})"
            )
        else:
            route_text = ""
        depart_text = f", departing {_display_date_label(pending.get('departureDate'))}" if departure else ""
        return f"Great - I'll use{route_text}{depart_text}. How many nights will you stay, or what date do you want to return?"
        route = ""
        if origin.get("airportCode") and destination.get("airportCode"):
            route = (
                f" for {origin.get('city') or origin.get('airportCode')} "
                f"({origin.get('airportCode')}) to "
                f"{destination.get('city') or destination.get('airportCode')} "
                f"({destination.get('airportCode')})"
            )
        depart_text = f", departing {_display_date_label(pending.get('departureDate'))}" if departure else ""
        return f"Great — I’ll use{route}{depart_text}. How many nights will you stay, or what date do you want to return?"
    if "origin" in missing and "returnDate" in missing and pending.get("departureDate"):
        return "Where are you flying from, and how many nights will you stay?"
    if "origin" in missing and "departureDate" in missing:
        return "Where are you flying from, and when do you want to depart?"
    if "origin" in missing:
        return "Where are you flying from?"
    if "destination" in missing:
        return "Where would you like to go?"
    if "departureDate" in missing:
        return "When do you want to depart?"
    return "How many nights will you stay, or what date do you want to return?"


def _merge_package_clarification(
    active_trip: dict[str, Any],
    message: str,
    *,
    start_new: bool,
) -> dict[str, Any] | None:
    pending = _new_pending_package_request(active_trip) if start_new else _pending_package_request(active_trip)
    if pending is None:
        return None

    route = _route_from_package_message(message)
    if route is not None:
        pending["origin"], pending["destination"] = route
    else:
        origin = _origin_from_package_message(message)
        destination = _destination_from_package_message(message)
        if origin is not None:
            pending["origin"] = origin
        if destination is not None:
            pending["destination"] = destination

    departure = resolve_relative_travel_date(message, today=today_utc_date())
    if departure is None:
        departure = resolve_future_date(message, today=today_utc_date())
    if departure is not None:
        normalized = _normalize_control_message(message)
        if "return" in normalized and pending.get("departureDate"):
            pending["returnDate"] = departure
        else:
            pending["departureDate"] = departure

    duration_days, duration_nights = _extract_trip_duration_components(message)
    nights = duration_nights
    return_after = _extract_return_after_days(message) or _after_days_from_message(message)
    if return_after:
        nights = return_after
        duration_days = duration_days or return_after + 1
    if nights:
        if duration_days:
            pending["tripDurationDays"] = duration_days
        pending["tripDurationNights"] = nights
        if pending.get("departureDate"):
            try:
                pending["returnDate"] = (
                    date.fromisoformat(str(pending["departureDate"])) + timedelta(days=nights)
                ).isoformat()
            except ValueError:
                pass

    budget = _extract_budget_amount(message)
    if budget is not None:
        pending["maxBudget"] = budget

    pending["missingFields"] = _pending_package_missing_fields(pending)
    pending["updatedAt"] = datetime.now(timezone.utc).isoformat()
    return pending


def _apply_pending_package_to_active_trip(
    active_trip: dict[str, Any],
    pending: dict[str, Any],
) -> None:
    if isinstance(pending.get("origin"), dict):
        active_trip["origin"] = deepcopy(pending["origin"])
    if isinstance(pending.get("destination"), dict):
        destination = deepcopy(pending["destination"])
        destination["source"] = destination.get("source") or "package_clarification"
        active_trip["selectedDestination"] = destination
    active_trip["dates"] = {
        "departureDate": pending.get("departureDate"),
        "returnDate": pending.get("returnDate"),
        "source": "package_clarification",
    }
    if pending.get("tripDurationDays"):
        active_trip["tripDurationDays"] = int(pending["tripDurationDays"])
    if pending.get("tripDurationNights"):
        active_trip["tripDurationNights"] = int(pending["tripDurationNights"])
    if pending.get("maxBudget") is not None:
        active_trip["budget"] = float(pending["maxBudget"])
    active_trip["passengerCount"] = int(pending.get("passengerCount") or active_trip.get("passengerCount") or 1)
    active_trip["rooms"] = int(pending.get("rooms") or active_trip.get("rooms") or 1)
    active_trip["requestedProducts"] = ["flights", "hotels"]
    active_trip["latestVisibleResultType"] = "package_clarification"
    active_trip["status"] = "package_clarification"


def _save_pending_package_request(
    db: Session,
    session_id: int,
    active_trip: dict[str, Any],
    pending: dict[str, Any],
) -> dict[str, Any]:
    _apply_pending_package_to_active_trip(active_trip, pending)
    active_trip["pendingPackageRequest"] = pending
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    return save_active_trip(db, session_id, active_trip)


def _looks_like_cheaper_followup(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "something cheaper",
            "any cheaper",
            "is there cheaper",
            "show me cheaper",
            "less expensive",
            "lower price",
            "under my budget",
            "cheaper",
        ]
    )


def _latest_visible_result_type(
    session_id: int,
    db: Session,
) -> str | None:
    active_trip = load_active_trip(db, session_id)
    visible = active_trip.get("latestVisibleResultType")
    if visible in {
        "flights",
        "flight_selected",
        "hotels",
        "hotel_selected",
        "packages",
        "cart",
        "checkout",
        "tickets",
    }:
        if visible == "flight_selected":
            return "flights"
        if visible == "hotel_selected":
            return "hotels"
        return str(visible)

    active_context = _active_context_type(session_id, db)
    if active_context == "flight" and _latest_flight_memory(session_id, db) is not None:
        return "flights"
    if active_context == "hotel" and _latest_hotel_memory(session_id, db) is not None:
        return "hotels"
    if active_context == "package" and _latest_package_options(active_trip):
        return "packages"
    if session_id in PENDING_BOOKINGS:
        return "packages"

    has_flights = _latest_flight_memory(session_id, db) is not None
    has_hotels = _latest_hotel_memory(session_id, db) is not None
    if has_flights and not has_hotels:
        return "flights"
    if has_hotels and not has_flights:
        return "hotels"
    return None


def _priced_options(
    options: list[dict[str, Any]],
    price_getter: Callable[[dict[str, Any]], float | None],
) -> list[tuple[int, dict[str, Any], float]]:
    priced: list[tuple[int, dict[str, Any], float]] = []
    for index, option in enumerate(options, start=1):
        price = price_getter(option)
        if price is not None:
            priced.append((index, option, price))
    priced.sort(key=lambda item: item[2])
    return priced


def _execute_cheaper_flight_followup(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    memory = _latest_flight_memory(session.id, db)
    if memory is None:
        return _flight_context_response(db, session, request)

    flights = memory.get("flights") if isinstance(memory.get("flights"), list) else []
    priced = _priced_options(flights, _flight_price)
    if not priced:
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "I do not have enough price information to compare these flight options.",
            _flight_results_for_chat(flights, CHAT_FLIGHT_CARD_LIMIT),
        )

    current = _current_displayed_flight(session.id, db)
    current_price = _flight_price(current[1]) if current else None
    if current_price is None:
        selected = priced[:CHAT_FLIGHT_CARD_LIMIT]
        first_option = selected[0][0]
        _set_current_flight_option(session.id, first_option, db)
        _set_last_tool_used(session.id, "cheaper_flight_followup", db)
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "Here are the cheapest flight options I currently have for this route and date.",
            _flight_results_for_chat([flight for _index, flight, _price in selected]),
        )

    cheaper = [
        item for item in priced if item[2] < current_price and item[0] != current[0]
    ]
    if not cheaper:
        _set_last_tool_used(session.id, "cheaper_flight_followup", db)
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            (
                "This is the cheapest flight option I currently have for this route "
                "and date. I can also check different dates or nearby airports."
            ),
            _flight_results_for_chat([current[1]]),
        )

    selected = cheaper[:CHAT_FLIGHT_CARD_LIMIT]
    _set_current_flight_option(session.id, selected[0][0], db)
    _set_last_tool_used(session.id, "cheaper_flight_followup", db)
    return _tool_response(
        db,
        session,
        request,
        _flight_follow_up_intent(session.id, request.message, db),
        f"I found {len(cheaper)} cheaper flight option(s) than the current one.",
        _flight_results_for_chat([flight for _index, flight, _price in selected]),
    )


def _execute_cheaper_hotel_followup(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    memory = _latest_hotel_memory(session.id, db)
    if memory is None:
        return _hotel_context_response(db, session, request)

    hotels = memory.get("hotels") if isinstance(memory.get("hotels"), list) else []
    priced = _priced_options(hotels, _hotel_price)
    if not priced:
        return _tool_response(
            db,
            session,
            request,
            _hotel_follow_up_intent(session.id, request.message, db),
            "I do not have enough price information to compare these hotel options.",
            _hotel_results_for_chat(hotels, CHAT_HOTEL_CARD_LIMIT),
        )

    current = _current_displayed_hotel(session.id, db)
    current_price = _hotel_price(current[1]) if current else None
    if current_price is None:
        selected = priced[:CHAT_HOTEL_CARD_LIMIT]
        _set_current_hotel_option(session.id, selected[0][0], db)
        reply = "Here are the cheapest hotel options I currently have for those dates."
    else:
        selected = [
            item for item in priced if item[2] < current_price and item[0] != current[0]
        ][:CHAT_HOTEL_CARD_LIMIT]
        if not selected:
            _set_last_tool_used(session.id, "cheaper_hotel_followup", db)
            return _tool_response(
                db,
                session,
                request,
                _hotel_follow_up_intent(session.id, request.message, db),
                "This is the cheapest hotel option I currently have for those dates.",
                _hotel_results_for_chat([current[1]]),
            )
        _set_current_hotel_option(session.id, selected[0][0], db)
        reply = f"I found cheaper hotel option(s) than the current one."

    _set_last_tool_used(session.id, "cheaper_hotel_followup", db)
    return _tool_response(
        db,
        session,
        request,
        _hotel_follow_up_intent(session.id, request.message, db),
        reply,
        _hotel_results_for_chat([hotel for _index, hotel, _price in selected]),
    )


def _execute_cheaper_package_followup(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    active_trip = _active_trip_for_session(session.id, db)
    packages = _latest_package_options(active_trip)
    if not packages:
        if session.id in PENDING_BOOKINGS:
            return _handle_package_option_message(db, session, request)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_view"),
            "I do not have package options open yet.",
        )

    current_package = (
        active_trip.get("selectedPackageOption")
        if isinstance(active_trip.get("selectedPackageOption"), dict)
        else None
    )
    current_price = (
        float(current_package.get("totalPrice"))
        if isinstance(current_package, dict)
        and current_package.get("totalPrice") is not None
        else None
    )
    priced = _priced_options(
        packages,
        lambda package: (
            float(package.get("totalPrice"))
            if package.get("totalPrice") is not None
            else None
        ),
    )
    selected = (
        [item for item in priced if current_price is not None and item[2] < current_price]
        or priced
    )[:5]
    if not selected:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_view"),
            "I do not have enough price information to compare these package options.",
            _package_results_for_chat(packages),
        )

    active_trip["latestVisibleResultType"] = "packages"
    save_active_trip(db, session.id, active_trip)
    _set_last_tool_used(session.id, "cheaper_package_followup", db)
    reply = "Here are the cheapest package options I currently have."
    if current_price is not None and selected[0][2] >= current_price:
        reply = "This is the cheapest package option I currently have."
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "package_option_view"),
        reply,
        _package_results_for_chat([package for _index, package, _price in selected]),
    )


def _package_price(package: dict[str, Any]) -> float | None:
    try:
        if package.get("totalPrice") is None:
            return None
        return float(package["totalPrice"])
    except (TypeError, ValueError):
        return None






def _package_flight_summary_text(package: dict[str, Any]) -> str:
    flight = package.get("flight") if isinstance(package.get("flight"), dict) else {}
    airline = str(flight.get("airline") or "the selected airline")
    origin = str(flight.get("origin") or "").upper()
    destination = str(flight.get("destination") or "").upper()
    route = f"{origin} to {destination}" if origin and destination else "the selected route"
    return f"{airline} flight from {route}"


def _package_hotel_summary_text(package: dict[str, Any]) -> str:
    hotel = package.get("hotel") if isinstance(package.get("hotel"), dict) else {}
    hotel_name = str(hotel.get("hotelName") or hotel.get("hotel_name") or "the selected hotel")
    nights = int(package.get("nights") or hotel.get("nights") or 1)
    check_in = str(hotel.get("checkInDate") or hotel.get("check_in_date") or "")
    check_out = str(hotel.get("checkOutDate") or hotel.get("check_out_date") or "")
    date_label = ""
    if check_in and check_out:
        date_label = (
            f" from {_display_date_label(check_in) or check_in} "
            f"to {_display_date_label(check_out) or check_out}"
        )
    return f"{nights}-night stay at {hotel_name}{date_label}"


def _package_summary_reply(prefix: str, package: dict[str, Any]) -> str:
    return (
        f"{prefix} {_package_option_label(package)} at "
        f"{_package_money_label(package)}. It includes a "
        f"{_package_flight_summary_text(package)} and a "
        f"{_package_hotel_summary_text(package)}."
    )


VISIBLE_RESULT_ACTIONS = {
    "select_cheapest",
    "select_option",
    "compare_results",
    "show_option",
    "add_selected_to_cart",
    "add_option_to_cart",
    "ask_result_question",
}


def _current_package_option(active_trip: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("currentPackageOption", "selectedPackageOption"):
        value = active_trip.get(key)
        if isinstance(value, dict):
            return value
    return None


def _set_current_package_option(
    db: Session,
    session_id: int,
    active_trip: dict[str, Any],
    package: dict[str, Any],
) -> None:
    active_trip["currentPackageOption"] = deepcopy(package)
    active_trip["latestVisibleResultType"] = "packages"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session_id, active_trip)


def _visible_result_type_from_context(
    session_id: int,
    db: Session,
    requested_type: str | None,
) -> str | None:
    requested = str(requested_type or "unknown").lower()
    if requested in {"package", "flight", "hotel"}:
        return requested
    visible = _latest_visible_result_type(session_id, db)
    if visible == "packages":
        return "package"
    if visible == "flights":
        return "flight"
    if visible == "hotels":
        return "hotel"
    return None


def _option_number_from_visible_args(arguments: dict[str, Any]) -> int | None:
    value = arguments.get("option_number") or arguments.get("optionNumber")
    try:
        option_number = int(value)
    except (TypeError, ValueError):
        return None
    return option_number if option_number >= 1 else None


def _execute_visible_package_results_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    active_trip = _active_trip_for_session(session.id, db)
    packages = _latest_package_options(active_trip)
    if not packages:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_view"),
            "I do not have package options open yet.",
        )

    action = str(tool_call.arguments.get("action") or "ask_result_question").lower()
    if action not in VISIBLE_RESULT_ACTIONS:
        action = "ask_result_question"
    option_number = _option_number_from_visible_args(tool_call.arguments)
    priced = _priced_options(packages, _package_price)
    sorted_packages = [package for _index, package, _price in priced] or packages

    if action == "compare_results":
        active_trip["latestVisibleResultType"] = "packages"
        save_active_trip(db, session.id, active_trip)
        _set_last_tool_used(session.id, "handle_visible_results", db)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_view"),
            "Here are your package options sorted by total price.",
            _package_results_for_chat(sorted_packages[:5]),
        )

    if action in {"show_option", "select_option", "add_option_to_cart"}:
        if option_number is None:
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "package_option_selection"),
                "Which package option would you like?",
                _package_results_for_chat(packages),
            )
        package = _find_package_option(packages, option_number)
        if package is None:
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "package_option_view"),
                f"I could not find package option {option_number}. Please choose a number between 1 and {len(packages)}.",
                _package_results_for_chat(packages),
            )
        if action == "add_option_to_cart":
            return _execute_package_selection_if_possible(
                db,
                session,
                request,
                option_number=option_number,
                force=True,
            ) or _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "package_option_selection"),
                "I could not add that package to your cart.",
                _package_results_for_chat(packages),
            )
        _set_current_package_option(db, session.id, active_trip, package)
        _set_last_tool_used(session.id, "handle_visible_results", db)
        prefix = "You selected" if action == "select_option" else "Here is"
        suffix = " Would you like me to add it to your cart?" if action == "select_option" else ""
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_view"),
            _package_summary_reply(prefix, package) + suffix,
            _package_results_for_chat([package]),
        )

    if action == "add_selected_to_cart":
        package = _current_package_option(active_trip)
        if package is None:
            if len(sorted_packages) == 1:
                package = sorted_packages[0]
            else:
                return _tool_response(
                    db,
                    session,
                    request,
                    _build_control_intent(request.message, "package_option_selection"),
                    "Which package option would you like to add to your cart?",
                    _package_results_for_chat(packages),
                )
        package_option_number = (
            package.get("optionNumber")
            or package.get("option_number")
            or option_number
        )
        if package_option_number is None:
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "package_option_selection"),
                "Which package option would you like to add to your cart?",
                _package_results_for_chat(packages),
            )
        return _execute_package_selection_if_possible(
            db,
            session,
            request,
            option_number=int(package_option_number),
            force=True,
        )

    cheapest = sorted_packages[0]
    _set_current_package_option(db, session.id, active_trip, cheapest)
    _set_last_tool_used(session.id, "handle_visible_results", db)
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "package_option_view"),
        _package_summary_reply("The cheapest option is", cheapest),
        _package_results_for_chat([cheapest]),
    )


def _execute_visible_results_tool(
    tool_call: TravelToolCall,
    db: Session,
    user: User,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    result_type = _visible_result_type_from_context(
        session.id,
        db,
        tool_call.arguments.get("result_type") or tool_call.arguments.get("resultType"),
    )
    if result_type is None:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "general_enquiry"),
            "Which results do you mean - flights, hotels, or packages?",
        )

    action = str(tool_call.arguments.get("action") or "ask_result_question").lower()
    option_number = _option_number_from_visible_args(tool_call.arguments)

    if result_type == "package":
        return _execute_visible_package_results_tool(tool_call, db, session, request)

    if result_type == "flight":
        visible = _latest_visible_result_type(session.id, db)
        if visible != "flights":
            return _flight_context_response(db, session, request)
        if action in {"select_cheapest", "ask_result_question", "compare_results"}:
            return _execute_rank_flights_tool(
                TravelToolCall(tool="rank_flights", arguments={"ranking": "cheapest", "limit": 1 if action != "compare_results" else 5}),
                db,
                session,
                request,
            )
        if action == "show_option":
            return _execute_show_flight_option_tool(
                TravelToolCall(tool="show_flight_option", arguments={"option_number": option_number}),
                db,
                session,
                request,
            )
        if action in {"select_option", "add_option_to_cart"}:
            response = _execute_select_flight_tool(
                TravelToolCall(tool="select_flight", arguments={"selection": "option_number", "option_number": option_number}),
                db,
                session,
                request,
            )
            if action == "add_option_to_cart":
                cart_response = _execute_selected_flight_cart_if_possible(db, session, request)
                return cart_response or response
            return response
        if action == "add_selected_to_cart":
            cart_response = _execute_selected_flight_cart_if_possible(db, session, request)
            if cart_response is not None:
                return cart_response
            return _execute_select_flight_tool(
                TravelToolCall(tool="select_flight", arguments={"selection": "current_displayed"}),
                db,
                session,
                request,
            )

    if result_type == "hotel":
        visible = _latest_visible_result_type(session.id, db)
        if visible != "hotels":
            return _hotel_context_response(db, session, request)
        if action in {"select_cheapest", "ask_result_question", "compare_results"}:
            return _execute_rank_hotels_tool(
                TravelToolCall(tool="rank_hotels", arguments={"ranking": "cheapest"}),
                db,
                session,
                request,
            )
        if action == "show_option":
            return _execute_show_hotel_option_tool(
                TravelToolCall(tool="show_hotel_option", arguments={"option_number": option_number}),
                db,
                session,
                request,
            )
        if action in {"select_option", "add_option_to_cart"}:
            response = _execute_select_hotel_tool(
                TravelToolCall(tool="select_hotel", arguments={"selection": "option_number", "option_number": option_number}),
                db,
                session,
                request,
            )
            if action == "add_option_to_cart":
                cart_response = _execute_selected_hotel_cart_if_possible(db, session, request)
                return cart_response or response
            return response
        if action == "add_selected_to_cart":
            cart_response = _execute_selected_hotel_cart_if_possible(db, session, request)
            if cart_response is not None:
                return cart_response
            return _execute_select_hotel_tool(
                TravelToolCall(tool="select_hotel", arguments={"selection": "current_displayed"}),
                db,
                session,
                request,
            )

    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "general_enquiry"),
        "I can help with the visible flight, hotel, or package results.",
    )


def _looks_like_package_results_followup(message: str) -> bool:
    normalized = _normalize_control_message(message)
    if not normalized:
        return False
    if any(
        phrase in normalized
        for phrase in [
            "cheapest package",
            "lowest price package",
            "least expensive package",
            "something cheaper",
            "any cheaper",
            "show me cheaper",
            "less expensive",
            "lower price",
            "which is the cheapest package",
            "which is the cheapest",
            "which package is cheapest",
            "which one is cheapest",
            "what is the cheapest",
            "which is lowest",
            "which one is the best price",
            "show me the cheapest",
            "cheapest one",
            "cheapest",
            "lowest",
            "best price",
            "which package is best",
            "best package",
            "compare the packages",
            "compare packages",
            "show package",
            "view package",
            "package option",
            "add package",
            "select package",
        ]
    ):
        return True
    return (
        _package_option_number_from_message(message) is not None
        and (
            _is_show_option_message(message)
            or _looks_like_package_selection(message)
            or "package" in normalized
        )
    )


def _execute_package_results_followup_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    packages = _latest_package_options(active_trip)
    if not packages:
        return None
    has_package_context = (
        active_trip.get("latestVisibleResultType") == "packages"
        or _latest_visible_result_type(session.id, db) == "packages"
        or bool(packages)
    )
    if not has_package_context:
        return None
    if not _looks_like_package_results_followup(request.message):
        return None

    normalized = _normalize_control_message(request.message)
    option_number = _package_option_number_from_message(request.message)

    if _looks_like_package_selection(request.message) and option_number is not None:
        return _execute_package_selection_if_possible(
            db,
            session,
            request,
            option_number=option_number,
            force=True,
        )

    priced = _priced_options(packages, _package_price)
    sorted_packages = [package for _index, package, _price in priced] or packages

    if "compare" in normalized:
        active_trip["latestVisibleResultType"] = "packages"
        save_active_trip(db, session.id, active_trip)
        _set_last_tool_used(session.id, "compare_package_results", db)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_view"),
            "Here are your package options sorted by total price.",
            _package_results_for_chat(sorted_packages[:5]),
        )

    if option_number is not None:
        package = _find_package_option(packages, option_number)
        if package is None:
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "package_option_view"),
                f"I could not find package option {option_number}. Please choose a number between 1 and {len(packages)}.",
                _package_results_for_chat(packages),
            )
        active_trip["latestVisibleResultType"] = "packages"
        save_active_trip(db, session.id, active_trip)
        _set_last_tool_used(session.id, "show_package_option", db)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_view"),
            _package_summary_reply("Here is", package),
            _package_results_for_chat([package]),
        )

    cheapest = sorted_packages[0]
    active_trip["latestVisibleResultType"] = "packages"
    save_active_trip(db, session.id, active_trip)
    _set_last_tool_used(session.id, "cheapest_package_result", db)
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "package_option_view"),
        _package_summary_reply("The cheapest package is", cheapest),
        _package_results_for_chat([cheapest]),
    )


def _execute_contextual_cheaper_followup_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    if not _looks_like_cheaper_followup(request.message):
        return None

    visible_type = _latest_visible_result_type(session.id, db)
    if visible_type == "flights":
        return _execute_cheaper_flight_followup(db, session, request)
    if visible_type == "hotels":
        return _execute_cheaper_hotel_followup(db, session, request)
    if visible_type == "packages":
        return _execute_cheaper_package_followup(db, session, request)

    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "general_enquiry"),
        "Do you want cheaper flights, hotels, or packages?",
    )


def _looks_like_cheapest_result_question(message: str) -> bool:
    normalized = _normalize_control_message(message)
    if not any(phrase in normalized for phrase in ["cheapest", "lowest price", "least expensive"]):
        return False
    return any(
        phrase in normalized
        for phrase in [
            "cheapest hotel",
            "hotel is cheapest",
            "which hotel is cheapest",
            "which is the cheapest hotel",
            "what is the cheapest one",
            "which one is cheapest",
            "which is cheapest",
            "cheapest one",
            "lowest price hotel",
        ]
    )




def _current_or_selected_flight_price(session_id: int, db: Session) -> tuple[float | None, str | None]:
    active_trip = _active_trip_for_session(session_id, db)
    selected = _selected_flight_from_active_trip(active_trip)
    flight = selected[1] if selected else None
    if flight is None:
        current = _current_displayed_flight(session_id, db)
        flight = current[1] if current else None
    if flight is None:
        memory = _latest_flight_memory(session_id, db)
        flights = memory.get("flights") if memory and isinstance(memory.get("flights"), list) else []
        flight = flights[0] if len(flights) == 1 else None
    if not isinstance(flight, dict):
        return None, None
    return _flight_price(flight), str(flight.get("currency") or "GBP").upper()


def _execute_contextual_cheapest_result_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    if not _looks_like_cheapest_result_question(request.message):
        return None
    visible = _latest_visible_result_type(session.id, db)
    normalized = _normalize_control_message(request.message)

    if "hotel" in normalized or visible == "hotels":
        match = _cheapest_hotel(session.id, db)
        if match is None:
            return None
        option_number, hotel = match
        _set_current_hotel_option(session.id, option_number, db)
        price = _hotel_price(hotel)
        currency = str(hotel.get("currency") or "GBP").upper()
        nights = int(hotel.get("nights") or 1)
        hotel_name = str(hotel.get("hotel_name") or f"hotel option {option_number}")
        check_in = _display_date_label(str(hotel.get("check_in_date") or "")) or hotel.get("check_in_date")
        check_out = _display_date_label(str(hotel.get("check_out_date") or "")) or hotel.get("check_out_date")
        reply = (
            f"The cheapest hotel is option {option_number}, {hotel_name}, at "
            f"{_format_money_for_reply(price, currency)} for {nights} nights"
        )
        if check_in and check_out:
            reply += f" from {check_in} to {check_out}"
        if any(word in normalized for word in ["book", "select", "choose"]) and not any(
            word in normalized for word in ["which", "what"]
        ):
            active_trip = _active_trip_for_session(session.id, db)
            active_trip["selectedHotelOption"] = {
                "optionNumber": option_number,
                **deepcopy(hotel),
            }
            active_trip["latestVisibleResultType"] = "hotel_selected"
            active_trip["status"] = "hotel_selected"
            active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
            save_active_trip(db, session.id, active_trip)
            _set_last_tool_used(session.id, "select_hotel", db)
            return _tool_response(
                db,
                session,
                request,
                _hotel_follow_up_intent(session.id, request.message, db),
                (
                    f"You selected {hotel_name}. Would you like to add it to your "
                    "cart and continue to checkout?"
                ),
                _hotel_results_for_chat([hotel]),
            )
        active_trip = _active_trip_for_session(session.id, db)
        budget = active_trip.get("budget")
        flight_price, flight_currency = _current_or_selected_flight_price(session.id, db)
        if isinstance(budget, (int, float)) and price is not None and (
            flight_price is None or str(flight_currency or currency).upper() == currency
        ):
            total = price + (flight_price or 0)
            budget_label = _format_money_for_reply(float(budget), currency)
            total_label = _format_money_for_reply(total, currency)
            if total <= float(budget):
                reply += f". With your flight, the estimated total is {total_label}, within your {budget_label} budget"
            else:
                reply += f". With your flight, the estimated total is {total_label}, above your {budget_label} budget"
        reply += ". Would you like to select this hotel?"
        _set_last_tool_used(session.id, "rank_hotels", db)
        return _tool_response(
            db,
            session,
            request,
            _hotel_follow_up_intent(session.id, request.message, db),
            reply,
            _hotel_results_for_chat([hotel]),
        )

    if visible == "flights":
        return _execute_rank_flights_tool(
            TravelToolCall(tool="rank_flights", arguments={"ranking": "cheapest", "limit": 1}),
            db,
            session,
            request,
        )
    if visible == "packages":
        return _execute_cheaper_package_followup(db, session, request)
    return None


def _execute_contextual_option_selection_if_possible(
    db: Session,
    user: User,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    option_number = _extract_option_number(request.message)
    if option_number is None or not _is_select_option_message(request.message):
        return None

    visible_type = _latest_visible_result_type(session.id, db)
    if visible_type == "flights":
        return _handle_flight_memory_action(
            db,
            session,
            request,
            "select_flight_option",
            option_number,
        )
    if visible_type == "hotels":
        return _execute_select_hotel_tool(
            TravelToolCall(
                tool="select_hotel",
                arguments={"selection": "option_number", "option_number": option_number},
            ),
            db,
            session,
            request,
        )
    if visible_type == "packages":
        return _execute_package_selection_if_possible(db, session, request)

    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "general_enquiry"),
        "Which option do you mean - a flight, hotel, or package option?",
    )


def _package_option_number_from_message(message: str) -> int | None:
    match = re.search(
        r"\b(?:package|option)\s+(\d+)\b",
        _normalize_control_message(message),
    )
    if match:
        return int(match.group(1))
    return _extract_option_number(message)


def _matching_date_comparison_result(
    active_trip: dict[str, Any],
    departure_iso: str,
) -> dict[str, Any] | None:
    comparison = _date_comparison_from_active_trip(active_trip)
    if not comparison:
        return None
    results = comparison.get("results")
    if not isinstance(results, list):
        return None
    for item in results:
        if isinstance(item, dict) and item.get("departureDate") == departure_iso:
            return item
    return None


def _looks_like_date_selection_followup(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "i will go on",
            "ill go on",
            "i'll go on",
            "lets do",
            "let's do",
            "choose the",
            "book the",
            "check the",
            "that date works",
            "date works",
            "option 1 date",
            "the first date",
        ]
    )


def _execute_date_selection_followup_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    comparison = _date_comparison_from_active_trip(active_trip)
    if comparison is None or _active_trip_destination(active_trip) is None:
        return None
    if not _looks_like_date_selection_followup(request.message):
        return None

    departure_iso = (
        _resolve_named_month_date_from_message(request.message)
        or _resolve_day_only_followup(
            request.message,
            base_date=_active_trip_day_only_base_date(active_trip),
        )
        or _resolve_explicit_departure_override(request.message, active_trip)
    )
    if departure_iso is None and "that date works" in _normalize_control_message(request.message):
        cheapest = comparison.get("cheapestResult")
        if isinstance(cheapest, dict):
            departure_iso = cheapest.get("departureDate")
    if not departure_iso:
        return None

    nights = active_trip.get("tripDurationNights")
    if not isinstance(nights, int) or nights < 1:
        nights = int(comparison.get("tripDurationNights") or 3)
    try:
        departure_date = date.fromisoformat(departure_iso)
    except ValueError:
        return None
    return_iso = (departure_date + timedelta(days=nights)).isoformat()

    _clear_active_trip_package_state(active_trip)
    active_trip["dates"] = {
        "departureDate": departure_iso,
        "returnDate": return_iso,
        "source": "selected_date_followup",
    }
    active_trip["tripDurationNights"] = nights
    active_trip["status"] = "ready_for_package_or_checkout"
    matched = _matching_date_comparison_result(active_trip, departure_iso)
    active_trip["selectedFlightCandidate"] = (
        matched.get("offerSummary") if isinstance(matched, dict) else None
    )
    active_trip["latestVisibleResultType"] = "date_selection"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    _set_last_tool_used(session.id, "select_trip_date", db)

    destination = _active_trip_destination(active_trip) or {}
    reply = (
        f"Great - I'll use {_display_date_label(departure_iso) or departure_iso} "
        f"to {_display_date_label(return_iso) or return_iso} for your "
        f"{nights}-night {destination.get('city') or 'trip'} trip. "
        "I can now find flights and hotels together."
    )
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "general_enquiry"),
        reply,
    )


def _message_explicitly_names_route(
    message: str,
    flight_search: FlightSearchRequest,
) -> bool:
    normalized = _normalize_control_message(message)
    if "from" not in normalized or "to" not in normalized:
        return False
    origin = flight_search.itinerary.origin
    destination = flight_search.itinerary.destination
    route_terms = [
        str(origin.code or "").lower(),
        str(destination.code or "").lower(),
        _normalize_control_message(origin.city or ""),
        _normalize_control_message(destination.city or ""),
    ]
    route_terms = [term for term in route_terms if term]
    return sum(1 for term in route_terms if _has_phrase(normalized, term)) >= 2


def validate_flight_search_against_active_trip(
    flight_search: FlightSearchRequest,
    active_trip: dict[str, Any],
    message: str,
) -> FlightSearchRequest:
    destination = _active_trip_destination(active_trip)
    origin = _active_trip_origin(active_trip)
    if not destination or not origin:
        return flight_search

    expected_destination = str(destination.get("airportCode") or "").upper()
    expected_origin = str(origin.get("airportCode") or "").upper()
    if not expected_destination or not expected_origin:
        return flight_search

    dates = _active_trip_dates(active_trip)
    expected_departure = dates.get("departureDate")
    expected_return = dates.get("returnDate")
    actual_origin = flight_search.itinerary.origin.code.upper()
    actual_destination = flight_search.itinerary.destination.code.upper()
    actual_departure = flight_search.itinerary.dates.departure.isoformat()
    actual_return = (
        flight_search.itinerary.dates.return_date.isoformat()
        if flight_search.itinerary.dates.return_date
        else None
    )
    matches_active_trip = (
        actual_origin == expected_origin
        and actual_destination == expected_destination
        and (not expected_departure or actual_departure == expected_departure)
        and (not expected_return or actual_return == expected_return)
    )
    if matches_active_trip or _message_explicitly_names_route(message, flight_search):
        return flight_search

    built_request, error = build_flight_request_from_active_trip(active_trip)
    if built_request is None:
        LOGGER.info("Active trip route guard could not rebuild request: %s", error)
        return flight_search
    LOGGER.info(
        "Active trip route guard replaced conflicting flight request: %s",
        {
            "expectedOrigin": expected_origin,
            "expectedDestination": expected_destination,
            "actualOrigin": actual_origin,
            "actualDestination": actual_destination,
            "activeTripRevision": active_trip.get("revision"),
        },
    )
    return built_request


def _trip_search_intent(message: str, products: list[str]) -> dict[str, Any]:
    if products == ["flights"]:
        intent_type = "flight_search"
    elif products == ["hotels"]:
        intent_type = "hotel_search"
    else:
        intent_type = "general_enquiry"
    return _build_control_intent(message, intent_type)


def _execute_active_trip_product_search(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    requested_products: list[str],
) -> ChatResponse:
    active_trip = _active_trip_for_session(session.id, db)
    intent = _trip_search_intent(request.message, requested_products)
    results = _empty_travel_results()
    reply_parts: list[str] = []
    provider_parts: list[str] = []

    if "flights" in requested_products:
        flight_request, error = build_flight_request_from_active_trip(active_trip)
        if flight_request is None:
            return _tool_response(db, session, request, intent, error or "When do you want to depart?")
        flight_request = validate_flight_search_against_active_trip(
            flight_request,
            active_trip,
            request.message,
        )
        provider_response = _search_flights_with_fallback(flight_request)
        LOGGER.info("Provider used: %s", provider_response.provider_used)
        _store_latest_flight_results(
            session.id,
            _build_llama_flight_intent(request.message, flight_request),
            provider_response,
            flight_request,
            db,
        )
        results["flights"] = _flight_search_results_for_chat(provider_response)["flights"]
        reply_parts.append(
            f"flights from {flight_request.itinerary.origin.code.upper()} "
            f"to {flight_request.itinerary.destination.code.upper()}"
        )
        provider_parts.append(provider_response.provider_used)

    if "hotels" in requested_products:
        hotel_request, error = build_hotel_request_from_active_trip(active_trip)
        if hotel_request is None:
            return _tool_response(db, session, request, intent, error or "Please choose hotel dates.")
        provider_response = _search_hotels_with_fallback(hotel_request)
        LOGGER.info("Provider used: %s", provider_response.provider_used)
        hotel_intent = _build_hotel_intent(request.message, hotel_request)
        _store_latest_hotel_results(
            session.id,
            hotel_intent,
            provider_response,
            hotel_request,
            db,
        )
        hotels = _normalised_hotel_dicts(provider_response)
        results["hotels"] = _hotel_results_for_chat(hotels, CHAT_HOTEL_CARD_LIMIT)["hotels"]
        city = hotel_request.destination.city or hotel_request.destination.country
        reply_parts.append(f"hotels in {city}")
        provider_parts.append(provider_response.provider_used)

    if not reply_parts:
        return _tool_response(
            db,
            session,
            request,
            intent,
            "Please choose whether you want flights, hotels, or both.",
        )

    destination = _active_trip_destination(active_trip) or {}
    dates = _active_trip_dates(active_trip)
    active_trip["requestedProducts"] = requested_products
    active_trip["status"] = "ready_for_search"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    _set_last_tool_used(
        session.id,
        "search_trip_options" if len(requested_products) > 1 else f"search_{requested_products[0]}",
        db,
    )
    _save_message(db, session.id, "user", request.message, intent)
    connector = " and " if len(reply_parts) == 2 else ", "
    reply = f"I searched {connector.join(reply_parts)}"
    nights = active_trip.get("tripDurationNights")
    if dates.get("departureDate") and dates.get("returnDate"):
        reply += (
            f" from {dates['departureDate']} to {dates['returnDate']}"
            + (f" for {nights} nights" if nights else "")
        )
    if provider_parts:
        reply += "."
    if destination.get("airportCode"):
        reply += f" The active trip destination is {destination.get('city')} ({destination.get('airportCode')})."
    _save_message(db, session.id, "assistant", reply)
    db.commit()
    LOGGER.info(
        "Routing trace: %s",
        {
            "route": "destination_selection_to_trip_search",
            "selectedDestination": destination.get("airportCode"),
            "origin": (_active_trip_origin(active_trip) or {}).get("airportCode"),
            "departureDate": dates.get("departureDate"),
            "returnDate": dates.get("returnDate"),
            "requestedProducts": requested_products,
            "activeTripRevision": active_trip.get("revision"),
        },
    )
    return ChatResponse(
        reply=reply,
        intent=intent,
        results=results,
        requires_confirmation=False,
        pending_action=None,
        sessionId=session.id,
    )


def _execute_package_request_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    *,
    force: bool = False,
) -> ChatResponse | None:
    if not force and not _looks_like_package_request(request.message):
        return None
    if not force and (
        _looks_like_package_selection(request.message)
        or _looks_like_package_checkout(request.message)
    ):
        return None

    active_trip = _active_trip_for_session(session.id, db)
    if (
        not force
        and isinstance(active_trip.get("pendingTripRequest"), dict)
        and isinstance(active_trip.get("destinationRegion"), dict)
        and (
            active_trip.get("selectedDestination") is None
            or not _active_trip_dates(active_trip).get("departureDate")
            or not _active_trip_dates(active_trip).get("returnDate")
        )
    ):
        return _execute_prepare_trip_request_tool(
            TravelToolCall(
                tool="prepare_trip_request",
                arguments={
                    "intent": active_trip["pendingTripRequest"].get("intent") or "package_search",
                    "use_active_trip_state": True,
                },
            ),
            db,
            session,
            request,
        )
    if not force:
        pending = _merge_package_clarification(
            active_trip,
            request.message,
            start_new=True,
        )
        if pending is not None:
            active_trip = _save_pending_package_request(
                db,
                session.id,
                active_trip,
                pending,
            )
    package_request, error = build_package_request_from_active_trip(active_trip)
    intent = _build_control_intent(request.message, "package_search")
    if package_request is None:
        pending = _pending_package_request(active_trip) or _new_pending_package_request(
            active_trip,
        )
        pending["missingFields"] = _pending_package_missing_fields(pending)
        active_trip = _save_pending_package_request(
            db,
            session.id,
            active_trip,
            pending,
        )
        question_text = _pending_package_question(pending) or error or "Please choose a destination and dates before I build a package."
        return _tool_response(
            db,
            session,
            request,
            intent,
            _clarification_reply_text(
                db,
                session.id,
                active_trip,
                _pending_package_missing_fields(pending),
                question_text,
            ),
        )
    if not _package_request_matches_active_trip(package_request, active_trip):
        package_request, error = build_package_request_from_active_trip(active_trip)
        if package_request is None:
            return _tool_response(
                db,
                session,
                request,
                intent,
                error or "Please choose a destination and dates before I build a package.",
            )

    flight_request = _flight_request_from_package_request(package_request)
    hotel_request = _hotel_request_from_package_request(package_request)
    if (
        flight_request.itinerary.origin.code.upper()
        != package_request.origin_airport_code.upper()
        or flight_request.itinerary.destination.code.upper()
        != package_request.destination_airport_code.upper()
        or hotel_request.destination.city != package_request.destination_city
        or hotel_request.check_in_date != package_request.departure_date
        or hotel_request.check_out_date != package_request.return_date
    ):
        return _tool_response(
            db,
            session,
            request,
            intent,
            "I could not safely build this package from the active trip. Please choose the destination and dates again.",
        )

    flight_response = _search_flights_with_fallback(flight_request)
    hotel_response = _search_hotels_with_fallback(hotel_request)
    _store_latest_flight_results(
        session.id,
        _build_llama_flight_intent(request.message, flight_request),
        flight_response,
        flight_request,
        db,
    )
    _store_latest_hotel_results(
        session.id,
        _build_hotel_intent(request.message, hotel_request),
        hotel_response,
        hotel_request,
        db,
    )
    active_trip = _active_trip_for_session(session.id, db)
    packages = _build_package_options(
        package_request,
        flight_response,
        hotel_response,
        int(active_trip.get("revision") or 0),
    )
    active_trip["latestPackageRequest"] = package_request.model_dump(
        by_alias=True,
        mode="json",
    )
    active_trip["pendingPackageRequest"] = None
    active_trip["pendingAction"] = None
    active_trip["latestPackageOptions"] = packages
    active_trip["selectedPackageOption"] = None
    active_trip["cart"] = None
    active_trip["checkout"] = None
    active_trip["flightTicket"] = None
    active_trip["hotelVoucher"] = None
    active_trip["latestVisibleResultType"] = "packages"
    active_trip["requestedProducts"] = ["flights", "hotels"]
    active_trip["status"] = "package_options_ready"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    _set_last_tool_used(session.id, "build_package_options", db)

    origin = _active_trip_origin(active_trip) or {}
    destination = _active_trip_destination(active_trip) or {}
    if not packages:
        reply = (
            "I searched flights and hotels for your active trip, but I could not "
            "combine matching-currency package options."
        )
    else:
        cheapest = packages[0]
        nights = (package_request.return_date - package_request.departure_date).days
        reply = (
            f"I built package options for your {nights}-night "
            f"{package_request.destination_city} trip from {_origin_message(origin)}, "
            f"departing {_display_date_label(package_request.departure_date.isoformat())} "
            f"and returning {_display_date_label(package_request.return_date.isoformat())}. "
            f"The cheapest package is {cheapest['currency']} {cheapest['totalPrice']:,.2f} "
            "including return flight and hotel."
        )
        if package_request.max_budget is not None:
            budget_label = f"{cheapest['currency']} {package_request.max_budget:,.0f}"
            if any(
                float(package.get("totalPrice") or 0) <= package_request.max_budget
                for package in packages
            ):
                reply += f" These options include choices within your {budget_label} budget."
            else:
                reply += (
                    f" I could not find packages under your {budget_label} budget, "
                    "but these are the closest options I found."
                )
        if destination.get("airportCode"):
            reply += (
                f" The package route is {package_request.origin_airport_code} "
                f"to {package_request.destination_airport_code}."
            )

    LOGGER.info(
        "Routing trace: %s",
        {
            "route": "active_trip_package_builder",
            "origin": package_request.origin_airport_code,
            "destination": package_request.destination_airport_code,
            "hotelCity": package_request.destination_city,
            "departureDate": package_request.departure_date.isoformat(),
            "returnDate": package_request.return_date.isoformat(),
            "packageCount": len(packages),
            "activeTripRevision": active_trip.get("revision"),
        },
    )
    return _tool_response(
        db,
        session,
        request,
        intent,
        reply,
        _package_results_for_chat(packages),
    )


def _latest_package_options(active_trip: dict[str, Any]) -> list[dict[str, Any]]:
    options = active_trip.get("latestPackageOptions")
    return options if isinstance(options, list) else []


def _find_package_option(
    packages: list[dict[str, Any]],
    option_number: int,
) -> dict[str, Any] | None:
    for package in packages:
        if int(package.get("optionNumber") or 0) == option_number:
            return package
        if int(package.get("option_number") or 0) == option_number:
            return package
    return None


def _package_option_matches_active_trip(
    package: dict[str, Any],
    active_trip: dict[str, Any],
) -> bool:
    origin = _active_trip_origin(active_trip) or {}
    destination = _active_trip_destination(active_trip) or {}
    dates = _active_trip_dates(active_trip)
    flight = package.get("flight") if isinstance(package.get("flight"), dict) else {}
    hotel = package.get("hotel") if isinstance(package.get("hotel"), dict) else {}
    if origin.get("airportCode") and str(flight.get("origin") or "").upper() != str(origin["airportCode"]).upper():
        return False
    if destination.get("airportCode") and str(flight.get("destination") or "").upper() != str(destination["airportCode"]).upper():
        return False
    if destination.get("airportCode") and str(package.get("destinationAirportCode") or "").upper() != str(destination["airportCode"]).upper():
        return False
    if dates.get("departureDate") and str(flight.get("departureDate") or "") != str(dates["departureDate"]):
        return False
    if dates.get("returnDate") and str(flight.get("returnDate") or "") != str(dates["returnDate"]):
        return False
    if dates.get("departureDate") and str(hotel.get("checkInDate") or "") != str(dates["departureDate"]):
        return False
    if dates.get("returnDate") and str(hotel.get("checkOutDate") or "") != str(dates["returnDate"]):
        return False
    return True


def _execute_package_selection_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    *,
    option_number: int | None = None,
    force: bool = False,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    packages = _latest_package_options(active_trip)
    if not packages:
        return None
    if not force and not _looks_like_package_selection(request.message):
        return None
    option_number = option_number or _package_option_number_from_message(request.message)
    if option_number is None:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_selection"),
            "Which package option would you like to add to your cart?",
            _package_results_for_chat(packages),
        )
    package = _find_package_option(packages, option_number)
    if package is None:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_selection"),
            f"I could not find package option {option_number}. Please choose a number between 1 and {len(packages)}.",
            _package_results_for_chat(packages),
        )
    if (
        active_trip.get("latestVisibleResultType") != "packages"
        or not _package_option_matches_active_trip(package, active_trip)
    ):
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_selection"),
            "I can build a package first, but I need your exact travel dates and destination city or airport before I can add one to the cart.",
        )

    cart = _package_cart_summary(package, session.id)
    active_trip["selectedPackageOption"] = package
    active_trip["cart"] = cart
    active_trip["cartStatus"] = "active"
    active_trip["latestCartId"] = cart.get("cartId")
    active_trip["checkout"] = None
    active_trip["flightTicket"] = None
    active_trip["hotelVoucher"] = None
    active_trip["latestVisibleResultType"] = "cart"
    active_trip["status"] = "package_in_cart"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    _set_last_tool_used(session.id, "add_package_to_cart", db)

    reply = (
        f"I added package option {option_number} to your cart. "
        f"The total is {package.get('currency')} {float(package.get('totalPrice') or 0):,.2f}. "
        "You can now proceed to checkout."
    )
    results = _package_results_for_chat([package])
    results["cart"] = cart
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "package_option_selection"),
        reply,
        results,
        next_action="navigate",
        next_path="/cart",
    )


def _looks_like_hotel_add_to_cart_request(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return (
        _is_confirmation_message(message)
        or _is_current_flight_reference(message)
        or _looks_like_package_checkout(message)
        or any(
            phrase in normalized
            for phrase in [
                "yes book it",
                "book it",
                "add to cart",
                "add it to cart",
                "add this to cart",
                "continue",
                "continue with it",
                "continue to cart",
                "go to cart",
            ]
        )
    )


def _looks_like_flight_add_to_cart_request(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return (
        _is_confirmation_message(message)
        or _is_current_flight_reference(message)
        or _looks_like_package_checkout(message)
        or any(
            phrase in normalized
            for phrase in [
                "confirm this booking",
                "confirm this flight",
                "book it",
                "add to cart",
                "add it to cart",
                "put it on checkout",
                "continue to checkout",
                "go to checkout",
                "go to cart",
            ]
        )
    )


def _selected_flight_from_active_trip(active_trip: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    selected = active_trip.get("selectedFlightOption")
    if not isinstance(selected, dict):
        return None
    option_number = selected.get("optionNumber") or selected.get("option_number")
    try:
        option = int(option_number)
    except (TypeError, ValueError):
        option = 1
    flight = deepcopy(selected)
    flight.pop("optionNumber", None)
    return option, flight


def _execute_selected_flight_cart_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db) if isinstance(db, Session) else {}
    selected = _selected_flight_from_active_trip(active_trip)
    pending_flight = _selected_pending_flight(session.id, db)
    if selected is None and isinstance(pending_flight, dict):
        selected = (
            _option_number_for_flight(session.id, pending_flight.get("id"), db) or 1,
            pending_flight,
        )
    visible = active_trip.get("latestVisibleResultType")
    memory = FLIGHT_SEARCH_MEMORY.get(session.id)
    cart = active_trip.get("cart") if isinstance(active_trip.get("cart"), dict) else None
    if cart is None and isinstance(memory, dict) and isinstance(memory.get("cart"), dict):
        cart = memory["cart"]
    cart_items = cart.get("items") if isinstance(cart, dict) else None
    has_flight_in_cart = isinstance(cart_items, list) and any(
        isinstance(item, dict)
        and (item.get("itemType") == "flight" or item.get("type") == "flight")
        for item in cart_items
    )

    if not _looks_like_flight_add_to_cart_request(request.message):
        return None
    if selected is None and not has_flight_in_cart:
        return None
    if selected is None and has_flight_in_cart and visible not in {"cart", "flight_selected"}:
        return None
    if selected is not None and visible != "flight_selected" and not has_flight_in_cart:
        return None

    if selected is not None:
        option_number, flight = selected
        if _cart_contains_flight(cart, flight):
            if isinstance(db, Session):
                active_trip["latestVisibleResultType"] = "cart"
                active_trip["cartStatus"] = "active"
                save_active_trip(db, session.id, active_trip)
            _set_last_tool_used(session.id, "flight_cart_already_added", db)
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "package_option_selection"),
                "That flight is already in your cart. You can continue to checkout.",
                {**_empty_travel_results(), "cart": cart},
                next_action="navigate",
                next_path="/cart",
            )

        cart = _flight_cart_summary(flight, option_number, session.id, _flight_price)
        if isinstance(memory, dict):
            memory["cart"] = cart
        _clear_pending_flight_selection(session.id, db)
        if isinstance(db, Session):
            active_trip = _active_trip_for_session(session.id, db)
            active_trip["cart"] = cart
            active_trip["cartStatus"] = "active"
            active_trip["latestCartId"] = cart.get("cartId")
            active_trip["latestVisibleResultType"] = "cart"
            active_trip["status"] = "flight_in_cart"
            active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
            save_active_trip(db, session.id, active_trip)
        _set_last_tool_used(session.id, "add_flight_to_cart", db)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_selection"),
            "Great - I added your selected flight to the cart. You can now continue to checkout.",
            {**_flight_results_for_chat([flight]), "cart": cart},
            next_action="navigate",
            next_path="/cart",
        )

    if isinstance(db, Session):
        active_trip["latestVisibleResultType"] = "cart"
        active_trip["cartStatus"] = "active"
        save_active_trip(db, session.id, active_trip)
    _set_last_tool_used(session.id, "flight_cart_already_added", db)
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "package_option_selection"),
        "That flight is already in your cart. You can continue to checkout.",
        {**_empty_travel_results(), "cart": cart},
        next_action="navigate",
        next_path="/cart",
    )


def _execute_this_flight_reference_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    if not _looks_like_this_flight_reference(request.message):
        return None
    visible = _latest_visible_result_type(session.id, db)
    if visible != "flights":
        return None
    memory = _latest_flight_memory(session.id, db)
    if memory is None:
        return None
    flights = memory.get("flights") if isinstance(memory.get("flights"), list) else []
    selected = _selected_flight_from_active_trip(_active_trip_for_session(session.id, db))
    if selected is None and len(flights) != 1:
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "Which flight option would you like to add to your cart?",
            _flight_results_for_chat(flights, CHAT_FLIGHT_CARD_LIMIT),
        )

    if selected is None:
        match = _flight_option(session.id, 1, db)
        if match is None:
            return _flight_context_response(db, session, request)
        selected_option, selected_flight = match
        _set_current_flight_option(session.id, selected_option, db)
        _select_pending_flight(session.id, selected_flight, db)
    else:
        selected_option, selected_flight = selected

    if _looks_like_direct_add_or_confirm(request.message):
        return _execute_selected_flight_cart_if_possible(db, session, request)

    return _tool_response(
        db,
        session,
        request,
        _flight_follow_up_intent(session.id, request.message, db),
        (
            f"You selected flight option {selected_option}. Would you like to add it "
            "to your cart and continue to checkout?"
        ),
        _flight_results_for_chat([selected_flight]),
    )


def _execute_this_hotel_reference_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    if not _looks_like_this_hotel_reference(request.message):
        return None
    visible = _latest_visible_result_type(session.id, db)
    if visible != "hotels":
        return None
    active_trip = _active_trip_for_session(session.id, db)
    selected = _selected_hotel_from_active_trip(active_trip)
    memory = _latest_hotel_memory(session.id, db)
    hotels = memory.get("hotels") if memory and isinstance(memory.get("hotels"), list) else []
    if selected is None and len(hotels) != 1:
        return _tool_response(
            db,
            session,
            request,
            _hotel_follow_up_intent(session.id, request.message, db),
            "Which hotel option would you like to add to your cart?",
            _hotel_results_for_chat(hotels, CHAT_HOTEL_CARD_LIMIT),
        )
    if selected is None:
        match = _hotel_option(session.id, 1, db)
        if match is None:
            return _hotel_context_response(db, session, request)
        selected_option, hotel = match
        _set_current_hotel_option(session.id, selected_option, db)
        active_trip["selectedHotelOption"] = {
            "optionNumber": selected_option,
            **deepcopy(hotel),
        }
        active_trip["latestVisibleResultType"] = "hotel_selected"
        active_trip["status"] = "hotel_selected"
        active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
        save_active_trip(db, session.id, active_trip)
    if _looks_like_direct_add_or_confirm(request.message):
        return _execute_selected_hotel_cart_if_possible(db, session, request)
    return _execute_select_hotel_tool(
        TravelToolCall(tool="select_hotel", arguments={"selection": "current_displayed"}),
        db,
        session,
        request,
    )


def _is_thank_you_message(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return normalized in {
        "thanks",
        "thank you",
        "okay thanks",
        "ok thanks",
        "perfect thanks",
        "great thanks",
        "thanks voyager",
        "thank you voyager",
    }


def _execute_thank_you_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    if not _is_thank_you_message(request.message):
        return None
    active_trip = _active_trip_for_session(session.id, db)
    cart = active_trip.get("cart") if isinstance(active_trip.get("cart"), dict) else None
    items = cart.get("items") if isinstance(cart, dict) else None
    if isinstance(items, list) and any(isinstance(item, dict) for item in items):
        reply = "You're welcome - your selected item is in the cart. You can continue to checkout when ready."
        results = {**_empty_travel_results(), "cart": cart}
    elif (
        isinstance(active_trip.get("selectedFlightOption"), dict)
        or isinstance(active_trip.get("selectedHotelOption"), dict)
        or isinstance(active_trip.get("selectedPackageOption"), dict)
    ):
        reply = "You're welcome - would you like me to add the selected option to your cart?"
        results = _empty_travel_results()
    else:
        reply = "You're welcome - let me know if you want flights, hotels, or packages."
        results = _empty_travel_results()
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "general_enquiry"),
        reply,
        results,
    )


def _selected_hotel_from_active_trip(active_trip: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    selected = active_trip.get("selectedHotelOption")
    if not isinstance(selected, dict):
        return None
    option_number = selected.get("optionNumber") or selected.get("option_number")
    try:
        option = int(option_number)
    except (TypeError, ValueError):
        option = 1
    hotel = deepcopy(selected)
    hotel.pop("optionNumber", None)
    return option, hotel


def _execute_selected_hotel_cart_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    selected = _selected_hotel_from_active_trip(active_trip)
    visible = active_trip.get("latestVisibleResultType")
    cart = active_trip.get("cart") if isinstance(active_trip.get("cart"), dict) else None
    cart_items = cart.get("items") if isinstance(cart, dict) else None
    has_hotel_in_cart = isinstance(cart_items, list) and any(
        isinstance(item, dict)
        and (item.get("itemType") == "hotel" or item.get("type") == "hotel")
        for item in cart_items
    )

    if not _looks_like_hotel_add_to_cart_request(request.message):
        return None
    if selected is None and not has_hotel_in_cart:
        return None
    if selected is not None and visible != "hotel_selected" and not has_hotel_in_cart:
        return None

    if selected is not None:
        option_number, hotel = selected
        hotel_name = str(hotel.get("hotel_name") or "That hotel")
        if _cart_contains_hotel(cart, hotel):
            active_trip["latestVisibleResultType"] = "cart"
            active_trip["cartStatus"] = "active"
            save_active_trip(db, session.id, active_trip)
            _set_last_tool_used(session.id, "hotel_cart_already_added", db)
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "package_option_selection"),
                f"{hotel_name} is already in your cart. You can continue to checkout.",
                {**_empty_travel_results(), "cart": cart},
                next_action="navigate",
                next_path="/cart",
            )

        cart = _hotel_cart_summary(hotel, option_number, session.id, _hotel_price)
        active_trip["cart"] = cart
        active_trip["cartStatus"] = "active"
        active_trip["latestCartId"] = cart.get("cartId")
        active_trip["latestVisibleResultType"] = "cart"
        active_trip["status"] = "hotel_in_cart"
        active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
        _clear_pending_hotel_selection(session.id, db)
        save_active_trip(db, session.id, active_trip)
        _set_last_tool_used(session.id, "add_hotel_to_cart", db)
        check_in = _display_date_label(str(hotel.get("check_in_date") or "")) or hotel.get("check_in_date")
        check_out = _display_date_label(str(hotel.get("check_out_date") or "")) or hotel.get("check_out_date")
        reply = (
            f"Great - I added {hotel_name} to your cart. "
            f"Your stay is {check_in} to {check_out}. You can now continue to checkout."
        )
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_selection"),
            reply,
            {**_hotel_results_for_chat([hotel]), "cart": cart},
            next_action="navigate",
            next_path="/cart",
        )

    active_trip["latestVisibleResultType"] = "cart"
    active_trip["cartStatus"] = "active"
    save_active_trip(db, session.id, active_trip)
    _set_last_tool_used(session.id, "hotel_cart_already_added", db)
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "package_option_selection"),
        "That hotel is already in your cart. You can continue to checkout.",
        {**_empty_travel_results(), "cart": cart},
        next_action="navigate",
        next_path="/cart",
    )


def _generate_package_booking_reference(db: Session) -> str:
    for _ in range(20):
        reference = f"PKG{randbelow(900000) + 100000}"
        exists = (
            db.query(Booking)
            .filter(Booking.booking_reference == reference)
            .first()
            is not None
        )
        if not exists:
            return reference
    return _generate_booking_reference(db)


def _travel_documents_for_package(
    package: dict[str, Any],
    booking_reference: str,
    passenger_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    flight = package.get("flight") if isinstance(package.get("flight"), dict) else {}
    hotel = package.get("hotel") if isinstance(package.get("hotel"), dict) else {}
    suffix = booking_reference[-6:]
    ticket = FlightTicket(
        ticketNumber=f"VAI-FLT-{suffix}",
        passengerName=passenger_name or "Demo Passenger",
        airline=flight.get("airline"),
        origin=str(flight.get("origin") or ""),
        destination=str(flight.get("destination") or ""),
        departureDate=str(flight.get("departureDate") or ""),
        returnDate=flight.get("returnDate"),
        departureTime=flight.get("departureTime"),
        returnTime=flight.get("returnTime"),
        cabin=str(flight.get("cabin") or "economy"),
        bookingReference=booking_reference,
        status="confirmed",
    )
    voucher = HotelVoucher(
        voucherNumber=f"VAI-HTL-{suffix}",
        hotelName=str(hotel.get("hotelName") or "Selected hotel"),
        city=str(hotel.get("city") or package.get("destinationCity") or ""),
        checkInDate=str(hotel.get("checkInDate") or ""),
        checkOutDate=str(hotel.get("checkOutDate") or ""),
        nights=int(hotel.get("nights") or package.get("nights") or 1),
        roomName=hotel.get("roomName"),
        boardType=hotel.get("boardType"),
        guests=int(package.get("passengers") or 1),
        bookingReference=booking_reference,
        status="confirmed",
    )
    return ticket.model_dump(mode="json"), voucher.model_dump(mode="json")


def _execute_package_checkout_if_possible(
    db: Session,
    user: User,
    session: ChatSession,
    request: ChatRequest,
    *,
    force: bool = False,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    if not force and not _looks_like_contextual_checkout_request(request.message, active_trip):
        return None
    cart = active_trip.get("cart") if isinstance(active_trip.get("cart"), dict) else None
    items = cart.get("items") if isinstance(cart, dict) else None
    if not items:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "booking_confirmation"),
            "Please add a package to your cart first.",
        )
    active_trip["latestVisibleResultType"] = "cart"
    active_trip["cartStatus"] = str(cart.get("status") or "active")
    active_trip["latestCartId"] = cart.get("cartId")
    save_active_trip(db, session.id, active_trip)
    _set_last_tool_used(session.id, "checkout_handoff", db)
    results = _empty_travel_results()
    results["cart"] = cart
    if user.email == DEMO_USER_EMAIL:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "booking_confirmation"),
            (
                "Please sign in to complete checkout. I saved your selected package "
                "so you can continue after signing in."
            ),
            results,
            next_action="navigate",
            next_path="/profile?next=/checkout",
        )
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "booking_confirmation"),
        "Great - I'll take you to checkout now.",
        results,
        next_action="navigate",
        next_path="/checkout",
    )


def _execute_structured_cart_tool(
    tool_call: TravelToolCall,
    db: Session,
    user: User,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    """Execute Llama-selected cart actions after backend state validation."""

    if tool_call.tool == "add_to_cart":
        item_type = str(
            tool_call.arguments.get("item_type")
            or tool_call.arguments.get("itemType")
            or "package"
        ).lower()
        raw_option = tool_call.arguments.get("option_number")
        if raw_option is None:
            raw_option = tool_call.arguments.get("optionNumber")
        try:
            option_number = int(raw_option) if raw_option is not None else None
        except (TypeError, ValueError):
            option_number = None

        if item_type == "package":
            if option_number is None:
                return _tool_response(
                    db,
                    session,
                    request,
                    _build_control_intent(request.message, "package_option_selection"),
                    "Please choose a package option number before adding one to your cart.",
                )
            return _execute_package_selection_if_possible(
                db,
                session,
                request,
                option_number=option_number,
                force=True,
            ) or _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "package_option_selection"),
                "I can build a package first, but I need your exact travel dates and destination city or airport before I can add one to the cart.",
            )
        if item_type == "flight":
            return _execute_selected_flight_cart_if_possible(db, session, request) or _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "package_option_selection"),
                "Please choose a flight option before adding it to your cart.",
            )
        if item_type == "hotel":
            return _execute_selected_hotel_cart_if_possible(db, session, request) or _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "package_option_selection"),
                "Please choose a hotel option before adding it to your cart.",
            )
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "general_enquiry"),
            "Do you want to add a flight, hotel, or package to your cart?",
        )

    if tool_call.tool == "checkout":
        return _execute_package_checkout_if_possible(
            db,
            user,
            session,
            request,
            force=True,
        )

    if tool_call.tool == "cart_view":
        active_trip = _active_trip_for_session(session.id, db)
        cart = active_trip.get("cart") if isinstance(active_trip.get("cart"), dict) else None
        results = _empty_travel_results()
        if cart is not None:
            results["cart"] = cart
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "package_option_selection"),
                "Here is your current cart.",
                results,
                next_action="navigate",
                next_path="/cart",
            )
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_option_selection"),
            "Your cart is empty.",
            results,
        )

    return None


def _looks_like_destination_set_flight_comparison(message: str) -> bool:
    normalized = _normalize_control_message(message)
    if not normalized:
        return False

    set_reference = any(
        phrase in normalized
        for phrase in [
            "of them",
            "of these",
            "to them",
            "to these",
            "these destinations",
            "recommended destinations",
            "the destinations",
            "which destination",
            "which one",
        ]
    )
    comparison_language = any(
        phrase in normalized
        for phrase in [
            "cheapest",
            "lowest price",
            "best price",
            "compare",
            "ticket",
            "tickets",
            "flight",
            "flights",
            "fly to",
        ]
    )
    explicit_destination_price_question = (
        "which destination" in normalized
        and any(phrase in normalized for phrase in ["cheapest", "lowest price"])
        and any(phrase in normalized for phrase in ["flight", "ticket", "fly to"])
    )
    return (set_reference and comparison_language) or explicit_destination_price_question


def _latest_destination_recommendations(
    session_id: int,
    db: Session,
) -> list[dict[str, Any]]:
    recommendations = load_session_memory(db, session_id).get("destination_recommendations")
    if not isinstance(recommendations, list):
        return []
    safe_recommendations: list[dict[str, Any]] = []
    for recommendation in recommendations:
        if not isinstance(recommendation, dict):
            continue
        code = str(recommendation.get("airportCode") or "").upper()
        if len(code) != 3:
            continue
        safe_recommendations.append({**recommendation, "airportCode": code})
    return safe_recommendations


def _resolve_destination_comparison_departure(
    message: str,
    active_trip: dict[str, Any],
) -> str | None:
    return _resolve_destination_followup_departure(message) or _active_trip_dates(
        active_trip
    ).get("departureDate")


def _resolve_destination_comparison_return(
    departure_iso: str,
    active_trip: dict[str, Any],
) -> str | None:
    nights = active_trip.get("tripDurationNights")
    if isinstance(nights, int) and nights > 0:
        return (date.fromisoformat(departure_iso) + timedelta(days=nights)).isoformat()

    dates = _active_trip_dates(active_trip)
    old_departure = dates.get("departureDate")
    old_return = dates.get("returnDate")
    if old_departure and old_return:
        try:
            duration = max(1, (date.fromisoformat(old_return) - date.fromisoformat(old_departure)).days)
        except ValueError:
            return old_return
        return (date.fromisoformat(departure_iso) + timedelta(days=duration)).isoformat()
    return None


def _build_flight_request_for_destination_recommendation(
    active_trip: dict[str, Any],
    destination: dict[str, Any],
    departure_iso: str,
    return_iso: str | None,
) -> FlightSearchRequest | None:
    origin = _active_trip_origin(active_trip)
    origin_code = str((origin or {}).get("airportCode") or "").upper()
    destination_code = str(destination.get("airportCode") or "").upper()
    if not origin_code or len(origin_code) != 3:
        return None
    if not destination_code or len(destination_code) != 3:
        return None
    if origin_code == destination_code:
        return None

    payload = {
        "searchType": "flight",
        "passengerCount": active_trip.get("passengerCount") or 1,
        "itinerary": {
            "tripType": "round_trip" if return_iso else "one_way",
            "origin": {
                "city": (origin or {}).get("city"),
                "code": origin_code,
            },
            "destination": {
                "city": destination.get("city"),
                "code": destination_code,
            },
            "dates": {"departure": departure_iso},
        },
        "preferences": {
            "cabinClass": active_trip.get("cabinClass") or "economy",
            "maxStops": None,
        },
    }
    if return_iso:
        payload["itinerary"]["dates"]["return"] = return_iso
    try:
        return FlightSearchRequest.model_validate(payload)
    except Exception as exc:
        LOGGER.warning(
            "Destination comparison flight request validation failed: %s",
            exc.__class__.__name__,
        )
        return None




def _execute_destination_set_flight_comparison_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    if not _looks_like_destination_set_flight_comparison(request.message):
        return None

    recommendations = _latest_destination_recommendations(session.id, db)
    if _active_context_type(session.id, db) != "destination" or not recommendations:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            "Please choose or request destination ideas first, then I can compare flights to them.",
        )

    active_trip = _active_trip_for_session(session.id, db)
    origin = _active_trip_origin(active_trip)
    origin_code = str((origin or {}).get("airportCode") or "").upper()
    if not origin_code:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            "Where are you travelling from?",
        )

    departure_iso = _resolve_destination_comparison_departure(request.message, active_trip)
    if departure_iso is None:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            "When do you want to depart?",
        )
    return_iso = _resolve_destination_comparison_return(departure_iso, active_trip)
    allowed_codes = {str(item.get("airportCode") or "").upper() for item in recommendations}
    _invalidate_dependent_state(
        db,
        session.id,
        "destination_set_flight_comparison",
        clear_flights=True,
        clear_hotels=False,
        clear_destinations=False,
    )

    ranked_flights: list[dict[str, Any]] = []
    missing_codes: list[str] = []
    search_requests: list[FlightSearchRequest] = []
    provider_names: set[str] = set()
    for destination in recommendations:
        flight_request = _build_flight_request_for_destination_recommendation(
            active_trip,
            destination,
            departure_iso,
            return_iso,
        )
        if flight_request is None:
            missing_codes.append(str(destination.get("airportCode") or "unknown"))
            continue

        request_destination = flight_request.itinerary.destination.code.upper()
        if (
            flight_request.itinerary.origin.code.upper() != origin_code
            or request_destination not in allowed_codes
            or flight_request.itinerary.dates.departure.isoformat() != departure_iso
        ):
            LOGGER.info(
                "Rejected destination comparison route mismatch: %s",
                {
                    "origin": flight_request.itinerary.origin.code,
                    "destination": request_destination,
                    "departureDate": flight_request.itinerary.dates.departure.isoformat(),
                    "allowedDestinations": sorted(allowed_codes),
                },
            )
            continue

        provider_response = _search_flights_with_fallback(flight_request)
        provider_names.add(provider_response.provider_used)
        search_requests.append(flight_request)
        normalised = _normalised_flight_dicts(provider_response)
        priced = [flight for flight in normalised if _flight_price(flight) is not None]
        if not priced:
            missing_codes.append(request_destination)
            continue
        cheapest_for_destination = min(priced, key=lambda flight: _flight_price(flight) or float("inf"))
        ranked_flights.append(cheapest_for_destination)

    ranked_flights.sort(key=lambda flight: _flight_price(flight) or float("inf"))
    for option_number, flight in enumerate(ranked_flights, start=1):
        flight["option_number"] = option_number
        flight["display_id"] = flight.get("display_id") or f"Offer {option_number}"

    results = _flight_results_for_chat(ranked_flights[:CHAT_FLIGHT_CARD_LIMIT])
    intent = _build_control_intent(request.message, "flight_search")
    active_trip["dates"] = {
        "departureDate": departure_iso,
        "returnDate": return_iso,
        "source": "destination_flight_comparison",
    }
    active_trip["requestedProducts"] = ["flights"]
    active_trip["status"] = "comparing_destination_flights"
    if ranked_flights:
        cheapest = ranked_flights[0]
        matched_destination = next(
            (
                item
                for item in recommendations
                if str(item.get("airportCode") or "").upper()
                == str(cheapest.get("destination") or "").upper()
            ),
            None,
        )
        if matched_destination:
            active_trip["selectedDestination"] = {
                "city": matched_destination.get("city"),
                "country": matched_destination.get("country"),
                "airportCode": matched_destination.get("airportCode"),
                "airportName": matched_destination.get("airportName"),
                "source": "flight_comparison_cheapest",
                "recommendationId": f"{matched_destination.get('airportCode')}:comparison",
            }
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)

    if ranked_flights and search_requests:
        provider_used = "duffel" if "duffel" in provider_names else "mock_fallback"
        comparison_response = FlightSearchResponse(
            provider_used=provider_used,
            flights=[
                NormalisedFlightResult.model_validate(flight)
                for flight in ranked_flights
            ],
            fallback_reason=(
                "Some destination flight searches used fallback results."
                if "mock_fallback" in provider_names
                else None
            ),
        )
        _store_latest_flight_results(
            session.id,
            intent,
            comparison_response,
            search_requests[0],
            db,
        )
        _set_last_tool_used(session.id, "compare_destination_flights", db)
        cheapest = ranked_flights[0]
        origin_text = _origin_message(origin or {"airportCode": origin_code})
        departure_label = _display_date_label(departure_iso) or departure_iso
        destination_name = cheapest.get("destination")
        if isinstance(active_trip.get("selectedDestination"), dict):
            destination_name = (
                active_trip["selectedDestination"].get("city")
                or active_trip["selectedDestination"].get("airportCode")
                or destination_name
            )
        reply = (
            f"I compared flights from {origin_text} to the recommended destinations "
            f"on {departure_label}. The cheapest option is {destination_name} "
            f"at {_format_price_for_reply(cheapest)}."
        )
        if missing_codes:
            reply += " No flight was found for " + ", ".join(sorted(set(missing_codes))) + " on that date."
        LOGGER.info(
            "Routing trace: %s",
            {
                "route": "destination_set_flight_comparison",
                "origin": origin_code,
                "departureDate": departure_iso,
                "returnDate": return_iso,
                "destinationsCompared": [
                    request_item.itinerary.destination.code.upper()
                    for request_item in search_requests
                ],
                "activeTripRevision": active_trip.get("revision"),
            },
        )
        return _tool_response(db, session, request, intent, reply, results=results)

    reply = (
        f"I compared flights from {origin_code} to the recommended destinations, "
        "but no flight options were returned for that date."
    )
    if missing_codes:
        reply += " No flight was found for " + ", ".join(sorted(set(missing_codes))) + "."
    return _tool_response(db, session, request, intent, reply, results=results)


def _looks_like_cheapest_day_message(message: str) -> bool:
    normalized = _normalize_control_message(message)
    if not normalized:
        return False
    if _resolve_month_range_from_message(message) and any(
        phrase in normalized
        for phrase in [
            "cheapest",
            "cheaper",
            "lowest price",
            "anything",
            "what about",
            "check",
            "try",
        ]
    ):
        return True
    return any(
        phrase in normalized
        for phrase in [
            "cheapest day",
            "cheaper day",
            "which day is cheaper",
            "which day is cheapest",
            "when is the cheapest",
            "cheapest ticket next week",
            "lowest price next week",
        ]
    )


def _looks_like_existing_cheaper_day_followup(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "is there a cheaper day",
            "any cheaper day",
            "anything cheaper",
            "is there anything cheaper",
        ]
    )


def _resolve_explicit_departure_override(
    message: str,
    active_trip: dict[str, Any] | None = None,
) -> str | None:
    normalized = _normalize_control_message(message)
    if not any(
        phrase in normalized
        for phrase in ["what about", "try", "check", "cheapest"]
    ):
        return None
    day_only_base = (
        _active_trip_day_only_base_date(active_trip)
        if isinstance(active_trip, dict)
        else None
    )
    return (
        _resolve_named_month_date_from_message(message)
        or _resolve_day_only_followup(message, base_date=day_only_base)
        or resolve_future_date(message, today=today_utc_date())
    )


def _comparison_cheapest_result(comparison: dict[str, Any]) -> dict[str, Any] | None:
    cheapest = comparison.get("cheapestResult")
    if isinstance(cheapest, dict):
        return cheapest
    results = comparison.get("results")
    if not isinstance(results, list):
        return None
    priced = [
        item
        for item in results
        if isinstance(item, dict) and item.get("cheapestPrice") is not None
    ]
    if not priced:
        return None
    return min(
        priced,
        key=lambda item: float(item.get("cheapestPrice") or float("inf")),
    )




def _answer_existing_date_price_comparison(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    active_trip: dict[str, Any],
) -> ChatResponse | None:
    if not _looks_like_existing_cheaper_day_followup(request.message):
        return None
    comparison = _date_comparison_from_active_trip(active_trip)
    if comparison is None:
        return None
    cheapest = _comparison_cheapest_result(comparison)
    if cheapest is None:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            "I already checked that range, but no priced options were returned. Do you want me to check the following week as well?",
        )

    destination = _active_trip_destination(active_trip) or {}
    departure = cheapest.get("departureDate")
    return_date = cheapest.get("returnDate")
    reply = (
        "I already compared the checked dates. The cheapest departure I found "
        f"for {destination.get('city') or destination.get('airportCode') or 'that destination'} "
        f"is {_display_date_label(departure) or departure}, returning "
        f"{_display_date_label(return_date) or return_date}, from "
        f"{_format_comparison_price(cheapest)}. Do you want me to check the following week as well?"
    )
    flight = cheapest.get("offerSummary")
    results = _flight_results_for_chat([flight]) if isinstance(flight, dict) else None
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "flight_search"),
        reply,
        results=results,
    )


def _search_active_trip_flight_for_departures(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    active_trip: dict[str, Any],
    departure_dates: list[str],
    *,
    range_type: str = "custom",
    range_label: str | None = None,
) -> ChatResponse:
    destination = _active_trip_destination(active_trip)
    origin = _active_trip_origin(active_trip)
    if not destination:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            "Please choose a destination first.",
        )
    if not origin or not origin.get("airportCode"):
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            "Where are you travelling from?",
        )

    origin_code = str(origin.get("airportCode") or "").upper()
    destination_code = str(destination.get("airportCode") or "").upper()
    nights = active_trip.get("tripDurationNights")
    if not isinstance(nights, int) or nights < 1:
        nights = 3

    if len(departure_dates) > MAX_FLEXIBLE_DATE_CHECKS:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            (
                f"That date range has {len(departure_dates)} possible departure days. "
                "Please narrow it to a shorter date range so I can compare prices safely."
            ),
        )

    _invalidate_dependent_state(
        db,
        session.id,
        "active_trip_date_price_comparison",
        clear_flights=True,
        clear_hotels=False,
        clear_destinations=False,
    )

    comparison_results: list[dict[str, Any]] = []
    ranked_flights: list[dict[str, Any]] = []
    search_requests: list[FlightSearchRequest] = []
    provider_names: set[str] = set()
    for departure_iso in departure_dates:
        return_iso = (date.fromisoformat(departure_iso) + timedelta(days=nights)).isoformat()
        flight_request = _build_flight_request_for_destination_recommendation(
            active_trip,
            destination,
            departure_iso,
            return_iso,
        )
        if flight_request is None:
            continue
        if (
            flight_request.itinerary.origin.code.upper() != origin_code
            or flight_request.itinerary.destination.code.upper() != destination_code
            or flight_request.itinerary.dates.departure.isoformat() != departure_iso
        ):
            LOGGER.info(
                "Rejected cheapest-day route mismatch: %s",
                {
                    "origin": flight_request.itinerary.origin.code,
                    "destination": flight_request.itinerary.destination.code,
                    "departureDate": flight_request.itinerary.dates.departure.isoformat(),
                    "expectedOrigin": origin_code,
                    "expectedDestination": destination_code,
                },
            )
            continue

        provider_response = _search_flights_with_fallback(flight_request)
        provider_names.add(provider_response.provider_used)
        search_requests.append(flight_request)
        normalised = _normalised_flight_dicts(provider_response)
        priced = [flight for flight in normalised if _flight_price(flight) is not None]
        if not priced:
            comparison_results.append(
                {
                    "departureDate": departure_iso,
                    "returnDate": return_iso,
                    "cheapestPrice": None,
                    "currency": None,
                    "offerSummary": None,
                }
            )
            continue
        cheapest_for_day = min(priced, key=lambda flight: _flight_price(flight) or float("inf"))
        ranked_flights.append(cheapest_for_day)
        comparison_results.append(
            {
                "departureDate": departure_iso,
                "returnDate": return_iso,
                "cheapestPrice": _flight_price(cheapest_for_day),
                "currency": cheapest_for_day.get("currency"),
                "offerSummary": cheapest_for_day,
            }
        )

    ranked_flights.sort(key=lambda flight: _flight_price(flight) or float("inf"))
    for option_number, flight in enumerate(ranked_flights, start=1):
        flight["option_number"] = option_number
        flight["display_id"] = flight.get("display_id") or f"Offer {option_number}"

    priced_results = [
        result
        for result in comparison_results
        if result.get("cheapestPrice") is not None
    ]
    cheapest_result = (
        min(priced_results, key=lambda item: float(item.get("cheapestPrice") or float("inf")))
        if priced_results
        else None
    )
    active_trip["dates"] = {
        "departureDate": (
            str(cheapest_result.get("departureDate"))
            if cheapest_result
            else departure_dates[0]
            if departure_dates
            else None
        ),
        "returnDate": (
            str(cheapest_result.get("returnDate"))
            if cheapest_result
            else (
                date.fromisoformat(departure_dates[0]) + timedelta(days=nights)
            ).isoformat()
            if departure_dates
            else None
        ),
        "source": "date_price_comparison",
    }
    _clear_active_trip_package_state(active_trip)
    active_trip["requestedProducts"] = ["flights"]
    active_trip["status"] = "date_price_compared"
    active_trip["latestDatePriceComparison"] = {
        "rangeType": range_type,
        "rangeLabel": range_label,
        "startDate": departure_dates[0] if departure_dates else None,
        "endDate": departure_dates[-1] if departure_dates else None,
        "originAirportCode": origin_code,
        "destinationAirportCode": destination_code,
        "checkedDateRange": {
            "start": departure_dates[0] if departure_dates else None,
            "end": departure_dates[-1] if departure_dates else None,
        },
        "tripDurationNights": nights,
        "results": comparison_results,
        "cheapestResult": cheapest_result,
    }
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)

    intent = _build_control_intent(request.message, "flight_search")
    if ranked_flights and search_requests:
        provider_used = "duffel" if "duffel" in provider_names else "mock_fallback"
        provider_response = FlightSearchResponse(
            provider_used=provider_used,
            flights=[NormalisedFlightResult.model_validate(flight) for flight in ranked_flights],
            fallback_reason=(
                "Some cheapest-day searches used fallback results."
                if "mock_fallback" in provider_names
                else None
            ),
        )
        _store_latest_flight_results(
            session.id,
            intent,
            provider_response,
            search_requests[0],
            db,
        )
        _set_last_tool_used(session.id, "compare_cheapest_day_flights", db)

    results = _flight_results_for_chat(ranked_flights[:CHAT_FLIGHT_CARD_LIMIT])
    destination_label = (
        f"{destination.get('city')} ({destination_code})"
        if destination.get("city")
        else destination_code
    )
    origin_label = _origin_message(origin)
    if cheapest_result is None:
        reply = (
            f"I compared flights from {origin_label} to {destination_label}, "
            "but no priced options were returned for those dates."
        )
    elif len(departure_dates) == 1:
        reply = (
            f"I checked {destination.get('city') or destination_code} for "
            f"{_display_date_label(cheapest_result.get('departureDate')) or cheapest_result.get('departureDate')} "
            f"to {_display_date_label(cheapest_result.get('returnDate')) or cheapest_result.get('returnDate')}. "
            f"The cheapest option I found is from {_format_comparison_price(cheapest_result)}."
        )
    elif range_type == "month" and range_label:
        reply = (
            f"I compared flights from {origin_label} to {destination_label} "
            f"for {nights}-night trips in {range_label}. The cheapest departure day is "
            f"{_display_date_label(cheapest_result.get('departureDate')) or cheapest_result.get('departureDate')}, "
            f"from {_format_comparison_price(cheapest_result)}."
        )
    else:
        reply = (
            f"I compared flights from {origin_label} to {destination_label} "
            f"for {nights}-night trips {range_label or 'across the checked dates'}. "
            "The cheapest departure day is "
            f"{_display_date_label(cheapest_result.get('departureDate')) or cheapest_result.get('departureDate')}, "
            f"from {_format_comparison_price(cheapest_result)}."
        )

    LOGGER.info(
        "Routing trace: %s",
        {
            "route": "active_trip_cheapest_day_comparison",
            "origin": origin_code,
            "destination": destination_code,
            "rangeType": range_type,
            "rangeLabel": range_label,
            "departureDates": departure_dates,
            "tripDurationNights": nights,
            "activeTripRevision": active_trip.get("revision"),
        },
    )
    return _tool_response(db, session, request, intent, reply, results=results)


def _execute_cheapest_day_for_selected_destination_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    existing_answer = _answer_existing_date_price_comparison(
        db,
        session,
        request,
        active_trip,
    )
    if existing_answer is not None:
        return existing_answer

    explicit_departure = _resolve_explicit_departure_override(request.message, active_trip)
    wants_cheapest_day = _looks_like_cheapest_day_message(request.message)
    if explicit_departure is None and not wants_cheapest_day:
        return None

    recommendations = _latest_destination_recommendations(session.id, db)
    selected = resolve_destination_selection(request.message, recommendations)
    if selected is not None:
        active_trip = _select_destination_in_active_trip(
            db,
            session.id,
            selected,
            ["flights"],
        )
    elif _active_trip_destination(active_trip) is None:
        explicit_destination = _resolve_explicit_destination_from_message(request.message)
        if explicit_destination is not None:
            active_trip = _set_explicit_destination_active_trip(
                db,
                session.id,
                explicit_destination,
                request.message,
            )
            active_trip["requestedProducts"] = ["flights"]
            save_active_trip(db, session.id, active_trip)

    if _active_trip_destination(active_trip) is None:
        if recommendations:
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "flight_search"),
                "First choose one of the destination ideas, then I can compare the cheapest day next week for that destination.",
            )
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            "Please tell me which destination you want to check, or ask for destination ideas first.",
        )

    if explicit_departure is not None:
        departure_dates = [explicit_departure]
        range_type = "custom"
        range_label = None
    else:
        month_range = (
            _resolve_month_range_from_message(request.message)
            or _resolve_next_month_range_from_message(request.message)
        )
        if month_range:
            departure_dates = list(month_range["departureDates"])
            range_type = str(month_range["rangeType"])
            range_label = str(month_range["rangeLabel"])
        elif "next week" in _normalize_control_message(request.message):
            departure_dates = _next_week_departure_dates()
            range_type = "week"
            range_label = "next week"
        else:
            comparison = _date_comparison_from_active_trip(active_trip)
            if comparison is not None:
                return _answer_existing_date_price_comparison(
                    db,
                    session,
                    request,
                    active_trip,
                )
            destination = _active_trip_destination(active_trip) or {}
            destination_label = destination.get("city") or destination.get("airportCode") or "that destination"
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "flight_search"),
                f"Which month or week should I check for the cheapest day to {destination_label}?",
            )

    return _search_active_trip_flight_for_departures(
        db,
        session,
        request,
        active_trip,
        departure_dates,
        range_type=range_type,
        range_label=range_label,
    )


def _execute_destination_selection_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    *,
    requested_products: list[str] | None = None,
) -> ChatResponse | None:
    memory = load_session_memory(db, session.id)
    recommendations = memory.get("destination_recommendations")
    if not isinstance(recommendations, list):
        recommendations = []
    if not recommendations and _active_context_type(session.id, db) == "destination":
        LOGGER.warning(
            "Destination selection attempted without persisted recommendations: %s",
            {"session_id": session.id, "message": request.message[:120]},
        )
    option_number = _destination_option_number_from_message(request.message)
    if option_number is not None and _active_context_type(session.id, db) != "destination":
        return None
    selected = resolve_destination_selection(request.message, recommendations)
    if selected is None:
        return None

    products = requested_products or _requested_products_from_message(request.message)
    _select_destination_in_active_trip(db, session.id, selected, products)
    if _looks_like_package_request(request.message):
        package_response = _execute_package_request_if_possible(db, session, request)
        if package_response is not None:
            return package_response
    return _execute_active_trip_product_search(db, session, request, products)


def _execute_active_trip_product_request_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    normalized = _normalize_control_message(request.message)
    products = _requested_products_from_message(
        request.message,
        default_when_selected=False,
    )
    if not products:
        return None
    if not any(
        phrase in normalized
        for phrase in [
            "what about",
            "there",
            "that place",
            "this place",
            "for it",
            "for there",
            "to it",
        ]
    ):
        return None
    active_trip = _active_trip_for_session(session.id, db)
    if _active_trip_destination(active_trip) is None:
        return None
    active_trip["requestedProducts"] = products
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    return _execute_active_trip_product_search(db, session, request, products)


def _build_llama_flight_intent(
    message: str,
    flight_search: FlightSearchRequest,
) -> dict[str, Any]:
    origin = flight_search.itinerary.origin
    destination = flight_search.itinerary.destination
    return {
        "intent_type": "flight_search",
        "destination": destination.city or destination.code,
        "booking_reference": None,
        "departure_city": origin.city or origin.code,
        "travelers": flight_search.passenger_count,
        "budget_level": "medium",
        "message_summary": message[:120],
    }


def _flight_search_results_for_chat(
    response: FlightSearchResponse,
) -> dict[str, Any]:
    return _flight_results_for_chat(
        _normalised_flight_dicts(response),
        limit=CHAT_FLIGHT_CARD_LIMIT,
    )


def _build_llama_flight_reply(
    flight_search: FlightSearchRequest,
    provider_response: FlightSearchResponse,
) -> str:
    origin = flight_search.itinerary.origin.code.upper()
    destination = flight_search.itinerary.destination.code.upper()
    passenger_label = (
        "1 passenger"
        if flight_search.passenger_count == 1
        else f"{flight_search.passenger_count} passengers"
    )

    if provider_response.provider_used == "duffel":
        return (
            f"I found real {flight_search.preferences.cabin_class.replace('_', ' ')} "
            f"flight options from {origin} to {destination} for {passenger_label} "
            "using Duffel."
        )

    if flight_search.preferences.cabin_class != "economy":
        return (
            f"I found {flight_search.preferences.cabin_class.replace('_', ' ')} "
            f"flight options from {origin} to {destination}."
        )

    return (
        f"I found flight options from {origin} to {destination}."
    )


def _create_user_session(db: Session, user: User) -> ChatSession:
    session = ChatSession(user_id=user.id, title="Travel chat session")
    db.add(session)
    db.flush()
    return session


def _get_or_create_user_session(
    db: Session,
    user: User,
    *,
    reuse_existing: bool = False,
) -> ChatSession:
    """Return a user chat session, creating a fresh one by default."""

    if reuse_existing:
        session = (
            db.query(ChatSession)
            .filter(ChatSession.user_id == user.id)
            .order_by(ChatSession.id.desc())
            .first()
        )
        if session is not None:
            return session
    else:
        sessions = (
            db.query(ChatSession)
            .filter(ChatSession.user_id == user.id)
            .order_by(ChatSession.id.desc())
            .all()
        )
        for session in sessions:
            if not session.messages and session.state is None:
                return session
    return _create_user_session(db, user)


def _get_or_create_demo_context(
    db: Session,
    *,
    reuse_existing: bool = False,
) -> tuple[User, ChatSession]:
    """Compatibility context for public chat requests without a Bearer token."""

    user = db.query(User).filter(User.email == DEMO_USER_EMAIL).first()
    if user is None:
        user = User(
            display_name="Demo User",
            full_name="Demo User",
            email=DEMO_USER_EMAIL,
            password_hash="",
        )
        db.add(user)
        db.flush()

    return user, _get_or_create_user_session(
        db,
        user,
        reuse_existing=reuse_existing,
    )


def _resolve_chat_context(
    db: Session,
    request: ChatRequest,
    current_user: User | None,
) -> tuple[User, ChatSession]:
    requested_session: ChatSession | None = None
    if request.session_id is not None:
        requested_session = (
            db.query(ChatSession)
            .filter(ChatSession.id == request.session_id)
            .first()
        )

    if current_user is None:
        demo_user = db.query(User).filter(User.email == DEMO_USER_EMAIL).first()
        if demo_user and requested_session and requested_session.user_id == demo_user.id:
            return demo_user, requested_session
        demo_user, demo_session = _get_or_create_demo_context(db)
        return demo_user, demo_session

    if requested_session:
        if requested_session.user_id == current_user.id:
            return current_user, requested_session
        demo_user = db.query(User).filter(User.email == DEMO_USER_EMAIL).first()
        if demo_user and requested_session.user_id == demo_user.id:
            requested_session.user_id = current_user.id
            db.flush()
            return current_user, requested_session

    return current_user, _get_or_create_user_session(db, current_user)


def _save_message(
    db: Session,
    session_id: int,
    sender: str,
    content: str,
    intent: dict[str, Any] | None = None,
) -> Message:
    message = Message(
        session_id=session_id,
        sender=sender,
        content=content,
        intent_type=intent.get("intent_type") if intent else None,
        intent_data=intent,
    )
    db.add(message)
    db.flush()
    if isinstance(db, Session):
        append_recent_fact(
            db,
            session_id,
            {"sender": sender, "text": content},
        )
    return message


def _generate_booking_reference(db: Session) -> str:
    for _ in range(20):
        reference = f"TA{randbelow(900000) + 100000}"
        exists = (
            db.query(Booking)
            .filter(Booking.booking_reference == reference)
            .first()
            is not None
        )
        if not exists:
            return reference

    return f"TA{randbelow(900000000) + 100000000}"


def _booking_to_details(booking: Booking) -> dict[str, Any]:
    details = _load_booking_details_json(booking.details_json)
    public_details = _public_booking_details(details)
    total_price = details.get("totalPrice")
    if total_price is None:
        total_price = details.get("total_price")
    if total_price is None and booking.estimated_price is not None:
        total_price = float(booking.estimated_price)
    return {
        "id": booking.id,
        "reference": booking.booking_reference,
        "status": booking.status,
        "destination": booking.destination,
        "summary": booking.summary,
        "flight_id": booking.flight_id,
        "hotel_id": booking.hotel_id,
        "estimated_price": booking.estimated_price,
        "currency": booking.currency,
        "booking_type": booking.booking_type,
        "created_at": booking.created_at,
        "totalPrice": total_price,
        "packageOption": details.get("package") or details.get("packageOption"),
        "packageSummary": details.get("packageSummary"),
        "flightTicket": public_details.get("flightTicket"),
        "hotelVoucher": public_details.get("hotelVoucher"),
        "checkoutDetails": public_details if public_details else None,
        "detailsAvailable": bool(details),
    }


def _load_booking_details_json(details_json: str | None) -> dict[str, Any]:
    if not details_json:
        return {}
    try:
        details = json.loads(details_json)
    except (TypeError, json.JSONDecodeError):
        return {}
    return details if isinstance(details, dict) else {}


def _public_travel_document(document: Any) -> dict[str, Any] | None:
    if not isinstance(document, dict):
        return None
    public_document = {
        key: value
        for key, value in document.items()
        if str(key).lower() != "disclaimer"
    }
    if public_document:
        ticket_number = public_document.get("ticketNumber")
        if isinstance(ticket_number, str) and ticket_number.startswith(f"S{'IM'}-FLT-"):
            public_document["ticketNumber"] = ticket_number.replace(f"S{'IM'}-FLT-", "VAI-FLT-", 1)
        voucher_number = public_document.get("voucherNumber")
        if isinstance(voucher_number, str) and voucher_number.startswith(f"S{'IM'}-HTL-"):
            public_document["voucherNumber"] = voucher_number.replace(f"S{'IM'}-HTL-", "VAI-HTL-", 1)
        public_document["status"] = "confirmed"
    return public_document or None


def _public_booking_details(details: dict[str, Any]) -> dict[str, Any]:
    if not details:
        return {}
    public_details = deepcopy(details)
    flight_ticket = _public_travel_document(public_details.get("flightTicket"))
    hotel_voucher = _public_travel_document(public_details.get("hotelVoucher"))
    if flight_ticket is not None:
        public_details["flightTicket"] = flight_ticket
    else:
        public_details.pop("flightTicket", None)
    if hotel_voucher is not None:
        public_details["hotelVoucher"] = hotel_voucher
    else:
        public_details.pop("hotelVoucher", None)
    return public_details


def _create_booking_record(
    db: Session,
    user_id: int,
    session_id: int | None,
    destination: str,
    summary: str,
    flight_id: str | None = None,
    hotel_id: str | None = None,
    estimated_price: int | None = None,
    currency: str = "USD",
    booking_type: str = "manual",
    details_json: str | None = None,
) -> Booking:
    booking = Booking(
        booking_reference=_generate_booking_reference(db),
        user_id=user_id,
        session_id=session_id,
        destination=destination,
        status="Confirmed",
        summary=summary,
        flight_id=flight_id,
        hotel_id=hotel_id,
        estimated_price=estimated_price,
        currency=currency,
        booking_type=booking_type,
        details_json=details_json,
    )
    db.add(booking)
    db.flush()
    return booking


def _search_booking_from_database(
    db: Session,
    booking_reference: str | None,
    user_id: int | None = None,
) -> dict[str, Any]:
    results = _empty_travel_results()
    if not booking_reference:
        return results

    query = db.query(Booking).filter(Booking.booking_reference == booking_reference.upper())
    if user_id is not None:
        query = query.filter(Booking.user_id == user_id)
    booking = query.first()
    if booking:
        results["booking"] = _booking_to_details(booking)

    return results


def _create_booking_from_package(
    db: Session,
    user: User,
    session: ChatSession,
    intent: dict[str, Any],
    package: dict[str, Any],
) -> Booking:
    destination = str(intent.get("destination") or package["destination"])

    return _create_booking_record(
        db=db,
        user_id=user.id,
        session_id=session.id,
        destination=destination,
        summary=str(package["summary"]),
        flight_id=str(package["flight_id"]),
        hotel_id=str(package["hotel_id"]),
        estimated_price=int(package["estimated_price"]),
        currency=str(package["currency"]),
    )


def _flight_booking_price(flight: dict[str, Any]) -> int | None:
    price = _flight_price(flight)
    return round(price) if price is not None else None


def _create_booking_from_flight(
    db: Session,
    user: User,
    session: ChatSession,
    flight: dict[str, Any],
) -> Booking:
    origin = str(flight.get("origin") or "Unknown origin")
    destination = str(flight.get("destination") or "Unknown destination")
    airline = str(flight.get("airline") or "Selected airline")
    cabin_class = str(
        flight.get("cabin_class_label")
        or _cabin_class_label(flight.get("cabin_class"))
    )
    passenger_count = int(flight.get("passenger_count") or 1)
    passenger_label = (
        "1 passenger" if passenger_count == 1 else f"{passenger_count} passengers"
    )
    stops_label = str(flight.get("stops_label") or _stops_label(flight.get("stops")))
    departure_date = str(
        flight.get("outbound_departure_date") or flight.get("departure_date") or "unknown date"
    )
    departure_time = flight.get("outbound_departure_time")
    return_date = flight.get("return_departure_date") or flight.get("return_date")
    return_time = flight.get("return_departure_time")
    price = flight.get("price")
    currency = str(flight.get("currency") or "USD")
    summary_parts = [
        f"Selected flight: {airline} {origin} to {destination}",
        cabin_class,
        passenger_label,
        stops_label.lower(),
        f"departing {departure_date}" + (f" at {departure_time}" if departure_time else ""),
    ]
    if return_date:
        summary_parts.append(
            f"returning {return_date}" + (f" at {return_time}" if return_time else "")
        )
    if price is not None:
        summary_parts.append(f"{currency} {price}")
    summary = ", ".join(summary_parts) + "."

    return _create_booking_record(
        db=db,
        user_id=user.id,
        session_id=session.id,
        destination=destination,
        summary=summary,
        flight_id=str(flight.get("id") or ""),
        estimated_price=_flight_booking_price(flight),
        currency=str(flight.get("currency") or "USD"),
    )


def _create_booking_from_hotel(
    db: Session,
    user: User,
    session: ChatSession,
    hotel: dict[str, Any],
) -> Booking:
    hotel_name = str(hotel.get("hotel_name") or "Selected hotel")
    location = str(hotel.get("location") or "Selected destination")
    check_in = str(hotel.get("check_in_date") or "unknown date")
    check_out = str(hotel.get("check_out_date") or "unknown date")
    guest_count = int(hotel.get("guest_count") or 1)
    room_count = int(hotel.get("room_count") or 1)
    guest_label = "1 guest" if guest_count == 1 else f"{guest_count} guests"
    room_label = "1 room" if room_count == 1 else f"{room_count} rooms"
    price = _hotel_price(hotel)
    currency = str(hotel.get("currency") or "GBP")
    room_name = hotel.get("room_name")
    board_type = hotel.get("board_type")
    summary_parts = [
        f"Selected hotel: {hotel_name} in {location}",
        f"{check_in} to {check_out}",
        f"{hotel.get('nights') or 1} nights",
        guest_label,
        room_label,
    ]
    if room_name:
        summary_parts.append(str(room_name))
    if board_type:
        summary_parts.append(str(board_type))
    if price is not None:
        summary_parts.append(f"{currency} {price:.2f}")

    return _create_booking_record(
        db=db,
        user_id=user.id,
        session_id=session.id,
        destination=location[:120],
        summary=", ".join(summary_parts) + ".",
        hotel_id=str(hotel.get("id") or "")[:40],
        estimated_price=round(price) if price is not None else None,
        currency=currency,
    )


def _store_pending_booking(
    session_id: int,
    intent: dict[str, Any],
    results: dict[str, Any],
    selected_option_number: int | None = None,
) -> None:
    PENDING_BOOKINGS[session_id] = {
        "intent": deepcopy(intent),
        "results": deepcopy(results),
        "current_option_number": 1,
        "selected_option_number": selected_option_number,
    }


def _clear_pending_booking(session_id: int) -> None:
    PENDING_BOOKINGS.pop(session_id, None)


def _find_package_by_option(
    packages: list[dict[str, Any]],
    option_number: int | None,
) -> dict[str, Any] | None:
    if option_number is None:
        return None

    for package in packages:
        if package.get("option_number") == option_number:
            return package

    return None


def _results_for_packages(
    base_results: dict[str, Any],
    packages: list[dict[str, Any]],
) -> dict[str, Any]:
    flight_ids = {package["flight_id"] for package in packages}
    hotel_ids = {package["hotel_id"] for package in packages}

    return {
        **_empty_travel_results(),
        "flights": [
            flight for flight in base_results["flights"] if flight["id"] in flight_ids
        ],
        "hotels": [
            hotel for hotel in base_results["hotels"] if hotel["id"] in hotel_ids
        ],
        "packages": packages,
    }


def _build_mock_reply(
    intent: dict,
    results: dict,
    requires_confirmation: bool = False,
) -> str:
    intent_type = intent["intent_type"]
    destination = intent.get("destination") or "the requested destination"

    if intent_type == "flight_search":
        flight_count = len(results["flights"])
        if flight_count:
            return f"I found {flight_count} flight option(s) for {destination}."
        return f"I could not find matching flight options for {destination}."

    if intent_type == "hotel_search":
        hotel_count = len(results["hotels"])
        return f"I found {hotel_count} hotel option(s) in {destination}."

    if intent_type == "package_search":
        if requires_confirmation and results["packages"]:
            package_count = len(results["packages"])
            if package_count == 1:
                return (
                    f"I found this package for {destination}. Please review the "
                    "flight and hotel details. Would you like to confirm this booking?"
                )

            return (
                f"I found {package_count} package options for {destination}. "
                "Please review them, then say something like 'select option 2'. "
                "You can also ask to show cheaper options."
            )

        package_count = len(results["packages"])
        if package_count == 0:
            return (
                f"I could not find matching flight and hotel options for "
                f"{destination}."
            )

        return (
            f"I found {package_count} package option(s) for {destination}, "
            "including flight and hotel data."
        )

    if intent_type == "booking_confirmation":
        booking = results.get("booking")
        if booking:
            return (
                "Your booking has been confirmed. Your reference is "
                f"{booking['reference']}."
            )
        return "I do not have a pending booking to confirm."

    if intent_type == "booking_cancellation":
        return "No problem. I have not created the booking."

    if intent_type == "booking_retrieval":
        booking_reference = intent.get("booking_reference")
        if not booking_reference:
            return "Please provide a booking reference, such as ABC123."

        booking = results.get("booking")
        if booking:
            return (
                f"Booking {booking_reference} is {booking['status']} "
                f"for {booking['destination']}."
            )
        return f"I could not find a mock booking for reference {booking_reference}."

    return (
        "Hello, I'm The Voyager. I can help you search flights and hotels, "
        "compare options, check availability, prepare bookings for confirmation, "
        "and guide you through Voyage AI's cart and checkout."
    )


def _handle_confirmation_message(
    db: Session,
    user: User,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    intent = _build_control_intent(request.message, "booking_confirmation")
    _save_message(db, session.id, "user", request.message, intent)

    travel_results = _empty_travel_results()
    selected_flight = _selected_pending_flight(session.id, db)
    selected_hotel = _selected_pending_hotel(session.id, db)
    pending_booking = PENDING_BOOKINGS.get(session.id)
    if selected_hotel is not None:
        booking = _create_booking_from_hotel(db, user, session, selected_hotel)
        travel_results = _hotel_results_for_chat([selected_hotel])
        travel_results["booking"] = _booking_to_details(booking)
        _clear_pending_hotel_selection(session.id, db)
        _clear_current_hotel_option(session.id, db)
        active_trip = _active_trip_for_session(session.id, db)
        active_trip["selectedHotelOption"] = None
        active_trip["latestVisibleResultType"] = "booking"
        active_trip["status"] = "booking_confirmed"
        active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
        save_active_trip(db, session.id, active_trip)
        assistant_message = (
            "Your selected hotel booking has been confirmed. Your reference is "
            f"{booking.booking_reference}."
        )
    elif selected_flight is not None:
        active_trip = _active_trip_for_session(session.id, db)
        selected = _selected_flight_from_active_trip(active_trip)
        option_number = selected[0] if selected is not None else (
            _option_number_for_flight(session.id, selected_flight.get("id"), db) or 1
        )
        cart = active_trip.get("cart") if isinstance(active_trip.get("cart"), dict) else None
        if _cart_contains_flight(cart, selected_flight):
            assistant_message = "That flight is already in your cart. You can continue to checkout."
        else:
            cart = _flight_cart_summary(selected_flight, option_number, session.id, _flight_price)
            assistant_message = (
                "Great - I added your selected flight to the cart. "
                "You can now continue to checkout."
            )
        travel_results = _flight_results_for_chat([selected_flight])
        travel_results["cart"] = cart
        _clear_pending_flight_selection(session.id, db)
        active_trip = _active_trip_for_session(session.id, db)
        active_trip["cart"] = cart
        active_trip["cartStatus"] = "active"
        active_trip["latestCartId"] = cart.get("cartId") if isinstance(cart, dict) else None
        active_trip["latestVisibleResultType"] = "cart"
        active_trip["status"] = "flight_in_cart"
        active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
        save_active_trip(db, session.id, active_trip)
        _set_last_tool_used(session.id, "add_flight_to_cart", db)
    elif pending_booking:
        package = _find_package_by_option(
            pending_booking["results"]["packages"],
            pending_booking.get("selected_option_number"),
        )
        if package is None:
            travel_results = _results_for_packages(
                pending_booking["results"],
                pending_booking["results"]["packages"],
            )
            travel_results["suggestions"] = [
                "Please choose an option first, such as select option 2.",
                "You can also ask to show cheaper options.",
            ]
            assistant_message = (
                "Please choose a package option before I confirm the booking."
            )
            _save_message(db, session.id, "assistant", assistant_message)
            db.commit()

            return ChatResponse(
                reply=assistant_message,
                intent=intent,
                results=travel_results,
                requires_confirmation=True,
                pending_action=PENDING_SELECTION_ACTION,
            )

        booking = _create_booking_from_package(
            db,
            user,
            session,
            pending_booking["intent"],
            package,
        )
        travel_results["booking"] = _booking_to_details(booking)
        _clear_pending_booking(session.id)
    else:
        travel_results["suggestions"] = [
            "Ask me to book a trip to Rome first.",
            "Review the package option, then confirm it.",
        ]

    if selected_flight is None and selected_hotel is None:
        assistant_message = _build_mock_reply(intent, travel_results)
    _save_message(db, session.id, "assistant", assistant_message)
    db.commit()

    return ChatResponse(
        reply=assistant_message,
        intent=intent,
        results=travel_results,
        requires_confirmation=False,
        pending_action=None,
        sessionId=session.id,
        nextAction="navigate" if selected_flight is not None else None,
        nextPath="/cart" if selected_flight is not None else None,
    )


def _handle_cancellation_message(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    intent = _build_control_intent(request.message, "booking_cancellation")
    _save_message(db, session.id, "user", request.message, intent)
    _clear_pending_booking(session.id)
    _clear_pending_flight_selection(session.id, db)
    _clear_pending_hotel_selection(session.id, db)
    active_trip = _active_trip_for_session(session.id, db)
    if isinstance(active_trip.get("selectedHotelOption"), dict):
        active_trip["selectedHotelOption"] = None
        if active_trip.get("latestVisibleResultType") == "hotel_selected":
            active_trip["latestVisibleResultType"] = "hotels"
        active_trip["status"] = "hotel_selection_cancelled"
        active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
        save_active_trip(db, session.id, active_trip)

    travel_results = _empty_travel_results()
    assistant_message = _build_mock_reply(intent, travel_results)
    _save_message(db, session.id, "assistant", assistant_message)
    db.commit()

    return ChatResponse(
        reply=assistant_message,
        intent=intent,
        results=travel_results,
        requires_confirmation=False,
        pending_action=None,
    )


def _flight_follow_up_intent(
    session_id: int,
    message: str,
    db: Session | None = None,
) -> dict[str, Any]:
    memory = _latest_flight_memory(session_id, db)
    if memory is None:
        return _build_control_intent(message, "flight_search")

    stored_intent = dict(memory["intent"])
    stored_intent["message_summary"] = message[:120]
    return stored_intent


def _handle_flight_memory_action(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    action: str,
    option_number: int | None = None,
) -> ChatResponse:
    intent = _flight_follow_up_intent(session.id, request.message, db)
    _save_message(db, session.id, "user", request.message, intent)

    memory = _latest_flight_memory(session.id, db)
    if memory is None:
        assistant_message = "Please search for flights first."
        _save_message(db, session.id, "assistant", assistant_message)
        db.commit()
        return ChatResponse(
            reply=assistant_message,
            intent=intent,
            results=_empty_travel_results(),
            requires_confirmation=False,
            pending_action=None,
        )

    requires_confirmation = False
    pending_action = None

    if action == "book_cheapest_flight":
        match = _cheapest_flight(session.id, db)
        if match is None:
            assistant_message = "I could not identify a cheapest flight option."
            travel_results = _flight_results_for_chat(memory["flights"])
        else:
            selected_option, selected_flight = match
            _set_current_flight_option(session.id, selected_option, db)
            _select_pending_flight(session.id, selected_flight, db)
            travel_results = _flight_results_for_chat([selected_flight])
            assistant_message = (
                f"I selected the cheapest flight, option {selected_option}. "
                "Would you like to add it to your cart and continue to checkout?"
            )
    elif action == "select_flight_option":
        if option_number is None:
            assistant_message = "Please specify a flight option number."
            travel_results = _flight_results_for_chat(memory["flights"])
        else:
            match = _flight_option(session.id, option_number, db)
            if match is None:
                assistant_message = f"I could not find flight option {option_number}."
                travel_results = _flight_results_for_chat(memory["flights"])
            else:
                selected_option, selected_flight = match
                _set_current_flight_option(session.id, selected_option, db)
                _select_pending_flight(session.id, selected_flight, db)
                travel_results = _flight_results_for_chat([selected_flight])
                assistant_message = (
                    f"You selected flight option {selected_option}. Would you like to "
                    "add it to your cart and continue to checkout?"
                )
    elif action == "select_current_flight":
        match = _current_displayed_flight(session.id, db)
        if match is None:
            assistant_message = "Please choose a flight option first."
            travel_results = _flight_results_for_chat(memory["flights"])
        else:
            selected_option, selected_flight = match
            _select_pending_flight(session.id, selected_flight, db)
            travel_results = _flight_results_for_chat([selected_flight])
            assistant_message = (
                f"You selected flight option {selected_option}. Would you like to "
                "add it to your cart and continue to checkout?"
            )
    elif action == "show_flight_option":
        if option_number is None:
            assistant_message = "Please specify a flight option number."
            travel_results = _flight_results_for_chat(memory["flights"])
        else:
            match = _flight_option(session.id, option_number, db)
            if match is None:
                assistant_message = f"I could not find flight option {option_number}."
                travel_results = _flight_results_for_chat(memory["flights"])
            else:
                selected_option, selected_flight = match
                _set_current_flight_option(session.id, selected_option, db)
                travel_results = _flight_results_for_chat([selected_flight])
                assistant_message = f"Here is flight option {selected_option}."
    elif action == "show_cheapest_flight":
        match = _cheapest_flight(session.id, db)
        if match is None:
            assistant_message = "I could not identify a cheapest flight option."
            travel_results = _flight_results_for_chat(memory["flights"])
        else:
            selected_option, selected_flight = match
            _set_current_flight_option(session.id, selected_option, db)
            travel_results = _flight_results_for_chat([selected_flight])
            assistant_message = f"The cheapest flight is option {selected_option}."
    else:
        match = _fastest_flight(session.id, db)
        if match is None:
            assistant_message = "I could not identify a fastest flight option."
            travel_results = _flight_results_for_chat(memory["flights"])
        else:
            selected_option, selected_flight = match
            _set_current_flight_option(session.id, selected_option, db)
            travel_results = _flight_results_for_chat([selected_flight])
            assistant_message = f"The fastest flight is option {selected_option}."

    _save_message(db, session.id, "assistant", assistant_message)
    db.commit()

    return ChatResponse(
        reply=assistant_message,
        intent=intent,
        results=travel_results,
        requires_confirmation=requires_confirmation,
        pending_action=pending_action,
    )


def _handle_flight_follow_up_message(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    normalized_message = _normalize_control_message(request.message)
    option_number = _extract_option_number(request.message)

    if "book the cheapest" in normalized_message:
        action = "book_cheapest_flight"
    elif option_number is not None and _is_select_option_message(request.message):
        action = "select_flight_option"
    elif option_number is not None and _is_show_option_message(request.message):
        action = "show_flight_option"
    elif "cheapest" in normalized_message or "lowest price" in normalized_message:
        action = "show_cheapest_flight"
    else:
        action = "show_fastest_flight"

    return _handle_flight_memory_action(
        db,
        session,
        request,
        action,
        option_number,
    )


def _handle_package_option_message(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    is_selection = _is_select_option_message(request.message)
    intent = _build_control_intent(
        request.message,
        "package_option_selection" if is_selection else "package_option_view",
    )
    _save_message(db, session.id, "user", request.message, intent)

    pending_booking = PENDING_BOOKINGS.get(session.id)
    travel_results = _empty_travel_results()
    requires_confirmation = False
    pending_action = None

    if pending_booking is None:
        travel_results["suggestions"] = [
            "Ask me to book a trip to Rome first.",
            "Then choose from the package options.",
        ]
        assistant_message = "I do not have package options open yet."
    else:
        all_results = pending_booking["results"]
        packages = all_results["packages"]
        destination = pending_booking["intent"].get("destination") or "this trip"
        option_number = _extract_option_number(request.message)
        requires_confirmation = True

        if _is_cheaper_options_message(request.message):
            sorted_packages = sorted(
                packages,
                key=lambda package: int(package["estimated_price"]),
            )
            travel_results = _results_for_packages(all_results, sorted_packages)
            pending_action = PENDING_SELECTION_ACTION
            assistant_message = (
                f"Here are the cheaper package options for {destination}, sorted "
                "by estimated price. Say 'select option 2' when you choose one."
            )
        elif _is_another_option_message(request.message):
            current_option = int(pending_booking.get("current_option_number", 1))
            next_option = current_option + 1
            package = _find_package_by_option(packages, next_option)

            if package is None:
                travel_results = _results_for_packages(all_results, packages)
                pending_action = PENDING_SELECTION_ACTION
                assistant_message = (
                    f"Those are all the package options I have for "
                    f"{destination}. You can select option 1, 2, or 3."
                )
            else:
                pending_booking["current_option_number"] = next_option
                travel_results = _results_for_packages(all_results, [package])
                pending_action = PENDING_SELECTION_ACTION
                assistant_message = (
                    f"Here is option {next_option} for {destination}. Say "
                    f"'select option {next_option}' if you want to continue with it."
                )
        elif option_number is not None:
            package = _find_package_by_option(packages, option_number)
            if package is None:
                travel_results = _results_for_packages(all_results, packages)
                travel_results["suggestions"] = [
                    "Choose one of the available option numbers.",
                    "Try select option 1, select option 2, or select option 3.",
                ]
                pending_action = PENDING_SELECTION_ACTION
                assistant_message = (
                    f"I could not find option {option_number} for {destination}."
                )
            elif is_selection:
                pending_booking["selected_option_number"] = option_number
                pending_booking["current_option_number"] = option_number
                travel_results = _results_for_packages(all_results, [package])
                pending_action = PENDING_CONFIRMATION_ACTION
                assistant_message = (
                    f"You selected option {option_number} for {destination}. "
                    "Would you like to confirm this booking?"
                )
            else:
                pending_booking["current_option_number"] = option_number
                travel_results = _results_for_packages(all_results, [package])
                pending_action = PENDING_SELECTION_ACTION
                assistant_message = (
                    f"Here is option {option_number} for {destination}. Say "
                    f"'select option {option_number}' if you want to continue with it."
                )
        else:
            travel_results = _results_for_packages(all_results, packages)
            pending_action = PENDING_SELECTION_ACTION
            assistant_message = (
                f"I found {len(packages)} package options for {destination}. "
                "Say 'show option 2', 'show cheaper options', or 'select option 3'."
            )

    _save_message(db, session.id, "assistant", assistant_message)
    db.commit()

    return ChatResponse(
        reply=assistant_message,
        intent=intent,
        results=travel_results,
        requires_confirmation=requires_confirmation,
        pending_action=pending_action,
    )


def _chat_date_source(value: Any, message: str) -> str:
    """Restore a yearless date phrase before normalising Llama's guessed year."""

    if has_explicit_year(message):
        return value.isoformat()
    yearless_month_days = extract_yearless_month_days(message)
    if (value.month, value.day) in yearless_month_days:
        return f"{value.day}/{value.month}"
    return value.isoformat()


def _normalise_chat_flight_search(
    flight_search: FlightSearchRequest,
    message: str,
) -> FlightSearchRequest:
    """Correct only clearly yearless chat dates, then validate the result."""

    dates = flight_search.itinerary.dates
    reference_today = today_utc_date()
    relative_departure = resolve_relative_travel_date(message, today=reference_today)
    departure_source = relative_departure or _chat_date_source(dates.departure, message)
    return_source = (
        _chat_date_source(dates.return_date, message)
        if dates.return_date is not None
        else None
    )
    if relative_departure and dates.return_date is not None:
        original_duration = max(1, (dates.return_date - dates.departure).days)
        return_source = (
            date.fromisoformat(relative_departure) + timedelta(days=original_duration)
        ).isoformat()
    departure, return_date = normalise_trip_dates(
        departure_source,
        return_source,
        today=reference_today,
    )
    payload = flight_search.model_dump(mode="json", by_alias=True)
    payload["itinerary"]["dates"]["departure"] = departure
    if return_date is None:
        payload["itinerary"]["dates"].pop("return", None)
    else:
        payload["itinerary"]["dates"]["return"] = return_date
    return FlightSearchRequest.model_validate(payload)


def _normalise_chat_hotel_search(
    hotel_search: HotelSearchRequest,
    message: str,
) -> HotelSearchRequest:
    """Correct only clearly yearless hotel dates, then validate the result."""

    reference_today = today_utc_date()
    relative_check_in = resolve_relative_travel_date(message, today=reference_today)
    check_in_source = relative_check_in or _chat_date_source(hotel_search.check_in_date, message)
    check_out_source = _chat_date_source(hotel_search.check_out_date, message)
    if relative_check_in:
        nights = max(1, (hotel_search.check_out_date - hotel_search.check_in_date).days)
        check_out_source = (
            date.fromisoformat(relative_check_in) + timedelta(days=nights)
        ).isoformat()
    check_in, check_out = normalise_hotel_dates(
        check_in_source,
        check_out_source,
        today=reference_today,
    )
    payload = hotel_search.model_dump(mode="json", by_alias=True)
    payload["checkInDate"] = check_in
    payload["checkOutDate"] = check_out
    return HotelSearchRequest.model_validate(payload)


def _travel_date_clarification(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    intent_type: str,
    error: TravelDateValidationError,
) -> ChatResponse:
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, intent_type),
        str(error),
    )


def _flight_search_missing_fields(
    flight_search: FlightSearchRequest,
) -> list[str]:
    missing: list[str] = []
    origin_code = str(flight_search.itinerary.origin.code or "").strip()
    destination_code = str(flight_search.itinerary.destination.code or "").strip()
    if not origin_code:
        missing.append("origin")
    if not destination_code:
        missing.append("destination")
    if flight_search.itinerary.dates.departure is None:
        missing.append("departureDate")
    return missing


def _flight_search_missing_field_response(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    missing_fields: list[str],
) -> ChatResponse:
    if "destination" in missing_fields:
        reply = "Where would you like to travel to?"
    elif "origin" in missing_fields:
        reply = "Where are you flying from?"
    else:
        reply = "When do you want to depart?"
    _log_route_trace(
        "blocked_incomplete_flight_search",
        "search_flights",
        None,
        missing_fields,
        missing_fields,
    )
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "flight_search"),
        reply,
    )


def _execute_flight_search_request(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    flight_search: FlightSearchRequest,
) -> ChatResponse | None:
    missing_fields = _flight_search_missing_fields(flight_search)
    if missing_fields:
        return _flight_search_missing_field_response(
            db,
            session,
            request,
            missing_fields,
        )
    try:
        flight_search = _normalise_chat_flight_search(flight_search, request.message)
    except TravelDateValidationError as exc:
        return _travel_date_clarification(
            db,
            session,
            request,
            "flight_search",
            exc,
        )
    missing_fields = _flight_search_missing_fields(flight_search)
    if missing_fields:
        return _flight_search_missing_field_response(
            db,
            session,
            request,
            missing_fields,
        )
    llama_intent = _build_llama_flight_intent(request.message, flight_search)
    _save_message(db, session.id, "user", request.message, llama_intent)

    provider_response = _search_flights_with_fallback(flight_search)
    LOGGER.info("Flight search provider used: %s", provider_response.provider_used)
    _store_latest_flight_results(
        session.id,
        llama_intent,
        provider_response,
        flight_search,
        db,
    )
    travel_results = _flight_search_results_for_chat(provider_response)
    assistant_message = _build_llama_flight_reply(
        flight_search,
        provider_response,
    )
    _save_message(db, session.id, "assistant", assistant_message)
    db.commit()

    return ChatResponse(
        reply=assistant_message,
        intent=llama_intent,
        results=travel_results,
        requires_confirmation=False,
        pending_action=None,
    )


def _execute_hotel_search_request(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    hotel_search: HotelSearchRequest,
) -> ChatResponse:
    try:
        hotel_search = _normalise_chat_hotel_search(hotel_search, request.message)
    except TravelDateValidationError as exc:
        return _travel_date_clarification(
            db,
            session,
            request,
            "hotel_search",
            exc,
        )
    intent = _build_hotel_intent(request.message, hotel_search)
    _save_message(db, session.id, "user", request.message, intent)
    provider_response = _search_hotels_with_fallback(hotel_search)
    LOGGER.info("Hotel search provider used: %s", provider_response.provider_used)
    _store_latest_hotel_results(
        session.id,
        intent,
        provider_response,
        hotel_search,
        db,
    )
    hotels = _normalised_hotel_dicts(provider_response)
    if not hotels:
        reply = "I could not find hotel results for that request."
    elif provider_response.provider_used == "liteapi":
        reply = f"I found {len(hotels)} hotel option(s) using LiteAPI rates."
    else:
        reply = f"I found {len(hotels)} hotel option(s)."
    _save_message(db, session.id, "assistant", reply)
    db.commit()
    return ChatResponse(
        reply=reply,
        intent=intent,
        results=_hotel_results_for_chat(hotels, CHAT_HOTEL_CARD_LIMIT),
        requires_confirmation=False,
        pending_action=None,
    )


def _hotel_context_response(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    return _tool_response(
        db,
        session,
        request,
        _hotel_follow_up_intent(session.id, request.message, db),
        "Please search for hotels first.",
    )


def _hotel_search_from_message(message: str) -> HotelSearchRequest | None:
    normalized = message.lower()
    city_match = re.search(r"\b(?:in|to)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)", message)
    city = city_match.group(1).title() if city_match else None
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", message)
    if city is None or len(dates) < 2:
        return None
    guest_match = re.search(r"\b(\d+)\s+(?:adult|adults|guest|guests|people)\b", normalized)
    room_match = re.search(r"\b(\d+)\s+rooms?\b", normalized)
    try:
        return HotelSearchRequest.model_validate(
            {
                "searchType": "hotel",
                "destination": {"city": city},
                "checkInDate": dates[0],
                "checkOutDate": dates[1],
                "guestCount": int(guest_match.group(1)) if guest_match else 1,
                "roomCount": int(room_match.group(1)) if room_match else 1,
                "preferences": {"currency": "GBP"},
            }
        )
    except (TypeError, ValueError):
        return None


def _hotel_search_from_tool_call(
    tool_call: TravelToolCall,
    message: str,
) -> HotelSearchRequest | None:
    raw_search_request = tool_call.arguments.get("search_request")
    candidate = raw_search_request if isinstance(raw_search_request, dict) else tool_call.arguments
    if isinstance(candidate, dict) and candidate.get("searchType") == "hotel":
        try:
            return HotelSearchRequest.model_validate(candidate)
        except Exception as exc:
            LOGGER.warning("Tool hotel request validation failed: %s", exc.__class__.__name__)
    return _hotel_search_from_message(message)


def _resolve_hotel_selection(
    session_id: int,
    selection: str,
    option_number: int | None,
    db: Session | None = None,
) -> tuple[int, dict[str, Any]] | None:
    if selection == "current_displayed":
        return _current_displayed_hotel(session_id, db)
    if selection == "cheapest":
        return _cheapest_hotel(session_id, db)
    if selection == "option_number":
        return _hotel_option(session_id, option_number, db)
    return None


def _execute_rank_hotels_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    if _latest_hotel_memory(session.id, db) is None:
        return _hotel_context_response(db, session, request)
    if tool_call.arguments.get("ranking", "cheapest") != "cheapest":
        return _tool_response(
            db,
            session,
            request,
            _hotel_follow_up_intent(session.id, request.message, db),
            "I can currently rank hotel results by lowest total price.",
        )
    match = _cheapest_hotel(session.id, db)
    if match is None:
        return _hotel_context_response(db, session, request)
    option_number, hotel = match
    _set_current_hotel_option(session.id, option_number, db)
    _set_last_tool_used(session.id, "rank_hotels", db)
    return _tool_response(
        db,
        session,
        request,
        _hotel_follow_up_intent(session.id, request.message, db),
        f"The cheapest hotel is option {option_number}.",
        _hotel_results_for_chat([hotel]),
    )


def _execute_show_hotel_option_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    memory = _latest_hotel_memory(session.id, db)
    if memory is None:
        return _hotel_context_response(db, session, request)
    option_number = _tool_option_number(tool_call.arguments)
    match = _hotel_option(session.id, option_number, db)
    if match is None:
        count = len(memory["hotels"])
        requested = option_number if option_number is not None else "that"
        return _tool_response(
            db,
            session,
            request,
            _hotel_follow_up_intent(session.id, request.message, db),
            f"I could not find option {requested}. Please choose a number between 1 and {count}.",
            _hotel_results_for_chat(memory["hotels"]),
        )
    selected_option, hotel = match
    _set_current_hotel_option(session.id, selected_option, db)
    _set_last_tool_used(session.id, "show_hotel_option", db)
    return _tool_response(
        db,
        session,
        request,
        _hotel_follow_up_intent(session.id, request.message, db),
        f"Here is hotel option {selected_option}.",
        _hotel_results_for_chat([hotel]),
    )


def _execute_select_hotel_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    if _latest_hotel_memory(session.id, db) is None:
        return _hotel_context_response(db, session, request)
    selection_details = _selection_from_tool_call(tool_call)
    if selection_details is None:
        return _hotel_context_response(db, session, request)
    selection, option_number = selection_details
    match = _resolve_hotel_selection(session.id, selection, option_number, db)
    if match is None:
        return _hotel_context_response(db, session, request)
    selected_option, hotel = match
    _clear_pending_flight_selection(session.id, db)
    _clear_pending_hotel_selection(session.id, db)
    _set_current_hotel_option(session.id, selected_option, db)
    active_trip = _active_trip_for_session(session.id, db)
    active_trip["selectedHotelOption"] = {
        "optionNumber": selected_option,
        **deepcopy(hotel),
    }
    active_trip["latestVisibleResultType"] = "hotel_selected"
    active_trip["status"] = "hotel_selected"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    _set_last_tool_used(session.id, "select_hotel", db)
    return _tool_response(
        db,
        session,
        request,
        _hotel_follow_up_intent(session.id, request.message, db),
        (
            f"You selected {hotel.get('hotel_name') or f'hotel option {selected_option}'}. "
            "Would you like to add it to your cart and continue to checkout?"
        ),
        _hotel_results_for_chat([hotel]),
    )


def _execute_hotel_selection_or_confirmation_if_possible(
    db: Session,
    user: User,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    visible = active_trip.get("latestVisibleResultType")
    has_pending_hotel = _selected_pending_hotel(session.id, db) is not None
    hotel_context = (
        visible in {"hotels", "hotel_selected"}
        or has_pending_hotel
        or _hotel_context_is_current(session.id, db)
    )
    if not hotel_context:
        return None

    if has_pending_hotel and _is_confirmation_message(request.message):
        return _handle_confirmation_message(db, user, session, request)

    option_number = _extract_option_number(request.message)
    if option_number is not None and _is_select_option_message(request.message):
        return _execute_select_hotel_tool(
            TravelToolCall(
                tool="select_hotel",
                arguments={"selection": "option_number", "option_number": option_number},
            ),
            db,
            session,
            request,
        )

    if option_number is not None and _is_show_option_message(request.message):
        return _execute_show_hotel_option_tool(
            TravelToolCall(tool="show_hotel_option", arguments={"option_number": option_number}),
            db,
            session,
            request,
        )

    if _is_current_flight_reference(request.message):
        return _execute_select_hotel_tool(
            TravelToolCall(tool="select_hotel", arguments={"selection": "current_displayed"}),
            db,
            session,
            request,
        )
    return None


def _handle_llama_flight_search(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    flight_search = extract_flight_search_with_llama(request.message)
    if flight_search is None:
        return None
    return _execute_flight_search_request(db, session, request, flight_search)


def _respond_to_intent(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    intent: dict[str, Any],
) -> ChatResponse:
    _save_message(db, session.id, "user", request.message, intent)

    if intent["intent_type"] == "booking_retrieval":
        travel_results = _search_booking_from_database(
            db,
            intent.get("booking_reference"),
            user_id=getattr(session, "user_id", None),
        )
    else:
        travel_results = search_mock_travel(intent)

    requires_confirmation = (
        intent["intent_type"] == "package_search" and bool(travel_results["packages"])
    )
    selected_option_number = None
    package_count = len(travel_results["packages"])
    if requires_confirmation and package_count == 1:
        selected_option_number = 1

    pending_action = None
    if requires_confirmation:
        pending_action = (
            PENDING_CONFIRMATION_ACTION
            if selected_option_number
            else PENDING_SELECTION_ACTION
        )
        _store_pending_booking(
            session.id,
            intent,
            travel_results,
            selected_option_number=selected_option_number,
        )

    assistant_message = _build_mock_reply(
        intent,
        travel_results,
        requires_confirmation=requires_confirmation,
    )
    _save_message(db, session.id, "assistant", assistant_message)
    db.commit()

    return ChatResponse(
        reply=assistant_message,
        intent=intent,
        results=travel_results,
        requires_confirmation=requires_confirmation,
        pending_action=pending_action,
    )


def _router_intent(
    message: str,
    route: LlamaChatActionRoute,
) -> dict[str, Any]:
    intent = extract_travel_intent(message)
    action_to_intent = {
        "hotel_search": "hotel_search",
        "package_search": "package_search",
        "booking_lookup": "booking_retrieval",
        "general_help": "general_enquiry",
    }
    intent_type = action_to_intent.get(route.action)
    if intent_type:
        intent["intent_type"] = intent_type
    if route.booking_reference:
        intent["booking_reference"] = route.booking_reference
    return intent


def _handle_missing_pending_action(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    action: str,
) -> ChatResponse:
    intent = _build_control_intent(request.message, "general_enquiry")
    _save_message(db, session.id, "user", request.message, intent)
    assistant_message = (
        "Please choose a flight or package before confirming."
        if action == "confirm_booking"
        else "There is no pending booking to cancel."
    )
    _save_message(db, session.id, "assistant", assistant_message)
    db.commit()
    return ChatResponse(
        reply=assistant_message,
        intent=intent,
        results=_empty_travel_results(),
        requires_confirmation=False,
        pending_action=None,
    )


def _execute_llama_route(
    route: LlamaChatActionRoute,
    db: Session,
    user: User,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    action = route.action

    if action == "flight_search":
        return _handle_llama_flight_search(db, session, request)

    if action in {
        "show_cheapest_flight",
        "show_fastest_flight",
        "show_flight_option",
        "select_flight_option",
        "select_current_flight",
        "book_cheapest_flight",
    }:
        if session.id in PENDING_BOOKINGS and _is_package_option_message(request.message):
            return None
        return _handle_flight_memory_action(
            db,
            session,
            request,
            action,
            route.option_number,
        )

    if action == "confirm_booking":
        flight_cart_response = _execute_selected_flight_cart_if_possible(
            db,
            session,
            request,
        )
        if flight_cart_response is not None:
            return flight_cart_response
        has_pending_flight = _selected_pending_flight(session.id, db) is not None
        if session.id in PENDING_BOOKINGS or has_pending_flight:
            return _handle_confirmation_message(db, user, session, request)
        return _handle_missing_pending_action(db, session, request, action)

    if action == "cancel_booking":
        has_pending_flight = _selected_pending_flight(session.id, db) is not None
        if session.id in PENDING_BOOKINGS or has_pending_flight:
            return _handle_cancellation_message(db, session, request)
        return _handle_missing_pending_action(db, session, request, action)

    if action in {"hotel_search", "package_search", "booking_lookup", "general_help"}:
        if action == "general_help" and session.id in PENDING_BOOKINGS and _is_package_option_message(request.message):
            return None
        if (
            action == "general_help"
            and session.id not in PENDING_BOOKINGS
            and _is_current_flight_reference(request.message)
        ):
            return _handle_flight_memory_action(
                db,
                session,
                request,
                "select_current_flight",
            )
        return _respond_to_intent(db, session, request, _router_intent(request.message, route))

    return None


def _tool_response(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    intent: dict[str, Any],
    reply: str,
    results: dict[str, Any] | None = None,
    requires_confirmation: bool = False,
    pending_action: str | None = None,
    next_action: str | None = None,
    next_path: str | None = None,
    redirect_to: str | None = None,
) -> ChatResponse:
    _save_message(db, session.id, "user", request.message, intent)
    _save_message(db, session.id, "assistant", reply)
    db.commit()
    return ChatResponse(
        reply=reply,
        intent=intent,
        results=results or _empty_travel_results(),
        requires_confirmation=requires_confirmation,
        pending_action=pending_action,
        sessionId=session.id,
        nextAction=next_action,
        nextPath=next_path,
        redirect_to=redirect_to or (next_path if next_action == "navigate" else None),
    )


def _flight_context_response(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    return _tool_response(
        db,
        session,
        request,
        _flight_follow_up_intent(session.id, request.message, db),
        "Please search for flights first.",
    )


def _flight_search_from_tool_call(
    tool_call: TravelToolCall,
    message: str,
) -> FlightSearchRequest | None:
    raw_search_request = tool_call.arguments.get("search_request")
    if isinstance(raw_search_request, dict):
        try:
            return FlightSearchRequest.model_validate(raw_search_request)
        except Exception as exc:
            details = exc.errors() if hasattr(exc, "errors") else str(exc)
            LOGGER.warning(
                "Tool search request validation failed: %s",
                {
                    "error": exc.__class__.__name__,
                    "details": details,
                    "keys": sorted(str(key) for key in raw_search_request.keys()),
                },
            )
            return None

    return extract_flight_search_with_llama(message)


def _destination_discovery_from_tool_call(
    tool_call: TravelToolCall,
    session_id: int,
    db: Session,
) -> DestinationDiscoveryRequest | None:
    arguments = dict(tool_call.arguments or {})
    default_origin = _default_origin_for_session(session_id, db)
    raw_origin = arguments.get("origin")
    if isinstance(raw_origin, dict):
        enriched_origin = _airport_payload_from_structured_slot(
            code=raw_origin.get("code") or raw_origin.get("airportCode"),
            text=raw_origin.get("city") or raw_origin.get("airportName"),
            source=str(raw_origin.get("source") or "explicit_origin"),
        )
        if enriched_origin:
            arguments["origin"] = {
                "city": enriched_origin.get("city") or raw_origin.get("city"),
                "code": enriched_origin.get("airportCode") or raw_origin.get("code"),
                "airportName": enriched_origin.get("airportName") or raw_origin.get("airportName"),
                "latitude": enriched_origin.get("latitude") or raw_origin.get("latitude"),
                "longitude": enriched_origin.get("longitude") or raw_origin.get("longitude"),
            }
    elif default_origin:
        arguments["origin"] = {
            "city": default_origin.get("city"),
            "code": default_origin.get("airportCode"),
            "airportName": default_origin.get("airportName"),
            "latitude": default_origin.get("latitude"),
            "longitude": default_origin.get("longitude"),
        }
    try:
        return DestinationDiscoveryRequest.model_validate(arguments)
    except Exception as exc:
        LOGGER.warning("Destination discovery tool validation failed: %s", exc.__class__.__name__)
        return None


def _latest_destination_discovery_request(
    session_id: int,
    db: Session,
) -> DestinationDiscoveryRequest | None:
    request_data = load_session_memory(db, session_id).get(
        "destination_discovery_request"
    )
    if not isinstance(request_data, dict):
        return None
    try:
        return DestinationDiscoveryRequest.model_validate(request_data)
    except Exception as exc:
        LOGGER.warning(
            "Stored destination discovery request validation failed: %s",
            exc.__class__.__name__,
        )
        return None


def _has_latest_destination_discovery(session_id: int, db: Session) -> bool:
    memory = load_session_memory(db, session_id)
    return isinstance(memory.get("destination_discovery_request"), dict) and bool(
        memory.get("destination_recommendations")
    )


def _execute_destination_results_confirmation_if_possible(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    if not _has_latest_destination_discovery(session.id, db):
        return None
    active_trip = _active_trip_for_session(session.id, db)
    if active_trip.get("latestVisibleResultType") not in {
        "destinations",
        "destination_discovery",
        "destination_discovery_pending",
    }:
        return None
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "general_enquiry"),
        "Please choose one of the destination options first, then I can search flights or build a package.",
    )


def _looks_like_destination_refinement_message(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "keep it in europe",
            "only europe",
            "in europe",
            "europe",
            "outside europe",
            "not in europe",
            "not europe",
            "no europe",
            "anywhere except europe",
            "not in european countries",
            "dont want it in europe",
            "do not want it in europe",
            "not long flights",
            "dont like long flights",
            "don't like long flights",
            "short flights",
            "shorter flights",
            "less than 2 hours",
            "under 2 hours",
            "closer",
            "nearby",
            "doesnt have to be in europe",
            "does not have to be in europe",
            "not necessarily europe",
            "anywhere",
            "any country",
            "dont care about long flights",
            "dint care about long flights",
            "do not care about long flights",
            "long flights are okay",
            "flight duration doesnt matter",
            "duration doesnt matter",
            "dont want skiing",
            "do not want skiing",
            "not skiing",
            "no skiing",
            "beach",
            "beaches",
            "sea",
            "coast",
            "water sports",
            "water activities",
            "places to enjoy",
            "things to do",
            "attractions",
            "activities",
            "sightseeing",
            "fun places",
        ]
    )


def detect_constraint_removals(message: str) -> dict[str, bool]:
    normalized = _normalize_control_message(message)
    remove_region = any(
        phrase in normalized
        for phrase in [
            "doesnt have to be in europe",
            "does not have to be in europe",
            "not necessarily europe",
            "anywhere",
            "any country",
        ]
    )
    remove_duration = any(
        phrase in normalized
        for phrase in [
            "i dont care about long flights",
            "i dint care about long flights",
            "i do not care about long flights",
            "dont care about long flights",
            "dint care about long flights",
            "do not care about long flights",
            "long flights are okay",
            "flight duration doesnt matter",
            "duration doesnt matter",
        ]
    )
    remove_skiing = any(
        phrase in normalized
        for phrase in [
            "i dont want skiing",
            "i do not want skiing",
            "dont want skiing",
            "do not want skiing",
            "not skiing",
            "no skiing",
        ]
    )
    return {
        "region": remove_region,
        "duration": remove_duration,
        "skiing": remove_skiing,
    }


def detect_excluded_regions(message: str) -> list[str]:
    normalized = _normalize_control_message(message)
    if any(
        phrase in normalized
        for phrase in [
            "i dont want it in europe",
            "i do not want it in europe",
            "dont want it in europe",
            "do not want it in europe",
            "not in europe",
            "outside europe",
            "not europe",
            "no europe",
            "anywhere except europe",
            "not in european countries",
        ]
    ):
        return ["Europe"]
    return []


def detect_trip_intent_shift(message: str) -> str | None:
    normalized = _normalize_control_message(message)
    if any(
        phrase in normalized
        for phrase in [
            "beach",
            "beaches",
            "sea",
            "coast",
            "water activities",
            "water sports",
            "swimming",
            "snorkelling",
            "snorkeling",
            "diving",
        ]
    ):
        return "beach"
    if any(
        phrase in normalized
        for phrase in [
            "places to enjoy",
            "things to do",
            "attractions",
            "activities",
            "sightseeing",
            "fun places",
        ]
    ):
        return "general_enjoyment"
    return None


def _normalised_preference_value(value: Any) -> str:
    return str(value).lower().replace(" ", "_")


def _remove_europe_region_preferences(
    regions: set[Any],
    soft_preferences: set[Any],
    hard_filters: set[Any],
) -> tuple[set[Any], set[Any], set[Any]]:
    regions = {
        region
        for region in regions
        if _normalised_preference_value(region) != "europe"
    }
    soft_preferences = {
        value
        for value in soft_preferences
        if _normalised_preference_value(value) != "region:europe"
    }
    hard_filters = {
        value
        for value in hard_filters
        if _normalised_preference_value(value) != "region:europe"
    }
    return regions, soft_preferences, hard_filters


def merge_destination_discovery_preferences(
    previous_request: DestinationDiscoveryRequest,
    new_update: dict[str, Any],
) -> DestinationDiscoveryRequest:
    payload = previous_request.model_dump(by_alias=True, mode="json")
    normalized = str(new_update.get("normalized_message") or "")
    removals = new_update.get("removals") or {}
    excluded_region_updates = set(new_update.get("excluded_regions") or [])
    intent_shift = new_update.get("intent_shift")

    regions = set(payload.get("regionPreferences") or [])
    excluded_regions = set(payload.get("excludedRegions") or [])
    soft_preferences = set(payload.get("softPreferences") or [])
    hard_filters = set(payload.get("hardFilters") or [])
    activity_preferences = list(payload.get("activityPreferences") or [])
    required_activities = list(payload.get("requiredActivities") or [])

    if removals.get("region"):
        regions, soft_preferences, hard_filters = _remove_europe_region_preferences(
            regions,
            soft_preferences,
            hard_filters,
        )

    if removals.get("duration"):
        payload["shortFlightPreference"] = False
        payload["maxFlightDurationMinutes"] = None
        soft_preferences = {
            value
            for value in soft_preferences
            if _normalised_preference_value(value) != "short_flight"
        }
        hard_filters = {
            value
            for value in hard_filters
            if not _normalised_preference_value(value).startswith("max_flight_minutes:")
        }

    removes_skiing = bool(removals.get("skiing"))
    if intent_shift in {"general_enjoyment", "beach"} and not removes_skiing:
        removes_skiing = "skiing" not in normalized and " ski " not in f" {normalized} "
    if removes_skiing:
        activity_preferences = [
            value
            for value in activity_preferences
            if _normalised_preference_value(value) not in {"ski", "skiing"}
        ]
        required_activities = [
            value
            for value in required_activities
            if _normalised_preference_value(value) not in {"ski", "skiing"}
        ]

    no_skiing_in_europe = any(
        phrase in normalized
        for phrase in [
            "dont want skiing in europe",
            "do not want skiing in europe",
            "no skiing in europe",
            "not skiing in europe",
        ]
    )
    if no_skiing_in_europe:
        regions, soft_preferences, hard_filters = _remove_europe_region_preferences(
            regions,
            soft_preferences,
            hard_filters,
        )
    if excluded_region_updates:
        if any(
            _normalised_preference_value(region) == "europe"
            for region in excluded_region_updates
        ):
            regions, soft_preferences, hard_filters = _remove_europe_region_preferences(
                regions,
                soft_preferences,
                hard_filters,
            )
            excluded_regions.add("Europe")
    should_add_europe = (
        "europe" in normalized
        and not removals.get("region")
        and not no_skiing_in_europe
        and not any(
            _normalised_preference_value(region) == "europe"
            for region in excluded_regions
        )
    )
    if should_add_europe:
        regions.add("Europe")
        soft_preferences.add("region:europe")
    if (
        "only europe" in normalized or "keep it in europe" in normalized
    ) and not removals.get("region"):
        hard_filters.add("region:europe")

    if any(
        phrase in normalized
        for phrase in [
            "not long flights",
            "dont like long flights",
            "do not like long flights",
            "short flights",
            "shorter flights",
            "closer",
            "nearby",
        ]
    ) and not removals.get("duration"):
        payload["shortFlightPreference"] = True
        soft_preferences.add("short_flight")

    hours_match = re.search(
        r"(?:less than|under|max(?:imum)?)\s+(\d+)\s+hours?",
        normalized,
    )
    if hours_match and not removals.get("duration"):
        payload["maxFlightDurationMinutes"] = int(hours_match.group(1)) * 60
        payload["shortFlightPreference"] = True
        soft_preferences.add("short_flight")
        if any(phrase in normalized for phrase in ["must", "only", "under", "less than"]):
            hard_filters.add(f"max_flight_minutes:{payload['maxFlightDurationMinutes']}")

    if intent_shift == "general_enjoyment":
        payload["tripPurpose"] = "general_holiday"
        for value in ["attractions", "sightseeing", "activities"]:
            if value not in {
                _normalised_preference_value(item) for item in activity_preferences
            }:
                activity_preferences.append(value)

    if intent_shift == "beach":
        payload["tripPurpose"] = "beach_holiday"
        environment_preferences = list(payload.get("environmentPreferences") or [])
        for value in ["beach", "sea"]:
            if value not in {
                _normalised_preference_value(item) for item in environment_preferences
            }:
                environment_preferences.append(value)
        for value in [
            "beach_activities",
            "water_sports",
            "swimming",
            "snorkelling",
            "diving",
        ]:
            if value not in {
                _normalised_preference_value(item) for item in activity_preferences
            }:
                activity_preferences.append(value)
        for value in ["beach", "beach_activities"]:
            if value not in {
                _normalised_preference_value(item) for item in required_activities
            }:
                required_activities.append(value)
        payload["environmentPreferences"] = environment_preferences

    payload["activityPreferences"] = activity_preferences
    payload["requiredActivities"] = required_activities
    payload["regionPreferences"] = sorted(regions)
    payload["excludedRegions"] = sorted(excluded_regions)
    payload["softPreferences"] = sorted(soft_preferences)
    payload["hardFilters"] = sorted(hard_filters)

    return DestinationDiscoveryRequest.model_validate(payload)


def _merge_destination_refinement(
    latest_request: DestinationDiscoveryRequest,
    message: str,
) -> DestinationDiscoveryRequest:
    normalized = _normalize_control_message(message)
    return merge_destination_discovery_preferences(
        latest_request,
        {
            "normalized_message": normalized,
            "removals": detect_constraint_removals(message),
            "excluded_regions": detect_excluded_regions(message),
            "intent_shift": detect_trip_intent_shift(message),
        },
    )


def _destination_refinement_note(
    discovery_request: DestinationDiscoveryRequest,
    message: str,
) -> str:
    removals = detect_constraint_removals(message)
    excluded_regions = detect_excluded_regions(message)
    intent_shift = detect_trip_intent_shift(message)
    notes: list[str] = []
    if "Europe" in excluded_regions:
        if "skiing" in {
            _normalised_preference_value(value)
            for value in discovery_request.required_activities
        }:
            notes.append("I excluded Europe and kept skiing as the main requirement.")
        else:
            notes.append("I excluded Europe from the search.")
    removed_constraints: list[str] = []
    if removals.get("region"):
        removed_constraints.append("Europe")
    if removals.get("duration"):
        removed_constraints.append("short-flight")
    if removals.get("skiing"):
        removed_constraints.append("skiing")
    if removed_constraints:
        notes.append(
            "I removed the "
            + ", ".join(removed_constraints)
            + " preference"
            + ("s" if len(removed_constraints) > 1 else "")
            + "."
        )
    if intent_shift == "general_enjoyment":
        notes.append(
            "I changed the search towards general places with attractions and things to do."
        )
    if intent_shift == "beach":
        notes.append("I'll plan this as a beach trip with beach activities.")
    if notes:
        return " " + " ".join(notes)
    if discovery_request.region_preferences and discovery_request.short_flight_preference:
        return " I refined the recommendations using your region and flight-duration preferences."
    if discovery_request.region_preferences:
        return " I refined the recommendations using your region preference."
    if discovery_request.short_flight_preference:
        return " I refined the recommendations using your shorter-flight preference."
    return ""


def _message_has_departure_date(message: str) -> bool:
    return _resolve_destination_followup_departure(message) is not None


def _message_mentions_family(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "family",
            "children",
            "child",
            "kids",
            "kid",
            "with my son",
            "with my daughter",
        ]
    )


def _message_mentions_region_constraint(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "europe",
            "africa",
            "middle east",
            "asia",
            "outside",
            "not in",
            "anywhere except",
        ]
    )


def _message_mentions_duration_constraint(message: str) -> bool:
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "not long flights",
            "dont like long flights",
            "do not like long flights",
            "short flights",
            "shorter flights",
            "closer",
            "nearby",
            "less than",
            "under",
            "hours",
        ]
    )


def _is_new_top_level_destination_discovery(message: str) -> bool:
    if not _looks_like_destination_discovery_message(message):
        return False
    removals = detect_constraint_removals(message)
    if removals.get("skiing"):
        return False
    if detect_trip_intent_shift(message) in {"beach", "general_enjoyment"}:
        return False
    normalized = _normalize_control_message(message)
    return any(
        phrase in normalized
        for phrase in [
            "i want",
            "i would like",
            "find",
            "recommend",
            "somewhere",
            "destination",
            "holiday",
        ]
    )


def _reset_active_trip_for_new_discovery(db: Session, session_id: int) -> None:
    active_trip = _active_trip_for_session(session_id, db)
    active_trip["selectedDestination"] = None
    active_trip["requestedProducts"] = []
    active_trip["dates"] = {
        "departureDate": None,
        "returnDate": None,
        "source": "new_destination_discovery",
    }
    active_trip["regionPreferences"] = []
    active_trip["excludedRegions"] = []
    active_trip["status"] = "discovering"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session_id, active_trip)
    _invalidate_dependent_state(
        db,
        session_id,
        "new_destination_discovery",
        clear_destinations=True,
    )


def _sanitize_new_destination_discovery_request(
    discovery_request: DestinationDiscoveryRequest,
    message: str,
) -> DestinationDiscoveryRequest:
    payload = discovery_request.model_dump(by_alias=True, mode="json")

    if not _message_has_departure_date(message):
        payload["dates"] = {}
        payload["missingFields"] = sorted(
            set(payload.get("missingFields") or []) | {"departureDate"}
        )

    if not _message_mentions_region_constraint(message):
        payload["regionPreferences"] = []
        payload["excludedRegions"] = []
        payload["softPreferences"] = [
            value
            for value in payload.get("softPreferences", [])
            if not _normalised_preference_value(value).startswith("region:")
        ]
        payload["hardFilters"] = [
            value
            for value in payload.get("hardFilters", [])
            if not _normalised_preference_value(value).startswith("region:")
        ]

    if not _message_mentions_duration_constraint(message):
        payload["shortFlightPreference"] = False
        payload["maxFlightDurationMinutes"] = None
        payload["softPreferences"] = [
            value
            for value in payload.get("softPreferences", [])
            if _normalised_preference_value(value) != "short_flight"
        ]
        payload["hardFilters"] = [
            value
            for value in payload.get("hardFilters", [])
            if not _normalised_preference_value(value).startswith("max_flight_minutes:")
        ]

    if not _message_mentions_family(message):
        travellers = dict(payload.get("travellers") or {})
        travellers["adults"] = max(1, int(travellers.get("adults") or 1))
        travellers["children"] = 0
        payload["travellers"] = travellers
        if str(payload.get("tripPurpose") or "").lower() in {
            "family",
            "family_holiday",
        }:
            payload["tripPurpose"] = "holiday"

    return DestinationDiscoveryRequest.model_validate(payload)


def _execute_destination_discovery_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
    *,
    preserve_request_origin: bool = False,
) -> ChatResponse:
    discovery_request = _destination_discovery_from_tool_call(tool_call, session.id, db)
    if discovery_request is None or discovery_request.origin is None:
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "general_enquiry"),
            "Where are you travelling from?",
        )
    if not preserve_request_origin:
        discovery_request = _apply_destination_origin_precedence(
            discovery_request,
            request.message,
            session.id,
            db,
        )
    is_new_top_level_discovery = _is_new_top_level_destination_discovery(
        request.message
    )
    if is_new_top_level_discovery:
        discovery_request = _sanitize_new_destination_discovery_request(
            discovery_request,
            request.message,
        )
        _reset_active_trip_for_new_discovery(db, session.id)

    trip_duration_nights = (
        discovery_request.trip_duration_nights
        or _extract_trip_duration_nights(request.message)
    )
    payload = discovery_request.model_dump(by_alias=True, mode="json")
    if trip_duration_nights:
        payload["tripDurationNights"] = trip_duration_nights
    discovery_request = DestinationDiscoveryRequest.model_validate(payload)
    if discovery_request.trip_duration_nights and not _destination_departure_date(
        discovery_request
    ):
        return _store_pending_destination_discovery_and_ask_date(
            db,
            session,
            request,
            discovery_request,
        )
    if (
        discovery_request.trip_duration_nights
        and _destination_departure_date(discovery_request)
        and not _destination_return_date(discovery_request)
    ):
        departure_iso = _destination_departure_date(discovery_request)
        assert departure_iso is not None
        payload = discovery_request.model_dump(by_alias=True, mode="json")
        dates = dict(payload.get("dates") or {})
        dates["return"] = (
            date.fromisoformat(departure_iso)
            + timedelta(days=discovery_request.trip_duration_nights)
        ).isoformat()
        payload["dates"] = dates
        discovery_request = DestinationDiscoveryRequest.model_validate(payload)

    _sync_active_trip_from_destination_request(
        db,
        session.id,
        discovery_request,
        status="discovering",
    )
    provider_response = discover_destinations(discovery_request)
    _set_last_tool_used(session.id, "discover_destinations", db)
    save_latest_destination_discovery(
        db,
        session.id,
        discovery_request.model_dump(by_alias=True, mode="json"),
        provider_response.recommendations,
    )
    persisted_recommendations = _latest_destination_recommendations(session.id, db)
    LOGGER.info(
        "Destination discovery persisted: %s",
        {
            "session_id": session.id,
            "recommendationCount": len(persisted_recommendations),
            "departureDate": _destination_departure_date(discovery_request),
            "returnDate": _destination_return_date(discovery_request),
        },
    )
    if provider_response.recommendations and not persisted_recommendations:
        LOGGER.warning(
            "Destination discovery rendered results but persisted no selectable recommendations: %s",
            {"session_id": session.id},
        )
    active_trip = _active_trip_for_session(session.id, db)
    active_trip["pendingAction"] = None
    active_trip["latestVisibleResultType"] = "destinations"
    active_trip["status"] = "destinations"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    results = _destination_results_for_chat(provider_response)
    if provider_response.recommendations:
        top = provider_response.recommendations[0]
        recommendations = provider_response.recommendations
        all_longer_than_short_preference = (
            discovery_request.short_flight_preference
            and recommendations
            and all(
                item.estimatedFlightMinutes is not None
                and item.estimatedFlightMinutes > 180
                for item in recommendations
            )
        )
        requires_skiing = "skiing" in {
            str(value).lower().replace(" ", "_")
            for value in discovery_request.required_activities
        }
        excludes_europe = "europe" in {
            str(value).lower().replace(" ", "_")
            for value in discovery_request.excluded_regions
        }
        if requires_skiing and all_longer_than_short_preference:
            closest = " and ".join(
                f"{item.city} ({item.airportCode})" for item in recommendations[:2]
            )
            if excludes_europe:
                reply = (
                    "I could not find a short-flight skiing destination outside "
                    "Europe in the current candidate set. "
                    f"The closest outside-Europe skiing matches are {closest}, "
                    "but they involve longer flights."
                )
            else:
                reply = (
                    "I could not find a short-flight European skiing destination in "
                    "the current candidate set. "
                    f"The closest skiing matches are {closest}, but they involve longer flights."
                )
        else:
            reply = (
                f"I found destination ideas for you. The strongest match is "
                f"{top.city}, {top.country} ({top.airportCode}) with a score of "
                f"{top.score}/100."
            )
        intro = _destination_discovery_intro(discovery_request)
        if intro:
            reply = f"{intro} {reply}"
        reply += _destination_refinement_note(discovery_request, request.message)
        if any(
            any("Trade-off" in warning for warning in item.warnings)
            for item in provider_response.recommendations
        ):
            reply += " Stronger skiing matches can involve longer flights, so I balanced suitability against travel time."
    else:
        requires_skiing = "skiing" in {
            str(value).lower().replace(" ", "_")
            for value in discovery_request.required_activities
        }
        excludes_europe = "europe" in {
            str(value).lower().replace(" ", "_")
            for value in discovery_request.excluded_regions
        }
        if requires_skiing and excludes_europe:
            reply = (
                "I could not find a strong non-Europe skiing match in the current "
                "candidate set. You may need to expand the candidate list or allow Europe."
            )
            reply += _destination_refinement_note(discovery_request, request.message)
        else:
            reply = "I could not find a strong destination match. Try adding a preference such as beach, skiing, culture, or short flight."

    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "general_enquiry"),
        reply,
        results=results,
    )


def _execute_destination_refinement(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    latest_request = _latest_destination_discovery_request(session.id, db)
    if latest_request is None:
        return None
    refined_request = _merge_destination_refinement(latest_request, request.message)
    return _execute_destination_discovery_tool(
        TravelToolCall(
            tool="discover_destinations",
            arguments=refined_request.model_dump(by_alias=True, mode="json"),
        ),
        db,
        session,
        request,
    )


def _execute_pending_destination_date_followup(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    pending_request = _pending_destination_discovery_request(session.id, db)
    if pending_request is None:
        return None
    missing_before = list(pending_request.missing_fields)
    raw_pending = load_session_memory(db, session.id).get("pending_destination_discovery")
    asked_question = (
        raw_pending.get("askedQuestion")
        if isinstance(raw_pending, dict) and isinstance(raw_pending.get("askedQuestion"), str)
        else _pending_destination_question_text(pending_request)
    )
    merged_request = _merge_pending_destination_discovery_dates(
        pending_request,
        request.message,
        asked_question=asked_question,
        default_origin=_default_origin_for_session(session.id, db),
    )
    if merged_request is None:
        active_trip = _active_trip_for_session(session.id, db)
        guarded_question = _clarification_reply_text(
            db,
            session.id,
            active_trip,
            missing_before or ["departureDate"],
            asked_question,
        )
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "general_enquiry"),
            guarded_question,
        )
    if merged_request.origin is None:
        active_trip = _sync_active_trip_from_destination_request(
            db,
            session.id,
            merged_request,
            status="awaiting_origin",
        )
        pending_payload = merged_request.model_dump(by_alias=True, mode="json")
        question_text = _pending_destination_question_text(merged_request)
        pending_payload["askedQuestion"] = question_text
        save_persistent_pending_destination_discovery(db, session.id, pending_payload)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "general_enquiry"),
            _clarification_reply_text(
                db,
                session.id,
                active_trip,
                ["origin"],
                question_text,
            ),
        )
    active_trip = _active_trip_for_session(session.id, db)
    clear_last_clarification(active_trip)
    save_active_trip(db, session.id, active_trip)
    _log_route_trace(
        "pending_destination_discovery",
        "discover_destinations",
        merged_request.origin.code if merged_request.origin else None,
        missing_before,
        list(merged_request.missing_fields),
    )
    return _execute_destination_discovery_tool(
        TravelToolCall(
            tool="discover_destinations",
            arguments=merged_request.model_dump(by_alias=True, mode="json"),
        ),
        db,
        session,
        request,
        preserve_request_origin=True,
    )


def _execute_pending_clarification(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    package_response = _execute_pending_package_clarification(
        db,
        session,
        request,
    )
    if package_response is not None:
        return package_response
    flight_response = _execute_pending_flight_clarification(
        db,
        session,
        request,
    )
    if flight_response is not None:
        return flight_response
    hotel_response = _execute_pending_hotel_clarification(
        db,
        session,
        request,
    )
    if hotel_response is not None:
        return hotel_response
    return _execute_pending_destination_date_followup(db, session, request)


def _execute_pending_package_clarification(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    active_trip = _active_trip_for_session(session.id, db)
    if _pending_package_request(active_trip) is None:
        return None

    pending = _merge_package_clarification(
        active_trip,
        request.message,
        start_new=False,
    )
    if pending is None:
        return None
    active_trip = _save_pending_package_request(
        db,
        session.id,
        active_trip,
        pending,
    )
    missing = _pending_package_missing_fields(pending)
    if missing:
        _set_last_tool_used(session.id, "package_clarification", db)
        question_text = _pending_package_question(pending)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "package_search"),
            _clarification_reply_text(
                db,
                session.id,
                active_trip,
                missing,
                question_text,
            ),
        )

    active_trip["pendingPackageRequest"] = None
    clear_last_clarification(active_trip)
    active_trip["status"] = "ready_for_package_or_checkout"
    active_trip["latestVisibleResultType"] = "package_clarification"
    active_trip["revision"] = int(active_trip.get("revision") or 0) + 1
    save_active_trip(db, session.id, active_trip)
    return _execute_package_request_if_possible(
        db,
        session,
        request,
        force=True,
    )


def is_partial_date_followup(message: str) -> bool:
    """Identify messages that modify context rather than naming a full route."""

    normalized = _normalize_control_message(message)
    if re.search(r"\bfrom\b.+\bto\b", normalized):
        return False
    if re.search(r"\b[a-z]{3}\s+(?:to|-)\s+[a-z]{3}\b", normalized):
        return False
    indicators = (
        "what about",
        "same route",
        "same flight",
        "make it",
        "change it to",
        "what about business",
        "what about economy",
        "for 2 passengers",
        "for 3 passengers",
        "for 2 people",
        "for 3 people",
    )
    return (
        resolve_relative_travel_date(normalized, today=today_utc_date()) is not None
        or any(indicator in normalized for indicator in indicators)
    )


def _message_mentions_location(message: str, value: Any) -> bool:
    location = str(value or "").strip()
    if not location:
        return False
    return re.search(rf"\b{re.escape(location.lower())}\b", message.lower()) is not None


def message_mentions_route(
    user_message: str,
    origin_code: str | None,
    destination_code: str | None,
    origin_city: str | None = None,
    destination_city: str | None = None,
) -> bool:
    """Return whether the user explicitly named both ends of a route."""

    mentions_origin = _message_mentions_location(user_message, origin_code) or _message_mentions_location(
        user_message,
        origin_city,
    )
    mentions_destination = _message_mentions_location(
        user_message,
        destination_code,
    ) or _message_mentions_location(user_message, destination_city)
    return mentions_origin and mentions_destination


def _latest_flight_search_request(
    session_id: int,
    db: Session | None = None,
) -> FlightSearchRequest | None:
    request_data: Any = None
    if isinstance(db, Session):
        request_data = load_session_memory(db, session_id).get("search_request")
        if not isinstance(request_data, dict):
            return None
    if not isinstance(request_data, dict):
        memory = FLIGHT_SEARCH_MEMORY.get(session_id)
        request_data = memory.get("search_request") if memory else None
    if not isinstance(request_data, dict):
        return None
    try:
        return FlightSearchRequest.model_validate(request_data)
    except Exception as exc:
        LOGGER.warning("Stored flight follow-up request validation failed: %s", exc.__class__.__name__)
        return None


def _latest_hotel_search_request(
    session_id: int,
    db: Session | None = None,
) -> HotelSearchRequest | None:
    request_data: Any = None
    if isinstance(db, Session):
        request_data = load_session_memory(db, session_id).get("hotel_search_request")
        if not isinstance(request_data, dict):
            return None
    if not isinstance(request_data, dict):
        memory = HOTEL_SEARCH_MEMORY.get(session_id)
        request_data = memory.get("search_request") if memory else None
    if not isinstance(request_data, dict):
        return None
    try:
        return HotelSearchRequest.model_validate(request_data)
    except Exception as exc:
        LOGGER.warning("Stored hotel follow-up request validation failed: %s", exc.__class__.__name__)
        return None


def _active_context_type(
    session_id: int,
    db: Session | None = None,
) -> str | None:
    last_tool: Any = None
    if isinstance(db, Session):
        last_tool = load_session_memory(db, session_id).get("last_tool")
    if not last_tool and not isinstance(db, Session):
        for memory in (
            HOTEL_SEARCH_MEMORY.get(session_id),
            FLIGHT_SEARCH_MEMORY.get(session_id),
        ):
            if memory and memory.get("last_tool_used"):
                last_tool = memory["last_tool_used"]
                break
    tool_name = str(last_tool or "")
    if "destination" in tool_name:
        return "destination"
    if "package" in tool_name or "cart" in tool_name or "checkout" in tool_name:
        return "package"
    if "hotel" in tool_name:
        return "hotel"
    if "flight" in tool_name:
        return "flight"
    return None


def _cabin_class_from_message(message: str) -> str | None:
    normalized = _normalize_control_message(message)
    if "premium economy" in normalized:
        return "premium_economy"
    if "business" in normalized:
        return "business"
    if "first class" in normalized or normalized == "first":
        return "first"
    if "economy" in normalized:
        return "economy"
    return None


def _passenger_count_from_message(message: str) -> int | None:
    normalized = _normalize_control_message(message)
    match = re.search(
        r"\b(?:make it|change it to|for|we are)\s+([1-9])\s+(?:passenger|passengers|people|traveller|travelers|travellers)\b",
        normalized,
    )
    if match:
        return int(match.group(1))
    match = re.search(r"\b(?:make it|change it to|we are)\s+([1-9])\b", normalized)
    return int(match.group(1)) if match else None


def _ambiguous_destination_followup(
    message: str,
    latest_request: FlightSearchRequest,
) -> bool:
    normalized = _normalize_control_message(message)
    if "what about" not in normalized:
        return False
    match = re.search(r"\bwhat about\s+(.+)$", message, re.IGNORECASE)
    if not match:
        return False
    possible_destination = re.sub(r"[?.!,]+$", "", match.group(1)).strip()
    if not possible_destination:
        return False
    resolved_destination = _known_destination_from_text(possible_destination)
    if resolved_destination is None:
        return False
    route_places = {
        str(latest_request.itinerary.origin.city or "").lower(),
        str(latest_request.itinerary.destination.city or "").lower(),
        latest_request.itinerary.origin.code.lower(),
        latest_request.itinerary.destination.code.lower(),
    }
    resolved_places = {
        _normalize_control_message(str(resolved_destination.get("city") or "")),
        str(resolved_destination.get("airportCode") or "").lower(),
    }
    mentioned_new_place = next((place for place in resolved_places if place), None)
    if mentioned_new_place is None or resolved_places.intersection(route_places):
        return False
    return not re.search(
        rf"\b(?:to|destination)\s+{re.escape(mentioned_new_place)}\b",
        normalized,
    )


def merge_flight_followup_with_latest(
    tool_request: FlightSearchRequest | None,
    latest_request: FlightSearchRequest,
    user_message: str,
) -> FlightSearchRequest:
    """Merge a partial request with the current route, never prompt examples."""

    base = latest_request.model_dump(mode="json", by_alias=True)
    candidate = (
        tool_request.model_dump(mode="json", by_alias=True)
        if tool_request is not None
        else None
    )
    partial = is_partial_date_followup(user_message)
    if candidate is not None and not partial:
        return tool_request

    if candidate is not None:
        candidate_itinerary = candidate.get("itinerary", {})
        origin = candidate_itinerary.get("origin", {})
        destination = candidate_itinerary.get("destination", {})
        if message_mentions_route(
            user_message,
            origin.get("code"),
            destination.get("code"),
            origin.get("city"),
            destination.get("city"),
        ):
            return tool_request

    relative_departure = resolve_relative_travel_date(
        user_message,
        today=today_utc_date(),
    )
    if relative_departure:
        base_dates = base["itinerary"]["dates"]
        old_departure = date.fromisoformat(base_dates["departure"])
        old_return = base_dates.get("return")
        base_dates["departure"] = relative_departure
        if old_return:
            duration = max(1, (date.fromisoformat(old_return) - old_departure).days)
            base_dates["return"] = (
                date.fromisoformat(relative_departure) + timedelta(days=duration)
            ).isoformat()

    passenger_count = _passenger_count_from_message(user_message)
    if passenger_count is not None:
        base["passengerCount"] = passenger_count

    cabin_class = _cabin_class_from_message(user_message)
    if cabin_class is not None:
        base["preferences"]["cabinClass"] = cabin_class

    normalized_message = _normalize_control_message(user_message)
    if any(phrase in normalized_message for phrase in ("direct", "non stop", "nonstop", "no stops")):
        base["preferences"]["maxStops"] = 0

    return FlightSearchRequest.model_validate(base)


def _merge_hotel_followup_with_latest(
    latest_request: HotelSearchRequest,
    user_message: str,
) -> HotelSearchRequest:
    payload = latest_request.model_dump(mode="json", by_alias=True)
    relative_check_in = resolve_relative_travel_date(
        user_message,
        today=today_utc_date(),
    )
    if relative_check_in:
        old_check_in = date.fromisoformat(payload["checkInDate"])
        old_check_out = date.fromisoformat(payload["checkOutDate"])
        nights = max(1, (old_check_out - old_check_in).days)
        payload["checkInDate"] = relative_check_in
        payload["checkOutDate"] = (
            date.fromisoformat(relative_check_in) + timedelta(days=nights)
        ).isoformat()
    return HotelSearchRequest.model_validate(payload)


def _route_clarification_response(
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    return _tool_response(
        db,
        session,
        request,
        _build_control_intent(request.message, "flight_search"),
        "I can do that, but I need the route first. Where are you flying from and to?",
    )


def _tool_ranking(arguments: dict[str, Any]) -> tuple[str, int] | None:
    ranking = arguments.get("ranking")
    if ranking not in {
        "cheapest",
        "fastest",
        "best",
        "earliest",
        "latest",
        "cheapest_fastest",
        "best_value",
    }:
        return None

    limit = arguments.get("limit", 1)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        return None
    return ranking, min(limit, 10)


def _tool_option_number(arguments: dict[str, Any]) -> int | None:
    option_number = arguments.get("option_number")
    if not isinstance(option_number, int) or isinstance(option_number, bool):
        return None
    return option_number if option_number >= 1 else None


def _selection_from_tool_call(
    tool_call: TravelToolCall,
) -> tuple[str, int | None] | None:
    selection = tool_call.arguments.get("selection")
    if selection not in {"current_displayed", "cheapest", "fastest", "option_number"}:
        return None

    option_number = _tool_option_number(tool_call.arguments)
    if selection == "option_number" and option_number is None:
        return None
    return selection, option_number


def _availability_selection_from_tool_call(
    tool_call: TravelToolCall,
) -> tuple[str, int | None] | None:
    selection = tool_call.arguments.get("selection", "selected_pending")
    if selection not in {
        "current_displayed",
        "selected_pending",
        "cheapest",
        "option_number",
    }:
        return None

    option_number = _tool_option_number(tool_call.arguments)
    if selection == "option_number" and option_number is None:
        return None
    return selection, option_number


def _availability_target(
    session_id: int,
    selection: str,
    option_number: int | None,
    db: Session | None = None,
) -> tuple[int, dict[str, Any]] | None:
    if selection == "selected_pending":
        selected_flight = _selected_pending_flight(session_id, db)
        selected_option = _option_number_for_flight(
            session_id,
            selected_flight.get("id") if selected_flight else None,
            db,
        )
        if selected_option is not None and selected_flight is not None:
            return selected_option, selected_flight

        # If nothing is selected, use the visible flight for natural follow-ups.
        return _current_displayed_flight(session_id, db)
    return _resolve_flight_selection(session_id, selection, option_number, db)


def _same_flight_offer(previous: dict[str, Any], refreshed: dict[str, Any]) -> bool:
    if previous.get("offer_id") and previous.get("offer_id") == refreshed.get(
        "offer_id"
    ):
        return True
    if previous.get("id") and previous.get("id") == refreshed.get("id"):
        return True

    fields = [
        "airline",
        "origin",
        "destination",
        "departure_date",
        "return_date",
        "price",
    ]
    return all(
        previous.get(field) is not None
        and previous.get(field) == refreshed.get(field)
        for field in fields
    )


def _closest_refreshed_flight(
    session_id: int,
    previous_flight: dict[str, Any] | None,
    db: Session | None = None,
) -> tuple[int, dict[str, Any]] | None:
    memory = _latest_flight_memory(session_id, db)
    if memory is None or previous_flight is None:
        return None

    previous_price = _flight_price(previous_flight)

    def match_score(item: tuple[int, dict[str, Any]]) -> tuple[int, float, int]:
        option_number, flight = item
        mismatches = sum(
            previous_flight.get(field) != flight.get(field)
            for field in [
                "airline",
                "origin",
                "destination",
                "outbound_departure_date",
                "outbound_departure_time",
                "return_departure_date",
                "cabin_class",
            ]
        )
        refreshed_price = _flight_price(flight)
        price_difference = (
            abs(refreshed_price - previous_price)
            if refreshed_price is not None and previous_price is not None
            else float("inf")
        )
        return mismatches, price_difference, option_number

    return min(enumerate(memory["flights"], start=1), key=match_score)


def _tool_passenger_count(arguments: dict[str, Any]) -> int | None:
    passenger_count = arguments.get("passenger_count")
    if (
        not isinstance(passenger_count, int)
        or isinstance(passenger_count, bool)
        or passenger_count < 1
        or passenger_count > 9
    ):
        return None
    return passenger_count


def _tool_description_field(arguments: dict[str, Any]) -> str | None:
    field = arguments.get("field", "summary")
    if field not in {"cabin_class", "passenger_count", "price", "times", "summary"}:
        return None
    return field


def _fallback_passenger_count(message: str) -> int | None:
    normalized_message = _normalize_control_message(message)
    if not any(
        phrase in normalized_message
        for phrase in ["passenger", "people", "traveller", "travelers", "travellers"]
    ):
        return None

    match = re.search(r"\b([1-9])\s+(?:passenger|people|traveller|travelers|travellers)\b", normalized_message)
    if match:
        return int(match.group(1))

    match = re.search(r"\b(?:we are|make it|change passengers to)\s+([1-9])\b", normalized_message)
    return int(match.group(1)) if match else None


def _fallback_description_field(message: str) -> str | None:
    normalized_message = _normalize_control_message(message)
    if "business or economy" in normalized_message or "cabin" in normalized_message:
        return "cabin_class"
    if "how many passengers" in normalized_message or "how many people" in normalized_message:
        return "passenger_count"
    if any(phrase in normalized_message for phrase in ["what time", "when does it leave"]):
        return "times"
    if any(phrase in normalized_message for phrase in ["how much", "what price", "cost"]):
        return "price"
    return None


def _fallback_ranking(message: str) -> str | None:
    normalized_message = _normalize_control_message(message)
    if (
        ("cheapest" in normalized_message or "cheap" in normalized_message)
        and ("fastest" in normalized_message or "shortest" in normalized_message)
    ):
        return "cheapest_fastest"
    if "best value" in normalized_message:
        return "best_value"
    return None


def _execute_rank_flights_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    if _latest_flight_memory(session.id, db) is None:
        return _flight_context_response(db, session, request)

    ranking_details = _tool_ranking(tool_call.arguments)
    if ranking_details is None:
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "Please specify whether you want the cheapest, fastest, best, earliest, or latest flight.",
        )

    ranking, limit = ranking_details
    ranked_flights = _rank_flights(session.id, ranking, limit, db)
    if not ranked_flights:
        return _flight_context_response(db, session, request)

    first_option, _first_flight = ranked_flights[0]
    _set_current_flight_option(session.id, first_option, db)
    _set_last_tool_used(session.id, "rank_flights", db)
    flights = [flight for _option, flight in ranked_flights]
    labels = {
        "cheapest": "cheapest",
        "fastest": "fastest",
        "best": "best",
        "earliest": "earliest-departing",
        "latest": "latest-departing",
        "cheapest_fastest": "best combined price-and-duration",
        "best_value": "best-value",
    }
    if ranking == "cheapest_fastest":
        reply = (
            f"The best combined option for price and duration is option {first_option}."
        )
    else:
        reply = f"The {labels[ranking]} flight is option {first_option}."

    duration_rankings = {"fastest", "cheapest_fastest", "best", "best_value"}
    if ranking in duration_rankings and any(
        not isinstance(flight.get("duration_minutes"), int)
        for flight in _latest_flight_memory(session.id, db)["flights"]
    ):
        reply += " Duration data is incomplete, so price and stops were also used."
    return _tool_response(
        db,
        session,
        request,
        _flight_follow_up_intent(session.id, request.message, db),
        reply,
        _flight_results_for_chat(flights),
    )


def _execute_show_flight_option_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    memory = _latest_flight_memory(session.id, db)
    if memory is None:
        return _flight_context_response(db, session, request)

    option_number = _tool_option_number(tool_call.arguments)
    match = _flight_option(session.id, option_number, db)
    if match is None:
        count = len(memory["flights"])
        requested = option_number if option_number is not None else "that"
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            f"I could not find option {requested}. Please choose a number between 1 and {count}.",
            _flight_results_for_chat(memory["flights"]),
        )

    selected_option, selected_flight = match
    _set_current_flight_option(session.id, selected_option, db)
    _set_last_tool_used(session.id, "show_flight_option", db)
    return _tool_response(
        db,
        session,
        request,
        _flight_follow_up_intent(session.id, request.message, db),
        f"Here is flight option {selected_option}.",
        _flight_results_for_chat([selected_flight]),
    )


def _execute_select_flight_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    if _latest_flight_memory(session.id, db) is None:
        return _flight_context_response(db, session, request)

    selection_details = _selection_from_tool_call(tool_call)
    if selection_details is None:
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "Please choose a flight option first.",
        )

    selection, option_number = selection_details
    match = _resolve_flight_selection(session.id, selection, option_number, db)
    if match is None:
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "Please choose a flight option first.",
        )

    selected_option, selected_flight = match
    _set_current_flight_option(session.id, selected_option, db)
    _select_pending_flight(session.id, selected_flight, db)
    _set_last_tool_used(session.id, "select_flight", db)
    return _tool_response(
        db,
        session,
        request,
        _flight_follow_up_intent(session.id, request.message, db),
        (
            f"You selected flight option {selected_option}. Would you like to add it "
            "to your cart and continue to checkout?"
        ),
        _flight_results_for_chat([selected_flight]),
    )


def _execute_update_passenger_count_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    memory = _latest_flight_memory(session.id, db)
    if memory is None or not memory.get("search_request"):
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "Please search for flights first, then I can update the passenger count.",
        )

    passenger_count = _tool_passenger_count(tool_call.arguments)
    if passenger_count is None:
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "Please tell me how many passengers will travel, from 1 to 9.",
        )

    try:
        previous_request = FlightSearchRequest.model_validate(memory["search_request"])
    except Exception as exc:
        LOGGER.warning(
            "Stored flight search request validation failed: %s",
            exc.__class__.__name__,
        )
        return _flight_context_response(db, session, request)

    previous_flight = _selected_pending_flight(session.id, db)
    if previous_flight is None:
        current_match = _current_displayed_flight(session.id, db)
        previous_flight = current_match[1] if current_match else None

    refreshed_request = previous_request.model_copy(
        update={"passenger_count": passenger_count}
    )
    provider_response = _search_flights_with_fallback(refreshed_request)
    LOGGER.info(
        "Passenger-count refresh provider used: %s", provider_response.provider_used
    )
    stored_intent = dict(memory["intent"])
    stored_intent["travelers"] = passenger_count
    stored_intent["message_summary"] = request.message[:120]
    _store_latest_flight_results(
        session.id,
        stored_intent,
        provider_response,
        refreshed_request,
        db,
    )
    _set_last_tool_used(session.id, "update_passenger_count", db)

    should_select = tool_call.arguments.get("select_after_refresh") is True
    if should_select:
        match = _closest_refreshed_flight(session.id, previous_flight, db)
        if match is None:
            match = _cheapest_flight(session.id, db)
        if match is not None:
            selected_option, selected_flight = match
            _set_current_flight_option(session.id, selected_option, db)
            _select_pending_flight(session.id, selected_flight, db)
            return _tool_response(
                db,
                session,
                request,
                _flight_follow_up_intent(session.id, request.message, db),
                (
                    f"I refreshed the offer for {passenger_count} passengers. You "
                    f"selected option {selected_option}. Would you like to add it "
                    "to your cart and continue to checkout?"
                ),
                _flight_results_for_chat([selected_flight]),
            )

    passenger_label = (
        "1 passenger" if passenger_count == 1 else f"{passenger_count} passengers"
    )
    return _tool_response(
        db,
        session,
        request,
        _flight_follow_up_intent(session.id, request.message, db),
        f"I updated the search to {passenger_label} and refreshed the flight options.",
        _flight_search_results_for_chat(provider_response),
    )


def _execute_describe_current_flight_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    current_match = _current_displayed_flight(session.id, db)
    if current_match is None:
        return _flight_context_response(db, session, request)

    description_field = _tool_description_field(tool_call.arguments)
    if description_field is None:
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "I can describe the current flight's cabin, passengers, price, times, or summary.",
        )

    option_number, flight = current_match
    cabin_label = str(
        flight.get("cabin_class_label")
        or _cabin_class_label(flight.get("cabin_class"))
    )
    passenger_count = int(flight.get("passenger_count") or 1)
    passenger_label = (
        "1 passenger" if passenger_count == 1 else f"{passenger_count} passengers"
    )
    if description_field == "cabin_class":
        reply = f"The current flight is {cabin_label} class."
    elif description_field == "passenger_count":
        reply = f"The current flight is currently priced for {passenger_label}."
    elif description_field == "price":
        price = flight.get("price") or "not available"
        currency = flight.get("currency") or ""
        reply = f"The current flight costs {price} {currency}.".strip()
    elif description_field == "times":
        outbound = " ".join(
            part
            for part in [
                flight.get("outbound_departure_date"),
                flight.get("outbound_departure_time"),
            ]
            if part
        )
        returning = " ".join(
            part
            for part in [
                flight.get("return_departure_date"),
                flight.get("return_departure_time"),
            ]
            if part
        )
        reply = f"Option {option_number} departs {outbound or 'at an unavailable time'}."
        if returning:
            reply += f" The return departs {returning}."
    else:
        reply = (
            f"Option {option_number} is {flight.get('airline') or 'an airline'} "
            f"from {flight.get('origin')} to {flight.get('destination')}, {cabin_label}, "
            f"for {passenger_label}."
        )

    _set_last_tool_used(session.id, "describe_current_flight", db)
    return _tool_response(
        db,
        session,
        request,
        _flight_follow_up_intent(session.id, request.message, db),
        reply,
        _flight_results_for_chat([flight]),
    )


def _execute_availability_tool(
    tool_call: TravelToolCall,
    db: Session,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    memory = _latest_flight_memory(session.id, db)
    if memory is None or not memory.get("search_request"):
        return _flight_context_response(db, session, request)

    selection_details = _availability_selection_from_tool_call(tool_call)
    if selection_details is None:
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "Please choose a flight option first.",
        )

    selection, option_number = selection_details
    target = _availability_target(session.id, selection, option_number, db)
    if target is None:
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "Please choose a flight option first.",
        )

    _target_option, target_flight = target
    was_selected = _selected_pending_flight(session.id, db)
    try:
        search_request = FlightSearchRequest.model_validate(memory["search_request"])
    except Exception as exc:
        LOGGER.warning("Stored flight search request validation failed: %s", exc.__class__.__name__)
        return _flight_context_response(db, session, request)

    provider_response = _search_flights_with_fallback(search_request)
    LOGGER.info("Availability refresh provider used: %s", provider_response.provider_used)
    stored_intent = dict(memory["intent"])
    _store_latest_flight_results(
        session.id,
        stored_intent,
        provider_response,
        search_request,
        db,
    )
    refreshed_memory = _latest_flight_memory(session.id, db)
    refreshed_flights = refreshed_memory["flights"] if refreshed_memory else []
    matched = next(
        (
            (index, flight)
            for index, flight in enumerate(refreshed_flights, start=1)
            if _same_flight_offer(target_flight, flight)
        ),
        None,
    )
    _set_last_tool_used(session.id, "check_flight_availability", db)

    if matched is not None:
        matched_option, matched_flight = matched
        _set_current_flight_option(session.id, matched_option, db)
        if was_selected is not None:
            _select_pending_flight(session.id, matched_flight, db)
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "This offer still appears in the latest Duffel search and appears available.",
            _flight_results_for_chat([matched_flight]),
        )

    alternative = _closest_refreshed_flight(session.id, target_flight, db)
    if alternative is None:
        alternative = _cheapest_flight(session.id, db)
    if alternative is None:
        return _tool_response(
            db,
            session,
            request,
            _flight_follow_up_intent(session.id, request.message, db),
            "This exact offer may have expired or changed. I refreshed the search, but no current options were returned.",
        )

    alternative_option, alternative_flight = alternative
    _set_current_flight_option(session.id, alternative_option, db)
    return _tool_response(
        db,
        session,
        request,
        _flight_follow_up_intent(session.id, request.message, db),
        "This exact offer may have expired or changed. I refreshed the search and found the latest closest alternative.",
        _flight_results_for_chat([alternative_flight]),
    )


def _execute_booking_lookup_tool(
    tool_call: TravelToolCall,
    db: Session,
    user: User,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    booking_reference = tool_call.arguments.get("booking_reference")
    if not isinstance(booking_reference, str) or not booking_reference.strip():
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "booking_retrieval"),
            "Please provide a booking reference, such as TA123456.",
        )

    intent = _build_control_intent(request.message, "booking_retrieval")
    intent["booking_reference"] = booking_reference.strip().upper()
    results = _search_booking_from_database(
        db,
        intent["booking_reference"],
        user_id=user.id,
    )
    return _tool_response(
        db,
        session,
        request,
        intent,
        _build_mock_reply(intent, results),
        results,
    )


def _execute_tool_call(
    tool_call: TravelToolCall,
    db: Session,
    user: User,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse | None:
    tool_call = _coerce_nested_structured_tool_call(tool_call) or tool_call
    tool_call = _coerce_tool_call_for_active_trip(
        tool_call,
        _active_trip_for_session(session.id, db),
    ) or tool_call
    LOGGER.info("Executing tool: %s", tool_call.tool)

    structured_cart_response = _execute_structured_cart_tool(
        tool_call,
        db,
        user,
        session,
        request,
    )
    if structured_cart_response is not None:
        _set_last_tool_used(session.id, tool_call.tool, db)
        return structured_cart_response

    has_pending_flight = _selected_pending_flight(session.id, db) is not None
    has_pending_hotel = _selected_pending_hotel(session.id, db) is not None
    has_pending_booking = session.id in PENDING_BOOKINGS
    if _should_handle_cancellation(request.message, session.id, db):
        if tool_call.tool != "cancel_booking":
            LOGGER.info("Overriding tool with pending-booking cancellation.")
        return _handle_cancellation_message(db, session, request)

    if (
        tool_call.tool == "confirm_booking"
        and _pending_destination_suggestion_action(_active_trip_for_session(session.id, db)) is not None
    ):
        destination_suggestion_response = _execute_pending_destination_suggestion_confirmation(
            db,
            session,
            request,
        )
        if destination_suggestion_response is not None:
            return destination_suggestion_response

    if tool_call.tool == "confirm_booking":
        destination_results_confirmation = _execute_destination_results_confirmation_if_possible(
            db,
            session,
            request,
        )
        if destination_results_confirmation is not None:
            return destination_results_confirmation

    if _pending_hotel_request(_active_trip_for_session(session.id, db)) is not None:
        structured_hotel_response = _execute_pending_hotel_structured_clarification(
            tool_call,
            db,
            session,
            request,
        )
        if structured_hotel_response is not None:
            return structured_hotel_response
        pending_hotel_response = _execute_pending_hotel_clarification(
            db,
            session,
            request,
        )
        if pending_hotel_response is not None:
            return pending_hotel_response

    if tool_call.tool == "handle_visible_results":
        visible_results_response = _execute_visible_results_tool(
            tool_call,
            db,
            user,
            session,
            request,
        )
        if visible_results_response is not None:
            return visible_results_response

    package_results_response = _execute_package_results_followup_if_possible(
        db,
        session,
        request,
    )
    if package_results_response is not None:
        return package_results_response

    if tool_call.tool == "prepare_trip_request":
        return _execute_prepare_trip_request_tool(tool_call, db, session, request)

    if tool_call.tool == "search_alternative_origins":
        return _execute_search_alternative_origins_tool(tool_call, db, session, request)

    thank_you_response = _execute_thank_you_if_possible(db, session, request)
    if thank_you_response is not None:
        return thank_you_response

    cheapest_result_response = _execute_contextual_cheapest_result_if_possible(
        db,
        session,
        request,
    )
    if cheapest_result_response is not None:
        return cheapest_result_response

    this_flight_response = _execute_this_flight_reference_if_possible(
        db,
        session,
        request,
    )
    if this_flight_response is not None:
        return this_flight_response

    this_hotel_response = _execute_this_hotel_reference_if_possible(
        db,
        session,
        request,
    )
    if this_hotel_response is not None:
        return this_hotel_response

    flight_cart_response = _execute_selected_flight_cart_if_possible(
        db,
        session,
        request,
    )
    if flight_cart_response is not None:
        return flight_cart_response

    hotel_cart_response = _execute_selected_hotel_cart_if_possible(
        db,
        session,
        request,
    )
    if hotel_cart_response is not None:
        return hotel_cart_response

    if (
        _is_confirmation_message(request.message)
        and (
            _pending_package_build_action(_active_trip_for_session(session.id, db)) is not None
            or has_pending_flight
            or has_pending_hotel
            or has_pending_booking
        )
    ):
        package_build_response = _execute_pending_package_build_confirmation(
            db,
            session,
            request,
        )
        if package_build_response is not None:
            return package_build_response
        if tool_call.tool != "confirm_booking":
            LOGGER.info("Overriding tool with pending-booking confirmation.")
        return _handle_confirmation_message(db, user, session, request)

    multi_city_response = _execute_multi_city_request_if_possible(db, session, request)
    if multi_city_response is not None:
        return multi_city_response

    multi_city_date_response = _execute_multi_city_date_followup_if_possible(
        db,
        session,
        request,
    )
    if multi_city_date_response is not None:
        return multi_city_date_response

    dateless_destination_response = _execute_dateless_destination_discovery_if_possible(
        db,
        session,
        request,
    )
    if dateless_destination_response is not None:
        return dateless_destination_response

    comparison_response = _execute_destination_set_flight_comparison_if_possible(
        db,
        session,
        request,
    )
    if comparison_response is not None:
        return comparison_response

    date_selection_response = _execute_date_selection_followup_if_possible(
        db,
        session,
        request,
    )
    if date_selection_response is not None:
        return date_selection_response

    option_selection_response = _execute_contextual_option_selection_if_possible(
        db,
        user,
        session,
        request,
    )
    if option_selection_response is not None:
        return option_selection_response

    package_selection_response = _execute_package_selection_if_possible(
        db,
        session,
        request,
    )
    if package_selection_response is not None:
        return package_selection_response

    flight_cart_response = _execute_selected_flight_cart_if_possible(
        db,
        session,
        request,
    )
    if flight_cart_response is not None:
        return flight_cart_response

    hotel_cart_response = _execute_selected_hotel_cart_if_possible(
        db,
        session,
        request,
    )
    if hotel_cart_response is not None:
        return hotel_cart_response

    package_checkout_response = _execute_package_checkout_if_possible(
        db,
        user,
        session,
        request,
    )
    if package_checkout_response is not None:
        return package_checkout_response

    hotel_selection_response = _execute_hotel_selection_or_confirmation_if_possible(
        db,
        user,
        session,
        request,
    )
    if hotel_selection_response is not None:
        return hotel_selection_response

    pending_package_response = _execute_pending_package_clarification(
        db,
        session,
        request,
    )
    if pending_package_response is not None:
        return pending_package_response

    pending_flight_response = _execute_pending_flight_clarification(
        db,
        session,
        request,
    )
    if pending_flight_response is not None:
        return pending_flight_response

    cheapest_day_response = _execute_cheapest_day_for_selected_destination_if_possible(
        db,
        session,
        request,
    )
    if cheapest_day_response is not None:
        return cheapest_day_response

    if not _looks_like_hotel_only_request(request.message) and not _looks_like_package_request(request.message):
        explicit_destination_response = _execute_explicit_destination_trip_if_possible(
            db,
            session,
            request,
        )
        if explicit_destination_response is not None:
            return explicit_destination_response

    pending_hotel_response = _execute_pending_hotel_clarification(
        db,
        session,
        request,
    )
    if pending_hotel_response is not None:
        return pending_hotel_response

    valid_hotel_tool_request = (
        tool_call.tool == "search_hotels"
        and _hotel_search_from_tool_call(tool_call, request.message) is not None
    )
    if not valid_hotel_tool_request:
        hotel_only_response = _execute_hotel_only_request_if_possible(
            db,
            session,
            request,
        )
        if hotel_only_response is not None:
            return hotel_only_response

    if not valid_hotel_tool_request:
        field_collection_response = _execute_flexible_trip_field_collection_if_possible(
            db,
            session,
            request,
        )
        if field_collection_response is not None:
            return field_collection_response

    selection_response = _execute_destination_selection_if_possible(
        db,
        session,
        request,
    )
    if selection_response is not None:
        return selection_response

    package_start_response = _execute_package_request_if_possible(
        db,
        session,
        request,
    )
    if package_start_response is not None:
        return package_start_response

    pending_destination_response = _execute_pending_destination_date_followup(
        db,
        session,
        request,
    )
    if pending_destination_response is not None:
        return pending_destination_response

    if not valid_hotel_tool_request:
        field_collection_response = _execute_flexible_trip_field_collection_if_possible(
            db,
            session,
            request,
        )
        if field_collection_response is not None:
            return field_collection_response

    cheaper_followup_response = _execute_contextual_cheaper_followup_if_possible(
        db,
        session,
        request,
    )
    if cheaper_followup_response is not None:
        return cheaper_followup_response

    # Keep package option flow on the existing fallback path.
    if (
        session.id in PENDING_BOOKINGS
        and _is_package_option_message(request.message)
        and tool_call.tool
        in {"rank_flights", "show_flight_option", "select_flight", "general_help"}
    ):
        return None

    selection_response = _execute_destination_selection_if_possible(
        db,
        session,
        request,
    )
    if selection_response is not None:
        return selection_response

    package_selection_response = _execute_package_selection_if_possible(
        db,
        session,
        request,
    )
    if package_selection_response is not None:
        return package_selection_response

    package_checkout_response = _execute_package_checkout_if_possible(
        db,
        user,
        session,
        request,
    )
    if package_checkout_response is not None:
        return package_checkout_response

    package_response = _execute_package_request_if_possible(db, session, request)
    if package_response is not None:
        return package_response

    active_trip_product_response = _execute_active_trip_product_request_if_possible(
        db,
        session,
        request,
    )
    if active_trip_product_response is not None:
        return active_trip_product_response

    if (
        _looks_like_destination_refinement_message(request.message)
        and _has_latest_destination_discovery(session.id, db)
    ):
        refined_response = _execute_destination_refinement(db, session, request)
        if refined_response is not None:
            return refined_response

    if (
        tool_call.tool in {"search_flights", "general_help"}
        and _looks_like_destination_discovery_message(request.message)
    ):
        return _execute_destination_discovery_tool(
            _fallback_destination_discovery_tool(request.message),
            db,
            session,
            request,
        )

    if (
        tool_call.tool in {"search_flights", "general_help"}
        and _looks_like_destination_only_flight_request(request.message)
    ):
        default_origin = _default_origin_for_session(session.id, db)
        if default_origin:
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "flight_search"),
                (
                    f"I can use your detected nearest airport, "
                    f"{_origin_message(default_origin)}, as the origin. "
                    "Please provide your departure date and return date."
                ),
            )
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            "Where are you flying from?",
        )

    if tool_call.tool == "search_flights":
        partial_followup = is_partial_date_followup(request.message)
        latest_flight_request = _latest_flight_search_request(session.id, db)
        latest_hotel_request = _latest_hotel_search_request(session.id, db)
        active_context = _active_context_type(session.id, db)
        if partial_followup and active_context == "hotel" and latest_hotel_request:
            refreshed_hotel_request = _merge_hotel_followup_with_latest(
                latest_hotel_request,
                request.message,
            )
            return _execute_hotel_search_request(
                db,
                session,
                request,
                refreshed_hotel_request,
            )
        if partial_followup and latest_flight_request is None:
            return _route_clarification_response(db, session, request)
        flight_search = _flight_search_from_tool_call(tool_call, request.message)
        if partial_followup and latest_flight_request is not None:
            if _ambiguous_destination_followup(request.message, latest_flight_request):
                return _tool_response(
                    db,
                    session,
                    request,
                    _build_control_intent(request.message, "flight_search"),
                    "Would you like to change the destination? Please say where you would like to fly.",
                )
            flight_search = merge_flight_followup_with_latest(
                flight_search,
                latest_flight_request,
                request.message,
            )
        if flight_search is None:
            if _looks_like_destination_only_flight_request(request.message):
                default_origin = _default_origin_for_session(session.id, db)
                if default_origin:
                    return _tool_response(
                        db,
                        session,
                        request,
                        _build_control_intent(request.message, "flight_search"),
                        (
                            f"I can use your detected nearest airport, "
                            f"{_origin_message(default_origin)}, as the origin. "
                            "Please provide your departure date and return date."
                        ),
                    )
                return _tool_response(
                    db,
                    session,
                    request,
                    _build_control_intent(request.message, "flight_search"),
                    "Where are you flying from?",
                )
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "flight_search"),
                "Please provide an origin, destination, departure date, and return date for your flight search.",
            )
        active_trip = _active_trip_for_session(session.id, db)
        flight_search = validate_flight_search_against_active_trip(
            flight_search,
            active_trip,
            request.message,
        )
        return _execute_flight_search_request(db, session, request, flight_search)

    if tool_call.tool == "search_hotels":
        partial_followup = is_partial_date_followup(request.message)
        latest_flight_request = _latest_flight_search_request(session.id, db)
        latest_hotel_request = _latest_hotel_search_request(session.id, db)
        active_context = _active_context_type(session.id, db)
        if partial_followup and active_context == "flight" and latest_flight_request:
            refreshed_flight_request = merge_flight_followup_with_latest(
                None,
                latest_flight_request,
                request.message,
            )
            return _execute_flight_search_request(
                db,
                session,
                request,
                refreshed_flight_request,
            )
        if partial_followup and active_context == "hotel" and latest_hotel_request:
            return _execute_hotel_search_request(
                db,
                session,
                request,
                _merge_hotel_followup_with_latest(latest_hotel_request, request.message),
            )
        hotel_search = _hotel_search_from_tool_call(tool_call, request.message)
        if hotel_search is None:
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "hotel_search"),
                "Please provide a city, check-in date, and check-out date for your hotel search.",
            )
        return _execute_hotel_search_request(db, session, request, hotel_search)

    if tool_call.tool == "discover_destinations":
        return _execute_destination_discovery_tool(tool_call, db, session, request)

    if tool_call.tool in {"select_destination", "search_trip_options"}:
        requested_products = tool_call.arguments.get("requestedProducts")
        if not isinstance(requested_products, list):
            requested_products = tool_call.arguments.get("requested_products")
        products = [
            str(product)
            for product in requested_products or _requested_products_from_message(request.message)
            if str(product) in {"flights", "hotels"}
        ]
        response = _execute_destination_selection_if_possible(
            db,
            session,
            request,
            requested_products=products or None,
        )
        if response is not None:
            return response
        active_response = _execute_active_trip_product_request_if_possible(
            db,
            session,
            request,
        )
        if active_response is not None:
            return active_response
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "general_enquiry"),
            "Please choose one of the destination options first.",
        )

    if tool_call.tool == "rank_flights":
        return _execute_rank_flights_tool(tool_call, db, session, request)

    if tool_call.tool == "show_flight_option":
        return _execute_show_flight_option_tool(tool_call, db, session, request)

    if tool_call.tool == "select_flight":
        return _execute_select_flight_tool(tool_call, db, session, request)

    if tool_call.tool == "update_passenger_count":
        return _execute_update_passenger_count_tool(tool_call, db, session, request)

    if tool_call.tool == "describe_current_flight":
        return _execute_describe_current_flight_tool(tool_call, db, session, request)

    if tool_call.tool == "check_flight_availability":
        return _execute_availability_tool(tool_call, db, session, request)

    if tool_call.tool == "rank_hotels":
        return _execute_rank_hotels_tool(tool_call, db, session, request)

    if tool_call.tool == "show_hotel_option":
        return _execute_show_hotel_option_tool(tool_call, db, session, request)

    if tool_call.tool == "select_hotel":
        return _execute_select_hotel_tool(tool_call, db, session, request)

    if tool_call.tool in {"confirm_booking", "confirm_hotel_booking"}:
        package_build_response = _execute_pending_package_build_confirmation(
            db,
            session,
            request,
        )
        if package_build_response is not None:
            _set_last_tool_used(session.id, "build_package_options", db)
            return package_build_response
        flight_cart_response = _execute_selected_flight_cart_if_possible(
            db,
            session,
            request,
        )
        if flight_cart_response is not None:
            return flight_cart_response
        has_pending_flight = _selected_pending_flight(session.id, db) is not None
        has_pending_hotel = _selected_pending_hotel(session.id, db) is not None
        if session.id in PENDING_BOOKINGS or has_pending_flight or has_pending_hotel:
            response = _handle_confirmation_message(db, user, session, request)
            _set_last_tool_used(session.id, tool_call.tool, db)
            return response
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "general_enquiry"),
            "Please choose a flight option first.",
        )

    if tool_call.tool == "cancel_booking":
        has_pending_flight = _selected_pending_flight(session.id, db) is not None
        has_pending_hotel = _selected_pending_hotel(session.id, db) is not None
        if session.id in PENDING_BOOKINGS or has_pending_flight or has_pending_hotel:
            response = _handle_cancellation_message(db, session, request)
            _set_last_tool_used(session.id, "cancel_booking", db)
            return response
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "general_enquiry"),
            "There is no pending booking to cancel.",
        )

    if tool_call.tool == "booking_lookup":
        return _execute_booking_lookup_tool(tool_call, db, user, session, request)

    if tool_call.tool == "general_help":
        active_trip_for_general_help = _active_trip_for_session(session.id, db)
        pending_trip_for_general_help = active_trip_for_general_help.get("pendingTripRequest")
        if isinstance(pending_trip_for_general_help, dict):
            return _execute_prepare_trip_request_tool(
                TravelToolCall(
                    tool="prepare_trip_request",
                    arguments={
                        "intent": pending_trip_for_general_help.get("intent") or "package_search",
                        "use_active_trip_state": True,
                    },
                ),
                db,
                session,
                request,
            )
        if is_partial_date_followup(request.message):
            active_context = _active_context_type(session.id, db)
            latest_flight_request = _latest_flight_search_request(session.id, db)
            latest_hotel_request = _latest_hotel_search_request(session.id, db)
            if active_context == "flight" and latest_flight_request:
                return _execute_flight_search_request(
                    db,
                    session,
                    request,
                    merge_flight_followup_with_latest(
                        None,
                        latest_flight_request,
                        request.message,
                    ),
                )
            if active_context == "hotel" and latest_hotel_request:
                return _execute_hotel_search_request(
                    db,
                    session,
                    request,
                    _merge_hotel_followup_with_latest(
                        latest_hotel_request,
                        request.message,
                    ),
                )
            return _route_clarification_response(db, session, request)
        rule_intent = extract_travel_intent(request.message)
        if _looks_like_destination_discovery_message(request.message):
            return _execute_destination_discovery_tool(
                _fallback_destination_discovery_tool(request.message),
                db,
                session,
                request,
            )
        if rule_intent["intent_type"] == "hotel_search":
            hotel_search = _hotel_search_from_message(request.message)
            if hotel_search is not None:
                return _execute_hotel_search_request(db, session, request, hotel_search)
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "hotel_search"),
                "Please provide a city, check-in date, and check-out date for your hotel search.",
            )
        if _looks_like_destination_only_flight_request(request.message):
            default_origin = _default_origin_for_session(session.id, db)
            if default_origin:
                return _tool_response(
                    db,
                    session,
                    request,
                    _build_control_intent(request.message, "flight_search"),
                    (
                        f"I can use your detected nearest airport, "
                        f"{_origin_message(default_origin)}, as the origin. "
                        "Please provide your departure date and return date."
                    ),
                )
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "flight_search"),
                "Where are you flying from?",
            )
        if rule_intent["intent_type"] == "package_search":
            return None
        if _hotel_context_is_current(session.id, db):
            option_number = _extract_option_number(request.message)
            if option_number is not None and _is_show_option_message(request.message):
                return _execute_show_hotel_option_tool(
                    TravelToolCall(tool="show_hotel_option", arguments={"option_number": option_number}),
                    db,
                    session,
                    request,
                )
            if _is_current_flight_reference(request.message):
                return _execute_select_hotel_tool(
                    TravelToolCall(tool="select_hotel", arguments={"selection": "current_displayed"}),
                    db,
                    session,
                    request,
                )
            normalized_message = _normalize_control_message(request.message)
            if "cheapest" in normalized_message or "lowest price" in normalized_message:
                return _execute_rank_hotels_tool(
                    TravelToolCall(tool="rank_hotels", arguments={"ranking": "cheapest"}),
                    db,
                    session,
                    request,
                )
        passenger_count = _fallback_passenger_count(request.message)
        if passenger_count is not None and _latest_flight_memory(session.id, db) is not None:
            return _execute_update_passenger_count_tool(
                TravelToolCall(
                    tool="update_passenger_count",
                    arguments={
                        "passenger_count": passenger_count,
                        "select_after_refresh": "book" in request.message.lower(),
                    },
                ),
                db,
                session,
                request,
            )
        description_field = _fallback_description_field(request.message)
        if description_field is not None:
            return _execute_describe_current_flight_tool(
                TravelToolCall(
                    tool="describe_current_flight",
                    arguments={"field": description_field},
                ),
                db,
                session,
                request,
            )
        ranking = _fallback_ranking(request.message)
        if ranking is not None:
            return _execute_rank_flights_tool(
                TravelToolCall(
                    tool="rank_flights",
                    arguments={"ranking": ranking, "limit": 1},
                ),
                db,
                session,
                request,
            )
        if _is_current_flight_reference(request.message):
            return _execute_select_flight_tool(
                TravelToolCall(
                    tool="select_flight",
                    arguments={"selection": "current_displayed"},
                ),
                db,
                session,
                request,
            )
        if _is_flight_availability_message(request.message):
            return _flight_context_response(db, session, request)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "general_enquiry"),
            "Hello, I'm The Voyager. I can help you search flights and hotels, "
            "compare options, check availability, prepare bookings for confirmation, "
            "and guide you through Voyage AI's cart and checkout.",
        )

    return None


def _is_flight_availability_message(message: str) -> bool:
    normalized_message = _normalize_control_message(message)
    return any(
        phrase in normalized_message
        for phrase in [
            "available",
            "still there",
            "price still valid",
            "offer still valid",
            "still valid",
        ]
    )


def _rule_based_chat_fallback(
    db: Session,
    user: User,
    session: ChatSession,
    request: ChatRequest,
) -> ChatResponse:
    multi_city_response = _execute_multi_city_request_if_possible(db, session, request)
    if multi_city_response is not None:
        return multi_city_response

    multi_city_date_response = _execute_multi_city_date_followup_if_possible(
        db,
        session,
        request,
    )
    if multi_city_date_response is not None:
        return multi_city_date_response

    dateless_destination_response = _execute_dateless_destination_discovery_if_possible(
        db,
        session,
        request,
    )
    if dateless_destination_response is not None:
        return dateless_destination_response

    comparison_response = _execute_destination_set_flight_comparison_if_possible(
        db,
        session,
        request,
    )
    if comparison_response is not None:
        return comparison_response

    date_selection_response = _execute_date_selection_followup_if_possible(
        db,
        session,
        request,
    )
    if date_selection_response is not None:
        return date_selection_response

    cheapest_day_response = _execute_cheapest_day_for_selected_destination_if_possible(
        db,
        session,
        request,
    )
    if cheapest_day_response is not None:
        return cheapest_day_response

    pending_package_response = _execute_pending_package_clarification(
        db,
        session,
        request,
    )
    if pending_package_response is not None:
        return pending_package_response

    selection_response = _execute_destination_selection_if_possible(
        db,
        session,
        request,
    )
    if selection_response is not None:
        return selection_response

    package_start_response = _execute_package_request_if_possible(
        db,
        session,
        request,
    )
    if package_start_response is not None:
        return package_start_response

    if not _looks_like_hotel_only_request(request.message) and not _looks_like_package_request(request.message):
        explicit_destination_response = _execute_explicit_destination_trip_if_possible(
            db,
            session,
            request,
        )
        if explicit_destination_response is not None:
            return explicit_destination_response

    pending_destination_response = _execute_pending_destination_date_followup(
        db,
        session,
        request,
    )
    if pending_destination_response is not None:
        return pending_destination_response

    cheaper_followup_response = _execute_contextual_cheaper_followup_if_possible(
        db,
        session,
        request,
    )
    if cheaper_followup_response is not None:
        return cheaper_followup_response

    selection_response = _execute_destination_selection_if_possible(
        db,
        session,
        request,
    )
    if selection_response is not None:
        return selection_response

    active_trip_product_response = _execute_active_trip_product_request_if_possible(
        db,
        session,
        request,
    )
    if active_trip_product_response is not None:
        return active_trip_product_response

    if is_partial_date_followup(request.message):
        active_context = _active_context_type(session.id, db)
        latest_flight_request = _latest_flight_search_request(session.id, db)
        latest_hotel_request = _latest_hotel_search_request(session.id, db)
        if active_context == "flight" and latest_flight_request:
            passenger_count = _passenger_count_from_message(request.message)
            if passenger_count is not None:
                return _execute_update_passenger_count_tool(
                    TravelToolCall(
                        tool="update_passenger_count",
                        arguments={
                            "passenger_count": passenger_count,
                            "select_after_refresh": "book" in request.message.lower(),
                        },
                    ),
                    db,
                    session,
                    request,
                )
            if _ambiguous_destination_followup(request.message, latest_flight_request):
                return _tool_response(
                    db,
                    session,
                    request,
                    _build_control_intent(request.message, "flight_search"),
                    "Would you like to change the destination? Please say where you would like to fly.",
                )
            return _execute_flight_search_request(
                db,
                session,
                request,
                merge_flight_followup_with_latest(
                    None,
                    latest_flight_request,
                    request.message,
                ),
            )
        if active_context == "hotel" and latest_hotel_request:
            return _execute_hotel_search_request(
                db,
                session,
                request,
                _merge_hotel_followup_with_latest(
                    latest_hotel_request,
                    request.message,
                ),
            )
        return _route_clarification_response(db, session, request)

    intent = extract_travel_intent(request.message)
    if (
        _looks_like_destination_refinement_message(request.message)
        and _has_latest_destination_discovery(session.id, db)
    ):
        refined_response = _execute_destination_refinement(db, session, request)
        if refined_response is not None:
            return refined_response

    if (
        _looks_like_flight_search_message(request.message)
        and intent["intent_type"] not in {"hotel_search", "package_search"}
    ):
        intent["intent_type"] = "flight_search"

    if _should_handle_cancellation(request.message, session.id, db):
        return _handle_cancellation_message(db, session, request)

    if _looks_like_destination_discovery_message(request.message):
        return _execute_destination_discovery_tool(
            _fallback_destination_discovery_tool(request.message),
            db,
            session,
            request,
        )

    if _looks_like_destination_only_flight_request(request.message):
        default_origin = _default_origin_for_session(session.id, db)
        if default_origin:
            return _tool_response(
                db,
                session,
                request,
                _build_control_intent(request.message, "flight_search"),
                (
                    f"I can use your detected nearest airport, "
                    f"{_origin_message(default_origin)}, as the origin. "
                    "Please provide your departure date and return date."
                ),
            )
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "flight_search"),
            "Where are you flying from?",
        )

    if intent["intent_type"] == "hotel_search":
        hotel_search = _hotel_search_from_message(request.message)
        if hotel_search is not None:
            return _execute_hotel_search_request(db, session, request, hotel_search)
        return _tool_response(
            db,
            session,
            request,
            _build_control_intent(request.message, "hotel_search"),
            "Please provide a city, check-in date, and check-out date for your hotel search.",
        )

    if _hotel_context_is_current(session.id, db):
        option_number = _extract_option_number(request.message)
        normalized_message = _normalize_control_message(request.message)
        if option_number is not None and _is_show_option_message(request.message):
            return _execute_show_hotel_option_tool(
                TravelToolCall(tool="show_hotel_option", arguments={"option_number": option_number}),
                db,
                session,
                request,
            )
        if option_number is not None and _is_select_option_message(request.message):
            return _execute_select_hotel_tool(
                TravelToolCall(
                    tool="select_hotel",
                    arguments={"selection": "option_number", "option_number": option_number},
                ),
                db,
                session,
                request,
            )
        if _is_current_flight_reference(request.message):
            return _execute_select_hotel_tool(
                TravelToolCall(tool="select_hotel", arguments={"selection": "current_displayed"}),
                db,
                session,
                request,
            )
        if "cheapest" in normalized_message or "lowest price" in normalized_message:
            return _execute_rank_hotels_tool(
                TravelToolCall(tool="rank_hotels", arguments={"ranking": "cheapest"}),
                db,
                session,
                request,
            )

    if _latest_flight_memory(session.id, db) is not None:
        passenger_count = _fallback_passenger_count(request.message)
        if passenger_count is not None:
            return _execute_update_passenger_count_tool(
                TravelToolCall(
                    tool="update_passenger_count",
                    arguments={
                        "passenger_count": passenger_count,
                        "select_after_refresh": "book" in request.message.lower(),
                    },
                ),
                db,
                session,
                request,
            )
        description_field = _fallback_description_field(request.message)
        if description_field is not None:
            return _execute_describe_current_flight_tool(
                TravelToolCall(
                    tool="describe_current_flight",
                    arguments={"field": description_field},
                ),
                db,
                session,
                request,
            )
        ranking = _fallback_ranking(request.message)
        if ranking is not None:
            return _execute_rank_flights_tool(
                TravelToolCall(
                    tool="rank_flights",
                    arguments={"ranking": ranking, "limit": 1},
                ),
                db,
                session,
                request,
            )

    if _is_flight_availability_message(request.message):
        if _latest_flight_memory(session.id, db) is None:
            return _flight_context_response(db, session, request)
        return _execute_availability_tool(
            TravelToolCall(
                tool="check_flight_availability",
                arguments={"selection": "selected_pending"},
            ),
            db,
            session,
            request,
        )

    if session.id in PENDING_BOOKINGS and _is_package_option_message(request.message):
        return _handle_package_option_message(db, session, request)

    if _is_flight_follow_up_message(request.message):
        return _handle_flight_follow_up_message(db, session, request)

    if _is_package_option_message(request.message):
        return _handle_package_option_message(db, session, request)

    if _is_confirmation_message(request.message):
        destination_results_confirmation = _execute_destination_results_confirmation_if_possible(
            db,
            session,
            request,
        )
        if destination_results_confirmation is not None:
            return destination_results_confirmation
        flight_cart_response = _execute_selected_flight_cart_if_possible(
            db,
            session,
            request,
        )
        if flight_cart_response is not None:
            return flight_cart_response
        return _handle_confirmation_message(db, user, session, request)

    return _respond_to_intent(db, session, request, intent)


def _last_two_assistant_messages(db: Session, session_id: int) -> list[Message]:
    return (
        db.query(Message)
        .filter(Message.session_id == session_id, Message.sender == "assistant")
        .order_by(Message.id.desc())
        .limit(2)
        .all()
    )


def _finalize_chat_response(
    db: Session,
    session: ChatSession,
    response: ChatResponse,
) -> ChatResponse:
    assistant_messages = _last_two_assistant_messages(db, session.id)
    if len(assistant_messages) < 2:
        return response
    current_message, previous_message = assistant_messages[0], assistant_messages[1]
    if current_message.content != previous_message.content:
        return response

    active_trip = _active_trip_for_session(session.id, db)
    clarification = active_trip.get("lastClarification")
    escalated: str | None = None
    if isinstance(clarification, dict):
        awaiting = (
            clarification.get("awaiting")
            if isinstance(clarification.get("awaiting"), list)
            else ["departureDate"]
        )
        asked_text = clarification.get("askedText")
        if isinstance(asked_text, str) and asked_text.strip():
            expected_clarification_texts = {
                asked_text,
                disambiguating_clarification_text(asked_text, list(awaiting)),
            }
            if response.reply in expected_clarification_texts:
                escalated = disambiguating_clarification_text(asked_text, list(awaiting))
    if escalated is None:
        escalated = (
            f"{response.reply} If I misunderstood, tell me what you want to change next."
        )
    if escalated == response.reply:
        escalated = f"{response.reply} Please choose one of the concrete options or give the missing detail in a new message."
    LOGGER.warning(
        "Duplicate assistant reply blocked by final chat guard: %s",
        {
            "session_id": session.id,
            "handler": active_trip.get("latestVisibleResultType")
            or response.intent.intent_type,
        },
    )
    current_message.content = escalated
    response.reply = escalated
    db.commit()
    return response


def _chat_unchecked(
    request: ChatRequest,
    db: Session,
    current_user: User | None,
) -> ChatResponse:
    user, session = _resolve_chat_context(db, request, current_user)
    structured_tool_call: TravelToolCall | None = None

    structured_tool_call = _coerce_nested_structured_tool_call(route_tool_with_llama(
        request.message,
        _conversation_state_for_llama(session.id, db),
    ))
    structured_tool_call = _coerce_tool_call_for_active_trip(
        structured_tool_call,
        _active_trip_for_session(session.id, db),
    )
    active_trip_for_structured = _active_trip_for_session(session.id, db)
    has_pending_structured_trip = isinstance(
        active_trip_for_structured.get("pendingTripRequest"),
        dict,
    )
    if structured_tool_call is not None and (
        structured_tool_call.tool in {
        "prepare_trip_request",
        "search_alternative_origins",
        "confirm_booking",
        "add_to_cart",
        "checkout",
        "cart_view",
        }
        or (
            has_pending_structured_trip
            and structured_tool_call.tool
            in {"general_help", "search_flights", "search_hotels"}
        )
    ):
        structured_response = _execute_tool_call(
            structured_tool_call,
            db,
            user,
            session,
            request,
        )
        if structured_response is not None:
            return structured_response

    if _should_handle_cancellation(request.message, session.id, db):
        return _handle_cancellation_message(db, session, request)

    thank_you_response = _execute_thank_you_if_possible(db, session, request)
    if thank_you_response is not None:
        return thank_you_response

    cheapest_result_response = _execute_contextual_cheapest_result_if_possible(
        db,
        session,
        request,
    )
    if cheapest_result_response is not None:
        return cheapest_result_response

    this_flight_response = _execute_this_flight_reference_if_possible(
        db,
        session,
        request,
    )
    if this_flight_response is not None:
        return this_flight_response

    this_hotel_response = _execute_this_hotel_reference_if_possible(
        db,
        session,
        request,
    )
    if this_hotel_response is not None:
        return this_hotel_response

    flight_cart_response = _execute_selected_flight_cart_if_possible(
        db,
        session,
        request,
    )
    if flight_cart_response is not None:
        return flight_cart_response

    hotel_cart_response = _execute_selected_hotel_cart_if_possible(
        db,
        session,
        request,
    )
    if hotel_cart_response is not None:
        return hotel_cart_response

    if (
        _is_confirmation_message(request.message)
        and (
            _pending_package_build_action(_active_trip_for_session(session.id, db)) is not None
            or
            _selected_pending_flight(session.id, db) is not None
            or _selected_pending_hotel(session.id, db) is not None
            or session.id in PENDING_BOOKINGS
        )
    ):
        package_build_response = _execute_pending_package_build_confirmation(
            db,
            session,
            request,
        )
        if package_build_response is not None:
            return package_build_response
        return _handle_confirmation_message(db, user, session, request)

    multi_city_response = _execute_multi_city_request_if_possible(db, session, request)
    if multi_city_response is not None:
        return multi_city_response

    multi_city_date_response = _execute_multi_city_date_followup_if_possible(
        db,
        session,
        request,
    )
    if multi_city_date_response is not None:
        return multi_city_date_response

    dateless_destination_response = _execute_dateless_destination_discovery_if_possible(
        db,
        session,
        request,
    )
    if dateless_destination_response is not None:
        return dateless_destination_response

    comparison_response = _execute_destination_set_flight_comparison_if_possible(
        db,
        session,
        request,
    )
    if comparison_response is not None:
        return comparison_response

    date_selection_response = _execute_date_selection_followup_if_possible(
        db,
        session,
        request,
    )
    if date_selection_response is not None:
        return date_selection_response

    option_selection_response = _execute_contextual_option_selection_if_possible(
        db,
        user,
        session,
        request,
    )
    if option_selection_response is not None:
        return option_selection_response

    package_selection_response = _execute_package_selection_if_possible(
        db,
        session,
        request,
    )
    if package_selection_response is not None:
        return package_selection_response

    flight_cart_response = _execute_selected_flight_cart_if_possible(
        db,
        session,
        request,
    )
    if flight_cart_response is not None:
        return flight_cart_response

    hotel_cart_response = _execute_selected_hotel_cart_if_possible(
        db,
        session,
        request,
    )
    if hotel_cart_response is not None:
        return hotel_cart_response

    package_checkout_response = _execute_package_checkout_if_possible(
        db,
        user,
        session,
        request,
    )
    if package_checkout_response is not None:
        return package_checkout_response

    hotel_selection_response = _execute_hotel_selection_or_confirmation_if_possible(
        db,
        user,
        session,
        request,
    )
    if hotel_selection_response is not None:
        return hotel_selection_response

    pending_package_response = _execute_pending_package_clarification(
        db,
        session,
        request,
    )
    if pending_package_response is not None:
        return pending_package_response

    pending_flight_response = _execute_pending_flight_clarification(
        db,
        session,
        request,
    )
    if pending_flight_response is not None:
        return pending_flight_response

    cheapest_day_response = _execute_cheapest_day_for_selected_destination_if_possible(
        db,
        session,
        request,
    )
    if cheapest_day_response is not None:
        return cheapest_day_response

    pending_hotel_response = _execute_pending_hotel_clarification(
        db,
        session,
        request,
    )
    if pending_hotel_response is not None:
        return pending_hotel_response

    if (
        (
            _is_confirmation_message(request.message)
            or (
                structured_tool_call is not None
                and structured_tool_call.tool == "confirm_booking"
            )
        )
        and _pending_destination_suggestion_action(_active_trip_for_session(session.id, db)) is not None
    ):
        destination_suggestion_response = _execute_pending_destination_suggestion_confirmation(
            db,
            session,
            request,
        )
        if destination_suggestion_response is not None:
            return destination_suggestion_response

    pending_response = _execute_pending_clarification(db, session, request)
    if pending_response is not None:
        return pending_response

    hotel_only_response = _execute_hotel_only_request_if_possible(
        db,
        session,
        request,
    )
    if hotel_only_response is not None:
        return hotel_only_response

    explicit_destination_response = _execute_explicit_destination_trip_if_possible(
        db,
        session,
        request,
    )
    if explicit_destination_response is not None:
        return explicit_destination_response

    field_collection_response = _execute_flexible_trip_field_collection_if_possible(
        db,
        session,
        request,
    )
    if field_collection_response is not None:
        return field_collection_response

    package_response = _execute_package_request_if_possible(db, session, request)
    if package_response is not None:
        return package_response

    tool_call = structured_tool_call or _coerce_nested_structured_tool_call(
        route_tool_with_llama(
            request.message,
            _conversation_state_for_llama(session.id, db),
        )
    )
    tool_call = _coerce_tool_call_for_active_trip(
        tool_call,
        _active_trip_for_session(session.id, db),
    )
    if tool_call is not None:
        routed_response = _execute_tool_call(tool_call, db, user, session, request)
        if routed_response is not None:
            return routed_response

    return _rule_based_chat_fallback(db, user, session, request)


@app.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
) -> ChatResponse:
    response = _chat_unchecked(request, db, current_user)
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == response.session_id)
        .first()
    )
    if session is None:
        return response
    return _finalize_chat_response(db, session, response)


@app.post("/booking/create", response_model=BookingDetails)
def create_booking(
    request: BookingCreateRequest,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
) -> BookingDetails:
    if current_user is None:
        user, session = _get_or_create_demo_context(db)
    else:
        user, session = current_user, _get_or_create_user_session(db, current_user)
    booking = _create_booking_record(
        db=db,
        user_id=user.id,
        session_id=session.id,
        destination=request.destination,
        summary=request.summary or f"Manual mock booking for {request.destination}",
        flight_id=request.flight_id,
        hotel_id=request.hotel_id,
        estimated_price=request.estimated_price,
        currency=request.currency,
    )
    db.commit()
    db.refresh(booking)

    return BookingDetails(**_booking_to_details(booking))


@app.get("/bookings", response_model=BookingListResponse)
def list_bookings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BookingListResponse:
    bookings = (
        db.query(Booking)
        .filter(Booking.user_id == current_user.id)
        .order_by(Booking.created_at.desc())
        .all()
    )
    return BookingListResponse(
        bookings=[BookingDetails(**_booking_to_details(booking)) for booking in bookings]
    )


@app.get("/bookings/{booking_reference}", response_model=BookingDetails)
def get_booking(
    booking_reference: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BookingDetails:
    booking = (
        db.query(Booking)
        .filter(
            Booking.user_id == current_user.id,
            Booking.booking_reference == booking_reference.upper(),
        )
        .first()
    )
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found.")
    return BookingDetails(**_booking_to_details(booking))


@app.post("/checkout/confirm", response_model=CheckoutResponse)
def confirm_checkout(
    request: CheckoutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CheckoutResponse:
    """Create one application booking for public cart items only."""

    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required for checkout.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user, session = current_user, _get_or_create_user_session(db, current_user)

    totals = _checkout_totals(request.items)
    item_counts = {
        item_type: sum(1 for item in request.items if item.type == item_type)
        for item_type in ("flight", "hotel", "package")
    }
    description_parts = [
        f"{count} {item_type}{'' if count == 1 else 's'}"
        for item_type, count in item_counts.items()
        if count
    ]
    total_label = ", ".join(
        f"{currency} {amount:,.2f}" for currency, amount in totals.items()
    )
    summary = (
        f"Voyage AI checkout: {' + '.join(description_parts)}, total {total_label}."
    )
    same_currency = len(totals) == 1
    currency = next(iter(totals), None) if same_currency else None
    total_price = next(iter(totals.values()), None) if same_currency else None
    safe_items = [_safe_checkout_item_details(item) for item in request.items]
    booking_reference = _generate_booking_reference(db)
    flight_ticket, hotel_voucher = _travel_documents_for_checkout_items(
        safe_items,
        booking_reference,
        request.customer.full_name,
    )
    package_summary = _checkout_package_summary(safe_items, totals)
    details_json = json.dumps(
        {
            "items": safe_items,
            "packageSummary": package_summary,
            "totalPrice": total_price,
            "totalsByCurrency": totals,
            "flightTicket": flight_ticket,
            "hotelVoucher": hotel_voucher,
            "cartStatus": "checked_out",
        },
        ensure_ascii=True,
    )
    destination = str(package_summary.get("destination") or "Voyage AI cart checkout")

    booking = Booking(
        booking_reference=booking_reference,
        user_id=user.id,
        session_id=session.id,
        destination=destination[:120],
        status="Confirmed",
        summary=summary,
        flight_id=str((flight_ticket or {}).get("ticketNumber") or "") or None,
        hotel_id=str((hotel_voucher or {}).get("voucherNumber") or "") or None,
        estimated_price=round(total_price) if total_price is not None else None,
        currency=currency or "MIXED",
        booking_type="cart_checkout",
        details_json=details_json,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    return CheckoutResponse(
        booking_reference=booking.booking_reference,
        summary=summary,
        total_price=total_price,
        currency=currency,
        totals_by_currency=totals,
        item_count=len(request.items),
        message=(
            "Checkout complete. Your booking has been confirmed. "
            "Your travel documents are ready."
        ),
        packageSummary=package_summary,
        flightTicket=flight_ticket,
        hotelVoucher=hotel_voucher,
        cartCleared=True,
    )
