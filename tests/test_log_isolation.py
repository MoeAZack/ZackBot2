"""Log isolation (2026-10-08 leak): importing app must not attach bot.log anywhere; only main() (the real startup path)
configures logging, exactly once, in the configured data folder. Test-shaped lines ('FakeX', 'ENTRY BTCUSDT LONG [T] 1.0
@ 100.0') reached the RUNNING bot's real %LOCALAPPDATA%\\ZackBot\\bot.log because app.py attached a RotatingFileHandler at
import time. The real folder is only ever READ here (the installed bot appends to it on its own hourly cycle)."""
import json, logging, os, subprocess, sys, textwrap, uuid
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, 'tests')
REAL_LAD = os.environ.get('ZB_REAL_LOCALAPPDATA', '')
REAL_LOG = os.path.join(REAL_LAD, 'ZackBot', 'bot.log') if REAL_LAD else ''

# Child: given LOCALAPPDATA before importing app, builds a fake Engine (test_safety.mk_engine) and
# logs a unique marker through the engine's 'zackbot' logger; optionally runs the startup logging setup first.
CHILD = textwrap.dedent('''
    import json, logging, os, sys
    sys.path[:0] = [sys.argv[1], sys.argv[2]]
    marker, configure = sys.argv[3], sys.argv[4] == '1'
    import app
    after_import = [getattr(h, 'baseFilename', None) for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
    if configure: app.configure_logging(); app.configure_logging()        # the startup path, twice: must stay idempotent
    from test_safety import mk_engine
    e, tmp = mk_engine()
    import engine as E
    E.log.warning(marker + ' engine=' + type(e).__name__)              # through the engine's own 'zackbot' logger
    for h in logging.getLogger().handlers: h.flush()
    fh = [h for lg in [logging.getLogger()] + [x for x in logging.Logger.manager.loggerDict.values() if isinstance(x, logging.Logger)]
          for h in lg.handlers if isinstance(h, logging.FileHandler)]
    print('RESULT ' + json.dumps(dict(after_import=after_import, file_handlers=[h.baseFilename for h in fh],
                                     data=app.DATA, log_f=app.LOG_F)))
''')


def _snap(p):
    """Read-only snapshot of a log file: None when absent, else (size, mtime, bytes)."""
    if not p or not os.path.exists(p): return None
    st = os.stat(p)
    with open(p, 'rb') as f: return st.st_size, st.st_mtime, f.read()


def _run_child(tmp_path, lad, marker, configure):
    script = tmp_path / 'child.py'; script.write_text(CHILD, encoding='utf-8')
    env = dict(os.environ, LOCALAPPDATA=str(lad), PYTHONIOENCODING='utf-8')
    r = subprocess.run([sys.executable, str(script), ROOT, TESTS, marker, '1' if configure else '0'], cwd=str(tmp_path), env=env,
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-3000:]
    line = [x for x in r.stdout.splitlines() if x.startswith('RESULT ')][-1]
    return json.loads(line[7:])


def _under(p, root):
    p, root = os.path.normcase(os.path.realpath(p)), os.path.normcase(os.path.realpath(root))
    return p == root or p.startswith(root.rstrip('\\/') + os.sep)


def _real_log_clean(before, after, marker):
    """The real default log is unchanged / still absent - or, if the RUNNING bot appended its own lines meanwhile (its
    hourly cycle), the appended bytes are only an extension of the old file and carry no marker and nothing test-shaped."""
    if before is None:
        assert after is None or marker.encode() not in after[2], 'test marker reached the real default bot.log'
        return
    assert after is not None, 'the real default bot.log disappeared'
    if after[:2] == before[:2]: return
    assert after[2].startswith(before[2]) or len(after[2]) < len(before[2]), 'real bot.log rewritten (not only appended)'
    new = after[2][len(before[2]):] if after[2].startswith(before[2]) else after[2]
    for bad in (marker.encode(), b'FakeX', b'@ 100.0 stop 95.0'):
        assert bad not in new, f'test-shaped line {bad!r} appended to the real default bot.log'


def test_importing_app_attaches_no_log_file_even_without_conftest_redirect(tmp_path):
    """A harness that imports app and builds an Engine, without ever calling the startup path, writes no bot.log at all.
    The child's LOCALAPPDATA is a sentinel standing in for the real default folder (never the real one)."""
    sentinel = tmp_path / 'sentinel'; sentinel.mkdir()
    marker = f'ZB-LEAK-MARKER-{uuid.uuid4().hex}'
    res = _run_child(tmp_path, sentinel, marker, configure=False)
    assert res['after_import'] == [] and res['file_handlers'] == [], res
    assert not (sentinel / 'ZackBot' / 'bot.log').exists(), 'importing app created/appended <LOCALAPPDATA>/ZackBot/bot.log'
    for dp, _, fs in os.walk(sentinel):
        for f in fs:
            assert marker.encode() not in open(os.path.join(dp, f), 'rb').read(), f'marker leaked into {f}'


def test_isolated_child_logs_only_to_its_temp_root_and_never_the_real_default(tmp_path):
    """Contract 4: the child gets the isolated root before importing app, runs the startup logging setup and a fake Engine.
    The marker lands only in the temp log, every FileHandler is under the temp root, the real default log is untouched."""
    lad = tmp_path / 'lad'; lad.mkdir()
    marker = f'ZB-LEAK-MARKER-{uuid.uuid4().hex}'
    before = _snap(REAL_LOG)
    res = _run_child(tmp_path, lad, marker, configure=True)
    after = _snap(REAL_LOG)
    assert res['after_import'] == [], res
    assert res['file_handlers'] and all(_under(p, lad) for p in res['file_handlers']), res
    assert len(res['file_handlers']) == 1, f'configure_logging() twice duplicated handlers: {res}'
    tlog = lad / 'ZackBot' / 'bot.log'
    assert marker in tlog.read_text(encoding='utf-8', errors='replace')
    if REAL_LOG: _real_log_clean(before, after, marker)


def test_startup_path_configures_one_rotating_scrubbed_handler_in_the_data_dir(tmp_path):
    """main() (here its --selftest branch, which runs no exchange / panel) attaches exactly one RotatingFileHandler at
    <data>/bot.log with rotation + secret scrubbing; a second configure call does not duplicate it."""
    lad = tmp_path / 'lad'; lad.mkdir()
    code = textwrap.dedent('''
        import json, logging, logging.handlers, sys
        sys.path.insert(0, sys.argv[1])
        import app
        app.selftest = lambda path: logging.getLogger('zackbot').warning('startup ok key SuperSecretApiKey123')
        app.SECRETS.add('SuperSecretApiKey123')
        sys.argv = ['app.py', '--selftest', 'x.json']; app.main(); app.configure_logging()
        logging.getLogger('zackbot').warning('second line key SuperSecretApiKey123')
        rh = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
        for h in logging.getLogger().handlers: h.flush()
        print('RESULT ' + json.dumps(dict(n=len(rh), rotating=all(isinstance(h, logging.handlers.RotatingFileHandler) for h in rh),
              path=[h.baseFilename for h in rh], maxb=[h.maxBytes for h in rh], bk=[h.backupCount for h in rh],
              scrub=[any(type(f).__name__ == 'Scrub' for f in h.filters) for h in rh], data=app.DATA, log_f=app.LOG_F)))
    ''')
    script = tmp_path / 'startup.py'; script.write_text(code, encoding='utf-8')
    r = subprocess.run([sys.executable, str(script), ROOT], cwd=str(tmp_path), env=dict(os.environ, LOCALAPPDATA=str(lad), PYTHONIOENCODING='utf-8'),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-3000:]
    res = json.loads([x for x in r.stdout.splitlines() if x.startswith('RESULT ')][-1][7:])
    want = os.path.join(str(lad), 'ZackBot', 'bot.log')
    assert res['n'] == 1 and res['rotating'] and res['scrub'] == [True], res
    assert os.path.normcase(res['path'][0]) == os.path.normcase(want) == os.path.normcase(res['log_f']), res
    assert res['maxb'] == [5_000_000] and res['bk'] == [3], res
    text = open(want, encoding='utf-8').read()
    assert 'startup ok key ***' in text and 'second line key ***' in text and 'SuperSecretApiKey123' not in text


def test_this_pytest_process_has_no_log_file_under_the_real_data_folder(request):
    """In-process guard: during this session no logging FileHandler resolves under the real %LOCALAPPDATA%\\ZackBot."""
    import app  # noqa: F401  (the import that used to attach bot.log)
    # The ROOT conftest's helper, found by file: a sub-folder conftest (tests/newcore_tnet) may own the bare name
    # 'conftest' in sys.modules in a full run.
    root = os.path.normcase(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'conftest.py'))
    file_handlers, = [m.file_handlers for m in request.config.pluginmanager.get_plugins()
                      if os.path.normcase(getattr(m, '__file__', '') or '') == root]
    if not REAL_LAD: pytest.skip('no real LOCALAPPDATA on this machine')
    real = os.path.join(REAL_LAD, 'ZackBot')
    assert not [h.baseFilename for _, h in file_handlers() if _under(h.baseFilename, real)]
    assert os.path.normcase(os.environ['LOCALAPPDATA']) != os.path.normcase(REAL_LAD)
