"""Regression tests for the Cowork attack on PR #38 (135 cases): the verified items E07, I05, S05, H05 and CP05/I03.
Each test here fails on ee79655 and passes after its fix; each has a mutation in nc01_mutations.py."""
import json

import pytest

import nc01_factories as F
from newcore.domain import DomainError, Outcome, decode_document, decode_result, encode_document, loads

# ----------------------------------------------------------------------------------------------------------- E07
ENVELOPE_FIELDS = ('format', 'schema_version', 'record_type', 'body')
WRONG = [[], {}, [1], {'a': 1}, 0, 1, -1, None, True, False, 1.5, '', 'x']


@pytest.mark.parametrize('field', ENVELOPE_FIELDS)
@pytest.mark.parametrize('value', WRONG, ids=repr)
def test_every_damaged_envelope_field_is_a_typed_failure(field, value):
    """E07: a damaged envelope field (record_type=[] crashed with an untyped TypeError) is always a typed failure, on
    every entry point: the dict decoder, text, bytes and the typed outcome API."""
    doc = encode_document(F.single_lot_portfolio()[0])
    if type(doc[field]) is type(value) and doc[field] == value:
        pytest.skip('the valid value itself')
    doc[field] = value
    with pytest.raises(DomainError):
        decode_document(doc)
    text = json.dumps(doc)
    for blob in (text, text.encode('utf-8')):
        with pytest.raises(DomainError):
            loads(blob)
        res = decode_result(blob)
        assert res.outcome is not Outcome.OK and res.record is None and isinstance(res.error, DomainError)


@pytest.mark.parametrize('doc', [[], 'x', 7, None, {1: 2}, {'format': 'zackbot.newcore', 'schema_version': 1,
                                                          'record_type': 'portfolio', 'body': {}, 3: 4}], ids=repr)
def test_non_dict_documents_and_non_text_keys_are_typed_failures(doc):
    with pytest.raises(DomainError):
        decode_document(doc)
