"""UI-only rerun under requirements-ui.txt's pinned Playwright; no gate result replacement."""
import os
import pathlib
import shutil
import sys

root = pathlib.Path(r'F:\ZackBot2_data\CLOCK-1021-REVIEW-20261113')
name = sys.argv[1]
assert name in ('head_r1', 'base')
repo = root / name
os.environ['LOCALAPPDATA'] = str(root / (name + '_ui_retry_appdata'))
os.environ['APPDATA'] = str(root / (name + '_ui_retry_roaming'))
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = r'C:\Users\princ\AppData\Local\ZackBot\ms-playwright'
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0,str(repo))
os.chdir(repo)
import verify as V

out = root / (name + '_ui_retry')
seeds = out / 'replay_replay1'
seeds.mkdir(parents=True,exist_ok=True)
for source in (root / (name + '_full') / 'replay_replay1').glob('seed_*.json'):
    shutil.copy2(source,seeds / source.name)
assert (seeds / 'seed_history.json').exists()
rep = V.Report('ui-retry',str(out))
rep.data['git'] = V.git_info()
rep.data['dependencies'] = V.dependency_versions()
rep.data['scope'] = 'UI-only environment retest, not a full-gate PASS or replacement of original summary'
V.ui_step(rep)
raise SystemExit(rep.finish())
