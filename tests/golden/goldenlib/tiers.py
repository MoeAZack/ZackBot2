"""Golden pack tiers (Codex call on 0ef7402): TIERS.json is a CHECKED manifest that puts every case in exactly one tier.

- core     : runs per commit (verify fast = pytest -m "not slow"). Representative long and short paths and one case per
             critical ownership / protection class, each listed with the reason it is core. Target CORE_TARGET_S.
- extended : every other mirror, fault and adapter case. test_golden.py marks these params `slow` FROM THIS FILE, so they run
             at the accepted exact-head full gate (verify full runs every test). Target EXTENDED_TARGET_S.
The 120 s hard stop covers both tiers together. The tier of a case comes only from TIERS.json (never an ad-hoc marker): a case in
neither tier, in both, or an id that is not a case is a TierError, reported as a failing test in BOTH tiers, so a case can never
silently disappear from the gate. Moving a case between tiers is a reviewed edit of TIERS.json; it changes no contract hash.
"""
import os

from . import GOLDEN_DIR, schema

TIERS_PATH = os.path.join(GOLDEN_DIR, 'TIERS.json')
SCHEMA = 'zb-golden-tiers/1'
TIERS = ('core', 'extended')
CORE_TARGET_S = 30.0
EXTENDED_TARGET_S = 90.0
HARD_STOP_S = 120.0                                  # both tiers together, whatever the selection or order
REASON_MIN = 20                                      # a core entry states why it is core


class TierError(ValueError):
    """TIERS.json does not put every case in exactly one tier (or is malformed). Always a hard failure."""


def read(path=TIERS_PATH):
    try:
        with open(path, encoding='utf-8-sig') as f:
            return schema.strict_loads(f.read())
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise TierError(f'{os.path.basename(path)}: not a readable JSON tier manifest ({type(e).__name__}: {e})') from None


def check(doc, case_ids):
    """doc: the parsed TIERS.json; case_ids: every case id in cases/. -> {case id: tier}. Raises TierError on any defect."""
    if not isinstance(doc, dict) or set(doc) != {'schema', 'note', 'core', 'extended'}:
        raise TierError(f"TIERS.json: exactly the keys ['core', 'extended', 'note', 'schema'], got "
                        f"{sorted(doc) if isinstance(doc, dict) else type(doc).__name__}")
    if doc['schema'] != SCHEMA:
        raise TierError(f"TIERS.json: schema must be {SCHEMA!r}, got {doc['schema']!r}")
    core, ext = doc['core'], doc['extended']
    if not isinstance(core, dict):
        raise TierError('TIERS.json: core is {case id: the reason it is core}')
    bad = sorted(k for k, v in core.items() if not isinstance(v, str) or len(v.strip()) < REASON_MIN)
    if bad:
        raise TierError(f'TIERS.json: core entries without a reason of at least {REASON_MIN} characters: {bad}')
    if not isinstance(ext, list) or not all(isinstance(x, str) for x in ext):
        raise TierError('TIERS.json: extended is a list of case ids')
    dup = sorted({x for x in ext if ext.count(x) > 1})
    if dup:
        raise TierError(f'TIERS.json: listed twice in extended: {dup}')
    both = sorted(set(core) & set(ext))
    if both:
        raise TierError(f'TIERS.json: cases listed in BOTH tiers (each case is in exactly one): {both}')
    listed = set(core) | set(ext)
    neither = sorted(set(case_ids) - listed)
    if neither:
        raise TierError(f'TIERS.json: cases in NEITHER tier (they would silently leave the gate): {neither}')
    unknown = sorted(listed - set(case_ids))
    if unknown:
        raise TierError(f'TIERS.json: ids that are not golden cases: {unknown}')
    return {**{c: 'core' for c in core}, **{c: 'extended' for c in ext}}


def load(case_ids=None):
    """{case id: tier} for the pack on disk (case ids from the case file names, so a broken case cannot hide a tier error)."""
    if case_ids is None:
        case_ids = [os.path.basename(p)[:-5] for p in schema.case_paths()]
    return check(read(), case_ids)


RUNNABLE = ('required', 'known_divergence')
LEGACY = ('legacy_backtest', 'legacy_engine')
_SLOT_PLAIN = ('id', 'sides', 'risk', 'share', 'symbols', 'stop', 'entry', 'max_pos')


def divergence_class(d):
    """The stable class of one recorded known divergence: the adapter it binds plus its ticket and finding id (the recorded
    defect). The case that carries it is that defect's xfail(strict) guard on that adapter."""
    return f"divergence:{d['adapter']}:{d['ticket']}:{d['finding']}"


def classes(case):
    """The coverage classes a case exercises, derived from the case file (never hand-listed): every expected exit code, every
    fault kind, every non-trivial slot feature, the entry type, per legacy adapter the side it runs (runnable only), every
    `behaviours` tag (Codex pre-review of 3f4abea #2: a new causal behaviour placed only in extended fails the per-commit
    coverage contract) and every recorded known divergence (adapter, ticket, finding): a case that is the ONLY guard of a
    recorded defect on an adapter must run per commit."""
    out = {f'exit:{t["exit"]}' for t in case['expect']['trades']}
    out |= {f'fault:{f["kind"]}' for f in case['faults']}
    out |= {f'slot:{k}' for k in case['slot'] if k not in _SLOT_PLAIN}
    out.add(f"entry:{(case['slot'].get('entry') or {}).get('type', 'market')}")
    out |= {f'behaviour:{b}' for b in case['behaviours']}
    out |= {divergence_class(d) for d in case['known_divergences']}
    for ad in LEGACY:
        if schema.status(case, ad) in RUNNABLE:
            out.add(f"{ad}:{case['side']}")
        if schema.status(case, ad) == 'required':
            out.add(f'{ad}:required')
    return out


def core_gaps(cases, tier_of):
    """Classes the pack exercises somewhere but the core tier does not (must be empty): core keeps representative long and
    short paths on each legacy adapter, a required (passing) case per legacy adapter, and one case per exit code, fault kind,
    slot feature, entry type, behaviour tag and recorded known divergence (adapter, ticket, finding)."""
    every, core = set(), set()
    for c in cases:
        k = classes(c)
        every |= k
        if tier_of[c['id']] == 'core':
            core |= k
    need = {f'{ad}:{s}' for ad in LEGACY for s in ('long', 'short', 'required')}
    return sorted((every | need) - core)
