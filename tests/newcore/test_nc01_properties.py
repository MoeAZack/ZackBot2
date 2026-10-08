"""NC-01 properties (contract r2 6.2): deterministic, seeded stdlib generation - no test dependency.

Every property draws its cases from random.Random(case_seed(SEED, i)); the property's SEED is written next to it and a
failure message names the seed and the case index, so `random.Random(case_seed(SEED, i))` replays the failing example
exactly. Covers codec round trip, invalid-state rejection, single-node document mutation, Decimal / tick / step
boundaries, ownership totality, priority, reason/action fit, and a broad wrong-type constructor sweep."""
import copy
import dataclasses
import random
import typing
from decimal import Decimal as D

import pytest

import nc01_factories as F
from nc01_factories import replace
from newcore.domain import (Action, DomainError, IntentState, InvalidRecord, OwnershipUnknown, ReasonCode, Rounding,
                            decode_document, dumps, encode_document, loads, owned_client_ids, ownership_families)
from newcore.domain.base import CTX, canonical_decimal, dec_str, field_spec
from newcore.domain.codec import DECIMAL_RE

SEEDS = (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 2026, 20261008)


def case_seed(seed, i):
    return seed * 1_000_003 + i


def cases(seed, n):
    for i in range(n):
        yield i, random.Random(case_seed(seed, i))


def where(seed, i):
    return f'seed={seed} case={i}: replay with random.Random(case_seed({seed}, {i}))'


# ----------------------------------------------------------------------------------------------------------- codec
@pytest.mark.parametrize('s', SEEDS)
def test_seeded_portfolio_fixtures_round_trip(s):
    p = F.gen_portfolio(random.Random(s))
    text = dumps(p)
    assert loads(text) == p and dumps(loads(text)) == text
    assert F.gen_portfolio(random.Random(s)) == p                     # the seed reproduces the exact record


SEED_ROUND_TRIP = 20261008_01


def test_property_round_trip_is_exact_and_canonical():
    for i, rng in cases(SEED_ROUND_TRIP, 80):
        p = F.gen_portfolio(rng)
        text = dumps(p)
        back = loads(text.encode('utf-8'))
        assert back == p and dumps(back) == text, where(SEED_ROUND_TRIP, i)


def _leaves(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _leaves(v, path + (i,))
    yield path


HOSTILE = [1.5, float('nan'), True, False, None, -1, 0, 2 ** 64, '', 'x', '1e3', 'NaN', '-0', '1.0', '+1', ' 1', '١',
           [], {}, '2026-10-08T00:00:00Z', 'acct_' + '0' * 32, 'lot_' + 'f' * 32, 'evt_' + '1' * 32]
SEED_MUTATION = 20261008_02


def test_property_any_single_mutation_fails_controlled_or_stays_canonical():
    """Mutating any one node of a valid document either raises a typed DomainError or yields a record whose canonical
    encoding is exactly the mutated document: never a silent coercion, never an untyped crash (contract 5)."""
    for i, rng in cases(SEED_MUTATION, 400):
        doc = encode_document(F.gen_portfolio(rng))
        paths = [x for x in _leaves(doc['body']) if x]
        path, value = rng.choice(paths), rng.choice(HOSTILE)
        node = doc['body']
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = copy.deepcopy(value)
        try:
            rec = decode_document(doc)
        except DomainError:
            continue
        except Exception as ex:                                       # pragma: no cover - the failure being guarded
            raise AssertionError(f'untyped {type(ex).__name__} at {path} = {value!r}; {where(SEED_MUTATION, i)}')
        assert encode_document(rec) == doc, f'silent coercion at {path} = {value!r}; {where(SEED_MUTATION, i)}'


# ----------------------------------------------------------------------------------------------------------- invalid states
def _break_ledger(p, rng):
    """A lot whose quantity no longer matches its fill ledger (the legacy quantity-only change)."""
    if not p.positions:
        return None
    lt = rng.choice(rng.choice(p.positions).lots)
    return lambda: replace(lt, qty=lt.qty + 1)


def _dangling_owner(p, rng):
    ws = [i for i in p.intents if i.owner_id and not i.orphan]
    if not ws:
        return None
    it = rng.choice(ws)
    gone = replace(it, owner_id=F.Ids(rng.getrandbits(32)).id(it.owner_id[:3].rstrip('_')))
    return lambda: replace(p, intents=tuple(gone if x is it else x for x in p.intents))


BREAKERS = {
    'unknown with collections': lambda p, rng: lambda: replace(p, ownership=F.Ownership.UNKNOWN, proof=None),
    'duplicate intent': lambda p, rng: (lambda: replace(p, intents=p.intents + (p.intents[0],))) if p.intents else None,
    'drop every intent': lambda p, rng: (lambda: replace(p, intents=())) if any(
        x.in_flight or x.stop.order or x.stop.replacement for x in p.lots) or p.entry_stops else None,
    'qty off the ledger': _break_ledger,
    'dangling owner reference': _dangling_owner,
    'lot of another account': lambda p, rng: (lambda: replace(p, account_id=F.Ids(rng.getrandbits(32)).id('acct')))
    if p.lots or p.intents else None,
    'pause without draining': lambda p, rng: (lambda: replace(p, entries_mode=F.EntriesMode.PAUSED,
                                                              pause_reasons=(ReasonCode.OPERATOR_PAUSE,))) if any(
        i.pullable and i.state is not IntentState.CANCELLING for i in p.intents) else None,
    'terminal intent kept': lambda p, rng: (lambda: replace(p, intents=(replace(p.intents[0], state=IntentState.FILLED),)
                                                            + p.intents[1:])) if p.intents else None,
    'empty but KNOWN': lambda p, rng: (lambda: replace(p, positions=(), intents=(), entry_stops=(),
                                                       ownership=F.Ownership.KNOWN)),
}
SEED_INVALID = 20261008_03


def test_property_invalid_states_are_always_rejected():
    hits = {k: 0 for k in BREAKERS}
    for i, rng in cases(SEED_INVALID, 300):
        p = F.gen_portfolio(rng)
        name = rng.choice(sorted(BREAKERS))
        build = BREAKERS[name](p, rng)
        if build is None:
            continue
        hits[name] += 1
        with pytest.raises(InvalidRecord):
            build()
            raise AssertionError(f'{name} accepted; {where(SEED_INVALID, i)}')
    assert all(hits.values()), hits                                 # every breaker actually exercised


# ----------------------------------------------------------------------------------------------------------- ownership
SEED_OWNERSHIP = 20261008_04


def test_property_position_qty_is_derived_and_ownership_total():
    for i, rng in cases(SEED_OWNERSHIP, 80):
        p = F.gen_portfolio(rng)
        for pos in p.positions:
            lots = list(pos.lots)
            rng.shuffle(lots)
            assert replace(pos, lots=tuple(lots)).qty == sum((x.qty for x in pos.lots), D(0)), where(SEED_OWNERSHIP, i)
        assert len(owned_client_ids(p)) == sum(len(x.client_ids) for x in p.intents), where(SEED_OWNERSHIP, i)
        fam = ownership_families(p)
        assert sorted(x for v in fam.values() for x in v) == sorted(x.intent_id for x in p.intents)


def test_property_unknown_is_never_empty():
    for i, rng in cases(20261008_05, 20):
        u = F.unknown_portfolio(F.Ids(rng.getrandbits(32)).id('acct'), rng.choice(list(F.HoldKind)))
        for f in (lambda: u.lots, lambda: owned_client_ids(u), lambda: ownership_families(u), lambda: u.intents_by_id()):
            with pytest.raises(OwnershipUnknown):
                f()
        assert u.positions is None and u.intents is None and u.entry_stops is None


# ----------------------------------------------------------------------------------------------------------- decisions
PROTECTING = {Action.PROTECT, Action.FLATTEN, Action.CLOSE, Action.REDUCE}
OPENING_ACTIONS = {Action.ADD, Action.ENTER}


def test_property_priority_puts_protection_before_new_risk():
    from newcore.domain.decision import PRIORITY
    for i, rng in cases(20261008_06, 300):
        actions = [rng.choice(list(Action)) for _ in range(rng.randint(0, 30))]
        order = sorted(actions, key=PRIORITY.__getitem__)
        last_protect = max((k for k, a in enumerate(order) if a in PROTECTING), default=-1)
        first_open = min((k for k, a in enumerate(order) if a in OPENING_ACTIONS), default=len(order))
        assert last_protect < first_open, where(20261008_06, i)


# Pinned independently of the implementation: which reason namespaces each action accepts.
ALLOWED = {
    Action.PROTECT: {'protect'}, Action.FLATTEN: {'exit'}, Action.CLOSE: {'exit'}, Action.REDUCE: {'exit'},
    Action.HALT: {'filter', 'operator', 'recovery', 'risk_gateway'},
    Action.PAUSE: {'operator', 'recovery', 'binding', 'connectivity', 'filter', 'reconcile'},
    Action.CANCEL_ENTRY: {'lifecycle', 'trailing', 'execution', 'filter', 'operator', 'capacity', 'risk_gateway'},
    Action.RECONCILE: {'reconcile', 'recovery', 'evidence', 'ownership', 'operator', 'binding'},
    Action.RESUME: {'operator'}, Action.ADD: {'entry'}, Action.ENTER: {'entry'},
    Action.WAIT: {r.namespace for r in ReasonCode},
    Action.SKIP: {'connectivity', 'input', 'side_mask', 'filter', 'capacity', 'regime', 'risk_gateway', 'config',
                  'execution', 'trailing', 'ownership', 'binding', 'recovery', 'reconcile', 'protect'},
}


@pytest.mark.parametrize('action', list(Action))
def test_reason_namespace_fits_the_action_exhaustively(action):
    for n, reason in enumerate(ReasonCode):
        ids = F.Ids(n)
        try:
            F.decision_with_intents(ids, ids.id('acct'), action, reason)
            built = True
        except InvalidRecord:
            built = False
        # r3a: reconcile.external_close only from a RECONCILE that books post-hoc intents (never a send); a late-fill
        # reconcile names its intent. The generic builder gives neither, so neither builds here.
        special = reason in (ReasonCode.RECONCILE_EXTERNAL_CLOSE, ReasonCode.RECONCILE_LATE_FILL)
        assert built is (reason.namespace in ALLOWED[action] and not special), (action, reason)


# ----------------------------------------------------------------------------------------------------------- numbers
def _floats_in_tree(node):
    if isinstance(node, float):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _floats_in_tree(v)
    elif isinstance(node, list):
        for v in node:
            yield from _floats_in_tree(v)


def test_property_no_float_ever_leaks_into_a_document():
    for i, rng in cases(20261008_07, 60):
        assert not list(_floats_in_tree(encode_document(F.gen_portfolio(rng)))), where(20261008_07, i)


STEPS = [D('0.001'), D('0.01'), D('0.1'), D('1'), D('5'), D('0.00000001'), D('0.25'), D('0.3')]
SEED_GRID = 20261008_08


def _number(rng):
    if rng.random() < 0.3:
        return rng.uniform(-1e6, 1e6)
    return D(rng.randint(-10 ** 15, 10 ** 15)).scaleb(-rng.randint(0, 9))


def test_property_quantize_respects_the_grid():
    for i, rng in cases(SEED_GRID, 3000):
        x, step = _number(rng), rng.choice(STEPS)
        r = replace(F.rules(), tick_size=step, step_size=step, min_qty=step, max_qty=step * 10 ** 6)
        exact = canonical_decimal(D(repr(x)) if isinstance(x, float) else x, 'x')
        down, up, near = (r.quantize_qty(x, m) for m in (Rounding.DOWN, Rounding.UP, Rounding.NEAREST))
        msg = f'x={x!r} step={step}; {where(SEED_GRID, i)}'
        for q in (down, up, near):
            assert r.on_step(q) and r.quantize_qty(q, Rounding.DOWN) == q, msg      # on grid, idempotent
        assert down <= exact <= up and up - down in (0, step), msg
        assert abs(near - exact) <= step / 2, msg
        assert r.quantize_price(x, Rounding.DOWN) == down, msg


SEED_DECIMAL = 20261008_09


def _spellings(rng):
    """A random bounded value and several numerically equal spellings of it (trailing zeroes, exponent forms)."""
    digits = rng.randint(1, 38)
    coeff = rng.randint(10 ** (digits - 1), 10 ** digits - 1) * rng.choice((1, -1))
    adjusted = rng.randint(-18, 18)
    value = D(coeff).scaleb(adjusted - (digits - 1), context=CTX)
    zeros = rng.randint(1, 3)
    return value, [value, value.quantize(D(1).scaleb(value.as_tuple().exponent - zeros), context=CTX),
                   D(f'{coeff}E{adjusted - (digits - 1)}')]


def test_property_decimal_text_is_exact_canonical_and_unique():
    for i, rng in cases(SEED_DECIMAL, 3000):
        value, spellings = _spellings(rng)
        canon = {canonical_decimal(v, 'x').as_tuple() for v in spellings}
        text = {dec_str(v) for v in spellings}
        msg = f'{spellings}; {where(SEED_DECIMAL, i)}'
        assert len(canon) == 1 and len(text) == 1, msg                  # one value, one encoding
        t = text.pop()
        assert DECIMAL_RE.fullmatch(t) and D(t) == value and len(t) <= 64, msg
        doc = encode_document(F.rules())
        doc['body']['min_notional'] = t
        if value >= 0:
            assert encode_document(decode_document(doc))['body']['min_notional'] == t, msg


@pytest.mark.parametrize('bad', [D('1e19'), D('-1e19'), D('1e-19'), D('1.' + '1' * 38), D('NaN'), D('sNaN'),
                                 D('Infinity'), D('-0'), D('-0.000')])
def test_decimal_bounds(bad):
    with pytest.raises(InvalidRecord):
        canonical_decimal(bad, 'x')


@pytest.mark.parametrize('x', [True, None, 'NaN', float('inf'), float('nan'), '1', 1e300])
def test_quantize_rejects_non_numbers(x):
    with pytest.raises(InvalidRecord):
        F.rules().quantize_qty(x, Rounding.DOWN)


def test_quantize_needs_an_explicit_rounding():
    with pytest.raises(InvalidRecord):
        F.rules().quantize_qty(D('1.234'), 'down')
    with pytest.raises(TypeError):
        F.rules().quantize_qty(D('1.234'))                            # no default rounding exists


def test_venue_checks_use_the_rules():
    r = F.rules()
    r.check_qty(D('0.05'))
    for bad in (D('0.015'), D('0.001'), D('20000')):
        with pytest.raises(InvalidRecord):
            r.check_qty(bad)
    r.check_qty(D('0.001') * 10, reduce_only=True)
    ids = F.Ids(4)
    it = F.maker_entry(ids, ids.id('acct'), symbol='SOLUSDT')
    r.check_intent(it)
    with pytest.raises(InvalidRecord, match='min notional'):
        r.check_intent(replace(it, qty=D('1')))
    with pytest.raises(InvalidRecord, match='post-only'):
        replace(r, capabilities=()).check_intent(it)


# ----------------------------------------------------------------------------------------------------------- one dialect
WRONG = [1.5, float('nan'), True, 7, 'x', '2026-10-08', b'1', [], {}, object(), D('1')]


def _acceptable(tp, v):
    """Would the strict dialect accept a value of this TYPE for the annotation (value checks aside)?"""
    args = typing.get_args(tp)
    if args and type(None) in args:
        return v is None or _acceptable(next(a for a in args if a is not type(None)), v)
    if typing.get_origin(tp) is tuple:
        return type(v) is tuple
    if isinstance(tp, type) and issubclass(tp, __import__('enum').Enum):
        return isinstance(v, tp)
    return type(v) is tp


def test_constructors_reject_every_wrong_type_like_the_codec():
    """Contract r2: constructors and codec share one strict dialect. For every field of every sample record, every
    value of a wrong TYPE (float, NaN, bool-as-int, int-as-Decimal, text, bytes, containers) is refused."""
    checked = 0
    for rec in F.samples() + [F.typical_portfolio().lots[0], F.typical_portfolio().positions[0]]:
        for name, tp, _ in field_spec(type(rec)):
            for v in WRONG:
                if _acceptable(tp, v):
                    continue
                checked += 1
                with pytest.raises(InvalidRecord):
                    dataclasses.replace(rec, **{name: v})
                    raise AssertionError(f'{type(rec).__name__}.{name} accepted {v!r}')
    assert checked > 1500
