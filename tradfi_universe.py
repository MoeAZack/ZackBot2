"""TRADFI-01 universe constants (T1). Pure data - no I/O.

TRADFI allow-list = TRADFI_GOLD + TRADFI_STOCKS. XAUUSDT is a TRADIFI_PERPETUAL contract on Binance futures and is admitted
only through feasibility.rules_from_exchange_info(..., allow_tradfi=True); PAXGUSDT is a normal PERPETUAL and passes the
ordinary path. Oil is disabled in this slice; natural gas, leveraged ETFs (SOXL/SOXS) and other names are deliberately absent.
"""

TRADFI_GOLD = ['XAUUSDT', 'PAXGUSDT']
GOLD_FAMILY = frozenset(TRADFI_GOLD)

TRADFI_STOCKS = ['SPYUSDT', 'QQQUSDT', 'NVDAUSDT', 'TSLAUSDT', 'AMZNUSDT', 'METAUSDT', 'GOOGLUSDT', 'PLTRUSDT',
                 'COINUSDT', 'MSTRUSDT', 'HOODUSDT', 'MUUSDT']
STOCK_SET = frozenset(TRADFI_STOCKS)
HIGH_BETA = frozenset({'NVDAUSDT', 'TSLAUSDT', 'PLTRUSDT', 'COINUSDT', 'MSTRUSDT', 'HOODUSDT'})

TRADFI_OIL = []
OIL_SET = frozenset(TRADFI_OIL)
TRADFI_OIL_ENABLED = False

TRADFI_ALLOW_LIST = TRADFI_GOLD + TRADFI_STOCKS


def check_oil_flag(v):
    """Validator for an oil-enable setting. In TRADFI-01 T1/T2 oil cannot be enabled: True raises ValueError."""
    if v is True or (not isinstance(v, bool) and bool(v)):
        raise ValueError('TRADFI oil is disabled in this slice (TRADFI_OIL_ENABLED must be False)')
    return False
