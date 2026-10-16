"""BR-01 dev-window wrapper (scratchpad research only, not product code).

Runs the FROZEN tools/research/run_breadth_candidate.run() of the clean checkout unchanged, with three thin shims:
  1. sleeve(): adds mgmt['br01'] = {'variant': V} to the slot (nothing else) when --variant is given;
  2. FeeProbe: records only the pnl-line exit-fee product inside backtest.close (the BR-01 build adds a second
     `q * px * FEE` product there for staged positions, which the frozen probe would double-count). With br01 absent
     there is exactly one product per close call, so the numbers are identical to the frozen probe;
  3. simulate(): strips the BR-01 trade columns (br, legs, br_status, fees) before enrich() (the 'fees' name would clash)
     and keeps them, with cv.attrs['br01'], for a sidecar <out>.br.json.
Without --variant it is the frozen runner (validated bit-for-bit on the packet sha256)."""
import argparse, json, os, sys

REPO = os.environ['ZB_REPO']
for _p in (REPO, os.path.join(REPO, 'tools', 'research')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest as BT                      # noqa: E402
import run_breadth_candidate as RB         # noqa: E402

_src = open(BT.__file__, encoding='utf-8').read().splitlines()
PNL_LINE = [i + 1 for i, s in enumerate(_src) if s.strip() == "pnl = p['side'] * (px - p['avg']) * q - q * px * FEE"]
assert len(PNL_LINE) == 1, PNL_LINE
PNL_LINE = PNL_LINE[0]


class Probe(RB.FeeProbe):
    __array_ufunc__ = None

    def __rmul__(self, other):
        out = float(other) * self.value
        f = sys._getframe(1)
        if f.f_code.co_name == 'close' and f.f_code.co_filename == BT.__file__ and f.f_lineno == PNL_LINE:
            L = f.f_locals
            p, q, px, s = L['p'], float(L['q']), float(L['px']), L['s']
            r = self.pos.setdefault((s, int(p['i'])), dict(gross=0.0, exit_fee=0.0, qty=0.0))
            r['gross'] += p['side'] * (px - p['avg']) * q
            r['exit_fee'] += q * px * self.value
            r['qty'] += q
            r.update(side=int(p['side']), e0=float(p['e0']), avg=float(p['avg']), R=float(p['R']),
                     best=float(p['best']), risk=float(p['risk']))
        return out

    __mul__ = __rmul__


CFG = None
CAP = {}
_sleeve, _simulate = RB.sleeve, RB.simulate


def sleeve(*a, **k):
    s = _sleeve(*a, **k)
    if CFG:
        s['mgmt']['br01'] = dict(CFG)
    return s


def simulate(*a, **k):
    tr, cv, book, pos = _simulate(*a, **k)
    cols = [c for c in ('br', 'legs', 'br_status', 'fees') if c in tr]
    CAP['br'] = tr[['sym', 'i_in', 'i_out'] + cols].to_dict('records') if len(tr) else []
    CAP['log'] = cv.attrs.get('br01')
    CAP['pos'] = {f'{s}|{i}': v for (s, i), v in pos.items()}
    return tr.drop(columns=cols), cv, book, pos


RB.sleeve, RB.simulate, RB.FeeProbe = sleeve, simulate, Probe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--variant', default=None)
    a, rest = ap.parse_known_args()
    global CFG
    CFG = {'variant': a.variant} if a.variant else None
    rc = RB.main(rest)
    out = rest[rest.index('--out') + 1]
    if rc == 0:
        log = CAP.get('log') or {}
        side = dict(variant=a.variant, br01_cfg=CFG, pnl_line=PNL_LINE, trades=CAP.get('br'),
                    counts=log.get('counts', {}), n_events=len(log.get('events', [])),
                    deadline_events=[e for e in log.get('events', []) if e.get('kind') == 'deadline'],
                    add_events=[e for e in log.get('events', []) if e.get('kind') == 'add'],
                    probe=CAP.get('pos'))
        with open(out + '.br.json', 'w') as f:
            json.dump(side, f, default=str)
    return rc


if __name__ == '__main__':
    sys.exit(main())
