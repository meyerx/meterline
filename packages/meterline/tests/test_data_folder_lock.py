"""A2: one run per data folder. The lock is an exclusive, non-blocking flock on ``<data_dir>/runs/.lock``."""
import os
import stat

import pytest

import meterline.run as run_module
from meterline.run import data_folder_lock, file_lock


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.mark.parametrize("umask", [0o022, 0o000], ids=["umask022", "umask000"])
def test_the_lock_file_is_0600_under_a_0700_folder(tmp_path, umask):
    old = os.umask(umask)
    try:
        with data_folder_lock(tmp_path / "data") as free:
            assert free is True
    finally:
        os.umask(old)
    assert mode(tmp_path / "data" / "runs") == 0o700 and mode(tmp_path / "data" / "runs" / ".lock") == 0o600
    assert sorted(p.name for p in (tmp_path / "data").iterdir()) == ["runs"]  # nothing else appears in the data folder


def test_a_second_holder_is_refused_at_once_with_a_message(tmp_path, capsys):
    with data_folder_lock(tmp_path) as first:
        assert first is True
        with data_folder_lock(tmp_path) as second:  # another open file: flock refuses it even in the same process
            assert second is False
    assert "transient: data folder is in use by another run" in capsys.readouterr().err


def test_the_lock_is_released_when_the_block_ends_or_raises(tmp_path):
    with data_folder_lock(tmp_path):
        pass
    with pytest.raises(RuntimeError):
        with data_folder_lock(tmp_path):
            raise RuntimeError("boom")
    with data_folder_lock(tmp_path) as again:
        assert again is True


def test_two_folders_do_not_block_each_other(tmp_path):
    with data_folder_lock(tmp_path / "a") as a, data_folder_lock(tmp_path / "b") as b:
        assert a is True and b is True


def test_file_lock_names_what_is_locked(tmp_path, capsys):
    with file_lock(tmp_path / ".lock", "gold folder"):
        with file_lock(tmp_path / ".lock", "gold folder") as held:
            assert held is False
    assert "transient: gold folder is in use by another run" in capsys.readouterr().err


def test_lock_skipped_without_fcntl(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run_module, "fcntl", None)
    monkeypatch.setattr(run_module, "_warned_no_fcntl", False)
    with data_folder_lock(tmp_path) as free:
        assert free is True
    with data_folder_lock(tmp_path) as free:  # still skipped, and the warning is not repeated
        assert free is True
    err = capsys.readouterr().err
    assert err.count("warning:") == 1 and "not locked" in err
    assert not (tmp_path / "runs" / ".lock").exists()
