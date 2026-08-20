# Voyage AI Response-Time Summary

Generated: 2026-08-11 16:41:29

Each test case was run 3 times using deterministic provider and router stubs.

| Test case | Average time | Median time | Min | Max | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| simple_help | 55.33 ms | 44.61 ms | 43.55 ms | 77.82 ms | Pass |
| flight_search | 42.81 ms | 43.13 ms | 40.43 ms | 44.86 ms | Pass |
| hotel_search | 43.59 ms | 43.43 ms | 41.78 ms | 45.56 ms | Pass |
| package_search | 45.53 ms | 45.60 ms | 45.10 ms | 45.89 ms | Pass |
| destination_discovery | 38.67 ms | 39.19 ms | 36.75 ms | 40.07 ms | Pass |
| cart_selection_flow | 747.65 ms | 740.68 ms | 718.84 ms | 783.43 ms | Pass |
| invalid_request | 723.74 ms | 715.77 ms | 693.54 ms | 761.89 ms | Pass |
| booking_lookup_or_profile_route | 29.80 ms | 29.74 ms | 29.71 ms | 29.95 ms | Pass |

## Thesis Interpretation Notes

- The response-time evaluation measures the backend chat workflow under controlled provider conditions.
- The timings focus on application orchestration, routing, validation and state handling rather than live supplier latency.
- Live provider and live local Llama timings may differ from these controlled measurements.
