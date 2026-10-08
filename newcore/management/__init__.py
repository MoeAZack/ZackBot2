"""NC-07 / slice S2: the pure position-management core.

Pure stdlib + newcore.domain (enforced by tests/newcore/test_nc01_import_boundary.py, which scans all of newcore/**):
no IO, clock, randomness or venue access.

    plan     ManagementPlan (fixed at the confirmed entry fill; planned risk incl. the reserved add <= cap),
             build_plan, planned_risk, admit_entry (cost-to-stop veto, default 0.15R)
    state    PositionState (frozen; the stop covers the position; applied fill log + racing allowances), ConfirmedFill
             (venue fill id), Rejected, Cancelled, Candle, Leg
    actions  ManagementAction + the one order protect > close > reduce > add > release
    core     step(plan, state, confirmed, closed_candle) -> Step(state, actions); initial_state, replay, realized_pnl,
             exit_ledger (R / PnL per exit leg + running), risk_to_stop
    sim      zb-path/1 candle simulator driving the same step (gap at the open, stop-first ambiguity)
    presets  RANGE-BB-MR.v1 (disabled by default, mechanics only, 4h)

What the runner (NC-08) must persist for restart: the ManagementPlan, the latest PositionState (or the StepInput log
that `replay` folds into it: every ConfirmedFill / Rejected batch, every closed Candle, close requests and funding, in
order), and the map leg -> OrderIntent id of the orders it created from the actions. On restart it reconciles those
intents with the venue (NC-01 lifecycle) and resumes calling step; it never re-derives the plan from market data.

Runner obligations (HOLD and confirmation stay outside the core, Codex ruling 10): the core emits REQUESTS. The runner
counts only CONFIRMED (WORKING) stop coverage as protection, sends no ADD until the stop covering the position is
confirmed, keeps an old stop until its replacement is confirmed, dedupes fills by venue fill id before calling step,
reports `Cancelled(leg)` once a cancel is confirmed, and applies hard-HOLD (protect / close / reduce only) itself.
"""
from .actions import ActionKind, ManagementAction, Tier, ordered
from .core import (ExitLedger, ExitLeg, Step, StepInput, average, exit_ledger, initial_state, realized_pnl, replay,
                   risk_to_stop, step)
from .plan import (Admission, AdmissionVerdict, CostModel, ManagementError, ManagementPlan, PlanBuild, PlanNote,
                   PlanRefused, admit_entry, build_plan, planned_risk)
from .presets import range_bb_mr_v1
from .sim import SimRun, SimStep, path_points, run, simulate_candle
from .state import AddPhase, Cancelled, Candle, ConfirmedFill, Leg, LegQty, Order, PositionState, Rejected, Stage
