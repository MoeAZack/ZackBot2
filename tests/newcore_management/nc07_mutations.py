"""NC-07 mutation evidence. A script, not a pytest module.

For each mutation it exports the committed HEAD (git archive) into a fresh temp folder, weakens ONE management rule
there, runs tests/newcore_management, and requires the suite to FAIL - naming the failing tests. The working tree is
never touched. Usage (one run at a time):

    python tests/newcore_management/nc07_mutations.py            # all mutations
    python tests/newcore_management/nc07_mutations.py NAME ...   # selected ones
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
M = 'newcore/management/'

# name -> [(file, exact source text, replacement), ...]; each anchor must occur exactly once in its file
MUTATIONS = {
    # ------------------------------------------------------------------------------------- the original 13 (fbd618b)
    'break-even on a touch': [(M + 'core.py',
        "    if plan.be_after_tp1 and w['tp1_done']:",
        "    if plan.be_after_tp1 and (w['tp1_done'] or (candle is not None and w['tp1'] is not None and (\n"
        "            candle.high >= w['tp1'].price if side is plan.side.LONG else candle.low <= w['tp1'].price))):")],
    'add cap removed': [
        (M + 'core.py', "            if CTX.add(w['add_filled'], q) > (plan.add_qty or ZERO):",
                        "            if False:"),
        (M + 'core.py', "            if w['add'] is None:\n                w['add_phase'] = AddPhase.CLOSED",
                        "            if w['add'] is None:\n                w['add_phase'] = AddPhase.PENDING")],
    'add risk not reserved in the plan': [(M + 'plan.py',
        "                 [(plan.entry_qty, plan.entry_price)] +\n"
        "                 ([(plan.add_qty, market_fill(plan.add_price, plan.side, plan.costs.slip, opening=True))]\n"
        "                  if plan.add_price is not None else []))",
        "                 [(plan.entry_qty, plan.entry_price)])")],
    'tp1 split rounds up': [(M + 'core.py',
        "                    q1 = plan.rules.quantize_qty(CTX.multiply(plan.tp1_frac, live), Rounding.DOWN)",
        "                    q1 = plan.rules.quantize_qty(CTX.multiply(plan.tp1_frac, live), Rounding.UP)")],
    'add size rounds up': [
        (M + 'plan.py', "            req(self.add_qty == r.quantize_qty(CTX.multiply(self.add_scale, self.entry_qty), Rounding.DOWN),",
                        "            req(self.add_qty == r.quantize_qty(CTX.multiply(self.add_scale, self.entry_qty), Rounding.UP),"),
        (M + 'plan.py', "        q = rules.quantize_qty(CTX.multiply(add_scale, entry_qty), Rounding.DOWN)",
                        "        q = rules.quantize_qty(CTX.multiply(add_scale, entry_qty), Rounding.UP)")],
    'protection after targets': [(M + 'actions.py',
        'TIER = {K.PLACE_STOP: Tier.PROTECT, K.REPLACE_STOP: Tier.PROTECT,',
        'TIER = {K.PLACE_STOP: Tier.ADD, K.REPLACE_STOP: Tier.ADD,')],
    'stop not resized to the position': [(M + 'core.py',
        "    return Order(price=price, qty=w['qty'])",
        "    return Order(price=price, qty=w['stop'].qty if w['stop'] is not None else w['qty'])")],
    'add kept after TP1': [(M + 'core.py',
        "        want_add = (plan.has_add and w['add_phase'] is not AddPhase.CLOSED and not w['tp1_confirmed']",
        "        want_add = (plan.has_add and w['add_phase'] is not AddPhase.CLOSED")],
    'gap fills at the level': [(M + 'sim.py',
        "            hit = next(((g, x) for g in PRIORITY for leg, p, falls in lv",
        "            hit = next(((g, p) for g in PRIORITY for leg, p, falls in lv")],
    'stop-first ambiguity removed': [(M + 'sim.py',
        '    blocked = st.stop is not None and candle.low <= st.stop.price <= candle.high',
        '    blocked = False')],
    'time exit off by one': [(M + 'core.py',
        '                if (candle.open_ms - plan.entry_candle_open_ms) // plan.tf_ms + 1 >= plan.time_exit_candles:',
        '                if (candle.open_ms - plan.entry_candle_open_ms) // plan.tf_ms >= plan.time_exit_candles:')],
    'cost veto disabled': [(M + 'plan.py', '    ok = cost <= CTX.multiply(max_cost_r, dist)', '    ok = True')],
    'late add kept open': [(M + 'core.py',
        "                flags['flatten'] = CTX.add(flags['flatten'], q)",
        "                pass")],
    # ------------------------------------------------------------------- Cowork's attack + Codex rulings (f668951)
    'cap not re-checked on the actual add fill': [(M + 'core.py',
        "            over = _excess_risk(plan, w, des_stop, CTX.subtract(live, cut)) if flags['added'] else ZERO",
        "            over = ZERO")],
    'racing add re-opens after a terminal stop': [(M + 'core.py',
        "    exiting = flags['terminal'] and w['qty'] > 0",
        "    exiting = False")],
    'duplicate fill applied twice': [(M + 'core.py',
        "        if ev.fill_id in seen:\n",
        "        if False:\n")],
    'fill on an unrequested / retired leg accepted': [(M + 'core.py',
        "    if not (0 < q <= CTX.add(have, left)):",
        "    if False:")],
    'targets not venue-feasible': [(M + 'core.py',
        "    return q >= r.min_qty and CTX.multiply(q, px) >= r.min_notional",
        "    return True")],
    'break-even on a partial TP1': [(M + 'core.py',
        "    if w['tp1_confirmed'] and w['tp1'] is None and _racing(w, Leg.TP1) == 0:",
        "    if w['tp1_confirmed']:")],
    'break-even at the raw average': [(M + 'core.py',
        "        be = _net_break_even(plan, w, avg)",
        "        be = rules.quantize_price(avg, toward_profit(side))")],
    'retained TP1 left on the loss side': [(M + 'core.py',
        "                    if CTX.multiply(s, CTX.subtract(w['tp1'].price, avg)) > 0:",
        "                    if True:")],
    'net break-even in the ambient decimal context': [(M + 'core.py',
        "DIV.multiply(DIV.add(ONE, slip), DIV.add(ONE, fee))",
        "DIV.multiply(ONE + slip, ONE + fee)")],
    'sim level distance in the ambient decimal context': [(M + 'sim.py',
        "CTX.abs(CTX.subtract(p, x))",
        "abs(p - x)")],
    # ---------------------------------------------------------------------- driver fault fuzz guards (M4 / REC-02)
    'coverage ignores what a FINAL stop executed': [(M + 'driver.py',
        "            out = CTX.add(out, max(CTX.subtract(b.executed, b.filled), ZERO))",
        "            pass")],
    'no racing allowance when a target shrinks': [(M + 'core.py',
        "                _set_racing(w, leg, CTX.add(_racing(w, leg), CTX.subtract(cur.qty, des.qty)))",
        "                pass")],
    'no racing allowance for a replaced stop': [(M + 'core.py',
        "            _set_racing(w, Leg.STOP, CTX.add(_racing(w, Leg.STOP), cur.qty))",
        "            pass")],
    'deferred reductions booked out of venue order': [(M + 'driver.py',
        "            blocked = blocked or not opening",
        "            blocked = False")],
    'racing retired under a deferred fill': [(M + 'driver.py',
        "        if not sources and not any(pf.fill.leg is r.leg for pf in w.d['deferred']):",
        "        if not sources:")],
    'a close that found nothing is retried': [(M + 'driver.py',
        "            if b.executed > 0:",
        "            if True:")],
    'a short reduce is retried as a full close': [(M + 'core.py',
        "        if whole and w['closing'] == 0:",
        "        if True:")],
    'a binding never counts booked fills': [(M + 'driver.py',
        "                w.replace_binding(b, filled=CTX.add(b.filled, pf.fill.qty))",
        "                pass")],
    'close fills limited to the current request': [(M + 'core.py',
        "        if not (q > 0 and (w['closing'] > 0 or _racing(w, Leg.CLOSE) > 0)):",
        "        if not (0 < q <= w['closing']):")],
    'a short close re-requests everything in flight': [(M + 'core.py',
        "        dead = w['closing'] if dead_qty is None else min(dead_qty, w['closing'])",
        "        dead = w['closing']")],
    'no algo route fallback (TNET-01)': [(M + 'driver.py',
        "    return outcome.detail == 'algo_route' or outcome.error_code in ALGO_ROUTE_CODES",
        "    return False")],
    'route fallback while the classic may be live (TNET-01)': [(M + 'driver.py',
        "    if b is None or b.leg is not Leg.STOP or b.route != 'classic' or b.state is not BindState.SENT:",
        "    if b is None:")],
    # ---------------------------------------------------------------------- Cowork attack on the driver (ad0406a)
    'a lost stop is not re-placed (HIGH 1/2)': [(M + 'driver.py',
        "    if pos.qty == 0 or pos.stop is None or pos.stage is not Stage.ACTIVE:",
        "    if True:")],
    'a refusal the core cannot take is raised (HIGH 3)': [(M + 'driver.py',
        "        w.reconcile.append(('core_refused', f'{type(ex).__name__}: {ex.path}'))",
        "        raise ex")],
    'a late stop refusal reaches the core (HIGH 3)': [(M + 'driver.py',
        "                if b.current and pos.stop is not None and not b.core_cancelled and pos.stage is Stage.ACTIVE:",
        "                if b.current:")],
    'a stale held close is released (MED 4)': [(M + 'driver.py',
        "            if left <= 0:",
        "            if False:")],
    'a foreign-asset fee is booked 1:1 (MED 5)': [(M + 'driver.py',
        "    if f.fee_asset == ds.quote_asset:",
        "    if True:")],
    'a close races an in-flight reduce (MED 6)': [(M + 'driver.py',
        "        if item.order_type is OrderType.MARKET and item.reduce_only and _reducing_in_flight(self):",
        "        if False:")],
    'HOLD keeps a flat stop cancel (MED 7)': [(M + 'driver.py',
        "            flat_stop = item.purpose is Purpose.PROTECT and pos.qty == 0",
        "            flat_stop = False")],
    'a stop already bound is submitted again (INFO 11)': [(M + 'driver.py',
        "            if same:  ",
        "            if False:  ")],
    'negative / non-canonical zero pnl': [(M + 'core.py',
        "    return _canonical_zero(CTX.subtract(CTX.subtract(gross, state.fees), state.funding))",
        "    return CTX.subtract(CTX.subtract(gross, state.fees), state.funding)")],
}



def export_head(dst):
    root_py = subprocess.run(['git', '-C', ROOT, 'ls-files', '--', '*.py'], capture_output=True, text=True,
                             check=True).stdout.split()
    root_py = [f for f in root_py if '/' not in f]
    blob = subprocess.run(['git', '-C', ROOT, 'archive', '--format=tar', 'HEAD', 'newcore', 'tests', 'pytest.ini',
                           *root_py], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        tar.extractall(dst, filter='data')


def run_suite(cwd):
    out = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q', '-o', 'addopts=',
                          '-m', 'not slow', 'tests/newcore_management'],
                         cwd=cwd, capture_output=True, text=True, timeout=900)
    failed = sorted(set(re.findall(r'^FAILED (.+?)(?: - .*)?$', out.stdout, re.M)))
    summary = (out.stdout.strip().splitlines() or [''])[-1]
    return out.returncode, failed, summary


def main(names):
    tmp = tempfile.mkdtemp(prefix='nc07_mut_')
    try:
        base = os.path.join(tmp, 'base')
        export_head(base)
        rc, failed, summary = run_suite(base)
        print(f'unmutated HEAD: rc={rc} {summary}')
        if rc != 0:
            print('  baseline must pass first:', failed)
            return 2
        survivors = []
        for name in names:
            work = os.path.join(tmp, re.sub(r'\W+', '_', name))
            shutil.copytree(base, work)
            ok = True
            for path, old, new in MUTATIONS[name]:
                src = open(os.path.join(work, path), encoding='utf-8').read()
                if src.count(old) != 1:
                    print(f'[{name}] mutation anchor not found exactly once in {path}: {old[:60]!r}')
                    ok = False
                    break
                with open(os.path.join(work, path), 'w', encoding='utf-8', newline='\n') as f:
                    f.write(src.replace(old, new))
            if not ok:
                survivors.append(name)
                continue
            rc, failed, summary = run_suite(work)
            killed = rc != 0
            print(f'[{name}] {"KILLED" if killed else "SURVIVED"} ({summary}); {len(failed)} failing tests')
            for t in failed[:5]:
                print(f'    {t}')
            if not killed:
                survivors.append(name)
            shutil.rmtree(work, ignore_errors=True)
        print('survivors:', survivors or 'none')
        return 1 if survivors else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:] or list(MUTATIONS)))
