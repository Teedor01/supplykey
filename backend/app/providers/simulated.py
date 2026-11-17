"""SupplyKey local payment simulator (``SimulatedPaymentProvider``).

This is a *provider*, not a test shortcut. It plays the role the payment
provider would play: it builds provider callbacks for a payment *using the
amount stored on the server*, signs them with a server-side secret, and hands
them to the same validated ingestion path real webhooks use. The browser never
sees a signing secret and can never declare a payment ``FUNDS_SECURED``; it can
only ask the backend to run a named scenario for a payment the caller owns.

It is separate from Paga. Everything it produces is labelled ``SIMULATED`` and
is never a Paga settlement.

Scenarios
---------
SUCCESS    PAYMENT_RECEIVED then FUNDS_SECURED (two events, exact stored amount).
FAILURE    Explicit provider failure. No ledger entries.
PENDING    Nothing happens; the payment stays PENDING.
DELAYED    Nothing now; the worker (or a status refresh) delivers SUCCESS once
           ``SIMULATED_DELAY_SECONDS`` have elapsed.
DUPLICATE  SUCCESS plus a replay of the same event id and a late, stale event
           with a new id. Proves idempotency and monotonic state.
TIMEOUT    The provider "times out": ambiguous outcome, payment becomes UNKNOWN.
UNKNOWN    The provider returns an unrecognised/ambiguous result: UNKNOWN.
"""
import os

SCENARIOS = ('SUCCESS', 'FAILURE', 'PENDING', 'DELAYED', 'DUPLICATE', 'TIMEOUT', 'UNKNOWN')
_ALIASES = {'FAILED': 'FAILURE', 'FAIL': 'FAILURE', 'OK': 'SUCCESS', 'SUCCEEDED': 'SUCCESS'}

SIMULATION_LABEL = 'SupplyKey payment simulation — no real money moves and this is not a Paga payment.'
MODE = 'SIMULATED'
PROVIDER_NAME = 'simulated'

# Environments in which the simulator may run. "demo" additionally requires an
# explicit opt-in so it can never switch on by accident. "production" is never
# allowed, and unknown environment names are not trusted.
_ALWAYS_ALLOWED = ('development', 'test', 'testing', 'local')


def current_env() -> str:
    return os.getenv('APP_ENV', 'development').strip().lower()


def simulator_allowed() -> bool:
    env = current_env()
    if env == 'production':
        return False
    if env in _ALWAYS_ALLOWED:
        return True
    if env == 'demo':
        return os.getenv('ENABLE_PAYMENT_SIMULATOR', '').strip().lower() == 'true'
    return False


def normalize_scenario(value, default=None):
    if value is None or str(value).strip() == '':
        return default
    v = str(value).strip().upper()
    v = _ALIASES.get(v, v)
    return v if v in SCENARIOS else None


def configured_scenario() -> str:
    """Scenario named by SIMULATED_PAYMENT_SCENARIO. Invalid values are an error, not a silent default."""
    raw = os.getenv('SIMULATED_PAYMENT_SCENARIO', 'PENDING')
    scenario = normalize_scenario(raw, 'PENDING')
    if scenario is None:
        raise ValueError(f'SIMULATED_PAYMENT_SCENARIO={raw!r} is not one of {", ".join(SCENARIOS)}.')
    return scenario


def delay_seconds() -> int:
    try:
        return max(1, min(3600, int(os.getenv('SIMULATED_DELAY_SECONDS', '20'))))
    except ValueError:
        return 20


def signing_secret() -> bytes:
    """Server-side signing key. Never returned by any API."""
    return (os.getenv('SIMULATOR_WEBHOOK_SECRET') or os.getenv('SUPPLYKEY_SECRET', 'dev-only-change-this-secret-before-deploy')).encode()


def _event(payment_id, scenario, n, status, provider_ref, amount_kobo, tx_reference, detail=None):
    e = {'event_id': f'sim:{payment_id}:{scenario}:{n}', 'provider_ref': provider_ref, 'status': status,
         'amount_kobo': amount_kobo, 'currency': 'NGN', 'transaction_reference': tx_reference, 'simulated': True}
    if detail:
        e['detail'] = detail
    return e


def build_events(scenario, payment_id, provider_ref, amount_kobo, tx_reference):
    """Provider callbacks for a scenario. Amount/reference always come from the stored payment.

    Event ids are deterministic per (payment, scenario, step) so a double click
    or retry produces replays, which ingestion treats as idempotent duplicates.
    """
    def ev(n, status, detail=None):
        return _event(payment_id, scenario, n, status, provider_ref, amount_kobo, tx_reference, detail)

    if scenario in ('SUCCESS', 'DELAYED'):
        return [ev(1, 'PAYMENT_RECEIVED'), ev(2, 'FUNDS_SECURED')]
    if scenario == 'FAILURE':
        return [ev(1, 'FAILED', 'SIMULATED_PROVIDER_DECLINE')]
    if scenario == 'DUPLICATE':
        return [ev(1, 'PAYMENT_RECEIVED'), ev(2, 'FUNDS_SECURED'),
                ev(2, 'FUNDS_SECURED'),                 # exact replay of the same event id
                ev(3, 'PAYMENT_RECEIVED', 'LATE_STALE'),  # older state, new event id
                ev(4, 'FUNDS_SECURED', 'REPEATED')]       # repeated success, new event id
    if scenario == 'TIMEOUT':
        return [ev(1, 'UNKNOWN', 'SIMULATED_PROVIDER_TIMEOUT')]
    if scenario == 'UNKNOWN':
        return [ev(1, 'UNKNOWN', 'SIMULATED_AMBIGUOUS_RESULT')]
    return []  # PENDING
