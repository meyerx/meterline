"""meterline-job: run the connectors, stage silver and build gold as one daily job."""

from importlib.metadata import PackageNotFoundError as _NotInstalled, version as _version

from meterline_job.cli import main
from meterline_job.config import ConfigError, load_config, parse_config
from meterline_job.job import run_job

try:
    __version__ = _version("meterline-job")  # the one source is pyproject.toml
except _NotInstalled:  # a source tree that is not installed
    __version__ = "0+unknown"

__all__ = ["ConfigError", "__version__", "load_config", "main", "parse_config", "run_job"]
