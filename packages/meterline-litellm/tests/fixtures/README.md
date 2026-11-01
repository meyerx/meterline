# Fixtures

Everything here is **synthetic**. Ids, aliases, emails (only `@example.com`), models and amounts are invented. Never put an answer from a real proxy in this folder.

Most test data for this connector is built in code by the fake proxy (`meterline_litellm.testing`), in the documented shapes of `docs/api-shapes/litellm/`. This folder holds what the fake does not produce.

| File | What it is |
|---|---|
| `user_list_filled.json` | A `GET /user/list` answer (endpoint C) with two users whose lists and maps are filled (`models`, `policies`, `allowed_cache_controls`, `metadata`, `model_spend`, `model_max_budget`), as a proxy with configured users sends them. The shape file and the fake leave them empty. `tests/test_review_f28_fixtures_litellm.py` compares it with the shape file under the rules of done-check L01: an empty list or map in the shape file is not compared, so a filled one in an answer is not a difference. |

Edit the file by hand; keep its keys those of `docs/api-shapes/litellm/user_list.json`.
