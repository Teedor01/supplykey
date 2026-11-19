import hashlib
import json
import urllib.error
from unittest.mock import patch

import pytest

from app.providers.paga import PagaClient, PagaConfig, PagaError, PagaUnknownOutcome


def client():
    return PagaClient(PagaConfig(
        mode='sandbox', public_key='public-test', secret_key='secret-test',
        hash_key='hash-test', callback_url='https://api.example.test/api/v1/webhooks/paga/subsidiary-account',
        payout_callback_url='https://api.example.test/api/v1/webhooks/paga/bank-transfer',
        business_name='SupplyKey', timeout_seconds=2,
    ))


def test_sha512_hash_uses_concatenated_fields_and_hash_key():
    paga = client()
    expected = hashlib.sha512(b'REF123ACCOUNT1234567890hash-test').hexdigest()
    assert paga.digest('REF123', 'ACCOUNT1234567890') == expected


def test_kobo_conversion_is_exact_and_rejects_nonpositive_amounts():
    assert client().amount_naira(123456) == '1234.56'
    with pytest.raises(PagaError):
        client().amount_naira(0)


def test_subsidiary_account_creation_uses_ledger_backed_account_and_expected_hash():
    paga = client()
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps({'statusCode': '0', 'accountNumber': '0750529406', 'accountReference': 'SKTX1234567890123456'}).encode()

    def fake_urlopen(request, timeout):
        captured['request'] = request
        captured['timeout'] = timeout
        return Response()

    with patch('urllib.request.urlopen', fake_urlopen):
        result = paga.create_transaction_account(
            reference='SKPAY1234567890123456', account_reference='SKTX1234567890123456',
            account_name='Test Buyer', first_name='Test', last_name='Buyer',
            phone='+2348012345678', email='buyer@example.test', amount_kobo=250000,
        )

    req = captured['request']
    payload = json.loads(req.data)
    assert req.full_url == 'https://beta-collect.paga.com/subsidiary-accounts'
    assert req.get_header('Hash') == paga.digest('SKPAY1234567890123456', 'SKTX1234567890123456', '', '', '', '', paga.config.callback_url)
    assert 'autoSweep' not in payload  # omission selects a ledger-backed account
    assert payload['fundingTransactionLimit'] == 2500.0
    assert payload['fundingDailyLimit'] == 2500.0
    assert result['accountNumber'] == '0750529406'


def test_funding_webhook_signature_matches_paga_documented_field_order():
    paga = client()
    body = {
        'transactionReference': 'TXREF-1',
        'transactionDate': '2026-11-16T12:00:00.000',
        'amount': '2500.00',
        'accountNumber': '0750529406',
        'timestamp': '2026-11-16T12:00:01.000',
    }
    signature = paga.digest(body['transactionReference'], body['transactionDate'], body['amount'], body['accountNumber'], body['timestamp'])
    assert paga.verify_subsidiary_funding_webhook(body, signature)
    assert not paga.verify_subsidiary_funding_webhook(body, '0' * 128)


def test_network_timeout_is_unknown_not_a_definitive_failure():
    paga = client()
    with patch('urllib.request.urlopen', side_effect=TimeoutError('timed out')):
        with pytest.raises(PagaUnknownOutcome):
            paga.create_transaction_account(
                reference='SKPAY1234567890123456', account_reference='SKTX1234567890123456',
                account_name='Test Buyer', first_name='Test', last_name='Buyer',
                phone='+2348012345678', email=None, amount_kobo=250000,
            )


def test_bank_transfer_webhook_signature_matches_documented_field_order():
    paga = client()
    body = {
        'referenceNumber': 'SKOUT123',
        'transactionDate': '2026-11-16 12:00:00.00',
        'transactionId': 'RP123',
        'amount': '1000.0',
        'sessionId': 'SESSION123',
    }
    signature = paga.digest(body['referenceNumber'], body['transactionDate'], body['transactionId'], body['amount'], body['sessionId'])
    assert paga.verify_bank_transfer_webhook(body, signature)
    assert not paga.verify_bank_transfer_webhook(body, '0' * 128)


def test_bank_validation_uses_paga_business_api_hash_fields():
    paga = client()
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps({'responseCode': 0, 'destinationAccountHolderNameAtBank': 'Test Buyer'}).encode()

    def fake_urlopen(request, timeout):
        captured['request'] = request
        return Response()

    with patch('urllib.request.urlopen', fake_urlopen):
        result = paga.validate_bank_account('VAL-123', 100000, 'BANK-UUID', '0123456789')
    req = captured['request']
    assert req.full_url.endswith('/paga-webservices/business-rest/secured/validateDepositToBank')
    assert req.get_header('Hash') == paga.digest('VAL-123', '1000.00', 'BANK-UUID', '0123456789')
    assert result['responseCode'] == 0


def test_subsidiary_operation_callback_signature_uses_exact_formatted_amount():
    paga = client()
    body = {
        'externalReferenceNumber': 'SKCHG123',
        'transactionDate': '2026-11-16 12:00:00.00',
        'amount': '1,000.00',
        'accountNumber': '0750529406',
    }
    signature = paga.digest(body['externalReferenceNumber'], body['transactionDate'], body['amount'], body['accountNumber'])
    assert paga.verify_subsidiary_operation_webhook(body, signature)
    assert not paga.verify_subsidiary_operation_webhook(body, '0' * 128)


def test_funding_webhook_uses_provider_supplied_hash_parameter_order_and_header_timestamp():
    paga = client()
    body = {
        'transactionReference': 'TXREF-2',
        'transactionDate': '2026-11-16T12:00:00.000',
        'amount': '1000.00',
        'accountNumber': '0750529406',
    }
    timestamp = '2026-11-16T12:00:01.000'
    signature = paga.digest(body['transactionReference'], body['transactionDate'], body['amount'], body['accountNumber'], timestamp)
    params = 'transactionReference,transactionDate,amount,accountNumber,timestamp,hashKey'
    assert paga.verify_subsidiary_funding_webhook(body, signature, params, {'x-paga-timestamp': timestamp})


def test_business_balance_uses_reference_number_hash():
    paga = client()
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps({'responseCode': 0, 'availableBalance': 2000.0}).encode()

    def fake_urlopen(request, timeout):
        captured['request'] = request
        return Response()

    with patch('urllib.request.urlopen', fake_urlopen):
        result = paga.get_business_balance('BAL-123')
    assert captured['request'].full_url.endswith('/paga-webservices/business-rest/secured/accountBalance')
    assert captured['request'].get_header('Hash') == paga.digest('BAL-123')
    assert result['availableBalance'] == 2000.0


def test_bank_transfer_callback_uses_provider_supplied_hash_parameters():
    paga = client()
    body = {
        'referenceNumber': 'PAY-42',
        'transactionDate': '2026-11-16 12:00:00.00',
        'transactionId': 'RP42',
        'amount': '5000.0',
        'sessionId': 'SESSION42',
    }
    signature = paga.digest(body['referenceNumber'], body['transactionDate'], body['transactionId'], body['amount'], body['sessionId'])
    params = 'referenceNumber,transactionDate,transactionId,amount,sessionId,hashKey'
    assert paga.verify_bank_transfer_webhook(body, signature, params, {})


def test_business_transaction_history_uses_reference_hash_and_utc_range():
    paga = client()
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps({'responseCode': 0, 'items': []}).encode()

    def fake_urlopen(request, timeout):
        captured['request'] = request
        return Response()

    with patch('urllib.request.urlopen', fake_urlopen):
        result = paga.get_business_transactions('HIS-1', '2026-11-15T00:00:00', '2026-11-16T00:00:00')
    req = captured['request']
    assert req.full_url.endswith('/paga-webservices/business-rest/secured/transactionHistory')
    assert req.get_header('Hash') == paga.digest('HIS-1')
    assert json.loads(req.data)['startDateUTC'] == '2026-11-15T00:00:00'
    assert result['responseCode'] == 0


def test_subsidiary_history_hash_excludes_reference_number():
    paga = client()
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps({'statusCode': '0', 'transactions': []}).encode()

    def fake_urlopen(request, timeout):
        captured['request'] = request
        return Response()

    with patch('urllib.request.urlopen', fake_urlopen):
        result = paga.get_subsidiary_history('SKTX1234567890', 'HIST-1', '2026-11-15T00:00:00', '2026-11-16T00:00:00')
    req = captured['request']
    assert '/subsidiary-accounts/SKTX1234567890/history?' in req.full_url
    assert req.get_header('Hash') == paga.digest('SKTX1234567890', '2026-11-15T00:00:00', '2026-11-16T00:00:00')
    assert result['statusCode'] == '0'


def test_subsidiary_top_up_uses_same_reference_amount_currency_narration_hash():
    paga = client()
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps({'statusCode': '0', 'newBalance': 1000.0}).encode()

    def fake_urlopen(request, timeout):
        captured['request'] = request
        return Response()

    with patch('urllib.request.urlopen', fake_urlopen):
        result = paga.top_up_subsidiary_account('SKTX1234567890', 'TOPUP-1', 100000, 'restore')
    req = captured['request']
    assert req.full_url.endswith('/subsidiary-accounts/SKTX1234567890/top-up')
    assert req.get_header('Hash') == paga.digest('TOPUP-1', '1000.00', 'NGN', 'restore')
    assert result['statusCode'] == '0'


def test_live_mode_is_blocked_without_written_custody_and_legal_approvals(monkeypatch):
    monkeypatch.setenv('PAGA_MODE','live')
    monkeypatch.setenv('PAGA_PUBLIC_KEY','live-public')
    monkeypatch.setenv('PAGA_SECRET_KEY','live-secret')
    monkeypatch.setenv('PAGA_HASH_KEY','live-hash')
    monkeypatch.setenv('PAGA_CALLBACK_URL','https://api.example.test/api/v1/webhooks/paga/subsidiary-account')
    monkeypatch.setenv('PAGA_CONDITIONAL_CUSTODY_APPROVED','false')
    monkeypatch.delenv('PAGA_LEGAL_APPROVAL_REFERENCE',raising=False)
    with pytest.raises(PagaError,match='live mode is blocked'):
        PagaConfig.from_env()


def test_provider_http_error_body_cannot_leak_bank_account_number_into_exception():
    paga=client()
    body=json.dumps({'responseCode':116,'message':'Invalid destination 0123456789','destinationBankAccountNumber':'0123456789'}).encode()
    error=urllib.error.HTTPError('https://beta.mypaga.com/test',400,'Bad Request',{},__import__('io').BytesIO(body))
    with patch('urllib.request.urlopen',side_effect=error):
        with pytest.raises(PagaError) as caught:
            paga.validate_bank_account('VAL-REDACTED',100000,'BANK-UUID','0123456789')
    assert '0123456789' not in str(caught.value)
