# Voyage AI Evaluation Overview

This file is generated as a thesis-facing index for the evaluation evidence in this folder. Run the evaluation scripts before using the latest figures in the dissertation.

## 1. Automated Backend Tests

The backend unittest suite is used for regression testing across routing, memory, authentication, flights, hotels, packages, checkout and booking behaviour.

Latest recorded run: see the terminal output from:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

Latest recorded result from this evaluation pass: 220 backend tests passed.

## 2. Frontend Build Verification

The frontend build is checked with:

```bash
npm run build
```

This confirms that the React and TypeScript frontend compiles successfully.

## 3. MultiWOZ-Style Benchmark

The project includes a 30-case custom benchmark inspired by MultiWOZ. It tests multi-turn dialogue, intent handling, slot extraction and dialogue state tracking.

Current benchmark summary: [benchmark_summary.md](benchmark_summary.md)

Current recorded metrics:

- Intent accuracy: 80.00%
- Slot accuracy: 84.82%
- Workflow success rate: 76.67%
- Context follow-up success rate: 66.67%
- Clarification success rate: 100.00%

This benchmark is custom rather than official MultiWOZ because Voyage AI includes project-specific workflows such as flight search, hotel search, destination discovery, packages, cart and checkout.

## 4. Manual Workflow Testing

Manual test checklist: [manual_workflow_tests.md](manual_workflow_tests.md)

Screenshots should be captured manually during the final demonstration and referenced in the thesis where useful.

## 5. Response-Time Testing

Response-time summary: [response_time_summary.md](response_time_summary.md)

The response-time script measures controlled chat workflow timings using deterministic provider and router stubs.

Current measured averages:

| Test case | Average time |
| --- | ---: |
| simple_help | 55.33 ms |
| flight_search | 42.81 ms |
| hotel_search | 43.59 ms |
| package_search | 45.53 ms |
| destination_discovery | 38.67 ms |
| cart_selection_flow | 747.65 ms |
| invalid_request | 723.74 ms |
| booking_lookup_or_profile_route | 29.80 ms |

## 6. Provider/Fallback and Error Testing

Fallback/error summary: [fallback_and_error_summary.md](fallback_and_error_summary.md)

These tests check that the system handles provider unavailability, missing information, invalid requests and unsafe checkout states without crashing.

Current recorded result: 10 fallback/error cases passed, 0 failed.

## 7. Limitations

- This is an academic prototype.
- It does not issue real airline tickets.
- It does not process real payment.
- It does not confirm supplier-side hotel reservations.
- The evaluation uses controlled tests rather than a large-scale public deployment.
- No SUS/user study evidence is included unless a separate user study is performed and documented.

## Thesis Interpretation Notes

- The evaluation combines automated regression tests, a task-oriented dialogue benchmark, response-time measurements, fallback/error checks and manual workflow testing.
- The evidence is intended to show both strengths and limitations of the implemented system.
- The controlled benchmark and timing scripts improve repeatability, while manual workflow testing supports demonstration-level validation.
