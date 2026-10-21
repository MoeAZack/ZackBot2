"""REX add-on loader (Codex ruling 6185035993 point 3): the base archive stays immutable; a separate, checksum-pinned
`zb-rex-gap-fill/1` add-on (binance_archive_fill.py) may fill ONLY the exact holes of the signed `zb-rex-gap-inventory/1`
report (rex_gap_inventory.py), never overwrite a base row, never interpolate, with its provenance retained.

SYNTHETIC ONLY: a tmp archive store with injected 4h-kline and 1h-mark holes, a hand-built attested manifest, the
committed split plan (no data), a fake data.binance.vision behind the injectable fetcher, a tmp ledger. No real store,
data folder, network or holdout."""
import copy
import hashlib
import io
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_range_vwap_rev_loader as LD                                                     # noqa: E402
import test_rex_gap_tools as GT                                                             # noqa: E402

import binance_archive_fill as BF                                                           # noqa: E402
import ledger as L                                                                          # noqa: E402
import manifest as M                                                                        # noqa: E402
import pit_run as PR                                                                        # noqa: E402
import progress as PG                                                                       # noqa: E402
import range_vwap_rev as RV                                                                 # noqa: E402
import rex_gap_inventory as GI                                                              # noqa: E402
import run_r4 as G                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402

ROOT, SYMS, HOUR, TF, DAY = LD.ROOT, LD.SYMS, LD.HOUR, LD.TF, LD.DAY
LO, HI, WINDOW = LD.LO, LD.HI, LD.WINDOW
U = S.parse_utc
# AAA: a 2-bar 4h hole (filled) + a 1-bar 4h hole the add-on does NOT cover; BBB: the 07:00 mark funding 08:00 needs
FILLED_BARS = {U('2024-02-20T08:00:00Z'), U('2024-02-20T12:00:00Z')}
OPEN_BAR = U('2024-03-12T16:00:00Z')
DROP_BARS = {'AAAUSDT': FILLED_BARS | {OPEN_BAR}}
DROP_MARKS = {'BBBUSDT': {U('2024-03-05T07:00:00Z')}}


def zbytes(member, lines) -> bytes:
    """A zip with a FIXED member timestamp (deterministic bytes, so manifest / tape digests are reproducible)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(zipfile.ZipInfo(member, date_time=(2024, 1, 1, 0, 0, 0)), '\n'.join(lines) + '\n')
    return buf.getvalue()


def put(store, rel, lines):
    p = os.path.join(store, *rel.split('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    raw = zbytes(rel.rsplit('/', 1)[1][:-4] + '.csv', lines)
    with open(p, 'wb') as f:
        f.write(raw)
    with open(p + '.ok', 'w') as f:
        f.write(hashlib.sha256(raw).hexdigest())


def build_store(store):
    """LD.build_store with holes injected (the true rows of the holes are what the fake archive serves)."""
    for k, sym in enumerate(SYMS):
        for y, m in LD.MONTHS:
            a, b = LD.month_range(y, m)
            tag = f'{y}-{m:02d}'
            put(store, f'um/monthly/klines/{sym}/4h/{sym}-4h-{tag}.zip',
                   [LD.kline(k, t, TF) for t in range(a, b, TF) if t not in DROP_BARS.get(sym, ())])
            put(store, f'um/monthly/markPriceKlines/{sym}/1h/{sym}-1h-{tag}.zip',
                   [LD.kline(k, t, HOUR) for t in range(a, b, HOUR) if t not in DROP_MARKS.get(sym, ())])
            put(store, f'um/monthly/fundingRate/{sym}/{sym}-fundingRate-{tag}.zip',
                   ['calc_time,funding_interval_hours,last_funding_rate'] +
                   [f'{t + 3},8,{0.0001 * (1 + k)}' for t in range(a, b, 8 * HOUR)])
    return M.build(['um/monthly'], manifest_id='fx-rex-addon', source_class='archive-verified', base=store,
                   data_root='binance_um', survivor_only=False, loader=M.ARCHIVE_LOADER)


def day_zip(series, sym, iv, day, *, only=None):
    """The archive's daily zip of one UTC day (TRUE rows, the same generator as the store); `only` limits the rows."""
    k = SYMS.index(sym)
    step = TF if iv == '4h' else HOUR
    a = U(f'{day}T00:00:00Z')
    name = f'{sym}-{iv}-{day}'
    rows = [LD.kline(k, t, step) for t in range(a, a + DAY, step) if only is None or t in only]
    raw = zbytes(name + '.csv', rows)
    return BF.url_for('daily', series, sym, iv, day), raw, f'{hashlib.sha256(raw).hexdigest()}  {name}.zip'.encode()


DAYS = {'bars': ('klines', 'AAAUSDT', '4h', '2024-02-20'), 'mark': ('markPriceKlines', 'BBBUSDT', '1h', '2024-03-05'),
        'open': ('klines', 'AAAUSDT', '4h', '2024-03-12')}


def archive(days=tuple(DAYS), extra=()):
    return [day_zip(*DAYS[d]) for d in days] + list(extra)


@pytest.fixture
def fx(tmp_path, monkeypatch):
    monkeypatch.setattr(PR, '_git', LD.fake_git())
    store = str(tmp_path / 'store')
    man = build_store(store)
    art = LD.artifact()
    notice = str(tmp_path / 'notice.json')
    with open(notice, 'w', newline='\n') as f:
        json.dump(art, f)
    ledger = str(tmp_path / 'ledger' / 'range_vwap_rev.jsonl')
    os.makedirs(os.path.dirname(ledger))
    src = {'manifest': man, 'static': LD.static(), 'rules': LD.RULES, 'ledger': ledger}
    plan = PR.rex_plan(ROOT)
    rep = GI.build(man, store, symbols=list(SYMS), window=WINDOW, plan=plan, tier='CORE-10', repo=ROOT)
    return {'store': store, 'man': man, 'art': art, 'notice': notice, 'ledger': ledger, 'src': src, 'plan': plan,
            'rep': rep, 'out': str(tmp_path / 'out'), 'tmp': tmp_path, 'zips': str(tmp_path / 'zips')}


def make_addon(fx, rep=None, days=tuple(DAYS), extra=()):
    """BF.fill against the fake archive; every verified zip + .CHECKSUM is kept in fx['zips'] (the re-proof source)."""
    c = GT.Clock()
    return BF.fill(rep or fx['rep'], GT.fake_archive(archive(days, extra)), sleep=c.sleep, clock=c.clock,
                   keep_dir=fx['zips'])


def addon_bytes(addon) -> bytes:
    return (json.dumps(addon, sort_keys=True, indent=1) + '\n').encode()


def with_addon(fx, addon, rep=None, pin=None, **kw):
    """rex_load with an add-on: raw file bytes, the pinned sha256 (default: of these bytes), report and kept zips."""
    raw = addon_bytes(addon)
    return load(fx, addon=raw, addon_sha256=pin or hashlib.sha256(raw).hexdigest(), addon_report=rep or fx['rep'],
                addon_zips=fx['zips'], **kw)


def load(fx, **kw):
    a = dict(repo=ROOT, store=fx['store'], tier='CORE-10', window=WINDOW, notice_path=fx['notice'],
             notice_sha256=fx['art']['digest'], author='t', cairo_date='2026-10-15', plan=fx['plan'])
    a.update(kw)
    return PR.rex_load(fx['src'], **a)


def run(fx, env, sub):
    e = dict(env, plan=fx['plan'], tier='CORE-10')
    return PR.run_rex(e, os.path.join(fx['out'], sub), window=WINDOW, clock=None)


def docs(final):
    doc = PG.read(final)
    d = os.path.dirname(final)
    rd = lambda k: json.load(open(os.path.join(d, f'{k}-{doc["run"]}.json')))
    return doc, rd('summary'), rd('detail')


def canon(o) -> str:
    return hashlib.sha256(json.dumps(o, sort_keys=True, default=repr).encode()).hexdigest()


def ledger_recs(fx) -> list:
    return L._check_state(fx['ledger'])[RV.FAMILY]


def golden(fx, sub='base') -> str:
    """The sealed summary / detail + the family ledger of a no-add-on run over the holed store. Excluded: the ledger
    chain fields and the progress run id (`detail.run` of the trial records), which hashes the evaluator CODE digest
    (pit_run.py itself) and so changes with any commit by design; the summary / detail `run` (sha of the run config)
    is kept."""
    final = run(fx, load(fx), sub)
    _, summary, detail = docs(final)
    recs = [dict({k: v for k, v in r.items() if k not in ('prev', 'seq', 'detail')},
                 detail={k: v for k, v in r['detail'].items() if k != 'run'}) for r in ledger_recs(fx)]
    return canon([summary, detail, recs])


# derived by executing `golden` against `git archive cbdef58` (the loader before this slice) on this exact fixture
GOLDEN_NO_ADDON = 'b1a811f3eb5f2cfbf9e5b591874128d5a82fa9097e2e09b2b6e7a5e89ab43a6c'


def reseal(addon, fn):
    a = copy.deepcopy(addon)
    fn(a)
    a['digest'] = BF.digest(a)
    return a


def row_of(addon, sym, t):
    return next(r for r in addon['rows'] if r['symbol'] == sym and r['open_ms'] == t)


def move_row(t, k=0, step=TF):
    """Re-point the AAA 2024-02-20T08:00 row to open `t` with a consistent verbatim line + sha (only the hole /
    window / cutoff rules can then refuse it)."""
    def fn(a):
        r = row_of(a, 'AAAUSDT', min(FILLED_BARS))
        r['open_ms'], r['line'] = t, LD.kline(k, t, step)
        r['sha256'] = hashlib.sha256(r['line'].encode()).hexdigest()
    return fn


def true_bar(sym, t, step=TF):
    c = LD.kline(SYMS.index(sym), t, step).split(',')
    return (t, float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5]), float(c[7]), t + step)


def incompletes(final):
    _, sm, _ = docs(final)
    return sm['status'], sorted({i['reason'] for i in sm.get('incomplete', [])})


# ------------------------------------------------------------------ no add-on: unchanged
def test_no_addon_is_byte_identical_to_the_loader_before_this_slice(fx):
    assert golden(fx) == GOLDEN_NO_ADDON
    for r in ledger_recs(fx):
        assert 'addon' not in r['detail']
    a = load(fx)
    b = load(fx, addon=None, addon_report=None)       # the explicit default is the same path
    assert set(a) == set(b) and 'addon' not in a and 'addon' not in a['manifest']['source']
    assert a['manifest'] == b['manifest']


def test_a_report_without_its_addon_or_vice_versa_refuses_before_access(fx):
    raw = addon_bytes(make_addon(fx))
    pin = hashlib.sha256(raw).hexdigest()
    for kw in ({'addon_report': fx['rep']}, {'addon': raw}, {'addon': raw, 'addon_sha256': pin},
               {'addon': raw, 'addon_sha256': pin, 'addon_report': fx['rep']}, {'addon_zips': fx['zips']}):
        with pytest.raises(PR.PitRunError, match='all four'):
            load(fx, **kw)
    assert not os.path.exists(fx['ledger'])


# ------------------------------------------------------------------ a valid add-on fills exactly the holes
def test_valid_addon_fills_exactly_the_holes_and_the_rows_become_evaluable(fx):
    base = load(fx)
    addon = make_addon(fx)
    assert [h['status'] for h in addon['holes']] == ['filled'] * 3
    e = with_addon(fx, addon)
    filled = {'AAAUSDT': FILLED_BARS | {OPEN_BAR}}
    for s in SYMS:
        x0, x1 = base['tape'][s], e['tape'][s]
        assert set(x1.bars) - set(x0.bars) == filled.get(s, set())          # only the holes are new
        assert {k: v for k, v in x1.bars.items() if k in x0.bars} == x0.bars    # no base row touched
        assert {k: v for k, v in x1.marks.items() if k in x0.marks} == x0.marks
        assert x1.funding == x0.funding and x1.rule == x0.rule
    for t in filled['AAAUSDT']:                       # verbatim archive values, never interpolated
        with pytest.raises(RV.Incomplete, match='incomplete.gap'):
            RV.bar_at(base['tape']['AAAUSDT'], t)
        assert tuple(RV.bar_at(e['tape']['AAAUSDT'], t)) == true_bar('AAAUSDT', t)
    (m,) = DROP_MARKS['BBBUSDT']
    with pytest.raises(RV.Incomplete, match='incomplete.funding_mark'):
        RV.mark_at(base['tape']['BBBUSDT'], m + HOUR)
    assert set(e['tape']['BBBUSDT'].marks) - set(base['tape']['BBBUSDT'].marks) == {m}
    assert RV.mark_at(e['tape']['BBBUSDT'], m + HOUR) == true_bar('BBBUSDT', m, HOUR)[4]
    assert PR.rex_preflight(e['manifest'], e['tape'], e['notice_artifact'], symbols=e['symbols'], window=(LO, HI),
                            artifact_sha=e['notice_sha256']) == []
    # the gap row that made the v1 run INCOMPLETE is now evaluated (what is left is the split-end class only)
    assert incompletes(run(fx, base, 'base')) == ('INCOMPLETE', ['incomplete.gap'])
    st, why = incompletes(run(fx, e, 'addon'))
    assert 'incomplete.gap' not in why and 'incomplete.funding_mark' not in why


def test_partial_coverage_leaves_the_remaining_holes_incomplete(fx):
    addon = make_addon(fx, days=('bars', 'mark'))           # the archive has no 2024-03-12 day file
    st = {h['hole']: h['status'] for h in addon['holes']}
    assert sorted(st.values()) == ['archive_missing', 'filled', 'filled']
    assert [g['start_ms'] for g in addon['genuine_gaps']] == [OPEN_BAR]
    e = with_addon(fx, addon)
    x = e['tape']['AAAUSDT']
    assert FILLED_BARS <= set(x.bars) and OPEN_BAR not in x.bars
    with pytest.raises(RV.Incomplete, match='incomplete.gap'):
        RV.bar_at(x, OPEN_BAR)
    assert e['addon']['filled'] == {'AAAUSDT|klines': 2, 'BBBUSDT|markPriceKlines': 1}
    _, sm, _ = docs(run(fx, e, 'partial'))
    assert sm['status'] == RV.INCOMPLETE and {i['reason'] for i in sm['incomplete']} == {'incomplete.gap'}


# ------------------------------------------------------------------ refusals (fail closed)
def _collision_case(fx):
    """A hole claimed at a timestamp the base DOES hold (re-signed report; the archive serves that day): the add-on
    passes validation but the merge finds the base row and refuses (never overwritten)."""
    t = U('2024-02-21T00:00:00Z')
    h0 = next(h for h in fx['rep']['holes'] if h['series'] == 'klines' and h['start_ms'] == min(FILLED_BARS))
    extra = dict(h0, start_ms=t, end_ms=t + TF, missing=1, start_utc=S.utc(t), end_utc=S.utc(t + TF))
    rep = GI.sign(dict(fx['rep'], holes=sorted(fx['rep']['holes'] + [extra],
                                               key=lambda h: (h['symbol'], h['series'], h['start_ms']))))
    return make_addon(fx, rep, extra=[day_zip('klines', 'AAAUSDT', '4h', '2024-02-21')]), rep


def _urls(old, new):
    def fn(a):
        for x in a['files'] + a['rows']:
            for k in ('url', 'source_url'):
                if k in x:
                    x[k] = x[k].replace(old, new)
    return fn


def _forge_file_sha(a):
    for f in a['files']:
        f['sha256'] = 'ab' * 32                      # an arbitrary well-formed hex digest


def _tamper_line(a):
    r = a['rows'][0]
    r['line'] = r['line'].replace(',', ',9', 1)       # a changed price, the recorded sha256 kept


CASES = {
    'collision_with_a_base_row': (_collision_case, 'collide with base rows'),
    'row_outside_any_hole': (lambda fx: (reseal(make_addon(fx), move_row(U('2024-02-21T00:00:00Z'))),
                                         fx['rep']), 'outside every exact hole'),
    'row_after_END': (lambda fx: (reseal(make_addon(fx), move_row(U('2026-09-28T00:00:00Z'))), fx['rep']),
                      'sealed cutoff 2026-09-28'),
    'row_outside_the_run_window': (lambda fx: (reseal(make_addon(fx), move_row(LO - TF)), fx['rep']),
                                   'outside the run access window'),
    'tampered_row_digest': (lambda fx: (reseal(make_addon(fx), _tamper_line), fx['rep']), 'row sha256'),
    'tampered_addon_digest': (lambda fx: (dict(make_addon(fx), fetched=99), fx['rep']), 'add-on digest'),
    'forged_file_sha256': (lambda fx: (reseal(make_addon(fx), _forge_file_sha), fx['rep']), 'disagree'),
    'wrong_base_manifest_pin': (lambda fx: (reseal(make_addon(fx),
                                                   lambda a: a.update(manifest_digest='0' * 64)), fx['rep']),
                                'another base manifest'),
    'wrong_report_pin': (lambda fx: (reseal(make_addon(fx), lambda a: a.update(inventory_digest='0' * 64)),
                                     fx['rep']), 'another gap report'),
    'tampered_report': (lambda fx: (make_addon(fx), dict(fx['rep'], holes=[
        dict(h, end_ms=h['end_ms'] + TF) for h in fx['rep']['holes']])), 'gap report refused'),
    'unverified_checksum_record': (lambda fx: (reseal(make_addon(fx), lambda a: a['files'][0].update(
        status='rejected_checksum')), fx['rep']), 'no verified archive checksum'),
    'non_archive_host': (lambda fx: (reseal(make_addon(fx),
                                            _urls('data.binance.vision', 'data.binance.vision.evil.example')),
                                     fx['rep']), 'is not on https'),
    'http_not_https': (lambda fx: (reseal(make_addon(fx), _urls('https://', 'http://')), fx['rep']),
                       'is not on https'),
}


@pytest.mark.parametrize('case', sorted(CASES))
def test_addon_refusals(fx, case):
    build, want = CASES[case]
    addon, rep = build(fx)
    with pytest.raises(PR.PitRunError, match=want):
        with_addon(fx, addon, rep)
    assert_no_access(fx)


def assert_no_access(fx):
    """Validated in full (collision included) BEFORE the Access opens: no ledger record, no run output."""
    assert not os.path.exists(fx['ledger']) or not ledger_recs(fx)
    assert not os.path.exists(fx['out'])


# ------------------------------------------------------------------ provenance + run identity
def test_run_id_changes_and_provenance_is_recorded(fx):
    d0, s0, _ = docs(run(fx, load(fx), 'base'))
    addon = make_addon(fx)
    e = with_addon(fx, addon)
    d1, s1, x1 = docs(run(fx, e, 'addon'))
    assert d0['run'] != d1['run'] and s0['run'] != s1['run']
    want_files = sorted(BF.url_for('daily', *DAYS[k]) for k in DAYS)
    prov = e['addon']
    assert prov['digest'] == addon['digest'] and prov['gap_report'] == fx['rep'][GI.SIG]
    assert prov['addon_sha256'] == hashlib.sha256(addon_bytes(addon)).hexdigest()
    assert prov['base_manifest'] == fx['man']['digest']
    assert prov['filled'] == {'AAAUSDT|klines': 3, 'BBBUSDT|markPriceKlines': 1}
    assert [f['url'] for f in prov['source_files']] == want_files
    assert all(len(f['sha256']) == 64 for f in prov['source_files'])
    acc = [r for r in ledger_recs(fx) if r['kind'] == 'data_access']
    assert 'addon' not in acc[0]['detail'] and acc[1]['detail']['addon'] == prov
    assert s1['addon'] == prov and x1['access']['detail']['addon'] == prov and 'addon' not in s0
    assert x1['access']['detail']['source']['addon'] == addon['digest']
    trials = [r for r in ledger_recs(fx) if r['kind'] != 'data_access']
    assert {r['detail']['run'] for r in trials} == {d0['run'], d1['run']}


def test_cli_rex_with_addon_end_to_end(fx, monkeypatch, capsys):
    monkeypatch.setattr(PR, 'rex_sources', lambda repo: fx['src'])
    addon = make_addon(fx)
    pa, pr = str(fx['tmp'] / 'addon.json'), str(fx['tmp'] / 'report.json')
    for p, d in ((pa, addon), (pr, fx['rep'])):
        with open(p, 'w', newline='\n') as f:
            json.dump(d, f)
    base = ['rex', '--window', WINDOW, '--tier', 'CORE-10', '--out-dir', fx['out'], '--store', fx['store'],
            '--notice', fx['notice'], '--notice-sha256', fx['art']['digest'], '--author', 't',
            '--cairo-date', '2026-10-15']
    pin = hashlib.sha256(open(pa, 'rb').read()).hexdigest()
    full = ['--addon', pa, '--addon-sha256', pin, '--addon-report', pr, '--addon-zips', fx['zips']]
    for part in (full[:2], full[:4], full[:6], full[2:]):              # all four go together
        assert G.main(base + part) == 3
    assert G.main(['rex', '--window', WINDOW, '--tier', 'CORE-10', '--out-dir', fx['out']] + full) == 2   # + a store
    assert G.main(base + ['--addon', pa, '--addon-sha256', 'e' * 64] + full[4:]) == 2                    # wrong pin
    assert not os.path.exists(fx['ledger']) or not ledger_recs(fx)
    capsys.readouterr()
    rc = G.main(base + full)
    out = capsys.readouterr()
    assert rc in (0, 4), out.err
    _, sm, _ = docs(out.out.strip().splitlines()[-1])
    assert sm['addon']['digest'] == addon['digest'] and sm['addon']['addon_sha256'] == pin


# ------------------------------------------------------------------ Cowork D1 (6185519839): values, pin, re-proof
def _forge(pick, **cols):
    """Forge value columns of one add-on row (row sha + add-on digest re-sealed by the forger)."""
    idx = {'open': 1, 'high': 2, 'low': 3, 'close': 4, 'volume': 5, 'qv': 7}

    def fn(a):
        r = pick(a)
        c = r['line'].split(',')
        for k, v in cols.items():
            c[idx[k]] = v
        r['line'] = ','.join(c)
        r['sha256'] = hashlib.sha256(r['line'].encode()).hexdigest()
    return fn


def _bar_row(a):
    return row_of(a, 'AAAUSDT', min(FILLED_BARS))


def _mark_row(a):
    return next(x for x in a['rows'] if x['series'] == 'markPriceKlines')


PROBES = {
    'all_999_ohlc': (dict(open='999', high='999', low='999', close='999'), _bar_row, 'byte for byte'),
    'nan': (dict(close='nan'), _bar_row, 'not finite'),
    'inf': (dict(high='inf'), _bar_row, 'not finite'),
    'negative_open': (dict(open='-1'), _bar_row, 'OHLC must be > 0'),
    'zero_close': (dict(close='0'), _bar_row, 'OHLC must be > 0'),
    'high_below_low': (dict(high='1', low='2', open='1.5', close='1.5'), _bar_row, 'inconsistent OHLC'),
    'abc': (dict(close='abc'), _bar_row, 'not a number'),
    'negative_volume': (dict(volume='-5'), _bar_row, 'negative volume'),
    'nan_quote_volume': (dict(qv='nan'), _bar_row, 'not finite'),
    'mark_nan': (dict(close='nan'), _mark_row, 'not finite'),
    'mark_high_below_close': (dict(high='1'), _mark_row, 'inconsistent OHLC'),
    'valid_looking_forgery': (None, _bar_row, 'byte for byte'),
}


@pytest.mark.parametrize('probe', sorted(PROBES))
def test_cowork_value_probes_refuse_before_the_access(fx, probe):
    cols, pick, want = PROBES[probe]
    addon = make_addon(fx)
    if cols is None:                                 # a plausible close inside [low, high]: only the re-proof sees it
        c = _bar_row(addon)['line'].split(',')
        cols = dict(close=repr(round((float(c[2]) + float(c[3])) / 2 + 0.00005, 5)))
    with pytest.raises(PR.PitRunError, match=want):
        with_addon(fx, reseal(addon, _forge(pick, **cols)))     # the forger also re-pins: internal checks must hold
    assert_no_access(fx)


def test_a_forged_resealed_addon_fails_the_external_pin(fx):
    addon = make_addon(fx)
    pin = hashlib.sha256(addon_bytes(addon)).hexdigest()
    forged = reseal(addon, _forge(_bar_row, open='999', high='999', low='999', close='999'))
    with pytest.raises(PR.PitRunError, match='not the pinned --addon-sha256'):
        with_addon(fx, forged, pin=pin)
    with pytest.raises(PR.PitRunError, match='pinned sha256'):
        with_addon(fx, addon, pin='not-hex')
    assert_no_access(fx)
    e = with_addon(fx, addon, pin=pin)               # the genuine file under its pin loads
    assert e['addon']['addon_sha256'] == pin


def _zip_of(fx, addon):
    return BF.zip_path(fx['zips'], sorted({r['source_url'] for r in addon['rows']})[0])


def _rewrite_zip(p, with_checksum):
    name = os.path.basename(p)[:-4]
    sym, iv, day = name.split('-', 2)
    step = TF if iv == '4h' else HOUR
    a = U(day + 'T00:00:00Z')
    raw = zbytes(name + '.csv', [LD.kline(SYMS.index(sym), t, step).replace(',', ',1', 1)
                                 for t in range(a, a + DAY, step)])
    with open(p, 'wb') as f:
        f.write(raw)
    if with_checksum:                                # the kept checksum agrees with the zip, not with the record
        with open(p + '.CHECKSUM', 'w') as f:
            f.write(f'{hashlib.sha256(raw).hexdigest()}  {name}.zip')


@pytest.mark.parametrize('how, want', [
    ('missing_zip', 'missing from the kept zips'),
    ('missing_checksum', 'missing from the kept zips'),
    ('zip_bytes_changed', 'disagree'),
    ('zip_and_checksum_rewritten', 'disagree'),
    ('no_zips_dir', 'zips directory'),
])
def test_archive_reproof_refuses_a_missing_or_mismatched_zip(fx, how, want):
    addon = make_addon(fx)
    p = _zip_of(fx, addon)
    zips = fx['zips']
    if how == 'missing_zip':
        os.remove(p)
    elif how == 'missing_checksum':
        os.remove(p + '.CHECKSUM')
    elif how == 'no_zips_dir':
        zips = str(fx['tmp'] / 'nowhere')
    else:
        _rewrite_zip(p, how == 'zip_and_checksum_rewritten')
    raw = addon_bytes(addon)
    with pytest.raises(PR.PitRunError, match=want):
        load(fx, addon=raw, addon_sha256=hashlib.sha256(raw).hexdigest(), addon_report=fx['rep'], addon_zips=zips)
    assert_no_access(fx)


def test_malformed_addon_bytes_refuse_before_the_access(fx):
    for raw in (b'{not json', b'[1, 2]', b'{"format": "zb-rex-gap-fill/1", "x": NaN}'):
        with pytest.raises(PR.PitRunError, match='add-on refused'):
            load(fx, addon=raw, addon_sha256=hashlib.sha256(raw).hexdigest(), addon_report=fx['rep'],
                 addon_zips=fx['zips'])
    assert_no_access(fx)


def test_fill_cli_fetch_requires_a_zips_dir(fx, monkeypatch):
    monkeypatch.setattr(BF, 'http_fetcher', lambda url: pytest.fail('network touched'))
    p = fx['tmp'] / 'report.json'
    p.write_text(json.dumps(fx['rep']))
    with pytest.raises(SystemExit):
        BF.main(['--report', str(p), '--out', str(fx['tmp'] / 'a.json'), '--fetch'])


def test_a_cited_file_whose_period_runs_past_the_sealed_end_is_refused(fx, monkeypatch):
    """Codex 6304510176 #1: even when every selected row is pre-cutoff, a cited archive file whose period ends after
    SEALED_END is refused before the Access (the #88 planner rule, re-applied by the loader)."""
    addon = make_addon(fx)
    monkeypatch.setattr(BF, 'check_report', lambda rep: rep)       # the report itself is pre-cutoff; isolate the file rule
    monkeypatch.setattr(BF, 'SEALED_END', '2024-03-12T20:00:00Z')  # mid-file: OPEN_BAR (16:00-20:00) still fits
    assert OPEN_BAR + TF <= U(BF.SEALED_END)
    with pytest.raises(PR.PitRunError, match='past the sealed end'):
        with_addon(fx, addon)
    assert_no_access(fx)
    monkeypatch.setattr(BF, 'SEALED_END', '2024-03-13T00:00:00Z')  # the file period ends exactly at the cutoff: accepted
    with_addon(fx, addon)


@pytest.mark.parametrize('empty', ['rows_and_files', 'rows_only'])
def test_an_addon_that_fills_nothing_is_refused(fx, empty):
    """Codex 6304510176 #2: a resealed no-op add-on must not change the run identity as if it had filled data."""
    def fn(a):
        a['rows'] = []
        if empty == 'rows_and_files':
            a['files'] = []
    with pytest.raises(PR.PitRunError, match='fills nothing'):
        with_addon(fx, reseal(make_addon(fx), fn))
    assert_no_access(fx)
