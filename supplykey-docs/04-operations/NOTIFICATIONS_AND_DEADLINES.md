# Notifications and Deadlines

| Version | Status | Last updated |
|---|---|---|
| 0.3.1 | Corrected review, for team sign-off | 2026-11-12 |

Every deadline the system enforces, who is told what and when, and the rules for displaying countdowns. Deadline values and statuses come from [Business Rules 2](../01-product/BUSINESS_RULES.md#2-deadlines-and-configuration) and the [State Machine](../03-technical-specs/TRANSACTION_STATE_MACHINE.md).

## 1. Deadline register

| Deadline | Starts at | Length | Expiry result | Status |
|---|---|---|---|---|
| Supplier dispatch | `funded_at` (backend-verified `FUNDS_SECURED`) | 72 hours | Dispute `NO_DISPATCH` for outstanding quantity; no payout or refund | CONFIRMED |
| Supplier return-receipt confirmation | Server commit time of validated return-handover evidence | 72 hours | One dispute per return for outstanding quantity; no refund | CONFIRMED |
| Pre-decision evidence window | Notice to both parties (acceptance submitted or dispute opened) | 48 hours; moderator may extend once with reason | Moves to moderator review | PROPOSED ([OD-02](../06-delivery/OPEN_DECISIONS.md)) |
| Moderator evidence request | Moderator request | Moderator-set `due_at` | Request `EXPIRED`; decision unblocked | Moderator-set |
| Supplier delivery photographs | Dispatch declaration | Dispatch time + agreed expected delivery days + 24h; earlier (receipt confirmation + 24h) if the buyer confirms receipt first | Requirement `OVERDUE`; notices; moderator confirmation needs an acknowledgement; nothing automatic | PROPOSED ([OD-16](../06-delivery/OPEN_DECISIONS.md)); the obligation itself is CONFIRMED |
| Buyer delivery photographs | Buyer receipt confirmation | Before acceptance or rejection; by `inspection_deadline_at` once approved | Acceptance and rejection refused (`DELIVERY_PHOTOS_REQUIRED`); requirement `OVERDUE` at the inspection deadline; nothing automatic | PROPOSED ([OD-16](../06-delivery/OPEN_DECISIONS.md)); the obligation itself is CONFIRMED |
| Buyer inspection period | Buyer's explicit receipt confirmation | 72 hours | Opens moderator review (`INSPECTION_PERIOD_EXPIRED`); never auto-accepts or auto-refunds | PROPOSED ([OD-01](../06-delivery/OPEN_DECISIONS.md)) |
| Buyer return handover | Rejection | 7 days | Opens moderator review (`RETURN_HANDOVER_OVERDUE`); never auto-refunds | PROPOSED ([OD-04](../06-delivery/OPEN_DECISIONS.md)) |
| Invitation | Send | 7 days | Transaction `EXPIRED` | PROPOSED |
| Payment attempt | Attempt creation | 30 minutes | Attempt `EXPIRED` after status check | PROPOSED, VERIFY |

Rules:

- Deadlines are computed on the server in UTC from values snapshotted at funding. They are stored once and not changed (apart from the single window extension and a redispatch deadline set by a recorded decision).
- The UI shows deadlines in Africa/Lagos (WAT) using `deadline_at` and `server_time`. Countdowns are informational; the server is the authority.
- Clicking Return, uploading unrelated evidence or adding further evidence never starts or resets the return timer.

## 2. Notification catalogue

Channels: in-app always; email by default; SMS optional (cost to be decided). Security-critical notices cannot be disabled. A notification failure never blocks or rolls back a transition.

| Event | Recipients | Content essentials |
|---|---|---|
| Invitation sent | Buyer | Transaction summary, link, expiry |
| Buyer agreed | Supplier | Agreement reference and hash |
| Terms changed | Buyer | Agreement voided; new version to review |
| Payment pending or unknown | Buyer | "Confirming payment"; no dispatch instruction to supplier |
| Funds secured, transaction funded | Buyer, supplier | Dispatch deadline in WAT |
| Dispatch reminder | Supplier | 24 hours and 6 hours before deadline (PROPOSED) |
| Dispatch declared | Buyer | Quantities, courier, tracking |
| Dispatch deadline missed (dispute opened) | Buyer, supplier | Affected quantities, funds held, evidence window end |
| Delivery photographs required | Supplier (at dispatch), buyer (at receipt confirmation) | What to photograph, due date in WAT |
| Delivery photograph reminder | The party whose requirement is `PENDING` | 24 hours and 6 hours before `due_at` (PROPOSED) |
| Delivery photographs overdue | Both parties and moderators | Which party, which dispatch or receipt; nothing is decided automatically |
| Delivery photographs added | The other party | Evidence added; no status change |
| Receipt confirmed | Supplier | Quantities, inspection period end (once approved) |
| Inspection reminder | Buyer | Before inspection expiry (once approved) |
| Acceptance submitted | Buyer and supplier | Quantities, evidence window end, how to add evidence or waive |
| Evidence window closing | Both | Time left |
| Moderator evidence request | Requested party (and the other) | Question, due date |
| Acceptance confirmed | Both | Quantities now eligible for payout |
| Rejection recorded | Supplier | Quantities, cited agreed conditions, evidence; funds remain held |
| Return opened | Supplier | Expect a handover record |
| Handover evidence uploaded | Supplier | 72-hour confirmation deadline in WAT |
| Return confirmation reminder | Supplier | 24 hours and 6 hours before deadline (PROPOSED) |
| Return receipt confirmed | Buyer | Quantities refund-eligible |
| Return contested | Buyer | Reason and dispute notice |
| Dispute opened | Both, immediately | Issue, quantities, funds status, evidence deadline |
| Dispute decision | Both | Outcome per quantity, written reason |
| Instruction awaiting approval (`PENDING_APPROVAL`) | Senior moderators (not the deciding moderator) | Amount, case summary, deciding moderator |
| Approval declined | Deciding moderator, both parties if a dispute opens | Reason, what happens next |
| Payout or refund succeeded, failed, or delayed | Recipient party; ops on failure or `UNKNOWN` | Amount and status |
| Financial exception | Senior moderators | Details |

## 3. Reminder mechanics

`reminder_sender` (every 15 minutes) reads deadlines and enqueues a reminder once per `(deadline, offset)`. Reminders are suppressed once the underlying action is complete.

## 4. Related documents

[Component Design 3](../02-architecture/COMPONENT_DESIGN.md#3-background-jobs) · [Dispute and Moderation](DISPUTE_AND_MODERATION.md) · [API 13](../03-technical-specs/API_SPECIFICATION.md#13-notifications)
