"""Local Ollama extraction and structured travel tool routing."""

import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Callable

import requests
from dotenv import load_dotenv
from pydantic import ValidationError

from app.date_utils import next_future_month_day, today_utc_date
from app.schemas import (
    ClarificationAnswer,
    FlightSearchRequest,
    LlamaChatActionRoute,
    LlamaPlannerToolCall,
    TravelToolCall,
)


LOGGER = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_DIR / ".env"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1:8b-instruct-q4_0"
OLLAMA_TIMEOUT_SECONDS = 90
REQUIRED_OLLAMA_MODEL = "llama3.1:8b-instruct-q4_0"

FLIGHT_EXTRACTION_PROMPT = """
Convert the user's flight request into exactly one FlightSearchRequest JSON object.
Return only valid JSON with no markdown, explanation, comments, prose, or extra keys.
Use the FlightSearchRequest schema exactly. Return only fields supported by it.

{
  "searchType": "flight",
  "passengerCount": 1,
  "itinerary": {
    "tripType": "round_trip",
    "origin": {"city": "London", "code": "LHR"},
    "destination": {"city": "New York", "code": "JFK"},
    "dates": {"departure": "YYYY-MM-DD", "return": "YYYY-MM-DD"}
  },
  "preferences": {"cabinClass": "economy", "maxStops": 0}
}

Rules:
- London Heathrow maps to LHR. London maps to LHR unless another airport is named.
- New York maps to JFK unless another airport is named.
- Use ISO date format YYYY-MM-DD.
- Use searchType exactly "flight".
- Use tripType "round_trip" with both departure and return dates when the user
  provides a return date. Otherwise use "one_way" and omit dates.return.
- Default passengerCount to 1 when it is not stated.
- Extract passengerCount from wording such as "for 3 passengers", "3 people",
  "we are 3", or "3 travellers". The count must be a positive integer.
- Map direct, non-stop, nonstop, and no stops to the integer maxStops 0.
- Use cabinClass exactly one of economy, premium_economy, business, or first.
  Use economy when it is not stated.
- Do not invent missing required dates or airports. Return {} when a valid flight
  search request cannot be formed.
""".strip()

FLIGHT_SEARCH_JSON_SCHEMA = FlightSearchRequest.model_json_schema(by_alias=True)
CLARIFICATION_ANSWER_JSON_SCHEMA = ClarificationAnswer.model_json_schema()
CHAT_ACTION_ROUTER_JSON_SCHEMA = LlamaChatActionRoute.model_json_schema()
TRAVEL_TOOL_CALL_JSON_SCHEMA = TravelToolCall.model_json_schema()
LLAMA_PLANNER_TOOL_CALL_JSON_SCHEMA = LlamaPlannerToolCall.model_json_schema()
TRAVEL_TOOL_ROUTER_PROMPT_VERSION = "travel_tool_router_2026_08_phase3d_visible_results"
TRAVEL_TOOL_SCHEMA_VERSION = "travel_planner_tool_call_schema_v1_phase3d"
LLAMA_TOOL_ROUTE_TRACE_HOOK: Callable[[dict[str, Any]], None] | None = None


def set_llama_tool_route_trace_hook(
    hook: Callable[[dict[str, Any]], None] | None,
) -> None:
    """Register an optional evaluator trace hook for tool-routing attempts."""

    global LLAMA_TOOL_ROUTE_TRACE_HOOK
    LLAMA_TOOL_ROUTE_TRACE_HOOK = hook


def _emit_llama_tool_route_trace(event: dict[str, Any]) -> None:
    if LLAMA_TOOL_ROUTE_TRACE_HOOK is None:
        return
    try:
        LLAMA_TOOL_ROUTE_TRACE_HOOK(event)
    except Exception:  # pragma: no cover - tracing must never affect routing.
        LOGGER.debug("Llama route trace hook failed.", exc_info=True)

CHAT_ACTION_ROUTER_PROMPT = """
You are a travel-assistant action router. Select exactly one allowed backend action
for the user's latest message. You never search flights, book travel, call APIs, or
write data. Use only the provided conversation state and return JSON matching the
required schema.

Allowed actions:
- flight_search
- hotel_search
- package_search
- show_cheapest_flight
- show_fastest_flight
- show_flight_option
- select_flight_option
- select_current_flight
- book_cheapest_flight
- confirm_booking
- cancel_booking
- booking_lookup
- general_help

Routing rules:
- A new request with flight language, routes, airport codes, or travel dates is flight_search.
- Direct, non-stop, nonstop, and no stops are flight constraints, never cancellation.
- "which is the cheapest" selects show_cheapest_flight when latest flight results exist.
- "which is fastest" selects show_fastest_flight when latest flight results exist.
- "show me the second one" is show_flight_option with option_number 2.
- "I like it", "can I book it", "book this one", and "I'll take that" are
  select_current_flight only when a currently displayed flight option exists.
- "go with the cheapest" and "book the cheapest" are book_cheapest_flight.
- Confirmation language such as "yes confirm it" is confirm_booking only when a
  pending action exists.
- Cancellation language such as "no cancel it" is cancel_booking only when a
  pending action exists. A bare "no" without a pending action is general_help.
- Use option_number only for show_flight_option or select_flight_option; otherwise null.
- Use booking_reference only for booking_lookup; otherwise null.
- Choose general_help when the requested action needs flight or booking state that
  is not available.
""".strip()

TRAVEL_TOOL_ROUTER_PROMPT = """
You are a travel assistant tool router. You are not the executor. Choose exactly
one backend tool and safe arguments based on the user message and conversation
state. Return JSON only, with no prose outside the JSON object.

You must never call external APIs, access secrets, write to databases, invent a
booking reference, or claim a booking was created. The backend validates and
executes the selected tool.

Supported tools and arguments:
- prepare_trip_request: {"requested_product": "flight|hotel|package|unknown", "intent": "flight_search|hotel_search|package_search", "origin_text": "Birmingham", "origin_code": "BHX", "destination_text": "Florida", "destination_code": null, "destination_region": "Florida", "destination_type": "region|airport|city", "destination_candidates": ["MCO", "MIA", "TPA", "FLL"], "departure_date": null, "return_date": null, "departure_day": null, "return_day": null, "date_text": "in november", "month": "2026-11", "nights": 3, "budget_amount": null, "budget_currency": "GBP", "passenger_count": 1, "missing_fields": ["exact_dates", "destination_airport_choice"], "use_active_trip_state": false, "search_candidates": false}. This is the main planning/state-update tool.
- rank_flights: {"ranking": "cheapest|fastest|best|earliest|latest|cheapest_fastest|best_value", "limit": 1}
- show_flight_option: {"option_number": 2}
- select_flight: {"selection": "current_displayed|cheapest|fastest|option_number", "option_number": 2}
- check_flight_availability: {"selection": "current_displayed|selected_pending|cheapest|option_number", "option_number": 2}
- update_passenger_count: {"passenger_count": 3, "select_after_refresh": false}
- describe_current_flight: {"field": "cabin_class|passenger_count|price|times|summary"}
- rank_hotels: {"ranking": "cheapest"}
- show_hotel_option: {"option_number": 2}
- select_hotel: {"selection": "current_displayed|cheapest|option_number", "option_number": 2}
- confirm_hotel_booking: {}
- discover_destinations: {"tripPurpose": "family_holiday", "travellers": {"adults": 2, "children": 2}, "environmentPreferences": ["beach"], "activityPreferences": ["skiing"], "requiredActivities": ["skiing"], "regionPreferences": ["Europe"], "excludedRegions": [], "maxFlightDurationMinutes": 120, "shortFlightPreference": true, "tripDurationNights": 3, "dates": {"departure": "2026-08-11", "return": "2026-08-14"}, "softPreferences": ["short_flight"], "hardFilters": []}
- select_destination: {"destinationText": "Erzurum", "requestedProducts": ["flights"]}
- search_trip_options: {"destinationText": "Hurghada", "requestedProducts": ["flights", "hotels"]} only when the user selects a destination from existing destination recommendation cards.
- search_alternative_origins: {"confirmed": true, "origin_text": "London", "origin_codes": ["LHR", "LGW"], "use_active_trip_state": true}
- add_to_cart: {"item_type": "package|flight|hotel", "option_number": 1}
- checkout: {}
- cart_view: {}
- confirm_booking: {"confirmed": true, "use_active_trip_state": true}
- cancel_booking: {}
- booking_lookup: {"booking_reference": "TA123456"}
- general_help: {}

Tool selection policy:
- Clean routing contract: Llama extracts structured intent and slots; the
  backend validates, merges with active_trip, asks clarification for missing
  required fields, builds packages when package state is complete, searches
  flights for complete flight-only state, searches hotels for complete
  hotel-only state, and executes pending actions only after confirmation.
- Exact day/month rule: if the user gives a day number plus a month, such as
  "on the 15th of November", this is not month-only. Return month "2026-11",
  departure_day 15, and missing_fields [] when nights or a return day are also
  present.
- Exact date-range rule: if the user gives "from 12 September to 16 September"
  or similar, return departure_date "2026-09-12", return_date "2026-09-16",
  and missing_fields [] even when active_trip contains stale dates.
- DEFAULT for trip, holiday, package, package-like, and multi-turn planning is
  prepare_trip_request. This is a state-update tool, not a provider search.
  It lets the backend decide later whether to search flights, hotels, or
  packages once required fields are complete.
- Use prepare_trip_request for package requests, trip requests, holiday
  requests, broad destination requests, explicit flight-only requests,
  explicit hotel-only requests, follow-up destination/date/duration/budget/
  passenger details, city choice after a broad region, month-only requests,
  day-only follow-ups, date-range follow-ups, and budget follow-ups for active
  trips.
- If conversation_state.active_trip.tripFrame is "broad_activity_trip" or
  active_trip.requiredActivities contains an activity such as skiing while
  selectedDestination is null, then short follow-ups that provide origin,
  month, date, or duration must keep that broad activity frame. Return
  prepare_trip_request with use_active_trip_state true, no destination_text,
  and preserve/echo the existing requiredActivities when possible. Do not
  invent or restore a previous region such as Florida.
- Direct search_flights and search_hotels are intentionally not exposed to you.
  For flight-only requests, return prepare_trip_request with
  requested_product "flight" and intent "flight_search". For hotel-only
  requests, return prepare_trip_request with requested_product "hotel" and
  intent "hotel_search". For package/trip/holiday/flight+hotel requests,
  return requested_product "package" and intent "package_search".
- Use confirm_booking as the pending-action confirmation tool only when
  conversation_state.active_trip.pendingAction exists and the user confirms.
  If pendingAction.type is build_package and the user says yes, yes please, go
  ahead, build it, or similar, return confirm_booking with confirmed true and
  use_active_trip_state true. If pendingAction.type is suggest_destinations,
  return confirm_booking with confirmed true and use_active_trip_state true so
  the backend can run destination discovery. Do not restart the earlier
  clarification.

Argument field descriptions for prepare_trip_request:
- requested_product: "package" for packages, holidays, trips, flight+hotel,
  total price, or stay+nights planning; "flight" for explicit flight-only;
  "hotel" for explicit hotel-only; "unknown" if the product is unclear.
- intent: "package_search" for packages, holidays, trips, flight+hotel, total
  price, or stay+nights planning. Use "flight_search" for flight-only planning
  and "hotel_search" for hotel-only planning.
- use_active_trip_state: true when the user is adding details to the current
  pending/active trip, choosing a city after a broad region, giving dates after
  a month-only request, adding nights, adding budget, or confirming a planned
  context. false for a new top-level trip.
- origin_text/origin_code: explicit departure place from the user. Birmingham
  is BHX. Keep explicit origin even if default_origin exists.
- destination_text: user wording such as Florida, Orlando, Egypt, Hurghada,
  Paris.
- destination_type: "region" for broad places with several airports, "city"
  when the user names a city in an active region, "airport" when a single
  airport/city has been chosen.
- destination_code: IATA code for the selected destination when known. In a
  Florida context, Orlando is MCO, Miami is MIA, Tampa is TPA, Fort Lauderdale
  is FLL.
- destination_candidates: IATA candidates for broad regions. Florida uses
  ["MCO", "MIA", "TPA", "FLL"]. Egypt may use ["HRG", "SSH", "CAI"].
- month: broad month context as YYYY-MM, for example "2026-11". Month-only
  requests must leave departure_date and return_date null.
- departure_day/return_day: numeric days for follow-ups inside active_trip.month.
  For "on the 15th", use departure_day 15. For "from the 15th till the 20th",
  use departure_day 15 and return_day 20. If the user says "on the 15th of
  November" or "15 November" and also gives a stay length, set month
  "2026-11", departure_day 15, nights to the stay length, and missing_fields
  [].
- Do not put "exact_dates" in missing_fields when the user gives a day number,
  a month, and either a return day or a number of nights. In that case the
  exact date can be completed by backend normalisation from month +
  departure_day + nights.
- departure_date/return_date: exact ISO dates only when the exact dates are
  known. Do not convert "in November" into 2026-11-01 to 2026-11-30.
- nights: number of nights, for example "stay there for 4 nights" is 4.
- budget_amount/budget_currency: numeric budget and ISO-like currency such as
  GBP when the user gives a budget.
- missing_fields: fields still needed, such as exact_dates,
  destination_airport_choice, return_date, destination, or origin.

Few-shot examples:
1. User: i want go on a trip to florida from birmingham in november
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"package","intent":"package_search","origin_text":"Birmingham","origin_code":"BHX","destination_text":"Florida","destination_region":"Florida","destination_type":"region","destination_candidates":["MCO","MIA","TPA","FLL"],"date_text":"in november","month":"2026-11","departure_date":null,"return_date":null,"missing_fields":["exact_dates","destination_airport_choice"],"use_active_trip_state":false}}

2. Active state: Florida, origin BHX, month 2026-11, pendingTripRequest.intent package_search.
User: i would like to go to Orlando on the 15th and stay there for 4 nights
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"package","intent":"package_search","destination_text":"Orlando","destination_type":"city","destination_code":"MCO","departure_day":15,"month":"2026-11","nights":4,"missing_fields":[],"use_active_trip_state":true}}

3. Active state: pendingAction.type build_package, origin BHX, destination MCO, departureDate 2026-11-15, returnDate 2026-11-19.
User: yes please
Return:
{"tool":"confirm_booking","arguments":{"confirmed":true,"use_active_trip_state":true}}

4. User: find me flights only from Birmingham to Orlando on 15 November returning 19 November
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"flight","intent":"flight_search","origin_text":"Birmingham","origin_code":"BHX","destination_text":"Orlando","destination_type":"city","destination_code":"MCO","departure_date":"2026-11-15","return_date":"2026-11-19","passenger_count":1,"missing_fields":[],"use_active_trip_state":false}}

5. User: find me a hotel only in Orlando from 15 November to 19 November
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"hotel","intent":"hotel_search","destination_text":"Orlando","destination_type":"city","destination_code":"MCO","departure_date":"2026-11-15","return_date":"2026-11-19","passenger_count":1,"missing_fields":[],"use_active_trip_state":false}}

6. User: Plan a 4 night trip to Orlando from Birmingham on the 15th of November
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"package","intent":"package_search","origin_text":"Birmingham","origin_code":"BHX","destination_text":"Orlando","destination_type":"city","destination_code":"MCO","month":"2026-11","departure_day":15,"nights":4,"missing_fields":[],"use_active_trip_state":false}}

7. User: I want a package from Birmingham to Paris from 12 September to 16 September
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"package","intent":"package_search","origin_text":"Birmingham","origin_code":"BHX","destination_text":"Paris","destination_type":"city","destination_code":"CDG","departure_date":"2026-09-12","return_date":"2026-09-16","missing_fields":[],"use_active_trip_state":false}}

8. User: i want to fly to somewhere that i can do skiing and i want to go for 3 nights
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"flight","intent":"destination_discovery","destination_text":null,"destination_type":"unknown","destination_code":null,"requiredActivities":["skiing"],"activityPreferences":["skiing"],"tripDurationNights":3,"missing_fields":["departure_date"],"use_active_trip_state":false}}

9. Active state: tripFrame broad_activity_trip, requiredActivities ["skiing"], tripDurationNights 3, selectedDestination null.
User: maybe next month and from east midlands airport
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"flight","intent":"destination_discovery","origin_text":"East Midlands Airport","origin_code":"EMA","destination_text":null,"destination_type":"unknown","destination_code":null,"month":"2026-09","date_text":"next month","requiredActivities":["skiing"],"activityPreferences":["skiing"],"missing_fields":["departure_date"],"use_active_trip_state":true}}

10. Active state: tripFrame broad_activity_trip, requiredActivities ["skiing"], tripDurationNights 3, month 2026-09.
User: on the 15th
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"flight","intent":"destination_discovery","destination_text":null,"destination_type":"unknown","destination_code":null,"departure_day":15,"requiredActivities":["skiing"],"activityPreferences":["skiing"],"missing_fields":[],"use_active_trip_state":true}}

Rules:
- Highest priority: broad destination regions/countries and month-only date
  ranges use prepare_trip_request, not search_flights. Do not convert a
  month-only value such as "in November" into the first day of the month. Do
  not choose one airport from a broad region unless the user explicitly chose
  that city or airport.
- Never use search_trip_options for a new broad region request such as
  "trip to Florida from Birmingham in November" or "fly to Florida from
  Birmingham". Use prepare_trip_request and extract origin_text/origin_code,
  destination_text, destination_type, destination_candidates, month, date_text,
  and intent.
- If the user message itself contains a fresh travel request with an origin,
  destination/region, or date phrase, extract that fresh request with
  prepare_trip_request even if active_trip already contains older dates or an
  older selected destination. Do not answer general_help for a fresh travel
  request. The new structured fields replace stale active_trip fields after
  backend validation.
- Example: with any stale active_trip, user "i want go on a trip to florida
  from birmingham in november" must return prepare_trip_request with
  intent "package_search", origin_text "Birmingham", origin_code "BHX",
  destination_text "Florida", destination_type "region",
  destination_candidates ["MCO", "MIA", "TPA", "FLL"], date_text
  "in november", month "2026-11", departure_date null, return_date null,
  missing_fields ["exact_dates", "destination_airport_choice"].
- New flight requests with a route, airport codes, and exact departure dates use
  prepare_trip_request with requested_product "flight". The backend decides
  whether it is complete enough to execute search_flights internally.
- Requests where the user does not know the destination and asks by preference
  use discover_destinations. Examples include beach, summer, relaxing, family
  holiday, children, skiing, mountains, city break, nightlife, culture, short
  flight, or less than 2 hours. Do not invent recommendations yourself; only
  extract preferences for the backend.
- Explicit activity requests such as skiing must also go in requiredActivities.
  Region and short-flight wording are preferences unless the user says only,
  must, or under/less than a fixed duration.
- Destination-discovery refinements use discover_destinations again, not
  search_flights. Examples: "keep it in Europe", "only Europe", "I don't like
  long flights", "short flights", "closer", "nearby", and "less than 2
  hours". Use latest_destination_context from conversation_state and add the
  new region or short-flight constraints.
- If latest destination recommendations exist and the user chooses one, use
  select_destination or search_trip_options. Examples: "I could go for
  Erzurum", "I like Hurghada", "option 1", "the second one", "search flights
  to ERZ", and "what about flights and hotels there". Only pass the selected
  text and requested products. Do not invent the origin, destination route, or
  dates; the backend resolves them from active_trip.
- When active_trip.selectedDestination exists, follow-ups such as "there",
  "it", "that place", "what about the flights", and "what about hotels" refer
  to the selected active-trip destination.
- Use prepare_trip_request when the user gives a flight/package/trip request
  that is incomplete or contains a broad destination region/country rather than
  a single airport. Examples include "trip to Florida from Birmingham in
  November", "fly to Florida from Birmingham", and later date follow-ups for
  that stored broad destination. Birmingham as an origin is BHX. Florida is a
  region, not an airport; set destination_type "region" and destination
  candidates ["MCO", "MIA", "TPA", "FLL"]. Do not create a destination called
  "Fly To Florida".
- If the user supplied an explicit origin, include origin_text/origin_code. A
  detected/default origin is only used by the backend when no explicit origin is
  present.
- For broad month wording such as "in November", set date_text and month, but
  leave departure_date/return_date null unless exact dates are stated.
- "I need a full package", "full package", "build a package", and "flight and
  hotel package" mean package planning/search. Use prepare_trip_request with
  requested_product "package", or search_trip_options only when the user is
  selecting from existing destination recommendation cards. Do not use
  add_to_cart unless the user explicitly says to add a specific option number
  to cart.
- Use add_to_cart only when the user explicitly asks to add an option to cart
  and provides an option number, such as "add package option 1 to cart".
- If active_trip has a pending broad trip and the user provides dates or nights,
  use prepare_trip_request with use_active_trip_state true and merge the new
  date slots; do not use general_help.
- If active_trip has a pending Florida trip and the user chooses a Florida city
  or airport, keep using prepare_trip_request with use_active_trip_state true.
  Orlando maps to MCO, Miami maps to MIA, Tampa maps to TPA, and Fort
  Lauderdale maps to FLL. Preserve the pending intent: if the active pending
  trip intent is package_search, keep intent package_search unless the user
  explicitly asks for flights only.
- If active_trip has a pending package/trip and the user adds a budget, keep
  using prepare_trip_request with use_active_trip_state true. Return
  budget_amount as a number and budget_currency such as "GBP". Preserve the
  existing origin, selected destination, month, dates, nights, and package_search
  intent unless the user explicitly changes them.
- For yearless day ranges in a pending month context, return structured day
  slots rather than guessing another month. For example, if active_trip.month is
  2026-11 and the user says "from the 15th till the 20th", return
  departure_day 15, return_day 20, month "2026-11", and use_active_trip_state
  true. This is a return trip; do not omit return_day.
- If active_trip has pending alternative-origin search and the user confirms or
  names another origin such as London, use search_alternative_origins with
  use_active_trip_state true. You understand "yes"; the backend only validates
  the structured action.
- If active_trip.pendingAction.type is build_package and the user confirms,
  return confirm_booking with use_active_trip_state true. Do not restart an
  earlier broad-region clarification.
- If conversation_state.pending_destination_discovery_exists is true and the
  user provides only a date or relative date such as "next week", "same day
  next week", "tomorrow", or "on the 11th", keep using discover_destinations.
  Merge the date into pending_destination_discovery. Do not use search_flights
  unless a concrete destination airport is already known.
- Preserve tripDurationNights. If a user asked for 3 nights and later gives a
  departure date, set return to departure plus 3 days.
- If the user says "not in Europe", "outside Europe", "no Europe", or "I don't
  want it in Europe", put "Europe" in excludedRegions and remove Europe from
  regionPreferences. If the user says "doesn't have to be in Europe", only
  remove the Europe preference; do not exclude Europe.
- For flight-only prepare_trip_request, extract complete route/date slots when
  the user provides sufficient information. London/London Heathrow maps to LHR
  and New York maps to JFK unless another airport is specified. Use YYYY-MM-DD
  dates, default one passenger, extract passenger_count, and put direct/no
  stops/non-stop/nonstop in the structured planning fields when relevant.
- For a partial follow-up, use active_context_type and latest_flight_context or
  latest_hotel_context. If no new route is named, reuse the latest active route;
  never invent LHR/JFK, London/New York, or any route from an example.
- After an existing complete flight search, "what about tomorrow?" and "what
  about next week?" use prepare_trip_request with requested_product "flight",
  use_active_trip_state true, and the changed date. "make it 2 passengers"
  uses update_passenger_count when flight context exists.
- If no active flight route exists, use general_help instead of inventing a
  route, unless default_origin is available and the user provides a destination.
  If hotel context is active, a partial hotel follow-up must use that
  hotel context, not a flight search.
- If the user says "I want to go to London" or names only a destination, use
  prepare_trip_request with requested_product "flight" or "unknown" and leave
  missing_fields for origin/date so the backend can ask clarification.
- For discover_destinations, use default_origin from conversation_state when no
  origin is explicit. Never include exact GPS coordinates.
- Europe is a region filter for discover_destinations, not a destination airport
  and not a flight_search destination.
- "what about Paris?" is ambiguous unless the user clearly says it is a new
  destination. Use general_help to ask for clarification rather than guessing.
- A passenger-count change after a flight search uses update_passenger_count.
  Map "for 3 passengers", "for 3 people", "we are 3", and "make it 3
  travellers" to passenger_count 3. For "can I book it for 3 passengers?",
  set select_after_refresh true; this only prepares a refreshed selection and
  never confirms a booking.
- "which is cheapest", "lowest price", "fastest", "best", "earliest", and
  "latest" use rank_flights when latest flight results exist. Map "shortest and
  cheapest" or "cheapest and fastest" to cheapest_fastest, and "best value" to
  best_value.
- "show me the second one" uses show_flight_option with option_number 2.
- "I like it", "can I book it", "book this one", and "I'll take that" use
  select_flight with selection current_displayed only when a current flight exists.
- "book the cheapest" uses select_flight with selection cheapest.
- Availability questions such as "is it still available" use
  check_flight_availability and resolve "it" from selected_pending first, then
  current_displayed.
- Questions about the visible flight, including "is it business or economy?",
  "how many passengers is this for?", "what time does it leave?", "what airline
  is it?", and "how much is it?", use describe_current_flight with the matching
  field. Do not show a different option to answer these questions.
- Confirm only for clear confirmation language and an existing pending action.
- Cancel only for clear cancellation language and an existing pending action.
- "no stops" in a flight request is a search constraint, never cancellation.
- New hotel-only requests use prepare_trip_request with requested_product
  "hotel" and intent "hotel_search". Include destination_text when available,
  YYYY-MM-DD departure_date/return_date as check-in/check-out equivalents,
  passenger_count or guest count when stated, and missing_fields for anything
  still needed. Do not invent missing dates.
- When latest hotel results are the current context, "which is cheapest" uses
  rank_hotels, "show option 2" uses show_hotel_option, and "I like it" or
  "can I book it" uses select_hotel with current_displayed. Selecting a hotel
  prepares confirmation; it never creates the booking immediately.
- A clear confirmation for a pending hotel uses confirm_hotel_booking.
- Package/cart/checkout wording is understood by you, not by backend phrase
  lists. If package options are visible and the user likes, chooses, books, or
  wants an option, use add_to_cart with item_type "package" and the option
  number. Examples include "i like package 1", "choose package 1", "book
  package 1", "take option 2", "i want this one", and similar wording or
  typos.
- If cart context exists and the user asks to check out, pay, continue to
  checkout, continue, or types a close typo such as "chekout", use checkout.
  The backend will validate that the cart exists and is allowed.
- If the user asks to view the cart, use cart_view.
- When required flight/booking context is absent, choose general_help.
""".strip()

TRAVEL_TOOL_ROUTER_PROMPT = """
You are The Voyager's structured planning router. Return exactly one JSON object
matching the supplied schema. No markdown, no prose outside JSON.

Architecture:
- Llama extracts structured intent and slots only.
- Backend validates, merges with active_trip, asks clarifying questions, and
  executes providers only after the state is safe and complete.
- Direct flight/hotel provider search tools are not available to you.

Main tool:
- prepare_trip_request is the default planner/state-update tool.
- handle_visible_results is the result-card follow-up tool. Use it when the
  user is referring to currently visible flight, hotel, or package results.

Use prepare_trip_request for:
- package requests, trip requests, holiday requests, flight+hotel requests,
  total-price requests, broad destinations, exact destinations, flight-only
  requests, hotel-only requests, follow-up city choices, follow-up dates,
  follow-up nights/duration, budget updates, passenger updates, and active-trip
  updates.

prepare_trip_request arguments:
- requested_product: "package" for trip/holiday/package/flight+hotel/total
  price/stay+nights planning; "flight" only when the user clearly wants flights
  only; "hotel" only when the user clearly wants hotels/accommodation only;
  "unknown" when unclear.
- intent: "package_search", "flight_search", or "hotel_search".
- use_active_trip_state: true for follow-ups to an active/pending trip.
- origin_text, origin_code.
- destination_text, destination_code, destination_region, destination_type,
  destination_candidates.
- area_or_landmark and location_label for hotel-only requests that mention an
  area or landmark such as Hyde Park, Central London, or Big Ben.
- month as YYYY-MM for month-only context.
- departure_date and return_date only for exact ISO dates.
- departure_day and return_day for day-only follow-ups inside active_trip.month.
- If a message contains a day number and a month, for example "on the 15th of
  November", set month "2026-11" and departure_day 15. If nights or return_day
  is also present, do not include exact_dates in missing_fields.
- If a message contains an exact date range, for example "from 12 September to
  16 September", set departure_date and return_date as ISO dates and do not
  include exact_dates in missing_fields.
- nights, budget_amount, budget_currency, passenger_count, missing_fields.
- activityPreferences, requiredActivities, and environmentPreferences for broad
  activity or environment requests such as skiing, beach, hiking, family, or
  water sports. If the user asks for somewhere to do an activity without naming
  a city/airport, leave destination_text and destination_code null and include
  the activity in requiredActivities.

Routing contract:
1. pendingAction exists + user confirms -> confirm_booking. If
   pendingAction.type is suggest_destinations, the backend will show
   destination ideas from the active broad activity trip.
2. Current visible result cards + user asks about/chooses/adds/compares those
   results -> handle_visible_results.
3. New or follow-up travel planning -> prepare_trip_request.
4. Visible flight result actions may also use rank/show/select/check/update/describe flight tools.
5. Visible hotel result actions may also use rank/show/select/confirm hotel tools.
6. Visible destination card selection -> select_destination or search_trip_options.
7. Cart/checkout page actions -> add_to_cart, cart_view, checkout.
8. Non-travel or impossible-without-context -> general_help.

Important product rules:
- A user asking for a trip, holiday, package, stay for N nights, flight and
  hotel, or total price is requesting requested_product "package".
- A broad month such as "in November" sets month "2026-11" and leaves
  departure_date and return_date null unless exact dates are provided.
- If only a month is known, do not create provider-search dates.
- If a broad destination region is known but exact airport/city is missing, use
  missing_fields such as "destination_airport_choice" and "exact_dates".
- Florida is a broad destination region. destination_region is "Florida",
  destination_type is "region", and destination_candidates are ["MCO", "MIA",
  "TPA", "FLL"].
- In an active Florida context, Orlando is MCO, Miami is MIA, Tampa is TPA, and
  Fort Lauderdale is FLL.
- Birmingham as a UK origin is BHX.
- "yes", "yes please", "go ahead", and "build it" use confirm_booking only
  when active_trip.pendingAction exists.
- If latestVisibleResultType is packages and latestPackageOptions exist, natural
  follow-ups such as "which is the cheapest?", "which one is best?", "compare
  them", "show me the first one", "I'll take the cheapest", "okay book it",
  "go with that", "add it to cart", and "proceed with it" use
  handle_visible_results with result_type "package". Do not use
  prepare_trip_request for these visible-result follow-ups.
- handle_visible_results actions:
  select_cheapest = identify the cheapest visible option and make it current;
  select_option = choose a visible option but do not add to cart yet;
  compare_results = compare/sort visible cards;
  show_option = show one visible option;
  add_selected_to_cart = add the current selected/current visible option;
  add_option_to_cart = add a numbered visible option;
  ask_result_question = answer a result-card question from visible results.
- Use result_type "flight" for visible flight cards, "hotel" for visible hotel
  cards, "package" for visible package cards. If uncertain, use "unknown" and
  use_visible_results true so the backend can validate the current visible
  context.

Examples:
User: i want go on a trip to florida from birmingham in november
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"package","intent":"package_search","origin_text":"Birmingham","origin_code":"BHX","destination_text":"Florida","destination_region":"Florida","destination_type":"region","destination_candidates":["MCO","MIA","TPA","FLL"],"date_text":"in november","month":"2026-11","departure_date":null,"return_date":null,"missing_fields":["exact_dates","destination_airport_choice"],"use_active_trip_state":false}}

Active state: destination_region Florida, origin BHX, month 2026-11,
pendingTripRequest.intent package_search.
User: i would like to go to Orlando on the 15th and stay there for 4 nights
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"package","intent":"package_search","destination_text":"Orlando","destination_type":"city","destination_code":"MCO","departure_day":15,"month":"2026-11","nights":4,"missing_fields":[],"use_active_trip_state":true}}

Active state: pendingAction.type build_package.
User: yes please
Return:
{"tool":"confirm_booking","arguments":{"confirmed":true,"use_active_trip_state":true}}

User: find me flights only from Birmingham to Orlando on 15 November returning 19 November
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"flight","intent":"flight_search","origin_text":"Birmingham","origin_code":"BHX","destination_text":"Orlando","destination_type":"city","destination_code":"MCO","departure_date":"2026-11-15","return_date":"2026-11-19","passenger_count":1,"missing_fields":[],"use_active_trip_state":false}}

User: find me a hotel only in Orlando from 15 November to 19 November
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"hotel","intent":"hotel_search","destination_text":"Orlando","destination_type":"city","destination_code":"MCO","departure_date":"2026-11-15","return_date":"2026-11-19","passenger_count":1,"missing_fields":[],"use_active_trip_state":false}}

User: I want a package from Birmingham to Paris from 12 September to 16 September
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"package","intent":"package_search","origin_text":"Birmingham","origin_code":"BHX","destination_text":"Paris","destination_type":"city","destination_code":"CDG","departure_date":"2026-09-12","return_date":"2026-09-16","missing_fields":[],"use_active_trip_state":false}}

User: i want to fly to somewhere that i can do skiing and i want to go for 3 nights
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"flight","intent":"destination_discovery","destination_text":null,"destination_type":"unknown","destination_code":null,"requiredActivities":["skiing"],"activityPreferences":["skiing"],"tripDurationNights":3,"missing_fields":["departure_date","destination"],"use_active_trip_state":false}}

User: I want to book a hotel in London Hyde Park preferably
Return:
{"tool":"prepare_trip_request","arguments":{"requested_product":"hotel","intent":"hotel_search","destination_text":"London","destination_type":"city","area_or_landmark":"Hyde Park","location_label":"near Hyde Park","missing_fields":["check_in_date","nights"],"use_active_trip_state":false}}

Active state: latestVisibleResultType packages, latestPackageOptions exist.
User: which is the cheapest?
Return:
{"tool":"handle_visible_results","arguments":{"action":"select_cheapest","result_type":"package","use_visible_results":true,"use_current_selection":false}}

Active state: latestVisibleResultType packages, latestPackageOptions exist, currentPackageOption is package 1.
User: okay book it
Return:
{"tool":"handle_visible_results","arguments":{"action":"add_selected_to_cart","result_type":"package","use_visible_results":true,"use_current_selection":true}}

Active state: latestVisibleResultType packages, latestPackageOptions exist.
User: show package 2
Return:
{"tool":"handle_visible_results","arguments":{"action":"show_option","result_type":"package","option_number":2,"use_visible_results":true,"use_current_selection":false}}

Active state: latestVisibleResultType packages, latestPackageOptions exist.
User: compare them
Return:
{"tool":"handle_visible_results","arguments":{"action":"compare_results","result_type":"package","use_visible_results":true,"use_current_selection":false}}
""".strip()


def _current_date_instruction() -> str:
    today = today_utc_date()
    current_date = today.isoformat()
    next_october_fifth = next_future_month_day(10, 5, today).isoformat()
    return (
        f"Today is {current_date}. If the user gives a day/month without a year, "
        "resolve it to the next future occurrence. Never output a date in the "
        "past unless the user explicitly asks for historical data. Resolve "
        "tomorrow as today plus one day and next week as today plus seven days. "
        "Use ISO YYYY-MM-DD dates only for travel searches. For example, a flight on "
        f"the 5th of October uses departure {next_october_fifth}. "
        "'hotel in Paris from 12 September to 16 "
        "September' uses the next future September dates."
    )


DESTINATION_STOP_WORDS = {
    "and",
    "for",
    "from",
    "next",
    "on",
    "please",
    "this",
    "tomorrow",
    "with",
}

BOOKING_REFERENCE_PATTERN = re.compile(r"\b[A-Z]{2,}\d{2,}\b", re.IGNORECASE)


def _detect_intent_type(message: str, booking_reference: str | None) -> str:
    has_flight_word = any(
        word in message
        for word in ["flight", "flights", "fly", "flying", "airfare", "airline"]
    )
    has_hotel_word = any(
        word in message
        for word in ["hotel", "hotels", "room", "stay", "accommodation"]
    )
    has_package_word = any(
        word in message for word in ["package", "trip", "vacation", "holiday"]
    )
    has_booking_word = any(word in message for word in ["booking", "reservation"])
    has_retrieval_word = any(
        word in message for word in ["check", "find", "lookup", "status", "view"]
    )

    if booking_reference or (has_booking_word and has_retrieval_word):
        return "booking_retrieval"
    if has_package_word or (has_flight_word and has_hotel_word):
        return "package_search"
    if has_flight_word:
        return "flight_search"
    if has_hotel_word:
        return "hotel_search"

    return "general_enquiry"


def _extract_booking_reference(message: str) -> str | None:
    match = BOOKING_REFERENCE_PATTERN.search(message)
    if not match:
        return None

    return match.group(0).upper()


def _extract_destination(message: str) -> str | None:
    tokens = re.findall(r"[A-Za-z]+", message)
    for index, token in enumerate(tokens):
        if token.lower() not in {"to", "in"}:
            continue

        destination_words: list[str] = []
        for next_token in tokens[index + 1 : index + 4]:
            normalized_token = next_token.lower()
            if normalized_token in DESTINATION_STOP_WORDS:
                break
            destination_words.append(next_token)

        if destination_words:
            return " ".join(destination_words).title()

    return None


def extract_travel_intent(message: str) -> dict[str, str | int | None]:
    """Return minimal fallback intent data when the model router is unavailable."""
    normalized_message = message.lower()
    booking_reference = _extract_booking_reference(message)
    destination = _extract_destination(message)
    intent_type = _detect_intent_type(normalized_message, booking_reference)

    return {
        "intent_type": intent_type,
        "destination": destination,
        "booking_reference": booking_reference,
        "departure_city": None,
        "travelers": 1,
        "budget_level": "medium",
        "message_summary": message[:120],
    }


def validate_ollama_model(model: str) -> str:
    selected_model = str(model or "").strip()
    if selected_model != REQUIRED_OLLAMA_MODEL:
        raise ValueError(
            "Invalid OLLAMA_MODEL. This project is configured to use the "
            "4-bit model llama3.1:8b-instruct-q4_0 for local speed. Please "
            "set OLLAMA_MODEL=llama3.1:8b-instruct-q4_0."
        )
    return selected_model


def _load_ollama_settings() -> tuple[str, str]:
    load_dotenv(dotenv_path=ENV_PATH)
    base_url = os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL).strip()
    model = os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip()
    selected_model = validate_ollama_model(model or DEFAULT_OLLAMA_MODEL)
    LOGGER.info("Selected Ollama model: %s", selected_model)
    return base_url or DEFAULT_OLLAMA_BASE_URL, selected_model


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


def _extract_ollama_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Ollama response was not a JSON object.")

    message = payload.get("message")
    if not isinstance(message, dict):
        raise ValueError("Ollama response did not include a message object.")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Ollama response content was empty.")

    return content


def _validate_flight_search_constraints(
    flight_search: FlightSearchRequest,
    message: str,
) -> None:
    if flight_search.search_type != "flight":
        raise ValueError("searchType must be flight.")

    if (
        flight_search.itinerary.trip_type == "round_trip"
        and flight_search.itinerary.dates.return_date is None
    ):
        raise ValueError("round_trip extraction requires dates.return.")

    message_lower = message.lower()
    direct_request = any(
        phrase in message_lower
        for phrase in ["no stops", "non-stop", "nonstop", "direct"]
    )
    if direct_request and flight_search.preferences.max_stops != 0:
        raise ValueError("Direct flight requests require maxStops 0.")


def extract_flight_search_with_llama(message: str) -> FlightSearchRequest | None:
    """Extract and validate a flight search request with local Ollama.

    The caller retains the existing deterministic fallback path when this function returns
    ``None``. No token or request payload is logged.
    """

    try:
        base_url, model = _load_ollama_settings()
    except ValueError as exc:
        LOGGER.warning("Llama flight extraction disabled: %s", exc)
        return None
    LOGGER.info("Llama flight extraction attempted.")

    raw_content: str | None = None
    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": FLIGHT_SEARCH_JSON_SCHEMA,
                "options": {"temperature": 0},
                "messages": [
                    {
                        "role": "system",
                        "content": f"{FLIGHT_EXTRACTION_PROMPT}\n\n{_current_date_instruction()}",
                    },
                    {"role": "user", "content": message},
                ],
            },
            timeout=OLLAMA_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        LOGGER.warning("Llama flight extraction failed: %s", exc.__class__.__name__)
        return None

    if not response.ok:
        LOGGER.warning("Llama flight extraction failed with HTTP %s.", response.status_code)
        return None

    try:
        raw_content = _extract_ollama_content(response.json())
        parsed_json = json.loads(_strip_json_fence(raw_content))
        flight_search = FlightSearchRequest.model_validate(parsed_json)
        _validate_flight_search_constraints(flight_search, message)
    except ValidationError as exc:
        LOGGER.warning("Llama flight extraction failed: ValidationError: %s", exc.errors())
        LOGGER.warning("Raw Llama response: %s", raw_content)
        return None
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        LOGGER.warning("Llama flight extraction failed: %s", exc)
        LOGGER.warning("Raw Llama response: %s", raw_content)
        return None

    LOGGER.info("Llama flight extraction succeeded.")
    return flight_search


def resolve_clarification_answer_with_llama(
    asked_question: str,
    user_reply: str,
    awaiting: list[str],
) -> ClarificationAnswer | None:
    """Extract only the slot(s) requested by a previous clarification question."""

    try:
        base_url, model = _load_ollama_settings()
    except ValueError as exc:
        LOGGER.warning("Llama clarification extraction disabled: %s", exc)
        return None

    awaiting_fields = [str(field) for field in awaiting if str(field).strip()]
    system_prompt = (
        "You extract answers to the assistant's latest clarification question. "
        f"{_current_date_instruction()} "
        "The assistant asked this question: "
        f"{asked_question!r}. "
        "The user replied with the next message. Extract only fields listed in "
        f"awaiting={awaiting_fields!r}. Resolve any travel dates to future ISO "
        "YYYY-MM-DD dates. Use departure_date for departure or check-in answers, "
        "return_date for return or check-out answers, nights for stay length, "
        "origin_code for a specific departure airport code, and origin_text for "
        "a non-code departure airport or origin answer. "
        "If the user's reply does not answer the asked question or changes subject, "
        "return resolved=false and leave all other fields null."
    )

    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": CLARIFICATION_ANSWER_JSON_SCHEMA,
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_reply},
                ],
            },
            timeout=OLLAMA_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        LOGGER.warning(
            "Llama clarification extraction failed: %s",
            exc.__class__.__name__,
        )
        return None

    if not response.ok:
        LOGGER.warning(
            "Llama clarification extraction failed with HTTP %s.",
            response.status_code,
        )
        return None

    try:
        raw_content = _extract_ollama_content(response.json())
        parsed_json = json.loads(_strip_json_fence(raw_content))
        return ClarificationAnswer.model_validate(parsed_json)
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        LOGGER.warning("Llama clarification extraction failed: %s", exc.__class__.__name__)
        return None


def route_chat_action_with_llama(
    message: str,
    conversation_state: dict[str, Any],
) -> LlamaChatActionRoute | None:
    """Route chat text to a validated action without performing any backend work."""

    try:
        base_url, model = _load_ollama_settings()
    except ValueError as exc:
        LOGGER.warning("Llama chat action routing disabled: %s", exc)
        return None
    LOGGER.info("Llama chat action routing attempted.")
    raw_content: str | None = None

    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": CHAT_ACTION_ROUTER_JSON_SCHEMA,
                "options": {"temperature": 0},
                "messages": [
                    {
                        "role": "system",
                        "content": f"{CHAT_ACTION_ROUTER_PROMPT}\n\n{_current_date_instruction()}",
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Conversation state:\n{json.dumps(conversation_state)}\n\n"
                            f"User message:\n{message}"
                        ),
                    },
                ],
            },
            timeout=OLLAMA_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        LOGGER.warning("Llama chat action routing failed: %s", exc.__class__.__name__)
        return None

    if not response.ok:
        LOGGER.warning("Llama chat action routing failed with HTTP %s.", response.status_code)
        return None

    try:
        raw_content = _extract_ollama_content(response.json())
        parsed_json = json.loads(_strip_json_fence(raw_content))
        route = LlamaChatActionRoute.model_validate(parsed_json)
    except ValidationError as exc:
        LOGGER.warning("Llama chat action routing failed: ValidationError: %s", exc.errors())
        LOGGER.warning("Raw Llama router response: %s", raw_content)
        return None
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        LOGGER.warning("Llama chat action routing failed: %s", exc)
        LOGGER.warning("Raw Llama router response: %s", raw_content)
        return None

    LOGGER.info("Llama chat action routing succeeded: %s", route.action)
    return route


def route_tool_with_llama(
    message: str,
    conversation_state: dict[str, Any],
) -> TravelToolCall | None:
    """Choose a validated backend tool without executing it."""

    try:
        base_url, model = _load_ollama_settings()
    except ValueError as exc:
        LOGGER.warning("Llama tool routing disabled: %s", exc)
        _emit_llama_tool_route_trace(
            {
                "event": "model_settings_error",
                "prompt_version": TRAVEL_TOOL_ROUTER_PROMPT_VERSION,
                "schema_version": TRAVEL_TOOL_SCHEMA_VERSION,
                "user_message": message,
                "safe_context_sent_to_llama": conversation_state,
                "validation_errors": [str(exc)],
                "failure_category": "MODEL_AVAILABILITY",
            }
        )
        return None
    LOGGER.info("Llama tool routing attempted.")

    def _call_router(system_prompt: str, *, retry: bool = False) -> TravelToolCall | None:
        raw_content: str | None = None
        event: dict[str, Any] = {
            "event": "tool_router_attempt",
            "retry": retry,
            "prompt_version": TRAVEL_TOOL_ROUTER_PROMPT_VERSION,
            "schema_version": TRAVEL_TOOL_SCHEMA_VERSION,
            "ollama_base_url": base_url,
            "model": model,
            "user_message": message,
            "safe_context_sent_to_llama": conversation_state,
            "raw_llama_response": None,
            "parsed_json": None,
            "selected_tool": None,
            "tool_arguments": None,
            "schema_valid": False,
            "validation_errors": [],
        }
        try:
            response = requests.post(
                f"{base_url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "format": LLAMA_PLANNER_TOOL_CALL_JSON_SCHEMA,
                    "options": {"temperature": 0},
                    "messages": [
                        {
                            "role": "system",
                            "content": f"{system_prompt}\n\n{_current_date_instruction()}",
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Conversation state:\n{json.dumps(conversation_state)}\n\n"
                                f"User message:\n{message}"
                            ),
                        },
                    ],
                },
                timeout=OLLAMA_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            LOGGER.warning("Llama tool routing failed: %s", exc.__class__.__name__)
            event["validation_errors"] = [f"MODEL_AVAILABILITY: {exc.__class__.__name__}"]
            event["failure_category"] = "MODEL_AVAILABILITY"
            _emit_llama_tool_route_trace(event)
            return None

        if not response.ok:
            LOGGER.warning("Llama tool routing failed with HTTP %s.", response.status_code)
            event["validation_errors"] = [f"MODEL_AVAILABILITY: HTTP {response.status_code}"]
            event["failure_category"] = "MODEL_AVAILABILITY"
            _emit_llama_tool_route_trace(event)
            return None

        try:
            raw_content = _extract_ollama_content(response.json())
            event["raw_llama_response"] = raw_content
            LOGGER.info(
                "Raw Llama tool response%s: %s",
                " retry" if retry else "",
                raw_content[:1000],
            )
            parsed_json = json.loads(_strip_json_fence(raw_content))
            event["parsed_json"] = parsed_json
            planner_call = LlamaPlannerToolCall.model_validate(parsed_json)
            tool_call = TravelToolCall.model_validate(planner_call.model_dump())
            event["selected_tool"] = tool_call.tool
            event["tool_arguments"] = tool_call.arguments
            event["schema_valid"] = True
            _emit_llama_tool_route_trace(event)
            return tool_call
        except ValidationError as exc:
            try:
                legacy_tool_call = TravelToolCall.model_validate(parsed_json)
                event["selected_tool"] = legacy_tool_call.tool
                event["tool_arguments"] = legacy_tool_call.arguments
                event["schema_valid"] = True
                event["validation_errors"] = []
                event["legacy_schema_fallback"] = True
                _emit_llama_tool_route_trace(event)
                return legacy_tool_call
            except ValidationError:
                LOGGER.warning("Llama tool validation failed: %s", exc.errors())
                LOGGER.warning("Raw Llama tool response: %s", raw_content)
                event["raw_llama_response"] = raw_content
                event["validation_errors"] = [f"SCHEMA_ARGS: {exc.errors()}"]
                event["failure_category"] = "SCHEMA_ARGS"
                _emit_llama_tool_route_trace(event)
                return None
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            LOGGER.warning("Llama tool routing failed: %s", exc)
            LOGGER.warning("Raw Llama tool response: %s", raw_content)
            event["raw_llama_response"] = raw_content
            category = "JSON_PARSE" if isinstance(exc, json.JSONDecodeError) else "SCHEMA_ARGS"
            event["validation_errors"] = [f"{category}: {exc}"]
            event["failure_category"] = category
            _emit_llama_tool_route_trace(event)
            return None

    tool_call = _call_router(TRAVEL_TOOL_ROUTER_PROMPT)
    if tool_call is None:
        return None
    needs_repair_retry = tool_call.tool == "general_help" or (
        tool_call.tool in {"search_flights", "search_hotels"}
        and not tool_call.arguments
    )
    if needs_repair_retry:
        retry_prompt = (
            "You are a strict travel tool router. Return one JSON object matching "
            "the schema. Prefer prepare_trip_request whenever the user message "
            "contains any travel origin, destination, region, month, date phrase, "
            "duration, package, flight, hotel, or budget. Return general_help only "
            "for non-travel chat.\n"
            "For fresh requests, trust the current user message over stale "
            "active_trip dates.\n"
            "Known normalisations: Birmingham origin is BHX. Florida is a broad "
            "region with destination candidates MCO, MIA, TPA, FLL. Orlando is "
            "MCO. Month-only wording such as in November becomes month 2026-11, "
            "date_text in november, with departure_date null and return_date null.\n"
            "If the previous intent is package_search and the user adds details, "
            "preserve package_search unless they explicitly ask for flights only.\n"
            "Example output for 'i want go on a trip to florida from birmingham "
            "in november': {\"tool\":\"prepare_trip_request\",\"arguments\":"
            "{\"intent\":\"package_search\",\"origin_text\":\"Birmingham\","
            "\"origin_code\":\"BHX\",\"destination_text\":\"Florida\","
            "\"destination_type\":\"region\",\"destination_candidates\":"
            "[\"MCO\",\"MIA\",\"TPA\",\"FLL\"],\"date_text\":\"in november\","
            "\"month\":\"2026-11\",\"departure_date\":null,\"return_date\":null,"
            "\"missing_fields\":[\"exact_dates\",\"destination_airport_choice\"]}}"
        )
        retry_tool_call = _call_router(retry_prompt, retry=True)
        if retry_tool_call is not None:
            tool_call = retry_tool_call

    LOGGER.info("Llama selected tool: %s", tool_call.tool)
    return tool_call
