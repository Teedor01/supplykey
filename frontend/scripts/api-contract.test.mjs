import test, { beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/lib/api.js';

beforeEach(() => api.reset());

test('POST requests receive an idempotency key and pending payment does not fund a transaction', async () => {
  api.switchRole('BUYER');
  const attempt = await api.action('/transactions/tx_a210/payments', {});
  assert.equal(attempt.status, 'PENDING');
  const transaction = await api.transaction('tx_a210');
  assert.equal(transaction.status, 'AWAITING_PAYMENT');
  assert.equal(transaction.payments.length, 1);
  assert.equal(transaction.payments[0].status, 'PENDING');
});

test('same idempotency key replays the original response and rejects a changed body', async () => {
  api.switchRole('BUYER');
  const headers = { 'Idempotency-Key': 'test-payment-key' };
  const first = await api.action('/transactions/tx_a210/payments', {}, headers);
  const replay = await api.action('/transactions/tx_a210/payments', {}, headers);
  assert.equal(replay.payment_id, first.payment_id);
  assert.equal((await api.transaction('tx_a210')).payments.length, 1);
  await assert.rejects(
    api.action('/transactions/tx_a210/payments', { amount_kobo: 1 }, headers),
    error => error?.data?.error?.code === 'IDEMPOTENCY_KEY_REUSED'
  );
});

test('dispatch is rejected until the transaction is backend-confirmed funded', async () => {
  api.switchRole('SUPPLIER');
  await assert.rejects(
    api.action('/transactions/tx_a210/dispatch', { lines: [{ item_id: 'it_2', quantity: 1 }] }),
    error => error?.data?.error?.code === 'INVALID_STATE_TRANSITION'
  );
});

test('high-value acceptance payout requires senior approval and never claims settlement', async () => {
  api.switchRole('MODERATOR');
  const result = await api.action('/mod/decisions/dec_1/confirm', {
    reason: 'The agreed specifications and submitted evidence have been reviewed. The accepted quantity satisfies the recorded agreement conditions.',
    evidence_ids_considered: []
  });
  assert.equal(result.payout.status, 'PENDING_APPROVAL');
  await assert.rejects(
    api.action(`/mod/payouts/${result.payout.id}/approve`, {}),
    error => error?.data?.error?.code === 'FORBIDDEN'
  );
  api.switchRole('SENIOR_MODERATOR');
  const approval = await api.action(`/mod/payouts/${result.payout.id}/approve`, {});
  assert.equal(approval.status, 'QUEUED');
  assert.notEqual(approval.status, 'SUCCEEDED');
});

test('mock idempotency records never persist raw registration passwords', async () => {
  api.switchRole('BUYER');
  await api.register({ display_name: 'Test User', email: 'test@example.com', password: 'not-for-storage-123', accepted_terms: true });
  assert.equal(JSON.stringify(api.getDemoState().idempotency).includes('not-for-storage-123'), false);
});

import { buildApiUrl, normalizeApiOrigin } from '../src/lib/api.js';

test('API base URL is an origin and rejects an already-versioned base path', () => {
  assert.equal(normalizeApiOrigin('http://localhost:8000/'), 'http://localhost:8000');
  assert.throws(() => normalizeApiOrigin('http://localhost:8000/api/v1'), /without \/api\/v1|Do not include \/api\/v1/);
});

test('all API endpoint families resolve to exactly one /api/v1 prefix', () => {
  const paths = [
    '/auth/register', '/auth/login', '/auth/verify-otp', '/auth/resend-otp',
    '/auth/refresh', '/auth/logout', '/me', '/me/profile', '/me/payout-account',
    '/transactions', '/transactions/tx_1', '/transactions/tx_1/payments',
    '/transactions/tx_1/dispatch', '/transactions/tx_1/delivery/evidence',
    '/evidence/upload-urls', '/evidence/ev_1/complete', '/evidence/ev_1/content', '/disputes/dis_1',
    '/mod/disputes', '/mod/disputes/dis_1/authorize-refund', '/mod/disputes/dis_1/decision', '/transactions/tx_1/returns', '/returns/ret_1/confirm-receipt', '/mod/acceptance-queue', '/notifications',
    '/admin/audit', '/admin/config/key', '/worker/run-deadlines'
  ];
  for (const path of paths) {
    const url = buildApiUrl('http://localhost:8000', path);
    assert.equal(url, `http://localhost:8000/api/v1${path}`);
    assert.equal(url.includes('/api/v1/api/v1'), false);
  }
  assert.throws(() => buildApiUrl('http://localhost:8000', '/api/v1/auth/login'), /versionless absolute path/);
});

test('Paga bank directory and beneficiary confirmation use versioned backend endpoints', () => {
  assert.equal(buildApiUrl('https://api.example.test', '/payments/paga/banks'), 'https://api.example.test/api/v1/payments/paga/banks');
  assert.equal(buildApiUrl('https://api.example.test', '/me/payout-account'), 'https://api.example.test/api/v1/me/payout-account');
  assert.equal(buildApiUrl('https://api.example.test', '/me/payout-account/confirm'), 'https://api.example.test/api/v1/me/payout-account/confirm');
});
