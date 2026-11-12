# SupplyKey Documentation Package

| Version | Status | Last updated | Authority |
|---|---|---|---|
| 0.3.1 | Corrected review, for team sign-off | 2026-11-12 | [v0.2 specification](README.md#2-source-of-truth) overrides v0.1 |

Entry point to the SupplyKey implementation blueprint: what the product is, how the documentation is organised, the reading order for each team member, and which source wins when documents disagree.

## 1. What SupplyKey is

SupplyKey is a conditional payment protection and transaction coordination platform for buyers and suppliers who do not fully trust each other. A buyer in Port Harcourt (Rivers State) can pay for goods from a supplier in Lagos without sending money to a stranger, and the supplier can dispatch knowing payment has been secured.

The platform coordinates the transaction, preserves the agreed terms, records evidence from both parties, enforces deadlines, and lets a human moderator decide disputes. Money moves only through the backend financial module.

SupplyKey does not provide logistics (no riders, no couriers), does not claim that photographs prove receipt, and does not claim Paga supports escrow or conditional custody. That is unconfirmed and subject to written provider confirmation and Nigerian legal approval. Development uses a SimulatedPaymentProvider; no real money moves until the launch gates in [Payment Provider Integration](04-operations/PAYMENT_PROVIDER_INTEGRATION.md) are cleared.

## 2. Source of truth

| Rank | Source | Role |
|---|---|---|
| 1 | `SupplyKey_Corrected_Technical_Specification_v0.2.docx` | Authoritative for requirements, locked rules, canonical statuses and proposed defaults. Overrides v0.1 wherever they conflict. |
| 2 | This package (v0.3.1, the merged blueprint) | The implementation blueprint. It merges the useful v0.1 detail (architecture, schema, endpoints, security, tests) with the v0.2 corrections. Build from this package. |
| 3 | The original v0.1 package | Superseded. Do not build from it. Everything useful is merged here. |

If this package ever disagrees with v0.2, v0.2 wins and the disagreement is a documentation bug: record it in [DOCUMENTATION_AUDIT.md](DOCUMENTATION_AUDIT.md).

Every requirement carries one of these labels:

| Label | Meaning |
|---|---|
| CONFIRMED | Product decision already made. Do not change without explicit sign-off. |
| RECOMMENDED | Implementation choice adopted to remove ambiguity. |
| PROPOSED | Sensible default that still needs product-owner approval. Implemented as configuration. |
| OPEN | Unresolved. Tracked in [Open Decisions](06-delivery/OPEN_DECISIONS.md). Nothing is silently decided. |
| VERIFY | External dependency that needs written evidence. |

## 3. Folder guide

| Folder | Contents | Purpose |
|---|---|---|
| [01-product](01-product/) | [Product Requirements](01-product/PRODUCT_REQUIREMENTS.md), [User Flows](01-product/USER_FLOWS.md), [Business Rules](01-product/BUSINESS_RULES.md) | What we are building and the rules that govern it |
| [02-architecture](02-architecture/) | [System Architecture](02-architecture/SYSTEM_ARCHITECTURE.md), [Component Design](02-architecture/COMPONENT_DESIGN.md), [Security Architecture](02-architecture/SECURITY_ARCHITECTURE.md) | How the system is built and who decides what |
| [03-technical-specs](03-technical-specs/) | [Database](03-technical-specs/DATABASE_SPECIFICATION.md), [API](03-technical-specs/API_SPECIFICATION.md), [State Machine](03-technical-specs/TRANSACTION_STATE_MACHINE.md), [Financial Engine](03-technical-specs/FINANCIAL_ENGINE.md) | The contracts engineers build against |
| [04-operations](04-operations/) | [Disputes and Moderation](04-operations/DISPUTE_AND_MODERATION.md), [Evidence and Files](04-operations/EVIDENCE_AND_FILE_HANDLING.md), [Notifications and Deadlines](04-operations/NOTIFICATIONS_AND_DEADLINES.md), [Payment Provider Integration](04-operations/PAYMENT_PROVIDER_INTEGRATION.md) | Operational workflows and external dependencies |
| [05-quality](05-quality/) | [Testing Strategy](05-quality/TESTING_STRATEGY.md), [Acceptance Criteria](05-quality/ACCEPTANCE_CRITERIA.md) | How we prove it works |
| [06-delivery](06-delivery/) | [Implementation Plan](06-delivery/IMPLEMENTATION_PLAN.md), [Team Responsibilities](06-delivery/TEAM_RESPONSIBILITIES.md), [Open Decisions](06-delivery/OPEN_DECISIONS.md) | How the team executes and what is still unresolved |
| [diagrams](diagrams/) | Mermaid sources: [system architecture](diagrams/SYSTEM_ARCHITECTURE.mmd), [lifecycle](diagrams/TRANSACTION_LIFECYCLE.mmd), [payment flow](diagrams/PAYMENT_FLOW.mmd), [dispute flow](diagrams/DISPUTE_FLOW.mmd), plus four supporting diagrams: [acceptance review flow](diagrams/ACCEPTANCE_REVIEW_FLOW.mmd), [database ER](diagrams/DATABASE_ER.mmd), [instruction lifecycle](diagrams/INSTRUCTION_LIFECYCLE.mmd) and [delivery-photo requirement](diagrams/DELIVERY_PHOTO_REQUIREMENT.mmd) | Single source for every diagram embedded in the documents (no diagram source lives inside a document) |

Root file: [DOCUMENTATION_AUDIT.md](DOCUMENTATION_AUDIT.md) lists the consistency checks performed and the remaining gaps.

## 4. Reading order

| Order | Everyone | Then |
|---|---|---|
| 1 | [Business Rules](01-product/BUSINESS_RULES.md) | |
| 2 | [Product Requirements](01-product/PRODUCT_REQUIREMENTS.md) | |
| 3 | [Transaction State Machine](03-technical-specs/TRANSACTION_STATE_MACHINE.md) (it defines the canonical status names used everywhere) | |
| 4 | [Open Decisions](06-delivery/OPEN_DECISIONS.md) | |

| Role | Continue with |
|---|---|
| UI/UX Designer | [User Flows](01-product/USER_FLOWS.md), [Notifications and Deadlines](04-operations/NOTIFICATIONS_AND_DEADLINES.md), [Evidence and Files](04-operations/EVIDENCE_AND_FILE_HANDLING.md), [Acceptance Criteria](05-quality/ACCEPTANCE_CRITERIA.md) |
| Frontend Engineer | [API Specification](03-technical-specs/API_SPECIFICATION.md), [System Architecture](02-architecture/SYSTEM_ARCHITECTURE.md) section on frontend, [Notifications and Deadlines](04-operations/NOTIFICATIONS_AND_DEADLINES.md) |
| Backend Engineer | [System Architecture](02-architecture/SYSTEM_ARCHITECTURE.md), [Component Design](02-architecture/COMPONENT_DESIGN.md), [Database](03-technical-specs/DATABASE_SPECIFICATION.md), [Financial Engine](03-technical-specs/FINANCIAL_ENGINE.md), [Payment Provider Integration](04-operations/PAYMENT_PROVIDER_INTEGRATION.md), [Security Architecture](02-architecture/SECURITY_ARCHITECTURE.md), [Testing Strategy](05-quality/TESTING_STRATEGY.md) |
| Moderator tooling (FE + BE) | [Dispute and Moderation](04-operations/DISPUTE_AND_MODERATION.md) |
| Planning | [Implementation Plan](06-delivery/IMPLEMENTATION_PLAN.md), [Team Responsibilities](06-delivery/TEAM_RESPONSIBILITIES.md) |

## 5. Conventions used throughout

- Money is an integer in kobo (`*_kobo`). ₦1,100,000 is `110000000`. No floating-point money.
- Timestamps are UTC ISO-8601; display in Africa/Lagos (WAT). Deadlines are computed on the server only.
- Status names are defined once, in [Transaction State Machine](03-technical-specs/TRANSACTION_STATE_MACHINE.md#2-canonical-enums). They are identical in the database, API, frontend types and diagrams.
- The running example is five Samsung Galaxy A15 phones at ₦220,000 each (₦1,100,000) sold by Ibrahim Traders (Lagos) to Chinedu Okafor (Port Harcourt).

## 6. Scope

This package is documentation only. No application code has been written.
