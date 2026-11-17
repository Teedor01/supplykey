"""Reusable API-level helpers for the integrity / simulator / SLA test modules."""
import hashlib, hmac, itertools, json, uuid
from fastapi.testclient import TestClient
from app.main import app, SECRET, _rate_windows
from app.db import SessionLocal, Payment, Aggregate, init_db

init_db()
client = TestClient(app)
_counter = itertools.count(1)


def register(name, email):
    _rate_windows.clear()
    r = client.post('/api/v1/auth/register', json={'display_name': name, 'email': email, 'password': 'StrongPassword123!', 'accepted_terms': True})
    assert r.status_code == 201, r.text
    r = client.post('/api/v1/auth/verify-otp', json={'contact': email, 'otp': '000000'})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['access_token']}


def unique_email(prefix):
    return f'{prefix}-{uuid.uuid4().hex[:10]}@helpers.supplykey.test'


def upload(headers, txid, purpose, content=b'\xff\xd8\xff' + b'photo-bytes'):
    digest = hashlib.sha256(content).hexdigest()
    r = client.post('/api/v1/evidence/upload-urls', headers=headers, json={'transaction_id': txid, 'purpose': purpose, 'files': [{'filename': 'proof.jpg', 'mime_type': 'image/jpeg', 'size_bytes': len(content), 'sha256': digest}]})
    assert r.status_code == 200, r.text
    item = r.json()['uploads'][0]
    r = client.put(item['upload_url'], content=content, headers={'Content-Type': 'image/jpeg'})
    assert r.status_code == 204, r.text
    r = client.post('/api/v1/evidence/' + item['evidence_id'] + '/complete', headers=headers)
    assert r.status_code == 200, r.text
    return item['evidence_id']


def awaiting_payment(quantity=2, unit_price_kobo=1000000):
    """Create supplier+buyer, agree terms, and return a transaction ready for payment."""
    n = next(_counter)
    sup_email, buy_email = unique_email('sup'), unique_email('buy')
    supplier = register(f'Supplier {n}', sup_email)
    buyer = register(f'Buyer {n}', buy_email)
    body = {'buyer_contact': {'email': buy_email}, 'delivery': {'destination_state': 'Rivers', 'city': 'Port Harcourt', 'expected_within_days': 3, 'delivery_fee_kobo': 0},
            'items': [{'description': 'Test item', 'specification': 'sealed', 'condition': 'new', 'quantity': quantity, 'unit_price_kobo': unit_price_kobo, 'conditions': [{'code': 'SEALED', 'kind': 'REJECTION', 'text': 'Must be sealed'}]}]}
    r = client.post('/api/v1/transactions', headers=supplier, json=body); assert r.status_code == 201, r.text
    txid = r.json()['data']['id']
    r = client.post(f'/api/v1/transactions/{txid}/send', headers=supplier); assert r.status_code == 200, r.text
    token = r.json()['invitation_token']
    assert client.post(f'/api/v1/invitations/{token}/accept', headers=buyer).status_code == 200
    agreement = client.get(f'/api/v1/transactions/{txid}/agreement', headers=buyer).json()['current_version']
    r = client.post(f'/api/v1/transactions/{txid}/agreement/confirm', headers=buyer, json={'agreement_version_id': agreement['id'], 'content_hash': agreement['content_hash']}); assert r.status_code == 200, r.text
    return {'supplier': supplier, 'buyer': buyer, 'txid': txid, 'token': token}


def start_payment(ctx, key=None):
    _rate_windows.clear()
    r = client.post(f"/api/v1/transactions/{ctx['txid']}/payments", headers={**ctx['buyer'], 'Idempotency-Key': key or uuid.uuid4().hex}, json={})
    assert r.status_code == 200, r.text
    pay = r.json()['data']
    s = SessionLocal()
    try:
        p = s.get(Payment, pay['payment_id'])
        info = {'payment_id': p.id, 'provider_ref': p.provider_ref, 'amount_kobo': p.amount_kobo}
        info['tx_reference'] = s.get(Aggregate, ctx['txid']).reference
    finally:
        s.close()
    return {**ctx, **info}


def pending_payment(**kw):
    return start_payment(awaiting_payment(**kw))


def sign(payload: dict):
    raw = json.dumps(payload, separators=(',', ':')).encode()
    return raw, hmac.new(SECRET, raw, hashlib.sha256).hexdigest()


def callback(pay, status, event_id=None, **overrides):
    """Send a correctly signed simulated-provider callback. Overrides replace/remove fields (None removes)."""
    payload = {'event_id': event_id or uuid.uuid4().hex, 'provider_ref': pay['provider_ref'], 'status': status,
               'amount_kobo': pay['amount_kobo'], 'currency': 'NGN', 'transaction_reference': pay['tx_reference']}
    for k, v in overrides.items():
        if v is None:
            payload.pop(k, None)
        else:
            payload[k] = v
    raw, sig = sign(payload)
    return client.post('/api/v1/webhooks/payments/simulated', content=raw, headers={'X-Sim-Signature': sig, 'Content-Type': 'application/json'})


def states(pay):
    s = SessionLocal()
    try:
        return s.get(Payment, pay['payment_id']).status, s.get(Aggregate, pay['txid']).status
    finally:
        s.close()


def journals(pay):
    from app.db import Journal
    s = SessionLocal()
    try:
        from sqlalchemy import select
        return sorted(j.entry_type for j in s.scalars(select(Journal).where(Journal.transaction_id == pay['txid'])).all())
    finally:
        s.close()
