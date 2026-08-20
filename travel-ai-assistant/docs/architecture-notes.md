# Architecture Notes

## Current Application

The application has two parts:

- `frontend`: React + Vite + TypeScript browser application.
- `backend`: FastAPI service that exposes `/health` and `/chat`.

The frontend sends a message to the backend. The backend extracts mocked travel
intent, searches mocked travel data, and returns a placeholder assistant reply.

SQLite persistence has been added for the backend application. The backend now
creates `users`, `sessions`, `messages`, and `bookings` tables and uses a demo
user/session until authentication is added.

## Future Llama Integration

`backend/app/llama_service.py` is the future home for intent extraction and
response generation. It currently returns mocked structured data so the API
contract can be tested before adding a real model.

## Future Database Integration

`backend/app/database.py` configures SQLite and creates local development
tables. `backend/app/models.py` defines the first SQLAlchemy schema. Future
database work can add migrations, authentication-owned users, travel searches,
preferences, payment state, and booking audit history.

## Future Expedia Integration

`backend/app/mock_provider.py` isolates mocked travel search behavior. A future
Expedia integration can replace or extend this module without forcing the
frontend to change its first chat flow.

## Future Voice Integration

Voice should stay browser-first at the start. The frontend can capture audio or
speech transcripts, then send text to the existing `/chat` endpoint or a future
voice-specific endpoint.

## PWA Direction

The app is built as a responsive website for laptop and Android mobile browsers.
Later PWA work can add a web app manifest, icons, service worker, offline
handling, and install prompts.
