import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { api, normalizeEvidenceCompletion } from '../src/lib/api.js';

beforeEach(() => api.reset());

test('evidence completion response exposes id expected by dispatch UI', () => {
  const normalized = normalizeEvidenceCompletion({ evidence_id: 'evidence-123', status: 'VERIFIED' });
  assert.equal(normalized.id, 'evidence-123');
  assert.equal(normalized.status, 'VERIFIED');
});

test('evidence completion rejects missing evidence identifiers instead of enabling a broken dispatch', () => {
  assert.throws(() => normalizeEvidenceCompletion({ status: 'VERIFIED' }), /without returning an evidence ID/);
});

test('supplier can submit dispatch with verified DISPATCH_PROOF evidence', async () => {
  api.switchRole('SUPPLIER');
  api.getDemoState().evidence.push({
    id: 'dispatch-proof-1', transaction_id: 'tx_7f42', uploader_id: 'usr_supplier',
    purpose: 'DISPATCH_PROOF', status: 'VERIFIED'
  });
  const result = await api.action('/transactions/tx_7f42/dispatch', {
    lines: [{ item_id: 'it_4', quantity: 2 }],
    courier_name: 'Test Courier', rider_name: 'Test Rider', rider_phone: '+2348012345678',
    tracking_ref: 'TRACK-123', evidence_ids: ['dispatch-proof-1']
  });
  assert.equal(result.dispatches.length, 1);
  assert.equal(result.lots.some(l => l.item_status === 'DISPATCHED'), true);
});
