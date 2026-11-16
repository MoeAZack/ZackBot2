import os, sys, json, hashlib, marshal, types, importlib.util, subprocess
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader
root=Path(r'C:\Users\princ\AppData\Local\ZackBot')
exe=root/'app'/'ZackBot.exe'; loose=root/'src'; repo=Path(r'C:\Dev\ZackBot2_lane1_20261116')
out=Path(r'C:\Dev\codex-inbox\lane1-provenance-20261116\report.txt')
def h(b): return hashlib.sha256(b).hexdigest()
def normal(c):
    return c.replace(co_filename='<normalized>', co_consts=tuple(normal(x) if isinstance(x,types.CodeType) else x for x in c.co_consts))
def digest_code(c): return h(marshal.dumps(normal(c)))
def compare(data,p):
    try:
        embedded=marshal.loads(data)
        if not isinstance(embedded,types.CodeType): return 'not-code'
        b=p.read_bytes()
        for opt in (0,1,2):
            compiled=compile(b, '<normalized>', 'exec', dont_inherit=True, optimize=opt)
            if digest_code(embedded)==digest_code(compiled): return f'match-opt{opt}'
        return 'different'
    except Exception as e: return 'error:'+type(e).__name__
a=CArchiveReader(str(exe)); pyz=a.open_embedded_archive('PYZ.pyz')
with exe.open('rb') as f:
    f.seek(pyz._start_offset); header=f.read(8)
lines=['Read-only archive comparison; no installed code executed or imported.',f'exe_sha256={h(exe.read_bytes())}', f'exe_size={exe.stat().st_size}',f'pyz_header_magic={header[4:8].hex()}',f'local_python_magic={importlib.util.MAGIC_NUMBER.hex()}',f'local_python={sys.version.split()[0]}']
if header[4:8] != importlib.util.MAGIC_NUMBER: raise SystemExit('Python bytecode magic mismatch; use matching interpreter')
for label,base in [('loose',loose),('repo',repo)]:
    results=[]
    for name in pyz.toc:
        p=base.joinpath(*name.split('.')).with_suffix('.py')
        if not p.is_file(): p=base.joinpath(*name.split('.'),'__init__.py')
        if p.is_file(): results.append((name,compare(pyz.extract(name,raw=True),p)))
    if (base/'app.py').is_file(): results.append(('app [CArchive script]',compare(a.extract('app'),base/'app.py')))
    matches=[n for n,s in results if s.startswith('match')]; diffs=[n for n,s in results if s=='different']; errors=[(n,s) for n,s in results if s not in ('different',) and not s.startswith('match')]
    lines += [f'{label}: compared={len(results)} matched={len(matches)} different={len(diffs)} errors={len(errors)}',f'{label}_matches='+','.join(matches),f'{label}_different='+','.join(diffs),f'{label}_errors='+repr(errors)]
    for name in ['app.py','build_info.py']:
        p=base/name
        if p.is_file(): lines.append(f'{label}_{name}_sha256={h(p.read_bytes())}')
for fn in ['build.json','install.json']:
    try:
        d=json.loads((root/fn).read_text(encoding='utf-8-sig'))
        allowed=('commit','git_commit','git_sha','revision','version','build_id','build_time','built_at','timestamp','source_commit','source_revision')
        lines.append(fn+'_safe_fields='+json.dumps({k:d[k] for k in allowed if k in d and isinstance(d[k],(str,int,float,bool))}))
    except Exception as e: lines.append(fn+'_read_error='+type(e).__name__)
try:
    lines.append('repo_HEAD='+subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip())
except Exception: pass
out.write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('\n'.join(lines))

