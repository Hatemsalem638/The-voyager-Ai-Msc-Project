# Voyager AI Response Evaluation Report (Mocked Live-Shaped)

Generated: 2026-08-26T03:50:37

## Metadata

| Field | Value |
| --- | --- |
| live_llama | False |
| model_name | llama3.1:8b-instruct-q4_0 |
| ollama_base_url | http://127.0.0.1:11434 |
| ollama_model_digest | - |
| prompt_version | travel_tool_router_2026_08_phase3d_visible_results |
| schema_version | travel_planner_tool_call_schema_v1_phase3d |
| timestamp | 2026-08-26T03:50:37 |

## Metrics

| Metric | Value |
| --- | ---: |
| Total scenarios | 40 |
| Total turns | 60 |
| Passed turns | 59 |
| Failed turns | 1 |
| JSON parse rate | 100.00% |
| Schema validity | 100.00% |
| Tool accuracy | 100.00% |
| Slot/state accuracy | 100.00% |
| Joint state accuracy | 100.00% |
| Prepare-trip usage rate | 60.00% |
| Prepare-trip planner accuracy | 94.74% |
| Requested-product accuracy | 80.00% |
| Execution decision accuracy | 98.33% |
| Provider execution gate accuracy | 100.00% |
| Pending-action accuracy | 100.00% |
| Repeat stability score | 97.50% |
| Stale override count | 0 |
| Pending-action passed | 93 |
| Pending-action failed | 0 |
| Illegal provider calls | 0 |





## Failure Categories

```json
{
  "RESPONSE_STATE_MISMATCH": 1
}
```

## Tool Confusion Matrix

| Expected tool | confirm_booking | general_help | handle_visible_results | prepare_trip_request | search_hotels | select_flight |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| prepare_trip_request | 0 | 0 | 0 | 36 | 0 | 0 |
| general_help | 0 | 10 | 0 | 0 | 0 | 0 |
| select_flight | 0 | 0 | 0 | 0 | 0 | 1 |
| confirm_booking | 4 | 0 | 0 | 0 | 0 | 0 |
| handle_visible_results | 0 | 0 | 7 | 0 | 0 | 0 |
| prepare_trip_request|search_hotels | 0 | 0 | 0 | 0 | 2 | 0 |

## Wrong-Tool Pattern Analysis

| Pattern | Count |
| --- | ---: |
| other routing/state mismatch | 1 |

## Failed Turn Diagnostics

| Scenario | Turn | Repeat | User message | Expected tool | Actual tool | Expected important slots | Actual slots | Category | Diagnostics | Likely cause |
| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- |
| flexible_unrelated_non_travel_no_provider_calls | 1 | 1 | Can you write my dissertation introduction for me? | ['general_help'] | general_help | {} | {"intent": null, "origin": "EMA", "destinationRegion": null, "selectedDestination": "MEH", "month": null, "departureDate": null, "returnDate": null, "nights": null, "pendingAction": null, "latestVisibleResultType": "flight_clarification"} | RESPONSE_STATE_MISMATCH | REQUIRED_RESPONSE_TEXT_MISSING | Response formatting issue: text did not match the validated state. |

## Failure Recommendations

| Category | Count | Recommendation |
| --- | ---: | --- |
| RESPONSE_STATE_MISMATCH | 1 | Response formatting issue: text did not match the validated state. |

## Scenario Repeats

| Scenario | Pass count | Fail count | Pass rate | Most common wrong tool | Most common wrong slots |
| --- | ---: | ---: | ---: | --- | --- |
| confirmed_destination_not_widened_by_date_only_region | 1 | 0 | 100.00% | - | - |
| confirmed_destination_date_nights_resume | 1 | 0 | 100.00% | - | - |
| confirmed_destination_date_days_resume | 1 | 0 | 100.00% | - | - |
| affirmative_after_destination_discovery_offer_executes_discovery | 2 | 0 | 100.00% | - | - |
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
| flexible_clear_flight_birmingham_paris_dates | 1 | 0 | 100.00% | - | - |
| flexible_clear_hotel_london_dates | 1 | 0 | 100.00% | - | - |
| flexible_broad_skiing_trip_asks_for_date | 1 | 0 | 100.00% | - | - |
| flexible_package_region_missing_details | 1 | 0 | 100.00% | - | - |
| flexible_destination_clarification_day_answer | 2 | 0 | 100.00% | - | - |
| flexible_visible_package_cheapest_and_second_cart | 2 | 0 | 100.00% | - | - |
| flexible_book_it_uses_current_package_selection | 1 | 0 | 100.00% | - | - |
| flexible_explicit_destination_correction_with_dates | 1 | 0 | 100.00% | - | - |
| flexible_invalid_candidate_rejected | 1 | 0 | 100.00% | - | - |
| flexible_unrelated_non_travel_no_provider_calls | 0 | 1 | 0.00% | - | - |
| florida_birmingham_november_fresh | 3 | 0 | 100.00% | - | - |
| florida_birmingham_november_polluted | 3 | 0 | 100.00% | - | - |
| package_budget_followup_preserves_confirmed_trip | 1 | 0 | 100.00% | - | - |

## Turn Results

| Scenario | Turn | Category | Result | Response excerpt |
| --- | ---: | --- | --- | --- |
| confirmed_destination_not_widened_by_date_only_region | 1 | - | PASS | I found real economy flight options from BHX to HRG for 1 passenger using Duffel. |
| confirmed_destination_date_nights_resume | 1 | - | PASS | I found real economy flight options from BHX to HRG for 1 passenger using Duffel. |
| confirmed_destination_date_days_resume | 1 | - | PASS | I found real economy flight options from BHX to HRG for 1 passenger using Duffel. |
| affirmative_after_destination_discovery_offer_executes_discovery | 1 | - | PASS | I can search beach destinations from East Midlands Airport (EMA), departing 18 Sep 2026. |
| affirmative_after_destination_discovery_offer_executes_discovery | 2 | - | PASS | I found destination ideas for you. The strongest match is Erzurum, Turkey (ERZ) with a score of 70/100. |
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
| hotel_london_budget_date_followup | 2 | - | PASS | I searched hotels in London around London. I found 3 hotel option(s) using LiteAPI rates. |
| flight_to_london_default_origin_invariant | 1 | - | PASS | I have your destination as London Heathrow Airport (LHR) from East Midlands Airport (EMA). Please provide your exact departure and return dates. |
| clarification_destination_this_month_resume | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use East Midlands Airport (EMA) as your departure airport. |
| clarification_destination_this_month_resume | 2 | - | PASS | I'll use East Midlands Airport (EMA) as your origin and search for skiing destination ideas for 3 nights, departing 19 Aug 2026 and returning 22 Aug 2026. I fou |
| clarification_destination_day_only_resume | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use East Midlands Airport (EMA) as your departure airport. |
| clarification_destination_day_only_resume | 2 | - | PASS | I'll use East Midlands Airport (EMA) as your origin and search for skiing destination ideas for 3 nights, departing 15 Aug 2026 and returning 18 Aug 2026. I fou |
| clarification_hotel_date_resume | 1 | - | PASS | Sure - I can search hotels in London. When do you want to check in, and how many nights will you stay? |
| clarification_hotel_date_resume | 2 | - | PASS | I searched hotels in London around London. I found 3 hotel option(s) using LiteAPI rates. |
| clarification_loop_guard_non_answer | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use East Midlands Airport (EMA) as your departure airport. |
| clarification_loop_guard_non_answer | 2 | - | PASS | I'll use East Midlands Airport (EMA) as your origin. When do you want to depart? To keep this moving, you can choose one of these: 27 Aug 2026, 02 Sep 2026, 09  |
| clarification_rejects_past_date | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use East Midlands Airport (EMA) as your departure airport. |
| clarification_rejects_past_date | 2 | - | PASS | I'll use East Midlands Airport (EMA) as your origin. When do you want to depart? To keep this moving, you can choose one of these: 27 Aug 2026, 02 Sep 2026, 09  |
| clarification_rejects_malformed_date | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use East Midlands Airport (EMA) as your departure airport. |
| clarification_rejects_malformed_date | 2 | - | PASS | I'll use East Midlands Airport (EMA) as your origin. When do you want to depart? To keep this moving, you can choose one of these: 27 Aug 2026, 02 Sep 2026, 09  |
| flexible_clear_flight_birmingham_paris_dates | 1 | - | PASS | I found real economy flight options from BHX to CDG for 1 passenger using Duffel. |
| flexible_clear_hotel_london_dates | 1 | - | PASS | I found 3 hotel option(s) using LiteAPI rates. |
| flexible_broad_skiing_trip_asks_for_date | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use East Midlands Airport (EMA) as your departure airport. |
| flexible_package_region_missing_details | 1 | - | PASS | I can help build a Florida package from Birmingham Airport (BHX). Florida has several main airport choices, including Orlando, Miami, Tampa and Fort Lauderdale. |
| flexible_destination_clarification_day_answer | 1 | - | PASS | I can help you find skiing destinations for a 3-night trip. When do you want to depart? I'll use East Midlands Airport (EMA) as your departure airport. |
| flexible_destination_clarification_day_answer | 2 | - | PASS | I'll use East Midlands Airport (EMA) as your origin and search for skiing destination ideas for 3 nights, departing 19 Aug 2026 and returning 22 Aug 2026. I fou |
| flexible_visible_package_cheapest_and_second_cart | 1 | - | PASS | Here are the cheapest package options I currently have. |
| flexible_visible_package_cheapest_and_second_cart | 2 | - | PASS | I added package option 2 to your cart. The total is GBP 390.00. You can now proceed to checkout. |
| flexible_book_it_uses_current_package_selection | 1 | - | PASS | I added package option 1 to your cart. The total is GBP 420.00. You can now proceed to checkout. |
| flexible_explicit_destination_correction_with_dates | 1 | - | PASS | I have your destination as Miami International Airport (MIA) from Birmingham Airport (BHX), travelling from 15 Nov 2026 to 19 Nov 2026. Would you like me to bui |
| flexible_invalid_candidate_rejected | 1 | - | PASS | I can help build a Atlantis package from Birmingham Airport (BHX). November is a broad date range, and I need a city or airport in Atlantis before searching. Pl |
| flexible_unrelated_non_travel_no_provider_calls | 1 | RESPONSE_STATE_MISMATCH | FAIL | I can use East Midlands Airport (EMA) as your origin and Mehamn (MEH) as your destination. Please provide your departure date and return date. |
| florida_birmingham_november_fresh | 1 | - | PASS | I can help build a Florida package from Birmingham Airport (BHX). Florida has several main airport choices, including Orlando, Miami, Tampa and Fort Lauderdale. |
| florida_birmingham_november_fresh | 2 | - | PASS | I have your destination as Orlando International Airport (MCO) from Birmingham Airport (BHX), travelling from 15 Nov 2026 to 19 Nov 2026. Would you like me to b |
| florida_birmingham_november_fresh | 3 | - | PASS | I built package options for your 4-night Orlando trip from Birmingham Airport (BHX), departing 15 Nov 2026 and returning 19 Nov 2026. The cheapest package is GB |
| florida_birmingham_november_polluted | 1 | - | PASS | I can help build a Florida package from Birmingham Airport (BHX). Florida has several main airport choices, including Orlando, Miami, Tampa and Fort Lauderdale. |
| florida_birmingham_november_polluted | 2 | - | PASS | I have your destination as Orlando International Airport (MCO) from Birmingham Airport (BHX), travelling from 15 Nov 2026 to 19 Nov 2026. Would you like me to b |
| florida_birmingham_november_polluted | 3 | - | PASS | I built package options for your 4-night Orlando trip from Birmingham Airport (BHX), departing 15 Nov 2026 and returning 19 Nov 2026. The cheapest package is GB |
| package_budget_followup_preserves_confirmed_trip | 1 | - | PASS | I found 1 package within your GBP 800 budget. The cheapest package is GBP 740.00 from 18 Sep 2026 to 23 Sep 2026. The package route is MAN to BCN. |

## Notes

This evaluator runs the same Voyager chat/state pipeline used by `/chat`.
Mocked mode replays live-shaped tool outputs. Live mode calls the local Ollama
router and stubs travel providers so the report focuses on tool routing, schema
validity, state merge correctness, pending-action handling, provider execution
gates, and response text checks.
