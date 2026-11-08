"""A-6 of the v0.3 hardening spec: a crash between the rename of a frozen month and the write of ``frozen.json`` is recovered.

The crash is simulated by making the write of ``frozen.json`` raise. Synthetic builds (``publish_helpers.make_build``) and a
table for the connectors' ``complete_through``, as in the other publish tests."""
import json
import os
from datetime import date, datetime, timezone

import pytest

from meterline_job import publish
from publish_helpers import config, make_build

NOW = datetime(2026, 5, 4, 6, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def connectors(monkeypatch):
    monkeypatch.setattr(publish, "connector_complete_through", lambda spec, data_dir: date(2026, 4, 30))
    monkeypatch.setenv("METERLINE_GOLD_KEY", "a-key-of-at-least-32-bytes-that-you-keep-out-of-git")


def run(tmp_path):
    return publish.run_publish(config(tmp_path), publish.Context(today=date(2026, 5, 4), now=lambda: NOW, run_complete=True))


def crash_before_frozen_json(monkeypatch):
    real = publish._write_json

    def write(path, obj):
        if path.name == "frozen.json":
            raise OSError("simulated crash between the rename and the write of frozen.json")
        return real(path, obj)

    monkeypatch.setattr(publish, "_write_json", write)
    return real


def crashed(tmp_path, monkeypatch):
    make_build(tmp_path / "gold", "2026-05-04")
    real = crash_before_frozen_json(monkeypatch)
    assert run(tmp_path).exit_code == 4  # the freeze got as far as the rename
    monkeypatch.setattr(publish, "_write_json", real)
    assert (tmp_path / "gold" / "frozen" / "2026-04").is_dir() and not (tmp_path / "gold" / "frozen.json").exists()


def test_the_next_publish_recovers_the_entry_a_clean_freeze_writes(tmp_path, monkeypatch):
    clean = tmp_path / "clean"
    clean.mkdir()
    (tmp_path / "crash").mkdir()
    make_build(clean / "gold", "2026-05-04")
    assert run(clean).exit_code == 0
    expected = json.loads((clean / "gold" / "frozen.json").read_text(encoding="utf-8"))
    crashed(tmp_path / "crash", monkeypatch)
    result = run(tmp_path / "crash")
    assert result.exit_code == 0, result
    assert "frozen_entry_recovered 2026-04" in result.extra["warnings"]
    assert json.loads((tmp_path / "crash" / "gold" / "frozen.json").read_text(encoding="utf-8")) == expected
    published = json.loads((tmp_path / "crash" / "gold" / "published" / "central" / "published.json").read_text(encoding="utf-8"))
    assert published["frozen_months"] == ["2026-04"] and "frozen_entry_recovered 2026-04" in published["warnings"]
    again = run(tmp_path / "crash")  # recovered once: the warning does not repeat
    assert again.exit_code == 0 and "frozen_entry_recovered 2026-04" not in again.extra["warnings"]


@pytest.mark.parametrize("damage", ["flip", "no_marker", "bad_marker", "wrong_month", "no_sums"])
def test_a_folder_whose_sums_or_marker_fail_still_stops_publish_with_exit_4(tmp_path, monkeypatch, damage):
    crashed(tmp_path, monkeypatch)
    folder = tmp_path / "gold" / "frozen" / "2026-04"
    if damage == "flip":
        victim = folder / "central" / "person_month.csv"
        victim.write_text(victim.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
    elif damage == "no_marker":
        (folder / publish.MARKER).unlink()
    elif damage == "bad_marker":
        (folder / publish.MARKER).write_text("{not json", encoding="utf-8")
    elif damage == "wrong_month":
        (folder / publish.MARKER).write_text(json.dumps({"month": "2026-03", "source_build": "2026-05-04"}), encoding="utf-8")
    else:
        (folder / publish.SUMS).unlink()
    # the damaged marker is covered by the sums, so the sums are rewritten: only the marker or the sums fail, not both at once
    if damage in ("no_marker", "bad_marker", "wrong_month"):
        publish.write_sums(folder)
    result = run(tmp_path)
    assert result.exit_code == 4 and result.failure_kind == "publish", result
    assert "2026-04" in result.error and "cannot be recovered" in result.error
    assert not (tmp_path / "gold" / "frozen.json").exists()
    assert not os.path.lexists(tmp_path / "gold" / "published")
