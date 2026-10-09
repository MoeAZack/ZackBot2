"""A strict, dependency-free evaluator for the JSON Schema 2020-12 subset the contracts/ schemas use.

jsonschema is not a pinned dependency, so the contract tests evaluate payloads with this instead (and cross-check with
jsonschema when it happens to be installed). An unknown keyword raises, so a schema can never rely on a keyword this
evaluator silently ignores. Semantics follow the spec: "integer" accepts 1.0 (that is why parse_strict exists), pattern
is an unanchored search, bool is never a number, and const / enum compare JSON values (1 != true).
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANNOTATIONS = {'$schema', '$id', 'title', 'description', '$defs'}
KEYWORDS = ANNOTATIONS | {'type', 'const', 'enum', 'pattern', 'minimum', 'maximum', 'minLength', 'maxLength', 'required',
                          'properties', 'additionalProperties', 'propertyNames', 'allOf', 'anyOf', 'oneOf', 'not', 'if',
                          'then', 'else', 'items', 'minItems', 'maxItems', 'uniqueItems', '$ref'}


def load(name):
    with open(os.path.join(ROOT, 'contracts', name), encoding='utf-8') as f:
        return json.load(f)


def _type_ok(v, t):
    if t == 'null': return v is None
    if t == 'boolean': return type(v) is bool
    if t == 'string': return type(v) is str
    if t == 'object': return type(v) is dict
    if t == 'array': return type(v) is list
    if t == 'integer': return (type(v) is int) or (type(v) is float and v.is_integer())
    if t == 'number': return type(v) in (int, float)
    raise ValueError(f'unknown type {t}')


def _key(v):
    """JSON-equality key: bool and number never compare equal; 1 == 1.0."""
    if type(v) is bool: return ('b', v)
    if type(v) in (int, float): return ('n', float(v))
    if type(v) is dict: return ('o', tuple(sorted((k, _key(x)) for k, x in v.items())))
    if type(v) is list: return ('a', tuple(_key(x) for x in v))
    return ('s' if type(v) is str else 'z', v)


def errors(schema, v, root=None, path='$'):
    """Every violation as a list of 'path: keyword' strings (empty = valid)."""
    root = schema if root is None else root
    if schema is True: return []
    if schema is False: return [f'{path}: false']
    unknown = set(schema) - KEYWORDS
    if unknown: raise ValueError(f'unsupported keyword(s) {sorted(unknown)} at {path}')
    out = []
    if '$ref' in schema:
        ref = schema['$ref']
        assert ref.startswith('#/$defs/'), ref
        out += errors(root['$defs'][ref[len('#/$defs/'):]], v, root, path)
    if 'type' in schema:
        ts = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        if not any(_type_ok(v, t) for t in ts): return out + [f'{path}: type']
    if 'const' in schema and _key(v) != _key(schema['const']): out.append(f'{path}: const')
    if 'enum' in schema and _key(v) not in {_key(e) for e in schema['enum']}: out.append(f'{path}: enum')
    if type(v) is str:
        if 'pattern' in schema and re.search(schema['pattern'], v) is None: out.append(f'{path}: pattern')
        if 'minLength' in schema and len(v) < schema['minLength']: out.append(f'{path}: minLength')
        if 'maxLength' in schema and len(v) > schema['maxLength']: out.append(f'{path}: maxLength')
    if type(v) in (int, float):
        if 'minimum' in schema and v < schema['minimum']: out.append(f'{path}: minimum')
        if 'maximum' in schema and v > schema['maximum']: out.append(f'{path}: maximum')
    if type(v) is dict:
        props = schema.get('properties', {})
        for k in schema.get('required', []):
            if k not in v: out.append(f'{path}.{k}: required')
        for k, x in v.items():
            if 'propertyNames' in schema: out += errors(schema['propertyNames'], k, root, f'{path}[name {k}]')
            if k in props: out += errors(props[k], x, root, f'{path}.{k}')
            elif 'additionalProperties' in schema: out += errors(schema['additionalProperties'], x, root, f'{path}.{k}')
    if type(v) is list:
        if 'minItems' in schema and len(v) < schema['minItems']: out.append(f'{path}: minItems')
        if 'maxItems' in schema and len(v) > schema['maxItems']: out.append(f'{path}: maxItems')
        if schema.get('uniqueItems') and len({_key(x) for x in v}) != len(v): out.append(f'{path}: uniqueItems')
        if 'items' in schema:
            for i, x in enumerate(v): out += errors(schema['items'], x, root, f'{path}[{i}]')
    for sub in schema.get('allOf', []): out += errors(sub, v, root, path)
    if 'anyOf' in schema and not any(not errors(s, v, root, path) for s in schema['anyOf']): out.append(f'{path}: anyOf')
    if 'oneOf' in schema and sum(not errors(s, v, root, path) for s in schema['oneOf']) != 1: out.append(f'{path}: oneOf')
    if 'not' in schema and not errors(schema['not'], v, root, path): out.append(f'{path}: not')
    if 'if' in schema:
        branch = 'then' if not errors(schema['if'], v, root, path) else 'else'
        if branch in schema: out += errors(schema[branch], v, root, path)
    return out
