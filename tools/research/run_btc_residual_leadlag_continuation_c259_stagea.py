"""Locked CLI for the C259 causal evaluator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None,""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

try:
    from tools.research.btc_residual_leadlag_continuation_c259_stagea import (
        EvaluationLocked, assert_real_evaluation_authorized, load_frozen_contract,
    )
except ImportError:
    from btc_residual_leadlag_continuation_c259_stagea import (  # type: ignore
        EvaluationLocked, assert_real_evaluation_authorized, load_frozen_contract,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C259 Stage-A evaluator (default-OFF; PAPER-only)",allow_abbrev=False)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-contract", action="store_true")
    mode.add_argument("--evaluate", action="store_true")
    parser.add_argument("--store",type=Path)
    args = parser.parse_args(argv)
    load_frozen_contract()
    if args.evaluate:
        from tools.research import c259_runner as runner
        if args.store is None: parser.error("--evaluate requires the checksum-bound --store")
        try:
            packet=runner.evaluate_once(store=args.store)
        except EvaluationLocked as exc:
            parser.error(str(exc))
        print(json.dumps(packet,sort_keys=True,separators=(",", ":"),allow_nan=False))
        return 0
    if not args.validate_contract:
        parser.error("only --validate-contract is available until final protected authority pins merge")
    print("C259_CONTRACT_VALID; AUTHORITY_UNPINNED; EVALUATION_LOCKED; DEFAULT_OFF; PAPER_ONLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
