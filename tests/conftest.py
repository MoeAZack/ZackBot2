"""Every test runs against a throw-away data folder - never the real %LOCALAPPDATA%\\ZackBot (state, keys, logs)."""
import os, sys, tempfile
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp(prefix='zb_test_')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_configure(config):
    config.addinivalue_line('markers', 'slow: long engine replays - skipped by the installer (-m "not slow"), run by run_checks')


import pytest                                                            # noqa: E402


@pytest.fixture(autouse=True)
def _close_fill_writers():
    """T05: every test leaves no telemetry writer thread behind."""
    yield
    try:
        import engine
        engine.close_fill_writers(2.0)
    except Exception:
        pass
