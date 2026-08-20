# Voyager AI Response Evaluation Report (Mocked Live-Shaped)

Generated: 2026-08-14T19:38:51

## Metadata

| Field | Value |
| --- | --- |
| live_llama | False |
| model_name | llama3.1:8b-instruct-q4_0 |
| ollama_base_url | http://127.0.0.1:11434 |
| ollama_model_digest | - |
| prompt_version | travel_tool_router_2026_08_phase3d_visible_results |
| schema_version | travel_planner_tool_call_schema_v1_phase3d |
| timestamp | 2026-08-14T19:38:51 |

## Metrics

| Metric | Value |
| --- | ---: |
| Total scenarios | 25 |
| Total turns | 42 |
| Passed turns | 42 |
| Failed turns | 0 |
| JSON parse rate | 100.00% |
| Schema validity | 100.00% |
| Tool accuracy | 100.00% |
| Slot/state accuracy | 100.00% |
| Joint state accuracy | 100.00% |
| Prepare-trip usage rate | 61.90% |
| Prepare-trip planner accuracy | 92.86% |
| Requested-product accuracy | 78.57% |
| Execution decision accuracy | 100.00% |
| Provider execution gate accuracy | 100.00% |
| Pending-action accuracy | 100.00% |
| Repeat stability score | 100.00% |
| Stale override count | 0 |
| Pending-action passed | 70 |
| Pending-action failed | 0 |
| Illegal provider calls | 0 |





## Failure Categories

```json
{}
```

## Tool Confusion Matrix

| Expected tool | confirm_booking | general_help | handle_visible_results | prepare_trip_request | search_hotels |
| --- | ---: | ---: | ---: | ---: | ---: |
| prepare_trip_request | 0 | 0 | 0 | 26 | 0 |
| confirm_booking | 4 | 0 | 0 | 0 | 0 |
| handle_visible_results | 0 | 0 | 4 | 0 | 0 |
| general_help | 0 | 6 | 0 | 0 | 0 |
| prepare_trip_request|search_hotels | 0 | 0 | 0 | 0 | 2 |

## Wrong-Tool Pattern Analysis

| Pattern | Count |
| --- | ---: |
| - | 0 |

## Failed Turn Diagnostics

| Scenario | Turn | Repeat | User message | Expected tool | Actual tool | Expected important slots | Actual slots | Category | Diagnostics | Likely cause |
| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- |
| - | - | - | - | - | - | - | - | - | - | - |

## Failure Recommendations

| Category | Count | Recommendation |
| --- | ---: | --- |
| - | 0 | No failures. |

## Scenario Repeats

| Scenario | Pass count | Fail count | Pass rate | Most common wrong tool | Most common wrong slots |
| --- | ---: | ---: | ---: | --- | --- |
| florida_holiday_package_paraphrase | 1 | 0 | 100.00% | - | - |
| orlando_exact_package_from_scratch | 1 | 0 | 100.00% | - | - |
| florida_active_orlando_short_followup | 1 | 0 | 100.00% | - | - |
| confirm_pending_package_go_ahead | 1 | 0 | 100.00% | - | - |
| pending_florida_month_only_followup | 1 | 0 | 100.00% | - | - |
| pending_florida_day_only_followup | 1 | 0 | 100.00% | - | - |
| pending_florida_day_range_followup | 1 | 0 | 100.00% | - | - |
| egypt_birmingham_december_broad_package | 1 | 0 | 100.00% | - | - |
| egypt_hurghada_followup_from_pending_region | 1 | 0 | 100.00% | - | - |
| exact_paris_package_birmingham_dates | 1 | 0 | 100.00% | - | - |
| polluted_london_package_then_exact_paris | 1 | 0 | 100.00% | - | - |
| orlando_package_results_cheapest_followup | 3 | 0 | 100.00% | - | - |
| orlando_package_results_add_current_visible_package | 1 | 0 | 100.00% | - | - |
| stale_orlando_package_then_new_skiing_discovery | 3 | 0 | 100.00% | - | - |
| broad_skiing_this_month_then_confirmation | 3 | 0 | 100.00% | - | - |
| hotel_london_budget_date_followup | 2 | 0 | 100.00% | - | - |
| flight_to_london_default_origin_invariant | 1 | 0 | 100.00% | - | - |
| clarification_destination_this_month_resume | 2 | 0 | 100.00% | - | - |
| clarification_destination_day_only_resume | 2 | 0 | 100.00% | - | - |
| clarification_hotel_date_resume | 2 | 0 | 100.00% | - | - |
| clarification_loop_guard_non_answer | 2 | 0 | 100.00% | - | - |
| clarification_rejects_past_date | 2 | 0 | 100.00% | - | - |
| clarification_rejects_malformed_date | 2 | 0 | 100.00% | - | - |
| florida_birmingham_november_fresh | 3 | 0 | 100.00% | - | - |
| florida_birmingham_november_polluted | 3 | 0 | 100.00% | - | - |

## Turn Results

| Scenario | Turn | Category | Result | Response excerpt |
| --- | ---: | --- | --- | --- |
| florida_holiday_package_paraphrase | 1 | - | PASS | I can help build a Florida package from Birmingham Airport (BHX). Florida has several main airport choices, including Orlando, Miami, Tampa and Fort Lauderdale. |
| orlando_exact_package_from_scratch | 1 | - | PASS | I have your destination as Orlando International Airport (MCO) from Birmingham Airport (BHX), travelling from 15 Nov 2026 to 19 Nov 2026. Would you like me to b |
| florida_active_orlando_short_followup | 1 | - | PASS | I have your destination as Orlando International Airport (MCO) from Birmingham Airport (BHX), travelling from 15 Nov 2026 to 19 Nov 2026. Would you like me to b |
| confirm_pending_package_go_ahead | 1 | - | PASS | I built package options for your 4-night Orlando trip from Birmingham Airport (BHX), departing 15 Nov 2026 and returning 19 Nov 2026. The cheapest package is GB |
| pending_florida_month_only_followup | 1 | - | PASS | I can help build a Florida package from Birmingham Airport (BHX). Florida has several main airport choices, including Orlando. November is a broad date range, s |
| pending_florida_day_only_followup | 1 | - | PASS | I have your trip as Birmingham Airport (BHX) to Florida departing 15 Nov 2026. Florida has several airports, including Orlando. Would you like me to search thos |
| pending_florida_day_range_followup | 1 | - | PASS | I have your destination as Orlando International Airport (MCO) from Birmingham Airport (BHX), travelling from 15 Nov 2026 to 20 Nov 2026. Would you like me to b |
| egypt_birmingham_december_broad_package | 1 | - | PASS | I can help build a Egypt package from Birmingham Airport (BHX). Egypt has several main airport choices, including Hurghada, Sharm El Sheikh and Cairo. December  |
| egypt_hurghada_followup_from_pending_region | 1 | - | PASS | I have your destination as Hurghada International Airport (HRG) from Birmingham Airport (BHX), travelling from 10 Dec 2026 to 15 Dec 2026. Would you like me to  |
| exact_paris_package_birmingham_dates | 1 | - | PASS | I have your destination as Paris Charles de Gaulle Airport (CDG) from Birmingham Airport (BHX), travelling from 12 Sep 2026 to 16 Sep 2026. Would you like me to |
| polluted_london_package_then_exact_paris | 1 | - | PASS | I have your destination as Paris Charles de Gaulle Airport (CDG) from Birmingham Airport (BHX), travelling from 12 Sep 2026 to 16 Sep 2026. Would you like me to |
| orlando_package_results_cheapest_followup | 1 | - | PASS | The cheapest option is Package 1 at £713.30. It includes a Duffel Airways flight from BHX to MCO and a 4-night stay at Rosen Inn International Near The Parks fr |
| orlando_package_results_cheapest_followup | 2 | - | PASS | Here are your package options sorted by total price. |
| orlando_package_results_cheapest_followup | 3 | - | PASS | Here is Package 1 at £713.30. It includes a Duffel Airways flight from BHX to MCO and a 4-night stay at Rosen Inn International Near The Parks from 15 Nov 2026  |
| orlando_package_results_add_current_visible_package | 1 | - | PASS | I added package option 1 to your cart. The total is GBP 713.30. You can now proceed to checkout. |
| stale_orlando_package_then_new_skiing_discovery | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use Birmingham Airport (BHX) as your departure airport. |
| stale_orlando_package_then_new_skiing_discovery | 2 | - | PASS | I can search skiing destinations for a 3-night trip from East Midlands Airport (EMA) in September. Please tell me the exact departure date, or I can suggest sui |
| stale_orlando_package_then_new_skiing_discovery | 3 | - | PASS | I'll use East Midlands Airport (EMA) as your origin and search for skiing destination ideas for 3 nights, departing 15 Sep 2026 and returning 18 Sep 2026. I fou |
| broad_skiing_this_month_then_confirmation | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use Birmingham Airport (BHX) as your departure airport. |
| broad_skiing_this_month_then_confirmation | 2 | - | PASS | I'll use Birmingham Airport (BHX) as your origin and search for skiing destination ideas for 3 nights, departing 19 Aug 2026 and returning 22 Aug 2026. I found  |
| broad_skiing_this_month_then_confirmation | 3 | - | PASS | Please choose one of the destination options first, then I can search flights or build a package. |
| hotel_london_budget_date_followup | 1 | - | PASS | Sure - I can search hotels in London. When do you want to check in, and how many nights will you stay? |
| hotel_london_budget_date_followup | 2 | - | PASS | I searched hotels in London around London. I found 3 hotel option(s). |
| flight_to_london_default_origin_invariant | 1 | - | PASS | I have your destination as London Heathrow Airport (LHR) from East Midlands Airport (EMA). Please provide your exact departure and return dates. |
| clarification_destination_this_month_resume | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use East Midlands Airport (EMA) as your departure airport. |
| clarification_destination_this_month_resume | 2 | - | PASS | I'll use East Midlands Airport (EMA) as your origin and search for skiing destination ideas for 3 nights, departing 19 Aug 2026 and returning 22 Aug 2026. I fou |
| clarification_destination_day_only_resume | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use East Midlands Airport (EMA) as your departure airport. |
| clarification_destination_day_only_resume | 2 | - | PASS | I'll use East Midlands Airport (EMA) as your origin and search for skiing destination ideas for 3 nights, departing 15 Aug 2026 and returning 18 Aug 2026. I fou |
| clarification_hotel_date_resume | 1 | - | PASS | Sure - I can search hotels in London. When do you want to check in, and how many nights will you stay? |
| clarification_hotel_date_resume | 2 | - | PASS | I searched hotels in London around London. I found 3 hotel option(s). |
| clarification_loop_guard_non_answer | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use East Midlands Airport (EMA) as your departure airport. |
| clarification_loop_guard_non_answer | 2 | - | PASS | I'll use East Midlands Airport (EMA) as your origin. When do you want to depart? To keep this moving, you can choose one of these: 15 Aug 2026, 21 Aug 2026, 28  |
| clarification_rejects_past_date | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use East Midlands Airport (EMA) as your departure airport. |
| clarification_rejects_past_date | 2 | - | PASS | I'll use East Midlands Airport (EMA) as your origin. When do you want to depart? To keep this moving, you can choose one of these: 15 Aug 2026, 21 Aug 2026, 28  |
| clarification_rejects_malformed_date | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use East Midlands Airport (EMA) as your departure airport. |
| clarification_rejects_malformed_date | 2 | - | PASS | I'll use East Midlands Airport (EMA) as your origin. When do you want to depart? To keep this moving, you can choose one of these: 15 Aug 2026, 21 Aug 2026, 28  |
| florida_birmingham_november_fresh | 1 | - | PASS | I can help build a Florida package from Birmingham Airport (BHX). Florida has several main airport choices, including Orlando, Miami, Tampa and Fort Lauderdale. |
| florida_birmingham_november_fresh | 2 | - | PASS | I have your destination as Orlando International Airport (MCO) from Birmingham Airport (BHX), travelling from 15 Nov 2026 to 19 Nov 2026. Would you like me to b |
| florida_birmingham_november_fresh | 3 | - | PASS | I built package options for your 4-night Orlando trip from Birmingham Airport (BHX), departing 15 Nov 2026 and returning 19 Nov 2026. The cheapest package is GB |
| florida_birmingham_november_polluted | 1 | - | PASS | I can help build a Florida package from Birmingham Airport (BHX). Florida has several main airport choices, including Orlando, Miami, Tampa and Fort Lauderdale. |
| florida_birmingham_november_polluted | 2 | - | PASS | I have your destination as Orlando International Airport (MCO) from Birmingham Airport (BHX), travelling from 15 Nov 2026 to 19 Nov 2026. Would you like me to b |
| florida_birmingham_november_polluted | 3 | - | PASS | I built package options for your 4-night Orlando trip from Birmingham Airport (BHX), departing 15 Nov 2026 and returning 19 Nov 2026. The cheapest package is GB |

## Notes

This evaluator runs the same Voyager chat/state pipeline used by `/chat`.
Mocked mode replays live-shaped tool outputs. Live mode calls the local Ollama
router and stubs travel providers so the report focuses on tool routing, schema
validity, state merge correctness, pending-action handling, provider execution
gates, and response text checks.
