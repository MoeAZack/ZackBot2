"""Fail-closed CLI for the C257 synthetic-only Stage-A prestage."""

from __future__ import annotations

import argparse

try:
    from tools.research.funding_shock_reversal_xsec_c257_stagea import (
        EvaluationLocked,
        assert_real_evaluation_authorized,
        load_frozen_contract,
    )
except ImportError:  # direct script execution from repository root
    from funding_shock_reversal_xsec_c257_stagea import (  # type: ignore
        EvaluationLocked,
        assert_real_evaluation_authorized,
        load_frozen_contract,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C257 Stage-A prestage (real evaluation locked)")
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
        parser.error("only --validate-contract is available on this prestage head")
    print("C257_CONTRACT_VALID; REAL_EVALUATION_LOCKED; DEFAULT_OFF; PAPER_ONLY; SEALED_HOLDOUT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
