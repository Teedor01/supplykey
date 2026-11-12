# Open Decisions

| Version | Status | Last updated |
|---|---|---|
| 0.3.1 | Corrected review, for team sign-off | 2026-11-12 |

Every unresolved product, technical and external question, with a recommended default, the consequence of the alternatives and who is affected. Defaults are configuration values or labelled proposals; none is treated as confirmed. Implementation can start now. "Needed by" names the phase that should not be completed on the default alone, or "Launch" for items that must be settled before real money moves.

## 1. Product-owner decisions

| ID | Question | Why it matters | Recommended default | Alternatives and consequences | Affected | Needed by |
|---|---|---|---|---|---|---|
| OD-01 | How long does the buyer have to inspect goods after confirming receipt, and what happens on expiry? | Without it funds can stay held indefinitely; silence is ambiguous | 72 hours from explicit receipt confirmation. Expiry opens a moderator review (`INSPECTION_PERIOD_EXPIRED` dispute). Never auto-accept or auto-refund | 24h risks rushed inspection; 7 days hurts supplier cash flow. Auto-accept favours suppliers and conflicts with SK-09; auto-refund invites abuse | BE, FE, UX | Phase 4 |
| OD-02 | How long is the pre-decision evidence window? | Fairness versus speed; applies to every acceptance and dispute | 48 hours, extendable once by a moderator with a recorded reason, configurable per transaction category | 24h disadvantages parties on poor connectivity; 5 days prolongs held funds | BE, FE, UX | Phase 5 |
| OD-03 | Is partial dispatch by quantity acceptable? | Supports real supplier operations | Allow. Undispatched quantity enters a dispute at 72 hours; dispatched quantity continues | All-or-nothing is simpler but blocks real trade; extending the deadline for the remainder delays buyers | BE, FE, UX | Phase 4 |
| OD-04 | Deadline for the buyer to submit return handover evidence after rejecting | Stops rejected goods and held funds remaining unresolved indefinitely | 7 days; expiry opens moderator review (`RETURN_HANDOVER_OVERDUE`), never an automatic refund | A longer period leaves cases unresolved for longer; a shorter one penalises slow couriers | BE, UX | Phase 5 |
| OD-05 | Delivery-fee allocation, payer and refund treatment; destination of fee shares on release and refund | Allocation correctness and money destination | Allocate pro rata by item value ([Financial Engine 3](../03-technical-specs/FINANCIAL_ENGINE.md#3-allocation)); refund treatment per agreed fee terms and moderator outcome. No refund instruction in production while `delivery_fee_refund_policy` is unset | Always refund (supplier bears loss), never refund (buyer bears), or decide per case | BE, UX, product | Phase 5, Launch |
| OD-06 | Platform fee amount, payer and refund treatment | Ledger and allocation | Zero in MVP until pricing is agreed. Model exists | Any fee needs a rule for partial refunds | BE, UX | Launch |
| OD-07 | High-value approval threshold and its scope | Insider risk against moderator workload | ₦500,000 (`50000000` kobo), at or above, evaluated per instruction; one payout and at most one refund per approving action; suspicious evidence always needs senior approval; approver is a different `SENIOR_MODERATOR`. Aggregation across separate decisions or actions on one transaction is unresolved | A lower threshold slows moderators, a higher one raises exposure; aggregation needs its own rule | BE, FE, Ops | Phase 6 |
| OD-08 | KYC and account verification levels | Fraud and regulation | Phone verification at minimum; payout bank account verified before payout; higher tier above a value threshold if required | Stricter KYC raises friction; exact regulatory duties need counsel | BE, UX | Launch |
| OD-09 | Cancellation rules | Funds are held; who may cancel and who bears cost | Before funding: either party may cancel. After funding: not supported in the MVP except through a manual dispute with a moderator decision | Mutual cancellation workflow or unilateral supplier stock-out cancel need their own transitions and approval | BE, FE, UX | Phase 2 |
| OD-10 | Mechanics of `REDISPATCH_OR_REPLACEMENT` | v0.2 allows it where both parties agree but does not specify consent capture or the new deadline | Both parties record `CONSENT` statements in the dispute; the decision sets a new dispatch deadline; default new deadline 72h | Remove the outcome from the MVP | BE, FE, UX | Phase 6 |
| OD-11 | Retention periods for evidence and financial records | Legal duty versus storage cost and privacy | 7 years for financial and dispute records (VERIFY with counsel) | Shorter risks non-compliance | BE | Launch |
| OD-12 | Technical operating defaults: file limits, reminder offsets (24h, 6h), invitation expiry (7 days), payment attempt expiry (30 min), rate limits | Operational behaviour | As stated in the relevant documents | Tune after pilot | BE, FE | Tunable |
| OD-13 | Moderator staffing, SLA and training | Decision-time target depends on it | Target median under 24 business hours after window closes (PROPOSED) | Slower SLA delays supplier payouts | Ops, product | Launch |
| OD-14 | Amount-level settlement of a single unit (for example a price reduction for a cosmetic defect) | v0.2 lists "split allocation"; this package implements it by quantity only and makes the restriction enforceable: for quantity-based `RELEASE` and `REFUND` the amount is computed from the allocation and any other amount is rejected by the API and by triggers | Not in MVP. An approved adjustment mechanism would need its own outcome, money status, ledger entry type, approval rule and tests | Allowing amounts to be typed would let a moderator move arbitrary sums, which the product forbids | BE, UX | Post-MVP |
| OD-15 | Exact instant the 72-hour return timer starts | v0.2 says "server upload-completion timestamp ... after the object is uploaded, validated and linked" | Server commit time of the handover submission (after uploads are complete, hash-verified and linked). Confirm this reading | Using each file's `server_received_at` would start the clock before the supplier can see the notice | BE, UX | Phase 5 |
| OD-16 | Delivery-photograph deadlines and consequences. The obligation that both parties submit delivery photographs is confirmed. When must each submit, and what happens if they do not? | Without a rule a missing photograph is silent; with the wrong rule it could move money | Supplier: due dispatch time + agreed expected delivery days + 24h (earlier: receipt confirmation + 24h). Buyer: before accepting or rejecting, and by `inspection_deadline_at` once OD-01 is approved. Consequences: buyer cannot accept or reject until submitted; supplier `PENDING` blocks moderator confirmation and `OVERDUE` needs an audited acknowledgement; nothing is paid, refunded, accepted, rejected or disputed automatically | Making supplier photographs a hard block with no override can leave buyers' acceptances unresolved; making them optional contradicts the requirement; auto-dispute on a missing photograph would add disputes and moderator load | BE, FE, UX | Phase 4 |
| OD-17 | Payout-account change cool-down | A changed bank account is a common takeover step | 24 hours, with notice to the old contact; pending payouts keep the destination snapshot taken at instruction creation | No cool-down is faster for users and weaker against takeover; longer delays legitimate changes | BE, UX | Phase 1 |

## 2. External dependencies (VERIFY)

| ID | Dependency | Required evidence | Needed by |
|---|---|---|---|
| EXT-01 | Provider capability confirmation: collection, conditional holding, partial payout, partial refund, status queries, idempotency, webhooks, reports, sandbox | Written confirmation from Paga (or alternative provider) | Launch |
| EXT-02 | Nigerian legal advice: legal holder of funds, licensing or partner requirements, customer-money treatment, KYC/AML, dispute liability, customer-facing claims | Written opinion | Launch |
| EXT-03 | Fees, limits and settlement rules for holding, payout and refund | Provider documentation or contract | Launch |

## 3. Questions closed by v0.2 (no longer open)

| v0.1 item | Resolution |
|---|---|
| Decision 13: unit rows versus `quantity_accepted` | Closed: line items with quantity, quantity lots and quantity-level decisions ([Database 5](../03-technical-specs/DATABASE_SPECIFICATION.md#5-quantity-model)) |
| Acceptance review without notice | Closed: every acceptance notifies both parties and opens an evidence window |
| Return timer start and transit tension | Closed: timer exactly as confirmed; transit consequence documented and moderators told not to infer fault |
| Fund-holding model | Still open externally (EXT-01, EXT-02) |
| Risk-based skipping of moderator review | Deferred: SK-09 stands; changing it needs explicit sign-off |

## 4. Related documents

[Business Rules](../01-product/BUSINESS_RULES.md) · [Payment Provider Integration](../04-operations/PAYMENT_PROVIDER_INTEGRATION.md) · [Implementation Plan](IMPLEMENTATION_PLAN.md)
