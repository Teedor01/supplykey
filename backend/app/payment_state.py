"""Explicit payment state-transition model.

Rules come from the documented payment state machine
(supplykey-docs/03-technical-specs/TRANSACTION_STATE_MACHINE.md, rows P1-P9):

* P2/P3  PENDING -> PAYMENT_RECEIVED -> FUNDS_SECURED (a provider that verifies
         collection and hold in one step applies P2 first, so PENDING ->
         FUNDS_SECURED is legal and posts both journals).
* P4     PENDING -> FAILED on an explicit provider failure.
* P6/P7  PENDING|PAYMENT_RECEIVED -> UNKNOWN on an ambiguous outcome; UNKNOWN
         resolves to PAYMENT_RECEIVED, FUNDS_SECURED or FAILED only on a
         definitive provider answer.
* P8     A duplicate (provider, event_id) leaves everything unchanged.
* P9     A second success for an already secured payment never moves state.
* P7 note: a late success after failure/cancellation is an exception that needs
         manual handling; it must never silently fund the transaction.

FUNDS_SECURED is a completed financial state and is never left by a callback.
FAILED is likewise terminal for callbacks. The model is pure: it decides, the
caller applies (atomically, with the ledger entries).
"""
from dataclasses import dataclass

PENDING = 'PENDING'
PAYMENT_RECEIVED = 'PAYMENT_RECEIVED'
FUNDS_SECURED = 'FUNDS_SECURED'
UNKNOWN = 'UNKNOWN'
FAILED = 'FAILED'
EXPIRED = 'EXPIRED'

PAYMENT_STATES = (PENDING, PAYMENT_RECEIVED, FUNDS_SECURED, UNKNOWN, FAILED, EXPIRED)
EVENT_STATUSES = (PAYMENT_RECEIVED, FUNDS_SECURED, FAILED, UNKNOWN)
# Events that assert money moved. These must carry a verified amount.
MONEY_EVENTS = (PAYMENT_RECEIVED, FUNDS_SECURED)
# Payment states that count as an attempt in progress (blocks a second attempt).
ACTIVE_STATES = (PENDING, PAYMENT_RECEIVED, UNKNOWN, FUNDS_SECURED)

APPLY, NOOP, REJECT = 'APPLY', 'NOOP', 'REJECT'


@dataclass(frozen=True)
class Decision:
    kind: str          # APPLY | NOOP | REJECT
    new_status: str    # status after the decision (unchanged for NOOP/REJECT)
    reason: str        # machine-readable reason code


def _d(kind, status, reason):
    return Decision(kind, status, reason)


def evaluate_transition(current: str, event_status: str) -> Decision:
    """Decide what a validated provider event means for a payment in ``current``.

    Never raises for known states; unknown inputs are rejected so a new event
    type cannot bypass validation.
    """
    if current not in PAYMENT_STATES:
        return _d(REJECT, current, 'UNKNOWN_CURRENT_STATE')
    if event_status not in EVENT_STATUSES:
        return _d(REJECT, current, 'UNSUPPORTED_EVENT_STATUS')

    if current == PENDING:
        return _d(APPLY, event_status, 'FORWARD_TRANSITION')

    if current == PAYMENT_RECEIVED:
        if event_status == PAYMENT_RECEIVED:
            return _d(NOOP, current, 'ALREADY_IN_STATE')
        if event_status == FUNDS_SECURED:
            return _d(APPLY, FUNDS_SECURED, 'FORWARD_TRANSITION')
        if event_status == UNKNOWN:
            return _d(APPLY, UNKNOWN, 'AMBIGUOUS_OUTCOME')
        # FAILED after the collection was verified is contradictory: keep the
        # received state and flag for review rather than silently failing money.
        return _d(REJECT, current, 'FAILURE_AFTER_PAYMENT_RECEIVED')

    if current == UNKNOWN:
        if event_status == UNKNOWN:
            return _d(NOOP, current, 'ALREADY_IN_STATE')
        return _d(APPLY, event_status, 'RECONCILED')

    if current == FUNDS_SECURED:
        if event_status in (PAYMENT_RECEIVED, FUNDS_SECURED, UNKNOWN):
            # Older, repeated or ambiguous events cannot regress a completed state.
            return _d(NOOP, current, 'STALE_OR_REPEATED_AFTER_SECURED')
        return _d(REJECT, current, 'FAILURE_AFTER_FUNDS_SECURED')

    # FAILED / EXPIRED
    if event_status in (FAILED, UNKNOWN):
        return _d(NOOP, current, 'ALREADY_TERMINAL')
    return _d(REJECT, current, 'LATE_SUCCESS_AFTER_FAILURE')
