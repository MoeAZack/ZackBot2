"""Fail-closed command line for the C258 Stage-A evaluator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

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
    parser.add_argument("--synthetic-evaluate", action="store_true")
    parser.add_argument("--authority-pins", type=Path)
    parser.add_argument("--authority-root", type=Path)
    parser.add_argument("--archive-store", type=Path)
    parser.add_argument("--claim-directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    load_frozen_contract()
    if args.evaluate:
        if args.synthetic_evaluate or args.authority_pins or args.authority_root:
            parser.error("production evaluation uses only protected amendment authority")
        try:
            assert_real_evaluation_authorized()
        except EvaluationLocked as exc:
            parser.error(str(exc))
        if not args.archive_store or not args.output:
            parser.error("evaluation requires an archive store and output path")
        from tools.research.c258_executable import run_archive_evaluation
        result = run_archive_evaluation(None, args.archive_store, args.claim_directory)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False), encoding="utf-8")
        print(result["result"]["verdict"]+"; DEFAULT_OFF; PAPER_ONLY")
        return 0
    if args.synthetic_evaluate:
        if not all((args.authority_pins, args.authority_root, args.archive_store,
                    args.claim_directory, args.output)):
            parser.error("synthetic replay requires pins, separate authority root, store, claim directory and output")
        from tools.research import cross_sectional_relative_strength_continuation_c258_stagea as C
        from tools.research.c258_executable import run_archive_evaluation
        raw = json.loads(args.authority_pins.read_text(encoding="utf-8"))
        pins = C.AuthorityPins(raw["protected_head"], **{
            name: C.ArtifactPin(**raw[name]) for name in C.EXPECTED_AUTHORITY_PATHS})
        result = run_archive_evaluation(pins, args.archive_store, args.claim_directory,
                                       root=args.authority_root, synthetic=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False), encoding="utf-8")
        print(result["result"]["verdict"]+"; SYNTHETIC_ONLY; DEFAULT_OFF; PAPER_ONLY")
        return 0
    if not args.validate_contract:
        parser.error("only --validate-contract is available until authority-v6 pins protected-merge")
    print("C258_CONTRACT_VALID; AUTHORITY_V6_OUTPUTS_UNPINNED; REAL_EVALUATION_LOCKED; DEFAULT_OFF; PAPER_ONLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
