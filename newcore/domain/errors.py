"""Domain errors. Every rejected record raises a DomainError whose `.path` names the field."""


class DomainError(ValueError):
    """Base class. `.path` is a dotted field path such as 'Lot.qty' or 'portfolio.positions[0].lots[1].qty'."""

    def __init__(self, path, msg):
        super().__init__(f'{path}: {msg}')
        self.path = path
        self.msg = msg


class InvalidRecord(DomainError):
    """Current-schema damage: a wrong type, an unknown or missing key, or a broken invariant.
    NC-02 keeps the evidence and fails closed (HOLD)."""


class ForeignDocument(InvalidRecord):
    """Not a NEWCORE document at all (for example a legacy state.json). Legacy state is never imported (ruling 10)."""


class FutureSchema(DomainError):
    """The document's schema is newer than this build. Raised BEFORE the body is inspected: NC-02 aborts read-only and
    touches no file (ABORT-RO). Deliberately not an InvalidRecord, so it can never be handled as damage."""


class UnknownSchema(FutureSchema):
    """The schema header is not a JSON integer >= 0. Treated as a future format (NC-02 R-VERSION-TYPE)."""


class OwnershipUnknown(DomainError):
    """Asked for owned items of a portfolio whose ownership is UNKNOWN. Unknown is never iterated as empty (ruling 4)."""
