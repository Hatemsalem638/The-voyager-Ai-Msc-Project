# Voyage AI Benchmark Failure Analysis

This table classifies the failed cases from the first benchmark run before benchmark scoring and small parser fixes.

| Case | Category | User messages | Expected result | Actual result | Reason for failure | Recommended action |
| --- | --- | --- | --- | --- | --- | --- |
| flight_003 | Benchmark scoring bug | London trip with GBP 1000 budget, then dates | Flight search with budget stored | Flight search succeeded, budget scored as missing | Runner read `budgetMax` but app stores `budget` | Fixed runner slot extraction |
| flight_004 | Provider/mock mismatch | Direct East Midlands to New York with dates | EMA to JFK flights | Legacy mock path returned 0 options | Offline benchmark bypassed Llama and did not reach deterministic provider | Added benchmark Llama-style router |
| flight_005 | Benchmark scoring bug | EMA to Paris with dates and 2 passengers | EMA to CDG, 2 passengers | Flight search reached CDG, passenger scored as 1 | Runner read active-trip default before returned flight card | Fixed passenger slot extraction |
| hotel_003 | Real application bug | Accommodation in Paris from 12 September to 16 September | Hotel results for September dates | Asked for check-in and nights | Hotel-only parser detected city but ignored dates in same message | Fixed hotel date parsing |
| hotel_004 | Real application bug | Stays in Rome from 1 October to 4 October for 2 guests | Hotel results for Rome | Treated dates as trip clarification | `stays` was not recognised as hotel-only language; month date parsing was weak | Fixed `stays` and hotel date parsing |
| hotel_005 | Real application bug | Hotel in Barcelona for 3 nights, then from the 15th | Hotel results for 3 nights | Asked again for nights | Initial hotel pending state did not store nights | Fixed hotel pending request creation |
| package_001 | Real application bug | Full package to London within GBP 1000, then dates | Package results | Flight results only | Package intent is lost after date follow-up | Keep as remaining routing issue |
| package_002 | Benchmark scoring bug | Flight and hotel package with my budget | Clarification | Clarification text scored as package search | Runner treated `trip_clarification` as package search | Fixed clarification scoring |
| package_003 | Real application bug | Skiing for 3 nights, same day next week, Erzurum package | Package for ERZ | Package clarification | Destination/duration context is not preserved strongly enough in this flow | Keep as remaining package-context issue |
| package_005 | Real application bug | Travel package to Paris from 12 September to 15 September | Paris package in September | Trip clarification with August dates | One-shot package date/location parsing is too loose | Keep as remaining package date/location issue |
| package_006 | Real application bug | Beach package for 4 nights, choose Hurghada, date, package | HRG package with 4-night return date | Asked for return/nights again | Duration is lost between destination discovery and package request | Keep as remaining package-context issue |
| context_001 | Real application bug | Paris hotel with dates, then cheapest hotel | Cheapest hotel context | Hotel clarification | Same root cause as hotel_003 | Fixed through hotel parser |
| context_002 | Test case expectation problem | Flight results, then “i like this flight” | Select current flight | Asked which option | Multiple flights were shown, so asking which option is acceptable | Adjusted expectation to clarification |
| context_003 | Benchmark scoring bug | Package flow, then confirm package 2 | Package option added to cart | Package options were rebuilt / nextPath missing | `confirm package` phrase was unsupported and metadata expectation was too strict | Added phrase support and relaxed `nextPath` |
| context_005 | Real application bug | Skiing, choose place first, then not in Europe | Destination discovery with Europe excluded | General help | Negative region follow-up still misses some dateless discovery state | Keep as remaining follow-up issue |
| context_006 | Test case expectation problem | Skiing, choose place first, then Beirut | Immediate flights | Asked for dates | Because user chose destination before date, asking for dates is acceptable | Adjusted intent expectation; duration loss remains |
| voice_001 | Benchmark scoring bug | Misrecognised “live” with dates | Clarification | Clarification text scored as package search | Runner labelled date clarification as package search | Fixed clarification scoring |
| voice_003 | Real application bug | Package results, then “option option one” | Package added to cart | Flight option selected | Ambiguous voice-style option text can bind to flight inside package data | Keep as remaining context-priority issue |
| voice_004 | Benchmark scoring bug | Hotel results, then “option option one” | Hotel selected | Hotel selected but option number scored missing | Runner did not parse selected option from reply | Fixed selected-option scoring |
| cart_001 | Test case expectation problem | Flight results, then select option 1 | Cart update | Flight selected, asks to add to cart | Selecting a flight is not the same as adding it to cart | Adjusted expectation |
| cart_002 | Real application bug | Hotel results, select option, yes book it | Hotel cart update | Hotel clarification | Same root cause as hotel date parser | Fixed through hotel parser |
| cart_003 | Test case expectation problem | Package cart, then checkout as guest | `/checkout` | `/profile?next=/checkout` | Guest checkout correctly requires sign-in | Adjusted expectation |
| cart_004 | Benchmark scoring bug | Package cart, yes book it, thank you | Cart remains available | Cart update scored too strictly by `nextPath` | Reply confirms cart state without returning `/cart` metadata | Relaxed metadata expectation |

## Final Remaining Failures

The final benchmark keeps the remaining failures visible instead of hiding them. They are mainly package-context routing issues and one voice-style option-priority issue.
