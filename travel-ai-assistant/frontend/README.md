# Voyage AI Frontend

React, Vite, and TypeScript frontend for the Voyage AI travel planning
application.

## Run Locally

```powershell
cd frontend
npm install
npm run dev
```

The app normally runs at `http://127.0.0.1:5173`.

## Pages

- Home: API-driven Voyage AI search hub with dynamic flight deals, package
  ideas, property types, destinations, and Voyager prompts.
- Flights: manual Duffel-backed flight search with mock fallback.
- Hotels: manual LiteAPI-backed hotel search with mock fallback.
- Cart: local browser trip cart persisted in `localStorage`.
- Checkout: sign-in protected booking confirmation with automatic cart clearing
  and visible generated ticket/voucher cards.
- The Voyager: existing Llama-backed chat assistant with optional browser voice
  input and spoken response summaries.
- Profile: local account login, registration, preferences, and previous bookings.

## Guest And Account Flow

Guests can search flights, search hotels, use The Voyager, discover
destinations, and build package options. Checkout requires sign-in. If a guest
goes to checkout, the selected cart stays in browser storage and the profile
page redirects back to checkout after successful login or registration.

The Voyager keeps a separate `voyage_session_id` in browser storage. If an old
token is invalid, chat retries once as a guest so the assistant does not require
manual sign-out before it can respond.
After checkout succeeds, the active cart is cleared automatically and the
latest booking reference is saved locally so the empty cart can link back to the
booking details page.

## The Voyager Voice Mode

Voice mode is implemented entirely in the browser:

- `src/hooks/useSpeechRecognition.ts` wraps `SpeechRecognition` /
  `webkitSpeechRecognition` for microphone input.
- `src/hooks/useTextToSpeech.ts` wraps `window.speechSynthesis` for read-aloud
  responses.
- `src/utils/speechSummary.ts` shortens flight, hotel, package, destination,
  booking, and document responses before speaking them.
- `src/components/VoiceControls.tsx` renders the microphone, stop button,
  voice-mode toggle, and visible voice status.

If speech recognition is not available, The Voyager shows a friendly fallback
message and typed chat continues normally.

## Backend URL

The frontend calls `http://127.0.0.1:8000` by default. To override it locally,
create an ignored `frontend/.env` file:

```text
VITE_API_BASE_URL=http://127.0.0.1:8000
```

For Android testing, use a reachable laptop IP address. Never place Duffel,
LiteAPI, or JWT secrets in any frontend environment file.

## Dynamic Homepage

The homepage calls backend homepage APIs through `src/api/homepageApi.ts`.
It no longer keeps static fake destination or package card arrays in React.

Homepage cards open:

- `/flights` with route and date query parameters
- `/hotels` with destination and property-type query parameters
- `/voyager` with an encoded prompt for The Voyager

The homepage search fields use backend airport and hotel-location suggestions,
so users search from provider/cache suggestions rather than frontend-only
airport lists.

## Application Scope

The cart contains only normalised public travel result data. Checkout creates a
local SQLite `TA...` reference through the backend for a signed-in user. It
does not process a payment or create a real airline or hotel reservation.
Newer checkout records store compact structured details so Profile can reopen
the generated flight ticket and hotel voucher from `/bookings/:reference`.
