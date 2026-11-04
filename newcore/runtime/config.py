"""RT-01 runtime configuration: typed, frozen, PAPER-simulation or Binance TESTNET only.

Mainnet, live mode and any ``LIVE_CONFIRM`` style switch are refused at construction.  The configuration names
accounts and directories only; it never carries credentials.
"""
from __future__ import annotations

import enum
import os
import re
from dataclasses import dataclass
from typing import Mapping

from newcore.scheduler import MAX_ACCOUNTS


class RuntimeConfigError(ValueError):
    """A stable, non-secret configuration refusal code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RuntimeEnvironment(enum.StrEnum):
    PAPER_SIM = 'paper_sim'
    TESTNET = 'testnet'


ACCOUNT_RE = re.compile(r'acct_[0-9a-f]{32}')
AGGREGATE_RE = re.compile(r'pf_[0-9a-f]{32}')
EGRESS_RE = re.compile(r'[A-Za-z0-9._-]{1,64}')
# Any of these words in a configured environment, or any key that tries to switch live trading on, is refused.
FORBIDDEN_WORDS = ('live', 'mainnet', 'prod', 'real')
FORBIDDEN_KEYS = frozenset({'live', 'live_confirm', 'mainnet', 'api_key', 'api_secret', 'secret', 'key',
                            'credentials'})
CONFIG_KEYS = frozenset({'schema', 'environment', 'accounts', 'max_snapshot_age_s', 'proof_max_age_ms',
                         'evidence_deadline_ms', 'refresh_period_ms', 'stop_grace_ms', 'entries_enabled'})
ACCOUNT_KEYS = frozenset({'account_id', 'aggregate_id', 'account_dir', 'egress_id'})
SCHEMA = 'zackbot.newcore.runtime.config.v1'


def _fail(code: str):
    raise RuntimeConfigError(code)


def _int(value: object, code: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(code)
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountSpec:
    account_id: str
    aggregate_id: str
    account_dir: str
    egress_id: str

    def __post_init__(self):
        if type(self.account_id) is not str or ACCOUNT_RE.fullmatch(self.account_id) is None:
            _fail('account_id_invalid')
        if type(self.aggregate_id) is not str or AGGREGATE_RE.fullmatch(self.aggregate_id) is None:
            _fail('aggregate_id_invalid')
        if (type(self.account_dir) is not str or not self.account_dir or len(self.account_dir) > 260
                or '\0' in self.account_dir):
            _fail('account_dir_invalid')
        if type(self.egress_id) is not str or EGRESS_RE.fullmatch(self.egress_id) is None:
            _fail('egress_id_invalid')


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeConfig:
    environment: RuntimeEnvironment
    accounts: tuple[AccountSpec, ...]
    max_snapshot_age_s: int = 5
    proof_max_age_ms: int = 15_000
    evidence_deadline_ms: int = 10_000
    refresh_period_ms: int = 5_000
    stop_grace_ms: int = 5_000
    entries_enabled: bool = False

    def __post_init__(self):
        if type(self.environment) is not RuntimeEnvironment:
            _fail('environment_invalid')
        if type(self.accounts) is not tuple or not 1 <= len(self.accounts) <= MAX_ACCOUNTS:
            _fail('accounts_invalid')
        if any(type(row) is not AccountSpec for row in self.accounts):
            _fail('accounts_invalid')
        for field in ('account_id', 'aggregate_id', 'account_dir'):
            values = [getattr(row, field) for row in self.accounts]
            if field == 'account_dir':
                values = [os.path.normpath(v.replace('\\', '/')).replace('\\', '/').rstrip('/').casefold()
                          for v in values]
            if len(set(values)) != len(values):
                _fail(f'duplicate_{field}')
        _int(self.max_snapshot_age_s, 'max_snapshot_age_s_invalid', minimum=1, maximum=60)
        _int(self.evidence_deadline_ms, 'evidence_deadline_ms_invalid', minimum=100, maximum=60_000)
        _int(self.refresh_period_ms, 'refresh_period_ms_invalid', minimum=1_000, maximum=60_000)
        _int(self.proof_max_age_ms, 'proof_max_age_ms_invalid', minimum=1_000, maximum=120_000)
        # A zero grace would let NC-04 stop cancel queued protection at once.
        _int(self.stop_grace_ms, 'stop_grace_ms_invalid', minimum=1_000, maximum=60_000)
        if self.proof_max_age_ms < self.refresh_period_ms + self.evidence_deadline_ms:
            # A proof must not expire before the next refresh can possibly finish.
            _fail('proof_max_age_ms_too_short')
        if type(self.entries_enabled) is not bool:
            _fail('entries_enabled_invalid')


def _environment(value: object) -> RuntimeEnvironment:
    if type(value) is not str:
        _fail('environment_invalid')
    lowered = value.casefold()
    if any(word in lowered for word in FORBIDDEN_WORDS):
        _fail('environment_forbidden')
    try:
        return RuntimeEnvironment(value)
    except ValueError:
        _fail('environment_invalid')


def _refuse_forbidden_keys(mapping: Mapping, where: str) -> None:
    for key in mapping:
        if type(key) is not str:
            _fail(f'{where}_key_invalid')
        if key.casefold() in FORBIDDEN_KEYS or 'live' in key.casefold() or 'mainnet' in key.casefold():
            _fail(f'{where}_key_forbidden')


def config_from_mapping(raw: object) -> RuntimeConfig:
    """Build a RuntimeConfig from parsed JSON.  Unknown, live or credential keys are refused, never ignored."""
    if type(raw) is not dict:
        _fail('config_not_object')
    _refuse_forbidden_keys(raw, 'config')
    if set(raw) - CONFIG_KEYS:
        _fail('config_key_unknown')
    if raw.get('schema') != SCHEMA:
        _fail('schema_invalid')
    accounts_raw = raw.get('accounts')
    if type(accounts_raw) is not list:
        _fail('accounts_invalid')
    accounts = []
    for row in accounts_raw:
        if type(row) is not dict:
            _fail('account_not_object')
        _refuse_forbidden_keys(row, 'account')
        if set(row) != ACCOUNT_KEYS:
            _fail('account_keys_invalid')
        accounts.append(AccountSpec(**row))
    values = {name: raw[name] for name in ('max_snapshot_age_s', 'proof_max_age_ms', 'evidence_deadline_ms',
                                           'refresh_period_ms', 'stop_grace_ms', 'entries_enabled') if name in raw}
    return RuntimeConfig(environment=_environment(raw.get('environment')), accounts=tuple(accounts), **values)


__all__ = ['ACCOUNT_KEYS', 'AccountSpec', 'CONFIG_KEYS', 'FORBIDDEN_KEYS', 'FORBIDDEN_WORDS', 'RuntimeConfig',
           'RuntimeConfigError', 'RuntimeEnvironment', 'SCHEMA', 'config_from_mapping']
