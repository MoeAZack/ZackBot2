"""Partial-clone transport fallback; unchanged LONGSCOUT V2 main/worker.

git archive prefetches unrelated large blobs in this partial clone. Read only
the SAME archive path list via cat-file instead. Validate every blob ID before
writing; patch only parent RS.export, never signal/execution/gate functions.
Usage and arguments: exactly longscout_v2_run.py. LONGSCOUT_CASES must be set.
"""
import hashlib
import json
import subprocess
from pathlib import Path
import longscout_v2_run as R


def export(name, out):
    spec = R.RS.CONTRACT['sources'][name]
    listing = subprocess.check_output(['git', '-C', R.RS.ROOT, 'ls-tree', '-r', spec['commit']], text=True)
    wanted = []
    for line in listing.splitlines():
        meta, path = line.split('\t', 1)
        if ((path.endswith('.py') and ('/' not in path or
                (path.startswith('research/') and path.count('/') == 1))) or
                path == 'data/exchange_rules_testnet.json'):
            wanted.append((path, meta.split()[2]))
    raw = subprocess.check_output(['git', '-C', R.RS.ROOT, 'cat-file', '--batch'],
                                 input=''.join(oid+'\n' for _,oid in wanted).encode())
    pos = 0; receipt = {}; dst = Path(out)/'_src'/name
    for path, oid in wanted:
        end = raw.index(b'\n', pos); header = raw[pos:end].decode().split()
        assert header[0] == oid and header[1] == 'blob', header
        size = int(header[2]); content = raw[end+1:end+1+size]; pos = end+size+2
        assert hashlib.sha1(b'blob '+str(size).encode()+b'\0'+content).hexdigest() == oid
        target = dst/path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(content)
        receipt[path] = dict(git_blob=oid, sha256=hashlib.sha256(content).hexdigest())
    assert pos == len(raw)
    (Path(out)/'transport_receipt.json').write_text(json.dumps(dict(commit=spec['commit'],files=receipt),indent=2))
    return str(dst)


if __name__ == '__main__':
    R.RS.export = export
    R.main()
