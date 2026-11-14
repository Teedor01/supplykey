# Component Design


Internal design of the backend modules, the transition pattern, background jobs and the payment adapter contract. Entities are in [Database](../03-technical-specs/DATABASE_SPECIFICATION.md); transitions in the [State Machine](../03-technical-specs/TRANSACTION_STATE_MACHINE.md).

## 1. Module layering

Each module has `controller (HTTP) -> service (use case) -> domain (rules and state machine) -> repository`. Cross-module calls go through service interfaces only; no module reads another's tables directly.

| Module | Owns | Key use cases |
|---|---|---|
| Auth and RBAC | users, sessions | Register, OTP, login, MFA, authorise by role, ownership and state |
| Agreement | agreement versions, conditions | Create version, hash, buyer agreement, lock |
| Transaction engine | transactions, lots, receipts, dispatches, decisions, returns | All transitions T, L, A, R |
| Evidence | evidence files, windows, requests | Signed upload, complete, hash, flag, open and close windows |
| Dispute and moderation | disputes, decisions, outcomes, approvals, notes | D transitions, decision validation, approvals |
| Financial | allocations, lot freezes, ledger, payouts, refunds, approvals, payments | P and F transitions, creation guard, journals, reconciliation |
| Payment Adapter | provider integration | Provider-neutral calls ([section 4](#4-payment-adapter-contract)) |
| Notification | notifications, outbox | Enqueue, send, retry, preferences |
| Audit | audit log | Append with hash chain |

## 2. Transition pattern (every state change)

1. Authenticate and authorise (role, ownership, current state) and check the idempotency key.
2. Open one database transaction; lock the aggregate rows (`SELECT ... FOR UPDATE`) in a fixed order: transaction, lots, return, dispute, instruction.
3. Check preconditions from the State Machine (for example evidence window closed, quantity available, approver differs).
4. Split lots if the action covers part of a lot.
5. Write new states, ledger journal (if any), instructions (if any), audit row, outbox rows (notifications, provider submissions).
6. Commit. Only then may the worker call external systems.

This is the transactional outbox pattern: nothing external is called inside the transaction, so a crash can never leave money moved without a record.

## 3. Background jobs

| Job | Frequency (RECOMMENDED) | Purpose | Idempotency |
|---|---|---|---|
| `dispatch_deadline_scanner` | 1 min | Lots `AWAITING_DISPATCH` past `dispatch_deadline_at` open one `NO_DISPATCH` dispute (L3) | Unique open dispute per transaction |
| `return_deadline_scanner` | 1 min | Outstanding quantity past `confirmation_deadline_at` opens or extends the return's dispute (L13) | Unique open dispute per return |
| `inspection_scanner` | 5 min | `RECEIPT_CONFIRMED` past `inspection_deadline_at` opens review (L15, PROPOSED, enabled only after [OD-01](../06-delivery/OPEN_DECISIONS.md)) | Unique per lot and reason |
| `handover_overdue_scanner` | 5 min | Rejected quantity without handover past `handover_due_at` (L16, PROPOSED) | Same |
| `evidence_window_closer` | 5 min | Closes elapsed windows, expires requests, notifies moderators | State check |
| `payment_pending_resolver` | 2 min | Polls provider for `PENDING`, `PAYMENT_RECEIVED`, `UNKNOWN` payments | Status-based |
| `outbox_dispatcher` | continuous | Submits `QUEUED` instructions, sends notifications, retries with backoff | Instruction idempotency key |
| `instruction_resolver` | 2 min | Queries `SUBMITTED` and `UNKNOWN` instructions with the original reference | Status-based |
| `delivery_photo_scanner` | 5 min | Pending requirements past `due_at` become `OVERDUE` (DP4); notifies both parties and moderators; creates no dispute, payout or refund | Status check |
| `reminder_sender` | 15 min | Deadline warnings at 24h and 6h before each deadline (PROPOSED) | Reminder table keyed by deadline and offset |
| `reconciliation` | daily and on demand | Compare ledger with provider records ([Financial Engine 6](../03-technical-specs/FINANCIAL_ENGINE.md#6-reconciliation)) | Exception unique per record |
| `invariant_check` | nightly | Recompute allocation columns and ledger invariants | Read-only plus alerts |

All jobs run the same transition functions as the API and are safe to run twice.

## 4. Payment adapter contract

The engine depends only on this provider-neutral interface. No provider endpoint names appear in the engine or in this documentation.

| Method | Returns | Notes |
|---|---|---|
| `createCollection(ref, amount, buyer)` | `PENDING` plus checkout info | `ref` is `payment_attempts.id` |
| `getCollectionStatus(ref)` | `SUCCESS`, `FAILED`, `PENDING`, `UNKNOWN` | Payment received |
| `getHoldStatus(ref)` | `SECURED`, `NOT_SECURED`, `UNKNOWN` | Distinct from collection; only `SECURED` allows `FUNDS_SECURED` |
| `verifyWebhook(headers, rawBody)` | valid or invalid | Scheme VERIFY |
| `requestPayout(ref, amount, destination)` | `ACCEPTED`, `REJECTED`, `UNKNOWN` | `ref` is the idempotency key |
| `getPayoutStatus(ref)` | `SUCCESS`, `FAILED`, `PENDING`, `UNKNOWN` | |
| `requestRefund(ref, amount, original_ref)` | `ACCEPTED`, `REJECTED`, `UNKNOWN` | Partial refunds are mandatory for this product |
| `getRefundStatus(ref)` | same | |
| `getSettlementReport(date)` | provider records | Reconciliation |
| `capabilities()` | `supportsConditionalHolding`, `supportsPartialPayout`, `supportsPartialRefund`, `supportsStatusQuery`, `supportsIdempotencyKey`, `webhookSignatureScheme`, `mode` (`SIMULATED` or `PRODUCTION`) | The engine refuses to create `FUNDS_SECURED` in `PRODUCTION` mode if any required capability is false or `production_funds_enabled` is not set |

Rules: any result other than explicit success or explicit failure is UNKNOWN; raw responses are stored; the engine never sees provider-specific codes.

## 5. Decision authority inside the backend

| Question | Authority |
|---|---|
| May this transition happen? | Transaction engine (State Machine tables) |
| May this instruction be created? | Financial module (lot not frozen, allocation invariant, unique indexes) |
| Is a second approval needed? | Financial module from the instruction amount against `high_value_threshold_kobo` (at or above) and evidence flags; approval attaches to the instruction |
| Is the evidence window closed? | Evidence module (`evidence_windows` and waivers) |
| Are both parties' delivery photographs in? | Evidence module (`delivery_photo_requirements`); gates buyer decisions and moderator confirmation |
| May a lot (frozen or not) receive an instruction? | Financial module creation guard: not frozen, or frozen only by the dispute whose approved decision authorises it ([State Machine 4.8](../03-technical-specs/TRANSACTION_STATE_MACHINE.md#48-payout-and-refund-instructions-f)) |
| What is the instruction amount? | Financial module `instruction_amount(lot)` from allocations; never a client or moderator value |
| Can the dispute be decided? | Dispute module (window closed or waived, no open request, valid outcomes) |
| What did the provider actually do? | Payment adapter plus resolver, never the client |

## 6. Related documents

[System Architecture](SYSTEM_ARCHITECTURE.md) · [Notifications and Deadlines](../04-operations/NOTIFICATIONS_AND_DEADLINES.md) · [Payment Provider Integration](../04-operations/PAYMENT_PROVIDER_INTEGRATION.md)
