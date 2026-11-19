"""Functional moderator evidence and case-action regression tests."""
import hashlib
import os
import tempfile
from pathlib import Path

os.environ['EVIDENCE_STORAGE_DIR'] = tempfile.mkdtemp(prefix='supplykey-moderator-evidence-')

from sqlalchemy import select
from app.db import SessionLocal, User, Evidence, Audit, Aggregate, Dispute
from app.main import issue_token
from tests.helpers import client, register, unique_email, awaiting_payment, start_payment, callback, upload


def moderator():
    email = unique_email('moderator')
    auth = register('Case Moderator', email)
    s = SessionLocal()
    try:
        user = s.scalar(select(User).where(User.email == email))
        user.role = 'MODERATOR'
        s.commit()
        token = issue_token(s, user)
    finally:
        s.close()
    return {'Authorization': 'Bearer ' + token}


def senior_moderator():
    email = unique_email('senior-moderator')
    auth = register('Senior Case Moderator', email)
    s = SessionLocal()
    try:
        user = s.scalar(select(User).where(User.email == email))
        user.role = 'SENIOR_MODERATOR'
        s.commit()
        token = issue_token(s, user)
    finally:
        s.close()
    return {'Authorization': 'Bearer ' + token}


def funded_dispatch_receipt_acceptance():
    ctx = awaiting_payment(quantity=1, unit_price_kobo=1_000_000)
    pay = start_payment(ctx)
    assert callback(pay, 'PAYMENT_RECEIVED').status_code == 200
    assert callback(pay, 'FUNDS_SECURED').status_code == 200
    tx = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()
    item_id = tx['items'][0]['id']

    dispatch_proof = upload(ctx['supplier'], ctx['txid'], 'DISPATCH_PROOF')
    dispatched = client.post(f"/api/v1/transactions/{ctx['txid']}/dispatch", headers=ctx['supplier'], json={
        'lines': [{'item_id': item_id, 'quantity': 1}], 'courier_name': 'Test Courier',
        'rider_name': 'Test Rider', 'rider_phone': '+2348000000000', 'evidence_ids': [dispatch_proof]
    })
    assert dispatched.status_code == 200, dispatched.text
    supplier_photo = upload(ctx['supplier'], ctx['txid'], 'DELIVERY_PHOTO')
    supplier_req = dispatched.json()['data']['delivery_photo_requirement']
    assert client.post(f"/api/v1/transactions/{ctx['txid']}/delivery/evidence", headers=ctx['supplier'], json={
        'requirement_id': supplier_req['id'], 'evidence_ids': [supplier_photo]
    }).status_code == 200

    receipt = client.post(f"/api/v1/transactions/{ctx['txid']}/delivery/confirm-received", headers=ctx['buyer'], json={
        'lines': [{'item_id': item_id, 'quantity': 1}]
    })
    assert receipt.status_code == 200, receipt.text
    buyer_photo = upload(ctx['buyer'], ctx['txid'], 'DELIVERY_PHOTO')
    buyer_req = receipt.json()['data']['delivery_photo_requirement']
    assert client.post(f"/api/v1/transactions/{ctx['txid']}/delivery/evidence", headers=ctx['buyer'], json={
        'requirement_id': buyer_req['id'], 'evidence_ids': [buyer_photo]
    }).status_code == 200
    acceptance_proof = upload(ctx['buyer'], ctx['txid'], 'ACCEPTANCE_PROOF')
    # Exercise the PDF path independently from the image path.
    pdf_bytes = b'%PDF-1.4\n% SupplyKey evidence test\n%%EOF'
    pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
    pdf_upload = client.post(f"/api/v1/evidence/upload-urls", headers=ctx['buyer'], json={'transaction_id': ctx['txid'], 'purpose': 'ACCEPTANCE_PROOF', 'files': [{'filename': 'acceptance.pdf', 'mime_type': 'application/pdf', 'size_bytes': len(pdf_bytes), 'sha256': pdf_hash}]})
    assert pdf_upload.status_code == 200, pdf_upload.text
    pdf_meta = pdf_upload.json()['uploads'][0]
    assert client.put(pdf_meta['upload_url'], content=pdf_bytes, headers={'Content-Type': 'application/pdf'}).status_code == 204
    assert client.post(f"/api/v1/evidence/{pdf_meta['evidence_id']}/complete", headers=ctx['buyer']).status_code == 200
    pdf_evidence_id = pdf_meta['evidence_id']
    submitted = client.post(f"/api/v1/transactions/{ctx['txid']}/decisions", headers=ctx['buyer'], json={
        'lines': [{'item_id': item_id, 'quantity_submitted': 1, 'quantity_accepted': 1,
                   'quantity_rejected': 0, 'accept_evidence_ids': [acceptance_proof, pdf_evidence_id]}]
    })
    assert submitted.status_code == 200, submitted.text
    decision_id = submitted.json()['data']['decisions'][0]['id']
    assert client.post(f'/api/v1/decisions/{decision_id}/waive-window', headers=ctx['buyer']).status_code == 200
    assert client.post(f'/api/v1/decisions/{decision_id}/waive-window', headers=ctx['supplier']).status_code == 200
    return ctx, decision_id, {'dispatch': dispatch_proof, 'supplier_photo': supplier_photo,
                              'buyer_photo': buyer_photo, 'acceptance': acceptance_proof, 'pdf': pdf_evidence_id}


def test_moderator_case_returns_real_evidence_and_protected_file_content():
    ctx, decision_id, evidence_ids = funded_dispatch_receipt_acceptance()
    mod = moderator()
    queue = client.get('/api/v1/mod/acceptance-queue', headers=mod)
    assert queue.status_code == 200, queue.text
    case = next(row for row in queue.json()['data'] if row['id'] == decision_id)
    assert case['buyer_photos'] == 'SUBMITTED'
    assert case['supplier_photos'] == 'SUBMITTED'
    assert case['item'] and case['buyer'] and case['supplier']

    detail = client.get(f'/api/v1/decisions/{decision_id}', headers=mod)
    assert detail.status_code == 200, detail.text
    ids = {row['id'] for row in detail.json()['evidence']}
    assert set(evidence_ids.values()).issubset(ids)
    metadata = client.get(f"/api/v1/evidence/{evidence_ids['acceptance']}", headers=mod)
    assert metadata.status_code == 200 and metadata.json()['view_url'].endswith('/content')
    content = client.get(f"/api/v1/evidence/{evidence_ids['acceptance']}/content", headers=mod)
    assert content.status_code == 200, content.text
    assert content.headers['content-type'].startswith('image/jpeg')
    assert content.headers['content-disposition'].startswith('inline;')
    assert content.headers['cache-control'] == 'private, no-store'
    assert content.content.startswith(b'\xff\xd8\xff')
    pdf_content = client.get(f"/api/v1/evidence/{evidence_ids['pdf']}/content", headers=mod)
    assert pdf_content.status_code == 200 and pdf_content.headers['content-type'].startswith('application/pdf')
    assert pdf_content.content.startswith(b'%PDF-')

    outsider = register('Unrelated Account', unique_email('outsider'))
    denied = client.get(f"/api/v1/evidence/{evidence_ids['acceptance']}/content", headers=outsider)
    assert denied.status_code == 404


def test_missing_private_file_returns_controlled_error():
    ctx, decision_id, evidence_ids = funded_dispatch_receipt_acceptance()
    mod = moderator()
    s = SessionLocal()
    try:
        ev = s.get(Evidence, evidence_ids['acceptance'])
        path = Path(os.environ['EVIDENCE_STORAGE_DIR']) / ev.object_path
    finally:
        s.close()
    path.unlink()
    response = client.get(f"/api/v1/evidence/{evidence_ids['acceptance']}/content", headers=mod)
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'EVIDENCE_FILE_MISSING'
    assert 'private file is missing' in response.json()['error']['message']


def test_moderator_acceptance_action_records_audit_and_removes_queue_item():
    ctx, decision_id, evidence_ids = funded_dispatch_receipt_acceptance()
    mod = moderator()
    detail = client.get(f'/api/v1/decisions/{decision_id}', headers=mod).json()
    response = client.post(f'/api/v1/mod/decisions/{decision_id}/confirm', headers={**mod, 'If-Match': str(detail['version'])}, json={
        'reason': 'Reviewed the uploaded photographs and verified the agreed acceptance conditions.',
        'evidence_ids_considered': [evidence_ids['acceptance'], evidence_ids['buyer_photo']]
    })
    assert response.status_code == 200, response.text
    assert response.json()['data']['review_status'] == 'CONFIRMED'
    queue = client.get('/api/v1/mod/acceptance-queue', headers=mod).json()['data']
    assert all(row['id'] != decision_id for row in queue)
    s = SessionLocal()
    try:
        event = s.scalar(select(Audit).where(Audit.action == 'ACCEPTANCE_CONFIRMED', Audit.entity_id == decision_id))
        assert event and event.actor_id
        tx = s.get(Aggregate, ctx['txid'])
        decision = next(x for x in tx.data['decisions'] if x['id'] == decision_id)
        assert decision['review_status'] == 'CONFIRMED'
        accepted_lot = next(x for x in tx.data['lots'] if x['id'] == decision['lot_id'])
        assert accepted_lot['item_status'] == 'ACCEPTED_CONFIRMED'
        assert accepted_lot['money_status'] == 'RELEASE_PENDING'
    finally:
        s.close()


def test_dispute_moderator_actions_and_audit_trail():
    ctx = awaiting_payment(quantity=1, unit_price_kobo=1_000_000)
    pay = start_payment(ctx)
    assert callback(pay, 'PAYMENT_RECEIVED').status_code == 200
    assert callback(pay, 'FUNDS_SECURED').status_code == 200
    tx = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()
    item_id = tx['items'][0]['id']; lot_id = tx['lots'][0]['id']
    dispute_proof = upload(ctx['buyer'], ctx['txid'], 'DISPUTE')
    opened = client.post(f"/api/v1/transactions/{ctx['txid']}/disputes", headers=ctx['buyer'], json={
        'reason_code': 'SPEC_DISAGREEMENT', 'statement': 'The supplied goods do not match the agreed specifications.',
        'lines': [{'item_id': item_id, 'quantity': 1}]
    })
    assert opened.status_code == 200, opened.text
    did = opened.json()['data']['id']
    assert client.post(f'/api/v1/disputes/{did}/evidence', headers=ctx['buyer'], json={
        'evidence_ids': [dispute_proof], 'kind': 'STATEMENT', 'statement': 'Photographic evidence of the discrepancy.'
    }).status_code == 200

    mod = moderator()
    queue = client.get('/api/v1/mod/disputes', headers=mod)
    assert queue.status_code == 200, queue.text
    row = next(x for x in queue.json()['data'] if x['id'] == did)
    assert row['item_id'] == item_id and row['lot_id'] == lot_id and row['quantity'] == 1
    detail = client.get(f'/api/v1/mod/disputes/{did}', headers=mod)
    assert detail.status_code == 200 and dispute_proof in {x['id'] for x in detail.json()['evidence']}
    content = client.get(f'/api/v1/evidence/{dispute_proof}/content', headers=mod)
    assert content.status_code == 200 and content.content.startswith(b'\xff\xd8\xff')
    def moderator_post(action, body):
        latest = client.get(f'/api/v1/mod/disputes/{did}', headers=mod)
        assert latest.status_code == 200, latest.text
        return client.post(f'/api/v1/mod/disputes/{did}/{action}', headers={**mod, 'If-Match': str(latest.json()['version'])}, json=body)

    requested = moderator_post('request-evidence', {
        'party': 'BUYER', 'question': 'Please provide a clearer photograph of the item label.', 'due_at': '2026-11-20T12:00:00Z'
    })
    assert requested.status_code == 200, requested.text
    # The evidence submission closes the request for the party that was asked.
    extra = upload(ctx['buyer'], ctx['txid'], 'DISPUTE')
    assert client.post(f'/api/v1/disputes/{did}/evidence', headers=ctx['buyer'], json={'evidence_ids': [extra], 'kind': 'DOCUMENT'}).status_code == 200
    assert moderator_post('extend-window', {'extra_hours': 12, 'reason': 'Additional time is needed to inspect the newly submitted evidence.'}).status_code == 200
    assert moderator_post('notes', {'note': 'Reviewed the evidence received from the buyer.'}).status_code == 200
    assert moderator_post('assign', {}).status_code == 200
    assert moderator_post('hold', {'reason': 'Pause review while the evidence is checked.'}).status_code == 200
    assert moderator_post('resume', {'reason': 'Evidence check is complete; review can continue.'}).status_code == 200

    refreshed_detail = client.get(f'/api/v1/mod/disputes/{did}', headers=mod).json()
    assert refreshed_detail['notes'] and refreshed_detail['dispute']['window_extended']
    assert client.post(f'/api/v1/disputes/{did}/waive-window', headers=ctx['buyer']).status_code == 200
    assert client.post(f'/api/v1/disputes/{did}/waive-window', headers=ctx['supplier']).status_code == 200
    latest = client.get(f'/api/v1/mod/disputes/{did}', headers=mod).json()
    decided = client.post(f'/api/v1/mod/disputes/{did}/decision', headers={**mod, 'If-Match': str(latest['version'])}, json={
        'reason': 'The evidence was reviewed; no financial adjustment is justified on this record.',
        'evidence_ids_considered': [dispute_proof, extra],
        'outcomes': [{'item_id': item_id, 'lot_id': lot_id, 'quantity': 1, 'outcome': 'NO_FINANCIAL_ACTION'}]
    })
    assert decided.status_code == 200, decided.text
    assert decided.json()['status'] == 'DECIDED' and decided.json()['instructions'] == []

    s = SessionLocal()
    try:
        actions = set(s.scalars(select(Audit.action).where(Audit.entity_type == 'DISPUTE', Audit.entity_id == did)).all())
        assert {'DISPUTE_EVIDENCE_REQUESTED', 'DISPUTE_EVIDENCE_WINDOW_EXTENDED', 'DISPUTE_INTERNAL_NOTE_ADDED',
                'DISPUTE_ASSIGNED', 'DISPUTE_HELD', 'DISPUTE_RESUMED', 'DISPUTE_DECIDED'}.issubset(actions)
    finally:
        s.close()


def test_moderator_only_case_routes_reject_non_moderators():
    ctx, decision_id, evidence_ids = funded_dispatch_receipt_acceptance()
    buyer_detail = client.get(f'/api/v1/decisions/{decision_id}', headers=ctx['buyer'])
    assert buyer_detail.status_code == 200
    # The participant can read their own case detail, but cannot call moderator-only actions.
    action = client.post(f'/api/v1/mod/decisions/{decision_id}/confirm', headers=ctx['buyer'], json={
        'reason': 'Attempted unauthorized confirmation.', 'evidence_ids_considered': [evidence_ids['acceptance']]
    })
    assert action.status_code == 403


def test_moderator_can_request_evidence_and_extend_acceptance_review_window():
    ctx, decision_id, _ = funded_dispatch_receipt_acceptance()
    mod = moderator()
    detail = client.get(f'/api/v1/decisions/{decision_id}', headers=mod).json()
    request = client.post(f'/api/v1/mod/decisions/{decision_id}/request-evidence', headers={**mod, 'If-Match': str(detail['version'])}, json={
        'party': 'BUYER', 'question': 'Please provide a clearer photograph of the serial label.', 'due_at': '2026-11-21T12:00:00Z'
    })
    assert request.status_code == 200, request.text
    latest = client.get(f'/api/v1/decisions/{decision_id}', headers=mod).json()
    assert latest['decision']['review_status'] == 'EVIDENCE_REQUESTED'
    assert latest['evidence_requests'][0]['party'] == 'BUYER'

    # Use a second acceptance case because requesting evidence changes its review state.
    ctx2, decision_id2, _ = funded_dispatch_receipt_acceptance()
    detail2 = client.get(f'/api/v1/decisions/{decision_id2}', headers=mod).json()
    extended = client.post(f'/api/v1/mod/decisions/{decision_id2}/extend-window', headers={**mod, 'If-Match': str(detail2['version'])}, json={
        'reason': 'The moderator needs additional time to inspect the evidence.', 'extra_hours': 12
    })
    assert extended.status_code == 200, extended.text
    assert extended.json()['window_status'] == 'OPEN'
    s = SessionLocal()
    try:
        actions = set(s.scalars(select(Audit.action).where(Audit.entity_type == 'DECISION', Audit.entity_id.in_([decision_id, decision_id2]))).all())
        assert {'ACCEPTANCE_EVIDENCE_REQUESTED', 'ACCEPTANCE_EVIDENCE_WINDOW_EXTENDED'}.issubset(actions)
    finally:
        s.close()


def test_moderator_can_open_dispute_from_acceptance_case_without_financial_side_effects():
    ctx, decision_id, _ = funded_dispatch_receipt_acceptance()
    mod = moderator()
    detail = client.get(f'/api/v1/decisions/{decision_id}', headers=mod).json()
    opened = client.post(f'/api/v1/mod/decisions/{decision_id}/open-dispute', headers={**mod, 'If-Match': str(detail['version'])}, json={
        'reason_code': 'OTHER', 'reason': 'The evidence requires a separate dispute investigation.'
    })
    assert opened.status_code == 200, opened.text
    did = opened.json()['dispute_id']
    queue = client.get('/api/v1/mod/disputes', headers=mod).json()['data']
    case = next(row for row in queue if row['id'] == did)
    assert case['item_id'] and case['lot_id'] and case['quantity'] == 1
    s = SessionLocal()
    try:
        dispute = s.get(Dispute, did)
        assert dispute and dispute.status == 'EVIDENCE_WINDOW_OPEN'
        assert s.scalar(select(Audit).where(Audit.action == 'DISPUTE_OPENED_BY_MODERATOR', Audit.entity_id == decision_id))
    finally:
        s.close()


def test_acceptance_payout_cannot_exceed_agreed_transaction_entitlement():
    from app.db import Instruction
    from app.main import tx_data, save_tx

    ctx, decision_id, evidence_ids = funded_dispatch_receipt_acceptance()
    session = SessionLocal()
    try:
        tx = session.get(Aggregate, ctx['txid'])
        data = tx_data(tx)
        data['lots'][0]['allocation_kobo'] = int(data['goods_total_kobo']) + 1
        save_tx(session, tx, data)
        session.commit()
    finally:
        session.close()

    mod = moderator()
    detail = client.get(f'/api/v1/decisions/{decision_id}', headers=mod).json()
    response = client.post(f'/api/v1/mod/decisions/{decision_id}/confirm', headers={**mod, 'If-Match': str(detail['version'])}, json={
        'reason': 'The acceptance evidence was inspected; verify that the resulting payout stays within the agreed amount.',
        'evidence_ids_considered': [evidence_ids['acceptance'], evidence_ids['buyer_photo']]
    })
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'PAYOUT_EXCEEDS_AGREED_ENTITLEMENT'
    session = SessionLocal()
    try:
        assert not session.scalars(select(Instruction).where(Instruction.transaction_id == ctx['txid'], Instruction.instruction_type == 'PAYOUT')).all()
    finally:
        session.close()


def test_declining_financial_instruction_reopens_review_without_moving_money():
    from app.db import Instruction, Journal
    from app.main import tx_data, save_tx

    ctx, decision_id, evidence_ids = funded_dispatch_receipt_acceptance()
    session = SessionLocal()
    try:
        tx = session.get(Aggregate, ctx['txid'])
        data = tx_data(tx)
        data['config_snapshot']['high_value_threshold_kobo'] = 1
        save_tx(session, tx, data)
        session.commit()
    finally:
        session.close()

    mod = moderator(); senior = senior_moderator()
    detail = client.get(f'/api/v1/decisions/{decision_id}', headers=mod).json()
    confirmed = client.post(f'/api/v1/mod/decisions/{decision_id}/confirm', headers={**mod, 'If-Match': str(detail['version'])}, json={
        'reason': 'Acceptance evidence was checked and the accepted quantity is supported.',
        'evidence_ids_considered': [evidence_ids['acceptance'], evidence_ids['buyer_photo']]
    })
    assert confirmed.status_code == 200, confirmed.text
    payout = confirmed.json()['data']['payout']
    assert payout['status'] == 'PENDING_APPROVAL'
    declined = client.post(f"/api/v1/mod/payouts/{payout['id']}/decline", headers=senior, json={
        'reason': 'The payout destination requires additional reconciliation before approval.'
    })
    assert declined.status_code == 200, declined.text
    assert declined.json()['status'] == 'CANCELLED'
    latest = client.get(f"/api/v1/transactions/{ctx['txid']}", headers=ctx['buyer']).json()
    assert latest['status'] != 'COMPLETED'
    assert latest['dispute']['status'] == 'EVIDENCE_WINDOW_OPEN'
    assert latest['lots'][0]['item_status'] == 'IN_DISPUTE'
    session = SessionLocal()
    try:
        instruction = session.get(Instruction, payout['id'])
        assert instruction.status == 'CANCELLED' and instruction.resolved is True
        assert not session.scalars(select(Journal).where(Journal.reference_id == payout['id'])).all()
    finally:
        session.close()
