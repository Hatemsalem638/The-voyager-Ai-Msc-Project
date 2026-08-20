# Voyage AI Manual Workflow Tests

This checklist is intended for final dissertation/demo evidence. The tester should complete the `Actual result`, `Pass/Fail`, and `Screenshot needed` columns manually during the final run.

## Flight-Only Workflow

| Scenario | Steps | Expected result | Actual result | Pass/Fail | Screenshot needed |
| --- | --- | --- | --- | --- | --- |
| Flight-only workflow | 1. Open The Voyager. 2. Ask for a flight to London. 3. Provide dates if asked. 4. Review flight cards. 5. Select option 1. 6. Confirm adding it to cart. | Flight cards appear, option 1 can be selected, the assistant asks whether to add it to cart, and the item appears in the cart after confirmation. |  |  |  |

## Hotel-Only Workflow

| Scenario | Steps | Expected result | Actual result | Pass/Fail | Screenshot needed |
| --- | --- | --- | --- | --- | --- |
| Hotel-only workflow | 1. Open The Voyager. 2. Ask for a hotel in London Hyde Park. 3. Provide check-in and check-out dates. 4. Review hotel cards. 5. Select option 1. 6. Confirm adding it to cart. | Hotel cards appear, option 1 can be selected, the assistant asks whether to add it to cart, and the item appears in the cart after confirmation. |  |  |  |

## Package Workflow

| Scenario | Steps | Expected result | Actual result | Pass/Fail | Screenshot needed |
| --- | --- | --- | --- | --- | --- |
| Package workflow | 1. Ask for a full flight and hotel package to London. 2. Provide dates and budget if needed. 3. Review package cards. 4. Select package 2. 5. Continue toward checkout. | Package cards appear as package cards only, package 2 can be selected, and the selected package is added or prepared for the cart. |  |  |  |

## Checkout Workflow

| Scenario | Steps | Expected result | Actual result | Pass/Fail | Screenshot needed |
| --- | --- | --- | --- | --- | --- |
| Checkout workflow | 1. Open the cart. 2. Continue checkout. 3. Sign in if required. 4. Confirm checkout. 5. Open the booking details page. | The system creates an application booking reference. Booking details open, and ticket/voucher views are available. |  |  |  |

## Booking History Workflow

| Scenario | Steps | Expected result | Actual result | Pass/Fail | Screenshot needed |
| --- | --- | --- | --- | --- | --- |
| Booking history workflow | 1. Log in. 2. Open Profile/My Trips. 3. Review previous bookings. 4. Open one booking's details. | Previous bookings appear for the signed-in user, and the selected booking details page opens correctly. |  |  |  |

## Destination Discovery Workflow

| Scenario | Steps | Expected result | Actual result | Pass/Fail | Screenshot needed |
| --- | --- | --- | --- | --- | --- |
| Destination discovery workflow | 1. Ask for a beach or skiing destination. 2. Review destination recommendations. 3. Select one destination. 4. Ask for flights/hotels or a package. | The system recommends suitable destinations, stores the selected destination, and can continue into a package search. |  |  |  |

## Voice Workflow

| Scenario | Steps | Expected result | Actual result | Pass/Fail | Screenshot needed |
| --- | --- | --- | --- | --- | --- |
| Voice workflow | 1. Open The Voyager. 2. Enable voice mode. 3. Start voice input. 4. Speak a travel request. 5. Wait for transcript and assistant response. 6. Stop voice. | Transcript is sent to chat, the assistant gives a relevant response, the assistant speaks a useful summary, and Stop voice works. |  |  |  |

## Reset/Persistence Workflow

| Scenario | Steps | Expected result | Actual result | Pass/Fail | Screenshot needed |
| --- | --- | --- | --- | --- | --- |
| Reset and persistence workflow | 1. Use The Voyager. 2. Navigate to another page. 3. Return to The Voyager. 4. Reset chat. 5. Open the cart. | Chat remains after navigating pages. Reset chat clears chat state but does not clear the cart. |  |  |  |

## Thesis Interpretation Notes

- The manual workflow checklist supports end-to-end validation of the user journey.
- The checklist separates observed results from expected results so the evaluation evidence remains transparent.
- Screenshots should be captured manually during the final demonstration and referenced in the dissertation where appropriate.
