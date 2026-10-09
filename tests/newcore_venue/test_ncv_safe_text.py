"""Codex (nc-tnet01-next): no raw exception text reaches a report, an incident, a cassette or console output. A raised
exception whose message carries a URL / header / key fragment shows only its type, an errno and a bounded ref."""
import glob
import importlib
import importlib.util
import io
import os
import re

import pytest

from fake_binance import FakeBinance
from test_ncv_tnet_harness import env, run, tool  # noqa: F401  (env is a fixture)

from newcore.venue.safe_text import OWN_CLIENT_ID, SAFE_MESSAGES, exc_msg, exc_ref, exc_text

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SECRET = ('https://testnet.binancefuture.com/fapi/v1/order?symbol=BTCUSDT&signature=' + 'f' * 64
          + ' X-MBX-APIKEY: ' + 'K' * 64 + ' C:/Users/someone/secret-path')
FRAGMENTS = ('signature=', 'X-MBX-APIKEY', 'K' * 12, 'f' * 12, 'binancefuture', 'secret-path')


class Boom(Exception):
    pass


def clean(text):
    return not any(f in text for f in FRAGMENTS)


# ---------------------------------------------------------------------------------------------- the helper
def test_an_unlisted_exception_keeps_only_its_type_and_a_bounded_ref():
    t = exc_text(Boom(SECRET))
    assert clean(t) and t.startswith('Boom (detail withheld, ref ') and re.search(r'ref [0-9a-f]{10}\)$', t)
    assert exc_ref(Boom(SECRET)) == exc_ref(Boom(SECRET)) != exc_ref(Boom('other'))     # stable, distinguishing


DICTIONARY = ('hunter2', 'password', 'BTCUSDT', 'C:/Users/someone', 'abc', 'x' * 8, '', 'secret', 'letmein')


def _dictionary_hits(tag, cls):
    """The dictionary words whose UNSALTED hash (sha256 / sha1 / md5 / sha512, any of the type + message forms an
    attacker with the source would try, prefix or suffix) reproduces `tag`."""
    import hashlib
    hits = []
    for w in DICTIONARY:
        forms = (f'{cls.__name__}\x00{w}', f'{cls.__qualname__}\x00{w}', f'{cls.__qualname__}\x00{(w,)!r}', w)
        for f in forms:
            for algo in ('sha256', 'sha1', 'md5', 'sha512'):
                h = hashlib.new(algo, f.encode('utf-8')).hexdigest()
                if tag in (h[:len(tag)], h[-len(tag):]):
                    hits.append(w)
    return sorted(set(hits))


def test_codex_6069136564_a_dictionary_of_unsalted_hashes_cannot_reproduce_a_tag():
    """The old ref was the first 8 hex of an UNSALTED SHA-256(type + message): a low-entropy message (a password, a
    path, a symbol) came back by dictionary. The tag is an HMAC under a per-process secret salt now."""
    for word in DICTIONARY:
        tag = exc_ref(Boom(word))
        assert re.fullmatch(r'[0-9a-f]{10}', tag) and _dictionary_hits(tag, Boom) == []


def test_the_dictionary_check_does_catch_the_old_unsalted_ref():
    import hashlib
    old = hashlib.sha256('Boom\x00hunter2'.encode()).hexdigest()[:8]          # the scheme before 6069136564
    assert _dictionary_hits(old, Boom) == ['hunter2']


def test_codex_6069136564_the_same_error_correlates_within_a_process_only():
    assert exc_ref(Boom('hunter2')) == exc_ref(Boom('hunter2')) != exc_ref(Boom('hunter3'))
    assert exc_ref(Boom('hunter2')) != exc_ref(ValueError('hunter2'))                     # the type is in the body
    import subprocess
    import sys
    code = ('import sys; sys.path.insert(0, sys.argv[1]); from newcore.venue.safe_text import exc_ref; '
            'print(exc_ref(ValueError("hunter2")))')
    tags = {subprocess.run([sys.executable, '-c', code, REPO], capture_output=True, text=True, check=True).stdout.strip()
            for _ in range(2)}
    assert len(tags) == 2 and exc_ref(ValueError('hunter2')) not in tags             # a new salt per process


def test_codex_6069136564_the_salt_is_never_written(env, monkeypatch):  # noqa: F811
    """The salt never leaves the process: not in a tag, not in the console, not in any file a run writes (report,
    cassette), in hex or in raw bytes."""
    import newcore.venue.safe_text as ST
    salt = ST._SALT
    assert salt.hex() not in exc_text(Boom(SECRET)) and salt.hex() not in exc_msg(Boom(SECRET))
    m = tool()

    def refuse(**kw):
        raise OSError(13, 'Permission denied', SECRET)
    monkeypatch.setattr(m, 'tnet_report', refuse)
    rc, out = run(env, ['--probe', 'P1'], http=FakeBinance(), mod=m)
    assert rc == 5 and 'detail withheld, ref ' in out and salt.hex() not in out
    for p in glob.glob(os.path.join(str(env['tmp']), '**', '*'), recursive=True):
        if os.path.isfile(p):
            data = open(p, 'rb').read()
            assert salt not in data and salt.hex().encode() not in data, p


def test_an_oserror_shows_its_errno_never_its_path():
    t = exc_text(OSError(13, 'Permission denied', SECRET))
    assert clean(t) and t.startswith('PermissionError errno 13 (detail withheld')   # OSError(13) is a PermissionError


def test_a_listed_class_keeps_its_message_scrubbed_and_capped_but_a_subclass_does_not():
    from newcore.tnet.seams import BoundExceeded
    assert exc_text(BoundExceeded('more than 2 cycles')) == 'BoundExceeded: more than 2 cycles'
    long = exc_text(BoundExceeded('x ' + 'K' * 64 + ' ' * 300))
    assert 'K' * 12 not in long and len(long) <= 200                                    # token-scrubbed, capped

    class Sub(BoundExceeded):
        pass
    assert clean(exc_text(Sub(SECRET))) and exc_text(Sub(SECRET)).startswith('Sub (detail withheld')


def test_exc_msg_is_the_message_alone_and_our_client_ids_survive_the_scrub():
    from newcore.tnet.seams import BoundExceeded
    from newcore.venue.tnet import NEWCORE_CID_RE
    assert OWN_CLIENT_ID.pattern == NEWCORE_CID_RE.pattern                     # one client-id grammar
    cid = 'zbn1o-' + 'a' * 26
    msg = f'submit_market {cid}: more than 2 orders'
    assert exc_msg(BoundExceeded(msg)) == msg                                  # the owner needs our client ids
    assert exc_msg(BoundExceeded('x ' + 'K' * 40)) == 'x <redacted>'            # any other long run is scrubbed
    assert exc_msg(Boom(SECRET)) == exc_text(Boom(SECRET))                     # unlisted: the same template


def test_a_broken_str_is_still_typed():
    class Bad(Exception):
        def __str__(self):
            raise RuntimeError(SECRET)
    assert exc_text(Bad()).startswith('Bad (detail withheld')


@pytest.mark.parametrize('mod, name', sorted(SAFE_MESSAGES))
def test_every_listed_class_exists(mod, name):
    cls = getattr(importlib.import_module(mod), name)
    assert isinstance(cls, type) and issubclass(cls, BaseException) and cls.__module__ == mod   # harness stops are
                                                                                                  # BaseExceptions


# ---------------------------------------------------------------------------------------------- no raw forms left
RAW = re.compile(r'(str|repr)\(ex\)|\{ex(![rsa])?\}|\.scrub\(ex\)|\bex\.args\b|format\(ex\)')


def test_no_raw_exception_text_in_the_venue_lane_the_harness_or_the_tools():
    hits = []
    for pat in ('newcore/venue/*.py', 'newcore/tnet/*.py', 'tools/*.py'):
        for path in glob.glob(os.path.join(REPO, *pat.split('/'))):
            if path.endswith('safe_text.py'):
                continue
            for n, line in enumerate(open(path, encoding='utf-8'), 1):
                if RAW.search(line):
                    hits.append(f'{os.path.relpath(path, REPO)}:{n}: {line.strip()}')
    assert hits == []


# ---------------------------------------------------------------------------------------------- end to end
def test_runner_a_scenario_that_raises_a_secret_shaped_error_reports_type_and_ref_only(tmp_path, monkeypatch):
    import newcore.tnet.driver as D

    def boom(self, st):
        raise Boom(SECRET)
    monkeypatch.setattr(D._Run, 'step', boom)
    sp = importlib.util.spec_from_file_location('cli_safe', os.path.join(REPO, 'tools', 'newcore_tnet_runner.py'))
    cli = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(cli)
    out = io.StringIO()
    rc = cli.main(['--target', 'fake', '--only', 'T01-long', '--report-dir', str(tmp_path), '-v'], out=out)
    text = out.getvalue()
    assert rc == 7 and clean(text), text
    reports = [open(p, encoding='utf-8').read() for p in glob.glob(str(tmp_path / '*'))]
    assert reports and all(clean(r) for r in reports)
    assert any('Boom (detail withheld, ref ' in r for r in reports)


def test_venue_cli_a_report_write_error_with_a_secret_path_prints_type_and_errno_only(env, monkeypatch):  # noqa: F811
    m = tool()

    def refuse(**kw):
        raise OSError(13, 'Permission denied', SECRET)
    monkeypatch.setattr(m, 'tnet_report', refuse)
    rc, out = run(env, ['--probe', 'P1'], http=FakeBinance(), mod=m)
    assert rc == 5 and 'report not written (PermissionError errno 13 (detail withheld, ref ' in out and clean(out), out
