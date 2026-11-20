import json

import pytest

from tools.pnl_reconcile import reconcile, main


def row(**extra):
    # Tracked actual-cost row: booked +10 differs from measured -4.
    r = dict(id='private-row', closed='2026-11-20T10:00:00+00:00', pnl=10,
             pnl_gross=12, pnl_fill_adj=-15, fees=2, fees_actual=1,
             funding_actual=0, fill_refs=[[0, 'private-order']])
    r.update(extra)
    return r


SETTINGS = dict(CAPITAL_CAP=4000, CAP_SINCE='2026-11-01T00:00:00+00:00', CAP_ADJ=[])
EQUITY = [[1795176000, 4008]]  # 20 Nov 14:00 Cairo (12:00 UTC)


def test_fill_correction_and_marked_residual_are_separate():
    r = reconcile([row()], EQUITY, SETTINGS)
    assert r['booked_closed_pnl'] == 10
    assert r['complete_closed_net'] == -4
    assert r['closed_capital'] == 3996
    assert r['residual_not_proven_open_pnl'] == 12
    assert r['status'] == 'UNRESOLVED_MARKED_COMPONENTS'
    assert r['equity_sample_cairo'] == '2026-11-20T14:00:00+02:00'


def test_native_funding_not_double_counted_and_replays_deduplicated():
    payment = dict(key='private-funding', amount=3, asset='USDT', time_ms=1795168800000)
    r = reconcile([row(funding_actual=3)], EQUITY, SETTINGS, [payment, payment])
    assert r['complete_closed_net'] == -1
    assert r['funding_evidence_total'] == 3
    assert r['funding_unique_payments'] == 1
    assert r['funding_evidence_added_to_closed_capital'] is False
    assert 'private' not in json.dumps(r)


def test_pending_measurement_never_becomes_booked_or_zero_actual():
    r = reconcile([row(fill_px_partial=['fill_px_pending'])], EQUITY, SETTINGS)
    assert r['complete_closed_net'] is None
    assert r['closed_capital'] is None
    assert r['residual_not_proven_open_pnl'] is None
    assert r['measurement_status'] == {'pending': 1}


def test_since_adjustments_and_time_mismatch():
    settings = dict(SETTINGS, CAP_ADJ=[dict(amount=-20), dict(amount=5)])
    r = reconcile([row(closed='2026-10-31T00:00:00+00:00'),
                   row(closed='2026-11-20T13:00:00+00:00')], EQUITY, settings)
    assert r['closed_rows'] == 1
    assert r['closed_capital'] == 3981
    assert r['closes_after_equity_sample'] == 1
    assert 'history_has_closes_after_equity_sample' in r['reasons']


def test_unknown_checkpoint_and_whole_account_are_not_guessed():
    r = reconcile([], EQUITY, dict(SETTINGS, CAPITAL_CAP=0))
    assert r['closed_capital'] is None
    assert 'whole_account_capital_basis' in r['reasons']


def test_persisted_reason_text_never_leaks_identifiers():
    r = reconcile([row(fill_px_partial=['private-order token=secret'],
                       funding_partial=['account-id-private', 'funding_unread'])], EQUITY, SETTINGS)
    assert r['measurement_reasons'] == {'unknown_measurement_reason': 2, 'funding_unread': 1}
    assert 'private' not in json.dumps(r)
    assert 'secret' not in json.dumps(r)


def test_conflicting_or_mixed_asset_funding_stays_unresolved():
    f = dict(key='private-funding', amount=3, asset='USDT', time_ms=1795168800000)
    r = reconcile([], EQUITY, SETTINGS, [f, dict(f, amount=4), dict(f, key='other', asset='BNB')])
    assert r['funding_conflicts'] == 1
    assert r['funding_evidence_total'] is None
    assert 'conflicting_funding_replays' in r['reasons']
    assert 'funding_assets_not_comparable' in r['reasons']


def test_cli_reads_only_ledger_inputs_and_redacts_errors(tmp_path, capsys):
    for name, value in [('history.json', [row()]), ('equity.json', EQUITY), ('settings.json', SETTINGS)]:
        (tmp_path / name).write_text(json.dumps(value))
    assert main([str(tmp_path)]) == 0
    assert 'private' not in capsys.readouterr().out
    (tmp_path / 'history.json').write_text('[null]')
    assert main([str(tmp_path)]) == 2
    assert 'INPUT_INVALID' in capsys.readouterr().out
    (tmp_path / 'history.json').write_text('private malformed data')
    assert main([str(tmp_path)]) == 2
    assert 'private' not in capsys.readouterr().out


@pytest.mark.parametrize('value', [True, float('nan'), float('inf')])
def test_invalid_equity_amount_rejected(value):
    with pytest.raises(ValueError):
        reconcile([], [[1795176000, value]], SETTINGS)
