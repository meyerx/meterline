"""Tests for tools/readme_block.py. Run: python3 -m pytest -q tools"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import readme_block as rb  # noqa: E402

DOC = """# t

## Quick start

Text.

```sh
echo one
echo two
```

```sh
echo not-this
```

### Other thing

```sh
echo other
```
"""


def test_first_sh_block_under_a_heading():
    assert rb.block_after(DOC, "Quick start") == "echo one\necho two\n"
    assert rb.block_after(DOC, "Other thing") == "echo other\n"


def test_a_missing_heading_or_block_is_an_error():
    for heading in ("Nope",):
        try:
            rb.block_after(DOC, heading)
        except SystemExit as e:
            assert "Nope" in str(e)
        else:
            raise AssertionError("expected SystemExit")
