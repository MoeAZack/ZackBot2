"""Own270 fixed per-side20k/21-trade stationary inference, no old259 seeds."""
import math
import numpy as np
from tools.research import btc_residual_leadlag_continuation_c259_stagea as E

RESAMPLES=20000
SEEDS={1:(27020261108,27020261107)}


def statistics(values,*,side):
    values=tuple(map(float,values))
    if type(side)is not int or side not in SEEDS or not values or any(not math.isfinite(v) for v in values):raise ValueError('C270_UNDEFINED_SIDE_BOOTSTRAP')
    null_seed,ci_seed=SEEDS[side];mean=math.fsum(values)/len(values)
    null=E.stationary_bootstrap(values,centered=True,seed=null_seed,resamples=RESAMPLES)
    ci=E.stationary_bootstrap(values,centered=False,seed=ci_seed,resamples=RESAMPLES)
    return {'mean':mean,'p_value':(1+int(np.count_nonzero(null>=mean)))/(RESAMPLES+1),
        'ci_low':float(np.quantile(ci,.025,method='linear')),'ci_high':float(np.quantile(ci,.975,method='linear'))}
