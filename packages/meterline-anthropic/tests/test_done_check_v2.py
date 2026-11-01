"""Done-checks A01 to A03 of the v2 spec (section 8, Anthropic migration)."""

import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from meterline_anthropic.testing import FakeApi
from meterline_anthropic.cli import main

HERE = Path(__file__).resolve().parent
V1_PUBLISHED = HERE / "fixtures" / "v1_published"


def _utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


def test_done_check_A01_all_17_v1_checks_pass_against_v2_records():
    """The 17 v1.1 checks are the 16 of test_done_check.py and check 12 of
    test_done_check_shapes.py; with the three assertion changes of spec
    section 4 (checks 5, 13, 16) they run against v2 records."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(HERE / "test_done_check.py"), str(HERE / "test_done_check_shapes.py")],
        capture_output=True, text=True, cwd=str(HERE),
    )
    lines = result.stdout.strip().splitlines()
    tail = lines[-1] if lines else ""
    failed = "\n".join(line for line in lines if line.startswith("FAILED"))
    assert result.returncode == 0 and tail.startswith("17 passed"), failed + "\n" + tail


def test_done_check_A02_input_total_is_the_sum_of_the_buckets(fake_api, fake_clock, tmp_path):
    from meterline.records import UsageRecord
    from meterline.writers import read_jsonl

    data_dir = tmp_path / "data"
    rc = main(
        ["backfill", "--since", "2026-10-01", "--data-dir", str(data_dir)],
        transport=fake_api.transport, clock=fake_clock, sleep=fake_clock.sleep,
        now=lambda: _utc(2026, 10, 9, 6), jitter=lambda: 0,
    )
    assert rc == 0
    records = []
    for path in sorted((data_dir / "records" / "usage").glob("*.jsonl")):
        records.extend(read_jsonl(path, UsageRecord))
    assert len(records) > 100
    for rec in records:
        assert rec.tokens_cache_write == rec.tokens_cache_write_5m + rec.tokens_cache_write_1h
        assert rec.tokens_input_total == (
            rec.tokens_input_uncached + rec.tokens_cache_read
            + rec.tokens_cache_write_5m + rec.tokens_cache_write_1h
        )


def test_done_check_A03_converted_v1_batches_equal_a_v2_rebuild(tmp_path):
    import meterline

    work = tmp_path / "work"
    for part in ("raw", "runs"):
        shutil.copytree(V1_PUBLISHED / part, work / part)
    assert main(["rebuild", "--data-dir", str(work)]) == 0
    rebuilt = sorted((work / "records").glob("*/*.jsonl"))
    assert len(rebuilt) == 5

    v1_files = sorted((V1_PUBLISHED / "records").glob("*/*.jsonl"))
    assert [p.relative_to(V1_PUBLISHED / "records") for p in v1_files] == [
        p.relative_to(work / "records") for p in rebuilt
    ]
    copy_root = tmp_path / "v1-copy"
    for v1_file in v1_files:
        rel = v1_file.relative_to(V1_PUBLISHED / "records")
        copied = copy_root / rel
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(v1_file, copied)
        before = copied.read_bytes()
        out = meterline.convert_v1_batch(copied)
        assert out.parent == copied.parent and out != copied
        assert copied.read_bytes() == before  # the v1 file is not changed
        assert out.read_bytes() == (work / "records" / rel).read_bytes()
