"""Review-only copy of receipt, confirming the FAKE account in its temporary directory."""
import sys, pathlib, os, tempfile, socket, hashlib, json
root=pathlib.Path('F:/ZackBot2_data/R6-PAPER-01-REVIEW-20261114')
os.environ['LOCALAPPDATA']=tempfile.mkdtemp(prefix='r6_diag_app_')
sys.dont_write_bytecode=True
def no_network(*a,**k): raise RuntimeError('NETWORK FORBIDDEN IN REVIEW')
socket.socket.connect=no_network
paths=[pathlib.Path('F:/ZackBot2_data/EXPLORATION-account4')/p for p in ['runtime/ZackBot/settings.json','runtime/ZackBot/install.json','source/engine.py']]
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
p=root/'tools/r6_sizing_receipt.py'
code=p.read_text(encoding='utf8').replace('        e.connect()','        e.connect()\n        e.confirm_install()  # FAKE engine and TEMP directory only\n        e.S["ENTRIES_PAUSED"] = False')
sys.argv=[str(p),'--source','F:/ZackBot2_data/EXPLORATION-account4/source','--settings',str(paths[0])]
try: exec(compile(code,str(p),'exec'),{'__name__':'__main__','__file__':str(p)})
finally:
    result={str(p):{'sha256':before[str(p)],'unchanged':before[str(p)]==hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths}
    (root/'account4_readonly_hashes.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2))
