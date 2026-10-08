"""Account identity (ruling 5).

`account_id` (acct_<uuid>) is the stable identity, assigned once at creation and carried by every record. It is NOT derived
from the API key. `AccountBinding` is separate, non-secret evidence of which exchange account the keys reach (venue,
environment, base URL, PBKDF2 key digest, exchange uid when known). Position equality never proves identity.

A key rotation changes the binding of the SAME account_id and walks a typed state machine:

    CONFIRMED --observe other binding--> MISMATCH --owner says "rotation"--> ROTATION_PENDING
    CONFIRMED --owner proposes---------> ROTATION_PENDING --typed confirmation--> RECONCILING --reconciliation--> CONFIRMED

Every state except CONFIRMED keeps entries non-active (Portfolio check in `check_account_portfolio`). The environment is
part of the binding and can never change through a rotation (testnet can never become mainnet here).
"""
from __future__ import annotations

import enum
import re

from .base import Record, check_id, record, req
from .errors import InvalidRecord
from .reasons import ReasonCode

DIGEST_RE = re.compile(r'[0-9a-f]{16}')
VENUE_RE = re.compile(r'[a-z0-9][a-z0-9-]{1,31}')
# https only, no userinfo, query or fragment: a binding can never carry a credential
BASE_URL_RE = re.compile(r'https://[a-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._/-]*)?')
UID_RE = re.compile(r'[0-9A-Za-z_-]{1,64}')


class Environment(enum.StrEnum):
    BACKTEST = 'backtest'
    SIM = 'sim'
    TESTNET = 'testnet'
    MAINNET = 'mainnet'


class BindingState(enum.StrEnum):
    UNCONFIRMED = 'unconfirmed'              # first bind, waiting for the owner's typed confirmation
    CONFIRMED = 'confirmed'
    MISMATCH = 'mismatch'                    # the keys reach another binding than the confirmed one; not requested
    ROTATION_PENDING = 'rotation_pending'    # a new binding is proposed for this account; waiting for typed confirmation
    RECONCILING = 'reconciling'              # confirmed new binding; entries stay paused until a reconciliation record


BINDING_TRANSITIONS = {
    BindingState.UNCONFIRMED: frozenset({BindingState.CONFIRMED}),
    BindingState.CONFIRMED: frozenset({BindingState.MISMATCH, BindingState.ROTATION_PENDING}),
    BindingState.MISMATCH: frozenset({BindingState.CONFIRMED, BindingState.ROTATION_PENDING}),
    BindingState.ROTATION_PENDING: frozenset({BindingState.CONFIRMED, BindingState.RECONCILING}),
    BindingState.RECONCILING: frozenset({BindingState.CONFIRMED}),
}


@record
class AccountBinding(Record):
    venue: str                       # e.g. 'binance-usdm'
    environment: Environment
    base_url: str
    key_digest: str                  # 16-hex PBKDF2 digest of the API key (legacy account_fingerprint formula)
    exchange_uid: str | None = None

    def _validate(self, p):
        req(VENUE_RE.fullmatch(self.venue) is not None, p + '.venue', f'{self.venue!r}')
        req(BASE_URL_RE.fullmatch(self.base_url) is not None, p + '.base_url', 'https URL without credentials / query')
        req(DIGEST_RE.fullmatch(self.key_digest) is not None, p + '.key_digest', 'not 16 lowercase hex')
        req(self.exchange_uid is None or UID_RE.fullmatch(self.exchange_uid) is not None, p + '.exchange_uid', 'bad uid')

    def same_account_as(self, other):
        """True only when every identifying field agrees (uid compared when both sides know it)."""
        if (self.venue, self.environment, self.base_url, self.key_digest) != \
                (other.venue, other.environment, other.base_url, other.key_digest):
            return False
        return self.exchange_uid is None or other.exchange_uid is None or self.exchange_uid == other.exchange_uid


def confirmation_phrase(account_id, key_digest):
    """What the owner must type to bind `key_digest` to `account_id`."""
    return f'BIND {account_id[-8:]} {key_digest[:8]}'


@record
class BindingConfirmation(Record):
    """The owner's typed confirmation that `new_key_digest` belongs to `account_id`."""
    account_id: str
    old_key_digest: str | None       # None for the first bind
    new_key_digest: str
    typed_phrase: str
    confirmed_at_ms: int

    def _validate(self, p):
        check_id(self.account_id, p + '.account_id', 'acct')
        for f in ('old_key_digest', 'new_key_digest'):
            v = getattr(self, f)
            req(v is None and f == 'old_key_digest' or v is not None and DIGEST_RE.fullmatch(v) is not None, f'{p}.{f}',
                'not 16 lowercase hex')
        req(self.old_key_digest != self.new_key_digest, p + '.new_key_digest', 'a rotation changes the key digest')
        req(self.typed_phrase == confirmation_phrase(self.account_id, self.new_key_digest), p + '.typed_phrase',
            'does not match the required confirmation phrase')


@record
class Account(Record):
    account_id: str                  # acct_<uuid>: stable; never derived from the key
    label: str
    hedge_mode: bool
    binding: AccountBinding
    binding_state: BindingState
    proposed_binding: AccountBinding | None = None   # MISMATCH: what was observed; ROTATION_PENDING: what was proposed
    confirmation: BindingConfirmation | None = None  # the confirmation of `binding` (None only while UNCONFIRMED)

    def _validate(self, p):
        check_id(self.account_id, p + '.account_id', 'acct')
        req(0 < len(self.label) <= 64 and self.label.isprintable(), p + '.label', '1..64 printable characters')
        S = self.binding_state
        has_proposal = S in (BindingState.MISMATCH, BindingState.ROTATION_PENDING)
        req((self.proposed_binding is not None) == has_proposal, p + '.proposed_binding',
            f'set exactly in MISMATCH / ROTATION_PENDING (state {S})')
        req((self.confirmation is None) == (S is BindingState.UNCONFIRMED), p + '.confirmation',
            'every binding past UNCONFIRMED carries its typed confirmation')
        c = self.confirmation
        if c is not None:
            req(c.account_id == self.account_id and c.new_key_digest == self.binding.key_digest, p + '.confirmation',
                'confirms another account or key')
        if S is BindingState.RECONCILING:
            req(c.old_key_digest is not None, p + '.confirmation', 'RECONCILING follows a rotation (old digest set)')
        if has_proposal:
            req(not self.proposed_binding.same_account_as(self.binding), p + '.proposed_binding', 'equal to the binding')
        if S is BindingState.ROTATION_PENDING:
            b, n = self.binding, self.proposed_binding
            req((n.venue, n.environment) == (b.venue, b.environment), p + '.proposed_binding',
                'a rotation never changes venue or environment')

    @property
    def entries_allowed(self):
        return self.binding_state is BindingState.CONFIRMED


BINDING_BLOCK_REASON = {
    BindingState.UNCONFIRMED: ReasonCode.ACCOUNT_BINDING_UNCONFIRMED,
    BindingState.MISMATCH: ReasonCode.ACCOUNT_BINDING_MISMATCH,
    BindingState.ROTATION_PENDING: ReasonCode.ACCOUNT_ROTATION_PENDING,
    BindingState.RECONCILING: ReasonCode.ACCOUNT_RECONCILING,
}


def observe_binding(account, observed):
    """Pure: the binding state after the venue reports `observed` for this account's keys.
    Equal positions or balances play no part: only the binding fields decide."""
    if not isinstance(observed, AccountBinding):
        raise InvalidRecord('observed', 'not an AccountBinding')
    if account.binding.same_account_as(observed):
        return account.binding_state
    if account.binding_state is BindingState.ROTATION_PENDING and account.proposed_binding.same_account_as(observed):
        return BindingState.ROTATION_PENDING
    return BindingState.MISMATCH
