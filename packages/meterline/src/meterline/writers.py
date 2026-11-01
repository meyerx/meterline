"""JSONL and Parquet writers for meterline records."""

import json
import os
import typing
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List, Optional, Type, Union

from meterline.records import RecordError, _unwrap

PathLike = Union[str, Path]


def _sorted(records: Iterable) -> List:
    return sorted(records, key=lambda r: r.key())


def write_jsonl(records: Iterable, path: PathLike) -> int:
    """Write one line per record, sorted by record key, keys sorted, LF line
    ends. Returns the number of records written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    items = _sorted(records)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        for rec in items:
            handle.write(json.dumps(rec.to_dict(), sort_keys=True, ensure_ascii=False))
            handle.write("\n")
    os.replace(tmp, target)
    return len(items)


def read_v1_lines(path: PathLike) -> List[dict]:
    """The JSON objects of a JSONL file, as dicts (numbers that are not
    integers become ``Decimal``)."""
    out = []
    with open(path, encoding="utf-8", newline="") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if line.strip():
                out.append(json.loads(line, parse_float=Decimal))
    return out


def read_jsonl(path: PathLike, cls: Type) -> List:
    return [cls.from_dict(line) for line in read_v1_lines(path)]


def write_parquet(records: Iterable, path: PathLike, cls: Optional[Type] = None) -> int:
    """Write records to Parquet (Decimal as decimal128). Needs the ``parquet``
    extra (``pip install meterline[parquet]``)."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "Parquet output needs pyarrow; install the 'parquet' extra "
            "(pip install 'meterline[parquet]')"
        ) from exc

    items = _sorted(records)
    if cls is None:
        if not items:
            raise RecordError("write_parquet: no records and no cls to take a schema from")
        cls = type(items[0])
    hints = typing.get_type_hints(cls)
    from dataclasses import fields

    arrow_types = {
        str: pa.string(),
        int: pa.int64(),
        bool: pa.bool_(),
        date: pa.date32(),
        datetime: pa.timestamp("us", tz="UTC"),
        Decimal: pa.decimal128(38, 12),  # scale = meterline.records.MAX_DECIMALS, pinned by a test
    }
    schema_fields = []
    columns = {}
    for f in fields(cls):
        base, _optional = _unwrap(hints[f.name])
        if typing.get_origin(base) is dict:
            value_type = typing.get_args(base)[1]
            if value_type is int:
                arrow_type = pa.map_(pa.string(), pa.int64())
                convert = int
            else:  # quantities and extras: one decimal type holds ints and Decimals
                arrow_type = pa.map_(pa.string(), pa.decimal128(38, 12))
                convert = Decimal
            values = [[(k, convert(v)) for k, v in sorted(getattr(r, f.name).items())] for r in items]
        else:
            arrow_type = arrow_types[base]
            values = [getattr(r, f.name) for r in items]
        schema_fields.append(pa.field(f.name, arrow_type))
        columns[f.name] = pa.array(values, type=arrow_type)
    table = pa.table(columns, schema=pa.schema(schema_fields))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, str(target))
    return len(items)
