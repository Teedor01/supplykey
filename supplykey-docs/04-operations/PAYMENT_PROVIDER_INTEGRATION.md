# Payment Provider Integration

Provider capabilities, the adapter used by SupplyKey, the Paga integration, and the gates that block real-money launch. A payment collection is not automatically proof of conditional custody or legal escrow.

## 1. Position

- Paga Engine is the intended payment infrastructure. Its current developer documentation describes collections, ledger-backed Subsidiary Accounts, internal account movements, bank payouts, refunds for selected collection products, callbacks and reconciliation.
- Paga documents Subsidiary Accounts as real Paga accounts with their own NUBAN and balance. Omitting `autoSweep` creates a ledger-backed account that holds funds. Paga also states that the business remains the legal owner of those accounts.
- These capabilities are useful building blocks, but they do **not** by themselves confirm that Paga has approved SupplyKey's marketplace transaction-protection model, conditional release rules, or third-party fund custody. Written Paga approval and qualified Nigerian legal advice remain mandatory before live funds are accepted.
- Development and staging use Paga Sandbox or the local simulator. No live provider credentials belong in source control.
- Live mode is disabled unless explicit provider-custody and legal-approval configuration gates are satisfied.

Official Paga references:

- [Paga Engine documentation](https://developer-docs.paga.com/)
- [Get started and sandbox setup](https://developer-docs.paga.com/docs/get-started)
- [Subsidiary Accounts](https://developer-docs.paga.com/docs/subsidiary-accounts)
- [Money Transfer](https://developer-docs.paga.com/docs/money-transfer-payout)
- [Dynamic virtual accounts](https://developer-docs.paga.com/docs/virtual-account)
- [Refunds](https://developer-docs.paga.com/docs/refunds)
- [Authentication](https://developer-docs.paga.com/docs/authentication)
- [Business API error codes](https://developer-docs.paga.com/docs/error-codes)

## 2. Relevant Paga products and limitations

| Paga capability | Documented behaviour | SupplyKey use / limitation |
|---|---|---|
| Dynamic virtual account | One-time collection account; payments settle to the merchant business account | Not used for transaction custody because it settles to the general business account |
| Ledger-backed Subsidiary Account | Permanent NUBAN with its own balance; omit `autoSweep` to hold funds | Used as the transaction-specific collection account in the Paga adapter |
| Subsidiary account charge | Moves funds from a subsidiary account to the Paga business balance | First step of a bank payout; must be recorded and reconciled |
| Subsidiary account transfer | Moves funds between two Subsidiary Accounts | Available for future internal account flows; legal and product approval still required |
| Business API bank transfer | Validates a bank destination and submits a transfer from the Paga business balance | Used for payout/refund bank-transfer submission after the transaction account is charged |
| `/refund/v2` | Full or partial refunds for Dynamic Virtual Account, Bank USSD and Paga Wallet payment requests | Not assumed to apply to Subsidiary Account deposits |
| Callbacks and status query | Signed callbacks and transaction/account status operations | Every callback is signature-checked and deduplicated; ambiguous outcomes remain unresolved until reconciliation |

## 3. Paga environments and credentials

Paga documents separate Sandbox and Live accounts, credentials and base URLs.

| Service | Sandbox | Live |
|---|---|---|
| Collect / Subsidiary Accounts | `https://beta-collect.paga.com` | `https://collect.paga.com` |
| Business API / bank transfers | `https://beta.mypaga.com` | `https://www.mypaga.com` |

Paga Sandbox requires account registration and approval. API keys are obtained from the Paga Business portal. Paga also requires the integration server's IP address to be whitelisted for Business API operations.

Backend configuration is supplied through environment variables:

- `PAYMENT_PROVIDER=simulated` or `paga`
- `PAGA_MODE=disabled`, `sandbox` or `live`
- `PAGA_PUBLIC_KEY`, `PAGA_SECRET_KEY`, `PAGA_HASH_KEY`
- `PAGA_CALLBACK_URL` for Subsidiary Account funding callbacks
- `PAGA_PAYOUT_CALLBACK_URL` for bank-transfer status callbacks
- `PAGA_BUSINESS_NAME`
- `PAYOUT_ENCRYPTION_KEY` for encrypted bank destinations
- `PAGA_CONDITIONAL_CUSTODY_APPROVED=true` and `PAGA_LEGAL_APPROVAL_REFERENCE` only after the corresponding written approvals exist

Callback URLs must be public HTTPS endpoints. A local development server needs a secure public tunnel for callback testing. Do not commit `.env` files or credentials.

## 4. Transaction collection flow

1. Buyer confirms the immutable agreement.
2. Backend creates a payment attempt with a stable provider reference and a transaction-specific Subsidiary Account reference.
3. Backend creates a Paga Subsidiary Account with `autoSweep` omitted, so the account is ledger-backed rather than swept to the business account or a bank.
4. Backend displays the returned Paga NUBAN, account name and exact amount to the buyer.
5. Buyer transfers the exact amount to that account.
6. Paga sends a funding callback to `PAGA_CALLBACK_URL`.
7. Backend verifies the documented `x-paga-hash-v3` signature, persists the event, and acknowledges it. The worker deduplicates the event, checks the exact amount and queries the Subsidiary Account balance.
8. Only a valid callback plus matching account balance can move the payment through `PAYMENT_RECEIVED` to `FUNDS_SECURED` in the configured approved mode.
9. Only then does the transaction become `FUNDED` and start the 72-hour dispatch deadline.

If account creation times out, the same account and request references are reconciled. A new account reference is not generated for an ambiguous operation. If the callback amount differs or the account balance cannot be confirmed, the payment does not become funded.

## 5. Payout and refund flow

The current Paga adapter uses the transaction-specific Subsidiary Account as the source of held funds.

1. A financial instruction is created under the SupplyKey financial rules. `PENDING_APPROVAL` instructions do not move funds.
2. Once queued, the worker charges the transaction Subsidiary Account using a stable instruction-derived reference. Paga documents this operation as moving funds to the business balance.
3. The worker submits a bank transfer using the Paga Business API `depositToBank` operation and a separate stable reference. Bank destinations must first be validated through Paga.
4. A successful submission response is not treated as final settlement. The instruction remains `SUBMITTED` until a verified Paga callback confirms a settled transfer or a status query resolves it.
5. Timeout, duplicate-reference and ambiguous response codes are not treated as failure or success. The original reference is queried; no new reference is generated.
6. Only confirmed settlement posts the final ledger journal and releases the corresponding lot freeze.

The bank-transfer path requires a verified and encrypted bank destination for the payout recipient. Refunds to a buyer use the buyer's verified bank destination. Fee treatment and production refund policy remain subject to the relevant Open Decisions.

## 6. Callback security and recovery

- Collect API requests use HTTP Basic authentication plus a SHA-512 `hash` header.
- Subsidiary Account funding callbacks are verified using Paga's `x-paga-hash-v3` scheme and the documented field order.
- Business API payout callbacks are verified using Paga's `x-paga-hash` scheme and the documented field order.
- Callback event identifiers and instruction references are deduplicated before state changes.
- Raw provider events are append-only. Processing outcomes are recorded separately.
- A provider timeout, duplicate reference or unknown response is not retried with a new reference.
- The worker queries the original reference and records unresolved cases as `UNKNOWN` for reconciliation. After a definitive bank-transfer failure, it tops the amount back up into the source Subsidiary Account and verifies the restored balance before marking the instruction failed.
- Paga documents `responseCode` values `3` as pending and `-1`, `105`, `135` as unknown outcomes requiring status checks. Other codes must be mapped using the current Paga error-code reference.

## 7. Local simulator

The `SimulatedPaymentProvider` remains available for development without Paga credentials. It supports reproducible success, failure, delay, duplicate, timeout and unknown scenarios. All simulator responses are labelled `SIMULATED`; they never represent real money movement.

Paga Sandbox is a separate provider mode. It uses Paga's simulated-money environment and must be labelled `PAGA_SANDBOX`. A successful Sandbox run is not evidence of live custody approval or production readiness.

## 8. Real-money launch gates

| # | Gate | Evidence required |
|---|---|---|
| G1 | Paga confirms in writing that the Subsidiary Account and payout arrangement may be used for SupplyKey's conditional marketplace settlement model | Written Paga approval or contract annex describing custody, release controls, account ownership, fees, limits and liability |
| G2 | Qualified Nigerian counsel approves the fund-holding model, holding entity, licensing or partner requirements, KYC/AML duties, dispute liability and customer-facing claims | Written legal opinion |
| G3 | Paga Sandbox collection, account balance, payout, refund, duplicate callback, timeout and daily business/subsidiary history reconciliation scenarios pass against actual Paga Sandbox credentials | Provider test evidence and reconciliation reports |
| G4 | Reconciliation is clean for 14 consecutive days | Job reports and matched provider statements |
| G5 | All P0 acceptance tests pass; PostgreSQL restore drill and security review pass | Test, restore and review reports |
| G6 | Moderator training and service levels are agreed | Operations sign-off |
| G7 | All product decisions marked Launch are resolved | Updated Open Decisions register |

If Paga does not approve this use case, evaluate a licensed escrow/holding partner or revise the product promise before accepting real money.

## 9. Related documents

[Financial Engine](../03-technical-specs/FINANCIAL_ENGINE.md) · [Component Design](../02-architecture/COMPONENT_DESIGN.md) · [Open Decisions](../06-delivery/OPEN_DECISIONS.md)
