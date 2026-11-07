"""Locked C257 protected-evaluation CLI."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from tools.research import funding_shock_reversal_xsec_c257_protected_adapter as A
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-locked", action="store_true")
    mode.add_argument("--evaluate-once", action="store_true")
    parser.add_argument("--store", type=Path)
    args = parser.parse_args(argv)
    if args.evaluate_once and args.store is None:
        parser.error("--evaluate-once requires the checksum-bound --store")
    packet = A.evaluate_once(store=args.store) if args.evaluate_once else A.validate_locked()
    print(json.dumps(packet, sort_keys=True, separators=(",", ":")))
    return 0
if __name__ == "__main__": raise SystemExit(main())
