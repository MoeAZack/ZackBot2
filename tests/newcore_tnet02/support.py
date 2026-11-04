"""Shared builders for TNET-02 tests: manifest, fake exchange, wired adapter."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from decimal import Decimal

from newcore.domain.account import (Account, AccountBinding, BindingConfirmation, BindingState, Environment, Venue,
                                    confirmation_phrase)
from newcore.domain.orders import Side
from newcore.testnet_harness import REQUIRED_SCENARIOS, HarnessBinding, ScenarioCase, ScenarioKind, SuiteManifest
from newcore.tnet02.adapter import HarnessConfig
from newcore.tnet02.journal import FileJournalOpener
from newcore.tnet02.wiring import build_adapter, key_digest

from .fake_binance import (SECRET_KEY, FakeAccountPort, FakeBinance, FakeClock, FakeCredentials, FakeFiltersPort,
                           limit_payloads)


ACCOUNT_ID = "acct_0123456789abcdef0123456789abcdef"
BOOT_ID = "boot_0123456789abcdef0123456789abcdef"
BUILD_SHA = "b" * 40
T0 = 2_000_000_000_000
DIGEST = key_digest(SECRET_KEY)


def account(*, digest: str = DIGEST, hedge: bool = True, uid: str | None = None,
            environment: Environment = Environment.TESTNET) -> Account:
    binding = AccountBinding(venue=Venue.BINANCE_USDM, environment=environment, settlement_asset="USDT",
                             key_digest=digest, exchange_uid=uid)
    confirmation = BindingConfirmation(account_id=ACCOUNT_ID, old_key_digest=None, new_key_digest=digest,
                                       typed_phrase=confirmation_phrase(ACCOUNT_ID, digest), confirmed_at_ms=T0 - 10)
    return Account(account_id=ACCOUNT_ID, label="Dedicated TNET-02", hedge_mode=hedge, binding=binding,
                   binding_state=BindingState.CONFIRMED, proposed_binding=None, confirmation=confirmation)


def manifest(*, acct: Account | None = None, symbols: tuple[str, ...] = ("BTCUSDT",), started: int = T0,
             max_notional: Decimal = Decimal("1000"), leverage: int = 3) -> SuiteManifest:
    cases = []
    for index, kind in enumerate(REQUIRED_SCENARIOS):
        side = Side.SHORT if kind in (ScenarioKind.SHORT_TARGET, ScenarioKind.SHORT_STOP) else Side.LONG
        case_id = "case_" + hashlib.sha256(f"tnet02-{index}".encode()).hexdigest()[:32]
        cases.append(ScenarioCase(case_id, kind, symbols[index % len(symbols)], side, leverage, max_notional, 12))
    binding = HarnessBinding(acct or account(), True, symbols)
    return SuiteManifest(BUILD_SHA, started, BOOT_ID, binding, tuple(cases))


def wave(amplitude: str = "0.002", period_ms: int = 60_000):
    amp = float(amplitude)

    def path(now: int, symbol: str) -> Decimal:
        base = Decimal("60000") if symbol == "BTCUSDT" else Decimal("3000")
        tick = Decimal("0.1") if symbol == "BTCUSDT" else Decimal("0.01")
        factor = Decimal(repr(1 + amp * math.sin(2 * math.pi * (now - T0) / period_ms)))
        return (base * factor / tick).quantize(Decimal("1")) * tick
    return path


@dataclass
class Rig:
    fake: FakeBinance
    clock: FakeClock
    wired: object
    journal_dir: object

    @property
    def adapter(self):
        return self.wired.adapter


def rig(tmp_path, *, config: HarnessConfig | None = None, price_path=None, credentials=None) -> Rig:
    clock = FakeClock(T0)
    fake = FakeBinance(clock)
    fake.price_path = price_path if price_path is not None else wave()
    fake.tick()
    exchange, limits = limit_payloads()
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    wired = build_adapter(sender=fake, credentials=credentials or FakeCredentials(), account_id=ACCOUNT_ID,
                          egress_id="egress-test", exchange_info=exchange, account_order_limits=limits,
                          account=FakeAccountPort(fake), filters=FakeFiltersPort(),
                          journals=FileJournalOpener(journal_dir), clock=clock, monotonic=clock,
                          sleeper=fake.advance, config=config or HarnessConfig(poll_ms=1000))
    return Rig(fake, clock, wired, journal_dir)
