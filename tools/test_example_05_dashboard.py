"""R-5 of the integrate part 1 spec: example 05 serves one audience folder of published/ and refuses everything else.

For each audience and each path of the audience-by-folder table of the gold README ("Who gets which folder"), the example's
``resolve`` either returns the file or refuses it. The expected answer is written here from that table, not read from
``servable()``. Two negative controls plant a defect and the same check must catch it: ``servable()`` stubbed to return True,
and the example's own path resolution removed (a symlink into another manager's folder and a ``..`` path then get served).

Run: python3 -m pytest -q tools/test_example_05_dashboard.py
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

meterline_gold = pytest.importorskip("meterline_gold")
meterline_sim = pytest.importorskip("meterline_sim")

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "05-dashboard" / "dashboard.py"
KEY = "a-key-of-at-least-32-bytes-that-you-keep-out-of-git"


def load():
    """A fresh copy of the example module, so a test can stub it without touching another test's copy."""
    spec = importlib.util.spec_from_file_location("example_05_dashboard_%d" % id(object()), EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gold_root(tmp_path_factory):
    """A gold root whose ``published`` is a link to a built folder, as the job's publish step leaves it."""
    run = meterline_sim.generate(preset="tiny", seed=7, months=6, out=tmp_path_factory.mktemp("d_sim"))
    base = Path(meterline_gold.__file__).resolve().parent / "examples"
    root = tmp_path_factory.mktemp("d_gold")
    meterline_gold.build(silver=run, org=Path(run) / "org", policy=base / "policy.example.yaml",
                         prices=base / "prices.example.yaml", key=KEY.encode(), out=root / "published-1")
    (root / "published").symlink_to("published-1")
    return root


def keys(root, audience):
    return sorted(p.name for p in (root / "published" / audience).iterdir())


def a_csv(root, folder):
    return sorted(p.name for p in (root / "published" / folder).glob("*.csv"))[0]


def table_of_the_gold_readme(root):
    """(audience, path, may_be_served) for every cell of the README table "Who gets which folder", plus ``delivery/``, ``../``, an
    absolute path and a path that spells the other audiences' folders."""
    m1, m2 = keys(root, "manager")[:2]
    p1, p2 = keys(root, "person")[:2]
    files = {"central": "central/" + a_csv(root, "central"), "exec": "exec/" + a_csv(root, "exec"),
             "m1": "manager/%s/%s" % (m1, a_csv(root, "manager/" + m1)), "m2": "manager/%s/%s" % (m2, a_csv(root, "manager/" + m2)),
             "p1": "person/%s/%s" % (p1, a_csv(root, "person/" + p1)), "p2": "person/%s/%s" % (p2, a_csv(root, "person/" + p2))}
    everything = list(files.values()) + ["manifest.json", "delivery/directory.csv", "../outside.csv", "manager/%s/../%s/x.csv" % (m1, m2),
                                        "/etc/passwd", files["exec"] + "/../../central/x.csv"]
    allowed = {"central": {files["central"], "manifest.json"}, "exec": {files["exec"]},
               "manager:" + m1: {files["m1"]}, "person:" + p1: {files["p1"]}}
    return [(audience, path, path in may) for audience, may in allowed.items() for path in everything]


def wrong_answers(module, root):
    """What the example gets wrong against the table: a path it served that it must refuse, or the reverse."""
    wrong = []
    for audience, path, may in table_of_the_gold_readme(root):
        try:
            served = module.resolve(root / "published", audience, path) is not None
        except (PermissionError, FileNotFoundError):
            served = False
        if served != may:
            wrong.append("%s %s: %s" % (audience, path, "served" if served else "refused"))
    return wrong


def plant_symlink(root, audience_folder, other_folder):
    """A link inside one folder that points at a file of another, the way a careless copy or a hostile upload would."""
    target = root / "published" / other_folder / a_csv(root, other_folder)
    link = root / "published" / audience_folder / "linked.csv"
    link.unlink(missing_ok=True)
    link.symlink_to(os.path.relpath(target, link.parent))
    return link


def test_each_audience_is_served_its_own_folder_and_nothing_else(gold_root):
    assert wrong_answers(load(), gold_root) == []


def test_a_symlink_into_another_managers_folder_is_refused(gold_root):
    m1, m2 = keys(gold_root, "manager")[:2]
    link = plant_symlink(gold_root, "manager/" + m1, "manager/" + m2)
    try:
        with pytest.raises(PermissionError):
            load().resolve(gold_root / "published", "manager:" + m1, "manager/%s/linked.csv" % m1)
    finally:
        link.unlink()


def test_negative_control_servable_stubbed_to_true_is_caught(gold_root):
    module = load()
    module.servable = lambda path, audience: True
    assert wrong_answers(module, gold_root) != []


def test_negative_control_path_resolution_removed_is_caught(gold_root):
    m1, m2 = keys(gold_root, "manager")[:2]
    link = plant_symlink(gold_root, "manager/" + m1, "manager/" + m2)
    module = load()
    module.inside = lambda path, base: True   # the example's containment check, removed
    try:
        served = module.resolve(gold_root / "published", "manager:" + m1, "manager/%s/linked.csv" % m1)
        assert served.resolve().parent.name == m2, "the planted symlink must reach the other manager's file once the check is gone"
    finally:
        link.unlink()


def test_the_page_is_rendered_from_the_audience_folder_only(gold_root):
    m1 = keys(gold_root, "manager")[0]
    page = load().render(gold_root / "published", "manager:" + m1)
    assert "groups_month.csv" in page and "<table>" in page
    assert "central/" not in page and "delivery" not in page and "manifest" not in page


def test_check_renders_once_and_exits_0(gold_root, capsys):
    m1 = keys(gold_root, "manager")[0]
    assert load().main([str(gold_root), "--audience", "manager:" + m1, "--check"]) == 0
    assert "ok" in capsys.readouterr().out


def test_exit_2_when_published_is_absent(tmp_path, capsys):
    assert load().main([str(tmp_path), "--audience", "exec", "--check"]) == 2
    assert "published" in capsys.readouterr().err


def test_exit_2_for_a_manager_without_a_key_or_an_unknown_audience(gold_root, capsys):
    assert load().main([str(gold_root), "--audience", "manager", "--check"]) == 2
    assert load().main([str(gold_root), "--audience", "nobody", "--check"]) == 2


def test_the_server_binds_to_loopback_by_default():
    assert load().DEFAULT_HOST == "127.0.0.1"


def test_the_example_stays_small_and_uses_the_standard_library_and_gold_only():
    text = EXAMPLE.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 200
    imports = {line.split()[1].split(".")[0] for line in text.splitlines() if line.startswith(("import ", "from "))}
    assert imports <= set(sys.stdlib_module_names) | {"meterline_gold"}
    assert "meterline_gold" in imports
