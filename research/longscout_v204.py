"""Research-only two-sided fade during announced full NYSE closures.

On a weekday when the NYSE has no core session, study the target's 12-16 UTC
bar. This contains the usual New York open in both US time regimes. Require a
1.5% directional price move followed by 20-80% reversal in log-price distance
by the next 4h bar close. In a sideways coin with nonbear BTC, fade the first
move. Early closes are outside the source schedule. Next 4h open fills.
"""
import numpy as np
import pandas as pd

import longscout_v1 as L1
import strategies as S

FAMILY_NOVELTY = ('Uses published full US cash-equity closures as a necessary exogenous '
                  'condition for fading the usual open window. V93 studies intraday '
                  'displacements and V106 learns weekday returns without venue closures.')
MARKET_FEATURE_SOURCE = 'ICE/NYSE historical holiday releases and the 2025 national mourning closure'
# ICE NYSE Group 2022-2024 holidays, released 2021-12-27:
# https://ir.theice.com/press/news-details/2021/NYSE-Group-Announces-2022-2023-and-2024-Holiday-and-Early-Closings-Calendar/default.aspx
# ICE NYSE Group 2024-2026 holidays, released 2023-11-10:
# https://ir.theice.com/press/news-details/2023/NYSE-Group-Announces-2024-2025-and-2026-Holiday-and-Early-Closings-Calendar/default.aspx
# Additional 2025-01-09 full closure, released 2024-12-30:
# https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx
NYSE_FULL_CLOSURES = pd.DatetimeIndex(pd.to_datetime([
    '2022-01-17', '2022-02-21', '2022-04-15', '2022-05-30', '2022-06-20',
    '2022-07-04', '2022-09-05', '2022-11-24', '2022-12-26',
    '2023-01-02', '2023-01-16', '2023-02-20', '2023-04-07', '2023-05-29',
    '2023-06-19', '2023-07-04', '2023-09-04', '2023-11-23', '2023-12-25',
    '2024-01-01', '2024-01-15', '2024-02-19', '2024-03-29', '2024-05-27',
    '2024-06-19', '2024-07-04', '2024-09-02', '2024-11-28', '2024-12-25',
    '2025-01-01', '2025-01-09', '2025-01-20', '2025-02-17', '2025-04-18',
    '2025-05-26', '2025-06-19', '2025-07-04', '2025-09-01', '2025-11-27', '2025-12-25',
    '2026-01-01', '2026-01-19', '2026-02-16', '2026-04-03', '2026-05-25',
    '2026-06-19', '2026-07-03', '2026-09-07', '2026-11-26', '2026-12-25',
]))
set_btc = L1.set_btc


def closure_fade(open_price, close_price, holidays):
    dates = close_price.index
    # Exchange data labels the opening time of the 4h candle.
    slot = (dates.hour == 16) & (dates == dates.floor('4h'))
    full_closure = dates.normalize().isin(holidays)
    contiguous = dates.to_series().diff().eq(pd.Timedelta(hours=4))
    log_close = np.log(close_price.where(np.isfinite(close_price) & close_price.gt(0)))
    log_open = np.log(open_price.where(np.isfinite(open_price) & open_price.gt(0)))
    first_move = (log_close - log_open).shift()
    retrace = -log_close.diff() / first_move.where(first_move.ne(0))
    can_fade = full_closure & slot & contiguous & retrace.between(.20, .80)
    return can_fade & first_move.le(np.log(.985)), can_fade & first_move.ge(np.log(1.015))


def sig_closure_fade(d, params, ctx):
    dates = pd.DatetimeIndex(pd.to_datetime(d.t))
    le, se = closure_fade(d.o.set_axis(dates), d.c.set_axis(dates), NYSE_FULL_CLOSURES)
    eligible = (L1.coin_regime(d) == 0) & np.isin(L1.btc_regime(d), (0., 1.))
    flat = pd.Series(False, index=d.index)
    return dict(le=S.X(pd.Series(le.to_numpy() & eligible, index=d.index)),
                se=S.X(pd.Series(se.to_numpy() & eligible, index=d.index)), lx=flat.copy(), sx=flat)


CANDIDATES = {
    'r204_nyse_full_closure_us_open_fade_4h': dict(
        name='[RESEARCH] R204 Midday range fade during full NYSE closure', style='range',
        fn=sig_closure_fade, sides='both', tf='4h', research_only=True, params={},
        mgmt=dict(stop_atr=2.5, tp_r=2.0, max_bars=30),
    ),
}


def register():
    for key, value in CANDIDATES.items():
        S.STRATEGIES[key] = dict(value, desc=value['name'])
