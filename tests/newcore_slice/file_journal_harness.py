"""The NC-02a FileJournal behind the slice harness: the same fault hooks as MemoryJournal, on the real file system.

FileJournalProxy wraps newcore.store.FileJournal (JournalPort: append / last_sequence / read / find_decision / gate)
and adds what the slice tests drive:
  fail_writes(n, after=k, error=None)  after k more event appends, the next n fail: error None = the store is down
                                       (every write / fsync / mkdir raises ENOSPC, so FileJournal raises
                                       DurabilityUnavailable, a JournalUnavailable); error=Crash = the process dies at
                                       the C-E1 boundary (before the frame is written)
  fail_next_write()                    one store failure
  reopen()                             a process restart: recover_journal from the files (CLEAN / REPAIRED expected)
The seam is FileJournal._fs (the store's own contract test wraps it the same way).
"""
import os

from newcore.store import Verdict, create_journal, recover_journal

ENOSPC = 28


class FaultFs:
    def __init__(self, inner):
        self.inner = inner
        self.down = False             # the store cannot write at all
        self._fail = 0
        self._after = 0
        self._error = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def arm(self, n, after, error):
        self._fail, self._after, self._error = n, after, error

    def mark(self, label):
        if label == 'C-E1' and self._fail > 0:                     # one event append is about to be written
            if self._after > 0:
                self._after -= 1
            else:
                self._fail -= 1
                if self._error is not None:
                    raise self._error(f'crash at {label}')
                self.down = True
        return self.inner.mark(label)

    def _io(self, name, *a):
        if self.down:
            raise OSError(ENOSPC, f'injected: no space left on device ({name})')
        return getattr(self.inner, name)(*a)

    def write(self, h, data):
        return self._io('write', h, data)

    def fsync(self, h):
        return self._io('fsync', h)

    def fsync_dir(self, p):
        return self._io('fsync_dir', p)

    def mkdir(self, p):
        return self._io('mkdir', p)

    def open_new(self, p):
        return self._io('open_new', p)


class FileJournalProxy:
    def __init__(self, journal, account_id, aggregate_id):
        self._j = journal
        self.account_id, self.aggregate_id = account_id, aggregate_id
        self.fs = FaultFs(journal._fs)
        journal._fs = self.fs

    @classmethod
    def create(cls, account_dir, account_id, aggregate_id):
        return cls(create_journal(account_dir, account_id, aggregate_id), account_id, aggregate_id)

    # JournalPort
    def append(self, event):
        return self._j.append(event)

    def last_sequence(self):
        return self._j.last_sequence()

    def read(self, after_sequence=0):
        return self._j.read(after_sequence)

    def find_decision(self, decision_id):
        return self._j.find_decision(decision_id)

    def gate(self):
        return self._j.gate()

    # faults / restart
    def fail_writes(self, n=1, *, after=0, error=None):
        self.fs.arm(n, after, error)

    def fail_next_write(self):
        self.fail_writes(1)

    def reopen(self):
        self._j.close()
        r = recover_journal(self._j.account_dir, self.account_id, self.aggregate_id)
        assert r.verdict in (Verdict.CLEAN, Verdict.REPAIRED), (r.verdict, r.findings)
        assert list(r.journal.read()) == list(self._j.read())       # files == in-process state, always
        return FileJournalProxy(r.journal, self.account_id, self.aggregate_id)


def new_account_dir(base):
    n = len(os.listdir(base))
    d = os.path.join(base, f'acct{n:04d}')
    return d
