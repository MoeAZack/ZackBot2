"""AUD-06a (owner decision 2026-10-07): DCA profile numbers are kept but relabelled UNVERIFIED everywhere they are shown
(profile cards, notes, research tab). They predate the FBL-BT01 backtester fix; nothing about the slots changes."""
import os, re
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
    assert re.search(r'const dcaRow=x=>/dca/i\.test', html)


def test_research_api_carries_the_same_label(monkeypatch):
    import app as A
    r = A.research_data() if hasattr(A, 'research_data') else None
    if r is None:                                                   # the label lives in the /api/research builder
        src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
        assert "out['unverified'] = dict(label=UNVERIFIED_BT01" in src
    else:
        assert r['unverified']['label'] == E.UNVERIFIED_BT01
