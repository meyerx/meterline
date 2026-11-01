"""Money is Decimal, never float (decision 0001): an AST scan of the source of meterline-copilot.

The scan walks every module of the package for the name ``float`` (a call, an annotation, ``isinstance``) and for a
float literal. A planted ``float(...)`` in a scratch module shows the scan can fail.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "meterline_copilot"


def float_offenders(root):
    offenders = []
    for path in sorted(Path(root).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "float":
                offenders.append((path.name, node.lineno, "name float"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, float):
                offenders.append((path.name, node.lineno, "float literal"))
    return offenders


def test_no_float_on_cost_path_copilot():
    assert sorted(SRC.glob("*.py")), "the scan found no source of the package"
    assert float_offenders(SRC) == []


def test_the_float_scan_copilot_flags_a_planted_float(tmp_path):
    (tmp_path / "planted.py").write_text("cost = float(1)\nrate = 0.5\n", encoding="utf-8")
    assert sorted(float_offenders(tmp_path)) == [("planted.py", 1, "name float"), ("planted.py", 2, "float literal")]
