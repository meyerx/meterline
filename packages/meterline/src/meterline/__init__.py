"""meterline: a common record format for AI usage data (v2)."""

from importlib.metadata import PackageNotFoundError as _NotInstalled, version as _version

from meterline.convert import convert_v1, convert_v1_batch
from meterline.records import (
    KINDS,
    MAX_DECIMALS,
    ActivityRecord,
    OrgTotal,
    RecordError,
    SeatAssignment,
    SeatSnapshot,
    UsageRecord,
    actor_cost_failures,
    amount_str,
    batch_failures,
    grain_for,
    latest_per_key,
    quantity_str,
    validate,
    validate_batch,
)
from meterline.run import RunResult, note_failure, result_of_run, run_manifests
from meterline.writers import read_jsonl, write_jsonl, write_parquet

try:
    __version__ = _version("meterline")  # the one source is pyproject.toml
except _NotInstalled:  # a source tree that is not installed
    __version__ = "0+unknown"

__all__ = [
    "KINDS",
    "MAX_DECIMALS",
    "ActivityRecord",
    "OrgTotal",
    "RecordError",
    "RunResult",
    "SeatAssignment",
    "SeatSnapshot",
    "UsageRecord",
    "actor_cost_failures",
    "amount_str",
    "batch_failures",
    "convert_v1",
    "convert_v1_batch",
    "grain_for",
    "latest_per_key",
    "note_failure",
    "quantity_str",
    "read_jsonl",
    "result_of_run",
    "run_manifests",
    "validate",
    "validate_batch",
    "write_jsonl",
    "write_parquet",
    "__version__",
]
