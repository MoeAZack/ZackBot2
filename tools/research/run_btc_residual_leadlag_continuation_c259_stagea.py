"""Locked CLI for the C259 causal evaluator."""

from __future__ import annotations

import argparse

try:
    from tools.research.btc_residual_leadlag_continuation_c259_stagea import (
        EvaluationLocked, assert_real_evaluation_authorized, load_frozen_contract,
    )
except ImportError:
    from btc_residual_leadlag_continuation_c259_stagea import (  # type: ignore
        EvaluationLocked, assert_real_evaluation_authorized, load_frozen_contract,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C259 Stage-A evaluator (default-OFF; PAPER-only)")
    parser.add_argument("--validate-contract", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    args = parser.parse_args(argv)
    load_frozen_contract()
    if args.evaluate:
        try:
            assert_real_evaluation_authorized()
        except EvaluationLocked as exc:
            parser.error(str(exc))
    if not args.validate_contract:
        parser.error("only --validate-contract is available until final protected authority pins merge")
    print("C259_CONTRACT_VALID; AUTHORITY_UNPINNED; EVALUATION_LOCKED; DEFAULT_OFF; PAPER_ONLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
