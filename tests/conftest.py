"""Every test runs against a throw-away data folder - never the real %LOCALAPPDATA%\\ZackBot (state, keys, logs)."""
import logging, os, sys, tempfile
# The real default location is remembered (read-only use: the log-isolation regression proves it is never written), then
# LOCALAPPDATA - the setting app.data_dir() honours - is pointed at a per-run temp root BEFORE anything imports app or
# engine. Subprocess tests inherit it (or pass their own temp LOCALAPPDATA), so a child never resolves the real folder.
os.environ.setdefault('ZB_REAL_LOCALAPPDATA', os.environ.get('LOCALAPPDATA', ''))
TEST_ROOT = tempfile.mkdtemp(prefix='zb_test_')
os.environ['LOCALAPPDATA'] = TEST_ROOT
os.environ['ZB_TEST_ROOT'] = TEST_ROOT
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_configure(config):
    config.addinivalue_line('markers', 'slow: long engine replays - skipped by the installer (-m "not slow"), run by run_checks')


import pytest                                                            # noqa: E402

_TMP_AREAS = tuple(os.path.normcase(os.path.realpath(p)) for p in {TEST_ROOT, tempfile.gettempdir()})


def _under(path, roots):
    p = os.path.normcase(os.path.realpath(path))
    return any(p == r or p.startswith(r.rstrip('\\/') + os.sep) for r in roots)


def file_handlers():
    """(logger name, handler) for every logging FileHandler attached anywhere in this process."""
    out = [('<root>', h) for h in logging.getLogger().handlers]
    out += [(n, h) for n, lg in list(logging.Logger.manager.loggerDict.items()) if isinstance(lg, logging.Logger) for h in lg.handlers]
    return [(n, h) for n, h in out if isinstance(h, logging.FileHandler)
            and not type(h).__module__.startswith('_pytest')]          # pytest's own --log-file sink (os.devnull by default)


@pytest.fixture(autouse=True)
def _no_log_file_outside_test_tmp():
    """No test may leave a logging FileHandler whose file lies outside the test temp area (the 2026-10-08 bot.log leak):
    an offender is detached + closed (so later tests cannot append through it) and the test fails."""
    yield
    bad = []
    for name, h in file_handlers():
        if not _under(h.baseFilename, _TMP_AREAS):
            bad.append(f'{name}: {h.baseFilename}')
            for lg in [logging.getLogger()] + [x for x in logging.Logger.manager.loggerDict.values() if isinstance(x, logging.Logger)]:
                if h in lg.handlers: lg.removeHandler(h)
            try: h.close()
            except Exception: pass
    assert not bad, f'logging FileHandler outside the test temp area (detached): {bad}'


@pytest.fixture(autouse=True)
def _close_fill_writers():
    """T05: every test leaves no telemetry writer thread behind."""
    yield
    try:
        import engine
        engine.close_fill_writers(2.0)
    except Exception:
        pass
