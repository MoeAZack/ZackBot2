"""Locked C257 protected-evaluation CLI."""
from __future__ import annotations
import argparse, json
from tools.research import funding_shock_reversal_xsec_c257_protected_adapter as A
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--validate-locked", action="store_true")
    args = parser.parse_args(argv)
    if not args.validate_locked:
        parser.error("evaluation requires the protected sealed-loader entrypoint")
    print(json.dumps(A.validate_locked(), sort_keys=True, separators=(",", ":")))
    return 0
if __name__ == "__main__": raise SystemExit(main())
