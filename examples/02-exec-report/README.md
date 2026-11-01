# 02 - a static exec report

**Goal.** Write one HTML page for an executive reader from the `exec/` folder of a gold build, with the standard library only, so the pattern can be copied for any audience view.

**Reads.** `exec/costs_month.csv` and `exec/seats_month.csv`, each opened only after `meterline_gold.servable(path, "exec")` returned true. It reads nothing from `central/`, `delivery/` or `manifest.json`, and the page links to none of them. An empty cell on a row whose `hidden` column is `true` is written as "withheld", never as 0. On a row whose `hidden` is `false` it is written as "none": the row has no such value, for example a provider with no seats has no seat cost. A row can be `hidden` for one column and still have an empty cell in a column the rule does not cover, so "withheld" can mean a missing value on such a row. A gold folder without the `hidden` column (schema version 1.0) cannot tell the two apart and shows "withheld".

## Run it

Run this from the repository root, after the install in the README quick start.

```sh
.venv/bin/meterline-sim generate --preset tiny --seed 7 --out sim-out
export METERLINE_GOLD_KEY="a-key-of-at-least-32-bytes-that-you-keep-out-of-git"
.venv/bin/meterline-gold build --silver sim-out/tiny-s7-m6 --org-files sim-out/tiny-s7-m6/org --policy packages/meterline-gold/src/meterline_gold/examples/policy.example.yaml --prices packages/meterline-gold/src/meterline_gold/examples/prices.example.yaml --out gold
.venv/bin/python examples/02-exec-report/report.py gold --out exec-report.html
```

**Expected output.** One line, `wrote exec-report.html`, and a page with two tables. Rows of a provider with few users show "withheld" in the cost cells the rule covers; a cost cell that is empty because the provider has none shows "none" on a visible row.
