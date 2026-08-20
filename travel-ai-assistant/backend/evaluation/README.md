# Voyage AI MultiWOZ-Style Benchmark

This folder contains a small MultiWOZ-style evaluation benchmark created for Voyage AI.

It is inspired by MultiWOZ because it tests multi-turn dialogue, slot filling, selected-option handling, and dialogue state tracking. It is not a full official MultiWOZ benchmark because Voyage AI has project-specific features such as flights, hotels, destination discovery, packages, cart, checkout, and travel documents.

The purpose is to evaluate whether the implemented assistant can understand travel intent, extract key travel slots, and preserve context across realistic user workflows.

## Files

- `benchmark_cases.json` contains the evaluation conversations and expected final state.
- `run_benchmark.py` runs the cases through the existing `/chat` endpoint with FastAPI `TestClient`.
- `benchmark_results.csv` is generated after a run.
- `benchmark_summary.md` is generated after a run and can be used in the dissertation evaluation chapter.

## Run

From the backend folder:

```bash
python evaluation/run_benchmark.py
```

The runner creates a temporary SQLite database and patches external travel providers with deterministic responses. This keeps the benchmark reproducible while still exercising the real `/chat` route and current backend state handling.

By default the runner also avoids live Ollama calls so it can be repeated without waiting on local model availability. To measure the full local-Llama routing path, start Ollama and run:

```bash
set VOYAGE_BENCHMARK_USE_LIVE_LLAMA=true
python evaluation/run_benchmark.py
```

## Voyager AI Response Evaluation

`run_ai_response_eval.py` evaluates Llama/tool-routing and active-trip state
transitions. By default it replays scenario files from `evaluation/scenarios/`
through the real chat handler with mocked live-shaped Llama outputs and
deterministic provider responses.

Run it from the backend folder:

```bash
python evaluation/run_ai_response_eval.py
```

To measure the actual local Ollama router with the same prompt, schema, safe
active-trip context, and model settings used by `/chat`, run:

```bash
python evaluation/run_ai_response_eval.py --live-llama
```

Repeat each scenario for stability checks:

```bash
python evaluation/run_ai_response_eval.py --live-llama --repeats 3
```

Live mode still blocks real Duffel and LiteAPI provider calls. It stubs those
responses so the report focuses on Llama JSON/tool output, schema validity,
state merging, pending actions, and provider execution gates.

The latest run is written to:

- `evaluation/runs/latest/evaluation_report.md`
- `evaluation/runs/latest/evaluation_report.json`
- `evaluation/runs/latest/failure_traces.jsonl`

Live mode writes:

- `evaluation/runs/latest/live_evaluation_report.md`
- `evaluation/runs/latest/live_evaluation_report.json`
- `evaluation/runs/latest/live_failure_traces.jsonl`

The live report includes model metadata, Ollama base URL, model digest when
available, prompt version, schema version, parse/schema rates, common wrong
tools, common wrong slots, and a mocked-vs-live comparison table.

The first corpus file covers the Florida/Birmingham/November flow in both fresh
and polluted-session variants. It checks raw tool output validity, post-merge
active state, pending package-build confirmation, illegal provider calls,
forbidden response text, stale state corrections, and provider request
arguments.

## Metrics

- Intent accuracy checks whether the final workflow matches the expected workflow.
- Slot accuracy checks expected slots such as origin, destination, dates, nights, selected option, and visible result type.
- Workflow success rate checks whether the conversation reached the expected final state.
- Context follow-up success rate checks whether multi-turn follow-ups used previous context rather than falling back to generic help or starting the wrong workflow.

Prices are not asserted because provider prices can change.
