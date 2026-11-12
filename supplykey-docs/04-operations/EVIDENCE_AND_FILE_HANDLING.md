# Evidence and File Handling

| Version | Status | Last updated |
|---|---|---|
| 0.3.1 | Corrected review, for team sign-off | 2026-11-12 |

What counts as evidence, how files are uploaded, validated, linked, flagged and viewed, and the separate delivery-photograph workflows for buyer and supplier. Evidence is traceable to transaction, item, party and event.

## 1. Principles

- A photograph is evidence, not proof of physical receipt (SK-08). Uploading delivery photographs never changes any status; the buyer separately confirms receipt ([API 7](../03-technical-specs/API_SPECIFICATION.md#7-delivery-evidence-and-receipt)).
- Evidence is immutable and never deleted. An admin can mark a file `RETIRED` with a reason; the original is kept.
- Every file is linked to a transaction, optionally an item, the uploading party and the event it supports.

## 2. Evidence types and who may upload

| Purpose | Who | Required by |
|---|---|---|
| `DISPATCH` | Supplier | Dispatch declaration (at least one) |
| `DELIVERY_PHOTO` | Buyer and supplier, each against their own requirement | Both are required (confirmed). Supplier: one submission per dispatch. Buyer: one submission per receipt confirmation and before accepting or rejecting. Deadlines and consequences are PROPOSED ([OD-16](../06-delivery/OPEN_DECISIONS.md)) |
| `ACCEPTANCE_PROOF` | Buyer | Acceptance (at least one, mandatory) |
| `REJECTION_PROOF` | Buyer | Rejection (at least one, mandatory) |
| `RETURN_HANDOVER` | Buyer | Handover evidence (at least one photo, mandatory) |
| `RETURN_RECEIPT` | Supplier | Return receipt confirmation (mandatory) |
| `DISPUTE` | Buyer, supplier | Any time while a window or request is open |
| `EVIDENCE_REQUEST_RESPONSE` | Requested party | Moderator request |
| `OTHER` | Any party | Messages, courier receipts, rider details documents |

Structured data (rider name and phone, courier, tracking reference, serial or IMEI) is stored on the event, not only as photographs.

## 3. Upload protocol

1. `POST /evidence/upload-urls` with filename, MIME type, size, SHA-256 and purpose. The server checks authorisation and state, creates `evidence_files` rows (`UPLOADING`) and returns signed URLs.
2. The client uploads directly to the private bucket (client-side compression for images; retry with resume on weak networks).
3. `POST /evidence/{id}/complete`. The server re-hashes the stored object, sniffs the content type, extracts EXIF, sets `server_received_at`, computes flags and sets the file `VERIFIED`. A file that fails verification stays `UPLOADING` and cannot be used.
4. The business action (dispatch, decisions, handover, dispute evidence and so on) references `evidence_ids`. Only `VERIFIED` files are accepted; the server links them (`LINKED`) as part of the transition.

An unlinked file is not evidence for any event. The 72-hour return timer starts only when the handover submission commits with validated, linked evidence, not at the Return button and not at the claimed photograph time ([State Machine L9](../03-technical-specs/TRANSACTION_STATE_MACHINE.md#43-item-lots-l)). Additional evidence never resets it.

## 4. Validation limits (PROPOSED, configurable)

| Item | Limit |
|---|---|
| Image types | JPEG, PNG, HEIC |
| Documents | PDF |
| Video | Short MP4 |
| Size | 10 MB per image, 20 MB per PDF, 50 MB per video |
| Count | 20 files per submission |
| Antivirus scan | RECOMMENDED before any viewing |

Rejections return `413 FILE_TOO_LARGE`, `415 UNSUPPORTED_FILE_TYPE`, `422 HASH_MISMATCH`, `422 EVIDENCE_NOT_ALLOWED_IN_STATE`.

## 5. Metadata and flags

Stored: SHA-256, size, MIME type, EXIF (informational only), `server_received_at` (the trusted time), uploader, party role, purpose, linked entity.

| Flag | Set when |
|---|---|
| `DUPLICATE_HASH` | Same SHA-256 exists in any other transaction or for another event in this one |
| `NO_EXIF` | No capture metadata |
| `EXIF_TIME_MISMATCH` | Capture time outside a plausible window for the event |
| `MODERATOR_SUSPICIOUS` | Moderator flagged it |

Flags inform the moderator; they never decide anything automatically. Perceptual-hash and AI forensics are deferred.

## 6. Visibility

| Who | Sees |
|---|---|
| Uploader | Their own evidence |
| Both parties | Dispatch and delivery evidence; once an acceptance review window or dispute is open, each other's evidence for that case (PROPOSED) |
| Assigned moderators | Everything for the case, with flags |
| Others | Nothing. All access is by short-lived signed URL after an authorisation check |

## 7. Delivery-photograph workflows

Both parties must submit delivery photographs. Each obligation is a `delivery_photo_requirements` row (`PENDING`, `SUBMITTED`, `OVERDUE`, `SUBMITTED_LATE`). A photograph is evidence only: it never changes a lot status and never independently establishes physical receipt. Receipt is a separate buyer action.

| | Supplier | Buyer |
|---|---|---|
| Requirement created | At each dispatch declaration | At each receipt confirmation |
| Action | `POST /transactions/{id}/delivery/evidence` with `requirement_id` | Same endpoint with the buyer's `requirement_id` |
| Due (PROPOSED) | Dispatch time + agreed expected delivery days + 24h; earlier (receipt confirmation time + 24h) if the buyer confirms receipt first | Before accepting or rejecting; by `inspection_deadline_at` once OD-01 is approved |
| Typical content | Goods as dispatched, courier handover note or proof of delivery, rider | Boxes, seals, each unit, labels, serial or IMEI where relevant |
| If not submitted | Requirement `OVERDUE`; moderators and both parties see it; a moderator cannot confirm acceptance while `PENDING` and needs an audited acknowledgement while `OVERDUE`; late upload becomes `SUBMITTED_LATE` | `422 DELIVERY_PHOTOS_REQUIRED` on acceptance or rejection; at the inspection deadline `OVERDUE` and the inspection-expiry review applies |
| Never | Pay, refund, accept, reject or open a dispute automatically | Same |

UX guidance: show each party their own requirement, due date (WAT) and what to photograph; show the other party's status read-only.

## 8. Retention

Retention periods for evidence and financial records are OPEN ([OD-11](../06-delivery/OPEN_DECISIONS.md)); RECOMMENDED starting point is 7 years for financial and dispute records, subject to legal advice (VERIFY).

## 9. Related documents

[Database 7](../03-technical-specs/DATABASE_SPECIFICATION.md#7-evidence-windows-and-requests) · [Security Architecture](../02-architecture/SECURITY_ARCHITECTURE.md) · [Dispute and Moderation](DISPUTE_AND_MODERATION.md)
