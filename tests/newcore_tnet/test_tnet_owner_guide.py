"""The owner guide's first run (Codex first-run ruling): probes + 5a ONLY. Every id in the 5a command must be a
testnet spec with management OFF; T04-algo and the management brackets are not in it; the cleanup recipe and the
key advice are present. A spec or guide drift fails here, not on the owner's machine."""
import os
import re

from newcore.tnet.rspec import bundled

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNNER = 'python tools' + chr(92) + 'newcore_tnet_runner.py'
GUIDE = open(os.path.join(REPO, 'docs', 'newcore', 'tnet', 'OWNER_FIRST_RUN.md'), encoding='utf-8').read()


def first_run_ids():
    sec = GUIDE.split('**5a.', 1)[1].split('**Optional', 1)[0]
    cmds = [ln for ln in sec.splitlines() if ln.startswith(RUNNER)]
    assert len(cmds) == 1
    return re.findall(r'--only (\S+)', cmds[0])


def test_the_first_run_is_testnet_scenarios_with_management_off():
    specs = {s['id']: s for s in bundled()}
    ids = first_run_ids()
    assert ids and len(set(ids)) == len(ids)
    for i in ids:
        s = specs[i]
        assert 'testnet' in s['targets'], i
        m = s.get('management')
        assert not (m and m.get('enabled', True)), i
    assert 'T04-algo' not in ids and not any(i.startswith(('T05', 'T06', 'T07', 'T08')) for i in ids)


def test_the_guide_keeps_the_first_run_rules_the_cleanup_recipe_and_the_key_advice():
    assert 'Withdrawals must stay disabled' in GUIDE
    assert '--cleanup --close-positions --adopt-foreign BTCUSDT:LONG:0.002' in GUIDE
    assert 'REC-02 is **not used**' in GUIDE and '5b) are **deferred**' in GUIDE
    assert RUNNER + ' --target testnet --only T04-algo' in GUIDE
