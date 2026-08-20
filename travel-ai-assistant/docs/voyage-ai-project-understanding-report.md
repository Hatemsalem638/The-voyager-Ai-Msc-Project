# Voyage AI Project Understanding Report

## 1. Project Overview

**Voyage AI** is a local travel planning web application. It has two complementary ways to plan a trip:

- A normal website flow for manually searching flights and hotels, adding choices to a cart, and creating a local booking record at checkout.
- **The Voyager**, a conversational assistant that interprets travel requests, safely chooses a backend action, and guides the user through search, comparison, selection, availability checks, and booking confirmation.

The system is deliberately split into two layers:

1. The local Llama model interprets a message and proposes a validated tool call.
2. The FastAPI backend validates and executes that action. It alone communicates with travel providers and writes to SQLite.

This separation prevents the LLM from accessing credentials, directly calling providers, or directly creating database records.

## 2. Main Project Structure

```text
travel-ai-assistant/
|-- backend/
|   |-- app/
|   |   |-- auth.py
|   |   |-- database.py
|   |   |-- date_utils.py
|   |   |-- llama_service.py
|   |   |-- main.py
|   |   |-- memory_store.py
|   |   |-- mock_provider.py
|   |   |-- models.py
|   |   |-- schemas.py
|   |   `-- providers/
|   |       |-- duffel_provider.py
|   |       |-- liteapi_hotel_provider.py
|   |       `-- mock_hotel_provider.py
|   |   |-- routes/
|   |   |-- services/
|   |   |   `-- chat_service.py
|   |   |-- state/
|   |   `-- utils/
|   |-- tests/
|   |-- README.md
|   |-- requirements.txt
|   |-- test_duffel.py
|   |-- test_liteapi_hotels.py
|   `-- test_llama_extraction.py
|-- frontend/
|   |-- src/
|   |   |-- api/
|   |   |-- assets/
|   |   |-- components/
|   |   |-- context/
|   |   |-- layout/
|   |   |-- pages/
|   |   |-- App.tsx
|   |   |-- index.css
|   |   `-- main.tsx
|   |-- package.json
|   `-- vite.config.ts
|-- docs/
|   |-- architecture-notes.md
|   |-- implementation-log.md
|   |-- project-file-and-architecture-guide.md
|   `-- voyage-ai-project-understanding-report.md
|-- README.md
`-- run_backend_frontend_commands.txt
```

### Backend files

| File or folder | Responsibility |
|---|---|
| `backend/app/main.py` | Small FastAPI entrypoint facade that preserves `uvicorn app.main:app` and existing import paths. |
| `backend/app/services/chat_service.py` | Current consolidated FastAPI application, HTTP endpoints, deterministic chat tool executor, provider fallback decisions, booking creation, package/cart flow, and checkout. |
| `backend/app/schemas.py` | Pydantic request and response contracts for chat, flights, hotels, checkout, authentication, preferences, and LLM tool calls. |
| `backend/app/models.py` | SQLAlchemy models for SQLite tables. |
| `backend/app/database.py` | SQLite engine, sessions, table creation, and lightweight compatibility columns for an existing local database. |
| `backend/app/auth.py` | Password hashing, signed bearer-token creation and validation, optional authentication, and required authentication dependencies. |
| `backend/app/llama_service.py` | Ollama/Llama prompting, JSON extraction, schema validation, and safe tool routing. |
| `backend/app/memory_store.py` | Persistent session-state and user-preference storage helpers. It stores compact normalised data rather than provider payloads. |
| `backend/app/date_utils.py` | Relative-date resolution, yearless-date handling, and past/invalid-date protection. |
| `backend/app/providers/` | Adapters for Duffel flights, LiteAPI hotels, and deterministic mock hotel results. |
| `backend/app/mock_provider.py` | Deterministic mock flight and legacy travel data used as a safe fallback. |

### Frontend files

| File or folder | Responsibility |
|---|---|
| `frontend/src/App.tsx` | Defines application routes and holds the signed-in browser session state. |
| `frontend/src/main.tsx` | Starts React with `BrowserRouter` and `CartProvider`. |
| `frontend/src/layout/AppLayout.tsx` | Renders the common navigation bar and the route outlet. |
| `frontend/src/pages/` | Page-level views: Home, Flights, Hotels, Cart, Checkout, Profile, and The Voyager. |
| `frontend/src/components/` | Reusable chat, card, navigation, login, registration, and profile components. |
| `frontend/src/api/` | Typed HTTP helpers for authentication, chat, flights, hotels, and checkout. |
| `frontend/src/context/CartContext.tsx` | Browser cart state, totals, and local-storage persistence. |
| `frontend/src/index.css` | Shared responsive bright turquoise travel theme, page layout, forms, cards, and chat styles. |

### Tests and documentation

| Area | Contents |
|---|---|
| `backend/tests/test_auth.py` | Registration, login, token, profile, preferences, authenticated and demo behaviour. |
| `backend/tests/test_chat_flight_routing.py` | Flight routing, tool validation, booking confirmation, persistent memory, and safe Llama context. |
| `backend/tests/test_hotel_search.py` | Hotel fallback, hotel memory, selection, confirmation, safe LiteAPI error handling, and no-key exposure. |
| `backend/tests/test_checkout.py` | Demo/authenticated checkout, totals, empty-cart rejection, and safe checkout data handling. |
| `backend/tests/test_date_handling.py` | Relative, yearless, past, return, and hotel-date validation. |
| `backend/tests/test_followup_context.py` | Route retention and correction for partial follow-up questions. |
| `docs/` | Architecture, implementation history, file guide, and this report. |

## 3. Backend Architecture

### Application startup and SQLite

`backend/app/main.py` is now a small entrypoint facade. The current application
implementation lives in `backend/app/services/chat_service.py`, which creates
the FastAPI application and registers a lifespan handler. On startup it calls
`create_db_and_tables()` from `database.py`. This creates the SQLAlchemy tables
and applies the small compatibility additions used by the current local SQLite
database.

The default local database is `backend/travel_assistant.db`. It stores application data locally; no provider responses are stored wholesale.

The API currently uses open development CORS (`allow_origins=["*"]`, credentials disabled), so a local Vite frontend can call it from browser and mobile development environments. This is appropriate for the current local setup, but must be restricted to approved origins before deployment.

### API endpoints

| Endpoint | Purpose | Authentication |
|---|---|---|
| `GET /health` | Simple health response. | Not required |
| `POST /auth/register` | Creates a local account, preferences, first chat session, and bearer token. | Not required |
| `POST /auth/login` | Verifies local credentials and returns a bearer token. | Not required |
| `GET /auth/me` | Reads the signed-in profile and preferences. | Required |
| `PATCH /auth/me` | Updates the signed-in display name/profile. | Required |
| `GET /auth/preferences` | Reads the signed-in preference record. | Required |
| `PATCH /auth/preferences` | Updates validated preference fields. | Required |
| `POST /flights/search` | Structured flight search with Duffel-first and mock fallback behaviour. | Not required |
| `POST /hotels/search` | Structured hotel search with LiteAPI-first and mock fallback behaviour. | Not required |
| `POST /chat` | The Voyager's conversational endpoint. | Optional |
| `POST /booking/create` | Existing direct local booking creation endpoint. | Optional |
| `POST /checkout/confirm` | Converts manual cart items into one local booking reference. | Optional |

### Schemas

Pydantic models in `schemas.py` reject malformed requests before execution. Important contracts include:

- `FlightSearchRequest`: trip type, origin and destination IATA codes, dates, passengers, cabin class, and maximum stops.
- `NormalisedFlightResult`: a provider-neutral flight card with option number, short offer identifier, airline, route, split date/time fields, duration, stops, cabin, passengers, price, currency, and provider.
- `HotelSearchRequest`: destination, dates, guest and room counts, rating/price/board/amenity preferences.
- `NormalisedHotelResult`: a provider-neutral hotel card with stay dates, nights, guest/room counts, rating, room and board details, refund state, price, currency, and provider.
- `ChatRequest` and `ChatResponse`: message input plus reply, detected intent, cards, booking result, suggestions, and confirmation status.
- `TravelToolCall`: strict Llama output containing a permitted tool, arguments, optional confidence, and explanation.
- `CheckoutRequest` and `CheckoutResponse`: a bounded list of public cart item summaries and a local booking result.
- Authentication/profile/preference schemas: bounded and validated local account data.

### SQLite data model

| Table/model | Stored data |
|---|---|
| `users` / `User` | Local account identity, display name, email, password hash, and timestamps. |
| `sessions` / `ChatSession` | A user's chat session and links to messages, bookings, and persistent state. |
| `messages` / `Message` | User and assistant message text, intent type/data, and timestamp. |
| `bookings` / `Booking` | Local reference, owner/session, destination, status, summary, optional flight/hotel identifiers, estimate, currency, type, safe details, and timestamp. |
| `session_states` / `SessionState` | Latest normalised flight/hotel search, result lists, current option, pending selection, pending action, last tool, and short recent facts. |
| `user_preferences` / `UserPreference` | Saved cabin, stops, budget, hotel, accessibility, departure-time, and sustainability preferences. |

Session-state JSON contains only validated request objects and normalised option cards. Raw Duffel/LiteAPI payloads, provider credentials, tokens, and environment values are not placed in this table.

## 4. Authentication and User Behaviour

### Register and login

1. The Profile page submits registration or login data through `frontend/src/api/authApi.ts`.
2. The backend validates the input. New account passwords are hashed before storage.
3. Registration creates a preference record and a first chat session. Login verifies the password hash.
4. A signed bearer token and minimal user summary are returned.
5. The frontend keeps that browser session in local storage and includes the bearer token in authenticated API calls.

### Profile and preferences

The Profile page composes the existing login, registration, and profile components. Signed-in users can read/update their name and saved travel preferences. Preference fields are persisted now, but they are not yet a full automatic recommendation or scoring engine.

### Required, optional, and demo authentication

- `/auth/me` and `/auth/preferences` require a valid bearer token.
- `/chat` and `/booking/create` use optional authentication.
- `/checkout/confirm`, `/bookings`, and `/bookings/{reference}` require a valid bearer token.
- When a valid token is supplied, the chat session, session memory, preferences, and booking belong to that user.
- When no token is supplied, the backend uses the existing demo user/session path. This preserves anonymous local demonstrations.

On guest-allowed `/chat`, an invalid or expired bearer token is treated as guest mode so The Voyager can keep responding. Protected checkout, profile, preferences, and booking history still return `401` for invalid authentication.

## 5. The Voyager Chatbot

### End-to-end message flow

1. `ChatBox.tsx` displays the welcome message and sends the user's text via `sendChatMessage()` in `chatApi.ts`.
2. `/chat` resolves an authenticated user/session when possible, otherwise the demo context.
3. The user message is saved in SQLite.
4. The backend loads compact, safe persistent context with `load_safe_llama_context()`.
5. `route_tool_with_llama()` sends the message plus that compact context to local Ollama/Llama and asks for JSON only.
6. The JSON is validated as `TravelToolCall`. Invalid model output is logged safely and then the deterministic rule-based fallback is used.
7. `services/chat_service.py` executes the selected tool. Llama never executes a provider call, edits SQLite, or receives credentials.
8. The result and assistant reply are saved as a message, then returned to `ChatBox` for card rendering.

### Supported tools/actions

| Tool | Backend behaviour |
|---|---|
| `search_flights` | Validates a flight request and searches Duffel or mock fallback. |
| `rank_flights` | Chooses cheapest, fastest, best/best-value, earliest, latest, or combined price/duration options from stored results. |
| `show_flight_option` | Displays a numbered flight option. |
| `select_flight` | Saves a pending flight selection and asks for confirmation. |
| `check_flight_availability` | Re-runs the last flight search and compares the selected/current offer with fresh normalised results. |
| `update_passenger_count` | Changes passengers, refreshes the flight search, and prevents booking an outdated one-passenger offer. |
| `describe_current_flight` | Answers focused questions about the displayed cabin, price, time, passengers, airline, or summary. |
| `search_hotels` | Validates a hotel request and searches LiteAPI or mock fallback. |
| `rank_hotels` | Ranks stored hotel results, including cheapest. |
| `show_hotel_option` | Displays a numbered hotel option. |
| `select_hotel` | Saves a pending hotel selection and asks for confirmation. |
| `confirm_hotel_booking` / `confirm_booking` | Creates a local SQLite booking only when a pending flight, hotel, or supported legacy selection exists. |
| `cancel_booking` | Clears only a real pending selection; phrases such as "no stops" are not treated as a cancellation. |
| `booking_lookup` | Looks up a stored local reference. |
| `general_help` | Returns a helpful explanation of supported actions. |

### Persistent conversation memory

`memory_store.py` makes SQLite session state the primary source of truth. It saves:

- Latest flight and hotel requests.
- Normalised result lists.
- Current displayed flight/hotel option.
- Pending selection and confirmation action.
- Most recent tool and a small list of safe conversation facts.

This enables follow-ups after process-memory dictionaries are cleared, for example: cheapest result, show option 2, select the displayed option, confirm, cancel, passenger change, or availability refresh.

### Follow-up context and stale-route prevention

The active stored route and travel details are supplied to Llama as compact context. Before a provider request, deterministic backend logic merges partial questions into the latest valid request. Therefore, after a Cairo-to-London search, a question such as "what about tomorrow?" keeps Cairo-to-London and changes the date instead of leaking an example London-to-New York route from a prompt. Without a prior route, the assistant asks for origin and destination rather than inventing one.

The system also distinguishes the latest hotel context from the latest flight context, so a hotel follow-up does not accidentally use flight memory.

### Date handling

`date_utils.py` uses the current UTC date to:

- Resolve `tomorrow`, `day after tomorrow`, and `next week`.
- Resolve a yearless date such as `5th of October` to the next future occurrence.
- Reject past departure/check-in dates.
- Require a flight return date after departure.
- Require hotel checkout after check-in.

The structured `/flights/search` and `/hotels/search` endpoints return a validation error for invalid date sequences. Chat applies the same normalisation before a provider search, so old LLM-generated dates do not reach a provider or fallback card.

## 6. Flight Search

### Direct structured search

The Flights page sends a validated `FlightSearchRequest` to `POST /flights/search`. The backend rejects past or invalid dates, then calls `_search_flights_with_fallback()`.

1. If server-side Duffel credentials are available, `duffel_provider.py` creates a provider request with adult passengers, slices, date(s), cabin class, and request headers.
2. The adapter handles timeouts, HTTP failures, malformed responses, and empty offers without exposing credentials.
3. Successful offers are normalised into `NormalisedFlightResult` cards.
4. If there are no usable provider results, `mock_provider.py` returns deterministic normalised mock cards with a `mock_fallback` provider result and a reason.

### Chat flight search and selection

The Voyager follows the same validated search and fallback path, then persists the request/results. Flight cards display:

- Option number and shortened offer identifier.
- Airline and route.
- Outbound and return dates/times.
- Cabin class, passenger count, stops, duration, price/currency, and provider.

Selecting a flight only creates a pending selection. A clear confirmation is required before `_create_booking_from_flight()` writes a local booking reference. Availability checks refresh the same stored search; they can say that an offer still appears in the latest response, but do not promise real-world reservation availability.

### Real versus generated flight behaviour

- Search results may come from the configured Duffel provider when it is available.
- The mock fallback is deliberately local and deterministic for demonstrations and safe failure handling.
- Selection, confirmation, booking references, and checkout records are local SQLite actions.
- The application does not create a real provider order or a live airline reservation.

## 7. Hotel Search

### Direct structured search

The Hotels page sends `HotelSearchRequest` data to `POST /hotels/search`. A request needs a city or coordinates, valid future/valid stay dates, guests, rooms, and optional preferences.

1. `_search_hotels_with_fallback()` checks whether the server-side LiteAPI integration is enabled and usable.
2. `liteapi_hotel_provider.py` calls the rates endpoint with a timeout and normalises only useful rate/hotel fields.
3. Missing configuration, disabled integration, error responses, timeouts, invalid JSON, unexpected shapes, or zero usable rates cause the fallback path.
4. `mock_hotel_provider.py` returns deterministic hotel cards for supported common cities, respecting relevant preferences such as rating and price where possible.

### Hotel cards and selection

Hotel cards show:

- Option number, hotel name, short identifier, and location.
- Check-in/check-out, nights, guests, and rooms.
- Rating, board type, room name, refund state, price/currency, and provider.

The Voyager stores hotel context in the same persistent `SessionState` row. Users can search, ask for the cheapest hotel, show an option, select it, confirm it, or cancel it. Confirmation creates a local hotel booking record only after a pending selection exists.

### Real versus generated hotel behaviour

- The LiteAPI adapter can provide configured hotel-rate data.
- Mock hotel cards preserve a consistent experience whenever the provider cannot be used.
- No raw LiteAPI payload is stored in SQLite.
- The application does not create a real hotel reservation.

## 8. Cart and Checkout

### Browser cart

`CartContext.tsx` stores normalised flight and hotel selections in browser local storage under a Voyage AI cart key. Each item has a generated local cart item ID, type, title, subtitle, provider name, price, currency, normalised item data, and added time.

`FlightCard` and `HotelCard` accept an optional `onAddToCart` callback. Manual Flights and Hotels pages provide this callback; The Voyager's chat cards do not, which keeps chat booking flow separate from manual cart flow.

The cart supports add, remove, clear, item count in the navbar, and totals grouped by currency. It does not convert currencies. When currencies differ, the Cart and Checkout pages list separate totals rather than presenting a misleading combined amount.

### Checkout flow

1. The Checkout page collects contact details and optional notes.
2. `checkoutApi.ts` sends the cart to `POST /checkout/confirm`, attaching a valid bearer token when one exists.
3. `CheckoutRequest` limits the item list and validates public fields such as type, title, price, and currency.
4. The backend calculates totals by currency, discards arbitrary client provider data, chooses the authenticated user or demo user, and creates one local `TA...` booking reference.
5. The response contains the booking reference, safe summary, item count, and totals. The UI displays confirmation and lets the user clear the cart.

Checkout stores compact safe item details and summary data in SQLite. It does not process a payment, use a payment gateway, create a Duffel order, or reserve a LiteAPI hotel.

## 9. Frontend Architecture

### Routes

| Route | Page | Purpose |
|---|---|---|
| `/` | `HomePage` | Travel-oriented welcome page with hero image and links to core tasks. |
| `/flights` | `FlightsPage` | Manual flight search and add-to-cart flow. |
| `/hotels` | `HotelsPage` | Manual hotel search and add-to-cart flow. |
| `/cart` | `CartPage` | Persistent browser-local trip cart and grouped currency totals. |
| `/checkout` | `CheckoutPage` | Contact form and local booking confirmation. |
| `/voyager` | `VoyagerPage` | The Voyager chat assistant. |
| `/profile` | `ProfilePage` | Login, registration, profile, and preferences. |
| any other path | Redirect | Returns to `/`. |

### Layout and appearance

`AppLayout` renders the `Navbar` and the active route. The navigation includes Home, Flights, Hotels, The Voyager, Cart with live item count, Profile/Login, and Logout when authenticated.

The current layout does **not** render a footer. `Footer.tsx` and related CSS remain in the repository but are unused by `AppLayout`.

The shared stylesheet gives the site a bright turquoise travel aesthetic: a tropical hero image, turquoise navigation accent, white cards, coral calls to action, compact badges, responsive grids, and mobile-specific layout rules.

### Frontend-to-backend communication

All API helper modules use the same configurable backend base URL, defaulting to the local FastAPI server. They send JSON, handle non-success responses, and add the locally stored bearer header when available.

- `authApi.ts`: authentication, profile, and preferences.
- `chatApi.ts`: The Voyager chat messages and typed response objects.
- `flightApi.ts`: manual flight search.
- `hotelApi.ts`: manual hotel search.
- `checkoutApi.ts`: manual cart checkout.

## 10. Current Completed Features

| Feature | Main file/area | How it works | Status |
|---|---|---|---|
| Flight search | `services/chat_service.py`, Duffel provider, Flights page | Validated direct and chat searches; provider-first with normalised mock fallback. | Complete |
| Hotel search | `services/chat_service.py`, LiteAPI provider, Hotels page | Validated direct and chat hotel searches; provider-first with normalised mock fallback. | Complete |
| The Voyager | `ChatBox.tsx`, `/chat`, `llama_service.py` | Local Llama selects a validated backend tool; backend executes safely. | Complete |
| Flight comparison | Chat tool executor | Cheapest, fastest, best value, earliest/latest, option display, selection, cabin/price/time answers. | Complete |
| Hotel comparison | Chat tool executor | Cheapest ranking, option display, selection, confirmation, and cancellation. | Complete |
| Persistent memory | `memory_store.py`, `SessionState` | Latest searches/options/pending actions survive in-process memory loss. | Complete |
| Follow-up route context | `services/chat_service.py`, `date_utils.py` | Reuses the latest flight/hotel context for partial questions and prevents prompt-example route leakage. | Complete |
| Date handling | `date_utils.py`, direct endpoints | Relative/yearless date resolution plus strict past and ordering validation. | Complete |
| Auth/profile/preferences | `auth.py`, profile components | Local account, signed token, profile editing, and stored preferences. | Complete |
| Website routing | React Router in `App.tsx` | Home, Flights, Hotels, Cart, Checkout, Voyager, and Profile pages. | Complete |
| Cart | `CartContext.tsx`, cards, Cart page | Browser-local flight/hotel cart with add/remove/clear and currency-grouped totals. | Complete |
| Checkout | `/checkout/confirm`, Checkout page | Produces one local booking reference linked to authenticated or demo context. | Complete |
| Booking records | `Booking` model and chat/checkout flows | Flight, hotel, legacy, and cart checkout summaries in SQLite. | Complete |
| Mock resilience | Provider adapters and mock providers | Safe fallback after missing configuration, network error, timeout, malformed response, or empty result. | Complete |
| Automated verification | `backend/tests/` and Vite build | Covers auth, flights, hotels, checkout, dates, persistence, and follow-ups. | Complete |

## 11. Current Limitations and Honest Scope

- Checkout is local only: it creates a SQLite record and reference, not a real booking transaction.
- Checkout is handled inside the Voyage AI application workflow.
- The project does not create Duffel airline orders or live airline reservations.
- The project does not create LiteAPI hotel reservations.
- Search provider behaviour depends on local server-side configuration and may use deterministic mock fallback.
- The cart is browser-local; it is not synchronised across browsers, devices, or accounts.
- Browser auth state is stored locally, which is acceptable for local demonstration but not a production-grade token-storage strategy.
- Development CORS accepts all origins and needs production restriction.
- Local authentication has no password reset, email verification, refresh-token rotation, rate limiting, HTTPS enforcement, account recovery, or production identity integration.
- User preferences are stored but do not yet drive a full personalised recommendation engine.
- Flight-plus-hotel package building has not been implemented.
- Travel suitability scoring, explainable recommendation scoring, budget optimisation, and carbon-aware ranking have not been implemented.
- Voice interaction, itinerary/PDF export, calendar export, multilingual response strategy, and an evaluation dashboard are not implemented.
- The large hero image produced by the current frontend build is about 2.3 MB; it is a sensible candidate for optimisation before a public web deployment.
- SQLite and the current automatic local compatibility updates are suitable for a local application. A production system would normally use managed database migrations and a server database.

## 12. Testing and Verification

The following safe verification commands were run on 22 June 2026. No provider calls or secret reads were required.

```powershell
cd backend
.\.venv\Scripts\python.exe -m py_compile app\main.py app\models.py app\database.py app\schemas.py app\llama_service.py app\memory_store.py app\auth.py app\date_utils.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v

cd frontend
npm run build
```

| Check | Result |
|---|---|
| Python compilation | Passed with no output/errors. |
| Backend tests | **83 tests passed** in 3.598 seconds. |
| Frontend TypeScript check | Passed. |
| Vite production build | Passed; 63 modules transformed. |
| Build warnings/errors | No build warnings or errors were reported. |

The test suite covers authentication, current-context routing, date validation, provider error handling, persistent flight/hotel memory, card selection, booking confirmation/cancellation, checkout totals, and safe data boundaries.

## 13. Manual Demonstration Flows

### A. Manual flight search to checkout

1. Open `/flights`.
2. Enter origin/destination airport codes, valid future date(s), passengers, cabin class, and stops preference.
3. Search and review normalised flight cards.
4. Use **Add to cart** on a flight card.
5. Open `/cart`, verify the flight item and currency total.
6. Select **Proceed to Checkout**.
7. Enter name and email, then confirm.
8. Observe a local `TA...` booking reference and summary.

### B. Manual hotel search to checkout

1. Open `/hotels`.
2. Enter a city, valid check-in/check-out dates, guests, rooms, and optional filters.
3. Search and review hotel cards.
4. Use **Add to cart** on a hotel card.
5. Review it on `/cart`.
6. Open `/checkout`, complete contact details, and confirm.
7. Observe one local booking reference with grouped totals.

### C. The Voyager flight flow

1. Open `/voyager`.
2. Ask: `I want to fly from London Heathrow to New York on 12 September 2026 and return on 19 September 2026. Economy, no stops.`
3. Review returned flight cards.
4. Ask: `Which is the cheapest?`
5. Ask: `Is it still available?`
6. Ask: `I like it, can I book it?`
7. Confirm with: `yes`.
8. Observe a local booking reference only after confirmation.

### D. The Voyager hotel flow

1. Open `/voyager`.
2. Ask: `Find me a hotel in Paris from 2026-09-12 to 2026-09-16 for 2 adults and 1 room.`
3. Ask: `Which is the cheapest hotel?`
4. Ask: `Show option 2.`
5. Ask: `I like it, can I book it?`
6. Confirm with: `yes`.
7. Observe a local hotel booking reference.

### E. Follow-up context and dates

1. Ask: `Is there flights from Cairo to London tomorrow?`
2. Ask: `Make it 2 passengers.` The backend retains the Cairo-to-London route and refreshes with the new passenger count.
3. Ask: `What about next week?` The backend retains the same route and changes the date context.
4. Ask a new yearless request such as: `Can I fly from Cairo to London on the 5th of October?`
5. The backend resolves the date to the next future 5 October before searching.

### F. Login, profile, and preferences

1. Open `/profile`.
2. Register a local account or log in.
3. Edit profile information and travel preferences.
4. Use The Voyager or Checkout while signed in.
5. The new messages, session state, preferences, and booking records are linked to the signed-in local user rather than the demo user.

## 14. Beginner-Friendly Explanation

### A. What Voyage AI is

Voyage AI is a travel-planning website. It lets a user search flights and hotels, compare options, save choices in a cart, and create a local booking record. It also includes an AI assistant called The Voyager.

### B. What The Voyager is

The Voyager is the chat page inside Voyage AI. It understands natural language such as "find the cheapest hotel" or "make it two passengers." It proposes a tool action, while the backend checks the request and performs the work safely.

### C. What happens when a user searches flights

The user either completes the Flights page form or asks The Voyager. The backend validates the route, date, passengers, cabin, and stops. It then searches the configured flight provider. If that cannot produce usable results, the backend returns normalised mock flights so the application still behaves predictably. The browser renders those results as flight cards.

### D. What happens when a user searches hotels

The user enters a city and stay details on the Hotels page or asks The Voyager. The backend validates the stay dates, guests, rooms, and preferences, then tries the hotel rates provider. It converts usable information into the project hotel-card format. If the provider cannot be used, it returns safe mock hotel results.

### E. What happens when a user uses the chatbot

The chat message goes to FastAPI. The backend gives Llama a short, safe summary of the current conversation and latest travel options. Llama chooses a tool such as search, rank, show option, select, availability check, confirm, or cancel. The backend validates the tool and performs the action. It remembers normalised results and current choices in SQLite so follow-up questions still make sense.

### F. What happens when a user adds to cart and checks out

The browser saves selected flight/hotel cards in local storage. At checkout, the backend validates the items, totals amounts separately for each currency, and creates one local booking reference. This is a stored application record, not a payment or provider reservation.

### G. What is stored in the database

SQLite stores local users, password hashes, sessions, chat messages, preferences, safe travel context, and local booking summaries/references. It does not store provider credentials or full raw provider responses.

### H. What is real API data and what is generated

Flight and hotel searches can use configured external provider integrations, but each has a mock fallback. Mock data is used when a provider is unavailable or returns no usable results. Booking confirmation and checkout are always local application records; booking references and travel documents are generated within Voyage AI.

### I. What the project has achieved so far

Voyage AI has a routed travel website, manual flight/hotel search, The Voyager conversational assistant, controlled local LLM tool routing, persistent chat context, date understanding, user accounts/preferences, cart, local checkout, robust provider fallbacks, and 83 passing backend tests.

### J. What is left for future work

The next major product features could be real provider order/reservation workflows, payment, package building, personalised suitability scoring, explainable recommendations, voice, itinerary export, performance/evaluation dashboards, stronger production security, and deployment readiness.
