"""F-15: which side owns which arithmetic is stated, and the gold README says what to read before changing gold."""

from gold_helpers import REPO

SIM_README = REPO / "packages" / "meterline-sim" / "README.md"
GOLD_README = REPO / "packages" / "meterline-gold" / "README.md"


def test_independent_oracle_is_stated_in_the_sim_readme():
    text = SIM_README.read_text(encoding="utf-8")
    assert "independent oracle" in text.lower()
    section = text.lower().split("independent oracle", 1)[1]
    # it says what is recomputed on purpose, and that a disagreement is not settled by sharing code
    assert "pricing" in text.lower() and "pool" in text.lower()
    assert "never import" in section or "do not import" in section or "not share" in section


def test_sim_readme_no_longer_says_it_computes_no_gold_figures_without_the_oracle_qualification():
    text = SIM_README.read_text(encoding="utf-8")
    assert "It computes no gold figures." not in text


def test_gold_readme_has_a_read_first_order_that_names_its_sources():
    text = GOLD_README.read_text(encoding="utf-8")
    assert "## Read this before changing gold" in text
    section = text.split("## Read this before changing gold", 1)[1].split("\n## ", 1)[0]
    order = [section.index(word) for word in ("README", "SPEC-v1.2", "change-list-meterline-gold", "gold-fixture",
                                               "sql/", "test_done_check")]
    assert order == sorted(order), "the list is not in reading order"
    assert "independent oracle" in section.lower() and "meterline-sim" in section


def test_gold_readme_links_resolve():
    import re

    text = GOLD_README.read_text(encoding="utf-8")
    for target in re.findall(r"\]\((\.\./[^)#]+)", text):
        assert (GOLD_README.parent / target).exists(), target
