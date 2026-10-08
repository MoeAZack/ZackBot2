"""Shared builders for the S1 slice tests: synthetic candles, rules, accounts."""
from decimal import Decimal

from newcore.domain import (Account, AccountBinding, BindingConfirmation, BindingState, Capability, Environment,
                            InstrumentId, InstrumentRules, Venue, confirmation_phrase)
from newcore.ports.bars import Bar

H4 = 14_400_000
T0 = 1_704_067_200_000            # 2024-01-01T00:00Z, a 4h boundary
ACCOUNT_ID = 'acct_' + '1' * 32
PORTFOLIO_ID = 'pf_' + '2' * 32
KEY_DIGEST = '0123456789abcdef'
D = Decimal


def bar(i, o, h, l, c, tf=H4, t0=T0):
    return Bar(open_ms=t0 + i * tf, close_ms=t0 + (i + 1) * tf, open=D(str(o)), high=D(str(h)), low=D(str(l)),
               close=D(str(c)), volume=D('1000'))


def flat_bars(n, px='100', wick='0.5', overrides=None, tf=H4, t0=T0):
    """The golden 'flat' market: o = c = px, h/l = px +- wick; a declared bar overrides one candle and later candles
    stay flat at its close (goldenlib/market.py)."""
    overrides = overrides or {}
    out, last = [], D(px)
    w = D(wick)
    for i in range(n):
        if i in overrides:
            o, h, l, c = (D(str(x)) for x in overrides[i])
            last = c
        else:
            o = c = last
            h, l = last + w, last - w
        out.append(Bar(open_ms=t0 + i * tf, close_ms=t0 + (i + 1) * tf, open=o, high=h, low=l, close=c,
                       volume=D('1000')))
    return tuple(out)


def fine_rules(symbol):
    """Filters that never bind (the golden legacy model has none): 1e-12 grid, no minimum notional."""
    return InstrumentRules(instrument=InstrumentId(venue=Venue.BINANCE_USDM, symbol=symbol), tick_size=D('1E-12'),
                           step_size=D('1E-12'), min_qty=D('1E-12'), max_qty=D('1000000000'), min_notional=D(0),
                           capabilities=(Capability.HEDGE_MODE, Capability.STOP_MARKET, Capability.REDUCE_ONLY))


def sim_account():
    binding = AccountBinding(venue=Venue.BINANCE_USDM, environment=Environment.SIM, settlement_asset='USDT',
                             key_digest=KEY_DIGEST, exchange_uid=None)
    conf = BindingConfirmation(account_id=ACCOUNT_ID, old_key_digest=None, new_key_digest=KEY_DIGEST,
                               typed_phrase=confirmation_phrase(ACCOUNT_ID, KEY_DIGEST), confirmed_at_ms=T0 - 1)
    return Account(account_id=ACCOUNT_ID, label='s1-sim', hedge_mode=True, binding=binding,
                   binding_state=BindingState.CONFIRMED, proposed_binding=None, confirmation=conf)
