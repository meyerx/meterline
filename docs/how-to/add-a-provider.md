# How to add a provider

A provider is a new connector package under `packages/`, next to the existing connectors. It reads one provider's usage API and writes the five record types of the [record reference](../records.md). It adds no record type and changes none. This guide gives the steps in the order that keeps each one testable. The rules a connector must keep are in the [connector contract](../reference/connector-contract.md); this page says how to build to it.

Read [architecture.md](../architecture.md) first for how the layers fit. You do not need another connector's code: the template, the shared modules below and the contract give everything a connector needs.

## What you get from the template

Make the skeleton with one command, from the repository root. The first line, `python3 -m venv .venv`, makes the virtual environment; skip it if you already have one. Every command in this guide runs with the python of that environment, `.venv/bin/python`, because the check in step 9 imports your new package and a system `python3` cannot find it. The core package comes first, with its `dev` extra: the wiring list that the command prints reads `pyproject.toml` files, which needs `tomli` on Python 3.10.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e "packages/meterline[conformance,dev]"
.venv/bin/python tools/new_connector.py acme
```

It writes `packages/meterline-acme/`: a `pyproject.toml`, a README with every required section, and the modules `cli.py`, `client.py`, `fetch.py`, `normalise.py` and `testing.py`. It also writes a test file that runs the conformance kit. The stub talks to an invented endpoint and passes the whole kit against its own fake API. Its fetcher already does what the contract asks. It keeps one raw file per query in the contract's format and follows a page cursor. It asks for one day per query, calls a day final 4 days after it ended and re-reads the last 5 closed days. You rewrite the endpoint paths and parameters, the cursor and page-size names of the provider, the fake's rows and the mapping in `normalise.py`. You change the three numbers (`FINAL_AFTER_DAYS`, `REREAD_DAYS` and `WINDOW_DAYS`) only from the provider's documentation, and keep the kit green.

The template is a command and not a folder of stubs because a command can be tested: `tools/test_new_connector.py` makes a package, runs the kit against it and checks its files. With `--two-endpoints` the stub has a second endpoint that feeds a second record kind, for a provider with more than one report.

## What the core package gives you

A connector imports these modules from `packages/meterline` and does not write its own copy of them:

- `meterline.http`: the throttle, the back-off, the `Retry-After` cap, the masking of the key in a message, and `FakeClock` for tests.
- `meterline.run`: the exit codes and `RunResult`, which the connector's `run()` returns; `note_failure` for an API failure; `WritePhase`, so the writes of a run all land or none do; and the helpers that report fields the provider sent that the connector does not know.
- `meterline.state`: reading and writing `state.json`, and the message for a damaged one. The connector keeps only the shape of its own state.
- `meterline.published`: the revision rule, `drop_unchanged`, and `load_published`, which reads what is already published.
- `meterline.records` and `meterline.writers`: the record types, `batch_failures`, `write_jsonl` and `write_parquet`.
- `meterline.conformance`: the kit, and `compare_shape` for the fake's shape check.

The Python entry point is `run(command, *, data_dir, credentials, transport, ...)`, which returns a `RunResult` and never raises for a failed run. Its arguments are in [the `run()` signature](../reference/connector-contract.md#the-run-signature-and-the-credentials-rule); the stub already has it, and the command line calls it.

Before you start, read the rules a new provider most often needs to decide: [the account label when a response has none](../reference/connector-contract.md#the-account-label-when-a-response-has-none), [finality and the re-read days](../reference/connector-contract.md#windows), [raw file naming](../reference/connector-contract.md#raw-page-naming-for-a-paged-endpoint) and [shape files](../reference/connector-contract.md#shape-files).

## Steps

1. **Map the provider to the record format on paper first.** For each endpoint, decide which record kind it feeds: `UsageRecord`, `ActivityRecord`, `SeatSnapshot`, `SeatAssignment` or `OrgTotal`. Decide the `actor_kind` of each row, the `grain`, the `cost_basis` (billed, consumption or estimate) and which tokens go into which of the four disjoint buckets. A measure the provider does not report is `null`, never `0`. Write the mapping as a table; it becomes the README's Record mapping section and a section of `docs/records.md`. If a fact fits no field, use `extras` or `counters` before you consider a new field, because a new field is a change to the format for every reader (see [Compatibility](../records.md#compatibility)). Mappings for five other tools already exist; see [where other mappings live](../reference/connector-contract.md#where-other-providers-mappings-live).
2. **Transcribe the response shapes.** Put one JSON file per endpoint under `docs/api-shapes/<provider>/`, with the keys and nesting of the provider's documented example response and synthetic values only. Use ids such as `user-a` and addresses at `example.com`. Add a `README.md` there that names the source page or source file of each shape, the date you read it and a [V] or [I] mark, says that every value is invented, and has a line starting `Provenance:`. Never call a shape current: shapes go stale, so record when you read them. Never commit a real response. The format is in [shape files](../reference/connector-contract.md#shape-files).
3. **Create the package.** Work in the repository's virtual environment; make it with `python3 -m venv .venv` if you have none. Upgrade pip with `.venv/bin/python -m pip install --upgrade pip`, because an older pip, such as the one bundled with early Python 3.10 releases, fails an editable install with "editable mode currently requires a setuptools-based build". Install `packages/meterline` in editable mode with its `conformance` and `dev` extras first, `.venv/bin/python -m pip install -e "packages/meterline[conformance,dev]"`; the `dev` extra brings `tomli`, which the next command needs on Python 3.10. Run `.venv/bin/python tools/new_connector.py NAME` as above. Then install the new package, `.venv/bin/python -m pip install -e "packages/meterline-<provider>[dev]"`. The package depends on `meterline` and `httpx`. The layout of the data directory (`raw/`, `runs/`, `records/`, `state.json`), the commands (`backfill`, `daily`, `rebuild`) and the exit codes (0, 1, 2, 3) come from the contract and are already in the stub.
4. **Write the fake API before the connector.** `<package>/testing.py` holds a public `FakeAPI`, so an integrator can test without a key. It serves the shape files' structure with synthetic data through `httpx.MockTransport`. It answers page by page as the provider pages, and caps the page size. It scripts faults per request with a `fail` method (429 with `retry-after`, 5xx, 4xx, an expired cursor), logs every request and takes an injected clock. It can also answer one row, page or day twice, which the `duplicate` hook of the kit's `Connector` uses for [R16](../reference/connector-contract.md#r16). [R14](../reference/connector-contract.md#r14) lists what it must offer. Write the shape check next: a test that compares every answer of the fake with the shape files in `docs/api-shapes/<provider>/` with `meterline.conformance.compare_shape`, by keys and nesting, so the fake cannot drift from what you transcribed.
5. **Write the client and the fetcher.** The client only talks to the API, through `meterline.http` for the throttle, the back-off, the `Retry-After` cap and the masking. The fetcher writes every response, as received, to a new raw file; it never overwrites one. Read every JSON answer and raw file with `parse_float=Decimal` ([R15](../reference/connector-contract.md#r15)). The key is read from one environment variable and never written to a file, a log or a manifest. Name that variable in `KEY_ENV = "ACME_API_KEY"` in `cli.py`, because the [wiring check](../reference/connector-contract.md#the-wiring-checklist) reads it from there: it must be the variable the connector really reads, such as an admin key when the provider's usage endpoints need one. Throttle below the documented rate limit and cap every wait. Use the injected `clock`, `sleep` and `wall`, so no test waits on real time.
6. **Write the normaliser.** It only reads raw files and emits records. Build them with `meterline.records` and check them with `meterline.batch_failures` before anything is written with `meterline.write_jsonl`, inside a `meterline.run.WritePhase`. The state file goes through `meterline.state`, and the revision rule through `meterline.published`. A missing required field stops the run with exit 2; it is never read as zero. Apply the revision rule: publish only records whose content differs from the latest published one. Because the normaliser reads raw files only, `rebuild` can re-create every record without the API.
7. **Run the kit and show each new check failing first.** Run the package's tests: they are the conformance kit, which checks [R01 to R16](../reference/connector-contract.md#the-rules). Then add your own acceptance tests, named `test_done_check_<letter><NN>_*` with a letter of your own, so `-k done_check` selects them. Cover the shape check, a missing required field failing the run, and money staying `Decimal`. Cover also what the kit does not check, as far as your connector has it: paging, the window of a query, finality, the re-read depth and the summing of rows that share a record key (see [what is not checked by a test](../reference/connector-contract.md#what-is-not-checked-by-a-test)). Run each new check against a broken connector first, and keep the failing output for the pull request.
8. **Document it.** The package `README.md` has a `## Known limits` section and follows the outline in the contract's [README rules](../reference/connector-contract.md#the-readme-rules). Its Status line uses one of the [evidence levels](../glossary.md#evidence-levels): a new connector is "built against documented shapes" until it has run against a live account. Add a "`<Provider>` sources" section to `docs/records.md`. Keep every sentence of the `docs/records.md` section to 45 words, because a test fails on a longer one.
9. **Wire it in.** Run `.venv/bin/python tools/check_new_connector.py NAME` until it prints nothing missing, then `pytest tools`. The first item for a new package is usually to track its files with `git add packages/meterline-<provider>`, because the tools tests read tracked files only. Use the python of the virtual environment, or the check reports "cannot be imported". Install every package first, because `pytest tools` imports them all; the command is under [Before the pull request](#before-the-pull-request). The command lists each place the package must be named, with the exact line to add; the [wiring checklist](../reference/connector-contract.md#the-wiring-checklist) names the places too, and the command is the authority.

Every fixture and example is synthetic, and the tests never touch the network: the test suite's network guard fails any test that tries.

## Before the pull request

Run the package's tests and the repository's `tools` tests. The package's own tests are quick: `.venv/bin/python -m pytest -q packages/meterline-<provider> -p no:cacheprovider`.

`pytest tools` imports every package of the repository, so every package must be installed first. Without that it fails with errors such as `No module named meterline_anthropic`. This one command installs every package, the new one included, in editable mode with its `dev` extra:

```sh
.venv/bin/python -m pip install $(for p in packages/*/pyproject.toml; do printf -- '-e %s[dev] ' "${p%/pyproject.toml}"; done)
```

Then run the `tools` tests:

```sh
.venv/bin/python -m pytest -q -n 4 tools -p no:cacheprovider
```

This takes about 10 minutes, so it is not a quick check. A growing line of dots is healthy. The kit, the shape check, the failing-first output of every new check, the README with its Known limits and the records.md section are what a reviewer looks for. The connector stays "built against documented shapes" until someone runs it against a live account and the shape files are updated from what the provider really sends (synthetic values only).
