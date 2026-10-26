import json
import math
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/research"))

import edge04_eval as E  # noqa: E402
import edge04_pit as A  # noqa: E402
import pit as P  # noqa: E402
import pit_run as PR  # noqa: E402


TF = E.TF_MS


def bars(seed=0, n=240, end=None):
    end = end if end is not None else n * TF
    start = end - n * TF
    out = []
    for i in range(n):
        # Deterministic, non-degenerate return history with different cross-sectional scores.
        close = 100 + 0.018 * (seed + 1) * i + 2.7 * math.sin((i + seed * 3) / 11)
        op = close * (1 - 0.001 * math.sin(i / 7))
        out.append(P.Bar(start + i * TF, op, max(op, close) + 0.7, min(op, close) - 0.7,
                         close, 1000 + i, 0, start + (i + 1) * TF))
    return out


class View:
    def __init__(self, books, *, t=None, nxt=123.0):
        self.books = books
        self.t = t if t is not None else max(b[-1].available_ms for b in books.values())
        self.nxt = nxt
        self.next_calls = []

    def members(self):
        return list(self.books)

    def bars(self, symbol, interval, n):
        assert interval == "4h"
        eligible = [b for b in self.books[symbol] if b.available_ms <= self.t]
        return eligible[-n:]

    def next_open(self, symbol, interval):
        assert interval == "4h"
        self.next_calls.append(symbol)
        return self.nxt

    def funding(self, symbol, start):
        return []

    def mark_bars(self, symbol, n):
        return []


def test_h1_h3_h4_are_bit_parity_with_runtime_rules():
    bs = bars(4)
    got = E._signals(bs, rank=2.0, prev_rank=7.0, bear=True)
    source = (ROOT / "strategies.py").read_text(encoding="utf-8")
    assert "se=dn & xup(d.rsi14 * 0 + 60, d.rsi14)" in source
    assert "le=xup(d.e20, d.e50) & (d.c > d.e200) & ml" in source
    assert "lx=(d.e20 < d.e50) | ~ml" in source
    assert "return _one_side(sig_ema_mom(d, p, ctx))" in source
    # Both trend hypotheses share exact entry/exit bits, while management remains separate in the prereg.
    assert got["H3"]["e"] == got["H4"]["e"]
    assert got["H3"]["x"] == got["H4"]["x"]
    atr = E._atr(bs, 14)[-1]
    assert float(got["H1"]["atr"]) == pytest.approx(2.0 * atr)
    assert float(got["H2"]["atr"]) == pytest.approx(3.0 * atr)
    assert float(got["H3"]["atr"]) == pytest.approx(2.5 * atr)


def test_h2_uses_the_runtime_synchronized_cross_section():
    books = {f"S{i}USDT": bars(i) for i in range(1, 8)}
    current = {s: E._score(b[-181:]) for s, b in books.items()}
    previous = {s: E._score(b[-182:-1]) for s, b in books.items()}
    ranks, prev = E._rank(current), E._rank(previous)
    source = (ROOT / "strategies.py").read_text(encoding="utf-8")
    assert "ret180 / (d.c.pct_change().rolling(180).std() * np.sqrt(180))" in source
    assert "rank = ra.rank(axis=1, ascending=False)" in source
    assert "in_top = (r <= k) & (d.c > d.e50)" in source
    for symbol in books:
        got = E._signals(books[symbol], rank=ranks[symbol], prev_rank=prev[symbol], bear=False)["H2"]
        assert got["rank"] == ranks[symbol]
        assert got["e"] == bool(got["rank"] <= 5 and got["above_ema50"] and not (
            got["prev_rank"] <= 5 and got["prev_above_ema50"]))
        assert got["x"] == bool(got["rank"] > 8 or not got["above_ema50"])


def test_missing_symbol_is_excluded_from_rank_not_forward_filled():
    t = 240 * TF
    books = {f"S{i}USDT": bars(i, end=t) for i in range(6)}
    books["S5USDT"] = books["S5USDT"][:-1]  # stale at this synchronized decision
    out = E.Edge04Tape().decide(View(books, t=t))
    recs = {r["s"]: r for r in out["r"]}
    assert "S5USDT" not in recs
    ranks = [r["q"]["H2"]["rank"] for r in recs.values()]
    assert sorted(ranks) == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_a_gap_restarts_warmup_instead_of_bridging_indicators():
    bs = bars(2)
    # Remove one of the last 181 bars.  The current candle remains present, but the
    # causal suffix is too short for ranking or momentum entries.
    del bs[-90]
    books = {"BTCUSDT": bars(0), "ETHUSDT": bs}
    out = E.Edge04Tape().decide(View(books))
    eth = next(r for r in out["r"] if r["s"] == "ETHUSDT")
    assert eth["q"]["H2"]["rank"] is None
    assert eth["q"]["H2"]["e"] is False
    assert eth["q"]["H3"]["e"] is False and eth["q"]["H4"]["e"] is False


def test_signals_use_closed_bars_and_next_open_is_only_a_fill_capability():
    books = {"BTCUSDT": bars(0), "ETHUSDT": bars(2)}
    a = E.Edge04Tape().decide(View(books, nxt=101.0))
    b = E.Edge04Tape().decide(View(books, nxt=9999.0))
    assert [r["q"] for r in a["r"]] == [r["q"] for r in b["r"]]
    assert [r["n"] for r in a["r"]] == [101.0, 101.0]
    assert [r["n"] for r in b["r"]] == [9999.0, 9999.0]


def test_fresh_tape_resets_all_split_state():
    first = {"BTCUSDT": bars(1), "ETHUSDT": bars(3)}
    second = {"BTCUSDT": bars(7, end=239 * TF), "ETHUSDT": bars(8, end=239 * TF)}
    used = E.Edge04Tape()
    used.decide(View(first))
    expected = E.Edge04Tape().decide(View(second))
    # A split is evaluated in a new sandbox/module, hence a new Edge04Tape.  Reusing the
    # old object at an overlapping clock is explicitly refused rather than leaking state.
    with pytest.raises(ValueError, match="non-decreasing"):
        used.decide(View(second))
    assert expected["format"] == E.FORMAT and len(expected["r"]) == 2


def test_holdout_and_calibration_are_refused_before_access():
    p = A.plan(str(ROOT))
    assert E.WINDOW == 1500                         # exact engine fetch cap
    assert p.lookback_ms == 240 * TF                # frozen boundary purge/warm-up
    with pytest.raises(A.PR.PitRunError, match="development splits only"):
        A.development_window(p, "holdout")
    with pytest.raises(A.PR.PitRunError, match="development splits only"):
        A.development_window(p, "calibration")

    class Access:
        plan = p
        opened = False

        def open(self, *args, **kwargs):
            self.opened = True
            raise AssertionError("must not open")

    access = Access()
    with pytest.raises(A.PR.PitRunError):
        A.evaluate_split(access, "holdout", [], repo=str(ROOT))
    assert access.opened is False
    with pytest.raises(A.Edge04Error, match="decision schedule"):
        A.evaluate_split(access, "train", [0], repo=str(ROOT))
    assert access.opened is False


def test_binding_is_pinned_and_outputs_are_write_once(tmp_path):
    bind = A.binding(str(ROOT))
    assert bind["candidate_id"] == "edge04_legacy_reeval.v1"
    assert [h["id"] for h in bind["hypotheses"]] == ["H1", "H2", "H3", "H4"]
    assert bind["hypotheses"][0]["side"] == "short"
    assert bind["hypotheses"][0]["management"]["tp1_frac"] == 0.5
    assert bind["hypotheses"][2]["management"]["tp_r"] == 3.0
    assert bind["exchange_rules"]["environment"] == "mainnet"
    out = tmp_path / "edge04.json"
    doc = {"format": E.FORMAT, "attestation": "a" * 64}
    assert A.write_once(str(out), doc) == str(out)
    before = out.read_bytes()
    A.write_once(str(out), json.loads(json.dumps(doc)))
    assert out.read_bytes() == before
    with pytest.raises(A.Edge04Error, match="different content"):
        A.write_once(str(out), {**doc, "attestation": "b" * 64})


def test_static_dataset_subset_is_explicit_ordered_and_opt_in(monkeypatch):
    # Constructor policy is tested without opening any manifest bytes or market data.
    static = {"format": "x", "label": "x", "owner_approved": True, "digest": "u",
              "symbols": [{"symbol": s} for s in ("A", "HYPE", "B")]}
    # The validation before rank policy is unrelated here; stub it and build a minimal promotion manifest.
    monkeypatch.setattr(PR.M, "validate", lambda m: None)
    monkeypatch.setattr(PR.M, "promotion_eligible", lambda m: True)
    monkeypatch.setattr(PR.SU, "FORMAT", "x")
    monkeypatch.setattr(PR, "LABEL", "x")
    monkeypatch.setattr(PR.SU, "BOOK", "crypto")
    manifest = {"digest": "d", "loader_version": PR.M.ARCHIVE_LOADER, "files": []}
    with pytest.raises(PR.PitRunError, match="rank prefix"):
        PR.StaticDataset(manifest, "unused", static, ["A", "B"])
    ds = PR.StaticDataset(manifest, "unused", static, ["A", "B"], exact_subset=True)
    assert ds._all == frozenset({"A", "B"})
    with pytest.raises(PR.PitRunError, match="preserve the frozen rank order"):
        PR.StaticDataset(manifest, "unused", static, ["B", "A"], exact_subset=True)
