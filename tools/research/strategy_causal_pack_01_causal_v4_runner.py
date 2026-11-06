"""Provisional causal-v4 PACK entry point; execution is intentionally impossible."""
from tools.research.causal_v4_runner_rebind import blocked_main


def main(argv=None) -> int:
    return blocked_main("pack", argv)


if __name__ == "__main__":
    raise SystemExit(main())
