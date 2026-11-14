"""SVS-1H signal (frozen ruling 1a1effaa, P1). Research only, default OFF; no account/runtime use.
Paired hour t: Q = spot_qv + fut_qv > 0, s = spot_qv / Q. Prior mean/std over exactly the preceding 168 consecutive
paired valid hours (excluding t, ddof 1); z = (s - mean) / std, zero/nonfinite std skips. Event: |fc - fo| >= ATR14_t > 0,
non-doji, and Q >= 2 * median(previous 168 paired Q). LEVEL rule: z >= 2 continues the candle, z <= -2 fades it.
Eligibility (both markets, SPB rule reused byte-pinned): prior-30 complete-day median qv >= $10m and 720 paired hours."""
import numpy as np, pandas as pd
from spb_signals_6f16d49a import eligibility

Z, VOLX, WIN = 2.0, 2.0, 168


def signal(spot, fut, atr):
    """spot: hourly frame (c, qv); fut: hourly frame (o, h, l, c, qv) on a complete UTC grid; atr: futures ATR14 on fut.index.
    Returns (side series {-1,0,1}, z, share, Q) indexed like fut; side is the futures trade direction."""
    ix = fut.index; s = spot.reindex(ix); f = fut
    fok = (np.isfinite(f[['o', 'h', 'l', 'c']]).all(axis=1) & (f[['o', 'h', 'l', 'c']] > 0).all(axis=1)
           & (f.h >= f[['o', 'c']].max(axis=1)) & (f.l <= f[['o', 'c']].min(axis=1)))
    paired = (np.isfinite(s.c) & (s.c > 0) & np.isfinite(f.c) & (f.c > 0) & np.isfinite(s.qv) & (s.qv >= 0)
              & np.isfinite(f.qv) & (f.qv >= 0))
    Q = (s.qv + f.qv).where(paired)
    paired = paired & (Q > 0)
    Q = Q.where(paired); sh = (s.qv / Q).where(paired)
    prior = sh.shift(1).rolling(WIN, min_periods=WIN)
    mean, std = prior.mean(), prior.std(ddof=1)
    z = (sh - mean) / std.where((std > 0) & np.isfinite(std))
    qmed = Q.shift(1).rolling(WIN, min_periods=WIN).median()
    a = pd.Series(np.asarray(atr, dtype=float), index=ix)
    body = f.c - f.o
    event = paired & fok & (a > 0) & (body != 0) & (body.abs() >= a) & (Q >= VOLX * qmed)
    ok = event & eligibility(s.qv, paired) & eligibility(f.qv, paired)
    dirn = np.sign(body)
    side = pd.Series(0, index=ix, dtype=int)
    side[ok & (z >= Z)] = dirn[ok & (z >= Z)].astype(int)
    side[ok & (z <= -Z)] = -dirn[ok & (z <= -Z)].astype(int)
    return side, z, sh, Q
