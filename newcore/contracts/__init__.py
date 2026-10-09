"""NEWCORE wire contracts (SIGNAL-01 / SCORE-01): application-level validators for the JSON contracts in contracts/.

The JSON Schemas carry the structure; this package carries the cross-field, time, bound, unit, hash and reason-registry
rules a JSON Schema cannot express. Pure stdlib + newcore.domain: no IO, clock, network or legacy module. An adapter runs
parse_strict -> JSON Schema -> the matching check_* function, and serializes results only through encode_result.
"""
