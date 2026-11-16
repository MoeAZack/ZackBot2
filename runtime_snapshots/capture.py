"""Read-only source capture; never imports or executes runtime code."""
import ast
import collections
import difflib
import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import tokenize
from datetime import datetime
from zoneinfo import ZoneInfo

REPO = Path(r'C:\Dev\ZackBot2_lane1_20261116')
OUT = REPO / 'runtime_snapshots'
ROOTS = {
    'acct1': Path(r'C:\Users\princ\AppData\Local\ZackBot\src'),
    'acct2': Path(r'F:\ZackBot2_data\OLD3-account2\source'),
    'acct3': Path(r'F:\ZackBot2_data\ACCOUNT3-breakeven\source'),
}
LAUNCHERS = {
    'acct2': Path(r'F:\ZackBot2_data\OLD3-account2\start_old3.py'),
    'acct3': Path(r'F:\ZackBot2_data\ACCOUNT3-breakeven\start_account3.py'),
}
SECRET = re.compile(r'(?:api[_-]?key|api[_-]?secret|token|password|passwd|private[_-]?key|account[_-]?id)', re.I)
TOKEN = re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|\d{7,}:[A-Za-z0-9_-]{25,}|-----BEGIN .*PRIVATE KEY-----)')

def sha(data):
    return hashlib.sha256(data).hexdigest()

def suspect(data):
    text = data.decode('utf-8-sig', errors='replace')
    if TOKEN.search(text):
        return True
    # Conservative: omit long high-entropy literals, including potential credentials.
    try:
        literals = [t.string for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type == tokenize.STRING]
    except (tokenize.TokenError, IndentationError):
        literals = re.findall(r'[^\s\x22\x27]{32,}', text)
    for value in literals:
        for part in re.findall(r'[A-Za-z0-9_+/=-]{32,}', value):
            counts = collections.Counter(part)
            entropy = -sum(n / len(part) * math.log2(n / len(part)) for n in counts.values())
            if entropy > 4.4:
                return True
    if re.search(r'(?:api[_-]?key|api[_-]?secret|token|password|passwd|account[_-]?id)\s*[=:]\s*[\x22\x27][A-Za-z0-9_+/-]{12,}[\x22\x27]', text, re.I):
        return True
    return False

def schema(value):
    if isinstance(value, dict):
        # Keys can themselves be identifiers; retain only ordinary setting names.
        return {k: schema(v) for k, v in value.items() if re.fullmatch(r'[A-Za-z_][A-Za-z_ -]{0,60}', k) and not SECRET.search(k)}
    if isinstance(value, list):
        return {'type': 'array', 'items': 'omitted'}
    return type(value).__name__

def main():
    OUT.mkdir(exist_ok=True)
    base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()
    manifest = {'captured_cairo': datetime.now(ZoneInfo('Africa/Cairo')).isoformat(), 'master_sha': base, 'files': [], 'excluded': [], 'config_schemas': [], 'excluded_accounts': ['acct4: active R6 canary explicitly protected by lane limits']}
    drift = []
    syntax = 0
    for account, root in ROOTS.items():
        paths = list(root.glob('*'))
        for folder in ['newcore', 'ml_shadow']:
            if (root / folder).is_dir():
                paths.extend((root / folder).rglob('*'))
        candidates = [(p, 'source/' + p.relative_to(root).as_posix()) for p in paths if p.is_file() and p.suffix in {'.py', '.html', '.js', '.css', '.bat', '.ps1'} and '__pycache__' not in p.parts]
        if account in LAUNCHERS:
            candidates.append((LAUNCHERS[account], 'launcher/' + LAUNCHERS[account].name))
        for source, relative in sorted(candidates):
            data = source.read_bytes()
            if suspect(data):
                manifest['excluded'].append({'account': account, 'path': relative, 'reason': 'conservative credential screen; bytes and hash withheld'})
                continue
            target = OUT / account / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if source.read_bytes() != data or target.read_bytes() != data:
                raise RuntimeError('Source changed during capture; discard capture')
            rel = relative.removeprefix('source/')
            master = REPO / rel
            master_data = master.read_bytes() if relative.startswith('source/') and master.is_file() else None
            status = 'identical' if master_data == data else ('modified' if master_data is not None else 'runtime-only')
            entry = {'account': account, 'path': relative, 'sha256': sha(data), 'size': len(data), 'master_relation': status}
            manifest['files'].append(entry)
            if status != 'identical':
                risk = {
                    'engine.py': 'HIGH: order/protection and strategy execution',
                    'binance_client.py': 'HIGH: exchange adapter and order semantics',
                    'app.py': 'HIGH: configuration, lifecycle and API orchestration',
                    'strategies.py': 'HIGH: signal generation and strategy economics',
                    'backtest.py': 'MEDIUM: economic evidence/parity',
                    'panel.html': 'MEDIUM: operator controls and presentation',
                    'strategy_shadow.py': 'MEDIUM: shadow research/evidence',
                }.get(source.name, 'MEDIUM: provenance/launch/UI behavior needs review')
                lines = list(difflib.ndiff((master_data or b'').decode('utf-8', errors='replace').splitlines(), data.decode('utf-8', errors='replace').splitlines()))
                added = sum(x.startswith('+ ') for x in lines)
                removed = sum(x.startswith('- ') for x in lines)
                nature = f'{status}; +{added}/-{removed} lines'
                entry['line_delta'] = {'added': added, 'removed': removed}
                drift.append(f'| {account} | `{relative}` | {nature} | {risk} |')
            if source.suffix == '.py':
                try:
                    ast.parse(data, filename=relative)
                    syntax += 1
                except SyntaxError:
                    entry['syntax'] = 'failed (capture preserves original bytes)'
        config_root = root.parent / 'runtime' / 'ZackBot' if account != 'acct1' else root.parent
        for name in ['settings.json', 'cohort.json', 'install.json']:
            source = config_root / name
            if source.is_file():
                try:
                    obj = json.loads(source.read_bytes())
                    target = OUT / account / (name + '.schema.json')
                    target.write_text(json.dumps(schema(obj), indent=2) + '\n', encoding='utf-8')
                    manifest['config_schemas'].append({'account': account, 'file': name, 'values': 'all omitted; no source hash'})
                except (ValueError, OSError):
                    manifest['config_schemas'].append({'account': account, 'file': name, 'values': 'unavailable'})
        # Secret-bearing config.env is deliberately never opened.
        master_paths = list(REPO.glob('*'))
        for folder in ['newcore', 'ml_shadow']:
            master_paths.extend((REPO / folder).rglob('*'))
        for master_path in sorted(master_paths):
            rel = master_path.relative_to(REPO)
            if master_path.is_file() and master_path.suffix in {'.py', '.html', '.js', '.css', '.bat', '.ps1'} and '__pycache__' not in rel.parts and not (root / rel).exists():
                manifest.setdefault('master_only', []).append({'account': account, 'path': rel.as_posix()})
                drift.append(f'| {account} | `source/{rel.as_posix()}` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |')
    exe = Path(r'C:\Users\princ\AppData\Local\ZackBot\app\ZackBot.exe')
    manifest['acct1_executable'] = {'sha256': sha(exe.read_bytes()), 'claim': 'on-disk executable only; loose source provenance and loaded memory not proven'}
    manifest['verification'] = {'source_and_copy_equal': len(manifest['files']), 'python_ast_parse_pass': syntax, 'runtime_code_executed': 0}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (OUT / 'DRIFT.md').write_text('# Captured-source drift from master\n\nCompared against `' + base + '`. Scope: top-level source files and newcore/ml_shadow, plus observed launchers. Every captured differing file and master-only file within that scope is listed below. Nature is byte-level; risk is a conservative triage category, not a semantic audit. Credential-screened files are unassessed (see manifest exclusions); no complete-drift claim is made.\n\n| Account | File | Nature | Risk |\n|---|---|---|---|\n' + '\n'.join(drift) + '\n', encoding='utf-8')
    print(json.dumps({'captured': len(manifest['files']), 'drifts': len(drift), 'screened_out': len(manifest['excluded']), 'ast_pass': syntax}))

if __name__ == '__main__':
    main()
