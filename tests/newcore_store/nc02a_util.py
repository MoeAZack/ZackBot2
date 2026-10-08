"""Shared helpers for the NC-02a store tests."""
import hashlib
import json
import os
import sys
import stat

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, SCENARIO
from nc02a_memfs import MemFs
from newcore.store import create_journal, recover_journal
from newcore.store.cipher import InsecureTestCipher
from newcore.store.envelope import read_envelope
from newcore.store.frame import FILE_HEADER, KIND_SEGMENT, RT_EVENT, RT_HEADER, file_header, frame, scan
from newcore.store.header import canonical_json

MEM_ROOT = os.path.join(os.path.abspath(os.sep), 'nc02a-mem')
ACCT_DIR = os.path.join(MEM_ROOT, 'accounts', ACCOUNT_ID)
TEST_CIPHER = InsecureTestCipher(test_only=True)          # in-memory drills only; real-FS tests: real_fs_cipher()


def dpapi_available():
    """DPAPI exists here (Windows) and is not switched off by NC02B_NO_DPAPI=1 (the Linux CI simulation)."""
    return sys.platform == 'win32' and not os.environ.get('NC02B_NO_DPAPI')


def real_fs_cipher():
    """The cipher a REAL-file-system test passes: None (= the production default, DPAPI) where DPAPI exists, else
    the portable test cipher - off Windows the production default fails closed (CipherUnavailable), by design."""
    return None if dpapi_available() else TEST_CIPHER


def opened(fs, ref, acct_dir=ACCT_DIR):
    """(metadata, plaintext) of the evidence envelope ref, verified with the test cipher."""
    return read_envelope(fs.read_bytes(os.path.join(acct_dir, *ref.name.split('/'))), TEST_CIPHER, ACCOUNT_ID)


def seg(n, acct_dir=ACCT_DIR):
    return os.path.join(acct_dir, 'journal', f'seg-{n:06d}.seg')


def mem_journal(events=SCENARIO, fs=None):
    """A MemFs holding a journal with `events` appended; the journal handle is closed."""
    fs = fs or MemFs(os.path.dirname(ACCT_DIR))
    j = create_journal(ACCT_DIR, ACCOUNT_ID, AGGREGATE_ID, fs=fs, cipher=TEST_CIPHER)
    for e in events:
        j.append(e)
    j.close()
    return fs


def recover(fs, acct_dir=ACCT_DIR):
    return recover_journal(acct_dir, ACCOUNT_ID, AGGREGATE_ID, fs=fs, cipher=TEST_CIPHER)


def records(data):
    return scan(data, KIND_SEGMENT).records


def rebuild(data, *, header_doc=None, payloads=None, file_hdr=None):
    """Re-frame a segment with valid CRCs: replace the header document and / or the event payloads."""
    recs = records(data)
    doc = json.loads(recs[0].payload) if header_doc is None else header_doc
    hdr_payload = canonical_json(doc) if isinstance(doc, dict) else doc
    evs = [r.payload for r in recs[1:]] if payloads is None else payloads
    out = (file_hdr if file_hdr is not None else data[:FILE_HEADER.size]) + frame(RT_HEADER, hdr_payload)
    return out + b''.join(frame(RT_EVENT, p) for p in evs)


def header_doc(data):
    return json.loads(records(data)[0].payload)


def tree(root):
    """rel path -> ('f', sha256, size, mtime_ns) | ('d',): bytes, mtimes and the listing, read straight from disk."""
    out = {}
    for dp, dns, fns in os.walk(root):
        for n in dns + fns:
            p = os.path.join(dp, n)
            st = os.lstat(p)
            rel = os.path.relpath(p, root).replace(os.sep, '/')
            if stat.S_ISDIR(st.st_mode):
                out[rel] = ('d',)
            elif n == ".lock":                                   # byte-range locked by a live writer: metadata only
                out[rel] = ("lock", st.st_size)
            else:
                with open(p, 'rb') as fh:
                    out[rel] = ('f', hashlib.sha256(fh.read()).hexdigest(), st.st_size, st.st_mtime_ns)
    return out


__all__ = ['MEM_ROOT', 'ACCT_DIR', 'TEST_CIPHER', 'dpapi_available', 'real_fs_cipher', 'opened', 'seg', 'mem_journal', 'recover', 'records', 'rebuild', 'header_doc', 'tree',
           'file_header', 'frame', 'KIND_SEGMENT', 'RT_EVENT', 'RT_HEADER']
