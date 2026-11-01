"""S01 byte-identical rerun, S02 the seed matters (spec section 15)."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import meterline_sim  # noqa: F401  (the package under test)
from sim_helpers import BASE_PARAMS, folder_diff, gen, read_json


def _manifest_failures(folder):
    folder = Path(folder)
    bad = []
    manifest = read_json(folder, "manifest.json")
    for entry in manifest["files"]:
        data = (folder / entry["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["bytes"]:
            bad.append(entry["path"])
    return bad


_CODE = (
    "import json, sys, meterline_sim;"
    "meterline_sim.generate(sys.argv[1], int(sys.argv[2]), months=int(sys.argv[3]),"
    " out=sys.argv[4], params=json.loads(sys.argv[5]))"
)


def _subprocess_run(out, preset, seed, months, hash_seed):
    env = dict(os.environ, PYTHONHASHSEED=str(hash_seed))
    subprocess.run(
        [sys.executable, "-c", _CODE, preset, str(seed), str(months), str(out), json.dumps(BASE_PARAMS)],
        check=True, env=env,
    )
    return Path(out) / ("%s-s%d-m%d" % (preset, seed, months))


def test_done_check_S01_byte_identical_rerun_tiny(tmp_path):
    first = _subprocess_run(tmp_path / "a", "tiny", 7, 6, 11)
    second = _subprocess_run(tmp_path / "b", "tiny", 7, 6, 22)
    assert folder_diff(first, second) == []
    assert _manifest_failures(first) == []
    assert _manifest_failures(second) == []
    from sim_helpers import all_files

    listed = sorted(e["path"] for e in read_json(first, "manifest.json")["files"])
    assert listed == [n for n in all_files(first) if n != "manifest.json"]


def test_done_check_S01_negative_control_other_seed_is_reported(tmp_path):
    a = gen(tmp_path / "a", "tiny", 7, 2)
    b = gen(tmp_path / "b", "tiny", 8, 2)
    assert folder_diff(a, b) != []


@pytest.mark.slow
def test_done_check_S01_byte_identical_rerun_vertical(vertical_run, tmp_path):
    other = _subprocess_run(tmp_path, "vertical", 7, 6, 33)
    assert read_json(vertical_run, "manifest.json") == read_json(other, "manifest.json")
    assert _manifest_failures(other) == []


def test_done_check_S02_the_seed_matters(tiny_run, tmp_path):
    other = gen(tmp_path, "tiny", 8, 6)
    assert (tiny_run / "org" / "directory.csv").read_bytes() != (other / "org" / "directory.csv").read_bytes()
    differing = [
        p.name
        for p in sorted((tiny_run / "records" / "usage").glob("*.jsonl"))
        if (other / "records" / "usage" / p.name).read_bytes() != p.read_bytes()
    ]
    assert differing


def test_done_check_S02_negative_control_same_seed_has_no_difference(tiny_run, tmp_path):
    same = gen(tmp_path, "tiny", 7, 6)
    assert folder_diff(tiny_run, same) == []
