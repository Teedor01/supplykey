# Implementation Plan


A phased plan the team can execute, with deliverables, tasks, owners, dependencies, acceptance and testing per phase. Durations are RECOMMENDED estimates for a three-person team, not commitments. Owners are defined in [Team Responsibilities](TEAM_RESPONSIBILITIES.md).

## 1. Principles

- Contracts first. Enums, schema, OpenAPI, deadline configuration and the simulator contract are frozen in Phase 0 so frontend and backend never build on incompatible assumptions.
- Money paths before screens. State machines and ledger are built and tested before the UI that triggers them.
- Simulator first. Everything is built against the SimulatedPaymentProvider. A real provider adapter is written only after written provider approval and legal clearance ([Payment Provider Integration 6](../04-operations/PAYMENT_PROVIDER_INTEGRATION.md#6-real-money-launch-gates)).
- Moderator tooling is built alongside, not after, because reviews and disputes are core.
- No scope expansion. Deferred items stay deferred ([Product Requirements 6](../01-product/PRODUCT_REQUIREMENTS.md#6-mvp-scope)).

## 2. Phases

BE = Backend Engineer, FE = Frontend Engineer, UX = UI/UX Designer.

| Phase | Backend | Frontend | UI/UX | Exit condition |
|---|---|---|---|---|
| 0. Contract freeze (1 week) | Adopt canonical enums, schema, OpenAPI, config keys, simulator contract; produce mock server from the API examples | Set up API client generation and mock data; align status handling to canonical enums | Approve buyer, supplier and moderator journeys including evidence windows and notices | All three sign off on the same contracts; no endpoint without request, response and error examples |
| 1. Foundations (2 weeks) | Auth and RBAC, migrations, audit, idempotency, object storage, append-only enforcement | App shell, auth screens, upload component, error and loading states | Design system, responsive flows, upload guidance | Staging app up; role and ownership tests pass |
| 2. Agreement and items (2 weeks) | Transactions, line items with quantities, agreement versions and hashing, invitations, T1 to T3, T6, T7 | Supplier create flow (single and multiple items), buyer agreement review | Single/multiple item form, agreement review with numbered conditions | Agreement hash and totals verified; self-transaction blocked |
| 3. Simulated payments and ledger (3 weeks) | Payment adapter, SimulatedPaymentProvider, `PAYMENT_RECEIVED` vs `FUNDS_SECURED`, balanced ledger, lot creation and allocation, resolver, P1 to P9 | Checkout, pending, unknown and funded screens; simulated-money banner | Payment states and trust copy | All payment failure, duplicate and unknown tests pass |
| 4. Dispatch, delivery and evidence (2 weeks) | Dispatch by quantity, lot splitting, 72h scanner, evidence module, delivery-photo requirements for both parties with overdue scanner and gates, `confirm-received`, dispute creation, notifications v1 | Buyer and supplier delivery-photo requirement screens and uploads, dispatch declaration, receipt confirmation | Dispatch and delivery capture, requirement due dates and overdue states | Both-side requirement tests (TS-C1 to TS-C14) and missed-dispatch dispute tests pass |
| 5. Acceptance, rejection and returns (3 weeks) | Quantity decisions, evidence windows and waivers, moderator acceptance queue, returns, 72h return timer, payout and refund instructions via simulator | Buyer item decisions, supplier return confirmation and contest, status timelines per quantity | Acceptance, rejection and return flows | 5-phone partial acceptance and partial refund simulation passes end to end |
| 6. Disputes and moderation (3 weeks) | Dispute machine, windows, requests, decisions, lot freezes and the authorised-instruction creation guard, instruction approvals by a different senior moderator, computed amounts, audit viewer, financial worker | Moderator case file, evidence viewer, action history, approvals | Case review, notice and decision screens | Both parties can submit evidence; moderator outcome suite passes |
| 7. Production readiness (3 weeks) | Reconciliation, kill switch, rate limits, monitoring, recovery drills; real provider adapter only after written approval | Operational states, support and error surfaces | Final copy, accessibility, usability checks | Legal and provider gates, security review and recovery drills pass |

## 3. Phase detail: dependencies, acceptance, testing

| Phase | Depends on | Acceptance | Testing required |
|---|---|---|---|
| 0 | Review of the defaults in [Open Decisions](OPEN_DECISIONS.md) | OpenAPI lint clean; mock server serves every documented example | Contract lint |
| 1 | 0 | CI green; staging deploy; append-only enforcement verified | Unit, API, authorisation tests |
| 2 | 1 | [AC-01](../05-quality/ACCEPTANCE_CRITERIA.md) | TS-A1 to TS-A3, state-machine tests T1 to T7 |
| 3 | 2 | AC-02, AC-15, AC-16 for payments | TS-A4 to TS-A13, adapter contract suite, webhook tests |
| 4 | 3 | AC-03, AC-04, AC-05, AC-18, AC-19 | TS-B1 to TS-B6, TS-C1 to TS-C14 |
| 5 | 4 | AC-06 to AC-13, AC-21, AC-22 (instruction creation and amounts) | TS-D1 to TS-D14, TS-E1 to TS-E12, TS-J1 to TS-J5, TS-K9 to TS-K15 |
| 6 | 5 | AC-14, AC-17, AC-20, AC-21 | TS-B7, TS-F1 to TS-F12, TS-G1 to TS-G7, TS-H1 to TS-H4, TS-K1 to TS-K8, TS-K16 |
| 7 | 6 | All AC pass; go-live gates G1 to G6 | Failure-recovery, load, security review |

## 4. Minimum viable demo versus production readiness

| | Demo (after Phase 5, about 12 weeks) | Production readiness (after Phase 7) |
|---|---|---|
| Money | SimulatedPaymentProvider only, labelled simulated | Real provider and a legally cleared holding arrangement |
| Flows | Happy path; partial acceptance and return; missed-dispatch dispute; moderator decision | All flows including exceptions and reconciliation |
| Security | Basic auth, RBAC, audit | Security review, MFA enforced, kill switch, backups tested |
| Compliance | None claimed | Counsel approval, terms and privacy policy, KYC as required |
| Operations | Developers on call | Runbooks, alerts, moderator training and SLA |

## 5. Shared contracts that must be agreed before coding (Phase 0 gate)

See [Team Responsibilities 3](TEAM_RESPONSIBILITIES.md#3-contracts-frozen-in-phase-0). Backend updates the database specification, OpenAPI contract and state-machine enums together; frontend builds against the agreed OpenAPI contract and mock server.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Provider cannot hold funds conditionally | Simulator-first design; alternative partner or revised promise ([Payment Provider Integration 6](../04-operations/PAYMENT_PROVIDER_INTEGRATION.md#6-real-money-launch-gates)) |
| A decision is still open when its phase ends | Each item has a default held in configuration; the "Needed by" column shows the phase to settle it by |
| Quantity splitting complexity | Property tests and database exclusion constraint on lot ranges from Phase 2 |
| Moderator load from mandatory review | Measure review time and overturn rate from the first pilot |

## 7. Related documents

[Team Responsibilities](TEAM_RESPONSIBILITIES.md) · [Open Decisions](OPEN_DECISIONS.md) · [Testing Strategy](../05-quality/TESTING_STRATEGY.md)
