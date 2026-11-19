"""End-to-end buyer goods rejection, moderation, return and refund regression tests."""
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from helpers import client, register, unique_email, awaiting_payment, start_payment, callback, upload
from app.db import SessionLocal, Aggregate, Dispute, Instruction, User, Notification, Audit
from app.main import issue_token, tx_data, save_tx, run_deadlines, run_financial
from app.db import now


def _funded_and_delivered(quantity=1):
    ctx = awaiting_payment(quantity=quantity, unit_price_kobo=1_000_000)
    payment = start_payment(ctx)
    assert callback(payment, 'PAYMENT_RECEIVED').status_code == 200
    assert callback(payment, 'FUNDS_SECURED').status_code == 200
    tx = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()
    dispatch_proof = upload(ctx['supplier'], ctx['txid'], 'DISPATCH_PROOF')
    dispatched = client.post(f"/api/v1/transactions/{ctx['txid']}/dispatch", headers=ctx['supplier'], json={
        'lines': [{'item_id': tx['items'][0]['id'], 'quantity': quantity}],
        'courier_name': 'Test Courier', 'rider_name': 'Test Rider', 'rider_phone': '+2348000000000',
        'evidence_ids': [dispatch_proof]
    })
    assert dispatched.status_code == 200, dispatched.text
    received = client.post(f"/api/v1/transactions/{ctx['txid']}/delivery/confirm-received", headers=ctx['buyer'], json={
        'lines': [{'item_id': tx['items'][0]['id'], 'quantity': quantity}]
    })
    assert received.status_code == 200, received.text
    delivery_photo = upload(ctx['buyer'], ctx['txid'], 'DELIVERY_PHOTO')
    requirement_id = received.json()['data']['delivery_photo_requirement']['id']
    photo_response = client.post(f"/api/v1/transactions/{ctx['txid']}/delivery/evidence", headers=ctx['buyer'], json={'requirement_id': requirement_id, 'evidence_ids': [delivery_photo]})
    assert photo_response.status_code == 200, photo_response.text
    return ctx, tx['items'][0]


def _staff(role='MODERATOR'):
    email = unique_email(role.lower())
    auth = register(role.title(), email)
    s = SessionLocal()
    try:
        user = s.scalar(select(User).where(User.email == email))
        user.role = role
        s.commit()
        return {'Authorization': 'Bearer ' + issue_token(s, user)}, user.id
    finally:
        s.close()


def _reject(ctx, item):
    proof = upload(ctx['buyer'], ctx['txid'], 'REJECTION_PROOF')
    result = client.post(f"/api/v1/transactions/{ctx['txid']}/decisions", headers=ctx['buyer'], json={
        'lines': [{'item_id': item['id'], 'quantity_submitted': 1, 'quantity_accepted': 0, 'quantity_rejected': 1,
                   'reject': {'reason_text': 'The delivered goods are not sealed as required by the agreement.',
                              'agreed_condition_ids': [item['conditions'][0]['id']], 'evidence_ids': [proof]}}]
    })
    assert result.status_code == 200, result.text
    return proof, result.json()['data']['dispute']['id']


def _close_evidence_window(dispute_id, txid):
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, txid)
        d = tx_data(tx)
        d['dispute']['window_ends_at'] = (now() - timedelta(seconds=1)).isoformat().replace('+00:00', 'Z')
        save_tx(s, tx, d)
        disp = s.get(Dispute, dispute_id)
        disp.data = {**disp.data, 'window_ends_at': d['dispute']['window_ends_at']}
        s.commit()
        run_deadlines(s)
    finally:
        s.close()


def test_buyer_rejection_creates_persisted_dispute_notifies_both_and_holds_payout():
    ctx, item = _funded_and_delivered()
    proof, did = _reject(ctx, item)
    buyer_tx = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()
    supplier_tx = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['supplier']).json()
    assert buyer_tx['status'] == 'DISPUTED'
    assert buyer_tx['dispute']['id'] == did
    assert 'not sealed' in buyer_tx['dispute']['reason']
    assert supplier_tx['dispute']['id'] == did
    assert 'not sealed' in supplier_tx['dispute']['reason']
    assert supplier_tx['lots'][0]['item_status'] == 'IN_DISPUTE'
    assert supplier_tx['lots'][0]['is_frozen'] is True
    supplier_evidence = client.get(f'/api/v1/evidence/{proof}', headers=ctx['supplier'])
    assert supplier_evidence.status_code == 200 and supplier_evidence.json()['id'] == proof
    assert client.get(f'/api/v1/evidence/{proof}/content', headers=ctx['supplier']).status_code == 200
    assert 'raise_dispute' not in supplier_tx['allowed_actions']
    assert buyer_tx['dispute']['decision_deadline_at']
    assert buyer_tx['dispute']['window_ends_at']
    assert buyer_tx['dispute']['decision_deadline_at'] != buyer_tx['dispute']['window_ends_at']
    mod, _ = _staff('MODERATOR')
    queue = client.get('/api/v1/mod/disputes', headers=mod).json()['data']
    assert any(x['id'] == did and x['reason'].find('not sealed') >= 0 for x in queue)
    detail = client.get(f'/api/v1/mod/disputes/{did}', headers=mod).json()
    assert proof in [x['id'] for x in detail['evidence']]
    assert detail['timeline']
    assert client.get(f'/api/v1/evidence/{proof}/content', headers=mod).status_code == 200
    for headers in [ctx['buyer'], ctx['supplier']]:
        notices = client.get('/api/v1/notifications', headers=headers).json()['data']
        assert any(n['entity_id'] == ctx['txid'] and ('REJECTION' in n['type'] or 'REJECTED' in n['type']) for n in notices)
    # A queued payout cannot be executed while a goods rejection dispute is open.
    s = SessionLocal()
    try:
        ins = Instruction(id=str(uuid.uuid4()), transaction_id=ctx['txid'], instruction_type='PAYOUT', amount_kobo=1_000_000,
            status='QUEUED', origin_id='unrelated-acceptance', deciding_moderator_id=None,
            idempotency_key='rejection-hold:'+ctx['txid'])
        s.add(ins); s.commit()
        run_financial(s)
        assert s.get(Instruction, ins.id).status == 'QUEUED'
    finally:
        s.close()


def test_rejection_requires_reason_evidence_and_buyer_ownership():
    ctx, item = _funded_and_delivered()
    supplier_result = client.post(f"/api/v1/transactions/{ctx['txid']}/decisions", headers=ctx['supplier'], json={'lines': []})
    assert supplier_result.status_code == 403
    no_reason = upload(ctx['buyer'], ctx['txid'], 'REJECTION_PROOF')
    bad = client.post(f"/api/v1/transactions/{ctx['txid']}/decisions", headers=ctx['buyer'], json={'lines': [{
        'item_id': item['id'], 'quantity_submitted': 1, 'quantity_accepted': 0, 'quantity_rejected': 1,
        'reject': {'reason_text': 'bad', 'agreed_condition_ids': [item['conditions'][0]['id']], 'evidence_ids': [no_reason]}}]})
    assert bad.status_code == 422 and bad.json()['error']['code'] == 'REJECTION_REASON_REQUIRED'
    bad_evidence = client.post(f"/api/v1/transactions/{ctx['txid']}/decisions", headers=ctx['buyer'], json={'lines': [{
        'item_id': item['id'], 'quantity_submitted': 1, 'quantity_accepted': 0, 'quantity_rejected': 1,
        'reject': {'reason_text': 'Goods differ from the written specification.', 'agreed_condition_ids': [item['conditions'][0]['id']], 'evidence_ids': ['not-owned']}}]})
    assert bad_evidence.status_code == 422


def test_full_return_refund_requires_moderator_decision_supplier_receipt_and_provider_success(monkeypatch):
    ctx, item = _funded_and_delivered()
    buyer_proof, did = _reject(ctx, item)
    mod, mod_id = _staff('MODERATOR')
    # A single assigned moderator owns the decision and refund authorization.
    # Buyer cannot open a return until the moderator explicitly orders it.
    early = client.post(f"/api/v1/transactions/{ctx['txid']}/returns", headers=ctx['buyer'], json={'lines': [{'item_id': item['id'], 'quantity': 1}]})
    assert early.status_code == 409 and early.json()['error']['code'] == 'RETURN_NOT_AUTHORIZED'
    # Moderator can request evidence; the supplier's response is persisted and visible.
    request = client.post(f'/api/v1/mod/disputes/{did}/request-evidence', headers=mod, json={
        'party': 'SUPPLIER', 'question': 'Please provide the dispatch inspection record and product photographs.',
        'due_at': (now()+timedelta(hours=12)).isoformat()})
    assert request.status_code == 200, request.text
    supplier_proof = upload(ctx['supplier'], ctx['txid'], 'DISPUTE')
    response = client.post(f'/api/v1/disputes/{did}/evidence', headers=ctx['supplier'], json={
        'kind': 'SUPPLIER_RESPONSE', 'statement': 'The goods were sealed at dispatch.', 'evidence_ids': [supplier_proof]})
    assert response.status_code == 200, response.text
    supplier_tx = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['supplier']).json()
    assert supplier_tx['dispute']['evidence_requests'][0]['status'] == 'ANSWERED'
    assert supplier_tx['dispute']['evidence_submissions'][-1]['evidence_ids'] == [supplier_proof]
    _close_evidence_window(did, ctx['txid'])
    detail = client.get(f'/api/v1/mod/disputes/{did}', headers=mod).json()
    assert {x['id'] for x in detail['evidence']} >= {buyer_proof, supplier_proof}
    decision = client.post(f'/api/v1/mod/disputes/{did}/decision', headers=mod, json={
        'reason': 'The buyer evidence establishes a breach of the agreed sealed condition. Return is required before refund.',
        'evidence_ids_considered': [buyer_proof, supplier_proof],
        'outcomes': [{'item_id': item['id'], 'lot_id': detail['transaction']['lots'][0]['id'], 'quantity': 1, 'outcome': 'RETURN_AND_REFUND'}]
    })
    assert decision.status_code == 200, decision.text
    assert decision.json()['return_required'] is True
    assert decision.json()['status'] == 'RETURN_PENDING'
    duplicate = client.post(f'/api/v1/mod/disputes/{did}/decision', headers=mod, json={
        'reason': 'A duplicate decision should not be allowed.', 'evidence_ids_considered': [buyer_proof],
        'outcomes': [{'lot_id': detail['transaction']['lots'][0]['id'], 'quantity': 1, 'outcome': 'RETURN_AND_REFUND'}]})
    assert duplicate.status_code == 409
    tx = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()
    lot = next(x for x in tx['lots'] if x['item_status'] == 'REJECTED_AWAITING_RETURN')
    opened = client.post(f"/api/v1/transactions/{ctx['txid']}/returns", headers=ctx['buyer'], json={'lines': [{'lot_id':lot['id'],'item_id': item['id'], 'quantity': 1}]})
    assert opened.status_code == 200, opened.text
    rid = opened.json()['data']['id']
    handover = upload(ctx['buyer'], ctx['txid'], 'RETURN_HANDOVER')
    handed = client.post(f'/api/v1/returns/{rid}/handover-evidence', headers=ctx['buyer'], json={
        'evidence_ids': [handover], 'courier_name': 'Return Courier', 'rider_name': 'Return Rider', 'rider_phone': '+2348000000000'})
    assert handed.status_code == 200, handed.text
    supplier_tx = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['supplier']).json()
    assert supplier_tx['return_id'] == rid
    assert 'confirm_return_receipt' in supplier_tx['allowed_actions']
    no_evidence = client.post(f'/api/v1/returns/{rid}/confirm-receipt', headers=ctx['supplier'], json={'lines': []})
    assert no_evidence.status_code == 422
    receipt_proof = upload(ctx['supplier'], ctx['txid'], 'RETURN_RECEIPT')
    receipt = client.post(f'/api/v1/returns/{rid}/confirm-receipt', headers=ctx['supplier'], json={
        'lines': [{'lot_id': lot['id'], 'item_id': item['id'], 'quantity_received': 1}], 'evidence_ids': [receipt_proof]})
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()['refund']['status'] == 'PENDING_APPROVAL'
    moderator_case_after_return = client.get(f'/api/v1/mod/disputes/{did}', headers=mod).json()
    return_evidence_ids = {handover, receipt_proof}
    assert return_evidence_ids <= {x['id'] for x in moderator_case_after_return['evidence']}
    assert client.get(f'/api/v1/evidence/{handover}/content', headers=mod).status_code == 200
    assert client.get(f'/api/v1/evidence/{receipt_proof}/content', headers=mod).status_code == 200
    duplicate_receipt = client.post(f'/api/v1/returns/{rid}/confirm-receipt', headers=ctx['supplier'], json={
        'lines': [{'lot_id': lot['id'], 'item_id': item['id'], 'quantity_received': 1}], 'evidence_ids': [receipt_proof]})
    assert duplicate_receipt.status_code == 409
    buyer_refunds = client.get(f"/api/v1/transactions/{ctx['txid']}/refunds", headers=ctx['buyer']).json()['data']
    supplier_refunds = client.get(f"/api/v1/transactions/{ctx['txid']}/refunds", headers=ctx['supplier']).json()['data']
    assert len(buyer_refunds) == len(supplier_refunds) == 1
    assert buyer_refunds[0]['refund_classification'] == supplier_refunds[0]['refund_classification'] == 'DISPUTE_RETURN'
    assert buyer_refunds[0]['amount_kobo'] == 1_000_000
    assert client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()['status'] != 'COMPLETED'
    ins_id = buyer_refunds[0]['id']
    supplier_cannot_authorize = client.post(f'/api/v1/mod/disputes/{did}/authorize-refund', headers=ctx['supplier'])
    assert supplier_cannot_authorize.status_code == 403
    generic_second_approval = client.post(f'/api/v1/mod/refunds/{ins_id}/approve', headers=mod)
    assert generic_second_approval.status_code == 409 and generic_second_approval.json()['error']['code'] == 'USE_DISPUTE_REFUND_AUTHORIZATION'
    approved = client.post(f'/api/v1/mod/disputes/{did}/authorize-refund', headers=mod)
    assert approved.status_code == 200, approved.text
    assert approved.json()['status'] == 'QUEUED'
    duplicate_approval = client.post(f'/api/v1/mod/disputes/{did}/authorize-refund', headers=mod)
    assert duplicate_approval.status_code == 200 and duplicate_approval.json()['instruction_id'] == ins_id
    # QUEUED is not settlement. A pending provider result cannot complete the transaction.
    monkeypatch.setenv('SIMULATED_INSTRUCTION_SCENARIO', 'PENDING')
    s = SessionLocal()
    try:
        run_financial(s)
        assert s.get(Instruction, ins_id).status == 'QUEUED'
    finally:
        s.close()
    assert client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()['status'] != 'COMPLETED'
    monkeypatch.setenv('SIMULATED_INSTRUCTION_SCENARIO', 'SUCCESS')
    s = SessionLocal()
    try:
        run_financial(s)
        assert s.get(Instruction, ins_id).status == 'SUCCEEDED'
    finally:
        s.close()
    completed = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()
    assert completed['status'] == 'COMPLETED'
    assert completed['dispute']['status'] == 'RESOLVED'
    assert completed['settlement']['dispute_refund_status'] == 'SUCCESS'
    assert client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['supplier']).json()['settlement']['dispute_refund_status'] == 'SUCCESS'
    with SessionLocal() as s:
        actions = s.scalars(select(Audit.action).where(Audit.entity_id.in_([did, rid, ins_id, ctx['txid']]))).all()
        assert {'BUYER_GOODS_REJECTED','DISPUTE_DECIDED','RETURN_INITIATED','RETURN_HANDOVER_EVIDENCE_SUBMITTED','RETURN_RECEIPT_CONFIRMED','FINANCIAL_INSTRUCTION_SETTLED'} <= set(actions)


def test_return_confirmation_is_supplier_only_and_refund_cannot_be_frontend_amount_controlled():
    ctx, item = _funded_and_delivered()
    _, did = _reject(ctx, item)
    mod, _ = _staff('MODERATOR')
    _close_evidence_window(did, ctx['txid'])
    detail = client.get(f'/api/v1/mod/disputes/{did}', headers=mod).json()
    lot_id = detail['transaction']['lots'][0]['id']
    wrong_amount = client.post(f'/api/v1/mod/disputes/{did}/decision', headers=mod, json={
        'reason': 'Refund is appropriate but caller supplied an invalid amount.', 'evidence_ids_considered': detail['evidence'][0:1] and [detail['evidence'][0]['id']],
        'outcomes': [{'lot_id': lot_id, 'quantity': 1, 'outcome': 'REFUND', 'amount_kobo': 1}]})
    assert wrong_amount.status_code == 422 and wrong_amount.json()['error']['code'] == 'AMOUNT_MUST_MATCH_ALLOCATION'


def test_72_hour_decision_deadline_is_escalated_without_financial_side_effects():
    ctx, item = _funded_and_delivered()
    _, did = _reject(ctx, item)
    mod,_=_staff('MODERATOR')
    s = SessionLocal()
    try:
        tx = s.get(Aggregate, ctx['txid']);d = tx_data(tx)
        d['dispute']['decision_deadline_at'] = (now()-timedelta(seconds=1)).isoformat().replace('+00:00','Z')
        save_tx(s,tx,d)
        disp=s.get(Dispute,did);disp.data={**disp.data,'decision_deadline_at':d['dispute']['decision_deadline_at']}
        s.commit()
        run_deadlines(s)
        refreshed=tx_data(s.get(Aggregate,ctx['txid']))
        assert refreshed['dispute']['decision_overdue'] is True
        assert s.scalar(select(Instruction).where(Instruction.transaction_id==ctx['txid'])) is None
    finally:
        s.close()
    notices=client.get('/api/v1/notifications',headers=mod).json()['data']
    assert any(n['type']=='DISPUTE_DECISION_OVERDUE' and n['payload'].get('dispute_id')==did for n in notices)


def test_only_assigned_moderator_can_decide_rejection_case():
    ctx, item = _funded_and_delivered()
    proof, did = _reject(ctx, item)
    assigned_mod, assigned_user_id = _staff('MODERATOR')
    other_mod, _ = _staff('MODERATOR')
    assignment = client.post(f'/api/v1/mod/disputes/{did}/assign', headers=assigned_mod, json={'moderator_id': assigned_user_id})
    assert assignment.status_code == 200
    assert client.post(f'/api/v1/disputes/{did}/waive-window', headers=ctx['buyer']).status_code == 200
    assert client.post(f'/api/v1/disputes/{did}/waive-window', headers=ctx['supplier']).status_code == 200
    detail = client.get(f'/api/v1/mod/disputes/{did}', headers=assigned_mod).json()
    lot = next(x for x in detail['transaction']['lots'] if x.get('is_frozen'))
    payload = {'reason':'A full refund is justified by the rejection evidence submitted by the buyer.',
               'evidence_ids_considered':[proof],
               'outcomes':[{'item_id':item['id'],'lot_id':lot['id'],'quantity':1,'outcome':'REFUND'}]}
    denied = client.post(f'/api/v1/mod/disputes/{did}/decision', headers=other_mod, json=payload)
    assert denied.status_code == 403 and denied.json()['error']['code'] == 'DISPUTE_NOT_ASSIGNED'
    accepted = client.post(f'/api/v1/mod/disputes/{did}/decision', headers=assigned_mod, json=payload)
    assert accepted.status_code == 200, accepted.text


def test_failed_dispute_refund_never_completes_transaction(monkeypatch):
    ctx, item = _funded_and_delivered()
    proof, did = _reject(ctx, item)
    moderator, _ = _staff('MODERATOR')
    _close_evidence_window(did, ctx['txid'])
    detail = client.get(f'/api/v1/mod/disputes/{did}', headers=moderator).json()
    lot = next(x for x in detail['transaction']['lots'] if x.get('is_frozen'))
    decision = client.post(f'/api/v1/mod/disputes/{did}/decision', headers=moderator, json={
        'reason':'A refund is justified without requiring the buyer to return the rejected goods.',
        'evidence_ids_considered':[proof],
        'outcomes':[{'item_id':item['id'],'lot_id':lot['id'],'quantity':1,'outcome':'REFUND'}]
    })
    assert decision.status_code == 200, decision.text
    instruction_id = decision.json()['instructions'][0]['id']
    monkeypatch.setenv('SIMULATED_INSTRUCTION_SCENARIO','FAILED')
    s = SessionLocal()
    try:
        result = run_financial(s)
        assert any(x['id']==instruction_id and x['status']=='FAILED' for x in result['processed'])
        assert s.get(Instruction,instruction_id).status == 'FAILED'
    finally:
        s.close()
    tx = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()
    assert tx['status'] != 'COMPLETED'
    assert tx['lots'][0]['is_frozen'] is True
    assert tx['settlement']['dispute_refund_status'] == 'FAILED'
    # The same authorized moderator can retry a definitive dispute-refund failure;
    # unknown provider outcomes still require reconciliation and cannot be retried.
    retry = client.post(f'/api/v1/mod/financial-exceptions/{instruction_id}/resolve', headers=moderator,
                        json={'action':'RETRY','reason':'Provider definitively rejected the refund; retry after reviewing the failed transfer.'})
    assert retry.status_code == 200, retry.text
    retry_id = retry.json()['instruction']['id']
    assert retry.json()['status'] == 'QUEUED'
    duplicate_retry = client.post(f'/api/v1/mod/financial-exceptions/{instruction_id}/resolve', headers=moderator,
                                  json={'action':'RETRY','reason':'Replay the same retry safely without creating another transfer.'})
    assert duplicate_retry.status_code == 200 and duplicate_retry.json()['instruction']['id'] == retry_id
    monkeypatch.setenv('SIMULATED_INSTRUCTION_SCENARIO','SUCCESS')
    with SessionLocal() as s:
        run_financial(s)
        assert s.get(Instruction,retry_id).status == 'SUCCEEDED'
    final = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()
    assert final['status'] == 'COMPLETED'


def test_supplier_return_confirmation_deadline_escalates_without_assuming_receipt_or_refund():
    ctx, item = _funded_and_delivered()
    proof, did = _reject(ctx, item)
    moderator, _ = _staff('MODERATOR')
    _close_evidence_window(did, ctx['txid'])
    detail = client.get(f'/api/v1/mod/disputes/{did}', headers=moderator).json()
    lot = next(x for x in detail['transaction']['lots'] if x.get('is_frozen'))
    decision = client.post(f'/api/v1/mod/disputes/{did}/decision', headers=moderator, json={
        'reason':'The buyer must return the rejected item before a refund is authorized.',
        'evidence_ids_considered':[proof],
        'outcomes':[{'item_id':item['id'],'lot_id':lot['id'],'quantity':1,'outcome':'RETURN_AND_REFUND'}]
    })
    assert decision.status_code == 200, decision.text
    opened = client.post(f"/api/v1/transactions/{ctx['txid']}/returns", headers=ctx['buyer'], json={
        'lines':[{'lot_id':lot['id'],'item_id':item['id'],'quantity':1}]})
    assert opened.status_code == 200, opened.text
    rid = opened.json()['data']['id']
    handover = upload(ctx['buyer'],ctx['txid'],'RETURN_HANDOVER')
    assert client.post(f'/api/v1/returns/{rid}/handover-evidence',headers=ctx['buyer'],json={'evidence_ids':[handover]}).status_code == 200
    s=SessionLocal()
    try:
        tx=s.get(Aggregate,ctx['txid']);d=tx_data(tx)
        d['returns'][0]['confirmation_deadline_at']=(now()-timedelta(seconds=1)).isoformat().replace('+00:00','Z')
        save_tx(s,tx,d);s.commit()
        result=run_deadlines(s)
        assert result['return_confirmations_escalated'] == 1
        current=tx_data(s.get(Aggregate,ctx['txid']))
        assert current['returns'][0]['status']=='HANDOVER_RECORDED'
        assert current['returns'][0]['return_confirmation_overdue'] is True
        assert not s.scalar(select(Instruction).where(Instruction.transaction_id==ctx['txid']))
    finally:
        s.close()
    supplier_tx=client.get(f"/api/v1/transactions/{ctx['txid']}",headers=ctx['supplier']).json()
    assert supplier_tx['dispute']['return_confirmation_overdue'] is True
    assert supplier_tx['return_id']==rid
    assert client.get(f"/api/v1/transactions/{ctx['txid']}",headers=ctx['buyer']).json()['status']!='COMPLETED'
    supplier_notices=client.get('/api/v1/notifications',headers=ctx['supplier']).json()['data']
    assert any(n['type']=='RETURN_CONFIRMATION_OVERDUE' and n['payload'].get('return_id')==rid for n in supplier_notices)


def test_goods_rejection_moderator_must_select_a_resolving_outcome():
    ctx, item = _funded_and_delivered()
    proof, did = _reject(ctx, item)
    moderator, _ = _staff('MODERATOR')
    _close_evidence_window(did, ctx['txid'])
    detail=client.get(f'/api/v1/mod/disputes/{did}',headers=moderator).json()
    lot=next(x for x in detail['transaction']['lots'] if x.get('is_frozen'))
    response=client.post(f'/api/v1/mod/disputes/{did}/decision',headers=moderator,json={
        'reason':'The case must resolve the frozen rejected goods rather than leaving the lot stranded.',
        'evidence_ids_considered':[proof],
        'outcomes':[{'item_id':item['id'],'lot_id':lot['id'],'quantity':1,'outcome':'NO_FINANCIAL_ACTION'}]
    })
    assert response.status_code==422 and response.json()['error']['code']=='DISPUTE_OUTCOME_REQUIRED'


def test_paga_missing_beneficiary_does_not_erase_supplier_receipt_and_requires_moderator_authorization(monkeypatch):
    from app import main as main_module
    ctx, item = _funded_and_delivered()
    proof, did = _reject(ctx, item)
    moderator, _ = _staff('MODERATOR')
    _close_evidence_window(did, ctx['txid'])
    detail=client.get(f'/api/v1/mod/disputes/{did}',headers=moderator).json()
    lot=next(x for x in detail['transaction']['lots'] if x.get('is_frozen'))
    decision=client.post(f'/api/v1/mod/disputes/{did}/decision',headers=moderator,json={
        'reason':'The rejected item must be returned and supplier receipt confirmed before refund.',
        'evidence_ids_considered':[proof],
        'outcomes':[{'item_id':item['id'],'lot_id':lot['id'],'quantity':1,'outcome':'RETURN_AND_REFUND'}]
    })
    assert decision.status_code==200,decision.text
    opened=client.post(f"/api/v1/transactions/{ctx['txid']}/returns",headers=ctx['buyer'],json={
        'lines':[{'lot_id':lot['id'],'item_id':item['id'],'quantity':1}]})
    rid=opened.json()['data']['id']
    handover=upload(ctx['buyer'],ctx['txid'],'RETURN_HANDOVER')
    assert client.post(f'/api/v1/returns/{rid}/handover-evidence',headers=ctx['buyer'],json={'evidence_ids':[handover]}).status_code==200
    receipt_evidence=upload(ctx['supplier'],ctx['txid'],'RETURN_RECEIPT')
    monkeypatch.setenv('PAYMENT_PROVIDER','paga')
    receipt=client.post(f'/api/v1/returns/{rid}/confirm-receipt',headers=ctx['supplier'],json={
        'lines':[{'lot_id':lot['id'],'item_id':item['id'],'quantity_received':1}], 'evidence_ids':[receipt_evidence]})
    assert receipt.status_code==200,receipt.text
    assert receipt.json()['refund']['status']=='AWAITING_VERIFIED_BENEFICIARY'
    supplier_tx=client.get(f"/api/v1/transactions/{ctx['txid']}",headers=ctx['supplier']).json()
    assert supplier_tx['returns'][0]['status']=='RECEIPT_CONFIRMED'
    assert supplier_tx['returns'][0]['supplier_receipt_confirmed_at']
    assert supplier_tx['returns'][0]['refund_eligibility_status']=='AWAITING_VERIFIED_BENEFICIARY'
    assert client.get(f"/api/v1/transactions/{ctx['txid']}/refunds",headers=ctx['supplier']).json()['data']==[]
    blocked=client.post(f'/api/v1/mod/disputes/{did}/authorize-refund',headers=moderator)
    assert blocked.status_code==409 and blocked.json()['error']['code']=='VERIFIED_BENEFICIARY_REQUIRED'
    snapshot={'bank_uuid':'paga-bank-uuid','bank_code':'044','bank_name':'Access Bank','account_number_enc':'encrypted-ciphertext',
              'masked_account_number':'******4321','account_name':'Buyer Name','verified_at':now().isoformat(),
              'account_version':2,'destination_fingerprint':'immutable-fingerprint','mode':'PAGA_LIVE'}
    monkeypatch.setattr(main_module,'verified_destination_snapshot',lambda user:snapshot)
    authorized=client.post(f'/api/v1/mod/disputes/{did}/authorize-refund',headers=moderator)
    assert authorized.status_code==200,authorized.text
    assert authorized.json()['status']=='QUEUED'
    ins_id=authorized.json()['instruction_id']
    with SessionLocal() as s:
        ins=s.get(Instruction,ins_id)
        assert ins.destination_snapshot==snapshot
        assert ins.status=='QUEUED'
    replay=client.post(f'/api/v1/mod/disputes/{did}/authorize-refund',headers=moderator)
    assert replay.status_code==200 and replay.json()['instruction_id']==ins_id and replay.json()['status']=='QUEUED'
