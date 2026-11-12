# Testing Strategy

| Version | Status | Last updated |
|---|---|---|
| 0.3.1 | Corrected review, for team sign-off | 2026-11-12 |

How we prove financial correctness and state integrity. Test IDs map to the [Acceptance Criteria](ACCEPTANCE_CRITERIA.md). Priority is financial correctness first, then state integrity, then authorisation, then experience.

## 1. Priorities

| Level | Meaning |
|---|---|
| P0 | Financial correctness and state integrity. Blocks release |
| P1 | Authorisation, evidence integrity, deadlines, notices |
| P2 | UX, notifications, performance |

Test data uses NGN kobo, Nigerian states and realistic parties: Ibrahim Traders (Lagos) selling to Chinedu Okafor (Port Harcourt); five Galaxy A15 phones at `22000000` kobo each.

## 2. Layers

| Layer | Scope | Notes |
|---|---|---|
| Unit | Allocation and rounding, deadline computation, transition guards, agreement hashing, approval thresholds, lot splitting | Pure functions |
| State machine | Every row in the [State Machine](../03-technical-specs/TRANSACTION_STATE_MACHINE.md) tables generated as table-driven tests: legal transition produces the expected DB and ledger effect; every other (state, event, actor) combination is rejected with no writes | P0. The tables are the single source for code and tests |
| Integration | Services with real PostgreSQL: row locks, partial unique indexes, exclusion constraint on lots, triggers (append-only, journal balance, return timestamps set once), outbox | Parallel connections for concurrency |
| API | Every endpoint: success, each documented error, validation, authorisation, idempotency, envelope shape; OpenAPI contract tests | |
| Payment adapter | One shared contract suite run against the simulator and any real sandbox | See section 6 |
| Webhook | Signature, replay, duplicates, out of order, malformed | |
| Authorisation | Generated matrix: endpoint × role × ownership × state | P1 |
| Dispute and review workflow | Evidence windows, requests, waivers, decisions, approvals | |
| Partial quantity and refund | Property-based tests over random valid event sequences | P0 |
| Freeze, approval and amount rules | Creation guard, lot freezes, threshold boundary, approver identity, amount-equals-allocation triggers (section K) | P0 |
| Failure and recovery | Provider outage, timeouts, worker crash mid-job, double scheduler run, DB failover | Fault injection in the simulator |
| End to end | Browser flows on staging with the simulator, mobile viewport included | |
| Non-functional | Load, slow-network uploads, accessibility | P2 |

## 3. Global invariants (asserted after every integration and end-to-end test)

1. Every `journal_id` sums to zero.
2. For every lot `released + refunded + pending_payout + pending_refund <= allocated`.
3. No lot has both a payout and a refund (settled or pending).
4. Paid out plus refunded never exceeds total received.
5. Lots of each item cover exactly `1..quantity` without overlap.
6. For each decision `accepted + rejected <= confirmed-received quantity`; for each return `confirmed + contested <= returned <= rejected`.
7. No ledger or audit row has been updated or deleted (attempts fail).
8. Each instruction has one idempotency key and at most one provider reference.
9. Every state change has an audit row with actor and timestamp.
10. `dispatch_deadline_at`, `handover_evidence_uploaded_at`, `confirmation_deadline_at` are unchanged after being set.
11. No instruction exists on a frozen lot except one with `origin_type = DISPUTE_DECISION` of that lot's freezing dispute; no instruction is creatable or submittable while a `PROVIDER_UNKNOWN` or `RECONCILIATION` freeze exists.
12. Every `RELEASE` and `REFUND` amount equals the computed lot allocation amount.
13. Every instruction at or above the threshold (or flagged) has an approval by a different `SENIOR_MODERATOR` before any authorisation journal exists; a `PENDING_APPROVAL` instruction has no ledger entries.
14. Every dispatch has a supplier delivery-photo requirement and every receipt confirmation a buyer requirement; no requirement transition changes a lot status.

## 4. Scenarios and expected results

### A. Agreement and payment

| ID | Scenario | Expected | AC |
|---|---|---|---|
| TS-A1 | Pay before agreeing to the current version | `409`/`422`; no attempt created | AC-01 |
| TS-A2 | Supplier edits terms after buyer agreed, before payment | New version; buyer agreement voided; payment blocked until re-agreed | AC-01 |
| TS-A3 | Terms edit after first attempt | `409 TERMS_LOCKED` | AC-01 |
| TS-A4 | Verified collection and secured hold, ₦1,100,000 | `FUNDS_SECURED`, `FUNDED`, `dispatch_deadline_at = funded_at + 72h`, lots `AWAITING_DISPATCH`, ledger J1 | AC-02 |
| TS-A5 | Collection succeeded but hold not secured | `PAYMENT_RECEIVED`, transaction `AWAITING_PAYMENT`, no dispatch notice, exception | AC-02 |
| TS-A6 | Browser redirect with no webhook | Not funded; UI "Confirming payment" | AC-02 |
| TS-A7 | Forged webhook | `401`, flagged, no state change | AC-02 |
| TS-A8 | Valid webhook but status query says failed or amount differs | No funding, exception | AC-02 |
| TS-A9 | Same webhook delivered 5 times | One effect | AC-15 |
| TS-A10 | Provider timeout on status | `UNKNOWN`; no funding; resolver retries; no new attempt key | AC-16 |
| TS-A11 | Late success after attempt expiry, transaction still awaiting payment | Funded once, ledger consistent | AC-16 |
| TS-A12 | Late success after cancellation | Exception, manual approved refund, no auto refund | AC-16 |
| TS-A13 | Double click pay with same idempotency key | One attempt | AC-15 |
| TS-A14 | Production mode with a provider whose `capabilities()` lacks conditional holding, or with the real-money flag unset | No `FUNDS_SECURED`, no instruction submitted; simulated mode unaffected | AC-23 |

### B. Dispatch

| ID | Scenario | Expected | AC |
|---|---|---|---|
| TS-B1 | Dispatch of all 5 at 71h59m | Lots `DISPATCHED`; no dispute | AC-03 |
| TS-B2 | Dispatch of 3 of 5 before deadline, none for the rest | At 72h one `NO_DISPATCH` dispute for units 4-5 only; units 1-3 continue; no ledger movement | AC-03, AC-18 |
| TS-B3 | Scanner runs twice concurrently | Exactly one dispute | AC-03, AC-15 |
| TS-B4 | Late dispatch via the dispatch endpoint | `409 DISPATCH_DEADLINE_PASSED` with `dispute_id` | AC-03 |
| TS-B5 | Late dispatch evidence posted to the dispute | Stored as dispute evidence; does not remove the dispute | AC-03 |
| TS-B6 | Config changed 72h to 48h after funding | In-flight transaction keeps 72h (snapshot) | AC-03 |
| TS-B7 | Dispute decided `REFUND` after window (units 4 and 5, `44000000` kobo) | Refund created once through the authorised exception, `QUEUED` (below threshold); lots stay `IN_DISPUTE`, frozen, `REFUND_PENDING`; executes once; ledger balanced; freeze released on success | AC-14, AC-20 |

### C. Delivery photographs and receipt

| ID | Scenario | Expected | AC |
|---|---|---|---|
| TS-C1 | Supplier submits delivery photographs against their requirement | Requirement `SUBMITTED`; evidence linked; no lot status change | AC-04, AC-19 |
| TS-C2 | Buyer submits delivery photographs against their requirement | Requirement `SUBMITTED`; no lot status change | AC-04, AC-19 |
| TS-C3 | Supplier calls `confirm-received` | `403` | AC-05 |
| TS-C4 | Buyer confirms receipt of 3 of 5 | Those lots `RECEIPT_CONFIRMED`; buyer requirement `PENDING`; inspection deadline only if the rule is approved | AC-05 |
| TS-C5 | Confirm quantity exceeding dispatched | `422 QUANTITY_EXCEEDS_AVAILABLE` | AC-05 |
| TS-C6 | Both parties upload photographs but the buyer never confirms receipt | Lots stay `DISPATCHED`: photographs do not establish receipt | AC-04, AC-05 |
| TS-C7 | Buyer submits acceptance, then rejection, without delivery photographs | `422 DELIVERY_PHOTOS_REQUIRED` both times; no lot or money change | AC-19 |
| TS-C8 | Supplier requirement due date | `dispatched_at + expected_delivery_days + 24h`; moved earlier to receipt confirmation + 24h when the buyer confirms receipt first; never later | AC-19 |
| TS-C9 | Supplier misses `due_at` | Requirement `OVERDUE`; both parties and moderators notified; no dispute, payout, refund, acceptance or rejection created | AC-19 |
| TS-C10 | Moderator confirms while the supplier requirement is `PENDING` | `409 SUPPLIER_DELIVERY_PHOTOS_PENDING` | AC-19 |
| TS-C11 | Moderator confirms while `OVERDUE` without acknowledgement, then with acknowledgement and reason | `422 ACKNOWLEDGEMENT_REQUIRED`, then success with the acknowledgement audited | AC-19 |
| TS-C12 | Supplier submits after the due date | `SUBMITTED_LATE`; no status change; confirm no longer needs acknowledgement | AC-19 |
| TS-C13 | Buyer passes `inspection_deadline_at` without photographs | Requirement `OVERDUE`; inspection-expiry review opens; nothing accepted, rejected, paid or refunded | AC-19 |
| TS-C14 | Buyer posts photographs against the supplier's requirement (and the reverse) | `403 FORBIDDEN` | AC-19 |

### D. Acceptance and rejection

| ID | Scenario | Expected | AC |
|---|---|---|---|
| TS-D1 | Accept with no evidence | `422 EVIDENCE_REQUIRED` | AC-06 |
| TS-D2 | Accept 3 with photos | `SUBMITTED_FOR_REVIEW`; both parties notified; window `OPEN`; no payout | AC-06 |
| TS-D3 | Moderator confirms while window open | `409 EVIDENCE_WINDOW_OPEN` | AC-06, AC-14 |
| TS-D4 | Only buyer waives | Window stays open | AC-14 |
| TS-D5 | Both waive | Window `CLOSED_WAIVED`; moderator may confirm | AC-06 |
| TS-D6 | Window elapses; moderator confirms with reason (supplier photographs `SUBMITTED`) | Lots `ACCEPTED_CONFIRMED`; one payout created with amount = allocation: `PENDING_APPROVAL` when at or above the threshold, else `QUEUED`; no ledger entry while `PENDING_APPROVAL` | AC-06, AC-21 |
| TS-D7 | Moderator requests evidence | `EVIDENCE_REQUESTED`; confirm blocked until due date or response | AC-06 |
| TS-D8 | Moderator opens dispute | Decision `DISPUTED`; lots `IN_DISPUTE` | AC-06 |
| TS-D9 | Moderator is a party | `403 MODERATOR_IS_PARTY` | AC-06 |
| TS-D10 | Reject without `agreed_condition_ids` | `422 REJECTION_REASON_NOT_IN_AGREEMENT` | AC-07 |
| TS-D11 | Reject citing a condition from another transaction | Same error | AC-07 |
| TS-D12 | Reject with valid condition | `REJECTED_AWAITING_RETURN`; funds held; no refund instruction | AC-08 |
| TS-D13 | Accept + reject exceed confirmed-received quantity | `422 QUANTITY_EXCEEDS_AVAILABLE` | AC-13 |
| TS-D14 | Duplicate submission | Idempotent replay or `409 ITEM_ALREADY_DECIDED` | AC-15 |
| TS-D15 | Inspection period passes with no buyer decision | Inspection-expiry review opens; nothing accepted, rejected, paid or refunded | AC-23 |

### E. Return and refund

| ID | Scenario | Expected | AC |
|---|---|---|---|
| TS-E1 | Open return, upload nothing | No timer; reminder only | AC-09 |
| TS-E2 | Handover evidence validated at 10:05:00Z | `confirmation_deadline_at = 10:05:00Z + 72h` | AC-09 |
| TS-E3 | Additional evidence on day 2 | Deadline unchanged | AC-09 |
| TS-E4 | Unrelated evidence uploaded, handover submission invalid | No timer | AC-09 |
| TS-E5 | Supplier confirms 1 of 2 | Only that unit `RETURN_RECEIPT_CONFIRMED`; refund for that unit; other unit keeps original timer | AC-10 |
| TS-E6 | Supplier confirms all returned units (₦440,000) | Return `RECEIPT_CONFIRMED`; refund created `QUEUED` (below threshold); executes once | AC-10 |
| TS-E7 | No confirmation at 72h | One `RETURN_NOT_CONFIRMED` dispute; no refund; notices to both | AC-11 |
| TS-E8 | Expiry while parcel in transit | Dispute opens; case file shows tracking; no fault inferred | AC-11 |
| TS-E9 | Supplier contests at hour 5 | Dispute opens immediately; no refund | AC-12 |
| TS-E10 | Contest of 1 unit plus 72h expiry of another | Single dispute with two `dispute_items` | AC-11, AC-12 |
| TS-E11 | Supplier confirms after dispute exists | Confirmation added as evidence; moderator decides | AC-11 |
| TS-E12 | Supplier confirms a return worth ₦1,100,000 (all 5 units) | Refund created `PENDING_APPROVAL`; no ledger entry; after approval by a different senior moderator `QUEUED`, executes once | AC-10, AC-21 |

### F. Disputes

| ID | Scenario | Expected | AC |
|---|---|---|---|
| TS-F1 | Dispute opened | Both notified; window `OPEN`; affected lots frozen | AC-14 |
| TS-F2 | Decide before window closes | `409 EVIDENCE_WINDOW_OPEN` | AC-14 |
| TS-F3 | Decide with open evidence request | `409 EVIDENCE_REQUEST_OPEN` | AC-14 |
| TS-F4 | Extend window twice | Second extension `409` | AC-14 |
| TS-F5 | Decision without reason or with short reason | `422` | AC-17 |
| TS-F6 | Decision whose instruction is at or above the threshold | Dispute `AWAITING_SECOND_APPROVAL`; instruction `PENDING_APPROVAL`; no ledger entry; nothing submitted | AC-17, AC-21 |
| TS-F7 | Deciding moderator approves their own instruction | `403 SELF_APPROVAL_NOT_ALLOWED`; a non-senior moderator or an admin also gets `403` | AC-17, AC-21 |
| TS-F8 | Decision carries an `amount_kobo` different from the allocation (higher or lower) | `422 AMOUNT_MUST_MATCH_ALLOCATION` with `expected_amount_kobo` | AC-13, AC-22 |
| TS-F9 | Two moderators decide simultaneously | One succeeds, one `409` | AC-15 |
| TS-F10 | Attempt to edit a recorded decision | No endpoint; a new decision supersedes; both retained | AC-17 |
| TS-F11 | Different senior moderator approves the `PENDING_APPROVAL` instruction | Instruction `QUEUED`; authorisation journal posted; dispute `DECIDED` | AC-21 |
| TS-F12 | Senior moderator declines | Instruction `CANCELLED`; no ledger entry to unwind; dispute back to `MODERATOR_REVIEW`; lots remain frozen; a new decision supersedes | AC-20, AC-21 |

### G. Instruction failure and recovery

| ID | Scenario | Expected | AC |
|---|---|---|---|
| TS-G1 | Provider definitively rejects payout | `FAILED`; lot stays pending with its reservation and authorisation journal; alert; no automatic retry or destination change | AC-16 |
| TS-G2 | Timeout during payout | `UNKNOWN`; lot frozen; no second payout; resolver uses original reference | AC-16 |
| TS-G3 | Resolver finds success | `SUCCEEDED`; `PAYOUT_SENT` posted once | AC-16 |
| TS-G4 | Senior moderator retries after confirmed failure | Old authorisation journal is reversed and reservation released before a new linked instruction (`retry_of_id`, new key) is created; same origin, lots, amount and destination snapshot; full guard and approval rules run again; no duplicate payable/refundable balance | AC-16, AC-17 |
| TS-G5 | Retry while `UNKNOWN` or while a provider-unknown/reconciliation freeze remains | `409 OUTCOME_UNKNOWN_RECONCILE_FIRST`; no reversal, new instruction or provider call | AC-16 |
| TS-G6 | Kill switch on | Instructions stay `QUEUED`; no provider calls | AC-16 |
| TS-G7 | Supplier changes bank account during pending payout | Payout uses destination snapshot | AC-16 |

### H. Reconciliation

| ID | Scenario | Expected | AC |
|---|---|---|---|
| TS-H1 | Provider payout with no ledger record | Exception; lots frozen; alert | AC-16 |
| TS-H2 | Ledger success with no provider record after lag | Exception | AC-16 |
| TS-H3 | Amount mismatch | Exception; resolved only by compensating entry | AC-17 |
| TS-H4 | Clean day | Report stored | AC-16 |

### I. Authorisation and security

| ID | Scenario | Expected | AC |
|---|---|---|---|
| TS-I1 | Buyer calls any `/mod/*` route | `403` | AC-17 |
| TS-I2 | User requests another user's transaction | `404` | AC-17 |
| TS-I3 | Supplier calls acceptance submission | `403` | AC-06 |
| TS-I4 | Update or delete a ledger row as the application database role | Denied by database permission | AC-17 |
| TS-I5 | Reused refresh token | All sessions revoked | AC-24 |
| TS-I6 | Upload an executable renamed `.jpg` | `415` after content sniffing | AC-24 |
| TS-I7 | Expired signed evidence URL | Denied | AC-24 |
| TS-I8 | Admin attempts instruction retry | `403` | AC-17 |

### J. Quantity-level and property tests

| ID | Scenario | Expected | AC |
|---|---|---|---|
| TS-J1 | 5-phone line: accept 3, reject 2 (full flow in [Financial Engine 7](../03-technical-specs/FINANCIAL_ENGINE.md#7-worked-example-five-phones-accept-3-reject-2)) | Moderator A confirms: payout `66000000` kobo `PENDING_APPROVAL` (above threshold), no ledger entry; senior moderator B approves: `QUEUED`, authorisation journal; submitted; provider success: `SETTLED_RELEASED`. Supplier confirms return: refund `44000000` kobo `QUEUED` directly (below threshold), then `SETTLED_REFUNDED`. Held `0`, 5 balanced journals, transaction `COMPLETED` | AC-13, AC-21 |
| TS-J2 | Lot split and merge ranges after multiple partial actions | Ranges never overlap; coverage `1..quantity` | AC-13 |
| TS-J3 | Fee remainder: `10000` kobo over 3 units | Shares `3333 + 3333 + 3334`; any contiguous split sums exactly | AC-13 |
| TS-J4 | Attempt refund of a lot already paid out | Rejected by index and trigger | AC-13 |
| TS-J5 | Property test: random valid event sequences over 1 to 10 lines | All global invariants hold after each step; 10,000 sequences | AC-13, AC-15 |
| TS-J6 | Fee policy unset in production mode | Refund instruction refused | AC-24 |

### K. Freeze, approval and amount rules

| ID | Scenario | Expected | AC |
|---|---|---|---|
| TS-K1 | Dispute `NO_DISPATCH` on units 4-5; window closes; moderator records `REFUND` for the two units | Refund created through the authorised exception, `QUEUED`, amount `44000000` computed by the backend; lots stay `IN_DISPUTE`, frozen, `REFUND_PENDING`; on provider success `SETTLED_REFUNDED` and the freeze is released | AC-20 |
| TS-K2 | While K1's refund is `QUEUED`, create an unrelated payout for the same lots | `409 LOT_FROZEN`; no instruction exists | AC-20 |
| TS-K3 | While frozen, the supplier confirms receipt of a return for the same lots (ordinary refund) | `409 LOT_FROZEN` | AC-20 |
| TS-K4 | A second dispute or a decision of a different dispute tries to instruct the lots | `409 DISPUTE_ALREADY_OPEN` or `409 LOT_FROZEN`; unique non-terminal instruction per lot | AC-20 |
| TS-K5 | A `PROVIDER_UNKNOWN` or `RECONCILIATION` freeze is added to a dispute-frozen lot | New creation refused; a `QUEUED` instruction is not submitted until the freeze is released | AC-20, AC-16 |
| TS-K6 | Decision recorded before the evidence window is closed or waived | `409 EVIDENCE_WINDOW_OPEN`; no instruction | AC-14, AC-20 |
| TS-K7 | Declined authorised instruction | Instruction `CANCELLED`; lots remain frozen; dispute `MODERATOR_REVIEW`; no ledger entries; new decision supersedes | AC-20 |
| TS-K8 | Direct database insert of an instruction on a dispute-frozen lot with `origin_type` other than the freezing dispute's decision | Rejected by trigger | AC-20 |
| TS-K9 | Threshold boundary: instruction of `49999999` and of `50000000` kobo | `QUEUED` and `PENDING_APPROVAL` respectively; the database refuses `QUEUED` creation at or above the threshold | AC-21 |
| TS-K10 | `PENDING_APPROVAL` payout | No ledger entries until approval; approval posts exactly one authorisation journal; no provider call before `QUEUED` | AC-21 |
| TS-K11 | Approval by: the deciding moderator, an unrelated non-senior moderator, an admin, a different senior moderator | `403`, `403`, `403`, success | AC-21 |
| TS-K12 | One approving action over several lots | One payout and at most one refund, covering all lots; creating two payouts to stay below the threshold is refused | AC-21 |
| TS-K13 | `decision_outcomes`, `payout_items` or `refund_items` with an amount that differs from the allocation, inserted through the API and directly | `422 AMOUNT_MUST_MATCH_ALLOCATION`; trigger rejection | AC-22 |
| TS-K14 | Mixed decision: `RELEASE` 3 units, `REFUND` 2 units of one line | Lot split by quantity; amounts `66000000` and `44000000` computed; each sub-lot gets its own authorised instruction | AC-13, AC-22 |
| TS-K15 | Decision attempting a price reduction on one unit (for example half price) | `422 AMOUNT_MUST_MATCH_ALLOCATION`; no adjustment path exists | AC-22 |
| TS-K16 | A `NO_FINANCIAL_ACTION` decision | Freeze released, prior status restored, no instruction, no ledger entry | AC-20 |

## 5. Failure and recovery tests

Inject: provider downtime, slow responses, worker crash between instruction creation and submission, database failover, both workers running the scanner, duplicate outbox delivery. Expected in every case: no duplicated money movement, no lost audit rows, state recoverable by the resolver.

## 6. Payment adapter contract tests

The same suite runs against the simulator always and against a real sandbox when it exists. Cases: success, explicit failure, pending then success, pending forever, 5xx, timeout, malformed response, duplicate event, out-of-order events, unexpected amount, collection success with hold not secured, partial payout, partial refund. Each case must normalise to `SUCCESS`, `FAILED`, `PENDING`, `SECURED`, `NOT_SECURED` or `UNKNOWN`, never raise provider-specific types into the engine, and store the raw response.

## 7. Exit criteria

All P0 pass; no open P0 or P1 defects; transition-table coverage 100%; invariants pass in the 10,000-sequence property test; authorisation matrix generated and passing; reconciliation clean in the sandbox for 14 days (for real-money launch only).

## 8. Related documents

[Acceptance Criteria](ACCEPTANCE_CRITERIA.md) · [State Machine](../03-technical-specs/TRANSACTION_STATE_MACHINE.md) · [Financial Engine](../03-technical-specs/FINANCIAL_ENGINE.md)
