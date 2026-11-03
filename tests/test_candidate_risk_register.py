import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "research_evidence" / "candidate_risk_register.v1.json"


def test_risk_register_is_explicit_and_default_safe():
    doc = json.loads(REGISTER.read_text(encoding="utf-8"))
    rows = {row["key"]: row for row in doc["candidates"]}
    assert rows["rotation_long_h2"]["tier"] == "extended_validation"
    assert rows["volatility_acceleration_long_1h"]["risk_label"] == "high"
    assert rows["volatility_acceleration_long_1h"]["max_dd_r"] == 43.682092
    assert all(row["tier"] != "paper_active" for row in rows.values())


def test_risk_scaling_caps_high_drawdown_candidates():
    doc = json.loads(REGISTER.read_text(encoding="utf-8"))
    for row in doc["candidates"]:
        dd = row.get("max_dd_r")
        cap = row.get("risk_multiplier_cap")
        if row["tier"] == "extended_validation":
            assert dd and dd > 15
            assert 0 < cap <= min(1.0, 15.0 / dd + 0.001)
