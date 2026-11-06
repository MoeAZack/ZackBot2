"""TNET-02 live account reads spend from, and report to, the account's NC-04 budget.

Closes the disclosed gap "AccountPort reads bypass the NC-04 budget": positionRisk and balance reads used for marks,
leverage and equity now reserve GENERAL weight, are refused during any rate block, and feed their own 429/418
evidence back so the venue is blocked too.
"""
from __future__ import annotations

import pytest

from newcore.account_context import Limiter
from newcore.domain.decision import Action
from newcore.ports.rate_blocks import RateBlockState
from newcore.testnet_harness import SuiteStatus, run_suite
from newcore.tnet02.adapter import AdapterError
from newcore.tnet02.journal import FileJournalOpener
from newcore.tnet02.live import LiveAccountPort, LiveError, LiveFiltersPort, SignedReader, read_limit_payloads
from newcore.tnet02.loss_store import FileLossStore
from newcore.tnet02.wiring import build_adapter, key_digest
from newcore.venue.binance_usdm_testnet import HttpResponse, RateEvidence

from .fake_binance import FakeBinance, FakeClock
from .support import ACCOUNT_ID, T0, account, manifest, wave
from .test_tnet02_live import DOC_KEY, _creds

ACCOUNT_PATHS = {"/fapi/v2/positionRisk", "/fapi/v2/balance"}


def _live(tmp_path):
    clock = FakeClock(T0)
    fake = FakeBinance(clock)
    fake.price_path = wave()
    fake.tick()
    creds = _creds()
    reader = SignedReader(sender=fake, credentials=creds, clock=clock)
    exchange, limits, info, raw = read_limit_payloads(reader)
    port = LiveAccountPort(reader, clock)
    (tmp_path / "j").mkdir(exist_ok=True)
    wired = build_adapter(sender=fake, credentials=creds, account_id=ACCOUNT_ID, egress_id="egress-test",
                          exchange_info=exchange, account_order_limits=limits, account=port,
                          filters=LiveFiltersPort(info, raw), journals=FileJournalOpener(tmp_path / "j"),
                          loss_store=FileLossStore(tmp_path / "j"),
                          clock=clock, monotonic=clock, sleeper=fake.advance)
    return fake, clock, port, reader, wired


def _weight(budget) -> int:
    return dict(budget.snapshot().usage)[Limiter.REQUEST_WEIGHT_1M]


def _account_hits(fake) -> list[str]:
    return [p for _m, p, _q in fake.requests if p in ACCOUNT_PATHS]


def test_wiring_binds_the_live_port_to_the_venue_budget(tmp_path):
    _fake, _clock, port, _reader, wired = _live(tmp_path)
    assert port._budget is wired.budget


def test_each_account_read_reserves_documented_weight(tmp_path):
    fake, _clock, port, _reader, wired = _live(tmp_path)
    before = _weight(wired.budget)
    port.facts(("BTCUSDT",))
    assert _weight(wired.budget) == before + 10            # positionRisk 5 + balance 5
    assert _account_hits(fake) == ["/fapi/v2/positionRisk", "/fapi/v2/balance"]


@pytest.mark.parametrize("status, retry, state", [
    (429, 30_000, RateBlockState.BLOCKED_UNTIL),
    (429, None, RateBlockState.BLOCKED_UNKNOWN_DURATION),
    (418, None, RateBlockState.BLOCKED_UNKNOWN_DURATION),
], ids=["429_retry_after", "429_no_deadline", "418_ban"])
def test_account_read_refused_while_the_budget_is_blocked_and_sends_nothing(tmp_path, status, retry, state):
    fake, clock, port, _reader, wired = _live(tmp_path)
    wired.budget.observe(RateEvidence(status, retry, None, clock()))
    assert wired.budget.snapshot().state is state
    before = len(fake.requests)
    with pytest.raises(LiveError) as caught:
        port.facts(("BTCUSDT",))
    assert caught.value.code == "account.budget_refused" and fake.requests[before:] == []


@pytest.mark.parametrize("status, retry, state, cause", [
    (429, 60_000, RateBlockState.BLOCKED_UNTIL, "rate_429_retry_after"),
    (418, None, RateBlockState.BLOCKED_UNKNOWN_DURATION, "ban_418"),
], ids=["429", "418"])
def test_a_rate_answer_on_an_account_read_blocks_the_whole_account(tmp_path, status, retry, state, cause):
    fake, _clock, port, _reader, wired = _live(tmp_path)
    fake._get_fapi_v2_positionRisk = lambda params: HttpResponse(status=status, body=b'{"code":-1003}',
                                                                 retry_after_ms=retry)
    with pytest.raises(LiveError):
        port.facts(("BTCUSDT",))
    snap = wired.budget.snapshot()
    assert snap.state is state and snap.cause == cause
    # the venue's own general reads are now refused as well
    with pytest.raises(AdapterError):
        wired.adapter._read_positions("BTCUSDT", Action.ENTER)


def test_used_weight_header_from_an_account_read_reaches_the_budget(tmp_path):
    fake, _clock, port, _reader, wired = _live(tmp_path)
    real = fake._get_fapi_v2_balance

    def heavy(params):
        response = real(params)
        return HttpResponse(status=response.status, body=response.body, used_weight_1m=2000)
    fake._get_fapi_v2_balance = heavy
    port.facts(("BTCUSDT",))
    assert _weight(wired.budget) >= 2000


def test_saturated_budget_refuses_account_reads(tmp_path):
    fake, clock, port, _reader, wired = _live(tmp_path)
    wired.budget.observe(RateEvidence(200, None, 2390, clock()))
    before = len(fake.requests)
    with pytest.raises(LiveError) as caught:
        port.facts(("BTCUSDT",))
    assert caught.value.code == "account.budget_refused" and fake.requests[before:] == []


def test_budget_and_observer_bind_only_once(tmp_path):
    _fake, _clock, port, reader, wired = _live(tmp_path)
    with pytest.raises(LiveError) as caught:
        port.bind_budget(wired.budget)
    assert caught.value.code == "account.budget_binding"
    with pytest.raises(LiveError) as caught:
        reader.set_rate_observer(lambda evidence: None)
    assert caught.value.code == "reader.rate_observer"
    fresh = LiveAccountPort(SignedReader(sender=_fake, credentials=_creds(), clock=_clock), _clock)
    with pytest.raises(LiveError) as caught:
        fresh.bind_budget(object())
    assert caught.value.code == "account.budget_binding"


def test_a_failing_observer_fails_the_read_closed(tmp_path):
    fake = FakeBinance(FakeClock(T0))
    reader = SignedReader(sender=fake, credentials=_creds(), clock=fake.clock)

    def broken(_evidence):
        raise RuntimeError("observer down")
    reader.set_rate_observer(broken)
    with pytest.raises(LiveError) as caught:
        reader.signed("/fapi/v2/balance")
    assert caught.value.code == "reader.rate_observer" and caught.value.__cause__ is None


def test_adapter_holds_instead_of_reading_marks_during_a_rate_block(tmp_path):
    fake, clock, _port, _reader, wired = _live(tmp_path)
    spec = manifest(acct=account(digest=key_digest(DOC_KEY)))
    wired.adapter.confirm_binding(spec.binding)
    wired.budget.observe(RateEvidence(429, 30_000, None, clock()))
    before = len(fake.requests)
    with pytest.raises(AdapterError) as caught:
        wired.adapter._facts(("BTCUSDT",))
    assert caught.value.code == "read.account_facts"
    assert _account_hits_since(fake, before) == []


def _account_hits_since(fake, start):
    return [p for _m, p, _q in fake.requests[start:] if p in ACCOUNT_PATHS]


def test_full_matrix_still_passes_with_budgeted_account_reads(tmp_path):
    fake, _clock, _port, _reader, wired = _live(tmp_path)
    report = run_suite(manifest(acct=account(digest=key_digest(DOC_KEY))), wired.adapter, [].append)
    assert report.status is SuiteStatus.PASSED, [(c.kind.value, c.failure) for c in report.cases]
    assert _account_hits(fake)
