# Tutorial: from a simulated organisation to a dashboard

This tutorial takes you from a fresh checkout to the example dashboard in about fifteen minutes, without a
provider account or an API key. You will:

1. install the packages;
2. invent an organisation and its AI usage with the simulator, which writes the same records a connector writes;
3. build the gold datasets from those records;
4. open one audience's datasets in the example dashboard.

Every command is shown with what it prints. Run them from the repository root, in this order. The words in
*italics* are defined in the [glossary](glossary.md); [architecture.md](architecture.md) shows how the steps fit
together.

## 1. Install

You need Python 3.10 or later and `git`. Create a virtual environment in the checkout and install the core
package, the simulator and gold:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e packages/meterline -e "packages/meterline-sim[dev]" -e "packages/meterline-gold[dev]"
```

The install takes a minute or two. The last line names what was installed, among them:

```text
Successfully installed ... meterline-0.3.0 meterline-gold-0.1.0 meterline-sim-0.1.0 ...
```

The list of dependencies around them depends on what your environment already had.

## 2. Invent an organisation and its usage

The simulator invents a small organisation of 20 people in two teams (the `tiny` preset) and six months of
their AI usage on three providers. It writes the *silver* records that the connectors of those providers would have written,
the organisation files, and a ground truth that says what the right answers are.

```sh
.venv/bin/meterline-sim generate --preset tiny --seed 7 --out sim-out
```

It prints the folder it wrote and the number of records of each kind:

```text
sim-out/tiny-s7-m6
activity: 5578
org_totals: 923
seat_assignments: 1647
seats: 366
usage: 3686
```

The same seed always gives the same files, byte for byte. Look at what it wrote:

```sh
ls sim-out/tiny-s7-m6 sim-out/tiny-s7-m6/records
```

```text
sim-out/tiny-s7-m6:
ground_truth.json
manifest.json
org
records

sim-out/tiny-s7-m6/records:
activity
org_totals
seat_assignments
seats
usage
```

`records/<kind>/<batch_id>.jsonl` holds one JSON line per record. A usage record says how much one *actor* used
one product and model on one day (the *grain*), with its tokens in four separate buckets and its cost. The
[record reference](records.md) explains every field. `org/` holds the organisation files: units, people,
memberships, identities and a directory of names.

## 3. Build the gold datasets

Gold turns silver, the organisation files, a price list and a policy file into datasets for four audiences
(central, exec, each manager, each person). It replaces each person by a keyed hash, so it needs a secret key
of at least 32 bytes in the environment. The example policy and price list ship with the package; every value
in them is invented.

```sh
export METERLINE_GOLD_KEY="a-key-of-at-least-32-bytes-that-you-keep-out-of-git"
.venv/bin/meterline-gold build --silver sim-out/tiny-s7-m6 --org-files sim-out/tiny-s7-m6/org --policy packages/meterline-gold/src/meterline_gold/examples/policy.example.yaml --prices packages/meterline-gold/src/meterline_gold/examples/prices.example.yaml --out gold
```

It prints the folder, the months it covers and one line per dataset of `central/`, with its row count:

```text
meterline-gold: warning: scenario unit_caps_plus_flat: unit_envelopes keys match no line unit: central-it, commercial, finance-legal, operations, people, product
gold folder: gold
months: 2026-04, 2026-05, 2026-06, 2026-07, 2026-08, 2026-09
central/alert_events.csv: 148 rows
central/comparison_groups.csv: 18 rows
central/costs_month.csv: 18 rows
central/data_quality.csv: 276 rows
central/groups_month.csv: 27 rows
central/identity_map.csv: 30 rows
central/named_exceptions.csv: 16 rows
central/people.csv: 31 rows
central/person_day.csv: 3564 rows
central/person_day_activity.csv: 5578 rows
central/person_month.csv: 119 rows
central/pools_month.csv: 6 rows
central/reconciliation_month.csv: 18 rows
central/scenario_persons.csv: 476 rows
central/scenario_summary.csv: 24 rows
central/scenario_units.csv: 0 rows
central/seats_month.csv: 6 rows
central/units.csv: 5 rows
```

The warning is expected: the example policy was written for the larger `vertical` preset, and its unit
envelopes name units the tiny organisation does not have. `--silver` is the folder that holds `records/`, not
`records/` itself. If `gold` already exists and is not empty, the build stops with exit 2; delete it or choose
another `--out`.

The gold folder has one subfolder per audience:

```sh
ls gold gold/exec
```

```text
gold:
central
delivery
exec
manager
manifest.json
person

gold/exec:
costs_month.csv
groups_month.csv
pools_month.csv
scenario_summary.csv
seats_month.csv
```

`central/` holds everything, pseudonymous; `exec/` holds group figures and costs; `manager/<key>/` and
`person/<key>/` hold what one manager or one person may see. A group figure is published only for a group of at
least *k* people (5 in the example policy): in `exec/groups_month.csv`, a row with `hidden` = `true` has
its figures left empty (*suppression*; only `central/` also says why). `delivery/` holds names and emails
and is never shown to anyone. [`packages/meterline-gold/README.md`](../packages/meterline-gold/README.md)
describes every folder.

## 4. Open the dashboard

The example dashboard is one HTML file that shows the CSV files of the folder it is served from. Serve one
audience folder, never the gold root:

```sh
cp packages/meterline-gold/src/meterline_gold/dashboard/index.html gold/exec/
cd gold/exec && python3 -m http.server 8000 --bind 127.0.0.1
```

The server prints one line and keeps running until you press Ctrl-C:

```text
Serving HTTP on :: port 8000 (http://[::]:8000/) ...
```

On some systems the address reads `0.0.0.0` instead of `::`. Open <http://localhost:8000/> in a browser. You
see the title, a line that says "Audience detected from the files: exec" and the currency (USD), and one
button per file: `groups_month`, `costs_month`, `seats_month`, `pools_month`, `scenario_summary`. The first
file is shown as a table in its own row order, with a bar chart of one column under it. Suppressed cells read
"suppressed (reason)".

## What you did, and where to go next

You ran the whole chain on invented data: records in the common format, gold datasets for four audiences with
group-size suppression, and a dashboard that reads them. With a real provider, step 2 is a connector instead of
the simulator; see the connector READMEs under [`packages/`](../packages/) and their Known limits first.

- To write your own policy: [how-to/configure-policy.md](how-to/configure-policy.md).
- To add a provider: [how-to/add-a-provider.md](how-to/add-a-provider.md).
- To see why the format is as it is: [decisions/](decisions/).

To clean up, stop the server and delete `sim-out/` and `gold/`; both are ignored by git.
