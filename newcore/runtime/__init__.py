"""RT-01: the NEWCORE replacement entrypoint and runtime wiring (PAPER simulation / Binance TESTNET only).

This package composes NC-02 recovery, NC-04 account contexts, NC-05 scheduling, NC-06 risk, NC-07 management,
NC-09 tracing and REC-02 reconciliation.  It imports no legacy module and installs nothing.
"""
from newcore.runtime.config import (AccountSpec, RuntimeConfig, RuntimeConfigError, RuntimeEnvironment,
                                    config_from_mapping)
from newcore.runtime.evidence import EvidenceError, VenueEvidence, confirmed_binding
from newcore.runtime.journaling import JournalingActionSink, SinkBinding, SinkProblem
from newcore.runtime.projection import JournalProjection
from newcore.runtime.ports import (AccountPorts, ActionSink, DurableStateSource, EntryCandidate, EntrySource,
                                   EvidencePort,
                                   ManagementSource, NoEntries, NoManagedLots, RefusingActionSink)
from newcore.runtime.runtime import (AccountPhase, AccountRuntimeSnapshot, HoldCode, Runtime, RuntimeBootError,
                                     RuntimeEvent, RuntimeSnapshot, boot)

__all__ = ['AccountPhase', 'AccountPorts', 'AccountRuntimeSnapshot', 'AccountSpec', 'ActionSink',
           'DurableStateSource', 'EntryCandidate', 'EvidenceError', 'EntrySource', 'EvidencePort', 'HoldCode', 'JournalingActionSink', 'JournalProjection',
           'ManagementSource', 'NoEntries',
           'NoManagedLots', 'RefusingActionSink', 'Runtime', 'RuntimeBootError', 'RuntimeConfig',
           'RuntimeConfigError', 'RuntimeEnvironment', 'RuntimeEvent', 'RuntimeSnapshot', 'SinkBinding', 'SinkProblem', 'VenueEvidence', 'confirmed_binding',
           'boot',
           'config_from_mapping']
