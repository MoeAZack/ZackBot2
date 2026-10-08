"""NC-02a: the minimal durable journal of the NEWCORE vertical slice (nc02_design.md sections 1, 3.1-3.4, 4.2, 10).

    errors      DurabilityUnavailable (a JournalUnavailable), SequenceConflict (a JournalConflict), JournalExists
    fs          FsSeam + RealFs: the only module that touches the OS
    frame       file header / record framing (length + CRC-32), torn-tail vs damage scan
    header      record 0: the typed segment version header (rule 1) and the seal list
    fold        Folder: admission through the step-0 JournalGate (+ NC-01 chain tripwire); JournalState
                (ownership UNKNOWN, live intents classified for the Runner)
    journal     FileJournal (JournalPort), create_journal
    recovery    recover_journal: ABORT_RO / UNREADABLE / DAMAGED / MISSING with zero writes; torn tail -> evidence,
                seal and roll (never truncate)
    hold        Verdict -> StoreDirective (HOLD kinds, reasons) and the store-failure contract for the Runner

NC-02b (later, not here): snapshots + generations, dual-slot HEAD, anchors / rollback, migration, the DPAPI evidence
envelope + ACL, GC, INIT and reconciliation / promotion.
"""
from .errors import DurabilityUnavailable, JournalExists, SequenceConflict
from .fold import Folder, IntentRecovery, IntentView, JournalState, fold
from .fs import FsSeam, RealFs
from .hold import StoreDirective, StoreOutcome, Verdict, directive_for, durability_hold, hard_hold_permits
from .journal import FileJournal, ReadOnlyJournal, create_journal
from .recovery import EvidenceRef, Finding, Recovery, recover_journal

__all__ = ['DurabilityUnavailable', 'JournalExists', 'SequenceConflict', 'Folder', 'IntentRecovery', 'IntentView',
           'JournalState', 'fold', 'FsSeam', 'RealFs', 'StoreDirective', 'StoreOutcome', 'Verdict', 'directive_for',
           'durability_hold', 'hard_hold_permits', 'FileJournal', 'ReadOnlyJournal', 'create_journal',
           'EvidenceRef',
           'Finding', 'Recovery', 'recover_journal']
