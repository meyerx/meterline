"""F-33: everything the connector writes under its data directory is readable by its owner only, whatever the
umask of the shell: files 0600, folders 0700, the data directory itself included when the run creates it. The
process umask is put back afterwards."""

import os
import stat
from datetime import datetime, timezone

import pytest

from meterline_anthropic.cli import main

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")

NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 5, 7, 0, 0, tzinfo=timezone.utc)


def _run(transport, clock, data_dir, *argv, now=NOW):
    return main([*argv, "--data-dir", str(data_dir)],
                transport=transport, clock=clock, sleep=clock.sleep, now=lambda: now, jitter=lambda: 0)


def _umask():
    old = os.umask(0)
    os.umask(old)
    return old


def test_output_files_private_permissions(fake_api, fake_clock, tmp_path, fake_key_env):
    pytest.importorskip("pyarrow")
    data_dir = tmp_path / "new-parent" / "data"
    old = os.umask(0o022)
    try:
        assert _run(fake_api.transport, fake_clock, data_dir, "backfill", "--since", "2026-09-20", "--parquet") == 0
        assert _run(fake_api.transport, fake_clock, data_dir, "daily", "--parquet", now=LATER) == 0
        assert main(["rebuild", "--data-dir", str(data_dir), "--parquet"]) == 0
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
