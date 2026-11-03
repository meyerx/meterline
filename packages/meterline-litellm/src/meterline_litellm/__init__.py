"""meterline-litellm: reads a LiteLLM proxy's admin API and emits meterline records."""

from importlib.metadata import PackageNotFoundError as _NotInstalled, version as _version

try:
    __version__ = _version("meterline-litellm")  # the one source is pyproject.toml
except _NotInstalled:  # a source tree that is not installed
    __version__ = "0+unknown"

from meterline import RunResult

from .cli import complete_through, run
from .client import ApiError, PaginationError
from .fetch import PathNotFound

# The public interface is the command line, ``run``, the records it writes and the documented ``testing`` module
# (README, "Public interface"); every other module of this package is internal.
__all__ = ["ApiError", "PaginationError", "PathNotFound", "RunResult", "complete_through", "run", "__version__"]
