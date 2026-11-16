import unittest
from tools.research.lane2_funding_recost import recost_trade, quantities, ORIGINAL_LUNC, expand_job, rate_coverage_gaps
from tools.research.lane2_funding_history import validate_page


class ReCostTests(unittest.TestCase):
    def trade(self, side=1, sym='BTCUSDT'):
        return dict(trade_id='test', sym=sym, side=side, risk=10, baseline_pnl=20,
                    baseline_R=2, proxy_cash_paid=3,
                    fills=[dict(kind='entry', time_ms=0, qty=2),
                           dict(kind='exit', time_ms=20000, qty=2, why='time')])

    def event(self, stamp=10000, rate='0.01', mark='100'):
        return dict(fundingTime=stamp, fundingRate=rate, markPrice=mark)

    def test_long_pays_and_old_proxy_removed(self):
        r = recost_trade(self.trade(), [self.event()], 10000, {})
        self.assertEqual(r['funding_cash'], -2)
        self.assertEqual(r['delta_R'], .1)
        self.assertEqual(r['recosted_R'], 2.1)

    def test_short_receives(self):
        r = recost_trade(self.trade(-1), [self.event()], 10000, {})
        self.assertEqual(r['funding_cash'], 2)
        self.assertEqual(r['delta_R'], .5)

    def test_negative_rate_reverses_cash(self):
        self.assertEqual(recost_trade(self.trade(), [self.event(rate='-.01')], 10000, {})['funding_cash'], 2)

    def test_missing_mark_not_zero(self):
        r = recost_trade(self.trade(), [self.event(mark='')], 10000, {})
        self.assertEqual(r['status'], 'INCOMPLETE')
        self.assertIsNone(r['delta_R'])

    def test_proxy_requires_approval_and_cache(self):
        t = self.trade(sym='OTHERUSDT'); e = self.event(mark='')
        p = {('OTHERUSDT', 10000): dict(value='100', low='90', high='110')}
        self.assertIsNone(recost_trade(t, [e], 10000, p)['delta_R'])
        self.assertTrue(recost_trade(t, [e], 10000, p, True)['labelled_proxy'])

    def test_original_four_lunc_proxies_authorized(self):
        t = self.trade(sym='1000LUNCUSDT'); stamp = min(ORIGINAL_LUNC)
        for f in t['fills']: f['time_ms'] += stamp - 10000
        p = {('1000LUNCUSDT', stamp): dict(value='100', low='90', high='110')}
        r = recost_trade(t, [self.event(stamp, mark='')], 10000, p)
        self.assertTrue(r['labelled_LUNC_proxy'])
        self.assertAlmostEqual(r['proxy_low_delta_R'], .08)
        self.assertAlmostEqual(r['proxy_high_delta_R'], .12)

    def test_partial_add_and_exit_quantities(self):
        t = self.trade()
        t['fills'] = [dict(kind='entry', time_ms=0, qty=2), dict(kind='add', time_ms=10, qty=1),
                      dict(kind='exit', time_ms=20, qty=1), dict(kind='exit', time_ms=30, qty=2)]
        self.assertEqual(quantities(t, [self.event(x) for x in (0, 10, 20, 30, 40)], 10), [2, 3, 2, 0, 0])

    def test_no_charge_after_full_exit(self):
        self.assertEqual(recost_trade(self.trade(), [self.event(30000)], 10000, {})['funding_cash'], 0)

    def test_boundary_equality_declared(self):
        t = self.trade()
        self.assertEqual(quantities(t, [self.event(0)], 10000), [2])
        self.assertEqual(quantities(t, [self.event(0)], 10000, before_equal=True), [0])

    def test_unknown_intrabar_timing_sensitivity(self):
        t = self.trade(); t['fills'][-1].pop('why')
        e = self.event(20001)
        r = recost_trade(t, [e], 10000, {})
        self.assertEqual(r['delta_R'], .3)
        self.assertEqual(r['late_delta_R'], .1)

    def test_duplicate_funding_event_rejected(self):
        r = dict(self.event(), symbol='BTCUSDT')
        with self.assertRaises(ValueError): validate_page([r, r], 'BTCUSDT', 0, 20000)

    def test_wrong_query_range_rejected(self):
        r = dict(self.event(), symbol='BTCUSDT')
        with self.assertRaises(ValueError): validate_page([r], 'BTCUSDT', 11000, 20000)

    def test_cost_expansion_uses_matching_baseline(self):
        t = self.trade(); t['pnl_by_cost'] = {'7': '22', '15': '20', '23': '18'}
        j = dict(job_id='lead', parity={'pass': True}, trades=[t])
        rows = list(expand_job(j))
        self.assertEqual([r['cost_bp'] for r in rows], [7, 15, 23])
        self.assertEqual([r['trades'][0]['baseline_R'] for r in rows], [2.2, 2, 1.8])
        self.assertTrue(all(r['parity']['matches'] for r in rows))

    def test_empty_rate_history_is_a_gap(self):
        self.assertTrue(rate_coverage_gaps({'events': []}, 0, 1000))

    def test_interior_cadence_gap_not_zeroed(self):
        c = dict(events=[self.event(0), self.event(40000000)], cadence_gaps=[(0, 40000000)])
        self.assertTrue(rate_coverage_gaps(c, 10000, 30000000))

    def test_native_mark_always_overrides_proxy(self):
        p = {('BTCUSDT', 10000): dict(value='999', low='900', high='1000')}
        r = recost_trade(self.trade(), [self.event()], 10000, p, True)
        self.assertFalse(r['labelled_proxy'])
        self.assertEqual(r['funding_cash'], -2)

    def test_nonfinite_proxy_rejected(self):
        p = {('BTCUSDT', 10000): dict(value='NaN', low='90', high='110')}
        with self.assertRaises(ValueError): recost_trade(self.trade(), [self.event(mark='')], 10000, p, True)


if __name__ == '__main__': unittest.main()
