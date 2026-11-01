"""The state file a connector keeps under its data directory: ``state.json``.

Reading, writing and the message for a damaged file are the same for every connector, so they live here. What the
file holds is not: each connector keeps its own ``_empty_state`` and ``_check_state`` and passes the check to
``read_state``. ``is_day_list`` and ``is_relative_path`` are the two checks those shapes share.
"""

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, Optional

STATE_NAME = "state.json"


class StateError(Exception):
    """The state file exists but does not hold what this program writes."""


def is_day_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, str):
            return False
        try:
            date.fromisoformat(item)
        except ValueError:
            return False
    return True


def is_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parts = value.replace("\\", "/").split("/")
    return not value.startswith(("/", "\\")) and ".." not in parts and ":" not in parts[0]


def read_state(data_dir: Path, check: Callable[[Any], None]) -> Optional[Dict[str, Any]]:
    """The state file as a dict, None when there is none. Raises StateError
    when it is there but damaged: not readable JSON, or ``check`` raises it."""
    path = data_dir / STATE_NAME
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):  # UnicodeDecodeError is a ValueError
        raise StateError("it is not readable JSON (cut short, empty or overwritten?)") from None
    check(state)
    return state


def write_state(data_dir: Path, state: Dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / STATE_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def state_damaged_message(data_dir: Path, exc: StateError) -> str:
    """What a connector prints (to stderr) when ``read_state`` raised."""
    return (
        "state file %s is damaged: %s. Nothing was fetched or changed. Recovery: restore the file from a backup; "
        "or, to start again, move it aside and run `backfill --since YYYY-MM-DD` (or `daily --since YYYY-MM-DD`) "
        "from the first day to load. Records already published are kept, and unchanged rows are not written twice."
        % (data_dir / STATE_NAME, exc)
    )


__all__ = [
    "STATE_NAME",
    "StateError",
    "is_day_list",
    "is_relative_path",
    "read_state",
    "state_damaged_message",
    "write_state",
]
