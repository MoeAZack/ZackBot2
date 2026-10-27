"""Factory candidate concentration / drawdown / cost stress harness over breadth-candidate packets (research only).

Reads one or more packets written by tools/research/run_breadth_candidate.py (format zb-breadth-candidate/1), verifies
each packet's own packet_sha256 exactly the way the runner defines it (sha256 of the canonical JSON - sort_keys, compact
separators, allow_nan=False - of the packet without that key) and fails closed on a schema mismatch, a status other than
'ok', a missing field, a NaN / non-finite number, a null core trade value, a stats mismatch or mixed strategies. It then
reports, per packet and pooled (all trades of all packets):

  1. concentration - R by symbol, top-1 / top-3 share of net R, HHI of the positive-R contributions, net R and PF(R) with
     the top symbol removed, leave-top-k-out k=1..3, R by exit month / quarter (top period's share of net R);
  2. drawdown - max DD of cumulative R in exit order (ties: entry time, then packet order, then the packet's own trade
     order = the runner's sort_values(['i_out', 'i_in'])), longest losing streak (R < 0), and a seeded bootstrap (trades
     resampled with replacement, random.Random(seed)) giving DD p50 / p95 (nearest rank) and P(net R <= 0);
  3. cost stress - net R with fees x1.5 / x2, funding x2 and an extra slippage of X bps per side, from per-trade fields only
     (see COST STRESS below); UNAVAILABLE with the reason whenever a field it needs is absent - never approximated;
  4. pyramid attribution - UNAVAILABLE for this schema: the runner merges every pyramid add into the one position (avg
     price, total qty) and writes no add-leg marker per trade;
  5. pre-registered DIAGNOSTIC flags (constants below, frozen by Codex 6447574071).

COST STRESS (exact under the runner's schema):
  - per trade risk_usd = pnl / R (backtest.close: R = realized / risk). Both are written rounded to 6 dp, so a trade with
    |R| < MIN_ABS_R or |pnl| < MIN_ABS_PNL cannot give its risk to better than ~1e-3 relative; any such trade makes every
    stress UNAVAILABLE (count reported) instead of being approximated or dropped.
  - fees = entry + exit (+ pyramid add) taker fees = fee_taker x traded notional (costs.entry_order 'market (taker)';
    every fill is charged costs.fee_taker), so traded notional = fees / fee_taker exactly.
  - fees x m: dR = -(m - 1) x fees / risk;  funding x m: dR = -(m - 1) x funding / risk (funding = gross - fees - pnl, the
    sign the runner records: positive = paid);  slippage +X bps per side: dR = -(X / 1e4) x (fees / fee_taker) / risk
    (X bps of every filled notional, entry, adds and exits).

Pure stdlib, deterministic, read-only: never runs a backtest, never writes anything but --out (never overwritten).

  py -3.14 tools/research/factory_stress.py --packet s1.json --packet s2.json --out report.json \
     [--boot 2000 --seed 20261027 --slip-bps 2]
"""
import argparse
import hashlib
import json
import math
import os
import random
import sys
from datetime import datetime, timezone

FORMAT_IN = 'zb-breadth-candidate/1'
FORMAT_OUT = 'zb-factory-stress/1'
STATUS_OK = 'ok'                    # run_breadth_candidate writes 'ok' or 'not emitted'; only 'ok' is accepted
TS_FMT = '%Y-%m-%dT%H:%M:%SZ'       # splits.TS_FMT, the runner's t_in / t_out format

# ------------------------------------------------------------------ pre-registered flags (do not tune after a run)
# Frozen by Integration, source: Codex 6447574071. Flags are DIAGNOSTIC only (report: flags_are_diagnostic = true).
FLAG_SOURCE = 'Codex 6447574071'
TOP1_SHARE_MAX = 0.40               # top-1 share of GROSS POSITIVE R >= this -> CONCENTRATED
HHI_MAX = 0.20                      # OR HHI of the gross-positive shares >= this -> CONCENTRATED
NO_TOP1_NET_R_MIN = 0.0             # net R without the top-1 symbol <= this -> DEPENDENT
NO_TOP3_NET_R_MIN = 0.0             # net R without the top-3 symbols <= this -> TOP3_DEPENDENT
BOOT_P_NONPOS_MAX = 0.10            # bootstrap P(net R <= 0) >= this -> FRAGILE
FEES_X2_NET_R_MIN = 0.0             # net R with fees x2 <= this -> COST_FRAGILE
COST_NET_R_MIN = 0.0                # OR any AVAILABLE mandatory cost stress net R <= this -> COST_FRAGILE
MAX_DD_R_MAX = 25.0                 # observed max DD (R) >= this -> HIGH_DD
BOOT_DD_P95_MAX = 35.0              # OR bootstrap DD p95 (R) >= this -> HIGH_DD

DEFAULT_BOOT, DEFAULT_SEED, DEFAULT_SLIP_BPS = 2000, 20261027, 2.0
FEE_MULTS, FUNDING_MULT = (1.5, 2.0), 2.0
LEAVE_OUT_K = (1, 2, 3)
MIN_ABS_R, MIN_ABS_PNL = 1e-3, 1e-3

TOP_KEYS = ('account', 'bust', 'cache', 'commit', 'costs', 'definitions', 'engine', 'evidence_class', 'format', 'nan_qv',
            'non_promotable', 'packet_sha256', 'purpose', 'raw_entry_signals', 'runner_sha256', 'side', 'sleeve', 'stats',
            'status', 'strategy', 'trades', 'tree', 'universe', 'warmup', 'window')
TRADE_CORE = ('symbol', 't_in', 't_out', 'pnl', 'R')        # missing / null -> fail closed
TRADE_COST = ('fees', 'funding')                             # missing / null -> the stresses needing them are UNAVAILABLE
ND = 6


class StressError(RuntimeError):
    pass


def canon(obj) -> bytes:
    """run_vwaprev_candidate._canon, the runner's packet_sha256 canonical form."""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _r(x):
    return None if x is None else round(float(x), ND)


def _bad_const(name):
    raise StressError(f'factory stress: non-finite JSON constant {name}')


# ------------------------------------------------------------------ load + fail-closed validation
def _finite_tree(x, where='packet'):
    if isinstance(x, float) and not math.isfinite(x):
        raise StressError(f'factory stress: non-finite number at {where}')
    if isinstance(x, dict):
        for k, v in x.items():
            _finite_tree(v, f'{where}.{k}')
    elif isinstance(x, list):
        for i, v in enumerate(x):
            _finite_tree(v, f'{where}[{i}]')


def _ts(s, where):
    if not isinstance(s, str):
        raise StressError(f'factory stress: {where} is not a timestamp string')
    try:
        return int(datetime.strptime(s, TS_FMT).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        raise StressError(f'factory stress: {where}={s!r} is not {TS_FMT}') from None


def _num(v, where):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise StressError(f'factory stress: {where} is missing / null / not a number')
    if not math.isfinite(float(v)):
        raise StressError(f'factory stress: {where} is non-finite')
    return float(v)


def validate(pkt: dict, label: str = 'packet') -> dict:
    """Fail closed on anything that is not a complete, self-consistent runner packet. Returns the packet."""
    if not isinstance(pkt, dict):
        raise StressError(f'factory stress: {label} is not a JSON object')
    miss = [k for k in TOP_KEYS if k not in pkt]
    if miss:
        raise StressError(f'factory stress: {label} lacks top-level fields {miss}')
    _finite_tree(pkt, label)
    body = {k: v for k, v in pkt.items() if k != 'packet_sha256'}
    sha = hashlib.sha256(canon(body)).hexdigest()
    if pkt['packet_sha256'] != sha:
        raise StressError(f'factory stress: {label} packet_sha256 mismatch (recorded {pkt["packet_sha256"]}, '
                          f'computed {sha})')
    if pkt['format'] != FORMAT_IN:
        raise StressError(f'factory stress: {label} format {pkt["format"]!r} != {FORMAT_IN!r}')
    if pkt['status'] != STATUS_OK:
        raise StressError(f'factory stress: {label} status {pkt["status"]!r} != {STATUS_OK!r} (incomplete run)')
    if not isinstance(pkt['trades'], list):
        raise StressError(f'factory stress: {label} trades is not a list')
    if not isinstance(pkt['strategy'], dict) or 'key' not in pkt['strategy']:
        raise StressError(f'factory stress: {label} strategy.key missing')
    for i, t in enumerate(pkt['trades']):
        if not isinstance(t, dict):
            raise StressError(f'factory stress: {label} trade {i} is not an object')
        for k in TRADE_CORE:
            if k not in t:
                raise StressError(f'factory stress: {label} trade {i} lacks {k}')
        if not isinstance(t['symbol'], str) or not t['symbol']:
            raise StressError(f'factory stress: {label} trade {i} symbol invalid')
        _num(t['pnl'], f'{label} trade {i} pnl')
        _num(t['R'], f'{label} trade {i} R')
        if _ts(t['t_out'], f'{label} trade {i} t_out') < _ts(t['t_in'], f'{label} trade {i} t_in'):
            raise StressError(f'factory stress: {label} trade {i} exits before it enters')
    bust_echo(pkt, label)                                               # fail closed on a malformed bust record
    agg = (pkt.get('stats') or {}).get('aggregate')
    if not isinstance(agg, dict) or 'trades' not in agg or 'r_sum' not in agg:
        raise StressError(f'factory stress: {label} stats.aggregate.trades / r_sum missing')
    if agg['trades'] != len(pkt['trades']):
        raise StressError(f'factory stress: {label} stats.aggregate.trades {agg["trades"]} != {len(pkt["trades"])} trades')
    rs = sum(float(t['R']) for t in pkt['trades'])
    if abs(rs - _num(agg['r_sum'], f'{label} stats.aggregate.r_sum')) > 1e-6 * (len(pkt['trades']) + 1):
        raise StressError(f'factory stress: {label} trade R sum {rs} != stats.aggregate.r_sum {agg["r_sum"]}')
    return pkt


def bust_echo(pkt: dict, label: str = 'packet') -> dict:
    """F12-BUST-ECHO-01 (Cowork 6450991919, Codex 6451073431): the runner's trades / stats.aggregate include any trades
    entered after a bust, so the report echoes the slice's bust record. Disclosure only, not a flag."""
    b = pkt.get('bust')
    if not isinstance(b, dict) or not isinstance(b.get('busted'), bool):
        raise StressError(f'factory stress: {label} bust.busted missing or not a boolean')
    post = ((pkt.get('stats') or {}).get('post_bust') or {}).get('aggregate', {}).get('trades')
    after = b.get('trades_after')
    n = post if post is not None else after
    if n is None:
        n = 0 if not b['busted'] else None
    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 0):
        raise StressError(f'factory stress: {label} post-bust trade count {n!r} invalid')
    if b['busted'] and n is None:
        raise StressError(f'factory stress: {label} busted but its post-bust trade count is missing')
    if post is not None and after is not None and post != after:
        raise StressError(f'factory stress: {label} stats.post_bust trades {post} != bust.trades_after {after}')
    return dict(busted=b['busted'], time=b.get('time'), post_bust_trades=n)


def load(path: str) -> dict:
    with open(path, 'rb') as f:
        raw = f.read()
    try:
        pkt = json.loads(raw.decode('utf-8'), parse_constant=_bad_const)
    except (ValueError, UnicodeDecodeError) as e:
        raise StressError(f'factory stress: {path} is not valid JSON ({e})') from None
    return validate(pkt, os.path.basename(path))


# ------------------------------------------------------------------ trade rows
def rows_of(pkt: dict, pidx: int) -> list:
    out = []
    for j, t in enumerate(pkt['trades']):
        out.append(dict(p=pidx, j=j, symbol=t['symbol'], t_in=_ts(t['t_in'], 't_in'), t_out=_ts(t['t_out'], 't_out'),
                        t_out_s=t['t_out'], R=float(t['R']), pnl=float(t['pnl']),
                        fees=t.get('fees'), funding=t.get('funding')))
    return out


def exit_order(rows: list) -> list:
    """Exit time, then entry time, then packet order, then the packet's own trade order (the runner's sort)."""
    return sorted(rows, key=lambda r: (r['t_out'], r['t_in'], r['p'], r['j']))


# ------------------------------------------------------------------ 1. concentration
def pf_r(rs):
    w = sum(x for x in rs if x > 0)
    l = -sum(x for x in rs if x < 0)
    return _r(w / l) if l > 0 else None


def share(x, net):
    return _r(x / net) if net > 0 else None


def concentration(rows: list) -> dict:
    net = sum(r['R'] for r in rows)
    by = {}
    for r in rows:
        by[r['symbol']] = by.get(r['symbol'], 0.0) + r['R']
    rank = sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))            # largest R first; ties by symbol name
    pos = sum(v for v in by.values() if v > 0)                            # gross positive R (symbols with net R > 0)
    hhi = _r(sum((v / pos) ** 2 for v in by.values() if v > 0)) if pos > 0 else None

    def gp_share(top):
        return _r(sum(v for _, v in top if v > 0) / pos) if pos > 0 else None
    leave = {}
    for k in LEAVE_OUT_K:
        drop = {s for s, _ in rank[:k]}
        rest = [r['R'] for r in rows if r['symbol'] not in drop]
        leave[str(k)] = dict(dropped=[s for s, _ in rank[:k]], trades=len(rest), net_r=_r(sum(rest)), pf_r=pf_r(rest))
    periods = {}
    for name, f in (('month', lambda s: s[:7]), ('quarter', lambda s: f'{s[:4]}-Q{(int(s[5:7]) - 1) // 3 + 1}')):
        g = {}
        for r in rows:
            g[f(r['t_out_s'])] = g.get(f(r['t_out_s']), 0.0) + r['R']
        top = sorted(g.items(), key=lambda kv: (-kv[1], kv[0]))[0] if g else (None, 0.0)
        periods[name] = dict(r_by_period={k: _r(v) for k, v in sorted(g.items())}, top_period=top[0],
                             top_period_r=_r(top[1]), top_period_share=share(top[1], net))
    return dict(
        net_r=_r(net), trades=len(rows), pf_r=pf_r([r['R'] for r in rows]),
        share_basis='PRIMARY: symbol R / gross positive R (sum of R over symbols whose net R > 0; defined even when net '
                    'R <= 0, None only when no symbol is net positive); HHI over the same gross-positive shares. '
                    'SECONDARY (*_net_secondary): symbol R / net R, None when net R <= 0',
        r_by_symbol={s: _r(v) for s, v in rank}, gross_positive_r=_r(pos),
        top1_symbol=rank[0][0] if rank else None, top1_r=_r(rank[0][1]) if rank else None,
        top1_share=gp_share(rank[:1]), top3_share=gp_share(rank[:3]),
        top1_share_net_secondary=share(rank[0][1], net) if rank else None,
        top3_share_net_secondary=share(sum(v for _, v in rank[:3]), net) if rank else None,
        hhi_positive=hhi, without_top1=leave['1'], leave_top_k_out=leave, periods=periods)


# ------------------------------------------------------------------ 2. drawdown
def max_dd(rs) -> float:
    peak = cum = dd = 0.0
    for x in rs:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def losing_streak(rs) -> int:
    best = cur = 0
    for x in rs:
        cur = cur + 1 if x < 0 else 0
        best = max(best, cur)
    return best


def nearest_rank(sorted_xs, p):
    return sorted_xs[max(0, math.ceil(p * len(sorted_xs)) - 1)]


def bootstrap(rs, n_boot, seed) -> dict:
    if not rs:
        return dict(n=n_boot, seed=seed, dd_p50=None, dd_p95=None, p_net_le_0=None, note='no trades')
    rng = random.Random(seed)
    n = len(rs)
    dds, nonpos = [], 0
    for _ in range(n_boot):
        draw = [rs[rng.randrange(n)] for _ in range(n)]
        dds.append(max_dd(draw))
        nonpos += sum(draw) <= 0
    dds.sort()
    return dict(n=n_boot, seed=seed, method='iid resample of trade R with replacement, n draws per path; '
                'random.Random(seed).randrange; nearest-rank percentiles', dd_p50=_r(nearest_rank(dds, 0.50)),
                dd_p95=_r(nearest_rank(dds, 0.95)), p_net_le_0=_r(nonpos / n_boot))


def drawdown(rows: list, n_boot: int, seed: int) -> dict:
    rs = [r['R'] for r in exit_order(rows)]
    return dict(order='t_out, t_in, packet order, packet trade order', max_dd_r=_r(max_dd(rs)),
                longest_losing_streak=losing_streak(rs), bootstrap=bootstrap(rs, n_boot, seed))


# ------------------------------------------------------------------ 3. cost stress
def _unavail(reason):
    return dict(status='UNAVAILABLE', reason=reason, net_r=None)


def cost_stress(rows: list, costs_list: list, slip_bps: float) -> dict:
    """costs_list: the `costs` dict of every packet the rows came from (row['p'] indexes it)."""
    base = sum(r['R'] for r in rows)
    out = {'base_net_r': _r(base), 'slip_bps_per_side': slip_bps}
    names = [f'fees_x{m:g}' for m in FEE_MULTS] + [f'funding_x{FUNDING_MULT:g}', f'slip_plus_{slip_bps:g}bps']
    risk_bad = [r for r in rows if abs(r['R']) < MIN_ABS_R or abs(r['pnl']) < MIN_ABS_PNL]
    no_fee = [r for r in rows if not isinstance(r['fees'], (int, float)) or isinstance(r['fees'], bool)]
    no_fund = [r for r in rows if not isinstance(r['funding'], (int, float)) or isinstance(r['funding'], bool)]
    taker = {}
    for i, c in enumerate(costs_list):
        ft = (c or {}).get('fee_taker')
        if isinstance(ft, (int, float)) and not isinstance(ft, bool) and ft > 0 \
                and 'taker' in str((c or {}).get('entry_order', '')):
            taker[i] = float(ft)
    common = None
    if not rows:
        common = 'no trades'
    elif risk_bad:
        common = (f'{len(risk_bad)} trade(s) with |R| < {MIN_ABS_R:g} or |pnl| < {MIN_ABS_PNL:g}: risk_usd = pnl / R '
                  'not exact from the 6-dp fields')
    for nm in names:
        if common:
            out[nm] = _unavail(common)
    if common:
        return out

    def risk(r):
        return r['pnl'] / r['R']

    for m in FEE_MULTS:
        nm = f'fees_x{m:g}'
        out[nm] = _unavail(f'{len(no_fee)} trade(s) lack a numeric fees field') if no_fee else dict(
            status='OK', net_r=_r(base - sum((m - 1) * r['fees'] / risk(r) for r in rows)))
    nm = f'funding_x{FUNDING_MULT:g}'
    out[nm] = _unavail(f'{len(no_fund)} trade(s) lack a numeric funding field') if no_fund else dict(
        status='OK', net_r=_r(base - sum((FUNDING_MULT - 1) * r['funding'] / risk(r) for r in rows)))
    nm = f'slip_plus_{slip_bps:g}bps'
    if no_fee:
        out[nm] = _unavail(f'{len(no_fee)} trade(s) lack a numeric fees field (traded notional = fees / fee_taker)')
    elif any(r['p'] not in taker for r in rows):
        out[nm] = _unavail('costs.fee_taker missing / not > 0 or entry_order not taker: traded notional not derivable')
    else:
        out[nm] = dict(status='OK', net_r=_r(base - sum(slip_bps / 1e4 * (r['fees'] / taker[r['p']]) / risk(r)
                                                        for r in rows)))
    return out


# ------------------------------------------------------------------ 4. pyramid attribution
def pyramid(pkts: list) -> dict:
    return dict(status='UNAVAILABLE', reason='the runner trade schema has no add-leg marker: backtest.run merges every '
                'pyramid add into the one position (avg price, total qty) and writes one trade row per position',
                mgmt_pyramid=[(p.get('strategy') or {}).get('mgmt', {}).get('pyramid') for p in pkts])


# ------------------------------------------------------------------ 5. flags
def _any_flag(checks, rule):
    """checks: [(name, value, threshold, trips(value))]. Raised when any computable check trips; None when none is
    computable (never silently False)."""
    vals = {n: dict(value=v, threshold=t, tripped=None if v is None else bool(f(v))) for n, v, t, f in checks}
    known = [x['tripped'] for x in vals.values() if x['tripped'] is not None]
    out = dict(raised=any(known) if known else None, rule=rule, checks=vals)
    if not known:
        out['note'] = 'not computable (UNAVAILABLE / undefined)'
    return out


def _le(thr):
    return lambda v: v <= thr


def _ge(thr):
    return lambda v: v >= thr


def flags(sec: dict) -> dict:
    c, d, s = sec['concentration'], sec['drawdown'], sec['cost_stress']
    lv = c['leave_top_k_out']
    stresses = [k for k in s if k not in ('base_net_r', 'slip_bps_per_side')]      # the 4 mandatory cost stresses
    cost = _any_flag([('fees_x2', s['fees_x2']['net_r'], FEES_X2_NET_R_MIN, _le(FEES_X2_NET_R_MIN))]
                     + [(k, s[k]['net_r'], COST_NET_R_MIN, _le(COST_NET_R_MIN)) for k in stresses if k != 'fees_x2'],
                     'fees x2 net R <= 0 OR any available mandatory cost stress net R <= 0 (UNAVAILABLE never trips)')
    cost['unavailable'] = [k for k in stresses if s[k].get('status') == 'UNAVAILABLE']
    return {
        'CONCENTRATED': _any_flag([('top1_share_gross_positive', c['top1_share'], TOP1_SHARE_MAX, _ge(TOP1_SHARE_MAX)),
                                   ('hhi_gross_positive', c['hhi_positive'], HHI_MAX, _ge(HHI_MAX))],
                                  'top-1 share of gross positive R >= 0.40 OR HHI >= 0.20'),
        'DEPENDENT': _any_flag([('net_r_without_top1', lv['1']['net_r'] if c['trades'] else None, NO_TOP1_NET_R_MIN,
                                 _le(NO_TOP1_NET_R_MIN))], 'net R without the top-1 symbol <= 0'),
        'TOP3_DEPENDENT': _any_flag([('net_r_without_top3', lv['3']['net_r'] if c['trades'] else None,
                                      NO_TOP3_NET_R_MIN, _le(NO_TOP3_NET_R_MIN))], 'net R without the top-3 symbols <= 0'),
        'FRAGILE': _any_flag([('boot_p_net_le_0', d['bootstrap']['p_net_le_0'], BOOT_P_NONPOS_MAX,
                               _ge(BOOT_P_NONPOS_MAX))], 'bootstrap P(net R <= 0) >= 0.10'),
        'COST_FRAGILE': cost,
        'HIGH_DD': _any_flag([('max_dd_r', d['max_dd_r'], MAX_DD_R_MAX, _ge(MAX_DD_R_MAX)),
                              ('boot_dd_p95', d['bootstrap']['dd_p95'], BOOT_DD_P95_MAX, _ge(BOOT_DD_P95_MAX))],
                             'observed max DD >= 25R OR bootstrap DD p95 >= 35R'),
    }


# ------------------------------------------------------------------ report
def section(rows, pkts, costs_list, n_boot, seed, slip_bps) -> dict:
    sec = dict(concentration=concentration(rows), drawdown=drawdown(rows, n_boot, seed),
               cost_stress=cost_stress(rows, costs_list, slip_bps), pyramid_attribution=pyramid(pkts))
    sec['flags'] = flags(sec)
    return sec


def tool_sha() -> str:
    with open(os.path.abspath(__file__), 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def analyse(pkts: list, labels: list, *, n_boot=DEFAULT_BOOT, seed=DEFAULT_SEED, slip_bps=DEFAULT_SLIP_BPS) -> dict:
    if not pkts:
        raise StressError('factory stress: at least one packet is required')
    if n_boot < 1:
        raise StressError('factory stress: --boot must be >= 1')
    if not math.isfinite(slip_bps) or slip_bps < 0:
        raise StressError('factory stress: --slip-bps must be a finite number >= 0')
    for p, lb in zip(pkts, labels):
        validate(p, lb)
    shas = [p['packet_sha256'] for p in pkts]
    if len(set(shas)) != len(shas):
        raise StressError('factory stress: the same packet was given twice')
    ident = {(p['strategy']['key'], p['side']) for p in pkts}
    if len(ident) != 1:
        raise StressError(f'factory stress: packets mix strategies / sides {sorted(ident)}; refusing to pool')
    costs = [p['costs'] for p in pkts]
    per, pooled_rows = [], []
    for i, (p, lb) in enumerate(zip(pkts, labels)):
        rows = rows_of(p, i)
        pooled_rows += rows
        s = section(rows, [p], costs, n_boot, seed, slip_bps)          # rows carry p=i, so costs[i] is used
        rd = (p.get('stats') or {}).get('aggregate', {}).get('max_dd_r')
        s['runner_crosscheck'] = dict(runner_max_dd_r=rd, match=(rd is not None and s['drawdown']['max_dd_r'] is not None
                                                                 and abs(rd - s['drawdown']['max_dd_r']) <= 1e-6 * (len(rows) + 1)))
        per.append(dict(label=lb, packet_sha256=p['packet_sha256'], window=p['window'], commit=p['commit'],
                        bust=bust_echo(p, lb), **s))
    pooled = section(pooled_rows, pkts, costs, n_boot, seed, slip_bps)
    key, side = next(iter(ident))
    return dict(format=FORMAT_OUT, non_promotable=True, flags_are_diagnostic=True, flag_source=FLAG_SOURCE, purpose='concentration / drawdown / cost stress of factory '
                'candidate packets (research only)', tool_sha256=tool_sha(), strategy=key, side=side,
                params=dict(boot=n_boot, seed=seed, slip_bps=slip_bps, fee_mults=list(FEE_MULTS),
                            funding_mult=FUNDING_MULT, leave_out_k=list(LEAVE_OUT_K)),
                thresholds=dict(TOP1_SHARE_MAX=TOP1_SHARE_MAX, HHI_MAX=HHI_MAX, NO_TOP1_NET_R_MIN=NO_TOP1_NET_R_MIN,
                                NO_TOP3_NET_R_MIN=NO_TOP3_NET_R_MIN, BOOT_P_NONPOS_MAX=BOOT_P_NONPOS_MAX,
                                FEES_X2_NET_R_MIN=FEES_X2_NET_R_MIN, COST_NET_R_MIN=COST_NET_R_MIN,
                                MAX_DD_R_MAX=MAX_DD_R_MAX, BOOT_DD_P95_MAX=BOOT_DD_P95_MAX),
                bust_disclosure=dict(
                    any_busted=any(x['bust']['busted'] for x in per),
                    slices_with_post_bust_trades=[x['label'] for x in per if x['bust']['post_bust_trades']],
                    pooled_includes_post_bust_trades=any(x['bust']['post_bust_trades'] for x in per),
                    note='trades entered after a bust are INCLUDED in every section (runner stats.aggregate does the same)'),
                packets=per, pooled=pooled)


def summary(rep: dict) -> str:
    def line(name, s):
        c, d, cs, f = s['concentration'], s['drawdown'], s['cost_stress'], s['flags']
        raised = [k for k, v in f.items() if v['raised']] or ['none']
        na = [k for k, v in f.items() if v['raised'] is None]
        return (f"{name}: {c['trades']} trades, net {c['net_r']}R, top1 {c['top1_symbol']} {c['top1_share']} of gross+R, "
                f"w/o top1 {c['without_top1']['net_r']}R, maxDD {d['max_dd_r']}R, boot DD p95 {d['bootstrap']['dd_p95']}R "
                f"P(net<=0) {d['bootstrap']['p_net_le_0']}, fees x2 {cs['fees_x2']['net_r']}R | diagnostic flags: {','.join(raised)}"
                + (f" | n/a: {','.join(na)}" if na else ''))
    out = [f"factory stress {rep['strategy']} {rep['side']} (boot {rep['params']['boot']}, seed {rep['params']['seed']})"]
    out += [line(p['label'], p) + (f" | BUSTED {p['bust']['time']}, {p['bust']['post_bust_trades']} post-bust trades "
                                   'included' if p['bust']['busted'] else '') for p in rep['packets']]
    bd = rep['bust_disclosure']
    out.append(line('POOLED', rep['pooled']) + (f" | includes post-bust trades from {','.join(bd['slices_with_post_bust_trades'])}"
                                                 if bd['pooled_includes_post_bust_trades'] else ''))
    return '\n'.join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--packet', action='append', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--boot', type=int, default=DEFAULT_BOOT)
    ap.add_argument('--seed', type=int, default=DEFAULT_SEED)
    ap.add_argument('--slip-bps', type=float, default=DEFAULT_SLIP_BPS)
    a = ap.parse_args(argv)
    try:
        if os.path.exists(a.out):
            raise StressError(f'factory stress: {a.out} exists; a report is never overwritten')
        pkts = [load(p) for p in a.packet]
        rep = analyse(pkts, [os.path.basename(p) for p in a.packet], n_boot=a.boot, seed=a.seed, slip_bps=a.slip_bps)
    except StressError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tmp = a.out + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(json.dumps(rep, sort_keys=True, indent=1, allow_nan=False).encode())
    os.replace(tmp, a.out)
    print(summary(rep))
    return 0


if __name__ == '__main__':
    sys.exit(main())
