"""Domain errors raised by core/. The API layer maps them to HTTP problem responses."""


class CoreError(Exception):
    """Base class. `message` is safe to show to an end user."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(CoreError):
    pass


class ConflictError(CoreError):
    """The request is well-formed but clashes with current state (duplicate, in use, wrong status)."""


class InvalidInputError(CoreError):
    """Input that passed schema validation but is still unusable (e.g. an unreadable file)."""
