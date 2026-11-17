"""SimulatedPaymentProvider: scenarios, ownership, environment gating, labelling."""
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import conftest  # noqa: F401
from helpers import (awaiting_payment, client, journals, pending_payment, register, start_payment, states, unique_email)
from app.main import app, run_simulated_payments, tx_data, iso
from app.db import SessionLocal, Aggregate, Payment, PagaSubsidiaryAccount, Audit, Notification, ProviderEvent, now
from app.providers import simulated as sim


def simulate(pay, scenario=None, headers=None, **extra):
    body = dict(extra)
    if scenario is not None:
        body['scenario'] = scenario
    return client.post(f"/api/v1/transactions/{pay['txid']}/payments/{pay['payment_id']}/simulate", headers=headers or pay['buyer'], json=body)


def tx_view(pay, who):
    return client.get(f"/api/v1/transactions/{pay['txid']}", headers=pay[who]).json()


def test_payment_start_is_labelled_simulated_and_amount_is_server_side():
    ctx = awaiting_payment()
    r = client.post(f"/api/v1/transactions/{ctx['txid']}/payments", headers={**ctx['buyer'], 'Idempotency-Key': 'k-label'}, json={'amount_kobo': 1})
    d = r.json()['data']
    assert d['mode'] == 'SIMULATED' and d['simulated'] is True and d['status'] == 'PENDING'
    assert d['amount_kobo'] == 2000000  # a client-supplied amount is ignored
    assert d['checkout']['not_paga'] is True and 'not a Paga payment' in d['checkout']['label']
    assert set(d['checkout']['scenarios']) == set(sim.SCENARIOS)
    assert 'simulate_payment' in client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()['allowed_actions']
    assert 'simulate_payment' not in client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['supplier']).json()['allowed_actions']


def test_success_funds_transaction_consistently_for_both_parties():
    pay = pending_payment()
    r = simulate(pay, 'SUCCESS')
    assert r.status_code == 200, r.text
    d = r.json()['data']
    assert d['status'] == 'FUNDS_SECURED' and d['transaction_status'] == 'FUNDED' and d['simulated'] is True
    assert [e['result'] for e in d['events']] == ['APPLIED', 'APPLIED']
    assert states(pay) == ('FUNDS_SECURED', 'FUNDED')
    for who in ('buyer', 'supplier'):
        v = tx_view(pay, who)
        assert v['status'] == 'FUNDED' and v['payments'][0]['status'] == 'FUNDS_SECURED' and v['payments'][0]['mode'] == 'SIMULATED'
    assert journals(pay) == ['FUNDS_SECURED', 'PAYMENT_RECEIVED']
    ledger = client.get(f"/api/v1/transactions/{pay['txid']}/ledger", headers=pay['buyer']).json()['data']
    assert ledger and all(row['provider_mode'] == 'SIMULATED' for row in ledger)
    s = SessionLocal()
    try:
        actions = [a.action for a in s.scalars(select(Audit).where(Audit.entity_id == pay['payment_id'])).all()]
        assert 'SIMULATED_PAYMENT_STARTED' in actions and 'SIMULATED_SCENARIO_REQUESTED' in actions and 'PROVIDER_WEBHOOK_PROCESSED' in actions
        notes = s.scalars(select(Notification).where(Notification.entity_id == pay['txid'], Notification.type == 'FUNDS_SECURED')).all()
        assert len(notes) == 2 and all(n.payload['mode'] == 'SIMULATED' for n in notes)
        assert s.scalar(select(PagaSubsidiaryAccount).where(PagaSubsidiaryAccount.transaction_id == pay['txid'])) is None
        assert s.get(Payment, pay['payment_id']).mode == 'SIMULATED'
    finally:
        s.close()


def test_failure_does_not_fund_and_allows_new_attempt():
    pay = pending_payment()
    d = simulate(pay, 'FAILURE').json()['data']
    assert d['status'] == 'FAILED' and d['transaction_status'] == 'AWAITING_PAYMENT'
    assert states(pay) == ('FAILED', 'AWAITING_PAYMENT') and journals(pay) == []
    assert tx_view(pay, 'supplier')['status'] == 'AWAITING_PAYMENT'
    second = start_payment(pay)
    assert simulate(second, 'SUCCESS').json()['data']['transaction_status'] == 'FUNDED'


def test_pending_scenario_changes_nothing():
    pay = pending_payment()
    d = simulate(pay, 'PENDING').json()['data']
    assert d['status'] == 'PENDING' and states(pay) == ('PENDING', 'AWAITING_PAYMENT') and journals(pay) == []


def test_delayed_scenario_confirms_only_after_release_time_via_worker():
    pay = pending_payment()
    d = simulate(pay, 'DELAYED').json()['data']
    assert d['status'] == 'PENDING' and 'will confirm' in d['note']
    s = SessionLocal()
    try:
        assert run_simulated_payments(s)['released'] == 0  # not due yet
    finally:
        s.close()
    assert states(pay)[0] == 'PENDING'
    # move release time into the past (deterministic, no sleeping)
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, pay['txid']); data = tx_data(tx)
        data['payments'][0]['simulation']['release_at'] = iso(now() - timedelta(seconds=1)); tx.data = data; s.commit()
        assert run_simulated_payments(s)['released'] >= 1
        assert run_simulated_payments(s)['released'] == 0  # repeated cycle: idempotent
    finally:
        s.close()
    assert states(pay) == ('FUNDS_SECURED', 'FUNDED') and journals(pay) == ['FUNDS_SECURED', 'PAYMENT_RECEIVED']
    assert tx_view(pay, 'buyer')['payments'][0]['simulation']['outcome'] == 'APPLIED'


def test_delayed_scenario_is_also_released_by_buyer_status_refresh():
    pay = pending_payment()
    simulate(pay, 'DELAYED')
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, pay['txid']); data = tx_data(tx)
        data['payments'][0]['simulation']['release_at'] = iso(now() - timedelta(seconds=1)); tx.data = data; s.commit()
    finally:
        s.close()
    r = client.post(f"/api/v1/transactions/{pay['txid']}/payments/{pay['payment_id']}/refresh", headers=pay['buyer'])
    assert r.status_code == 200 and r.json()['status'] == 'FUNDS_SECURED' and r.json()['transaction_status'] == 'FUNDED'


def test_duplicate_scenario_is_idempotent_and_never_double_books():
    pay = pending_payment()
    d = simulate(pay, 'DUPLICATE').json()['data']
    results = [e['result'] for e in d['events']]
    assert results[:2] == ['APPLIED', 'APPLIED'] and results[2] == 'DUPLICATE'
    assert results[3].startswith('IGNORED') and results[4].startswith('IGNORED')
    assert states(pay) == ('FUNDS_SECURED', 'FUNDED') and journals(pay) == ['FUNDS_SECURED', 'PAYMENT_RECEIVED']


def test_double_click_success_is_safe():
    pay = pending_payment()
    simulate(pay, 'SUCCESS')
    again = simulate(pay, 'SUCCESS')
    assert again.status_code == 200 and [e['result'] for e in again.json()['data']['events']] == ['DUPLICATE', 'DUPLICATE']
    assert journals(pay) == ['FUNDS_SECURED', 'PAYMENT_RECEIVED']


@pytest.mark.parametrize('scenario,detail', [('TIMEOUT', 'SIMULATED_PROVIDER_TIMEOUT'), ('UNKNOWN', 'SIMULATED_AMBIGUOUS_RESULT')])
def test_timeout_and_unknown_leave_payment_unknown_and_reconcile(scenario, detail):
    pay = pending_payment()
    d = simulate(pay, scenario).json()['data']
    assert d['status'] == 'UNKNOWN' and d['transaction_status'] == 'AWAITING_PAYMENT' and journals(pay) == []
    # an unknown attempt blocks a second attempt (no assumptions, no new key)
    r = client.post(f"/api/v1/transactions/{pay['txid']}/payments", headers={**pay['buyer'], 'Idempotency-Key': 'again-' + scenario}, json={})
    assert r.status_code == 409
    # a definitive result reconciles it
    assert simulate(pay, 'SUCCESS').json()['data']['transaction_status'] == 'FUNDED'


def test_unknown_can_reconcile_to_failure():
    pay = pending_payment()
    simulate(pay, 'TIMEOUT')
    assert simulate(pay, 'FAILURE').json()['data']['status'] == 'FAILED'
    assert states(pay) == ('FAILED', 'AWAITING_PAYMENT')


def test_configured_default_scenario_is_used_when_none_supplied(monkeypatch):
    monkeypatch.setenv('SIMULATED_PAYMENT_SCENARIO', 'FAILURE')
    pay = pending_payment()
    assert pay['buyer'] and client.post(f"/api/v1/transactions/{pay['txid']}/payments/{pay['payment_id']}/simulate", headers=pay['buyer'], json={}).json()['data']['status'] == 'FAILED'


def test_invalid_configured_scenario_is_an_error_not_a_silent_default(monkeypatch):
    monkeypatch.setenv('SIMULATED_PAYMENT_SCENARIO', 'BANANA')
    with pytest.raises(ValueError):
        sim.configured_scenario()
    import app.main as m
    monkeypatch.setattr(m, 'APP_ENV', 'development')
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass


def test_scenario_aliases_and_unknown_scenario():
    pay = pending_payment()
    assert simulate(pay, 'bogus').status_code == 400
    assert simulate(pay, 'failed').json()['data']['scenario'] == 'FAILURE'


# --------------------------------------------------------------------------- security
def test_client_cannot_supply_amount_status_signature_or_reference():
    pay = pending_payment()
    for field, value in [('amount_kobo', 1), ('status', 'FUNDS_SECURED'), ('signature', 'x'), ('provider_ref', pay['provider_ref']), ('event_id', 'e')]:
        r = simulate(pay, 'SUCCESS', **{field: value})
        assert r.status_code == 400 and r.json()['error']['details']['rejected_fields'] == [field]
    assert states(pay) == ('PENDING', 'AWAITING_PAYMENT')


def test_outsider_supplier_and_other_buyer_cannot_confirm_payment():
    pay = pending_payment()
    outsider = register('Outsider', unique_email('out'))
    other_buyer = register('Other Buyer', unique_email('ob'))
    assert simulate(pay, 'SUCCESS', headers=outsider).status_code == 404
    assert simulate(pay, 'SUCCESS', headers=other_buyer).status_code == 404
    r = simulate(pay, 'SUCCESS', headers=pay['supplier'])
    assert r.status_code == 403
    assert client.post(f"/api/v1/transactions/{pay['txid']}/payments/{pay['payment_id']}/simulate", json={'scenario': 'SUCCESS'}).status_code in (401, 403)
    assert states(pay) == ('PENDING', 'AWAITING_PAYMENT') and journals(pay) == []


def test_payment_of_another_transaction_cannot_be_used():
    a, b = pending_payment(), pending_payment()
    r = client.post(f"/api/v1/transactions/{a['txid']}/payments/{b['payment_id']}/simulate", headers=a['buyer'], json={'scenario': 'SUCCESS'})
    assert r.status_code == 404
    assert states(b) == ('PENDING', 'AWAITING_PAYMENT')


def test_state_transition_validation_applies_to_simulated_events():
    pay = pending_payment()
    simulate(pay, 'SUCCESS')
    r = simulate(pay, 'FAILURE')
    assert r.status_code == 409 and r.json()['error']['code'] == 'INVALID_PAYMENT_TRANSITION'
    assert states(pay) == ('FUNDS_SECURED', 'FUNDED')
    pay2 = pending_payment()
    simulate(pay2, 'FAILURE')
    r = simulate(pay2, 'SUCCESS')
    assert r.status_code == 409 and states(pay2) == ('FAILED', 'AWAITING_PAYMENT') and journals(pay2) == []


def test_amount_validation_applies_to_simulated_events():
    pay = pending_payment()
    s = SessionLocal()
    try:
        p = s.get(Payment, pay['payment_id']); p.amount_kobo = 1; s.commit()
    finally:
        s.close()
    r = simulate(pay, 'SUCCESS')
    assert r.status_code == 409 and r.json()['error']['code'] == 'PAYMENT_AMOUNT_INCONSISTENT'
    assert states(pay) == ('PENDING', 'AWAITING_PAYMENT') and journals(pay) == []


def test_simulator_cannot_complete_a_non_simulated_payment():
    pay = pending_payment()
    s = SessionLocal()
    try:
        p = s.get(Payment, pay['payment_id']); p.mode = 'PAGA_SANDBOX'; s.commit()
    finally:
        s.close()
    assert simulate(pay, 'SUCCESS').status_code == 409
    from helpers import callback
    r = callback(pay, 'FUNDS_SECURED')
    assert r.status_code == 409 and r.json()['error']['code'] == 'CALLBACK_PROVIDER_MISMATCH'
    assert states(pay)[0] == 'PENDING'


# --------------------------------------------------------------------------- environment gating
@pytest.mark.parametrize('env,flag,allowed', [('development', None, True), ('test', None, True), ('local', None, True), ('production', None, False), ('production', 'true', False),
                                               ('demo', None, False), ('demo', 'false', False), ('demo', 'true', True), ('staging', 'true', False), ('Production ', 'true', False)])
def test_simulator_environment_gate(monkeypatch, env, flag, allowed):
    monkeypatch.setenv('APP_ENV', env)
    monkeypatch.delenv('ENABLE_PAYMENT_SIMULATOR', raising=False)
    if flag is not None:
        monkeypatch.setenv('ENABLE_PAYMENT_SIMULATOR', flag)
    assert sim.simulator_allowed() is allowed


def test_production_blocks_start_simulate_and_webhook(monkeypatch):
    pay = pending_payment()
    ctx = awaiting_payment()
    monkeypatch.setenv('APP_ENV', 'production')
    r = client.post(f"/api/v1/transactions/{ctx['txid']}/payments", headers={**ctx['buyer'], 'Idempotency-Key': 'prod-1'}, json={})
    assert r.status_code == 503 and r.json()['error']['code'] == 'SIMULATOR_DISABLED'
    r = simulate(pay, 'SUCCESS')
    assert r.status_code == 403 and r.json()['error']['code'] == 'SIMULATOR_DISABLED'
    from helpers import callback
    assert callback(pay, 'FUNDS_SECURED').status_code == 404
    assert 'simulate_payment' not in tx_view(pay, 'buyer')['allowed_actions']
    monkeypatch.setenv('APP_ENV', 'development')
    assert states(pay) == ('PENDING', 'AWAITING_PAYMENT')


def test_production_startup_refuses_the_simulator(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m, 'APP_ENV', 'production')
    monkeypatch.setenv('PAYMENT_PROVIDER', 'simulated')
    with pytest.raises(RuntimeError, match='never permitted in production'):
        with TestClient(app):
            pass


def test_worker_does_nothing_for_simulator_outside_allowed_environments(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    s = SessionLocal()
    try:
        assert run_simulated_payments(s) == {'released': 0, 'mode': 'SIMULATED', 'enabled': False}
    finally:
        s.close()


def test_signing_secret_is_never_returned_by_any_simulator_response():
    pay = pending_payment()
    secret = sim.signing_secret().decode()
    for r in (simulate(pay, 'SUCCESS'), client.get(f"/api/v1/transactions/{pay['txid']}", headers=pay['buyer']), client.get(f"/api/v1/transactions/{pay['txid']}/payments", headers=pay['buyer'])):
        assert secret not in r.text
        assert 'signature' not in r.text.lower()
