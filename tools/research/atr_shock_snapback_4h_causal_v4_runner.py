"""Provisional causal-v4 ATR-shock entry point; execution is intentionally impossible."""
from tools.research.causal_v4_runner_rebind import blocked_main


def main(argv=None) -> int:
    return blocked_main("atr_shock", argv)


if __name__ == "__main__":
    raise SystemExit(main())
