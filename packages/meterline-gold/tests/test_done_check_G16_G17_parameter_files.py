"""G16 (parameter files, bad inputs exit 2) and G17 (the example policy is visibly synthetic)."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from gold_helpers import (
    FIXTURE, KEY, fixture_inputs, money_rule_violations, policy_money_amounts,
)


def _pkg():
    import meterline_gold

    return Path(meterline_gold.__file__).resolve().parent


def _generated_schema(model):
    return json.dumps(model.model_json_schema(mode="validation"), sort_keys=True, indent=2) + "\n"


def _assert_committed_equals_generated(path, model):
    assert Path(path).read_text(encoding="utf-8") == _generated_schema(model), Path(path).name


def test_done_check_G16_schema_files_equal_generated(tmp_path):
    from meterline_gold import cli, params

    models = (("policy.schema.json", params.Policy), ("prices.schema.json", params.PriceList))
    for fname, model in models:
        _assert_committed_equals_generated(_pkg() / "schemas" / fname, model)
    out = tmp_path / "schemas"
    assert cli.main(["schemas", "--out", str(out)]) == 0
    for fname in ("policy.schema.json", "prices.schema.json"):
        assert (out / fname).read_bytes() == (_pkg() / "schemas" / fname).read_bytes()
    # negative control (see `test_done_check_stronger_controls.py`): the same assertion on a committed file that lost one property, and on one that
    # gained one, fails
    for fname, model in models:
        committed = json.loads((_pkg() / "schemas" / fname).read_text(encoding="utf-8"))
        lost = json.loads(json.dumps(committed))
        lost["properties"].pop(sorted(lost["properties"])[0])
        gained = json.loads(json.dumps(committed))
        gained["properties"]["added_by_hand"] = {"type": "string"}
        for name, variant in (("lost", lost), ("gained", gained)):
            broken = tmp_path / (name + "-" + fname)
            broken.write_text(json.dumps(variant, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            with pytest.raises(AssertionError):
                _assert_committed_equals_generated(broken, model)
        # and the unbroken copy, written the same way, passes: the control differs by the one property only
        same = tmp_path / ("same-" + fname)
        same.write_text(json.dumps(committed, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        _assert_committed_equals_generated(same, model)


def test_done_check_G16_example_files_validate(example_files):
    from meterline_gold import params

    policy_path, prices_path = example_files
    params.Policy.model_validate(yaml.safe_load(policy_path.read_text(encoding="utf-8")))
    params.PriceList.model_validate(yaml.safe_load(prices_path.read_text(encoding="utf-8")))
    with pytest.raises(Exception):
        params.Policy.model_validate({"policy_format": 1})


def _variant(tmp_path, name, policy=None, prices=None, invoice=None, identities=None):
    """A copy of the fixture inputs with some files replaced by (old, new) text edits."""
    inp = fixture_inputs()
    base = tmp_path / name
    base.mkdir()
    out = {"silver": inp["silver"], "org": base / "org"}
    shutil.copytree(inp["org"], out["org"])
    for key, edit in (("policy", policy), ("prices", prices), ("invoice", invoice)):
        text = inp[key].read_text(encoding="utf-8")
        if edit is not None:
            old, new = edit
            assert old in text, (name, old)
            text = text.replace(old, new) if old else text + new
        target = base / inp[key].name
        target.write_text(text, encoding="utf-8")
        out[key] = target
    if identities is not None:
        path = out["org"] / "identities.csv"
        path.write_text(path.read_text(encoding="utf-8") + identities, encoding="utf-8")
    return out


def _build(files, out, monkeypatch, key=KEY.decode()):
    from meterline_gold import cli

    if key is None:
        monkeypatch.delenv("METERLINE_GOLD_KEY", raising=False)
    else:
        monkeypatch.setenv("METERLINE_GOLD_KEY", key)
    args = [
        "build", "--silver", str(files["silver"]), "--org-files", str(files["org"]),
        "--policy", str(files["policy"]), "--prices", str(files["prices"]), "--out", str(out),
    ]
    if files.get("invoice") is not None:
        args += ["--invoice", str(files["invoice"])]
    return cli.main(args)


CASES = {
    "unknown_policy_key": dict(policy=("", "\nbogus_key: 1\n")),
    "unquoted_amount": dict(policy=('daily_breaker: "17.89"', "daily_breaker: 17.89")),
    "k_is_one": dict(policy=("k: 5", "k: 1")),
    "unknown_compare_by": dict(policy=("compare_by: [function, level]", "compare_by: [function, shoe_size]")),
    "manager_money_rule": dict(
        policy=(
            '{id: NX2, audience: manager, condition: spike_events_at_least, threshold: "1"}',
            '{id: NX2, audience: manager, condition: month_cost_ref_above, threshold: "1.23"}',
        )
    ),
    "currency_differs": dict(prices=("currency: USD", "currency: EUR")),
    "invoice_other_currency": dict(
        invoice=("anthropic,00000000-0000-0000-0000-00000000f001,2026-05,USD,seats", "anthropic,00000000-0000-0000-0000-00000000f001,2026-05,EUR,seats")
    ),
    "one_account_two_persons": dict(
        identities="p02,anthropic,email,user,user_f01,,x-p02@example.com,2026-05-01,\n"
    ),
}


def test_done_check_G16_baseline_inputs_build(tmp_path, monkeypatch):
    # positive control for the failures below: the unmodified inputs build and return 0
    files = _variant(tmp_path, "baseline")
    assert _build(files, tmp_path / "out", monkeypatch) == 0


@pytest.mark.parametrize("case", sorted(CASES))
def test_done_check_G16_bad_input_exits_2(tmp_path, monkeypatch, case):
    files = _variant(tmp_path, case, **CASES[case])
    out = tmp_path / "out"
    assert _build(files, out, monkeypatch) == 2
    assert not out.exists()


def test_done_check_G16_missing_and_short_key_exit_2(tmp_path, monkeypatch, capsys):
    files = _variant(tmp_path, "keys")
    capsys.readouterr()
    assert _build(files, tmp_path / "out1", monkeypatch, key=None) == 2
    assert "METERLINE_GOLD_KEY is not set" in capsys.readouterr().err
    assert _build(files, tmp_path / "out2", monkeypatch, key="k" * 31) == 2
    short_err = capsys.readouterr().err
    assert "at least 32 bytes" in short_err and "METERLINE_GOLD_KEY" in short_err
    assert "k" * 31 not in short_err  # the message names the variable, never its value
    assert _build(files, tmp_path / "out3", monkeypatch, key="k" * 32) == 0


def test_done_check_G16_non_empty_output_folder_exits_2(tmp_path, monkeypatch):
    files = _variant(tmp_path, "nonempty")
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("x", encoding="utf-8")
    assert _build(files, out, monkeypatch) == 2
    assert (out / "keep.txt").read_text(encoding="utf-8") == "x"


def test_done_check_G16_two_litellm_keys_for_one_model_exit_2(tmp_path):
    from meterline_gold import cli

    sample = json.loads((FIXTURE / "litellm" / "sample.json").read_text(encoding="utf-8"))
    sample["other-vendor/model-q-two"] = dict(sample["model-q-two"])
    src = tmp_path / "sample.json"
    src.write_text(json.dumps(sample), encoding="utf-8")
    keys = tmp_path / "keys.txt"
    keys.write_text("model-q-two\nother-vendor/model-q-two\n", encoding="utf-8")
    out = tmp_path / "prices.yaml"
    args = ["import-litellm-prices", "--file", str(src), "--keys", str(keys), "--as-of", "2026-10-01", "--out", str(out)]
    assert cli.main(args) == 2
    assert not out.exists()
    # positive control: one of the two keys alone is fine
    keys.write_text("model-q-two\n", encoding="utf-8")
    assert cli.main(args) == 0


def test_done_check_G17_example_policy_is_visibly_synthetic(example_files):
    policy = yaml.safe_load(example_files[0].read_text(encoding="utf-8"))
    amounts = policy_money_amounts(policy)
    assert len(amounts) >= 12
    assert money_rule_violations(amounts) == []
    assert policy["currency"] == "USD"
    label = policy["label"].lower()
    assert "invented" in label and "made up" in label
    # negative control: a copy with a cap of "50.00" is flagged
    broken = yaml.safe_load(example_files[0].read_text(encoding="utf-8"))
    broken["scenarios"][1]["person_caps"]["default"] = "50.00"
    assert money_rule_violations(policy_money_amounts(broken)) == ["50.00"]
