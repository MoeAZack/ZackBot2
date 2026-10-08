"""#44's gate: pytest put realized synthetic secrets into parametrized node ids, which land in the JUnit artifact the
CI secret scan reads. Every secret-shaped parameter in tests/newcore_venue and tests/newcore_tnet has an explicit
safe id; this collects both suites (--collect-only, nothing runs) and asserts no node id carries one.

Secret-shaped: a known synthetic key / secret (in any case, split by separators, percent-encoded or full-width) or a
long base64 / hex-looking run (24+ characters of [A-Za-z0-9+=] mixing letters and digits)."""
import codecs
import os
import re
import subprocess
import sys
import unicodedata
import urllib.parse

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ncv_support import DUMMY_KEY, DUMMY_SECRET  # noqa: E402

KNOWN = (
    DUMMY_KEY, DUMMY_SECRET,
    'REPLAYDUMMYKEY' + 'r' * 50, 'REPLAYDUMMYSECRET' + 'q' * 47,          # newcore.venue.scenarios
    'REPLAYKEY' + 'z' * 55, 'REPLAYSECRET' + 'y' * 52,                    # test_ncv_cassette
    'Ab+Cd/Ef' * 8, 'AbCdEfGh' * 8,                                         # test_ncv_redact / _cassette_leaks
    'dbefbc809e3e83c283a984c3a1459732ea7db1360ca80c5c2c8867408d28cc83',     # Binance documentation example key
    '2b5eb11e18796d12d88f13dc27dbbd02c2cc51ff7059765ed9821957d82bb4d9',     # Binance documentation example secret
)
RUN = re.compile(r'[A-Za-z0-9+=]{24,}')          # '/' excluded: URL paths; known keys with '/' match by window
WINDOW = 12


def _norm(text):
    return re.sub(r'[^a-z0-9]', '', text.lower())


KNOWN_WINDOWS = {w for k in KNOWN for n in [_norm(k)] for w in (n[i:i + WINDOW] for i in range(len(n) - WINDOW + 1))
                 if len(set(w)) > 2}                                     # 'rrrrrrrrrrrr' alone is not the key


def decoded_forms(param):
    forms = [param, urllib.parse.unquote(param)]
    try:
        forms.append(codecs.decode(param, 'unicode_escape'))           # pytest escapes non-ASCII as \\uXXXX
    except (UnicodeDecodeError, ValueError):
        pass
    return [unicodedata.normalize('NFKC', f) for f in forms]


def secret_shaped(param):
    for f in decoded_forms(param):
        if any(re.search(r'[A-Za-z]', m) and re.search(r'[0-9]', m) for m in RUN.findall(f)):
            return 'long mixed token run'
        n = _norm(f)
        if any(w in n for w in KNOWN_WINDOWS):
            return 'a known synthetic secret'
    return None


def offenders(node_ids):
    out = []
    for nid in node_ids:
        if '[' in nid and nid.endswith(']'):
            why = secret_shaped(nid.split('[', 1)[1][:-1])
            if why:
                out.append(f'{why}: {nid[:160]}')
    return out


def test_the_detector_catches_the_shapes_that_failed_44():
    assert secret_shaped(KNOWN[0]) and secret_shaped(KNOWN[1].upper()) and secret_shaped('DUMM-YKEY-zzLE-AKCH-ECKq-q012')
    assert secret_shaped('Ab%2BCd%2FEfAb%2BCd%2FEfAb%2BCd%2FEf') and secret_shaped('8c1d2e3f4a5b4c6d8e7f90a1b2c3d4e5')
    assert secret_shaped('\\uff24\\uff35\\uff2d\\uff2d\\uff39\\uff2b\\uff25\\uff39zzLEAKCHECKqq')    # full-width escape
    assert not secret_shaped('KeyboardInterrupt-cassette') and not secret_shaped('https://fapi.binance.com/fapi/v2/x')
    assert not secret_shaped('newcore.venue.credentials-MainnetCredentialRefused')


def test_no_parametrized_node_id_carries_a_secret_shaped_value():
    dirs = [d for d in ('tests/newcore_venue', 'tests/newcore_tnet') if os.path.isdir(os.path.join(REPO, d))]
    r = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '--collect-only', '-q', *dirs],
                       cwd=REPO, capture_output=True, text=True, encoding='utf-8', errors='backslashreplace',
                       timeout=600)
    ids = [ln.strip() for ln in r.stdout.splitlines() if '::' in ln]
    assert len(ids) > 1000, r.stdout[-2000:] + r.stderr[-2000:]
    assert offenders(ids) == []
