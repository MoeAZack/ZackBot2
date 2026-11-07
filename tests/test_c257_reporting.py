from decimal import Decimal
from tools.research import c257_reporting as R
from tools.research import funding_shock_reversal_xsec_c257_stagea as S
from tools.research import funding_shock_reversal_xsec_c257_protected_adapter as A
from tools.research import funding_shock_reversal_xsec_c257_evaluator as E
import pytest


def test_report_only_ema200_fold_reset_warmup_seed_causality_and_gap():
    bars=tuple(S.HourBar(hour*S.HOUR_MS,100,101,99,100,1_000_000) for hour in range(200))
    labels=R.regime_labels(bars)
    assert labels[199*S.HOUR_MS]=="UNCLASSIFIED"
    assert labels[200*S.HOUR_MS]=="BTC_ABOVE_EMA200"
    lower=S.HourBar(200*S.HOUR_MS,90,91,89,90,1_000_000)
    later=R.regime_labels(bars+(lower,))
    assert later[200*S.HOUR_MS]==labels[200*S.HOUR_MS]
    assert later[201*S.HOUR_MS]=="BTC_BELOW_EMA200"
    gap=S.HourBar(202*S.HOUR_MS,100,101,99,100,1_000_000)
    assert R.regime_labels(bars+(lower,gap))[203*S.HOUR_MS]=="UNCLASSIFIED"
    assert R.regime_labels((lower,))[201*S.HOUR_MS]=="UNCLASSIFIED"


def test_confidence_interval_percentile_interpolates_fixed_uncentered_draws():
    values=[Decimal(0),Decimal(10),Decimal(20),Decimal(30),Decimal(40)]
    assert R.percentile(values,Decimal("0.025"))==Decimal(1)
    assert R.percentile(values,Decimal("0.975"))==Decimal(39)


def test_diagnostic_pairs_seeded_uniform_without_replacement_and_sign_flip():
    import numpy as np
    shocks=[S.FundingShock(f"L{i}",0,0,0,1,-3-i) for i in range(6)]
    shocks += [S.FundingShock(f"S{i}",0,0,0,1,3+i) for i in range(6)]
    one=R.diagnostic_pairs(shocks,"random_opposite_tail",np.random.Generator(np.random.PCG64(R.BASELINE_SEED)))
    two=R.diagnostic_pairs(shocks,"random_opposite_tail",np.random.Generator(np.random.PCG64(R.BASELINE_SEED)))
    assert one==two and len(one)==3
    assert len({pair.long_symbol for pair in one})==3 and len({pair.short_symbol for pair in one})==3
    original=S.fixed_index_pairs(shocks)
    flipped=R.diagnostic_pairs(shocks,"sign_flipped",None)
    assert [(p.index,p.long_symbol,p.short_symbol) for p in flipped]==[(p.index,p.short_symbol,p.long_symbol) for p in original]


def test_ema_seed_uses_exact_fsum_and_written_recurrence(monkeypatch):
    import math
    original=math.fsum
    observed=[]
    def capture(values):
        observed.append(tuple(values))
        return original(values)
    monkeypatch.setattr(R.math,"fsum",capture)
    closes=[1e16]+[1.0]*198+[original([1e16]+[1.0]*198)/199]
    seed=original(closes)/200
    next_close=seed
    bars=tuple(S.HourBar(hour*S.HOUR_MS,value,value+1,max(value-1,0),value,1_000_000)
               for hour,value in enumerate(closes+[next_close]))
    labels=R.regime_labels(bars)
    assert observed==[tuple(closes)]
    expected=(2/201)*next_close+(199/201)*seed
    assert labels[201*S.HOUR_MS]==("BTC_ABOVE_EMA200" if next_close>=expected else "BTC_BELOW_EMA200")


def test_correlation_never_substitutes_zero_for_undefined_variance():
    assert R._pearson([Decimal(1),Decimal(2)],[Decimal(2),Decimal(1)])==Decimal(-1)
    assert R._pearson([Decimal(1),Decimal(1)],[Decimal(2),Decimal(3)]) is None


def test_full_hourly_book_keeps_flat_interval_between_exposures():
    from types import SimpleNamespace as NS
    fills=[]
    for hour,phase,side in ((1,"entry",1),(2,"exit",-1),(8,"entry",1),(9,"exit",-1)):
        fill=E.DerivedFill(hour*S.HOUR_MS,"X",side,Decimal(1),Decimal(100),phase,hour*S.HOUR_MS)
        fills.append(NS(fill=fill,movement=Decimal(0),fee=Decimal(1),slippage=Decimal(0),impact=Decimal(0)))
    rows=(NS(attempt_id="one",fills=tuple(fills[:2]),funding=()),
          NS(attempt_id="two",fills=tuple(fills[2:]),funding=()))
    marks=(E.MarkClose("X",S.HOUR_MS,Decimal(100)),E.MarkClose("X",8*S.HOUR_MS,Decimal(100)))
    path=R._hourly_book_path(rows,marks,Decimal(1000),timeline=tuple(hour*S.HOUR_MS for hour in range(11)))
    assert len(path)==11
    assert all(equity==Decimal(998) for timestamp,equity in path if 2*S.HOUR_MS<=timestamp<=7*S.HOUR_MS)
    with pytest.raises(S.IncompleteAuthority,match="missing canonical hour"):
        R._hourly_book_path(rows,marks,Decimal(1000),timeline=(0,S.HOUR_MS,3*S.HOUR_MS))


def test_atr24_uses_seed_then_wilder_recurrence_over_causal_prefix():
    bars=tuple(S.HourBar(hour*S.HOUR_MS,100,101 if hour<25 else 103,
                        99 if hour<25 else 97,100,1_000_000) for hour in range(50))
    expected=Decimal(2)
    for _ in range(25): expected=(expected*23+Decimal(6))/24
    assert E._atr24(bars,50*S.HOUR_MS)==expected
    assert expected!=Decimal(6)  # a re-seeded trailing mean is forbidden
    future=S.HourBar(50*S.HOUR_MS,100,1000,1,100,1_000_000)
    assert E._atr24(bars+(future,),50*S.HOUR_MS)==expected


def test_incomplete_report_gate_precedes_any_identity_or_archive_access(monkeypatch,tmp_path):
    monkeypatch.setattr(A,"authenticate_before_loader",lambda:A.Bindings("a"*40,"b"*64,{},{}))
    monkeypatch.setattr(A,"REPORT_REQUIREMENTS_BOUND",False)
    monkeypatch.setattr(A,"_identity_directory",lambda:(_ for _ in ()).throw(AssertionError("identity accessed")))
    with pytest.raises(A.ProtectedAdapterError,match="C257_REPORT_UNBOUND"):
        A.evaluate_once(store=tmp_path)
    assert list(tmp_path.iterdir())==[]


def test_protected_authentication_uses_base_lineage_and_keeps_missing_pin_lock(monkeypatch):
    original_git = A._git
    head = original_git("rev-parse", "HEAD")
    def git(*args):
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "--verify", f"{A.PROTECTED_REF}^{{commit}}"):
            return head
        return original_git(*args)
    ancestry = []
    monkeypatch.setattr(A, "_git", git)
    monkeypatch.setattr(A, "_ancestor", lambda base, target: ancestry.append((base, target)))
    with pytest.raises(A.ProtectedAdapterError):
        A.authenticate_before_loader()
    assert ancestry == [(A.PROTECTED_PLAN_COMMIT, head)]


def test_original_scaffold_object_is_explicitly_available_in_an_isolated_shallow_store(tmp_path):
    import hashlib
    import subprocess
    from tools.research import c257_successor_guard as G
    repo = tmp_path / 'shallow'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    path = 'research_evidence/prereg/funding_shock_reversal_xsec_c257_successor.v1.json'
    missing = subprocess.run(['git', '-C', str(repo), 'cat-file', '-e', f'{A.SUCCESSOR_HEAD}:{path}'],
                             capture_output=True)
    assert missing.returncode != 0
    # CI fetches this exact bound object into the source store; no moving branch,
    # replacement fixture contract or alternate scaffold is accepted.
    subprocess.run(['git', '-C', str(repo), 'fetch', '--no-tags', '--depth=1', str(A.ROOT), A.SUCCESSOR_HEAD],
                   check=True, capture_output=True)
    assert subprocess.check_output(['git', '-C', str(repo), 'rev-parse', '--is-shallow-repository']).strip() == b'true'
    raw = G._tree_bytes(repo, A.SUCCESSOR_HEAD, path)
    assert hashlib.sha256(raw).hexdigest() == G.SCAFFOLD_BYTE_SHA256


def test_empty_book_is_reported_as_rejection_without_invented_statistics(monkeypatch):
    monkeypatch.setattr(S,"frozen_splits",lambda:{"development":(0,24*S.HOUR_MS,False)})
    raw=E.SealedEvidence("development",Decimal(1000),(),())
    report=E.evaluate(raw)
    assert report["verdict"]=="REJECT_DEVELOPMENT"
    assert report["mean_R"] is None and report["profit_factor"] is None
    assert report["bootstrap_mean_R_95pct_interval"] is None
    assert report["attempts"]==0 and not report["report_statistics_defined"]
