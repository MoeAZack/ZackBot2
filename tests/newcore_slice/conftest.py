"""Slice test plumbing.

- tests/newcore_ports is importable (the step-0 contract helpers).
- `journal_kind`: run a module against BOTH journals, MemoryJournal and the NC-02a FileJournal on the real file system
  (pytestmark = pytest.mark.usefixtures('journal_kind') in the crash-matrix modules).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'newcore_ports'))


@pytest.fixture(params=['memory', 'file'])
def journal_kind(request, tmp_path):
    import slice_helpers
    base = tmp_path / 'accounts'
    base.mkdir()
    old = dict(slice_helpers.JOURNAL)
    slice_helpers.JOURNAL.update(kind=request.param, base=str(base))
    yield request.param
    slice_helpers.JOURNAL.update(old)
