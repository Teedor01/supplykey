# SupplyKey

**Trade with confidence.**

SupplyKey is a protected-transaction platform for buyers and suppliers. It is designed to make transactions more accountable through agreed terms, evidence-backed delivery and acceptance, moderator review, and controlled payout or refund decisions.

The platform provides a structured process for both parties to follow, from creating and accepting a transaction agreement to confirming delivery, resolving disputes, and authorising the appropriate financial outcome.

This repository contains the frontend application, backend implementation, project specifications, and verification reports.

## Project structure

```text
SupplyKey/
├── frontend/             # React and Vite application
├── backend/              # FastAPI application and supporting services
├── supplykey-docs/       # Product, architecture and technical specifications
├── STATUS_REPORT.md      # Current implementation and verification status
└── IMPLEMENTATION_REPORT.md
                           # Implementation scope and known limitations
```

The backend includes payment adapters, a local payment simulator, persistence, automated tests, and a background worker.

The documentation covers product rules, user flows, architecture, API and database specifications, financial state transitions, operational guidance, and acceptance criteria.

## Getting started

### Requirements

- Python and pip
- Node.js and npm
- Git

### 1. Backend setup

Open a terminal in the project root and run:

**Windows Command Prompt**

```bat
cd backend

copy .env.example .env

py -m venv .venv
.venv\Scripts\activate

python -m pip install -r requirements.txt

python -m app.migrate
python -m app.seed

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The API will be available at:

- API: http://localhost:8000
- Interactive API documentation: http://localhost:8000/docs

### 2. Start the background worker

Open a second terminal:

```bat
cd backend
.venv\Scripts\activate

python -m app.worker
```

The worker handles background processing configured by the backend.

### 3. Frontend setup

Open another terminal:

```bat
cd frontend

copy .env.example .env.local

npm install
npm run dev
```

The frontend will be available at the local address printed by Vite.

### 4. Connect the frontend to the backend

To use the live local API, configure `frontend/.env.local`:

```dotenv
VITE_API_MODE=live
VITE_API_BASE_URL=http://localhost:8000
```

**Important:** `VITE_API_BASE_URL` must contain only the backend origin. Do not append `/api/v1`. The frontend API client adds that prefix automatically.

For PowerShell, Linux/macOS, environment variables, and Paga configuration, refer to:

- `backend/ENVIRONMENT_SETUP.md`
- `backend/README.md`
- `backend/PAGA_SANDBOX_SETUP.md`

## Payment simulation

SupplyKey includes a backend-controlled simulated payment provider for local development and demonstrations.

To enable it in a demo environment, configure:

```dotenv
APP_ENV=demo
ENABLE_PAYMENT_SIMULATOR=true
```

The simulator supports seven scenarios:

| Scenario | Purpose |
|---|---|
| `SUCCESS` | Simulates a successful payment |
| `FAILURE` | Simulates a failed payment |
| `PENDING` | Leaves payment pending |
| `DELAYED` | Simulates delayed processing |
| `DUPLICATE` | Tests duplicate event handling |
| `TIMEOUT` | Simulates a timeout |
| `UNKNOWN` | Tests an unrecognised outcome |

The backend generates and signs simulator events. The browser submits only the selected scenario, not a trusted funding event.

The backend simulator is available for controlled local testing. However, the frontend checkout screen for selecting and running these scenarios has not yet been completed. The full user-facing simulator checkout flow is therefore not ready.

The simulator is independent of Paga. It does not process real payments or move real money.

## Verification status

The latest reported verification results for this implementation are:

| Check | Result |
|---|---:|
| Backend tests | 129 passed, 0 failed, 0 skipped |
| Frontend tests | 10 passed |
| Frontend contract checks | Passed |
| Frontend production build | Passed |
| Browser tests | Not run |
| PostgreSQL migration and concurrency tests | Not run |
| Live Paga Sandbox tests | Not run |

These results describe the reported test run. They should not be interpreted as proof that every user journey or external integration has been verified.

### Outstanding issues

The following work remains incomplete:

- The separate 72-hour moderator-resolution deadline has not been implemented. It is distinct from the existing 48-hour evidence window.
- The frontend simulator checkout screen is missing.
- Return and refund workflows still have identified defects, including empty receipt confirmations, repeated partial confirmations, and missing ledger authorization entries for queued refunds and releases.
- Dispute details require further access-control work to prevent parties from seeing moderator-only internal notes.
- Full browser-based transaction verification has not been completed.
- PostgreSQL migration and concurrency behaviour remain unverified.
- Live Paga Sandbox processing has not been verified.

See [`STATUS_REPORT.md`](STATUS_REPORT.md) for the current findings and [`IMPLEMENTATION_REPORT.md`](IMPLEMENTATION_REPORT.md) for implementation scope and limitations.

## Safety and production readiness

SupplyKey is currently a development implementation. It has not been approved for production custody of customer funds or real-money transactions.

The Paga Sandbox integration remains unverified against live Sandbox credentials. A previous account-creation attempt was rejected because the account was not sufficiently verified.

Do not retry the integration until Paga confirms that the account has completed the required verification and that the necessary Sandbox products and permissions are enabled.

Other outstanding production requirements include:

- Production OTP delivery.
- Staff provisioning.
- Dual-admin configuration approval.
- PostgreSQL operational verification.
- Production object storage.
- Malware scanning.
- External email and SMS notification delivery.
- Provider approval and legal review.



---

**SupplyKey is built around one principle: a transaction should not depend on trust alone. Both parties should have clear obligations, verifiable evidence, and an accountable process for resolving disagreements.**