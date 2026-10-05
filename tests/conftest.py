"""Every test runs against a throw-away data folder - never the real %LOCALAPPDATA%\\ZackBot (state, keys, logs)."""
import os, sys, tempfile
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp(prefix='zb_test_')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
