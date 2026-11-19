"""Functional regression tests for refund separation and settlement completion."""
import json
import uuid

from sqlalchemy import select

from helpers import client, register, unique_email, awaiting_payment, start_payment, callback
from app.db import SessionLocal, Aggregate, Payment, Instruction, User, Audit, Journal, ProviderEvent, ProviderEventProcessing
from app.main import (create_overpayment_refund_instruction, reconcile_transaction_completion,
                      issue_token, tx_data, save_tx, post_journal, process_paga_payout_events)


def _funded_context():
    ctx = awaiting_payment(quantity=1, unit_price_kobo=1_000_000)
    payment = start_payment(ctx)
    assert callback(payment, 'PAYMENT_RECEIVED').status_code == 200
    assert callback(payment, 'FUNDS_SECURED').status_code == 200
    return ctx, payment


def _make_overpayment_and_dispute_refund(ctx, payment, excess=250_000):
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, ctx['txid'])
        p = s.get(Payment, payment['payment_id'])
        d = tx_data(tx)
        d['payments'] = [{**x, 'checkout': {**x.get('checkout', {}),
            'amount_received_kobo': p.amount_kobo + excess,
            'overpayment_kobo': excess,
            'overpayment_refund_status': 'PENDING_MODERATOR_APPROVAL'}} if x.get('id') == p.id else x
            for x in d.get('payments', [])]
        save_tx(s, tx, d)
        excess_instruction = create_overpayment_refund_instruction(s, p, tx, excess)
        # A separate rejection/dispute refund is classified independently.
        dispute_instruction = Instruction(id=str(uuid.uuid4()), transaction_id=tx.id,
            instruction_type='REFUND', refund_classification='DISPUTE_RETURN',
            amount_kobo=100_000, status='QUEUED', origin_id=str(uuid.uuid4()),
            deciding_moderator_id=None, idempotency_key='test-dispute-refund:'+tx.id)
        s.add(dispute_instruction)
        post_journal(s, tx.id, 'TEST_DISPUTE_REFUND_AUTHORIZED', dispute_instruction.id,
                     [('HELD_FUNDS', 'DEBIT', 100_000), ('BUYER_REFUNDABLE', 'CREDIT', 100_000)])
        payout_instruction = Instruction(id=str(uuid.uuid4()), transaction_id=tx.id,
            instruction_type='PAYOUT', amount_kobo=1_000_000, status='SUCCEEDED',
            origin_id=str(uuid.uuid4()), deciding_moderator_id=None,
            idempotency_key='test-supplier-payout:'+tx.id, provider_reference='PAYOUT-REF-PRIVATE')
        s.add(payout_instruction)
        post_journal(s, tx.id, 'TEST_PAYOUT_SENT', payout_instruction.id,
                     [('SUPPLIER_PAYABLE', 'DEBIT', 1_000_000), ('PROVIDER_CLEARING', 'CREDIT', 1_000_000)])
        s.commit()
        return excess_instruction.id, dispute_instruction.id, payout_instruction.id
    finally:
        s.close()


def _moderator_headers():
    email = unique_email('settlement-mod')
    auth = register('Settlement Moderator', email)
    s = SessionLocal()
    try:
        user = s.scalar(select(User).where(User.email == email))
        user.role = 'SENIOR_MODERATOR'
        s.commit()
        return {'Authorization': 'Bearer ' + issue_token(s, user)}
    finally:
        s.close()


def test_supplier_cannot_retrieve_excess_refund_but_both_parties_see_dispute_refund():
    ctx, payment = _funded_context()
    excess_id, dispute_id, payout_id = _make_overpayment_and_dispute_refund(ctx, payment)

    supplier_tx = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['supplier'])
    assert supplier_tx.status_code == 200
    supplier_payload = json.dumps(supplier_tx.json())
    assert 'overpayment_kobo' not in supplier_payload
    assert 'overpayment_refund_status' not in supplier_payload
    assert 'excess_payment_refund_amount_kobo' not in supplier_payload
    assert excess_id not in supplier_payload
    assert 'provider_ref' not in supplier_payload and 'payment_id' not in supplier_payload
    supplier_payment_rows = client.get(f"/api/v1/transactions/{ctx['txid']}/payments", headers=ctx['supplier']).json()['data']
    assert supplier_payment_rows and 'payment_id' not in supplier_payment_rows[0] and 'provider_ref' not in supplier_payment_rows[0]

    supplier_refunds = client.get(f"/api/v1/transactions/{ctx['txid']}/refunds", headers=ctx['supplier'])
    assert supplier_refunds.status_code == 200
    assert [x['id'] for x in supplier_refunds.json()['data']] == [dispute_id]
    assert supplier_refunds.json()['data'][0]['refund_classification'] == 'DISPUTE_RETURN'

    buyer_tx = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()
    assert buyer_tx['settlement']['excess_payment_refund_amount_kobo'] == 250_000
    assert buyer_tx['settlement']['excess_payment_refund_status'] == 'PENDING'
    buyer_refunds = client.get(f"/api/v1/transactions/{ctx['txid']}/refunds", headers=ctx['buyer']).json()['data']
    assert {x['id'] for x in buyer_refunds} == {excess_id, dispute_id}

    # Direct ledger reads are also role-filtered; the supplier cannot infer the excess.
    supplier_ledger = client.get(f"/api/v1/transactions/{ctx['txid']}/ledger", headers=ctx['supplier']).json()['data']
    buyer_ledger = client.get(f"/api/v1/transactions/{ctx['txid']}/ledger", headers=ctx['buyer']).json()['data']
    assert not any(x['entry_type'] == 'OVERPAYMENT_RECEIVED' for x in supplier_ledger)
    assert any(x['entry_type'] == 'OVERPAYMENT_RECEIVED' and x['amount_kobo'] == 250_000 for x in buyer_ledger)
    assert not any(x['entry_type'] == 'TEST_PAYOUT_SENT' for x in buyer_ledger)
    assert any(x['entry_type'] == 'TEST_PAYOUT_SENT' for x in supplier_ledger)
    buyer_payouts = client.get(f"/api/v1/transactions/{ctx['txid']}/payouts", headers=ctx['buyer']).json()['data']
    supplier_payouts = client.get(f"/api/v1/transactions/{ctx['txid']}/payouts", headers=ctx['supplier']).json()['data']
    assert buyer_payouts and buyer_payouts[0]['type'] == 'PAYOUT' and 'amount_kobo' not in buyer_payouts[0]
    assert supplier_payouts and supplier_payouts[0]['amount_kobo'] == 1_000_000
    assert supplier_payouts[0]['provider_reference'] == 'PAYOUT-REF-PRIVATE'
    overpayment_rows = [x for x in buyer_ledger if x['entry_type'] == 'OVERPAYMENT_RECEIVED']
    assert {x['account_code'] for x in overpayment_rows} == {'PROVIDER_CLEARING', 'BUYER_REFUNDABLE'}
    assert all(x['account_code'] != 'SUPPLIER_PAYABLE' for x in overpayment_rows)

    moderator = _moderator_headers()
    mod_tx = client.get(f"/api/v1/mod/transactions/{ctx['txid']}/settlement", headers=moderator).json()
    assert mod_tx['settlement']['original_payment_amount_kobo'] == 1_250_000
    assert mod_tx['settlement']['payment_statuses'][0]['provider_reference'] == payment['provider_ref']
    assert any(x['id'] == payout_id for x in mod_tx['settlement']['payouts'])
    assert mod_tx['settlement']['agreed_amount_kobo'] == 1_000_000
    assert mod_tx['settlement']['excess_payment_refund_amount_kobo'] == 250_000
    assert {x['refund_classification'] for x in mod_tx['settlement']['refunds']} == {'EXCESS_PAYMENT', 'DISPUTE_RETURN'}


def test_overpayment_refund_creation_is_idempotent():
    ctx, payment = _funded_context()
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, ctx['txid']); p = s.get(Payment, payment['payment_id'])
        first = create_overpayment_refund_instruction(s, p, tx, 200_000)
        second = create_overpayment_refund_instruction(s, p, tx, 200_000)
        assert first.id == second.id
        rows = s.scalars(select(Instruction).where(Instruction.transaction_id == tx.id,
            Instruction.refund_classification == 'EXCESS_PAYMENT')).all()
        assert len(rows) == 1
    finally:
        s.close()


def _settlement_fixture(excess=200_000, payout_status='SUCCEEDED', refund_status='PENDING'):
    ctx, payment = _funded_context()
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, ctx['txid']); p = s.get(Payment, payment['payment_id'])
        d = tx_data(tx)
        if excess:
            d['payments'] = [{**x, 'checkout': {**x.get('checkout', {}), 'amount_received_kobo': p.amount_kobo + excess}}
                             if x.get('id') == p.id else x for x in d.get('payments', [])]
        for lot in d['lots']:
            lot.update({'item_status': 'SETTLED_RELEASED', 'money_status': 'RELEASED', 'is_frozen': False})
        save_tx(s, tx, d)
        payout = Instruction(id=str(uuid.uuid4()), transaction_id=tx.id, instruction_type='PAYOUT',
            amount_kobo=1_000_000, status=payout_status, origin_id=str(uuid.uuid4()),
            deciding_moderator_id=None, idempotency_key='test-payout:'+tx.id)
        s.add(payout)
        if excess:
            refund = Instruction(id=str(uuid.uuid4()), transaction_id=tx.id, instruction_type='REFUND',
                refund_classification='EXCESS_PAYMENT', amount_kobo=excess, status=refund_status,
                origin_id=p.id, deciding_moderator_id=None, idempotency_key='test-excess-refund:'+tx.id)
            s.add(refund)
        else: refund = None
        s.flush()
        reconcile_transaction_completion(s, tx, d)
        s.commit()
        return ctx, payout.id, refund.id if refund else None
    finally:
        s.close()


def _reconcile(ctx):
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, ctx['txid'])
        reconcile_transaction_completion(s, tx, tx_data(tx))
        s.commit()
        return tx.status
    finally:
        s.close()


def _set_instruction_status(iid, status):
    s = SessionLocal()
    try:
        ins = s.get(Instruction, iid); ins.status = status
        s.commit()
    finally:
        s.close()


def test_payout_success_with_excess_refund_pending_does_not_complete():
    ctx, payout_id, refund_id = _settlement_fixture(excess=200_000, payout_status='SUCCEEDED', refund_status='PENDING')
    assert _reconcile(ctx) == 'SETTLEMENT_PENDING'
    _set_instruction_status(refund_id, 'SUCCEEDED')
    assert _reconcile(ctx) == 'COMPLETED'


def test_excess_refund_success_with_payout_pending_does_not_complete():
    ctx, payout_id, refund_id = _settlement_fixture(excess=200_000, payout_status='QUEUED', refund_status='SUCCEEDED')
    assert _reconcile(ctx) == 'SETTLEMENT_PENDING'
    _set_instruction_status(payout_id, 'SUCCEEDED')
    assert _reconcile(ctx) == 'COMPLETED'


def test_no_excess_refund_is_not_required_for_completion():
    ctx, payout_id, refund_id = _settlement_fixture(excess=0, payout_status='SUCCEEDED')
    assert refund_id is None
    assert _reconcile(ctx) == 'COMPLETED'


def test_failed_or_unknown_settlement_never_completes():
    ctx, payout_id, refund_id = _settlement_fixture(excess=0, payout_status='FAILED')
    for state in ('FAILED', 'UNKNOWN', 'QUEUED', 'SUBMITTED'):
        _set_instruction_status(payout_id, state)
        assert _reconcile(ctx) == 'SETTLEMENT_PENDING'


def test_return_refund_requires_supplier_receipt_before_instruction_creation():
    ctx, payment = _funded_context()
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, ctx['txid']); d = tx_data(tx)
        lot = d['lots'][0]
        lot['item_status'] = 'REJECTED_AWAITING_RETURN'
        ret_id = str(uuid.uuid4())
        d['returns'] = [{'id': ret_id, 'status': 'AWAITING_HANDOVER_EVIDENCE',
                         'lines': [{'lot_id': lot['id'], 'item_id': lot['item_id'], 'quantity': lot['quantity']}]}]
        save_tx(s, tx, d); s.commit()
    finally:
        s.close()
    response = client.post(f'/api/v1/returns/{ret_id}/confirm-receipt', headers=ctx['supplier'], json={'lines': [], 'evidence_ids': []})
    assert response.status_code == 409
    s = SessionLocal()
    try:
        assert not s.scalars(select(Instruction).where(Instruction.transaction_id == ctx['txid'],
            Instruction.instruction_type == 'REFUND')).all()
    finally:
        s.close()


def test_moderator_financial_endpoint_is_role_protected():
    ctx, _ = _funded_context()
    ordinary_buyer = client.get(f"/api/v1/mod/transactions/{ctx['txid']}/settlement", headers=ctx['buyer'])
    assert ordinary_buyer.status_code == 403


def test_buyer_without_overpayment_gets_not_required_refund_state():
    ctx, _ = _funded_context()
    buyer_tx = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()
    assert buyer_tx['settlement']['payment_amount_kobo'] == 1_000_000
    assert buyer_tx['settlement']['expected_payment_amount_kobo'] == 1_000_000
    assert buyer_tx['settlement']['excess_payment_refund_amount_kobo'] == 0
    assert buyer_tx['settlement']['excess_payment_refund_status'] == 'NOT_REQUIRED'


def test_resolved_failed_instruction_does_not_block_a_successful_replacement_settlement():
    ctx, failed_payout_id, _ = _settlement_fixture(excess=0, payout_status='FAILED')
    s = SessionLocal()
    try:
        failed = s.get(Instruction, failed_payout_id)
        failed.resolved = True
        replacement = Instruction(id=str(uuid.uuid4()), transaction_id=ctx['txid'], instruction_type='PAYOUT',
            amount_kobo=1_000_000, status='SUCCEEDED', origin_id=str(uuid.uuid4()),
            deciding_moderator_id=None, idempotency_key='resolved-retry:'+ctx['txid'])
        s.add(replacement)
        tx = s.get(Aggregate, ctx['txid'])
        assert reconcile_transaction_completion(s, tx, tx_data(tx)) == 'COMPLETED'
        s.commit()
    finally:
        s.close()


def test_failed_instruction_retry_is_idempotent_and_reauthorizes_the_same_amount():
    ctx, failed_id, _ = _settlement_fixture(excess=0, payout_status='FAILED')
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, ctx['txid'])
        post_journal(s, tx.id, 'RELEASE_AUTHORIZED', failed_id,
                     [('HELD_FUNDS', 'DEBIT', 1_000_000), ('SUPPLIER_PAYABLE', 'CREDIT', 1_000_000)])
        s.commit()
    finally:
        s.close()
    senior = _moderator_headers()
    path = f'/api/v1/mod/financial-exceptions/{failed_id}/resolve'
    body = {'action': 'RETRY', 'reason': 'Provider confirmed the prior transfer failed and the reserved amount was restored.'}
    first = client.post(path, headers=senior, json=body)
    assert first.status_code == 200, first.text
    retry_id = first.json()['instruction']['id']
    assert first.json()['instruction']['retry_of_id'] == failed_id
    assert first.json()['instruction']['amount_kobo'] == 1_000_000
    assert first.json()['status'] == 'QUEUED'
    replay = client.post(path, headers=senior, json=body)
    assert replay.status_code == 200
    assert replay.json()['instruction']['id'] == retry_id
    assert replay.json()['idempotent_replay'] is True
    s = SessionLocal()
    try:
        old = s.get(Instruction, failed_id); new = s.get(Instruction, retry_id)
        assert old.resolved is True and new.retry_of_id == failed_id
        assert len(s.scalars(select(Instruction).where(Instruction.retry_of_id == failed_id)).all()) == 1
        journal_types = {x.entry_type for x in s.scalars(select(Journal).where(Journal.reference_id.in_([failed_id, retry_id]))).all()}
        assert 'REVERSAL' in journal_types and 'RELEASE_AUTHORIZED' in journal_types
        tx_status = s.get(Aggregate, ctx['txid']).status
        assert tx_status == 'SETTLEMENT_PENDING'
    finally:
        s.close()


def test_unknown_instruction_cannot_be_retried_before_provider_reconciliation():
    ctx, unknown_id, _ = _settlement_fixture(excess=0, payout_status='UNKNOWN')
    response = client.post(f'/api/v1/mod/financial-exceptions/{unknown_id}/resolve',
        headers=_moderator_headers(), json={'action': 'RETRY', 'reason': 'Attempt retry after a delayed provider status.'})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'OUTCOME_UNKNOWN_RECONCILE_FIRST'


def test_excess_refund_failure_must_be_retried_as_excess_and_keeps_its_classification():
    ctx, _, refund_id = _settlement_fixture(excess=200_000, payout_status='SUCCEEDED', refund_status='FAILED')
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, ctx['txid'])
        post_journal(s, tx.id, 'REFUND_AUTHORIZED', refund_id,
                     [('HELD_FUNDS', 'DEBIT', 200_000), ('BUYER_REFUNDABLE', 'CREDIT', 200_000)])
        s.commit()
    finally:
        s.close()
    senior = _moderator_headers()
    exceptions = client.get('/api/v1/mod/financial-exceptions', headers=senior).json()['data']
    exception = next(x for x in exceptions if x['id'] == refund_id)
    assert exception['allowed_actions'] == ['RETRY']
    path = f'/api/v1/mod/financial-exceptions/{refund_id}/resolve'
    release = client.post(path, headers=senior, json={'action': 'RELEASE', 'reason': 'Do not issue this refund.'})
    assert release.status_code == 422
    assert release.json()['error']['code'] == 'EXCESS_REFUND_RETRY_REQUIRED'
    retry = client.post(path, headers=senior, json={'action': 'RETRY', 'reason': 'Provider confirmed the failed excess refund and restored the funds.'})
    assert retry.status_code == 200
    assert retry.json()['instruction']['refund_classification'] == 'EXCESS_PAYMENT'
    s = SessionLocal()
    try:
        old = s.get(Instruction, refund_id)
        new = s.get(Instruction, retry.json()['instruction']['id'])
        assert old.resolved is True and new.refund_classification == 'EXCESS_PAYMENT'
        assert new.retry_of_id == old.id
    finally:
        s.close()


def test_late_provider_success_after_resolved_failure_and_retry_is_frozen_for_reconciliation(monkeypatch):
    import app.main as main_module
    ctx, old_id, _ = _settlement_fixture(excess=0, payout_status='FAILED')
    transfer_ref = 'SKOUT' + old_id.replace('-', '')
    restore_ref = 'SKBACK' + old_id.replace('-', '')
    event_id = str(uuid.uuid4())
    retry_id = str(uuid.uuid4())
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, ctx['txid']);d = tx_data(tx)
        d['lots'][0].update({'item_status':'ACCEPTED_CONFIRMED','money_status':'RELEASE_PENDING','is_frozen':False})
        save_tx(s, tx, d)
        old = s.get(Instruction, old_id)
        old.resolved = True;old.provider_reference = restore_ref;old.provider_stage = 'FUNDS_RESTORED'
        old.provider_response = {'transfer_reference':transfer_ref,'restored_balance_kobo':1_000_000}
        retry = Instruction(id=retry_id, transaction_id=tx.id, instruction_type='PAYOUT', amount_kobo=1_000_000,
            status='QUEUED', origin_id=old.origin_id, deciding_moderator_id=None,
            idempotency_key='late-callback-retry:'+tx.id, retry_of_id=old.id)
        s.add(retry)
        post_journal(s, tx.id, 'RELEASE_AUTHORIZED', retry.id,
                     [('HELD_FUNDS','DEBIT',1_000_000),('SUPPLIER_PAYABLE','CREDIT',1_000_000)])
        s.add(ProviderEvent(id=event_id, provider_name='PAGA_PAYOUT', provider_event_id='late-'+event_id,
            event_type='BANK_TRANSFER_SETTLED', signature_valid=True,
            raw_body={'responseCode':'0','referenceNumber':transfer_ref,
                      'transactionType':'USER_SEND_CASH_TO_BANK_ACCOUNT_SETTLED','reversalId':None}))
        s.commit()
    finally:
        s.close()
    monkeypatch.setenv('PAYMENT_PROVIDER','paga');monkeypatch.setenv('PAGA_MODE','sandbox')
    monkeypatch.setattr(main_module, 'PagaClient', lambda: object())
    s = SessionLocal()
    try:
        result = process_paga_payout_events(s)
        assert result['pending'] == 1
        old = s.get(Instruction, old_id);retry = s.get(Instruction, retry_id)
        assert old.status == 'UNKNOWN' and old.resolved is False
        assert old.provider_stage == 'CONFLICTING_LATE_SUCCESS'
        assert retry.status == 'CANCELLED' and retry.resolved is True
        assert not s.scalars(select(Journal).where(Journal.reference_id.in_([old_id,retry_id]), Journal.entry_type == 'PAYOUT_SENT')).all()
        assert s.get(ProviderEventProcessing, s.scalar(select(ProviderEventProcessing.id).where(ProviderEventProcessing.provider_event_id == event_id))).status == 'PROCESSED'
    finally:
        s.close()


def test_high_value_retry_stays_pending_approval_until_senior_approval():
    ctx, failed_id, _ = _settlement_fixture(excess=0, payout_status='FAILED')
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, ctx['txid']);d = tx_data(tx)
        d.setdefault('config_snapshot', {})['high_value_threshold_kobo'] = 1
        save_tx(s, tx, d)
        post_journal(s, tx.id, 'RELEASE_AUTHORIZED', failed_id,
                     [('HELD_FUNDS','DEBIT',1_000_000),('SUPPLIER_PAYABLE','CREDIT',1_000_000)])
        s.commit()
    finally:
        s.close()
    senior = _moderator_headers()
    retry_response = client.post(f'/api/v1/mod/financial-exceptions/{failed_id}/resolve', headers=senior,
        json={'action':'RETRY','reason':'The provider confirmed failure and the source funds were restored.'})
    assert retry_response.status_code == 200
    retry_id = retry_response.json()['instruction']['id']
    assert retry_response.json()['status'] == 'PENDING_APPROVAL'
    s = SessionLocal()
    try:
        assert not s.scalars(select(Journal).where(Journal.reference_id == retry_id, Journal.entry_type == 'RELEASE_AUTHORIZED')).all()
    finally:
        s.close()
    approved = client.post(f'/api/v1/mod/payouts/{retry_id}/approve', headers=senior, json={})
    assert approved.status_code == 200 and approved.json()['status'] == 'QUEUED'
    s = SessionLocal()
    try:
        assert s.scalars(select(Journal).where(Journal.reference_id == retry_id, Journal.entry_type == 'RELEASE_AUTHORIZED')).first()
    finally:
        s.close()


def test_late_callback_conflict_status_query_resolves_failure_without_second_fund_restoration(monkeypatch):
    import app.main as main_module
    from app.main import resolve_paga_instruction_statuses
    ctx, old_id, _ = _settlement_fixture(excess=0, payout_status='UNKNOWN')
    transfer_ref = 'SKOUT' + old_id.replace('-', '')
    retry_id = str(uuid.uuid4())
    s = SessionLocal()
    try:
        for other in s.scalars(select(Instruction).where(Instruction.status.in_(['SUBMITTED','UNKNOWN']),Instruction.id != old_id)).all():
            other.status='FAILED';other.resolved=True
        old = s.get(Instruction, old_id)
        old.status='UNKNOWN';old.resolved=False;old.provider_stage='CONFLICTING_LATE_SUCCESS';old.provider_reference=transfer_ref
        retry = Instruction(id=retry_id, transaction_id=ctx['txid'], instruction_type='PAYOUT', amount_kobo=1_000_000,
            status='CANCELLED', resolved=True, provider_stage='CANCELLED_AFTER_LATE_CALLBACK_CONFLICT',
            origin_id=old.origin_id, retry_of_id=old.id, idempotency_key='late-query-retry:'+ctx['txid'])
        s.add(retry);s.commit()
    finally:
        s.close()
    monkeypatch.setenv('PAYMENT_PROVIDER','paga')
    class FakePaga:
        def transaction_status(self, reference):
            assert reference == transfer_ref
            return {'responseCode':'101','transactionType':'USER_SEND_CASH_TO_BANK_ACCOUNT_FAILED'}
    monkeypatch.setattr(main_module, 'PagaClient', lambda: FakePaga())
    s = SessionLocal()
    try:
        result = resolve_paga_instruction_statuses(s)
        assert result['checked'] == 1
        old = s.get(Instruction, old_id);retry = s.get(Instruction, retry_id)
        assert old.status == 'FAILED' and old.resolved is True
        assert retry.status == 'FAILED' and retry.resolved is False
        assert retry.provider_stage == 'RETRY_CANCELLED_NOT_SUBMITTED'
    finally:
        s.close()
    # A fresh retry is chained to the definitively failed, never-submitted retry instruction.
    senior = _moderator_headers()
    retry_again = client.post(f'/api/v1/mod/financial-exceptions/{retry_id}/resolve', headers=senior,
        json={'action':'RETRY','reason':'The late event was reconciled as failed; safely retry the restored amount.'})
    assert retry_again.status_code == 200
    assert retry_again.json()['instruction']['retry_of_id'] == retry_id
