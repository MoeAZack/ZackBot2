"""NC-01 mutation evidence (contract 6.7). A script, not a pytest module.

For each mutation it exports the committed HEAD (git archive) into a fresh temp folder, removes or weakens ONE domain
rule there, runs tests/newcore, and requires the suite to FAIL - naming the failing tests. The working tree is never
touched. Usage (one run at a time):

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

# name -> (file, exact source text, replacement)
MUTATIONS = {
    'numeric type check': (D + 'base.py', "    req(type(v) is Decimal, path, f'not a Decimal ({type(v).__name__})')\n",
                           "    if type(v) is not Decimal:\n        return\n"),
    'unknown vs empty': (D + 'portfolio.py', "        req(all(c is None for c in cols) if unknown else all(c is not None for c in cols), p + '.positions',",
                         "        req(True, p + '.positions',"),
    'not-found ambiguity': (D + 'orders.py', "            req(self.executed_qty is None and self.avg_price is None and ev is None and not self.corroboration\n"
                                             "                and self.resolved_by is None, p,",
                            "            req(True, p,"),
    'terminal monotonicity': (D + 'orders.py', '    _S.FILLED: frozenset(), _S.CANCELLED',
                              '    _S.FILLED: frozenset({_S.WORKING}), _S.CANCELLED'),
    'protection bound': (D + 'protection.py', '    req(prot.qty <= exposure or prot.replacement is not None,',
                         '    req(True,'),
    'manual pause bypass': (D + 'portfolio.py', '                req(pf.permits(it.purpose, Op.PLACE, one_shot=it.authorized_by is not None), ip,',
                            '                req(True, ip,'),
    'reason-code membership': (D + 'base.py', "        return lambda v, p: req(isinstance(v, tp), p, f'{v!r} is not a {tp.__name__}')",
                               '        return lambda v, p: None'),
    'hard HOLD widened': (D + 'modes.py', '    | {(Purpose.PROTECT, Op.PLACE)}', '    | {(Purpose.PROTECT, Op.PLACE), (Purpose.CLOSE, Op.MANAGE)}'),
    'apply before durable result': (D + 'events.py', "                req(fin is not None, p + '.to_state', 'a terminal step needs a durable FINAL result first')\n"
                                                    "                req(terminal_for(fin) is ev.to_state,",
                                    "                req(fin is None or terminal_for(fin) is ev.to_state,"),
    'fill ledger': (D + 'portfolio.py', "        req(run == self.qty, p + '.qty',", "        req(True, p + '.qty',"),
    'future before body': (D + 'codec.py', '    if v > SCHEMA_VERSION:\n        raise FutureSchema(',
                           '    if v > SCHEMA_VERSION and doc["body"] is not None:\n        raise FutureSchema('),
    'drain resting maker': (D + 'portfolio.py', '            if it.pullable:', '            if False:'),
}


def export_head(dst):
    blob = subprocess.run(['git', '-C', ROOT, 'archive', '--format=tar', 'HEAD', 'newcore', 'tests', 'engine.py',
                           'trade_audit.py', 'pytest.ini'], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        tar.extractall(dst, filter='data')


def run_suite(cwd):
    out = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q', '-o', 'addopts=',
                          'tests/newcore', '--ignore=tests/newcore/test_nc01_perf.py'],
                         cwd=cwd, capture_output=True, text=True, timeout=900)
    failed = sorted(set(re.findall(r'^FAILED (\S+?)(?: - .*)?$', out.stdout, re.M)))
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
            path, old, new = MUTATIONS[name]
            work = os.path.join(tmp, re.sub(r'\W+', '_', name))
            shutil.copytree(base, work)
            src = open(os.path.join(work, path), encoding='utf-8').read()
            if src.count(old) != 1:
                print(f'[{name}] mutation anchor not found exactly once in {path}')
                survivors.append(name)
                continue
            with open(os.path.join(work, path), 'w', encoding='utf-8', newline='\n') as f:
                f.write(src.replace(old, new))
            rc, failed, summary = run_suite(work)
            killed = rc != 0
            print(f'[{name}] {"KILLED" if killed else "SURVIVED"} ({summary}); {len(failed)} failing tests')
            for t in failed[:6]:
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
