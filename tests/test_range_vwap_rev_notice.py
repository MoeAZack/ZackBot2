"""REX-01 synthetic fixtures F16: the `zb-delist-notice/1` register (v5 section 5 + P1 / P2 / P4). Announcement
documents are generated here; nothing is fetched."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rex_synth as S                                                                       # noqa: E402
from rex_synth import ms, rex                                                               # noqa: E402

RV = S.RV
import range_vwap_rev_notice as NT                                                          # noqa: E402

SYM = 'AAAUSDT'
FROZEN = (SYM, 'BBBUSDT', 'BTCUSDT')
LE = S.long_entry(5)
PERP = 'Binance Futures Will Delist USDⓈ-M AAAUSDT Perpetual Contract'


def item(code, title=PERP, rd=None, url='https://www.binance.com/en/support/announcement/x'):
    return {'code': code, 'title': title, 'releaseDate': rd, 'url': url}


def fetch(ts, items, ok=True, schema=NT.SCHEMA):
    return {'fetch_ts': ts, 'ok': ok, 'schema': schema, 'raw_digest': f'{ts:064x}'[-64:], 'items': items}


H2 = item('h2', 'Binance Futures Will Launch USDⓈ-M ZZZUSDT Perpetual', ms(-100))


def base(ts=0):
    """R_hist with two unrelated announcements (the overlap anchors)."""
    reg = NT.Register(FROZEN)
    reg.ingest(fetch(ts, [H2, item('h1', 'Notice on Something Else', ms(-200))]))
    return reg


def run(nv, n=14, holes=()):
    tape = {SYM: S.build(n=n, vwap=LE, holes=holes)}
    return S.run(tape, 'long', n, notices=nv)


F16 = ['backfilled_notice', 'release_after_decision_close_ignored', 'inclusion_rule', 'match_without_releasedate',
       'append_only_edit_delete_post_binding', 'r_ext_bound_before_runner_and_reveal', 'outage_with_overlap',
       'outage_without_overlap', 'nfkc_circled_s', 'edited_releasedate_ignored', 'non_delisting_notice_excluded']


@rex('F16', F16)
def test_f16_notice_register(case):
    if case == 'backfilled_notice':                  # published at t, first fetched long after: known from t only
        reg = base()
        reg.bind_registration(ms(-1))
        t = ms(9) + S.HOUR
        reg.ingest(fetch(ms(20), [item('n1', rd=t), H2]))
        plain, noted = run(RV.NO_NOTICES), run(reg.view())
        assert [s for s in noted['series'] if s[0] <= ms(10)] == [s for s in plain['series'] if s[0] <= ms(10)]
        (tr,) = noted['trades']
        assert (tr['reason'], tr['exit_ms']) == ('exit.delist', ms(10))
        assert reg.report()['lag_gt_24h'] == 1 and reg.report()['post_binding'] == 1
    elif case == 'release_after_decision_close_ignored':
        r = run(RV.NoticeView({SYM: ms(6) + 1}))   # after the decision close ms(6): that decision still enters
        assert r['signals'][0][:2] == (SYM, ms(6))
        assert (r['trades'][0]['reason'], r['trades'][0]['exit_ms']) == ('exit.delist', ms(7))
        r = run(RV.NoticeView({SYM: ms(6)}))       # known AT the decision close: no entry
        assert r['signals'] == [] and r['trades'] == []
    elif case == 'inclusion_rule':
        want = {PERP: 'include',
                'Binance Futures Will Delist USDⓈ-M AAAUSDT Perpetual and Relaunch as BBBUSDT (Rename)': 'include',
                'Binance Futures Will Delist AAAUSDT Quarterly Delivery Contract': 'excluded_match',
                'Binance Will Delist AAAUSDT on Spot': 'excluded_match',
                'Binance Margin Will Delist AAAUSDT Isolated Margin Pair': 'excluded_match',
                'Binance Futures Will Delist COIN-M AAAUSD Perpetual': 'no_match'}
        assert {t: NT.classify(t, FROZEN)[0] for t in want} == want
        assert NT.classify(list(want)[1], FROZEN)[1] == [SYM, 'BBBUSDT']                  # the old symbol counts
    elif case == 'match_without_releasedate':
        reg = base()
        with pytest.raises(NT.NoticeError, match='releaseDate'):
            reg.ingest(fetch(ms(1), [item('n1', rd=None), H2]))
        reg = base()
        reg.ingest(fetch(ms(1), [item('x1', 'Binance Will Delist AAAUSDT on Spot', None), H2]))
        assert reg.report()['excluded_matches'] == 1
    elif case == 'append_only_edit_delete_post_binding':
        reg = base()
        reg.ingest(fetch(ms(1), [item('n1', rd=ms(0)), H2]))
        reg.bind_registration(ms(2))
        reg.ingest(fetch(ms(3), [item('n1', PERP + ' (Updated)', ms(0)), H2]))
        reg.ingest(fetch(ms(4), [item('n2', PERP.replace('AAAUSDT', 'BBBUSDT'), ms(3)), H2]))
        rep = reg.report()
        assert [e['code'] for e in reg.entries] == ['n1', 'n2'] and reg.entries[0]['releaseDate'] == ms(0)
        assert (rep['edits'], rep['deletions'], rep['post_binding']) == (1, 1, 1)
    elif case == 'r_ext_bound_before_runner_and_reveal':
        reg = base()
        h = reg.digest()
        reg.bind_registration(ms(0))
        b = {'r_hist_digest': h, 'r_ext_digest': reg.digest(), 'parser_sha': NT.parser_sha(), 'rule_id': NT.RULE_ID}
        for stage in ('runner', 'reveal'):
            with pytest.raises(NT.NoticeError, match='final'):
                NT.require_binding(b, reg, stage=stage)                      # no final fetch yet
        b['r_ext_digest'] = reg.finalize(ms(40))
        for stage in ('runner', 'reveal'):
            NT.require_binding(b, reg, stage=stage)
            with pytest.raises(NT.NoticeError):
                NT.require_binding(dict(b, r_ext_digest='0' * 64), reg, stage=stage)
            with pytest.raises(NT.NoticeError):
                NT.require_binding(dict(b, r_hist_digest=None), reg, stage=stage)
        with pytest.raises(NT.NoticeError):
            reg.ingest(fetch(ms(41), []))                                    # append-only, closed after the final
    elif case == 'outage_with_overlap':
        a, b = base(), base()
        b.ingest(fetch(ms(3), [], ok=False))
        b.ingest(fetch(ms(4), [], schema='changed/v2'))
        for reg in (a, b):
            reg.ingest(fetch(ms(5), [item('h3', 'Other', ms(4)), H2]))
            reg.finalize(ms(14))
        assert b.gaps == [] and a.digest() == b.digest() and run(a.view()) == run(b.view())
    elif case == 'outage_without_overlap':
        reg = base(ms(2))
        reg.ingest(fetch(ms(3), [], ok=False))
        reg.ingest(fetch(ms(8), [item('h9', 'Other', ms(7))]))              # nothing already recorded: a GAP
        reg.finalize(ms(14))
        assert reg.gaps == [(ms(2), ms(8))]
        r = run(reg.view())
        assert r['status'] == RV.INCOMPLETE
        assert (r['censor']['reason'], r['censor']['first_unaccountable_ms']) == ('incomplete.notice_gap', ms(2))
        # Codex 6169708729 item 4, boundary 1: a gap affecting the decision at close ms(7) halts BEFORE those
        # decisions; the entry filled at the open ms(6) (ordered by the fully accountable close ms(6)) and the bar
        # [ms(6), ms(7)) stay - never rolled back.
        r = run(RV.NoticeView({}, ((ms(6) + S.HOUR, ms(9)),)))
        assert (r['censor']['reason'], r['censor']['cycle_ms'], r['censor']['last_valid_ms']) ==                ('incomplete.notice_gap', ms(7), ms(7))
        assert r['series'][-1][0] == ms(7) and r['signals'] == [(SYM, ms(6), -1.5)]
        (lot,) = [x for x in r['incomplete'] if x['symbol'] == SYM]
        assert (lot['entry_ms'], lot['reason']) == (ms(6), 'incomplete.book_halt')
        assert [x['symbol'] for x in r['incomplete'] if x['secondary'] is False] == ['*']
        stopped = S.run({SYM: S.build(n=14, vwap=LE, ohlc={6: (100, 101, 97.0, 99)})}, 'long', 14,
                        notices=RV.NoticeView({}, ((ms(6) + S.HOUR, ms(9)),)))
        assert [t['exit_ms'] for t in stopped['trades']] == [ms(6)]           # the intrabar stop fill stays too
        # boundary 2: a gap already affecting the prior decision (start <= ms(6)) is checked before that
        # decision's open fills: no entry at the open ms(6).
        r = run(RV.NoticeView({}, ((ms(6), ms(9)),)))
        assert r['censor']['first_unaccountable_ms'] == ms(6) and r['censor']['cycle_ms'] == ms(6)
        assert r['incomplete'][0]['entry_ms'] is None and r['trades'] == [] and r['signals'] == []
        tail = base(ms(2))
        tail.ingest(fetch(ms(3), [], ok=False))
        tail.finalize(ms(14))                                                 # never recovered: GAP to the end
        assert tail.gaps == [(ms(2), ms(14))]
    elif case == 'nfkc_circled_s':
        assert NT.norm('USDⓈ-M') == NT.norm('USDS-M') == 'usds-m'
        assert NT.classify(PERP, FROZEN) == NT.classify(PERP.replace('Ⓢ', 'S'), FROZEN) == ('include', [SYM])
    elif case == 'edited_releasedate_ignored':
        reg = base()
        reg.ingest(fetch(ms(1), [item('n1', rd=ms(9)), H2]))
        reg.ingest(fetch(ms(2), [item('n1', rd=ms(5)), H2]))
        assert reg.view().release(SYM) == ms(9) and reg.report()['edits'] == 1
    else:
        reg = base()
        titles = ['Binance Futures Will Close USDⓈ-M AAAUSDT Perpetual Contract',
                  'Binance Futures Will Settle USDⓈ-M AAAUSDT Perpetual Contract']
        reg.ingest(fetch(ms(1), [item(f'c{i}', t, ms(0)) for i, t in enumerate(titles)] + [H2]))
        assert reg.entries == [] and reg.report()['excluded_matches'] == 2 and reg.view().release(SYM) is None
