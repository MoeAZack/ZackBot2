import os,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools/research'))
import edge04_stage_a as A
import edge04_orchestrate as O
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

def test_real_orchestration_missing_next_open_becomes_persistent_incomplete_row():
    # Same real path as the SOL failure: a genuine signal, present decision bar, absent
    # executable bar opening at the decision instant. It must not escape as a process crash.
    out=[]
    for i in range(15):
        t=1_700_000_000_000+(i+1)*PR.TF
        q={h:{"e":False,"x":False,"atr":"2.5"} for h in ("H1","H2","H3","H4")}
        if i==14:q["H2"]["e"]=True
        out.append({"t":t,"r":[{"s":"SOLUSDT","b":[100,101,99,100],"f":[],"q":q,
                                  "n":None if i==14 else 100}]})
    row=A.orchestrate_row(out,"H2",rules={"SOLUSDT":{"tick":.01,"step":.1,"min_qty":.1,
                                                       "min_notional":5}},slip_c=.05,
                            scenario=O.STRESS["base"],ds=None,hi=out[-1]["t"])
    assert row["summary"]["status"]==PR.INCOMPLETE
    assert "SOLUSDT at" in row["summary"]["reason"] and "next executable open missing" in row["summary"]["reason"]
    assert row["book"]["failure"]["class"]=="OrchestrationError"


# ---------------------------------------------------------------- EDGE04-B0-01: capacity-valid matched B0
T0=1_700_000_000_000-(1_700_000_000_000%PR.TF)
GOLDEN_UNCONGESTED="2fb18ef0c45b8295931a88f914c85672c25024a1a79ed09cfd481ba664b2698f"
RULES={s:{"tick":.01,"step":.001,"min_qty":.001,"min_notional":5} for s in ("BTC","ETH")}

class FakeSym:
    def __init__(self,n):
        self.rec={T0+i*PR.TF:{"n":100.0+i,"b":[100.0+i]*4} for i in range(n)}
    def bps(self,at,vol,c): return 0.0
    def events(self,a,b): return []

def b0_case(monkeypatch,symbols,holds,books=50,bars=12,**kw):
    syms={s:FakeSym(bars) for s in set(symbols)}
    monkeypatch.setattr(A.PR,"read_tape",lambda outputs:([],syms))
    monkeypatch.setattr(A.O,"B0_BOOKS",books)
    monkeypatch.setattr(A.O,"STRESS",{"base":A.O.STRESS["base"]})
    acc=[{"status":PR.COMPLETE,"symbol":s,"side":"long","entry_ms":T0,"exit_ms":T0+h*PR.TF,
          "entry_price":100.0,"initial_stop":95.0} for s,h in zip(symbols,holds)]
    first,last=T0,T0+(bars-5)*PR.TF
    return A.b0([], "H1", {"status":PR.COMPLETE,"accepted":acc}, "wf3[0]", first, last,
                T0+(bars-1)*PR.TF, RULES, .05, None, **kw)

def test_b0_capacity_tight_case_reaches_all_requested_books(monkeypatch):
    # Two same-symbol templates holding 3 bars over 8 entry slots: one-per-symbol refuses ~46% of
    # whole-book draws. The pre-fix generator returned INCOMPLETE with ~27/50 valid books.
    got=b0_case(monkeypatch,["BTC","BTC"],[3,3])
    base=got["stresses"]["base"]
    assert got["status"]==PR.COMPLETE and base["valid_books"]==50
    assert base["rejected_attempts"]>0 and base["rejection_reasons"]=={"capacity.one_per_symbol":base["rejected_attempts"]}
    assert sum(base["attempts_per_draw"])==50+base["rejected_attempts"]

def test_b0_resample_is_deterministic(monkeypatch):
    assert b0_case(monkeypatch,["BTC","BTC"],[3,3])==b0_case(monkeypatch,["BTC","BTC"],[3,3])

def test_b0_budget_exhaustion_fails_closed(monkeypatch):
    # Three same-symbol 3-bar templates in 8 slots: valid draws exist but are rare; budget 1 must exhaust.
    got=b0_case(monkeypatch,["BTC","BTC","BTC"],[3,3,3],max_attempts=1)
    base=got["stresses"]["base"]
    assert got["status"]==PR.INCOMPLETE and base["status"]==PR.INCOMPLETE
    assert base["exhausted"]["attempts"]==1 and "attempt budget" in base["reason"]

def test_b0_uncongested_case_is_byte_identical_to_pre_fix_generator(monkeypatch):
    # Golden digest captured from the pre-fix b0() at b5957d58 on this exact fixture.
    got=b0_case(monkeypatch,["BTC","ETH"],[3,2])
    base=got["stresses"]["base"]
    assert got["status"]==PR.COMPLETE and base["valid_books"]==50 and base["rejected_attempts"]==0
    assert set(base["attempts_per_draw"])=={1}
    assert base["outcomes_digest"]==GOLDEN_UNCONGESTED

def test_b0_diagnostics_present(monkeypatch):
    got=b0_case(monkeypatch,["BTC","BTC"],[3,3])
    base=got["stresses"]["base"]; d=base["diagnostics"]
    assert got["rule"]==A.B0_RULE and got["max_attempts_per_draw"]==A.B0_MAX_ATTEMPTS
    assert d["accepted_books"]==50 and d["total_attempts"]==sum(base["attempts_per_draw"])
    assert set(d["attempts_quantiles"])=={"min","p50","p90","p99","max"}
    assert sum(d["attempts_histogram"].values())==50
    assert d["accepted_trade_count_quantiles"]["min"]==d["accepted_trade_count_quantiles"]["max"]==2
    assert d["attempts_digest"]==A.sha_doc(base["attempts_per_draw"])

def test_b0_attempt_seeds():
    assert A.b0_seed("H1","wf3[0]",7,0)==A.O.seed("edge04_legacy_reeval.v1","H1","wf3[0]",7)
    assert len({A.b0_seed("H1","wf3[0]",7,a) for a in range(50)})==50

def _fail_on_call(n,exc=None,book=None):
    """Wrap O.route_shared: the n-th call raises `exc` or returns `book`; earlier calls are real."""
    real=A.O.route_shared; calls={"n":0}
    def wrapped(cand,replay,**kw):
        calls["n"]+=1
        if calls["n"]==n:
            if exc is not None: raise exc
            return book
        return real(cand,replay,**kw)
    return wrapped

@pytest.mark.parametrize("kind,patch,reason",[
    ("incomplete",lambda: _fail_on_call(5,exc=PR.Incomplete("incomplete.gap")),"data.incomplete.gap"),
    ("orchestration",lambda: _fail_on_call(5,exc=A.O.OrchestrationError("BTC: causal mark missing")),
     "data.orchestration: BTC: causal mark missing"),
    ("cost",lambda: _fail_on_call(5,exc=A.C.CostError("bad cost")),"data.cost: bad cost"),
    ("episode",lambda: _fail_on_call(5,book={"status":PR.INCOMPLETE,"accepted":[{"status":PR.INCOMPLETE}],
                                             "refused":[],"wallet_end":None}),"episode.incomplete"),
])
def test_b0_data_or_episode_incompleteness_fails_closed_without_resampling(monkeypatch,kind,patch,reason):
    seen=[];real_seed=A.b0_seed
    monkeypatch.setattr(A,"b0_seed",lambda h,k,i,a:(seen.append((i,a)),real_seed(h,k,i,a))[1])
    monkeypatch.setattr(A.O,"route_shared",patch())
    got=b0_case(monkeypatch,["BTC","BTC"],[3,3])
    base=got["stresses"]["base"]
    assert got["status"]==PR.INCOMPLETE and base["status"]==PR.INCOMPLETE
    assert len(seen)==5 and base["diagnostics"]["total_attempts"]==5   # no attempt after the failing one
    assert base["failed"]=={"draw":seen[-1][0],"attempt":seen[-1][1],"reason":reason}
    assert reason in base["reason"] and "exhausted" not in base
    assert base["valid_books"]+base["rejected_attempts"]==4

def test_b0_discloses_unpaired_stress_rows(monkeypatch):
    got=b0_case(monkeypatch,["BTC","BTC"],[3,3])
    assert "NOT paired" in got["pairing"] and got["stresses"]["base"]["diagnostics"]["pairing"]==got["pairing"]

# ---------------------------------------------------------------- r3: STRUCTURAL / PATH_DEPENDENT refusal groups
def test_b0_refusal_classification():
    for r in ("capacity.one_per_symbol","capacity.max_positions","proposal.collision"):
        assert A.b0_refusal_group(r)=="structural"
    for r in ("risk.daily_loss","execution.size_min"):
        assert A.b0_refusal_group(r)=="path_dependent"
    for r in ("capacity.unknown","capacity.weird","",None):
        assert A.b0_refusal_group(r) is None

@pytest.mark.parametrize("struct,path,share,label",[
    (0,0,0.0,"CLEAN_ON_DIAGNOSTIC"),(7,0,0.0,"CLEAN_ON_DIAGNOSTIC"),
    (999,1,0.001,"CONDITIONED_NULL"),          # any path-dependent refusal leaves CLEAN
    (20,1,1/21,"CONDITIONED_NULL"),            # 4.76%: just below 5%
    (19,1,0.05,"MATERIAL_CONDITIONING"),       # exactly 5%
    (0,3,1.0,"MATERIAL_CONDITIONING")])
def test_b0_conditioning_share_and_label_boundaries(struct,path,share,label):
    got=A.b0_conditioning(struct,path)
    assert got=={"structural_refusals":struct,"path_dependent_refusals":path,"total_resampled_refusals":struct+path,
                 "path_dependent_share":share,"conditioning":label}

def _refuse_on_call(n,reasons):
    real=A.O.route_shared; calls={"n":0}
    def wrapped(cand,replay,**kw):
        calls["n"]+=1
        book=real(cand,replay,**kw)
        if calls["n"]==n:
            book=dict(book,accepted=book["accepted"][:1],
                      refused=[{"symbol":"BTC","entry_ms":0,"reason":r} for r in reasons])
        return book
    return wrapped

def test_b0_unclassified_refusal_fails_closed(monkeypatch):
    seen=[];real_seed=A.b0_seed
    monkeypatch.setattr(A,"b0_seed",lambda h,k,i,a:(seen.append((i,a)),real_seed(h,k,i,a))[1])
    monkeypatch.setattr(A.O,"route_shared",_refuse_on_call(4,["capacity.weird"]))
    base=b0_case(monkeypatch,["BTC","BTC"],[3,3])["stresses"]["base"]
    assert base["status"]==PR.INCOMPLETE and len(seen)==4
    assert base["failed"]=={"draw":seen[-1][0],"attempt":seen[-1][1],"reason":"unclassified.refusal: capacity.weird"}

def test_b0_path_dependent_refusals_are_counted_and_labelled(monkeypatch):
    monkeypatch.setattr(A.O,"route_shared",_refuse_on_call(2,["risk.daily_loss","execution.size_min"]))
    got=b0_case(monkeypatch,["BTC","BTC"],[3,3]); base=got["stresses"]["base"]
    hist=base["refusal_reason_histogram"]
    assert got["status"]==PR.COMPLETE and hist["risk.daily_loss"]==1 and hist["execution.size_min"]==1
    assert base["path_dependent_refusals"]==2 and base["structural_refusals"]==hist["capacity.one_per_symbol"]
    assert base["total_resampled_refusals"]==sum(hist.values())
    assert base["path_dependent_share"]==2/sum(hist.values())
    assert base["conditioning"]==A.b0_conditioning(base["structural_refusals"],2)["conditioning"]
    assert got["refusal_groups"]["structural"]==list(A.B0_STRUCTURAL)

def test_b0_uncongested_is_clean_on_diagnostic(monkeypatch):
    base=b0_case(monkeypatch,["BTC","ETH"],[3,2])["stresses"]["base"]
    assert base["conditioning"]=="CLEAN_ON_DIAGNOSTIC" and base["total_resampled_refusals"]==0

def test_b0_amendment_twins_agree_on_groups_and_thresholds():
    import json,re
    doc=ROOT/"docs/newcore/research"
    j=json.loads((doc/"EDGE04_B0_AMENDMENT_v1.json").read_text(encoding="utf-8"))
    md=(doc/"EDGE04_B0_AMENDMENT_v1.md").read_text(encoding="utf-8")
    assert j["status"].startswith("DRAFT") and j["signature"] is None and "DRAFT" in md.splitlines()[2]
    assert "pnl_independence" not in json.dumps(j) and "never reads" not in md and "never P&L" not in md
    for name,code in (("STRUCTURAL",A.B0_STRUCTURAL),("PATH_DEPENDENT",A.B0_PATH_DEPENDENT)):
        line=next(x for x in md.splitlines() if x.startswith(f"- **{name}**:"))
        assert re.findall(r"`([^`]+)`",line)==j["refusal_groups"][name]==list(code)
    assert j["interpretation"]["material_share"]==A.B0_MATERIAL_SHARE==0.05
    labels=list(j["interpretation"]["labels"])
    assert labels==["CLEAN_ON_DIAGNOSTIC","CONDITIONED_NULL","MATERIAL_CONDITIONING"]
    rows=[x for x in md.splitlines() if x.startswith("| ") and "`" in x]
    assert [re.findall(r"`([^`]+)`",x)[0] for x in rows]==labels
    assert rows[0].startswith("| 0% ") and "below 5%" in rows[1] and rows[2].startswith("| 5% or more ")
    assert j["budget"]["max_attempts_per_draw"]==A.B0_MAX_ATTEMPTS and "10,000 attempts" in md
