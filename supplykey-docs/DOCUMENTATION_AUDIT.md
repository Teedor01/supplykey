# Documentation Integrity and Readiness

This document describes what the SupplyKey documentation covers, what consistency checks have been performed on the files, and what remains unverified. It is not evidence that an application has been implemented or tested.

## 1. Documentation coverage

The package defines the product requirements, confirmed business rules, user flows, system architecture, component responsibilities, security controls, database model, API contract, transaction and financial state machines, evidence handling, dispute operations, payment-provider integration, testing strategy, acceptance criteria, implementation plan, team responsibilities and unresolved decisions. The Mermaid files provide the source diagrams referenced by the written specifications.

## 2. Consistency requirements

The following definitions must remain aligned throughout implementation:

| Area | Canonical reference | Documents that must agree |
|---|---|---|
| Product rules | [Business Rules](01-product/BUSINESS_RULES.md) | Product Requirements, User Flows, API, State Machine, Acceptance Criteria |
| Statuses and transitions | [Transaction State Machine](03-technical-specs/TRANSACTION_STATE_MACHINE.md) | Database, API, Financial Engine, diagrams, tests and frontend types |
| Data structures and constraints | [Database Specification](03-technical-specs/DATABASE_SPECIFICATION.md) | API, State Machine, Financial Engine |
| Money movement and ledger | [Financial Engine](03-technical-specs/FINANCIAL_ENGINE.md) | Database, API, Payment Provider Integration, State Machine |
| Deadlines and evidence | [Business Rules](01-product/BUSINESS_RULES.md) and [Notifications and Deadlines](04-operations/NOTIFICATIONS_AND_DEADLINES.md) | API, Evidence Handling, State Machine, User Flows and tests |
| Unresolved decisions | [Open Decisions](06-delivery/OPEN_DECISIONS.md) | Every affected implementation plan, configuration and acceptance test |

A conflict must be raised and resolved before the affected behaviour is implemented. No team member should silently choose a different status, endpoint, field, deadline or financial rule.

## 3. Verification limits

The documentation defines expected behaviour. It does not establish that the behaviour has been implemented or that external systems support it. In particular, the following have not been verified by this documentation package:

- Paga's ability to provide conditional custody, partial payout, partial refund, status queries, idempotency, signed webhooks, settlement reports and a suitable sandbox.
- The legal structure required to hold or control customer funds in Nigeria, including licensing, KYC/AML duties and customer-money obligations.
- Real provider behaviour under timeout, duplicate webhook, delayed settlement and ambiguous outcomes.
- Actual PostgreSQL migrations, triggers, constraints, indexes and transaction isolation under concurrent load.
- API conformance through a generated and validated OpenAPI document.
- Application security, usability, accessibility, performance and recovery behaviour.
- Execution of the test scenarios and acceptance criteria against working application code.

Until provider capability and legal requirements are confirmed in writing, development must use the `SimulatedPaymentProvider`, and real-money mode must remain disabled.

## 4. Decisions required before affected work is complete

The [Open Decisions](06-delivery/OPEN_DECISIONS.md) register identifies the product and technical questions, recommended defaults, affected roles and the phase or launch gate by which each decision is needed. A recommended default is not a confirmed product rule. Keep proposed values configurable until the product owner approves them.

The external launch dependencies are:

1. Written provider confirmation for the required collection, holding, payout, refund, status-query, idempotency, webhook, reporting and sandbox capabilities.
2. Written Nigerian legal advice on fund custody, licensing or partner requirements, KYC/AML, dispute liability and customer-facing claims.
3. Confirmed provider fees, limits and settlement rules.

Real-money launch is blocked until these dependencies and all launch-critical open decisions are resolved.

## 5. Implementation readiness

| Workstream | Readiness | Conditions |
|---|---|---|
| UI/UX | Can begin | Use the documented user flows, statuses, evidence requirements and unresolved-decision labels. Do not design unapproved behaviour as settled. |
| Frontend | Can begin foundation and mock integration | Agree the API contract and shared types with backend. Render server-authoritative statuses, deadlines and `allowed_actions`. |
| Backend | Can begin foundation and simulator-backed implementation | Establish schema, state transitions, ledger invariants, adapter contract, idempotency, audit and reconciliation. Generate and validate OpenAPI before frontend integration is treated as stable. |
| Real-money integration | Blocked | Requires written provider capability confirmation and Nigerian legal approval. |

The package is an implementation specification. Application correctness can only be established through implementation, automated tests, security review, integration testing and operational recovery exercises.
