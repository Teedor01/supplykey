# Build and Verification Report

**Updated:** 16 November 2026

## Actual results

- **Node contract tests:** PASS — `npm test`, 5 tests passed.
- **Mock workflow smoke checks:** PASS — `node scripts/smoke-check.mjs`.
- **JavaScript/JSX parse:** PASS — TypeScript compiler parse command documented in `AUDIT_REPORT.md`.
- **API client syntax:** PASS — `node --check src/lib/api.js`.
- **Dependency installation:** BLOCKED — npm registry DNS lookup failed with `EAI_AGAIN registry.npmjs.org`.
- **Production build:** BLOCKED — `npm run build` returned `vite: not found` because installation did not complete.
- **Vitest:** BLOCKED — `npm run test:vitest` returned `vitest: not found`.
- **Browser/Playwright:** NOT TESTED — no production/dev bundle could be built without Vite/React dependencies.
- **Live backend:** NOT TESTED — no backend URL, OpenAPI deployment or credentials supplied.

No production build, live API integration or browser test is claimed as successful.

## Regression coverage

The dependency-free tests verify universal POST idempotency, safe replay and changed-body rejection, pending payment not funding a transaction, dispatch blocked before confirmed funding, and distinct Senior Moderator approval for high-value instructions. The smoke script covers multi-item creation, invitation/agreement, payment attempt, evidence, partial dispatch/receipt, acceptance/rejection, return/refund instruction, moderator review and senior approval. The mock never simulates successful settlement.

See `AUDIT_REPORT.md` for defects found, corrections, test evidence, architecture deviations and outstanding dependencies.
