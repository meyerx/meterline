"""The simulator's output is readable by its owner only (files 0600, folders 0700), whatever the caller's umask."""
import os
import stat

import pytest

import meterline_sim


def modes(root):
    """(files, folders) under ``root`` whose permission bits are not the private ones, as readable strings."""
    bad = []
    count = 0
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(base, name)
            mode = stat.S_IMODE(os.lstat(path).st_mode)
            want = 0o700 if os.path.isdir(path) else 0o600
            count += 1
            if mode != want:
                bad.append("%s %o (want %o)" % (os.path.relpath(path, root), mode, want))
    return count, bad


@pytest.mark.parametrize("umask", [0o022, 0o000], ids=["umask022", "umask000"])
def test_output_private_sim(tmp_path, umask):
    old = os.umask(umask)
    try:
        run = meterline_sim.generate(preset="tiny", seed=7, months=2, out=tmp_path / "sim-out")
        assert os.umask(umask) == umask, "the umask was left changed after the run"
    finally:
        os.umask(old)
    count, bad = modes(run)
    assert count > 20
    assert not bad, "%d of %d not private under umask %03o: %s" % (len(bad), count, umask, "; ".join(bad[:5]))
    # the output folder the run created for itself (its parent did not exist) is private too
    assert stat.S_IMODE(os.stat(tmp_path / "sim-out").st_mode) == 0o700
