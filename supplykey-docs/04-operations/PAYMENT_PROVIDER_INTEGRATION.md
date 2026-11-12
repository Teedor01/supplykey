# Payment Provider Integration

| Version | Status | Last updated |
|---|---|---|
| 0.3.1 | Corrected review, for team sign-off | 2026-11-12 |

What we know and do not know about the payment provider, the simulator used for all development, the adapter requirements, and the gates that block real-money launch. This document does not claim that Paga supports escrow or conditional custody.

## 1. Position

- Paga is the *intended* payment infrastructure. No Paga documentation was provided to this documentation effort, so no Paga capability below is treated as documented. Every row marked VERIFY needs written confirmation from Paga.
- A subsidiary account, merchant balance or wallet is not an approved escrow or conditional-custody arrangement. Do not simulate escrow with an ordinary operating account.
- Real-money launch is BLOCKED until (1) the provider confirms the arrangement in writing and (2) qualified Nigerian counsel approves it.
- All development, staging and demos use the SimulatedPaymentProvider. All demo money is labelled simulated; no real funds move.

## 2. Capability matrix

| Capability needed | Status | Action |
|---|---|---|
| Collection of buyer payment (card, transfer, wallet) | ASSUMPTION: likely offered | VERIFY product, fees, limits, settlement timing |
| Conditional holding of collected funds, not available to the supplier until released | UNKNOWN | Written confirmation from provider and legal opinion |
| Distinction between payment received and funds held or secured | UNKNOWN | VERIFY that status queries expose both |
| Partial payout to supplier by lot or amount | UNKNOWN | VERIFY |
| Partial refund to buyer (original source) | UNKNOWN | VERIFY, including timing and fee treatment |
| Webhook notifications with signature | UNKNOWN | VERIFY scheme, retry policy, ordering, replay protection |
| Status query for collection, hold, payout, refund | UNKNOWN | Needed to resolve `UNKNOWN` outcomes |
| Idempotency or unique merchant reference | UNKNOWN | Needed for safe retries |
| Settlement reports | UNKNOWN | Needed for reconciliation |
| Sandbox that reproduces delay, failure, duplicates | UNKNOWN | Needed for adapter tests |
| Fees, limits, settlement times for holding, payout, refund | UNKNOWN | EXT-03 in [Open Decisions](../06-delivery/OPEN_DECISIONS.md) |
| Regulatory status of holding third-party funds (licensing, customer-money treatment, KYC/AML, dispute liability, the legal entity that holds funds) | UNKNOWN | EXT-02 legal advice |

## 3. Written questions to send to the provider

1. Which product collects payments, and can money be held after collection so that it cannot be paid to the merchant until we instruct release?
2. Who is the legal holder of the funds during the hold? Is there a trust, escrow or partner arrangement?
3. Can release be partial (by amount) and repeated for one collection? Can refund be partial and to the original source?
4. How do status queries distinguish collected, held, released, refunded and failed?
5. What are the webhook signing scheme, retry behaviour, ordering and replay protection?
6. Is there an idempotency key or unique reference for payout and refund requests? What happens on duplicates?
7. Fees, transaction and balance limits, settlement and refund times.
8. Settlement report format and availability for reconciliation.
9. Sandbox behaviour for delays, failures, duplicates and ambiguous results.
10. Terms for disputes, chargebacks and reversals involving held funds.

## 4. SimulatedPaymentProvider specification

Implements the full [adapter contract](../02-architecture/COMPONENT_DESIGN.md#4-payment-adapter-contract) with `mode = SIMULATED`. All scenarios are selectable per payment via a test flag so the team can reproduce every state-machine path.

| Scenario | Behaviour | Expected platform result |
|---|---|---|
| Success | Collection received and held | `PAYMENT_RECEIVED` then `FUNDS_SECURED`, `FUNDED` |
| Received but not secured | Collection succeeds, hold query says not secured | Stays `PAYMENT_RECEIVED`, transaction not funded, exception |
| Explicit failure | Provider reports failure | `FAILED` |
| Delayed success | Webhook after minutes | `PENDING` then success path |
| Pending forever | Never resolves | `UNKNOWN` after expiry; resolver polling |
| Timeout on status | Query times out | `UNKNOWN`, no funding |
| Duplicate webhook | Same event delivered repeatedly | One effect |
| Forged webhook | Bad signature | `401`, stored and flagged |
| Amount mismatch | Provider amount differs | No funding, exception |
| Payout success, failure, timeout, duplicate | Configurable per instruction | `SUCCEEDED`, `FAILED`, `UNKNOWN`, one effect |
| Refund success, failure, timeout, duplicate | Same | Same |
| Partial payout and refund | Supported | Lot-level instructions |
| Settlement report | Generated from simulator records, with optional injected mismatches | Reconciliation exceptions |

Every API response that involves money includes `"mode": "SIMULATED"` and the UI shows a "Simulated money" banner.

## 5. Adapter requirements

- Provider-neutral interface; no provider types or codes leak into the transaction engine or the financial module.
- Raw requests and responses stored.
- Any result other than explicit success or failure is `UNKNOWN`; never retry with a new idempotency key.
- `capabilities()` is checked before enabling production mode. If `supportsConditionalHolding`, `supportsPartialPayout`, `supportsPartialRefund`, `supportsStatusQuery` or `supportsIdempotencyKey` is false, the engine refuses to produce `FUNDS_SECURED` in `PRODUCTION` mode.
- Every real adapter must pass the same contract test suite as the simulator ([Testing Strategy 6](../05-quality/TESTING_STRATEGY.md#6-payment-adapter-contract-tests)).

## 6. Real-money launch gates

| # | Gate | Evidence required |
|---|---|---|
| G1 | Provider confirms every row of section 2 in writing, or an alternative provider is cleared | Signed confirmation or contract annex |
| G2 | Qualified Nigerian counsel approves the fund-holding model, the holding entity, licensing or partner requirements, KYC/AML duties, dispute liability and customer-facing claims | Written opinion |
| G3 | Reconciliation clean in the provider sandbox for 14 consecutive days | Job reports |
| G4 | All P0 tests pass; restore drill passed; security review done | Test and review reports |
| G5 | Moderator training and SLA agreed | Ops sign-off |
| G6 | Product owner has settled every decision marked "Launch" in [Open Decisions](../06-delivery/OPEN_DECISIONS.md) | Updated register |

If the provider cannot support the arrangement, evaluate a licensed holding or escrow partner, or revise the product promise, before accepting real money.

## 7. Related documents

[Financial Engine](../03-technical-specs/FINANCIAL_ENGINE.md) · [Component Design](../02-architecture/COMPONENT_DESIGN.md) · [Open Decisions](../06-delivery/OPEN_DECISIONS.md)
