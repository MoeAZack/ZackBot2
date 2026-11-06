"""Fail-closed entrypoint for the C255 Stage-A prestage runner."""

from __future__ import annotations

import argparse

try:
    from tools.research.cointegrated_residual_reversion_c255_stagea import (
        EvaluationLocked,
        assert_real_evaluation_authorized,
        load_frozen_contract,
    )
except ImportError:  # direct script execution from repository root
    from cointegrated_residual_reversion_c255_stagea import (  # type: ignore
        EvaluationLocked,
        assert_real_evaluation_authorized,
        load_frozen_contract,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C255 Stage-A prestage (real evaluation locked)")
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
    print("C255_CONTRACT_VALID; REAL_EVALUATION_LOCKED; DEFAULT_OFF")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
