# Security Architecture


Authentication, authorisation, platform controls, the threat and fraud register with MVP mitigations, and the limits of the evidence system.

## 1. Authentication

- Email or phone with password and OTP verification. Short-lived access token; rotating refresh token in an httpOnly cookie; reuse of a refresh token revokes all sessions.
- Staff: mandatory TOTP MFA, separate login path, IP logging, session timeout.
- Rate limits and lockout on login, OTP and payment refresh endpoints.

## 2. Authorisation

Authorisation is role plus ownership plus state, evaluated on the backend for every request. For example a buyer may confirm receipt only for lots of their own transaction that are `DISPATCHED`.

| Rule | Enforcement |
|---|---|
| Moderator cannot be a party to the transaction | Checked by user id and payout bank account; `403 MODERATOR_IS_PARTY` |
| Approver of an instruction is a `SENIOR_MODERATOR` different from the deciding moderator | `403 SELF_APPROVAL_NOT_ALLOWED`; database trigger on `instruction_approvals` |
| No instruction on a frozen lot except the authorised dispute instruction | Creation guard and database triggers; `409 LOT_FROZEN` |
| Instruction amounts are computed, never chosen | `instruction_amount(lot)`; triggers on `decision_outcomes` and instruction items; `422 AMOUNT_MUST_MATCH_ALLOCATION` |
| Admin has no financial execution rights | No admin route creates, approves, retries or resolves instructions or financial exceptions |
| Nobody edits financial records | Application database role has INSERT and SELECT only on append-only tables |
| Non-existent or foreign objects return `404` | Avoids object enumeration |

## 3. Platform controls

- TLS everywhere; encryption at rest; field-level encryption for bank account numbers and MFA secrets; managed secrets.
- Parameterised queries; CSRF protection for cookie flows; strict CORS; content security policy on the moderator dashboard; request-size limits.
- Structured logs with no secrets or full account numbers; alerts on repeated failures, role changes, configuration changes and large decisions.
- Daily encrypted backups, point-in-time recovery, restore drill before pilot.
- Kill switch: one admin action stops all provider submissions.
- Incident runbooks for provider outage, suspected fraud and credential leak.

## 4. Threat register and MVP mitigations

| # | Threat | Mitigations (MVP) | Residual risk |
|---|---|---|---|
| 1 | Fake payment confirmation | Funded only after signature-verified webhook and independent provider status and hold query matching amount, currency and reference. Redirect return has no authority. Supplier is not told to dispatch before `FUNDED` | Provider-side compromise; reconciliation catches it afterwards |
| 2 | Webhook forgery and replay | Signature check (scheme VERIFY), timestamp tolerance if supplied, unique `(provider_name, provider_event_id)`, status re-query before any state change, raw payload stored, IP allow-list if published | If the provider has no signatures the status query is the only defence |
| 3 | Forged or reused delivery or dispatch photographs | SHA-256 duplicate detection across all transactions; flags for no EXIF or EXIF time outside the dispatch window; server receipt time; in-app capture guidance; moderator sees flags | Sophisticated forgeries will not be caught; forensic analysis is deferred |
| 4 | Buyer claims non-delivery after receiving goods | Supplier must give courier or rider details and tracking; buyer receipt confirmation is a separate action; dispute with evidence window; history of repeated claims shown to the moderator | Both-sides-lie cases depend on real-world evidence |
| 5 | Supplier falsely claims dispatch | Dispatch is a declaration, not proof. No payout without buyer acceptance and moderator confirmation; buyer confirms receipt or non-receipt; moderator can request courier proof; repeated claims flagged | Consequence of a lie is delay then refund |
| 6 | Buyer falsely claims return handover | Handover evidence starts a clock but never releases money. Refund needs supplier confirmation or a moderator decision after the evidence window | Courier collusion, unverifiable handovers |
| 7 | Supplier refuses to confirm returned goods | 72-hour countdown then automatic dispute; evidence from both sides; moderator inspects tracking and does not assume fault from expiry | Evidence of receipt may not exist |
| 8 | Buyer returns different or damaged goods | Acceptance and rejection photographs and optional serial or IMEI records before return; supplier receipt photographs and contest reasons `WRONG_ITEM`, `DAMAGED`, `QUANTITY_MISMATCH`; moderator compares | Without identifiers it is one word against another |
| 9 | Duplicate payout or refund attempts | Idempotency keys; lot-level unique indexes; row locks; instructions created only by transitions; `UNKNOWN` blocks retries; nightly invariants | Provider bugs; reconciliation is the backstop |
| 10 | Unauthorised or colluding moderator action | Role separation; second approval above threshold and for suspicious-evidence cases; party check; assignment logged; reason and evidence ids required; hash-chained audit; staff MFA; sampled quality review | Collusion of two staff; random second review and unusual-pattern alerts |
| 11 | Account takeover | Password policy, OTP on new device, rate limits, bank-account change cool-down with notice, re-authentication for sensitive actions, session revocation | SIM-swap risk for SMS OTP; offer TOTP |
| 12 | Evidence manipulation | Evidence immutable; hash re-verified on view; no delete; retire needs admin and reason and keeps the original; audit | Manipulation before upload is undetectable |
| 13 | Privilege escalation, IDOR | Server-side authorisation on every route; staff routes under `/mod` and `/admin` with MFA claim; ownership checks; generated authorisation test matrix | Misconfiguration, covered by tests |
| 14 | Self-dealing | Distinct user and bank-account checks; device and IP correlation flag; velocity limits; KYC thresholds ([OD-08](../06-delivery/OPEN_DECISIONS.md)) | Distinct fraudulent identities |
| 15 | Terms manipulation after payment | Immutable hashed agreement versions; edits blocked after first attempt; rejection must cite an agreed condition | |
| 16 | Invitation hijack | Single-use token, expiry, bound to contact; supplier sees who accepted | Forwarded message |
| 17 | Malicious uploads | Content sniffing, size limits, antivirus scan (RECOMMENDED), separate serving domain, no inline execution | Zero-days |
| 18 | Premature moderator decision | Decision endpoints refuse while an evidence window or request is open unless both parties waived (`EVIDENCE_WINDOW_OPEN`) | Both parties could be pressured to waive; waivers are audited |
| 19 | Splitting a payout or refund to stay under the approval threshold | One payout and at most one refund per approving action covering all its lots; threshold evaluated on the instruction amount with exactly-at-threshold requiring approval; sampled review of sequences of near-threshold actions | A determined insider using many separate actions; pattern alerts and sampling |
| 20 | Instruction against disputed or unknown-outcome money | Lot freezes (`DISPUTE`, `PROVIDER_UNKNOWN`, `RECONCILIATION`); creation guard and submission re-check; only the freezing dispute's approved decision may instruct a disputed lot | Provider-side behaviour; reconciliation |
| 21 | Moderator or client choosing an amount | Amounts computed from allocations; amount-level adjustment unsupported (OD-14) | A future approved adjustment mechanism will need its own controls |

## 5. Limits of the evidence system

- Photographs do not prove delivery, dispatch, return or condition. A photograph shows what a camera captured at some time, possibly not the claimed one, possibly of another object. EXIF data can be edited or stripped.
- Moderation does not guarantee the truth. A moderator weighs probabilities from incomplete evidence and can be wrong, deceived or pressured.
- The system therefore aims to raise the cost of lying, keep a consistent record, surface inconsistencies, make decisions reviewable and limit damage with approval thresholds. It does not aim to prove facts.
- User-facing wording: "SupplyKey reviews the evidence you and the other party provide. Decisions are based on that evidence and the agreed terms." Never promise that goods or payments are guaranteed.

## 6. Approval thresholds (PROPOSED)

| Value of one payout or refund | Requirement |
|---|---|
| Below ₦500,000 (`49999999` kobo or less) | One moderator; instruction created `QUEUED` |
| ₦500,000 and above (`50000000` kobo or more) | Instruction created `PENDING_APPROVAL`; a different `SENIOR_MODERATOR` approves; then `QUEUED` |
| Any decision with an acknowledged suspicious-evidence flag | Same approval path regardless of value |
| Manual retry after confirmed failure, exception resolution, cancellation of a `QUEUED` instruction | Senior moderator |

The threshold is evaluated on the instruction amount. A single moderator action creates at most one payout and one refund covering all its lots. No ledger entry exists while an instruction is `PENDING_APPROVAL`.

Thresholds live in `system_config`; changing them needs two admins and is recorded in `config_history` ([OD-07](../06-delivery/OPEN_DECISIONS.md)).

## 7. Related documents

[Dispute and Moderation](../04-operations/DISPUTE_AND_MODERATION.md) · [Evidence and Files](../04-operations/EVIDENCE_AND_FILE_HANDLING.md) · [Testing Strategy](../05-quality/TESTING_STRATEGY.md)
