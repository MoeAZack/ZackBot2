"""NC-01 performance guard (contract 6.6). The baseline was measured first (perf_baseline_v1.json); the budget is
BUDGET_X times that baseline, generous enough for slower CI runners, plus the brief's absolute bound: validating a
typical portfolio stays well under 1 ms."""
import dataclasses
import json
import os
import statistics
import time

import nc01_factories as F
from newcore.domain import dumps, loads

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = json.load(open(os.path.join(HERE, 'perf_baseline_v1.json'), encoding='utf-8'))
BUDGET_X = 10
VALIDATE_ABS_US = 1000


def _median_us(fn, n, repeats=5):
    out = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        out.append((time.perf_counter() - t0) / n * 1e6)
    return statistics.median(out)


def test_typical_portfolio_codec_and_validation_budget():
    p = F.typical_portfolio()
    text = dumps(p)
    assert len(p.lots) == 11 and len(text) == 19516               # the baseline's portfolio (+ r3 item 6 stop_distance)
    validate = _median_us(lambda: dataclasses.replace(p, generation=p.generation + 1), 200)
    encode = _median_us(lambda: dumps(p), 40)
    decode = _median_us(lambda: loads(text), 20)
    report = dict(validate_us=round(validate), encode_us=round(encode), decode_us=round(decode))
    assert validate < VALIDATE_ABS_US, report
    for k in ('validate_us', 'encode_us', 'decode_us'):
        assert report[k] <= BUDGET_X * BASE[k], (k, report, BASE[k])
