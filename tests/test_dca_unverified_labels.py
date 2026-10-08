"""AUD-06a (owner decision 2026-10-07): DCA profile numbers are kept but relabelled UNVERIFIED everywhere they are shown
(profile cards, notes, research tab). They predate the FBL-BT01 backtester fix; nothing about the slots changes."""
import os, re, shutil, subprocess, json
import pytest
import engine as E

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DCA = {k for k, p in E.PRESETS.items() if any(s['key'] == 'dca_dip' for s in p['sleeves'])}


def test_every_dca_profile_is_marked_unverified_and_others_are_not():
    assert DCA >= {'calm', 'balanced', 'aggressive', 'active', 'boost_active', 'steady_mix', 'active_dca', 'boost'}
    for k, p in E.PRESETS.items():
        if k in DCA:
            assert p['bt']['unverified'] == E.UNVERIFIED_BT01 and p['bt']['verified'] is False, k
            assert p['note'].startswith(E.UNVERIFIED_NOTE) and p['note'].count(E.UNVERIFIED_NOTE) == 1, k
        else:
            assert 'unverified' not in p['bt'] and not p['note'].startswith(E.UNVERIFIED_NOTE), k
    assert 'UNVERIFIED' in E.UNVERIFIED_BT01 and 'FBL-BT01' in E.UNVERIFIED_BT01


def test_numbers_and_slots_are_unchanged_only_labels_added():
    a = E.PRESETS['active_dca']
    assert a['bt']['end'] == 4637 and '$500 -> $4,637' in a['note']            # kept (relabel, not hide)
    assert [s['key'] for s in a['sleeves']] == ['dca_dip']                        # no default / slot change here


def test_panel_shows_the_warning_on_cards_and_the_research_tab():
    html = open(os.path.join(ROOT, 'panel.html'), encoding='utf-8').read()
    badge = html[html.index('function btBadge('):html.index('function btFeas(') if 'function btFeas(' in html else html.index('function pfBadge(') if 'function pfBadge(' in html else html.index('function renderPresets(')]
    assert 'note bad' in badge and 'UNVERIFIED' in badge and 'Old, unverified' in badge and 'role="alert"' in badge
    assert 'id="resUnverified"' in html and 'UNVERIFIED DCA results' in html
    rr = html[html.index('async function renderResearch('):html.index("['rtf','rcomb','rsing']")]
    assert rr.count('${unvTag(x)}') == 3, 'every research table (timeframes, combos, single) tags DCA rows'
    assert not re.search(r'const dcaRow=x=>/dca/i', html), 'classification must not regex display text'
    assert re.search(r'const dcaRow=x=>x\.uses_dca!==false', html)


def test_research_api_carries_the_same_label(monkeypatch):
    import app as A
    r = A.research_data() if hasattr(A, 'research_data') else None
    if r is None:                                                   # the label lives in the /api/research builder
        src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
        assert "out['unverified'] = dict(label=UNVERIFIED_BT01" in src
    else:
        assert r['unverified']['label'] == E.UNVERIFIED_BT01


# ---- Codex P2 on PR #21: every DCA-containing research row is marked, classified structurally ----
TAG = '<span class="tag t-warn" title="Old backtester (before FBL-BT01) - not re-validated">unverified</span>'


def _html():
    return open(os.path.join(ROOT, 'panel.html'), encoding='utf-8').read()


def test_long_history_rows_are_marked_from_preset_definitions():
    html = _html()
    card = html[html.index('id="resLong"'):]
    body = card[card.index('<tbody>'):card.index('</tbody>')]
    trs = re.findall(r'<tr([^>]*)><td><b>(.*?)</b>(.*?)</td>', body)
    assert len(trs) == body.count('<tr') == 12
    seen = {}
    for attrs, name, rest in trs:
        m = re.search(r'data-preset="(\w+)"', attrs)
        assert m and m.group(1) in E.PRESETS, f'long-history row {name!r} needs data-preset=<preset key>'
        seen[name] = (m.group(1), TAG in rest)
        assert (TAG in rest) == (m.group(1) in DCA), f'{name}: unverified tag must match whether {m.group(1)} has a dca_dip slot'
    for n in ('Balanced', 'Aggressive', 'Boost', 'Calm', 'Balanced · trend slots bull-only', 'Aggressive · bull-only',
              'Boost · bull-only', 'Active (DCA 1h + Breakout 1h)', 'Active DCA (1h) — new'):
        assert seen[n][1], n
    assert seen['Original'] == ('original', False)                    # the one non-DCA profile stays unmarked


def test_research_api_flags_rows_structurally():
    import app as A
    r = A.research()
    run = r['results_runner.csv']
    assert run and all('uses_dca' in x for k in ('results_single.csv', 'results_combos.csv', 'results_timeframes.csv',
                                                  'results_runner.csv') for x in r[k])
    for prof in ('Balanced', 'Aggressive', 'Boost (short-term, high risk)', 'Calm', 'Active (1h, more trades)'):
        rows = [x for x in run if x['profile'] == prof]
        assert rows and all(x['uses_dca'] is True for x in rows), prof            # incl. 'Standard' / non-DCA-named variants
    assert any(x['variant'].startswith('DCA runner') and x['uses_dca'] for x in run)
    sing = {x['key']: x['uses_dca'] for x in r['results_single.csv']}
    assert sing['dca_dip'] is True and sing['ema_mom'] is False and sing['breakout_pyramid'] is False
    tf = {x['strategy']: x['uses_dca'] for x in r['results_timeframes.csv']}
    assert tf['DCA dip buyer (safety orders)'] is True and tf['EMA cross + Momentum'] is False
    co = {x['combo']: x['uses_dca'] for x in r['results_combos.csv']}
    assert co['MOM40 + DCA40'] is True and co['DCA40'] is True and co['MOM40 + ST8'] is False and co['BRK8'] is False
    # renamed / unknown rows fail safe (flagged), and the flag ignores the display text
    assert A.research_uses_dca('results_runner.csv', {'profile': 'Renamed profile'}) is True
    assert A.research_uses_dca('results_combos.csv', {'combo': 'MOM40 + NEW9'}) is True
    assert A.research_uses_dca('results_single.csv', {'key': 'ema_mom', 'name': 'DCA-ish name'}) is False


def test_research_maps_stay_in_sync_with_definitions():
    import app as A
    import strategies as S
    src = open(os.path.join(ROOT, 'research_combos.py'), encoding='utf-8').read()
    slv = dict(re.findall(r"'([\w+]+)':dict\(key='(\w+)'", src[src.index('SLV={'):src.index('rows=[]')]))
    assert slv and A.RESEARCH_COMBO_SLOT_KEYS == slv
    r = A.research()
    for x in r['results_combos.csv']:
        assert all(t in slv for t in x['combo'].split(' + ')), x['combo']
    names = {p['name'] for p in E.PRESETS.values()}
    assert {x['profile'] for x in r['results_runner.csv']} <= names         # every runner profile maps to a preset
    snames = {v['name'] for v in S.STRATEGIES.values()}
    assert {x['strategy'] for x in r['results_timeframes.csv']} <= snames


def test_panel_tags_runner_rows_and_profiles():
    html = _html()
    rr = html[html.index('function renderRunner('):html.index('RER.rrun=renderRunner')]
    assert '<td><b>${esc(x.variant)}</b>${unvTag(x)}</td>' in rr
    assert '${unvTag(rr.find(x=>x.profile===p))}</button>' in rr


def test_panel_unvtag_semantics_in_node():
    node = shutil.which('node') or next((p for p in (os.path.join(os.environ.get('ProgramFiles', ''), 'nodejs', 'node.exe'),) if os.path.exists(p)), None)
    if not node:
        import pytest
        pytest.skip('node not available')
    html = _html()
    lines = [l for l in html.splitlines() if l.startswith('const dcaRow=') or l.startswith('const unvTag=')]
    assert len(lines) == 2
    rows = [dict(profile='Balanced', variant='Standard (current rules)', uses_dca=True),
            dict(profile='Boost (short-term, high risk)', variant='Hold winners', uses_dca=True),
            dict(profile='Aggressive', variant='DCA runner: half off', uses_dca=True),
            dict(combo='MOM40 + ST8', uses_dca=False), dict(name='Renamed DCA thing', key='ema_mom', uses_dca=False)]
    js = '\n'.join(lines) + f'\nconsole.log(JSON.stringify({json.dumps(rows)}.map(x=>unvTag(x).includes("unverified"))))'
    out = subprocess.run([node, '-e', js], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [True, True, True, False, False]


@pytest.mark.parametrize('key', ['dca_dip_v2', 'DCA_dip', 'dca_dip ', 'dca', 'dca_dip2', 'renamed'])
def test_single_strategy_rows_with_an_unknown_key_are_flagged(key):
    """Cowork F1 (P3): results_single.csv was fail-OPEN for a renamed/variant key; unknown keys are flagged like the others."""
    import app
    assert app.research_uses_dca('results_single.csv', dict(key=key, name='x')) is True
    assert app.research_uses_dca('results_single.csv', dict(key='ema_mom', name='DCA-ish name')) is False


def test_static_research_notes_quoting_dca_figures_are_tagged():
    """Cowork F2 (P3): the two Research note cards that quote DCA-derived figures carry the unverified tag themselves."""
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'panel.html'), encoding='utf-8').read()
    for head in ("Active's weak spot", 'Mixing uncorrelated slots'):
        i = html.index(f'<b>{head}</b>')
        assert 'unverified</span>' in html[i:i + 250], head


def test_runner_profile_buttons_wrap_at_every_width():
    """Protected gate (tablet 768px): the unverified tags on the runner profile buttons widened the Research tab by 80px.
    The profile group must wrap at every width, not only below 640px."""
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'panel.html'), encoding='utf-8').read()
    m = re.search(r'#runP\{([^}]*)\}', html)
    assert m and 'flex-wrap:wrap' in m.group(1) and 'max-width:100%' in m.group(1)
