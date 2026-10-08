"""Slice tests reuse the step-0 event builders (nc_events, journal_contract) from tests/newcore_ports."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'newcore_ports'))
