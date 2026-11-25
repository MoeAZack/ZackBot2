import json

import pytest

from tools.pnl_reconcile import epoch, main, reconcile


def row(**extra):
    # Tracked actual-cost row: booked +10 differs from measured -4.
    r = dict(id='private-row', closed='2026-11-20T10:00:00+00:00', pnl=10,
             pnl_gross=12, pnl_fill_adj=-15, fees=2, fees_actual=1,
             funding_actual=0, fill_refs=[[0, 'private-order']])
    r.update(extra)
    return r


SETTINGS = dict(CAPITAL_CAP=4000, CAP_SINCE='2026-11-01T00:00:00+00:00', CAP_ADJ=[])
EQUITY = [[1795176000, 4008]]  # 20 Nov 14:00 Cairo (12:00 UTC)


def snapshot(**overrides):
    return dict({'sample_timestamp': '2026-11-20T14:00:00+02:00',
                 'open_price_pnl': 12, 'exchange_margin_balance': 4100}, **overrides)


@pytest.mark.parametrize('count,booked,gross,fill,fees,funding,net,seconds,marked,open_price,residual', [
    (6, 63.3330, 65.1263, -1.80287291, 1.43509724, 0.24954216,
     62.1378, '55', 4072.83, 10.6922, 0),
    (19, -8.7186, 16.0422, -45.85078687, 19.81011865, -0.68859210,
     -50.3074, '53', 3958.34, 7.6474, 1),
], ids=['A3-exact', 'A4-one-dollar'])
def test_nov20_aggregate_tie_out(count, booked, gross, fill, fees, funding, net, seconds,
                                 marked, open_price, residual):
    # Use synthetic rows to preserve the displayed gross and the row-rounded net.
    rows = [row(pnl=booked, pnl_gross=gross - 0.00012, pnl_fill_adj=fill,
                fees_actual=fees, funding_actual=funding)]
    rows += [row(pnl=0, pnl_gross=0.00004, pnl_fill_adj=0, fees_actual=0) for _ in range(3)]
    rows += [row(pnl=0, pnl_gross=0, pnl_fill_adj=0, fees_actual=0) for _ in range(count - 4)]
    stamp = f'2026-11-20T15:17:{seconds}+02:00'
    r = reconcile(rows, [[epoch(stamp), marked]], SETTINGS,
                  open_positions=snapshot(sample_timestamp=stamp, open_price_pnl=open_price))
    assert r['closed_rows'] == count
    assert r['booked_closed_pnl'] == booked
    assert round(r['booked_gross'], 4) == gross
    assert r['complete_closed_net'] == net
    assert r['computed_equity'] == marked - residual
    assert r['residual'] == residual
    assert r['cap_effect'] == 0
    assert r['status'] == 'TIED_OUT'


def test_cap_binding_and_open_cash_components_are_reported_separately():
    r = reconcile([row()], EQUITY, SETTINGS, open_positions=snapshot(
        open_price_pnl=24, exchange_margin_balance=4008, open_realized_partials=50,
        open_fees=3, open_funding=-7))
    assert r['status'] == 'TIED_OUT'
    assert r['computed_equity'] == 4008
    assert r['cap_effect'] == -12
    assert (r['open_realized_partials'], r['open_fees'], r['open_funding']) == (50, 3, -7)


def test_zero_floor_and_cent_rounding():
    r = reconcile([], [[EQUITY[0][0], 0]], SETTINGS, open_positions=snapshot(exchange_margin_balance=-3))
    assert (r['cap_effect'], r['floor_effect'], r['computed_equity'], r['tolerance']) == (-4015, 3, 0, 1.5)
    assert r['status'] == 'TIED_OUT'
    r = reconcile([row()], EQUITY, SETTINGS, open_positions=snapshot(open_price_pnl=12.0049))
    assert r['computed_equity'] == 4008


def test_gap_with_binding_cap_points_to_exchange_margin_balance():
    r = reconcile([row()], EQUITY, SETTINGS, open_positions=snapshot(exchange_margin_balance=4005))
    assert r['status'] == 'GAP'
    assert r['residual'] == 3
    assert r['tolerance'] == 2.004
    assert r['likely_component'] == 'exchange_margin_balance_or_sample_alignment'


@pytest.mark.parametrize('seconds,status', [('02:00', 'TIED_OUT'), ('02:01', 'UNRESOLVED_MARKED_COMPONENTS')])
def test_open_snapshot_window(seconds, status):
    r = reconcile([row()], EQUITY, SETTINGS,
                  open_positions=snapshot(sample_timestamp=f'2026-11-20T14:{seconds}+02:00'))
    assert r['status'] == status
    if status != 'TIED_OUT':
        assert 'open_positions_sample_outside_window' in r['reasons']
        assert r['computed_equity'] is None


def test_newer_close_blocks_tie_out_even_with_open_snapshot():
    r = reconcile([row(closed='2026-11-20T13:00:00+00:00')], EQUITY, SETTINGS, open_positions=snapshot())
    assert r['status'] == 'UNRESOLVED_MARKED_COMPONENTS'
    assert 'history_has_closes_after_equity_sample' in r['reasons']


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
    r = reconcile([row(funding_actual=3)], EQUITY, SETTINGS, [payment, payment],
                  open_positions=snapshot(open_price_pnl=9, open_funding=2))
    assert r['status'] == 'TIED_OUT'
    assert r['computed_equity'] == 4008


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


@pytest.mark.parametrize('bad_input', ['null', '[]', '{broken', json.dumps(snapshot(open_price_pnl=True)),
                                       json.dumps(snapshot(open_price_pnl='private')), json.dumps(snapshot(open_price_pnl=float('inf'))),
                                       json.dumps(snapshot(sample_timestamp='2026-11-20T14:00:00')),
                                       json.dumps(snapshot(private='private-secret'))])
def test_cli_open_positions_table_json_and_sanitized_errors(tmp_path, capsys, bad_input):
    for name, value in [('history.json', [row()]), ('equity.json', EQUITY), ('settings.json', SETTINGS)]:
        (tmp_path / name).write_text(json.dumps(value))
    open_file = tmp_path / 'open.json'
    open_file.write_text(json.dumps(snapshot()))
    args = [str(tmp_path), '--open-positions', str(open_file)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'TIED_OUT'
    assert main(args + ['--format', 'table']) == 0
    table = capsys.readouterr().out
    assert '| Open price P&L | 12.0000 |' in table
    assert '| Status | TIED_OUT |' in table
    open_file.write_text(bad_input)
    assert main(args) == 2
    output = capsys.readouterr().out
    assert json.loads(output)['status'] == 'INPUT_INVALID'
    assert 'private' not in output


@pytest.mark.parametrize('value', [True, float('nan'), float('inf')])
def test_invalid_equity_amount_rejected(value):
    with pytest.raises(ValueError):
        reconcile([], [[1795176000, value]], SETTINGS)
