"""NC-02a mutation evidence. A script, not a pytest module (same mechanics as tests/newcore/nc01_mutations.py).

For each mutation it exports the committed HEAD (git archive) into a fresh temp folder, removes or weakens ONE store
rule there, runs tests/newcore_store + the FileJournal contract test, and requires the run to FAIL, naming the failing
tests. The working tree is never touched. Usage (one run at a time):

    python tests/newcore_store/nc02a_mutations.py            # all mutations
    python tests/newcore_store/nc02a_mutations.py NAME ...   # selected ones
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
S = 'newcore/store/'
SUITE = ['tests/newcore_store', 'tests/newcore_ports/test_nc02a_file_journal_contract.py']

# name -> (file, [(exact source text, replacement)]); each anchor must occur exactly once
MUTATIONS = {
    'append without fsync': (S + 'journal.py', [('            self._fs.fsync(self._h)\n', '            pass\n')]),
    'segment create without file fsync': (S + 'journal.py', [('        fs.fsync(h)\n    finally:\n        _close_quiet',
                                                              '        pass\n    finally:\n        _close_quiet')]),
    'segment create without directory fsync': (S + 'journal.py', [('    fs.fsync_dir(journal_dir)\n', '')]),
    'no CRC check': (S + 'frame.py', [('    if _crc(rtype, flags, payload) != crc:\n        return None\n', '')]),
    'future header version allowed': (S + 'header.py', [('    if fv > READER_VERSION or mr > READER_VERSION:\n'
                                                         '        return VersionVerdict.FUTURE\n', '')]),
    'unknown header version type allowed': (S + 'header.py', [(
        '    if not (_is_count(fv) and _is_count(mr)) or fv == 0:\n        return VersionVerdict.UNKNOWN\n', '')]),
    'future event schema allowed': (S + 'recovery.py', [('                if res.outcome is Outcome.UNSUPPORTED_VERSION:',
                                                         '                if False:')]),
    'seam gains a truncate (repair could cut torn bytes)': (S + 'fs.py', [(
        "    def open_append(self, p):\n        return _Handle(os.open(p, os.O_WRONLY | os.O_APPEND | _BINARY), p)\n",
        "    def open_append(self, p):\n        return _Handle(os.open(p, os.O_WRONLY | os.O_APPEND | _BINARY), p)\n\n"
        "    def truncate(self, p, n):\n        os.truncate(p, n)\n")]),
    'mid-segment damage treated as torn tail': (S + 'frame.py', [(
        '            later = _valid_record_after(data, off)\n', '            later = None\n')]),
    'evidence not copied before the seal': (S + 'recovery.py', [(
        "            ref, new = write_evidence(fs, account_dir, account_id, plan.names[no], off, data)\n"
        "            evidence.append(ref)\n            if new:\n                created.append(ref.name)\n",
        "            pass\n")]),
    'failed fsync reopens the dirty segment instead of rolling': (S + 'journal.py', [(
        '            if not extra:                                         # nothing of the failed append reached the file',
        '            if True:')]),
    'gate committed before the write': (S + 'journal.py', [(
        "        staged = self._folder.stage(event)                        # gate().stage: validate only, nothing consumed\n",
        "        staged = self._folder.stage(event)                        # gate().stage: validate only, nothing consumed\n"
        "        if staged is not Admission.ALREADY_APPLIED:\n            self._folder.commit(staged)\n"), (
        "        self._folder.commit(staged)                               # staged.commit(): only now is it consumed\n",
        "")]),
    'gate bypassed on append': (S + 'fold.py', [(
        '        staged = _seq_typed(lambda: self.gate.stage(ev))\n',
        "        staged = type('B', (), {'event': ev, 'commit': lambda self: None})()\n")]),
    'sent intent classified as never sent': (S + 'fold.py', [('            elif sent is None:\n',
                                                              '            elif True:\n')]),
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
    out = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q', '-o', 'addopts=', *SUITE],
                         cwd=cwd, capture_output=True, text=True, timeout=1800)
    failed = sorted(set(re.findall(r'^(?:FAILED|ERROR) (.+?)(?: - .*)?$', out.stdout, re.M)))
    summary = (out.stdout.strip().splitlines() or [''])[-1]
    return out.returncode, failed, summary


def mutate(base, dst, path, edits):
    shutil.copytree(base, dst)
    p = os.path.join(dst, *path.split('/'))
    with open(p, encoding='utf-8') as fh:
        src = fh.read()
    for old, new in edits:
        if src.count(old) != 1:
            raise SystemExit(f'anchor not unique in {path}: {old[:60]!r} ({src.count(old)})')
        src = src.replace(old, new)
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(src)


def main(names):
    tmp = tempfile.mkdtemp(prefix='nc02a_mut_')
    bad = []
    try:
        base = os.path.join(tmp, 'base')
        export_head(base)
        rc, failed, summary = run_suite(base)
        print(f'unmutated HEAD: rc={rc} {summary}', flush=True)
        if rc != 0:
            print('  baseline must pass first:', failed)
            return 2
        for i, name in enumerate(names):
            path, edits = MUTATIONS[name]
            dst = os.path.join(tmp, f'm{i}')
            mutate(base, dst, path, edits)
            rc, failed, summary = run_suite(dst)
            caught = rc != 0 and failed
            print(f'{"CAUGHT" if caught else "SURVIVED"}  {name}: {summary}', flush=True)
            for f in failed[:4]:
                print(f'    {f}')
            if not caught:
                bad.append(name)
            shutil.rmtree(dst, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'{len(names) - len(bad)}/{len(names)} mutations caught' + (f'; SURVIVED: {bad}' if bad else ''))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:] or list(MUTATIONS)))
