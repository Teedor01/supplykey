# API Specification


The REST contract the frontend and backend build against. Statuses and error codes are exactly those in the [State Machine](TRANSACTION_STATE_MACHINE.md#2-canonical-enums). The frontend sends intents and renders `allowed_actions`; it never decides whether money can move.

## 1. Conventions

- Base path `/api/v1`. JSON over HTTPS. Field names `snake_case`. Money is integer kobo in `*_kobo` fields (₦1,100,000 = `110000000`). Timestamps are ISO-8601 UTC. Ids are UUIDs; transactions also have a `reference` such as `SK-2026-000123`.
- Auth: `Authorization: Bearer <access_token>`. Routes under `/mod/*` and `/admin/*` require a staff role and a session with completed MFA.
- Idempotency: every state-changing `POST` requires `Idempotency-Key: <uuid>`. Same key and body returns the stored response with `Idempotent-Replay: true`; same key with a different body returns `409 IDEMPOTENCY_KEY_REUSED`.
- Concurrency: mutable resources return a `version`; decision and moderator endpoints accept `If-Match: <version>`, and a mismatch returns `409 VERSION_CONFLICT`.
- Time: every response carries `meta.server_time`. Deadlines are `*_at` fields computed on the server. Countdown UI uses them and is informational only.
- Pagination: `?limit=20&cursor=...`; `meta.next_cursor`.
- Evidence: actions accept `evidence_ids` of files that are `VERIFIED` ([section 12](#12-evidence-uploads)). The server links them to the event in the same transaction; an unverified file is refused (`EVIDENCE_NOT_VERIFIED`).
- Provider outcomes: `502`/`503 PROVIDER_UNAVAILABLE` is returned only when the adapter confirms the request was not submitted. If a request may have reached the provider, persist `UNKNOWN`, start reconciliation and return `202 OUTCOME_PENDING`; never tell the client that no money moved.

Success envelope:

```json
{ "data": {}, "meta": { "request_id": "req_8f2a", "server_time": "2026-11-12T09:30:00Z" } }
```

Error envelope:

```json
{ "error": { "code": "INVALID_STATE_TRANSITION", "message": "Quantity is not in a state that allows this action.",
  "details": { "item_id": "it_1", "current_status": "SUBMITTED_FOR_REVIEW", "allowed_actions": [] }, "request_id": "req_8f2a" } }
```

## 2. Errors

| HTTP | Code | Meaning | Client behaviour |
|---|---|---|---|
| 400 | `VALIDATION_FAILED` | Field errors in `details.fields[]` | Show field messages |
| 401 | `UNAUTHENTICATED` | Missing or expired token | Refresh or login |
| 403 | `FORBIDDEN` | Role or ownership failure | Show no access |
| 403 | `SELF_APPROVAL_NOT_ALLOWED` | Approver is the decision maker | |
| 403 | `MODERATOR_IS_PARTY` | Moderator is buyer or supplier on the transaction | |
| 404 | `NOT_FOUND` | Also used for objects the caller may not know exist | |
| 409 | `INVALID_STATE_TRANSITION` | Backend refused the transition | Reload; use `allowed_actions` |
| 409 | `VERSION_CONFLICT`, `IDEMPOTENCY_KEY_REUSED` | | Reload |
| 409 | `TERMS_LOCKED` | Payment attempt exists; terms immutable | |
| 409 | `AGREEMENT_VERSION_STALE` | Buyer agreed to a non-current version | Reload terms |
| 409 | `PAYMENT_ALREADY_IN_PROGRESS`, `PAYMENT_ALREADY_FUNDED` | | |
| 409 | `DISPATCH_DEADLINE_PASSED` | `details.dispute_id` points to the open dispute; submit evidence there | |
| 409 | `ITEM_NOT_DECIDABLE`, `ITEM_ALREADY_DECIDED` | Lots not in `RECEIPT_CONFIRMED` for the quantity | |
| 409 | `HANDOVER_ALREADY_SUBMITTED` | Timer already started | Use additional-evidence |
| 409 | `DISPUTE_ALREADY_OPEN` | Duplicate for same reason and quantities | |
| 409 | `EVIDENCE_WINDOW_OPEN`, `EVIDENCE_REQUEST_OPEN` | Moderator cannot decide yet | |
| 409 | `OUTCOME_UNKNOWN_RECONCILE_FIRST` | Retry blocked on an `UNKNOWN` instruction | |
| 413 / 415 | `FILE_TOO_LARGE`, `UNSUPPORTED_FILE_TYPE` | | |
| 422 | `EVIDENCE_REQUIRED` | Acceptance, rejection, dispatch or handover without evidence | |
| 422 | `REJECTION_REASON_NOT_IN_AGREEMENT` | `agreed_condition_ids` missing or not in the agreed version | |
| 422 | `QUANTITY_EXCEEDS_AVAILABLE` | Requested quantity exceeds lots in the required status | |
| 422 | `AMOUNT_MUST_MATCH_ALLOCATION` | A supplied `amount_kobo` for a quantity-based `RELEASE` or `REFUND` differs from the backend-computed allocation amount (`details.expected_amount_kobo`). Amount-level adjustment is not supported ([OD-14](../06-delivery/OPEN_DECISIONS.md)) | Remove the amount or correct it |
| 422 | `DELIVERY_PHOTOS_REQUIRED` | Buyer submits acceptance or rejection before their delivery photographs are `SUBMITTED` or `SUBMITTED_LATE` | Upload photographs first |
| 422 | `ACKNOWLEDGEMENT_REQUIRED` | Moderator confirms while the supplier's photographs are `OVERDUE` without the acknowledgement and reason | Add the acknowledgement |
| 409 | `SUPPLIER_DELIVERY_PHOTOS_PENDING` | Moderator confirms while the supplier's photographs are `PENDING` | Wait or request evidence |
| 409 | `LOT_FROZEN` | An instruction is attempted on a frozen lot outside the authorised dispute exception ([State Machine 2.4](TRANSACTION_STATE_MACHINE.md#24-lot-money-status-and-freezes)) | |
| 409 | `INSTRUCTION_NOT_PENDING_APPROVAL` | Approve or decline on an instruction in another status | Reload |
| 422 | `EVIDENCE_NOT_VERIFIED` | An `evidence_ids` entry is not `VERIFIED` or belongs to another user or transaction | Complete the upload first |
| 422 | `SELF_TRANSACTION_NOT_ALLOWED`, `HASH_MISMATCH`, `EVIDENCE_NOT_ALLOWED_IN_STATE`, `BUSINESS_RULE_VIOLATION` | | |
| 429 | `RATE_LIMITED` | `Retry-After` header | |
| 502 / 503 | `PROVIDER_UNAVAILABLE` | Adapter confirms the request was not submitted to the provider | Retry using the same idempotency key |
| 500 | `INTERNAL_ERROR` | Quote `request_id` | |

## 3. Authentication and profile

| Method and URL | Purpose | Auth | Request | Response | Errors |
|---|---|---|---|---|---|
| `POST /auth/register` | Create account | None | `email` or `phone`, `password` (min 10), `display_name`, `accepted_terms: true` | `201 {user_id, verification_required}` | `EMAIL_TAKEN`, `PHONE_TAKEN`, `WEAK_PASSWORD` |
| `POST /auth/verify-otp` | Verify contact | None | `contact`, `otp` | `200` tokens | `OTP_INVALID`, `OTP_EXPIRED`, `429` after 5 tries |
| `POST /auth/login` | Login | None | `identifier`, `password` | `200 {access_token, expires_in}`; staff get `mfa_required: true` | `INVALID_CREDENTIALS`, `ACCOUNT_LOCKED` |
| `POST /auth/mfa/verify` | Staff TOTP | Partial session | `code` | `200` tokens | `MFA_INVALID` |
| `POST /auth/refresh` | Rotate token | Refresh cookie | none | `200` | `REFRESH_REUSED` revokes sessions |
| `POST /auth/logout` | End session | Bearer | none | `204` | |
| `GET /me` | Own profile | Bearer | none | `{user, profile}` | |
| `PATCH /me/profile` | Update profile | Bearer | `display_name`, `business_name`, `state`, `address` | `200` | `VALIDATION_FAILED` |
| `PUT /me/payout-account` | Set payout bank account | Bearer | `bank_code`, `account_number` | `200 {account_name, verified, effective_at}` | `ACCOUNT_NAME_MISMATCH`; cool-down (24h PROPOSED, [OD-17](../06-delivery/OPEN_DECISIONS.md)); pending payouts keep their destination snapshot |

## 4. Transactions, agreement and invitations

| Method and URL | Purpose | Authz | State rule |
|---|---|---|---|
| `POST /transactions` | Create draft | Verified user becomes supplier | Creates `DRAFT` |
| `GET /transactions` | List mine (`role`, `status`) | Participants | |
| `GET /transactions/{id}` | Detail with items, lots summary, deadlines, `allowed_actions` | Participants, assigned staff | |
| `PATCH /transactions/{id}` | Edit draft or propose revised terms | Supplier | `DRAFT`, `AWAITING_BUYER_AGREEMENT`, `AWAITING_PAYMENT`; new agreement version, buyer agreement voided (T3). `TERMS_LOCKED` after first payment attempt |
| `POST /transactions/{id}/send` | Finalise and invite buyer | Supplier | `DRAFT` to `AWAITING_BUYER_AGREEMENT` (T1) |
| `GET /invitations/{token}` | Preview invite | Token holder | `INVITATION_EXPIRED`, `INVITATION_USED` |
| `POST /invitations/{token}/accept` | Bind logged-in user as buyer | Bearer | `SELF_TRANSACTION_NOT_ALLOWED` |
| `GET /transactions/{id}/agreement` | Current and past versions with hashes and conditions | Participants, staff | Read only |
| `POST /transactions/{id}/agreement/confirm` | Buyer agrees to a named version | Buyer | `{agreement_version_id, content_hash}`; `AWAITING_BUYER_AGREEMENT` to `AWAITING_PAYMENT` (T2) |
| `POST /transactions/{id}/cancel` | Cancel before funding | Buyer or supplier | T6 only. Cancellation after funding is OPEN ([OD-09](../06-delivery/OPEN_DECISIONS.md)); `409 INVALID_STATE_TRANSITION` |

*Create transaction (multiple items).* A single-item transaction is the same call with one entry in `items`.

```json
{
  "buyer_contact": { "phone": "+2348031234567" },
  "delivery": { "destination_state": "Rivers", "city": "Port Harcourt", "expected_within_days": 5,
                "fee_payer": "BUYER", "delivery_fee_kobo": 0 },
  "items": [
    { "description": "Samsung Galaxy A15 128GB", "specification": "New, factory sealed, 128GB, black",
      "condition": "Brand new", "quantity": 5, "unit_price_kobo": 22000000,
      "conditions": [
        { "code": "SEALED", "kind": "REJECTION", "text": "Box must be factory sealed" },
        { "code": "SPEC_MATCH", "kind": "REJECTION", "text": "Must be 128GB, black" },
        { "code": "QTY", "kind": "ACCEPTANCE", "text": "Quantity must match the order" } ] }
  ],
  "general_terms": "Replacement units are not acceptable."
}
```

Response `201`:

```json
{ "data": { "id": "tx_b7c1", "reference": "SK-2026-000123", "status": "DRAFT",
  "goods_total_kobo": 110000000, "delivery_fee_kobo": 0, "platform_fee_kobo": 0, "total_payable_kobo": 110000000,
  "items": [ { "id": "it_1", "line_no": 1, "quantity": 5, "unit_price_kobo": 22000000, "line_total_kobo": 110000000,
               "conditions": [ { "id": "c_1", "code": "SEALED" }, { "id": "c_2", "code": "SPEC_MATCH" }, { "id": "c_3", "code": "QTY" } ] } ],
  "allowed_actions": ["edit", "send"] } }
```

Validation: `quantity >= 1`; `unit_price_kobo > 0`; totals are computed by the server and any client total is ignored; at least one condition per item; destination must be a valid Nigerian state; at most 50 lines (PROPOSED).

## 5. Payments

| Method and URL | Purpose | Authz | Rules |
|---|---|---|---|
| `POST /transactions/{id}/payments` | Start payment | Buyer | `AWAITING_PAYMENT`; buyer agreed to current version; `Idempotency-Key` mandatory (P1). Errors `PAYMENT_ALREADY_IN_PROGRESS`, `PAYMENT_ALREADY_FUNDED`, `PROVIDER_UNAVAILABLE` |
| `GET /transactions/{id}/payments` | Payment status and attempts (no raw provider data for non-staff) | Participants | |
| `POST /transactions/{id}/payments/{pid}/refresh` | Ask the backend to re-check with the provider | Buyer | 1 per 30 s; the backend queries the provider, never the client |
| `POST /webhooks/payments/{provider}` | Provider callback | Signature, not Bearer | Stores the raw event first and replies `200` quickly; processing is asynchronous. Invalid signature: stored, flagged, `401`. Duplicate: `200`, no effect |

Response to payment start:

```json
{ "data": { "payment_id": "pay_1", "status": "PENDING", "amount_kobo": 110000000, "mode": "SIMULATED",
  "checkout": { "type": "ADAPTER_DEFINED" }, "transaction_status": "AWAITING_PAYMENT" } }
```

The checkout shape is adapter-defined (VERIFY with the real provider). After checkout the UI shows "Confirming payment" and polls `GET /transactions/{id}`. The UI shows "Funds secured" only when `payment.status = FUNDS_SECURED` and `status = FUNDED`; `PAYMENT_RECEIVED` is shown as "Payment received, confirming secure holding".

## 6. Dispatch

`POST /transactions/{id}/dispatch` , Supplier. Requires `status = FUNDED`, server time before `dispatch_deadline_at`, and lots `AWAITING_DISPATCH` covering the declared quantities (L2). Partial dispatch by quantity is allowed (RECOMMENDED, [OD-03](../06-delivery/OPEN_DECISIONS.md)).

```json
{ "lines": [ { "item_id": "it_1", "quantity": 3 } ],
  "courier_name": "GIGL", "rider_name": "Emeka Obi", "rider_phone": "+2348055550101", "tracking_ref": "GIG-778812",
  "evidence_ids": ["ev_201", "ev_202"], "note": "3 of 5 collected today, remaining 2 tomorrow" }
```

Response `200`:

```json
{ "data": { "dispatch_id": "dsp_1", "lots": [
  { "lot_id": "lot_a", "item_id": "it_1", "units": [1, 3], "quantity": 3, "item_status": "DISPATCHED" },
  { "lot_id": "lot_b", "item_id": "it_1", "units": [4, 5], "quantity": 2, "item_status": "AWAITING_DISPATCH" } ],
  "dispatch_deadline_at": "2026-11-14T09:30:00Z",
  "delivery_photo_requirement": { "id": "dpr_1", "party_role": "SUPPLIER", "status": "PENDING", "due_at": "2026-11-20T09:30:00Z" } } }
```

The dispatch also creates the supplier's delivery-photograph requirement (state machine DP1; `due_at` = dispatch time + agreed expected delivery days + 24h grace, PROPOSED [OD-16](../06-delivery/OPEN_DECISIONS.md)).

Errors: `422 EVIDENCE_REQUIRED`, `422 QUANTITY_EXCEEDS_AVAILABLE`, `409 DISPATCH_DEADLINE_PASSED` (with `details.dispute_id`). Late evidence goes to `POST /disputes/{id}/evidence` and cannot bypass the missed-deadline dispute.

## 7. Delivery evidence and receipt

Both the buyer and the supplier must submit delivery photographs. Each party has its own requirement (a `delivery_photo_requirements` row, status `PENDING`, `SUBMITTED`, `OVERDUE`, `SUBMITTED_LATE`). Photographs are evidence only: submitting them never changes a lot status and never establishes physical receipt.

| Method and URL | Purpose | Authz | Rules |
|---|---|---|---|
| `POST /transactions/{id}/delivery/evidence` | Submit delivery photographs against a requirement | Buyer or supplier, matching `requirement.party_role` | Body `{requirement_id, evidence_ids[] (at least 1 photo), note}`. Purpose `DELIVERY_PHOTO`, `party_role` taken from the caller. First valid submission sets the requirement `SUBMITTED` (or `SUBMITTED_LATE` if after `due_at`); later calls add more photographs. Never changes any lot status. Errors `422 EVIDENCE_REQUIRED`, `403 FORBIDDEN` for the other party's requirement |
| `POST /transactions/{id}/delivery/confirm-received` | Confirm physical receipt | Buyer only | Line and quantity selection; moves lots `DISPATCHED` to `RECEIPT_CONFIRMED` (L4); creates the buyer's requirement (DP2); sets `inspection_deadline_at` once [OD-01](../06-delivery/OPEN_DECISIONS.md) is approved. Not implied by a photograph |

`GET /transactions/{id}` returns the caller's and the other party's requirements:

```json
"delivery_photo_requirements": [
  { "id": "dpr_1", "party_role": "SUPPLIER", "source_type": "DISPATCH", "status": "SUBMITTED", "due_at": "2026-11-20T09:30:00Z", "submitted_at": "2026-11-15T14:40:00Z" },
  { "id": "dpr_2", "party_role": "BUYER", "source_type": "RECEIPT_CONFIRMATION", "status": "PENDING", "due_at": "2026-11-18T14:20:00Z", "submitted_at": null } ]
```

*When each party must submit, and what happens if they do not* (PROPOSED, [OD-16](../06-delivery/OPEN_DECISIONS.md)):

| Party | Due | If not submitted |
|---|---|---|
| Supplier | By the dispatch requirement's `due_at`: dispatch time + agreed expected delivery days + 24h; earlier if the buyer confirms receipt first (confirmation time + 24h) | Requirement becomes `OVERDUE` (both parties and moderators are told). A moderator cannot confirm acceptance while it is `PENDING` (`409 SUPPLIER_DELIVERY_PHOTOS_PENDING`); while `OVERDUE` only with an audited acknowledgement (`ACKNOWLEDGEMENT_REQUIRED`). Late submission becomes `SUBMITTED_LATE`. Nothing is paid, refunded, accepted, rejected or disputed automatically |
| Buyer | Before submitting acceptance or rejection for the confirmed quantities, and by `inspection_deadline_at` once approved | `422 DELIVERY_PHOTOS_REQUIRED` on `POST /transactions/{id}/decisions`; at the inspection deadline the requirement becomes `OVERDUE` and the inspection-expiry review (L15) applies. Nothing is paid, refunded, accepted or rejected automatically |

```json
// POST /transactions/tx_b7c1/delivery/evidence   (supplier)
{ "requirement_id": "dpr_1", "evidence_ids": ["ev_301", "ev_302"], "note": "Delivered by GIGL at 14:10, rider signed" }
// POST /transactions/tx_b7c1/delivery/evidence   (buyer)
{ "requirement_id": "dpr_2", "evidence_ids": ["ev_311"], "note": "Boxes at the shop, seals intact on 3, broken on 2" }
// POST /transactions/tx_b7c1/delivery/confirm-received
{ "lines": [ { "item_id": "it_1", "quantity": 5 } ] }
```

Response of the evidence call: `{ "requirement": { "id": "dpr_2", "status": "SUBMITTED", "submitted_at": "2026-11-17T14:31:00Z" }, "lots_changed": [] }`. Response of confirm-received: `{ "lots": [ { "lot_id": "lot_a", "item_status": "RECEIPT_CONFIRMED", "inspection_deadline_at": "2026-11-20T14:20:00Z" } ], "delivery_photo_requirement": { "id": "dpr_2", "party_role": "BUYER", "status": "PENDING" } }`. Errors: `422 QUANTITY_EXCEEDS_AVAILABLE`, `409 INVALID_STATE_TRANSITION`.

## 8. Acceptance and rejection

| Method and URL | Purpose | Authz | Rules |
|---|---|---|---|
| `POST /transactions/{id}/decisions` | Submit per-line accepted and rejected quantities | Buyer | Quantities must be in lots `RECEIPT_CONFIRMED`. `ACCEPT` needs evidence. `REJECT` needs `agreed_condition_ids` and evidence. The buyer's delivery-photograph requirement for the receipt must be `SUBMITTED` or `SUBMITTED_LATE` (`422 DELIVERY_PHOTOS_REQUIRED`). Accepted quantity becomes `SUBMITTED_FOR_REVIEW` (L5, A1), both parties are notified and an evidence window opens; no payout. Rejected quantity becomes `REJECTED_AWAITING_RETURN` (L6), no refund |
| `GET /decisions/{id}` | View a decision, its window and evidence | Participants, staff | |
| `POST /decisions/{id}/evidence` | Add evidence during the window | Buyer, supplier | Allowed while the window is `OPEN` or a request is `OPEN` |
| `POST /decisions/{id}/waive-window` | Waive further evidence | Buyer, supplier | Window closes (`CLOSED_WAIVED`) only when both have waived |
| `GET /mod/acceptance-queue` | Review queue with `window_status` | Moderator | Filters `ready`, `window_open`, `high_value` |
| `POST /mod/decisions/{id}/confirm` | Confirm acceptance | Moderator, not a party | `review_status = SUBMITTED_FOR_REVIEW`; window `CLOSED_ELAPSED` or `CLOSED_WAIVED`; no `OPEN` evidence request; supplier delivery photographs `SUBMITTED` or `SUBMITTED_LATE`, or `OVERDUE` with `acknowledge_missing_supplier_photos: true` and `acknowledgement_reason`; body `{reason, evidence_ids_considered[], acknowledge_missing_supplier_photos?, acknowledgement_reason?}` (L7, A4). Creates one payout with amount = allocation: `PENDING_APPROVAL` when at or above the threshold (or flagged), otherwise `QUEUED` |
| `POST /mod/decisions/{id}/request-evidence` | Ask a party for more | Moderator | Body `{party, question, due_at}`; review becomes `EVIDENCE_REQUESTED` (A2); both parties notified |
| `POST /mod/decisions/{id}/open-dispute` | Convert to a dispute | Moderator | Body `{reason_code, reason}`; review becomes `DISPUTED`; lots `IN_DISPUTE` (A5) |
| `POST /mod/decisions/{id}/extend-window` | Extend once | Moderator | Body `{reason, extra_hours}`; both notified; `409` if already extended |

Acceptance review is handled through the moderator confirmation, evidence-request and dispute-opening endpoints defined above.

Confirm response for the 3 accepted phones (₦660,000 is above the ₦500,000 threshold, so the payout waits for a different senior moderator):

```json
{ "data": { "decision_id": "dec_1", "review_status": "CONFIRMED",
  "lots": [ { "lot_id": "lot_c", "units": [1, 3], "item_status": "ACCEPTED_CONFIRMED", "money_status": "RELEASE_PENDING" } ],
  "payout": { "id": "po_1", "status": "PENDING_APPROVAL", "amount_kobo": 66000000, "origin_type": "ACCEPTANCE_CONFIRMATION", "approval_required_from": "SENIOR_MODERATOR_OTHER_THAN_DECIDER" } } }
```

Approval then returns `{ "payout": { "id": "po_1", "status": "QUEUED" } }`; submission and success follow asynchronously (`SUBMITTED`, `SUCCEEDED`). The refund of ₦440,000 for the 2 rejected phones is below the threshold and is created `QUEUED` directly when the supplier confirms the return.

Submit decisions: 5 phones, accept 3, reject 2.

```json
{ "lines": [
  { "item_id": "it_1", "quantity_submitted": 5, "quantity_accepted": 3, "quantity_rejected": 2,
    "accept_evidence_ids": ["ev_401", "ev_402", "ev_403"],
    "reject": { "agreed_condition_ids": ["c_2"], "reason_text": "Two units are 64GB, not 128GB", "evidence_ids": ["ev_404"] } } ] }
```

Response `200`:

```json
{ "data": { "submission_id": "sub_1", "decisions": [ { "id": "dec_1", "item_id": "it_1", "quantity_accepted": 3, "quantity_rejected": 2,
    "review_status": "SUBMITTED_FOR_REVIEW", "evidence_window": { "status": "OPEN", "ends_at": "2026-11-19T10:00:00Z" } } ],
  "lots": [ { "lot_id": "lot_c", "units": [1, 3], "item_status": "SUBMITTED_FOR_REVIEW", "money_status": "HELD" },
            { "lot_id": "lot_d", "units": [4, 5], "item_status": "REJECTED_AWAITING_RETURN", "money_status": "HELD" } ],
  "next_steps": { "return_required_for": [ { "item_id": "it_1", "quantity": 2 } ] } } }
```

Errors: `422 EVIDENCE_REQUIRED`; `422 REJECTION_REASON_NOT_IN_AGREEMENT` (missing or foreign condition id); `409 ITEM_NOT_DECIDABLE` (lists offenders); `409 ITEM_ALREADY_DECIDED`; `422 QUANTITY_EXCEEDS_AVAILABLE`.

Early moderator confirm attempt:

```json
{ "error": { "code": "EVIDENCE_WINDOW_OPEN", "message": "Evidence window is open until 2026-11-19T10:00:00Z or until both parties waive.",
  "details": { "window_ends_at": "2026-11-19T10:00:00Z", "waivers": { "BUYER": false, "SUPPLIER": false } } } }
```

## 9. Returns

| Method and URL | Purpose | Authz | Rules |
|---|---|---|---|
| `POST /transactions/{id}/returns` | Open return for rejected quantities | Buyer | Lots `REJECTED_AWAITING_RETURN`; body `{lines:[{item_id, quantity}]}`; return `AWAITING_HANDOVER_EVIDENCE`; no timer (R1) |
| `GET /returns/{id}` | Return with quantities and deadline | Participants, staff | |
| `POST /returns/{id}/handover-evidence` | Submit handover evidence; starts the 72h timer | Buyer | Body `{evidence_ids[] (at least 1 photo), courier_name, rider_name, rider_phone, tracking_ref?, note}`. All evidence objects must be uploaded, hash-verified and linked. The server sets `handover_evidence_uploaded_at` to its commit time and `confirmation_deadline_at = that + 72h` (R2, L9). `409 HANDOVER_ALREADY_SUBMITTED` |
| `POST /returns/{id}/additional-evidence` | Further evidence | Buyer, supplier | Never resets or extends the timer |
| `POST /returns/{id}/confirm-receipt` | Supplier confirms quantities | Supplier | Body `{lines:[{item_id, quantity_received, condition_matches_original}], evidence_ids[], note}`. Only the stated quantity becomes `RETURN_RECEIPT_CONFIRMED` (L10); a partial confirmation never marks the whole return confirmed; `condition_matches_original: false` is treated as a contest of that quantity |
| `POST /returns/{id}/contest` | Supplier contests | Supplier | Body `{lines:[{item_id, quantity, reason_code}], note, evidence_ids[]}` with `reason_code` in `WRONG_ITEM`, `DAMAGED`, `QUANTITY_MISMATCH`, `NOT_RECEIVED`. Opens or extends the return's dispute immediately (L12); no refund |
| `GET /transactions/{id}/payouts`, `GET /transactions/{id}/refunds` | Instruction status | Participants (own amounts), staff | Read only |

Handover response:

```json
{ "data": { "return_id": "ret_1", "status": "HANDOVER_RECORDED",
  "handover_evidence_uploaded_at": "2026-11-20T08:15:00Z", "confirmation_deadline_at": "2026-11-23T08:15:00Z",
  "lots": [ { "lot_id": "lot_d", "units": [4, 5], "item_status": "RETURN_HANDOVER_RECORDED" } ] } }
```

Partial confirmation request and response:

```json
{ "lines": [ { "item_id": "it_1", "quantity_received": 1, "condition_matches_original": true } ], "evidence_ids": ["ev_501"], "note": "One phone received" }
```

```json
{ "data": { "return_id": "ret_1", "status": "HANDOVER_RECORDED",
  "quantities": [ { "item_id": "it_1", "returned": 2, "confirmed": 1, "contested": 0, "outstanding": 1 } ],
  "lots": [ { "units": [4, 4], "item_status": "RETURN_RECEIPT_CONFIRMED", "money_status": "REFUND_PENDING" },
            { "units": [5, 5], "item_status": "RETURN_HANDOVER_RECORDED", "money_status": "HELD" } ],
  "confirmation_deadline_at": "2026-11-23T08:15:00Z" } }
```

## 10. Disputes (parties)

| Method and URL | Purpose | Authz | Rules |
|---|---|---|---|
| `POST /transactions/{id}/disputes` | Raise a manual dispute | Buyer, supplier | Body `{reason_code, lines:[{item_id, quantity}], statement}`; `409 DISPUTE_ALREADY_OPEN` (D1) |
| `GET /disputes/{id}` | Dispute view | Participants | Both sides' evidence, window end, affected quantities, funds status; no internal notes |
| `POST /disputes/{id}/evidence` | Evidence or statement | Participants | Allowed while the window is `OPEN` or a request is `OPEN`; `kind` `STATEMENT` or `CONSENT` |
| `POST /disputes/{id}/waive-window` | Waive further evidence | Participants | Closes only when both waive |

Dispute opened automatically for a missed dispatch deadline (what both parties receive in `GET /disputes/{id}`):

```json
{ "data": { "id": "dis_1", "reason_code": "NO_DISPATCH", "origin": "AUTO", "status": "EVIDENCE_WINDOW_OPEN",
  "affected": [ { "item_id": "it_1", "units": [4, 5], "quantity": 2 } ],
  "funds": { "status": "HELD", "is_frozen": true, "amount_kobo": 44000000 },
  "evidence_window": { "status": "OPEN", "ends_at": "2026-11-16T09:30:00Z" } } }
```

## 11. Moderator and admin

| Method and URL | Purpose | Authz | Rules |
|---|---|---|---|
| `GET /mod/disputes` | Queue (`status`, `high_value`, `window_closing`) | Moderator | |
| `GET /mod/disputes/{id}` | Full case file | Moderator, not a party | Agreement version, per-quantity timeline, evidence flags, prior disputes |
| `POST /mod/disputes/{id}/assign` | Take or assign | Moderator, senior | |
| `POST /mod/disputes/{id}/request-evidence` | Ask a party for more | Moderator | `{party, question, due_at}`; dispute `AWAITING_EVIDENCE` (D3) |
| `POST /mod/disputes/{id}/notes` | Internal note | Moderator | Append-only |
| `POST /mod/disputes/{id}/hold`, `POST /mod/disputes/{id}/resume` | Keep under review / resume | Moderator | `{reason}` (D5, D6) |
| `POST /mod/disputes/{id}/extend-window` | Extend once | Moderator | `{reason, extra_hours}` |
| `POST /mod/disputes/{id}/decision` | Record decision | Moderator, not a party | Window `CLOSED_ELAPSED` or `CLOSED_WAIVED`, no `OPEN` request, per-quantity outcomes, reason and evidence ids (D7). All staff routes use the `/mod` prefix. |
| `POST /mod/payouts/{id}/approve`, `POST /mod/refunds/{id}/approve` | Approve an instruction that is `PENDING_APPROVAL` (any origin, including dispute decisions) | Senior moderator, different from the deciding moderator | `PENDING_APPROVAL` to `QUEUED`; authorisation journal posts (F2, D8); `403 SELF_APPROVAL_NOT_ALLOWED`; `409 INSTRUCTION_NOT_PENDING_APPROVAL` |
| `POST /mod/payouts/{id}/decline`, `POST /mod/refunds/{id}/decline` | Decline approval | Senior moderator, different from the deciding moderator | Body `{reason}`; instruction `CANCELLED`; a dispute opens or the origin dispute returns to `MODERATOR_REVIEW` (F9, L22, D9) |
| `POST /mod/evidence/{id}/flag` | Mark suspicious | Moderator | Append-only |
| `GET /mod/financial-exceptions` | Failed or `UNKNOWN` instructions, reconciliation mismatches, unsecured payments, overpayments | Senior moderator | Reads `financial_exceptions` |
| `POST /mod/financial-exceptions/{id}/resolve` | Resolve a failed or unknown instruction or a reconciliation mismatch | Senior moderator | Body `{action, reason}` with `RETRY` or `RELEASE` (definitively failed instruction, F10), `CONFIRM_NOT_EXECUTED` (unknown instruction, F7), `RECORD_ADJUSTMENT` (compensating entry). `RETRY` first reverses the old authorisation journal and releases its reservation, then creates a linked instruction through the full guard and approval rules. `409 OUTCOME_UNKNOWN_RECONCILE_FIRST` for `RETRY` or `RELEASE` on an `UNKNOWN` instruction |
| `GET/PUT /admin/config/{key}`, `POST /admin/staff`, `PATCH /admin/staff/{id}`, `GET /admin/audit`, `POST /admin/reconciliation/run` | Configuration, staff and audit access; start a reconciliation run | Admin | Configuration applies to future transactions only. Admins have no endpoint that creates, approves, retries or resolves any financial instruction or exception |

*Dispute decision request* (no dispatch for units 4 and 5; refund). There is no amount field to choose: the backend computes `44000000` kobo from the lot allocations. If a client sends `amount_kobo` it must equal that value.

```json
{ "outcomes": [ { "item_id": "it_1", "lot_id": "lot_b", "quantity": 2, "outcome": "REFUND" } ],
  "reason": "No dispatch evidence for units 4 and 5 within 72 hours. Tracking GIG-778812 covers only the three dispatched units according to the courier record supplied by the buyer. Supplier did not respond to the evidence request.",
  "evidence_ids_considered": ["ev_31", "ev_32"] }
```

Response. ₦440,000 is below the ₦500,000 threshold, so the refund is created `QUEUED`; the lots stay `IN_DISPUTE`, frozen, with `REFUND_PENDING`:

```json
{ "data": { "decision_id": "md_1", "dispute_status": "DECIDED", "requires_second_approval": false,
  "instructions": [ { "type": "REFUND", "id": "ref_1", "status": "QUEUED", "amount_kobo": 44000000, "origin_type": "DISPUTE_DECISION" } ],
  "lots": [ { "lot_id": "lot_b", "item_status": "IN_DISPUTE", "money_status": "REFUND_PENDING", "is_frozen": true } ] } }
```

The same decision covering all 5 units (`110000000` kobo, above the threshold) returns `"dispute_status": "AWAITING_SECOND_APPROVAL"`, `"requires_second_approval": true` and the refund `PENDING_APPROVAL`; a different senior moderator then calls `POST /mod/refunds/{id}/approve`.

Validation: outcome lines refer to whole lots (split a lot by quantity first); for `RELEASE` and `REFUND` the amount is computed from the lot allocation, and a supplied `amount_kobo` that differs is `422 AMOUNT_MUST_MATCH_ALLOCATION` with `details.expected_amount_kobo` (amount-level settlement is not supported, [OD-14](../06-delivery/OPEN_DECISIONS.md)); `REDISPATCH_OR_REPLACEMENT` and `NO_FINANCIAL_ACTION` carry no amount; reason at least 50 characters; the same lot cannot have both `RELEASE` and `REFUND`; partial settlement is expressed by `RELEASE` and `REFUND` lines over different quantity lots; `REDISPATCH_OR_REPLACEMENT` needs `consent_statement_ids` from both parties and `new_dispatch_deadline_at` ([OD-10](../06-delivery/OPEN_DECISIONS.md)); the moderator is not a party; a lot frozen by another dispute, a provider-unknown or a reconciliation freeze returns `409 LOT_FROZEN`; the evidence window must be closed or waived (`409 EVIDENCE_WINDOW_OPEN`).

## 12. Evidence uploads

| Method and URL | Purpose | Authz | Rules |
|---|---|---|---|
| `POST /evidence/upload-urls` | Get signed upload URLs | Participant or assigned staff | Body `{transaction_id, purpose, files:[{filename, mime_type, size_bytes, sha256}]}`; returns `{uploads:[{evidence_id, upload_url, expires_in}]}`; `415`, `413`, `422 EVIDENCE_NOT_ALLOWED_IN_STATE` |
| `POST /evidence/{id}/complete` | Confirm upload finished | Uploader | Server re-hashes the object; mismatch `422 HASH_MISMATCH`; sets `server_received_at`; computes flags |
| `GET /evidence/{id}` | Metadata and short-lived view URL | Participants, staff | Both parties see each other's evidence once a review window or dispute is open (PROPOSED); before that only their own plus dispatch and delivery evidence |

There is no endpoint to delete or edit evidence.

## 13. Notifications

`GET /notifications`, `POST /notifications/{id}/read`, `GET/PUT /me/notification-preferences` (security-critical notices cannot be disabled). Catalogue: [Notifications and Deadlines](../04-operations/NOTIFICATIONS_AND_DEADLINES.md).

## 14. `allowed_actions` vocabulary

| Actor | Values |
|---|---|
| Supplier | `edit`, `send`, `cancel`, `dispatch`, `upload_delivery_photos`, `add_review_evidence`, `waive_window`, `confirm_return_receipt`, `contest_return`, `raise_dispute`, `add_dispute_evidence` |
| Buyer | `agree`, `pay`, `refresh_payment`, `cancel`, `upload_delivery_photos`, `confirm_received`, `submit_decisions`, `add_review_evidence`, `waive_window`, `open_return`, `submit_handover_evidence`, `raise_dispute`, `add_dispute_evidence` |

The UI renders buttons only from this list.

## 15. Alignment with the state machine

| Endpoint | Transition(s) | Statuses returned |
|---|---|---|
| `POST /transactions/{id}/send` | T1 | `AWAITING_BUYER_AGREEMENT` |
| `POST .../agreement/confirm` | T2 | `AWAITING_PAYMENT` |
| `POST .../payments`, webhook, refresh | P1 to P9, T4 | `PENDING`, `PAYMENT_RECEIVED`, `FUNDS_SECURED`, `FAILED`, `EXPIRED`, `UNKNOWN`; `FUNDED` |
| `POST .../dispatch` | L2 | `DISPATCHED`, `AWAITING_DISPATCH` |
| Scanner | L3 | `IN_DISPUTE`, dispute `EVIDENCE_WINDOW_OPEN` |
| `POST .../delivery/evidence` (buyer or supplier) | DP3, DP5 | Requirement `SUBMITTED` or `SUBMITTED_LATE`; no lot status change |
| Scanner (delivery-photo deadlines) | DP4 | Requirement `OVERDUE`; nothing decided automatically |
| `POST .../delivery/confirm-received` | L4, DP2 | `RECEIPT_CONFIRMED`; buyer requirement `PENDING` |
| `POST .../decisions` | L5, L6, A1 | `SUBMITTED_FOR_REVIEW`, `REJECTED_AWAITING_RETURN` |
| `POST /mod/decisions/{id}/confirm` | L7, A4 | `CONFIRMED`, `ACCEPTED_CONFIRMED` |
| `POST /mod/decisions/{id}/request-evidence` | A2 | `EVIDENCE_REQUESTED` |
| `POST /mod/decisions/{id}/open-dispute` | A5, L14 | `DISPUTED`, `IN_DISPUTE` |
| `POST .../returns`, `.../handover-evidence` | R1, R2, L9 | `AWAITING_HANDOVER_EVIDENCE`, `HANDOVER_RECORDED`, `RETURN_HANDOVER_RECORDED` |
| `POST /returns/{id}/confirm-receipt` | L10, R3 | `RETURN_RECEIPT_CONFIRMED` |
| `POST /returns/{id}/contest` | L12, R4 | `IN_DISPUTE` |
| `POST /mod/disputes/{id}/decision` | D7, L17 to L21 | `DECIDED` or `AWAITING_SECOND_APPROVAL`; instructions `QUEUED` or `PENDING_APPROVAL` |
| `POST /mod/payouts/{id}/approve`, `POST /mod/refunds/{id}/approve` | F2, D8 | Instruction `QUEUED`; dispute `DECIDED` |
| `POST /mod/payouts/{id}/decline`, `POST /mod/refunds/{id}/decline` | F9, L22, D9 | Instruction `CANCELLED`; lots `IN_DISPUTE`; dispute `MODERATOR_REVIEW` or opened |
| Instruction worker | F1 to F8, L8, L11 | `SETTLED_RELEASED`, `SETTLED_REFUNDED` |

## 16. Related documents

[State Machine](TRANSACTION_STATE_MACHINE.md) · [Database](DATABASE_SPECIFICATION.md) · [Evidence and Files](../04-operations/EVIDENCE_AND_FILE_HANDLING.md) · [Acceptance Criteria](../05-quality/ACCEPTANCE_CRITERIA.md)
