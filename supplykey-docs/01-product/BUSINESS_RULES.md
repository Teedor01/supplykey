# Business Rules


The single list of locked product rules, configurable deadlines, calculation rules and known tensions. Other documents reference these rule IDs instead of restating them.

## 1. Locked rules (CONFIRMED)

These rules are binding. Each has a stable rule ID so the other specifications can reference it without redefining it.

| ID | Rule | Implemented in |
|---|---|---|
| SK-01 | Supplier creates the transaction; buyer reviews and agrees before paying. | [API 4](../03-technical-specs/API_SPECIFICATION.md#4-transactions-agreement-and-invitations), [DB agreement tables](../03-technical-specs/DATABASE_SPECIFICATION.md) |
| SK-02 | A transaction has one item or multiple separately listed items (line items). | [DB `transaction_items`](../03-technical-specs/DATABASE_SPECIFICATION.md) |
| SK-03 | Terms agreed before payment are immutable for the funded transaction. | `agreement_versions` with canonical hash; no edit after first payment attempt |
| SK-04 | Buyer pays before dispatch. The transaction is `FUNDED` only after the backend verifies that funds are secured through an approved arrangement. Never from a browser redirect, client claim or unverified webhook. | [Financial Engine](../03-technical-specs/FINANCIAL_ENGINE.md), [Payment Provider Integration](../04-operations/PAYMENT_PROVIDER_INTEGRATION.md) |
| SK-05 | Supplier has 72 hours from funding to dispatch: `dispatch_deadline_at = funded_at + 72 hours`. | [State Machine](../03-technical-specs/TRANSACTION_STATE_MACHINE.md) |
| SK-06 | If the deadline passes without dispatch of all agreed quantities, a dispute opens and a moderator decides. No automatic refund, payout or cancellation. | [Dispute and Moderation](../04-operations/DISPUTE_AND_MODERATION.md) |
| SK-07 | Supplier arranges delivery independently. SupplyKey provides no riders or logistics. | [Product Requirements 5](PRODUCT_REQUIREMENTS.md#5-what-supplykey-does-not-do) |
| SK-08 | Both the buyer and the supplier must submit delivery photographs (each has its own requirement). A photograph is evidence, never independent proof of delivery or physical receipt, and never changes a status. Receipt is a separate buyer action. Deadlines and consequences of non-submission: section 2.1 (PROPOSED). | [Evidence and Files 7](../04-operations/EVIDENCE_AND_FILE_HANDLING.md#7-delivery-photograph-workflows), [API 7](../03-technical-specs/API_SPECIFICATION.md#7-delivery-evidence-and-receipt), [State Machine 4.9](../03-technical-specs/TRANSACTION_STATE_MACHINE.md#49-delivery-photograph-requirements-dp) |
| SK-09 | Buyer acceptance requires supporting proof and moderator review before confirmation. A button click is never sufficient.  Buyer silence never counts as acceptance. | [Dispute and Moderation 3](../04-operations/DISPUTE_AND_MODERATION.md) |
| SK-10 | Rejection reasons must reference conditions agreed before payment (`agreed_condition_ids`). Free text is supplemental. | [API 8](../03-technical-specs/API_SPECIFICATION.md#8-acceptance-and-rejection) |
| SK-11 | Rejection never refunds. Funds stay held. | State machine |
| SK-12 | Buyer arranges the return and uploads evidence of handing goods to the return rider. | [User Flows](USER_FLOWS.md) |
| SK-13 | Supplier confirmation of receipt is required before an ordinary refund. | State machine |
| SK-14 | The 72-hour return timer starts from the server timestamp at which the handover-evidence upload is completed, validated and linked. Not the button click, not the photo's claimed time. | [State Machine](../03-technical-specs/TRANSACTION_STATE_MACHINE.md) |
| SK-15 | No supplier confirmation within 72 hours opens exactly one dispute per return and affected quantity. No automatic refund. | State machine |
| SK-16 | Partial acceptance, dispatch, returns and refunds work at item-quantity level. | [Database](../03-technical-specs/DATABASE_SPECIFICATION.md), [Financial Engine](../03-technical-specs/FINANCIAL_ENGINE.md) |
| SK-17 | Both parties get notice and a chance to add evidence before any moderator decision (disputes and acceptance reviews). | [Dispute and Moderation](../04-operations/DISPUTE_AND_MODERATION.md) |
| SK-18 | Every moderator action is audited. Moderators cannot edit financial history. Corrections use compensating ledger entries. | [Financial Engine](../03-technical-specs/FINANCIAL_ENGINE.md) |

Deferred by decision: a risk-based fast track that skips moderator review of acceptances. SK-09 stands for the MVP; changing it needs explicit sign-off because it removes the human check the product promises.

## 2. Deadlines and configuration

All deadlines are computed on the server in UTC and read from `system_config`; each transaction snapshots the values at funding so later changes never alter in-flight deadlines.

| Config key | Default | Status | Rule |
|---|---|---|---|
| `dispatch_deadline_hours` | 72 | CONFIRMED | From backend-confirmed `funded_at` |
| `return_confirmation_hours` | 72 | CONFIRMED | From validated return-handover evidence upload |
| `evidence_window_hours` | 48 | PROPOSED ([OD-02](../06-delivery/OPEN_DECISIONS.md)) | Pre-decision window for disputes and acceptance reviews; moderator may extend once with a recorded reason |
| `inspection_period_hours` | 72 | PROPOSED ([OD-01](../06-delivery/OPEN_DECISIONS.md)) | From buyer's explicit receipt confirmation; expiry opens moderator review, never auto-accepts or auto-refunds |
| `return_handover_deadline_days` | 7 | PROPOSED ([OD-04](../06-delivery/OPEN_DECISIONS.md)) | From rejection; expiry routes to moderator review, never an automatic refund |
| `supplier_delivery_photo_grace_hours` | 24 | PROPOSED ([OD-16](../06-delivery/OPEN_DECISIONS.md)) | Added to dispatch time + agreed expected delivery days to give the supplier's photograph due date |
| `invitation_expiry_days` | 7 | PROPOSED | |
| `payment_attempt_expiry_minutes` | 30 | PROPOSED / VERIFY | Subject to provider semantics |
| `high_value_threshold_kobo` | 50000000 (₦500,000) | PROPOSED ([OD-07](../06-delivery/OPEN_DECISIONS.md)) | Payout or refund instruction at or above this needs approval by a different senior moderator |
| `platform_fee` | 0 | OPEN ([OD-06](../06-delivery/OPEN_DECISIONS.md)) | Do not invent a fee |

### 2.1 Delivery-photograph rules (PROPOSED, OD-16)

The obligation that both parties submit delivery photographs is confirmed. The timing and consequences below are PROPOSED and implemented as configuration.

| Rule | Statement |
|---|---|
| DP-01 | The supplier must submit at least one delivery photograph for each dispatch declaration. Due at dispatch time + the agreed expected delivery days + 24 hours; if the buyer confirms receipt first, due at receipt confirmation + 24 hours. |
| DP-02 | The buyer must submit at least one delivery photograph for each receipt confirmation before submitting acceptance or rejection for those quantities, and by `inspection_deadline_at` once [OD-01](../06-delivery/OPEN_DECISIONS.md) is approved. |
| DP-03 | A requirement is satisfied by at least one hash-verified, linked `DELIVERY_PHOTO` from that party. More can be added. |
| DP-04 | A missed buyer requirement blocks the buyer's acceptance and rejection until submitted, and the inspection-expiry review applies. A missed supplier requirement becomes `OVERDUE`, blocks moderator confirmation while `PENDING`, and needs an audited acknowledgement while `OVERDUE`. No payout, refund, acceptance, rejection or dispute is ever created automatically. |

## 3. Calculation rules

1. All money is integer kobo. A refund is never computed from a floating-point percentage.
2. For every allocated quantity: `released + refunded + pending_payout + pending_refund <= allocated_amount`.
3. A payout and a refund must never both settle the same quantity.
4. For quantity-based `RELEASE` and `REFUND` the amount equals the allocation of that quantity, computed by the backend. Nobody chooses an amount; partial-price adjustment is not supported ([OD-14](../06-delivery/OPEN_DECISIONS.md)).
5. A payout or refund instruction whose amount is at or above `high_value_threshold_kobo` (or whose decision is flagged) is `PENDING_APPROVAL`, posts no ledger entry, and becomes `QUEUED` only after a different senior moderator approves.
6. A frozen lot accepts no instruction except the one that executes an approved decision of the dispute that froze it ([State Machine 2.4](../03-technical-specs/TRANSACTION_STATE_MACHINE.md#24-lot-money-status-and-freezes)).
7. Delivery and platform fee shares are modelled explicitly per quantity; the apportionment algorithm is in [Financial Engine](../03-technical-specs/FINANCIAL_ENGINE.md#3-allocation). What happens to fee shares on release or refund is OPEN ([OD-05](../06-delivery/OPEN_DECISIONS.md)).

## 4. Known tensions (documented, not hidden)

| Tension | Consequence |
|---|---|
| The 72-hour return timer starts at buyer handover, not supplier receipt. | On a Lagos to Rivers return the timer can expire while the parcel is still in transit and open a dispute. The moderator must inspect tracking and both parties' evidence; expiry is not proof the supplier is at fault. |
| All acceptances need moderator review (SK-09). | Moderator workload and payout delay scale with volume. Measure review time and decide later whether a fast track is wanted. |
| Photographs are evidence, not proof. | Decisions rest on probabilities from incomplete evidence. See [Security Architecture](../02-architecture/SECURITY_ARCHITECTURE.md#5-limits-of-the-evidence-system). |

## 5. Glossary

| Term | Meaning |
|---|---|
| Transaction | One agreement between one buyer and one supplier |
| Line item (item) | One product, variant or price line with a quantity |
| Lot | A quantity of one line item that currently shares the same status and money position (see [Database](../03-technical-specs/DATABASE_SPECIFICATION.md#5-quantity-model)) |
| Agreement version | Immutable, hashed snapshot of the terms |
| Held funds | Money secured under the approved holding arrangement and not yet released |
| Evidence | A file or statement linked to a transaction, item, party and event |
| Evidence window | Period in which both parties can add evidence before a moderator may decide |
| Dispute | A moderator-reviewed case scoped to specific item quantities |
| Release | Authorised movement of held funds to the supplier |
| Refund | Authorised movement of held funds to the buyer |
