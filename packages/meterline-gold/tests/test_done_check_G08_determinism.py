"""G08: the same inputs and key give the same bytes, with any thread count and hash seed (spec 9.4)."""

import sys

from gold_helpers import KEY, fixture_inputs, folder_bytes, run_cli_subprocess


def _args(silver, org, policy, prices, out, invoice=None):
    args = ["build", "--silver", silver, "--org-files", org, "--policy", policy, "--prices", prices, "--out", out]
    if invoice is not None:
        args += ["--invoice", invoice]
    return args


def test_done_check_G08_fixture_two_builds_same_bytes(tmp_path):
    import meterline_gold

    inp = fixture_inputs()
    first = tmp_path / "first"
    meterline_gold.build(
        silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
        invoice=inp["invoice"], key=KEY, out=first, threads=4,
    )
    second = tmp_path / "second"
    proc = run_cli_subprocess(
        _args(inp["silver"], inp["org"], inp["policy"], inp["prices"], second, inp["invoice"])
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert folder_bytes(first) == folder_bytes(second)
    # negative control: another key gives other bytes
    other = tmp_path / "other"
    meterline_gold.build(
        silver=inp["silver"], org=inp["org"], policy=inp["policy"], prices=inp["prices"],
        invoice=inp["invoice"], key=b"another-key-of-more-than-thirty-two-bytes", out=other,
    )
    assert folder_bytes(first) != folder_bytes(other)


def test_done_check_G08_tiny_sim_two_builds_same_bytes(tmp_path, sim_runs, example_files):
    import meterline_gold

    run = sim_runs["tiny"]
    policy, prices = example_files
    first = tmp_path / "first"
    meterline_gold.build(silver=run.silver, org=run.org, policy=policy, prices=prices, key=KEY, out=first, threads=4)
    second = tmp_path / "second"
    proc = run_cli_subprocess(_args(run.silver, run.org, policy, prices, second), hashseed="777")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert folder_bytes(first) == folder_bytes(second)
    other = tmp_path / "other"
    meterline_gold.build(
        silver=run.silver, org=run.org, policy=policy, prices=prices,
        key=b"another-key-of-more-than-thirty-two-bytes", out=other,
    )
    assert folder_bytes(first) != folder_bytes(other)
    assert sys.version_info >= (3, 10)
