"""Typed domain failures. Every rejection is a DomainError whose `.path` names the field."""


MAX_PATH_CHARS = 240        # PR #44 (Cowork 3): an error never echoes a huge value - path and message are bounded
MAX_MSG_CHARS = 400


def _bounded(text, n):
    text = str(text)
    return text if len(text) <= n else f'{text[:n - 40]}...<{len(text)} chars>'


class DomainError(ValueError):
    """Base class. `.path` is a dotted field path such as 'Lot[lot_..].qty' or 'portfolio.positions[0].lots[1].qty'.
    Both `.path` and `.msg` are bounded (MAX_PATH_CHARS / MAX_MSG_CHARS): a hostile value never sizes the error."""

    def __init__(self, path, msg):
        path, msg = _bounded(path, MAX_PATH_CHARS), _bounded(msg, MAX_MSG_CHARS)
        super().__init__(f'{path}: {msg}')
        self.path = path
        self.msg = msg


class InvalidRecord(DomainError):
    """Current-version damage: a wrong type, an unknown / missing / duplicate field, a broken invariant or a dangling
    cross-record reference. Never coerced into empty ownership."""


class ForeignDocument(InvalidRecord):
    """Not a NEWCORE document at all (for example a legacy state.json). Legacy state is never imported."""


class UnsupportedVersion(DomainError):
    """The document's schema_version is not the one this build reads. The body is NOT inspected. What to do about it
    (abort read-only, migrate as a new generation) is NC-02 policy. `.direction` is 'newer', 'older' or 'unknown'."""
    direction = 'unknown'


class FutureSchema(UnsupportedVersion):
    direction = 'newer'


class UnknownSchema(FutureSchema):
    """schema_version is not a JSON integer >= 0: treated as an unknown (future) format."""
    direction = 'unknown'


class OlderSchema(UnsupportedVersion):
    direction = 'older'


class OwnershipUnknown(DomainError):
    """Asked for owned items of a portfolio whose ownership is UNKNOWN. Unknown is never iterated as empty."""


class EventOrderError(InvalidRecord):
    """An event that cannot be admitted: a sequence gap, an already-used sequence, an event id re-used with different
    canonical bytes, or another aggregate's event (contract invariant 12)."""
