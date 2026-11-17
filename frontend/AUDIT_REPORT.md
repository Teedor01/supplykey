# SupplyKey Frontend Engineering Audit

**Audit date:** 16 November 2026  
**Audited package:** `SupplyKey-Documentation-and-Frontend.zip` (previous delivery)  
**Documentation authority:** `../supplykey-docs/`  
**Scope:** frontend source, API client/mock adapter, UI flows, configuration, test sources and documentation/API alignment.

## Executive summary

The audit found and corrected multiple defects in the frontend/API boundary and moderator workflows. The most important fixes were universal POST idempotency, authentication profile hydration from `GET /me`, evidence selection in moderator decisions, redispatch deadline capture, separation of the `ADMIN` role from moderator permissions, and safer signed-file upload completion. Transaction, moderator queue, financial-exception and admin-audit collection reads now support cursor pagination where the API returns `meta.next_cursor`.

The corrected project passes five dependency-free Node contract tests, the existing end-to-end mock workflow smoke script, and TypeScript's JavaScript/JSX syntax/module parse check. A production build, Vitest run and browser run remain **blocked**, not passed: npm cannot resolve `registry.npmjs.org` in this environment (`EAI_AGAIN`), so Vite/React/Vitest packages are not installed. No live backend was supplied, so no real API integration or backend-authoritative workflow can be certified.

## A. Findings and corrections

| ID | Severity | Finding | Affected area | Correction / disposition |
|---|---|---|---|---|
| AUD-01 | High | Only payment POST requests received an `Idempotency-Key`; other state-changing POST calls could be duplicated on retry. | `src/lib/api.js` | Added automatic idempotency keys to all POST requests, stable per method/path/body in the browser session, retained for ambiguous payment outcomes, and replay protection in the mock adapter. |
| AUD-02 | High | Login, OTP and MFA handlers expected a `user` object in token responses, but the API contract returns tokens/MFA status and defines `GET /me` for profile hydration. Live login could throw or select the wrong role. | `src/App.jsx` | All successful login/OTP/MFA flows now fetch `/me`; session restore hydrates the authenticated user and staff role. Buyer/Supplier are persisted workspace views for backend `USER`; `ADMIN` is not mapped to moderator. |
| AUD-03 | High | Moderator confirmation and dispute decisions always sent `evidence_ids_considered: []`; the UI had no evidence-selection control despite the moderation record requiring evidence considered. | `src/App.jsx` | Added per-evidence selection in the case file and submit the selected evidence IDs. |
| AUD-04 | High | `REDISPATCH_OR_REPLACEMENT` required both parties' consent IDs and a new dispatch deadline, but the UI only sent consent IDs. | `src/App.jsx` | Added a required date/time input and sends `new_dispatch_deadline_at`; the outcome is only offered when two consent statements are present in the case data. |
| AUD-05 | High | `ADMIN` fell through to moderator navigation, risking presentation of moderator controls to a role explicitly denied financial execution rights. | `src/App.jsx` | Admin has a separate audit-only navigation/read path. Admin is never treated as Moderator or Senior Moderator. Admin configuration/staff management is not implemented. |
| AUD-06 | High | If the signed upload response lacked `upload_url`, the client skipped object-storage PUT and still called evidence completion. | `src/lib/api.js` | Live mode now refuses to complete evidence without a signed URL and a successful PUT. |
| AUD-07 | Medium | Return actions could construct URLs with a fabricated `return-id` when the backend omitted the identifier. A dead return-receipt modal only closed without calling the API. | `src/App.jsx` | Removed placeholder IDs; actions are disabled until a real return ID exists; removed the inert modal form. |
| AUD-08 | Medium | Payout-account form expected a nested `payout_account` response, while the documented response can be a flat object. | `src/App.jsx` | Accepts either the wrapped mock response or the flat documented API response. |
| AUD-09 | Medium | Decision/moderator actions did not forward resource versions through `If-Match`. | `src/App.jsx` | Case actions now forward `If-Match` when a version is present in the returned case/decision data; instruction approval and exception actions do the same when available. |
| AUD-10 | Medium | Collection reads ignored `meta.next_cursor`, so transaction lists could stop at the first API page. | `src/lib/api.js`, `src/App.jsx` | Added cursor pagination for transactions, moderator acceptance/dispute queues, senior financial exceptions and admin audit events. |
| AUD-11 | Medium | The previous smoke test compared `duplicatePayment.id` values even though the payment response uses `payment_id`; both were `undefined`, creating a false-positive assertion. | `scripts/smoke-check.mjs` | Assertion now compares `payment_id` and verifies only one payment attempt exists. Added changed-body/idempotency-key protection. |
| AUD-12 | Low | Registration sent empty email/phone fields even when the user supplied only one contact method. | `src/App.jsx` | Empty contact properties are omitted from the registration payload. |
| AUD-13 | Low | Logout, invitation acceptance and notification-read calls sent unnecessary empty JSON bodies where the API has no body. | `src/App.jsx`, `src/lib/api.js` | Those calls now omit the body while still receiving idempotency keys. |
| AUD-14 | High | Mock idempotency records persisted the raw request body as their signature in browser `localStorage`, which could include registration passwords. | `src/lib/api.js` | Replaced raw JSON signatures with a 128-bit non-cryptographic fingerprint; added a regression test asserting the raw password is absent from the idempotency cache. |
| AUD-15 | Medium | The buyer decision form did not locally gate acceptance/rejection on the buyer's required delivery photographs, despite AC-19. | `src/App.jsx` | Decision controls now remain disabled until the buyer photo requirement is submitted; the form offers the outstanding photo upload action. The mock regression workflow verifies the backend rejects decisions before the photo requirement is satisfied. |

### Business-rule review

The corrected frontend continues to treat the backend as authoritative:

- The supplier dispatch UI is driven by backend `allowed_actions`; the mock rejects dispatch unless the transaction is `FUNDED`.
- Delivery photographs remain evidence only; buyer receipt confirmation is a separate action.
- Acceptance/rejection remains a submission for review, not a payout/refund. The mock keeps funds held and creates a moderator review.
- Rejection requires an agreed rejection condition, reason and evidence; return handover evidence is a separate action.
- Supplier return confirmation creates a refund instruction; it does not claim a completed refund. The mock keeps instructions `QUEUED` or `PENDING_APPROVAL`.
- The mock payment provider only returns `PENDING`; it never changes a transaction to `FUNDED` or claims funds secured.
- High-value payout approval requires a different Senior Moderator in the mock; approval moves the instruction to `QUEUED`, never `SUCCEEDED`.
- The UI does not calculate or choose payout/refund amounts. It sends item/lot/quantity outcomes and renders backend instruction states.
- Frozen/disputed lots do not expose ordinary participant actions through the mock's state-derived action list; the live UI relies on server `allowed_actions`.
- The client does not implement dispatch/return deadline scanners. Expiry consequences remain backend worker responsibilities and are not simulated as refunds.

## B. Changes made

- `src/lib/api.js`: central POST idempotency, idempotent mock replay, changed-body conflict, session refresh idempotency, pagination envelope handling, signed-upload URL validation, admin audit read, and API response normalization.
- `src/App.jsx`: `/me` hydration, session restoration, workspace persistence, role separation for Admin, pagination controls, evidence-considered checkboxes, redispatch deadline capture, conditional `If-Match`, flat/wrapped payout response handling, and removal of fabricated return identifiers/inert form.
- `src/styles.css`: pagination/evidence-selection styling.
- `scripts/api-contract.test.mjs`: five dependency-free regression tests.
- `scripts/smoke-check.mjs`: corrected payment replay assertion and expanded idempotency/transaction workflow checks.
- `package.json`: `npm test` now runs Node's built-in test runner without requiring frontend packages; Vitest remains available as `npm run test:vitest` when dependencies are installed.
- `README.md`, `BUILD_REPORT.md`, `AUDIT_REPORT.md`: setup, API limitations and verified results updated.

## C. Test results

| Test / check | Result | Evidence / reason |
|---|---|---|
| `npm install --no-audit --no-fund --fetch-retries=0 --fetch-timeout=15000` | **BLOCKED** | npm registry DNS lookup failed: `EAI_AGAIN registry.npmjs.org`. No dependency installation completed. No lockfile could be generated without resolving package metadata; the supplied project did not contain one. |
| `npm test` | **PASS** | 5 Node contract/regression tests passed: automatic POST idempotency, duplicate replay/body mismatch, funded-only dispatch, and distinct Senior Moderator approval. |
| `node scripts/smoke-check.mjs` | **PASS** | Multi-item create/invitation/agreement, pending payment/idempotency, evidence, partial dispatch/receipt, rejection/return/refund instruction, moderator review and senior approval smoke workflow. |
| `tsc --noEmit --allowJs --checkJs false --jsx react-jsx --target ES2022 --module ESNext --moduleResolution bundler src/App.jsx src/main.jsx src/lib/api.js src/lib/constants.js` | **PASS** | JavaScript/JSX syntax and module parse check. This is not a full typed TypeScript check. |
| `node --check src/lib/api.js` | **PASS** | API client parses successfully. |
| `npm run build` | **BLOCKED** | `vite: not found` because dependencies could not be installed. Production build not verified. |
| `npm run test:vitest` | **BLOCKED** | `vitest: not found` because dependencies could not be installed. |
| Lint | **NOT CONFIGURED** | No ESLint/Biome lint script or config exists in the project. |
| Live API integration | **NOT TESTED** | No running backend, OpenAPI server or credentials were supplied. |
| Browser / Playwright end-to-end | **NOT TESTED** | Chromium and Python Playwright are present, but Vite/React dependencies could not be installed and no built app exists to serve. No browser test is claimed. |
| Responsive visual testing | **NOT TESTED** | Source inspection confirms responsive CSS breakpoints at 1100px, 760px and 420px; actual viewport rendering was not browser-tested. |
| ZIP integrity | **PASS** | Final archive is tested with `unzip -t`. |
| Original documentation preservation | **PASS** | Original documentation and diagram files are copied from the supplied documentation ZIP and SHA-256 compared before packaging. |

## D. Outstanding dependencies and limitations

### Backend/API dependencies

1. No live backend was provided. Validate every live response envelope, error code, auth cookie/CORS policy, resource version, upload signature and pagination cursor against the backend's generated OpenAPI contract.
2. `PATCH /transactions/{id}` is documented without a request schema. Draft editing and revised-term submission remain intentionally unavailable rather than inventing a payload.
3. The API documentation does not define a read endpoint for the senior approval queue. Approval actions are present, but the live senior queue cannot be populated until a read endpoint is specified.
4. The backend must return resource versions for the frontend to send `If-Match`; if omitted, the client cannot invent one.
5. Confirm whether transaction, moderator queue, financial-exception and admin-audit list responses use `data: []` or `data: {items: []}`; the client accepts both and reads `meta.next_cursor`.
6. The moderator app remains in the same SPA/build as the buyer/supplier app. The architecture document recommends two separate apps/builds and a stricter moderator CSP. This architectural separation is not completed in this correction pass and requires a dedicated refactor/build-security task.
7. The frontend is JavaScript, not the architecture document's recommended TypeScript/OpenAPI-generated typed client. This is a recommendation-level deviation; generated API types require a published OpenAPI artifact.
8. Admin audit read is implemented, but admin configuration, staff management and reconciliation controls are not part of the requested buyer/supplier/moderator workflow and are not implemented.
9. Participant-wide activity feed and notification preferences are not implemented because the API contract does not define the necessary response/request shapes. In-app notification listing and read state are supported.
10. The local adapter is a development workflow adapter, not a full state-machine or financial-engine emulator. It does not run the deadline scanners, evidence-window worker, notification outbox, provider webhook resolver, reconciliation or nightly invariant checks.

### Open product and external decisions

Do not treat recommended defaults as approved. OD-01 through OD-17 remain open as documented, including inspection/evidence windows, return handover timing, delivery-fee treatment, platform fees, approval thresholds, KYC, cancellation after funding, redispatch mechanics, retention, staffing/SLA, delivery-photo consequences and payout-account cool-down.

External launch gates remain unresolved:

- EXT-01: written provider confirmation for collection, conditional holding, partial payout/refund, status queries, idempotency, webhooks, settlement reports and sandbox.
- EXT-02: written Nigerian legal advice on fund custody, licensing/partner requirements, KYC/AML and dispute liability.
- EXT-03: confirmed provider fees, limits and settlement rules.

No real-money custody or settlement integration is implemented.

## E. Reproduction commands

```bash
cd frontend
cp .env.example .env
npm install
npm run dev

# Checks that do not require Vite/React packages
npm test
npm run check:contracts

# Once npm dependencies are installed
npm run build
npm run test:vitest
```

The current environment can run `npm test`, `npm run check:contracts`, and the TypeScript JSX parse command. It cannot run the Vite build or browser tests until registry access is restored and dependencies are installed.
