# Voyage AI Benchmark Summary

Generated: 2026-08-12 10:54:45

## Scope

This small MultiWOZ-style benchmark evaluates Voyage AI's implemented travel dialogue workflows: flights, hotels, packages, follow-up context, voice-style misrecognition, and cart/checkout handling.

## Cases

Total cases: 30

Categories tested:

- cart_checkout: 4
- flight: 5
- follow_up_context: 6
- hotel: 5
- package: 6
- voice_style_misrecognition: 4

## Metrics

| Metric | Result |
| --- | ---: |
| Intent accuracy | 80.00% |
| Slot accuracy | 84.82% |
| Workflow success rate | 76.67% |
| Context follow-up success rate | 66.67% |
| Clarification success rate | 100.00% |

## Failed Cases

| Case | Category | Expected intent | Actual intent | Failed slots |
| --- | --- | --- | --- | --- |
| package_001 | package | package_search | flight_search | latest_visible_result_type: expected 'packages', got 'flights' |
| package_003 | package | package_search | clarification | destination: expected 'ERZ', got None<br>destination_city: expected 'Erzurum', got None<br>nights: expected 3, got None<br>latest_visible_result_type: expected 'packages', got 'package_clarification' |
| package_005 | package | package_search | clarification | destination: expected 'CDG', got None<br>destination_city: expected 'Paris', got None<br>departure_date: expected '2026-09-12', got '2026-08-12'<br>return_date: expected '2026-09-15', got '2026-08-15'<br>latest_visible_result_type: expected 'packages', got 'trip_clarification' |
| package_006 | package | package_search | clarification | return_date: expected '2026-09-16', got None<br>latest_visible_result_type: expected 'packages', got 'package_clarification' |
| context_005 | follow_up_context | destination_discovery | general_enquiry | excluded_region: expected 'Europe', got []<br>required_activity: expected 'skiing', got []<br>latest_visible_result_type: expected 'destinations', got None |
| context_006 | follow_up_context | clarification | clarification | nights: expected 3, got None |
| voice_003 | voice_style_misrecognition | cart_update | flight_selection | latest_visible_result_type: expected 'cart', got 'flight_selected' |

## Interpretation

The benchmark measures whether the current Voyage AI backend can preserve travel dialogue state and reach the expected workflow state across realistic multi-turn prompts. Provider calls are replaced with deterministic benchmark data so the results focus on intent handling, slot extraction, and state tracking rather than live API availability or price changes.
