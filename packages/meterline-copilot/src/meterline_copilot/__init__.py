"""meterline-copilot: reads GitHub Copilot usage reports, billing and seats and emits meterline records."""

from importlib.metadata import PackageNotFoundError as _NotInstalled, version as _version

try:
    __version__ = _version("meterline-copilot")  # the one source is pyproject.toml
except _NotInstalled:  # a source tree that is not installed
    __version__ = "0+unknown"

from meterline import RunResult

from .cli import StateError, complete_through, run
from .client import ApiError, UnusableBody
from .fetch import BadShape, PaginationError, RawFileExists

# The public interface is the command line, ``run``, the records they write and the documented ``testing``
# module (README, "Public interface"); every other module of this package is internal.
__all__ = [
    "ApiError", "BadShape", "PaginationError", "RawFileExists", "RunResult", "StateError", "UnusableBody",
    "__version__", "complete_through", "run",
]
