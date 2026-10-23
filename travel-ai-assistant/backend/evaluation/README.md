# Voyage AI Evaluation Folder

This folder contains the evaluation scripts, input scenarios and result files used as evidence for Chapter 5 of the dissertation.

## Folder Layout

- `scripts/` contains Python scripts that run the evaluation checks.
- `scenarios/` contains JSON input cases used by the evaluators.
- `results/` contains generated reports, CSV result tables and dissertation-facing summaries.
- `_archive_not_submitted/` contains old helper files, caches and outputs that are not intended for final submission.

Do not include `_archive_not_submitted/` in the final submission zip.

## Scripts

Run these commands from the `backend` folder:

```bash
python evaluation/scripts/run_benchmark.py
python evaluation/scripts/run_ai_response_eval.py
python evaluation/scripts/robustness_and_error_tests.py
python evaluation/scripts/response_time_tests.py
```

For live Llama evaluation, Ollama must be running locally:

```bash
python evaluation/scripts/run_ai_response_eval.py --live-llama --repeats 3
```

Provider keys are loaded from the backend `.env` file. They are not included in the repository.

## Scenarios

- `scenarios/benchmark_cases.json` supports the first controlled dialogue benchmark with 30 project-specific cases.
- `scenarios/florida_birmingham_november.json` supports fresh-session and polluted-session Florida package routing checks.
- `scenarios/diagnostic_trip_router_expansion.json` supports the larger structured-action routing evaluation.
- `scenarios/flexible_human_travel_inputs.json` checks flexible user phrasing, follow-ups, corrections and unavailable-result handling.
- `scenarios/destination_discovery_regressions.json` checks destination-discovery state and clarification regressions.
- `scenarios/package_budget_regressions.json` checks package budget, ranking and duplicate-option regressions.

## Main Result Files

- `results/benchmark_summary.md` and `results/benchmark_results.csv` support the first controlled dialogue benchmark.
- `results/failure_analysis.md` records the earlier benchmark failure analysis and classification work.
- `results/latest/evaluation_report.md`, `results/latest/evaluation_report.json` and `results/latest/failure_traces.jsonl` support the final structured-action evaluator result.
- `results/robustness_and_error_summary.md` and `results/robustness_and_error_results.csv` support robustness and error-handling evidence.
- `results/response_time_summary.md` and `results/response_time_results.csv` support response-time evidence for simple help, flight search, hotel search, package search, destination discovery, cart selection, invalid request handling and booking lookup.
- `results/manual_workflow_tests.md` is the manual browser testing checklist for end-to-end demo evidence.
- `results/evaluation_overview.md` is the dissertation-facing index of the evaluation evidence.

If live Llama evaluation is run, live result files are also written to `results/latest/`.
