# Implementation Log

## Project Architecture Guide

- Added `docs/project-file-and-architecture-guide.md` as the current detailed
  reference for project files, runtime architecture, APIs, data contracts,
  local setup, test commands, security boundaries, and known limitations.

## Initial Scaffold

- Created FastAPI backend with CORS enabled for the Vite frontend.
- Added `GET /health`.
- Added `POST /chat` with placeholder assistant response.
- Added mocked Llama intent extraction.
- Added mocked travel provider data for flights and hotels.
- Added placeholder database, model, and schema files.
- Created React + Vite + TypeScript frontend.
- Added responsive chat interface placeholder.
- Added API service file for backend communication.
- Added documentation and project ignore rules.

## Deferred Work

- No authentication.
- No production database.
- No Expedia API.
- Local model routing was added in a later stage.
- No voice input.
- No PWA manifest or service worker yet.

## Stage 2 Database Scaffold

- Added SQLite database setup with SQLAlchemy.
- Added `users`, `sessions`, `messages`, and `bookings` tables.
- Added automatic table creation on FastAPI startup.
- Added demo user/session handling until authentication exists.
- Updated `/chat` to save user and assistant messages.
- Updated booking retrieval to search the database instead of hardcoded mock bookings.
- Added automatic booking reference generation.
- Added `POST /booking/create` for creating application bookings.

## Still Deferred After Stage 2

- No authentication.
- No Expedia API.
- Local model routing was added in a later stage.
- No voice input.
- No PWA manifest or service worker yet.
- No Alembic migrations yet.

## Stage 3 Frontend Result Cards

- Added structured frontend result types for flights, hotels, packages, bookings, and suggestions.
- Updated the chat UI so assistant responses can show normal reply text plus structured result cards.
- Added responsive card layouts for laptop and Android browser widths.
- Kept backend logic unchanged for this stage.

## Booking Confirmation Workflow

- Changed chat-based package booking so it no longer creates a booking immediately.
- Package searches now return mock package options with `requires_confirmation: true`.
- Added temporary demo-session pending booking state.
- Added confirmation handling for messages such as `yes`, `confirm`, and `book it`.
- Added cancellation handling for messages such as `no`, `cancel`, and `do not book`.
- Confirmed bookings are saved to SQLite and returned as booking cards.

## Multiple Package Options

- Added multiple mock package options for Rome and Paris.
- Added numbered package options with airline, hotel, and estimated price details.
- Added simple option browsing for messages such as `show option 2` and `is there another option?`.
- Added cheaper option sorting for `show cheaper options`.
- Added option selection for messages such as `select option 3` and `I like option 2`.
- Kept booking creation behind a final confirmation after an option is selected.

## Fuzzy Confirmation And Cancellation

- Added more confirmation phrases such as `yeah`, `yep`, `proceed`, `go ahead`, `sounds good`, and `sure`.
- Added more cancellation phrases such as `nope`, `stop`, and `never mind`.
- Added fuzzy matching for small spelling mistakes such as `confrim`, `go ahad`, `sounds gud`, and `cansel`.
- Kept the pending package selection and final confirmation workflow unchanged.

## Stage 4A Duffel Flight Search

- Added `POST /flights/search` for structured flight searches.
- Added typed flight search request and response schemas in `backend/app/schemas.py`.
- Added `backend/app/providers/duffel_provider.py` to call Duffel sandbox offer requests.
- Converted the project request shape into Duffel slices, adult passengers, cabin class, and max connections.
- Normalised Duffel offers into frontend-friendly flight result objects.
- Kept `app/mock_provider.py` and added a normalised mock fallback helper.
- Added safe fallback behavior for missing credentials, HTTP errors, timeouts, malformed responses, and empty offers.
- Kept `/chat`, package selection, booking confirmation, SQLite storage, and frontend cards unchanged.

Run the backend:

```powershell
cd backend
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0
```

Example `POST /flights/search` body:

```json
{
  "searchType": "flight",
  "passengerCount": 1,
  "itinerary": {
    "tripType": "round_trip",
    "origin": {
      "city": "London",
      "code": "LHR"
    },
    "destination": {
      "city": "New York",
      "code": "JFK"
    },
    "dates": {
      "departure": "2026-09-12",
      "return": "2026-09-19"
    }
  },
  "preferences": {
    "cabinClass": "economy",
    "maxStops": 0
  }
}
```

PowerShell test:

```powershell
$body = @{
  searchType = "flight"
  passengerCount = 1
  itinerary = @{
    tripType = "round_trip"
    origin = @{ city = "London"; code = "LHR" }
    destination = @{ city = "New York"; code = "JFK" }
    dates = @{ departure = "2026-09-12"; return = "2026-09-19" }
  }
  preferences = @{ cabinClass = "economy"; maxStops = 0 }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/flights/search" `
  -ContentType "application/json" `
  -Body $body | ConvertTo-Json -Depth 6
```

The response field `provider_used` is `"duffel"` when Duffel sandbox was used
and `"mock_fallback"` when fallback mock data was returned. This stage is Duffel
sandbox only; use a `duffel_test_` token.

## Stage 5A Local Llama Extraction Test

- Added `backend/test_llama_extraction.py` as a standalone Ollama connection test.
- Added `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, and `USE_LOCAL_LLM` placeholders to `backend/.env.example`.
- The script calls local Ollama at `POST /api/chat` with `stream=false`, JSON output, and temperature `0`.
- The script sends a fixed travel request and asks Llama 3.1 to return the existing flight search JSON structure.
- The returned JSON is parsed and validated with `FlightSearchRequest`.
- Llama is not integrated into `/chat` yet; the existing rule-based logic remains active.
- Duffel, `/flights/search`, booking, package, confirmation, and SQLite logic remain unchanged.

Manual Ollama setup:

```powershell
ollama pull llama3.1:8b-instruct-q4_0
ollama run llama3.1:8b-instruct-q4_0
```

Run the standalone test:

```powershell
cd backend
.\.venv\Scripts\activate
python test_llama_extraction.py
```

If Ollama is not running, the script prints:

```text
Ollama is not reachable. Start Ollama and run: ollama pull llama3.1:8b-instruct-q4_0
```

The project now uses the local 8B Llama model quantized to 4-bit precision:
`llama3.1:8b-instruct-q4_0`. This reduces local memory requirements and
improves response speed compared with a 16-bit model. Because Llama only
chooses or extracts backend tool inputs, Pydantic validation and deterministic
backend execution remain the safety boundary.

## Stage 5B Chat To Llama To Duffel Flight Search

- Updated `app/llama_service.py` with local Ollama flight JSON extraction using the existing `FlightSearchRequest` schema.
- Kept the existing rule-based intent logic and added `fly` and `flying` detection for natural flight requests.
- Updated `/chat` to attempt Llama extraction only for rule-classified flight searches.
- Reused the same Duffel-first and mock-fallback helper used by `POST /flights/search`.
- Preserved SQLite logging for user and assistant chat messages.
- Added safe fallback to the existing rule-based mock flight flow when Ollama extraction fails or returns invalid data.
- Kept hotel, package, booking, selection, confirmation, and cancellation flows unchanged.
- Updated frontend chat types so existing flight cards can render normalised Duffel flight results.

## Stage 5C Flight Result Memory And Follow-Ups

- Added demo-session memory for the latest normalised flight results.
- Added deterministic cheapest, fastest, show-option, select-option, and book-cheapest flight follow-ups.
- Added confirmation handling that writes a selected flight booking through the existing SQLite booking helper.
- Preserved cancellation safeguards so cancellation applies only to pending package or flight selections.

## Stage 5D Llama-First Chat Router

- Added a validated Llama action router with constrained backend actions and non-secret conversation state.
- Routed natural follow-ups such as `I like it` and `can I book it?` to deterministic current-flight selection.
- Kept Llama separate from Duffel and SQLite; backend code remains the only executor.
- Kept rule-based routing as a fallback when Llama is unavailable or returns invalid action JSON.

## Destination Discovery Flexible-Date Fix

- Added support for dateless destination discovery follow-ups such as `choose the place first`.
- The assistant can now show destination suitability ideas without inventing a departure date; flight prices are checked after a destination/date is chosen.
- Added cheapest-day comparison for a selected destination. For `next week`, the backend compares the next Monday through Sunday and stores the date-price comparison in active trip memory.
- Added explicit day overrides such as `what about 13th`, which rebuild the flight request from the active trip destination, origin, and trip duration.
- Prevented stale flight routes such as old `LHR -> JFK` requests from overriding active trip state.
- Removed invented family suitability reasons unless the user actually mentions family, children, or kids.
- Made the local Llama configuration mandatory for `llama3.1:8b-instruct-q4_0`.
- Preserved package, booking, confirmation, cancellation, Duffel, and SQLite workflows.

## Stage 5E Tool-Based Llama Router

- Replaced the primary narrow Llama action path with a validated `TravelToolCall`
  contract: Llama now selects a tool and arguments while backend code executes
  every external call and database write.
- Added safe conversation context for the router, including the latest flight
  search request, normalised result count and route, current displayed flight,
  pending selection, last tool, and short recent chat facts. No API keys or raw
  Duffel payloads are included.
- Added deterministic executors for flight search, ranking, option display,
  selection, availability refresh, booking confirmation, cancellation, and
  booking lookup.
- Availability checks rerun the stored Duffel search, match by offer ID first
  and then stable flight fields, and make only a Duffel test-mode availability
  statement.
- Extended demo-session memory with normalised result data, the original typed
  search request, current option, selected pending flight, pending action, and
  last tool used.
- Retained the rule-based path only as a safe fallback for unavailable or invalid
  Llama routing and to preserve existing package-option behavior.
- Added unit coverage for tool validation, no-context availability, cheapest
  ranking, natural current-flight selection and confirmation, refreshed
  availability, cancellation, and booking lookup.

## Stage 5F Flight Result Quality And Smarter Follow-Ups

- Enriched normalised Duffel and mock flight results with full/short offer IDs,
  option numbers, split departure and arrival dates/times, duration and stops
  labels, cabin labels, and passenger count.
- Preserved the original ID internally for matching and booking while using the
  short display ID in the UI.
- Added `update_passenger_count`, which validates 1-9 passengers, reruns the
  stored typed flight search, and only selects a refreshed offer when the user
  explicitly asks to book it for the new party size.
- Added `describe_current_flight` so cabin, price, passenger, time, and summary
  questions are answered from the current displayed option rather than by
  showing a different flight.
- Added deterministic `cheapest_fastest` and `best_value` rankings. Combined
  rankings account for price, duration where present, and stops.
- Updated availability wording to distinguish a matching current test offer
  from a changed/expired Duffel test offer and its closest refreshed alternative.
- Expanded flight booking summaries with airline, route, cabin, passengers,
  stops, dates/times, price, and currency without changing the SQLite schema.
- Replaced the technical frontend flight cards with a shared responsive card
  used by chat and direct search, including cabin and provider badges and clean
  date/time labels.
- Added automated coverage for business and passenger extraction, Duffel adult
  payload construction, passenger refresh/selection, cabin follow-ups, combined
  ranking, and booking-summary content.

## Stage 5G Persistent Conversational Travel Memory

- Added the SQLite `session_states` table for the latest validated flight search,
  normalised results, current option, pending flight selection, pending action,
  last tool, and compact recent facts.
- Added the SQLite `user_preferences` table as a safe foundation for future
  personalised recommendations.
- Added `app/memory_store.py` to isolate JSON serialisation, session state,
  safe Llama context, and preference helpers from `main.py`.
- Made SQLite-backed session state the primary source for flight ranking, option
  display, selection, confirmation, cancellation, passenger refresh, and
  availability checks; the in-memory dictionary remains a compatibility fallback.
- Stored only validated flight-search requests and normalised flight results.
  Raw Duffel payloads, API keys, tokens, and environment values are never stored.
- Added defensive redaction for token-shaped text before recent facts are stored
  or supplied to the Llama router.
- Added persistence tests that clear the in-memory dictionary before ranking,
  confirmation, and cancellation, plus preference and safe-context tests.

## Stage 5H Authentication And User Profile Foundation

- Added local account fields to `users`: non-null email, bcrypt password hash,
  optional full name, and update timestamp while retaining `display_name` for
  application compatibility.
- Added `app/auth.py` for password hashing, password verification, JWT issue
  and validation, required authentication, and optional authentication.
- Added register, login, profile, and preference endpoints. Registering a user
  also creates its initial `UserPreference` and `ChatSession` records.
- Made `/chat` and `/booking/create` ownership-aware when a valid bearer token
  is supplied. Requests with no token still use the original demo user/session.
- Scoped booking lookup to the active user, avoiding cross-account booking
  lookup through a guessed reference.
- Added a small React account surface for register, login, logout, profile
  editing, and saved preference editing. The token stays in local browser
  storage and is attached only as a bearer header to backend calls.
- Added ten isolated SQLite unittest cases for account registration, password
  hashing, duplicate users, login success/failure, token profile lookup,
  preferences, authenticated chat ownership, demo fallback, and booking
  ownership.
- Pinned `bcrypt==4.0.1` because it is compatible with `passlib==1.7.4` in this
  local Python environment.
- This remains local application authentication. It intentionally does not add
  password recovery, verification emails, refresh tokens, role management, or
  production deployment hardening.

## Stage 6A Hotel Search Integration

- Added typed `HotelSearchRequest`, `NormalisedHotelResult`, and
  `HotelSearchResponse` contracts.
- Added `app/providers/liteapi_hotel_provider.py`, which calls LiteAPI
  `POST /hotels/rates` through the backend only and converts supported fields to
  compact hotel cards.
- Added deterministic `mock_hotel_provider.py` results for London, Paris, New
  York, Rome, Barcelona, Dubai, Cairo, and Tokyo. It is used when LiteAPI is
  disabled, unavailable, invalid, empty, or missing credentials.
- Extended `session_states` and `memory_store.py` with persistent hotel search,
  result, current-option, and pending-selection data. Only normalised cards and
  validated search requests are stored.
- Added hotel tools for search, cheapest ranking, option display, selection, and
  confirmation. The existing cancellation and booking flows now safely handle
  pending hotels as well as flights and legacy package options.
- Added `POST /hotels/search` plus chat hotel cards in the React UI.
- Added twelve network-free tests for fallback, normalisation, persistence,
  ranking, option display, selection, confirmation, cancellation, provider
  failures, and safe context output.
- LiteAPI raw payloads and `LITEAPI_KEY` values are never sent to Llama, the
  frontend, application responses, logs, tests, or SQLite state.
- Flight-plus-hotel package building is intentionally not included in Stage 6A.

## Stage 6B/6C Voyage AI Website, Cart, And Checkout

- Rebranded the React product as Voyage AI and made The Voyager the dedicated
  conversational assistant page.
- Added React Router routes for Home, Flights, Hotels, Cart, Checkout, The
  Voyager, and Profile, wrapped in a responsive shared navigation and footer.
- Added manual Flights and Hotels pages that call the existing typed search
  endpoints and reuse the established flight and hotel cards.
- Added optional Add to cart actions to manual result cards without changing
  their chat rendering behavior.
- Added `CartContext` with browser `localStorage` persistence for normalised,
  public flight and hotel selections. Cart totals are grouped by currency; no
  currency conversion occurs.
- Added `POST /checkout/confirm`, which creates one local SQLite
  `cart_checkout` booking for a demo or authenticated user. It does not call
  payment, Duffel order, or LiteAPI reservation APIs.
- Added `booking_type` and `details_json` booking fields plus local SQLite
  compatibility columns. Checkout details contain only safe public summary
  fields and deliberately discard arbitrary `item_data`.
- Added six isolated checkout tests covering demo/authenticated ownership,
  empty carts, single/mixed currency totals, and secret/raw-provider-data
  exclusion.
- Existing chat, Llama routing, flight and hotel provider flows, auth, and demo
  fallback behavior remain unchanged.

## Stage 6E Date Understanding And General Help Fix

- Added `app/date_utils.py` with deterministic UTC-date, yearless-date,
  future-date, trip-date, and hotel-date validation helpers.
- Added current-date instructions to Llama flight extraction and tool routing,
  including the rule that an unyearred month/day must resolve to its next future
  occurrence in ISO format.
- Added a backend chat safety layer that restores the original yearless date
  phrase before normalising a model-generated old year. This prevents past dates
  from reaching Duffel, LiteAPI, or mock fallback providers.
- Made direct `/flights/search` and `/hotels/search` requests reject explicit
  past dates or invalid date ordering with a clear HTTP 400 error.
- Updated mock results to preserve validated request dates and use clean,
  route-based offer IDs such as `MOCK-CAI-LHR-001` and `Offer CAI-LHR-001`.
- Updated The Voyager general help to describe flights, hotels, availability,
  booking preparation, the Voyage AI cart, and checkout.
- Added ten regression tests for yearless October and hotel dates, chat
  correction, direct API rejection, invalid return/checkout ordering, mock
  date/ID quality, and updated general help.

## Stage 6F Follow-Up Context And Route Memory Fix

- Extended safe Llama context with detailed latest flight route, cities, dates,
  passenger count, cabin class, stops, result count, current option, detailed
  hotel context, and an `active_context_type` derived from persistent last-tool
  state.
- Added deterministic partial-follow-up handling before provider execution.
  Date, cabin, passenger, and similar follow-ups reuse the latest persisted
  route instead of accepting a route invented by a model prompt example.
- Added relative travel date support for `tomorrow`, `day after tomorrow`, and
  `next week`.
- Made partial follow-ups with no active route ask for origin and destination.
  Ambiguous destination changes such as `what about Paris?` request
  clarification instead of silently changing a route.
- Added hotel-active protection so an ambiguous hotel follow-up cannot turn into
  a random flight search.
- Added ten isolated follow-up tests covering initial and follow-up relative
  dates, route reuse, next-week dates,
  passenger updates, no-context clarification, prompt-route leakage,
  hotel context, latest-route replacement, safe context fields, and the
  rule-based fallback path.

## Stage 6G Current Location And Destination Discovery

- Added `POST /location/current`, which receives browser geolocation
  coordinates, asks Duffel Places for nearby airport suggestions, chooses the
  nearest airport with Haversine distance, and stores only the safe
  default-origin airport context in SQLite session state.
- Reworked `app/airport_lookup.py` so it no longer contains a hard-coded
  airport catalogue. It is provider-backed and raises a controlled error when
  airport lookup is unavailable, allowing the frontend to ask for manual origin.
- Added `app/destination_candidates.py`, `app/destination_discovery.py`, and
  `app/providers/opentripmap_provider.py` for preference-based destination
  recommendations. OpenTripMap enriches curated destination seeds when
  available; fallback tags keep the demo deterministic if the API is missing or
  unavailable.
- Added `discover_destinations` to the Llama tool router. Llama extracts
  preferences such as beach, skiing, family, children, culture, and max flight
  duration; the backend performs scoring and recommendation execution.
- Added destination recommendation response fields and chat result rendering for
  city, country, airport code, score, match reasons, warnings, evidence summary,
  and estimated flight minutes.
- Added frontend location support in The Voyager using
  `navigator.geolocation.getCurrentPosition()` with graceful fallback if
  permission is denied or unavailable.
- Added safe origin handling for destination-only flight requests: explicit
  origin, saved/default origin, browser-derived nearest airport, then a
  clarification asking where the user is flying from.
- Added eight destination/location tests covering nearest airport lookup,
  fallback scoring, flight-duration warnings, provider failure fallback, safe
  Llama context, default-origin use, and missing-origin clarification.
- Added `OPENTRIPMAP_API_KEY` and `OPENTRIPMAP_BASE_URL` placeholders to
  `.env.example`. The real key remains in local `backend/.env` only.

## Stage 6G Refinement: Destination Discovery Quality Fix

- Tightened skiing evidence classification so weak/general OpenTripMap places
  such as museums, churches, aquariums, monuments, beaches, generic resorts,
  national parks, or generic mountains no longer count as skiing evidence.
- Skiing now requires strong terms such as ski, skiing, ski resort, winter
  sports, snow, alpine, piste, slope, cable car, chairlift, gondola, or mountain
  resort, or a trusted fallback tag such as `skiing_nearby`.
- Explicit activity requests are now treated as required activities. For
  skiing requests, destinations without strong skiing evidence or a trusted
  skiing fallback tag are excluded from the main recommendation list, so
  Europe/short-flight preferences cannot rank non-skiing destinations first.
- Added candidate regions and region-aware refinement. Europe follow-ups keep
  Europe and Europe-adjacent options and remove Africa/Middle East beach
  destinations from the refined result set.
- Added persistent destination discovery request/result memory so follow-up
  prompts like `keep it in europe and i dont like long flights` refine the
  previous recommendations instead of falling into flight search. Refinements
  preserve original required activities such as skiing.
- Added short-flight preference scoring. Without an explicit limit, under two
  hours is strongly preferred, 2-3 hours is acceptable, and longer flights are
  penalised with a clear warning.
- Added a trade-off explanation when stronger skiing suitability conflicts with
  shorter flight preferences, including an honest message when no short-flight
  European skiing match exists in the current candidate set.
- Added regression tests for weak skiing evidence, Red Sea skiing misranking,
  Europe refinement, short-flight ordering, and avoiding mock flight search for
  region follow-ups.

## Stage 6G Refinement: Constraint Removal And Intent Shift

- Added destination-discovery follow-up logic that can remove constraints as
  well as add them. Phrases such as `doesn't have to be in Europe` remove the
  Europe region preference, while `I don't care about long flights` clears
  short-flight and max-duration constraints.
- Added skiing removal handling for prompts such as `I don't want skiing`,
  `no skiing`, and `I don't want skiing in Europe`. These remove skiing from
  both `activityPreferences` and `requiredActivities` instead of keeping
  Geneva/Lyon sticky from the previous request.
- Added an intent-shift path for broader leisure requests such as `places to
  enjoy`, `things to do`, `attractions`, and `sightseeing`. These become
  general activity preferences rather than hard skiing requirements.
- Updated response wording so The Voyager clearly says when constraints were
  removed and no longer claims it refined using region/flight-duration
  preferences after the user explicitly removed those constraints.
- Added regression tests for Europe removal, flight-duration removal, skiing
  removal, general-enjoyment intent shift, and the ambiguous `I don't want
  skiing in Europe` follow-up.

## Stage 6G Refinement: Excluded Regions

- Added `excludedRegions` to destination discovery memory and safe Llama
  context so the backend can distinguish `doesn't have to be in Europe` from
  `I don't want it in Europe`.
- Negative Europe follow-ups such as `not in Europe`, `outside Europe`, `no
  Europe`, and `no I don't want it in Europe` now remove Europe as a positive
  preference and add Europe to the excluded region list.
- Destination scoring filters excluded regions before ranking, so Geneva, Lyon,
  Nice, Rome, and Athens are not shown when Europe is excluded.
- Added outside-Europe skiing candidates including Marrakesh, Beirut, Erzurum,
  and Kayseri so skiing remains possible after Europe is rejected.
- Added regression tests to ensure skiing stays required, Europe is excluded,
  response wording says Europe was excluded, and the assistant explains the gap
  if no outside-Europe skiing candidate exists.

## Stage 6G Refinement: Pending Destination Dates

- Added persistent pending destination-discovery memory for requests that name
  preferences and trip length but do not yet include a departure date. The
  backend now stores compact state such as origin, required activities,
  `tripDurationNights`, missing fields, and creation date.
- Date-like follow-ups such as `same day next week`, `next week`, `tomorrow`,
  and `on the 11th` now merge into the pending destination-discovery request
  before any flight-search routing can run.
- Added return-date inference for trip duration. For example, with current date
  `2026-08-04`, `same day next week` resolves to `2026-08-11`, and a 3-night
  trip returns on `2026-08-14`.
- Strengthened origin precedence for destination discovery. Explicit user
  origin wins first, then pending request origin, then saved default origin from
  location memory. The backend no longer accepts unmentioned model guesses such
  as CAI when the session has East Midlands Airport (`EMA`) saved.
- Added a guard so date-only destination-discovery follow-ups do not become
  default or mock flight searches and do not invent London/LHR.
- Added regression tests for EMA preservation, no CAI/LHR contamination, no
  invented destination, 3-night date inference, current-date parsing, and
  session isolation.

## Stage 6G Cleanup: Active Routing Path

- Consolidated the live `/chat` path so pending clarifications are checked
  before Llama tool routing, then validated tool execution runs before the
  rule-based backup path.
- Added a flight-search completeness guard. `search_flights` now refuses to run
  unless origin, destination, and departure date are present, and asks a clear
  clarification instead of inventing London/LHR.
- Removed conflicting hard-coded route defaults from control intents and Llama
  prompt examples. The backend no longer uses CAI/LHR as an implicit default
  route when a user gives only a date or incomplete follow-up.
- Made SQLite session state the source of truth whenever a database session is
  available. The older in-memory dictionaries remain only as non-database
  compatibility helpers and no longer leak stale route context into a new
  session with the same numeric id.
- Added safe routing trace logs with route, tool, origin airport code, and
  missing-field lists. These logs do not include provider keys, raw API
  payloads, or exact browser GPS coordinates.
- Added cleanup regression tests proving date-only destination follow-ups stay
  on destination discovery, missing flight destinations are not replaced with
  LHR, browser-location EMA origin is preserved, and new sessions do not reuse
  old CAI/LHR memory.

## Stage 6G Refinement: Active Trip State And Destination Selection

- Added a canonical `active_trip_json` state field to persistent session memory.
  It stores safe trip facts such as origin airport, selected destination,
  departure/return dates, trip duration, required activities, excluded regions,
  requested products, passenger count, cabin class, status, and revision.
- Added deterministic destination selection resolution for latest
  recommendations. Phrases such as `I could go for Erzurum`, `I like Hurghada`,
  `option 1`, `the second one`, and airport codes such as `ERZ` now resolve
  against stored recommendation cards before Llama flight execution.
- Provider requests are now built from the active trip when a recommendation is
  selected. For example, selecting Erzurum after an EMA-based discovery creates
  an `EMA -> ERZ` flight search using the active trip dates, not a stale or
  model-invented route.
- Added active-trip builders for flights and hotels. Flight requests use
  active origin, selected destination, departure date, return date, passenger
  count, and cabin class. Hotel requests use selected destination city plus the
  same check-in/check-out dates.
- Added stale-state invalidation. When the selected destination changes, old
  flight/hotel results and pending selections are cleared. When the main
  activity changes from skiing to beach, the previous destination and dependent
  flight/hotel memory are cleared while preserving origin and dates.
- Improved pending destination date merging so a follow-up such as `I don't
  want it in Europe and maybe same day next month` applies both the date and
  the region exclusion. With current date `2026-08-04`, that resolves to
  departure `2026-09-04` and, for a 3-night trip, return `2026-09-07`.
- Added beach intent replacement. Follow-ups that reject skiing and ask for
  beaches or beach activities remove skiing and add beach, beach activities,
  water sports, swimming, snorkelling, and diving preferences.
- Added a semantic flight-route guard so an active trip such as `EMA -> HRG`
  cannot be overridden by a stale Llama/tool request such as `ERZ -> LHR`
  unless the user explicitly asks for that route.
- Added regression tests in `backend/tests/test_active_trip_state.py` covering
  active origin/duration storage, same-day-next-month date inference, Europe
  exclusion, Erzurum selection, stale route rejection, beach replacement,
  Hurghada flight/hotel compound search, and no ERZ reuse after the destination
  changes.

## Stage 6H Package Builder And Generated Checkout

- Added active-trip package state for `latestPackageRequest`,
  `latestPackageOptions`, `selectedPackageOption`, `cart`, `checkout`,
  `flightTicket`, `hotelVoucher`, and `latestVisibleResultType`.
- Added deterministic date selection from the latest month comparison. For
  example, after a September comparison, `okay I will go on the 1st` selects
  `2026-09-01` and infers `2026-09-04` for a 3-night trip.
- Added package schemas for package build requests, flight summaries, hotel
  summaries, package options, package cart items, generated checkout responses,
  generated flight tickets, and generated hotel vouchers.
- Added a backend package builder that searches flights and hotels from the
  active trip, combines same-currency options, sorts by total price, and stores
  only normalised public package data.
- Added package cart and checkout chat flow. Selecting a package adds it to the
  session cart; checkout generates one local SQLite package booking reference.
- Added travel-document generation for completed checkout records, including
  flight ticket and hotel voucher details stored with the booking.
- Updated the Voyager frontend cards to render package totals, cart summaries,
  generated flight tickets, and generated hotel vouchers.
- Added `backend/tests/test_package_builder.py` covering date selection,
  package request validation, active-trip provider arguments, total price,
  stale-route blocking, cart selection, checkout, and generated document
  generation.

## Stage 6I Website UX And Authentication Flow

- Improved the Voyage AI website shell with a stronger travel homepage, tabbed
  search entry points, inspiration cards, continue-trip messaging, and more
  polished manual flight and hotel result controls.
- Improved flights and hotels pages with elevated search panels, sorting,
  result filtering, loading states, and clearer result layouts while preserving
  existing provider endpoints.
- Made `/chat` robust for guest and signed-in users. Optional authentication now
  treats missing, expired, or invalid bearer tokens as guest mode for
  guest-allowed endpoints, so The Voyager no longer requires sign-out when a
  stale token exists.
- Added stable browser chat session handling through `sessionId`, allowing a
  guest active trip/package cart to be adopted by the signed-in user after
  login without losing the conversation state.
- Changed cart checkout to require authentication. Guests can still search,
  chat, discover destinations, and build package options, but checkout asks them
  to sign in and preserves their selected cart/package state.
- Added protected booking history endpoints, `GET /bookings` and
  `GET /bookings/{reference}`, filtered by the authenticated user only.
- Updated the profile page with improved login/register forms, password
  visibility controls, saved preferences, and a previous-bookings section.
- Added backend tests for invalid optional auth, authenticated checkout
  requirement, and booking-history ownership.

## Stage 6J Dynamic Homepage

- Replaced the static homepage card arrays with backend-driven homepage
  endpoints for random flight deals, random package ideas, property types,
  trending destinations, and quick trip ideas.
- Added `frontend/src/api/homepageApi.ts` so React loads homepage cards from
  FastAPI rather than keeping fake destination/package data in the browser.
- Homepage cards now link into `/flights`, `/hotels`, or `/voyager` with query
  parameters or prompts already prepared.
- Flights and Hotels pages now read query parameters so homepage clicks can
  pre-fill routes, destinations, dates, cabin, passengers, and property type.
- Removed old homepage image-card sections from the active UI. The homepage now
  uses data-focused travel cards with source labels, route codes, dates, prices,
  and direct next actions.
- Added backend tests for homepage flight shape, package total calculation,
  provider-failure degradation, property types, and Voyager trip idea links.

## Stage 6K Post-Checkout Booking Documents

- Checkout now clears the active cart automatically after successful
  confirmation and records the latest booking reference for the empty-cart
  state.
- Manual cart checkout now saves compact structured booking details in
  `Booking.details_json`, including safe item summaries, totals, and generated
  document data where flight/hotel details are available.
- Chat package checkout now marks the backend session cart as checked out,
  clears active cart items, clears the selected package option, and keeps the
  generated ticket/voucher with the saved booking.
- `GET /bookings/{reference}` now returns saved package summary,
  `flightTicket`, and `hotelVoucher` fields for newer bookings while older
  summary-only bookings continue to load safely.
- Added a frontend booking details route at `/bookings/:reference`, and Profile
  now links previous bookings to their details/tickets.
- Checkout confirmation immediately renders generated flight ticket and hotel
  voucher cards when the backend returns them.

## Stage 6L The Voyager Voice Mode

- Added optional browser voice input to The Voyager using
  `SpeechRecognition` / `webkitSpeechRecognition` where available.
- Added browser text-to-speech using `window.speechSynthesis`, controlled by a
  Voice mode toggle so spoken replies remain optional.
- Added polished voice controls in the chat area: microphone start, stop,
  listening/thinking/speaking status, and unsupported-browser fallback text.
- Voice transcripts are sent through the existing `/chat` endpoint, preserving
  current text-chat routing, active-trip memory, package builder, cart, and
  checkout behaviour.
- Added response summarisation before speech so The Voyager reads concise
  summaries for flight, hotel, destination, package, booking, and travel
  document responses instead of reading long cards aloud.
- Kept voice as a frontend feature only; no separate voice backend endpoint was
  added.

## Stage 6M Package Clarification State Merge

- Added a SQLite-backed `pendingPackageRequest` field inside active trip memory
  so incomplete package requests keep their destination, route, date, stay
  length, passenger count, and missing fields across follow-up messages.
- Package clarification now runs before generic destination and flight routing,
  preventing replies such as `Cairo to London` from being treated as a separate
  stale trip when the user is completing a package request.
- Explicit routes override default origin safely, so `Cairo to London` updates
  the package route to `CAI -> LHR` while preserving an earlier departure date.
- Stay-length replies such as `3 nights` now calculate the package return date
  and trigger the existing deterministic package builder once all required
  fields are present.
- Added regression tests for London package drafts, date merge, explicit Cairo
  route override, and final `CAI -> LHR` package execution.

## Backend Structure Cleanup

- Reduced `backend/app/main.py` to a small FastAPI entrypoint facade while
  preserving `uvicorn app.main:app` and existing test import paths.
- Moved the existing consolidated backend implementation into
  `backend/app/services/chat_service.py` without changing endpoint behaviour,
  schemas, database tables, provider calls, or frontend contracts.
- Added package folders for future incremental organisation:
  `backend/app/routes`, `backend/app/services`, `backend/app/state`, and
  `backend/app/utils`.
- Cleaned local machine-specific paths from project documentation and command
  notes so setup instructions use relative project paths.
- Continued the safe split of `backend/app/services/chat_service.py` by moving
  pure helper logic into focused modules:
  `utils/option_parsing.py`, `utils/budget_parsing.py`,
  `utils/response_formatting.py`, `state/active_trip_state.py`,
  `services/cart_service.py`, `services/package_service.py`,
  `services/flight_service.py`, `services/hotel_service.py`, and
  `services/checkout_service.py`.
- Kept FastAPI route handlers in `chat_service.py`; extracted modules preserve
  the same helper names/imports so API behaviour and frontend contracts remain
  unchanged.

## Structured Llama Cart And Checkout Routing

- Added structured Llama tool outputs for `add_to_cart`, `checkout`, and
  `cart_view` so package/cart follow-up language is interpreted by Llama rather
  than by adding more backend phrase lists.
- The backend now validates those structured tools against the current session
  state: package options must exist before `add_to_cart`, option numbers must
  resolve to a real package, and checkout requires a non-empty cart.
- Chat responses now include `redirect_to` alongside the existing `nextAction`
  and `nextPath` fields. Package add-to-cart returns `/cart`, while checkout
  returns `/checkout` or the sign-in route when authentication is required.
- The frontend chat handler reads `redirect_to` directly and navigates with
  React Router without inspecting assistant message text.

## Structured Broad Destination Routing

- Added structured Llama tool support for broad or incomplete trip requests
  through `prepare_trip_request` and `search_alternative_origins`.
- Broad destinations such as Florida are stored as a region-style active trip
  target with candidate airports such as Orlando, Miami, Tampa, and Fort
  Lauderdale instead of being converted into a fake airport or card title.
- Explicit user origins now take priority over browser/default origin context;
  for example, Birmingham is resolved to `BHX` even if the saved default origin
  is East Midlands Airport (`EMA`).
- Month-only and incomplete package requests are persisted in active trip
  memory as pending trip requests, so date follow-ups can merge into the same
  route without falling into general help or stale flight memory.
- If a broad-region search has no viable results from the selected origin, the
  backend suggests safe alternative UK origins such as London Heathrow, Gatwick,
  or Manchester instead of silently changing the route.
- Added regression coverage for Birmingham-to-Florida structured slots,
  Florida date follow-ups, package month clarification, alternative-origin
  suggestions, and Llama-structured acceptance of those alternatives.
