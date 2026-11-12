# Acceptance Criteria

| Version | Status | Last updated |
|---|---|---|
| 0.3.1 | Corrected review, for team sign-off | 2026-11-12 |

The mandatory conditions before implementation is considered complete. These are the v0.2 section 12 tests in Given/When/Then form, with traceability to rules, requirements and tests.

## 1. Mandatory acceptance criteria

| ID | Criterion | Rules / FR | Tests |
|---|---|---|---|
| AC-01 | Given a buyer who has not agreed to the exact current agreement version, when they try to pay, then the API refuses and no payment attempt exists. | SK-01, SK-03, FR-04, FR-06 | TS-A1, TS-A2, TS-A3 |
| AC-02 | Given a payment attempt, when the provider reports collection but backend verification does not confirm secured funds, then the transaction is not `FUNDED` and the supplier is not told to dispatch. A browser redirect, client claim or unverified webhook never funds a transaction. | SK-04, FR-07 | TS-A4 to TS-A8 |
| AC-03 | Given a funded transaction, when 72 hours pass after `funded_at` with any quantity undispatched, then exactly one dispute opens for the outstanding quantity and no payout, refund or cancellation occurs. Late evidence goes to the dispute and does not bypass it. | SK-05, SK-06, FR-10, FR-12 | TS-B1 to TS-B6 |
| AC-04 | Given dispatched goods, when the buyer or the supplier submits delivery photographs against their own requirement, then the photographs are stored as evidence of that party and no status changes. Photographs never establish physical receipt: lots stay `DISPATCHED` until the buyer explicitly confirms receipt. | SK-08, FR-13 | TS-C1, TS-C2, TS-C6 |
| AC-05 | Given dispatched quantity, when the buyer explicitly confirms physical receipt for a line and quantity, then that quantity becomes `RECEIPT_CONFIRMED` and the configured inspection period starts. A photograph alone never does this and a supplier cannot do it. | SK-08, FR-14 | TS-C3 to TS-C5 |
| AC-06 | Given received goods, when the buyer submits acceptance, then it requires evidence and the buyer's delivery photographs, both parties are notified, an evidence window opens, the quantity is `SUBMITTED_FOR_REVIEW`, no payout exists, and only after the window closes or both waive (and the supplier's delivery-photograph requirement is satisfied or acknowledged as overdue) can a moderator (not a party) confirm with a recorded reason, which creates the payout instruction (`PENDING_APPROVAL` or `QUEUED` by amount). | SK-09, SK-17, FR-15 to FR-18 | TS-D1 to TS-D9, TS-D6, TS-I3 |
| AC-07 | Given a rejection without an `agreed_condition_id` from the agreed terms, when it is submitted, then the API rejects it with `REJECTION_REASON_NOT_IN_AGREEMENT`. | SK-10, FR-19 | TS-D10, TS-D11 |
| AC-08 | Given a valid rejection, when it is recorded, then funds remain held and no refund instruction is created. | SK-11, FR-19 | TS-D12 |
| AC-09 | Given a rejected quantity, when the buyer opens a return, then no timer runs; the 72-hour timer starts only when validated handover evidence is committed and is never reset by clicking Return, unrelated evidence or additional evidence. | SK-12, SK-14, FR-20, FR-21 | TS-E1 to TS-E4 |
| AC-10 | Given a return of several units, when the supplier confirms only part, then only that quantity becomes `RETURN_RECEIPT_CONFIRMED` and refund-eligible; the return is not marked fully confirmed. | SK-13, FR-22 | TS-E5, TS-E6 |
| AC-11 | Given no supplier confirmation 72 hours after handover evidence, then one dispute opens for the outstanding quantity and nothing is refunded automatically. The moderator must assess tracking and both sides' evidence; expiry is not proof of supplier fault. | SK-15, FR-24 | TS-E7, TS-E8, TS-E10, TS-E11 |
| AC-12 | Given a supplier contest (wrong item, damaged, quantity mismatch, not received), then a dispute opens immediately without waiting for the timer and no refund occurs. | SK-15, FR-23 | TS-E9, TS-E10 |
| AC-13 | Given a 5-unit line, when the buyer accepts 3 and rejects 2, then payout `66000000` kobo (₦660,000, above the threshold: `PENDING_APPROVAL`, approved by a different senior moderator, then `QUEUED`) and refund `44000000` kobo (below the threshold: `QUEUED`) reconcile exactly to lot allocations, accepted + rejected never exceeds confirmed-received quantity, and no kobo is created or lost by fee apportionment. | SK-16, FR-15, FR-19, FR-30 | TS-D13, TS-F8, TS-J1 to TS-J5, TS-K14 |
| AC-14 | Given a dispute or acceptance review, then both parties receive notice, can add evidence, and neither a moderator decision nor a financial instruction can finalise before the window closes or both waive and no requested-evidence deadline is open. | SK-17, FR-26, FR-27 | TS-B7, TS-D3, TS-D4, TS-F1 to TS-F4, TS-K6 |
| AC-15 | Given duplicate requests, duplicate webhooks, duplicate job runs or concurrent moderator actions, then no payout or refund is duplicated. | SK-16, FR-31 | TS-A9, TS-A13, TS-B3, TS-D14, TS-F9, TS-J5 |
| AC-16 | Given a provider timeout or ambiguous result, then the affected money is frozen, status is `UNKNOWN`, no retry or new idempotency key is used, and reconciliation resolves it. After a definitive failure, a senior moderator may retry only after the old authorisation journal is reversed and reservation released; the linked retry retains the same origin, lots, amount and destination snapshot and passes approval again. | SK-04, FR-09, FR-32 | TS-A10 to TS-A12, TS-G1 to TS-G7, TS-H1, TS-H2, TS-H4, TS-K5 |
| AC-17 | Given any moderator or admin, then nobody can edit or delete a ledger entry; every decision and approval is auditable; approval of an instruction is by a different senior moderator; corrections use compensating entries. | SK-18, FR-28 to FR-30, FR-33 | TS-F5, TS-F6, TS-F7, TS-F10 to TS-F12, TS-H3, TS-I1, TS-I2, TS-I4, TS-I8 |
| AC-18 | Given partial dispatch, then dispatched quantities continue to delivery while only undispatched quantity is disputed at 72 hours. | FR-11, [OD-03](../06-delivery/OPEN_DECISIONS.md) | TS-B2 |
| AC-19 | Given a dispatch and a receipt confirmation, then the supplier and the buyer each have a delivery-photograph requirement; the buyer cannot accept or reject until theirs is submitted; a supplier requirement that is `PENDING` blocks moderator confirmation and an `OVERDUE` one needs an audited acknowledgement; a missed requirement never creates a payout, refund, acceptance, rejection or dispute automatically; photographs never change a lot status. | SK-08, FR-13, FR-37, FR-38, [OD-16](../06-delivery/OPEN_DECISIONS.md) | TS-C1, TS-C2, TS-C7 to TS-C14 |
| AC-20 | Given a lot frozen by a dispute, then no unrelated payout, ordinary refund or other dispute's decision can create an instruction on it (`LOT_FROZEN`). The instruction executing an approved decision of that same dispute, recorded after the evidence window, is allowed; a retry of that instruction is allowed only through F10 after definitive failure and reversal of the prior authorisation journal. The freeze persists until success or a later decision releases it. Provider-unknown and reconciliation freezes allow no exception. | SK-06, SK-18, FR-40 | TS-B7, TS-F12, TS-G4, TS-K1 to TS-K8, TS-K16 |
| AC-21 | Given a payout or refund instruction, then one at or above the threshold (or flagged) is created `PENDING_APPROVAL` with no ledger entry, becomes `QUEUED` only after approval by a different senior moderator, and is then submitted and confirmed; one below it is created `QUEUED`; a single action creates at most one payout and one refund. | FR-29 | TS-D6, TS-E12, TS-F6, TS-F7, TS-F11, TS-F12, TS-J1, TS-K9 to TS-K12 |
| AC-23 | Given a deployment, then payment providers are reached only through the adapter; development and staging use the simulated provider; production mode cannot produce `FUNDS_SECURED` or submit instructions until the adapter reports every required capability and the real-money flag is set after the launch gates in [Payment Provider Integration](../04-operations/PAYMENT_PROVIDER_INTEGRATION.md#6-real-money-launch-gates) are met; buyer silence never creates acceptance, payout or refund. | SK-04, SK-09 | TS-A14, TS-D15 |
| AC-24 | **Given** sessions, evidence downloads and refunds, **then** a reused refresh token revokes the user's sessions, unsafe or expired evidence files and URLs are refused, and production refunds are refused while the fee policy is unset. | FR-36, [OD-05](../06-delivery/OPEN_DECISIONS.md) | TS-I5, TS-I6, TS-I7, TS-J6 |
| AC-22 | Given a quantity-based `RELEASE` or `REFUND`, then the amount equals the allocation of that quantity, computed by the backend; any other amount is rejected by the API and by database triggers; no partial-price adjustment exists. | SK-16, FR-39, [OD-14](../06-delivery/OPEN_DECISIONS.md) | TS-F8, TS-K13 to TS-K15 |

## 2. Definition of done for a feature

1. The relevant transition rows have table-driven tests and pass.
2. API contract tests pass for every documented success and error.
3. Global invariants hold after the feature's integration and end-to-end tests.
4. UI renders only `allowed_actions` and uses canonical status names.
5. Audit rows exist for every state change.
6. No status name outside the [canonical enums](../03-technical-specs/TRANSACTION_STATE_MACHINE.md#2-canonical-enums) appears in code, docs or tests.

## 3. Related documents

[Testing Strategy](TESTING_STRATEGY.md) · [Business Rules](../01-product/BUSINESS_RULES.md) · [Product Requirements](../01-product/PRODUCT_REQUIREMENTS.md)
