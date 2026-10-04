import glob, os, pandas as pd
def load(folder=os.path.join(os.path.dirname(__file__),'data'), syms=None):
    out={}
    for f in sorted(glob.glob(os.path.join(folder,'*_4h.csv'))):
        s=os.path.basename(f).split('_')[0]
        if syms and s not in syms: continue
        out[s]=pd.read_csv(f,parse_dates=['t'])
    return out
