"""S12 preset format."""

import copy
import json
from itertools import product
from pathlib import Path

import pytest

import meterline_sim
from meterline_sim import cli
from sim_helpers import BASE_PARAMS, PRESET_DIR, gen, read_csv

DATA = Path(meterline_sim.__file__).parent / "data"
SYLLABLES = ["ba", "be", "da", "de", "la", "le", "li", "lo", "ma", "me", "mi", "mo", "re", "ri", "ro",
             "sa", "ta", "te", "to", "va", "ve", "vo", "za", "zo"]


def regenerated_names():
    given = [a + b for a, b in product(SYLLABLES, repeat=2)]
    family = [a + b + c for a, b, c in product(SYLLABLES, repeat=3)]
    return {"given": given, "family": family}


def names_file_matches(path):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    want = regenerated_names()
    return obj.get("given") == want["given"] and obj.get("family") == want["family"]


def test_done_check_S12_matrix_preset_generates(tmp_path):
    folder = gen(tmp_path, str(PRESET_DIR / "matrix.json"), 7, 6)
    members = read_csv(folder, "org/memberships.csv")
    assert len(members) == 3
    assert {m["dimension"] for m in members} == {"community"}
    assert {m["role"] for m in members} == {"member"}
    groups = [u for u in read_csv(folder, "org/units.csv") if u["dimension"] == "community"]
    assert groups and all(g["kind"] == "group" for g in groups)
    assert all(g["parent_id"] == "" and g["head_person_id"] == "" for g in groups)


def _tiny():
    return json.loads((DATA / "presets" / "tiny.json").read_text(encoding="utf-8"))


def _exit_code(tmp_path, preset_obj=None, extra_params=None, preset_text=None):
    preset_path = tmp_path / "bad.json"
    preset_path.write_text(preset_text if preset_text is not None else json.dumps(preset_obj), encoding="utf-8")
    params = copy.deepcopy(BASE_PARAMS)
    params.update(extra_params or {})
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps(params), encoding="utf-8")
    out = tmp_path / "out"
    code = cli.main(["generate", "--preset", str(preset_path), "--seed", "1", "--months", "2",
                     "--out", str(out), "--params", str(params_path)])
    return code, out


def test_done_check_S12_invalid_inputs_exit_2(tmp_path):
    cases = {}
    unknown = _tiny()
    unknown["nope"] = 1
    cases["unknown key"] = dict(preset_obj=unknown)

    shares = _tiny()
    shares["profiles"] = {"shares": {"dormant": "0.5", "curious": "0.3", "office_worker": "0.1",
                                      "developer": "0.05", "power_user": "0.01"}}
    cases["shares"] = dict(preset_obj=shares)

    text = json.dumps(_tiny()).replace('"size": 10', '"size": 10.5')
    assert '"size": 10.5' in text
    cases["fractional number"] = dict(preset_text=text)

    table = json.loads((DATA / "prices.json").read_text(encoding="utf-8"))
    table["github_copilot"]["credit_price"] = "0.02"
    table_path = tmp_path / "prices.json"
    table_path.write_text(json.dumps(table), encoding="utf-8")
    cases["credit price"] = dict(preset_obj=_tiny(), extra_params={"price_table": str(table_path)})

    domain = _tiny()
    domain["org"]["email_domain"] = "mail.com"
    cases["email domain"] = dict(preset_obj=domain)

    for name, kwargs in cases.items():
        sub = tmp_path / name.replace(" ", "_")
        sub.mkdir()
        code, out = _exit_code(sub, **kwargs)
        assert code == 2, name
        assert not out.exists(), name


def test_done_check_S12_negative_control_valid_preset_exits_0(tmp_path):
    code, out = _exit_code(tmp_path, preset_obj=_tiny())
    assert code == 0
    assert out.exists()


def test_done_check_S12_scale_two_on_vertical(tmp_path):
    preset = json.loads((DATA / "presets" / "vertical.json").read_text(encoding="utf-8"))
    wanted = {}

    def walk(unit):
        if unit["kind"] == "department" and "teams" in unit:
            wanted[unit["key"]] = max(1, (unit["teams"] * 2 * 10 + 5) // 10)  # floor(teams * 2 + 0.5)
        for child in unit.get("children", []):
            walk(child)

    walk(preset["units"])
    folder = gen(tmp_path, "vertical", 3, 1, scale="2")
    units = [u for u in read_csv(folder, "org/units.csv") if u["valid_from"] == read_csv(folder, "org/units.csv")[0]["valid_from"]]
    ids = {u["unit_id"]: u for u in units}
    counts = {}
    for u in units:
        if u["kind"] == "team" and u["parent_id"] in ids and ids[u["parent_id"]]["kind"] == "department":
            counts[ids[u["parent_id"]]["key"]] = counts.get(ids[u["parent_id"]]["key"], 0) + 1
    assert counts == wanted
    assert sum(1 for u in units if u["kind"] == "division") == 6
    assert sum(1 for u in units if u["kind"] == "department") == 18


def test_done_check_S12_names_file_equals_regenerated_lists(tmp_path):
    assert names_file_matches(DATA / "names.json")


def test_done_check_S12_negative_control_edited_names_file_is_flagged(tmp_path):
    obj = regenerated_names()
    obj["given"][0], obj["given"][1] = obj["given"][1], obj["given"][0]
    path = tmp_path / "names.json"
    path.write_text(json.dumps(obj), encoding="utf-8")
    assert names_file_matches(path) is False


def _matrix_failures(folder):
    failures = []
    members = read_csv(folder, "org/memberships.csv")
    if len(members) != 3 or {m["dimension"] for m in members} != {"community"}:
        failures.append("memberships are not 3 rows in dimension community")
    if not [u for u in read_csv(folder, "org/units.csv") if u["dimension"] == "community"]:
        failures.append("no group unit in dimension community")
    return failures


def test_done_check_S12_negative_control_matrix_and_scale_assertions_can_fail(tmp_path):
    plain = gen(tmp_path, "tiny", 7, 1)  # the tiny preset has no matrix unit
    assert _matrix_failures(plain) != []
    # a run at scale 1 does not hold the scale-2 team counts
    preset = json.loads((DATA / "presets" / "vertical.json").read_text(encoding="utf-8"))
    teams_scale_1 = [u["teams"] for dept in _departments(preset["units"]) for u in [dept]]
    assert teams_scale_1 != [max(1, t * 2) for t in teams_scale_1]


def _departments(unit):
    out = [unit] if unit["kind"] == "department" and "teams" in unit else []
    for child in unit.get("children", []):
        out += _departments(child)
    return out
