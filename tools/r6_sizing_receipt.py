"""R6-PAPER-01 sizing receipt (read-only; places nothing, no network, no keys).

Runs the REAL open_lot of a given engine source tree (e.g. account 4's deployed source) on a COPY of a real
settings.json, with a fake exchange, and prints what slot EX14 would be sized at: risk USD, quantity, notional and the
leverage (notional) cap. Proves the $8 / $3,333.33 basis from the actual settings instead of inferring it.

  python tools/r6_sizing_receipt.py --source <dir with engine.py> --settings <settings.json> [--balance 5000]
"""
import argparse, json, os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True); ap.add_argument('--settings', required=True)
    ap.add_argument('--balance', type=float, default=5000.0); ap.add_argument('--symbol', default='AAVEUSDT')
    a = ap.parse_args(argv)
    sys.path[:0] = [os.path.abspath(a.source), os.path.join(HERE, 'tests'), HERE]
    import engine as E, test_safety as TS, r6_regime_flip as R, r6_paper_overlay as OV
    tmp = tempfile.mkdtemp(prefix='r6_receipt_')
    try:
        doc = json.load(open(a.settings, encoding='utf-8'))
        for k in [k for k in doc if 'KEY' in k.upper() or 'SECRET' in k.upper() or 'TOKEN' in k.upper()]: doc.pop(k)
        json.dump(doc, open(os.path.join(tmp, 'settings.json'), 'w', encoding='utf-8'))
        E.Futures = TS.FakeX
        e = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
        e.data = e.trade; e.trade.balance = a.balance; e.trade.base = 'https://demo-fapi.binance.com'
        for s in R.SYMBOLS: e.trade.mark.setdefault(s, 100.0)
        e.connect()
        out = dict(engine_sha256=__import__('hashlib').sha256(open(os.path.join(a.source, 'engine.py'), 'rb').read()).hexdigest(),
                   settings=dict(CAPITAL_CAP=e.S.get('CAPITAL_CAP'), MAX_LEVERAGE=e.S.get('MAX_LEVERAGE'), COMPOUND=e.S.get('COMPOUND'),
                                 PAPER_BOOKS=e.S.get('PAPER_BOOKS'), TRUE_BOOKS=e.S.get('TRUE_BOOKS'),
                                 sleeves=[(x['id'], x['key'], x['enabled'], round(x['share'], 6), x['risk'], x['max_pos']) for x in e.S['SLEEVES']]))
        try:
            OV.install(e, pace=False)
        except OV.R6InstallError as ex:
            out['install'] = f'REFUSED: {ex}'; print(json.dumps(out, indent=1)); return 2
        sl = OV.sleeve(True); e.S['SLEEVES'] = [x for x in e.S['SLEEVES'] if x['id'] != sl['id']] + [sl]
        eq = e.equity(); out['sizing_equity'] = eq; out['cases'] = []
        for atr in (2.0, 0.01):
            e.state['lots'].clear()
            ok = e.open_lot(sl, a.symbol, 'LONG', dict(close=100.0, atr=atr, time='receipt'), None, eq)
            lot = next(iter(e.state['lots'].values()), None)
            out['cases'].append(dict(px=100.0, atr=atr, opened=bool(ok), skip=e.last_skip if not ok else '',
                                     risk_usd=lot and lot['risk_usd'], qty=lot and lot['qty'],
                                     notional=lot and round(lot['qty'] * lot['avg'], 2), book=lot and lot.get('book')))
        print(json.dumps(out, indent=1)); return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
