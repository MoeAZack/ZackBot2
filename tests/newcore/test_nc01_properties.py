"""NC-01 property tests (contract 6.2): pinned Hypothesis with a recorded @seed on every property, plus explicit seeded
fixtures (stdlib random.Random with the seeds listed in SEEDS) for codec round trips, invalid-state rejection and
Decimal / tick / step boundaries."""
import copy
import random
from decimal import Decimal as D

import pytest
from hypothesis import given, seed, strategies as st

import nc01_factories as F
from nc01_factories import replace
from newcore.domain import (Action, DomainError, IntentState, InvalidRecord, OwnershipUnknown, ReasonCode, Rounding,
                            decode_document, dumps, encode_document, loads, owned_client_ids, ownership_families)
from newcore.domain.base import dec_str
from newcore.domain.codec import DECIMAL_RE

SEEDS = (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 2026, 20261008)
portfolios = st.randoms(use_true_random=False).map(F.gen_portfolio)


# ----------------------------------------------------------------------------------------------------------- codec
@pytest.mark.parametrize('s', SEEDS)
def test_seeded_portfolio_fixtures_round_trip(s):
    p = F.gen_portfolio(random.Random(s))
    text = dumps(p)
    assert loads(text) == p and dumps(loads(text)) == text
    assert F.gen_portfolio(random.Random(s)) == p                     # the seed reproduces the exact record


@seed(1001)
@given(portfolios)
def test_property_round_trip_is_exact_and_canonical(p):
    text = dumps(p)
    back = loads(text)
    assert back == p and dumps(back) == text


def _leaves(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _leaves(v, path + (i,))
    yield path


HOSTILE = [1.5, float('nan'), True, False, None, -1, 0, 2 ** 64, '', 'x', '1e3', 'NaN', '-0', [], {}, '2026-10-08T00:00:00Z',
           'acct_' + '0' * 32, 'lot_' + 'f' * 32]


@seed(1002)
@given(portfolios, st.data())
def test_property_any_single_mutation_fails_controlled_or_stays_canonical(p, data):
    """Mutating any one node of a valid document either raises a typed DomainError or yields a record whose canonical
    encoding is exactly the mutated document: never a silent coercion, never an untyped crash (contract 5)."""
    doc = encode_document(p)
    paths = [x for x in _leaves(doc['body']) if x]
    path = data.draw(st.sampled_from(paths))
    value = data.draw(st.sampled_from(HOSTILE))
    mutated = copy.deepcopy(doc)
    node = mutated['body']
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = copy.deepcopy(value)
    try:
        rec = decode_document(mutated)
    except DomainError:
        return
    assert encode_document(rec) == mutated


# ----------------------------------------------------------------------------------------------------------- invalid states
def _break_ledger(p, rng):
    """A lot whose quantity no longer matches its fill ledger (the legacy quantity-only change)."""
    if not p.positions:
        return None
    lt = rng.choice(rng.choice(p.positions).lots)
    return lambda: replace(lt, qty=lt.qty + 1)


BREAKERS = {
    'unknown with collections': lambda p, rng: lambda: replace(p, ownership=F.Ownership.UNKNOWN, proof=None),
    'duplicate intent': lambda p, rng: (lambda: replace(p, intents=p.intents + (p.intents[0],))) if p.intents else None,
    'drop every intent': lambda p, rng: (lambda: replace(p, intents=())) if any(
        x.in_flight or x.stop.order or x.stop.replacement for x in p.lots) or p.entry_stops else None,
    'qty off the ledger': _break_ledger,
    'lot of another account': lambda p, rng: (lambda: replace(p, account_id=F.Ids(rng.getrandbits(32)).id('acct'))) if p.lots or p.intents else None,
    'pause without draining': lambda p, rng: (lambda: replace(p, entries_mode=F.EntriesMode.PAUSED,
                                                              pause_reasons=(ReasonCode.OPERATOR_PAUSE,))) if any(
        i.pullable and i.state is not IntentState.CANCELLING for i in p.intents) else None,
    'terminal intent kept': lambda p, rng: (lambda: replace(p, intents=(replace(p.intents[0], state=IntentState.FILLED),)
                                                            + p.intents[1:])) if p.intents else None,
}


@seed(1003)
@given(portfolios, st.sampled_from(sorted(BREAKERS)), st.randoms(use_true_random=False))
def test_property_invalid_states_are_always_rejected(p, name, rng):
    build = BREAKERS[name](p, rng)
    if build is None:
        return
    with pytest.raises(InvalidRecord):
        build()


# ----------------------------------------------------------------------------------------------------------- ownership
@seed(1004)
@given(portfolios, st.randoms(use_true_random=False))
def test_property_position_qty_is_derived(p, rng):
    for pos in p.positions:
        lots = list(pos.lots)
        rng.shuffle(lots)
        assert replace(pos, lots=tuple(lots)).qty == sum((x.qty for x in pos.lots), D(0))


@seed(1005)
@given(portfolios)
def test_property_ownership_is_total_and_unique(p):
    ids = owned_client_ids(p)
    assert len(ids) == sum(len(i.client_ids) for i in p.intents)
    fam = ownership_families(p)
    flat = [x for v in fam.values() for x in v]
    assert sorted(flat) == sorted(i.intent_id for i in p.intents)


@seed(1006)
@given(st.randoms(use_true_random=False), st.sampled_from(list(F.HoldKind)))
def test_property_unknown_is_never_empty(rng, kind):
    u = F.unknown_portfolio(F.Ids(rng.getrandbits(32)).id('acct'), kind)
    for f in (lambda: u.lots, lambda: owned_client_ids(u), lambda: ownership_families(u), lambda: u.intents_by_id()):
        with pytest.raises(OwnershipUnknown):
            f()
    assert u.positions is None and u.intents is None and u.entry_stops is None


# ----------------------------------------------------------------------------------------------------------- decisions
PROTECTING = {Action.PROTECT, Action.FLATTEN, Action.CLOSE, Action.REDUCE}
OPENING_ACTIONS = {Action.ADD, Action.ENTER}


@seed(1007)
@given(st.lists(st.sampled_from(list(Action)), max_size=30), st.randoms(use_true_random=False))
def test_property_priority_puts_protection_before_new_risk(actions, rng):
    from newcore.domain.decision import PRIORITY
    order = sorted(actions, key=PRIORITY.__getitem__)
    last_protect = max((i for i, a in enumerate(order) if a in PROTECTING), default=-1)
    first_open = min((i for i, a in enumerate(order) if a in OPENING_ACTIONS), default=len(order))
    assert last_protect < first_open


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


@seed(1008)
@given(st.sampled_from(list(Action)), st.sampled_from(list(ReasonCode)), st.integers(0, 2 ** 32))
def test_property_reason_namespace_fits_the_action(action, reason, s):
    ids = F.Ids(s)
    try:
        F.decision_with_intents(ids, ids.id('acct'), action, reason)
        built = True
    except InvalidRecord:
        built = False
    assert built is (reason.namespace in ALLOWED[action])


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


@seed(1009)
@given(portfolios)
def test_property_no_float_ever_leaks_into_a_document(p):
    assert not list(_floats_in_tree(encode_document(p)))


STEPS = [D('0.001'), D('0.01'), D('0.1'), D('1'), D('5'), D('0.00000001'), D('0.25')]
magnitudes = st.decimals(min_value=D('-999999'), max_value=D('999999'), allow_nan=False, allow_infinity=False, places=9)


@seed(1010)
@given(st.one_of(magnitudes, st.floats(min_value=-1e6, max_value=1e6, allow_nan=False)), st.sampled_from(STEPS))
def test_property_quantize_respects_the_grid(x, step):
    r = F.rules()
    r = replace(r, tick_size=step, step_size=step, min_qty=step, max_qty=step * 10 ** 6)
    exact = D(repr(x)) if isinstance(x, float) else x
    down, up, near = (r.quantize_qty(x, m) for m in (Rounding.DOWN, Rounding.UP, Rounding.NEAREST))
    for q in (down, up, near):
        assert q % step == 0 and r.on_step(q)
        assert r.quantize_qty(q, Rounding.DOWN) == q                  # idempotent
    assert down <= exact <= up and up - down in (0, step)
    assert abs(near - exact) <= step / 2
    assert r.quantize_price(x, Rounding.DOWN) == down


@seed(1011)
@given(st.decimals(min_value=D('-999999999999999'), max_value=D('999999999999999'), allow_nan=False, allow_infinity=False,
                   places=12))
def test_property_decimal_text_is_exact_and_canonical(x):
    from newcore.domain.base import check_decimal
    if x.is_zero() and x.is_signed():
        x = D(0)
    check_decimal(x, 'x')
    text = dec_str(x)
    assert DECIMAL_RE.fullmatch(text) and D(text) == x
    doc = encode_document(F.rules())
    doc['body']['min_notional'] = text
    if x >= 0:
        assert decode_document(doc).min_notional == x


@pytest.mark.parametrize('bad', [D('1e15'), D('-1e15'), D('0.0000000000001'), D('NaN'), D('sNaN'), D('Infinity'), D('-0')])
def test_decimal_bounds(bad):
    from newcore.domain.base import check_decimal
    with pytest.raises(InvalidRecord):
        check_decimal(bad, 'x')


@pytest.mark.parametrize('x', [True, None, 'NaN', float('inf'), float('nan'), '1'])
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
