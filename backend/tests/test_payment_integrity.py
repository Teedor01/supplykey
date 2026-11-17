"""Regression tests for payment-integrity defects A and B.

A: a correctly signed FUNDS_SECURED callback with amount_kobo=1 funded a 2,000,000-kobo payment.
B: a later callback with a new event id regressed Payment.status while Transaction.status stayed FUNDED.
"""
import hashlib, hmac, json, threading, uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func

import conftest  # noqa: F401  (sets the isolated test environment first)
from helpers import (callback, client, journals, pending_payment, sign, states)
from app.main import app
from app.db import SessionLocal, LedgerEntry, ProviderEvent, Audit, ProviderEventProcessing, Aggregate, Payment, Journal


def rejection_audits(pay):
    s = SessionLocal()
    try:
        return [a for a in s.scalars(select(Audit).where(Audit.action == 'PAYMENT_CALLBACK_REJECTED', Audit.entity_id == pay['payment_id'])).all()]
    finally:
        s.close()


def ledger_rows(pay):
    s = SessionLocal()
    try:
        return s.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == pay['txid'])).all()
    finally:
        s.close()


def assert_unchanged_pending(pay):
    assert states(pay) == ('PENDING', 'AWAITING_PAYMENT')
    assert journals(pay) == []


# --------------------------------------------------------------------------- Defect A
class TestAmountAndReferenceValidation:
    def test_exact_matching_amount_funds_payment(self):
        pay = pending_payment()
        assert pay['amount_kobo'] == 2000000
        r = callback(pay, 'FUNDS_SECURED')
        assert r.status_code == 200 and r.json()['processed'] is True
        assert states(pay) == ('FUNDS_SECURED', 'FUNDED')
        assert journals(pay) == ['FUNDS_SECURED', 'PAYMENT_RECEIVED']

    def test_one_kobo_amount_is_rejected_and_nothing_changes(self):
        pay = pending_payment()  # reproduces the confirmed defect: expected 2,000,000, callback says 1
        r = callback(pay, 'FUNDS_SECURED', amount_kobo=1)
        assert r.status_code == 422
        assert r.json()['error']['code'] == 'CALLBACK_AMOUNT_MISMATCH'
        assert r.json()['error']['details'] == {'expected_amount_kobo': 2000000, 'received_amount_kobo': 1}
        assert_unchanged_pending(pay)
        assert ledger_rows(pay) == []
        audits = rejection_audits(pay)
        assert len(audits) == 1 and audits[0].details['code'] == 'CALLBACK_AMOUNT_MISMATCH' and audits[0].details['requires_review'] is True

    @pytest.mark.parametrize('amount', [1999999, 0, -2000000])
    def test_lower_amount_is_rejected(self, amount):
        pay = pending_payment()
        assert callback(pay, 'FUNDS_SECURED', amount_kobo=amount).status_code == 422
        assert_unchanged_pending(pay)

    @pytest.mark.parametrize('amount', [2000001, 4000000, 20000000000])
    def test_higher_amount_is_rejected(self, amount):
        pay = pending_payment()
        r = callback(pay, 'FUNDS_SECURED', amount_kobo=amount)
        assert r.status_code == 422 and r.json()['error']['code'] == 'CALLBACK_AMOUNT_MISMATCH'
        assert_unchanged_pending(pay)

    @pytest.mark.parametrize('status', ['FUNDS_SECURED', 'PAYMENT_RECEIVED'])
    def test_missing_amount_is_rejected_for_money_events(self, status):
        pay = pending_payment()
        r = callback(pay, status, amount_kobo=None)
        assert r.status_code == 422 and r.json()['error']['code'] == 'CALLBACK_AMOUNT_INVALID'
        assert_unchanged_pending(pay)

    @pytest.mark.parametrize('bad', ['2000000', 2000000.0, True, None, [2000000], {'v': 1}])
    def test_non_integer_amount_types_are_rejected(self, bad):
        """Amounts must be integer kobo. 2000000.0, "2000000" and booleans are not."""
        pay = pending_payment()
        r = callback(pay, 'FUNDS_SECURED', amount_kobo=bad)
        assert r.status_code == 422 and r.json()['error']['code'] == 'CALLBACK_AMOUNT_INVALID'
        assert_unchanged_pending(pay)

    def test_incorrect_payment_reference_is_rejected(self):
        pay = pending_payment()
        r = callback(pay, 'FUNDS_SECURED', provider_ref='sim_doesnotexist')
        assert r.status_code == 404 and r.json()['error']['code'] == 'PAYMENT_NOT_FOUND'
        assert_unchanged_pending(pay)

    def test_missing_payment_reference_is_a_validation_error(self):
        pay = pending_payment()
        assert callback(pay, 'FUNDS_SECURED', provider_ref=None).status_code == 400
        assert_unchanged_pending(pay)

    def test_reference_of_another_payment_cannot_fund_this_one(self):
        """A callback for payment B that names payment A's transaction must not move either."""
        a, b = pending_payment(), pending_payment()
        r = callback(b, 'FUNDS_SECURED', transaction_reference=a['tx_reference'])
        assert r.status_code == 422 and r.json()['error']['code'] == 'CALLBACK_REFERENCE_MISMATCH'
        assert_unchanged_pending(a); assert_unchanged_pending(b)

    def test_incorrect_transaction_reference_is_rejected(self):
        pay = pending_payment()
        r = callback(pay, 'FUNDS_SECURED', transaction_reference='SK-WRONG-REFERENCE')
        assert r.status_code == 422 and r.json()['error']['code'] == 'CALLBACK_REFERENCE_MISMATCH'
        assert_unchanged_pending(pay)

    @pytest.mark.parametrize('currency', ['USD', 'GBP', 'ngn ', 'XOF'])
    def test_incorrect_currency_is_rejected(self, currency):
        pay = pending_payment()
        r = callback(pay, 'FUNDS_SECURED', currency=currency)
        # 'ngn ' (trailing space) is not NGN; case-insensitive match applies only to a clean code.
        assert r.status_code == 422 and r.json()['error']['code'] == 'CALLBACK_CURRENCY_MISMATCH'
        assert_unchanged_pending(pay)

    def test_lowercase_ngn_is_accepted(self):
        pay = pending_payment()
        assert callback(pay, 'FUNDS_SECURED', currency='ngn').status_code == 200

    def test_invalid_signature_is_rejected_and_changes_nothing(self):
        pay = pending_payment()
        raw, _ = sign({'event_id': 'sig-1', 'provider_ref': pay['provider_ref'], 'status': 'FUNDS_SECURED', 'amount_kobo': pay['amount_kobo']})
        for headers in ({'X-Sim-Signature': 'deadbeef'}, {}, {'X-Sim-Signature': hmac.new(b'wrong-secret', raw, hashlib.sha256).hexdigest()}):
            r = client.post('/api/v1/webhooks/payments/simulated', content=raw, headers=headers)
            assert r.status_code == 401 and r.json()['error']['code'] == 'INVALID_WEBHOOK_SIGNATURE'
        assert_unchanged_pending(pay)

    def test_tampered_body_after_signing_is_rejected(self):
        pay = pending_payment()
        raw, sig = sign({'event_id': 'tamper-1', 'provider_ref': pay['provider_ref'], 'status': 'FUNDS_SECURED', 'amount_kobo': 1})
        tampered = raw.replace(b'"amount_kobo":1', b'"amount_kobo":2000000')
        r = client.post('/api/v1/webhooks/payments/simulated', content=tampered, headers={'X-Sim-Signature': sig})
        assert r.status_code == 401
        assert_unchanged_pending(pay)

    def test_unsigned_request_cannot_burn_a_genuine_event_id(self):
        """Invalid-signature traffic must not consume (provider, event_id), or real events would look like replays."""
        pay = pending_payment()
        victim_event = f"sim:{pay['payment_id']}:SUCCESS:2"
        bad = client.post('/api/v1/webhooks/payments/simulated', json={'event_id': victim_event, 'provider_ref': pay['provider_ref'], 'status': 'FUNDS_SECURED'}, headers={'X-Sim-Signature': 'x'})
        assert bad.status_code == 401
        r = callback(pay, 'FUNDS_SECURED', event_id=victim_event)
        assert r.status_code == 200 and r.json().get('duplicate') is None and r.json()['processed'] is True

    def test_signed_but_invalid_callbacks_are_recorded_safely(self):
        pay = pending_payment()
        callback(pay, 'FUNDS_SECURED', amount_kobo=5)
        callback(pay, 'FUNDS_SECURED', currency='USD')
        callback(pay, 'FUNDS_SECURED', transaction_reference='nope')
        s = SessionLocal()
        try:
            statuses = [p.status for p in s.scalars(select(ProviderEventProcessing).join(ProviderEvent, ProviderEvent.id == ProviderEventProcessing.provider_event_id).where(ProviderEvent.raw_body['provider_ref'].as_string() == pay['provider_ref'])).all()]
        finally:
            s.close()
        assert statuses.count('REJECTED') == 3
        assert len(rejection_audits(pay)) == 3
        assert_unchanged_pending(pay)

    def test_unsupported_status_is_rejected(self):
        pay = pending_payment()
        r = callback(pay, 'REFUNDED')
        assert r.status_code == 422 and r.json()['error']['code'] == 'CALLBACK_STATUS_INVALID'
        assert_unchanged_pending(pay)

    def test_expected_amount_comes_from_stored_payment_not_the_callback(self):
        """Even if the callback copies a plausible-looking amount, only the stored payment amount is accepted."""
        pay = pending_payment(quantity=3, unit_price_kobo=1234500)
        assert pay['amount_kobo'] == 3703500
        assert callback(pay, 'FUNDS_SECURED', amount_kobo=3703499).status_code == 422
        assert callback(pay, 'FUNDS_SECURED', amount_kobo=3703500).status_code == 200

    def test_payment_amount_must_still_equal_transaction_total(self):
        pay = pending_payment()
        s = SessionLocal()
        try:
            p = s.get(Payment, pay['payment_id']); p.amount_kobo = 5; s.commit()
        finally:
            s.close()
        r = callback(pay, 'FUNDS_SECURED', amount_kobo=5)
        assert r.status_code == 409 and r.json()['error']['code'] == 'PAYMENT_AMOUNT_INCONSISTENT'
        assert_unchanged_pending(pay)

    def test_rejected_event_id_is_not_reusable_to_force_through(self):
        pay = pending_payment()
        eid = uuid.uuid4().hex
        assert callback(pay, 'FUNDS_SECURED', event_id=eid, amount_kobo=1).status_code == 422
        again = callback(pay, 'FUNDS_SECURED', event_id=eid)
        assert again.status_code == 200 and again.json()['duplicate'] is True
        assert_unchanged_pending(pay)
        # A corrected event with a NEW id is processed normally.
        assert callback(pay, 'FUNDS_SECURED').status_code == 200


# --------------------------------------------------------------------------- Defect B
class TestMonotonicStateTransitions:
    def test_received_then_secured(self):
        pay = pending_payment()
        assert callback(pay, 'PAYMENT_RECEIVED').json()['payment_status'] == 'PAYMENT_RECEIVED'
        assert states(pay) == ('PAYMENT_RECEIVED', 'AWAITING_PAYMENT')
        assert callback(pay, 'FUNDS_SECURED').json()['payment_status'] == 'FUNDS_SECURED'
        assert states(pay) == ('FUNDS_SECURED', 'FUNDED')
        assert journals(pay) == ['FUNDS_SECURED', 'PAYMENT_RECEIVED']

    def test_secured_then_late_received_does_not_regress(self):
        """Confirmed defect B: this used to set Payment.status back to PAYMENT_RECEIVED while the transaction stayed FUNDED."""
        pay = pending_payment()
        assert callback(pay, 'FUNDS_SECURED').status_code == 200
        before = journals(pay)
        r = callback(pay, 'PAYMENT_RECEIVED')  # brand-new event id
        assert r.status_code == 200 and r.json()['processed'] is False
        assert r.json()['reason'] == 'STALE_OR_REPEATED_AFTER_SECURED'
        assert states(pay) == ('FUNDS_SECURED', 'FUNDED')
        assert journals(pay) == before

    def test_secured_then_secured_with_new_event_id_is_idempotent(self):
        pay = pending_payment()
        callback(pay, 'FUNDS_SECURED')
        rows = len(ledger_rows(pay))
        r = callback(pay, 'FUNDS_SECURED')
        assert r.status_code == 200 and r.json()['processed'] is False
        assert states(pay) == ('FUNDS_SECURED', 'FUNDED')
        assert len(ledger_rows(pay)) == rows
        s = SessionLocal()
        try:
            tx = s.get(Aggregate, pay['txid'])
            assert len(tx.data['lots']) == 1  # lots were not rebuilt
        finally:
            s.close()

    def test_duplicate_event_id_is_a_noop_and_creates_no_second_ledger_entry(self):
        pay = pending_payment()
        assert callback(pay, 'PAYMENT_RECEIVED', event_id='dup-A').json()['processed'] is True
        rows = len(ledger_rows(pay))
        for _ in range(3):
            r = callback(pay, 'PAYMENT_RECEIVED', event_id='dup-A')
            assert r.status_code == 200 and r.json()['duplicate'] is True
        assert len(ledger_rows(pay)) == rows == 2

    def test_repeated_received_with_new_id_is_ignored_not_double_booked(self):
        pay = pending_payment()
        callback(pay, 'PAYMENT_RECEIVED')
        r = callback(pay, 'PAYMENT_RECEIVED')
        assert r.json()['processed'] is False and r.json()['reason'] == 'ALREADY_IN_STATE'
        assert journals(pay) == ['PAYMENT_RECEIVED']

    def test_failed_then_late_success_is_rejected_and_flagged(self):
        pay = pending_payment()
        assert callback(pay, 'FAILED').json()['payment_status'] == 'FAILED'
        for status in ('PAYMENT_RECEIVED', 'FUNDS_SECURED'):
            r = callback(pay, status)
            assert r.status_code == 409 and r.json()['error']['code'] == 'INVALID_PAYMENT_TRANSITION'
            assert r.json()['error']['details']['reason'] == 'LATE_SUCCESS_AFTER_FAILURE'
        assert states(pay) == ('FAILED', 'AWAITING_PAYMENT')
        assert journals(pay) == []
        assert all(a.details['requires_review'] for a in rejection_audits(pay))

    def test_success_then_late_failure_is_rejected_and_state_kept(self):
        pay = pending_payment()
        callback(pay, 'FUNDS_SECURED')
        r = callback(pay, 'FAILED')
        assert r.status_code == 409 and r.json()['error']['details']['reason'] == 'FAILURE_AFTER_FUNDS_SECURED'
        assert states(pay) == ('FUNDS_SECURED', 'FUNDED')  # payment and transaction stay consistent

    def test_received_then_failed_is_rejected(self):
        pay = pending_payment()
        callback(pay, 'PAYMENT_RECEIVED')
        r = callback(pay, 'FAILED')
        assert r.status_code == 409 and r.json()['error']['details']['reason'] == 'FAILURE_AFTER_PAYMENT_RECEIVED'
        assert states(pay) == ('PAYMENT_RECEIVED', 'AWAITING_PAYMENT')

    def test_late_unknown_after_secured_cannot_regress(self):
        pay = pending_payment()
        callback(pay, 'FUNDS_SECURED')
        assert callback(pay, 'UNKNOWN').json()['processed'] is False
        assert states(pay) == ('FUNDS_SECURED', 'FUNDED')

    @pytest.mark.parametrize('resolution,expected', [('FUNDS_SECURED', ('FUNDS_SECURED', 'FUNDED')), ('PAYMENT_RECEIVED', ('PAYMENT_RECEIVED', 'AWAITING_PAYMENT')), ('FAILED', ('FAILED', 'AWAITING_PAYMENT'))])
    def test_unknown_resolves_only_on_a_definitive_result(self, resolution, expected):
        pay = pending_payment()
        assert callback(pay, 'UNKNOWN').json()['payment_status'] == 'UNKNOWN'
        assert callback(pay, 'UNKNOWN').json()['processed'] is False
        assert callback(pay, resolution).status_code == 200
        assert states(pay) == expected

    def test_direct_pending_to_secured_posts_both_journals_balanced(self):
        """Previously only FUNDS_SECURED was posted, debiting RECEIVED_UNSECURED that was never credited."""
        pay = pending_payment()
        callback(pay, 'FUNDS_SECURED')
        bal = {}
        for row in ledger_rows(pay):
            bal[row.account_code] = bal.get(row.account_code, 0) + (row.amount_kobo if row.direction == 'CREDIT' else -row.amount_kobo)
        assert bal == {'PROVIDER_CLEARING': -2000000, 'RECEIVED_UNSECURED': 0, 'HELD_FUNDS': 2000000}

    def test_money_callback_after_transaction_cancelled_is_not_applied(self):
        pay = pending_payment()
        callback(pay, 'FAILED')
        r = client.post(f"/api/v1/transactions/{pay['txid']}/cancel", headers=pay['buyer'])
        assert r.status_code == 200, r.text
        late = callback(pay, 'FUNDS_SECURED')
        assert late.status_code == 409
        assert states(pay)[1] == 'CANCELLED'

    def test_failed_attempt_allows_a_new_attempt_without_funding(self):
        from helpers import start_payment
        pay = pending_payment()
        callback(pay, 'FAILED')
        second = start_payment(pay)
        assert second['payment_id'] != pay['payment_id']
        assert callback(second, 'FUNDS_SECURED').status_code == 200
        assert states(second) == ('FUNDS_SECURED', 'FUNDED')
        # the earlier failed attempt stays failed
        assert states(pay)[0] == 'FAILED'


# --------------------------------------------------------------------------- concurrency
def _fire(pay, jobs):
    """Run jobs concurrently, each in its own TestClient, released together by a barrier."""
    barrier = threading.Barrier(len(jobs)); out = [None] * len(jobs)

    def run(i, status, event_id):
        c = TestClient(app)
        payload = {'event_id': event_id, 'provider_ref': pay['provider_ref'], 'status': status, 'amount_kobo': pay['amount_kobo'], 'currency': 'NGN', 'transaction_reference': pay['tx_reference']}
        raw, sig = sign(payload)
        barrier.wait()
        out[i] = c.post('/api/v1/webhooks/payments/simulated', content=raw, headers={'X-Sim-Signature': sig})

    threads = [threading.Thread(target=run, args=(i, st, ev)) for i, (st, ev) in enumerate(jobs)]
    [t.start() for t in threads]; [t.join(timeout=60) for t in threads]
    return out


class TestConcurrentCallbacks:
    def test_same_event_delivered_concurrently_is_processed_once(self):
        pay = pending_payment()
        results = _fire(pay, [('FUNDS_SECURED', 'same-evt')] * 8)
        assert all(r.status_code == 200 for r in results), [r.text for r in results]
        assert sum(1 for r in results if r.json().get('processed') is True) == 1
        assert sum(1 for r in results if r.json().get('duplicate') is True) == 7
        assert states(pay) == ('FUNDS_SECURED', 'FUNDED')
        assert journals(pay) == ['FUNDS_SECURED', 'PAYMENT_RECEIVED']
        s = SessionLocal()
        try:
            n = s.scalar(select(func.count()).select_from(ProviderEvent).where(ProviderEvent.provider_event_id == 'same-evt', ProviderEvent.raw_body['provider_ref'].as_string() == pay['provider_ref']))
        finally:
            s.close()
        assert n == 1

    def test_two_different_events_delivered_concurrently_end_consistent(self):
        for _ in range(4):  # repeat to shake out races
            pay = pending_payment()
            results = _fire(pay, [('PAYMENT_RECEIVED', uuid.uuid4().hex), ('FUNDS_SECURED', uuid.uuid4().hex)])
            assert all(r.status_code == 200 for r in results), [r.text for r in results]
            assert states(pay) == ('FUNDS_SECURED', 'FUNDED')  # whichever order, it ends secured
            assert journals(pay) == ['FUNDS_SECURED', 'PAYMENT_RECEIVED']

    def test_many_mixed_events_concurrently_never_leave_inconsistent_state(self):
        pay = pending_payment()
        jobs = [('PAYMENT_RECEIVED', uuid.uuid4().hex), ('FUNDS_SECURED', uuid.uuid4().hex), ('PAYMENT_RECEIVED', uuid.uuid4().hex), ('FUNDS_SECURED', uuid.uuid4().hex), ('UNKNOWN', uuid.uuid4().hex)]
        results = _fire(pay, jobs)
        assert all(r.status_code in (200, 409) for r in results), [r.text for r in results]
        payment_status, tx_status = states(pay)
        # A FUNDS_SECURED event applies from every non-terminal state, so any ordering of these
        # five events must end secured, with payment and transaction in agreement.
        assert (payment_status, tx_status) == ('FUNDS_SECURED', 'FUNDED')
        assert len(set(journals(pay))) == len(journals(pay))
        s = SessionLocal()
        try:
            n_fs = s.scalar(select(func.count()).select_from(Journal).where(Journal.transaction_id == pay['txid'], Journal.entry_type == 'FUNDS_SECURED'))
        finally:
            s.close()
        assert n_fs <= 1

    def test_concurrent_success_and_failure_cannot_both_win(self):
        for _ in range(4):
            pay = pending_payment()
            results = _fire(pay, [('FUNDS_SECURED', uuid.uuid4().hex), ('FAILED', uuid.uuid4().hex)])
            payment_status, tx_status = states(pay)
            assert (payment_status, tx_status) in [('FUNDS_SECURED', 'FUNDED'), ('FAILED', 'AWAITING_PAYMENT')]
            winners = [r for r in results if r.status_code == 200 and r.json().get('processed')]
            assert len(winners) == 1
