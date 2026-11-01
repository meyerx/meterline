"""meterline-sim: an invented organisation and its AI usage, as silver records plus a ground truth."""

from importlib.metadata import PackageNotFoundError as _NotInstalled, version as _version

try:
    __version__ = _version("meterline-sim")  # the one source is pyproject.toml
except _NotInstalled:  # a source tree that is not installed
    __version__ = "0+unknown"

from meterline_sim.pipeline import generate  # noqa: E402
from meterline_sim.verify import verify  # noqa: E402

__all__ = ["generate", "verify", "__version__"]
