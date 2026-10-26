import os,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools/research'))
import edge04_stage_a as A
import pit_run as PR

def outputs(t=100):
    q={h:{"e":True,"x":False,"atr":"1"} for h in ("H1","H2","H3","H4")}
    q["H1"]["raw_e"]=True
    q["H2"].update(rank=4,prev_rank=6,above_ema50=True,prev_above_ema50=True)
    return [{"t":t,"r":[{"s":"BTC","b":[1,2,.5,1],"f":[],"q":q,"n":1}]}]

def test_variant_boundaries_and_finite_overrides():
    v=next(v for v in A.VARIANTS if v[0]=="H2.k3")
    q=A.variant_outputs(outputs(),v,100,100)[0]["r"][0]["q"]
    assert q["H2"]["e"] is False and q["H2"]["x"] is False
    q=A.variant_outputs(outputs(),next(v for v in A.VARIANTS if v[0]=="H1.any_ablation"),100,100)[0]["r"][0]["q"]
    assert q["H1"]["e"] is True and q["H2"]["e"] is False
    assert not A.variant_outputs(outputs(),A.VARIANTS[0],101,200)[0]["r"][0]["q"]["H1"]["e"]

def test_exact_head_refuses_dirty_or_wrong(monkeypatch,tmp_path):
    monkeypatch.setattr(A.subprocess,"check_output",lambda cmd,**kw:"abc\n" if "rev-parse" in cmd else "")
    with pytest.raises(A.StageAError,match="exact clean head"): A.exact_head(str(tmp_path),"def")

def test_summary_fails_closed_on_censor():
    b={"status":PR.INCOMPLETE,"wallet_end":9990,"accepted":[{"status":PR.INCOMPLETE,
       "reason":"right_censored_runtime_no_cap"}],"refused":[]}
    s=A.account_summary(b)
    assert s["status"]==PR.INCOMPLETE and s["incomplete"]=={"right_censored_runtime_no_cap":1}

def test_registered_surface_is_exact():
    assert [v[0] for v in A.VARIANTS]==["H1.primary","H1.any_ablation","H2.primary_k5","H2.k3","H2.k7",
                                        "H3.primary","H4.primary"]
    assert len(A.O.STRESS)==6 and A.O.B0_BOOKS==10_000

def test_b0_refuses_to_define_match_for_incomplete_primary():
    got=A.b0([],"H2",{"status":PR.INCOMPLETE},"train",0,1,2,{},.05,None)
    assert got["status"]==PR.INCOMPLETE and got["valid_books"]==0
