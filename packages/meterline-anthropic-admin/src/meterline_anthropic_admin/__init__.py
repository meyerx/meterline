"""meterline-anthropic-admin: reads the Anthropic Admin Usage and Cost API of a Console organisation and emits meterline records."""

from importlib.metadata import PackageNotFoundError as _NotInstalled, version as _version

try:
    __version__ = _version("meterline-anthropic-admin")  # the one source is pyproject.toml
except _NotInstalled:  # a source tree that is not installed
    __version__ = "0+unknown"

# after __version__: client.py reads it
from meterline import RunResult  # noqa: E402

from .cli import complete_through, run  # noqa: E402
from .client import ApiError, PaginationError  # noqa: E402

# The public interface is the command line, ``run`` and its ``RunResult``, the error classes below, the records the
# commands write (README, "Public interface") and the documented ``testing`` module; every other module is internal.
__all__ = ["ApiError", "PaginationError", "RunResult", "__version__", "complete_through", "run"]
