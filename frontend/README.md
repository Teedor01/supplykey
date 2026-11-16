# SupplyKey Frontend

A responsive React frontend for the SupplyKey buyer, supplier, moderator and admin-audit workspaces. The documentation package at `../supplykey-docs/` is the source of truth for product rules, transaction states, evidence requirements and API contracts.

## Prerequisites

- Node.js 20.19+ or 22.12+
- npm 10+

## Install and run

Windows Command Prompt:

```bat
cd frontend
copy .env.example .env.local
npm install
npm run dev
```

PowerShell:

```powershell
Set-Location frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

Linux/macOS:

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

Vite loads `.env.local` automatically. Restart the Vite server after changing it. Never put backend or Paga secrets in frontend environment files.

Vite prints the local development URL (normally `http://localhost:5173`). In mock mode, use the demo role switcher on the sign-in page to explore Buyer, Supplier, Moderator and Senior Moderator workspaces. Buyer and Supplier are workspace personas for the backend's `USER` role; they are not separate privileged backend roles. An authenticated live user can switch their transaction workspace from the account menu. Demo data is stored in browser `localStorage`; use **Reset demo data** in the demo banner to restore the sample records. Demo registration accepts a six-digit placeholder OTP and must not be mistaken for real identity verification.

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `VITE_API_MODE` | `mock` uses the local development adapter; `live` uses the REST API | `mock` |
| `VITE_API_BASE_URL` | Backend API origin, e.g. `https://api.example.com` | empty |

Set `VITE_API_MODE=live` and `VITE_API_BASE_URL` to the backend origin only, for example `http://localhost:8000`. Do not include `/api/v1` or another path: the API client owns and appends `/api/v1` exactly once, and rejects a base URL containing a path. The API client sends bearer access tokens where available and includes cookies for refresh/session operations. The backend must implement the routes and request/response contracts in `supplykey-docs/03-technical-specs/API_SPECIFICATION.md`. Generate and validate OpenAPI with the backend before treating the integration as production-ready.

## Commands

```bash
npm run dev          # local development server
npm run build        # production build in dist/
npm run preview      # serve production build locally
npm test             # dependency-free Node API contract/regression tests
npm run check:contracts # end-to-end mock adapter smoke workflow
npm run test:vitest  # optional Vitest unit tests (requires installed dependencies)
```

## Architecture

- `src/App.jsx` — role-specific shell, pages, workflows and reusable interface components.
- `src/lib/api.js` — API client and replaceable mock adapter. All state-changing POST requests receive an idempotency key; ambiguous provider outcomes retain the key for safe retry/reconciliation.
- `src/lib/constants.js` — documented frontend enums, Nigerian states, WAT date formatting and kobo formatting.
- `scripts/api-contract.test.mjs` — Node built-in contract/regression tests.
- `scripts/smoke-check.mjs` — end-to-end mock workflow checks covering multi-item agreements, pending payment, evidence, partial dispatch/receipt, rejection/return and senior approval.
- `src/test/constants.test.js` — optional Vitest unit-test source.
- `src/styles.css` — responsive design system and component styles.

The UI displays server-authoritative transaction statuses and `allowed_actions` when provided. The local adapter is explicitly a development/demo tool, not a financial simulator. Payment attempts remain `PENDING`; it never marks funds secured, executes payouts/refunds or claims settlement. A production backend must supply signed upload URLs, validate file content and hashes, link verified evidence to actions, enforce role permissions, deadlines and state transitions, and return authoritative transaction data.

## API integration notes

The client uses documented routes for authentication/MFA/OTP, `/me`, profile and payout-account updates, transaction creation/retrieval, invitation preview/acceptance, agreement version history/confirmation, idempotent payments, dispatch, delivery evidence, receipt confirmation, per-quantity decisions, returns, disputes, signed evidence uploads, notifications/read state, moderator case queues, evidence review, financial exceptions, and payout/refund instruction reads. Successful login, OTP and MFA responses are followed by `GET /me`; the API specification does not promise a `user` object in the token response. Decision and moderator actions forward `If-Match` when the backend supplies a resource version. The documented `PATCH /transactions/{id}` route does not specify its request body, so draft/revised-term editing is not exposed. The API also does not define a read endpoint for the second-approval queue; the senior-approval queue is demonstrated only in the mock adapter. Notification preference fields are not specified, so only notification listing and read state are implemented.

## Security and financial boundaries

- Monetary amounts are represented as integer kobo in API payloads and formatted as naira only for display.
- The frontend does not calculate final transaction allocations or decide whether funds are secured.
- A payment status of `PAYMENT_RECEIVED` is not presented as funds secured; only the backend's confirmed `FUNDS_SECURED` + `FUNDED` response may establish that state.
- Unknown payment outcomes remain unresolved; the frontend does not display them as confirmed failures or successes and retains the original idempotency key for the same request.
- No provider adapter or Paga integration is implemented in the frontend. The local adapter creates only `PENDING` payment attempts; the backend must use its documented simulated provider and return authoritative statuses.
- No real-money custody, payout or settlement is performed by this frontend.
- Evidence is uploaded through the documented signed-upload protocol in live mode. A missing signed URL or failed object-store PUT stops completion; the frontend does not call the evidence-complete endpoint unless the file upload succeeded.

## Known limitations and launch dependencies

This is an implementation source package, not a deployed or production-verified application. It has not been connected to a running backend. The documentation's Open Decisions remain open; recommended values are not treated as approved business rules. The local adapter exercises participant and moderator paths, partial quantity handling, evidence requests, review windows, return instructions and high-value second approval, but it is not a complete backend/state-machine emulator. It does not run deadline scanners, reminders, provider webhooks, reconciliation, KYC, real bank verification, or settlement. Draft editing/revised terms are not exposed because the PATCH body is unspecified. The live second-approval queue cannot be populated until the backend publishes a read endpoint. Notification preferences, admin configuration/staff management, payout-account verification cooldown enforcement, and participant-wide activity feed are not implemented. The admin role is kept separate from moderator permissions; only the documented admin audit read is exposed in this frontend.

The following documented decisions remain open and must not be treated as approved policy: OD-01 through OD-17 in `supplykey-docs/06-delivery/OPEN_DECISIONS.md`, including inspection and evidence windows, partial dispatch recommendation, return handover window, delivery-fee allocation/refund policy, platform fee, high-value threshold, KYC tiers, cancellation after funding, redispatch consent/deadline, retention, operating defaults, moderator staffing/SLA, amount-level adjustment, return timer commit event, delivery-photo deadline consequences, and payout-account change cool-down.

External launch gates EXT-01 to EXT-03 remain unresolved: written provider confirmation for conditional holding/collection/payout/refund and related capabilities; written Nigerian legal advice; and confirmed provider fees, limits and settlement rules. The real-money provider adapter is deliberately not implemented.

## Verification

See `AUDIT_REPORT.md` and `BUILD_REPORT.md` for exact checks, results and limitations. Node contract tests, mock workflow smoke checks and TypeScript JSX parse checks are runnable without installing frontend dependencies. A Vite production build and browser tests still require the dependencies to be installed in an environment with npm registry access.

## Connect to the SupplyKey backend

The project root contains the FastAPI backend. Start it from `backend/` using the instructions in `backend/README.md`, then configure this frontend:

```bash
cp .env.example .env.local
# Ensure .env.local contains:
# VITE_API_MODE=live
# VITE_API_BASE_URL=http://localhost:8000
npm install
npm run dev
```

The frontend appends `/api/v1` exactly once to the origin-only `VITE_API_BASE_URL`. The backend allows the default Vite origins (`localhost:5173` and `127.0.0.1:5173`). Set `VITE_API_MODE=mock` and leave the base URL empty to use the local sample adapter instead.

Live registration returns a development OTP (`000000`) only when the backend `APP_ENV` is not `production`. Staff demo accounts require TOTP; see `backend/README.md`. The frontend's live Senior Moderator approval queue remains unavailable because the source API specification defines approval actions but no approval-queue read endpoint.
