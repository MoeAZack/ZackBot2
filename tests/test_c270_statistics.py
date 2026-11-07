"""Own270 seeds/20k/21 C-order inference; no previous candidate seed reuse."""
import math
import numpy as np
import pytest
from tools.research import c270_statistics as S
from tools.research import btc_residual_leadlag_continuation_c259_stagea as E


def test_fixed_own270_scalar_statistics_full_draws_and_plusone():
 values=tuple((i%7-2)/10 for i in range(43));mean=math.fsum(values)/len(values)
 null=E.stationary_bootstrap(values,centered=True,seed=27020261108,resamples=20000)
 ci=E.stationary_bootstrap(values,centered=False,seed=27020261107,resamples=20000)
 assert S.SEEDS=={1:(27020261108,27020261107)}
 assert S.statistics(values,side=1)=={'mean':mean,'p_value':(1+int(np.count_nonzero(null>=mean)))/20001,
  'ci_low':float(np.quantile(ci,.025,method='linear')),'ci_high':float(np.quantile(ci,.975,method='linear'))}


def test_empty_or_other_side_statistics_cannot_qualify():
 for values,side in(((),1),((1.,),-1),((1.,),True),((float('nan'),),1)):
  with pytest.raises(ValueError):S.statistics(values,side=side)
