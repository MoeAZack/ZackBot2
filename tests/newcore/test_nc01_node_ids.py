"""#44 full gate: no test node id may carry a secret-shaped value.

pytest writes parametrized values into node ids, and node ids into the JUnit report the CI secret scan reads. A
synthetic credential assembled at run time (so the SOURCE scan passes) still lands in the artifact unless its param has
an explicit safe id. This collects the whole suite's node ids and scans them with the same shapes verify.py uses, plus
the common token prefixes."""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SHAPES = [
    ('key-like 64-char string', re.compile(r'(?<![A-Za-z0-9])[A-Za-z0-9]{64}(?![A-Za-z0-9])')),
    ('Telegram bot token', re.compile(r'(?<![0-9])[0-9]{8,10}:AA[A-Za-z0-9_-]{20,}')),
    ('private key block', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')),
    ('api key assignment', re.compile(r'(?i)api[_-]?key\s*[=:]\s*\S{12,}')),
    ('bearer token', re.compile(r'(?i)bearer\s+[A-Za-z0-9._~+/-]{12,}')),
    ('sk- / ghp_ token', re.compile(r'(?<![A-Za-z0-9])(?:sk-|ghp_|xox[bp]-)[A-Za-z0-9_-]{8,}')),
    ('base64 blob', re.compile(r'(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{24,}={1,2}')),
]


def _hits(line):
    out = []
    for name, rx in SHAPES:
        for m in rx.finditer(line):
            v = m.group(0)
            if name.startswith('key-like') and (re.fullmatch(r'[0-9a-fA-F]{64}', v) or len(set(v)) <= 4):
                continue                                        # a hash / a padding run, as verify.py exempts
            out.append(name)
    return out


def test_the_scan_catches_what_it_must():
    shaped = ['t[binance apiKey=' + 'vmPUZE6mv9SD5VNHk4HlWF' + 'sOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A]',
              't[telegram ' + '123456789:AA' + 'HdqTcvCH1vGWJxfSeofSAs0K5PALDsaw0]',
              't[secret: ' + 'c2VjcmV0LXZhbHVl' + 'LXRoYXQtaXMtbG9uZw==]', 't[' + 'sk-' + 'live0123456789ab]']
    assert all(_hits(s) for s in shaped)
    assert not _hits('tests/newcore/test_x.py::test_a_long_descriptive_test_name_with_many_words[binance_apikey_shape]')


def test_no_collected_node_id_carries_a_secret_shaped_value():
    r = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider', 'tests'],
                       cwd=ROOT, capture_output=True, text=True, timeout=600)
    ids = [ln for ln in r.stdout.splitlines() if '::' in ln]
    assert len(ids) > 1000, r.stdout[-2000:] + r.stderr[-2000:]
    bad = [(ln.split('[', 1)[0], _hits(ln)) for ln in ids if _hits(ln)]   # never print the matched value itself
    assert bad == []
