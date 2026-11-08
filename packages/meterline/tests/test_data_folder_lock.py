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


# ---- v0.3 hardening A-11: a lock the job needs fails closed -----------------------------------------------------------------

import pytest  # noqa: E402

import meterline.run as run_module  # noqa: E402
from meterline.run import LockUnavailable, file_lock, lock_problem  # noqa: E402


def test_the_default_lock_still_fails_open_without_fcntl(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run_module, "fcntl", None)
    monkeypatch.setattr(run_module, "_warned_no_fcntl", False)
    with file_lock(tmp_path / ".lock") as free:
        assert free is True
    assert "not available on this platform" in capsys.readouterr().err


def test_a_required_lock_without_fcntl_raises_before_the_block(tmp_path, monkeypatch):
    monkeypatch.setattr(run_module, "fcntl", None)
    entered = []
    with pytest.raises(LockUnavailable, match="fcntl"):
        with file_lock(tmp_path / ".lock", required=True):
            entered.append(True)
    assert entered == []


def test_a_required_lock_whose_file_cannot_be_made_raises_and_the_default_goes_on(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(LockUnavailable, match="cannot make the lock file"):
        with file_lock(blocker / "gold" / ".lock", required=True):
            raise AssertionError("the block ran")
    with file_lock(blocker / "gold" / ".lock") as free:  # today's behaviour for a connector: a warning, and it goes on
        assert free is True
    assert "cannot make the lock file" in capsys.readouterr().err


def test_a_required_lock_that_is_held_is_not_an_error_but_a_false(tmp_path):
    with file_lock(tmp_path / ".lock", required=True) as first:
        assert first is True
        with file_lock(tmp_path / ".lock", required=True) as second:
            assert second is False


def test_data_folder_lock_passes_required_on(tmp_path, monkeypatch):
    monkeypatch.setattr(run_module, "fcntl", None)
    with pytest.raises(LockUnavailable):
        with run_module.data_folder_lock(tmp_path, required=True):
            pass


def test_lock_problem_names_what_is_missing_in_plain_words(tmp_path, monkeypatch):
    assert lock_problem(None) is None and lock_problem(tmp_path / "gold" / ".lock") is None
    assert mode(tmp_path / "gold") == 0o700 and mode(tmp_path / "gold" / ".lock") == 0o600  # asking makes the file the lock would use
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert "cannot make the lock file" in lock_problem(blocker / "gold" / ".lock")
    monkeypatch.setattr(run_module, "fcntl", None)
    message = lock_problem(None)
    assert "fcntl" in message and "POSIX" in message
