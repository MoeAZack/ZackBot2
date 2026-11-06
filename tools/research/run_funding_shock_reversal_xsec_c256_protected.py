"""CLI entry point for the fail-closed C256 protected-evaluation adapter."""
from __future__ import annotations

import sys

try:
    from tools.research.funding_shock_reversal_xsec_c256_protected_adapter import (
        ProtectedAdapterError,
        guarded_dispatch,
    )
except ImportError:  # direct execution from repository root
    from funding_shock_reversal_xsec_c256_protected_adapter import (  # type: ignore
        ProtectedAdapterError,
        guarded_dispatch,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        return guarded_dispatch(sys.argv[1:] if argv is None else argv)
    except ProtectedAdapterError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
