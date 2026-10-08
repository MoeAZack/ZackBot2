"""Builders for the runner-harness tests: a TestnetTarget over the stateful fake Binance (real transport, fake HTTP,
DUMMY credentials, a fake clock that advances when the harness sleeps)."""
import copy
from dataclasses import dataclass

from fake_binance import FakeBinance
from ncv_support import DUMMY_KEY, DUMMY_SECRET

from newcore.tnet.rspec import bundled
from newcore.tnet.targets import TestnetTarget
from newcore.venue.credentials import StaticCredentials, binding_digest

DIGEST = binding_digest(DUMMY_KEY)
ACCOUNT = 'acct_' + 'ab' * 16


@dataclass(frozen=True)
class Cfg:
    mode: str = 'TESTNET'
    venue_kind: str = 'testnet'
    factory: str = 'newcore.venue.factory:build_testnet'
    account_id: str = ACCOUNT
    key_digest: str = DIGEST
    symbols: tuple = ('SOLUSDT',)


class Store:
    def load(self, scrubber=None):
        if scrubber is not None:
            scrubber.register(DUMMY_KEY, DUMMY_SECRET)
        return StaticCredentials(DUMMY_KEY, DUMMY_SECRET)


class World:
    """fb (FakeBinance) + a fake wall clock shared by the target's local clock, its sleep and the harness monotonic."""

    def __init__(self, **fb_kw):
        self.fb = FakeBinance(**fb_kw)
        self.t = self.fb.now
        self.slept = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += int(round(s * 1000))
        self.fb.now = self.t

    def monotonic(self):
        return self.t / 1000

    def target(self, **kw):
        return TestnetTarget(kw.pop('config', Cfg()), http=self.fb, sleep=self.sleep, local_clock=self.clock,
                             store=Store(), **kw)


def spec(id_):
    return copy.deepcopy(next(s for s in bundled() if s['id'] == id_))
