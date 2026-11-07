"""Synthetic authority-boundary checks; no actual C260 market data is read."""
from dataclasses import replace
import hashlib
import json

import pytest

from tools.research import c260_authority as A


@pytest.fixture
def issued_metadata(tmp_path, monkeypatch):
    store = tmp_path / 'synthetic-store'
    store.mkdir()
    monkeypatch.setattr(A, 'ROOT', tmp_path)
    monkeypatch.setattr(A, '_git', lambda *args: '.git' if args == ('rev-parse', '--git-common-dir') else pytest.fail(args))
    monkeypatch.setattr(A, '_authenticate_metadata', lambda: ('a' * 40, 'b' * 64, store, (), 1, 2))
    return tmp_path


def test_durable_claim_precedes_capability_and_a_failure_never_refunds(issued_metadata):
    with pytest.raises(RuntimeError, match='synthetic failure'):
        with A.development_authority() as authority:
            A.validate_issued(authority)
            assert json.loads(authority.claim_path.read_bytes())['state'] == 'CONSUMED_BEFORE_SOURCE'
            assert hashlib.sha256(authority.claim_path.read_bytes()).hexdigest() == authority.claim_sha256
            raise RuntimeError('synthetic failure')
    with pytest.raises(A.C.C260Locked, match='ISSUED'):
        A.validate_issued(authority)
    with pytest.raises(A.C.C260Locked, match='ALREADY_SPENT'):
        with A.development_authority():
            pytest.fail('spent candidate reopened')


def test_completed_read_does_not_refund_and_copy_cannot_authorize_source(issued_metadata):
    with A.development_authority() as authority:
        with pytest.raises(A.C.C260Locked, match='ISSUED'):
            A.validate_issued(replace(authority))
        A.validate_issued(authority)
    with pytest.raises(A.C.C260Locked, match='ALREADY_SPENT'):
        with A.development_authority():
            pytest.fail('completed candidate reopened')


@pytest.mark.parametrize('attack', ['corrupt', 'missing'])
def test_claim_tampering_revokes_even_the_actual_issued_object(issued_metadata, attack):
    with A.development_authority() as authority:
        if attack == 'corrupt':
            authority.claim_path.write_bytes(b'changed')
        else:
            authority.claim_path.unlink()
        with pytest.raises(A.C.C260Locked, match='SPEND'):
            A.validate_issued(authority)


@pytest.mark.parametrize('value', [True, False, None, {}, 'development'])
def test_caller_flags_and_docs_are_not_issued_authority(value):
    with pytest.raises(A.C.C260Locked, match='ISSUED'):
        A.validate_issued(value)


def test_authentication_failure_precedes_claim_directory_creation(tmp_path, monkeypatch):
    def refused():
        raise A.C.C260Locked('missing protected receipt')
    monkeypatch.setattr(A, '_authenticate_metadata', refused)
    monkeypatch.setattr(A, '_git', lambda *args: pytest.fail('claim path accessed'))
    with pytest.raises(A.C.C260Locked, match='missing protected receipt'):
        with A.development_authority():
            pytest.fail('unauthenticated source opened')
    assert list(tmp_path.iterdir()) == []


def test_missing_protected_receipt_reads_no_price_file_or_identity(monkeypatch):
    monkeypatch.setattr(A, '_git', lambda *args: '' if args == ('status', '--porcelain') else 'a' * 40)
    monkeypatch.setattr(A, '_tree', lambda path, head='HEAD': (_ for _ in ()).throw(A.C.C260Locked('PROTECTED_DOCUMENT_ABSENT')))
    with pytest.raises(A.C.C260Locked, match='PROTECTED_DOCUMENT_ABSENT'):
        A._authenticate_metadata()


@pytest.mark.parametrize('dirty,head', [(True, 'a' * 40), (False, 'c' * 40)])
def test_unprotected_or_dirty_head_refuses_before_authority_document(dirty, head, monkeypatch):
    def git(*args):
        if args == ('rev-parse', 'HEAD'): return head
        if args == ('status', '--porcelain'): return ' M code.py' if dirty else ''
        return 'a' * 40
    monkeypatch.setattr(A, '_git', git)
    monkeypatch.setattr(A, '_tree', lambda *args: pytest.fail('unprotected document read'))
    with pytest.raises(A.C.C260Locked, match='CLEAN_EXACT_PROTECTED'):
        A._authenticate_metadata()


def test_authority_parser_rejects_ambiguous_and_nonfinite_json():
    for raw in (b'{"candidate_id":260,"candidate_id":261}', b'{"number":NaN}', b'[]'):
        with pytest.raises(A.C.C260Locked):
            A._strict(raw)
