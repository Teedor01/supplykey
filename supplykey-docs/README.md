# SupplyKey Documentation

This documentation defines the SupplyKey product, business rules, system architecture, technical contracts, operational procedures, quality requirements and implementation plan. It is the shared reference for product, design, frontend and backend work.

## 1. Product overview

SupplyKey is a conditional payment protection and transaction coordination platform for buyers and suppliers who do not fully trust each other. A buyer can purchase goods from a supplier in another city without sending money directly to an unknown party, while the supplier can dispatch after the system verifies that the required funds are secured through an approved arrangement.

SupplyKey coordinates transaction terms, preserves the agreement, records evidence from both parties, enforces deadlines and provides a moderator-led process for resolving disputes. Financial operations are controlled by the backend financial module.

SupplyKey does not provide logistics, riders or couriers. Photographs are evidence and do not independently prove delivery or physical receipt. Paga's ability to support conditional holding or escrow-like custody has not been confirmed. Real-money operation requires written provider confirmation and Nigerian legal approval. Development must use the `SimulatedPaymentProvider`; no real customer funds may move before the launch requirements in [Payment Provider Integration](04-operations/PAYMENT_PROVIDER_INTEGRATION.md) are satisfied.

## 2. Authority and interpretation

The documents in this package form one specification and must be read together. The [Business Rules](01-product/BUSINESS_RULES.md) define confirmed product rules. The [Transaction State Machine](03-technical-specs/TRANSACTION_STATE_MACHINE.md) is the canonical source for statuses and legal transitions. The [Database Specification](03-technical-specs/DATABASE_SPECIFICATION.md), [API Specification](03-technical-specs/API_SPECIFICATION.md), [Financial Engine](03-technical-specs/FINANCIAL_ENGINE.md), diagrams and tests must implement those definitions consistently.

If two documents appear to conflict, do not choose one silently. Raise the conflict, identify the affected requirements and contracts, and resolve it before implementing the affected behaviour. Record unresolved product or technical questions in [Open Decisions](06-delivery/OPEN_DECISIONS.md).

Requirement labels have the following meanings:

| Label | Meaning |
|---|---|
| CONFIRMED | Product decision already made. Do not change without explicit approval. |
| RECOMMENDED | Technical or operational approach selected to remove ambiguity. |
| PROPOSED | Suggested default that still requires product-owner approval. Keep configurable until approved. |
| OPEN | Unresolved decision tracked in [Open Decisions](06-delivery/OPEN_DECISIONS.md). |
| VERIFY | External dependency requiring written evidence. |

## 3. Documentation map

| Folder | Documents | Purpose |
|---|---|---|
| [01-product](01-product/) | [Product Requirements](01-product/PRODUCT_REQUIREMENTS.md), [User Flows](01-product/USER_FLOWS.md), [Business Rules](01-product/BUSINESS_RULES.md) | Product scope, users, workflows and binding rules |
| [02-architecture](02-architecture/) | [System Architecture](02-architecture/SYSTEM_ARCHITECTURE.md), [Component Design](02-architecture/COMPONENT_DESIGN.md), [Security Architecture](02-architecture/SECURITY_ARCHITECTURE.md) | System structure, module responsibilities and security controls |
| [03-technical-specs](03-technical-specs/) | [Database](03-technical-specs/DATABASE_SPECIFICATION.md), [API](03-technical-specs/API_SPECIFICATION.md), [Transaction State Machine](03-technical-specs/TRANSACTION_STATE_MACHINE.md), [Financial Engine](03-technical-specs/FINANCIAL_ENGINE.md) | Engineering contracts and financial behaviour |
| [04-operations](04-operations/) | [Disputes and Moderation](04-operations/DISPUTE_AND_MODERATION.md), [Evidence and Files](04-operations/EVIDENCE_AND_FILE_HANDLING.md), [Notifications and Deadlines](04-operations/NOTIFICATIONS_AND_DEADLINES.md), [Payment Provider Integration](04-operations/PAYMENT_PROVIDER_INTEGRATION.md) | Operational workflows, evidence and external payment dependencies |
| [05-quality](05-quality/) | [Testing Strategy](05-quality/TESTING_STRATEGY.md), [Acceptance Criteria](05-quality/ACCEPTANCE_CRITERIA.md) | Verification approach and completion criteria |
| [06-delivery](06-delivery/) | [Implementation Plan](06-delivery/IMPLEMENTATION_PLAN.md), [Team Responsibilities](06-delivery/TEAM_RESPONSIBILITIES.md), [Open Decisions](06-delivery/OPEN_DECISIONS.md) | Delivery sequence, ownership and unresolved decisions |
| [diagrams](diagrams/) | Eight Mermaid diagram source files | Shared diagrams referenced by the specifications |

[Documentation Integrity and Readiness](DOCUMENTATION_AUDIT.md) records the scope and limits of the documentation checks.

## 4. Reading order

Everyone should read these first:

1. [Business Rules](01-product/BUSINESS_RULES.md)
2. [Product Requirements](01-product/PRODUCT_REQUIREMENTS.md)
3. [Transaction State Machine](03-technical-specs/TRANSACTION_STATE_MACHINE.md)
4. [Open Decisions](06-delivery/OPEN_DECISIONS.md)

Then continue by role:

| Role | Documents |
|---|---|
| UI/UX Designer | [User Flows](01-product/USER_FLOWS.md), [Notifications and Deadlines](04-operations/NOTIFICATIONS_AND_DEADLINES.md), [Evidence and Files](04-operations/EVIDENCE_AND_FILE_HANDLING.md), [Acceptance Criteria](05-quality/ACCEPTANCE_CRITERIA.md) |
| Frontend Engineer | [API Specification](03-technical-specs/API_SPECIFICATION.md), [System Architecture](02-architecture/SYSTEM_ARCHITECTURE.md), [Notifications and Deadlines](04-operations/NOTIFICATIONS_AND_DEADLINES.md) |
| Backend Engineer | [System Architecture](02-architecture/SYSTEM_ARCHITECTURE.md), [Component Design](02-architecture/COMPONENT_DESIGN.md), [Database](03-technical-specs/DATABASE_SPECIFICATION.md), [Financial Engine](03-technical-specs/FINANCIAL_ENGINE.md), [Payment Provider Integration](04-operations/PAYMENT_PROVIDER_INTEGRATION.md), [Security Architecture](02-architecture/SECURITY_ARCHITECTURE.md), [Testing Strategy](05-quality/TESTING_STRATEGY.md) |
| Moderator tooling | [Dispute and Moderation](04-operations/DISPUTE_AND_MODERATION.md), [API Specification](03-technical-specs/API_SPECIFICATION.md) |
| Delivery planning | [Implementation Plan](06-delivery/IMPLEMENTATION_PLAN.md), [Team Responsibilities](06-delivery/TEAM_RESPONSIBILITIES.md), [Open Decisions](06-delivery/OPEN_DECISIONS.md) |

## 5. Shared conventions

- Monetary values are integers in kobo (`*_kobo`). For example, ₦1,100,000 is `110000000`. Never use floating-point values for money.
- Timestamps are stored in UTC ISO-8601 and displayed in `Africa/Lagos` (WAT). The server calculates all deadlines.
- Status names are defined in [Transaction State Machine](03-technical-specs/TRANSACTION_STATE_MACHINE.md#2-canonical-enums) and must match across the database, API, frontend types, diagrams and tests.
- The worked example uses five Samsung Galaxy A15 phones at ₦220,000 each, for a total of ₦1,100,000, sold by Ibrahim Traders in Lagos to Chinedu Okafor in Port Harcourt.

## 6. Scope

This package specifies the product and its implementation. It does not contain an implemented application, database migrations, a generated OpenAPI document or executed application tests. The test cases and acceptance criteria are specifications until implemented and run.
