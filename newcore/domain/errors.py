"""Typed domain failures. Every rejection is a DomainError whose `.path` names the field."""


class DomainError(ValueError):
    """Base class. `.path` is a dotted field path such as 'Lot[lot_..].qty' or 'portfolio.positions[0].lots[1].qty'."""

    def __init__(self, path, msg):
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
