"""Immutable preread independent20k/four-seed synthetic scalar oracle."""
import hashlib
import json
from pathlib import Path
import pytest
from tools.research import c267_statistics as S


@pytest.mark.parametrize('side',[1,-1])
def test_own267_side_statistics_exact_independent_7cb1b26f_oracle(side):
    raw=(Path(__file__).parent/'fixtures/c267_stationary_oracle_expected.v1.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='06b808975b27f06164c3ddf1c3f2481eaa2e89d269fd2957e152c1a8efdfaa33'
    expected=json.loads(raw);gold=expected['sides'][str(side)]
    actual=S.statistics(expected['ordered_values'],side=side)
    assert actual=={key:gold[key] for key in('mean','p_value','ci_low','ci_high')}
    assert actual['ci_low']>0 and actual['p_value']>.05/(267*2)


def test_empty_or_pooled_or_wrong_side_inference_cannot_qualify():
    for values,side in(((),1),((1.,),0),((1.,),True),((float('nan'),),-1)):
        with pytest.raises(ValueError):S.statistics(values,side=side)
