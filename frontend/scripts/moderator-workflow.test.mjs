import test from 'node:test';
import assert from 'node:assert/strict';
import { buildModeratorActionRequest } from '../src/lib/moderation.js';
import { api } from '../src/lib/api.js';

const acceptance = { id: 'decision-1', status: 'SUBMITTED_FOR_REVIEW', version: 7, supplier_photos: 'SUBMITTED' };
const dispute = { id: 'dispute-1', status: 'MODERATOR_REVIEW', version: 11, item_id: 'item-1', lot_id: 'lot-1', quantity: 2 };
const detail = { version: 13, consent_statement_ids: ['buyer-consent', 'supplier-consent'] };

test('acceptance confirmation uses the decision ID, version and considered evidence IDs', () => {
  const req = buildModeratorActionRequest({ caseItem: acceptance, detail, action: 'confirm', fields: { reason: 'Reviewed all evidence.', evidenceIdsConsidered: ['proof-1'] } });
  assert.equal(req.path, '/mod/decisions/decision-1/confirm');
  assert.equal(req.headers['If-Match'], '13');
  assert.deepEqual(req.body.evidence_ids_considered, ['proof-1']);
});

test('acceptance actions map to the matching backend endpoints and schemas', () => {
  const request = buildModeratorActionRequest({ caseItem: acceptance, detail, action: 'request', fields: { party: 'BUYER', question: 'Please clarify the label.', dueAt: '2026-11-20T12:00' } });
  assert.equal(request.path, '/mod/decisions/decision-1/request-evidence');
  assert.equal(request.body.due_at, '2026-11-20T12:00:00.000Z');
  const extend = buildModeratorActionRequest({ caseItem: acceptance, detail, action: 'extend', fields: { reason: 'More time to review.', extraHours: '12' } });
  assert.deepEqual(extend.body, { reason: 'More time to review.', extra_hours: 12 });
  const open = buildModeratorActionRequest({ caseItem: acceptance, detail, action: 'dispute', fields: { reasonCode: 'OTHER', reason: 'Further independent review is required.' } });
  assert.equal(open.path, '/mod/decisions/decision-1/open-dispute');
});

test('dispute decision sends item, lot, quantity, outcome and evidence considered', () => {
  const req = buildModeratorActionRequest({ caseItem: dispute, detail, action: 'decision', fields: { outcome: 'NO_FINANCIAL_ACTION', quantity: '2', reason: 'Evidence does not justify a financial adjustment.', evidenceIdsConsidered: ['dispute-proof'] } });
  assert.equal(req.path, '/mod/disputes/dispute-1/decision');
  assert.deepEqual(req.body.outcomes[0], { item_id: 'item-1', lot_id: 'lot-1', quantity: 2, outcome: 'NO_FINANCIAL_ACTION' });
  assert.deepEqual(req.body.evidence_ids_considered, ['dispute-proof']);
  assert.equal(req.headers['If-Match'], '13');
});

test('redispatch dispute decision requires and serializes a valid deadline', () => {
  const req = buildModeratorActionRequest({ caseItem: dispute, detail, action: 'decision', fields: { outcome: 'REDISPATCH_OR_REPLACEMENT', newDispatchDeadlineAt: '2026-11-22T10:30', evidenceIdsConsidered: ['proof'], reason: 'Both parties have consented to a replacement.' } });
  assert.deepEqual(req.body.outcomes[0].consent_statement_ids, ['buyer-consent', 'supplier-consent']);
  assert.equal(req.body.outcomes[0].new_dispatch_deadline_at, '2026-11-22T10:30:00.000Z');
  assert.throws(() => buildModeratorActionRequest({ caseItem: dispute, detail, action: 'decision', fields: { outcome: 'REDISPATCH_OR_REPLACEMENT' } }), /New dispatch deadline is required/);
});

test('dispute request, extension, note, assignment, hold and resume use matching API schemas', () => {
  assert.equal(buildModeratorActionRequest({ caseItem: dispute, detail, action: 'request', fields: { question: 'More evidence', dueAt: '2026-11-20T12:00' } }).path, '/mod/disputes/dispute-1/request-evidence');
  assert.equal(buildModeratorActionRequest({ caseItem: dispute, detail, action: 'extend', fields: { reason: 'Need time.', extraHours: '6' } }).body.extra_hours, 6);
  assert.deepEqual(buildModeratorActionRequest({ caseItem: dispute, detail, action: 'note', fields: { internalNote: 'Internal review note.' } }).body, { note: 'Internal review note.' });
  assert.deepEqual(buildModeratorActionRequest({ caseItem: dispute, detail, action: 'assign', fields: { assignee: 'moderator-2' } }).body, { moderator_id: 'moderator-2' });
  assert.equal(buildModeratorActionRequest({ caseItem: dispute, detail, action: 'hold', fields: { reason: 'Pause while checking.' } }).path, '/mod/disputes/dispute-1/hold');
  assert.equal(buildModeratorActionRequest({ caseItem: dispute, detail, action: 'resume', fields: { reason: 'Review may continue.' } }).path, '/mod/disputes/dispute-1/resume');
});

test('invalid evidence due date fails locally before any API request is sent', () => {
  assert.throws(() => buildModeratorActionRequest({ caseItem: dispute, detail, action: 'request', fields: { question: 'More evidence', dueAt: '' } }), /Evidence due date is required/);
});

test('local demo mode reports that binary evidence viewing requires configured private storage', async () => {
  await assert.rejects(api.evidenceFile('evidence-demo'), /actual file viewing requires the configured backend and private evidence storage/);
});
