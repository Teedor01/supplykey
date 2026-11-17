"""Paga Engine adapter.

Implements the documented Subsidiary Accounts (ledger-backed) collection flow and
Business API bank transfer primitives. Live operation is gated separately by the
application because provider API availability is not equivalent to escrow approval.
"""
from __future__ import annotations

# Also load backend/.env when this adapter is imported independently.
from .. import config as _config

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


class PagaError(Exception):
    """A definitive provider/API/configuration error."""


class PagaUnknownOutcome(PagaError):
    """The request may have reached Paga; resolve using the original reference."""


@dataclass(frozen=True)
class PagaConfig:
    mode: str
    public_key: str
    secret_key: str
    hash_key: str
    callback_url: str
    payout_callback_url: str
    business_name: str
    timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "PagaConfig":
        mode = os.getenv("PAGA_MODE", "disabled").strip().lower()
        if mode not in {"disabled", "sandbox", "live"}:
            raise PagaError("PAGA_MODE must be disabled, sandbox or live.")
        cfg = cls(
            mode=mode,
            public_key=os.getenv("PAGA_PUBLIC_KEY", "").strip(),
            secret_key=os.getenv("PAGA_SECRET_KEY", "").strip(),
            hash_key=os.getenv("PAGA_HASH_KEY", "").strip(),
            callback_url=os.getenv("PAGA_CALLBACK_URL", "").strip(),
            payout_callback_url=os.getenv("PAGA_PAYOUT_CALLBACK_URL", "").strip(),
            business_name=os.getenv("PAGA_BUSINESS_NAME", "SupplyKey").strip(),
            timeout_seconds=float(os.getenv("PAGA_TIMEOUT_SECONDS", "15")),
        )
        if mode == "live" and (os.getenv("PAGA_CONDITIONAL_CUSTODY_APPROVED", "false").lower() != "true" or not os.getenv("PAGA_LEGAL_APPROVAL_REFERENCE", "").strip()):
            raise PagaError("Paga live mode is blocked until written conditional-custody approval and Nigerian legal approval are recorded.")
        if mode != "disabled":
            missing = [name for name, value in (("PAGA_PUBLIC_KEY", cfg.public_key), ("PAGA_SECRET_KEY", cfg.secret_key), ("PAGA_HASH_KEY", cfg.hash_key), ("PAGA_CALLBACK_URL", cfg.callback_url)) if not value]
            if missing:
                raise PagaError("Missing Paga configuration: " + ", ".join(missing))
            parsed = urllib.parse.urlparse(cfg.callback_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise PagaError("PAGA_CALLBACK_URL must be a publicly reachable HTTPS URL.")
            if cfg.payout_callback_url:
                payout_parsed = urllib.parse.urlparse(cfg.payout_callback_url)
                if payout_parsed.scheme != "https" or not payout_parsed.netloc:
                    raise PagaError("PAGA_PAYOUT_CALLBACK_URL must be a publicly reachable HTTPS URL.")
        return cfg

    @property
    def collect_base(self) -> str:
        return "https://collect.paga.com" if self.mode == "live" else "https://beta-collect.paga.com"

    @property
    def business_base(self) -> str:
        return "https://www.mypaga.com" if self.mode == "live" else "https://beta.mypaga.com"

    @property
    def provider_mode(self) -> str:
        return "PAGA_LIVE" if self.mode == "live" else "PAGA_SANDBOX"


class PagaClient:
    def __init__(self, config: PagaConfig | None = None):
        self.config = config or PagaConfig.from_env()
        if self.config.mode == "disabled":
            raise PagaError("Paga integration is disabled. Set PAGA_MODE=sandbox to enable it.")

    def digest(self, *parts: Any) -> str:
        message = "".join("" if value is None else str(value) for value in parts) + self.config.hash_key
        return hashlib.sha512(message.encode("utf-8")).hexdigest()

    def _request(self, method: str, base: str, path: str, payload: dict | None = None, *, hash_value: str, query: dict | None = None, business_api: bool = False) -> dict:
        url = base.rstrip("/") + "/" + path.lstrip("/")
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = json.dumps(payload or {}, separators=(",", ":")).encode("utf-8") if method != "GET" else None
        if business_api:
            headers = {
                "principal": self.config.public_key,
                "credentials": self.config.secret_key,
                "hash": hash_value,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        else:
            basic = base64.b64encode(f"{self.config.public_key}:{self.config.secret_key}".encode()).decode()
            headers = {"Authorization": f"Basic {basic}", "hash": hash_value, "Content-Type": "application/json", "Accept": "application/json"}
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                raw = response.read()
                result = json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                detail = json.loads(raw.decode("utf-8"))
            except Exception:
                detail = {"message": raw.decode("utf-8", "replace")[:500]}
            # A 5xx may occur after Paga processed the operation.
            if exc.code >= 500:
                raise PagaUnknownOutcome(f"Paga returned HTTP {exc.code}; query the original reference.") from exc
            raise PagaError(f"Paga rejected the request (HTTP {exc.code}): {detail}") from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise PagaUnknownOutcome("Paga request outcome is unknown; query the original reference before retrying.") from exc
        if not isinstance(result, dict):
            raise PagaUnknownOutcome("Paga returned an unexpected response; reconcile the original reference.")
        return result

    @staticmethod
    def amount_naira(amount_kobo: int) -> str:
        if not isinstance(amount_kobo, int) or amount_kobo <= 0:
            raise PagaError("Amount must be a positive integer number of kobo.")
        return str((Decimal(amount_kobo) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def create_transaction_account(self, *, reference: str, account_reference: str, account_name: str, first_name: str, last_name: str, phone: str | None, email: str | None, amount_kobo: int) -> dict:
        if not (phone or email):
            raise PagaError("Paga requires a buyer phone number or email address.")
        if not 11 <= len(account_reference) <= 30:
            raise PagaError("Paga accountReference must contain 11 to 30 characters.")
        callback = self.config.callback_url
        payload: dict[str, Any] = {
            "referenceNumber": reference,
            "accountReference": account_reference,
            "accountName": account_name[:100],
            "firstName": first_name[:50],
            "lastName": last_name[:50],
            "status": "ACTIVE",
            "fundingTransactionLimit": float(self.amount_naira(amount_kobo)),
            "fundingDailyLimit": float(self.amount_naira(amount_kobo)),
            "callbackUrl": callback,
        }
        if phone:
            payload["phoneNumber"] = phone
        if email:
            payload["email"] = email
        # Subsidiary account creation hash order is fixed by Paga's documentation.
        hash_value = self.digest(reference, account_reference, "", "", "", "", callback)
        result = self._request("POST", self.config.collect_base, "/subsidiary-accounts", payload, hash_value=hash_value)
        if str(result.get("statusCode")) != "0" or not result.get("accountNumber"):
            code = str(result.get("statusCode", "UNKNOWN"))
            if code == "102":
                raise PagaUnknownOutcome("Paga returned a duplicate account reference. Retrieve the existing account before proceeding.")
            raise PagaError(f"Paga account creation was not confirmed: {result.get('statusMessage', code)}")
        return result

    def get_subsidiary_account(self, account_reference: str, reference: str) -> dict:
        path = f"/subsidiary-accounts/{urllib.parse.quote(account_reference, safe='')}"
        digest = self.digest(reference, account_reference)
        return self._request("GET", self.config.collect_base, path, hash_value=digest, query={"referenceNumber": reference})

    def get_subsidiary_balance(self, account_reference: str, reference: str) -> dict:
        path = f"/subsidiary-accounts/{urllib.parse.quote(account_reference, safe='')}/balance"
        digest = self.digest(reference, account_reference)
        return self._request("GET", self.config.collect_base, path, hash_value=digest, query={"referenceNumber": reference})

    def charge_subsidiary_account(self, account_reference: str, reference: str, amount_kobo: int, narration: str) -> dict:
        amount = self.amount_naira(amount_kobo)
        payload = {"referenceNumber": reference, "amount": float(amount), "currency": "NGN", "narration": narration[:120]}
        digest = self.digest(reference, amount, "NGN", narration[:120])
        result = self._request("POST", self.config.collect_base, f"/subsidiary-accounts/{urllib.parse.quote(account_reference, safe='')}/charge", payload, hash_value=digest)
        if str(result.get("statusCode")) != "0":
            code = str(result.get("statusCode", "UNKNOWN"))
            if code in {"102", "-1", "105", "135"}:
                raise PagaUnknownOutcome(f"Paga charge outcome {code}; reconcile the original reference.")
            raise PagaError(f"Paga charge was not confirmed: {result.get('statusMessage', code)}")
        return result

    def get_business_balance(self, reference: str) -> dict:
        return self._business_post("accountBalance", {"referenceNumber": reference}, [reference])

    def top_up_subsidiary_account(self, account_reference: str, reference: str, amount_kobo: int, narration: str) -> dict:
        amount = self.amount_naira(amount_kobo)
        payload = {"referenceNumber": reference, "amount": float(amount), "currency": "NGN", "narration": narration[:120]}
        digest = self.digest(reference, amount, "NGN", narration[:120])
        result = self._request("POST", self.config.collect_base, f"/subsidiary-accounts/{urllib.parse.quote(account_reference, safe='')}/top-up", payload, hash_value=digest)
        if str(result.get("statusCode")) != "0":
            code = str(result.get("statusCode", "UNKNOWN"))
            if code in {"102", "-1", "105", "135"}:
                raise PagaUnknownOutcome(f"Paga top-up outcome {code}; reconcile the original reference.")
            raise PagaError(f"Paga top-up was not confirmed: {result.get('statusMessage', code)}")
        return result

    def get_banks(self) -> dict:
        reference = "SKBANK" + __import__("secrets").token_hex(12).upper()
        return self._business_post("getBanks", {"referenceNumber": reference}, [reference])

    def validate_bank_account(self, reference: str, amount_kobo: int, bank_uuid: str, account_number: str) -> dict:
        amount = self.amount_naira(amount_kobo)
        payload = {"referenceNumber": reference, "amount": amount, "currency": "NGN", "destinationBankUUID": bank_uuid, "destinationBankAccountNumber": account_number}
        return self._business_post("validateDepositToBank", payload, [reference, amount, bank_uuid, account_number])

    def deposit_to_bank(self, reference: str, amount_kobo: int, bank_uuid: str, account_number: str, callback_url: str, remarks: str) -> dict:
        amount = self.amount_naira(amount_kobo)
        payload = {"referenceNumber": reference, "amount": amount, "currency": "NGN", "destinationBankUUID": bank_uuid, "destinationBankAccountNumber": account_number, "statusCallbackUrl": callback_url, "remarks": remarks[:120]}
        return self._business_post("depositToBank", payload, [reference, amount, bank_uuid, account_number])

    def get_business_transactions(self, reference: str, start_date_utc: str, end_date_utc: str) -> dict:
        payload = {"referenceNumber": reference, "startDateUTC": start_date_utc, "endDateUTC": end_date_utc}
        return self._business_post("transactionHistory", payload, [reference])

    def get_subsidiary_history(self, account_reference: str, reference: str, start_date_utc: str, end_date_utc: str, page_size: int = 20, page_number: int = 0) -> dict:
        path = f"/subsidiary-accounts/{urllib.parse.quote(account_reference, safe='')}/history"
        query = {"referenceNumber": reference, "startDateTimeUTC": start_date_utc, "endDateTimeUTC": end_date_utc, "pageSize": page_size, "pageNumber": page_number}
        digest = self.digest(account_reference, start_date_utc, end_date_utc)
        return self._request("GET", self.config.collect_base, path, hash_value=digest, query=query)

    def transaction_status(self, reference: str) -> dict:
        return self._business_post("transactionStatus", {"referenceNumber": reference, "locale": "en"}, [reference])

    def _business_post(self, operation: str, payload: dict, hash_fields: list[str]) -> dict:
        return self._request("POST", self.config.business_base, f"/paga-webservices/business-rest/secured/{operation}", payload, hash_value=self.digest(*hash_fields), business_api=True)

    def verify_subsidiary_funding_webhook(self, body: dict, signature: str | None, parameters: str | None = None, headers: Any = None) -> bool:
        if not signature:
            return False
        # Paga supplies the hash-field order in x-paga-hash-v3-parameters.
        # Resolve each field from the body first, then the HTTP headers (timestamp
        # may be supplied separately). Never silently omit a named hash field.
        if parameters:
            parts=[]
            header_map={str(k).lower():v for k,v in (headers.items() if headers else [])}
            for name in [x.strip() for x in parameters.split(',') if x.strip()]:
                if name.lower() in {'hashkey','hash_key'}:
                    continue
                if name in body:
                    parts.append(body.get(name));continue
                header_value=header_map.get(name.lower())
                if header_value is None and name.lower()=='timestamp':
                    header_value=header_map.get('x-paga-timestamp')
                if header_value is None:
                    return False
                parts.append(header_value)
            expected=self.digest(*parts)
        else:
            # Fixed order from the current Paga reference, retained for callers
            # that do not expose the parameter header.
            expected = self.digest(body.get("transactionReference"), body.get("transactionDate"), body.get("amount"), body.get("accountNumber"), body.get("timestamp"))
        return hmac.compare_digest(expected.lower(), signature.strip().lower())

    def verify_subsidiary_operation_webhook(self, body: dict, signature: str | None) -> bool:
        if not signature:
            return False
        # Paga subsidiary charge/top-up/transfer callback: preserve the amount string exactly.
        expected = self.digest(body.get("externalReferenceNumber"), body.get("transactionDate"), body.get("amount"), body.get("accountNumber"))
        return hmac.compare_digest(expected.lower(), signature.strip().lower())

    def verify_bank_transfer_webhook(self, body: dict, signature: str | None, parameters: str | None = None, headers: Any = None) -> bool:
        if not signature:
            return False
        if parameters:
            parts=[];header_map={str(k).lower():v for k,v in (headers.items() if headers else [])}
            for name in [x.strip() for x in parameters.split(',') if x.strip()]:
                if name.lower() in {'hashkey','hash_key'}:continue
                if name in body:parts.append(body.get(name));continue
                value=header_map.get(name.lower())
                if value is None:return False
                parts.append(value)
            expected=self.digest(*parts)
        else:
            expected = self.digest(body.get("referenceNumber"), body.get("transactionDate"), body.get("transactionId"), body.get("amount"), body.get("sessionId"))
        return hmac.compare_digest(expected.lower(), signature.strip().lower())
