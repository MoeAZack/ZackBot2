"""NC-01 mutation evidence (contract r2 6.7). A script, not a pytest module.

For each mutation it exports the committed HEAD (git archive) into a fresh temp folder, removes or weakens ONE domain
rule there, runs tests/newcore + tests/newcore_ports, and requires the suite to FAIL - naming the failing tests. The
working tree is never touched. Usage (one run at a time):

    python tests/newcore/nc01_mutations.py            # all mutations
    python tests/newcore/nc01_mutations.py NAME ...   # selected ones
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
D = 'newcore/domain/'

# name -> (file, [(exact source text, replacement), ...]); each anchor must occur exactly once
MUTATIONS = {
    'numeric type check': (D + 'base.py', [(
        "    if type(v) is not Decimal:\n        raise InvalidRecord(path, f'not a Decimal ({type(v).__name__})')\n",
        "    if type(v) is not Decimal:\n        return v\n")]),
    'unknown vs empty': (D + 'portfolio.py', [(
        "        req(all(c is None for c in cols) if unknown else all(c is not None for c in cols), p + '.positions',",
        "        req(True, p + '.positions',")]),
    'not-found ambiguity': (D + 'orders.py', [(
        "            req(self.executed_qty is None and self.avg_price is None and ev is None and not self.corroboration\n"
        "                and self.resolved_by is None, p,",
        "            req(True, p,")]),
    'terminal monotonicity': (D + 'orders.py', [('    _S.FILLED: frozenset(), _S.CANCELLED',
                                                 '    _S.FILLED: frozenset({_S.WORKING}), _S.CANCELLED')]),
    'protection bound': (D + 'protection.py', [('    req(prot.qty <= exposure or prot.replacement is not None,',
                                                '    req(True,')]),
    'unconfirmed replacement counted as confirmed coverage': (D + 'protection.py', [(
        '    unpromoted replacement is never counted here, whatever its state."""\n',
        '    unpromoted replacement is never counted here, whatever its state."""\n'
        '    if prot.replacement is not None:\n        return intents_by_id[prot.replacement].qty\n')]),
    'replacing status hides an undersized stop': (D + 'protection.py', [(
        '    if confirmed_coverage(prot, intents_by_id) < exposure:\n',
        '    if prot.replacement is not None:\n        return ProtectionStatus.REPLACING\n'
        '    if confirmed_coverage(prot, intents_by_id) < exposure:\n')]),
    'cross-record symbol/side agreement': (D + 'portfolio.py', [(
        "        req((owner.symbol, owner.side) == (it.symbol, it.side), ip, 'owner of another symbol / side')",
        "        req(True, ip, 'owner of another symbol / side')")]),
    'cross-record account agreement': (D + 'portfolio.py', [(
        "        req(it.account_id == acct, ip + '.account_id', 'intent of another account')", "        pass")]),
    'cross-record quantity agreement': (D + 'protection.py', [(
        "        req((it.qty, it.stop_price) == (prot.qty, prot.price), path + '.order',",
        "        req(True, path + '.order',")]),
    'parity: bool accepted as int': (D + 'base.py', [(
        "    req(type(v) is int, path, f'not an int ({type(v).__name__})')",
        "    req(isinstance(v, int), path, f'not an int ({type(v).__name__})')")]),
    'parity: enum text accepted in memory': (D + 'base.py', [(
        "        return lambda v, p: req(isinstance(v, tp), p, f'{v!r} is not a {tp.__name__}')",
        "        return lambda v, p: req(isinstance(v, tp) or v in {m.value for m in tp}, p, 'x')")]),
    'fresh snapshot age': (D + 'portfolio.py', [(
        "    req(age <= max_age_ms, 'Portfolio.proof.at_ms',", "    req(True, 'Portfolio.proof.at_ms',")]),
    'protection carried by a non-PROTECT intent': (D + 'protection.py', [(
        "    req(it.purpose is Purpose.PROTECT and it.order_type is OrderType.STOP_MARKET and it.owner_id == prot.owner_id,",
        "    req(it.owner_id == prot.owner_id,")]),
    'client id collision': (D + 'portfolio.py', [(
        "            req(c not in cids, ip + '.client_order_id', 'duplicate client order id')", "            pass")]),
    'binding confirmation phrase': (D + 'account.py', [(
        "        req(self.typed_phrase == confirmation_phrase(self.account_id, self.new_key_digest), p + '.typed_phrase',",
        "        req(True, p + '.typed_phrase',")]),
    'manual pause bypass': (D + 'portfolio.py', [(
        '                req(pf.permits(it.purpose, Op.PLACE, one_shot=it.authorized_by is not None), ip,',
        '                req(True, ip,')]),
    'reason-code membership': (D + 'base.py', [(
        "        return lambda v, p: req(isinstance(v, tp), p, f'{v!r} is not a {tp.__name__}')",
        '        return lambda v, p: None')]),
    'hard HOLD widened': (D + 'modes.py', [('    | {(Purpose.PROTECT, Op.PLACE)}',
                                            '    | {(Purpose.PROTECT, Op.PLACE), (Purpose.CLOSE, Op.MANAGE)}')]),
    'apply before durable result': (D + 'events.py', [(
        "                req(fin is not None, p + '.to_state', 'a terminal step needs a durable FINAL result first')\n"
        "                req(terminal_for(fin) is ev.to_state,",
        "                req(fin is None or terminal_for(fin) is ev.to_state,")]),
    'fill ledger': (D + 'portfolio.py', [("        req(run == self.qty, p + '.qty',", "        req(True, p + '.qty',")]),
    'future before body': (D + 'codec.py', [('    if v > SCHEMA_VERSION:\n        raise FutureSchema(',
                                             '    if v > SCHEMA_VERSION and set(doc["body"]) is not None:\n'
                                             '        raise FutureSchema(')]),
    'damage before version (text)': (D + 'codec.py', [(
        "    check_header(doc)\n    if problems:\n        raise InvalidRecord('document', f'hostile JSON: {problems[0]}')\n",
        "    if problems:\n        raise InvalidRecord('document', f'hostile JSON: {problems[0]}')\n    check_header(doc)\n")]),
    'drain resting maker': (D + 'portfolio.py', [('            if it.pullable:', '            if False:')]),
    'E07: record_type looked up before its type is checked': (D + 'codec.py', [(
        '    if type(rtype) is not str or rtype not in RECORD_TYPES:', '    if rtype not in RECORD_TYPES:')]),
    'I05: owner_kind ignored (orphan never recognized)': (D + 'orders.py', [(
        '        return self.owner_kind is OwnerKind.PORTFOLIO', '        return False')]),
    'S05: reducing intents not bounded by their lot': (D + 'portfolio.py', [(
        '        req(q <= lots[lot_id].qty,', '        req(True,')]),
    'S05 re-check: orphan cancel work counted as live reducing': (D + 'portfolio.py', [(
        '        if it.purpose not in (Purpose.REDUCE, Purpose.CLOSE) or it.owner_kind is not OwnerKind.LOT or it.intent_id in preds:',
        '        if it.purpose not in (Purpose.REDUCE, Purpose.CLOSE) or it.owner_kind is OwnerKind.ENTRY_INTENT or it.intent_id in preds:')]),
    'H05: Lot not encodable standalone': (D + 'codec.py', [("    'lot': portfolio.Lot,\n", '')]),
    'CP05: a position accepts duplicate lots': (D + 'portfolio.py', [(
        "        req(len({x.lot_id for x in self.lots}) == len(self.lots), p + '.lots',", "        req(True, p + '.lots',")]),
    'ruling 1: Decision.symbol unvalidated': (D + 'decision.py', [(
        "            check_symbol(self.symbol, p + '.symbol')", '            pass')]),
    'ruling 2: whitespace-only text accepted': (D + 'base.py', [("    req(v.strip() != '', path,", '    req(True, path,')]),
    'ruling 4: position lots keep their given order': (D + 'portfolio.py', [(
        "        if ordered != self.lots:\n            object.__setattr__(self, 'lots', ordered)", '        pass')]),
    'ports: journal ignores owner_kind (every owner treated as a lot)': ('newcore/ports/journal.py', [(
        '            if h.owner_kind is OwnerKind.ENTRY_INTENT:', '            if False:')]),
    'r3 item 2: daily_halt missing from the registry': (D + 'reasons.py', [(
        "    RISK_DAILY_HALT = 'risk_gateway.daily_halt'\n", '')]),
    'r3 item 1: an entry / exit reason accepted as incident kind': (D + 'incident.py', [(
        "        req(self.kind.namespace not in NOT_INCIDENT_KINDS,", '        req(True,')]),
    'r3 item 1: incident journaled under another kind': ('newcore/ports/journal.py', [(
        '        return EventHeader(kind=EventKind.INCIDENT_RECORDED, **base)',
        '        return EventHeader(kind=EventKind.BINDING_CHANGED, **base)')]),
    'P1: cancel-replace link not checked (predecessor need not be cancelling)': (D + 'portfolio.py', [(
        "        req(old.state is IntentState.CANCELLING, ip, 'the predecessor of a cancel-replace must be CANCELLING')",
        '        pass')]),
    'P1: cancel-replace lot mismatch accepted': (D + 'portfolio.py', [(
        '        req((old.account_id, old.symbol, old.side, old.owner_id) == (it.account_id, it.symbol, it.side, it.owner_id), ip,',
        '        req((old.account_id, old.symbol, old.side) == (it.account_id, it.symbol, it.side), ip,')]),
    'P1: cumulative fills unbounded (ledger closes past the lot)': (D + 'portfolio.py', [(
        "            req(run > 0, f'{p}.fills[{i}]',", "            req(True, f'{p}.fills[{i}]',")]),
    'P2: portfolio collections not canonical': (D + 'portfolio.py', [(
        "            _canonical(self, 'positions', lambda x: (x.symbol, x.side.value))\n"
        "            _canonical(self, 'intents', lambda x: x.intent_id)", '            pass')]),
    'r3 item 3a: exchange_external accepted on a normal close': (D + 'orders.py', [(
        "    req((ev is Evidence.EXCHANGE_EXTERNAL) <= is_post_hoc(intent), p + '.evidence',", "    req(True, p + '.evidence',")]),
    'r3 item 3a: external trades need not sum to the booking': (D + 'orders.py', [(
        "            req(total == self.executed_qty == self.requested_qty, p + '.executed_qty',",
        "            req(True, p + '.executed_qty',")]),
    'r3 item 3a: a post-hoc booking may be sent': (D + 'orders.py', [(
        '    return intent.state is IntentState.DURABLE and not is_post_hoc(intent)',
        '    return intent.state is IntentState.DURABLE')]),
    'r3 item 3b: a zero fill supersedes a corroborated not-found': (D + 'orders.py', [(
        'new.evidence is Evidence.EXCHANGE_FINAL and new.executed_qty > 0',
        'new.evidence is Evidence.EXCHANGE_FINAL')]),
    'r3 item 3b: any FINAL prior is superseded': (D + 'orders.py', [(
        '    return (prior.phase is ResultPhase.FINAL and prior.evidence is Evidence.NOT_FOUND_CORROBORATED\n',
        '    return (prior.phase is ResultPhase.FINAL\n')]),
    'r3 item 3b: superseding is refused (exchange evidence never wins)': (D + 'events.py', [(
        '            if prior is not None and r.intent_id not in superseding and supersedes(prior, r):',
        '            if False:')]),
    'r3 item 3b: superseded more than once': (D + 'events.py', [(
        '            if prior is not None and r.intent_id not in superseding and supersedes(prior, r):',
        '            if prior is not None and supersedes(prior, r):')]),
    'r3 item 3b: a late-fill reconcile without its record': (D + 'events.py', [(
        '                req(late is not None and late.result_id in d.evidence, p + \'.decision\',',
        '                req(True, p + \'.decision\',')]),
    'r3 item 3b: the terminal step ignores a superseding fill': (D + 'events.py', [(
        '                if r.intent_id in live:\n                    finals[r.intent_id] = r',
        '                if False:\n                    finals[r.intent_id] = r')]),
    'r3 item 3b: a late-fill decision about no intent': (D + 'decision.py', [(
        "            req(a is Action.RECONCILE and self.subject_id is not None, p + '.reason',",
        "            req(True, p + '.reason',")]),
    'r3 item 3b: the journal gate lets any record follow a FINAL': ('newcore/ports/journal.py', [(
        '                req(supersedes(final, ev.result), \'event.result\',',
        '                req(True, \'event.result\',')]),
    'r3 item 3b: the journal supersedes more than once': ('newcore/ports/journal.py', [(
        '        if (k is EventKind.RESULT_RECORDED and st.final is Evidence.NOT_FOUND_CORROBORATED and not st.superseded',
        '        if (k is EventKind.RESULT_RECORDED and st.final in (Evidence.NOT_FOUND_CORROBORATED, Evidence.EXCHANGE_FINAL)')]),
    'r3 item 4: a resting target may open risk': (D + 'orders.py', [(
        "            req(u in (Purpose.REDUCE, Purpose.CLOSE) and self.owner_kind is OwnerKind.LOT, p + '.order_type',\n"
        "                'a resting reduce-only target is a lot REDUCE / CLOSE')",
        '            pass')]),
    'r3 item 4: a resting target on any exit reason': (D + 'orders.py', [(
        "            req(self.reason in TARGET_REASONS, p + '.reason',", "            req(True, p + '.reason',")]),
    'r3 item 4: a resting target needs no price': (D + 'orders.py', [(
        '        if t in LIMIT_TYPES:', '        if t is OrderType.LIMIT_POST_ONLY:')]),
    'r3 item 4: the venue grid ignores the reduce-only capability': (D + 'instrument.py', [(
        "            req(self.supports(Capability.REDUCE_ONLY), p + '.order_type',",
        "            req(True, p + '.order_type',")]),
    'r3 item 5: a management tick about no lot': (D + 'decision.py', [(
        "            req(a is Action.WAIT and self.authority is Authority.STRATEGY and self.subject_id is not None, p + '.reason',",
        "            req(a is Action.WAIT and self.authority is Authority.STRATEGY, p + '.reason',")]),
    'r3 item 5: a management tick about an intent': (D + 'decision.py', [(
        "            check_id(self.subject_id, p + '.subject_id', 'lot')\n        if self.reason is ReasonCode.RECONCILE_LATE_FILL:",
        "            check_id(self.subject_id, p + '.subject_id', 'lot', 'int')\n        if self.reason is ReasonCode.RECONCILE_LATE_FILL:")]),
    'r3 item 5: a management tick under any authority': (D + 'decision.py', [(
        "            req(a is Action.WAIT and self.authority is Authority.STRATEGY and self.subject_id is not None, p + '.reason',",
        "            req(a is Action.WAIT and self.subject_id is not None, p + '.reason',")]),
    'r3 item 6: an ENTRY may omit its stop distance': (D + 'orders.py', [(
        "            req(self.stop_distance is not None, p + '.stop_distance', 'an ENTRY carries its planned stop distance')\n"
        "            positive(self.stop_distance, p + '.stop_distance')",
        "            pass")]),
    'r3 item 6: any intent may carry a stop distance': (D + 'orders.py', [(
        "            req(self.stop_distance is None, p + '.stop_distance', 'only an ENTRY plans a stop distance')",
        "            pass")]),
    'r3 item 6: a non-positive stop distance': (D + 'orders.py', [(
        "            positive(self.stop_distance, p + '.stop_distance')\n        else:",
        "            pass\n        else:")]),
    'duplicate JSON keys': (D + 'codec.py', [("            if k in out:\n                problems.append(",
                                              "            if False:\n                problems.append(")]),
    'truncated JSON': (D + 'codec.py', [(
        "        raise InvalidRecord('document', f'not one JSON document ({type(ex).__name__})') from None",
        "        doc = {}")]),
    'missing persisted fields': (D + 'codec.py', [
        ('    if d.keys() != names:', '    if d.keys() - names:'),
        ("    kw = {name: dec(d[name], f'{path}.{name}') for name, dec in spec}",
         "    kw = {name: dec(d.get(name), f'{path}.{name}') for name, dec in spec}")]),
    'event ordering': (D + 'ledger.py', [('    if event.sequence != expected:', '    if event.sequence < expected:')]),
    'idempotent replay': (D + 'ledger.py', [('            if d.sequence == event.sequence and d.sha256 == digest:',
                                             '            if d.sequence == event.sequence:')]),
}


def export_head(dst):
    root_py = subprocess.run(['git', '-C', ROOT, 'ls-files', '--', '*.py'], capture_output=True, text=True,
                             check=True).stdout.split()
    root_py = [f for f in root_py if '/' not in f]          # every root legacy module (the boundary test lists them)
    blob = subprocess.run(['git', '-C', ROOT, 'archive', '--format=tar', 'HEAD', 'newcore', 'tests', 'pytest.ini',
                           *root_py], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        tar.extractall(dst, filter='data')


def run_suite(cwd):
    out = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q', '-o', 'addopts=',
                          'tests/newcore', 'tests/newcore_ports', '--ignore=tests/newcore/test_nc01_perf.py'],
                         cwd=cwd, capture_output=True, text=True, timeout=900)
    failed = sorted(set(re.findall(r'^FAILED (.+?)(?: - .*)?$', out.stdout, re.M)))
    summary = (out.stdout.strip().splitlines() or [''])[-1]
    return out.returncode, failed, summary


def main(names):
    tmp = tempfile.mkdtemp(prefix='nc01_mut_')
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
            path, pairs = MUTATIONS[name]
            work = os.path.join(tmp, re.sub(r'\W+', '_', name))
            shutil.copytree(base, work)
            src = open(os.path.join(work, path), encoding='utf-8').read()
            bad = [old for old, _ in pairs if src.count(old) != 1]
            if bad:
                print(f'[{name}] mutation anchor not found exactly once in {path}: {bad[0][:60]!r}')
                survivors.append(name)
                continue
            for old, new in pairs:
                src = src.replace(old, new)
            with open(os.path.join(work, path), 'w', encoding='utf-8', newline='\n') as f:
                f.write(src)
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
