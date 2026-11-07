"""Fail-closed command line for the C258 Stage-A evaluator."""

from __future__ import annotations

import argparse

try:
    from tools.research.cross_sectional_relative_strength_continuation_c258_stagea import (
        EvaluationLocked, assert_real_evaluation_authorized, load_frozen_contract,
    )
except ImportError:
    from cross_sectional_relative_strength_continuation_c258_stagea import (  # type: ignore
        EvaluationLocked, assert_real_evaluation_authorized, load_frozen_contract,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C258 Stage-A evaluator (default OFF; PAPER-only)")
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
        parser.error("only --validate-contract is available until authority-v6 pins protected-merge")
    print("C258_CONTRACT_VALID; AUTHORITY_V6_OUTPUTS_UNPINNED; REAL_EVALUATION_LOCKED; DEFAULT_OFF; PAPER_ONLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
