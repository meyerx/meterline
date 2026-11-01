"""Review finding F-28: spec v2 section 7 lists ``tests/fixtures/`` for this
connector. The folder holds a synthetic ``/user/list`` answer whose lists and
maps are filled, as a proxy with configured users sends them; the fake's own
answers leave them empty. These tests read it, so the folder is checked rather
than decorative, and the L01 helper meets a filled answer."""

import copy
import json
from pathlib import Path

from test_done_check_L01_shapes import differences

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SHAPES = Path(__file__).resolve().parents[3] / "docs" / "api-shapes" / "litellm"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_filled_user_list_matches_the_documented_shape():
    body = _json(FIXTURES / "user_list_filled.json")
    assert differences(body, "user_list.json") == []
    # the fixture does fill what the shape file leaves empty
    first = body["users"][0]
    assert first["models"] and first["policies"] and first["allowed_cache_controls"] and first["metadata"]


def test_filled_user_list_still_shows_a_wrong_shape():
    body = copy.deepcopy(_json(FIXTURES / "user_list_filled.json"))
    del body["users"][0]["user_email"]
    body["users"][1]["unknown_field"] = 1
    body["users"][1]["models"] = "model-a"
    assert differences(body, "user_list.json") == [
        "$.users[0].user_email: missing",
        "$.users[1].models: expected array, got scalar",
        "$.users[1].unknown_field: not in the documented shape",
    ]


def test_filled_user_list_is_synthetic():
    body = _json(FIXTURES / "user_list_filled.json")
    assert all(u["user_email"].endswith("@example.com") for u in body["users"])
