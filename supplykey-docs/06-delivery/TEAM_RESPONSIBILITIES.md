# Team Responsibilities


Who owns what, which contracts must be frozen before coding, and the working agreements that keep the blueprint consistent.

## 1. Roles

| Role | Owns |
|---|---|
| Backend Engineer (BE) | API, database and migrations, state machines and deadlines, financial module, payment adapter and simulator, background jobs, audit, notifications backend, OpenAPI |
| Frontend Engineer (FE) | Public app (buyer and supplier) and moderator dashboard; typed API client; upload components; rendering `allowed_actions`; status timelines |
| UI/UX Designer (UX) | Journeys, screens, interaction states, design system, copy for statuses, deadlines, notices and errors, upload guidance, accessibility |

## 2. Responsibility matrix

R = responsible, A = accountable, C = consulted, I = informed.

| Area | BE | FE | UX |
|---|---|---|---|
| Canonical status enums ([State Machine](../03-technical-specs/TRANSACTION_STATE_MACHINE.md#2-canonical-enums)) | A, R | C | C |
| Database schema | A, R | I | I |
| OpenAPI and error codes | A, R | C | I |
| Mock server | R | C | I |
| Agreement and conditions UI | C | R | A |
| Payment and funding states, copy | C | R | A |
| Dispatch, delivery photos, receipt confirmation | C | R | A |
| Acceptance, rejection, return flows | C | R | A |
| Evidence windows and notices | R | R | A |
| Moderator dashboard | C | A, R | R |
| Ledger, allocation, instructions | A, R | I | I |
| Payment adapter and simulator | A, R | C (simulator flags in staging) | I |
| Deadlines and scanners | A, R | C (countdown display) | C |
| Notifications catalogue (content) | C | C | A |
| Notifications delivery | A, R | I | I |
| Security controls | A, R | R (client side) | C |
| Test strategy | A (backend and state machine) | A (UI and e2e) | C |
| Open decisions register | R | C | C |

Product owner (outside the three roles) is accountable for every item in [Open Decisions](OPEN_DECISIONS.md).

## 3. Contracts frozen in Phase 0

| Contract | Owner | Sign-off |
|---|---|---|
| Canonical enums and glossary | BE | FE, UX |
| OpenAPI generated from the [API Specification](../03-technical-specs/API_SPECIFICATION.md), including `allowed_actions` vocabulary and error codes | BE | FE |
| Evidence protocol (signed upload, complete, link by action) | BE | FE |
| Money (kobo integers) and time (UTC, server-supplied deadlines) formats | BE | FE |
| Configuration keys and defaults ([Business Rules 2](../01-product/BUSINESS_RULES.md#2-deadlines-and-configuration)) | BE | All |
| Notification catalogue (event, recipient, channel, copy) | UX | BE |
| Design tokens and component library | UX | FE |
| Simulator scenarios and test flags | BE | FE |
| Defaults for the decisions in [Open Decisions](OPEN_DECISIONS.md) | Product owner | All |

## 4. Working agreements

1. One vocabulary. A status name that is not in the canonical enums is a bug in code, design or documentation.
2. Backend authority. The frontend never derives whether money can move. It renders `allowed_actions` and server-provided statuses.
3. Change control. A change to an enum, endpoint, table or rule is proposed in a pull request that updates all affected documents together and is reviewed by all three roles.
4. Do not decide silently. If a rule is missing, add it to [Open Decisions](OPEN_DECISIONS.md) with a proposed default and build it as configuration.
5. No real money. Nobody integrates real provider money movement or describes the product as escrow before the launch gates clear.
6. Definition of done per [Acceptance Criteria 2](../05-quality/ACCEPTANCE_CRITERIA.md#2-definition-of-done-for-a-feature).

## 5. Related documents

[Implementation Plan](IMPLEMENTATION_PLAN.md) · [Open Decisions](OPEN_DECISIONS.md)
