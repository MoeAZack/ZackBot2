"""REC-02 test plumbing: the slice helpers (World, FakeVenue builders) and the step-0 port helpers are importable."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.dirname(HERE)
for sub in ('newcore_slice', 'newcore_ports'):
    p = os.path.join(TESTS, sub)
    if p not in sys.path:
        sys.path.insert(0, p)
