"""The large synthetic fixture dataset of the test suite (the small sample ships in ``meterline_anthropic.testing``)."""

import gzip
import json
from pathlib import Path
from typing import Any, Dict

DATA_DIR = Path(__file__).resolve().parent / "fixtures" / "data"


def load_dataset() -> Dict[str, Any]:
    with gzip.open(DATA_DIR / "dataset.json.gz", "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))
