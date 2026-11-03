"""Connector that turns Anthropic Enterprise Analytics API data into meterline records."""

from importlib.metadata import PackageNotFoundError as _NotInstalled, version as _version

try:
    __version__ = _version("meterline-anthropic")  # the one source is pyproject.toml
except _NotInstalled:  # a source tree that is not installed
    __version__ = "0+unknown"

# after __version__: client.py reads it
from meterline import RunResult

from .cli import StateError, complete_through, run
from .client import ApiError
from .fetch import RawFileExists  # noqa: E402

# The public interface is the command line, ``run``, the records it writes and the documented ``testing`` module
# (README, "Public interface"); every other module of this package is internal.
__all__ = ["ApiError", "RawFileExists", "RunResult", "StateError", "__version__", "complete_through", "run"]
