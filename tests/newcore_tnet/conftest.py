"""TNET runner-harness test plumbing: tests/newcore_venue is importable (the stateful fake Binance + dummy keys).
No network, no real keys: the testnet target always gets a fake `http` and a fake credential store."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'newcore_venue'))
