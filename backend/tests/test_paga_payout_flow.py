import json
import uuid
from fastapi.testclient import TestClient

from app.db import SessionLocal, User, Aggregate, Payment, PagaSubsidiaryAccount, Instruction, LedgerEntry
from app.main import app, payout_cipher, process_paga_payout_events, run_paga_financial
from app.providers.paga import PagaClient


def test_paga_payout_submission_and_signed_settlement_callback(monkeypatch):
    monkeypatch.setenv('PAYMENT_PROVIDER','paga')
    monkeypatch.setenv('PAGA_MODE','sandbox')
    monkeypatch.setenv('PAGA_PUBLIC_KEY','sandbox-public')
    monkeypatch.setenv('PAGA_SECRET_KEY','sandbox-secret')
    monkeypatch.setenv('PAGA_HASH_KEY','sandbox-hash')
    monkeypatch.setenv('PAGA_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/subsidiary-account')
    monkeypatch.setenv('PAGA_PAYOUT_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/bank-transfer')
    monkeypatch.setenv('PAYOUT_ENCRYPTION_KEY','test-payout-encryption-key-32-bytes-minimum')

    supplier_id=str(uuid.uuid4());buyer_id=str(uuid.uuid4());txid=str(uuid.uuid4());payment_id=str(uuid.uuid4());ins_id=str(uuid.uuid4());lot_id=str(uuid.uuid4());decision_id=str(uuid.uuid4());account_number=str(uuid.uuid4().int % 10**10).zfill(10)
    session=SessionLocal()
    try:
        supplier=User(id=supplier_id,email=f'{supplier_id}@test.ng',phone=None,password_hash='x',display_name='Supplier',role='USER',verified=True,profile={},payout_account={
            'bank_uuid':'BANK-UUID','bank_code':'044','bank_name':'Access Bank',
            'account_number_enc':payout_cipher().encrypt(b'0123456789').decode(),
            'masked_account_number':'••••6789','account_name':'Supplier Name','verified':True,
        })
        buyer=User(id=buyer_id,email=f'{buyer_id}@test.ng',phone=None,password_hash='x',display_name='Buyer',role='USER',verified=True,profile={},payout_account={})
        tx=Aggregate(id=txid,reference='SK-PAGA-PAYOUT-TEST',supplier_id=supplier_id,buyer_id=buyer_id,status='FUNDED',version=1,data={
            'items':[{'id':'item-1','quantity':1,'unit_price_kobo':100000,'line_total_kobo':100000}],
            'lots':[{'id':lot_id,'item_id':'item-1','quantity':1,'item_status':'ACCEPTED_CONFIRMED','money_status':'RELEASE_PENDING','allocation_kobo':100000,'is_frozen':False}],
            'decisions':[{'id':decision_id,'lot_id':lot_id}],
        })
        payment=Payment(id=payment_id,transaction_id=txid,amount_kobo=100000,status='FUNDS_SECURED',provider_ref='SKPAYTEST',mode='PAGA_SANDBOX')
        account=PagaSubsidiaryAccount(id=str(uuid.uuid4()),transaction_id=txid,payment_id=payment_id,account_reference='SKTXPAGATEST123',account_number=account_number,provider_reference='SKPAYTEST',status='FUNDED')
        instruction=Instruction(id=ins_id,transaction_id=txid,instruction_type='PAYOUT',amount_kobo=100000,status='QUEUED',origin_id=decision_id,deciding_moderator_id=None,idempotency_key='paga-test-instruction',destination_snapshot={'bank_uuid':'BANK-UUID','account_number_enc':payout_cipher().encrypt(b'0123456789').decode(),'account_name':'Supplier Name','verified':True})
        session.add_all([supplier,buyer,tx,payment,account,instruction]);session.commit()
    finally:
        session.close()

    # A later account change must not redirect an already-authorized instruction.
    session=SessionLocal()
    try:
        stored=session.get(User,supplier_id)
        stored.payout_account={**stored.payout_account,'bank_uuid':'CHANGED-BANK','account_number_enc':payout_cipher().encrypt(b'9999999999').decode(),'account_name':'Different Person'}
        session.commit()
    finally:
        session.close()

    calls=[]
    def fake_charge(self, account_reference, reference, amount_kobo, narration):
        calls.append(('charge',account_reference,reference,amount_kobo));return {'statusCode':'0','transactionId':'CHG-1','newBalance':'0.00'}
    def fake_validate(self, reference, amount_kobo, bank_uuid, account_number):
        assert bank_uuid=='BANK-UUID' and account_number=='0123456789'
        return {'responseCode':0,'destinationAccountHolderNameAtBank':'Supplier Name','fee':10,'vat':0.75}
    def fake_business_balance(self, reference):
        return {'responseCode':0,'availableBalance':'5000.00'}
    def fake_deposit(self, reference, amount_kobo, bank_uuid, account_number, callback_url, remarks):
        calls.append(('deposit',reference,amount_kobo,bank_uuid,account_number,callback_url));return {'responseCode':0,'transactionId':'RP-1'}
    def fake_balance(self, account_reference, reference):
        return {'statusCode':'0','balance':'1000.00'}
    monkeypatch.setattr(PagaClient,'get_subsidiary_balance',fake_balance)
    monkeypatch.setattr(PagaClient,'charge_subsidiary_account',fake_charge)
    monkeypatch.setattr(PagaClient,'validate_bank_account',fake_validate)
    monkeypatch.setattr(PagaClient,'get_business_balance',fake_business_balance)
    monkeypatch.setattr(PagaClient,'deposit_to_bank',fake_deposit)

    session=SessionLocal()
    try:
        result=run_paga_financial(session)
        assert next(x for x in result['processed'] if x['id']==ins_id)['status']=='SUBMITTED', (result, instruction.provider_response, instruction.provider_stage)
        instruction=session.get(Instruction,ins_id)
        assert instruction.status=='SUBMITTED'
        assert instruction.provider_stage=='TRANSFER_SUBMITTED'
        assert [x[0] for x in calls]==['charge','deposit'] and calls[1][3:5]==('BANK-UUID','0123456789')
        transfer_ref=instruction.provider_reference
    finally:
        session.close()
    session=SessionLocal()
    try:
        repeated=run_paga_financial(session)
        assert not any(x['id']==ins_id for x in repeated['processed'])
        assert len([x for x in calls if x[0]=='deposit'])==1
    finally:
        session.close()

    paga=PagaClient();date='2026-11-16 12:00:00.00';amount='1000.0';session_id='SESSION-PAGA-1';transaction_id='RP-1'
    signature=paga.digest(transfer_ref,date,transaction_id,amount,session_id)
    raw=(f'{{"responseCode":0,"referenceNumber":"{transfer_ref}","transactionId":"{transaction_id}","transactionType":"USER_SEND_CASH_TO_BANK_ACCOUNT_SETTLED","transactionDate":"{date}","amount":"{amount}","currency":"NGN","sessionId":"{session_id}","reversalId":null}}').encode()
    callback=TestClient(app).post('/api/v1/webhooks/paga/bank-transfer',content=raw,headers={'Content-Type':'application/json','x-paga-hash':signature})
    assert callback.status_code==200,callback.text
    duplicate=TestClient(app).post('/api/v1/webhooks/paga/bank-transfer',content=raw,headers={'Content-Type':'application/json','x-paga-hash':signature})
    assert duplicate.status_code==200 and duplicate.json().get('duplicate') is True

    session=SessionLocal()
    try:
        processed=process_paga_payout_events(session)
        assert processed['processed']==1
        instruction=session.get(Instruction,ins_id)
        assert instruction.status=='SUCCEEDED'
        entries=session.query(LedgerEntry).filter(LedgerEntry.transaction_id==txid).all()
        assert len(entries)==2
        assert sum(x.amount_kobo for x in entries if x.direction=='DEBIT')==100000
        assert sum(x.amount_kobo for x in entries if x.direction=='CREDIT')==100000
        repeated_events=process_paga_payout_events(session)
        assert repeated_events['processed']==0
        assert len(session.query(LedgerEntry).filter(LedgerEntry.transaction_id==txid).all())==2
    finally:
        session.close()


def test_paga_subsidiary_charge_callback_resolves_unknown_charge(monkeypatch):
    monkeypatch.setenv('PAYMENT_PROVIDER','paga')
    monkeypatch.setenv('PAGA_MODE','sandbox')
    monkeypatch.setenv('PAGA_PUBLIC_KEY','sandbox-public')
    monkeypatch.setenv('PAGA_SECRET_KEY','sandbox-secret')
    monkeypatch.setenv('PAGA_HASH_KEY','sandbox-hash')
    monkeypatch.setenv('PAGA_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/subsidiary-account')
    monkeypatch.setenv('PAGA_PAYOUT_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/bank-transfer')

    from app.db import ProviderEvent
    from app.main import process_paga_subsidiary_operation_events

    supplier_id=str(uuid.uuid4());buyer_id=str(uuid.uuid4());txid=str(uuid.uuid4());payment_id=str(uuid.uuid4());ins_id=str(uuid.uuid4());lot_id=str(uuid.uuid4());decision_id=str(uuid.uuid4())
    charge_ref='SKCHG'+ins_id.replace('-','')
    session=SessionLocal()
    try:
        supplier=User(id=supplier_id,email=f'{supplier_id}@test.ng',phone=None,password_hash='x',display_name='Supplier',role='USER',verified=True,profile={},payout_account={})
        buyer=User(id=buyer_id,email=f'{buyer_id}@test.ng',phone=None,password_hash='x',display_name='Buyer',role='USER',verified=True,profile={},payout_account={})
        tx=Aggregate(id=txid,reference='SK-PAGA-CHARGE-UNKNOWN',supplier_id=supplier_id,buyer_id=buyer_id,status='FUNDED',version=1,data={'lots':[{'id':lot_id,'item_id':'item-1','quantity':1,'item_status':'ACCEPTED_CONFIRMED','money_status':'RELEASE_PENDING','allocation_kobo':100000,'is_frozen':True,'freeze_reason':'PROVIDER_UNKNOWN'}],'decisions':[{'id':decision_id,'lot_id':lot_id}]})
        payment=Payment(id=payment_id,transaction_id=txid,amount_kobo=100000,status='FUNDS_SECURED',provider_ref='SKPAY-UNKNOWN-CHARGE',mode='PAGA_SANDBOX')
        account=PagaSubsidiaryAccount(id=str(uuid.uuid4()),transaction_id=txid,payment_id=payment_id,account_reference='SKTXCHARGEUNKNOWN1',account_number=str(uuid.uuid4().int % 10**10).zfill(10),provider_reference='SKPAY-UNKNOWN-CHARGE',status='FUNDED')
        instruction=Instruction(id=ins_id,transaction_id=txid,instruction_type='PAYOUT',amount_kobo=100000,status='UNKNOWN',origin_id=decision_id,idempotency_key='paga-unknown-charge',provider_reference=charge_ref,provider_stage='CHARGE_UNKNOWN',provider_response={'balance_before_kobo':100000})
        session.add_all([supplier,buyer,tx,payment,account,instruction]);session.commit()
    finally:session.close()

    paga=PagaClient();date='2026-11-16 12:00:00.00';amount='1000.00';account_number=None
    session=SessionLocal()
    try:account_number=session.query(PagaSubsidiaryAccount).filter_by(transaction_id=txid).one().account_number
    finally:session.close()
    signature=paga.digest(charge_ref,date,amount,account_number)
    body={'event':'SUBSIDIARY_ACCOUNT_CHARGE_COMPLETE','notificationId':'PAGA-CHARGE-NOTIFY-'+ins_id,'statusCode':'0','externalReferenceNumber':charge_ref,'transactionType':'DEBIT','transactionDate':date,'accountReference':'SKTXCHARGEUNKNOWN1','amount':amount,'fee':0.0,'accountNumber':account_number,'hash':signature,'timestamp':'2026-11-16T12:00:01.000'}
    callback=TestClient(app).post('/api/v1/webhooks/paga/subsidiary-account',json=body)
    assert callback.status_code==200,callback.text
    session=SessionLocal()
    try:
        result=process_paga_subsidiary_operation_events(session)
        assert result['processed']==1
        instruction=session.get(Instruction,ins_id)
        assert instruction.status=='QUEUED'
        assert instruction.provider_stage=='SUBSIDIARY_CHARGED'
        assert instruction.provider_reference=='SKOUT'+ins_id.replace('-','')
    finally:session.close()


def test_paga_definitive_bank_transfer_failure_restores_funds_before_marking_failed(monkeypatch):
    monkeypatch.setenv('PAYMENT_PROVIDER','paga')
    monkeypatch.setenv('PAGA_MODE','sandbox')
    monkeypatch.setenv('PAGA_PUBLIC_KEY','sandbox-public')
    monkeypatch.setenv('PAGA_SECRET_KEY','sandbox-secret')
    monkeypatch.setenv('PAGA_HASH_KEY','sandbox-hash')
    monkeypatch.setenv('PAGA_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/subsidiary-account')
    monkeypatch.setenv('PAGA_PAYOUT_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/bank-transfer')
    monkeypatch.setenv('PAYOUT_ENCRYPTION_KEY','test-payout-encryption-key-32-bytes-minimum')

    supplier_id=str(uuid.uuid4());buyer_id=str(uuid.uuid4());txid=str(uuid.uuid4());payment_id=str(uuid.uuid4());ins_id=str(uuid.uuid4());lot_id=str(uuid.uuid4());decision_id=str(uuid.uuid4());account_number=str(uuid.uuid4().int % 10**10).zfill(10)
    session=SessionLocal()
    try:
        supplier=User(id=supplier_id,email=f'{supplier_id}@test.ng',phone=None,password_hash='x',display_name='Supplier',role='USER',verified=True,profile={},payout_account={
            'bank_uuid':'BANK-UUID','bank_code':'044','bank_name':'Access Bank',
            'account_number_enc':payout_cipher().encrypt(b'0123456789').decode(),
            'masked_account_number':'••••6789','account_name':'Supplier Name','verified':True,
        })
        buyer=User(id=buyer_id,email=f'{buyer_id}@test.ng',phone=None,password_hash='x',display_name='Buyer',role='USER',verified=True,profile={},payout_account={})
        tx=Aggregate(id=txid,reference='SK-PAGA-RESTORE-TEST',supplier_id=supplier_id,buyer_id=buyer_id,status='FUNDED',version=1,data={
            'lots':[{'id':lot_id,'item_id':'item-1','quantity':1,'item_status':'ACCEPTED_CONFIRMED','money_status':'RELEASE_PENDING','allocation_kobo':100000,'is_frozen':False}],
            'decisions':[{'id':decision_id,'lot_id':lot_id}],
        })
        payment=Payment(id=payment_id,transaction_id=txid,amount_kobo=100000,status='FUNDS_SECURED',provider_ref='SKPAY-RESTORE',mode='PAGA_SANDBOX')
        account=PagaSubsidiaryAccount(id=str(uuid.uuid4()),transaction_id=txid,payment_id=payment_id,account_reference='SKTXRESTORETEST123',account_number=account_number,provider_reference='SKPAY-RESTORE',status='FUNDED')
        instruction=Instruction(id=ins_id,transaction_id=txid,instruction_type='PAYOUT',amount_kobo=100000,status='QUEUED',origin_id=decision_id,idempotency_key='paga-restore-instruction',destination_snapshot={'bank_uuid':'BANK-UUID','account_number_enc':payout_cipher().encrypt(b'0123456789').decode(),'account_name':'Supplier Name','verified':True})
        session.add_all([supplier,buyer,tx,payment,account,instruction]);session.commit()
    finally:session.close()

    balances=iter(['1000.00','1000.00'])
    def fake_balance(self, account_reference, reference):return {'statusCode':'0','balance':next(balances)}
    def fake_charge(self, account_reference, reference, amount_kobo, narration):return {'statusCode':'0','transactionId':'CHG-RESTORE','newBalance':'0.00'}
    def fake_validate(self, reference, amount_kobo, bank_uuid, account_number):return {'responseCode':0,'fee':10,'vat':0.75}
    def fake_business_balance(self, reference):return {'responseCode':0,'availableBalance':'5000.00'}
    def fake_deposit(self, reference, amount_kobo, bank_uuid, account_number, callback_url, remarks):return {'responseCode':116,'message':'Invalid destination'}
    def fake_topup(self, account_reference, reference, amount_kobo, narration):return {'statusCode':'0','transactionId':'TOPUP-RESTORE'}
    monkeypatch.setattr(PagaClient,'get_subsidiary_balance',fake_balance)
    monkeypatch.setattr(PagaClient,'charge_subsidiary_account',fake_charge)
    monkeypatch.setattr(PagaClient,'validate_bank_account',fake_validate)
    monkeypatch.setattr(PagaClient,'get_business_balance',fake_business_balance)
    monkeypatch.setattr(PagaClient,'deposit_to_bank',fake_deposit)
    monkeypatch.setattr(PagaClient,'top_up_subsidiary_account',fake_topup)

    session=SessionLocal()
    try:
        result=run_paga_financial(session)
        instruction=session.get(Instruction,ins_id)
        assert result['processed'][0]['status']=='FAILED'
        assert instruction.status=='FAILED'
        assert instruction.provider_stage=='FUNDS_RESTORED'
        assert instruction.provider_reference=='SKBACK'+ins_id.replace('-','')
    finally:session.close()


def test_daily_paga_statement_reconciliation_resolves_settled_unknown_transfer(monkeypatch):
    monkeypatch.setenv('PAYMENT_PROVIDER','paga')
    monkeypatch.setenv('PAGA_MODE','sandbox')
    monkeypatch.setenv('PAGA_PUBLIC_KEY','sandbox-public')
    monkeypatch.setenv('PAGA_SECRET_KEY','sandbox-secret')
    monkeypatch.setenv('PAGA_HASH_KEY','sandbox-hash')
    monkeypatch.setenv('PAGA_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/subsidiary-account')
    monkeypatch.setenv('PAGA_PAYOUT_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/bank-transfer')

    from app.main import run_paga_statement_reconciliation

    supplier_id=str(uuid.uuid4());buyer_id=str(uuid.uuid4());txid=str(uuid.uuid4());payment_id=str(uuid.uuid4());ins_id=str(uuid.uuid4());lot_id=str(uuid.uuid4());decision_id=str(uuid.uuid4());account_number=str(uuid.uuid4().int % 10**10).zfill(10)
    transfer_ref='SKOUT'+ins_id.replace('-','')
    session=SessionLocal()
    try:
        supplier=User(id=supplier_id,email=f'{supplier_id}@test.ng',phone=None,password_hash='x',display_name='Supplier',role='USER',verified=True,profile={},payout_account={})
        buyer=User(id=buyer_id,email=f'{buyer_id}@test.ng',phone=None,password_hash='x',display_name='Buyer',role='USER',verified=True,profile={},payout_account={})
        tx=Aggregate(id=txid,reference='SK-PAGA-RECON-TEST',supplier_id=supplier_id,buyer_id=buyer_id,status='FUNDED',version=1,data={
            'lots':[{'id':lot_id,'item_id':'item-1','quantity':1,'item_status':'ACCEPTED_CONFIRMED','money_status':'RELEASE_PENDING','allocation_kobo':100000,'is_frozen':True,'freeze_reason':'PROVIDER_UNKNOWN'}],
            'decisions':[{'id':decision_id,'lot_id':lot_id}],
        })
        payment=Payment(id=payment_id,transaction_id=txid,amount_kobo=100000,status='FUNDS_SECURED',provider_ref='SKPAY-RECON',mode='PAGA_SANDBOX')
        account=PagaSubsidiaryAccount(id=str(uuid.uuid4()),transaction_id=txid,payment_id=payment_id,account_reference='SKTXRECONTEST1234',account_number=account_number,provider_reference='SKPAY-RECON',status='FUNDED')
        instruction=Instruction(id=ins_id,transaction_id=txid,instruction_type='PAYOUT',amount_kobo=100000,status='UNKNOWN',origin_id=decision_id,idempotency_key='paga-recon-unknown',provider_reference=transfer_ref,provider_stage='TRANSFER_UNKNOWN')
        session.add_all([supplier,buyer,tx,payment,account,instruction]);session.commit()
    finally:session.close()

    monkeypatch.setattr(PagaClient,'get_business_transactions',lambda self,reference,start,end:{'responseCode':0,'items':[{'referenceNumber':transfer_ref,'status':'SUCCESSFUL','amount':1000.0,'transactionType':'USER_SEND_CASH_TO_BANK_ACCOUNT_SETTLED'}]})
    monkeypatch.setattr(PagaClient,'get_subsidiary_balance',lambda self,account_reference,reference:{'statusCode':'0','balance':'0.00'})
    monkeypatch.setattr(PagaClient,'get_subsidiary_history',lambda self,account_reference,reference,start,end,page_size,page_number:{'statusCode':'0','totalPages':1,'transactions':[{'transactionType':'DEBIT','amount':1000.0}]})
    session=SessionLocal()
    try:
        summary=run_paga_statement_reconciliation(session,PagaClient())
        assert summary['history_resolved_instructions']==1
        instruction=session.get(Instruction,ins_id)
        assert instruction.status=='SUCCEEDED'
        assert instruction.provider_stage=='SETTLED'
    finally:session.close()


def test_paga_dispute_refund_uses_buyers_verified_destination_and_waits_for_provider_confirmation(monkeypatch):
    monkeypatch.setenv('PAYMENT_PROVIDER','paga')
    monkeypatch.setenv('PAGA_MODE','sandbox')
    monkeypatch.setenv('PAGA_PUBLIC_KEY','sandbox-public')
    monkeypatch.setenv('PAGA_SECRET_KEY','sandbox-secret')
    monkeypatch.setenv('PAGA_HASH_KEY','sandbox-hash')
    monkeypatch.setenv('PAGA_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/subsidiary-account')
    monkeypatch.setenv('PAGA_PAYOUT_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/bank-transfer')
    monkeypatch.setenv('PAYOUT_ENCRYPTION_KEY','test-payout-encryption-key-32-bytes-minimum')
    supplier_id=str(uuid.uuid4());buyer_id=str(uuid.uuid4());txid=str(uuid.uuid4());payment_id=str(uuid.uuid4());ins_id=str(uuid.uuid4());lot_id=str(uuid.uuid4());decision_id=str(uuid.uuid4())
    session=SessionLocal()
    try:
        supplier=User(id=supplier_id,email=f'{supplier_id}@test.ng',phone=None,password_hash='x',display_name='Supplier',role='USER',verified=True,profile={},payout_account={})
        buyer=User(id=buyer_id,email=f'{buyer_id}@test.ng',phone=None,password_hash='x',display_name='Buyer',role='USER',verified=True,profile={},payout_account={})
        tx=Aggregate(id=txid,reference='SK-PAGA-REFUND-TEST',supplier_id=supplier_id,buyer_id=buyer_id,status='SETTLEMENT_PENDING',version=1,data={
            'items':[{'id':'item-1','quantity':1,'unit_price_kobo':100000,'line_total_kobo':100000}],
            'lots':[{'id':lot_id,'item_id':'item-1','quantity':1,'item_status':'RETURN_RECEIPT_CONFIRMED','money_status':'REFUND_PENDING','allocation_kobo':100000,'is_frozen':False}],
            'returns':[{'id':'return-1','status':'RECEIPT_CONFIRMED','supplier_receipt_confirmed_at':'2026-11-19T00:00:00Z'}],
                'dispute':{'id':'dispute-1','status':'RETURN_RECEIPT_CONFIRMED','return_required':True}
        })
        payment=Payment(id=payment_id,transaction_id=txid,amount_kobo=100000,status='FUNDS_SECURED',provider_ref='SKPAY-REFUND',mode='PAGA_SANDBOX')
        account=PagaSubsidiaryAccount(id=str(uuid.uuid4()),transaction_id=txid,payment_id=payment_id,account_reference='SKTXPAGAREFUND123',account_number='0123456789',provider_reference='SKPAY-REFUND',status='FUNDED')
        destination={'bank_uuid':'BUYER-BANK-UUID','account_number_enc':payout_cipher().encrypt(b'9876543210').decode(),'account_name':'Buyer Refund Name','masked_account_number':'••••3210','account_version':'version-1','destination_fingerprint':'fingerprint-1'}
        instruction=Instruction(id=ins_id,transaction_id=txid,instruction_type='REFUND',refund_classification='DISPUTE_RETURN',amount_kobo=100000,status='QUEUED',origin_id='return-1',deciding_moderator_id='moderator-1',idempotency_key='paga-refund-test',destination_snapshot=destination)
        session.add_all([supplier,buyer,tx,payment,account,instruction]);session.commit()
    finally:session.close()
    calls=[]
    monkeypatch.setattr(PagaClient,'get_subsidiary_balance',lambda self,account_reference,reference:{'statusCode':'0','balance':'1000.00'})
    monkeypatch.setattr(PagaClient,'charge_subsidiary_account',lambda self,account_reference,reference,amount_kobo,narration:{'statusCode':'0','transactionId':'CHG-REFUND','newBalance':'0.00'})
    def validate(self,reference,amount_kobo,bank_uuid,account_number):
        assert bank_uuid=='BUYER-BANK-UUID' and account_number=='9876543210'
        return {'responseCode':0,'destinationAccountHolderNameAtBank':'Buyer Refund Name','fee':10,'vat':0.75}
    monkeypatch.setattr(PagaClient,'validate_bank_account',validate)
    monkeypatch.setattr(PagaClient,'get_business_balance',lambda self,reference:{'responseCode':0,'availableBalance':'5000.00'})
    def deposit(self,reference,amount_kobo,bank_uuid,account_number,callback_url,remarks):
        calls.append((amount_kobo,bank_uuid,account_number,remarks))
        return {'responseCode':0,'transactionId':'RP-REFUND-1'}
    monkeypatch.setattr(PagaClient,'deposit_to_bank',deposit)
    session=SessionLocal()
    try:
        result=run_paga_financial(session)
        assert next(x for x in result['processed'] if x['id']==ins_id)['status']=='SUBMITTED'
        saved=session.get(Instruction,ins_id)
        assert saved.status=='SUBMITTED' and saved.provider_stage=='TRANSFER_SUBMITTED'
        assert calls==[(100000,'BUYER-BANK-UUID','9876543210',f'SupplyKey refund {ins_id[:8]}')]
    finally:session.close()
