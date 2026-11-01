# Fixtures

Everything here is **synthetic**. Ids, logins and counts are invented. Never put a report from a real organization or enterprise in this folder; done-check C12 scans it for tokens and for emails outside `@example.com`.

Most test data for this connector is built in code by the fake API (`meterline_copilot.testing`), in the documented shapes of `docs/api-shapes/copilot/`. This folder holds a whole report file as the download host serves it.

| File | What it is |
|---|---|
| `users_1_day_report.ndjson` | A file behind a `users-1-day` download link (endpoint A) for 2026-09-18, organization level, three people. Each line starts from the documented row of `docs/api-shapes/copilot/users_1_day_row.ndjson`: `login-a` carries every documented field, `login-b` leaves out every field the C01 check counts as optional, `login-c` has CLI use, no app use and zero credits. `tests/test_review_f28_fixtures_copilot.py` compares each line with the documented row and runs the connector over the file. |

Edit the file by hand; keep its keys those of the documented row.
