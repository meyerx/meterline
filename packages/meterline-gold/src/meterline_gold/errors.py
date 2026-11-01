"""The two failure classes of a build and their exit codes (spec 10)."""


class InputError(ValueError):
    """Bad arguments, input files, policy, price list or key: exit 2. Nothing is written."""


class BuildFailure(RuntimeError):
    """An assertion or invariant failed during the build: exit 1. Nothing is written."""
