"""Public interface of meterline-copilot (review finding F-38): one source for the version, declared public names.

The version lives in pyproject.toml only; ``__version__`` is read from the installed package metadata.
"""

import importlib
import importlib.metadata
import re
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
DIST = "meterline-copilot"
MODULE = "meterline_copilot"


def _pyproject_version():
    text = (PACKAGE_DIR / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"$', text, flags=re.M).group(1)


def _readme_section(heading):
    text = (PACKAGE_DIR / "README.md").read_text(encoding="utf-8")
    start = text.index("\n" + heading + "\n")
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else len(text)]


def test_version_matches_metadata():
    module = importlib.import_module(MODULE)
    assert module.__version__ == importlib.metadata.version(DIST) == _pyproject_version()


def test_version_is_not_a_literal_in_the_code():
    source = (PACKAGE_DIR / "src" / MODULE / "__init__.py").read_text(encoding="utf-8")
    assert not re.search(r"__version__\s*=\s*['\"]\d+\.", source)


def test_public_names_are_declared_and_resolve():
    module = importlib.import_module(MODULE)
    assert "__version__" in module.__all__
    missing = [name for name in module.__all__ if not hasattr(module, name)]
    assert missing == []


def test_readme_names_the_public_interface_and_what_is_internal():
    section = _readme_section("## Public interface")
    assert "`meterline_copilot.__all__`" in section
    assert "internal" in section
