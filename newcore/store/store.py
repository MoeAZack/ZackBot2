"""NC-02b: the account store unit and its boot rules (acceptance draft r4 section 3a; nc02_design.md sections 2-7).

Layout (design 2, decisions D1 / D2 / D14):

    <base>/data/                                  the data folder (legacy files may sit here: REJECT, rule 0)
    <base>/data/accounts/<account_id>/HEAD.a|b    dual-slot commit record: the ONLY commit point (D1)
    <base>/data/accounts/<account_id>/snap/g<20>.snap   write-once snapshot generations
    <base>/data/accounts/<account_id>/journal/    the NC-02a journal (continuous across generations)
    <base>/data/accounts/<account_id>/evidence/   encrypted envelopes, private DACL (D5)
    <base>/anchors/<account_id>.a|b               high-water anchor OUTSIDE data\\ (D2) + hard-HOLD marker (D11)
    <base>/anchors/by-binding/<key digest>        which account a binding belongs to (written once)
    <base>/incidents/boot.jsonl                   out-of-band incident log (D14)

boot() applies the rules in order (first match wins, rule 0 always runs):
  0 REJECT legacy files (hash + metadata only, D15).
  1 any future / unknown member (HEAD or anchor slot, any snapshot - orphans too (D7) -, a binding / settings / NC-01
    record inside one, a journal segment or event, an envelope; a newer writer_seq, D8): ABORT_RO, zero writes in data\\
    and no anchor write; one incident line outside data\\.
  2 high-water: an anchor ahead of HEAD, an anchor with no store, HEAD's own high_water ahead, or an older writer after a
    newer one: HOLD (rollback). The D11 hard-HOLD marker in the anchor forces HOLD too.
  3 an unreadable member or a non-file at a member path: HOLD with no write at all (D6: soft; the cause is re-derived).
  4 damage (A17 / A18 rejections, a missing member, a journal behind its snapshot, HEAD lost while generations exist,
    unexpected members; a stray file in snap/ is only reported, N4): evidence copied into the envelope (A04), a HOLD generation committed (A05), the newest intact
    generation offered as candidate (A07), never managed.
  5 the exchange is down: HOLD (INIT waits with nothing written).
  6 trust MANAGED + the configured binding confirmed and equal + no unapplied journal tail + a fresh MATCH: MANAGE.
  7 otherwise HOLD with the per-item list; a MANAGED generation entering HOLD commits a HOLD generation (A05).
  8 nothing exists for this account: INIT. Flat exchange + confirmed binding: MANAGE known-empty (R-KNOWN-EMPTY).
    Anything else: HOLD-INIT with one item per exchange position / order. An interrupted INIT is re-run (D9).
Leaving HOLD is only promote() (A08): a MATCH on a fresh snapshot, re-checked against a second fresh snapshot, then a
PROMOTION generation. An identity mismatch or a trivially-empty candidate never auto-promotes; both need the owner's
decision id AND the fresh match.

Generations are monotonic (G' = 1 + the highest generation seen in HEAD, the anchor and every snap/ name). A commit is
S0 encode + re-decode (A18) -> S1 O_EXCL snapshot -> S2 write + fsync -> S3 dir flush -> S6 HEAD older slot + fsync
(|commit|) -> S7 anchor older slot + fsync (a crash before S7 leaves the anchor behind HEAD: harmless, M55).
Retention (D16, minimal): HEAD keeps the current generation, the newest RETAIN retained ones and every quarantined
(damaged) one; older generations move to HEAD.retired so they are never reported as orphans. Nothing is ever deleted.
"""
from __future__ import annotations

import dataclasses
import enum
import os
from dataclasses import dataclass

from newcore.domain import (BindingState, EntriesMode, HoldKind, OwnershipProof, Portfolio, ProofKind, ReasonCode,
                            Snapshot, check_account_portfolio)
from newcore.domain.errors import DomainError
from newcore.domain.events import DecisionRecorded, IntentRecorded, IntentStateChanged, ResultObserved
from newcore.domain.portfolio import Ownership

from .envelope import peek_version, write_envelope
from .errors import DurabilityUnavailable, failure_reason
from .frame import KIND_ANCHOR, KIND_HEAD
from .header import VersionVerdict, canonical_json, strict_json, HeaderError
from .incidents import append_incident
from .journal import ACCT_RE, JOURNAL_DIR, create_journal
from .legacy import scan_legacy
from .reconcile import ExchangeDown, HoldItem, verdict, VerdictKind
from .records import (ProvenanceKind, Reader, SNAP_NAME_RE, Trust, derive_hex, incident_id, provenance,
                      reconciliation_id, sha256_hex, snap_name, validate_anchor, validate_head, writer_regressed,
                      FORMAT_ANCHOR, FORMAT_HEAD, STORE_FORMAT)
from .recovery import Verdict as JVerdict, inspect_journal, open_journal
from .slots import PairState, read_pair, write_slot
from .snapfile import SnapDamage, SnapFuture, SnapOlder, decode_snapshot, encode_snapshot, peek

RETAIN = 3


class Mode(enum.StrEnum):
    ABORT_RO = 'abort_ro'
    HOLD = 'hold'
    HOLD_INIT = 'hold_init'
    MANAGE = 'manage'
    INIT_WAIT = 'init_wait'


@dataclass(frozen=True)
class Paths:
    base: str
    account_id: str

    @property
    def data(self):
        return os.path.join(self.base, 'data')

    @property
    def accounts(self):
        return os.path.join(self.data, 'accounts')

    @property
    def account(self):
        return os.path.join(self.accounts, self.account_id)

    @property
    def snap(self):
        return os.path.join(self.account, 'snap')

    @property
    def anchors(self):
        return os.path.join(self.base, 'anchors')

    @property
    def by_binding(self):
        return os.path.join(self.anchors, 'by-binding')


@dataclass
class BootResult:
    mode: Mode
    hold_kind: HoldKind | None = None
    reason: ReasonCode | None = None
    portfolio: object | None = None          # MANAGE: the managed portfolio; HOLD: the UNKNOWN HOLD portfolio
    candidate: object | None = None          # A07: offered for reconciliation, never managed
    candidate_kind: str | None = None        # 'G-1' | 'current' | 'trivially_empty' | None
    items: tuple = ()
    findings: tuple = ()
    evidence: tuple = ()
    legacy: tuple = ()
    incidents: tuple = ()
    writes: frozenset = frozenset()
    store: object | None = None
    view: object | None = None               # hard HOLD: read-only journal view (events, gate, state; appends refused)

    @property
    def account_context(self):
        return self.mode is not Mode.ABORT_RO

    @property
    def ownership(self):
        if self.mode in (Mode.ABORT_RO, Mode.INIT_WAIT):
            return None
        return None if self.portfolio is None else str(self.portfolio.ownership)


def aggregate_for(account_id):
    """The portfolio aggregate id of an account (journal aggregate, Portfolio.portfolio_id)."""
    return 'pf_' + derive_hex('aggregate_id', account_id, n=32)


def _ownership_changing(ev):
    if isinstance(ev, DecisionRecorded):
        return bool(ev.decision.intents)
    return isinstance(ev, (IntentRecorded, IntentStateChanged, ResultObserved))


def unknown_portfolio(account_id, generation, now_ms, reason, hold_kind=HoldKind.NORMAL, portfolio_id=None):
    reasons = (reason,)
    if hold_kind is HoldKind.DURABILITY_UNAVAILABLE and ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE not in reasons:
        reasons += (ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE,)
    return Portfolio(portfolio_id=portfolio_id or aggregate_for(account_id), account_id=account_id, generation=generation,
                     ownership=Ownership.UNKNOWN, proof=None, entries_mode=EntriesMode.HOLD, mode_since_ms=now_ms,
                     pause_reasons=reasons, positions=None, intents=None, entry_stops=None, hold_kind=hold_kind)


class _Abort(Exception):
    def __init__(self, findings):
        self.findings = tuple(findings)


# ======================================================================================================== the store
class AccountStore:
    """The opened store unit of one account. Built by boot(); the runner keeps it for checkpoints and promotion."""

    def __init__(self, fs, paths, account, reader, cipher, aggregate_id=None):
        self.fs, self.paths, self.reader, self.cipher = fs, paths, reader, cipher
        self.configured = account            # the binding the launcher is configured with
        self.account = account               # the binding the store holds (set from the current generation)
        self.account_id = account.account_id
        self.aggregate_id = aggregate_id or aggregate_for(self.account_id)
        self.head, self.head_slot = None, None
        self.anchor, self.anchor_slot = None, None
        self.current = None                  # SnapFile of HEAD's generation
        self.candidate = None                # SnapFile offered for reconciliation
        self.settings = {}
        self.journal = None
        self.mode = None
        self.hold_kind = None
        self.writes = set()
        self.max_seen = 0
        self.view = None

    # ---------------------------------------------------------------------------------------------- commit protocol
    def next_generation(self):
        hw = self.head['high_water']['generation'] if self.head else 0
        an = self.anchor['generation'] if self.anchor else 0
        return max(hw, an, self.max_seen, self.head['generation'] if self.head else 0) + 1

    def commit(self, portfolio, prov, now_ms, *, account=None, settings=None, lsn_upto=None, quarantine=(),
               write_class='commit'):
        """Commit a new generation (design 4.1). Returns the SnapFile. Raises DurabilityUnavailable."""
        fs, paths = self.fs, self.paths
        account = account or self.account
        settings = dict(self.settings if settings is None else settings)
        g = self.next_generation()
        lsn = self.journal.last_sequence() if (lsn_upto is None and self.journal is not None) else (lsn_upto or 0)
        try:
            pf = dataclasses.replace(portfolio, generation=g)
            snap = Snapshot(account_id=self.account_id, generation=g, last_sequence=lsn, written_at_ms=now_ms,
                            writer_build=self.reader.writer_build, portfolio=pf)
        except DomainError as ex:
            raise ValueError(f'the generation does not validate: {ex.path}') from None
        raw = encode_snapshot(account=account, settings=settings, snapshot=snap, prov=prov,
                              writer_build=self.reader.writer_build, writer_seq=self.reader.writer_seq,
                              written_ms=now_ms, fmt=self.reader.snapshot_format)
        sf = decode_snapshot(raw, self.reader, account_id=self.account_id)        # S0: the writer reads like the reader
        name = snap_name(g)
        p = os.path.join(paths.snap, name)
        step = 'snapshot create'
        try:
            fs.mark('C-S1')
            h = fs.open_new(p)
            try:
                fs.write(h, raw)
                fs.fsync(h)
            finally:
                fs.close(h)
            fs.mark('C-S3')
            fs.fsync_dir(paths.snap)
            ref = {'generation': g, 'name': name, 'sha256': sf.sha256, 'len': sf.length}
            head = self._next_head(ref, now_ms, quarantine, account)
            step = 'HEAD commit'
            fs.mark('C-S6')
            write_slot(fs, paths.account, 'HEAD', self._head_target(), KIND_HEAD, head)      # |commit|
        except OSError as ex:
            raise DurabilityUnavailable(step, p, ex) from None
        self.head, self.head_slot = head, self._head_target()
        self.max_seen = max(self.max_seen, g)
        self.current, self.account, self.settings = sf, account, settings
        self.writes.add(write_class)
        fs.mark('C-S7')
        keep_marker = bool(self.anchor and self.anchor['hard_hold']) and write_class != 'promotion'
        self._write_anchor(now_ms, hard_hold=keep_marker)                       # D11 marker cleared only by A08
        return sf

    def _head_target(self):
        return 'a' if self.head_slot is None else ('b' if self.head_slot == 'a' else 'a')

    def _next_head(self, ref, now_ms, quarantine, account):
        old = self.head
        retained = []
        if old is not None and old['snapshot']['name'] not in {q['name'] for q in quarantine}:
            retained.append(old['snapshot'])
        if old is not None:
            retained += old['retained']
        retained = [r for r in retained if r['name'] not in {q['name'] for q in quarantine}]
        keep, retired = retained[:RETAIN], [r['name'] for r in retained[RETAIN:]]
        quar = (old['quarantined'] if old else []) + [dict(q) for q in quarantine]
        commit_seq = (old['commit_seq'] if old else 0) + 1
        hist = list(old['writer_history']) if old else []
        if not hist or hist[-1]['writer_seq'] != self.reader.writer_seq:
            hist.append({'writer_seq': self.reader.writer_seq, 'from_commit': commit_seq})
        hw_gen = max(ref['generation'], old['high_water']['generation'] if old else 0)
        hw_seq = max(self.reader.writer_seq, old['high_water']['writer_seq'] if old else 0)
        return {'account_id': self.account_id, 'binding_digest': account.binding.key_digest,
                'commit_seq': commit_seq, 'format': FORMAT_HEAD, 'format_version': STORE_FORMAT,
                'min_reader_version': STORE_FORMAT, 'generation': ref['generation'], 'snapshot': ref,
                'retained': keep, 'quarantined': quar, 'retired': sorted(set((old['retired'] if old else []) + retired)),
                'high_water': {'generation': hw_gen, 'writer_seq': hw_seq}, 'writer_history': hist,
                'open_incidents': sorted(set((old['open_incidents'] if old else []) +
                                             [q['incident_id'] for q in quarantine])),
                'written_ms': now_ms}

    def _write_anchor(self, now_ms, *, hard_hold):
        """S7, best-effort (a lagging anchor is harmless, M55). Writes outside data\\."""
        fs, paths = self.fs, self.paths
        doc = {'account_id': self.account_id, 'binding_digest': self.account.binding.key_digest,
               'commit_seq': (self.anchor['commit_seq'] if self.anchor else 0) + 1, 'format': FORMAT_ANCHOR,
               'head_commit': self.head['commit_seq'] if self.head else 0,
               'format_version': STORE_FORMAT, 'min_reader_version': STORE_FORMAT,
               'generation': self.head['generation'] if self.head else 0, 'writer_seq': self.reader.writer_seq,
               'hard_hold': hard_hold, 'written_ms': now_ms}
        target = 'a' if self.anchor_slot is None else ('b' if self.anchor_slot == 'a' else 'a')
        try:
            for d, parent in ((paths.anchors, paths.base),):
                if fs.kind(d) == 'missing':
                    fs.mkdir(d)
                    fs.fsync_dir(parent)
            write_slot(fs, paths.anchors, self.account_id, target, KIND_ANCHOR, doc)
        except OSError:
            return False
        self.anchor, self.anchor_slot = doc, target
        self.writes.add('anchor')
        return True

    def mark_hard_hold(self, now_ms):
        """D11: best-effort marker outside data\\ so a restart with a writable store still lands in HOLD."""
        return self._write_anchor(now_ms, hard_hold=True)

    def bind(self, now_ms):
        """anchors/by-binding/<digest>: written once (O_EXCL), never overwritten. True when the entry names this account
        (written now or already there); False when it cannot be written or names / holds anything else (N7).
        The one exception to "written once": a torn entry of THIS account's own interrupted bind (a prefix of its exact
        canonical bytes, possibly NUL-filled) is completed in place - never an entry naming anything else."""
        fs, paths = self.fs, self.paths
        p = os.path.join(paths.by_binding, self.account.binding.key_digest)
        own = _own_entry(self.account)
        try:
            for d, parent in ((paths.anchors, paths.base), (paths.by_binding, paths.anchors)):
                if fs.kind(d) == 'missing':
                    fs.mkdir(d)
                    fs.fsync_dir(parent)
            state, owner = _by_binding(fs, paths, self.account)
            if state == 'torn':
                h = fs.open_slot(p)
                try:
                    fs.write_at(h, own, 0)
                    fs.fsync(h)
                finally:
                    fs.close(h)
                fs.fsync_dir(paths.by_binding)
                return fs.read_bytes(p) == own
            if state != 'absent':
                return state == 'ok' and owner == self.account_id
            if fs.kind(p) == 'missing':
                h = fs.open_new(p)
                try:
                    fs.write(h, own)
                    fs.fsync(h)
                finally:
                    fs.close(h)
                fs.fsync_dir(paths.by_binding)
        except OSError:
            return False
        return True

    # ---------------------------------------------------------------------------------------------- runner API
    @property
    def checkpoint_due(self):
        """True when the journal holds events the current generation does not cover (call checkpoint())."""
        return (self.journal is not None and self.current is not None
                and self.journal.last_sequence() > self.current.lsn_upto)

    def checkpoint(self, portfolio, now_ms):
        """The runner's checkpoint (ruling item 6): a new generation holding the runner's folded NC-01 Portfolio at the
        journal's last sequence, so a restart MANAGEs without an event -> portfolio apply in the store. Call it after
        every ownership-changing event (or every N cycles; `checkpoint_due`). Crash-safe: it is an ordinary generation
        commit (S0-S7); a crash before the HEAD write leaves the previous generation + the journal tail, which the boot
        `fold` hook (or HOLD) covers.

        Trust: MANAGED only while the store is MANAGE and the portfolio's ownership is proven and not in HOLD; anything
        else is a HOLD generation (A05: a save in HOLD never produces a store the next start trusts). The portfolio
        must name this account / aggregate, and a JOURNAL proof may not reach past the journal (NC-01 Snapshot).

        A failed write raises DurabilityUnavailable and leaves the store in hard HOLD (D11 marker best-effort, as a
        failed promotion does); every later checkpoint is refused the same way until a restart reconciles (A08)."""
        if self.hold_kind is HoldKind.DURABILITY_UNAVAILABLE:
            raise DurabilityUnavailable('checkpoint (hard HOLD)', None)
        if portfolio.account_id != self.account_id or portfolio.portfolio_id != self.aggregate_id:
            raise ValueError('the checkpoint portfolio belongs to another account / aggregate')
        managed = (self.mode is Mode.MANAGE and portfolio.ownership is not Ownership.UNKNOWN
                   and portfolio.entries_mode is not EntriesMode.HOLD)
        trust = Trust.MANAGED if managed else Trust.HOLD
        kind = ProvenanceKind.CHECKPOINT if trust is Trust.MANAGED else ProvenanceKind.HOLD
        items = () if managed else ({'scope': 'account', 'cause': 'checkpoint_in_hold', 'ref': ''},)
        prov = provenance(kind, trust, from_generation=self.current.generation if self.current else None,
                          incident_ids=self.current.provenance['incident_ids'] if self.current else (), items=items)
        try:
            return self.commit(portfolio, prov, now_ms, write_class='checkpoint')
        except DurabilityUnavailable:
            self.mode, self.hold_kind = Mode.HOLD, HoldKind.DURABILITY_UNAVAILABLE
            self.mark_hard_hold(now_ms)
            raise

    def close(self):
        """Release the journal (its writer lock). Idempotent; the store writes nothing after it."""
        j, self.journal = self.journal, None
        if j is not None:
            j.close()

    def tail_after(self, lsn):
        if self.journal is None:
            return ()
        return tuple(e for e in self.journal.read(lsn))

    def promote(self, exchange, now_ms, *, candidate=None, owner_decision_id=None, account=None):
        """A08: the only exit from HOLD / HOLD-INIT. Returns a BootResult (MANAGE, or HOLD with items)."""
        account = account or self.account
        if self.mode not in (Mode.HOLD, Mode.HOLD_INIT) or self.hold_kind is HoldKind.DURABILITY_UNAVAILABLE:
            raise ValueError('promote() needs a (soft) HOLD store')
        cand_pf = candidate if candidate is not None else (self.candidate.portfolio if self.candidate else None)
        cand_lsn = self.journal.last_sequence() if candidate is not None else (
            self.candidate.lsn_upto if self.candidate else 0)
        items = []
        if candidate is None and any(_ownership_changing(e) for e in self.tail_after(cand_lsn)):
            items.append(HoldItem('account', 'journal_tail_unapplied', f'after {cand_lsn}'))
        if account.binding_state is not BindingState.CONFIRMED:
            items.append(HoldItem('account', 'identity', 'binding not confirmed'))
        bp = _binding_problem(self.fs, self.paths, account)       # N7: never adopt a key bound to another account
        if bp is not None:
            items.append(HoldItem('account', 'identity', bp[1]))
        identity_change = account.binding.key_digest != self.account.binding.key_digest or any(
            i['cause'] == 'identity' for i in (self.current.provenance['items'] if self.current else ()))
        empty = cand_pf is not None and cand_pf.ownership is Ownership.KNOWN_EMPTY
        needs_owner = identity_change or empty or self.mode is Mode.HOLD_INIT
        if needs_owner and owner_decision_id is None:
            items.append(HoldItem('account', 'owner_confirmation_required',
                                  'identity' if identity_change else 'trivially_empty' if empty else 'hold_init'))
        if items:
            return self._hold_result(items)
        try:
            s1 = exchange.snapshot()
        except ExchangeDown:
            return self._hold_result([HoldItem('account', 'exchange_down')])
        v1 = verdict(account, cand_pf, s1, now_ms, empty_proven=owner_decision_id is not None)
        if not v1.match:
            return self._hold_result(list(v1.items))
        try:
            s2 = exchange.snapshot()                                     # the atomic re-check (A08)
        except ExchangeDown:
            return self._hold_result([HoldItem('account', 'exchange_down')])
        v2 = verdict(account, cand_pf, s2, now_ms, empty_proven=owner_decision_id is not None)
        if not v2.match or s1.canonical().split(b'|', 2)[2] != s2.canonical().split(b'|', 2)[2]:
            return self._hold_result(list(v2.items) or [HoldItem('account', 'exchange_changed_during_promotion')])
        g = self.next_generation()
        rec = reconciliation_id(self.account_id, s2.taken_ms, g)
        try:
            if empty:
                proof = OwnershipProof(kind=ProofKind.FLAT_SNAPSHOT, at_ms=s2.taken_ms, reconciliation_id=rec,
                                       key_digest=account.binding.key_digest, decision_id=None, through_sequence=None)
            else:
                proof = OwnershipProof(kind=ProofKind.RECONCILED, at_ms=s2.taken_ms, reconciliation_id=rec,
                                       key_digest=None, decision_id=None, through_sequence=None)
            mode = cand_pf.entries_mode
            reasons, hold = cand_pf.pause_reasons, cand_pf.hold_kind
            if mode is EntriesMode.HOLD:
                mode, reasons, hold = EntriesMode.PAUSED, (ReasonCode.RECONCILE_MATCH,), None
            pf = dataclasses.replace(cand_pf, proof=proof, entries_mode=mode, pause_reasons=reasons, hold_kind=hold,
                                     mode_since_ms=now_ms)
            if empty:
                check_account_portfolio(account, pf)
        except DomainError as ex:
            return self._hold_result([HoldItem('account', 'candidate_invalid', ex.path)])
        prov = provenance(ProvenanceKind.PROMOTION, Trust.MANAGED, from_generation=self.current.generation
                          if self.current else None,
                          recon={'recon_id': rec, 'taken_ms': s2.taken_ms, 'key_digest': s2.key_digest,
                                 'verdict': 'owner_resolved' if owner_decision_id else 'match',
                                 'snapshot_sha256': sha256_hex(s2.canonical())},
                          decision_id=owner_decision_id)
        try:
            self.commit(pf, prov, now_ms, account=account, write_class='promotion')
            if identity_change:
                self.bind(now_ms)                                        # the owner-confirmed new binding
        except DurabilityUnavailable:
            self.mode, self.hold_kind = Mode.HOLD, HoldKind.DURABILITY_UNAVAILABLE
            self.mark_hard_hold(now_ms)
            return self._hold_result([HoldItem('account', 'store_unwritable')])
        self.mode, self.hold_kind, self.candidate = Mode.MANAGE, None, None
        return BootResult(Mode.MANAGE, None, None, self.current.portfolio, writes=frozenset(self.writes), store=self)

    def _hold_result(self, items):
        return BootResult(self.mode, self.hold_kind or HoldKind.NORMAL, ReasonCode.RECONCILE_UNRECONCILED,
                          self.current.portfolio if self.current else None,
                          self.candidate.portfolio if self.candidate else None,
                          'G-1' if self.candidate else None, tuple(items), writes=frozenset(self.writes), store=self)


# ======================================================================================================== boot
@dataclass
class _Seen:
    findings: list = dataclasses.field(default_factory=list)
    future: list = dataclasses.field(default_factory=list)
    unreadable: list = dataclasses.field(default_factory=list)
    damage: list = dataclasses.field(default_factory=list)
    damaged_members: list = dataclasses.field(default_factory=list)       # (rel path, generation or None)
    rollback: list = dataclasses.field(default_factory=list)
    identity: list = dataclasses.field(default_factory=list)


def boot(base, account, *, exchange, now_ms, fs=None, reader=None, cipher=None, settings=None, aggregate_id=None,
         fold=None):
    """Open (or INIT) the store of `account` (the configured NC-01 Account: id + binding + confirmation state).
    aggregate_id: the journal aggregate / Portfolio.portfolio_id (default: derived from the account id).
    fold(journal, snapshot_portfolio) -> Portfolio | None: the runner's own fold of the journal, used ONLY when the
    journal holds ownership-changing events after the newest MANAGED generation (a crash between an event and its
    checkpoint). Its portfolio must carry a JOURNAL proof through the journal's last sequence; it is matched against a
    fresh exchange snapshot like any MANAGE decision and, on a match, checkpointed at once."""
    from .fs import RealFs
    fs = fs or RealFs()
    reader = reader or Reader()
    paths = Paths(base, account.account_id)
    store = AccountStore(fs, paths, account, reader, cipher, aggregate_id)
    store.settings = dict(settings or {})
    seen = _Seen()
    incidents = []

    def incident(kind, reason, details):
        ok = append_incident(fs, base, account.account_id, kind, reason, now_ms, details)
        incidents.append({'kind': kind, 'reason': reason, 'logged': ok})

    # ---- rule 0
    legacy = scan_legacy(fs, paths.data)
    if legacy:
        incident('legacy_rejected', 'rule_0', {'files': [r.doc() for r in legacy]})

    def result(mode, **kw):
        kw.setdefault('legacy', legacy)
        kw.setdefault('incidents', tuple(incidents))
        kw.setdefault('writes', frozenset(store.writes))
        return BootResult(mode, **kw)

    try:
        exists = _read_only(fs, paths, account, reader, store, seen)
    except OSError as ex:                                         # N3: never a raw OSError; never INIT on it either
        seen.unreadable.append(f'read error during the scan ({type(ex).__name__})')
        exists = True
    except _Abort as ab:
        incident('abort_ro', 'rule_1', {'findings': list(ab.findings)})
        return result(Mode.ABORT_RO, reason=ReasonCode.RECOVERY_SCHEMA_FUTURE, findings=ab.findings,
                      incidents=tuple(incidents), writes=frozenset())
    findings = tuple(seen.findings)

    # ---- rule 8: nothing exists
    if not exists:
        return _init(store, exchange, now_ms, incident, result, findings)

    # ---- rule 3: unreadable, no writes at all
    if seen.unreadable:
        store.mode, store.hold_kind = Mode.HOLD, HoldKind.NORMAL
        return result(Mode.HOLD, hold_kind=HoldKind.NORMAL, reason=ReasonCode.RECOVERY_STATE_UNREADABLE,
                      portfolio=None, candidate=store.candidate.portfolio if store.candidate else None,
                      candidate_kind='G-1' if store.candidate else None,
                      items=tuple(HoldItem('account', 'unreadable', u) for u in seen.unreadable),
                      findings=findings, store=store)

    # ---- rules 2 / 4 / identity: a HOLD generation (evidence first)
    hold_items = ([HoldItem('account', 'rollback', r) for r in seen.rollback] +
                  [HoldItem('account', 'damage', d) for d in seen.damage] +
                  [HoldItem('account', 'identity', i) for i in seen.identity])
    if hold_items:
        reason = (ReasonCode.RECOVERY_GENERATION_ROLLBACK if seen.rollback else
                  ReasonCode.RECOVERY_SCHEMA_INVALID if seen.damage else ReasonCode.BINDING_MISMATCH)
        return _enter_hold(store, hold_items, reason, now_ms, incident, result, findings, seen,
                           candidate=store.candidate or store.current)

    # ---- the journal (repair a torn tail now that rule 1 / 2 / 3 / 4 passed)
    rec = open_journal(store._insp, cipher=cipher)
    if rec.journal is None:
        store.view = rec.view
        why = '; '.join(f.detail for f in rec.findings if f.kind in ('cipher_unavailable', 'unwritable'))
        return _hard_hold(store, now_ms, incident, result, findings, 'journal not writable' + (f': {why}' if why else ''))
    store.journal = rec.journal
    if rec.evidence:
        store.writes.add('tail_seal')
    tail = store.tail_after(store.current.lsn_upto)

    # ---- A13: an older format is translated into a NEW generation, in HOLD; the old one stays retained
    if store.current.header['format_version'] < reader.snapshot_format:
        old = store.current
        prov = provenance(ProvenanceKind.MIGRATION, Trust.HOLD, from_generation=old.generation,
                          migration={'from_format': old.header['format_version'], 'from_sha256': old.sha256},
                          items=[{'scope': 'account', 'cause': 'migrated', 'ref': f'format {old.header["format_version"]}'}])
        try:
            store.commit(old.portfolio, prov, now_ms, account=old.account, lsn_upto=old.lsn_upto, write_class='migration')
        except (DurabilityUnavailable, ValueError):
            return _hard_hold(store, now_ms, incident, result, findings, 'migration cannot commit')
        incident('migration', 'A13', {'from_format': old.header['format_version'], 'generation': store.current.generation})

    # ---- the current generation's own trust
    prov = store.current.provenance
    if prov['trust'] != Trust.MANAGED:
        store.mode = Mode.HOLD_INIT if prov['trust'] == Trust.HOLD_INIT else Mode.HOLD
        store.hold_kind = HoldKind.NORMAL
        if prov['kind'] == ProvenanceKind.MIGRATION or (
                store.candidate is None and store.current.portfolio.ownership is not Ownership.UNKNOWN):
            store.candidate = store.current                              # a migrated generation is its own candidate
        items = tuple(HoldItem(**i) for i in prov['items']) or (HoldItem('account', 'unreconciled'),)
        return result(store.mode, hold_kind=HoldKind.NORMAL, reason=ReasonCode.RECONCILE_UNRECONCILED,
                      portfolio=store.current.portfolio, candidate=store.candidate.portfolio if store.candidate else None,
                      candidate_kind=_cand_kind(store), items=items, findings=findings, evidence=rec.evidence,
                      store=store)

    # ---- rule 5: the exchange
    try:
        snap = exchange.snapshot()
    except ExchangeDown:
        store.mode, store.hold_kind, store.candidate = Mode.HOLD, HoldKind.NORMAL, store.current
        return result(Mode.HOLD, hold_kind=HoldKind.NORMAL, reason=ReasonCode.CONNECTIVITY_EXCHANGE_OUTAGE,
                      portfolio=store.current.portfolio, candidate=store.current.portfolio, candidate_kind='current',
                      items=(HoldItem('account', 'exchange_down'),), findings=findings, store=store)

    # ---- rules 6 / 7
    items = []
    if account.binding_state is not BindingState.CONFIRMED or store.account.binding != account.binding:
        items.append(HoldItem('account', 'identity', 'binding not confirmed'))
    pf = store.current.portfolio
    folded = False
    if any(_ownership_changing(e) for e in tail):
        pf2 = _folded(store, fold, pf)
        if pf2 is None:
            items.append(HoldItem('account', 'journal_tail_unapplied', f'after {store.current.lsn_upto}'))
        else:
            pf, folded = pf2, True
    jmode = store.journal.state().mode
    if jmode is EntriesMode.HOLD:
        items.append(HoldItem('account', 'journaled_hold'))
    if not items:
        v = verdict(account, pf, snap, now_ms, empty_proven=_empty_proven(store.current, tail) and not folded)
        if v.kind is VerdictKind.TRIVIALLY_EMPTY:
            items.append(HoldItem('account', 'trivially_empty'))
        items.extend(v.items if not v.match else ())
    if not items:
        store.mode = Mode.MANAGE
        if folded:                                       # cover the tail at once (the hook ran; the match held)
            try:
                store.checkpoint(pf, now_ms)
            except (DurabilityUnavailable, ValueError):
                return _hard_hold(store, now_ms, incident, result, findings, 'checkpoint of the folded tail failed')
        return result(Mode.MANAGE, portfolio=pf, findings=findings, evidence=rec.evidence, store=store)
    return _enter_hold(store, items, ReasonCode.RECONCILE_UNRECONCILED, now_ms, incident, result, findings, seen,
                       candidate=store.current)


def _folded(store, fold, snapshot_pf):
    """The runner's fold of the journal tail, or None (no hook, a refusal, or a portfolio that is not a JOURNAL-proven
    view of exactly this journal)."""
    if fold is None:
        return None
    try:
        pf = fold(store.journal, snapshot_pf)
    except (ValueError, DomainError):
        return None
    if pf is None or pf.account_id != store.account_id or pf.portfolio_id != store.aggregate_id:
        return None
    proof = pf.proof
    if proof is None or proof.kind is not ProofKind.JOURNAL or proof.through_sequence != store.journal.last_sequence():
        return None
    return pf


def _cand_kind(store):
    if store.candidate is None:
        return None
    pf = store.candidate.portfolio
    if pf.ownership is Ownership.KNOWN_EMPTY:
        return 'trivially_empty'
    return 'current' if store.candidate is store.current else 'G-1'


def _empty_proven(sf, tail):
    """R-KNOWN-EMPTY for the current generation: a KNOWN_EMPTY portfolio is proven by its own flat reconciliation
    (INIT on a flat exchange, or an owner-resolved / matched promotion) with no journaled activity after it."""
    pf, prov = sf.portfolio, sf.provenance
    if pf.ownership is not Ownership.KNOWN_EMPTY or any(_ownership_changing(e) for e in tail):
        return False
    r = prov['recon']
    return (prov['kind'] in (ProvenanceKind.INIT_FLAT, ProvenanceKind.PROMOTION) and r is not None
            and r['recon_id'] == pf.proof.reconciliation_id)


def _read_only(fs, paths, account, reader, store, seen):
    """Rule 1 over every member, then rules 2 / 3 / 4 classification. Never writes. Returns whether a store exists."""
    acct_kind = _kind(fs, paths.account)                         # N3: a failing kind() is unreadable, typed
    acct_exists = acct_kind != 'missing'
    # anchor + by-binding (outside data\)
    anchors_kind = _kind(fs, paths.anchors)
    if anchors_kind == 'unreadable':
        seen.unreadable.append('anchors directory')
    anchor = read_pair(fs, paths.anchors, account.account_id, KIND_ANCHOR, validate_anchor) \
        if anchors_kind == 'dir' else None
    if anchor is not None:
        if anchor.state is PairState.FUTURE:
            seen.future.append('anchor slot')
        elif anchor.state is PairState.OK:
            store.anchor, store.anchor_slot = anchor.doc, anchor.current
            if anchor.doc['writer_seq'] > reader.writer_seq:
                seen.future.append('anchor written by a newer build')
        elif anchor.state is PairState.UNREADABLE:
            seen.unreadable.append('anchor')
        elif anchor.state is PairState.DAMAGE:
            seen.damage.append('anchor slots')
    bp = _binding_problem(fs, paths, account)                    # N7: the index is used at every boot
    if bp is not None:
        (seen.identity if bp[0] == 'identity' else seen.unreadable).append(bp[1])
    elif _by_binding(fs, paths, account)[0] == 'torn':           # visible, outcome unchanged (Cowork, 7af893a)
        seen.findings.append('by-binding entry torn (empty or a prefix of this account\'s own entry: an interrupted '
                             'bind): treated as absent until the next bind() completes it')
    head = None
    if acct_exists:
        if acct_kind != 'dir':
            seen.unreadable.append('account path unreadable' if acct_kind == 'unreadable'
                                   else 'account path is not a directory')
        else:
            head = read_pair(fs, paths.account, 'HEAD', KIND_HEAD, validate_head)
            if head.state is PairState.FUTURE:
                seen.future.append('HEAD slot')
            elif head.state is PairState.UNREADABLE:
                seen.unreadable.append('HEAD slot')
            elif head.state is PairState.DAMAGE:
                seen.damage.append('HEAD slots: ' + head.detail)
                seen.damaged_members += [('HEAD.a', None), ('HEAD.b', None)]
            elif head.state is PairState.OK:
                store.head, store.head_slot = head.doc, head.current
                hd = head.doc
                if hd['high_water']['writer_seq'] > reader.writer_seq:
                    seen.future.append('HEAD written by a newer build')
            _scan_snapshots(fs, paths, reader, store, seen)
            _scan_evidence(fs, paths, seen)
            store._insp = inspect_journal(paths.account, account.account_id, store.aggregate_id, fs=fs)
            jv = store._insp.verdict
            if jv is JVerdict.ABORT_RO:
                seen.future.append('journal: ' + '; '.join(f.kind for f in store._insp.findings))
    if seen.future:
        raise _Abort(seen.future)
    if not acct_exists:
        if store.anchor is not None:                            # D2: an emptied / lost data folder
            seen.rollback.append('the anchor names a store that is gone')
            return True
        return False
    if seen.unreadable:
        return True
    hd = store.head
    insp = store._insp
    if hd is None:                                              # HEAD absent or damaged
        if head.state is PairState.ABSENT and _interrupted_init(store, insp):
            return False                                        # D9: re-INIT
        if head.state is PairState.ABSENT:
            seen.damage.append('HEAD missing while the account has generations or events (A11)')
        return True
    # identity / rollback (rule 2)
    if hd['account_id'] != account.account_id:
        seen.damage.append('HEAD names another account')
    if hd['binding_digest'] != account.binding.key_digest:
        seen.identity.append('the configured binding differs from the store binding')
    if store.anchor is not None:
        if store.anchor['generation'] > hd['generation'] or store.anchor['head_commit'] > hd['commit_seq']:
            seen.rollback.append('anchor ahead of HEAD')
        if store.anchor['binding_digest'] != hd['binding_digest']:
            seen.identity.append('anchor binding differs from HEAD')
        if store.anchor['hard_hold']:
            seen.rollback.append('hard-HOLD marker (D11)')
    if hd['high_water']['generation'] > hd['generation']:
        seen.rollback.append('HEAD high_water ahead of its generation')
    if writer_regressed(hd['writer_history']):
        seen.rollback.append('an older writer ran after a newer one')
    # current generation (rule 4)
    if store.current is None:
        seen.damage.append('the current snapshot is missing or damaged')
        seen.damaged_members.append((f'snap/{hd["snapshot"]["name"]}', hd['generation']))
    if insp.verdict in (JVerdict.DAMAGED, JVerdict.MISSING):
        seen.damage.append(f'journal {insp.verdict}')
        seen.damaged_members += [(f'{JOURNAL_DIR}/{n}', None) for n in _journal_files(fs, paths)]
    elif insp.verdict is JVerdict.UNREADABLE:
        seen.unreadable.append('journal')
    elif store.current is not None and insp.last_sequence < store.current.lsn_upto:
        seen.damage.append('journal behind its snapshot')
    return True


def _kind(fs, p):
    """fs.kind(p), or 'unreadable' when the OS cannot say (N3: never a raw OSError out of boot)."""
    try:
        return fs.kind(p)
    except OSError:
        return 'unreadable'


def _by_binding(fs, paths, account):
    """N7: the anchors/by-binding/<key digest> index entry of this account's configured key, as (state, value):
    ('absent', None) | ('torn', None) | ('ok', owner account id) | ('bad', why) | ('unreadable', why). 'torn' is this
    account's own interrupted bind() (a prefix of its exact canonical bytes, maybe NUL-filled): treated as absent and
    completed by the next bind(). An entry is 'ok' only when it is
    the exact canonical document the store writes ({account_id, binding_digest}) for THIS digest."""
    p = os.path.join(paths.by_binding, account.binding.key_digest)
    try:
        k = fs.kind(p)
        if k == 'missing':
            return 'absent', None
        if k != 'file':
            return 'bad', f'a {k} at the entry path'
        raw = fs.read_bytes(p)
    except OSError as ex:
        return 'unreadable', type(ex).__name__
    own = _own_entry(account)
    if raw != own and len(raw) <= len(own) and own.startswith(raw.rstrip(b'\0')):
        return 'torn', None                                       # this account's own interrupted bind()
    try:
        doc, problems = strict_json(raw)
    except HeaderError:
        return 'bad', 'not JSON'
    if (problems or type(doc) is not dict or set(doc) != {'account_id', 'binding_digest'}
            or type(doc['account_id']) is not str or not ACCT_RE.fullmatch(doc['account_id'])
            or doc['binding_digest'] != account.binding.key_digest or raw != canonical_json(doc)):
        return 'bad', 'not the canonical entry for this key'
    return 'ok', doc['account_id']


def _own_entry(account):
    return canonical_json({'account_id': account.account_id, 'binding_digest': account.binding.key_digest})


def _binding_problem(fs, paths, account):
    """(cause, why) when the by-binding index forbids this account on its configured key, else None. 'absent' is fine
    (a first run, or an index entry a failed best-effort bind() never wrote)."""
    state, v = _by_binding(fs, paths, account)
    if state == 'ok' and v != account.account_id:
        return 'identity', 'the binding belongs to another account'
    if state == 'bad':
        return 'identity', f'by-binding entry unusable ({v}): identity unprovable'
    if state == 'unreadable':
        return 'unreadable', f'by-binding entry unreadable ({v})'
    return None


def _journal_files(fs, paths):
    try:
        return [n for n in fs.listdir(os.path.join(paths.account, JOURNAL_DIR))]
    except OSError:
        return []


def _scan_snapshots(fs, paths, reader, store, seen):
    """Peek EVERY file in snap/ for rule 1 (D7); decode the generations HEAD names; report orphans."""
    hd = store.head
    named = {}
    if hd is not None:
        named[hd['snapshot']['name']] = ('current', hd['snapshot'])
        for r in hd['retained']:
            named[r['name']] = ('retained', r)
    quarantined = {q['name'] for q in hd['quarantined']} if hd else set()
    retired = set(hd['retired']) if hd else set()
    k = _kind(fs, paths.snap)
    if k == 'unreadable':
        seen.unreadable.append('snap directory')
        return
    if k == 'missing':
        names = []
    elif k != 'dir':
        seen.unreadable.append('snap is not a directory')
        return
    else:
        try:
            names = fs.listdir(paths.snap)
        except OSError:
            seen.unreadable.append('snap listing')
            return
    raws, decoded = {}, {}
    for n in names:
        m = SNAP_NAME_RE.fullmatch(n)
        if m is None:
            seen.findings.append(f'stray file in snap/: {n} (not a generation: reported, ignored, kept)')   # N4
            continue
        store.max_seen = max(store.max_seen, int(m.group(1)))
        p = os.path.join(paths.snap, n)
        try:
            if fs.kind(p) != 'file':
                if n in named:
                    seen.unreadable.append(f'snap/{n} is not a file')
                continue
            raw = fs.read_bytes(p)
        except OSError:
            if n in named:
                seen.unreadable.append(f'snap/{n}')
            continue
        raws[n] = raw
        if peek(raw, reader) == 'future':
            seen.future.append(f'snap/{n}')
    for n, (role, ref) in named.items():
        if n in quarantined or n not in raws:
            continue
        raw = raws[n]
        try:
            sf = decode_snapshot(raw, reader, account_id=store.account_id)     # rule 1 first (a future record)
            if sha256_hex(raw) != ref['sha256'] or len(raw) != ref['len']:
                raise SnapDamage('bytes differ from the HEAD reference')
        except SnapFuture:
            seen.future.append(f'snap/{n} record')
            continue
        except SnapOlder:
            seen.future.append(f'snap/{n} older format without a migration path')
            continue
        except SnapDamage as ex:
            if role == 'current':
                seen.findings.append(f'snap/{n}: {ex}')
            else:
                seen.findings.append(f'retained snap/{n} damaged: {ex}')
            continue
        decoded[n] = sf
    if hd is not None:
        store.current = decoded.get(hd['snapshot']['name'])
        if store.current is not None:
            store.settings = dict(store.current.settings)
            store.account = store.current.account
            fg = store.current.provenance['from_generation']
            cands = _candidates([decoded[r['name']] for r in hd['retained'] if r['name'] in decoded])
            if store.current.provenance['trust'] != Trust.MANAGED:
                pref = [c for c in cands if c.generation == fg] or cands
                store.candidate = pref[0] if pref else None
        else:
            cands = _candidates([decoded[r['name']] for r in hd['retained'] if r['name'] in decoded])
            store.candidate = cands[0] if cands else None
    else:
        best = _candidates(x for x in (_try_decode(raws[n], reader, store) for n in raws) if x is not None)
        store.candidate = max(best, key=lambda x: x.generation) if best else None
    if store.current is None and store.candidate is not None:
        store.account = store.candidate.account
    for n in raws:
        if n not in named and n not in quarantined and n not in retired:
            seen.findings.append(f'orphan snap/{n} (uncommitted: never read as state, kept)')


def _candidates(sfs):
    """A07 candidates: decodable generations whose ownership is proven (an UNKNOWN portfolio owns nothing to offer)."""
    return [x for x in sfs if x.portfolio.ownership is not Ownership.UNKNOWN]


def _try_decode(raw, reader, store):
    try:
        return decode_snapshot(raw, reader, account_id=store.account_id)
    except (SnapDamage, SnapFuture, SnapOlder):
        return None


def _scan_evidence(fs, paths, seen):
    ed = os.path.join(paths.account, 'evidence')
    try:
        if fs.kind(ed) != 'dir':
            return
        for n in fs.listdir(ed):
            p = os.path.join(ed, n)
            if fs.kind(p) == 'file' and peek_version(fs.read_bytes(p)) is not VersionVerdict.OK:
                seen.future.append(f'evidence/{n}')
    except OSError:
        seen.findings.append('evidence unreadable (incident, not HOLD)')


def _interrupted_init(store, insp):
    """D9: no HEAD, no anchor, the journal has no events, and every snapshot is an INIT generation (or none)."""
    if store.anchor is not None or insp.verdict not in (JVerdict.CLEAN, JVerdict.MISSING) or insp.last_sequence:
        return False
    fs, paths = store.fs, store.paths
    try:
        names = fs.listdir(paths.snap) if fs.kind(paths.snap) == 'dir' else []
        for n in names:
            if SNAP_NAME_RE.fullmatch(n) is None:             # N4: a stray file is not a generation
                continue
            raw = fs.read_bytes(os.path.join(paths.snap, n))
            sf = _try_decode(raw, store.reader, store)
            if sf is None:
                if peek(raw, store.reader) in ('ok', 'damage'):
                    continue                                  # a torn create of the G1 snapshot: an orphan, kept
                return False
            if sf.provenance['kind'] not in (ProvenanceKind.INIT_FLAT, ProvenanceKind.INIT_HOLD) or sf.generation != 1:
                return False
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------------------------------- write paths
def _copy_evidence(store, seen, now_ms):
    """A04: copy every damaged member into the envelope (originals untouched). Returns (refs, incident ids)."""
    fs, paths = store.fs, store.paths
    refs, incs = [], []
    for rel, _gen in seen.damaged_members:
        p = os.path.join(paths.account, *rel.split('/'))
        try:
            if fs.kind(p) != 'file':
                continue
            data = fs.read_bytes(p)
            _, mtime_ns = fs.stat(p)
        except OSError:
            continue
        inc = incident_id(store.account_id, rel, sha256_hex(data))
        ref, created = write_envelope(fs, paths.account, store.account_id, data, source='damaged_member', rel_path=rel,
                                      mtime_ms=mtime_ns // 1_000_000, incident_id=inc,
                                      reason=str(ReasonCode.RECOVERY_SCHEMA_INVALID), cipher=store.cipher)
        refs.append(ref)
        incs.append(inc)
        if created:
            store.writes.add('evidence_copy')
    return refs, incs


def _enter_hold(store, items, reason, now_ms, incident, result, findings, seen, candidate=None):
    fs, paths = store.fs, store.paths
    store.mode, store.hold_kind = Mode.HOLD, HoldKind.NORMAL
    if candidate is not None:
        store.candidate = candidate
    cur = store.current
    refs = []
    head_damaged = store.head is None and any(rel in ('HEAD.a', 'HEAD.b') for rel, _ in seen.damaged_members)
    try:
        refs, incs = _copy_evidence(store, seen, now_ms)
        already = (cur is not None and cur.provenance['trust'] != Trust.MANAGED
                   and set(incs) <= set(cur.provenance['incident_ids'])
                   and {i['cause'] for i in cur.provenance['items']} >= {i.cause for i in items})
        if head_damaged:              # both HEAD slots damaged: the evidence copy is the only write here; a damaged
            already = True            # slot is rewritten only by promotion, after its evidence exists (A04)
        if not already:
            quarantine = [{'generation': g, 'name': rel.split('/')[-1], 'incident_id': inc}
                          for (rel, g), inc in zip(seen.damaged_members, incs) if g is not None and rel.startswith(
                              'snap/')] if seen.damaged_members else []
            g = store.next_generation()
            pf = unknown_portfolio(store.account_id, g, now_ms, reason, portfolio_id=store.aggregate_id)
            prov = provenance(ProvenanceKind.HOLD, Trust.HOLD,
                              from_generation=store.candidate.generation if store.candidate else None,
                              incident_ids=incs, items=[i.doc() for i in items],
                              evidence=[{'name': r.name, 'sha256': r.sha256, 'size': r.size, 'source': r.source,
                                         'rel_path': r.rel_path, 'incident_id': r.incident_id} for r in refs])
            for d in (paths.data, paths.accounts, paths.account, paths.snap):
                if fs.kind(d) == 'missing':
                    fs.mkdir(d)
                    fs.fsync_dir(os.path.dirname(d))
            store.commit(pf, prov, now_ms, lsn_upto=store._insp.last_sequence if getattr(store, '_insp', None)
                         else 0, quarantine=quarantine, write_class='hold_snapshot')
        if store.journal is None and getattr(store, '_insp', None) is not None and store._insp.plan is not None:
            rec = open_journal(store._insp, cipher=store.cipher)
            store.journal = rec.journal
    except (OSError, DurabilityUnavailable, ValueError) as ex:
        return _hard_hold(store, now_ms, incident, result, findings,
                          f'store not writable while entering HOLD: {failure_reason(ex)[1]}', items=items)
    incident('hold', str(reason), {'items': [i.doc() for i in items]})
    return result(Mode.HOLD, hold_kind=HoldKind.NORMAL, reason=reason,
                  portfolio=store.current.portfolio if store.current else None,
                  candidate=store.candidate.portfolio if store.candidate else None, candidate_kind=_cand_kind(store),
                  items=tuple(items), findings=findings, evidence=tuple(refs), store=store)


def _hard_hold(store, now_ms, incident, result, findings, why, items=()):
    store.mode, store.hold_kind = Mode.HOLD, HoldKind.DURABILITY_UNAVAILABLE
    store.mark_hard_hold(now_ms)
    incident('hard_hold', str(ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE), {'why': why})
    return result(Mode.HOLD, hold_kind=HoldKind.DURABILITY_UNAVAILABLE, reason=ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE,
                  portfolio=None, candidate=store.candidate.portfolio if store.candidate else None,
                  candidate_kind=_cand_kind(store), items=tuple(items) + (HoldItem('account', 'store_unwritable', why),),
                  findings=findings, store=store, view=getattr(store, 'view', None))


def _init(store, exchange, now_ms, incident, result, findings):
    """Rule 8 / design 4.6. Nothing is written unless the exchange snapshot succeeded."""
    fs, paths, account = store.fs, store.paths, store.account
    try:
        snap = exchange.snapshot()
    except ExchangeDown:
        return result(Mode.INIT_WAIT, findings=findings, writes=frozenset())
    bp = _binding_problem(fs, paths, account)                    # N7: INIT never claims a key it cannot prove
    if bp is not None:
        return result(Mode.HOLD, hold_kind=HoldKind.NORMAL, reason=ReasonCode.BINDING_MISMATCH,
                      items=(HoldItem('account', 'identity', bp[1]),), findings=findings, store=None)
    flat_ok = snap.flat and account.binding_state is BindingState.CONFIRMED and snap.key_digest == \
        account.binding.key_digest
    try:
        for d in (paths.data, paths.accounts, paths.account, paths.snap):
            if fs.kind(d) == 'missing':
                fs.mkdir(d)
                fs.fsync_dir(os.path.dirname(d))
        from .envelope import ensure_evidence_dir
        ensure_evidence_dir(fs, paths.account)
        insp = inspect_journal(paths.account, account.account_id, store.aggregate_id, fs=fs)
        if insp.verdict is JVerdict.MISSING:
            store.journal = create_journal(paths.account, account.account_id, store.aggregate_id, fs=fs,
                                           cipher=store.cipher)
        else:
            rec = open_journal(insp, cipher=store.cipher)
            if rec.journal is None or rec.journal.last_sequence():
                raise OSError(5, 'interrupted INIT left an unusable journal')
            store.journal = rec.journal
        g = store.next_generation()
        rec_id = reconciliation_id(account.account_id, snap.taken_ms, g)
        recon = {'recon_id': rec_id, 'taken_ms': snap.taken_ms, 'key_digest': snap.key_digest,
                 'verdict': 'flat', 'snapshot_sha256': sha256_hex(snap.canonical())}
        if flat_ok:
            pf = Portfolio(portfolio_id=store.aggregate_id, account_id=account.account_id, generation=g,
                           ownership=Ownership.KNOWN_EMPTY,
                           proof=OwnershipProof(kind=ProofKind.FLAT_SNAPSHOT, at_ms=snap.taken_ms,
                                                reconciliation_id=rec_id, key_digest=account.binding.key_digest,
                                                decision_id=None, through_sequence=None),
                           entries_mode=EntriesMode.ACTIVE, mode_since_ms=now_ms, pause_reasons=(), positions=(),
                           intents=(), entry_stops=(), hold_kind=None)
            check_account_portfolio(account, pf)
            prov = provenance(ProvenanceKind.INIT_FLAT, Trust.MANAGED, recon=recon)
        else:
            pf = unknown_portfolio(account.account_id, g, now_ms, ReasonCode.RECONCILE_UNRECONCILED,
                                   portfolio_id=store.aggregate_id)
            items = [HoldItem(f'{p.symbol}:{p.side}', 'unowned_position', str(p.qty)) for p in snap.positions if p.qty]
            items += [HoldItem(f'{o.symbol}:{o.side}', 'unowned_order', o.client_id) for o in snap.orders]
            if account.binding_state is not BindingState.CONFIRMED or snap.key_digest != account.binding.key_digest:
                items.append(HoldItem('account', 'identity', 'binding not confirmed for this key'))
            prov = provenance(ProvenanceKind.INIT_HOLD, Trust.HOLD_INIT, items=[i.doc() for i in items])
        store.commit(pf, prov, now_ms, lsn_upto=0, write_class='init_commit')
        store.bind(now_ms)
    except (OSError, DurabilityUnavailable):
        if store.journal is not None:                               # INIT did not commit: release the writer
            store.journal.close()
            store.journal = None
        if snap.flat:
            return result(Mode.INIT_WAIT, findings=findings + ('store not writable: INIT cannot commit',))
        store.mode, store.hold_kind = Mode.HOLD_INIT, HoldKind.DURABILITY_UNAVAILABLE
        incident('hard_hold', str(ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE), {'why': 'INIT cannot commit'})
        return result(Mode.HOLD_INIT, hold_kind=HoldKind.DURABILITY_UNAVAILABLE,
                      reason=ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE, findings=findings, store=None,
                      items=(HoldItem('account', 'store_unwritable'),))
    if flat_ok:
        store.mode = Mode.MANAGE
        return result(Mode.MANAGE, portfolio=store.current.portfolio, findings=findings, store=store)
    store.mode, store.hold_kind = Mode.HOLD_INIT, HoldKind.NORMAL
    return result(Mode.HOLD_INIT, hold_kind=HoldKind.NORMAL, reason=ReasonCode.RECONCILE_UNRECONCILED,
                  portfolio=store.current.portfolio, items=tuple(HoldItem(**i) for i in prov['items']),
                  findings=findings, store=store)


__all__ = ['Mode', 'Paths', 'BootResult', 'AccountStore', 'boot', 'aggregate_for', 'unknown_portfolio']
