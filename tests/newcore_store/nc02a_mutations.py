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
    'the torn check re-reads the by-binding entry (P1)': (S + 'store.py', [(
        "    elif entry[0] == 'torn':", "    elif _by_binding(fs, paths, account)[0] == 'torn':")]),
    'failure_reason echoes the exception message (P2)': (S + 'errors.py', [(
        "        return 'cipher_unavailable', CIPHER_UNAVAILABLE_REASON", "        return 'cipher_unavailable', f'{CIPHER_UNAVAILABLE_REASON}: {ex}'")]),
    'failure_reason classifies by message text (P2)': (S + 'errors.py', [(
        "    if _cipher_unavailable(ex):", "    if _cipher_unavailable(ex) or 'CipherUnavailable' in str(ex):")]),
    'a cipher-less repair fails without a typed reason (N6)': (S + 'recovery.py', [(
        "                        tuple(plan.findings) + (Finding(kind, None, None, why),), tuple(evidence), tuple(created),",
        "                        tuple(plan.findings), tuple(evidence), tuple(created),")]),
    'a torn own by-binding entry is silent (7af893a)': (S + 'store.py', [(
        "    elif entry[0] == 'torn':", "    elif False:")]),
    'a stray file in snap/ read as damage (N4)': (S + 'store.py', [("            seen.findings.append(f'stray file in snap/: {n} (not a generation: reported, ignored, kept)')", "            seen.damage.append('unexpected file in snap/')")]),
    'a malformed by-binding entry ignored (N7)': (S + 'store.py', [("    if state == 'bad':\n        return 'identity'", "    if False:\n        return 'identity'")]),
    'promotion ignores the by-binding index (N7)': (S + 'store.py', [('        bp = _binding_problem(self.fs, self.paths, account)', '        bp = None  # ')]),
    'bind() accepts any existing entry (N7)': (S + 'store.py', [("            if state != 'absent':\n                return state == 'ok' and owner == self.account_id\n", "            if False:\n                return True\n")]),
    'a failing kind() escapes boot (N3)': (S + 'store.py', [("    except OSError:\n        return 'unreadable'\n", "    except KeyError:\n        return 'unreadable'\n")]),
    'reused evidence not made durable (HIGH-1)': (S + 'envelope.py', [('            _make_durable(fs, p, ed, cipher, account_id)', '            pass  # ')]),
    'evidence dir entry not flushed when the dir exists (HIGH-1)': (S + 'envelope.py', [('    fs.set_private_acl(ed)\n    fs.fsync_dir(account_dir)\n', '    fs.set_private_acl(ed)\n')]),
    'a future snapshot record behind damage read as damage (HIGH-2, 02b)': (S + 'snapfile.py', [('        if _future_anywhere(raw, reader):\n            raise SnapFuture', '        if False:\n            raise SnapFuture')]),
    'no rule-1 sweep behind corruption (HIGH-2)': (S + 'recovery.py', [('            future.extend(_future_frames(n, resync_records(data, start)))', '            pass')]),
    'lock I/O error read as contention (N1)': (S + 'fs.py', [('            if ex.errno in _CONTENDED:\n', '            if True:\n')]),
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
        "            ref, new = write_evidence(fs, account_dir, account_id, plan.names[no], off, data, src, cipher)\n"
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
    'complete-length bad frame treated as a tail (finding 1, Codex ruling)': (S + 'frame.py', [(
        "        return False, 'a complete-length record fails its check'\n",
        "        return True, 'a complete-length record fails its check'\n")]),
    'corrupted length of a complete record treated as a tail (finding 1)': (S + 'frame.py', [(
        "        return False, 'a complete record with a corrupted length field'\n",
        "        return True, 'a complete record with a corrupted length field'\n")]),
    'header segment mismatch not stopped and no exception net (finding 2)': (S + 'recovery.py', [(
        "            raise _Out(Verdict.DAMAGED, damage + [Finding('foreign', names[m], 8,\n",
        "            damage.append(Finding('foreign', names[m], 8,\n"), (
        "                                                          'the journal names another account / aggregate / "
        "segment')])\n",
        "                                                          'the journal names another account / aggregate / "
        "segment'))\n"), (
        "    except (IndexError, KeyError, TypeError, ValueError, AttributeError, OverflowError) as ex:\n",
        "    except ZeroDivisionError as ex:\n")]),
    'no writer lock (finding 3)': (S + 'journal.py', [(
        "    lock = fs.lock_exclusive(os.path.join(journal_dir, LOCK_NAME))\n    if lock is None:\n",
        "    lock = fs.lock_exclusive(os.path.join(journal_dir, LOCK_NAME))\n    if False:\n")]),
    'no size fence before an append (finding 3)': (S + 'journal.py', [(
        '        if size != self._seg_len:                                 # Cowork finding 3: someone else wrote here\n',
        '        if False:\n')]),
    'non-canonical header accepted (finding 5)': (S + 'header.py', [(
        '    if payload is not None and canonical_json(doc) != payload:\n', '    if False:\n')]),
    # ------------------------------------------------------------------------------------------- NC-02b
    'HEAD picks the OLDER slot (D1)': (S + 'slots.py', [(
        '        seq, which, doc, _ = max(valid, key=lambda v: v[0])\n',
        '        seq, which, doc, _ = min(valid, key=lambda v: v[0])\n')]),
    'anchor ahead of HEAD ignored (D2, rule 2)': (S + 'store.py', [(
        "            seen.rollback.append('anchor ahead of HEAD')\n", "            pass\n")]),
    'configured binding vs store binding ignored (A11)': (S + 'store.py', [(
        "        seen.identity.append('the configured binding differs from the store binding')\n",
        "        pass\n")]),
    'trivially-empty / identity / HOLD-INIT promote without the owner (A08)': (S + 'store.py', [(
        '        needs_owner = identity_change or empty or self.mode is Mode.HOLD_INIT\n',
        '        needs_owner = False\n')]),
    'promotion without the second fresh snapshot (A08 atomicity)': (S + 'store.py', [(
        "        if not v2.match or s1.canonical().split(b'|', 2)[2] != s2.canonical().split(b'|', 2)[2]:\n",
        "        if not v1.match:\n")]),
    'orphan / any snapshot not peeked for rule 1 (D7)': (S + 'store.py', [(
        "        if peek(raw, reader) == 'future':\n", "        if False:\n")]),
    'HOLD entered without copying the evidence (A04)': (S + 'store.py', [(
        '        refs, incs = _copy_evidence(store, seen, now_ms)\n', '        refs, incs = [], []\n')]),
    'a checkpoint in HOLD is trusted (A05)': (S + 'store.py', [(
        '        trust = Trust.MANAGED if managed else Trust.HOLD\n',
        '        trust = Trust.MANAGED\n')]),
    'any KNOWN_EMPTY counts as proven (R-KNOWN-EMPTY)': (S + 'store.py', [(
        "    return (prov['kind'] in (ProvenanceKind.INIT_FLAT, ProvenanceKind.PROMOTION) and r is not None\n",
        "    return True or (prov['kind'] in (ProvenanceKind.INIT_FLAT, ProvenanceKind.PROMOTION) and r is not None\n")]),
    'stale exchange snapshot accepted (R-MATCH 2)': (S + 'reconcile.py', [(
        '    if not 0 <= age < max_age_ms:\n', '    if False:\n')]),
    'quantity tolerance ignored (R-MATCH 3)': (S + 'reconcile.py', [(
        '        elif abs(o - e) * 2 >= step:\n', '        elif False:\n')]),
    'a future settings record read as current (NF-03)': (S + 'snapfile.py', [(      # both guards (HIGH-2 sweep too)
        "        raise SnapFuture('settings record version')", "        pass  # raise SnapFuture('settings record version')"),
        ('        if r.rtype in (RT_HEADER, RT_SETTINGS):\n', '        if r.rtype in (RT_HEADER,):\n')]),
    'a torn first slot read as damage (drill finding)': (S + 'slots.py', [(
        "        return PairRead(PairState.ABSENT, None, None, states, 'torn first write')\n",
        "        return PairRead(PairState.DAMAGE, None, None, states, 'torn first write')\n")]),
    'sent intent classified as never sent': (S + 'fold.py', [('            elif sent is None:\n',
                                                              '            elif True:\n')]),
    'uncovered journal tail ignored at boot (item 6)': (S + 'store.py', [(
        '    if any(_ownership_changing(e) for e in tail):\n', '    if False:\n')]),
    'fold proof not tied to the journal (item 6)': (S + 'store.py', [(
        '    if proof is None or proof.kind is not ProofKind.JOURNAL or proof.through_sequence != '
        'store.journal.last_sequence():\n', '    if proof is None:\n')]),
    'fold of another account / aggregate accepted (item 6)': (S + 'store.py', [(
        '    if pf is None or pf.account_id != store.account_id or pf.portfolio_id != store.aggregate_id:\n',
        '    if pf is None:\n')]),
    'a failed checkpoint leaves the store writable': (S + 'store.py', [(
        '            self.mode, self.hold_kind = Mode.HOLD, HoldKind.DURABILITY_UNAVAILABLE\n'
        '            self.mark_hard_hold(now_ms)\n            raise\n', '            raise\n')]),
    'store close keeps the writer lock': (S + 'store.py', [(
        '        if j is not None:\n            j.close()\n', '        pass\n')]),
}


def export_head(dst):
    root_py = subprocess.run(['git', '-C', ROOT, 'ls-files', '--', '*.py'], capture_output=True, text=True,
                             check=True).stdout.split()
    root_py = [f for f in root_py if '/' not in f]
    blob = subprocess.run(['git', '-C', ROOT, 'archive', '--format=tar', 'HEAD', 'newcore', 'tests', 'pytest.ini',
                           *root_py], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        tar.extractall(dst, filter='data')
    _shim_step0_builder(dst)


def _shim_step0_builder(dst):
    """In the TEMP export only: until step 0's tests/newcore_ports/nc_events.py adopts NC-01 440bfbd's required
    OrderIntent.owner_kind, give its builder the field so the shared contract suite can run. The repo is not touched."""
    p = os.path.join(dst, 'tests', 'newcore_ports', 'nc_events.py')
    with open(p, encoding='utf-8') as fh:
        src = fh.read()
    if 'owner_kind' in src:
        return
    old = 'created_at_ms=at, owner_id=owner,'
    new = ("created_at_ms=at, owner_id=owner, owner_kind=None if owner is None else __import__('newcore.domain', "
           "fromlist=['OwnerKind']).OwnerKind('entry_intent' if owner in self.intents else 'lot'),")
    if src.count(old) == 1:
        with open(p, 'w', encoding='utf-8') as fh:
            fh.write(src.replace(old, new))


def run_suite(cwd):
    out = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q', '-o', 'addopts=', *SUITE,
                          '-k', 'not test_the_whole_store_suite_passes_with_dpapi_unavailable'],   # a gate, not a detector
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
