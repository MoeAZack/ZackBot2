"""`python -m newcore.run`: the NEWCORE runnable entry point (see newcore/runner/app.py). PAPER / TESTNET only."""
import sys

from newcore.runner.app import main

if __name__ == '__main__':
    sys.exit(main())
