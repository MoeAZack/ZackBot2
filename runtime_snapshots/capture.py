"""Authorized read-only runtime capture; writes only to this checkout and inbox."""
import ast
from datetime import datetime
import difflib
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tokenize
from zoneinfo import ZoneInfo
from sanitize import sanitize, remaining_hits

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / 'runtime_snapshots'
ROOTS = {
    'acct1': Path(r'C:\Users\princ\AppData\Local\ZackBot\src'),
    'acct2': Path(r'F:\ZackBot2_data\OLD3-account2\source'),
    'acct3': Path(r'F:\ZackBot2_data\ACCOUNT3-breakeven\source'),
    'acct4': Path(r'F:\ZackBot2_data\EXPLORATION-account4\source'),
}
EXTRAS = {'acct2': ['start_old3.py'], 'acct3': ['start_account3.py'],
          'acct4': ['start_account4.py', 'r6_account4_runtime_gate.py', 'r6_panel_client.py', 'r6_readonly_monitor.py']}
EXT = {'.py', '.html', '.js', '.css', '.bat', '.ps1'}
SENSITIVE = re.compile(r'(?:secret|token|password|passwd|(?:api|private)[_-]?key|(?:account|user|chat)[_-]?id)', re.I)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def scope(root):
    paths = list(root.glob('*'))
    for folder in ['newcore', 'ml_shadow']:
        paths.extend((root / folder).rglob('*'))
    return sorted(p for p in paths if p.is_file() and p.suffix in EXT and '__pycache__' not in p.parts)

def schema(value):
    if isinstance(value, dict):
        return {k: schema(v) for k, v in value.items() if re.fullmatch(r'[A-Za-z_][A-Za-z_ -]{0,60}', k) and not SENSITIVE.search(k)}
    return {'type': 'array', 'items': 'omitted'} if isinstance(value, list) else type(value).__name__

def write(name, obj):
    (OUT / name).write_text(json.dumps(obj, indent=2) + '\n', encoding='utf-8', newline='\n')

def main():
    assert REPO == Path(r'C:\Dev\ZackBot2_lane1_20261116')
    base = subprocess.check_output(['git', 'rev-parse', 'origin/master'], cwd=REPO, text=True).strip()
    assert base == json.loads((OUT / 'manifest.json').read_text())['master_sha'], 'Baseline changed'
    manifest = {'captured_cairo': datetime.now(ZoneInfo('Africa/Cairo')).isoformat(), 'master_sha': base,
        'files': [], 'excluded': [], 'config_schemas': [], 'excluded_accounts': [], 'master_only': [],
        'authorization': '16 Nov owner continuation: account-4/R6 read-only inventory; no runtime actions'}
    redactions, drift = [], []
    for account, root in ROOTS.items():
        candidates = [(p, 'source/' + p.relative_to(root).as_posix()) for p in scope(root)]
        candidates += [(root.parent / name, ('launcher/' if name.startswith('start_') else 'overlay/') + name) for name in EXTRAS.get(account, [])]
        for source, relative in sorted(candidates):
            raw = source.read_bytes()
            try:
                data, records = sanitize(raw, source.suffix)
                if remaining_hits(data, source.suffix):
                    raise ValueError('Unresolved strict scan candidates')
            except (ValueError, SyntaxError, UnicodeError, IndentationError, tokenize.TokenError) as exc:
                manifest['excluded'].append({'account': account, 'path': relative, 'reason': type(exc).__name__ + ': doubtful sanitization; omitted entirely'})
                stale = (OUT / account / relative).resolve()
                assert stale.is_relative_to(OUT.resolve())
                if stale.is_file():
                    stale.unlink()
                continue
            assert source.read_bytes() == raw, 'Source changed during capture'
            target = OUT / account / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            assert target.read_bytes() == data
            master_path = REPO / relative.removeprefix('source/')
            master = master_path.read_bytes() if relative.startswith('source/') and master_path.is_file() else None
            relation = 'identical' if raw == master else ('modified' if master is not None else 'runtime-only')
            entry = {'account': account, 'path': relative, 'sha256': sha(data), 'size': len(data),
                'source_sha256': sha(raw), 'source_size': len(raw), 'byte_identical': data == raw,
                'redaction_count': len(records), 'master_relation': relation}
            manifest['files'].append(entry)
            redactions.extend({'file': target.relative_to(REPO).as_posix(), **record} for record in records)
            if relation != 'identical':
                delta = list(difflib.ndiff((master or b'').decode('utf-8', errors='replace').splitlines(), raw.decode('utf-8', errors='replace').splitlines()))
                added, removed = sum(x.startswith('+ ') for x in delta), sum(x.startswith('- ') for x in delta)
                entry['line_delta'] = {'added': added, 'removed': removed}
                risk = 'HIGH: engine/exchange/configuration boundary' if source.name in {'engine.py', 'app.py', 'binance_client.py', 'strategies.py', 'r6_paper_overlay.py', 'r6_account4_runtime_gate.py'} else 'MEDIUM: launch/UI/research/provenance; review before deployment'
                drift.append(f'| {account} | `{relative}` | {relation}; +{added}/-{removed}; {len(records)} redactions | {risk} |')
        config_root = root.parent / 'runtime' / 'ZackBot' if account != 'acct1' else root.parent
        for name in ['settings.json', 'cohort.json', 'install.json']:
            source = config_root / name
            if source.is_file():
                write(account + '/' + name + '.schema.json', schema(json.loads(source.read_bytes())))
                manifest['config_schemas'].append({'account': account, 'file': name, 'values': 'all omitted; no source configuration hash'})
        # config.env, account state, orders and trade logs are never opened.
        for path in scope(REPO):
            rel = path.relative_to(REPO)
            if not (root / rel).exists():
                manifest['master_only'].append({'account': account, 'path': rel.as_posix()})
                drift.append(f'| {account} | `source/{rel.as_posix()}` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |')
    exe = ROOTS['acct1'].parent / 'app' / 'ZackBot.exe'
    manifest['acct1_executable'] = {'sha256': sha(exe.read_bytes()), 'claim': '45/45 comparable embedded code entries match loose source; see PROVENANCE.md for method/limits'}
    manifest['verification'] = {'snapshot_hash_checks': len(manifest['files']), 'byte_identical_files': sum(e['byte_identical'] for e in manifest['files']),
        'redacted_files': sum(not e['byte_identical'] for e in manifest['files']), 'redactions': len(redactions),
        'python_ast_parse_pass': sum(e['path'].endswith('.py') for e in manifest['files']), 'runtime_code_executed': 0}
    write('manifest.json', manifest)
    write('redactions.json', redactions)
    (OUT / 'DRIFT.md').write_text('# On-disk source drift from master\n\nBaseline `' + base + '`. Scope: top-level source + newcore/ml_shadow and captured launchers/overlays. Counts compare original on-disk bytes before redaction; no secret-bearing diff text is emitted. Risk is conservative triage, not semantic acceptance.\n\n| Account | File | Nature | Risk |\n|---|---|---|---|\n' + '\n'.join(drift) + '\n', encoding='utf-8', newline='\n')
    note = '# Redaction review — no original values\n\nSource line numbers refer to on-disk inputs; snapshot line numbers identify the placeholder. Replacements are conservative, not claims that real credentials were present. Placeholder: `REDACTED_VALUE`. Redacted files are evidence only, not deployable or byte-identical.\n\n| File | Source line | Snapshot line | Kind of value |\n|---|---:|---:|---|\n'
    note += '\n'.join(f'| `{r["file"]}` | {r["line"]} | {r["snapshot_line"]} | {r["kind"]} |' for r in redactions) + '\n'
    if manifest['excluded']:
        note += '\nDoubtful files omitted entirely:\n' + '\n'.join(f'- {e["account"]}/{e["path"]}: {e["reason"]}' for e in manifest['excluded']) + '\n'
    (OUT / 'REDACTIONS.md').write_text(note, encoding='utf-8', newline='\n')
    Path(r'C:\Dev\codex-inbox\lane1-redactions.md').write_text(note, encoding='utf-8', newline='\n')
    print(json.dumps({**manifest['verification'], 'omitted_doubtful_files': len(manifest['excluded'])}))

if __name__ == '__main__':
    main()
