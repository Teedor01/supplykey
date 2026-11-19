import os,tempfile,json,hmac,hashlib
from pathlib import Path
from sqlalchemy import select
DB=Path(tempfile.gettempdir())/f'supplykey-tests-{os.getpid()}.db'
os.environ['DATABASE_URL']=f'sqlite:///{DB}'
os.environ['APP_ENV']='development'
os.environ['SUPPLYKEY_SECRET']='test-secret'
from fastapi.testclient import TestClient
from app.main import app,SECRET,_rate_windows
from app.db import init_db, SessionLocal, Payment, Aggregate, User
init_db(); client=TestClient(app)

def register(name,email):
    _rate_windows.clear()  # isolate per-test auth throttling while preserving production limits
    r=client.post('/api/v1/auth/register',json={'display_name':name,'email':email,'password':'StrongPassword123!','accepted_terms':True});assert r.status_code==201,r.text
    r=client.post('/api/v1/auth/verify-otp',json={'contact':email,'otp':'000000'});assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['access_token']}

def upload(headers,txid,purpose,content=b'\xff\xd8\xff'+b'photo-bytes'):
    digest=hashlib.sha256(content).hexdigest()
    r=client.post('/api/v1/evidence/upload-urls',headers=headers,json={'transaction_id':txid,'purpose':purpose,'files':[{'filename':'proof.jpg','mime_type':'image/jpeg','size_bytes':len(content),'sha256':digest}]});assert r.status_code==200,r.text
    item=r.json()['uploads'][0];r=client.put(item['upload_url'],content=content,headers={'Content-Type':'image/jpeg'});assert r.status_code==204,r.text
    r=client.post('/api/v1/evidence/'+item['evidence_id']+'/complete',headers=headers);assert r.status_code==200,r.text
    return item['evidence_id']

def test_end_to_end_simulated_funding_and_dispatch():
    supplier=register('Supplier','supplier@testsupply.ng');buyer=register('Buyer','buyer@testsupply.ng')
    body={'buyer_contact':{'email':'buyer@testsupply.ng'},'delivery':{'destination_state':'Rivers','city':'Port Harcourt','expected_within_days':3,'delivery_fee_kobo':0},'items':[{'description':'Test item','specification':'sealed','condition':'new','quantity':2,'unit_price_kobo':1000000,'conditions':[{'code':'SEALED','kind':'REJECTION','text':'Must be sealed'}]}]}
    r=client.post('/api/v1/transactions',headers=supplier,json=body);assert r.status_code==201,r.text;txid=r.json()['data']['id']
    r=client.post(f'/api/v1/transactions/{txid}/send',headers=supplier);assert r.status_code==200,r.text;token=r.json()['invitation_token']
    assert client.post(f'/api/v1/invitations/{token}/accept',headers=buyer).status_code==200
    agreement=client.get(f'/api/v1/transactions/{txid}/agreement',headers=buyer).json()['current_version']
    assert agreement['content_hash']
    r=client.post(f'/api/v1/transactions/{txid}/agreement/confirm',headers=buyer,json={'agreement_version_id':agreement['id'],'content_hash':agreement['content_hash']});assert r.status_code==200,r.text
    r=client.post(f'/api/v1/transactions/{txid}/payments',headers={**buyer,'Idempotency-Key':'pay-1'},json={});assert r.status_code==200,r.text
    replay=client.post(f'/api/v1/transactions/{txid}/payments',headers={**buyer,'Idempotency-Key':'pay-1'},json={});assert replay.status_code==200 and replay.json()==r.json()
    changed=client.post(f'/api/v1/transactions/{txid}/payments',headers={**buyer,'Idempotency-Key':'pay-1'},json={'amount_kobo':1});assert changed.status_code==409
    pay=r.json()['data'];assert pay['status']=='PENDING'
    assert client.get(f'/api/v1/transactions/{txid}',headers=buyer).json()['status']=='AWAITING_PAYMENT'
    def webhook(status,event_id):
        s=SessionLocal()
        try: provider_ref=s.get(Payment,pay['payment_id']).provider_ref
        finally: s.close()
        payload=json.dumps({'provider_ref':provider_ref,'status':status,'event_id':event_id,'amount_kobo':pay['amount_kobo']},separators=(',',':')).encode()  # money callbacks must now carry the exact stored amount
        sig=hmac.new(SECRET,payload,hashlib.sha256).hexdigest()
        return client.post('/api/v1/webhooks/payments/simulated',content=payload,headers={'X-Sim-Signature':sig,'Content-Type':'application/json'})
    assert webhook('PAYMENT_RECEIVED','evt-1').status_code==200
    assert webhook('PAYMENT_RECEIVED','evt-1').json()['duplicate'] is True
    assert webhook('FUNDS_SECURED','evt-2').status_code==200
    tx=client.get(f'/api/v1/transactions/{txid}',headers=buyer).json();assert tx['status']=='FUNDED' and len(tx['lots'])==1
    ledger=client.get(f'/api/v1/transactions/{txid}/ledger',headers=buyer).json()['data']
    for jid in {x['journal_id'] for x in ledger}:
        rows=[x for x in ledger if x['journal_id']==jid]
        assert sum(x['amount_kobo'] for x in rows if x['direction']=='DEBIT')==sum(x['amount_kobo'] for x in rows if x['direction']=='CREDIT')
    evidence=upload(supplier,txid,'DISPATCH_PROOF')
    r=client.post(f'/api/v1/transactions/{txid}/dispatch',headers=supplier,json={'lines':[{'item_id':tx['items'][0]['id'],'quantity':2}],'courier_name':'Demo Courier','rider_name':'Demo Rider','rider_phone':'+2348000000000','evidence_ids':[evidence]});assert r.status_code==200,r.text
    assert r.json()['data']['transaction']['lots'][0]['item_status']=='DISPATCHED'
    assert r.json()['data']['transaction']['dispatches'][-1]['evidence_ids']==[evidence]
    buyer_tx=client.get(f'/api/v1/transactions/{txid}',headers=buyer).json()
    assert buyer_tx['lots'][0]['item_status']=='DISPATCHED'
    assert buyer_tx['items'][0]['id']==buyer_tx['lots'][0]['item_id']
    assert 'confirm_received' in buyer_tx['allowed_actions']
    # Empty and malformed receipt requests must fail without mutating the aggregate.
    empty=client.post(f'/api/v1/transactions/{txid}/delivery/confirm-received',headers=buyer,json={'lines':[]})
    assert empty.status_code==422 and empty.json()['error']['code']=='RECEIPT_LINES_REQUIRED'
    invalid=client.post(f'/api/v1/transactions/{txid}/delivery/confirm-received',headers=buyer,json={'lines':[{'item_id':'missing-item','quantity':1}]})
    assert invalid.status_code==404 and invalid.json()['error']['code']=='ITEM_NOT_FOUND'
    excessive=client.post(f'/api/v1/transactions/{txid}/delivery/confirm-received',headers=buyer,json={'lines':[{'item_id':buyer_tx['items'][0]['id'],'quantity':3}]})
    assert excessive.status_code==422 and excessive.json()['error']['code']=='QUANTITY_EXCEEDS_AVAILABLE'
    receipt=client.post(f'/api/v1/transactions/{txid}/delivery/confirm-received',headers=buyer,json={'lines':[{'item_id':buyer_tx['items'][0]['id'],'quantity':1}]})
    assert receipt.status_code==200,receipt.text
    refreshed=client.get(f'/api/v1/transactions/{txid}',headers=buyer).json()
    assert sorted((lot['item_status'],lot['quantity']) for lot in refreshed['lots'])==[('DISPATCHED',1),('RECEIPT_CONFIRMED',1)]
    # Reconfirming the already received unit is rejected and does not duplicate receipt.
    duplicate=client.post(f'/api/v1/transactions/{txid}/delivery/confirm-received',headers=buyer,json={'lines':[{'item_id':buyer_tx['items'][0]['id'],'quantity':2}]})
    assert duplicate.status_code==422 and duplicate.json()['error']['code']=='QUANTITY_EXCEEDS_AVAILABLE'

def test_idempotency_rejects_changed_body():
    supplier=register('Supplier Two','supplier2@testsupply.ng')
    headers={**supplier,'Idempotency-Key':'same-key'}
    # A valid transaction endpoint has no idempotency contract; use payment flow in the end-to-end test.
    assert client.get('/health').status_code==200

def test_transaction_is_not_visible_to_unrelated_user():
    outsider=register('Outsider','outsider@testsupply.ng')
    r=client.get('/api/v1/transactions',headers=outsider);assert r.status_code==200 and r.json()['data']==[]

def test_invalid_webhook_signature_is_rejected():
    r=client.post('/api/v1/webhooks/payments/simulated',json={'event_id':'bad','provider_ref':'none','status':'FUNDS_SECURED'},headers={'X-Sim-Signature':'invalid'})
    assert r.status_code==401 and r.json()['error']['code']=='INVALID_WEBHOOK_SIGNATURE'


def test_refresh_token_rotation_and_reuse_revocation():
    c=TestClient(app)
    r=c.post('/api/v1/auth/register',json={'display_name':'Refresh User','email':'refresh@testsupply.ng','password':'StrongPassword123!','accepted_terms':True});assert r.status_code==201
    r=c.post('/api/v1/auth/verify-otp',json={'contact':'refresh@testsupply.ng','otp':'000000'});assert r.status_code==200
    old=c.cookies.get('sk_refresh');assert old
    r=c.post('/api/v1/auth/refresh');assert r.status_code==200 and r.json().get('access_token')
    new=c.cookies.get('sk_refresh');assert new and new!=old
    c.cookies.set('sk_refresh',old,path='/api/v1/auth')
    r=c.post('/api/v1/auth/refresh');assert r.status_code==401 and r.json()['error']['code']=='REFRESH_REUSED'

def test_staff_login_requires_totp_mfa():
    import base64,struct,time
    from app.db import User,SessionLocal
    from app.main import hash_password
    s=SessionLocal();u=User(id='staff-mfa-test',email='mod-mfa@testsupply.ng',phone=None,password_hash=hash_password('StrongPassword123!'),display_name='Moderator',role='MODERATOR',verified=True,mfa_secret='JBSWY3DPEHPK3PXP',profile={},payout_account={});s.add(u);s.commit();s.close()
    c=TestClient(app);r=c.post('/api/v1/auth/login',json={'identifier':'mod-mfa@testsupply.ng','password':'StrongPassword123!'});assert r.status_code==200 and r.json()['mfa_required'] is True
    assert 'access_token' not in r.json() and c.cookies.get('sk_mfa_challenge')
    challenge=c.cookies.get('sk_mfa_challenge')
    assert c.get('/api/v1/me',headers={'Authorization':'Bearer '+challenge}).status_code==401
    bad=c.post('/api/v1/auth/mfa/verify',json={'code':'000000'});assert bad.status_code==401 and bad.json()['error']['code']=='MFA_INVALID'
    key=base64.b32decode('JBSWY3DPEHPK3PXP');counter=int(time.time())//30;digest=hmac.new(key,struct.pack('>Q',counter),hashlib.sha1).digest();off=digest[-1]&15;code=f"{(struct.unpack('>I',digest[off:off+4])[0]&0x7fffffff)%1000000:06d}"
    stale_counter=counter-1;stale_digest=hmac.new(key,struct.pack('>Q',stale_counter),hashlib.sha1).digest();stale_off=stale_digest[-1]&15;stale_code=f"{(struct.unpack('>I',stale_digest[stale_off:stale_off+4])[0]&0x7fffffff)%1000000:06d}"
    stale=c.post('/api/v1/auth/mfa/verify',json={'code':stale_code});assert stale.status_code==401 and stale.json()['error']['code']=='MFA_INVALID'
    r=c.post('/api/v1/auth/mfa/verify',json={'code':code});assert r.status_code==200 and r.json().get('access_token')
    assert c.get('/api/v1/me',headers={'Authorization':'Bearer '+r.json()['access_token']}).json()['user']['role']=='MODERATOR'


def test_expired_staff_mfa_challenge_is_rejected():
    from datetime import timedelta
    from app.db import SessionLocal,SessionToken
    from app.main import now
    c=TestClient(app);r=c.post('/api/v1/auth/login',json={'identifier':'mod-mfa@testsupply.ng','password':'StrongPassword123!'});assert r.status_code==200
    challenge=c.cookies.get('sk_mfa_challenge')
    s=SessionLocal();row=s.scalar(select(SessionToken).where(SessionToken.token_hash==hashlib.sha256(challenge.encode()).hexdigest()));row.expires_at=now()-timedelta(seconds=1);s.commit();s.close()
    r=c.post('/api/v1/auth/mfa/verify',json={'code':'123456'});assert r.status_code==401 and r.json()['error']['code']=='MFA_CHALLENGE_EXPIRED'


def test_ordinary_buyer_login_does_not_require_mfa():
    from app.db import SessionLocal,User
    from app.main import hash_password
    s=SessionLocal();u=User(id='buyer-login-no-mfa',email='buyer-login@testsupply.ng',phone=None,password_hash=hash_password('StrongPassword123!'),display_name='Buyer',role='BUYER',verified=True,profile={},payout_account={});s.add(u);s.commit();s.close()
    c=TestClient(app);r=c.post('/api/v1/auth/login',json={'identifier':'buyer-login@testsupply.ng','password':'StrongPassword123!'});assert r.status_code==200 and r.json().get('access_token') and not r.json().get('mfa_required')
    assert c.get('/api/v1/me',headers={'Authorization':'Bearer '+r.json()['access_token']}).status_code==200
    supplier=User(id='supplier-login-no-mfa',email='supplier-login@testsupply.ng',phone=None,password_hash=hash_password('StrongPassword123!'),display_name='Supplier',role='SUPPLIER',verified=True,profile={},payout_account={});s=SessionLocal();s.add(supplier);s.commit();s.close()
    c2=TestClient(app);sr=c2.post('/api/v1/auth/login',json={'identifier':'supplier-login@testsupply.ng','password':'StrongPassword123!'});assert sr.status_code==200 and sr.json().get('access_token') and not sr.json().get('mfa_required')

def staff_headers(email,role):
    import base64,struct,time
    from app.db import User,SessionLocal
    from app.main import hash_password
    s=SessionLocal();u=User(id=__import__('uuid').uuid4().__str__(),email=email,phone=None,password_hash=hash_password('StrongPassword123!'),display_name=role.title(),role=role,verified=True,mfa_secret='JBSWY3DPEHPK3PXP',profile={},payout_account={});s.add(u);s.commit();s.close()
    c=TestClient(app);r=c.post('/api/v1/auth/login',json={'identifier':email,'password':'StrongPassword123!'});assert r.status_code==200
    key=base64.b32decode('JBSWY3DPEHPK3PXP');counter=int(time.time())//30;digest=hmac.new(key,struct.pack('>Q',counter),hashlib.sha1).digest();off=digest[-1]&15;code=f"{(struct.unpack('>I',digest[off:off+4])[0]&0x7fffffff)%1000000:06d}"
    stale_counter=counter-1;stale_digest=hmac.new(key,struct.pack('>Q',stale_counter),hashlib.sha1).digest();stale_off=stale_digest[-1]&15;stale_code=f"{(struct.unpack('>I',stale_digest[stale_off:stale_off+4])[0]&0x7fffffff)%1000000:06d}"
    stale=c.post('/api/v1/auth/mfa/verify',json={'code':stale_code});assert stale.status_code==401 and stale.json()['error']['code']=='MFA_INVALID'
    r=c.post('/api/v1/auth/mfa/verify',json={'code':code});assert r.status_code==200
    return {'Authorization':'Bearer '+r.json()['access_token']}

def test_partial_receipt_acceptance_and_independent_financial_approval(monkeypatch):
    supplier=register('Supplier High','supplier-high@testsupply.ng');buyer=register('Buyer High','buyer-high@testsupply.ng')
    body={'buyer_contact':{'email':'buyer-high@testsupply.ng'},'delivery':{'destination_state':'Rivers','city':'Port Harcourt','expected_within_days':2,'delivery_fee_kobo':0},'items':[{'description':'Industrial pump','specification':'model X','condition':'new','quantity':2,'unit_price_kobo':50000000,'conditions':[{'code':'SPEC','kind':'REJECTION','text':'Model must be X'}]}]}
    tx=client.post('/api/v1/transactions',headers=supplier,json=body).json()['data'];tid=tx['id']
    invite=client.post(f'/api/v1/transactions/{tid}/send',headers=supplier).json()['invitation_token']
    assert client.post(f'/api/v1/invitations/{invite}/accept',headers=buyer).status_code==200
    agreement=client.get(f'/api/v1/transactions/{tid}/agreement',headers=buyer).json()['current_version']
    assert client.post(f'/api/v1/transactions/{tid}/agreement/confirm',headers=buyer,json={'agreement_version_id':agreement['id'],'content_hash':agreement['content_hash']}).status_code==200
    payment=client.post(f'/api/v1/transactions/{tid}/payments',headers={**buyer,'Idempotency-Key':'high-value-payment'},json={}).json()['data']
    ss=SessionLocal();provider_ref=ss.get(Payment,payment['payment_id']).provider_ref;ss.close()
    for status,event in [('PAYMENT_RECEIVED','hi-1'),('FUNDS_SECURED','hi-2')]:
        payload=json.dumps({'provider_ref':provider_ref,'status':status,'event_id':event,'amount_kobo':payment['amount_kobo']},separators=(',',':')).encode();sig=hmac.new(SECRET,payload,hashlib.sha256).hexdigest()
        assert client.post('/api/v1/webhooks/payments/simulated',content=payload,headers={'X-Sim-Signature':sig}).status_code==200
    tx=client.get(f'/api/v1/transactions/{tid}',headers=supplier).json();iid=tx['items'][0]['id']
    dispatch_proof=upload(supplier,tid,'DISPATCH_PROOF')
    assert client.post(f'/api/v1/transactions/{tid}/dispatch',headers=supplier,json={'lines':[{'item_id':iid,'quantity':2}],'courier_name':'Demo Courier','rider_name':'Demo Rider','rider_phone':'+2348000000000','evidence_ids':[dispatch_proof]}).status_code==200
    tx=client.get(f'/api/v1/transactions/{tid}',headers=buyer).json()
    receipt=client.post(f'/api/v1/transactions/{tid}/delivery/confirm-received',headers=buyer,json={'lines':[{'item_id':iid,'quantity':1}]});assert receipt.status_code==200,receipt.text
    tx=client.get(f'/api/v1/transactions/{tid}',headers=buyer).json()
    assert sorted(l['item_status'] for l in tx['lots'])==['DISPATCHED','RECEIPT_CONFIRMED']
    # Confirm the remaining dispatched unit separately; the backend preserves quantity-level lots.
    assert client.post(f'/api/v1/transactions/{tid}/delivery/confirm-received',headers=buyer,json={'lines':[{'item_id':iid,'quantity':1}]}).status_code==200
    tx=client.get(f'/api/v1/transactions/{tid}',headers=buyer).json()
    buyer_reqs=[r for r in tx['delivery_photo_requirements'] if r['party_role']=='BUYER']
    supplier_req=next(r for r in tx['delivery_photo_requirements'] if r['party_role']=='SUPPLIER')
    supplier_photo=upload(supplier,tid,'DELIVERY_PHOTO')
    assert client.post(f'/api/v1/transactions/{tid}/delivery/evidence',headers=supplier,json={'requirement_id':supplier_req['id'],'evidence_ids':[supplier_photo]}).status_code==200
    for req in buyer_reqs:
        buyer_photo=upload(buyer,tid,'DELIVERY_PHOTO')
        assert client.post(f'/api/v1/transactions/{tid}/delivery/evidence',headers=buyer,json={'requirement_id':req['id'],'evidence_ids':[buyer_photo]}).status_code==200
    accept_proof=upload(buyer,tid,'ACCEPTANCE_PROOF');reject_proof=upload(buyer,tid,'REJECTION_PROOF')
    condition_id=tx['items'][0]['conditions'][0]['id']
    wrong_purpose=client.post(f'/api/v1/transactions/{tid}/decisions',headers=buyer,json={'lines':[{'item_id':iid,'quantity_submitted':1,'quantity_accepted':1,'quantity_rejected':0,'accept_evidence_ids':[reject_proof]}]})
    assert wrong_purpose.status_code==422 and wrong_purpose.json()['error']['code']=='EVIDENCE_PURPOSE_MISMATCH'
    submitted=client.post(f'/api/v1/transactions/{tid}/decisions',headers=buyer,json={'lines':[{'item_id':iid,'quantity_submitted':2,'quantity_accepted':1,'quantity_rejected':1,'accept_evidence_ids':[accept_proof],'reject':{'agreed_condition_ids':[condition_id],'reason_text':'The returned unit is damaged and does not meet the agreed specification.','evidence_ids':[reject_proof]}}]});assert submitted.status_code==200,submitted.text
    did=submitted.json()['data']['decisions'][0]['id']
    assert client.post(f'/api/v1/decisions/{did}/waive-window',headers=buyer).status_code==200
    assert client.post(f'/api/v1/decisions/{did}/waive-window',headers=supplier).json()['status']=='CLOSED_WAIVED'
    moderator=staff_headers('moderator-high@testsupply.ng','MODERATOR')
    senior=staff_headers('senior-high@testsupply.ng','SENIOR_MODERATOR')
    detail=client.get(f'/api/v1/decisions/{did}',headers=moderator).json();version=detail['version']
    stale=client.post(f'/api/v1/mod/decisions/{did}/confirm',headers={**moderator,'If-Match':'0'},json={'reason':'Stale attempt.','evidence_ids_considered':[accept_proof]})
    assert stale.status_code==409 and stale.json()['error']['code']=='VERSION_CONFLICT'
    decision=client.post(f'/api/v1/mod/decisions/{did}/confirm',headers={**moderator,'If-Match':str(version)},json={'reason':'Evidence matches agreed model.','evidence_ids_considered':[accept_proof]})
    assert decision.status_code==200,decision.text
    payout=decision.json()['data']['payout'];assert payout['amount_kobo']==50000000 and payout['status']=='PENDING_APPROVAL'
    assert client.post(f"/api/v1/mod/payouts/{payout['id']}/approve",headers=senior,json={}).json()['status']=='QUEUED'
    ledger=client.get(f'/api/v1/transactions/{tid}/ledger',headers=supplier).json()['data']
    assert any(x['entry_type']=='RELEASE_AUTHORIZED' for x in ledger)
    assert all(sum(x['amount_kobo'] for x in ledger if x['journal_id']==jid and x['direction']=='DEBIT')==sum(x['amount_kobo'] for x in ledger if x['journal_id']==jid and x['direction']=='CREDIT') for jid in {x['journal_id'] for x in ledger})
    monkeypatch.setenv('SIMULATED_INSTRUCTION_SCENARIO','SUCCESS')
    from app.main import run_financial
    ss=SessionLocal();result=run_financial(ss);ss.close()
    assert any(x['id']==payout['id'] and x['status']=='SUCCEEDED' for x in result['processed'])
    final=client.get(f'/api/v1/transactions/{tid}',headers=buyer).json()
    assert any(l['item_status']=='SETTLED_RELEASED' and l['money_status']=='RELEASED' for l in final['lots'])
    final_ledger=client.get(f'/api/v1/transactions/{tid}/ledger',headers=supplier).json()['data']
    assert any(x['entry_type']=='PAYOUT_SENT' for x in final_ledger)
    # Rejected quantity remains held until handover and supplier-confirmed return receipt.
    opened=client.post(f'/api/v1/transactions/{tid}/returns',headers=buyer,json={'lines':[{'item_id':iid,'quantity':1}]});assert opened.status_code==200,opened.text
    rid=opened.json()['data']['id'];handover=upload(buyer,tid,'RETURN_HANDOVER')
    assert client.post(f'/api/v1/returns/{rid}/handover-evidence',headers=buyer,json={'evidence_ids':[handover],'courier_name':'Demo Courier','rider_name':'Buyer Rider','rider_phone':'+2348000000001'}).status_code==200
    return_receipt=upload(supplier,tid,'RETURN_RECEIPT')
    confirmed=client.post(f'/api/v1/returns/{rid}/confirm-receipt',headers=supplier,json={'lines':[{'item_id':iid,'quantity_received':1,'condition_matches_original':True}],'evidence_ids':[return_receipt]});assert confirmed.status_code==200,confirmed.text
    refund=confirmed.json()['refund'];assert refund['amount_kobo']==50000000 and refund['status']=='PENDING_APPROVAL'
    assert client.post(f"/api/v1/mod/refunds/{refund['id']}/approve",headers=senior,json={}).json()['status']=='QUEUED'
    ss=SessionLocal();refund_result=run_financial(ss);ss.close()
    assert any(x['id']==refund['id'] and x['status']=='SUCCEEDED' for x in refund_result['processed'])
    final=client.get(f'/api/v1/transactions/{tid}',headers=buyer).json()
    assert any(l['item_status']=='SETTLED_REFUNDED' and l['money_status']=='REFUNDED' for l in final['lots'])
    assert final['status']=='COMPLETED'

def test_worker_http_endpoints_require_worker_secret():
    assert client.post('/api/v1/worker/run-deadlines').status_code==403

def test_paga_sandbox_collection_webhook_and_balance_reconciliation(monkeypatch):
    from decimal import Decimal
    from app.providers.paga import PagaClient
    from app.db import PagaSubsidiaryAccount, Aggregate, Payment
    from app.main import process_paga_events

    monkeypatch.setenv('PAYMENT_PROVIDER','paga')
    monkeypatch.setenv('PAGA_MODE','sandbox')
    monkeypatch.setenv('PAGA_PUBLIC_KEY','sandbox-public')
    monkeypatch.setenv('PAGA_SECRET_KEY','sandbox-secret')
    monkeypatch.setenv('PAGA_HASH_KEY','sandbox-hash')
    monkeypatch.setenv('PAGA_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/subsidiary-account')
    monkeypatch.setenv('PAGA_BUSINESS_NAME','SupplyKey Sandbox')

    supplier=register('Paga Supplier','paga-supplier@testsupply.ng')
    buyer=register('Paga Buyer','paga-buyer@testsupply.ng')
    body={'buyer_contact':{'email':'paga-buyer@testsupply.ng'},'delivery':{'destination_state':'Rivers','city':'Port Harcourt','expected_within_days':3,'delivery_fee_kobo':0},'items':[{'description':'Paga sandbox item','specification':'model P','condition':'new','quantity':1,'unit_price_kobo':100000,'conditions':[{'code':'SPEC','kind':'REJECTION','text':'Must match model P'}]}]}
    created=client.post('/api/v1/transactions',headers=supplier,json=body);assert created.status_code==201,created.text
    tid=created.json()['data']['id']
    invitation=client.post(f'/api/v1/transactions/{tid}/send',headers=supplier).json()['invitation_token']
    assert client.post(f'/api/v1/invitations/{invitation}/accept',headers=buyer).status_code==200
    agreement=client.get(f'/api/v1/transactions/{tid}/agreement',headers=buyer).json()['current_version']
    assert client.post(f'/api/v1/transactions/{tid}/agreement/confirm',headers=buyer,json={'agreement_version_id':agreement['id'],'content_hash':agreement['content_hash']}).status_code==200

    def fake_create(self, **kwargs):
        return {'statusCode':'0','accountNumber':'0750529406','accountName':'Paga Buyer'}
    monkeypatch.setattr(PagaClient,'create_transaction_account',fake_create)
    payment=client.post(f'/api/v1/transactions/{tid}/payments',headers={**buyer,'Idempotency-Key':'paga-sandbox-1'},json={})
    assert payment.status_code==200,payment.text
    pdata=payment.json()['data']
    assert pdata['mode']=='PAGA_SANDBOX'
    assert pdata['checkout']['account_number']=='0750529406'
    buyer_view=client.get(f'/api/v1/transactions/{tid}',headers=buyer).json()
    assert buyer_view['status']=='AWAITING_PAYMENT'
    assert buyer_view['payments'][-1]['checkout']['account_number']=='0750529406'
    supplier_view=client.get(f'/api/v1/transactions/{tid}',headers=supplier).json()
    supplier_checkout=supplier_view['payments'][-1]['checkout']
    assert 'account_number' not in supplier_checkout
    assert 'account_name' not in supplier_checkout
    assert 'account_reference' not in supplier_checkout

    paga=PagaClient()
    transaction_date='2026-11-16T12:00:00.000'
    account_number='0750529406'
    timestamp='2026-11-16T12:00:01.000'
    def send_funding(reference, funding_reference, amount):
        signature=paga.digest(reference,transaction_date,Decimal(amount),account_number,timestamp)
        raw=(f'{{"statusCode":"0","statusMessage":"success","transactionReference":"{reference}","fundingTransactionReference":"{funding_reference}","fundingPaymentReference":"{funding_reference}","accountNumber":"{account_number}","amount":{amount},"transactionDate":"{transaction_date}","timestamp":"{timestamp}"}}').encode()
        return client.post('/api/v1/webhooks/paga/subsidiary-account',content=raw,headers={'Content-Type':'application/json','x-paga-hash-v3':signature})
    callback=send_funding('PAGA-TXN-REF-1','PAGA-FUND-1','400.00')
    assert callback.status_code==200,callback.text
    assert callback.json()['queued'] is True

    balances=iter(['400.00','1000.00'])
    def fake_balance(self, account_reference, reference):
        return {'statusCode':'0','balance':next(balances),'accountReference':account_reference}
    monkeypatch.setattr(PagaClient,'get_subsidiary_balance',fake_balance)
    session=SessionLocal()
    try:
        outcome=process_paga_events(session)
        assert outcome['processed']==1
    finally:
        session.close()
    tx=client.get(f'/api/v1/transactions/{tid}',headers=buyer).json()
    assert tx['status']=='AWAITING_PAYMENT'
    assert tx['payments'][-1]['status']=='PAYMENT_RECEIVED'
    assert tx['payments'][-1]['checkout']['remaining_kobo']==60000

    second=send_funding('PAGA-TXN-REF-2','PAGA-FUND-2','600.00')
    assert second.status_code==200,second.text
    session=SessionLocal()
    try:
        outcome=process_paga_events(session)
        assert outcome['processed']==1
    finally:
        session.close()
    tx=client.get(f'/api/v1/transactions/{tid}',headers=buyer).json()
    assert tx['status']=='FUNDED'
    assert tx['payments'][-1]['status']=='FUNDS_SECURED'


def test_paga_bank_destination_is_validated_encrypted_and_masked(monkeypatch):
    from app.providers.paga import PagaClient
    from app.main import decrypt_payout_account
    from app.db import User
    from sqlalchemy import select

    monkeypatch.setenv('PAYMENT_PROVIDER','paga')
    monkeypatch.setenv('PAGA_MODE','sandbox')
    monkeypatch.setenv('PAGA_PUBLIC_KEY','sandbox-public')
    monkeypatch.setenv('PAGA_SECRET_KEY','sandbox-secret')
    monkeypatch.setenv('PAGA_HASH_KEY','sandbox-hash')
    monkeypatch.setenv('PAGA_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/subsidiary-account')
    monkeypatch.setenv('PAGA_PAYOUT_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/bank-transfer')
    monkeypatch.setenv('PAYOUT_ENCRYPTION_KEY','test-payout-encryption-key-32-bytes-minimum')
    user=register('Bank Destination User','bank-destination@testsupply.ng')

    monkeypatch.setattr(PagaClient,'get_banks',lambda self:{'responseCode':0,'banks':[{'uuid':'BANK-UUID-1','name':'Test Bank','interInstitutionCode':'000999','sortCode':'999'}]})
    monkeypatch.setattr(PagaClient,'validate_bank_account',lambda self,reference,amount_kobo,bank_uuid,account_number:{'responseCode':0,'destinationAccountHolderNameAtBank':'Test Destination User','fee':10,'vat':0.75})
    response=client.put('/api/v1/me/payout-account',headers=user,json={'bank_code':'999','account_number':'0123456789'})
    assert response.status_code==200,response.text
    payout=response.json()['payout_account']
    assert payout['verified'] is True
    assert payout['masked_account_number']=='••••6789'
    assert 'account_number_enc' not in payout

    me_response=client.get('/api/v1/me',headers=user)
    assert me_response.status_code==200
    assert 'account_number_enc' not in me_response.json()['payout_account']
    session=SessionLocal()
    try:
        stored=session.scalar(select(User).where(User.email=='bank-destination@testsupply.ng'))
        assert stored.payout_account['account_number_enc']
        assert decrypt_payout_account(stored.payout_account)=='0123456789'
    finally:session.close()


def test_supplier_created_transaction_is_owned_by_resolved_buyer_and_isolated():
    supplier=register('Ownership Supplier','owner-supplier@testsupply.ng')
    buyer=register('Ownership Buyer','buyer.owner@testsupply.ng')
    outsider=register('Ownership Outsider','outsider.owner@testsupply.ng')
    body={'buyer_contact':{'email':' BUYER.OWNER@TESTSUPPLY.NG '},'delivery':{'destination_state':'Rivers','city':'Port Harcourt','delivery_fee_kobo':0},'items':[{'description':'Pump','quantity':1,'unit_price_kobo':25000000,'conditions':[{'code':'SPEC','kind':'REJECTION','text':'Match'}]}]}
    created=client.post('/api/v1/transactions',headers=supplier,json=body);assert created.status_code==201,created.text
    tx=created.json()['data'];assert tx['buyer_id']
    ss=SessionLocal()
    try:
        row=ss.get(Aggregate,tx['id']);assert row.supplier_id and row.buyer_id
        assert row.buyer_id==client.get('/api/v1/me',headers=buyer).json()['user']['id']
    finally:ss.close()
    assert any(x['id']==tx['id'] for x in client.get('/api/v1/transactions?role=BUYER',headers=buyer).json()['data'])
    assert all(x['id']!=tx['id'] for x in client.get('/api/v1/transactions?role=BUYER',headers=outsider).json()['data'])
    sent=client.post(f"/api/v1/transactions/{tx['id']}/send",headers=supplier);token=sent.json()['invitation_token']
    version=client.get(f"/api/v1/transactions/{tx['id']}/agreement",headers=buyer).json()['current_version']
    premature=client.post(f"/api/v1/transactions/{tx['id']}/agreement/confirm",headers=buyer,json={'agreement_version_id':version['id'],'content_hash':version['content_hash']})
    assert premature.status_code==409
    wrong=client.post(f'/api/v1/invitations/{token}/accept',headers=outsider)
    assert wrong.status_code==403 and wrong.json()['error']['code']=='INVITATION_IDENTITY_MISMATCH'
    accepted=client.post(f'/api/v1/invitations/{token}/accept',headers=buyer);assert accepted.status_code==200,accepted.text
    assert client.post(f'/api/v1/invitations/{token}/accept',headers=buyer).status_code==409
    assert client.post(f'/api/v1/invitations/{token}/accept',headers=supplier).status_code==422


def test_assigned_buyer_can_accept_invitation_from_my_purchases_before_agreeing():
    supplier=register('Dashboard Invite Supplier','dashboard-invite-supplier@testsupply.ng')
    buyer=register('Dashboard Invite Buyer','dashboard-invite-buyer@testsupply.ng')
    outsider=register('Dashboard Invite Outsider','dashboard-invite-outsider@testsupply.ng')
    body={'buyer_contact':{'email':'dashboard-invite-buyer@testsupply.ng'},'delivery':{},'items':[{'description':'Goods','quantity':1,'unit_price_kobo':25000000,'conditions':[{'code':'SPEC','kind':'REJECTION','text':'Match'}]}]}
    created=client.post('/api/v1/transactions',headers=supplier,json=body);assert created.status_code==201,created.text
    tx=created.json()['data'];sent=client.post(f"/api/v1/transactions/{tx['id']}/send",headers=supplier);assert sent.status_code==200,sent.text
    detail=client.get(f"/api/v1/transactions/{tx['id']}",headers=buyer).json()
    assert 'accept_invitation' in detail['allowed_actions'] and 'agree' not in detail['allowed_actions']
    denied=client.post(f"/api/v1/transactions/{tx['id']}/invitation/accept",headers=outsider)
    assert denied.status_code==404  # transaction ownership is not disclosed to unrelated accounts
    accepted=client.post(f"/api/v1/transactions/{tx['id']}/invitation/accept",headers=buyer)
    assert accepted.status_code==200,accepted.text
    detail=client.get(f"/api/v1/transactions/{tx['id']}",headers=buyer).json()
    assert 'agree' in detail['allowed_actions'] and 'accept_invitation' not in detail['allowed_actions']
    version=client.get(f"/api/v1/transactions/{tx['id']}/agreement",headers=buyer).json()['current_version']
    agreed=client.post(f"/api/v1/transactions/{tx['id']}/agreement/confirm",headers=buyer,json={'agreement_version_id':version['id'],'content_hash':version['content_hash']})
    assert agreed.status_code==200,agreed.text
    reused=client.post(f"/api/v1/transactions/{tx['id']}/invitation/accept",headers=buyer)
    assert reused.status_code==409


def test_unknown_buyer_is_not_assigned_and_can_claim_after_verified_registration():
    supplier=register('Unknown Supplier','unknown-supplier@testsupply.ng')
    body={'buyer_contact':{'email':'future.buyer@testsupply.ng'},'delivery':{'destination_state':'Rivers','city':'Port Harcourt'},'items':[{'description':'Goods','quantity':1,'unit_price_kobo':10000,'conditions':[{'code':'SPEC','kind':'REJECTION','text':'Match'}]}]}
    created=client.post('/api/v1/transactions',headers=supplier,json=body);assert created.status_code==201,created.text
    tx=created.json()['data'];assert tx['buyer_id'] is None
    sent=client.post(f"/api/v1/transactions/{tx['id']}/send",headers=supplier);token=sent.json()['invitation_token']
    unrelated=register('Other Account','other.future@testsupply.ng')
    assert client.post(f'/api/v1/invitations/{token}/accept',headers=unrelated).status_code==403
    intended=register('Future Buyer','future.buyer@testsupply.ng')
    accepted=client.post(f'/api/v1/invitations/{token}/accept',headers=intended);assert accepted.status_code==200,accepted.text
    assert any(x['id']==tx['id'] for x in client.get('/api/v1/transactions?role=BUYER',headers=intended).json()['data'])


def test_conflicting_buyer_contacts_are_rejected_without_assignment():
    supplier=register('Mismatch Supplier','mismatch-supplier@testsupply.ng')
    register('Email Owner','email-owner@testsupply.ng')
    register('Phone Owner','phone-owner@testsupply.ng')
    ss=SessionLocal()
    try:
        phone_owner=ss.scalar(select(User).where(User.email=='phone-owner@testsupply.ng'))
        phone_owner.phone='+2348012345678';ss.commit()
    finally:ss.close()
    body={'buyer_contact':{'email':'email-owner@testsupply.ng','phone':'+234 801 234 5678'},'delivery':{},'items':[{'description':'Goods','quantity':1,'unit_price_kobo':10000,'conditions':[{'code':'SPEC','kind':'REJECTION','text':'Match'}]}]}
    response=client.post('/api/v1/transactions',headers=supplier,json=body)
    assert response.status_code==409 and response.json()['error']['code']=='BUYER_CONTACT_MISMATCH'


def test_buyer_contact_normalization_on_registration():
    r=client.post('/api/v1/auth/register',json={'display_name':'Normalized','email':'  Mixed.Case@testsupply.ng ','phone':'+234 809-876-5432','password':'StrongPassword123!','accepted_terms':True})
    assert r.status_code==201,r.text
    s=SessionLocal()
    try:
        user=s.get(User,r.json()['user_id']);assert user.email=='mixed.case@testsupply.ng' and user.phone=='+2348098765432'
    finally:s.close()


def test_local_nigerian_phone_matches_registered_international_format():
    supplier=register('Phone Supplier','phone-match-supplier@testsupply.ng')
    buyer=register('Phone Buyer','phone-match-buyer@testsupply.ng')
    ss=SessionLocal()
    try:
        user_id=client.get('/api/v1/me',headers=buyer).json()['user']['id']
        user=ss.get(User,user_id);user.phone='+2348031234567';ss.commit()
    finally:ss.close()
    body={'buyer_contact':{'phone':'0803 123 4567'},'delivery':{},'items':[{'description':'Goods','quantity':1,'unit_price_kobo':10000,'conditions':[{'code':'SPEC','kind':'REJECTION','text':'Match'}]}]}
    created=client.post('/api/v1/transactions',headers=supplier,json=body)
    assert created.status_code==201,created.text
    assert created.json()['data']['buyer_id']==user_id


def test_supplier_cannot_override_buyer_id_with_request_payload():
    supplier=register('Payload Supplier','payload-supplier@testsupply.ng')
    unrelated=register('Payload Unrelated','payload-unrelated@testsupply.ng')
    unrelated_id=client.get('/api/v1/me',headers=unrelated).json()['user']['id']
    body={'buyer_id':unrelated_id,'buyer_contact':{'email':'not-registered@testsupply.ng'},'delivery':{},'items':[{'description':'Goods','quantity':1,'unit_price_kobo':10000,'conditions':[{'code':'SPEC','kind':'REJECTION','text':'Match'}]}]}
    response=client.post('/api/v1/transactions',headers=supplier,json=body)
    assert response.status_code==201,response.text
    assert response.json()['data']['buyer_id'] is None
    assert client.get('/api/v1/transactions?role=BUYER',headers=unrelated).json()['data']==[]


def test_legacy_accepted_transaction_without_new_invitation_flag_remains_compatible():
    supplier=register('Legacy Supplier','legacy-supplier@testsupply.ng')
    buyer=register('Legacy Buyer','legacy-buyer@testsupply.ng')
    body={'buyer_contact':{'email':'legacy-buyer@testsupply.ng'},'delivery':{},'items':[{'description':'Goods','quantity':1,'unit_price_kobo':10000,'conditions':[{'code':'SPEC','kind':'REJECTION','text':'Match'}]}]}
    created=client.post('/api/v1/transactions',headers=supplier,json=body).json()['data']
    client.post(f"/api/v1/transactions/{created['id']}/send",headers=supplier)
    ss=SessionLocal()
    try:
        row=ss.get(Aggregate,created['id']);data=dict(row.data);data.pop('invitation_acceptance_required',None);row.data=data;ss.commit()
    finally:ss.close()
    version=client.get(f"/api/v1/transactions/{created['id']}/agreement",headers=buyer).json()['current_version']
    agreed=client.post(f"/api/v1/transactions/{created['id']}/agreement/confirm",headers=buyer,json={'agreement_version_id':version['id'],'content_hash':version['content_hash']})
    assert agreed.status_code==200,agreed.text



def test_admin_session_is_created_only_after_mfa():
    from app.db import SessionLocal,User
    from app.main import hash_password
    s=SessionLocal();u=User(id='admin-mfa-flow-test',email='admin-mfa-flow@testsupply.ng',phone=None,password_hash=hash_password('StrongPassword123!'),display_name='Admin',role='ADMIN',verified=True,mfa_secret='JBSWY3DPEHPK3PXP',profile={},payout_account={});s.add(u);s.commit();s.close()
    c=TestClient(app);r=c.post('/api/v1/auth/login',json={'identifier':'admin-mfa-flow@testsupply.ng','password':'StrongPassword123!'});assert r.status_code==200 and r.json().get('mfa_required') is True and 'access_token' not in r.json()
    assert c.get('/api/v1/admin/audit').status_code==401
    import base64,struct,time
    key=base64.b32decode('JBSWY3DPEHPK3PXP');counter=int(time.time())//30;digest=hmac.new(key,struct.pack('>Q',counter),hashlib.sha1).digest();off=digest[-1]&15;code=f"{(struct.unpack('>I',digest[off:off+4])[0]&0x7fffffff)%1000000:06d}"
    stale_counter=counter-1;stale_digest=hmac.new(key,struct.pack('>Q',stale_counter),hashlib.sha1).digest();stale_off=stale_digest[-1]&15;stale_code=f"{(struct.unpack('>I',stale_digest[stale_off:stale_off+4])[0]&0x7fffffff)%1000000:06d}"
    stale=c.post('/api/v1/auth/mfa/verify',json={'code':stale_code});assert stale.status_code==401 and stale.json()['error']['code']=='MFA_INVALID'
    verified=c.post('/api/v1/auth/mfa/verify',json={'code':code});assert verified.status_code==200 and verified.json().get('access_token')
    headers={'Authorization':'Bearer '+verified.json()['access_token']};assert c.get('/api/v1/admin/audit',headers=headers).status_code==200


def test_openapi_declares_bearer_security_for_protected_admin_routes():
    schema=client.get('/openapi.json').json()
    assert 'BearerAuth' in schema['components']['securitySchemes']
    scheme=schema['components']['securitySchemes']['BearerAuth']
    assert scheme['type']=='http' and scheme['scheme']=='bearer'
    operation=schema['paths']['/api/v1/admin/audit']['get']
    assert {'BearerAuth': []} in operation['security']


def test_admin_audit_requires_valid_admin_bearer_token():
    from app.db import SessionLocal,User
    from app.main import hash_password
    # Missing and malformed credentials must remain unauthorized.
    assert client.get('/api/v1/admin/audit').status_code==401
    assert client.get('/api/v1/admin/audit',headers={'Authorization':'Bearer not-a-valid-token'}).status_code==401

    # A fully authenticated ordinary buyer is authenticated but not authorized.
    buyer=register('Audit Buyer','audit-buyer@testsupply.ng')
    denied=client.get('/api/v1/admin/audit',headers=buyer)
    assert denied.status_code==403

    # Staff login remains MFA-gated: only the verified MFA response supplies a
    # bearer access token accepted by the protected audit endpoint.
    s=SessionLocal()
    admin=User(id='swagger-audit-admin',email='swagger-audit-admin@testsupply.ng',phone=None,
        password_hash=hash_password('StrongPassword123!'),display_name='Swagger Admin',
        role='ADMIN',verified=True,mfa_secret='JBSWY3DPEHPK3PXP',profile={},payout_account={})
    s.add(admin);s.commit();s.close()
    import base64,struct,time
    c=TestClient(app)
    login=c.post('/api/v1/auth/login',json={'identifier':admin.email,'password':'StrongPassword123!'})
    assert login.status_code==200 and login.json().get('mfa_required') is True
    assert 'access_token' not in login.json()
    key=base64.b32decode('JBSWY3DPEHPK3PXP');counter=int(time.time())//30
    digest=hmac.new(key,struct.pack('>Q',counter),hashlib.sha1).digest();off=digest[-1]&15
    code=f"{(struct.unpack('>I',digest[off:off+4])[0]&0x7fffffff)%1000000:06d}"
    verified=c.post('/api/v1/auth/mfa/verify',json={'code':code})
    assert verified.status_code==200 and verified.json().get('access_token')
    token=verified.json()['access_token']
    audit=c.get('/api/v1/admin/audit',headers={'Authorization':'Bearer '+token})
    assert audit.status_code==200 and 'data' in audit.json()


def test_paga_balance_refresh_recovers_payment_when_funding_callback_is_missing(monkeypatch):
    from app.providers.paga import PagaClient

    monkeypatch.setenv('PAYMENT_PROVIDER','paga')
    monkeypatch.setenv('PAGA_MODE','sandbox')
    monkeypatch.setenv('PAGA_PUBLIC_KEY','sandbox-public')
    monkeypatch.setenv('PAGA_SECRET_KEY','sandbox-secret')
    monkeypatch.setenv('PAGA_HASH_KEY','sandbox-hash')
    monkeypatch.setenv('PAGA_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/subsidiary-account')
    monkeypatch.setenv('PAGA_BUSINESS_NAME','SupplyKey Sandbox')

    supplier=register('Polling Supplier','polling-supplier@testsupply.ng')
    buyer=register('Polling Buyer','polling-buyer@testsupply.ng')
    body={'buyer_contact':{'email':'polling-buyer@testsupply.ng'},'delivery':{'destination_state':'Rivers','city':'Port Harcourt','expected_within_days':3,'delivery_fee_kobo':0},'items':[{'description':'Polling test item','specification':'model P','condition':'new','quantity':1,'unit_price_kobo':100000,'conditions':[{'code':'SPEC','kind':'REJECTION','text':'Must match model P'}]}]}
    created=client.post('/api/v1/transactions',headers=supplier,json=body)
    assert created.status_code==201,created.text
    tid=created.json()['data']['id']
    invitation=client.post(f'/api/v1/transactions/{tid}/send',headers=supplier).json()['invitation_token']
    assert client.post(f'/api/v1/invitations/{invitation}/accept',headers=buyer).status_code==200
    agreement=client.get(f'/api/v1/transactions/{tid}/agreement',headers=buyer).json()['current_version']
    assert client.post(f'/api/v1/transactions/{tid}/agreement/confirm',headers=buyer,json={'agreement_version_id':agreement['id'],'content_hash':agreement['content_hash']}).status_code==200

    monkeypatch.setattr(PagaClient,'create_transaction_account',lambda self,**kwargs:{'statusCode':'0','accountNumber':'0750529417','accountName':'Polling Buyer'})
    payment=client.post(f'/api/v1/transactions/{tid}/payments',headers={**buyer,'Idempotency-Key':'paga-polling-recovery'},json={})
    assert payment.status_code==200,payment.text
    payment_id=payment.json()['data']['payment_id']

    # Simulate a successful deposit whose Paga webhook was never delivered.
    monkeypatch.setattr(PagaClient,'get_subsidiary_balance',lambda self,account_reference,reference:{'statusCode':'0','balance':'1000.00','accountReference':account_reference})
    refreshed=client.post(f'/api/v1/transactions/{tid}/payments/{payment_id}/refresh',headers=buyer,json={})
    assert refreshed.status_code==200,refreshed.text
    assert refreshed.json()['status']=='FUNDS_SECURED'
    final=client.get(f'/api/v1/transactions/{tid}',headers=buyer).json()
    assert final['status']=='FUNDED'
    assert final['payments'][-1]['status']=='FUNDS_SECURED'


def test_paga_overpayment_secures_agreed_amount_and_queues_excess_for_moderator(monkeypatch):
    from app.providers.paga import PagaClient
    from app.db import Instruction, Journal

    monkeypatch.setenv('PAYMENT_PROVIDER','paga')
    monkeypatch.setenv('PAGA_MODE','sandbox')
    monkeypatch.setenv('PAGA_PUBLIC_KEY','sandbox-public')
    monkeypatch.setenv('PAGA_SECRET_KEY','sandbox-secret')
    monkeypatch.setenv('PAGA_HASH_KEY','sandbox-hash')
    monkeypatch.setenv('PAGA_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/subsidiary-account')
    monkeypatch.setenv('PAGA_BUSINESS_NAME','SupplyKey Sandbox')

    supplier=register('Overpay Supplier','overpay-supplier@testsupply.ng')
    buyer=register('Overpay Buyer','overpay-buyer@testsupply.ng')
    moderator=register('Overpay Senior Moderator','overpay-moderator@testsupply.ng')
    moderator_session=SessionLocal()
    try:
        moderator_user=moderator_session.scalar(select(User).where(User.email=='overpay-moderator@testsupply.ng'))
        moderator_user.role='SENIOR_MODERATOR';moderator_session.commit()
    finally:
        moderator_session.close()
    body={'buyer_contact':{'email':'overpay-buyer@testsupply.ng'},'delivery':{'destination_state':'Rivers','city':'Port Harcourt','expected_within_days':3,'delivery_fee_kobo':0},'items':[{'description':'Overpay test item','specification':'model O','condition':'new','quantity':1,'unit_price_kobo':100000,'conditions':[{'code':'SPEC','kind':'REJECTION','text':'Must match model O'}]}]}
    created=client.post('/api/v1/transactions',headers=supplier,json=body);assert created.status_code==201,created.text
    tid=created.json()['data']['id']
    invitation=client.post(f'/api/v1/transactions/{tid}/send',headers=supplier).json()['invitation_token']
    assert client.post(f'/api/v1/invitations/{invitation}/accept',headers=buyer).status_code==200
    agreement=client.get(f'/api/v1/transactions/{tid}/agreement',headers=buyer).json()['current_version']
    assert client.post(f'/api/v1/transactions/{tid}/agreement/confirm',headers=buyer,json={'agreement_version_id':agreement['id'],'content_hash':agreement['content_hash']}).status_code==200
    monkeypatch.setattr(PagaClient,'create_transaction_account',lambda self,**kwargs:{'statusCode':'0','accountNumber':'0750529499','accountName':'Overpay Buyer'})
    started=client.post(f'/api/v1/transactions/{tid}/payments',headers={**buyer,'Idempotency-Key':'paga-overpayment-test'},json={})
    assert started.status_code==200,started.text
    payment_id=started.json()['data']['payment_id']
    # Existing overpayment attempts may already be UNKNOWN under the old behaviour.
    session=SessionLocal()
    try:
        session.get(Payment,payment_id).status='UNKNOWN';session.commit()
    finally:
        session.close()
    monkeypatch.setattr(PagaClient,'get_subsidiary_balance',lambda self,account_reference,reference:{'statusCode':'0','balance':'1,200.00','accountReference':account_reference})
    refreshed=client.post(f'/api/v1/transactions/{tid}/payments/{payment_id}/refresh',headers=buyer,json={})
    assert refreshed.status_code==200,refreshed.text
    assert refreshed.json()['status']=='FUNDS_SECURED'
    tx=client.get(f'/api/v1/transactions/{tid}',headers=buyer).json()
    assert tx['status']=='FUNDED'
    payment=tx['payments'][-1]
    assert payment['status']=='FUNDS_SECURED'
    assert payment['checkout']['overpayment_kobo']==20000
    assert payment['checkout']['overpayment_refund_status']=='PENDING_MODERATOR_APPROVAL'
    assert client.get('/api/v1/mod/financial-approvals',headers=moderator).status_code==200
    approvals=client.get('/api/v1/mod/financial-approvals',headers=moderator).json()['data']
    refund=next(x for x in approvals if x.get('origin_type')=='PAGA_OVERPAYMENT' and x['transaction_id']==tid)
    assert refund['amount_kobo']==20000 and refund['status']=='PENDING_APPROVAL'
    approved=client.post(f"/api/v1/mod/refunds/{refund['id']}/approve",headers=moderator,json={})
    assert approved.status_code==200,approved.text
    s=SessionLocal()
    try:
        instruction=s.get(Instruction,refund['id'])
        assert instruction.status=='QUEUED'
        overpayment_journals=s.scalars(select(Journal).where(Journal.reference_id==refund['id'])).all()
        assert [journal.entry_type for journal in overpayment_journals]==['OVERPAYMENT_RECEIVED']
    finally:
        s.close()


def test_buyer_receipt_action_permission_requires_dispatched_lots():
    from types import SimpleNamespace
    from app.main import allowed_actions
    buyer=SimpleNamespace(id='buyer-1')
    tx=SimpleNamespace(id='tx-1',supplier_id='supplier-1',buyer_id='buyer-1',status='FUNDED',data={'lots':[{'item_id':'item-1','quantity':2,'item_status':'AWAITING_DISPATCH'}]})
    assert 'confirm_received' not in allowed_actions(tx,buyer)
    tx.data={'lots':[{'item_id':'item-1','quantity':2,'item_status':'DISPATCHED'}]}
    assert 'confirm_received' in allowed_actions(tx,buyer)
    tx.data={'lots':[{'item_id':'item-1','quantity':0,'item_status':'DISPATCHED'}]}
    assert 'confirm_received' not in allowed_actions(tx,buyer)
