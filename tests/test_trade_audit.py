"""T05a draft: pure trade-audit functions (observe only)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trade_audit as TA                                                  # noqa: E402


def lot(side='LONG', avg=100.0, qty=1.0, **k):
    return dict(dict(symbol='BTCUSDT', side=side, avg=avg, qty=qty, e0=avg, R=2.0, risk_usd=2.0, mgmt=dict(tp_r=2.0)), **k)


def test_long_excursions_peak_and_target_touch():
    l = lot()
    for t, m in enumerate([101, 103, 99, 104.5, 102]): TA.observe(l, float(m), f't{t}', fee_rate=0.0)
    ex = l['ex']
    assert (ex['mfe_px'], ex['mfe_t'], ex['mae_px'], ex['mae_t']) == (104.5, 't3', 99.0, 't2')
    assert ex['peak_pnl'] == 4.5 and ex['peak_r'] == 2.25 and ex['target_touch_t'] == 't3'   # target 100 + 2*2 = 104


def test_short_is_mirrored():
    l = lot(side='SHORT')
    for t, m in enumerate([99, 97, 101]): TA.observe(l, float(m), f't{t}', fee_rate=0.0)
    assert (l['ex']['mfe_px'], l['ex']['mae_px'], l['ex']['peak_pnl']) == (97.0, 101.0, 3.0)


def test_bad_marks_are_ignored_and_never_raise():
    l = lot()
    for m in (None, 'x', float('nan'), -1.0, 0.0, True): assert TA.observe(l, m, 't') is None
    assert 'ex' not in l
    assert TA.observe({}, 100.0, 't') is None


def test_close_record_giveback_and_labelled_counterfactuals():
    l = lot()
    for t, m in enumerate([102, 105, 101]): TA.observe(l, float(m), f't{t}', fee_rate=0.0)
    rec = TA.close_record(l, net_pnl=1.0, fee_rate=0.0, path=[('t0', 102.0), ('t1', 105.0), ('t2', 101.0)])
    assert rec['peak_pnl'] == 5.0 and rec['giveback'] == 4.0 and rec['giveback_pct'] == 80.0 and rec['giveback_r'] == 2.0
    kinds = {c['kind']: c for c in rec['counterfactuals']}
    assert kinds['hindsight_ceiling']['decision_t'] == 't1' and 'NOT available live' in kinds['hindsight_ceiling']['info_cutoff']
    cp = next(c for c in rec['counterfactuals'] if c.get('rule') == TA.RULE_TARGET)
    assert cp['decision_t'] == cp['decision_at']['t'] == 't1' and cp['causal_ok'] and cp['value'] > rec['net_pnl']   # 104 at t1
    assert cp['info_cutoff']['t'] <= 't1' and cp['predeclared'] and kinds['causal_policy']['policy_v'] == 1


def test_causal_policy_uses_only_information_up_to_its_decision():
    """The target-close decision is the FIRST touch in time order; changing the path after it changes nothing."""
    l = lot(); TA.observe(l, 104.0, 't0')
    a = TA.close_record(l, 0.0, path=[('t0', 104.0), ('t1', 90.0)])
    b = TA.close_record(l, 0.0, path=[('t0', 104.0), ('t1', 150.0)])
    ca = [c for c in a['counterfactuals'] if c['kind'] == 'causal_policy'][0]
    cb = [c for c in b['counterfactuals'] if c['kind'] == 'causal_policy'][0]
    assert ca == cb


def test_without_a_path_the_policy_says_why():
    l = lot(); TA.observe(l, 101.0, 't0')
    cp = [c for c in TA.close_record(l, 0.5, path=[])['counterfactuals'] if c['kind'] == 'causal_policy'][0]
    assert cp['value'] is None and 'no time-ordered price path' in cp['limitations']


def test_engine_records_excursions_and_writes_an_audit_record_at_close():
    import json
    from test_safety import mk_engine, opened
    e, _ = mk_engine()
    k = opened(e)
    for m in (101.0, 104.0, 99.5, 102.0):
        e.trade.mark['BTCUSDT'] = m; e.manage(e.trade.marks())
    ex = e.state['lots'][k]['ex']
    assert ex['mfe_px'] == 104.0 and ex['mae_px'] == 99.5 and ex['n'] >= 4
    e.close_lot(k, 'signal', mark=102.0)
    assert e._auditw.flush(5)
    rec = [json.loads(l) for l in open(e.F['audit'])][-1]
    assert rec['kind'] == 'trade_audit' and rec['id'] == k and rec['mfe_px'] == 104.0 and rec['giveback'] > 0
    assert {c['kind'] for c in rec['counterfactuals']} >= {'hindsight_ceiling', 'causal_policy'}


def test_every_known_gate_text_has_a_stable_code():
    cases = {
        'not connected to Binance': ('connectivity', 'not_connected'),
        'hedge mode off (shorts unavailable)': ('side_mask', 'hedge_off'),
        'daily loss halt': ('filter', 'halt'), 'daily loss halt is active': ('filter', 'halt'),
        'entries paused': ('filter', 'paused'), 'coin switched off': ('filter', 'coin_off'),
        'strategy slot switched off': ('filter', 'slot_off'), 'outside entry hours': ('filter', 'hours'),
        'volatility filter': ('filter', 'volatility'), 'already in a trade on this coin': ('capacity', 'in_trade'),
        'an entry is already working on this coin': ('capacity', 'entry_working'),
        'max positions reached (2)': ('capacity', 'max_positions'), 'regime: bear market': ('regime', 'regime'),
        'regime unknown (x)': ('regime', 'regime_unknown'),
        'pump guard: signal candle 3.1 ATR > 3': ('risk_gateway', 'pump_guard'),
        'an open trade on this coin is waiting for its stop to be confirmed': ('risk_gateway', 'stop_unconfirmed'),
        'Binance holds an untracked position on this coin/side - resolve it first': ('risk_gateway', 'untracked_position'),
        'FOOUSDT is not tradable': ('risk_gateway', 'not_tradable'),
        'maker entry not filled (no market fallback)': ('execution', 'maker_unfilled'),
        'order failed': ('execution', 'order_failed'), 'WARNING: coin cap': ('filter', 'warning'),
        'something new': ('other', 'other'), None: ('other', 'other'),
    }
    for text, want in cases.items():
        assert TA.reason_code(text) == want, text


def test_miss_records_the_code_and_keeps_the_text(tmp_path):
    from test_safety import mk_engine, SL, SG
    e, _ = mk_engine()
    e.miss(SL, 'BTCUSDT', 'LONG', SG, 'max positions reached (2)')
    m = e.missed[-1]
    assert m['reason'] == 'max positions reached (2)' and (m['stage'], m['code']) == ('capacity', 'max_positions')


# ------------------------------------------------------------------ T05a: more predeclared causal policies
import random                                                              # noqa: E402
from datetime import datetime, timedelta, timezone                         # noqa: E402

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def iso(h): return (T0 + timedelta(hours=h)).isoformat(timespec='seconds')


def plot(side='LONG', **k):
    """A lot shaped like engine._create_lot's (entry time in 'opened', tf, atr0/atr_now, mgmt), with the state snapshot
    observe() records at entry (policies are valued with the state valid at their decision time)."""
    l = lot(side=side, **dict(dict(tf='1h', opened=iso(0), atr0=1.0, atr_now=1.0, entry0=100.0), **k))
    TA.observe(l, 100.0, iso(0), fee_rate=0.0)
    return l


def pol(cfs, prefix):
    return next(c for c in cfs if c['kind'] in ('causal_policy', 'what_if') and c['rule'].startswith(prefix))


POLICIES = ('close the remainder', 'trailing stop', 'time cap')


def test_trailing_stop_long_and_short():
    l = plot(mgmt=dict(tp_r=50.0, trail_atr=2.0))                          # stop trails 2 ATR = 2.0 behind the best
    path = [(iso(1), 101.0), (iso(2), 104.0), (iso(3), 102.5), (iso(4), 101.9), (iso(5), 90.0)]
    c = pol(TA.counterfactuals(l, 0.5, 0.0, 0.0, path), 'trailing')
    assert c['decision_t'] == iso(4) and c['causal_ok'] and c['info_cutoff']['t'] == iso(2) and c['k'] == 2.0 and c['stop'] == 102.0
    assert c['value'] == 1.9 and c['vs_actual'] == 1.4 and c['atr_src'] == 'atr0'          # filled at the crossing mark
    s = plot(side='SHORT', mgmt=dict(tp_r=50.0, trail_atr=2.0))
    path = [(iso(1), 99.0), (iso(2), 96.0), (iso(3), 97.5), (iso(4), 98.1), (iso(5), 110.0)]
    c = pol(TA.counterfactuals(s, 0.5, 0.0, 0.0, path), 'trailing')
    assert c['decision_t'] == iso(4) and c['stop'] == 98.0 and c['value'] == 1.9 and c['vs_actual'] == 1.4


def test_trailing_stop_defaults_and_fallbacks():
    l = plot(mgmt=dict(tp_r=2.0), atr0=None, atr_now=1.5)                  # k default 2.0, ATR declared from atr_now (flagged)
    assert l['ap']['policies']['trailing']['params'] == dict(k=2.0, k_src='default', atr=1.5, atr_src='atr_now', best_from=100.0,
                                                             fill='the observed mark that crossed the stop (never improved)')
    c = pol(TA.counterfactuals(l, 0.0, 0.0, 0.0, [(iso(1), 96.9)]), 'trailing')
    assert c['k'] == 2.0 and c['atr'] == 1.5 and c['decision_t'] == iso(1) and c['kind'] == 'causal_policy'
    assert 'default' in c['limitations'] and 'atr_now' in c['limitations']
    l['atr_now'] = 9.0                                                      # a later refresh never changes the declared ATR
    assert pol(TA.counterfactuals(l, 0.0, 0.0, 0.0, [(iso(1), 96.9)]), 'trailing')['atr'] == 1.5
    l = plot(atr0=None, atr_now=None)                                       # ATR from R = 2.0 -> stop 96
    c = pol(TA.counterfactuals(l, 0.0, 0.0, 0.0, [(iso(1), 96.5), (iso(2), 95.9)]), 'trailing')
    assert c['atr_src'] == 'R' and c['decision_t'] == iso(2)
    l = plot(atr0=None, atr_now=None, R=None)
    c = pol(TA.counterfactuals(l, 0.0, 0.0, 0.0, [(iso(1), 50.0)]), 'trailing')
    assert c['value'] is None and 'no ATR' in c['limitations']
    c = pol(TA.counterfactuals(plot(), 0.0, 0.0, 0.0, [(iso(1), 100.5)]), 'trailing')
    assert c['value'] is None and c['decision_t'] is None and 'not triggered' in c['limitations']


def test_time_cap_uses_the_entry_time_and_the_tf_bar_length():
    l = plot(tf='4h')                                                       # 48 x 4h = 192h after 'opened'
    path = [(iso(h), 100.0 + h / 100) for h in (1, 100, 191, 192, 193, 300)]
    c = pol(TA.counterfactuals(l, 1.0, 0.0, 0.0, path), 'time cap')
    assert c['decision_t'] == iso(192) and c['value'] == 1.92 and c['vs_actual'] == 0.92 and c['bars'] == 48
    c = pol(TA.counterfactuals(plot(tf='15m'), 0.0, 0.0, 0.0, path, cap_bars=4), 'time cap')   # 1h
    assert c['decision_t'] == iso(1) and c['bars'] == 4 and c['kind'] == 'what_if' and not c['predeclared']   # not the declared 48
    s = plot(side='SHORT', tf='1h')
    c = pol(TA.counterfactuals(s, 0.0, 0.0, 0.0, [(iso(47), 90.0), (iso(48), 97.0)]), 'time cap')
    assert c['decision_t'] == iso(48) and c['value'] == 3.0                 # short: entry 100, out at 97
    assert TA.tf_seconds('1h') == 3600 and TA.tf_seconds('15m') == 900 and TA.tf_seconds('x') is None


def test_time_cap_missing_inputs_say_why():
    path = [(iso(100), 101.0)]
    for l, why in ((plot(tf=None), 'unknown bar length'), (plot(opened=None), 'no parseable entry time'),
                   (plot(opened='garbage'), 'no parseable entry time')):
        c = pol(TA.counterfactuals(l, 0.0, 0.0, 0.0, path), 'time cap')
        assert c['value'] is None and c['vs_actual'] is None and why in c['limitations']
    c = pol(TA.counterfactuals(plot(), 0.0, 0.0, 0.0, [(iso(10), 101.0)]), 'time cap')
    assert c['value'] is None and 'ends before the cap' in c['limitations']
    c = pol(TA.counterfactuals(plot(), 0.0, 0.0, 0.0, [('bad', 1.0), (iso(48), 101.0)]), 'time cap')
    assert c['decision_t'] == iso(48) and '1 path points had no parseable time' in c['limitations']


def test_every_policy_is_present_and_never_raises_on_junk():
    for l in (plot(), {}, dict(side='LONG'), dict(side='SHORT', avg='x', mgmt=None), plot(mgmt='junk')):
        for path in (None, [], [(iso(1), None), (None, 'x'), (iso(60), float('nan'))], [(iso(50), 120.0)]):
            cfs = TA.counterfactuals(l, 0.0, 0.0005, 2.0, path)
            got = [c for c in cfs if c['kind'] in ('causal_policy', 'what_if')]
            assert [any(c['rule'].startswith(p) for c in got) for p in POLICIES] == [True] * 3
            for c in got:
                assert set(c) >= {'kind', 'rule', 'decision_t', 'info_cutoff', 'value', 'vs_actual', 'limitations'}
                if c['value'] is None: assert c['limitations'] and c['vs_actual'] is None


def test_policies_are_causal_future_data_never_changes_a_decision():
    """Property: for every policy, replacing all path points AFTER its decision time with arbitrary prices leaves its
    decision_t and value unchanged."""
    rng = random.Random(5)
    hits = dict.fromkeys(POLICIES, 0)
    for trial in range(400):
        side = rng.choice(('LONG', 'SHORT'))
        l = plot(side=side, tf=rng.choice(('15m', '1h')), mgmt=dict(tp_r=rng.uniform(0.5, 4.0), trail_atr=rng.uniform(0.5, 3.0)))
        n = rng.randint(1, 80)
        px, path = 100.0, []
        for i in range(n):
            px = max(1.0, px * (1 + rng.gauss(0, 0.01))); path.append((iso(i * rng.choice((0.25, 1))), px))
        path.sort(key=lambda x: x[0])
        net = rng.uniform(-3, 3)
        base = TA.counterfactuals(l, net, 0.0005, 1.0, path, cap_bars=rng.randint(1, 60))
        for p in POLICIES:
            c = pol(base, p)
            if c['decision_t'] is None: continue
            hits[p] += 1
            i = max(k for k, (t, _) in enumerate(path) if t <= c['decision_t'])
            for _ in range(3):
                fut = [(t, rng.choice((rng.uniform(0.1, 1000.0), path[i][1], 1e-9))) for t, _ in path[i + 1:]]
                again = pol(TA.counterfactuals(l, net, 0.0005, 1.0, path[:i + 1] + fut, cap_bars=c.get('bars')), p)
                assert (again['decision_t'], again['value']) == (c['decision_t'], c['value']), (p, trial)
    assert min(hits.values()) >= 20, hits                                  # the property was actually exercised


# ------------------------------------------------------------------ T05a: attribution summary
def crec(sleeve='A', tf='1h', side='LONG', r=1.0, gb=20.0, target=104.0, touch='t', vs=None, ru=None):
    cf = [dict(kind='hindsight_ceiling', rule='best observed mark', vs_actual=9.0)]
    if vs is not None: cf.append(dict(kind='causal_policy', rule=TA.RULE_TARGET, value=1.0, vs_actual=vs))
    return dict(kind='trade_audit', sleeve=sleeve, tf=tf, side=side, r=r, giveback_pct=gb, target=target, target_touch_t=touch,
                counterfactuals=cf, runner=None if ru is None else dict(value_vs_activation=ru))


def test_attribution_groups_counts_and_stats():
    recs = [crec(r=1.0, gb=10.0, vs=0.5, ru=0.75), crec(r=3.0, gb=30.0, touch=None, vs=-2.0, ru=0.75), crec(r=-1.0, gb=None, target=None),
            crec(side='SHORT', r=2.0, vs=1.0, ru=-1.0), crec(sleeve='B', tf='4h', r=None)]
    a = TA.attribution(recs)
    g = {(x['sleeve'], x['tf'], x['side']): x for x in a['groups']}
    x = g[('A', '1h', 'LONG')]
    assert x['n'] == 3 and x['avg_r'] == 1.0 and x['median_r'] == 1.0 and x['avg_giveback_pct'] == 20.0
    assert x['with_target'] == 2 and x['target_touch_rate'] == 0.5                       # no-target record excluded
    assert x['close_at_target_minus_actual'] == -1.5 and x['runner_value'] == 1.5 and x['n_target_policy'] == 2 and x['n_runner'] == 2
    assert g[('A', '1h', 'SHORT')]['runner_value'] == -1.0 and g[('B', '4h', 'LONG')]['avg_r'] is None
    assert a['closes'] == 5 and a['n_groups'] == 3 and a['malformed'] == 0 and a['groups'][0]['n'] == 3


def test_attribution_funnel_histogram_and_other_kinds():
    missed = [dict(reason='max positions reached (2)', stage='capacity', code='max_positions'),
              dict(reason='max positions reached (3)', stage='capacity', code='max_positions'),
              dict(reason='pump guard: big candle'),                                       # old record: classified from text
              dict(reason='WARNING: coin cap', kind='warning'), dict(reason='pyramid_add blocked: x', kind='add_blocked')]
    a = TA.attribution(missed)
    assert a['funnel'] == {'capacity/max_positions': 2, 'risk_gateway/pump_guard': 1} and a['not_taken'] == 3
    assert a['other_kinds'] == {'warning': 1, 'add_blocked': 1} and a['closes'] == 0 and a['malformed'] == 0


def test_attribution_survives_malformed_records():
    junk = [None, 'x', 3, [], {}, dict(kind=None), dict(kind='trade_audit', r='x', giveback_pct=float('nan'), target=True,
            counterfactuals='junk', sleeve=['l'], tf=7), dict(kind='trade_audit', counterfactuals=[None, 'x', dict(rule=TA.RULE_TARGET,
            kind='causal_policy', vs_actual='1')]), dict(reason='x', stage=1, code=None)]
    a = TA.attribution(junk + [crec(r=2.0, vs=1.0)])
    assert a['malformed'] == 6 and a['closes'] == 3 and a['funnel'] == {'other/other': 1}
    g = {(x['sleeve'], x['tf'], x['side']): x for x in a['groups']}
    assert g[('A', '1h', 'LONG')]['avg_r'] == 2.0 and g[('?', '?', '?')]['n'] == 2 and g[('?', '?', '?')]['avg_r'] is None
    assert TA.attribution(None)['closes'] == 0 and TA.attribution(5)['malformed'] == 1


def test_engine_audit_summary_reads_the_window_and_skips_malformed_lines():
    import json, engine as E
    from test_safety import mk_engine, opened
    e, tmp = mk_engine()
    with open(os.path.join(tmp, 'trade_audit.jsonl'), 'w') as f:            # written before the writer exists
        f.write(json.dumps(crec(sleeve='Z', r=1.5, vs=0.25)) + '\n{not json\n[1,2]\n' + json.dumps(dict(kind='')) + '\n')
    E.close_fill_writer(e.F['audit'])
    e2, _ = mk_engine(tmp)
    try:
        e2.miss(__import__('test_safety').SL, 'BTCUSDT', 'LONG', __import__('test_safety').SG, 'entries paused')
        a = e2.audit_summary()
        g = {x['sleeve']: x for x in a['groups']}
        assert g['Z']['avg_r'] == 1.5 and g['Z']['close_at_target_minus_actual'] == 0.25 and g['Z']['runner_value'] is None
        assert a['telemetry']['invalid_records'] == 3 and a['telemetry']['in_window'] == 1
        assert a['funnel'].get('filter/paused') == 1
        k = opened(e2); e2.trade.mark['BTCUSDT'] = 103.0; e2.manage(e2.trade.marks()); e2.close_lot(k, 'signal', mark=103.0)
        assert e2._auditw.flush(5)
        assert e2.audit_summary()['closes'] == 2
    finally:
        E.close_fill_writer(e2.F['audit'])


def test_engine_audit_summary_never_raises(monkeypatch):
    from test_safety import mk_engine
    e, _ = mk_engine()
    monkeypatch.setattr(TA, 'attribution', lambda *a: 1 / 0)
    assert 'error' in e.audit_summary()


def test_status_exposes_the_audit_summary():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py'), encoding='utf-8').read()
    assert "audit=e.audit_summary() if hasattr(e, 'audit_summary') else None" in src


# ------------------------------------------------------------------ T05a: observe only - no behaviour change
def _scenario(monkeypatch, broken):
    import copy, engine as E
    from test_safety import mk_engine, opened, SL, SG
    fixed = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(E, 'now_utc', lambda: fixed)
    monkeypatch.setattr(E.time, 'time', lambda: fixed.timestamp())
    if broken:
        def boom(*a, **k): raise RuntimeError('audit broken')
        for name in ('observe', 'close_record', 'counterfactuals', 'target_price', 'reason_code', 'reason_info', 'attribution',
                     'policy_target', 'policy_trailing', 'policy_time_cap', 'recorded_path', 'state_at', 'final_close_state',
                     'mark_restart', 'slim_record', '_online_step', 'declare', 'checkpoint', 'load_checkpoints',
                     'restore_checkpoint', 'fill_events', 'cohort_ledger', 'hold_eval', 'funnel_event', 'coverage',
                     'coverage_event', 'clean'):
            monkeypatch.setattr(TA, name, boom)
    e, _ = mk_engine()
    if broken: monkeypatch.setattr(e._auditw, 'emit', boom)                # and the audit writer itself refuses everything
    try:
        a = opened(e); b = opened(e, 'ETHUSDT', 'SHORT')
        for btc, eth in ((101.0, 49.5), (104.0, 48.0), (106.5, 47.0), (108.0, 49.0), (103.0, 51.0), (102.0, 50.5)):
            e.trade.mark.update(BTCUSDT=btc, ETHUSDT=eth); e.manage(e.trade.marks())
        e.miss(SL, 'BTCUSDT', 'LONG', dict(SG, time='2026-10-04 04:00:00'), 'max positions reached (2)')
        if a in e.state['lots']: e.close_lot(a, 'signal', mark=102.0)
        e.trade.mark['ETHUSDT'] = 49.0; e.manage(e.trade.marks())
        lots = {k: {f: v for f, v in l.items() if f not in ('ex', 'ap')} for k, l in e.state['lots'].items()}
        missed = [{f: v for f, v in m.items() if f not in ('stage', 'code', 'detail')} for m in e.missed]
        return copy.deepcopy((lots, dict(e.trade.stops), list(e.trade.calls), dict(e.trade.pos), e.history, missed, b in e.state['lots']))
    finally:
        E.close_fill_writer(e.F['audit'])


def test_audit_never_changes_trading_even_when_every_audit_function_raises(monkeypatch):
    normal = _scenario(monkeypatch, False)
    broken = _scenario(monkeypatch, True)
    assert normal == broken
    lots, stops, calls, pos, history, missed, b_open = normal
    assert history and calls.count('stop') >= 2 and missed                 # the scenario really traded, moved stops and closed


# ------------------------------------------------------------------ T05a review fixes (adversarial review findings)
import ast, json                                                           # noqa: E402


def _last_audit(e):
    assert e._auditw.flush(5)
    return [json.loads(x) for x in open(e.F['audit'])][-1]


def _cf(rec, kind, prefix=''):
    return next(c for c in rec['counterfactuals'] if c['kind'] == kind and c['rule'].startswith(prefix))


def _walk(e, marks):
    for m in marks:
        e.trade.mark['BTCUSDT'] = m; e.manage(e.trade.marks())


def test_fee_estimate_matches_the_engine():
    import engine as E
    assert TA.FEE_EST == E.FEE_EST


def test_hindsight_ceiling_on_a_market_close_uses_the_closed_quantity():
    """Review R1: the ceiling was computed on qty=0 for every market close. Entry 100 x 1.0 (fee 0.05), best mark 104,
    closed at 102: actual 2 - 0.05 - 0.051 = 1.899; ceiling 4 - 0.05 - 0.052 = 3.898."""
    import engine as E
    from test_safety import mk_engine, opened
    e, _ = mk_engine()
    try:
        k = opened(e); _walk(e, (101.0, 104.0, 99.5))
        e.trade.mark['BTCUSDT'] = 102.0; e.close_lot(k, 'signal', mark=102.0)
        rec = _last_audit(e); hc = _cf(rec, 'hindsight_ceiling')
        assert rec['net_pnl'] == 1.899 and e.history[-1]['pnl'] == 1.899
        assert hc['value'] == 3.898 and hc['vs_actual'] == 1.999 and hc['final_qty'] == 1.0
    finally:
        E.close_fill_writer(e.F['audit'])


def test_hindsight_ceiling_on_an_exchange_stop_includes_banked_partials_and_all_fees():
    """Review R1b: the exchange-stop path left qty in place and ignored the banked tp1 and the entry fee. Half closed at
    104 (+2.0 banked), rest stopped at 95: the ceiling (final 0.5 closed at the best mark 104) is the SAME number as on
    the market-close path, 2.0 + 0.5 * 4 - (0.05 + 0.026 + 0.026) = 3.898."""
    import engine as E
    from test_safety import mk_engine, opened
    e, _ = mk_engine()
    try:
        k = opened(e); _walk(e, (101.0, 104.0)); lot_ = e.state['lots'][k]
        e._market_close(lot_, 0.5, 'take_profit_1', 104.0)
        _walk(e, (100.0,))
        e.trade.pos[('BTCUSDT', 'LONG')] = 0; e.trade.stops.clear(); e.reconcile(500)
        assert e.history[-1]['exit_reason'] == 'stop'
        rec = _last_audit(e); hc = _cf(rec, 'hindsight_ceiling')
        assert hc['value'] == 3.898 and hc['final_qty'] == 0.5
        assert rec['net_pnl'] == round(2.0 - 2.5 - (0.05 + 0.026 + 0.5 * 95 * 0.0005), 6)
    finally:
        E.close_fill_writer(e.F['audit'])


def test_final_close_state_from_the_fills():
    l = dict(side='SHORT', avg=100.0, qty=0.0, realized=3.0, fees=0.2,
             fills=[['t0', 'entry', 1.0, 100.0], ['t1', 'take_profit_1', 0.5, 96.0], ['t2', 'stop', 0.5, 98.0]])
    fs, note = TA.final_close_state(l, 0.0005)
    assert note is None and fs['qty'] == 0.5 and abs(fs['realized'] - 2.0) < 1e-12 and abs(fs['fees'] - (0.2 - 0.0245)) < 1e-12
    fs, note = TA.final_close_state(dict(side='LONG', avg=100.0, qty=0.3, fills=[['t0', 'entry', 1.0, 100.0]]))
    assert fs['qty'] == 0.3 and 'not found' in note


def test_no_giveback_when_closed_at_the_peak_mark():
    """Review R2: peak was gross, actual net -> a close exactly at the peak reported a positive give-back."""
    import engine as E
    from test_safety import mk_engine, opened
    e, _ = mk_engine()
    try:
        k = opened(e); _walk(e, (101.0, 104.0))
        e.trade.mark['BTCUSDT'] = 104.0; e.close_lot(k, 'signal', mark=104.0)
        rec = _last_audit(e)
        assert e.history[-1]['exit'] == 104.0 and rec['giveback'] == 0 and rec['peak_pnl'] == rec['net_pnl'] == 3.898
        assert rec['peak_r'] == rec['r'] == round(3.898 / 5.0, 4)
    finally:
        E.close_fill_writer(e.F['audit'])


def test_peak_is_net_of_fees_paid_and_the_estimated_exit_fee():
    l = lot(realized=1.0, fees=0.1); TA.observe(l, 103.0, 't0', fee_rate=0.001)
    assert l['ex']['peak_pnl'] == round(1.0 - 0.1 + 3.0 - 0.103, 6) and l['ex']['peak_r'] == round(3.797 / 2.0, 4)


def test_engine_close_evaluates_causal_policies_from_the_live_path():
    """Review R3: production never passed a path, so every causal policy was always None."""
    import engine as E
    from test_safety import mk_engine, opened
    e, _ = mk_engine()
    try:
        k = opened(e); _walk(e, (101.0, 104.0, 106.0, 112.0, 103.0))
        assert len(e.state['lots'][k]['ex']['path']) == 5
        e.trade.mark['BTCUSDT'] = 102.0; e.close_lot(k, 'signal', mark=102.0)
        rec = _last_audit(e)
        tr = _cf(rec, 'causal_policy', 'trailing')                # best 112, k=2 x ATR 2 -> stop 108, crossed at 103
        assert rec['path_points'] >= 5 and tr['stop'] == 108.0 and tr['value'] == round(3.0 - 0.05 - 103 * 0.0005, 6)
    finally:
        E.close_fill_writer(e.F['audit'])


def test_policy_uses_the_state_of_its_observation_even_with_equal_timestamps():
    """A partial close between two observations stamped the same second must not leak into the earlier decision."""
    import engine as E
    from test_safety import mk_engine, opened
    e, _ = mk_engine()
    try:
        k = opened(e); _walk(e, (101.0, 104.0, 99.5)); lot_ = e.state['lots'][k]   # trailing (stop 100) triggers at 99.5
        e._market_close(lot_, 0.5, 'take_profit_1', 104.0); _walk(e, (100.0,))
        assert len({p[0] for p in lot_['ex']['path']}) <= 2                       # same-second stamps (fast test clock)
        tr = TA.policy_trailing(lot_, 0.0, E.FEE_EST, 0.0, TA.recorded_path(lot_))
        assert tr['value'] == round(-0.5 - 0.05 - 99.5 * 0.0005, 6)               # qty 1.0 and nothing banked yet
    finally:
        E.close_fill_writer(e.F['audit'])


def test_live_path_is_bounded_time_ordered_and_keeps_extremes():
    l = plot()
    for i in range(1, 1000): TA.observe(l, 100.0 + (5.0 if i == 777 else 0.001 * (i % 7)), iso(i / 60), fee_rate=0.0)
    ex = l['ex']
    assert len(ex['path']) <= TA.PATH_MAX and ex['stride'] > 1
    ts = [p[0] for p in ex['path']]
    assert ts == sorted(ts) and ts[0] == iso(0)
    rp = TA.recorded_path(l)
    assert [p[0] for p in rp] == sorted(p[0] for p in rp) and (iso(777 / 60), 105.0, 0, 777) in rp and rp[-1][0] == iso(999 / 60)
    assert len(json.dumps(ex)) < 8000


def test_state_snapshots_are_bounded_and_overflow_withholds_later_decisions():
    l = plot()
    for i in range(1, 200):
        l['qty'] = 1.0 + i / 1000; TA.observe(l, 100.0, iso(i), fee_rate=0.0)
    ex = l['ex']
    assert len(ex['st']) == TA.SNAP_MAX and ex['st_full_t'] == iso(TA.SNAP_MAX)
    assert TA.state_at(l, iso(3))[0]['qty'] == 1.003
    assert TA.state_at(l, iso(150))[0] is None and 'full' in TA.state_at(l, iso(150))[1]
    assert ex['path'][-1][2] is None                                        # live point after overflow: state unknown
    c = pol(TA.counterfactuals(l, 0.0, 0.0, 0.0, [(iso(150), 50.0)]), 'trailing')
    assert c['value'] is None and 'full' in c['limitations']


def test_policies_without_snapshots_are_withheld_not_leaked():
    l = lot(tf='1h', opened=iso(0), atr0=1.0, entry0=100.0)                 # an old lot: no 'ex' at all
    for p in POLICIES:
        c = pol(TA.counterfactuals(l, 0.0, 0.0, 0.0, [(iso(1), 104.5), (iso(60), 90.0)]), p)
        assert c['value'] is None and 'snapshot' in c['limitations'], (p, c)


def _with_states(side, states):
    """A lot whose recorded state changed over time: states = [(t, avg, qty, tgt)]."""
    l = plot(side=side)
    l['ex']['st'] = [[t, a, q, g, 2.0, 100.0, 0.0, 0.0] for t, a, q, g in states]
    return l


def test_target_policy_value_independent_of_post_decision_adds():
    """Review R4: a decision at t1 was valued with avg/qty that only existed after a later add."""
    path = [(iso(1), 104.5), (iso(2), 95.0), (iso(3), 99.0)]
    a = _with_states('LONG', [(iso(0), 100.0, 1.0, 104.0)])
    b = _with_states('LONG', [(iso(0), 100.0, 1.0, 104.0), (iso(2), 97.0, 3.0, 101.0)]); b.update(avg=97.0, qty=3.0, tp=101.0)
    ca, cb = (pol(TA.counterfactuals(x, 0.0, 0.0, 0.0, path), 'close the remainder') for x in (a, b))
    assert ca['decision_t'] == cb['decision_t'] == iso(1) and ca['value'] == cb['value'] == 4.0


def test_target_policy_uses_the_target_known_at_decision_time():
    """Review R4b: a DCA basket target moved closer after a safety order; the final tp was applied back to t1."""
    l = _with_states('LONG', [(iso(0), 100.0, 1.0, 104.0), (iso(2), 98.0, 2.0, 101.0)]); l['tp'] = 101.0
    c = pol(TA.counterfactuals(l, 0.0, 0.0, 0.0, [(iso(1), 101.5), (iso(2), 96.0), (iso(3), 101.2)]), 'close the remainder')
    assert c['decision_t'] == iso(3) and c['target'] == 101.0 and c['value'] == 6.0      # 2 x (101 - 98)


def test_tied_snapshot_times_resolve_to_the_earliest_state():
    l = _with_states('LONG', [(iso(0), 100.0, 1.0, 104.0), (iso(1), 100.0, 1.0, 104.0), (iso(1), 90.0, 9.0, 91.0)])
    assert TA.state_at(l, iso(1))[0]['qty'] == 1.0 and TA.state_at(l, iso(2))[0]['qty'] == 9.0


def test_tracking_started_after_entry_is_flagged():
    l = plot(); assert l['ex']['late'] is False
    l2 = lot(tf='1h', opened=iso(0)); TA.observe(l2, 100.0, iso(5))
    l3 = lot(tf='1h', opened=iso(0), fills=[[iso(0), 'entry', 1.0, 100.0], [iso(0), 'take_profit_1', 0.5, 104.0]])
    TA.observe(l3, 100.0, iso(0))
    assert l2['ex']['late'] is True and l3['ex']['late'] is True
    rec = TA.close_record(l2, 0.0)
    assert rec['tracking_late'] is True and any('tracking started after entry' in x for x in rec['limitations'])


def test_observe_ignores_a_lot_with_a_broken_average():
    for bad in (float('nan'), None, 0.0, 'x', float('inf')):
        l = lot(avg=bad); assert TA.observe(l, 100.0, 't') is None and 'ex' not in l
    assert TA.observe(lot(qty=float('nan')), 100.0, 't') is None


def test_policies_are_causal_under_post_decision_state_changes():
    """Property (review R4): for every policy, changing the lot state AFTER its decision (later snapshots with other
    avg/qty/target, and the final lot fields avg/qty/tp/R/e0) together with arbitrary future prices leaves its
    decision_t and value unchanged."""
    rng = random.Random(11)
    hits = dict.fromkeys(POLICIES, 0)
    for trial in range(300):
        side = rng.choice(('LONG', 'SHORT')); sd = 1 if side == 'LONG' else -1
        l = plot(side=side, tf=rng.choice(('15m', '1h')), mgmt=dict(tp_r=rng.uniform(0.5, 4.0), trail_atr=rng.uniform(0.5, 3.0)))
        n = rng.randint(2, 60)
        times = sorted(iso(i * rng.choice((0.25, 1))) for i in range(1, n + 1))
        px, path = 100.0, []
        for t in times: px = max(1.0, px * (1 + rng.gauss(0, 0.01))); path.append((t, px))
        st = [[iso(0), 100.0, 1.0, 100.0 + sd * rng.uniform(0.5, 6.0), 2.0, 100.0, 0.0, 0.05]]
        for t in sorted(rng.sample(times, min(len(times), rng.randint(0, 4)))):
            a = rng.uniform(90, 110); st.append([t, a, rng.uniform(0.2, 3.0), a + sd * rng.uniform(0.5, 6.0), 2.0, a, rng.uniform(-1, 1), 0.1])
        l['ex']['st'] = st
        net, cap = rng.uniform(-3, 3), rng.randint(1, 60)
        base = TA.counterfactuals(l, net, 0.0005, 1.0, path, cap_bars=cap)
        for p in POLICIES:
            c = pol(base, p)
            if c['decision_t'] is None: continue
            hits[p] += 1
            i = max(k for k, (t, _) in enumerate(path) if t <= c['decision_t'])
            for _ in range(3):
                m = json.loads(json.dumps(l))
                m['ex']['st'] = [s for s in st if s[0] <= c['decision_t']] + \
                    [[t, rng.uniform(50, 150), rng.uniform(0.1, 9), rng.uniform(50, 150), 7.0, 120.0, 5.0, 1.0]
                     for t, _ in path[i + 1:] if t > c['decision_t']]
                m.update(avg=rng.uniform(50, 150), qty=rng.uniform(0.1, 9), tp=rng.uniform(50, 150), R=rng.uniform(0.5, 9),
                         e0=rng.uniform(50, 150), realized=9.0, fees=3.0)
                fut = [(t, rng.choice((rng.uniform(0.1, 1000.0), path[i][1], 1e-9))) for t, _ in path[i + 1:]]
                again = pol(TA.counterfactuals(m, net, 0.0005, 1.0, path[:i + 1] + fut, cap_bars=cap), p)
                assert (again['decision_t'], again['value']) == (c['decision_t'], c['value']), (p, trial)
    assert min(hits.values()) >= 20, hits


# ---- funnel: every reason text the engine can produce has a code
REASONS = {
    'not connected to Binance': ('connectivity', 'not_connected'), 'FOOUSDT is not tradable': ('risk_gateway', 'not_tradable'),
    'bad direction': ('input', 'bad_direction'), 'hedge mode off (shorts unavailable)': ('side_mask', 'hedge_off'),
    'daily loss halt': ('filter', 'halt'), 'daily loss halt is active': ('filter', 'halt'),
    'an open trade on this coin is waiting for its stop to be confirmed': ('risk_gateway', 'stop_unconfirmed'),
    'Binance holds an untracked position on this coin/side - resolve it first': ('risk_gateway', 'untracked_position'),
    'entries paused': ('filter', 'paused'), 'coin switched off': ('filter', 'coin_off'),
    'strategy slot switched off': ('filter', 'slot_off'), 'outside entry hours': ('filter', 'hours'),
    'volatility filter': ('filter', 'volatility'), 'already in a trade on this coin': ('capacity', 'in_trade'),
    'an entry is already working on this coin': ('capacity', 'entry_working'),
    'max positions reached (2)': ('capacity', 'max_positions'), 'regime: bear market': ('regime', 'regime'),
    'regime unknown (timeout)': ('regime', 'regime_unknown'),
    'pump guard: signal candle 3.1 ATR > 3': ('risk_gateway', 'pump_guard'),
    'pump guard: BTC moved 2.1% in the last hour (> 2%)': ('risk_gateway', 'pump_guard'),
    'risk rule coin_cap: BTCUSDT exposure 900 USDT > 1.5x bot capital (750)': ('risk_gateway', 'coin_cap'),
    'risk rule open_risk_cap: open risk to stops 7.0% > 6% of bot capital': ('risk_gateway', 'open_risk_cap'),
    'risk rule correlated_cap: 3 open longs on coins correlated > 0.8 with ETHUSDT (max 2)': ('risk_gateway', 'correlated_cap'),
    'risk rule btc_breaker: BTC moved 3.1% in an hour - entries paused until 2026-10-06T12:00+00:00': ('risk_gateway', 'btc_breaker'),
    'risk rule funding_filter: funding 0.050% against a long (limit 0.030%)': ('risk_gateway', 'funding_filter'),
    'DCA basket without a hard stop (stop_atr) is not allowed': ('config', 'dca_no_stop'),
    'DCA settings out of range (n 1-8, scale 1-3)': ('config', 'dca_range'),
    'leverage cap reached for this slot': ('capacity', 'leverage_cap'),
    'size below Binance minimum (raise capital or risk)': ('execution', 'size_min'),
    'Claude review vetoed: weak trend': ('filter', 'ai_veto'),
    'could not set leverage/margin on Binance: leverage 20x refused': ('execution', 'leverage'),
    'entry order unconfirmed': ('execution', 'entry_unconfirmed'),
    'stop order failed - trade closed again': ('execution', 'stop_failed'),
    'order failed': ('execution', 'order_failed'),
    'order failed: BinanceError -4028 Leverage 30 is not valid': ('execution', 'order_failed'),
    'order failed: minimum notional too small': ('execution', 'order_failed'),
    'trailing entry expired (no rebound in time)': ('trailing', 'expired'),
    'trailing entry cancelled: max positions reached (2)': ('trailing', 'max_positions'),
    'trailing entry cancelled: risk rule coin_cap: x': ('trailing', 'coin_cap'),
    'maker entry not filled (no market fallback)': ('execution', 'maker_unfilled'),
    'maker entry not filled; market fallback blocked: entries paused': ('execution', 'maker_fallback_blocked'),
    'WARNING: BTCUSDT exposure 900 USDT > 1.5x bot capital': ('filter', 'warning'),
}


def test_every_engine_reason_text_maps_to_its_code():
    bad = {t: TA.reason_code(t) for t, want in REASONS.items() if TA.reason_code(t) != want}
    assert not bad, bad
    assert TA.reason_info('order failed: BinanceError -4028 Leverage 30 is not valid')['detail'] == 'BinanceError -4028 Leverage 30 is not valid'
    assert TA.reason_info('trailing entry cancelled: max positions reached (2)')['detail'] == 'capacity/max_positions'
    assert TA.reason_info('maker entry not filled; market fallback blocked: risk rule coin_cap: y')['detail'] == 'risk_gateway/coin_cap: y'
    assert TA.reason_code('risk rule  ??? : x') == ('risk_gateway', 'unnamed') and TA.reason_code('something new') == ('other', 'other')


def _engine_reason_literals():
    """Every reason text literal (f-string fields become 'X') the engine passes to miss(), assigns to last_skip, returns
    from entry_block/_rules_block, assigns to `why` in cycle(), or passes to the trailing cancel()."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tree = ast.parse(open(os.path.join(root, 'engine.py'), encoding='utf-8').read())

    def lits(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str): return [node.value]
        if isinstance(node, ast.JoinedStr):
            return [''.join(v.value if isinstance(v, ast.Constant) else 'X' for v in node.values)]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):      # 'WARNING: ' + w_, 'BTC ... - ' + (...)
            left = lits(node.left); return [l + 'X' for l in left] if left else []
        if isinstance(node, (ast.BoolOp, ast.IfExp)):
            return [x for v in (node.values if isinstance(node, ast.BoolOp) else (node.body, node.orelse)) for x in lits(v)]
        return []
    out = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef): continue
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == 'last_skip' for t in n.targets):
                out.update(lits(n.value))
            elif fn.name in ('entry_block', '_rules_block') and isinstance(n, ast.Return) and n.value is not None:
                out.update(lits(n.value))
            elif fn.name == '_rules_block' and isinstance(n, ast.JoinedStr):           # the enforce texts it returns
                out.update(lits(n))
            elif fn.name == 'cycle' and isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'why' for t in n.targets):
                out.update(lits(n.value))
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'miss' and len(n.args) >= 5:
                out.update(lits(n.args[4]))
            elif fn.name == '_trail_entries' and isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'cancel':
                out.update(lits(n.args[0]))
    out.discard(''); out.discard('maker entry working')                     # '' = reset; 'maker entry working' is a success
    return out


def test_no_engine_reason_text_falls_into_other():
    """Design: a NEW gate text fails here only if it lands in other/other. A text that starts with a known prefix (e.g.
    'risk rule <new>: ...', 'pump guard: ...', 'order failed: ...') inherits that code by design and passes."""
    found = _engine_reason_literals()
    assert len(found) >= 35, sorted(found)                                  # the scan really found the gates
    for must in ('bad direction', 'leverage cap reached for this slot', 'stop order failed - trade closed again',
                 'risk rule X: X', 'trailing entry cancelled: X', 'Claude review vetoed: X', 'order failed: X'):
        assert must in found, must
    other = sorted(t for t in found if TA.reason_code(t) == ('other', 'other'))
    assert not other, other


def test_miss_stores_the_detail_of_wrapped_reasons():
    import engine as E
    from test_safety import mk_engine, SL, SG
    e, _ = mk_engine()
    try:
        e.miss(SL, 'BTCUSDT', 'LONG', SG, 'order failed: BinanceError -4028 Leverage 30 is not valid')
        m = e.missed[-1]
        assert (m['stage'], m['code'], m['detail']) == ('execution', 'order_failed', 'BinanceError -4028 Leverage 30 is not valid')
        assert m['reason'] == 'order failed: BinanceError -4028 Leverage 30 is not valid'
    finally:
        E.close_fill_writer(e.F['audit'])


def test_replay_leaves_no_audit_writer_thread():
    """Review R6: replay.py closed only the fills writer."""
    import copy, engine as E
    from test_causality import synth, MODES, T0 as CT0
    from replay import run_replay
    before = set(E._FILL_WRITERS)
    run_replay(synth(n=520, seed=23), copy.deepcopy(MODES['trend long + pyramid']), CT0, steps=3)
    alive = [p for p in E._FILL_WRITERS if p not in before and E._FILL_WRITERS[p].alive()]
    assert not alive, alive


def test_audit_rotation_keeps_the_whole_window(monkeypatch, tmp_path):
    """Review R7: byte rotation (5 MB) with ~3 KB records held fewer than FILL_WINDOW records in .1 + current. The audit
    writer rotates by record count, so a restart always rebuilds the full window, whatever the record size."""
    import engine as E
    monkeypatch.setattr(E, 'FILL_WINDOW', 5)
    monkeypatch.setattr(E, 'FILL_ROTATE_BYTES', 300)                        # byte mode would rotate every ~1 record here
    p = str(tmp_path / 'trade_audit.jsonl')
    w = E.fill_writer(p, rotate_lines=E.FILL_WINDOW)
    try:
        for i in range(13): assert w.emit(dict(kind='trade_audit', i=i, pad='x' * 500))
        assert w.flush(5)
    finally:
        assert E.close_fill_writer(p)
    assert sum(1 for _ in open(p + '.1')) == 5 and sum(1 for _ in open(p)) == 3
    w2 = E.fill_writer(p, rotate_lines=E.FILL_WINDOW)
    try:
        assert [r['i'] for r in w2.win] == [8, 9, 10, 11, 12] and w2.lines == 3
    finally:
        E.close_fill_writer(p)


def test_engine_audit_writer_rotates_by_the_window_size():
    import engine as E
    from test_safety import mk_engine
    e, _ = mk_engine()
    try:
        assert e._auditw.rotate_lines == E.AUDIT_ROTATE_LINES and e._fillw.rotate_lines is None
        assert e._auditw.slim is TA.slim_record and e._fillw.slim is None          # V2-5: the audit window is slimmed
    finally:
        E.close_fill_writer(e.F['audit'])


def test_a_decision_after_a_same_second_state_change_uses_the_new_state():
    """Equal timestamps both ways: the observation AFTER a partial close (same second) must use the post-partial state
    (the recorded snapshot index, not a time lookup that would pick the earlier tied state)."""
    l = plot(mgmt=dict(tp_r=50.0, trail_atr=2.0))                          # snapshot 0 at iso(0)
    l['fees'] = 0.1; TA.observe(l, 104.0, iso(1), fee_rate=0.0)            # snapshot 1 at iso(1)
    l.update(qty=0.5, realized=2.0)                                         # half banked at 104, same second
    TA.observe(l, 99.0, iso(1), fee_rate=0.0)                               # snapshot 2 at iso(1); crosses the stop 102
    assert [s_[0] for s_ in l['ex']['st']] == [iso(0), iso(1), iso(1)]
    c = TA.policy_trailing(l, 0.0, 0.0, 0.0, TA.recorded_path(l))
    assert c['decision_t'] == iso(1) and c['value'] == 1.4                  # 2.0 + 0.5 x (99 - 100) - 0.1; qty 1.0: -1.1



# ------------------------------------------------------------------ T05a second review (verifier v2 findings)
import copy, math, time, tracemalloc                                        # noqa: E402


def _ts2(sec): return (T0 + timedelta(hours=1, seconds=sec)).isoformat(timespec='seconds')


def test_same_timestamp_extreme_keeps_its_observation_order_in_the_path():
    """V2-2: replay (one timestamp per bar) and fast live ticks stamp several observations with the same second; the
    MFE observed BEFORE a sampled point of that second must stay before it (points carry the observation index n)."""
    l = plot(mgmt=dict(trail_atr=2.0))                                    # atr0=1 -> stop 2.0 behind the best
    s = 0
    while l['ex'].get('stride', 1) < 2:
        s += 1; TA.observe(l, 100.0, _ts2(s), fee_rate=0.0)
    while l['ex']['n'] % 2 == 0:
        s += 1; TA.observe(l, 100.0, _ts2(s), fee_rate=0.0)
    T = _ts2(s + 1)
    TA.observe(l, 110.0, T, fee_rate=0.0)                                 # odd index: NOT sampled, but the MFE
    TA.observe(l, 107.5, T, fee_rate=0.0)                                 # even index: sampled; true order 110 then 107.5
    p = TA.recorded_path(l)
    assert [(x[0], x[1]) for x in p if x[0] == T] == [(T, 110.0), (T, 107.5)]
    assert [x[3] for x in p] == sorted(x[3] for x in p)
    c = TA.policy_trailing(l, 0.0, 0.0, 0.0, p)
    assert c['decision_t'] == T and c['value'] == 7.5, c


def test_same_timestamp_order_holds_for_the_path_based_fallback_too():
    """The path-based trailing evaluation (no online state, e.g. an older lot) on the same-second path."""
    l = plot(mgmt=dict(trail_atr=2.0)); l['ex']['on'] = None
    s = 0
    while l['ex'].get('stride', 1) < 2:
        s += 1; TA.observe(l, 100.0, _ts2(s), fee_rate=0.0)
    while l['ex']['n'] % 2 == 0:
        s += 1; TA.observe(l, 100.0, _ts2(s), fee_rate=0.0)
    T = _ts2(s + 1)
    TA.observe(l, 110.0, T, fee_rate=0.0); TA.observe(l, 107.5, T, fee_rate=0.0)
    c = TA.policy_trailing(l, 0.0, 0.0, 0.0, TA.recorded_path(l))
    assert c['decision_t'] == T and c['value'] == 7.5 and not c.get('online'), c


def _realistic_lot():
    l = plot(mgmt=dict(tp_r=3.0, tp1_r=1.0, tp1_frac=0.5, trail_atr=2.0, be_r=1.0), symbol='ETHUSDT', sleeve='S1', tf='4h')
    for i in range(1, 20000):                                             # ~11 h of 2 s ticks
        m = 100 + 3 * math.sin(i / 500.0) + i * 0.0002
        if i % 900 == 0: l['realized'] = (l.get('realized') or 0) + 0.01; l['fees'] = (l.get('fees') or 0) + 0.001
        TA.observe(l, m, _ts2(2 * i), fee_rate=0.0005)
    l['fills'] = [[iso(0), 'entry', 1.0, 100.0], [_ts2(40000), 'take_profit', 1.0, 104.0]]
    return l


def test_state_size_per_lot_and_record_size_are_bounded():
    l = _realistic_lot()
    rec = TA.close_record(l, 3.0, fee_rate=0.0005)
    rec.update(kind='trade_audit', id='S1|ETHUSDT|x', exit_reason='take_profit', closed=_ts2(40000))
    # record bound 6000 -> 9000 B (owner scope 2026-10-07: metrics, flags, segment + the two v2 policies add ~2.8 KB); the
    # audit file stays bounded by AUDIT_ROTATE_BYTES and the in-memory window by slim_record (see the next test)
    assert len(json.dumps(l['ex'])) < 12000 and len(json.dumps(rec)) < 9000


def test_audit_window_is_slimmed_and_the_summary_is_fast():
    """V2-5: the in-memory window keeps only what attribution reads; the result equals the one from full records."""
    l = _realistic_lot()
    rec = TA.close_record(l, 3.0, fee_rate=0.0005); rec.update(kind='trade_audit', id='k', exit_reason='take_profit', closed='x')
    line = json.dumps(rec)
    tracemalloc.start()
    win = [TA.slim_record(json.loads(line)) for _ in range(5000)]
    cur, _ = tracemalloc.get_traced_memory(); tracemalloc.stop()
    t = time.perf_counter(); out = TA.attribution(win + [dict(reason='entries paused')] * 600); dt = time.perf_counter() - t
    assert cur < 10e6 and dt < 0.1, (cur, dt)
    assert out == TA.attribution([json.loads(line)] * 5000 + [dict(reason='entries paused')] * 600)
    assert TA.slim_record(dict(reason='x')) == dict(reason='x') and TA.slim_record('junk') == 'junk'


def test_engine_audit_window_holds_slimmed_records(tmp_path):
    import engine as E
    p = str(tmp_path / 'trade_audit.jsonl')
    full = dict(kind='trade_audit', sleeve='A', tf='1h', side='LONG', r=1.0, giveback_pct=5.0, target=104.0, target_touch_t='t',
                path_points=60, limitations=['x' * 500], counterfactuals=[dict(kind='causal_policy', rule=TA.RULE_TARGET, vs_actual=1.5,
                                                                                  limitations='y' * 300)])
    open(p, 'w').write(json.dumps(full) + '\n')
    w = E.fill_writer(p, rotate_lines=E.FILL_WINDOW, slim=TA.slim_record)
    try:
        w.emit(dict(full, r=2.0)); assert w.flush(5)
        assert len(w.win) == 2 and all('limitations' not in r and 'path_points' not in r for r in w.win)
        assert w.win[1]['r'] == 2.0 and w.win[1]['counterfactuals'] == [dict(kind='causal_policy', rule=TA.RULE_TARGET, vs_actual=1.5)]
        assert json.loads(open(p).read().splitlines()[-1])['limitations'] == ['x' * 500]       # the file keeps the full record
    finally:
        E.close_fill_writer(p)


def test_engine_audit_summary_is_cached_until_the_window_or_missed_changes(monkeypatch):
    import engine as E
    from test_safety import mk_engine, SL, SG
    e, _ = mk_engine()
    try:
        calls = []
        real = TA.attribution
        monkeypatch.setattr(TA, 'attribution', lambda recs, *a: calls.append(1) or real(recs, *a))
        a = e.audit_summary(); b = e.audit_summary()
        assert len(calls) == 1 and a['funnel'] == b['funnel'] and 'telemetry' in b
        e.miss(SL, 'BTCUSDT', 'LONG', dict(SG, time='2026-10-04 08:00:00'), 'entries paused')
        c = e.audit_summary(); assert len(calls) == 2 and c['funnel'].get('filter/paused') == 1
        e._auditw.emit(dict(kind='trade_audit', sleeve='Z', tf='1h', side='LONG', r=1.0)); assert e._auditw.flush(5)
        d = e.audit_summary(); assert len(calls) == 3 and any(g['sleeve'] == 'Z' for g in d['groups'])
        d['groups'] = None; assert e.audit_summary()['groups'] and len(calls) == 3      # callers cannot corrupt the cache
    finally:
        E.close_fill_writer(e.F['audit'])


def _carry(e, e2):
    """The fake exchange of a re-created Engine keeps the positions and stops (as Binance would)."""
    e2.trade.pos, e2.trade.stops, e2.trade.n = dict(e.trade.pos), dict(e.trade.stops), e.trade.n


def test_restart_continues_from_the_newest_checkpoint_and_records_the_gap(monkeypatch):
    """Contract: the mark loop never saves state for the audit; tracking survives a restart through the checkpoint events
    (non-blocking writer) and the restart is flagged as a tracking gap from the last known observation."""
    import engine as E
    from test_safety import mk_engine, opened
    monkeypatch.setattr(TA, 'CKPT_MIN_S', 0)                              # checkpoint every change (bounded by the change rate)
    e, tmp = mk_engine()
    try:
        k = opened(e)
        _walk(e, (100.0, 100.4, 101.2, 100.6))                            # small moves: no stop/partial/add
        assert e.state['lots'][k]['ex']['mfe_px'] == 101.2 and e.state['lots'][k]['ex']['n'] == 4
        assert 'ex' not in json.load(open(e.F['state']))['lots'][k]      # the mark loop did NOT save state for the audit
        assert e._auditw.flush(5)
    finally:
        E.close_fill_writer(e.F['audit'])
    e2, _ = mk_engine(tmp); _carry(e, e2)
    try:
        l2 = e2.state['lots'][k]; ex = l2['ex']
        assert ex['mfe_px'] == 101.2 and ex['n'] == 3 and ex['restored_from'] and k not in e2._audit_restored
        T = ex['last'][0]
        assert ex['gap_open'] == T
        _walk(e2, (100.3,))
        assert ex['n'] == 4 and ex['mfe_px'] == 101.2 and ex['gaps'] == [[T, ex['last'][0]]] and 'gap_open' not in ex
        e2.close_lot(k, 'signal', mark=100.3)
        rec = _last_audit(e2)
        assert rec['tracking_gaps'] == [[T, ex['last'][0]]] and any('tracking gap' in x for x in rec['limitations'])
        assert rec['coverage'] == 'partial' and 'restart gap' in rec['coverage_why']
    finally:
        E.close_fill_writer(e2.F['audit'])


def test_rate_limited_checkpoints_lose_only_what_the_gap_says():
    """Default CKPT_MIN_S: only the forced first checkpoint exists, so a restart continues from it and the lost
    observations are inside the reported gap (nothing is invented)."""
    import engine as E
    from test_safety import mk_engine, opened
    e, tmp = mk_engine()
    try:
        k = opened(e)
        _walk(e, (100.0, 100.4, 101.2, 100.6))
        assert e._auditw.flush(5)
        cks = [json.loads(x) for x in open(e.F['audit']) if '"excursion"' in x]
        assert len(cks) == 1 and cks[0]['n'] == 1                          # forced first-observation checkpoint only
    finally:
        E.close_fill_writer(e.F['audit'])
    e2, _ = mk_engine(tmp); _carry(e, e2)
    try:
        ex = e2.state['lots'][k]['ex']
        assert ex['n'] == 1 and ex['mfe_px'] == 100.0 and ex['gap_open'] == ex['last'][0]
        TA.observe(e2.state['lots'][k], 100.3, E.now_utc().isoformat(timespec='seconds'), E.FEE_EST)
        assert ex['n'] == 2 and ex['gaps_n'] == 1
    finally:
        E.close_fill_writer(e2.F['audit'])


def test_restart_gap_never_observed_again_is_reported_until_close():
    l = plot(); TA.observe(l, 101.0, iso(1), fee_rate=0.0)
    assert TA.mark_restart(l) is True and TA.mark_restart(l) is True and l['ex']['gap_open'] == iso(1)   # earliest kept
    rec = TA.close_record(l, 0.0)
    assert rec['tracking_gaps'] == [[iso(1), None]] and any(f'{iso(1)} -> close' in x for x in rec['limitations'])
    assert TA.mark_restart(dict(side='LONG')) is False and TA.mark_restart(None) is None


def test_lot_restored_without_tracking_data_is_flagged_late():
    import engine as E
    from test_safety import mk_engine, opened
    e, tmp = mk_engine()
    try:
        k = opened(e)
    finally:
        E.close_fill_writer(e.F['audit'])
    st = json.load(open(os.path.join(tmp, 'state.json'))); st['lots'][k].pop('ex', None)
    json.dump(st, open(os.path.join(tmp, 'state.json'), 'w'))
    e2, _ = mk_engine(tmp); _carry(e, e2)
    try:
        assert k in e2._audit_restored
        _walk(e2, (100.5,))
        ex = e2.state['lots'][k]['ex']
        assert ex['late'] is True and 'restored' in ex['late_why'] and k not in e2._audit_restored
        rec = TA.close_record(e2.state['lots'][k], 0.0)
        assert any('tracking started after entry' in x and 'restored' in x for x in rec['limitations'])
    finally:
        E.close_fill_writer(e2.F['audit'])


def test_checkpoint_marker_only_moves_on_meaningful_changes():
    l = plot(); TA.observe(l, 100.0, _ts2(0), fee_rate=0.0); c = l['ex']['chg']    # (closes the 1h no-sample interval)
    TA.observe(l, 101.0, _ts2(1), fee_rate=0.0); assert l['ex']['chg'] == c + 1     # new MFE / peak / best
    c = l['ex']['chg']
    for i in range(2, 30): TA.observe(l, 100.5, _ts2(i), fee_rate=0.0)              # inside the range, samples 1 s apart
    assert l['ex']['chg'] == c
    l['qty'] = 2.0; TA.observe(l, 100.5, _ts2(30), fee_rate=0.0); assert l['ex']['chg'] == c + 1   # new snapshot
    c = l['ex']['chg']; TA.observe(l, 104.0, _ts2(31), fee_rate=0.0); assert l['ex']['chg'] > c     # target touch


def test_nan_realized_or_fees_do_not_flood_the_snapshots():
    l = plot(); l['realized'] = float('nan'); l['fees'] = float('inf')
    for i in range(1, 100): TA.observe(l, 100.0 + (i % 3) * 0.01, iso(i), fee_rate=0.0)
    ex = l['ex']
    assert len(ex['st']) == 2 and not ex.get('st_full_t') and ex['st'][-1][6:8] == [None, None]
    assert json.dumps(ex)                                                              # no NaN leaks into state.json


def test_live_path_policy_uses_the_snapshot_of_its_observation_not_the_final_one():
    """V2-3: the live path's exact snapshot index (state_at's si branch) - a state change after the decision."""
    l = plot(mgmt=dict(tp_r=2.0))                                          # target 104, snapshot 0: qty 1 @ 100
    TA.observe(l, 104.5, iso(1), fee_rate=0.0)                             # target touched with qty 1
    l.update(qty=2.0, avg=101.0)                                           # an add AFTER the decision
    TA.observe(l, 103.0, iso(2), fee_rate=0.0)                             # snapshot 1
    p = TA.recorded_path(l)
    assert [x[2] for x in p] == [0, 0, 1]
    c = TA.policy_target(l, 0.0, 0.0, 0.0, p)
    assert c['decision_t'] == iso(1) and c['value'] == 4.0, c              # final state would give 2 x (104 - 101) = 6


def test_live_online_policies_value_with_the_snapshot_of_their_observation():
    """V2-3 for the online trailing / time-cap decisions: later partial closes / adds must not leak in."""
    l = plot(tf='15m', mgmt=dict(tp_r=50.0, trail_atr=2.0))                 # cap 48 x 15m = 12h; stop 2.0 behind the best
    TA.observe(l, 103.0, iso(1), fee_rate=0.0)
    TA.observe(l, 100.5, iso(12), fee_rate=0.0)                            # time cap AND trailing (stop 101) here, qty 1
    l.update(qty=3.0, avg=95.0, realized=7.0)
    TA.observe(l, 99.0, iso(13), fee_rate=0.0)
    cfs = TA.counterfactuals(l, 0.0, 0.0, 0.0, TA.recorded_path(l))
    for p in ('trailing', 'time cap'):
        c = pol(cfs, p)
        assert c['online'] and c['decision_t'] == iso(12) and c['value'] == 0.5, (p, c)


def test_target_touch_point_is_part_of_the_live_path():
    """V2-3: the first target touch can fall on a decimated-away observation; without its point in the path the
    target policy would decide later (at the MFE)."""
    l = plot(mgmt=dict(tp_r=2.0, trail_atr=50.0))                          # target 104; trailing never triggers
    s = 0
    while l['ex'].get('stride', 1) < 2:
        s += 1; TA.observe(l, 100.0, _ts2(s), fee_rate=0.0)
    while l['ex']['n'] % 2 == 0:
        s += 1; TA.observe(l, 100.0, _ts2(s), fee_rate=0.0)
    TA.observe(l, 104.2, _ts2(s + 1), fee_rate=0.0)                        # odd index: not sampled; the first touch
    TA.observe(l, 100.0, _ts2(s + 2), fee_rate=0.0)
    TA.observe(l, 100.0, _ts2(s + 3), fee_rate=0.0)
    TA.observe(l, 106.0, _ts2(s + 4), fee_rate=0.0)                        # later MFE (odd: not sampled)
    for j in range(5, 9): TA.observe(l, 100.0, _ts2(s + j), fee_rate=0.0)
    assert not any(p[1] == 104.2 for p in l['ex']['path']) and l['ex']['on']['tb_pt'][1] == 106.0
    c = TA.policy_target(l, 0.0, 0.0, 0.0, TA.recorded_path(l))
    assert c['decision_t'] == _ts2(s + 1) and c['value'] == 4.0, c


def test_time_cap_is_decided_online_at_the_first_observation_after_the_cap():
    """V2-4: on a long decimated path the time cap is exact (online), not the next recorded sample hours later."""
    l = plot(mgmt=dict(tp_r=50.0))
    for i in range(1, 48 * 1800 + 600):
        TA.observe(l, 100.0 + (i % 977) * 0.001, _ts2(2 * i - 3600), fee_rate=0.0)
    assert l['ex']['stride'] > 1000
    c = TA.policy_time_cap(l, 0.0, 0.0, 0.0, TA.recorded_path(l))
    assert c['online'] and c['decision_t'] == c['cap_t'] == iso(48), c
    assert TA.policy_time_cap(l, 0.0, 0.0, 0.0, TA.recorded_path(l), n=47).get('online') is None   # other N: path fallback


def test_trailing_is_decided_online_with_the_atr_known_at_the_decision():
    """V2-4: online trailing uses the running best of EVERY observation and the ATR known then (atr_now refreshed
    later must not move an earlier decision)."""
    l = plot(mgmt=dict(tp_r=50.0), atr0=None, atr_now=1.0)                  # declared: k default 2, ATR atr_now 1.0
    for i in range(1, 400): TA.observe(l, 100.0 if i < 201 else (103.0 if i == 201 else 101.5), _ts2(i), fee_rate=0.0)   # best 103 (unsampled)
    TA.observe(l, 100.9, _ts2(400), fee_rate=0.0)                          # crosses 101.0
    for i in range(401, 500): TA.observe(l, 100.0, _ts2(i), fee_rate=0.0)
    l['atr_now'] = 5.0                                                     # a later refresh
    c = TA.policy_trailing(l, 0.0, 0.0, 0.0, TA.recorded_path(l))
    assert c['online'] and c['decision_t'] == _ts2(400) and c['atr'] == 1.0 and c['stop'] == 101.0 and c['value'] == 0.9, c
    assert 'atr_now as known at the decision' in c['limitations']
    assert TA.policy_trailing(l, 0.0, 0.0, 0.0, TA.recorded_path(l), k=3.0).get('online') is None   # other k: fallback
    assert tuple(l['ex']['on']['tr'][:4]) in TA.recorded_path(l)


def test_online_policies_are_causal_future_observations_never_change_a_decision():
    """Property (V2-4): online decisions only depend on observations up to them - any continuation leaves the
    trailing / time-cap decision and value unchanged."""
    rng = random.Random(17)
    hits = dict(trailing=0, tc=0)
    for trial in range(120):
        side = rng.choice(('LONG', 'SHORT'))
        base = plot(side=side, tf='15m', mgmt=dict(tp_r=50.0, trail_atr=rng.uniform(0.5, 3.0)))
        px, n = 100.0, rng.randint(5, 120)
        for i in range(1, n):
            px = max(1.0, px * (1 + rng.gauss(0, 0.004))); TA.observe(base, px, iso(i * 0.25), fee_rate=0.0)
        a = copy.deepcopy(base); b = copy.deepcopy(base)
        for i in range(n, n + 60):
            TA.observe(a, rng.uniform(1, 500), iso(i * 0.25), fee_rate=0.0)
            b.update(qty=rng.uniform(0.1, 5), avg=rng.uniform(50, 150)); TA.observe(b, rng.uniform(1, 500), iso(i * 0.25), fee_rate=0.0)
        for p, key in (('trailing', 'tr'), ('time cap', 'tc')):
            c0 = base['ex']['on'][key]
            if c0 is None: continue
            hits['trailing' if key == 'tr' else 'tc'] += 1
            vals = [(c['decision_t'], c['value']) for c in (pol(TA.counterfactuals(x, 0.0, 0.0005, 1.0, TA.recorded_path(x)), p) for x in (base, a, b))]
            assert vals[0] == vals[1] == vals[2] and vals[0][0] == c0[0], (p, trial, vals)
    assert min(hits.values()) >= 20, hits


def test_grid_style_snapshot_overflow_is_a_listed_limitation():
    l = plot()
    for i in range(1, 80): l['qty'] = 1.0 + i / 100; TA.observe(l, 100.0, iso(i), fee_rate=0.0)
    rec = TA.close_record(l, 0.0)
    assert any('state history full' in x for x in rec['limitations'])


# ================================================================== T05a contract (Codex PR #8 pre-design) acceptance
import builtins, threading                                                 # noqa: E402
import numpy as np                                                         # noqa: E402


def _clocked(monkeypatch, start=datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)):
    """Engine wall clock under test control: returns a 1-item list holding the current time."""
    import engine as E
    clk = [start]
    monkeypatch.setattr(E, 'now_utc', lambda: clk[0])
    return clk


def _events(e, kind=None):
    assert e._auditw.flush(5)
    rows = [json.loads(x) for x in open(e.F['audit'])] if os.path.exists(e.F['audit']) else []
    return [r for r in rows if kind is None or r.get('kind') == kind]


def _trend_scenario(monkeypatch, audit_on):
    """A trending mark sequence on a trailing-stop lot: returns (save_state calls during manage, stop moves)."""
    import engine as E
    from test_safety import mk_engine
    sl = E.sleeve('T', 'ema_mom', 0.5, 0.02, 2, 'all', mgmt=dict(stop_atr=2.5, trail_atr=2.0))
    from test_safety import SG
    if not audit_on:                                                       # the T05b-only engine: no audit at all
        for n in ('observe', 'checkpoint', 'declare'): monkeypatch.setattr(TA, n, lambda *a, **k: None)
        monkeypatch.setattr(TA, 'fill_events', lambda *a, **k: [])
    e, _ = mk_engine()
    try:
        assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
        calls = []
        real = e.save_state
        monkeypatch.setattr(e, 'save_state', lambda: calls.append(1) or real())
        lot_ = next(iter(e.state['lots'].values()))
        for i in range(1, 241):                                            # +0.05 per tick: a new MFE / peak on EVERY tick
            e.trade.mark['BTCUSDT'] = m = 100.0 + 0.05 * i; e.manage(e.trade.marks())
            if i == 120: e._add_qty(lot_, 0.5, m, 'pyramid_add')            # cohort event: still no extra save
            if i == 200: e._market_close(lot_, 0.5, 'take_profit_1', m)     # cohort + runner events: still no extra save
        return len(calls), e.trade.calls.count('stop')
    finally:
        E.close_fill_writer(e.F['audit'])


def test_trending_mark_loop_saves_state_exactly_as_often_as_without_the_audit(monkeypatch):
    """Contract: NO disk I/O in the mark loop for the audit. The draft marked state changed on every new MFE/peak (one
    state.json write per tick in a trend); now the save count equals the audit-free (T05b-only) engine's."""
    with_audit = _trend_scenario(monkeypatch, True)
    monkeypatch.undo()
    without = _trend_scenario(monkeypatch, False)
    assert with_audit == without, (with_audit, without)
    assert with_audit == (49, 50)       # pinned: the SAME scenario on the T05b-only branch (t05b-next db163e9) gives 49 saves /
    #                                     50 stop orders; the previous T05a draft saved on every tick (240)


def test_mark_loop_opens_no_file_on_the_calling_thread(monkeypatch):
    """Observation + checkpoint events only touch memory and the writer QUEUE: the manage thread opens no file when
    nothing trades (the writer thread does the appending)."""
    import engine as E
    from test_safety import mk_engine, opened
    monkeypatch.setattr(TA, 'CKPT_MIN_S', 0)                              # make every tick emit a checkpoint event
    e, _ = mk_engine()
    try:
        k = opened(e)
        e.trade.mark['BTCUSDT'] = 100.0; e.manage(e.trade.marks())
        me, opens = threading.current_thread(), []
        real_open = builtins.open
        monkeypatch.setattr(builtins, 'open', lambda *a, **kw: (opens.append(a[0]) if threading.current_thread() is me else None) or real_open(*a, **kw))
        for i in range(1, 60):
            e.trade.mark['BTCUSDT'] = 100.0 + 0.01 * i; e.manage(e.trade.marks())   # new extremes, no stop move (no trailing)
        monkeypatch.setattr(builtins, 'open', real_open)
        assert opens == [], opens
        assert len(_events(e, 'excursion')) >= 50 and e.state['lots'][k]['ex']['n'] == 60
    finally:
        E.close_fill_writer(e.F['audit'])


# ---- events: schema, finite-or-null, no secrets / ids / raw exchange dumps
KEY = 'AbCdEf0123456789' * 4
SECRET = 'ZyXwVu9876543210' * 4


def test_clean_makes_events_finite_or_null_and_drops_secret_keys():
    ev = dict(a=float('nan'), b=[float('inf'), 1.5, np.float64(2.5), np.int64(3)], c=dict(apiKey=KEY, orderId=12, stop_id='o:1',
              clientOrderId='x', nested=dict(TELEGRAM_CHAT='123', chat_id=5, ok=1)), raw={'avgPrice': '1'},
              text=f'order failed: https://fapi/x?signature={SECRET}&timestamp=1 {{"code":-2019,"orderId":77}} tok {KEY}')
    c = TA.clean(ev)
    s = json.dumps(c, allow_nan=False)
    assert c['a'] is None and c['b'] == [None, 1.5, 2.5, 3] and c['c'] == dict(nested=dict(ok=1)) and 'raw' not in c
    for bad in (KEY, SECRET, 'orderId', 'signature', '-2019', 'o:1', 'avgPrice'): assert bad not in s, bad
    assert c['text'].startswith('order failed: https://fapi/x {...} tok [redacted]')
    assert TA.clean('trend_long_pyramid_breakout_slot_one|BTCUSDT') == 'trend_long_pyramid_breakout_slot_one|BTCUSDT'


def test_audit_files_contain_no_credentials_telegram_ids_or_raw_exchange_dumps(monkeypatch):
    """Allowed: symbol, side, sleeve, lot id, times, prices, quantities, P&L, reason codes and scrubbed reason details,
    policy parameters. Not allowed (scanned): API key/secret, Telegram token/chat id, signatures, order ids / client ids /
    stop tags, raw exchange JSON keys, NaN/Infinity."""
    import engine as E
    from test_safety import mk_engine, opened, SL, SG
    monkeypatch.setattr(TA, 'CKPT_MIN_S', 0)
    e, _ = mk_engine(TELEGRAM_ON=False, TELEGRAM_TOKEN='777:' + SECRET, TELEGRAM_CHAT='987654321')
    e.cfg.update(API_KEY=KEY, API_SECRET=SECRET)
    try:
        k = opened(e); lot_ = e.state['lots'][k]
        lot_.update(orderId=4242, clientOrderId='zb-' + KEY, raw={'avgPrice': '100', 'executedQty': '1'})   # junk on the lot
        _walk(e, (101.0, 104.0, 103.0))
        e.trade.mark['BTCUSDT'] = 104.0; e._market_close(lot_, 0.5, 'take_profit_1', 104.0)
        e.miss(SL, 'BTCUSDT', 'LONG', dict(SG, time='2026-10-04 04:00:00'),
               f'order failed: 400 https://testnet.binancefuture.com/fapi/v1/order?symbol=BTCUSDT&signature={SECRET}&timestamp=1 '
               '{"code":-2019,"msg":"Margin is insufficient.","orderId":987654321123}')
        _walk(e, (102.0,)); e.close_lot(k, 'signal', mark=102.0)
        rows = _events(e)
        text = open(e.F['audit']).read()
        assert {r['kind'] for r in rows} >= {'excursion', 'cohort', 'runner', 'funnel', 'trade_audit'}
        for ln in text.splitlines():
            json.loads(ln, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))   # finite-or-null only
        for bad in (KEY, SECRET, '987654321', '777:', 'signature', 'orderId', 'clientOrderId', 'stop_id', 'avgPrice',
                    'executedQty', '"o:', 'totalMarginBalance', 'API_KEY', 'TELEGRAM'):
            assert bad not in text, bad
        assert all(r.get('v') == TA.AUDIT_VERSION and r.get('kind') in TA.EVENT_KINDS for r in rows)
        f = [r for r in rows if r['kind'] == 'funnel'][-1]
        assert (f['stage'], f['code']) == ('execution', 'order_failed') and '{...}' in f['detail'] and len(f['detail']) <= 160
    finally:
        E.close_fill_writer(e.F['audit'])


# ---- raw long/short signals BEFORE side masking; candidate funnel events
def _signal_engine(monkeypatch, sleeves, raw):
    """An engine whose compute_signals runs for real on synthetic candles with S.signals patched to `raw`."""
    import engine as E, strategies as S_
    from test_safety import mk_engine
    e, _ = mk_engine()
    n = 300
    df = pd.DataFrame(dict(t=pd.date_range('2026-09-01', periods=n, freq='4h'), o=100.0, h=101.0, l=99.0, c=100.0, v=1.0, atr=2.0))
    monkeypatch.setattr(e, 'candles', lambda s, tf: df)
    monkeypatch.setattr(S_, 'build_context', lambda al, b: {s: None for s in al})
    monkeypatch.setattr(S_, 'signals', lambda key, d, ctx, params=None, mask_sides=True: {
        k: np.array([False] * (len(d) - 1) + [raw[k]]) for k in ('le', 'se', 'lx', 'sx')})
    e.S['SLEEVES'] = sleeves
    return e, df


import pandas as pd                                                        # noqa: E402


def test_raw_signals_are_kept_before_side_masking_and_masked_ones_are_counted(monkeypatch):
    import engine as E
    longonly = E.sleeve('L', 'ema_mom', 0.5, 0.02, 2, ['BTCUSDT'], sides='long', tf='4h', enabled=False)
    e, df = _signal_engine(monkeypatch, [longonly], dict(le=False, se=True, lx=False, sx=False))
    try:
        sigs, _ = e.compute_signals('4h', ['BTCUSDT'])
        g = sigs['L|BTCUSDT']
        assert (g['le'], g['se']) == (False, False)                        # the decision input is unchanged (masked)
        assert e._sig_raw['L|BTCUSDT'] == dict(le=False, se=True, sides='long', le_m=False, se_m=False, time=g['time'])
        e.cycle('4h')
        f = [r for r in _events(e, 'funnel') if r['symbol'] == 'BTCUSDT']
        assert len(f) == 1 and f[0]['decision'] == 'side_masked' and f[0]['side'] == 'SHORT' and f[0]['raw_short'] and not f[0]['raw_long']
        assert not e.missed                                                 # the masked signal is NOT a missed-trade record
    finally:
        E.close_fill_writer(e.F['audit'])


def test_both_raw_sides_keep_the_unused_short_on_the_candidate_record(monkeypatch):
    import engine as E
    both = E.sleeve('B', 'ema_mom', 0.5, 0.02, 2, ['BTCUSDT'], sides='both', tf='4h', enabled=False)
    e, _ = _signal_engine(monkeypatch, [both], dict(le=True, se=True, lx=False, sx=False))
    try:
        e.cycle('4h')
        f = _events(e, 'funnel')[-1]
        assert f['decision'] == 'not_taken' and f['side'] == 'LONG' and f['both_raw'] and f['raw_short'] and f['masked_short']
        assert (f['stage'], f['code']) == ('filter', 'slot_off') and e.missed[-1]['reason'] == 'strategy slot switched off'
    finally:
        E.close_fill_writer(e.F['audit'])


def test_taken_and_armed_candidates_emit_funnel_events(monkeypatch):
    import engine as E
    from test_safety import mk_engine, SL, SG
    e, _ = mk_engine()
    try:
        e.S['SLEEVES'] = [SL]
        e.compute_signals = lambda tf, syms, extra=(): ({f"{SL['id']}|BTCUSDT": dict(SG)}, {})
        e.cycle('4h')
        f = _events(e, 'funnel')
        assert [x['decision'] for x in f] == ['taken'] and f[0]['side'] == 'LONG' and e.state['lots']
    finally:
        E.close_fill_writer(e.F['audit'])


# ---- candle-close hold / close evaluations (info cutoff = candle close; highs/lows are labelled upper bounds)
def test_hold_eval_labels_candle_extremes_and_enforces_the_cutoff():
    l = plot(tf='1h', mgmt=dict(tp_r=2.0), stop=96.0)                     # target 104
    TA.observe(l, 103.0, iso(1.5), fee_rate=0.0)
    cnd = dict(t=iso(1), h=104.5, l=95.5, c=103.0, tf_s=3600)
    ev = TA.hold_eval(l, 'k', cnd, iso(2.01), 'hold', exit_signal=False, fee_rate=0.0)
    assert ev['causal_ok'] and ev['info_cutoff']['t'] == iso(2) and ev['decision_at']['t'] == iso(2.01)
    assert ev['target'] == dict(level=104.0, at_close=False, by_extreme=True, extreme_label='upper_bound')
    assert ev['stop']['by_extreme'] is True and ev['stop']['extreme_label'] == 'upper_bound' and 'upper bounds' in ev['hl_label']
    TA.observe(l, 104.2, iso(1.75), fee_rate=0.0)                           # a mark sample inside the candle proves the touch
    assert TA.hold_eval(l, 'k', cnd, iso(2.01), 'hold')['target']['extreme_label'] == 'proven by a mark sample in this candle'
    early = TA.hold_eval(l, 'k', cnd, iso(2), 'hold')                       # decision AT the close: not strictly after
    assert not early['causal_ok'] and 'target' not in early and early['withheld']
    out = TA.hold_eval(l, 'k', cnd, iso(2.5), 'hold', missing=True)         # T05b outage: a missing sample
    assert out['missing_sample'] and not out['causal_ok'] and 'target' not in out


def test_cycle_emits_one_hold_eval_per_open_lot_per_closed_candle(monkeypatch):
    import engine as E
    from test_safety import mk_engine, opened, SL, SG
    clk = _clocked(monkeypatch)
    e, _ = mk_engine()
    try:
        k = opened(e)
        df = pd.DataFrame(dict(t=[pd.Timestamp('2026-10-04 04:00:00')], h=[103.0], l=[99.0], c=[102.0], atr=[2.0]))
        e.S['SLEEVES'] = [SL]
        e.compute_signals = lambda tf, syms, extra=(): ({f"{SL['id']}|BTCUSDT": dict(SG, le=False, lx=False)}, {'BTCUSDT': df})
        clk[0] = datetime(2026, 10, 4, 8, 0, 5, tzinfo=timezone.utc); e.cycle('4h')
        h = _events(e, 'hold_eval')
        assert len(h) == 1 and h[0]['id'] == k and h[0]['action'] == 'hold' and h[0]['causal_ok']
        assert h[0]['info_cutoff']['t'] == '2026-10-04T08:00:00+00:00' and h[0]['close'] == 102.0 and k in e.state['lots']
        e.compute_signals = lambda tf, syms, extra=(): ({f"{SL['id']}|BTCUSDT": dict(SG, le=False, lx=True)}, {'BTCUSDT': df})
        e.cycle('4h')
        h = _events(e, 'hold_eval')
        assert h[-1]['action'] == 'close_exit_signal' and h[-1]['exit_signal'] is True and k not in e.state['lots']
    finally:
        E.close_fill_writer(e.F['audit'])


# ---- price MFE/MAE vs lifecycle net-P&L peak/trough; long/short sign symmetry
def test_price_excursion_and_lifecycle_pnl_are_separate():
    """A pyramid add after the price peak: the PRICE MFE stays at the best mark, the LIFECYCLE peak/trough follow the
    net P&L of the changing quantity (realized - fees + open - exit fee)."""
    l = plot(mgmt=dict(tp_r=50.0)); l['fees'] = 0.0
    for t, m in ((1, 104.0), (2, 103.0)): TA.observe(l, m, iso(t), fee_rate=0.0)
    l.update(qty=3.0, avg=(100.0 + 2 * 103.0) / 3)                          # +2 @ 103
    TA.observe(l, 102.0, iso(3), fee_rate=0.0)                              # open P&L 3 x (102 - 102) = 0
    TA.observe(l, 105.0, iso(4), fee_rate=0.0)                              # 3 x 3 = 9 > 4
    rec = TA.close_record(l, 9.0, fee_rate=0.0)
    assert rec['price_excursion']['mfe_px'] == 105.0 and rec['price_excursion']['mae_px'] == 100.0
    assert rec['lifecycle_pnl']['peak'] == 9.0 and rec['lifecycle_pnl']['trough'] == 0.0 and rec['lifecycle_pnl']['peak_t'] == iso(4)
    assert 'net' in rec['lifecycle_pnl']['basis'] and 'mark price samples' in rec['price_excursion']['basis']


def _mirror_pair(rng):
    marks = [100.0]
    for _ in range(rng.randint(5, 80)): marks.append(max(60.0, min(140.0, marks[-1] * (1 + rng.gauss(0, 0.01)))))
    ll = plot('LONG', tf='15m', mgmt=dict(tp_r=rng.uniform(0.5, 3), trail_atr=rng.uniform(0.5, 3)))
    ss = plot('SHORT', tf='15m', mgmt=dict(tp_r=ll['mgmt']['tp_r'], trail_atr=ll['mgmt']['trail_atr']))
    for i, m in enumerate(marks[1:], 1):
        TA.observe(ll, m, iso(i * 0.25), fee_rate=0.0); TA.observe(ss, 200.0 - m, iso(i * 0.25), fee_rate=0.0)
    return ll, ss


def test_long_short_sign_symmetry():
    """A short on the mirrored path (200 - p) must get exactly the long's numbers (fees off)."""
    rng = random.Random(3)
    for trial in range(60):
        ll, ss = _mirror_pair(rng)
        a, b = ll['ex'], ss['ex']
        assert (a['peak_pnl'], a['trough_pnl'], a['peak_t'], a['trough_t']) == (b['peak_pnl'], b['trough_pnl'], b['peak_t'], b['trough_t'])
        assert round(a['mfe_px'] - 100, 9) == round(100 - b['mfe_px'], 9) and a['mfe_t'] == b['mfe_t'] and a['target_touch_t'] == b['target_touch_t']
        ca = {c['rule']: (c['decision_t'], c['value']) for c in TA.close_record(ll, 0.0, fee_rate=0.0)['counterfactuals']}
        cb = {c['rule']: (c['decision_t'], c['value']) for c in TA.close_record(ss, 0.0, fee_rate=0.0)['counterfactuals']}
        assert ca == cb, (trial, ca, cb)
    l = dict(side='LONG', fills=[['t0', 'entry', 1.0, 100.0], ['t1', 'take_profit_1', 0.5, 104.0], ['t2', 'stop', 0.5, 102.0]])
    s = dict(side='SHORT', fills=[['t0', 'entry', 1.0, 100.0], ['t1', 'take_profit_1', 0.5, 96.0], ['t2', 'stop', 0.5, 98.0]])
    assert TA.cohort_ledger(l, 0.0)['runner']['value_vs_activation'] == TA.cohort_ledger(s, 0.0)['runner']['value_vs_activation'] == -1.0


# ---- quantity cohort ledger: DCA / pyramid / partial / runner (same remaining quantity)
def test_cohort_ledger_pyramid_partial_runner_and_a_post_activation_add():
    l = dict(side='LONG', fills=[['t0', 'entry', 1.0, 100.0], ['t1', 'pyramid_add', 0.5, 106.0], ['t2', 'take_profit_1', 0.75, 110.0],
                                 ['t3', 'pyramid_add', 0.5, 112.0], ['t4', 'stop', 1.25, 108.0]])
    led = TA.cohort_ledger(l, 0.0)
    r = led['runner']
    assert r['qty'] == 0.75 and r['px'] == 110.0 and r['cohorts'] == [[0, 0.25], [1, 0.5]]   # FIFO: the tp took cohort 0 first
    assert r['closes'] == [['t4', 'stop', 0.75, 108.0]] and r['qty_closed'] == 0.75 and r['qty_open'] == 0.0
    assert r['value_vs_activation'] == -1.5                                  # (108 - 110) x 0.75: the post-activation add is NOT runner
    assert [c['open'] for c in led['cohorts']] == [0.0, 0.0, 0.0] and led['unmatched_close_qty'] is None
    fee = TA.cohort_ledger(l, 0.0005)['runner']['value_vs_activation']
    assert fee == round(-1.5 - 0.0005 * 0.75 * (108 - 110), 6)


def test_cohort_ledger_dca_basket_runner_and_ladder_and_no_runner_cases():
    s = dict(side='SHORT', fills=[['t0', 'entry', 1.0, 100.0], ['t1', 'safety_order', 2.0, 103.0], ['t2', 'safety_order', 4.0, 106.0],
                                  ['t3', 'basket_tp_part', 5.0, 101.0], ['t4', 'take_profit', 2.0, 99.0]])
    r = TA.cohort_ledger(s, 0.0)['runner']
    assert r['qty'] == 2.0 and r['cohorts'] == [[2, 2.0]] and r['value_vs_activation'] == 4.0     # short: (101 - 99) x 2
    lad = dict(side='LONG', fills=[['a', 'entry', 1.0, 100.0], ['b', 'take_profit_ladder', 0.3, 103.0], ['c', 'take_profit_ladder', 0.3, 106.0],
                                   ['d', 'stop', 0.4, 101.0]])
    r = TA.cohort_ledger(lad, 0.0)['runner']
    assert r['qty'] == 0.7 and r['closes'] == [['c', 'take_profit_ladder', 0.3, 106.0], ['d', 'stop', 0.4, 101.0]]
    assert r['value_vs_activation'] == round(0.3 * 3 - 0.4 * 2, 6)
    grid = dict(side='LONG', fills=[['a', 'entry', 1.0, 100.0], ['b', 'resync', 0.4, 99.0], ['c', 'stop', 0.6, 98.0]])
    assert TA.cohort_ledger(grid, 0.0)['runner'] is None                     # a resync partial is not a runner activation
    assert TA.cohort_ledger(dict(side='LONG', fills=[['a', 'stop', 1.0, 9.0]]), 0.0)['unmatched_close_qty'] == 1.0
    assert TA.cohort_ledger({}, 0.0)['cohorts'] == [] and TA.cohort_ledger(None)['runner'] is None


def test_engine_emits_cohort_and_runner_events_and_attributes_the_runner_quantity():
    import engine as E
    from test_safety import mk_engine, opened
    e, _ = mk_engine()
    try:
        k = opened(e); lot_ = e.state['lots'][k]
        e.trade.mark['BTCUSDT'] = 106.0; assert e._add_qty(lot_, 0.5, 106.0, 'pyramid_add')
        e.trade.mark['BTCUSDT'] = 110.0; e._market_close(lot_, 0.75, 'take_profit_1', 110.0)
        e.trade.mark['BTCUSDT'] = 108.0; e.close_lot(k, 'signal', mark=108.0)
        rows = _events(e)
        co = [r for r in rows if r['kind'] == 'cohort']
        assert [c['move'] for c in co] == ['add', 'partial_close'] and co[1]['open_cohorts'] == [[0, 0.25], [1, 0.5]]
        ru = [r for r in rows if r['kind'] == 'runner']
        assert len(ru) == 1 and ru[0]['qty'] == 0.75 and ru[0]['px'] == 110.0 and ru[0]['id'] == k
        rec = [r for r in rows if r['kind'] == 'trade_audit'][-1]
        assert rec['runner']['qty'] == 0.75 and rec['runner']['value_vs_activation'] == round(-1.5 - 0.0005 * 0.75 * -2.0, 6)
        rp = _cf(rec, 'causal_policy', TA.RULE_RUNNER)
        assert rp['value'] == round(rec['net_pnl'] - rec['runner']['value_vs_activation'], 6) and rp['qty'] == 0.75
        assert [c['qty'] for c in rec['cohorts']] == [1.0, 0.5] and rec['cohorts_n'] == 2
    finally:
        E.close_fill_writer(e.F['audit'])


# ---- predeclared policies + causal cutoff (info_cutoff < decision_at), mutation-proofed
def _live_lot(rng, side='LONG'):
    l = plot(side=side, tf='15m', mgmt=dict(tp_r=rng.uniform(0.5, 3.0), trail_atr=rng.uniform(0.5, 3.0)))
    px = 100.0
    for i in range(1, rng.randint(20, 150)):
        px = max(1.0, px * (1 + rng.gauss(0, 0.006)))
        if rng.random() < 0.05: l.update(qty=l['qty'] * rng.uniform(0.5, 1.5), avg=l['avg'] * rng.uniform(0.98, 1.02))
        TA.observe(l, px, _ts2(i * 3), fee_rate=0.0005)
    return l


def test_every_live_causal_decision_has_info_cutoff_strictly_before_the_decision():
    rng = random.Random(29)
    seen = 0
    for _ in range(150):
        l = _live_lot(rng, rng.choice(('LONG', 'SHORT')))
        for c in TA.close_record(l, 0.0)['counterfactuals']:
            if c['kind'] != 'causal_policy' or c['decision_t'] is None or c['rule'] == TA.RULE_RUNNER: continue
            seen += 1
            cut, dec = c['info_cutoff'], c['decision_at']
            assert c['causal_ok'] and cut['basis'] == 'observation order' and cut['seq'] < dec['seq'], c
            assert cut['t'] <= dec['t'] and c['predeclared']
    assert seen >= 100, seen


def test_cutoff_mutations_withhold_the_decision():
    """Mutation proof: make one input of a decision come from the decision's own observation (or later) -> withheld."""
    l = plot(mgmt=dict(tp_r=50.0, trail_atr=2.0))
    for i, m in enumerate((101.0, 104.0, 103.0, 101.9, 101.0), 1): TA.observe(l, m, _ts2(i), fee_rate=0.0)
    ok = pol(TA.close_record(l, 0.0, fee_rate=0.0)['counterfactuals'], 'trailing')
    assert ok['value'] == 1.9 and ok['causal_ok'] and ok['info_cutoff']['what'].startswith('running best')
    on = l['ex']['on']
    m1 = copy.deepcopy(l); m1['ex']['on']['tr_cut'] = [on['tr'][0], on['tr'][3]]             # best "seen" at the decision itself
    m2 = copy.deepcopy(l); m2['ex']['st'][0][8] = on['tr'][3] + 1                             # state recorded after the decision
    m3 = copy.deepcopy(l); m3['ap']['seq'] = 10 ** 6                                          # policy declared after the decision
    for m in (m1, m2, m3):
        c = pol(TA.close_record(m, 0.0, fee_rate=0.0)['counterfactuals'], 'trailing')
        assert c['value'] is None and c['causal_ok'] is False and 'withheld' in c['limitations'], c


def test_rules_are_never_reoptimised_after_the_close():
    """The parameters come from the declaration on the lot: editing the lot's mgmt after entry changes nothing; a
    different k / N passed by a caller is a labelled what-if, never a causal policy."""
    l = plot(mgmt=dict(tp_r=50.0, trail_atr=2.0))
    for i, m in enumerate((101.0, 104.0, 103.0, 101.9, 101.0), 1): TA.observe(l, m, _ts2(i), fee_rate=0.0)
    base = TA.close_record(l, 0.0)
    l['mgmt']['trail_atr'] = 0.5; l['atr0'] = 7.0; l['tf'] = '1h'
    again = TA.close_record(l, 0.0)
    assert [c['value'] for c in again['counterfactuals']] == [c['value'] for c in base['counterfactuals']]
    w = pol(TA.close_record(l, 0.0, trail_k=0.5)['counterfactuals'], 'trailing')
    assert w['kind'] == 'what_if' and w['predeclared'] is False
    assert l['ap']['v'] == TA.POLICY_VERSION and l['ap']['declared_at'] == iso(0) and l['ap']['seq'] == -1


def test_engine_declares_policies_at_entry_before_any_observation():
    import engine as E
    from test_safety import mk_engine, opened
    e, _ = mk_engine()
    try:
        k = opened(e)
        lot_ = e.state['lots'][k]
        assert 'ex' not in lot_ and lot_['ap']['coverage'] == 'full' and lot_['ap']['declared_at'] == lot_['opened']
        saved = json.load(open(e.F['state']))['lots'][k]['ap']
        assert saved['policies']['trailing']['params']['atr'] == 2.0 and saved['policies']['time_cap']['params']['bars'] == 48
    finally:
        E.close_fill_writer(e.F['audit'])


# ---- T05b outage / missing samples
def test_outage_samples_are_missing_not_used_and_gaps_are_attributed():
    l = plot(mgmt=dict(tp_r=50.0, trail_atr=2.0))
    TA.observe(l, 100.0, _ts2(0), fee_rate=0.0)
    TA.observe(l, 130.0, _ts2(8), fee_rate=0.0, missing=True)               # stale/untrusted mark during the outage
    TA.observe(l, 80.0, _ts2(16), fee_rate=0.0, missing=True)
    ex = l['ex']
    assert ex['mfe_px'] == 100.0 and ex['mae_px'] == 100.0 and ex['miss_obs'] == 2 and ex['n'] == 2
    TA.observe(l, 101.0, _ts2(200), fee_rate=0.0)
    assert ex['missing'][-1] == [_ts2(0), _ts2(200), 'exchange_outage'] and ex['mfe_px'] == 101.0
    TA.observe(l, 101.0, _ts2(500), fee_rate=0.0, down_last=_ts2(300))      # silent pause with a T05b failure inside it
    TA.observe(l, 101.0, _ts2(700), fee_rate=0.0, down_last=_ts2(300))      # silent pause, no failure inside it
    assert [m[2] for m in ex['missing'][-2:]] == ['exchange_outage', 'no_samples']
    TA.observe(l, 98.9, _ts2(900), fee_rate=0.0)                             # trailing (best 101, stop 99) fires after a gap
    tr = pol(TA.close_record(l, 0.0)['counterfactuals'], 'trailing')
    assert tr['after_gap'] and 'after a gap' in tr['limitations']
    rec = TA.close_record(l, 0.0)
    assert rec['coverage'] == 'partial' and 'exchange outage (T05b) during the trade' in rec['coverage_why'] and rec['missing_obs'] == 2


def test_engine_uses_t05b_incident_state_for_missing_samples(monkeypatch):
    import engine as E
    from test_safety import mk_engine, opened
    clk = _clocked(monkeypatch)
    e, _ = mk_engine()
    try:
        k = opened(e)
        e.trade.mark['BTCUSDT'] = 100.0; e.manage(e.trade.marks())
        e.health['incidents']['exchange-down'] = dict(key='exchange-down', open=False, keyed=True, count=3,
                                                      first=(clk[0] + timedelta(seconds=20)).isoformat(), last=(clk[0] + timedelta(seconds=90)).isoformat())
        assert e._audit_ctx() == (False, e.health['incidents']['exchange-down']['last'])
        clk[0] += timedelta(seconds=180); e.trade.mark['BTCUSDT'] = 101.0; e.manage(e.trade.marks())
        ex = e.state['lots'][k]['ex']
        assert ex['missing'] == [[ex['t0'], clk[0].isoformat(timespec='seconds'), 'exchange_outage']]
        e.health['incidents']['exchange-down']['open'] = True
        assert e._audit_ctx()[0] is True                                     # while open: samples are missing
    finally:
        E.close_fill_writer(e.F['audit'])


# ---- legacy / upgraded coverage
def test_legacy_closed_trades_are_audit_unavailable_and_upgraded_open_lots_partial(monkeypatch):
    import engine as E
    from test_safety import mk_engine, opened
    e, tmp = mk_engine()
    try:
        k = opened(e)
    finally:
        E.close_fill_writer(e.F['audit'])
    st = json.load(open(os.path.join(tmp, 'state.json'))); st['lots'][k].pop('ap')           # a lot opened before T05a
    json.dump(st, open(os.path.join(tmp, 'state.json'), 'w'))
    json.dump([dict(id='OLD|BTCUSDT|LONG|1', pnl=1.0)], open(os.path.join(tmp, 'history.json'), 'w'))   # closed before T05a
    e2, _ = mk_engine(tmp); _carry(e, e2)
    try:
        l2 = e2.state['lots'][k]
        assert l2['ap']['coverage'] == 'partial' and 'before T05a' in l2['ap']['why']
        cov = _events(e2, 'coverage')
        assert len(cov) == 1 and cov[0]['id'] == k and cov[0]['coverage'] == 'partial'
        _walk(e2, (101.0,)); e2.close_lot(k, 'signal', mark=101.0)
        rec = _last_audit(e2)
        assert rec['coverage'] == 'partial' and any('declared late' in w for w in rec['coverage_why'])
        c = e2.audit_summary()['coverage']
        assert c == dict(closed=2, audit_available=1, audit_unavailable=1, partial=1, legacy=1, audit_rotated=0, audit_missing=0)
        assert TA.audit_available(dict(id='OLD|BTCUSDT|LONG|1'), _events(e2)) is False and TA.audit_available(dict(id=k), _events(e2))
    finally:
        E.close_fill_writer(e2.F['audit'])


# ---- restart / rotation equivalence, bounded volume
_CMP = ('mfe_px', 'mfe_t', 'mae_px', 'mae_t', 'peak_pnl', 'peak_t', 'trough_pnl', 'trough_t', 'target_touch_t', 'target_touch_px')


def _view(l):
    ex = l['ex']; on = ex.get('on') or {}
    return ({k: ex.get(k) for k in _CMP}, [s[:8] for s in ex['st']],
            None if on.get('tr') is None else on['tr'][:2], None if on.get('tc') is None else on['tc'][:2])


def test_restart_from_a_checkpoint_is_equivalent_to_no_restart(monkeypatch):
    """Property: crash after observation k with state.json saved at j < k; restore from the newest checkpoint (one per
    change) and continue -> every excursion value, snapshot and online decision equals the uninterrupted run, and the
    restart is reported as a gap."""
    monkeypatch.setattr(TA, 'CKPT_MIN_S', 0)
    rng = random.Random(41)
    for trial in range(80):
        side = rng.choice(('LONG', 'SHORT'))
        a = plot(side=side, tf='15m', mgmt=dict(tp_r=rng.uniform(0.5, 3), trail_atr=rng.uniform(0.5, 3)))
        b = copy.deepcopy(a)
        marks = [100.0]
        for _ in range(rng.randint(10, 120)): marks.append(max(1.0, marks[-1] * (1 + rng.gauss(0, 0.006))))
        n = len(marks); j = rng.randint(0, n - 2); k = rng.randint(j + 1, n - 1)
        saved, ck = None, None
        for i, m in enumerate(marks):
            t = _ts2(i * 3)
            TA.observe(a, m, t, fee_rate=0.0005)
            if i < k:
                TA.observe(b, m, t, fee_rate=0.0005)
                ck = TA.checkpoint(b, 'id', t) or ck
                traded = rng.random() < 0.05                                # management changed the lot: the engine saves
                if traded: b['qty'] = a['qty'] = round(b['qty'] * 1.1, 6)  # state.json (its normal cadence) after the tick
                if i == j or (traded and i > j): saved = json.loads(json.dumps(b))
            else:
                if i == k:                                                  # crash + restart: state.json from j + newest checkpoint
                    b = saved; TA.restore_checkpoint(b, json.loads(json.dumps(ck))); TA.mark_restart(b)
                TA.observe(b, m, t, fee_rate=0.0005)
        assert _view(a) == _view(b), trial
        assert b['ex']['gaps_n'] == 1


def test_rotation_keeps_checkpoints_and_the_trade_window(monkeypatch, tmp_path):
    import engine as E
    p = str(tmp_path / 'trade_audit.jsonl')
    w = E.fill_writer(p, rotate_lines=10, slim=TA.slim_record)
    try:
        for i in range(17):
            if i % 4 == 3: w.emit(TA.clean(dict(crec(sleeve=f'S{i}'), id=f'L{i}', v=3)))
            else: w.emit(TA.clean(dict(v=3, kind='excursion', t=_ts2(i), id='L', n=i, ex=dict(n=i, mfe_px=100.0 + i))))
        assert w.flush(5)
        live = list(w.win)
    finally:
        assert E.close_fill_writer(p)
    assert sum(1 for _ in open(p + '.1')) == 10 and sum(1 for _ in open(p)) == 7
    w2 = E.fill_writer(p, rotate_lines=10, slim=TA.slim_record)
    try:
        assert list(w2.win) == live and len(live) == 4 and all(r['kind'] == 'trade_audit' for r in live)   # events are not in the window
    finally:
        E.close_fill_writer(p)
    ck = TA.load_checkpoints((p + '.1', p), ['L', 'X'])
    assert list(ck) == ['L'] and ck['L']['n'] == 16
    open(p, 'a').write('{broken\n' + json.dumps(dict(kind='excursion', id='L', n='x', ex={})) + '\n')
    assert TA.load_checkpoints((p + '.1', p, p + '.missing'), ['L'])['L']['n'] == 16


def test_event_volume_per_lot_is_bounded():
    """One lot for a day of 8 s mark ticks in a steady trend (a new extreme on EVERY tick) with outage pauses:
    checkpoint events stay within the documented bound; candle evaluations are one per closed candle."""
    l = plot(tf='15m', mgmt=dict(tp_r=500.0, trail_atr=50.0))
    n_ck, t_s, i = 0, 0, 0
    while t_s < 86400:
        i += 1; t_s += 8 if i % 2000 else 400                               # every 2000 ticks a 400 s silent pause
        if TA.observe(l, 100.0 + i * 0.001, _ts2(t_s), fee_rate=0.0) is None: raise AssertionError
        n_ck += TA.checkpoint(l, 'k', _ts2(t_s)) is not None
        if i % 500 == 0: l['qty'] = round(l['qty'] + 0.01, 6)               # occasional state changes (snapshots)
    bound = 1 + TA.SNAP_MAX + 1 + 2 + TA.GAPS_MAX + TA.MISS_MAX + 86400 // TA.CKPT_MIN_S + 1
    assert n_ck <= bound and n_ck < 200, (n_ck, bound, i)
    assert len(json.dumps(l['ex'])) < 15000
    ev = TA.checkpoint(l, 'k', _ts2(t_s + 10 ** 6))
    assert ev is None or len(json.dumps(TA.clean(ev))) < 12000


# ------------------------------------------------------------------ T05a third internal adversarial pass (verifier repros + fixes)
import re as _re                                                          # noqa: E402

_T3 = '2026-10-04T12:00:0'


def _grid_lot():
    # grid lot as grid._create -> engine._create_lot, then grid adds via engine._add_qty(..., 'grid_buy') (grid.py)
    return dict(symbol='BTCUSDT', side='LONG', avg=99.0, qty=2.0, e0=100.0, R=5.0, risk_usd=5.0, tf='1h', mgmt={},
                opened=_T3 + '0+00:00',
                fills=[[_T3 + '0+00:00', 'entry', 1.0, 100.0], [_T3 + '1+00:00', 'grid_buy', 1.0, 98.0]])


def test_grid_add_is_a_cohort_not_a_close():
    led = TA.cohort_ledger(_grid_lot())
    assert len(led['cohorts']) == 2 and [c['open'] for c in led['cohorts']] == [1.0, 1.0], led
    assert led['unmatched_close_qty'] is None and 'qty_mismatch' not in led


def test_grid_sell_on_a_short_grid_lot_is_an_add_too():
    l = dict(_grid_lot(), side='SHORT', avg=101.0, e0=100.0)
    l['fills'] = [[_T3 + '0+00:00', 'entry', 1.0, 100.0], [_T3 + '1+00:00', 'grid_sell', 1.0, 102.0]]
    assert len(TA.cohort_ledger(l)['cohorts']) == 2 and TA.fill_events(l, 'G')[0]['move'] == 'add'


def test_grid_add_cohort_event_is_an_add():
    ev = TA.fill_events(_grid_lot(), 'G|BTCUSDT|LONG|1')
    assert ev and ev[0]['move'] == 'add', ev


def test_grid_add_does_not_mark_tracking_late():
    l = _grid_lot()
    assert TA._tracking_late(l, _T3 + '2+00:00') is False


def test_grid_take_profit_after_adds_is_still_a_close():
    l = _grid_lot(); l['qty'] = 1.5
    l['fills'].append([_T3 + '3+00:00', 'grid_tp', 0.5, 101.0])
    led = TA.cohort_ledger(l)
    assert [c['open'] for c in led['cohorts']] == [0.5, 1.0] and 'qty_mismatch' not in led
    assert TA.fill_events(l, 'G')[0]['move'] == 'partial_close'


def test_an_unknown_add_kind_shows_up_as_a_quantity_mismatch():
    l = _grid_lot(); l['fills'][1][1] = 'mystery_add'                    # an add kind the whitelist does not know
    led = TA.cohort_ledger(l)
    assert led['qty_mismatch'] == -2.0
    rec = TA.close_record(dict(l, ex=None), 0.0)
    assert rec['qty_mismatch'] == -2.0 and any('unknown fill kind' in x for x in rec['limitations'])


def _add_whys(path):
    """Every literal `why` passed to _add_qty / _apply_add in a source file (IfExp branches included), plus the kind
    _create_lot writes into a new lot's fills; and every literal `why` of a close call."""
    tree = ast.parse(open(path, encoding='utf-8').read())
    adds, closes = set(), set()
    lits = lambda n: ({n.value} if isinstance(n, ast.Constant) and isinstance(n.value, str) else
                      lits(n.body) | lits(n.orelse) if isinstance(n, ast.IfExp) else set())
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            nm, a = n.func.attr, n.args
            if nm in ('_add_qty', '_apply_add') and len(a) >= 4: adds |= lits(a[3])
            elif nm in ('_market_close', '_apply_close') and len(a) >= 3: closes |= lits(a[2])
            elif nm in ('close_lot', '_finish') and len(a) >= 2: closes |= lits(a[1])
        if isinstance(n, ast.keyword) and n.arg == 'fills' and isinstance(n.value, ast.List):   # _create_lot: fills=[[t, 'entry', q, px]]
            for el in n.value.elts:
                if isinstance(el, ast.List) and len(el.elts) >= 2: adds |= lits(el.elts[1])
    return adds, closes


def test_every_add_fill_kind_the_engine_and_grid_book_is_an_entry_kind():
    """Whitelist guard: every fill `why` booked through _create_lot / _add_qty / _apply_add in engine.py and grid.py is
    an add (ENTRY_KINDS); no literal close `why` is. A new add kind fails here (and as qty_mismatch at runtime)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    adds, closes = set(), set()
    for f in ('engine.py', 'grid.py'):
        a, c = _add_whys(os.path.join(root, f)); adds |= a; closes |= c
    assert {'entry', 'pyramid_add', 'safety_order', 'entry_fallback', 'grid_buy', 'grid_sell'} <= adds, adds
    assert adds <= set(TA.ENTRY_KINDS), adds - set(TA.ENTRY_KINDS)
    assert closes and not closes & set(TA.ENTRY_KINDS), closes & set(TA.ENTRY_KINDS)
    assert {'grid_tp', 'grid_flat', 'take_profit_1', 'resync'} <= closes


def test_engine_grid_add_books_an_add_cohort_event():
    import engine as E
    from test_safety import mk_engine, opened
    e, _ = mk_engine()
    try:
        k = opened(e); l = e.state['lots'][k]
        _walk(e, (100.0,))
        assert e._add_qty(l, l['qty'], 99.0, 'grid_buy')
        ev = _events(e, 'cohort')[-1]
        assert ev['move'] == 'add' and ev['why'] == 'grid_buy' and ev['open_cohorts_n'] == 2
        assert l['ex']['late'] is False
    finally:
        E.close_fill_writer(e.F['audit'])


# ---- funnel: exactly one terminal outcome per candidate
_TERMINAL = ('taken', 'not_taken')


def _funnel(e, sym='BTCUSDT'):
    return [r['decision'] for r in _events(e, 'funnel') if r.get('symbol') == sym]


def test_maker_unfilled_candidate_is_not_both_taken_and_not_taken():
    """open_lot() returns True for ENTRY_ORDER=maker when the post-only order is PLACED: that is 'order_placed', the
    unfilled finalize is the one terminal 'not_taken'."""
    import engine as E
    from test_v31_engine import maker_engine, age
    from test_safety import SL, SG
    e, bk = maker_engine(MAKER_FALLBACK=False)
    try:
        sl = dict(SL); e.S['SLEEVES'] = [sl]
        e.compute_signals = lambda tf, syms, extra=(): ({f"{sl['id']}|BTCUSDT": dict(SG)}, {})
        e.cycle('4h')
        assert e.state['resting_entries'] and _funnel(e) == ['order_placed']
        for _ in range(10): age(e); e.manage(e.trade.marks())
        assert not e.state['lots'] and 'maker entry not filled' in e.missed[-1]['reason']
        assert _funnel(e) == ['order_placed', 'not_taken']
    finally:
        E.close_fill_writer(e.F['audit'])


def test_maker_filled_candidate_is_taken_once_when_the_lot_exists():
    import engine as E
    from test_v31_engine import maker_engine
    from test_safety import SL, SG
    e, bk = maker_engine()
    try:
        e.S['SLEEVES'] = [SL]
        e.compute_signals = lambda tf, syms, extra=(): ({f"{SL['id']}|BTCUSDT": dict(SG)}, {})
        e.cycle('4h')
        assert _funnel(e) == ['order_placed'] and not e.state['lots']
        bk.fill(); e.manage(e.trade.marks())
        assert e.state['lots'] and _funnel(e) == ['order_placed', 'taken']
        f = _events(e, 'funnel')[-1]
        assert f['candle'] == SG['time'] and f['sleeve'] == SL['id'] and f['side'] == 'LONG'
    finally:
        E.close_fill_writer(e.F['audit'])


def test_maker_partial_fill_with_market_fallback_is_taken_once():
    import engine as E
    from test_v31_engine import maker_engine, age
    from test_safety import SL, SG
    e, bk = maker_engine()
    try:
        e.S['SLEEVES'] = [SL]
        e.compute_signals = lambda tf, syms, extra=(): ({f"{SL['id']}|BTCUSDT": dict(SG)}, {})
        e.cycle('4h'); bk.fill(0.4)
        age(e); e.manage(e.trade.marks())
        for _ in range(3): age(e, total=True); e.manage(e.trade.marks())
        assert e.state['lots'] and not e.state['resting_entries']
        assert _funnel(e) == ['order_placed', 'taken']
    finally:
        E.close_fill_writer(e.F['audit'])


def test_maker_unfilled_market_fallback_is_taken_once():
    import engine as E
    from test_v31_engine import maker_engine, age
    from test_safety import SL, SG
    e, bk = maker_engine()
    try:
        e.S['SLEEVES'] = [SL]
        e.compute_signals = lambda tf, syms, extra=(): ({f"{SL['id']}|BTCUSDT": dict(SG)}, {})
        e.cycle('4h')
        for _ in range(10): age(e, total=True); e.manage(e.trade.marks())
        assert e.state['lots'] and 'open' in e.trade.calls
        assert _funnel(e) == ['order_placed', 'taken']
    finally:
        E.close_fill_writer(e.F['audit'])


def test_trailing_entry_that_fills_is_recorded_as_taken():
    import engine as E
    from test_v31_engine import cyc, lot_of
    from test_safety import mk_engine
    e, _ = mk_engine()
    try:
        cyc(e)
        for px in (99.0, 97.0, 98.1): e.trade.mark['BTCUSDT'] = px; e.manage(e.trade.marks())
        assert lot_of(e)['avg'] == 98.1
        assert _funnel(e) == ['armed_trailing', 'taken']
    finally:
        E.close_fill_writer(e.F['audit'])


def test_trailing_entry_expired_or_slot_removed_has_one_not_taken():
    import engine as E
    from test_v31_engine import cyc
    from test_safety import mk_engine
    for how in ('expire', 'removed'):
        e, _ = mk_engine()
        try:
            cyc(e)
            p = e.state['pending_entries']['T|BTCUSDT']
            if how == 'expire': p['until'] = 0
            else: e.S['SLEEVES'] = []
            e.trade.mark['BTCUSDT'] = 99.0; e.manage(e.trade.marks()); e.manage(e.trade.marks())
            assert not e.state['pending_entries'] and not e.state['lots']
            dec = _funnel(e)
            assert dec == ['armed_trailing', 'not_taken'], (how, dec)
            f = _events(e, 'funnel')[-1]
            assert (f['stage'], f['code']) == (('trailing', 'expired') if how == 'expire' else ('trailing', 'slot_removed'))
        finally:
            E.close_fill_writer(e.F['audit'])


# ---- no host IP / order ids in the audit files
_IP4 = _re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
_IP6 = _re.compile(r'[0-9A-Fa-f]{1,4}::[0-9A-Fa-f]{0,4}|(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}')
_ID8 = _re.compile(r'(?<![\d.])\d{8,}(?!\d|\.\d)')


def _strings(x, key=None):
    if isinstance(x, str): yield key, x
    elif isinstance(x, dict):
        for k, v in x.items(): yield from _strings(v, k)
    elif isinstance(x, list):
        for v in x: yield from _strings(v, key)


def test_funnel_detail_does_not_leak_the_request_ip():
    import binance_client as BC
    e = BC.BinanceError(-2015, 'Invalid API-key, IP, or permissions for action, request ip: 203.0.113.57')
    ev = TA.clean(TA.funnel_event('t', 'A', 'BTCUSDT', 'LONG', 'c', 'not_taken', f'could not set leverage/margin on Binance: {e}', None))
    assert '203.0.113.57' not in json.dumps(ev), ev['detail']
    assert (ev['stage'], ev['code']) == ('execution', 'leverage') and '[ip]' in ev['detail']


def test_redaction_keeps_times_prices_and_lot_ids():
    ev = TA.clean(dict(id='A|BTCUSDT|LONG|1759579200', t='2026-10-04T12:00:00+00:00', why='qty 12345678.5 @ 0.00012345678',
                       detail='order 123456789012 from 2001:db8::7 / 10.0.0.1 rejected'))
    assert ev['id'] == 'A|BTCUSDT|LONG|1759579200' and ev['t'] == '2026-10-04T12:00:00+00:00'
    assert ev['why'] == 'qty 12345678.5 @ 0.00012345678'
    assert ev['detail'] == 'order [id] from [ip] / [ip] rejected'


def test_audit_events_contain_no_ip_addresses_or_long_digit_ids(monkeypatch):
    """Every string in every emitted event (except the lot id under 'id') is free of IPv4/IPv6 addresses and 8+ digit
    runs, on the real engine paths: a -2015 leverage refusal in cycle(), an order failure with order ids, a lot's life."""
    import engine as E, binance_client as BC
    from test_safety import mk_engine, opened, SL, SG
    monkeypatch.setattr(TA, 'CKPT_MIN_S', 0)
    e, _ = mk_engine()
    try:
        def refuse(*a, **k): raise BC.BinanceError(-2015, 'Invalid API-key, IP, or permissions for action, request ip: 198.51.100.23')
        monkeypatch.setattr(e, '_ensure_leverage', refuse)
        e.S['SLEEVES'] = [SL]
        e.compute_signals = lambda tf, syms, extra=(): ({f"{SL['id']}|BTCUSDT": dict(SG)}, {})
        e.cycle('4h')
        assert 'request ip: 198.51.100.23' in e.missed[-1]['reason']        # the engine's own text is unchanged
        e.miss(SL, 'ETHUSDT', 'LONG', dict(SG, time='2026-10-04 08:00:00'),
               'order failed: APIError(code=-2019) orderId 8389765519262011 clientOrderId 123456789 host 2a01:4f8:c0c:1::2 ip 192.0.2.10')
        monkeypatch.undo(); monkeypatch.setattr(TA, 'CKPT_MIN_S', 0)
        k = opened(e)
        _walk(e, (101.0, 104.0, 103.0)); e.close_lot(k, 'signal', mark=102.0)
        rows = _events(e)
        assert {r['kind'] for r in rows} >= {'funnel', 'excursion', 'trade_audit'}
        bad = [(r['kind'], key, s) for r in rows for key, s in _strings(r)
               if key != 'id' and (_IP4.search(s) or _IP6.search(s) or _ID8.search(s))]
        assert not bad, bad[:5]
        assert any('[ip]' in (r.get('detail') or '') for r in rows if r['kind'] == 'funnel')
    finally:
        E.close_fill_writer(e.F['audit'])


# ---- rotation: line AND byte cap
def test_excursion_checkpoint_line_size_is_bounded_by_the_byte_cap():
    """A realistic DCA/grid excursion line is several KB, so 20k lines alone could reach ~145 MB: the byte cap bounds
    any audit file to AUDIT_ROTATE_BYTES + one line (<= 4x the fills cap)."""
    import engine as E
    from datetime import datetime as _dt
    l = dict(symbol='1000PEPEUSDT', side='LONG', avg=0.0123456789, qty=123456.0, e0=0.0123456789, R=0.000456789,
             risk_usd=12.3456, tf='1h', mgmt=dict(tp_r=2.0), opened='2026-10-04T12:00:00+00:00', realized=0.0, fees=0.0)
    t0 = _dt(2026, 10, 4, 12, tzinfo=timezone.utc).timestamp()
    for i in range(3000):
        if i % 50 == 0: l['qty'] += 1000.0; l['avg'] *= 0.999; l['fees'] += 0.0123456
        TA.observe(l, 0.0123 * (1 + 0.01 * ((i * 37) % 17 - 8) / 8), _dt.fromtimestamp(t0 + 8 * i, timezone.utc).isoformat(timespec='seconds'))
    ck = TA.checkpoint(l, 'DCA1H|1000PEPEUSDT|LONG|1759579200', '2026-10-05T12:00:00+00:00')
    n = len(json.dumps(TA.clean(ck)))
    worst = min(n * E.AUDIT_ROTATE_LINES, E.AUDIT_ROTATE_BYTES + n)
    assert worst <= E.FILL_ROTATE_BYTES * 4, (n, worst)
    assert n * E.AUDIT_ROTATE_LINES > E.AUDIT_ROTATE_BYTES                  # the byte cap is the one that binds here


def test_audit_writer_rotates_at_the_byte_cap_and_keeps_window_and_checkpoints(tmp_path):
    import engine as E
    p = str(tmp_path / 'trade_audit.jsonl')
    big = 'x' * 900
    w = E.fill_writer(p, rotate_lines=10_000, slim=TA.slim_record, rotate_bytes=5000)
    try:
        for i in range(14):
            if i % 5 == 4: w.emit(dict(crec(sleeve=f'S{i}'), id=f'L{i}', t=_ts2(i), v=3))
            else: w.emit(dict(v=3, kind='excursion', t=_ts2(i), id='L', n=i, ex=dict(n=i, pad=big)))
        assert w.flush(5)
        live = list(w.win)
    finally:
        assert E.close_fill_writer(p)
    assert os.path.getsize(p) <= 5000 and os.path.getsize(p + '.1') <= 5000
    w2 = E.fill_writer(p, rotate_lines=10_000, slim=TA.slim_record, rotate_bytes=5000)
    try:
        assert w2.nbytes == os.path.getsize(p)                               # a restart continues the byte count
        w2.emit(dict(v=3, kind='excursion', t=_ts2(20), id='L', n=20, ex=dict(n=20, pad=big))); assert w2.flush(5)
        assert os.path.getsize(p) <= 5000
    finally:
        E.close_fill_writer(p)
    assert all(r['kind'] == 'trade_audit' for r in live)
    assert TA.load_checkpoints((p + '.1', p), ['L'])['L']['n'] == 20


def test_engine_audit_writer_has_a_byte_cap():
    import engine as E
    from test_safety import mk_engine
    e, _ = mk_engine()
    try:
        assert e._auditw.rotate_lines == E.AUDIT_ROTATE_LINES and e._auditw.rotate_bytes == E.AUDIT_ROTATE_BYTES == 16_000_000
        assert e._fillw.rotate_bytes is None                                  # the fills writer keeps its own 5 MB rule
    finally:
        E.close_fill_writer(e.F['audit'])


def test_restart_after_the_checkpoint_rotated_away_is_flagged_not_broken(monkeypatch):
    import engine as E
    from test_safety import mk_engine, opened
    monkeypatch.setattr(TA, 'CKPT_MIN_S', 0)
    e, tmp = mk_engine()
    try:
        k = opened(e); _walk(e, (101.0, 104.0)); e.save_state()
    finally:
        E.close_fill_writer(e.F['audit'])
    os.replace(e.F['audit'], e.F['audit'] + '.old')                          # both files rotated past the checkpoint
    open(e.F['audit'], 'w').write(json.dumps(dict(v=3, kind='funnel', t='x')) + '\n')
    e2, _ = mk_engine(tmp); _carry(e, e2)
    try:
        l2 = e2.state['lots'][k]
        assert l2['ex']['ck_lost'] and l2['ex']['gap_open']
        _walk(e2, (103.0,)); e2.close_lot(k, 'signal', mark=103.0)
        rec = _last_audit(e2)
        assert rec['kind'] == 'trade_audit' and any('not found at a restart' in x for x in rec['limitations'])
        assert 'restart gap' in rec['coverage_why']
    finally:
        E.close_fill_writer(e2.F['audit'])


# ---- coverage: legacy vs rotated vs missing
def test_coverage_tells_legacy_rotated_and_missing_apart():
    recs = [dict(kind='trade_audit', id='B', t='2026-10-04T10:00:00+00:00', coverage='full')]
    hist = [dict(id='OLD', closed='2026-10-01T00:00:00+00:00'),             # before the audit existed
            dict(id='ROT', closed='2026-10-03T00:00:00+00:00'),             # audited, record rotated out of the window
            dict(id='B', closed='2026-10-04T10:00:00+00:00'),
            dict(id='GONE', closed='2026-10-04T11:00:00+00:00'),            # inside the window, no record
            dict(id='NOCLOSE')]
    c = TA.coverage(hist, recs, since='2026-10-02T00:00:00+00:00')
    assert c == dict(closed=5, audit_available=1, audit_unavailable=4, partial=0, legacy=2, audit_rotated=1, audit_missing=1)
    assert TA.coverage(hist, recs)['legacy'] == 4                            # unknown start: all unavailable are legacy
    assert TA.slim_record(dict(recs[0], counterfactuals=[]))['t'] == recs[0]['t']


def test_engine_records_the_audit_start_once(tmp_path):
    import engine as E
    from test_safety import mk_engine
    e, tmp = mk_engine(str(tmp_path))
    try:
        s1 = open(e.F['audit'] + '.since').read()
        assert TA._ts(s1) is not None and e._audit_since == s1
    finally:
        E.close_fill_writer(e.F['audit'])
    open(e.F['audit'] + '.since', 'w').write('2026-01-01T00:00:00+00:00')
    e2, _ = mk_engine(tmp)
    try:
        assert e2._audit_since == '2026-01-01T00:00:00+00:00'
        json.dump([dict(id='X', closed='2025-12-01T00:00:00+00:00'), dict(id='Y', closed='2026-02-01T00:00:00+00:00')],
                  open(e2.F['history'], 'w'))
        e2.history = json.load(open(e2.F['history']))
        c = e2.audit_summary()['coverage']
        assert (c['legacy'], c['audit_rotated']) == (1, 1)
    finally:
        E.close_fill_writer(e2.F['audit'])


# ---- restored-lot flag survives a missing first sample
def test_restored_flag_is_kept_until_observe_really_starts_tracking(monkeypatch):
    import engine as E
    from test_safety import mk_engine, opened
    e, tmp = mk_engine()
    try:
        k = opened(e)
    finally:
        E.close_fill_writer(e.F['audit'])
    st = json.load(open(os.path.join(tmp, 'state.json'))); st['lots'][k].pop('ex', None)
    json.dump(st, open(os.path.join(tmp, 'state.json'), 'w'))
    e2, _ = mk_engine(tmp); _carry(e, e2)
    try:
        assert k in e2._audit_restored
        monkeypatch.setattr(e2, '_audit_ctx', lambda: (True, None))         # first manage pass: samples missing
        e2.trade.mark['BTCUSDT'] = 100.0; e2.manage(e2.trade.marks())
        assert 'ex' not in e2.state['lots'][k] and k in e2._audit_restored
        monkeypatch.setattr(e2, '_audit_ctx', lambda: (False, None))
        e2.manage(e2.trade.marks())
        ex = e2.state['lots'][k]['ex']
        assert ex['late'] is True and 'restored at a restart' in ex['late_why'] and k not in e2._audit_restored
    finally:
        E.close_fill_writer(e2.F['audit'])


# ---- cycle(): every audit hook raising changes nothing
def _cycle_scenario(monkeypatch, broken, maker=False):
    import copy, engine as E
    from test_safety import mk_engine, SL, SG
    from test_v31_engine import TSL, Book
    fixed = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(E, 'now_utc', lambda: fixed)
    clock = [fixed.timestamp()]
    monkeypatch.setattr(E.time, 'time', lambda: clock[0])
    if broken:
        def boom(*a, **k): raise RuntimeError('audit broken')
        for name in [n for n in dir(TA) if callable(getattr(TA, n)) and not n.startswith('__') and
                     getattr(getattr(TA, n), '__module__', None) == 'trade_audit']:
            monkeypatch.setattr(TA, name, boom)
    e, _ = mk_engine(**(dict(ENTRY_ORDER='maker', MAKER_REPRICE=1, MAKER_WAIT_S=20) if maker else {}))
    bk = Book(e.trade) if maker else None
    if broken: monkeypatch.setattr(e._auditw, 'emit', boom)
    try:
        sl2 = dict(SL, id='B', symbols=['ETHUSDT'])
        tsl = dict(TSL, id='T', symbols=['SOLUSDT']) if 'SOLUSDT' in e.rules else None
        sls = [SL, sl2] + ([tsl] if tsl else [])
        e.S['SLEEVES'] = sls
        sig = {f"{SL['id']}|BTCUSDT": dict(SG), f"B|ETHUSDT": dict(SG, le=False, se=True, close=50.0)}
        if tsl: sig['T|SOLUSDT'] = dict(SG)
        e.compute_signals = lambda tf, syms, extra=(): (sig, {})
        e.cycle('4h')
        if maker:
            bk.fill(); clock[0] += 5; e.manage(e.trade.marks())
            for _ in range(4): clock[0] += 30; e.manage(e.trade.marks())
        for btc in (101.0, 104.0, 103.0):
            e.trade.mark.update(BTCUSDT=btc); clock[0] += 8; e.manage(e.trade.marks())
        exit_sig = {f"{SL['id']}|BTCUSDT": dict(SG, le=False, lx=True, time='2026-10-04 08:00:00'),
                    'B|ETHUSDT': dict(SG, le=False, se=False, time='2026-10-04 08:00:00')}
        e.compute_signals = lambda tf, syms, extra=(): (exit_sig, {})
        clock[0] += 4 * 3600; e.cycle('4h')
        lots = {k: {f: v for f, v in l.items() if f not in ('ex', 'ap')} for k, l in e.state['lots'].items()}
        missed = [{f: v for f, v in m.items() if f not in ('stage', 'code', 'detail')} for m in e.missed]
        return copy.deepcopy((lots, dict(e.trade.stops), list(e.trade.calls), dict(e.trade.pos), e.history, missed,
                              dict(e.state.get('pending_entries') or {}), dict(e.state.get('resting_entries') or {})))
    finally:
        E.close_fill_writer(e.F['audit'])


def test_cycle_never_changes_trading_when_every_audit_function_raises(monkeypatch):
    for maker in (False, True):
        normal = _cycle_scenario(monkeypatch, False, maker); monkeypatch.undo()
        broken = _cycle_scenario(monkeypatch, True, maker); monkeypatch.undo()
        assert normal == broken, maker
        lots, stops, calls, pos, history, missed, pend, rest = normal
        assert history or lots, maker                                       # entries really happened in cycle()


# ------------------------------------------------------------------ owner scope 2026-10-07 (PR #8): metrics, failure classes,
# regime snapshots, breakeven / regime-exit policies, segmentation, missed shorts, summary endpoint
def _snap(sym='BTCUSDT', tf='4h', t='2026-10-04T00:00:00', c=100.0, e20=101.0, e50=102.0, e200=110.0):
    return TA.regime_snapshot(sym, tf, t, None, c, e20, e50, e200)


def test_metrics_usd_pct_r_and_time_to_mfe_long_and_short():
    for side, sd in (('LONG', 1), ('SHORT', -1)):
        l = plot(side=side, mgmt=dict(tp_r=50.0)); l['realized'] = 0.0; l['fees'] = 0.0      # entry 100 @ iso(0), qty 1, risk 2
        TA.observe(l, 100 + sd * 3.0, iso(2), fee_rate=0.0)                 # MFE +3 at 2h
        l.update(qty=2.0, avg=100 - sd * 1.0)                                # an add: avg 99 (long) / 101 (short), qty 2
        TA.observe(l, 100 - sd * 4.0, iso(3), fee_rate=0.0)                 # MAE at 3h with the NEW state
        TA.observe(l, 100 + sd * 0.5, iso(4), fee_rate=0.0)
        rec = TA.close_record(l, 1.0, fee_rate=0.0)
        m = rec['metrics']
        assert m['mfe']['price'] == 3.0 and m['mfe']['usd'] == 3.0 and m['mfe']['pct'] == 3.0 and m['mfe']['r'] == 1.5, side
        assert m['mae']['avg_at'] == 100 - sd and m['mae']['qty_at'] == 2.0 and m['mae']['price'] == -3.0 and m['mae']['usd'] == -6.0
        assert m['mae']['pct'] == round(-3.0 / (100 - sd) * 100, 4) and m['mae']['r'] == -3.0
        assert m['time_to_mfe_s'] == 7200 and m['time_to_mae_s'] == 10800 and m['time_basis'].startswith('entry')
        assert m['net_usd'] == 1.0 and m['net_r'] == 0.5 and m['peak_net_usd'] == 3.0 and m['time_to_peak_net_s'] == 7200
        assert m['giveback_usd'] == 2.0 and m['giveback_pct_of_mfe'] == round(2 / 3 * 100, 2) and 'hindsight' in m['label']


def test_executable_mfe_is_the_net_peak_at_a_mark_sample_minus_slippage_never_a_candle_extreme():
    l = plot(mgmt=dict(tp_r=50.0)); l['realized'] = 0.0; l['fees'] = 0.0
    TA.observe(l, 104.0, iso(1), fee_rate=0.0005)
    TA.hold_eval(l, 'k', dict(t=iso(1), h=120.0, l=99.0, c=103.0, tf_s=3600), iso(2.01), 'hold')   # candle high 120: not used
    TA.observe(l, 101.0, iso(2), fee_rate=0.0005)
    m = TA.trade_metrics(l, 0.9, fee_rate=0.0005, slip_bps=10.0)
    peak = 4.0 - 104.0 * 0.0005
    assert l['ex']['peak_pnl'] == round(peak, 6) and m['executable_px'] == 104.0 and m['executable_qty'] == 1.0
    assert abs(m['executable_mfe_usd'] - (peak - 0.001 * 104.0 * (1 - 0.0005))) < 1e-6 and m['executable_mfe_usd'] < m['peak_net_usd']
    assert 'no candle extremes' in m['executable_basis'] and m['mfe']['px'] == 104.0
    old = plot(); old['ex'].pop('peak_px')                                   # tracked before the field: not guessed
    assert TA.trade_metrics(old, 0.0)['executable_mfe_usd'] is None


def _flags(peak, final, side='LONG', exec_px=True):
    l = plot(side=side)
    l['ex'].update(peak_pnl=peak, peak_t=iso(1))
    if not exec_px: l['ex'].pop('peak_px', None)
    else: l['ex'].update(peak_px=100.0, peak_q=1.0)
    m = TA.trade_metrics(l, final, fee_rate=0.0, slip_bps=0.0)
    return TA.failure_flags(l, final, m)


def test_failure_flags_green_red_giveback_never_green_both_sides():
    for side in ('LONG', 'SHORT'):
        assert _flags(-0.5, -1.0, side)['flags'][:1] == ['never_green']
        assert _flags(0.0, -1.0, side)['flags'][:1] == ['never_green']       # peak exactly 0 is never green
        f = _flags(4.0, -1.0, side)['flags']
        assert 'green_to_red' in f and 'gave_back_gt_50pct_mfe' in f and 'never_green' not in f
        f = _flags(4.0, 1.0, side)['flags']
        assert f[:1] == ['gave_back_gt_50pct_mfe'] and 'green_to_red' not in f
        assert not {'green_to_red', 'gave_back_gt_50pct_mfe', 'never_green'} & set(_flags(4.0, 2.0, side)['flags'])   # exactly 50%: no
        assert not {'green_to_red', 'gave_back_gt_50pct_mfe'} & set(_flags(4.0, 3.5, side)['flags'])
    g = _flags(0.01, -1.0)                                                    # slippage makes the peak non-executable
    l = plot(); l['ex'].update(peak_pnl=0.01, peak_px=100.0, peak_q=1.0)
    m = TA.trade_metrics(l, -1.0, fee_rate=0.0, slip_bps=5.0)               # 0.01 - 0.05 < 0: green only before slippage
    f = TA.failure_flags(l, -1.0, m)
    assert 'green_to_red' not in f['flags'] and 'never_green' not in f['flags'] and f['detail']['green_basis'] == 'executable_mfe_usd'
    assert 'green_to_red' in g['flags']
    u = plot(); u['ex']['peak_pnl'] = None
    f = TA.failure_flags(u, -1.0, TA.trade_metrics(u, -1.0))
    assert {'never_green', 'green_to_red', 'gave_back_gt_50pct_mfe'} <= set(f['unknown']) and not f['flags']


def test_regime_snapshot_and_causal_lookup():
    s = _snap()                                                               # candle 00:00-04:00, close < e200, e20 < e50
    assert s['cutoff_t'] == '2026-10-04T04:00:00+00:00' and s['trend'] == 'down' and s['above200'] is False
    ok = TA.regime_at(s, '2026-10-04T04:00:05+00:00')
    assert ok['status'] == 'ok' and ok['causal_ok'] and ok['info_cutoff'] <= ok['decision_at']
    early = TA.regime_at(s, '2026-10-04T03:59:59+00:00')                      # decision BEFORE the candle closed
    assert early['status'] == 'unknown' and early['causal_ok'] is False and 'not causal' in early['why']
    assert TA.regime_at(s, '2026-10-04T16:00:01+00:00')['status'] == 'unknown'   # > 2 bars old: stale
    assert TA.regime_at(None, 'x')['status'] == 'unknown'
    assert TA.regime_snapshot('X', '4h', '2026-10-04T00:00:00', None, 100.0, float('nan'), 1.0, 1.0)['trend'] == 'unknown'
    assert TA.regime_at(TA.regime_snapshot('X', '4h', '2026-10-04T00:00:00', None, 100.0, None, 1.0, 1.0), '2026-10-04T05:00:00')['status'] == 'unknown'
    assert TA.regime_snapshot('X', '7x', 'junk', None, 1, 1, 1, 1) is None
    up = _snap(c=120.0, e20=105.0, e50=104.0)
    assert up['trend'] == 'up' and _snap(c=120.0)['trend'] == 'mixed'


def test_trend_against_is_symmetric_and_never_guesses():
    at = lambda **k: TA.regime_at(_snap(**k), '2026-10-04T05:00:00+00:00')
    down, up, mixed = at(), at(c=120.0, e20=105.0, e50=104.0), at(c=120.0)    # mixed: above 200 but EMA20 < EMA50
    assert TA.trend_against('LONG', down) is True and TA.trend_against('SHORT', down) is False
    assert TA.trend_against('LONG', up) is False and TA.trend_against('SHORT', up) is True
    assert TA.trend_against('LONG', mixed) is True and TA.trend_against('SHORT', mixed) is True
    assert TA.trend_against('LONG', TA.regime_at(None, 'x')) is None and TA.trend_against('SHORT', None) is None


def _dca_lot(side, regimes):
    l = plot(side=side)
    l['fills'] = [[iso(0), 'entry', 1.0, 100.0]]
    l['ap']['ctx'] = dict(entry=None)
    for j, rg in enumerate(regimes):
        f = [iso(1 + j), 'safety_order', 1.0, 99.0]; l['fills'].append(f)
        TA.note_add(l, f, rg)
    return l


def test_dca_into_trend_long_short_and_unknown_regime_is_not_flagged():
    at = lambda **k: TA.regime_at(_snap(**k), '2026-10-04T05:00:00+00:00')
    down, up = at(), at(c=120.0, e20=105.0, e50=104.0)
    for side, against, wth in (('LONG', down, up), ('SHORT', up, down)):
        r = lambda l: TA.failure_flags(l, -1.0, TA.trade_metrics(l, -1.0))
        f = r(_dca_lot(side, [wth, against]))
        assert 'dca_into_trend' in f['flags'] and [a['against'] for a in f['detail']['dca_adds']] == [False, True], side
        f = r(_dca_lot(side, [wth, wth]))
        assert 'dca_into_trend' not in f['flags'] and 'dca_into_trend' not in f['unknown']
        f = r(_dca_lot(side, [wth, TA.regime_at(None, iso(2))]))             # regime not recorded -> unknown, not flagged
        assert 'dca_into_trend' not in f['flags'] and 'dca_into_trend' in f['unknown']
    l = _dca_lot('LONG', [down]); l['ap']['ctx']['adds'] = []                 # add fill without a stored snapshot
    f = TA.failure_flags(l, -1.0, TA.trade_metrics(l, -1.0))
    assert 'dca_into_trend' not in f['flags'] and 'dca_into_trend' in f['unknown']
    p = _dca_lot('LONG', []); p['fills'].append([iso(2), 'pyramid_add', 1.0, 101.0]); TA.note_add(p, p['fills'][-1], down)
    assert 'dca_into_trend' not in TA.failure_flags(p, 0.0, TA.trade_metrics(p, 0.0))['flags']      # only safety orders count


def test_long_in_bear_and_short_in_bull_from_the_entry_regime():
    at = lambda **k: TA.regime_at(_snap(**k), '2026-10-04T05:00:00+00:00')
    bear, bull = at(), at(c=120.0, e20=105.0, e50=104.0)
    ctx = lambda s, b: TA.entry_context(s, b, 'BTCUSDT 4h')
    assert ctx(bear, bear)['label'] == 'bear' and ctx(bull, bull)['label'] == 'bull' and ctx(bear, bull)['label'] == 'mixed'
    assert ctx(bear, TA.regime_at(None, 'x'))['label'] == 'unknown'
    for side, c, want in (('LONG', ctx(bear, bear), 'long_in_bear_regime'), ('SHORT', ctx(bull, bull), 'short_in_bull_regime')):
        l = plot(side=side); l['ap']['ctx'] = dict(entry=c)
        f = TA.failure_flags(l, 0.0, TA.trade_metrics(l, 0.0))
        assert want in f['flags'] and f['regime'] == c['label']
        l['ap']['ctx'] = dict(entry=ctx(bull, bull) if side == 'LONG' else ctx(bear, bear))      # with the regime: no flag
        assert want not in TA.failure_flags(l, 0.0, TA.trade_metrics(l, 0.0))['flags']
        l['ap']['ctx'] = dict(entry=ctx(bear, bull))                                             # mixed: no flag
        assert want not in TA.failure_flags(l, 0.0, TA.trade_metrics(l, 0.0))['flags']
        l['ap'].pop('ctx')
        f = TA.failure_flags(l, 0.0, TA.trade_metrics(l, 0.0))
        assert want not in f['flags'] and want in f['unknown']


def test_breakeven_after_costs_online_long_and_short_is_causal():
    for side, sd in (('LONG', 1), ('SHORT', -1)):
        l = plot(side=side, mgmt=dict(tp_r=50.0)); l['realized'] = 0.0; l['fees'] = 0.0      # R = 2: arms at +2
        for h, d in ((1, 1.0), (2, 2.5), (3, 1.0), (4, 0.0), (5, -3.0)): TA.observe(l, 100 + sd * d, iso(h), fee_rate=0.0005)
        on = l['ex']['on']
        assert on['be_arm'][0] == iso(2) and on['be'][0] == iso(4), side      # net <= 0 first at the entry price (exit fee)
        rec = TA.close_record(l, -3.0, fee_rate=0.0005)
        c = _cf(rec, 'causal_policy', 'breakeven')
        assert c['causal_ok'] and c['online'] and c['decision_at']['t'] == iso(4) and c['info_cutoff']['seq'] < c['decision_at']['seq']
        assert c['armed_t'] == iso(2) and abs(c['value'] - (-100.0 * 0.0005)) < 1e-9 and c['vs_actual'] > 0
        n = plot(side=side, mgmt=dict(tp_r=50.0)); n['realized'] = 0.0; n['fees'] = 0.0
        for h, d in ((1, 1.9), (2, -1.0)): TA.observe(n, 100 + sd * d, iso(h), fee_rate=0.0005)
        c = _cf(TA.close_record(n, -1.0), 'causal_policy', 'breakeven')
        assert c['value'] is None and 'never armed' in c['limitations']
    v1 = plot(); v1['ap']['policies'].pop('breakeven'); v1['ap']['policies'].pop('regime_exit')
    cs = {c['rule'][:12]: c for c in TA.close_record(v1, 0.0)['counterfactuals']}
    assert cs['breakeven af']['value'] is None and 'not declared' in cs['breakeven af']['limitations']
    assert cs['regime exit:']['value'] is None and 'not declared' in cs['regime exit:']['limitations']


def test_breakeven_path_fallback_needs_an_earlier_arming_point():
    l = plot(mgmt=dict(tp_r=50.0)); l['realized'] = 0.0; l['fees'] = 0.0
    path = [(iso(1), 102.0), (iso(2), 99.0)]
    c = TA.policy_breakeven(l, -1.0, 0.0, 0.0, path)
    assert c['causal_ok'] and c['decision_t'] == iso(2) and c['armed_t'] == iso(1) and c['value'] == -1.0
    c = TA.policy_breakeven(l, -1.0, 0.0, 0.0, [(iso(1), 99.0), (iso(2), 102.0)])   # never back after arming
    assert c['value'] is None


def test_regime_exit_trigger_is_causal_and_fills_at_the_next_mark():
    for side, sd, trend in (('LONG', 1, dict()), ('SHORT', -1, dict(c=120.0, e20=105.0, e50=104.0))):
        l = plot(side=side, mgmt=dict(tp_r=50.0), tf='4h'); l['realized'] = 0.0; l['fees'] = 0.0
        s = _snap(**trend)                                                    # against the side, closed 04:00
        assert TA.regime_trigger(l, s, '2026-10-04T04:00:00+00:00') is None  # decision AT the close: not strictly after
        assert TA.regime_trigger(l, _snap(**(dict(c=120.0, e20=105.0, e50=104.0) if side == 'LONG' else {})), '2026-10-04T04:00:05+00:00') is None
        d = TA.regime_trigger(l, s, '2026-10-04T04:00:05+00:00')
        assert d and d[0] == s['cutoff_t'] and TA.regime_trigger(l, s, '2026-10-04T08:00:05+00:00') is None   # decided once
        TA.observe(l, 100 + sd * 1.0, '2026-10-04T04:00:13+00:00', fee_rate=0.0)
        TA.observe(l, 100 - sd * 5.0, '2026-10-04T05:00:00+00:00', fee_rate=0.0)
        c = _cf(TA.close_record(l, -5.0, fee_rate=0.0), 'causal_policy', 'regime exit')
        assert c['causal_ok'] and c['decision_t'] == '2026-10-04T04:00:13+00:00' and c['value'] == 1.0 and c['vs_actual'] == 6.0, side
        assert c['info_cutoff']['t'] <= c['decision_at']['t'] and c['candle_cutoff'] == s['cutoff_t']
    l = plot(); assert _cf(TA.close_record(l, 0.0), 'causal_policy', 'regime exit')['value'] is None


def _arec(i, sleeve='A', side='LONG', net=1.0, r=0.5, flags=(), seg=None, sym='BTCUSDT'):
    return dict(kind='trade_audit', id=f'{sleeve}|{sym}|{side}|{i}', sleeve=sleeve, side=side, tf='4h', symbol=sym, net_pnl=net, r=r,
                flags=list(flags), flags_unknown=[], segment=dict(dict(strategy=sleeve, side=side, symbol=sym, tf='4h', regime='bull',
                                                                        dca='0', runner='no_runner'), **(seg or {})))


def test_segments_aggregate_counts_expectancy_flags_and_samples():
    recs = [_arec(1, net=2.0, r=1.0), _arec(2, net=-1.0, r=-0.5, flags=['green_to_red', 'gave_back_gt_50pct_mfe']),
            _arec(3, side='SHORT', net=-2.0, r=-1.0, flags=['never_green'], seg=dict(regime='bear', dca='3+', runner='runner')),
            _arec(4, net=-1.0, r=-0.5, flags=['green_to_red']), _arec(5, net=0.5, r=0.25, flags=['green_to_red']),
            _arec(6, net=-0.5, r=-0.25, flags=['green_to_red']), dict(reason='entries paused'), 'junk', dict(kind='trade_audit')]
    s = TA.segments(recs)
    assert s['trades'] == 7
    long_ = next(x for x in s['by']['side'] if x['key'] == 'LONG')
    assert long_['n'] == 5 and long_['wins'] == 2 and long_['win_rate'] == 0.4 and long_['exp_usd'] == 0.0 and long_['exp_r'] == 0.0
    assert long_['flags'] == {'green_to_red': 4, 'gave_back_gt_50pct_mfe': 1}
    assert {x['key']: x['n'] for x in s['by']['dca']} == {'0': 5, '3+': 1, '?': 1} and {x['key'] for x in s['by']['runner']} == {'runner', 'no_runner'}
    assert {x['key'] for x in s['by']['regime']} == {'bull', 'bear', 'unknown'}
    assert s['flags']['green_to_red']['n'] == 4 and s['flags']['green_to_red']['samples'] == ['A|BTCUSDT|LONG|4', 'A|BTCUSDT|LONG|5', 'A|BTCUSDT|LONG|6']
    assert s['flags']['never_green'] == dict(n=1, samples=['A|BTCUSDT|SHORT|3']) and s['flags']['dca_into_trend']['n'] == 0
    assert set(s['by']) == set(TA.SEG_DIMS)
    assert TA.segments([TA.slim_record(json.loads(json.dumps(r))) for r in recs]) == s      # the slimmed window gives the same


def test_close_record_carries_metrics_flags_and_segment():
    l = plot(mgmt=dict(tp_r=50.0), sleeve='S1', tf='4h'); l['realized'] = 0.0; l['fees'] = 0.0
    for h, m in ((1, 104.0), (2, 99.0)): TA.observe(l, m, iso(h), fee_rate=0.0)
    l['fills'] = [[iso(0), 'entry', 1.0, 100.0], [iso(1.5), 'take_profit_1', 0.5, 103.0], [iso(2), 'stop', 0.5, 99.0]]
    rec = TA.close_record(l, 1.0, fee_rate=0.0)
    assert rec['flags'] == ['gave_back_gt_50pct_mfe'] and 'long_in_bear_regime' in rec['flags_unknown']
    assert rec['segment'] == dict(strategy='S1', side='LONG', symbol='BTCUSDT', tf='4h', regime='unknown', dca='0', runner='runner')
    assert rec['metrics']['mfe']['usd'] == 4.0 and TA.clean(rec) == json.loads(json.dumps(TA.clean(rec), allow_nan=False))


def test_missed_short_definition():
    ms = TA.missed_short
    assert ms('side_masked', 'SHORT', dict(se=True)) == 'side_masked' and ms('side_masked', 'LONG', dict(le=True, se=False)) is None
    assert ms('not_taken', 'SHORT', None) == 'not_taken' and ms('not_taken', 'LONG', dict(le=True, se=True)) == 'long_preferred'
    assert ms('not_taken', 'LONG', dict(le=True, se=False)) is None and ms('taken', 'LONG', dict(le=True, se=True)) == 'long_preferred'
    assert ms('taken', 'SHORT', dict(se=True)) is None and ms('order_placed', 'SHORT', None) is None and ms('warning', 'SHORT', None) is None
    ev = TA.funnel_event('t', 'L', 'BTCUSDT', 'SHORT', 'c', 'side_masked', raw=dict(le=False, se=True, sides='long'))
    assert ev['missed_short_opportunity'] == 'side_masked'
    assert 'missed_short_opportunity' not in TA.funnel_event('t', 'L', 'BTCUSDT', 'LONG', 'c', 'taken', raw=dict(le=True, se=False))


def test_engine_counts_missed_shorts_once_per_candle_and_summarises_them(monkeypatch):
    import engine as E
    longonly = E.sleeve('L', 'ema_mom', 0.5, 0.02, 2, ['BTCUSDT'], sides='long', tf='4h', enabled=False)
    e, df = _signal_engine(monkeypatch, [longonly], dict(le=False, se=True, lx=False, sx=False))
    try:
        e.cycle('4h'); e.cycle('4h')                                          # the same candle twice: counted once
        a = e.audit_summary()['missed_short']
        assert a['n'] == 1 and a['by_why'] == {'side_masked': 1} and a['samples'][0]['symbol'] == 'BTCUSDT'
        from test_safety import SG
        e.miss(longonly, 'ETHUSDT', 'SHORT', dict(SG, time='2026-10-05 00:00:00'), 'hedge mode off (shorts unavailable)')
        a = e.audit_summary()['missed_short']
        assert a['n'] == 2 and a['by_code'] == {'side_mask/hedge_off': 1} and a['short_not_taken_in_missed_list'] == 1
        assert 'no hindsight' in a['basis']
    finally:
        E.close_fill_writer(e.F['audit'])


def _ind_frame(n=300, c=100.0, e20=101.0, e50=102.0, e200=110.0):
    return pd.DataFrame(dict(t=pd.date_range(end='2026-10-04 00:00:00', periods=n, freq='4h'), o=c, h=c + 1, l=c - 1, c=c, v=1.0,
                             atr=2.0, e20=e20, e50=e50, e200=e200))


def test_engine_records_the_entry_and_dca_add_regime_causally_and_flags_the_close(monkeypatch):
    import engine as E
    from test_safety import mk_engine, SL, SG
    clk = _clocked(monkeypatch, datetime(2026, 10, 4, 4, 0, 5, tzinfo=timezone.utc))
    monkeypatch.setattr(E.time, 'time', lambda: clk[0].timestamp())
    e, _ = mk_engine()
    try:
        bear = _ind_frame()                                                   # close 100 < EMA200 110, EMA20 < EMA50
        e._audit_regime_cache('4h', {'BTCUSDT': bear, 'ETHUSDT': bear})
        def boom(*a, **k): raise AssertionError('no fetch / I/O for a regime snapshot')
        monkeypatch.setattr(e, 'candles', boom); monkeypatch.setattr(e.data, 'klines', boom, raising=False)
        sl = dict(SL, key='dca_dip', mgmt={'dca': {'n': 3, 'step_atr': 1.0, 'scale': 1.5, 'tp_atr': 1.0, 'stop_atr': 2.0}}); e.S['SLEEVES'] = [sl]
        e.S['DCA_ENABLED'] = True  # DCA mechanics test: explicit opt-in (owner decision 2026-10-07 = DCA off by default)
        assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
        k, lot_ = next(iter(e.state['lots'].items()))
        ent = lot_['ap']['ctx']['entry']
        assert ent['label'] == 'bear' and ent['btc_basis'] == 'BTCUSDT 4h' and ent['symbol']['status'] == 'ok'
        assert ent['symbol']['info_cutoff'] <= ent['symbol']['decision_at'] == lot_['opened']
        clk[0] += timedelta(seconds=30)
        e.trade.mark['BTCUSDT'] = lot_['levels'][0] - 0.01; e.manage(e.trade.marks())
        assert lot_['dca'] == 1
        add = lot_['ap']['ctx']['adds'][0]
        assert add['why'] == 'safety_order' and add['regime']['status'] == 'ok' and add['regime']['trend'] == 'down'
        assert add['regime']['info_cutoff'] <= add['regime']['decision_at'] and add['fill_i'] == 1
        e.close_lot(k, 'signal', mark=lot_['levels'][0] - 1.0)
        rec = _last_audit(e)
        assert rec['kind'] == 'trade_audit' and {'dca_into_trend', 'long_in_bear_regime', 'never_green'} <= set(rec['flags'])
        assert rec['segment']['regime'] == 'bear' and rec['segment']['dca'] == '1'
        s = e.audit_summary()['segments']
        assert s['flags']['dca_into_trend'] == dict(n=1, samples=[k]) and s['by']['regime'][0]['key'] == 'bear'
    finally:
        E.close_fill_writer(e.F['audit'])


def test_engine_without_a_cached_snapshot_records_unknown_never_a_guess(monkeypatch):
    import engine as E
    from test_safety import mk_engine, opened
    e, _ = mk_engine()
    try:
        k = opened(e)
        ent = e.state['lots'][k]['ap']['ctx']['entry']
        assert ent['label'] == 'unknown' and ent['symbol']['status'] == 'unknown'
        e._audit_regime_cache('4h', {'BTCUSDT': pd.DataFrame(dict(t=[pd.Timestamp('2026-10-04')], c=[1.0]))})   # no EMAs: unknown
        assert e._audit_rg[('BTCUSDT', '4h')]['trend'] == 'unknown'
        e._audit_regime_cache('4h', {'BTCUSDT': 'junk'}); e._audit_regime_cache('4h', None)   # never raises
    finally:
        E.close_fill_writer(e.F['audit'])


def test_cycle_hold_records_the_candle_trend_and_triggers_the_regime_exit(monkeypatch):
    import engine as E
    from test_safety import mk_engine, opened, SL, SG
    clk = _clocked(monkeypatch)
    e, _ = mk_engine()
    try:
        k = opened(e)
        e.trade.mark['BTCUSDT'] = 100.5; e.manage(e.trade.marks())
        df = _ind_frame(n=1); df['t'] = [pd.Timestamp('2026-10-04 04:00:00')]
        e.S['SLEEVES'] = [SL]
        e.compute_signals = lambda tf, syms, extra=(): ({f"{SL['id']}|BTCUSDT": dict(SG, le=False, lx=False)}, {'BTCUSDT': df})
        clk[0] = datetime(2026, 10, 4, 8, 0, 5, tzinfo=timezone.utc); e.cycle('4h')
        h = _events(e, 'hold_eval')[-1]
        assert h['regime'] == dict(trend='down', above200=False, ema20_gt_ema50=False) and k in e.state['lots']
        on = e.state['lots'][k]['ex']['on']
        assert on['rx_dec'][0] == '2026-10-04T08:00:00+00:00' and on['rx_dec'][1] == '2026-10-04T08:00:05+00:00'
        clk[0] = datetime(2026, 10, 4, 8, 0, 13, tzinfo=timezone.utc)
        e.trade.mark['BTCUSDT'] = 99.0; e.manage(e.trade.marks())
        assert on['rx'][0] == '2026-10-04T08:00:13+00:00' and on['rx'][1] == 99.0
        assert e._audit_rg[('BTCUSDT', '4h')]['trend'] == 'down'
    finally:
        E.close_fill_writer(e.F['audit'])


def test_audit_summary_endpoint_is_read_only_and_exposes_segments():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py'), encoding='utf-8').read()
    assert "if p == '/api/audit_summary':" in src
    blk = src[src.index("if p == '/api/audit_summary':"):].split('\n', 1)[1].split("if p == '/api/history'")[0]   # the handler body
    assert 'audit_summary()' in blk and 'lock' not in blk and 'save' not in blk
    from test_safety import mk_engine
    import engine as E
    e, _ = mk_engine()
    try:
        a = e.audit_summary()
        assert set(a['segments']) >= {'trades', 'by', 'flags'} and a['missed_short']['n'] == 0
        json.dumps(a, allow_nan=False)
    finally:
        E.close_fill_writer(e.F['audit'])


def test_panel_audit_card_is_additive_collapsed_and_escaped():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'panel.html'), encoding='utf-8').read()
    if 'id="auditCard"' not in src: return                                    # API-only build (the UI commit dropped)
    card = src[src.index('id="auditCard"') - 60:][:400]
    assert '<details' in card and ' open' not in card.split('>')[0]
    js = src[src.index('function renderAudit('):src.index('function renderTrades(')]
    assert 'esc(' in js and 'innerHTML' not in js and 'try{renderAudit()}catch(e){}' in src


def test_panel_audit_card_renders_escaped():
    import shutil, subprocess, tempfile
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'panel.html'), encoding='utf-8').read()
    if 'function renderAudit(' not in src or not shutil.which('node'): return
    pre = '\n'.join(src[src.index(k):].split('\n', 1)[0] for k in ('const fmt=', 'const susd=', 'const esc='))
    js = pre + '\n' + src[src.index('const AUDF='):src.index('function renderTrades(')]
    evil = '<img src=x onerror=alert(1)>'
    seg = dict(trades=2, by=dict(symbol=[dict(key=evil, n=2, win_rate=0.5, exp_usd=-1.25, exp_r=-0.5)]),
               flags=dict(green_to_red=dict(n=1, samples=[evil]), never_green=dict(n=0, samples=[])))
    D = dict(health=dict(audit=dict(segments=seg, missed_short=dict(n=3, by_why={evil: 3}))))
    harness = js + '\nlet out="";const el={};const $=()=>el;const setHTML=(e,h)=>{out=h};const D=' + json.dumps(D) + \
              ';renderAudit();process.stdout.write(out);'
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f: f.write(harness)
    try: html = subprocess.run(['node', f.name], capture_output=True, text=True, encoding='utf-8', timeout=30, check=True).stdout
    finally: os.unlink(f.name)
    assert '<img' not in html and '&lt;img src=x onerror=alert(1)&gt;' in html and 'green to red' in html and '50%' in html


def test_breakeven_never_arms_and_exits_on_the_same_observation():
    """A booked loss makes the net negative at the very observation that arms (+1R): the exit can only be decided at a
    LATER observation (arming is information from before the decision)."""
    l = plot(mgmt=dict(tp_r=50.0)); l['realized'] = -3.0; l['fees'] = 0.0             # R = 2
    TA.observe(l, 102.5, iso(1), fee_rate=0.0)                                          # arms; net = -0.5
    on = l['ex']['on']
    assert on['be_arm'][0] == iso(1) and on['be'] is None
    TA.observe(l, 102.6, iso(2), fee_rate=0.0)
    assert on['be'][0] == iso(2)
    c = _cf(TA.close_record(l, -0.4, fee_rate=0.0), 'causal_policy', 'breakeven')
    assert c['causal_ok'] and c['decision_at']['t'] == iso(2) and c['info_cutoff']['seq'] < c['decision_at']['seq']


def test_segments_breakeven_trade_is_not_a_win():
    s = TA.segments([_arec(1, net=0.0, r=0.0), _arec(2, net=1.0, r=0.5)])
    row = s['by']['side'][0]
    assert row['wins'] == 1 and row['win_rate'] == 0.5 and row['exp_usd'] == 0.5 and row['exp_r'] == 0.25


def test_engine_missed_short_counter_keys_per_candle_and_keeps_the_first_reason():
    from test_safety import mk_engine
    import engine as E
    e, _ = mk_engine()
    try:
        raw = dict(le=True, se=True, time='c1')
        e._audit_missed_short('S', 'BTCUSDT', 'LONG', 'c1', 'taken', raw)                       # long preferred
        e._audit_missed_short('S', 'BTCUSDT', 'SHORT', 'c1', 'not_taken', None, 'entries paused')   # same candle: not again
        assert list(e._audit_mso.values()) == [dict(sleeve='S', symbol='BTCUSDT', candle='c1', why='long_preferred', code=None)]
        e._audit_missed_short('S', 'BTCUSDT', 'SHORT', 'c2', 'not_taken', raw, 'entries paused')   # next candle counts
        assert len(e._audit_mso) == 2 and e._audit_mso[('S', 'BTCUSDT', 'c2')]['code'] == 'filter/paused'
        e._audit_missed_short('S', 'BTCUSDT', None, 'c3', 'side_masked', raw)                     # raw of ANOTHER candle: ignored
        assert len(e._audit_mso) == 2
        for i in range(700): e._audit_missed_short('S', 'X', 'SHORT', f'k{i}', 'not_taken', None)
        assert len(e._audit_mso) == 600                                                           # bounded
    finally:
        E.close_fill_writer(e.F['audit'])
