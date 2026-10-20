import json
import os
import re
import shutil
import subprocess

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _panel():
    return open(os.path.join(ROOT, 'panel.html'), encoding='utf-8').read()


def test_dashboard_history_behavior_in_node():
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    src = _panel()
    fn = re.search(r'function dashboardHistory\(d,h\)\{[^\n]+\}', src)
    assert fn, 'dashboardHistory helper missing'
    js = fn.group(0) + r'''
const history = [
  {cohort_id:'old', pnl:-9, fees:2},
  {cohort_id:'active', pnl:3, fees:1},
  {cohort_id:'active', pnl:-1, fees:.5}
];
const active = dashboardHistory({book_ledger:{cohort_id:'active'}}, history);
const fallback = dashboardHistory({}, history);
console.log(JSON.stringify({
  activeCid: active.cid,
  activeN: active.history.length,
  activeNet: active.history.reduce((n,x)=>n+x.pnl,0),
  activeFees: active.history.reduce((n,x)=>n+x.fees,0),
  activeOnly: active.history.every(x=>x.cohort_id==='active'),
  fallbackCid: fallback.cid,
  fallbackN: fallback.history.length,
  fallbackSame: fallback.history===history
}));
'''
    run = subprocess.run([node, '-e', js], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {
        'activeCid': 'active', 'activeN': 2, 'activeNet': 2, 'activeFees': 1.5,
        'activeOnly': True, 'fallbackCid': None, 'fallbackN': 3, 'fallbackSame': True,
    }


def test_dashboard_labels_scope_but_trades_remains_all_time():
    src = _panel()
    dashboard = src[src.index('function renderDash()'):src.index('function renderTrades()')]
    trades = src[src.index('function renderTrades()'):src.index('function renderClosedTable()')]

    assert "cid?'Current cohort P&L':'Net P&L (closed)'" in dashboard
    assert 'All-time results remain in Trades.' in dashboard
    assert 'dashboardHistory(D,CH.history)' in dashboard
    assert 'closedFiltered' in trades
    assert 'dashboardHistory' not in trades
