"""Command-line guard shared by the owner tools (tools/newcore_keys.py, tools/newcore_smoke.py).

Keys and secrets must never be passed as arguments (shell history, process list). An argument is refused when it is a
key/secret/token/password flag, or when it looks like a key (a run of 24+ letters/digits that is not a path).

The --account-id value is the one long token that legitimately appears: it must be a dashed UUID (8-4-4-4-12 hex).
A UUID is exempt from the key heuristic (Cowork round 2: a valid id was refused as a key); anything else given as
--account-id is refused with an account-id message and never echoed, because it might be a pasted key.
"""
import re

ACCOUNT_UUID_RE = re.compile(r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}'
                             r'|acct_[0-9a-f]{32}')       # or the runner's NC-01 AccountId (account.id in the run config)
_SECRET_FLAG = re.compile(r'-{1,2}(api[-_]?key|key|apikey|secret|api[-_]?secret|password|passwd|token)(=.*)?',
                          re.I | re.S)
_TOKEN_LIKE = re.compile(r'[A-Za-z0-9]{24,}')
KEY_REFUSAL = 'keys are never passed on the command line; type them at the hidden prompts of tools/newcore_keys.py'
ACCOUNT_REFUSAL = ('--account-id must be a dashed UUID (8-4-4-4-12 hex digits) or the runner AccountId '
                   '(acct_ + 32 lowercase hex)')


def is_account_uuid(value):
    return isinstance(value, str) and ACCOUNT_UUID_RE.fullmatch(value) is not None


def _looks_like_key(arg):
    return bool(_SECRET_FLAG.fullmatch(arg)) or (not any(c in arg for c in '\\/:') and bool(_TOKEN_LIKE.search(arg)))


def argv_refusal(argv):
    """None when argv is acceptable, else a refusal message (which never contains an argument value)."""
    args = list(argv)
    i = 0
    while i < len(args):
        a = args[i]
        if not isinstance(a, str):
            return KEY_REFUSAL
        if a == '--account-id':
            if i + 1 < len(args):
                if not is_account_uuid(args[i + 1]):
                    return ACCOUNT_REFUSAL
                i += 2
                continue
        elif a.startswith('--account-id='):
            if not is_account_uuid(a.split('=', 1)[1]):
                return ACCOUNT_REFUSAL
            i += 1
            continue
        if _looks_like_key(a):
            return KEY_REFUSAL
        i += 1
    return None
