# SupplyKey

**Trade with confidence.**

SupplyKey is a protected-transaction platform for buyers and suppliers. It makes transactions more accountable through agreed terms, evidence-backed delivery and acceptance, moderator review, and controlled payout or refund decisions.

The platform provides a structured process for both parties to follow, from creating and accepting a transaction agreement to confirming delivery, resolving disputes, and authorising the appropriate financial outcome.

SupplyKey is built around a simple principle: **a transaction should not depend on trust alone. Both parties should have clear obligations, verifiable evidence, and an accountable process for resolving disagreements.**

This repository contains the frontend application, backend implementation, project specifications, operational documentation, and automated tests.

## Project Structure

```text
SupplyKey/
├── frontend/              # React and Vite application
├── backend/               # FastAPI application and supporting services
├── supplykey-docs/        # Product, architecture and technical specifications
├── README.md
└── ...
```

The backend includes:

- Transaction management and persistence
- Authentication and role-based authorization
- Payment provider adapters
- Payment simulation
- Bank account verification and beneficiary management
- Payout and refund processing
- Moderator dispute resolution
- Evidence management
- Background workers
- Payment reconciliation
- Automated tests

The documentation covers product rules, user flows, architecture, API and database specifications, financial state transitions, operational guidance, security requirements, and acceptance criteria.

## Core Features

### Protected Transactions

- Transaction creation and agreement acceptance.
- Buyer and supplier workflows.
- Payment confirmation and funding status.
- Delivery evidence submission.
- Buyer receipt confirmation.
- Goods acceptance and rejection.
- Moderator review.
- Controlled supplier payout and buyer refund.

### Evidence-Based Delivery

Delivery and acceptance actions are supported by evidence submissions.

Evidence is associated with the relevant transaction and workflow, allowing buyers, suppliers, and moderators to review the information required for their respective actions.

### Dispute Resolution

When a buyer rejects goods:

1. The buyer submits a rejection reason and supporting evidence.
2. The transaction enters the appropriate dispute state.
3. Supplier payout is blocked.
4. Both parties can provide relevant evidence.
5. A moderator reviews the dispute and makes a decision.
6. If a return is required, the buyer submits return evidence.
7. The supplier confirms receipt of returned goods.
8. The moderator authorises the appropriate financial outcome.
9. The refund is processed and reconciled.
10. The transaction is completed when all required conditions are satisfied.

Dispute decisions operate under a 72-hour decision deadline. Evidence submission windows are managed separately.

### Settlement and Refund Management

SupplyKey distinguishes between supplier payouts and buyer refunds.

**Supplier payout**

The supplier receives the agreed transaction amount, subject to the transaction's settlement conditions and moderator approval where required.

**Excess payment refund**

If the buyer pays more than the agreed amount, the excess is refunded to the buyer.

This refund is private to the buyer. The supplier does not see the excess amount or its refund status.

**Dispute-related refund**

If goods are rejected and a refund is approved through the dispute process, both parties can see the relevant refund outcome and status.

Refunds and payouts are tracked independently. A transaction is not completed until all required financial operations have been confirmed.

### Bank Account Verification and Security

SupplyKey supports bank beneficiary verification and controlled financial transfers.

The bank account workflow includes:

- Provider bank directory.
- Bank account number validation.
- Account name enquiry.
- Account holder confirmation.
- Beneficiary ownership confirmation.
- Secure beneficiary storage.
- Verified beneficiary binding.
- Account-change protection.
- Payout and refund destination snapshots.
- Transfer idempotency.
- Provider callback verification.
- Settlement reconciliation.
- Sensitive financial information protection.

Approved financial instructions are bound to their verified destination to prevent account changes from silently redirecting existing payouts or refunds.

### Payment Integration

SupplyKey integrates with Paga Engine through separate payment services for collections and bank transfers.

The integration supports:

- Bank directory retrieval.
- Bank account verification.
- Account name enquiry.
- Bank transfers.
- Transfer status tracking.
- Callback processing.
- Financial reconciliation.
- Supplier payouts.
- Buyer refunds.

Payment configuration supports the following modes:

| Mode | Purpose |
|---|---|
| `simulated` | Controlled development and demonstration payments |
| `paga` | Paga payment integration |
| `sandbox` | Provider testing environment |
| `live` | Production payment environment |

The backend controls payment state transitions and validates payment events. Frontend requests alone cannot establish that funds have been received or transferred.

### Payment Simulation

SupplyKey includes a backend-controlled payment simulator for development and demonstration purposes.

Supported scenarios include:

| Scenario | Purpose |
|---|---|
| `SUCCESS` | Successful payment |
| `FAILURE` | Failed payment |
| `PENDING` | Pending processing |
| `DELAYED` | Delayed processing |
| `DUPLICATE` | Duplicate event handling |
| `TIMEOUT` | Timeout handling |
| `UNKNOWN` | Unrecognised payment outcome |

The backend generates and validates simulator events. The browser does not submit trusted funding events directly.

The simulator is independent of Paga and does not move real money.

## Transaction Lifecycle

A typical SupplyKey transaction follows this sequence:

```text
Transaction Created
        │
Agreement Accepted
        │
Payment Secured
        │
Goods Dispatched
        │
Delivery Confirmed
        │
Buyer Acceptance
        │
Evidence Review
        │
Moderator Approval
        │
Settlement
        │
Financial Reconciliation
        │
Transaction Completed
```

When a buyer rejects goods, the transaction follows the dispute and return workflow instead of proceeding directly to supplier payout.

## Transaction Completion

A transaction reaches `COMPLETED` only when all applicable requirements have been satisfied.

For a successful purchase:

- Required delivery and acceptance conditions are satisfied.
- Required evidence has been reviewed.
- Supplier payout has been confirmed.
- Any excess buyer payment has been refunded.
- No outstanding dispute, financial hold, or settlement action remains.

For a rejected-goods transaction:

- The dispute has been resolved.
- Required return conditions have been satisfied.
- Supplier receipt of returned goods has been confirmed where required.
- The refund has been authorised.
- The refund has been confirmed.
- No outstanding financial or dispute action remains.

Pending, failed, or unconfirmed financial operations do not constitute completed settlement.

## Getting Started

### Requirements

- Python
- pip
- Node.js
- npm
- Git

### 1. Backend Setup

Open a terminal in the project root.

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

The migration command applies the database migrations and initializes or upgrades the local development database.

The API will be available at:

- API: http://localhost:8000
- Interactive API documentation: http://localhost:8000/docs

### 2. Start the Background Worker

Open another terminal:

```bat
cd backend
.venv\Scripts\activate

python -m app.worker
```

The background worker handles configured processing tasks, including financial instruction processing and settlement workflows.

### 3. Frontend Setup

Open another terminal:

```bat
cd frontend

copy .env.example .env.local

npm install
npm run dev
```

The frontend will be available at the local address printed by Vite.

### 4. Connect the Frontend to the Backend

Configure `frontend/.env.local`:

```dotenv
VITE_API_MODE=live
VITE_API_BASE_URL=http://localhost:8000
```

**Important:** `VITE_API_BASE_URL` must contain only the backend origin. Do not append `/api/v1`. The frontend API client adds the prefix automatically.

For PowerShell, Linux/macOS, environment variables, and payment configuration, refer to:

- `backend/ENVIRONMENT_SETUP.md`
- `backend/README.md`
- `backend/PAGA_SANDBOX_SETUP.md`
- `supplykey-docs/04-operations/PAYMENT_PROVIDER_INTEGRATION.md`

## Payment Configuration

Payment configuration is managed by the backend.

The payment provider, integration mode, credentials, callback configuration, encryption keys, and worker configuration must be supplied through backend environment variables.

Never place payment-provider secret keys, signing keys, beneficiary encryption keys, or backend authentication secrets in frontend environment variables.

Refer to the backend environment documentation for the complete configuration.

## Security

SupplyKey incorporates financial and transaction security controls, including:

- Backend-enforced authorization.
- Role-based data visibility.
- Verified bank beneficiaries.
- Encrypted beneficiary information.
- Immutable financial destination snapshots.
- Account-change protection.
- Transfer idempotency.
- Callback signature verification.
- Duplicate event protection.
- Payment reconciliation.
- Moderator approval workflows.
- Transaction audit records.
- Financial state-transition validation.
- Protection against unauthorized payout and refund initiation.

Sensitive financial information is returned only to authorized parties and is excluded from unnecessary responses and logs.

## Testing

The project includes backend, frontend, API contract, payment adapter, settlement, authorization, and workflow tests.

Backend:

```bat
cd backend
.venv\Scripts\activate
python -m pytest -q
```

Frontend:

```bat
cd frontend
npm test
npm run check:contracts
npm run build
```

## Documentation

Project documentation is located in `supplykey-docs/` and the backend documentation directory.

It covers:

- Product requirements.
- Transaction workflows.
- System architecture.
- API specifications.
- Database schema.
- Financial settlement rules.
- Payment provider integration.
- Bank account verification.
- Security controls.
- Moderator operations.
- Dispute and return workflows.
- Testing and acceptance criteria.

## Project Principle

**SupplyKey is built around one principle: a transaction should not depend on trust alone. Both parties should have clear obligations, verifiable evidence, and an accountable process for resolving disagreements.**

Trade with confidence.