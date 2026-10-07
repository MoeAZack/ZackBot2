"""zb-golden/1: engine-agnostic golden cases for the legacy engine, the legacy backtester and (later) NEWCORE.

Design: AUD-08 golden-case design (section 2 fixture format, section 6 correction ledger, section 7 runner).
Inputs are neutral, outputs are canonical: a case never names engine.py fields, sleeve keys or backtester columns; each
adapter translates the neutral input into its own configuration and its own output into canonical records.
Stdlib + numpy/pandas only (no new dependency).
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_DIR = os.path.dirname(HERE)
CASES_DIR = os.path.join(GOLDEN_DIR, 'cases')
MANIFEST_PATH = os.path.join(GOLDEN_DIR, 'MANIFEST.json')
LEDGER_PATH = os.path.join(GOLDEN_DIR, 'CORRECTIONS.json')
REPO_ROOT = os.path.dirname(os.path.dirname(GOLDEN_DIR))
