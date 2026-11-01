"""The LiteLLM importer refuses a listed key without an input or output price (gold change-list finding 36)."""

import json

import yaml

from gold_helpers import FIXTURE


def _run(tmp_path, entry, capsys):
    from meterline_gold import cli

    source = tmp_path / "prices.json"
    source.write_text(json.dumps({"vendor-z/model-z": entry}), encoding="utf-8")
    keys = tmp_path / "keys.txt"
    keys.write_text("vendor-z/model-z\n", encoding="utf-8")
    out = tmp_path / "out" / "prices.yaml"
    args = ["import-litellm-prices", "--file", str(source), "--keys", str(keys), "--as-of", "2026-10-01", "--out", str(out)]
    code = cli.main(args)
    return code, out, capsys.readouterr().err


def _assert_refused(code, out, err):
    assert code == 2
    assert "meterline-gold: input error:" in err
    assert "model-z" in err
    assert "vendor-z/model-z" in err
    assert not out.exists()
    assert not out.parent.exists()


def test_missing_input_price_exits_2_and_writes_nothing(tmp_path, capsys):
    _assert_refused(*_run(tmp_path, {"output_cost_per_token": 4e-06, "litellm_provider": "vendor-z"}, capsys))


def test_missing_output_price_exits_2_and_writes_nothing(tmp_path, capsys):
    _assert_refused(*_run(tmp_path, {"input_cost_per_token": 2e-07, "litellm_provider": "vendor-z"}, capsys))


def test_null_input_price_is_a_missing_price(tmp_path, capsys):
    _assert_refused(*_run(tmp_path, {"input_cost_per_token": None, "output_cost_per_token": 4e-06}, capsys))


def test_null_output_price_is_a_missing_price(tmp_path, capsys):
    _assert_refused(*_run(tmp_path, {"input_cost_per_token": 2e-07, "output_cost_per_token": None}, capsys))


def test_fixture_price_less_model_is_refused(tmp_path, capsys):
    from meterline_gold import cli

    keys = tmp_path / "keys.txt"
    keys.write_text("vendor-q/model-q-one\nmodel-q-three\n", encoding="utf-8")
    out = tmp_path / "prices.yaml"
    args = [
        "import-litellm-prices", "--file", str(FIXTURE / "litellm" / "sample.json"),
        "--keys", str(keys), "--as-of", "2026-10-01", "--out", str(out),
    ]
    assert cli.main(args) == 2
    assert "model-q-three" in capsys.readouterr().err
    assert not out.exists()


def test_missing_cache_prices_still_import(tmp_path, capsys):
    code, out, err = _run(
        tmp_path, {"input_cost_per_token": 2e-07, "output_cost_per_token": 8e-07, "litellm_provider": "vendor-z"}, capsys
    )
    assert code == 0, err
    price = yaml.safe_load(out.read_text(encoding="utf-8"))["token_prices"][0]["per_million"]
    assert price["input_uncached"] == "0.200000"
    assert price["output"] == "0.800000"
    assert price["cache_read"] == "0.200000"
    assert price["cache_write_5m"] == "0.200000"
    assert price["cache_write_1h"] is None
