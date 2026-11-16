import unittest
from tools.research.lane2_funding_probe import QUERY, classify, funding_rows


class FundingProbeTests(unittest.TestCase):
    def row(self, mark=''):
        return dict(symbol='1000LUNCUSDT', fundingTime=QUERY['startTime'] + 15,
                    fundingRate='0.0001', markPrice=mark)

    def candle(self):
        m = QUERY['startTime']
        return [[m, '0.15', '0.16', '0.14', '0.155', '0', m + 59999]]

    def test_empty_mark_stays_missing_even_with_candle(self):
        out = classify(self.row(), self.candle())
        self.assertIsNone(out['settlement_mark'])
        self.assertEqual(out['status'], 'INCOMPLETE_SETTLEMENT_MARK')

    def test_native_mark_preserved(self):
        self.assertEqual(classify(self.row('0.151'), self.candle())['settlement_mark'], '0.151')

    def test_absent_mark_stays_missing(self):
        row = self.row(); del row['markPrice']
        self.assertIsNone(classify(row, self.candle())['settlement_mark'])

    def test_missing_candle_rejected(self):
        with self.assertRaises(ValueError): classify(self.row(), [])

    def test_wrong_minute_rejected(self):
        candle = self.candle(); candle[0][0] += 60000
        with self.assertRaises(ValueError): classify(self.row(), candle)

    def test_invalid_ohlc_rejected(self):
        candle = self.candle(); candle[0][2] = '0.10'
        with self.assertRaises(ValueError): classify(self.row(), candle)

    def test_duplicate_event_rejected(self):
        with self.assertRaises(ValueError): funding_rows([self.row(), self.row()])

    def test_nonfinite_rate_rejected(self):
        row = self.row(); row['fundingRate'] = 'NaN'
        with self.assertRaises(ValueError): funding_rows([row])

    def test_nonpositive_mark_rejected(self):
        with self.assertRaises(ValueError): funding_rows([self.row('0')])

    def test_empty_history_rejected(self):
        with self.assertRaises(ValueError): funding_rows([])

    def test_wrong_symbol_rejected(self):
        row = self.row(); row['symbol'] = 'BTCUSDT'
        with self.assertRaises(ValueError): funding_rows([row])


if __name__ == '__main__': unittest.main()
