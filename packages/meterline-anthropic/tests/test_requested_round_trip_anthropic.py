"""The run manifest's optional ``requested`` field: written by ``to_dict``, read back by ``read``; an older manifest
without it reads as ``None``, so ``rebuild`` keeps working on old and new manifests.

Run: python3 -m pytest -q packages/meterline-anthropic/tests/test_requested_round_trip_anthropic.py
"""
import json
from datetime import datetime, timezone

from meterline.run import requested

from meterline_anthropic.fetch import RunManifest

PULLED_AT = datetime(2026, 9, 15, 6, 0, tzinfo=timezone.utc)
BATCH = "20260915T060000Z"


def test_requested_round_trips(tmp_path):
    with requested("auto"):
        made = RunManifest(command="backfill", batch_id=BATCH, pulled_at=PULLED_AT)
    assert made.requested == "auto"
    path = made.write(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["requested"] == "auto"
    back = RunManifest.read(path)
    assert back.requested == "auto"
    assert back == made


def test_a_plain_run_writes_requested_null(tmp_path):
    made = RunManifest(command="daily", batch_id=BATCH, pulled_at=PULLED_AT)
    path = made.write(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["requested"] is None
    assert RunManifest.read(path).requested is None


def test_a_manifest_written_before_auto_reads_as_requested_none(tmp_path):
    path = RunManifest(command="backfill", batch_id=BATCH, pulled_at=PULLED_AT).write(tmp_path)
    old = json.loads(path.read_text(encoding="utf-8"))
    del old["requested"]
    path.write_text(json.dumps(old), encoding="utf-8")
    with requested("auto"):  # read inside an auto run: the file's absence of the field wins over the run's request
        assert RunManifest.read(path).requested is None
