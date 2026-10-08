"""Offline replay of a recorded runner-target scenario: the SAME spec through the SAME Runner, the transport answered by
the cassette (newcore.venue.cassette.CassettePlayer: requests must match in order - method, URL, every parameter;
timestamp / signature by presence), with the candle-close tape and a virtual clock. No network, no real key (a dummy
key signs; the player checks only that a signature and a key header are present).

replay(cassette_path) -> ReplayResult: same = the replayed verdict equals the recorded one AND every recorded
interaction was replayed AND no request diverged. A divergence (CassetteMismatch) surfaces as a FAIL of the replayed
scenario with the mismatch text.
"""
import json
import os
from dataclasses import dataclass
from decimal import Decimal

from newcore.venue.cassette import CassetteMismatch, CassettePlayer
from newcore.venue.cassette_replay import leak_audit
from newcore.venue.credentials import StaticCredentials, binding_digest

from .driver import run_scenario
from .recording import REPLAY_FORMAT
from .rspec import validate_rspec
from .seams import DeadlineExceeded
from .targets import TestnetTarget

# dummy credentials for replays only (never a real key; distinctive so a leak would be obvious)
REPLAY_KEY = 'REPLAYKEYzzNOTAREALKEYqq0123456789abcdefABCDEF0123456789abcdefAB'
REPLAY_SECRET = 'REPLAYSECRETzzNOTAREALSECRETqq9876543210fedcbaFEDCBA9876543210fe'


class ReplayError(ValueError):
    """The cassette or its replay sidecar is missing, malformed or not a runner-target recording."""


class ReplayClock:
    def __init__(self, t_ms):
        self.t = int(t_ms)

    def __call__(self):
        return self.t

    def monotonic(self):
        return self.t / 1000


class _Store:
    def load(self, scrubber=None):
        if scrubber is not None:
            scrubber.register(REPLAY_KEY, REPLAY_SECRET)
        return StaticCredentials(REPLAY_KEY, REPLAY_SECRET)


@dataclass(frozen=True)
class _Config:
    account_id: str
    key_digest: str
    symbols: tuple
    mode: str = 'TESTNET'
    venue_kind: str = 'testnet'
    factory: str = 'newcore.venue.factory:build_testnet'


class ReplayTarget(TestnetTarget):
    """TestnetTarget over the player: no seam injection, the cycles follow the recorded candle-close tape (the clock
    jumps to close + settle, as on the recorded run), sleeps are virtual."""

    def __init__(self, config, *, player, clock, tape, settle_ms):
        self._tape, self._clock = list(tape), clock
        super().__init__(config, http=player, sleep=lambda s: None, local_clock=clock, store=_Store(),
                         settle_ms=settle_ms, faults=False)

    def next_close(self, deadline_s=None):
        if not self._tape:
            raise DeadlineExceeded('replay: the recorded run made no further cycle here')
        t = self._tape.pop(0)
        self._clock.t = t + self.settle_ms
        return t


@dataclass
class ReplayResult:
    recorded_verdict: str
    replayed: object                 # ScenarioResult
    remaining: int                   # recorded interactions not replayed
    same: bool


def _boot_server_ms(doc):
    for it in doc['interactions']:
        if it['request']['url'].endswith('/fapi/v1/time') and 'response' in it:
            try:
                return int(json.loads(it['response']['body_text'])['serverTime'])
            except (KeyError, ValueError, TypeError):
                break
    raise ReplayError('the cassette has no server-time answer (not a factory boot recording)')


def load_bundle(cassette_path):
    if not str(cassette_path).endswith('.json') or str(cassette_path).endswith('.meta.json'):
        raise ReplayError('give the cassette (.json), not its .meta.json')
    meta_path = str(cassette_path)[:-len('.json')] + '.meta.json'
    try:
        with open(cassette_path, encoding='utf-8') as fh:
            doc = json.load(fh)
        with open(meta_path, encoding='utf-8') as fh:
            meta = json.load(fh)
    except (OSError, ValueError) as ex:
        raise ReplayError(f'cannot read the cassette / its sidecar: {type(ex).__name__}') from None
    if not isinstance(meta, dict) or meta.get('format') != REPLAY_FORMAT:
        raise ReplayError(f'{meta_path} is not a {REPLAY_FORMAT} sidecar')
    if not isinstance(doc, dict) or not isinstance(doc.get('interactions'), list):
        raise ReplayError('not a cassette')
    problem = leak_audit(doc)
    if problem is not None:
        raise ReplayError(f'the cassette fails the leak audit: {problem}')
    validate_rspec(meta['spec'])
    return doc, meta


def replay(cassette_path):
    doc, meta = load_bundle(cassette_path)
    player = CassettePlayer(doc)
    clock = ReplayClock(_boot_server_ms(doc))
    cfg = _Config(account_id=meta['account_id'], key_digest=binding_digest(REPLAY_KEY), symbols=tuple(meta['symbols']))
    target = ReplayTarget(cfg, player=player, clock=clock, tape=meta['cycle_times'], settle_ms=meta['settle_ms'])
    baseline = {(s, side): Decimal(q) for s, side, q in meta['baseline']}
    r = run_scenario(meta['spec'], target, run_nonce=meta['run_nonce'], monotonic=clock.monotonic,
                     baseline=baseline)
    try:
        player.assert_exhausted()
        remaining = 0
    except CassetteMismatch:
        remaining = player.remaining
    same = r.verdict == meta['verdict'] and remaining == 0 and not (r.error or '').startswith('CassetteMismatch')
    return ReplayResult(meta['verdict'], r, remaining, same)


def describe(rr, path):
    name = os.path.basename(str(path))
    head = 'SAME' if rr.same else 'DIFFERENT'
    lines = [f'{head:<9} {name}: recorded {rr.recorded_verdict}, replayed {rr.replayed.verdict}'
             + (f', {rr.remaining} interaction(s) not replayed' if rr.remaining else '')]
    if rr.replayed.error:
        lines.append(f'    replay error: {rr.replayed.error}')
    return '\n'.join(lines)
