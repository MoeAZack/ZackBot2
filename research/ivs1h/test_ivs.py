"""Synthetic tests for IVS-1H (no market data, no outcomes). Ruling 82f70688."""
import io, os, sys, zipfile
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ivs_hourly as A
import ivs_signals as S

HU = A.HOUR_US
H0 = 1_760_000_400_000_000 - (1_760_000_400_000_000 % HU)   # an hour boundary in us


def rows(n=3600, start=H0, step=1_000_000, val='50.0'):
    return [(start + i * step, val, True) for i in range(n)]


# ---------------- stage A hourly aggregation ----------------
def test_last_in_hour_not_next_hour():
    r = rows(); r[-1] = (r[-1][0], '51.5', True)
    rec = A.hour_stats(r, H0)
    assert rec['valid'] == 1 and rec['value'] == '51.5' and rec['last_calc_time_us'] == H0 + HU - 1_000_000
    # a next-hour observation is never assigned to this hour by audit_day bucketing
    hrs, _ = A.audit_day(_zip('2025-10-09', [(H0 + HU - 1_000_000, '1.0'), (H0 + HU, '2.0')], H0), '2025-10-09')
    assert [v for _, v, _ in hrs[H0]] == ['1.0'] and [v for _, v, _ in hrs[H0 + HU]] == ['2.0']


def test_3000_count_and_end_freshness():
    assert A.hour_stats(rows(3000), H0 + 0)['reason'] == 'stale_last'          # last obs at +2999s: 601s before end
    r = rows(2999, start=H0 + HU - 2999 * 1_000_000)
    assert 'count_lt_3000' in A.hour_stats(r, H0)['reason']
    r = rows(3000, start=H0 + HU - 3000 * 1_000_000)                            # last at end-1s
    assert A.hour_stats(r, H0)['valid'] == 1
    r = rows(3000, start=H0 + HU - 3000 * 1_000_000 - 60_000_000)              # last exactly end-61s
    assert A.hour_stats(r, H0)['reason'] == 'stale_last'
    r = rows(3000, start=H0 + HU - 3000 * 1_000_000 - 59_000_000)              # last exactly end-60s -> fresh
    assert A.hour_stats(r, H0)['valid'] == 1


def test_positive_finite_iv_only():
    for bad in ('0', '-1.2', 'nan', 'inf', '', 'x'):
        r = rows(); r[5] = (r[5][0], bad, A.pos_finite(bad))
        rec = A.hour_stats(r, H0)
        assert rec['valid'] == 0 and 'invalid_row' in rec['reason'] and rec['value'] == ''


def test_duplicate_and_unordered_hour_invalid():
    r = rows(); r[10] = (r[9][0], '50.0', True)
    assert 'duplicate_time' in A.hour_stats(r, H0)['reason']
    r = rows(); r[10], r[11] = r[11], r[10]
    assert A.hour_stats(r, H0)['reason'] == 'unordered_time'


def test_time_units():
    assert A.to_us('1728000000001') == (1728000000001000, 'ms')
    assert A.to_us('1760000000000123') == (1760000000000123, 'us')
    assert A.to_us('17280000000') == (None, None) and A.to_us('-1') == (None, None)


def _zip(date, obs, h0):
    body = 'calc_time,symbol,base_asset,quote_asset,index_value\n' + ''.join(f'{t // 1000},BTCBVOLUSDT,BTCBVOL,USDT,{v}\n' for t, v in obs)
    b = io.BytesIO(); z = zipfile.ZipFile(b, 'w'); z.writestr(f'BTCBVOLUSDT-BVOLIndex-{date}.csv', body); z.close(); return b.getvalue()


# ---------------- levels / x / z ----------------
def _V(vals, start='2025-01-01'):
    ix = pd.date_range(start, periods=len(vals), freq='h'); return pd.Series(np.asarray(vals, float), index=ix)


def _walk(n, seed=1):
    rng = np.random.default_rng(seed); return 50 * np.exp(np.cumsum(0.01 * rng.standard_normal(n)))


def test_z_exact_prior_only_ddof1_and_offset():
    v = _walk(400); x, z = S.zscores(_V(v))
    lx = np.log(v[1:] / v[:-1])
    assert np.isnan(z.iloc[168]) and np.isfinite(z.iloc[169])                   # first z needs x[1..168] + x[169]: 169 prior levels + current
    t = 300; w = lx[t - 169:t - 1]                                                # x[t-168..t-1]
    assert len(w) == 168 and abs(z.iloc[t] - (lx[t - 1] - w.mean()) / w.std(ddof=1)) < 1e-12


def test_prefix_stability_no_lookahead():
    v = _walk(400); _, z1 = S.zscores(_V(v)); v2 = v.copy(); v2[350:] *= 3.0; _, z2 = S.zscores(_V(v2))
    assert np.allclose(z1.iloc[:350], z2.iloc[:350], equal_nan=True)


def test_gap_refuses_cross_gap_return_and_rewarms():
    v = _walk(800); v[400] = np.nan; x, z = S.zscores(_V(v))
    assert np.isnan(x.iloc[400]) and np.isnan(x.iloc[401])                        # no return across the missing level
    assert z.iloc[402:402 + 168].isna().all() and np.isfinite(z.iloc[402 + 168])  # 168 new consecutive x needed


def test_std_zero_skips():
    v = np.full(300, 50.0); x, z = S.zscores(_V(v))                             # constant level -> x = 0 exactly -> std 0
    assert (x.iloc[1:] == 0).all()
    assert z.isna().all()


def test_level_events_threshold_inclusive():
    z = pd.Series([2.999, 3.0, 4.0, np.nan, -5.0]); assert S.events(z).tolist() == [False, True, True, False, False]


# ---------------- coin sides / execution / frequency ----------------
def _fut(n=5):
    ix = pd.date_range('2025-01-01', periods=n, freq='h'); return pd.DataFrame(dict(o=100.0, h=100.5, l=99.5, c=100.0), index=ix)


def test_symmetric_price_unit_directions():
    f = _fut(); f.iloc[1] = [100, 100.1, 98.4, 98.5]; f.iloc[2] = [100, 101.6, 99.9, 101.5]; f.iloc[3] = [100, 101.6, 99.9, 101.5]
    ev = pd.Series([True, True, True, False, True], index=f.index); a = pd.Series(1.5, index=f.index)
    assert S.coin_sides(ev, f, a).tolist() == [0, 1, -1, 0, 0]                    # exactly -ATR / +ATR qualify; no event -> none
    a2 = a.copy(); a2.iloc[1] = 1.5000001; assert S.coin_sides(ev, f, a2).iloc[1] == 0
    a3 = a.copy(); a3.iloc[2] = 0.0; assert S.coin_sides(ev, f, a3).iloc[2] == 0  # ATR must be > 0


def test_original_atr_next_open_and_missing_next_hour():
    t = np.array(pd.date_range('2025-01-01', periods=4, freq='h').to_numpy()); atr = np.array([1.0, 2.0, 7.0, 9.0])
    assert S.decide(1, t, atr, True) == (None, (t[2], 6.0))
    assert S.decide(1, t, atr, False) == ('ineligible', None) and S.decide(None, t, atr, True)[0] == 'ineligible'
    assert S.decide(3, t, atr, True)[0] == 'missing_next_hour'
    t2 = t.copy(); t2[2] = t2[2] + np.timedelta64(1, 'h'); assert S.decide(1, t2, atr, True)[0] == 'missing_next_hour'


def test_frequency_bound_counts_distinct_times_and_calendar_weeks():
    T0 = pd.Timestamp('2025-01-01'); T1 = T0 + pd.Timedelta(days=70)
    same = [T0 + pd.Timedelta(hours=5)] * 9                                       # nine coins, one timestamp -> one possible fill
    r = S.frequency_bound(same + [T0 + pd.Timedelta(hours=k) for k in range(10, 40)], T0, T1)
    assert r['distinct_fill_times'] == 31 and r['calendar_weeks'] == 10 and r['FREQUENCY_FAIL']
    r = S.frequency_bound([T0 + pd.Timedelta(hours=k) for k in range(100)], T0, T1)
    assert r['upper_bound_per_week'] == 10 and not r['FREQUENCY_FAIL']
    r = S.frequency_bound([T0 + pd.Timedelta(hours=k) for k in range(99)], T0, T0 + pd.Timedelta(days=7))
    assert r['FREQUENCY_FAIL']                                                    # < 100 closes even at high weekly rate
    r = S.frequency_bound([T0 + pd.Timedelta(hours=k) for k in range(150)], T0, T0 + pd.Timedelta(days=360))
    assert r['FREQUENCY_FAIL']                                                    # >= 100 but < 3 per calendar week


def test_levels_drop_invalid_and_nonpositive():
    h = pd.DataFrame(dict(hour_open_utc=[str(t) for t in pd.date_range('2025-01-01', periods=4, freq='h')],
                          value=['50.1', '', '0', '49.9'], valid=['1', '0', '1', '0']))
    v = S.levels(h); assert v.iloc[0] == 50.1 and v.iloc[1:].isna().all()
