import test from 'node:test';
import assert from 'node:assert/strict';
import { settlementRows } from '../src/lib/settlement.js';
import { api } from '../src/lib/api.js';

const summary = {
  payment_amount_kobo: 12_000_000,
  expected_payment_amount_kobo: 10_000_000,
  agreed_amount_kobo: 10_000_000,
  original_payment_amount_kobo: 12_000_000,
  excess_payment_refund_amount_kobo: 2_000_000,
  excess_payment_refund_status: 'PENDING',
  supplier_entitlement_kobo: 10_000_000,
  supplier_payout_amount_kobo: 10_000_000,
  supplier_payout_status: 'SUCCESS',
  dispute_refund_amount_kobo: 4_000_000,
  dispute_refund_status: 'PENDING',
  return_status: 'RECEIPT_CONFIRMED',
  payment_statuses: [{ status: 'FUNDS_SECURED', amount_kobo: 12_000_000, mode: 'PAGA' }],
  refunds: [{ id: 'private-excess-refund-id', refund_classification: 'EXCESS_PAYMENT', amount_kobo: 2_000_000, status: 'PENDING' }, { id: 'dispute-refund-id', refund_classification: 'DISPUTE_RETURN', amount_kobo: 4_000_000, status: 'SUCCESS' }],
  payouts: [{ id: 'supplier-payout-id', amount_kobo: 10_000_000, status: 'SUCCESS' }],
};

test('buyer settlement view includes their excess refund and dispute refund progress', () => {
  const rows = settlementRows(summary, 'BUYER');
  assert.deepEqual(rows.map(x => x.label), [
    'Payment amount received', 'Agreed payment amount', 'Supplier payout status',
    'Excess payment refund amount', 'Excess payment refund status',
    'Dispute refund amount', 'Dispute refund status',
  ]);
  assert.equal(rows[2].value, 'SUCCESS');
  assert.equal(rows[3].value, 2_000_000);
});

test('supplier settlement view exposes payout and dispute refund but no excess payment data', () => {
  const rows = settlementRows(summary, 'SUPPLIER');
  const serialized = JSON.stringify(rows);
  assert.match(serialized, /your payout/i);
  assert.match(serialized, /dispute refund/i);
  assert.doesNotMatch(serialized, /excess|original payment|private-excess-refund-id/i);
});

test('moderator settlement view includes the complete financial picture', () => {
  const rows = settlementRows(summary, 'SENIOR_MODERATOR');
  assert.equal(rows.find(x => x.label === 'Original payment received').value, 12_000_000);
  assert.equal(rows.find(x => x.label === 'Excess payment refund').value, 2_000_000);
  assert.equal(rows.find(x => x.label === 'Supplier entitlement').value, 10_000_000);
  assert.match(rows.find(x => x.label === 'Refund instructions').value, /EXCESS_PAYMENT/);
  assert.match(rows.find(x => x.label === 'Refund instructions').value, /DISPUTE_RETURN/);
});

test('unknown roles receive no settlement display fields', () => {
  assert.deepEqual(settlementRows(summary, 'USER'), []);
});


test('moderator settlement detail is available in the local demo adapter', async () => {
  api.reset();
  api.switchRole('MODERATOR');
  const result = await api.moderatorSettlement('tx_3c55');
  assert.equal(result.transaction_id, 'tx_3c55');
  assert.equal(result.settlement.excess_payment_refund_status, 'NOT_REQUIRED');
  assert.ok(Array.isArray(result.settlement.refunds));
});
