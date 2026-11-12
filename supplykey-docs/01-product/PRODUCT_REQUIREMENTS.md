# Product Requirements

| Version | Status | Last updated |
|---|---|---|
| 0.3.1 | Corrected review, for team sign-off | 2026-11-12 |

Defines the product vision, users, scope, functional and non-functional requirements and success criteria. Rules are referenced by ID from [Business Rules](BUSINESS_RULES.md); flows are in [User Flows](USER_FLOWS.md).

## 1. Vision and problem

Make it safe for two Nigerian businesses that do not know each other to trade goods across states. Money moves only when agreed conditions are met, and disagreements are settled by a human moderator on recorded evidence.

*Problem.* Chinedu Okafor runs a phone shop in Port Harcourt and wants five Samsung Galaxy A15 phones from Ibrahim Traders in Ikeja, Lagos (₦220,000 each, ₦1,100,000 total). Chinedu will not wire money to a stranger. Ibrahim will not ship stock without secured payment. If the goods arrive wrong, nobody holds a neutral record of what was agreed or what arrived.

*Core value:* conditional financial settlement and trust between buyers and suppliers.

## 2. Users and roles

| Role | Description | Scope |
|---|---|---|
| Supplier | Seller. Creates the transaction on that transaction only. | Per transaction |
| Buyer | Purchaser. Agrees, pays, inspects, accepts or rejects. | Per transaction |
| Moderator | SupplyKey staff. Confirms acceptances, resolves disputes below the approval threshold. | Platform |
| Senior moderator | Second approver above the threshold; resolves financial exceptions. | Platform |
| Admin | Manages staff and configuration. No financial execution rights. | Platform |

One account can be a buyer on one transaction and a supplier on another, never both on the same transaction. The backend rejects a transaction where buyer and supplier are the same user or share a verified payout bank account. A moderator can never be a party to a transaction they review.

| Capability | Supplier | Buyer | Moderator | Senior moderator | Admin |
|---|---|---|---|---|---|
| Create / edit transaction terms (before payment) | Yes | No | No | No | No |
| Agree to terms, pay | No | Yes | No | No | No |
| Upload dispatch evidence | Yes | No | No | No | No |
| Upload delivery photographs (required of each party) | Yes | Yes | No | No | No |
| Confirm physical receipt | No | Yes | No | No | No |
| Submit acceptance / rejection | No | Yes | No | No | No |
| Open return, upload handover evidence | No | Yes | No | No | No |
| Confirm or contest returned goods | Yes | No | No | No | No |
| Submit evidence in a review or dispute | Yes | Yes | No | No | No |
| Confirm acceptance, decide disputes | No | No | Yes | Yes | No |
| Approve instructions at or above the threshold, sensitive actions | No | No | No | Yes | No |
| Retry failed payout/refund, resolve reconciliation exceptions | No | No | No | Yes | No |
| Manage staff and configuration | No | No | No | No | Yes |
| Edit any financial record | Nobody. Corrections are compensating ledger entries. | | | | |

## 3. Functional requirements

Each requirement traces to rule IDs in [Business Rules](BUSINESS_RULES.md) and to acceptance criteria in [Acceptance Criteria](../05-quality/ACCEPTANCE_CRITERIA.md).

### 3.1 Transactions and agreement

| ID | Requirement | Rules |
|---|---|---|
| FR-01 | Supplier creates a transaction with one or more line items. Each line has description, specification, condition, `quantity`, `unit_price_kobo`. UI offers an explicit single-item and multiple-item experience. | SK-02 |
| FR-02 | Transaction records delivery expectations, acceptance conditions and rejection conditions as explicit, individually identifiable `agreed_conditions`. | SK-01, SK-10 |
| FR-03 | Supplier invites the buyer by phone or email through a single-use, expiring link. | SK-01 |
| FR-04 | Buyer agrees to an exact agreement version. A change before payment creates a new version and voids the buyer's agreement. | SK-01, SK-03 |
| FR-05 | After the first payment attempt, terms are locked. No party can change them. | SK-03 |

### 3.2 Payment and funding

| ID | Requirement | Rules |
|---|---|---|
| FR-06 | Payment can start only after the buyer agreed to the current version. | SK-01 |
| FR-07 | The transaction becomes `FUNDED` only after backend verification that funds are secured by the approved arrangement; payment received alone is not enough. | SK-04 |
| FR-08 | Every payment attempt, provider reference and raw provider response is stored. | SK-04 |
| FR-09 | Ambiguous or delayed provider outcomes become `UNKNOWN`; money is frozen and never retried with a new idempotency key. | SK-04 |

### 3.3 Dispatch and delivery

| ID | Requirement | Rules |
|---|---|---|
| FR-10 | On funding the server sets `dispatch_deadline_at = funded_at + 72 hours`. | SK-05 |
| FR-11 | Supplier declares dispatch per line and quantity with evidence, rider or courier details and any tracking reference. Partial dispatch by quantity is allowed (RECOMMENDED, [OD-03](../06-delivery/OPEN_DECISIONS.md)). | SK-05, SK-16 |
| FR-12 | At the deadline each undispatched quantity opens a dispute (`NO_DISPATCH`). Dispatched quantities continue. No automatic payout or refund. | SK-06 |
| FR-13 | Buyer and supplier must each submit delivery photographs, each through their own action and requirement. Upload stores evidence only; it never changes a status. | SK-08 |
| FR-14 | Buyer separately confirms physical receipt per line and quantity. Confirmation starts the inspection period (PROPOSED, [OD-01](../06-delivery/OPEN_DECISIONS.md)). Photographs do not establish receipt. | SK-08 |
| FR-37 | Supplier delivery-photograph requirement per dispatch with a due date; overdue is visible to both parties and moderators, blocks or conditions moderator confirmation, and creates nothing automatically (PROPOSED, [OD-16](../06-delivery/OPEN_DECISIONS.md)). | SK-08 |
| FR-38 | Buyer delivery-photograph requirement per receipt confirmation; acceptance and rejection are refused until it is submitted (PROPOSED, [OD-16](../06-delivery/OPEN_DECISIONS.md)). | SK-08, SK-09 |

### 3.4 Acceptance, rejection and return

| ID | Requirement | Rules |
|---|---|---|
| FR-15 | Buyer submits acceptance quantities with proof. Status becomes `SUBMITTED_FOR_REVIEW`. No payout is created. | SK-09 |
| FR-16 | A submission notifies both parties and opens a pre-decision evidence window. | SK-17 |
| FR-17 | After the window closes or both waive, a moderator confirms, requests evidence, or opens a dispute, recording reason and evidence considered. | SK-09, SK-17 |
| FR-18 | Only confirmed accepted quantities are payout-eligible. | SK-09 |
| FR-19 | Rejection requires at least one `agreed_condition_id`, plus evidence. Funds stay held and no refund is created. | SK-10, SK-11 |
| FR-20 | Buyer opens a return record for rejected quantities. The timer does not start. | SK-12 |
| FR-21 | Handover evidence upload, once validated and linked, sets `handover_evidence_uploaded_at` and the 72-hour confirmation deadline. Further evidence never resets it. | SK-14 |
| FR-22 | Supplier confirms receipt of specific quantities with evidence and condition check. A partial confirmation confirms only that quantity. Confirmed quantities become refund-eligible. | SK-13 |
| FR-23 | Supplier contest (identity, condition, quantity or non-receipt) opens a dispute immediately. | SK-15 |
| FR-24 | If the 72-hour deadline passes without confirmation, one dispute opens for the outstanding quantity. | SK-15 |

### 3.5 Disputes, moderation and money

| ID | Requirement | Rules |
|---|---|---|
| FR-25 | Disputes open automatically (deadline) or manually (party or moderator), scoped to item quantities. | SK-06, SK-15 |
| FR-26 | Both parties receive notice and can upload evidence and statements. | SK-17 |
| FR-27 | A moderator cannot finalise a decision while an evidence window or requested-evidence deadline is open, unless both parties waive. | SK-17 |
| FR-28 | A decision records per-quantity outcomes, written reason, evidence considered, actor and timestamp. | SK-18 |
| FR-29 | Payout or refund instructions at or above the threshold are `PENDING_APPROVAL` and need approval by a different senior moderator before authorisation; below it they are `QUEUED`. | PROPOSED |
| FR-39 | Decision amounts for quantity-based `RELEASE` and `REFUND` equal the allocation of the quantity; amount-level adjustment is not supported ([OD-14](../06-delivery/OPEN_DECISIONS.md)). | SK-16 |
| FR-40 | Disputed quantities are frozen. Only the instruction executing an approved decision of the freezing dispute can pay or refund them. | SK-06, SK-18 |
| FR-30 | The ledger is append-only and balanced. Corrections use linked compensating journals. | SK-18 |
| FR-31 | Every payout and refund has an idempotency key and cannot duplicate. | SK-16 |
| FR-32 | A daily reconciliation job compares the ledger to provider records and raises exceptions. | SK-04 |
| FR-33 | Every important action writes an audit record (actor, role, time, before/after, request id). | SK-18 |

### 3.6 Platform

| ID | Requirement |
|---|---|
| FR-34 | In-app and email notifications for every event in [Notifications and Deadlines](../04-operations/NOTIFICATIONS_AND_DEADLINES.md). SMS is optional. |
| FR-35 | Moderator dashboard with queues, case file, notes, decisions and an audit viewer. |
| FR-36 | Registration, OTP verification, login, staff MFA, role-plus-ownership-plus-state authorisation. |

## 4. Non-functional requirements

| Area | Requirement |
|---|---|
| Correctness | No double payout or refund under retries, concurrency, duplicate webhooks or duplicate jobs. |
| Backend authority | All business rules and financial decisions enforced on the backend. The frontend sends intents and renders `allowed_actions`. |
| Availability | RECOMMENDED 99.5% for MVP. Provider downtime degrades safely (no money movement). |
| Performance | Usable on 3G/4G. Client-side image compression. P95 API latency under 500 ms excluding provider calls. |
| Mobile | Mobile-first responsive web for buyer and supplier; desktop-first moderator dashboard. |
| Time | UTC storage, WAT display, server-computed deadlines, countdowns informational only. |
| Auditability | Tamper-evident append-only audit and ledger. Retention period OPEN ([OD-11](../06-delivery/OPEN_DECISIONS.md)). |
| Privacy | Nigeria Data Protection Act obligations to be confirmed with counsel (VERIFY). |
| Configurability | Deadlines, windows and thresholds are configuration, not code. |

## 5. What SupplyKey does not do

- Provide riders, couriers, delivery or return logistics (SK-07, SK-12).
- Guarantee product quality, authenticity or the truth of any evidence.
- Act as a bank or claim to be an escrow agent. No claim that Paga supports escrow or conditional custody until confirmed in writing and approved by counsel.
- Automatically refund or pay out on a deadline expiry, a rejection, or an acceptance click.
- Let any frontend action release money.
- Offer insurance, credit, financing or price negotiation.

## 6. MVP scope

*In MVP:* everything in section 3, against the SimulatedPaymentProvider for development and demo (all demo money clearly labelled as simulated), mobile-first buyer and supplier app, desktop moderator dashboard, in-app and email notifications, configurable thresholds, daily reconciliation job with an exception queue.

*Deferred:* native apps; automated image forensics or AI evidence analysis; risk-based skip of moderator review; ratings and reputation; courier API integration or tracking; multi-currency or cross-border; instalments, credit, insurance; general chat beyond dispute statements; bulk import and public merchant APIs; amount-level price adjustment of a single unit ([OD-14](../06-delivery/OPEN_DECISIONS.md)).

## 7. Success criteria (PROPOSED targets)

| Criterion | Target |
|---|---|
| Ledger versus provider discrepancy after reconciliation | Zero unresolved after 1 business day |
| Duplicate payouts or refunds | 0 |
| Disputes and reviews decided with recorded reason and evidence ids | 100% |
| Median moderator decision time after window closes | Under 24 business hours |
| Unassisted completion of create, agree, pay, upload in usability tests | At least 85% |

## 8. Related documents

[Business Rules](BUSINESS_RULES.md) · [User Flows](USER_FLOWS.md) · [Acceptance Criteria](../05-quality/ACCEPTANCE_CRITERIA.md) · [Open Decisions](../06-delivery/OPEN_DECISIONS.md)
