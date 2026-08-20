# Backend

FastAPI backend for the AI-Powered Travel Booking Assistant application.

## Backend structure

`app/main.py` is the small FastAPI entrypoint used by Uvicorn and the existing
test imports. The current route and chat orchestration implementation lives in
`app/services/chat_service.py`. The `routes`, `services`, `state`, and `utils`
packages provide a cleaner structure for future incremental extraction without
changing the current API behaviour.

## Run locally

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0
```

The API will run at `http://127.0.0.1:8000`.

For Android browser testing on the same network, use your laptop IP address,
for example `http://192.168.1.20:8000`.

## Duffel API connection test

Create `backend/.env` with your Duffel test token:

```text
DUFFEL_API_KEY=duffel_test_your_token_here
DUFFEL_BASE_URL=https://api.duffel.com
FLIGHT_PROVIDER=duffel
```

Run the standalone connection test:

```powershell
cd backend
.\.venv\Scripts\activate
pip install -r requirements.txt
python test_duffel.py
```

The script calls Duffel's test API airport endpoint and prints only the status
code plus a small response summary. It does not print the full API token.

## Local Llama extraction test

Stage 5A introduced this standalone Ollama connection test. It remains useful
for checking local model setup independently of the chat route.

Install Ollama manually from `https://ollama.com/download`, then start Ollama.
Pull the local model:

```powershell
ollama pull llama3.1:8b-instruct-q4_0
ollama run llama3.1:8b-instruct-q4_0
```

Optional `.env` values:

```text
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.1:8b-instruct-q4_0
USE_LOCAL_LLM=false
```

Voyage AI is configured to use the 8B 4-bit quantized Llama model only. If
`OLLAMA_MODEL` is set to a different tag, the backend Llama service raises a
clear configuration error before sending requests to Ollama.

Run the standalone extraction test:

```powershell
cd backend
.\.venv\Scripts\activate
pip install -r requirements.txt
python test_llama_extraction.py
```

The script sends one travel request to Ollama, asks for JSON output, validates
the response with the backend `FlightSearchRequest` schema, and prints the final
parsed JSON. It does not call Duffel or change `/chat`.

## Llama-powered chat flight searches

For flight-search messages only, `/chat` now uses local Llama through Ollama to
extract a validated `FlightSearchRequest`, then uses the same Duffel-first helper
as `POST /flights/search`. Messages and assistant replies are still saved to
SQLite as before.

The chat pipeline asks Llama to convert natural-language messages into
structured backend tools. The backend then validates the selected action and
state before calling providers, updating carts, moving to checkout, or writing
to SQLite. If Ollama cannot be reached or returns invalid JSON, `/chat` can use
a narrow deterministic fallback so the demo remains usable, but Llama is the
main natural-language understanding layer.

For example, send this to `/chat`:

```json
{
  "message": "I want to fly from London Heathrow to New York on 12 September 2026 and return on 19 September 2026. Economy, no stops."
}
```

The chat reply states whether Duffel returned real results or mock fallback
results. Flight cards include a `provider` field of `"duffel"` or `"mock"`.

## Stage 5E Tool-Based Llama Router

`/chat` now asks local Llama to select one validated backend tool rather than
relying on a long list of phrase-specific actions. Llama receives only safe,
compact conversation state: recent messages, the latest route and flight count,
the currently displayed result, and any pending selection. It cannot call
Duffel, access `.env`, create a booking, or write to SQLite.

The backend validates the selected tool and executes it deterministically. The
current tools include `search_flights`, `rank_flights`, `show_flight_option`,
`select_flight`, `check_flight_availability`, `search_hotels`,
`discover_destinations`, `prepare_trip_request`, `search_alternative_origins`,
`add_to_cart`, `checkout`, `cart_view`, `confirm_booking`, `cancel_booking`,
`booking_lookup`, and `general_help`.

For example, after a flight search you can ask `which is the cheapest?`, `is it
still available?`, or `ok I like it, can I book it please?`. Selecting a flight
only creates a pending selection; a separate clear confirmation is required
before SQLite creates a booking.

An availability check repeats the stored search and tries to match the current
or pending offer. Duffel test offers can change or expire, so a positive answer
means the offer appears in the refreshed Duffel **test-mode** search, not a
guarantee of real-world availability.

Package and checkout messages now use structured Llama tools such as
`add_to_cart` and `checkout`. The backend does not need to enumerate phrases
like “I like package 1” or checkout typos as the primary route; it validates the
structured tool against the current package/cart state and returns a navigation
target such as `redirect_to: "/cart"` or `redirect_to: "/checkout"`.

Broad trip requests also use structured Llama tools. For example, a request
from Birmingham to Florida keeps Birmingham as `BHX`, stores Florida as a
region with candidate airports such as Orlando, Miami, Tampa, and Fort
Lauderdale, and asks for missing airport/date details before searching or
building a package. If there are no viable results from the selected origin,
the backend can suggest alternative UK origins without silently replacing the
user's route.

## Stage 5F Flight Result Quality

Normalised flight results now include safe display data alongside the original
offer ID: a short offer reference, option number, split outbound/return dates
and times, arrival times where available, duration, stops label, cabin label,
and passenger count. The full Duffel offer ID remains internal and is not used
as the card title.

Llama can now select two additional backend tools:

- `update_passenger_count` refreshes the stored search with a new count. A
  request such as `can I book it for 3 passengers?` refreshes first, selects a
  refreshed option, and still requires a separate confirmation.
- `describe_current_flight` answers questions about the displayed flight, such
  as its cabin class, price, times, or passenger count, without changing the
  selected option.

Ranking supports `cheapest_fastest` and `best_value` as well as cheapest,
fastest, best, earliest, and latest. When journey duration is incomplete, the
assistant says that price and stops were also used.

The frontend flight cards show option number, airline, route, clean dates and
times, cabin, passenger count, stops, duration, price, provider, and a short
offer reference. Duffel offers are sandbox: an availability refresh can only
say an offer appears available in the latest test search, never guarantee a
real booking.

Run the focused backend tests:

```powershell
cd backend
.\.venv\Scripts\activate
python -m unittest discover -s tests -v
```

## Stage 5G Persistent Travel Memory

Flight follow-up context is now stored in SQLite rather than being limited to
the running Python process. The `session_states` table stores only validated
search requests, normalised flight cards, the current displayed option, pending
selection, pending action, last tool, and compact recent facts. This allows
flight ranking, option selection, confirmation, cancellation, passenger
updates, and availability refreshes to continue after an in-memory reset.

The `user_preferences` table provides a safe foundation for future
personalisation. It currently supports cabin, stops, time, budget, hotel,
accessibility, and sustainability preference fields, but does not change flight
recommendations yet.

No raw Duffel response payloads, API keys, tokens, or `.env` values are stored
in either table or passed to Llama. The old in-memory flight dictionary remains
only as a compatibility fallback during this application transition.

## Stage 5H Authentication And User Profile Foundation

The backend now supports local application accounts with bcrypt password hashes
and signed JWT bearer tokens. The protected account endpoints are:

- `POST /auth/register` creates an account, default preference row, and first
  chat session, then returns a bearer token.
- `POST /auth/login` returns a bearer token for valid local credentials.
- `GET /auth/me` and `PATCH /auth/me` read and update the signed-in profile.
- `GET /auth/preferences` and `PATCH /auth/preferences` read and update saved
  travel preferences.
- `GET /bookings` and `GET /bookings/{reference}` return only the signed-in
  user's own booking history.

`POST /chat` and `POST /booking/create` accept an optional
`Authorization: Bearer <token>` header. With a valid token, messages, session
state, preferences, and bookings belong to that user. Without one, they retain
the original demo-user behaviour so existing demonstrations and API use still
work.

Optional authentication is tolerant for guest-allowed routes. If `/chat`
receives an expired or invalid bearer token, the backend continues in guest
mode instead of failing the conversation. Protected routes such as
`/checkout/confirm`, `/auth/me`, preferences, and booking history still require
a valid token.

The React app provides simple register, login, logout, and preference editing
controls. Preferences are stored safely for a later recommendation stage; they
do not yet change Duffel ranking.

For local development, add these values to `backend/.env` (use a unique secret
outside this application):

```text
JWT_SECRET_KEY=replace-with-a-long-local-secret
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
```

This is deliberately local application authentication, not production identity
management. It has no password reset, email verification, refresh-token
rotation, HTTPS enforcement, rate limiting, or account recovery flow.

## Stage 6A Hotel Search Integration

Hotel search is now available through `POST /hotels/search` and the chat tool
router. The backend tries LiteAPI's hotel-rate endpoint first when
`USE_LITEAPI_HOTELS=true` and a local `LITEAPI_KEY` exists. Missing credentials,
disabled feature flags, timeouts, HTTP errors, malformed data, and empty rate
results automatically return deterministic `mock_hotel_fallback` cards instead.

Chat supports hotel search, cheapest-hotel ranking, showing an option, selecting
an option, confirmation, and cancellation. The selected hotel is saved through
the existing SQLite bookings table only after confirmation. Each session stores
only validated hotel searches and normalised hotel cards, never raw LiteAPI
payloads or provider credentials. Existing local SQLite databases receive the
new nullable `session_states` hotel columns automatically at backend startup;
deleting `travel_assistant.db` is not required for this application update.

Add these local values to `backend/.env`:

```text
LITEAPI_KEY=your_local_liteapi_key
LITEAPI_BASE_URL=https://api.liteapi.travel/v3.0
USE_LITEAPI_HOTELS=true
```

The frontend renders hotel option cards with rates, dates, nights, guests,
rooms, ratings, board type, refund status, provider, and a short reference.
Flight-plus-hotel package building is intentionally a later stage.

## Stage 6E Date Understanding And General Help Fix

Travel searches are now current-date aware. When a chat message includes a
month and day without a year, such as `5th of October`, The Voyager resolves it
to the next future occurrence before searching. The backend applies the same
rule after Llama tool selection, so an old generated year cannot reach Duffel,
LiteAPI, or a mock fallback.

Direct structured endpoints remain strict: `POST /flights/search` rejects a
past departure or a return date that is not after departure, and
`POST /hotels/search` rejects a past check-in or invalid checkout date with an
HTTP 400 validation message. Mock cards use the already validated request dates
and flight fallbacks now use route-based references such as `Offer CAI-LHR-001`.

General help now introduces The Voyager and mentions flights, hotels,
availability, booking preparation, Voyage AI cart, and checkout.

## Stage 6F Follow-Up Context And Route Memory Fix

The Voyager now treats partial follow-ups as changes to the latest active
session context. After a CAI to LHR search, messages such as `what about
tomorrow?` and `what about next week?` reuse CAI to LHR and change only the
date. Passenger updates retain the same stored route and search request.

The backend stores and supplies a compact active flight or hotel context to
Llama, including route, dates, passenger/guest counts, cabin or stay details,
result count, and current option. Before any provider call, deterministic
backend logic merges partial requests with the latest persistent request. It
never uses an example route as a default. With no active route, it asks the user
to provide their departure and destination instead of searching an invented
route.

## Real flight search

`POST /flights/search` searches Duffel sandbox first when `DUFFEL_API_KEY`
is available. If Duffel credentials are missing, Duffel fails, or Duffel returns
no usable offers, the endpoint returns normalised mock fallback flights.

This endpoint is for Duffel sandbox. Use a token that starts with
`duffel_test_`; do not use a live token for this application stage.

Start the backend:

```powershell
cd backend
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0
```

Example request body:

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

PowerShell test command:

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

Check `provider_used` in the response:

- `"duffel"` means Duffel test API results were used.
- `"mock_fallback"` means the endpoint safely fell back to mock results. The
  `fallback_reason` field explains why.

## Endpoints

- `GET /health` returns a basic health response.
- `POST /flights/search` searches Duffel sandbox with mock fallback results.
- `POST /hotels/search` searches LiteAPI hotel rates with mock fallback results.
- `POST /location/current` converts browser coordinates into a safe nearest-airport default origin.
- `POST /chat` safely routes conversation requests to backend tools and returns travel results.
- `POST /booking/create` creates a local SQLite booking with a generated reference.
- `POST /checkout/confirm` turns manual Voyage AI cart selections into one local
  application booking reference. It never calls payment, Duffel order, or LiteAPI
  reservation endpoints. This endpoint requires authentication.
- `POST /auth/register` creates a local account and returns a bearer token.
- `POST /auth/login` authenticates a local account and returns a bearer token.
- `GET`/`PATCH /auth/me` reads or updates the authenticated profile.
- `GET`/`PATCH /auth/preferences` reads or updates authenticated preferences.
- `GET /bookings` and `GET /bookings/{reference}` read authenticated booking
  history.
  Newer checkout bookings include saved package summaries, generated flight
  tickets, and generated hotel vouchers; older summary-only bookings still load
  safely without document data.

## Stage 6B/6C Voyage AI Website, Cart, And Checkout

The React product is now branded **Voyage AI** and contains Home, Flights,
Hotels, Cart, Checkout, The Voyager, and Profile pages. The Voyager remains the
existing chat experience: it keeps the Llama tool router, Duffel/LiteAPI
searches, session memory, selection, confirmation, and cancellation flows.

The Flights and Hotels pages support direct manual search. A user can add a
normalised result card to a browser-local cart, review it at `/cart`, then call
`POST /checkout/confirm` from `/checkout`. Checkout now requires sign-in. If a
guest reaches checkout, the frontend sends the user to Profile/Login and keeps
the local cart so checkout can continue after authentication. The endpoint
creates one local `TA...` reference linked to the authenticated user.

Checkout stores only compact public item details: type, title, subtitle,
provider name, price, and currency. It intentionally discards arbitrary client
`item_data`, raw provider payloads, tokens, and secrets. Mixed currencies are
grouped and reported separately; no exchange-rate conversion is performed.
After a successful checkout, the response includes saved booking details when
available, plus generated flight ticket and hotel voucher objects. The active
cart is marked checked out and cleared for the next trip.

Example request body:

```json
{
  "items": [
    {
      "type": "flight",
      "title": "LHR to JFK - British Airways",
      "subtitle": "12 Sep 2026 to 19 Sep 2026",
      "provider": "duffel",
      "price": 408.33,
      "currency": "GBP",
      "item_data": {}
    }
  ],
  "customer": {
    "full_name": "Demo Traveller",
    "email": "traveller@example.com"
  }
}
```

This is a local application checkout only. It does not process payment or create
a live airline or hotel reservation.

## Dynamic Homepage API

The homepage is driven by backend data rather than hard-coded frontend
destination or package arrays.

Guest-safe homepage endpoints:

- `GET /homepage/random-flights`
- `GET /homepage/random-packages`
- `GET /homepage/property-types`
- `GET /homepage/trending-destinations`
- `GET /homepage/trip-ideas`

The random flight and package sections use the existing Duffel-first and
LiteAPI-first search helpers with mock fallback. If a provider is unavailable,
the endpoint returns fallback/cache-backed cards or a safe empty section rather
than exposing backend errors or secrets.

Each homepage card includes a frontend navigation URL for `/flights`, `/hotels`,
or `/voyager`, with query parameters or prompts already prepared.

## Stage 6G Location And Destination Discovery

The Voyager can now use browser geolocation as a safe default origin and can
recommend destinations from preferences such as beach, family, skiing, culture,
or short-flight requirements.

The frontend asks for browser permission with `navigator.geolocation`. It sends
coordinates only to `POST /location/current`; the backend converts them to the
nearest known airport, such as Cairo International Airport (`CAI`), and stores
only that safe airport context in session memory. Exact GPS coordinates are not
sent to Llama or stored as travel memory.

OpenTripMap support is backend-only:

```text
OPENTRIPMAP_API_KEY=your_local_opentripmap_key
OPENTRIPMAP_BASE_URL=https://api.opentripmap.com/0.1/en
```

Destination discovery uses curated candidate seeds plus OpenTripMap place
evidence when available. If the API key is missing, OpenTripMap fails, or
evidence is weak, deterministic fallback tags keep the demo stable.

Explicit activities are treated as requirements. For example, if a user asks
for skiing, Voyage AI only recommends destinations with strong skiing evidence
or a trusted skiing fallback tag. A follow-up such as `keep it in Europe and I
don't like long flights` keeps skiing as the main requirement, then applies
Europe and shorter-flight preferences as refinements.

Follow-up refinements can also remove constraints. For example, `it doesn't
have to be in Europe` removes the Europe preference, `I don't care about long
flights` removes short-flight limits, and `I don't want skiing` removes skiing
from the required activities before re-ranking destinations.

Negative region follow-ups are handled separately. `I don't want it in Europe`,
`not in Europe`, or `outside Europe` add Europe to `excludedRegions`, so Europe
is removed from the recommendations while the main activity, such as skiing,
stays active.

If the user gives a preference trip without a date, such as `somewhere I can do
skiing for 3 nights`, the backend stores a pending destination-discovery request
instead of inventing a destination. A later date message like `same day next
week` completes that request, keeps the saved origin, and infers the return
date from the 3-night duration.

The live `/chat` route now checks pending clarification before Llama tool
routing. Flight search is blocked unless origin, destination, and departure date
are present, so date-only follow-ups and incomplete requests cannot silently
turn into sample CAI/LHR or London/LHR searches. SQLite session state is used as
the primary memory source for database-backed requests; older in-process memory
exists only as a compatibility fallback for non-database helper paths.

Example chat messages:

```text
I want somewhere near the beach
I have 2 children and want a skiing holiday but the flight should be less than 2 hours
I want to go to London
```

For destination-only flight requests, the backend uses this order: origin
explicitly stated by the user, saved/default origin from memory,
browser-location-derived nearest airport, then a clarification asking where the
user is flying from.

## Package builder and generated checkout

The Voyager can now build a local flight-plus-hotel package from the active
trip state. After the user has chosen a destination and dates, messages such as
`find me a full package`, `flights and hotels`, or `give me the total price`
search flights and hotels using the active origin, selected destination, and
selected date range.

The package builder is deterministic: Llama may identify intent, but the
backend constructs the flight route and hotel dates from `active_trip_json`.
Stale routes such as an old LHR/JFK search cannot override an active trip like
EMA to LYS.

Selecting a package adds it to a session cart. Checkout creates a local SQLite
booking reference and returns travel documents:

- flight ticket
- hotel voucher

Provider credentials remain server-side, and checkout records are stored in
Voyage AI.

## Database

The backend uses SQLite for the application. By default it creates:

```text
backend/travel_assistant.db
```

Tables are created automatically when FastAPI starts. Authenticated users have
their own chat session, messages, bookings, session state, and preferences.
Requests without a bearer token continue to use the demo user/session.
Session state also stores the safe default origin airport, not raw browser
coordinates.

To override the database location:

```bash
DATABASE_URL=sqlite:///./travel_assistant.db
```

## Future work

- Add Alembic migrations when the database schema starts changing often.
- Replace `app/mock_provider.py` with real travel providers such as Expedia.
- Add intelligent package scoring and explainable recommendations.
- Add voice input later through browser audio capture and backend transcription.
- Replace the local application account flow with production identity management
  before any public deployment.
