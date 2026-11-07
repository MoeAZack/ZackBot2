"""Provisional causal-v4 RBR-short entry point; execution is intentionally impossible."""
from tools.research.causal_v4_runner_rebind import blocked_main


def main(argv=None) -> int:
    return blocked_main("rbr_short", argv)


if __name__ == "__main__":
    raise SystemExit(main())
