# Contributing

Thanks for looking. meterline is a small project; changes that keep it small are the easiest to accept.

## Set up

Follow the install and test steps in the README: [Quick start](README.md#quick-start) installs every package in editable mode, and [Development](README.md#development) says how to run the tests. You need Python 3.10 or later.

## Making a change

1. Branch from `main` and keep the change to one subject.
2. Add or change a test first, and see it fail for the reason you expect, then make it pass.
3. Run the tests of every package you touched (`.venv/bin/python -m pytest -q packages/<package>`). The full suite takes a while; the pull request runs it too, and it must pass.
4. Open a pull request. Describe what changed and why, and name the test that proves it.

Accepted spec files in `docs/` are frozen. A finding about a spec goes into the matching change-list file, for the next version of the spec.

## Data

Fixtures, examples and test data are **synthetic only**: invented people, `@example.com` addresses, invented amounts. Never add an export from a real account, and never commit a key or token (they are read from the environment, and `.env` is ignored by git).

## Money

Amounts are `Decimal` end to end, never `float`.

## Licence

meterline is licensed under the Apache License 2.0 (see [LICENSE](LICENSE)). By submitting a contribution you agree that it is licensed under the same terms.

## Security

Do not report a vulnerability in a public issue or pull request; see [SECURITY.md](SECURITY.md).
