# Voyage AI

Voyage AI is an AI travel planning application. It combines a React and TypeScript frontend with a FastAPI backend, SQLite storage, local Llama tool routing through Ollama, Duffel test-mode flight search, LiteAPI hotel rates, destination discovery, cart handling and local application booking records.

The project has two main ways to plan travel:

Manual mode:
Users can search for flights and hotels using forms, view provider-backed result cards, add selected results to the cart and complete an application-level checkout workflow.

The Voyager:
Users can use the conversational assistant to describe a trip in natural language. The assistant can help with flight search, hotel search, destination discovery, package planning, visible result questions, cart actions and checkout workflow records. On supported browsers, The Voyager also supports optional microphone input and spoken responses.

Voyage AI keeps provider credentials server-side. The LLM does not directly call providers, write database records or create booking references. It proposes a structured action, and the backend validates the action before anything is executed.

## Requirements

Before running the project, install:

- Python 3.11 or later
- Node.js and npm
- Ollama
- The Llama model used by the project

To install the model with Ollama, run:

```powershell
ollama pull llama3.1:8b-instruct-q4_0
```

The backend expects Ollama to be running locally when live Llama features are used.

## Environment variables

Create a local `.env` file inside the backend folder.

Provider keys and secrets should be stored in the local `.env` file only. They are not included in the repository.

You may need values for:

```text
DUFFEL_ACCESS_TOKEN=
LITEAPI_KEY=
JWT_SECRET=
DATABASE_URL=
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

Use your own local test keys where needed.

## Run the backend

Open a terminal:

```powershell
cd backend
.\.venv\Scripts\activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If the virtual environment is not already created, create it and install the backend requirements first:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The backend should run at:

```text
http://127.0.0.1:8000
```

## Run the frontend

Open a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL shown in the terminal. It is normally:

```text
http://127.0.0.1:5173
```

## Main pages

```text
/          Voyage AI homepage
/flights   Manual flight search
/hotels    Manual hotel search
/cart      Local trip cart
/checkout  Application checkout workflow
/voyager   The Voyager conversational assistant
/profile   Login, registration, preferences and previous bookings
```

## Voice mode

The Voyager includes optional browser-based voice mode.

Users can click the microphone button, speak a travel request and send the recognised text through the same chat endpoint as typed messages.

Spoken responses use the browser speech synthesis feature. If speech recognition is not available in the browser, typed chat still works.

## Testing

Backend tests can be run from the backend folder:

```powershell
cd backend
.\.venv\Scripts\activate
pytest
```

Note: The backend evaluation suite expects a local Ollama instance to be installed and available if live Llama tests are enabled. Provider keys should be supplied through a local `.env` file and are not included in the repository.

## Security and project boundaries

Provider credentials, JWT secrets and raw supplier payloads stay in the backend.

The browser receives normalised public travel data only.

Checkout creates a local application booking record and generated travel documents. It does not take payment, issue airline tickets or confirm supplier-side hotel reservations.

The project uses provider-backed travel data for normal user-facing travel results. If provider-backed results are unavailable, the system should return a safe unavailable response instead of inventing travel results.

## Extra documentation

See:

```text
backend/README.md
docs/project-file-and-architecture-guide.md
```

for more backend and architecture details.
