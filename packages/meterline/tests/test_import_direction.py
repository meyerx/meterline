"""F-13: the package architecture as a test. Imports point one way only:

- the core package ``meterline`` imports no other meterline package;
- a connector (``meterline_anthropic``, ``meterline_anthropic_admin``, ``meterline_litellm``, ``meterline_copilot``)
  imports the core only: never gold, the simulator or another connector;
- gold and the simulator import the core only (the simulator is the independent
  oracle for gold, and neither may lean on a connector's code).

The check reads every source file under ``packages/*/src`` with ``ast`` (nothing
is imported), including imports inside functions and ``importlib.import_module``
or ``__import__`` calls with a literal name.
"""

import ast
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]

CORE = "meterline"


def _package_rules():
    """Every folder under packages/ is a package; each may import the core only (a connector, gold and the simulator
    alike), and the core imports nothing. A new package needs no edit here."""
    names = sorted(p.parent.name for p in PACKAGES.glob("*/pyproject.toml"))
    return {name.replace("-", "_"): (set() if name == CORE else {CORE}) for name in names}


ALLOWED = _package_rules()
CONNECTORS = {name for name in ALLOWED if name not in (CORE, "meterline_gold", "meterline_sim")}


def _top_level(name):
    return (name or "").split(".")[0]


def _imported_names(tree):
    """Top-level module names a module imports, with line numbers."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, _top_level(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield node.lineno, _top_level(node.module)
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in ("import_module", "__import__") and isinstance(node.args[0].value, str):
                yield node.lineno, _top_level(node.args[0].value)


def import_direction_problems(packages_dir):
    """One line per import that points the wrong way, for every module of
    ``packages_dir/*/src/<package>/``."""
    problems = []
    for src in sorted(Path(packages_dir).glob("*/src")):
        for package_dir in sorted(p for p in src.iterdir() if p.is_dir() and p.name in ALLOWED):
            own = package_dir.name
            for path in sorted(package_dir.rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for line, name in _imported_names(tree):
                    if name in ALLOWED and name != own and name not in ALLOWED[own]:
                        problems.append("%s:%d: %s imports %s" % (path.relative_to(packages_dir), line, own, name))
    return sorted(problems)


def test_import_direction_holds_for_every_package():
    found = sorted(p.parent.name for p in PACKAGES.glob("*/src/*/__init__.py"))
    assert set(found) == set(ALLOWED), "a package was added or renamed: give it a rule in ALLOWED"
    assert import_direction_problems(PACKAGES) == []


def _plant(root, package, module, source):
    path = root / package / "src" / module / "planted.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "__init__.py").write_text("", encoding="utf-8")
    path.write_text(source, encoding="utf-8")


def test_import_direction_flags_planted_imports(tmp_path):
    # negative control: each planted wrong-way import is reported, an allowed one is not
    _plant(tmp_path, "meterline", "meterline", "import meterline_anthropic.client\n")
    _plant(tmp_path, "meterline-litellm", "meterline_litellm", "from meterline_gold import build\nfrom meterline import http\n")
    _plant(tmp_path, "meterline-copilot", "meterline_copilot",
           "def f():\n    import importlib\n    return importlib.import_module('meterline_litellm.fetch')\n")
    _plant(tmp_path, "meterline-anthropic", "meterline_anthropic", "x = __import__('meterline_sim')\n")
    _plant(tmp_path, "meterline-gold", "meterline_gold", "from meterline_copilot import client\n")
    _plant(tmp_path, "meterline-sim", "meterline_sim", "import meterline_gold\nfrom . import names\n")
    assert import_direction_problems(tmp_path) == [
        "meterline-anthropic/src/meterline_anthropic/planted.py:1: meterline_anthropic imports meterline_sim",
        "meterline-copilot/src/meterline_copilot/planted.py:3: meterline_copilot imports meterline_litellm",
        "meterline-gold/src/meterline_gold/planted.py:1: meterline_gold imports meterline_copilot",
        "meterline-litellm/src/meterline_litellm/planted.py:1: meterline_litellm imports meterline_gold",
        "meterline-sim/src/meterline_sim/planted.py:1: meterline_sim imports meterline_gold",
        "meterline/src/meterline/planted.py:1: meterline imports meterline_anthropic",
    ]
