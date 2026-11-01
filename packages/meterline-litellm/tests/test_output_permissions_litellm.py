"""F-33: everything the connector writes under its data directory is readable by its owner only, whatever the
umask of the shell: files 0600, folders 0700, the data directory itself included when the run creates it. The
process umask is put back afterwards."""

import os
import stat

import pytest

from litellm_helpers import at, backfill, basic_rows, daily, run, users

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")


def _umask():
    old = os.umask(0)
    os.umask(old)
    return old


def test_output_files_private_permissions(make_proxy, fake_clock, tmp_path):
    pytest.importorskip("pyarrow")
    data_dir = tmp_path / "new-parent" / "data"
    proxy = make_proxy(basic_rows(), users=users("user-a", "user-b"))
    old = os.umask(0o022)
    try:
        assert backfill(data_dir, proxy, fake_clock, "2026-09-14", at("2026-09-20T06:00:00"), "--parquet") == 0
        assert daily(data_dir, proxy, fake_clock, at("2026-09-20T07:00:00"), "--parquet") == 0
        assert run(["rebuild", "--data-dir", str(data_dir), "--parquet"], proxy, fake_clock) == 0
        assert _umask() == 0o022, "the run left the process umask changed"
    finally:
        os.umask(old)
    root = data_dir.parent
    paths = [root, *root.rglob("*")]
    files = {p.relative_to(root).as_posix(): stat.S_IMODE(p.stat().st_mode) for p in paths if p.is_file()}
    dirs = {p.relative_to(root).as_posix(): stat.S_IMODE(p.stat().st_mode) for p in paths if p.is_dir()}
    for top in ("raw", "records", "runs", "state.json"):
        assert any(n.startswith("data/" + top) for n in files), top
    assert any(n.endswith(".parquet") for n in files)
    assert {n: oct(m) for n, m in files.items() if m != 0o600} == {}
    assert {n: oct(m) for n, m in dirs.items() if m != 0o700} == {}
