# Voyage AI

Voyage AI is an AI-powered travel planning application. It combines a React and
TypeScript travel website with a FastAPI backend, local Llama tool routing,
Duffel test-mode flights, LiteAPI hotel rates, safe provider fallbacks, local
accounts, persistent travel context, and SQLite application bookings.

The product has two planning paths:

- Manual mode: search flights and hotels, add public result cards to a local
  trip cart, then sign in to create one local application checkout reference.
- The Voyager: use the conversational assistant to search, compare, select,
  and confirm individual flight or hotel bookings. On supported browsers, The
  Voyager also supports optional microphone input and spoken responses.

The homepage is now API-driven. It loads flight ideas, package ideas, property
types, trending destinations, and Voyager prompts from FastAPI endpoints, then
links users into the Flights, Hotels, or The Voyager pages with filters already
prepared.

After checkout, Voyage AI clears the active cart and stores structured booking
details with the generated reference. Signed-in users can reopen previous
bookings from Profile and view the saved generated flight ticket and hotel
voucher again.

Voyage AI keeps provider credentials server-side and records bookings inside the application.

## Run Locally

Start the backend:

```powershell
cd backend
.\.venv\Scripts\activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Start the frontend in a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL, normally `http://127.0.0.1:5173`.

## Main Routes

- `/` Voyage AI home page
- `/flights` manual flight search and cart additions
- `/hotels` manual hotel search and cart additions
- `/cart` persistent local trip cart
- `/checkout` sign-in protected local application checkout
- `/voyager` The Voyager conversational assistant
- `/profile` local account login, registration, preferences, and previous bookings

## The Voyager Voice Mode

The Voyager includes optional browser-based voice mode. Users can click the
microphone button, speak a travel request, and have the recognised text sent
through the same `/chat` endpoint as typed messages. When Voice mode is on, The
Voyager reads a short summary of each assistant response aloud.

Voice input uses the browser SpeechRecognition API where available. Spoken
responses use `window.speechSynthesis`. Browsers without speech recognition
fall back cleanly to typed chat.

## Security Boundaries

Provider credentials, JWT secrets, and raw supplier payloads remain in the
backend. The browser receives normalised public flight and hotel data only.
Cart checkout requires a local account, stores a compact summary of public
selections, and creates a local `TA...` reference; it does not call a supplier
reservation endpoint. The Voyager remains usable as a guest, and stale bearer
tokens on chat are ignored so users do not need to sign out to keep planning.

See [backend/README.md](backend/README.md) for API and environment setup, and
[docs/project-file-and-architecture-guide.md](docs/project-file-and-architecture-guide.md)
for the current architecture.
