# Documentation Audit

| Version | Status | Last updated | Authority |
|---|---|---|---|
| 0.3.1 | Corrected review, for team sign-off | 2026-11-12 | [v0.2 specification](README.md#2-source-of-truth) overrides v0.1 |

Final consistency pass. Lists what was corrected, the checks that were run and their results, what was not verified, and the open items that remain.

## 1. Corrections in this pass

| Area | Correction |
|---|---|
| Ledger | Collection and hold are now separate ledger events. Accounts are `PROVIDER_CLEARING`, `RECEIVED_UNSECURED`, `HELD_FUNDS`, `SUPPLIER_PAYABLE`, `BUYER_REFUNDABLE` and `UNALLOCATED_OVERPAYMENT`; `BUYER_PAYMENTS_CLEARING` and `EXTERNAL_PAID_OUT` are gone. P2 posts `PAYMENT_RECEIVED`, P3 posts `FUNDS_SECURED`, and payout and refund success credit `PROVIDER_CLEARING`. The worked example was renumbered (J1 to J6) |
| Reservations | Defined as the lot's `pending_payout_kobo` and `pending_refund_kobo`, kept with the instruction items; the Financial Engine and Database now say the same thing |
| Failed and unknown instructions | F10 now reverses the prior authorisation journal and releases the reservation before either retrying as a linked instruction or releasing to dispute. An `UNKNOWN` outcome becomes `FAILED` only on a definitive provider answer or a senior moderator's `CONFIRM_NOT_EXECUTED` backed by the settlement report, so reconciliation always precedes a retry |
| Exception handling | `financial_exceptions` table added (it was referenced but undefined). Resolution is one senior-moderator endpoint, `POST /mod/financial-exceptions/{id}/resolve`. The admin endpoints for resolving reconciliation exceptions were removed, because admins have no financial execution rights |
| Freezes | Freeze creation and release write audit rows; `lot_freezes` records the creating and releasing event. The Financial Engine no longer restates the rule and points to State Machine 2.4 |
| Evidence | New file status `VERIFIED`: only verified files can be referenced by an action (`EVIDENCE_NOT_VERIFIED`) |
| API and database | `return_receipt_confirmations` added for partial supplier confirmations; delivery terms in `terms_json` stated; resource `version` for `If-Match` stated |
| Open decisions | Column changed from "Blocking" to "Needed by" (a phase, Launch, Tunable or Post-MVP). Added OD-17 (payout-account cool-down). OD-07 now states that aggregation across separate decisions is unresolved |
| Acceptance and tests | AC-23 (adapter-only providers, real money disabled, silence never accepts) with TS-A14 and TS-D15. Test ids prefixed `TS-` because the bare ids collided with transition ids |
| Style | Bold emphasis, em dashes and stock phrasing removed; document headers reduced to a version, status and date table; version set to 0.3.1 |

## 2. Verification performed

All of these are documentation checks run by script or by reading. **No application code, migrations or OpenAPI file exist, so nothing in the specifications has been executed, and no test has been run.** The test scenarios are specifications.

| Check | Result |
|---|---|
| Relative links and anchors | 299 checked, 0 broken |
| Status and error tokens against the canonical enums and API error table | No unregistered status or error code |
| Obsolete names (`UNDER_REVIEW`, `ACCEPTANCE_UNDER_REVIEW`, `DELIVERED_PENDING_DECISION`, `FUNDED_AWAITING_DISPATCH`, `SPLIT` outcome, `decision_approvals`, `frozen_reason`, `BUYER_PAYMENTS_CLEARING`, `EXTERNAL_PAID_OUT`) | Present only in "not allowed" lists and change notes |
| Endpoints, transition ids and configuration keys referenced in one document exist in the owning document | No dangling reference |
| Cross-references among 24 acceptance criteria, 115 test scenarios, functional requirements, rules and decisions | Every criterion has tests; every reference resolves |
| Financial path for each of the 77 transitions | All covered in [Financial Engine 9](03-technical-specs/FINANCIAL_ENGINE.md#9-financial-path-of-every-transition) |
| Approval threshold wording and examples | Consistent: at or above `50000000` kobo is `PENDING_APPROVAL`, below is `QUEUED` |
| Mermaid syntax | All 8 diagrams render with Mermaid CLI |

A subsequent manual review found two cross-document safety defects not covered by the checks above: retrying a definitively failed instruction did not reverse its prior authorisation journal, and the frozen-lot rules simultaneously prohibited retries that F10 permitted. These are corrected in this package's 0.3.1 review edits. The API also incorrectly stated that every 502/503 meant no money moved; it now distinguishes a confirmed pre-submit failure from an ambiguous post-submit outcome.

Not verified: Paga capabilities, legal position, real provider behaviour, database triggers and constraints as written, API contracts as machine-validated, and the usability of the screens.

## 3. Open decisions and implementation impact

The full register is in [Open Decisions](06-delivery/OPEN_DECISIONS.md). Implementation can start: every item has a default held in configuration. None of these defaults is a confirmed rule.

| Needed by | Decisions |
|---|---|
| Phase 1 | OD-17 payout-account cool-down |
| Phase 2 | OD-09 cancellation after funding (default: not supported after funding) |
| Phase 4 | OD-01 inspection period, OD-03 partial dispatch, OD-16 delivery-photo deadlines and consequences |
| Phase 5 | OD-02 evidence-window length, OD-04 handover deadline, OD-05 delivery fee treatment, OD-15 return timer start instant |
| Phase 6 | OD-07 approval threshold and aggregation, OD-10 redispatch mechanics |
| Launch | OD-05, OD-06 platform fee, OD-08 KYC, OD-11 retention, OD-13 moderator staffing and SLA |

## 4. External dependencies blocking real-money launch

1. EXT-01: written confirmation from Paga (or an alternative provider) of collection, conditional holding, partial payout and refund, status queries, idempotency, signed webhooks, reports and sandbox.
2. EXT-02: Nigerian legal advice on who may hold customer funds, licensing, KYC/AML, dispute liability and customer-facing claims.
3. EXT-03: provider fees, limits and settlement times.

Until these clear, real-money mode stays disabled and the simulated provider is used ([Payment Provider Integration 6](04-operations/PAYMENT_PROVIDER_INTEGRATION.md#6-real-money-launch-gates)).

## 5. Interpretations to confirm in Phase 0

| Item | Treatment | Reference |
|---|---|---|
| Quantity state | Lots (unit ranges) carry the canonical status and money position | [Database 5](03-technical-specs/DATABASE_SPECIFICATION.md#5-quantity-model) |
| No "refund authorised" item status in v0.2 | A dispute refund keeps the lot `IN_DISPUTE`, frozen, `REFUND_PENDING` until provider success | [State Machine 4.3](03-technical-specs/TRANSACTION_STATE_MACHINE.md#43-item-lots-l) |
| Approval attaches to the instruction; the authorisation journal posts when it becomes `QUEUED`; a declined approval opens a dispute | One rule for acceptance payouts, return refunds and dispute decisions | [State Machine 4.8](03-technical-specs/TRANSACTION_STATE_MACHINE.md#48-payout-and-refund-instructions-f) |
| Return timer start | Server commit time of the handover submission | OD-15 |
| Split allocation | By quantity only; amounts are computed, never chosen | OD-14 |
| Staff route prefix | `/mod/disputes/{id}/decision` rather than v0.2's `/disputes/{id}/decision` | [API 11](03-technical-specs/API_SPECIFICATION.md#11-moderator-and-admin) |
| Buyer who never confirms receipt | v0.2 gives no timer; unchanged | OD-01 |

## 6. Readiness

| Role | Assessment |
|---|---|
| Backend | Ready to start. Schema, state machines, instruction guard, ledger journals, adapter contract and simulator are specified. Confirm the interpretations in section 5 and generate the OpenAPI file first |
| Frontend | Ready to start against a mock server built from the API examples. Render `allowed_actions`, requirement statuses and server deadlines only |
| UI/UX | Ready to start. User flows, deadline register, notification catalogue and evidence guidance are defined. Copy must not describe photographs as proof or a timer expiry as a finding |
| Launch with real money | Not ready: EXT-01 to EXT-03 and the launch-time decisions above |
