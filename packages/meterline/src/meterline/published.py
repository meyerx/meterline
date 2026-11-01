"""What a connector has already published, and which new records are worth publishing.

A record is published again only when its content differs from the latest published record of the same key, ignoring
the volatile fields (``VOLATILE_FIELDS``: when it was pulled and which batch wrote it). ``load_published`` reads the
latest record per key for each kind from ``records/<kind>/*.jsonl`` under the data directory; each connector passes
its own kinds.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from meterline.records import latest_per_key
from meterline.writers import read_jsonl

VOLATILE_FIELDS = ("pulled_at", "data_refreshed_at", "batch_id")


def comparable(record: Any) -> Any:
    return replace(record, **{name: None for name in VOLATILE_FIELDS})


def drop_unchanged(run_records: Iterable[Any], published_latest: Dict[tuple, Any]) -> List[Any]:
    """Records of one kind that differ from the latest published record of the
    same key, ignoring ``pulled_at``, ``data_refreshed_at`` and ``batch_id``.
    A record with no published counterpart is kept."""
    out = []
    for rec in run_records:
        prev = published_latest.get(rec.key())
        if prev is not None and comparable(prev) == comparable(rec):
            continue
        out.append(rec)
    return out


def load_published(data_dir: Path, kinds: Mapping[str, type]) -> Dict[str, Dict[tuple, Any]]:
    """Latest record per key for each kind, from ``records/<kind>/*.jsonl``."""
    out: Dict[str, Dict[tuple, Any]] = {}
    for kind, cls in kinds.items():
        found: List[Any] = []
        folder = Path(data_dir) / "records" / kind
        if folder.is_dir():
            for path in sorted(folder.glob("*.jsonl")):
                found.extend(read_jsonl(path, cls))
        out[kind] = latest_per_key(found)
    return out


__all__ = ["VOLATILE_FIELDS", "comparable", "drop_unchanged", "load_published"]
