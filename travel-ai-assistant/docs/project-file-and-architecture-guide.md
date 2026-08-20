# Voyage AI Project File And Architecture Guide

## Purpose

This document is the current technical guide for Voyage AI. It describes the
maintained project files, data flow, local setup, API behavior, security
boundaries, and current limitations.

The application is a React and Vite browser app backed by FastAPI. It uses
local 8B 4-bit Llama 3.1 through Ollama for validated tool selection, Duffel sandbox
for real flight offers, a mock fallback provider, and SQLite for application
message and booking storage.

## Technology Stack

| Layer | Technology | Responsibility |
| --- | --- | --- |
| Browser UI | React 19, TypeScript, Vite, React Router | Voyage AI pages, manual search, cart, checkout, The Voyager chat, location button, destination cards, and responsive layout. |
| HTTP API | FastAPI, Uvicorn | `/health`, `/chat`, `/flights/search`, `/hotels/search`, `/location/current`, `/booking/create`, and `/checkout/confirm`. |
| Validation | Pydantic | Request, response, Llama tool, and provider-data validation. |
| Local AI | Ollama and `llama3.1:8b-instruct-q4_0` | Flight JSON extraction and safe tool selection with the mandatory 8B 4-bit quantized model. |
| Flight provider | Duffel API v2 sandbox | Real test flight offers. |
| Persistence | SQLite and SQLAlchemy | Demo sessions, messages, and confirmed bookings. |

## Architecture

```text
React browser
    |
    | POST /chat, /flights/search, /hotels/search, or /checkout/confirm
    v
FastAPI backend
    |
    +--> Pydantic validation
    +--> Ollama/Llama tool router (chooses a tool only)
    +--> Deterministic Python tool executor
    +--> Duffel test API or mock fallback
    +--> SQLite messages, session state, and local application bookings
```

Llama never receives API keys, calls Duffel directly, writes SQLite directly,
or creates a booking. The Python backend validates and executes all actions.

## How The Main Flows Work

### Natural-Language Chat Flight Search

1. The user enters a message in `ChatBox.tsx`.
2. `chatApi.ts` sends it to `POST /chat`.
3. `main.py` loads the FastAPI entrypoint; the existing chat orchestration runs
   in `services/chat_service.py`, which creates or reuses the demo user and
   session.
4. `llama_service.py` sends the message plus safe, compact session state to
   local Ollama.
5. Llama returns a Pydantic-validated `TravelToolCall`, usually
   `search_flights` containing a `FlightSearchRequest`.
6. `services/chat_service.py` validates the request and calls the Duffel-first
   search helper.
7. `duffel_provider.py` creates Duffel slices and adult passengers, calls
   Duffel, and normalises offers.
8. The backend stores normalised results and the typed search request in
   in-memory session flight memory.
9. Messages are saved in SQLite and React renders clean flight cards.

### Direct Structured Flight Search

`FlightSearch.tsx` sends a typed payload through `flightApi.ts` to
`POST /flights/search`. This route uses the same Duffel-first helper but does
not call Llama or write chat messages. It is useful for provider testing.

### Follow-Ups, Selection, And Booking

The session remembers the latest flight results, original search request,
current displayed option, pending selection, and short message history. This
supports questions such as `which is the cheapest?`, `is it business or
economy?`, and `is it still available?`.

Selecting a flight does not write a booking. It creates a persistent pending
selection in the session state. Only a clear confirmation such as `yes`,
`confirm`, or `book it` creates a SQLite booking and generated `TAxxxxxx`
reference. Cancellation only clears a pending selection.

### Manual Search, Cart, And Checkout

1. A user opens `/flights` or `/hotels` and submits a typed search form.
2. `flightApi.ts` or `hotelApi.ts` calls the existing FastAPI search endpoint.
3. The browser receives normalised public cards and may add one or more to
   `CartContext`.
4. `CartContext` persists only public result data in browser `localStorage` so
   a page refresh does not lose the draft trip.
5. `/checkout` sends selected cart summaries to `POST /checkout/confirm`.
6. `services/chat_service.py` creates a local `cart_checkout` booking reference
   for the signed-in user, or for the existing demo user if no bearer token is
   present.

The checkout route does not call a provider order/reservation API and does not
process a payment. It drops arbitrary client `item_data` and stores only compact
public item details in `bookings.details_json`.

### Date Safety

`app/date_utils.py` prevents a travel search from using historical dates by
default. A chat phrase with a day and month but no year is interpreted as the
next future occurrence. The original user text is checked again after Llama
returns a typed tool call, which protects against an old inferred year such as
`2023-10-05`. Direct structured API requests are deliberately stricter and
return HTTP 400 for explicit past dates or invalid return/check-out ordering.

Only validated future request dates are passed to Duffel, LiteAPI, or the mock
providers. This also means stored session memory and resulting cards retain the
same corrected future dates.

### Follow-Up Context Safety

Session memory records the latest validated flight and hotel request separately.
The safe Llama context includes both request summaries plus an
`active_context_type` based on the most recent travel action. For a partial
message such as `what about tomorrow?`, backend code selects the active context,
reuses its route or hotel destination, and changes only the requested constraint.

This rule is enforced independently of the Llama router. If the router emits an
example route that the user did not name, the backend replaces it with the
latest persisted route. If no route is available, Voyage AI asks for departure
and destination details rather than attempting a default search.

Destination discovery now has an additional active-trip layer. When the user
selects a recommended destination such as `Erzurum` or `Hurghada`, the backend
stores that destination in `active_trip_json` and builds provider requests from
the active trip. Llama can identify intent, but it does not decide the final
route. For example, an active trip with origin `EMA` and selected destination
`ERZ` creates `EMA -> ERZ`; stale or model-invented routes such as `ERZ -> LHR`
are rejected or replaced unless the user explicitly asks for that route.

For broad destination wording, such as `Florida`, active trip state records the
destination type, region name, candidate airports, pending month/date text, and
alternative-origin search state. This lets later messages complete the same
trip without treating the broad region as an airport or falling back to an old
route.

## Root Files

| File | Usage |
| --- | --- |
| `README.md` | Original project overview and setup notes. Some early-stage statements are historical; this guide and `backend/README.md` describe the newer Llama/Duffel stages. |
| `run_backend_frontend_commands.txt` | Practical Windows commands for starting both services and trying basic chat messages. |
| `.gitignore` | Prevents secrets, databases, virtual environments, dependencies, build output, and editor files from being committed. |

## Backend Configuration And Utilities

| File | Usage |
| --- | --- |
| `backend/requirements.txt` | Python dependencies: FastAPI, Uvicorn, Pydantic, python-dotenv, requests, and SQLAlchemy. |
| `backend/.env.example` | Safe configuration template. Copy its keys into local `backend/.env`, never commit the real file. |
| `backend/README.md` | Backend runtime instructions, Ollama setup, Duffel test-mode notes, and tool-routing details. |
| `backend/test_duffel.py` | Safe standalone Duffel connection test. Calls airports and never prints the full token. |
| `backend/test_llama_extraction.py` | Standalone Ollama extraction test. Validates returned JSON with `FlightSearchRequest`. |

## Backend Application Files

| File | Usage |
| --- | --- |
| `backend/app/main.py` | Small FastAPI entrypoint facade. It preserves `uvicorn app.main:app` and existing test import paths while delegating the current implementation to `app/services/chat_service.py`. |
| `backend/app/services/chat_service.py` | Current consolidated FastAPI application and orchestration layer. Defines endpoints, SQLite-backed travel memory integration, provider fallbacks, deterministic tool execution, ranking, availability refresh, destination discovery, confirmation, cancellation, booking lookup, package/cart/checkout flow, and message persistence. |
| `backend/app/routes/` | Reserved package for future route modules as the API is split incrementally. |
| `backend/app/services/chat_service.py` | Current consolidated chat/API orchestration module. FastAPI route handlers remain here while pure helpers are extracted incrementally. |
| `backend/app/services/cart_service.py` | Cart summary builders and duplicate item checks for selected flights, hotels, and packages. |
| `backend/app/services/checkout_service.py` | Checkout totals, safe public cart-item persistence, booking package summary, and generated travel-document helpers. |
| `backend/app/services/flight_service.py` | Flight price extraction and ranking algorithms used by chat follow-ups. |
| `backend/app/services/hotel_service.py` | Hotel price extraction and cheapest-hotel helper logic. |
| `backend/app/services/package_service.py` | Package summary builders, package budget sorting, and package chat-result formatting. |
| `backend/app/state/active_trip_state.py` | Small active-trip state helpers for list normalisation and latest visible result tracking. |
| `backend/app/utils/option_parsing.py` | Option number parsing, including `option one`, `first one`, and package/option number phrases. |
| `backend/app/utils/budget_parsing.py` | Natural-language budget extraction such as pounds, GBP, under-budget, and thousand-pound phrases. |
| `backend/app/utils/response_formatting.py` | Shared display helpers for flight date/time splitting, cabin labels, stops labels, and duration labels. |
| `backend/app/schemas.py` | Pydantic schemas for chat, bookings, location/default origin, active trip state, destination discovery, `FlightSearchRequest`, `NormalisedFlightResult`, `FlightSearchResponse`, hotel contracts, and `TravelToolCall`. |
| `backend/app/llama_service.py` | Local Ollama client. Contains flight-extraction and tool-routing prompts, calls `/api/chat`, validates JSON, and safely returns `None` for unavailable or invalid model output. |
| `backend/app/airport_lookup.py` | Provider-backed Duffel Places nearest-airport lookup. It uses Haversine distance to choose the nearest returned airport and does not contain a hard-coded airport catalogue. |
| `backend/app/destination_candidates.py` | Curated destination seed list for preference-based recommendation scoring. |
| `backend/app/destination_discovery.py` | Deterministic destination scoring using candidate tags, estimated flight duration, traveller context, and optional OpenTripMap evidence. |
| `backend/app/providers/duffel_provider.py` | Duffel v2 adapter. Builds slices and adult passengers, handles safe HTTP errors, filters stops, and normalises provider offers. |
| `backend/app/providers/opentripmap_provider.py` | Backend-only OpenTripMap evidence adapter. Reads the key from `.env`, handles failures safely, and returns compact place evidence. |
| `backend/app/mock_provider.py` | Mock travel data and normalised flight fallback when Duffel cannot be used. |
| `backend/app/database.py` | SQLAlchemy engine/session setup. Uses the local SQLite file by default and creates tables on startup. |
| `backend/app/models.py` | SQLAlchemy models for users, chat sessions, messages, bookings, persistent session state including active trip JSON, and user preferences. |
| `backend/app/memory_store.py` | Safe JSON, active-trip persistence, dependent-state invalidation, persistent session-state, safe Llama-context, and user-preference helper layer. |
| `backend/app/date_utils.py` | Current-date-aware future-date resolution and reusable flight/hotel date ordering validation. |
| `backend/tests/test_followup_context.py` | Regression coverage for active route reuse, relative-date follow-ups, passenger updates, hotel context, example-route leakage, and no-context clarification. |
| `backend/tests/test_chat_flight_routing.py` | Unit tests for extraction, tools, passenger handling, ranking, availability, booking summaries, confirmation, cancellation, lookup, and SQLite-backed travel memory. |
| `backend/tests/test_checkout.py` | Isolated checkout tests for demo and authenticated booking ownership, totals, empty-cart validation, and safe persistence. |
| `backend/tests/test_date_handling.py` | Regression tests for yearless date resolution, direct past-date rejection, chat correction, mock date safety, and general help. |
| `backend/tests/test_destination_discovery.py` | Tests for nearest airport lookup, destination scoring, provider fallback, safe Llama context, and missing/default origin behaviour. |
| `backend/tests/test_active_trip_state.py` | Regression tests for active trip persistence, recommendation selection, stale route rejection, beach intent replacement, and compound flight/hotel search from selected destinations. |

## Frontend Files

### Configuration And Entry Files

| File | Usage |
| --- | --- |
| `frontend/package.json` | React/Vite dependencies and `dev`, `build`, and `preview` scripts. |
| `frontend/package-lock.json` | Exact npm dependency lock file. |
| `frontend/index.html` | Browser HTML page containing the React root element. |
| `frontend/src/App.tsx` | Route definitions and local authentication session coordination. |
| `frontend/src/layout/AppLayout.tsx` | Shared Voyage AI navigation, main route outlet, and footer. |
| `frontend/src/pages/` | Home, Flights, Hotels, Cart, Checkout, The Voyager, and Profile page components. |
| `frontend/src/context/CartContext.tsx` | Typed cart state and public-result `localStorage` persistence. |
| `frontend/src/api/checkoutApi.ts` | Auth-aware client for the local application checkout endpoint. |
| `frontend/src/api/hotelApi.ts` | Typed client for direct hotel searches. |
| `frontend/src/api/locationApi.ts` | Auth-aware client for saving browser-location-derived nearest-airport origin context. |
| `frontend/src/components/ChatBox.tsx` | Existing conversational assistant, now presented as The Voyager page. |

## Stage 6B/6C Product Shell

Voyage AI is now a multi-page travel website rather than a chat-only screen.
The normal site workflow uses independent Flights and Hotels searches, then a
manual cart and a local application checkout. The conversational workflow remains
available on `/voyager` and still uses validated Llama tool selection plus
backend-only provider and database execution.

The cart is deliberately browser-local and contains normalised public card data
only. Checkout now requires JWT authentication, so guests are redirected to
sign in while the selected cart remains in browser storage. A checkout result
is a local SQLite record, not a live supplier order.

## Stage 6G Location And Destination Discovery

The Voyager now supports two related planning improvements:

1. The frontend can ask the browser for location permission and send the
   coordinates to `POST /location/current`.
2. The backend maps those coordinates to the nearest known airport and stores
   only that compact `default_origin` in SQLite session state.
3. Llama receives only safe origin context such as Cairo and `CAI`, never exact
   GPS coordinates.
4. Preference prompts such as `somewhere near the beach` or `skiing holiday
   under 2 hours` route to `discover_destinations`.
5. The backend scores curated destination candidates using fallback tags,
   estimated flight duration, traveller context, and optional OpenTripMap place
   evidence.

This is not full worldwide destination search yet. It is a safe recommendation
foundation that can later feed package building and suitability scoring.

Destination discovery memory is persisted in `session_states`, so a follow-up
such as `keep it in Europe and I don't like long flights` refines the latest
recommendation request instead of starting a flight search. Skiing evidence is
treated strictly: general attractions, museums, churches, aquariums, beaches,
generic mountains, and generic parks do not count as skiing evidence. Explicit
activity requests such as skiing are stored as `requiredActivities`, which means
region or short-flight preferences can refine the list but cannot rank a
non-skiing destination above skiing matches. Later follow-ups can also remove
or replace constraints: `doesn't have to be in Europe` clears the Europe
preference, `I don't care about long flights` clears short-flight constraints,
and `I don't want skiing` removes skiing from the required activity set.
Negative region follow-ups such as `not in Europe` or `outside Europe` are
stored as `excludedRegions`, which removes Europe from the result set while
preserving other required activities such as skiing.

When a preference-based discovery request includes a trip length but no
departure date, Voyage AI stores a `pending_destination_discovery` state in
SQLite. A later date-only message such as `same day next week` completes that
pending request, infers the return date from `tripDurationNights`, and continues
destination discovery rather than starting a flight search.

The active trip stores the selected destination separately from older flight or
hotel result memory. If the user changes the main activity from skiing to beach,
the backend clears the previous selected destination and dependent flight/hotel
results while preserving useful origin and date context. A later request such as
`okay I like Hurghada, what about the flights and hotels` becomes a deterministic
compound search: flights from the active origin to `HRG` plus hotels in
Hurghada for the active trip dates.
| `frontend/vite.config.ts` | Enables React and configures Vite development-server defaults. |
| `frontend/tsconfig.json` | Strict TypeScript settings for `src`. |
| `frontend/tsconfig.node.json` | TypeScript settings for the Vite configuration file. |
| `frontend/src/vite-env.d.ts` | Vite TypeScript environment declarations. |
| `frontend/src/main.tsx` | Browser entry point; mounts `App` and imports global CSS. |
| `frontend/src/App.tsx` | Composes the chat assistant and direct flight-search panels. |
| `frontend/src/index.css` | Responsive styling for chat, forms, cards, badges, result grids, and mobile layout. |
| `frontend/README.md` | Frontend installation and API-base-URL notes. |

### API And Component Files

| File | Usage |
| --- | --- |
| `frontend/src/api/chatApi.ts` | TypeScript chat response definitions, including destination recommendation cards, and `POST /chat` client. |
| `frontend/src/api/flightApi.ts` | Structured search types, cabin-class type, normalised result type, and `POST /flights/search` client. |
| `frontend/src/components/ChatBox.tsx` | Conversation UI with loading/error states, browser location button, and flight, hotel, destination, package, booking, and suggestion rendering. |
| `frontend/src/components/FlightCard.tsx` | Shared responsive card for chat and direct search. Displays a short offer reference, option number, route, dates/times, cabin, passengers, stops, duration, price, and provider. |
| `frontend/src/components/FlightSearch.tsx` | Standalone flight form with airports, dates, passenger count, and cabin selector. |

## Documentation Files

| File | Usage |
| --- | --- |
| `docs/architecture-notes.md` | Earlier architecture and future-work notes. Some statements predate real Llama and Duffel integration. |
| `docs/implementation-log.md` | Chronological record of completed stages, including Duffel, Llama tools, memory, and Stage 5F UI work. |
| `docs/project-file-and-architecture-guide.md` | This current technical guide. |

## Flight Search Contract

The typed `FlightSearchRequest` contains `searchType`, `passengerCount`, an
itinerary with trip type, airport codes and dates, plus cabin and stop
preferences. Cabin values are economy, premium economy, business, and first.
`maxStops` zero means non-stop. A one-way request omits the return date.

Raw Duffel offers are converted to `NormalisedFlightResult`. The result includes
the full internal offer ID, short display ID, option number, airline, route,
split outbound and return dates/times, duration, stops, cabin, passenger count,
price, currency, and provider. The full offer ID is not used as the card title.

## Hotel Search Contract

`HotelSearchRequest` contains a hotel `searchType`, destination city/country or
coordinates, ISO check-in/check-out dates, guest and room counts, and optional
rating, total-price, currency, board, and amenity preferences. LiteAPI results
are converted to `NormalisedHotelResult`, containing only safe UI fields such as
hotel/room name, stay dates, nights, guests, rooms, rating, board type,
refundability, total price, currency, provider, and short ID.

The backend queries LiteAPI's hotel rates endpoint only when enabled locally;
otherwise it returns deterministic mock hotel cards. The application never
stores raw LiteAPI provider objects or API credentials. `session_states` keeps
the normalised hotel search, current option, and pending selection for safe
follow-up actions.

## Llama Tool Contract

Llama returns a `TravelToolCall` containing a tool name, validated arguments,
optional confidence, and optional explanation. Supported tools include
`search_flights`, `rank_flights`, `show_flight_option`, `select_flight`,
`check_flight_availability`, `update_passenger_count`,
`describe_current_flight`, `discover_destinations`, `select_destination`,
`search_trip_options`, `prepare_trip_request`, `search_alternative_origins`,
`add_to_cart`, `checkout`, `cart_view`, `confirm_booking`, `cancel_booking`,
`booking_lookup`, and `general_help`.

Hotel tools are `search_hotels`, `rank_hotels`, `show_hotel_option`,
`select_hotel`, and `confirm_hotel_booking`. `cancel_booking` applies to an
active hotel selection too.

The backend accepts combined ranking values `cheapest_fastest` and `best_value`
as well as cheapest, fastest, best, earliest, and latest.

Cart and checkout routing follows the same contract: Llama interprets phrases
such as choosing a package or continuing to checkout and returns a structured
tool. The backend validates that package options or cart items actually exist,
then returns `redirect_to` for the frontend to navigate. The frontend does not
inspect assistant text to decide whether to open `/cart` or `/checkout`.

Broad-destination routing also follows the tool contract. Llama may identify
that the user asked for a broad region such as Florida, but the backend stores
that as structured active-trip state with candidate airports and required
clarifications. The backend still resolves the final origin, destination,
dates, and provider requests deterministically.

## API Endpoints

| Method and path | Purpose |
| --- | --- |
| `GET /health` | Returns `{"status": "ok"}` for a quick backend check. |
| `POST /chat` | Main AI route: Llama tool routing, session memory, Duffel/mock searches, and booking handling. |
| `POST /flights/search` | Direct structured flight search using Duffel first and mock fallback. |
| `POST /hotels/search` | Direct structured hotel search using LiteAPI first and mock fallback. |
| `POST /location/current` | Converts browser coordinates into a safe nearest-airport default origin and stores that compact origin in session memory. |
| `POST /booking/create` | Manual application booking endpoint. Chat normally uses the confirmation flow instead. |
| `POST /auth/register` | Creates a local account, profile/preferences foundation, and returns a bearer token. |
| `POST /auth/login` | Validates local credentials and returns a bearer token. |
| `GET`/`PATCH /auth/me` | Reads or updates the authenticated local user profile. |
| `GET`/`PATCH /auth/preferences` | Reads or updates authenticated travel preferences. |
| `GET /docs` | Interactive FastAPI documentation while the backend runs. |

## Stage 5H Authentication And User Profile Foundation

Local accounts use bcrypt password hashes and backend-issued JWT bearer tokens.
Registering a user creates a user-owned chat session and preference record.
When a valid bearer token is sent to `/chat` or `/booking/create`, the existing
flight memory, messages, preferences, and booking records are associated with
that account. Requests without a token deliberately retain the original demo
user/session fallback for backwards-compatible demonstrations.

The frontend account panel stores the local application token in browser storage,
attaches it only to backend API calls, and exposes basic preference editing.
Provider credentials remain backend-only. Preference data is stored now and
will influence recommendation ranking in a later stage.

## Database Model

The local database file is `backend/travel_assistant.db` and is ignored by Git.

| Table | Usage |
| --- | --- |
| `users` | Stores local account identity, bcrypt password hashes, optional full names, and the demo fallback user. |
| `sessions` | Groups messages and bookings under each authenticated user's stable chat session, or the demo fallback session. |
| `messages` | Stores user and assistant text plus optional intent metadata. |
| `bookings` | Stores confirmed application bookings, reference, summary, flight/hotel IDs, price, and currency. |
| `session_states` | Stores safe, validated flight and hotel follow-up context, default origin airport, normalised searches, current options, and pending selections. |
| `user_preferences` | Stores saved user preferences for one user; recommendation use is a later stage. |

Latest flight results, hotel results, current option, pending selection, last
tool, safe default origin, and compact recent facts are stored in
`session_states`. `FLIGHT_SEARCH_MEMORY` remains only as a compatibility
fallback, so the core travel context can now survive an in-memory reset or
backend process restart.

For live request handling with a SQLAlchemy database session, SQLite session
state is the source of truth. The legacy in-process dictionaries are ignored in
database-backed flows so stale route data from another process/test session
cannot reappear as a default flight search.

## Chat Routing Order

The active `/chat` architecture is:

1. Create or load the authenticated/user demo session.
2. Load persistent session memory, including active trip and latest destination
   recommendations.
3. Handle pending clarification first, especially pending destination-discovery
   dates and constraints.
4. Resolve destination selections from latest recommendations before Llama
   flight execution.
5. Handle active-trip product follow-ups such as `what about flights and hotels
   there`.
6. Build safe conversation state from SQLite memory.
7. Ask the local Llama tool router to choose one backend tool.
8. Validate the returned tool call with Pydantic.
9. Execute the deterministic backend handler and semantic route guard.
10. Call provider APIs only from backend code.
11. Save the assistant response and updated memory.
12. Use the rule-based fallback only if Llama tool routing fails or returns no
   executable action.

Flight search is guarded: the backend will not call Duffel or mock flight
search unless origin, destination, and departure date are present. Missing
destinations are clarified instead of being replaced by a sample London/LHR
route.

## Environment And Security

Create `backend/.env` locally from `backend/.env.example`. The relevant keys are
`DUFFEL_API_KEY`, `DUFFEL_BASE_URL`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`,
`LITEAPI_KEY`, `LITEAPI_BASE_URL`, `USE_LITEAPI_HOTELS`,
`OPENTRIPMAP_API_KEY`, `OPENTRIPMAP_BASE_URL`, `DATABASE_URL`,
`JWT_SECRET_KEY`, `JWT_ALGORITHM`, and
`ACCESS_TOKEN_EXPIRE_MINUTES`.

- Use a Duffel token that begins with `duffel_test_` for this application.
- Never commit `backend/.env`, `travel_assistant.db`, or generated environments.
- Never place a Duffel token in frontend code, screenshots, logs, or documents.
- Never place a LiteAPI key in frontend code, screenshots, logs, documents, or
  persisted conversation state.
- Never place an OpenTripMap key in frontend code, screenshots, logs,
  documents, or persisted conversation state.
- Browser GPS coordinates are converted to a nearest-airport default origin by
  the backend. Llama receives only the safe airport context, not exact GPS.
- The backend passes only safe state to Llama, not API keys, environment values,
  or large raw Duffel payloads.
- JWTs are issued and verified only by the backend. The frontend keeps its local
  application bearer token in browser storage and never receives provider keys.
- A Duffel availability result is test-mode information only; it is not a
  guarantee of a real-world booking.

## Local Run Guide

1. Install the local model with `ollama pull llama3.1:8b-instruct-q4_0`.
   Start Ollama with `ollama serve` when it is not already running as a
   service, or run `ollama run llama3.1:8b-instruct-q4_0` to start the model
   interactively.
2. In one terminal, open `backend`,
   activate `.\.venv\Scripts\activate`, then run `uvicorn app.main:app
   --reload --host 127.0.0.1 --port 8000`.
3. The backend is then available at `http://127.0.0.1:8000`; FastAPI docs are at
   `http://127.0.0.1:8000/docs`.
4. In a second terminal, open `frontend`
   and run `npm run dev`.
5. Open the URL printed by Vite, normally `http://127.0.0.1:5173`. When that
   port is occupied, Vite selects another port such as `5174`.

For Android testing on the same network, set the frontend API base URL to the
laptop IP and use a backend host reachable from the phone. Do not expose the
Duffel token to the frontend.

Voyage AI validates the configured Ollama model before Llama calls. Set
`OLLAMA_MODEL=llama3.1:8b-instruct-q4_0`; older or non-q4 tags are rejected so
the project consistently uses the faster 4-bit local model.

## Package Builder Architecture

The package builder is driven by the SQLite-backed active trip state. Once the
active trip has an origin, selected destination, departure date, return date,
and duration, the backend can build a package request without trusting Llama to
invent route fields.

Package flow:

1. The user selects a trip date, such as `okay I will go on the 1st`.
2. The backend resolves the date inside the latest active comparison month when
   available, then updates `active_trip_json`.
3. A package request such as `find me a full package` builds a flight request
   from active origin/destination/dates and a hotel request from active
   destination city/check-in/check-out.
4. Flight and hotel provider responses are normalised and combined into
   same-currency package options.
5. Selecting a package adds it to a session cart stored in active trip memory.
6. Checkout creates a local SQLite booking reference and generated generated
   flight ticket and hotel voucher data.

This stage is deliberately a workflow. It does not call payment providers,
Duffel order creation, or hotel reservation APIs.

## Website Authentication And Session Flow

Voyage AI separates account identity from travel conversation state:

- `user_id` owns protected account data such as profile, preferences, checkout
  bookings, and booking history.
- `session_id` owns chat state such as active trip, selected destination, date
  comparison, package options, and session cart.

The browser stores the chat session id as `voyage_session_id` and sends it with
`/chat`. Guests can use The Voyager, destination discovery, flight/hotel search,
and package building. When a guest signs in from checkout, the backend can
attach the current guest chat session to the authenticated user so the selected
trip, package options, and cart are preserved.

Authentication is optional only on guest-allowed endpoints such as `/chat`.
Invalid or expired bearer tokens are treated as guest mode there. Checkout,
profile, preferences, and booking history use required authentication and reject
missing or invalid tokens.

The protected booking history endpoints are:

- `GET /bookings`
- `GET /bookings/{reference}`

Both filter by the authenticated user's id and return compact booking summaries
only. They do not expose raw provider payloads or secrets.
For newer checkout records, `GET /bookings/{reference}` also returns compact
saved document data from `Booking.details_json`, including generated flight
tickets and hotel vouchers. Older bookings without structured details still
return their summary safely.

Post-checkout frontend flow:

- `frontend/src/pages/CheckoutPage.tsx` confirms checkout, clears the cart, and
  renders generated documents immediately.
- `frontend/src/pages/BookingDetailsPage.tsx` reloads a previous booking and
  renders saved ticket/voucher cards.
- `frontend/src/components/ProfilePanel.tsx` shows previous-booking actions for
  details and tickets.
- `frontend/src/components/TravelDocuments.tsx` owns the reusable generated
  flight ticket and hotel voucher cards.

## The Voyager Voice Mode

The Voyager voice layer is frontend-only and reuses the same chat endpoint as
typed messages:

- `frontend/src/components/ChatBox.tsx` owns the chat state, sends recognised
  text through `sendChatMessage`, and triggers spoken replies when Voice mode is
  enabled.
- `frontend/src/components/VoiceControls.tsx` renders microphone, stop,
  voice-mode toggle, and status controls.
- `frontend/src/hooks/useSpeechRecognition.ts` wraps browser speech recognition
  and exposes listening state, interim transcript, final transcript, and safe
  error messages.
- `frontend/src/hooks/useTextToSpeech.ts` wraps `window.speechSynthesis` and
  exposes speaking state plus stop support.
- `frontend/src/utils/speechSummary.ts` turns rich chat responses into short
  spoken summaries so cards are not read aloud line by line.

Unsupported browsers show a voice-input fallback message while typed chat
continues to work normally.

## Homepage Data Flow

The Voyage AI homepage is a frontend shell over backend homepage endpoints:

- `frontend/src/pages/HomePage.tsx` renders the search hero and homepage
  sections.
- `frontend/src/api/homepageApi.ts` calls FastAPI homepage endpoints.
- `backend/app/services/chat_service.py` builds homepage deals through existing provider helpers
  and backend suggestion caches.
- `backend/app/schemas.py` defines `HomepageFlightDeal`,
  `HomepagePackageDeal`, `HomepageDestination`, `HomepageTripIdea`, and
  `HomepagePropertyType`.

The homepage does not store fake flight, hotel, destination, or package arrays
in React. Backend responses include navigation URLs that send users into the
manual Flights page, Hotels page, or The Voyager with relevant query parameters
or prompts.

## Tests And Verification

| Check | Command or action |
| --- | --- |
| Duffel connection | From `backend`, run `python test_duffel.py`. Success normally reports HTTP 200 and a short airport summary. |
| Ollama extraction | From `backend`, run `python test_llama_extraction.py`. Success reports JSON parsing and schema validation as true. |
| Python compilation | From the project root, run `backend\.venv\Scripts\python.exe -m py_compile backend\app\main.py backend\app\models.py backend\app\database.py backend\app\schemas.py backend\app\llama_service.py backend\app\memory_store.py backend\app\auth.py`. |
| Backend tests | Set `PYTHONPATH` to the `backend` directory, then run `backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v`. |
| Frontend build | From `frontend`, run `npm run build`. |

## Recommended Manual Conversation

1. `hello`
2. `I want to fly from London Heathrow to New York on 12 October 2026, business, no stops.`
3. `which is the cheapest?`
4. `is it still available?`
5. `is it business or economy?`
6. `can I book it for 3 passengers?`
7. `yes`

Expected outcome: the app shows business-class Duffel test cards or a clear
fallback explanation, updates the displayed option, refreshes availability,
answers the cabin question, refreshes the search for three passengers before
selection, and finally returns a application SQLite booking reference and summary.

## Current Limitations And Planned Work

- Authentication is local-application only. It has no email verification,
  password reset, refresh tokens, roles, rate limiting, or production identity
  provider integration.
- Unauthenticated traffic deliberately remains on the demo user/session for
  backwards-compatible classroom demonstration and existing tests.
- Passenger counts are represented as adults only; child, infant, and traveller
  details are future work.
- Duffel operates in sandbox. The app does not create a real airline order,
  collect payment, or issue a ticket.
- Flight follow-up memory is persistent for each session, but the legacy
  in-memory compatibility dictionary still exists during the application transition.
- Flight-plus-hotel packages can now be built from the active trip, added to a
  session cart, and checked out as a local workflow with generated flight
  ticket and hotel voucher records. Intelligent suitability scoring remains a
  future stage.
- Alembic migrations, production database support, payments, and PWA
  installation are future stages.

## Generated And Local-Only Files

| Path | Why it is excluded from the maintained-file list |
| --- | --- |
| `backend/.env` | Local secrets. Never commit or document its real values. |
| `backend/travel_assistant.db` and other `.db` files | Generated local SQLite data. |
| `backend/.venv` and `venv` | Generated Python environments. |
| `frontend/node_modules` | Installed npm packages. |
| `frontend/dist` | Generated production build output. |
| `__pycache__`, `.vite`, Python bytecode, TypeScript build files | Generated runtime and compiler caches. |
