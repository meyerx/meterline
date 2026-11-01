"""F-33: a gold build is readable by its owner only, whatever the umask of the shell that runs it.

Files are 0600 and folders 0700, the output folder and any parent the build creates included. The process umask
is put back afterwards, after a failing build too.
"""

import os
import stat

import pytest

from gold_helpers import KEY, fixture_inputs

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")


def _modes(root):
    files, dirs = {}, {}
    for path in [root, *root.rglob("*")]:
        mode = stat.S_IMODE(path.lstat().st_mode)
        (dirs if path.is_dir() else files)[path.relative_to(root.parent).as_posix()] = mode
    return files, dirs


def _umask():
    old = os.umask(0)
    os.umask(old)
    return old


def test_output_files_private_permissions(tmp_path):
    import meterline_gold

    pytest.importorskip("pyarrow")
    inp = fixture_inputs()
    out = tmp_path / "new-parent" / "gold"
    old = os.umask(0o022)
    try:
        meterline_gold.build(silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
                             invoice=inp["invoice"], key=KEY, out=out, parquet=True)
        assert _umask() == 0o022, "the build left the process umask changed"
    finally:
        os.umask(old)
    files, dirs = _modes(out.parent)
    assert len(files) > 50 and any(name.endswith(".parquet") for name in files)
    assert {n: oct(m) for n, m in files.items() if m != 0o600} == {}
    assert {n: oct(m) for n, m in dirs.items() if m != 0o700} == {}


def test_output_files_private_permissions_umask_restored_after_a_failed_build(tmp_path):
    import meterline_gold

    inp = fixture_inputs()
    old = os.umask(0o022)
    try:
        with pytest.raises(meterline_gold.InputError):
            meterline_gold.build(silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
                                 key=b"short", out=tmp_path / "gold")
        assert _umask() == 0o022
    finally:
        os.umask(old)
