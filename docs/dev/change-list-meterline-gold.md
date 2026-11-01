---
type: change-list
status: open
updated: 2026-10-17
about: docs/dev/history/meterline-gold-core-SPEC-v1.0-2026-10-12.md (accepted; frozen)
---

# meterline-gold: change list against spec v1.0

Findings from the phase 1 build. The accepted spec is frozen: nothing here edits it. A finding defaults to the code (L1 rule on specs); each entry says what the build did meanwhile and what change to the spec or the checks is proposed. Entries are numbered in the order found.

## Findings against the spec

### 1. Section 4.3 (`unit_envelopes` keys must exist in `units.csv`) contradicts section 4.4 with G08-G13 and G21

- **Observed.** The example policy (4.4) carries envelope keys of the simulator's `vertical` preset (`commercial`, `operations`, ...). The `tiny` preset has no such units (`company`, `alpha`, `beta`, `alpha-a`, `alpha-b`). Section 14 builds the tiny sim with the example policy (G08, G09, G10, G12, G13), and 4.3 says an envelope key missing from `units.csv` is exit 2. Both cannot hold. Section 8.1 already handles an envelope whose cell does not exist that month.
- **Built meanwhile.** Phase 1 builds no scenario, so it does not enforce this one rule of 4.3; every other 4.3 rule is enforced (by the Pydantic models in `params.py`). Nothing else changes.
- **Proposed change.** Drop the 4.3 bullet (an unknown envelope key means "no cell", as in 8.1), or turn it into a data-quality row. Alternative: keep the bullet and ship an example policy without envelope keys.

### 2. Section 2 and G23: no interface reaches the internal tables

- **Observed.** G23 says a test "corrupts one table or file of a fixture build" and checks the assertion fires. Section 2 names three interfaces (`run.ASSERTIONS`, `run.run_privacy`, `write.DATASET_KEYS`); none gives a test the tables or the A09 check.
- **Built meanwhile.** `run.prepare(silver, org, policy, prices, invoice, key, threads)` returns the DuckDB connection with every dataset table in place and no assertion run; `run.failures(con)` runs `run.ASSERTIONS` and returns `(name, first offending row)` pairs; `write.a09_violations(out_dir, org_dir, silver_dir)` is A09. The internal tables carry the dataset names (`person_day`, `person_month`, `costs_month`, ...).
- **Proposed change.** Add these three names to the interface list of section 2 and section 14.

### 3. G20: no convention says how the dataset queries are found

- **Observed.** G20 requires "every dataset named in `write.DATASET_KEYS` is produced by a query that ends with ORDER BY, and at least one such query is found per dataset", but not how a test locates a dataset's query in `sql/*.sql`.
- **Built meanwhile.** Each dataset query is the statement preceded by the comment line `-- dataset: <name>` (`-- dataset: exec/<name>` for an exec form). The runner fetches its datasets through the same markers, so the convention is functional. Assertions use `-- assertion: <name>`.
- **Proposed change.** State the marker convention in section 2 or 9.4.

### 4. G08: the second build runs through `cli.main` with `threads=1`, but the CLI has no thread option

- **Observed.** Section 10 gives `build` no `--threads`; section 2 gives `threads` to `build()` only, "for the determinism check only".
- **Built meanwhile.** `cli.main` calls `meterline_gold.build` through the module attribute, so the check wraps `meterline_gold.build` in the subprocess and passes `threads=1`.
- **Proposed change.** Say in G08 that the subprocess wraps `meterline_gold.build`, or add a hidden `--threads` option.

### 5. Section 9.3: what `suppression` holds in a phase 1 manifest

- **Observed.** The manifest shape lists `suppression: {cells, k, differencing, secondary}`; phase 1 builds no cell (13).
- **Built meanwhile.** `{"cells": 0, "differencing": 0, "k": <policy k>, "secondary": 0}`.
- **Proposed change.** Say so in section 13, or omit the key until phase 2.

### 6. Section 6.6: `is_final` of a pool without billing records

- **Observed.** "AND of the billing records' `is_final`" is vacuously true with no record, which would call an empty month final.
- **Built meanwhile.** `false` when there is no billing record.
- **Proposed change.** State the rule.

### 7. Section 6.5: the day on which a seat holder's actor is resolved

- **Observed.** "A holder is matched to its usage by `person_key` when its actor resolves (5.2)" does not say on which day the identity row is read for a holder taken from a snapshot.
- **Built meanwhile.** The day for which the holder is counted (the snapshot applies to every day from its own day up to the next snapshot).
- **Proposed change.** State the rule.

### 8. Section 3.2: "`unit_id` must be a line unit valid that day"

- **Observed.** The rule is per day, but a person row spans many days and the unit's own rows may be split (a unit row changes whenever a column such as `parent_id` changes).
- **Built meanwhile.** A people row is accepted when the line-unit rows of its `unit_id` cover every day of the row without a gap.
- **Proposed change.** State that reading.

### 9. Section 2: the `dev` extra does not list the simulator

- **Observed.** Section 14 builds the tiny and vertical sim in most checks, so the tests import `meterline_sim`, which the `dev` extra (`pytest`, `hypothesis`) does not install. CI installs it by name (`.github/workflows/ci.yml`).
- **Built meanwhile.** `dev = ["pytest", "hypothesis"]` as in the spec; CI installs `packages/meterline-sim[dev]` beside it.
- **Proposed change.** Note in section 2 that the tests need `meterline-sim`.

## Findings against the done-checks

These tests were committed before the build and failed for defects in the test code itself, not for a wrong build. The rule of this build is that a done-check is never changed to fit the code, so the owner decided; on the owner's instruction (2026-10-13) both were corrected in their own commit, which shows the diff in the pull request. No assertion was loosened.

### 10. `test_done_check_G10a_ground_truth_totals` (G10a)

- **Observed.** The test loops over `MEASURES`, which includes `tokens_cache_write_5m` and `tokens_cache_write_1h`. The ground-truth `totals` rows of the simulator (sim spec 11.3) carry only `tokens_input_uncached`, `tokens_cache_read`, `tokens_cache_write`, `tokens_input_total`, `tokens_output`, `requests` and `quantities`. The test stops with `KeyError: 'tokens_cache_write_5m'` before it compares anything.
- **Fixed (owner's instruction).** The loop skips `tokens_cache_write_5m` and `tokens_cache_write_1h`; the six measures the totals rows carry are compared as before.

### 11. `test_done_check_G12_tiny_sim_has_no_identifying_data` (G12, negative control)

- **Observed.** `first = read_csv(directory)[1][0]` is the first row (a list), not a person id, so `first + "@example.com"` raises `TypeError`. The positive assertions before it pass.
- **Proposed change.** `first = read_csv(directory)[1][0][0]`.

### 12. Negative controls that prove little (found by the independent review)

Not changed: they are weak, not wrong, so they are left for the next version of section 14. Each is a weakness of a check, not of the build.

- **G05:** the control `assert rows[P03]["projected_cost_ref"] == "1.85"` inside `pytest.raises` follows an assertion that it equals `"1.84"`; it cannot fail.
- **G09:** the control bumps a copied dict and compares it with silver; it never runs the gold-side comparison.
- **G10a:** the control compares two rounded constants.
- **G16 (schema files):** the control compares the committed text with a string-edited copy of the generated text; true by construction.
- **G19:** the injected assertion fails before any write, so cleanup after a failure during writing (the A09 path) is not exercised. G23 calls `a09_violations` directly and does not check that a build exits 1.
- **G03 (exec):** the fixture always has rows under `k`, so neither "no row needs hiding" nor a multi-step secondary hiding is covered. The build had this bug (exec over-hiding when no provider is under `k`); it was fixed after the review, and the case is covered only by the review's own run, not by a check.
- **G13:** checks `central/` and `delivery/` only, not `exec/`; G20 checks that an `ORDER BY` ends the query, not that it covers the full key.
- **G08:** the other-key control differs through `person_key` alone and says nothing about order stability; thread-count nondeterminism is unlikely to show on the small fixture.
- **Proposed change.** Strengthen the controls above in the next version of section 14, and add a fixture variant with every provider at or above `k`.

## Findings from the phase 2 build

Same rule as above: the accepted spec is frozen, a finding defaults to the code, and each entry says what the build did meanwhile and what change is proposed.

### 13. Section 2 and G11, G22: what `run.run_privacy` returns, and where `active_persons` is decided

- **Observed.** Section 2 gives the signature `run_privacy(con, k, *, _disable_block_rule=False)` and 14 says it "runs 7.4 on the DuckDB tables `cells(...)`", but not what it returns, where the states land, or where the `active_persons` decision of 7.2 (which G11 feeds to its attacker as a published measure) is made.
- **Built meanwhile.** `run_privacy` reads `cells(month, cell_id, parent_cell_id, persons_list, receivers_list)` and returns `{(month, cell_id): reason}` for every suppressed cell. It leaves `cell_state(month, cell_id, suppressed, reason)` in the connection. When the connection holds `person_flags(month, person_key, active)`, it also runs the `active_persons` pass of 7.2 and leaves `cell_active_published(month, cell_id)`. A build calls it once, between `60_groups.sql` and the later files.
- **Proposed change.** State this return value and the two tables in section 2 and in G11/G22. Also name the other internal tables the tests reach (`cell_members`, `cell_state`, `cell_figures`, `exec_groups_month`, `manager_groups_month`), as finding 2 proposed for phase 1.

### 14. Section 7.2: "decided after 7.4 in its own pass until nothing changes" leaves the fixed point open

- **Observed.** A cell's `active_persons` depends on which descendants publish theirs, and those depend on their own descendants. Starting from "every candidate publishes" and only ever removing values would over-suppress (a parent is removed because a child was still counted, and the child is removed later). Re-evaluating every cell against the current published set until nothing changes gives one answer, the bottom-up one.
- **Built meanwhile.** The pass re-evaluates every candidate cell against the current published set until the set no longer changes (the candidates are the unsuppressed cells with at least `k` active and `k` inactive persons). It ends within (tree height + 1) rounds and gives the bottom-up result.
- **Proposed change.** Say "evaluated bottom-up" in 7.2.

### 15. Section 7.4 b2: "direct members" is defined for unit trees only

- **Observed.** 7.1 gives direct members as the persons whose own unit is the unit, and "always empty" for the attribute tree. The tables of G11 and G22 (`cells`) carry no direct-members column.
- **Built meanwhile.** Direct members are the persons of a cell that sit in none of its children. That is the 7.1 definition for a unit tree. For an attribute tree a non-leaf cell's children partition it (so none), and a leaf cell has all its persons as direct members, which can never fail a block test once the primary rule holds (its block has at least `k` persons and it has no receivers), so the outcome equals "empty".
- **Proposed change.** State this derivation in 7.4, so that the `cells` table needs no extra column.

### 16. G11: the generator, the attacker and the coverage condition are underspecified

- **Observed.** 14 fixes the ranges (depth ≤ 3, 1-4 children, 0-6 direct members, ≤ 25 persons, `k` in 2..6) but not their distribution, and asks that at least half of the examples have a suppressed cell under a published ancestor and at least 3 published cells. With near-uniform draws only about 38-42 % of the examples reached that condition. 7.4 and 14 also leave the attacker's view open: which cells "P's receivers get", and whether `active_persons` is a separate linear system.
- **Built meanwhile.** The generators stay inside the stated ranges. Three examples in four are built to have a small group under `k` beside larger ones (a small child unit under a root with three larger ones; a small value group beside three larger ones), and one in four is drawn uniformly over the ranges, which brings the coverage condition to about 80 %. The generators were changed once after the first run of the check (their weights only, commit following `Phase 2 done-checks`): the first version drew uniformly and reached 38 % on the condition. Assertions a and b, the examples and the coverage threshold are as stated. The attacker sees, for each distinct set of receivers, the published cells that any of them receives (the unit cells they head and below), and substitutes the receivers' own values; the exec and central attacker sees every published cell. Each published measure is its own system; `active_persons` is published for a subset of the published cells, so its row space lies inside that of `cost_ref` and the second system is built only when the subset is a proper one. The exact search finds sets of up to `k` - 1 persons whose columns of the null space sum to zero (meet in the middle).
- **Proposed change.** State the attacker's view and the generator weights in 14.

### 17. G11 does not cover the binary leak of `active_persons`

- **Observed.** The attacker of G11 is linear over real values. The remainder rule of 7.2 protects against a different leak: a remainder of at least `k` persons who are all active, or all inactive. That is not a row-space fact, so G11 cannot see it; only A06 (and G15's A07 run) reads it.
- **Built meanwhile.** A06 re-evaluates the 7.2 rule on the published state, and G23 checks that A06 fires on a corrupted state. No check generates data that tries to break the remainder rule.
- **Proposed change.** Add to G11 an assertion over the generated data: every published `active_persons` passes the remainder test (the generated flags make it non-trivial), or a second property test for it.

### 18. Section 13/14: the layout list of G06 is phase 3, and G13 and G23 list no phase 2 datasets or assertions

- **Observed.** G06 includes "the sorted list of output paths equals `expected/layout.txt`", which names phase 3 files; section 13 puts that part in phase 3. G13 ("every dataset present in the phase") and G23 ("each of A01-A09") are phase 1 checks that do not name `groups_month`, `comparison_groups`, `comparison` or A04-A07.
- **Built meanwhile.** The phase 1 checks are unchanged. Phase 2 adds `test_done_check_G06_fixture_layout_without_phase3_files` (the layout minus the six named phase 3 files and the manager `named_exceptions.csv`), `test_done_check_G13_phase2_datasets_sorted_by_key` and `test_done_check_G23_phase2_assertion_fires[A04..A07]`.
- **Proposed change.** Name the phase 2 datasets in G13 and A04-A07 in G23, and split the layout list of G06 by phase.

### 19. Section 7.1: a cell id of an attribute tree is ambiguous when a value holds `|` or `=`

- **Observed.** `attr:<a1>=<v1>|<a2>=<v2>` is the cell id. A value that contains `|` or `=` can give two tuples the same id; 4.1 puts no pattern on attribute values.
- **Built meanwhile.** Nothing escapes; the ids are built as written.
- **Proposed change.** Either forbid `|` and `=` in an attribute value used in the grid or a comparison group (exit 2), or escape them.

### 20. Section 9.5 A06: the assertion list leaves out step a (differencing)

- **Observed.** "No unsuppressed cell fails step 1, b1 or b2 of 7.4" omits step a, so a published pair that differs by 1 to `k` − 1 persons would pass A06.
- **Built meanwhile.** A06 is as written (steps 1, b1, b2 and the 7.2 rule). The differencing property is covered by G11 (a published pair with a small difference puts a small set in the row space) and G22.
- **Proposed change.** Add step a to A06.

### 21. Section 7.4 b2: a head can subtract their own figure into a suppressed child (found by the independent review)

- **Observed.** The third failure case of b2 ("the block is exactly one piece that is a suppressed child's uncovered part") reads the full block only. When P's direct members are all receivers, P's unknown block is exactly one suppressed child's uncovered part, and nothing fires. Example (k = 5): root R with its head as only direct member, a child C of 5 persons (suppressed, reason `k`, because 5 - its head = 4), and a sibling root of 6. R is published; its head, who gets R and C and knows their own figure, computes R - own = C's total. The G11 attacker finds it (assertion a, receivers {head}); the 300 derandomized examples of the first generators did not reach it.
- **Built.** The third case also fires on the unknown block: when it is non-empty and all of it lies in one piece that is a suppressed child's uncovered part. The same test sits in A06. A regression test (`test_done_check_G11_head_cannot_subtract_into_a_suppressed_child`) was written first and failed on the earlier build. The fixture outputs are unchanged.
- **Proposed change.** Add this case to 7.4 b2 and to A06 in section 9.5.

### 22. Section 7.4 Manifest: `suppression.k` is the count of cells suppressed for reason `k`

- **Observed.** The text says the manifest counts "all cell-months (`cells`) and the suppressed ones by reason", and the shape of 9.3 names `k`, `differencing`, `secondary`. The first phase 2 build wrote the policy's `k` there (carried over from finding 5), which the review caught.
- **Built.** `k` is the number of cell-months suppressed with reason `k` (1 on the fixture).
- **Proposed change.** Say so in 9.3; the key name next to the policy's own `k` is easy to misread.

### 23. G11 generators and negative controls (review notes)

- The first re-weighting of the G11 generators (finding 16) left three quarters of the examples as one fixed shape. The final generators give half of the examples that shape, a quarter deep trees (every unit above depth 3 has 1-3 children) and a quarter drawn uniformly. The assertions are unchanged.
- Rejected: three negative controls (G22 `state != wrong`, the G06 layout list with one more path, the manifest `!=`) are true once the positive check holds. Section 14 asks controls of byte comparisons and scans only, and the real controls (`compare_with_control`, the band check, the four G15 plants, G11's disabled block rule) do fail for their stated reason.
- Rejected: the cascade rule of 7.6.1 is not re-implemented in G15; it would repeat the algorithm in the test, and the fixture's exec costs are compared byte for byte by G03. G15 now also checks that a pool of a hidden costs row shows no figure and that an exec `idle_seats` value equals the central one.

### 24. Section 7.1 and A05: a unit whose parent differs between persons' attribution days

- **Observed.** The grid takes a unit's parent as the lowest `parent_id` among the persons' unit paths of the month. If a unit moved within a month, cells can nest as sets (A05 passes) while the recorded parent is the wrong one.
- **Built meanwhile.** Nothing; an edge case that needs a reorganisation inside one month.
- **Proposed change.** A05 also checks that each cell's recorded parent contains it and that all persons agree on a unit's parent, or the build stops with exit 1.

## Findings from the phase 3 build, against spec v1.1

Phase 3 was built on the accepted v1.1 (`docs/dev/history/meterline-gold-core-SPEC-v1.1-2026-10-14.md`). Findings 1-24 above are resolved there; these are new.

### 25. Section 8.1/8.2: a threshold of 0 and a top-up of 0

- **Observed.** `params.Dec` accepts `"0"` for a cap, an envelope, an alert fraction or `top_up_amount`. 8.1 evaluates positions on every day of the window, so a fraction of 0 fires on the window's first day; `qceil(excess, 0)` is undefined.
- **Built.** Each month's first day is a position day for every subject, so a zero threshold fires there (found by the independent review). A `top_up_amount` of `"0"` gives null `top_ups` and `top_up_total`.
- **Proposed change.** Add to 4.3: caps, envelopes and `top_up_amount` are greater than 0, alert fractions are greater than 0.

### 26. Section 7.7: named exceptions cover attributed persons only

- **Observed.** Rules select "persons per month". The build selects among persons with a `person_month` row, since `manager_key` and the audience come from it. A resolved account with breaker or spike events in a month where the person has no employed day gets an `alert_events` row and no `named_exceptions` row.
- **Built.** As stated. **Owner to confirm** this reading, or the exception rows need a person with no `person_month` row to be routed (no manager exists for them).

### 27. G23 phase 3 A06 case: step a is mostly implied by b2 on a tree

- **Observed.** On a tree, two published cells that differ by 1 to `k` - 1 persons almost always also fail b2. The build's own loop (7.4 step a) is therefore hard to reach with a fixture state.
- **Built.** The check changes `cells` so that org and `u01` differ by one person, lets `run.run_privacy` suppress both (reason `differencing`), puts both back as published and requires A06 to report the pair with rule `step a`.

### 28. G06: the phase 2 layout test is replaced from phase 3

- **Observed.** Spec 14 says the layout list is the whole of `expected/layout.txt` from phase 3.
- **Built.** `test_done_check_G06_fixture_layout_without_phase3_files` is replaced by `test_done_check_G06_fixture_layout_is_the_whole_list`, in the done-checks commit before the build. Nothing else of phase 2 was changed.

### 29. Section 8.1: a unit key shared by several unit ids

- **Observed.** `unit_envelopes` is keyed by line-unit `key`; `units.csv` does not require a key to be unique across `unit_id`s.
- **Built.** Every unit id with that key whose cell exists in the month is a subject with the same envelope.
- **Proposed change.** State this in 8.1, or require line-unit keys to be unique in 3.2.

## Spec v1.2: the `hidden` marker

Built as one change on the phase 3 package, against `docs/dev/meterline-gold-core-SPEC-v1.2-2026-10-15.md`: the `hidden` column on five audience datasets, gold schema version `1.1`, the dashboard change and done-check G26. No finding against the spec.

## Review fixes of 2026-10-17: changes proposed for spec v1.3

Raised by the fix session for the 2026-10-15 review (branch `fix/review-gold-correctness`). The accepted specs are frozen; each entry says what the code now does and what the next spec version should say.

### 30. Section 7.6 audience table: a person folder carries no manager_key (owner decision of 2026-10-16)

- **Observed.** `person/<key>/person_month.csv` ended with the person's `manager_key`, so 20 of 21 person folders of the fixture held another person's key.
- **Built.** The `person_month` file of a person folder has the central columns without `manager_key`. `central/person_month.csv` keeps it, manager folders keep the person keys of their `named_exceptions` (a manager needs to know who; names are joined at delivery), and `manifest.json` is documented as a central file because it lists every key through its paths. The recorded sha256 lines of `person/*/person_month.csv` in `docs/gold-fixture/expected-v1.1-sha256.txt` and `tiny-sim-v1.1-sha256.txt` were regenerated; no other line changed. Test: `test_person_files_carry_no_other_key`; G15 compares a person folder's `person_month` with the central rows less `manager_key`.
- **Proposed change.** In the audience table of 7.6, `person_month` for the person audience lists every column but `manager_key`; 7.6 states that `manifest.json` is never delivered to an audience.

### 31. Sections 3 and 4: currency is checked, and no sum crosses currencies

- **Observed.** The policy's and price list's `currency` was any string, and `person_day.cost_source` (Σ silver `cost`), `costs_month.usage_cost_source`, `reconciliation_month` and the pool amounts summed silver `cost` across currencies; `ref_vs_source_gap` subtracted a source amount from a reference amount whatever their currencies.
- **Built.** A currency of the policy, the price list or a silver cost is one of the ISO 4217 codes in `iso4217.py`. A build stops with exit 2 when the cost-bearing silver records (usage and org totals) are in more than one currency, or in a currency other than the policy's. The error names the currencies. The dashboard writes the currency into the header of every money column and states that days and months are UTC. Test: `test_currency_not_mixed`.
- **Proposed change.** State in 3 that silver costs are in the reference currency; list the ISO 4217 rule in 4.1 and 3.3. Grouping by currency in 6.2, 6.6 to 6.8 (instead of refusing) is left open: it would add `currency` to their keys.

### 32. Sections 3, 6.9, 9.4 and 9.5: silver is validated on read, flagged inputs are counted, A01 covers every key, new A10

- **Observed.** Gold read silver without calling the record validator: a negative cost reached `person_day.csv` with exit 0. A person dropped from `people.csv` removed that person's cost from `person_month` with no row in `data_quality`. A01 covered 11 of the 20 dataset keys.
- **Built.** Every silver record read passes `meterline.validate`; a failure is exit 2 with the file and record number. `data_quality` gets `rows_person_missing` and `cost_ref_person_missing` (per month, provider, org) and `units_parent_unknown` (per month, provider and org empty); the three are written only where not zero, so a clean build has the same rows as before. `manifest.json` gets `input_flags` (`persons_missing`, `rows_person_missing`, `units_parent_unknown`) and the build command prints a warning when one is not zero. A01 covers all 20 keys of `write.DATASET_KEYS`. A10: per month, `costs_month.usage_cost_ref` equals `person_month.cost_ref` plus the cost that `data_quality` accounts for (`cost_ref_unresolved`, `cost_ref_non_person`, `cost_ref_person_missing`). Tests: `test_silver_validated_on_read`, `test_person_missing_from_people_flagged`, `test_unique_keys_all_datasets`.
- **Proposed change.** Add the validation to 3.1, the three metrics to 6.9, `input_flags` to the manifest in 9.4, A10 to 9.5, and the nine datasets to A01.

### 33. Section 7.4: limits and boundary tests (no change of behaviour)

- **Observed.** Seven of the ten boundary points of the suppression rule had no named test, and the gold README did not state the across-builds limit, that keys are pseudonyms, or that rotating the hashing key leaves the connectors' unkeyed sha256 labels as they are.
- **Built.** Named tests pin the primary rule and the receiver clause at k - 1, k and k + 1 for k = 5 and k = 2 (`test_privacy_suppression_exact_k_*`), shown failing against off-by-one mutations of `70_privacy_step.sql`. The gold README states the limits and gives an example of what disappears. `test_keys_stay_out_of_git` scans tracked files for a key-shaped value. One mutation (`z.persons < p.k - 1` in the first half of step 1) cannot be told from the original: the second half, `z.persons - receivers < k`, already implies the first.
- **Proposed change.** Move the README's limits into 7.4's Limits paragraph.

### 34. Section 8.1: a top_up_amount of "0" is now null everywhere

- **Observed.** Finding 25 says a `top_up_amount` of `"0"` gives null `top_ups` and `top_up_total`. The build did so only where the excess was above 0: a row with no excess showed `top_ups` 0 and `top_up_total` 0.00, and the summary showed 0.00.
- **Built.** A scenario with `top_up_amount: "0"` is loaded as a scenario without a top-up, so every `top_ups`, `top_up_total`, `persons_top_up_total` and `units_top_up_total` of it is empty. Test: `test_top_up_amount_zero_gives_null_top_ups_everywhere`.
- **Proposed change.** As in finding 25: caps, envelopes and `top_up_amount` are greater than 0 in 4.3.

### 35. Section 12 and 4.1: constants and branches the spec does not list, and a second example

- **Observed.** The review found four fixed values that sit in the code and in no list of constants: the unit-tree recursion cap of 64 (a deeper hierarchy would silently lose ancestors), ratios rounded half up to 4 decimals, `projected_cost_ref` rounded to 2 decimals, and price decimals at most 6 with per-million prices shifted by 6 places. Three policy branches had no test (`bands.metric: tokens`, `breaker_events_at_least`, the grid attributes `cost_center` and `population`), and no shipped policy used an attribute tree.
- **Built.** The gold README lists the four values (section "Fixed values"). `test_config_branch_*` runs each branch, each shown failing against a mutation of its SQL. `policy.attributes.example.yaml` is a second example: an attribute tree of three dimensions, a tokens metric and the breaker rule; it builds on the simulator's output and obeys the 4.4 rule for amounts.
- **Proposed change.** Add the four values to the list of constants in section 12, and name the second example in GD1.

### 36. Section 3.4 and G18 (Appendix A.10): a listed model without an input or output price is an input error (owner decision of 2026-10-17)

- **Observed.** Section 3.4 says "A missing input or output price gives `per_million: null`." The importer followed it: a listed key with no `input_cost_per_token` or no `output_cost_per_token` (or one set to `null`) was written as a model with `per_million: null`, so a price list could be built with a model that has no price and no warning.
- **Built.** `meterline-gold import-litellm-prices` stops with exit 2 and `meterline-gold: input error: ...`, naming the model and its key, and writes no file. The unchanged rules: a missing `cache_read` or `cache_write_5m` price is taken from the input price, and a missing 1-hour price stays `cache_write_1h: null`. G18's fixture changed after acceptance, to follow this decision: `model-q-three` (output price only) is no longer in `docs/gold-fixture/litellm/keys.txt`, and `docs/gold-fixture/litellm/expected-prices.yaml` was regenerated and holds the two priced models; the only lines removed are `model-q-three`'s `models` entry and its `token_prices` entry (`per_million: null`). `sample.json` still holds `model-q-three`, as the price-less case. Tests: `test_review_litellm_missing_price.py` (missing input, missing output, explicit `null` for each, the price-less fixture model, and the unchanged cache rules).
- **Proposed change.** In 3.4 the line becomes "a missing input or output price is an input error (exit 2) naming the model". In G18 and Appendix A.10 the keys are `vendor-q/model-q-one` and `model-q-two`, the expected file has those two models, and the list of fixture models says `model-q-three` is in `sample.json` but not in `keys.txt`.

### 37. Sections 4, 7.3, 7.4 b2, 8.1 and 8.2: wording (no change of meaning)

- **Observed.** The review of 2026-10-15 found rules that hold several conditions in one sentence, "published" used in three senses, an obligation written as "is allowed", and six restrictive clauses written with a preposition and "which". None changes what the code does. The conventions are in `docs/dev/style.md`; this entry gives the wording for the next version.
- **4.3.** "`month_cost_ref_above` is allowed with audience `central` only" is an obligation: the condition must be used with audience `central` only, and any other audience is an input error (exit 2), as `params.py` does.
- **7.3.** The heading "Suppression: what is published" covers every row written, suppressed ones included. The next version reads "Suppression: what a suppressed row keeps", and "published" then means only a cell whose figures are written (`docs/glossary.md`).
- **7.4 b2.** P fails when any one of these holds:
  1. 1 ≤ |block| < `k`, which protects against every viewer;
  2. 1 ≤ |unknown block| < `k`, which protects against a receiver who knows their own figures;
  3. the block is non-empty and is exactly one piece that is a suppressed child's uncovered part, or (v1.1) the unknown block is non-empty and lies entirely within one such piece.
- **8.1.** For each scenario, month and subject:
  1. position(d) is the month-to-date `cost_ref` from the window's first day to d, on every day of the window; a unit's position adds everyone attributed to its subtree, and seat cost never counts;
  2. `stopped_on` is the first day with position(d) > cap (strictly), else null;
  3. `excess` = max(0, position at the window's end − cap); `top_ups` = qceil(`excess`, `top_up_amount`) and `top_up_total` = `top_ups` × `top_up_amount`, both null without a `top_up_amount`.
- **8.2 spike.** A spike event fires on day d when all of these hold:
  1. the window has at least `min_history_days` days;
  2. the day cost is greater than `floor`;
  3. the day cost × (days in the window) is greater than `multiplier` × (the sum of day costs over the window), a day with no row counting 0.
- **"Which".** In 4.1 (`alerts.daily_breaker`), 4.2 (`alert_fractions`), 5.3 (employed days), 6.5 (the day a holder is resolved), 6.6 (`budget_crossed_on`) and 7.1 (the month's unit tree), the next version writes the restrictive clause with "that".

### 38. Section 1 (dependencies): duckdb 1.5.3 or later

- **Observed.** The minimum-versions CI job (duckdb 1.5.0, the declared lower bound) failed G08 once: the in-process build reported every token-priced row as unpriced, with reference cost 0.00, while the command-line build of the same inputs priced them. Reproduced in a Linux container (Python 3.10, lowest direct versions, 4 CPUs). A four-thread build on duckdb 1.5.0 sometimes creates `price_day` empty: 2 in 400 builds of the branch and 1 in 400 of `main` before it. Re-running the person_day SQL on a prepared connection gave an empty or short `price_day` 5 to 13 times in each set of 1,500 runs on 1.5.0, 1.5.1 and 1.5.2, and 0 times in 3,000 runs on each of 1.5.3, 1.5.4, 1.5.5 and 1.5.6. The tables loaded before it are identical in every run, and the same query run again on the same connection returns the 15 expected rows. So this is a fault of those duckdb releases, not of the gold SQL or of any input; it was not seen on macOS. It matches duckdb issue #22075 (wrong result with `join_filter_pushdown` on a grouped probe-side subtree, parallel bloom filter; `price_day` joins `price_tokens` to a `SELECT DISTINCT` subquery), fixed by duckdb pull request #22218 ("Defer Bloom Filter Pushdown until it's done") and released in 1.5.3. Confirmed on 1.5.0: with two runs side by side, the default settings gave 13 and 6 wrong `price_day` results in 1,500 runs each (some empty, some with 4 or 12 of the 15 rows), and `SET disabled_optimizers = 'join_filter_pushdown'` gave none in 1,500 runs, twice.
- **Built.** `meterline-gold` declares `duckdb>=1.5.3,<2`, so the minimum-versions job now tests 1.5.3. `test_review_duckdb_floor.py` keeps the bound there.
- **Proposed change.** In section 1 the dependency line reads `duckdb>=1.5.3,<2`.

### 39. Sections 7.2, 7.3, 7.6 and 7.6.2: `groups_month` outside `central/` says hidden, never why (owner decision of 2026-10-17)

- **Observed.** Spec v1.2 left `suppression_reason` (`k`, `differencing`, `secondary`) in the exec and manager copies of `groups_month`, and recorded the question for the owner ("Changes from v1.1", the paragraph "Not changed, and recorded for the owner"). A manager or an executive could read from `k` that a cell holds fewer than `k` people. The owner decided on 2026-10-17: hide it.
- **Built meanwhile.** `exec/groups_month` and `manager/<key>/groups_month` carry the central columns without `suppressed` and `suppression_reason`, and end with `hidden`. `hidden` is `true` exactly when central's `suppressed` is `true` for the same cell, and false otherwise. Every other value is the one v1.2 publishes. `central/groups_month` is unchanged. A07 compares the internal tables, which still hold the reason. G26 c now covers `groups_month`. G26 d skips these files, because their v1.1 bytes cannot be rebuilt from a file that has no reason. G06 and G15 compare each row with the marked form of central's row. The hand-built expected files of the exec and manager folders changed to match.
- **Open point.** A figure withheld on a published cell (`active_persons`, a quantile) stays blank with `hidden` = `false`, as 7.2 (v1.2) says. So for `groups_month` the 7.6.2 reading "an empty covered value on a row with `hidden` = `false` means none" does not hold. The build keeps `hidden` equal to the old `suppressed`. Extending the marker to those blanks is a separate decision.
- **Proposed change.** In 7.2, give the exec and manager columns and remove the sentence that `groups_month` needs no `hidden` column. In 7.6.2, add a row for these two datasets and drop the clause that leaves the reason in their copies. In "Changes from v1.1", remove the paragraph that records the question. In G26, c covers `groups_month`. In section 11, "suppressed (reason)" shows in `central/` only.

### 40. Sections 6.7, 7.6.1 and 7.6.2: `costs_month` says what its figure is

- **Observed.** On the vertical preset (seed 7, 6 months), `costs_month` shows GitHub Copilot at 662.49 USD for 2026-04 with `cost_source_basis` `consumption`. The invoiced amount of 47.49 USD (the overage) appeared only in `reconciliation_month` and `pools_month`. A reader of `costs_month` takes 662.49 for the bill.
- **Built meanwhile.** `costs_month` in `central/` and `exec/` ends with two columns. `cost_basis_label` gives the basis in words: `usage value, not the invoice` for `consumption`, `estimate, not the invoice` for `estimate`, `billed amount, as invoiced` for `billed`, and `mix of billed, usage-value and estimated amounts` for `mixed`. It is empty on a row with no usage figure. `invoice_usage` is the invoice amount of kind `usage` for the same month, provider and organisation, the value `reconciliation_month` shows, and empty without one. In `exec/costs_month` both are covered columns: they are empty on a row with `hidden` = `true`. The columns come last, before `hidden`, so positions of the v1.1 columns do not move. `schema_version` stays `1.1`. Tests cover the consumption, estimate and billed bases (the billed one on a copy of the fixture with the anthropic records moved to that basis).
- **Proposed change.** In 6.7, add the two columns and the four label texts. In 7.6.1 and 7.6.2, add them to the covered columns of `exec/costs_month`. Decide whether adding a column and renaming one (finding 41) need `GOLD_SCHEMA_VERSION` `1.2`; the build kept `1.1`, so G14 still holds.

### 41. Sections 6.8 and 6.9: `unallocated_share` has one meaning per name (owner decision of 2026-10-17)

- **Observed.** For LiteLLM in one month, `unallocated_share` is 0.0000 in `reconciliation_month` (6.8: the organisation total minus the actors' cost, over the total) and 1.0000 in `data_quality` (6.9: unresolved plus non-person cost, over the total cost). Both follow the spec. One name for two measures reads as one.
- **Built meanwhile.** The `reconciliation_month` column is `unreconciled_share`. The `data_quality` metric keeps `unallocated_share`, because it counts cost that no resolved person owns, and it is a value in a long table, not a column. The rename touches one dataset. The fixture's expected header, the glossary (new entry `unreconciled_share`) and the README follow; the dashboard and `docs/schemas` do not name the column. The siblings `unallocated_cost_source` and `unallocated_credits` keep their names.
- **Proposed change.** In 6.8, rename the column. Consider renaming `unallocated_cost_source` and `unallocated_credits` in the next major version, so the three columns carry one name.

### 42. Section 11: the dashboard opens on a cost chart, with a month selector and frozen columns

- **Observed.** On the exec page the first chart was a bar chart of `schema_version` (48 identical bars). The groups table needed sideways scrolling to reach any cost column, and there was no month selector.
- **Built meanwhile.** A file with a `month` column opens on its latest month, with a selector for another month or all months. It filters the rows shown and computes nothing. The chart opens on a cost column (`total_cost_ref`, `usage_cost_ref`, `cost_ref` or another cost column, whichever the file has) and never offers `schema_version` or `depth`. The two columns after `schema_version` stay in view on a sideways scroll, with fixed widths set by CSS classes; a longer value is cut and keeps its text in the title; below 700 pixels nothing is frozen. The page sets no style from script, so the CSP of the page keeps its form; its hashes were regenerated. `tools/dashboard_axe.js` also fails when the first chart is of `schema_version`. Checked in headless Chrome on the tiny sim (6 months): the exec page opens on `cost_ref` for 2026-09 (5 of 27 rows), and after a sideways scroll the month and cell columns stay at the left edge. The axe run reports no violation.
- **Proposed change.** In section 11, add three bullets: the month selector, the cost chart on opening, and the frozen columns.

### 43. Section 4.4 and GD1: the example policy is sized for the vertical preset

- **Observed.** On the vertical preset (seed 7, 6 months, 407 persons a month) the example policy stopped 38 to 40 persons a month under `default_cap` (about 10 percent), 30 on average under `tiered_caps`, and raised 1,708 alert events in 6 months (285 a month). Three of the six envelopes were below the division's cost in every month. The policy fits the invented figures of the fixture, not a company of that size.
- **Built meanwhile.** The caps and thresholds of `policy.example.yaml` are sized on the monthly cost per person of that build (p90 20.62, p99 1235.59): person caps near the p99 of monthly cost, envelopes near each division's p90 month, the spike floor just under the planted runaway day (G10c needs that day to fire; the p99 of daily cost is 86.92). They keep the GD1 form (two decimals, a non-zero last digit).

| Value | Before | After |
|---|---|---|
| `default_cap`, `flat_cap` | 67.89, 876.54 | 1234.56, 1543.21 |
| `tiered_caps` L1-L3, L4, L5 | 456.78, 234.56, 67.89 | 265.79, 237.41, 1304.82 |
| Envelopes (commercial, operations, finance-legal, people, product, central-it) | 3456.78, 2345.67, 1234.56, 987.65, 1876.54, 5432.19 | 5698.76, 3098.76, 3987.65, 107.65, 257.65, 5432.19 |
| Spike floor; alert fractions of the two envelope and level scenarios | 45.67; 0.5, 0.8, 1.0 | 59.87; 0.8, 1.0 |

| Measure, vertical preset, seed 7, 6 months | Before | After |
|---|---|---|
| Persons stopped a month, `default_cap` | 39.0 | 4.2 |
| Persons stopped a month, `tiered_caps` | 30.5 | 4.0 |
| Persons stopped a month, `flat_cap` | 6.7 | 1.7 |
| Divisions over their envelope a month | 3 to 4 | 1.0 on average |
| Alert events a month | 285 | 94 |
| Team cells hidden (depth 4) | 219 of 270 | 219 of 270 |

- **Not built.** Small teams are not collapsed into their department before the grid: with `privacy.tree` of kind `unit` the collapse is structural (7.1) and the policy has no key for it. A calibration helper that reads `person_month` and prints the percentiles and stop counts would be smaller than the hand sizing for the next adopter, because every organisation needs its own numbers. It is left as a proposal.
- **Proposed change.** Replace the listing in 4.4 with the shipped file. Consider a policy key that merges a unit with fewer than `k` persons into its parent before the grid, and a `calibrate` command.

### 44. Sections 4.4 and 7.5: the example policy compares by function only

- **Observed.** With `compare_by: [function, level]`, 476 of 2,442 `person_month` rows (19.5 percent) had no comparison band, and 156 of 222 comparison groups published no median. Most groups of one function and one level were too small: the median needs at least `2k` persons.
- **Built meanwhile.** `policy.example.yaml` sets `compare_by: [function]`. 42 of 2,442 rows (1.7 percent) have no band: the 7 persons of the `executive` function in each of 6 months. 6 of 78 comparison groups publish no median. `privacy.collapse` for `level` stays, because the level scenarios use it.
- **Proposed change.** Replace `compare_by` in the 4.4 listing. A policy for a smaller organisation may need a coarser grouping still.

## Decisions of the build that the spec leaves open (no change needed)

- Pool and seat price ties (same `valid_from` and org): the build prefers the higher `per_month`, then the higher `included_credits`; duplicate price keys are rejected by validation.
- A people row with `org_id ''` seats entry: every entry of the provider is a candidate (5.2/6.5 "matched on provider alone"); the latest `valid_from` among them wins, then the higher `per_month`.
- `credits × price` is computed at 12 decimals; a product with more than 12 decimals would round (amounts are capped at 12 decimals by validation, credits by 3.1).
- A leftover `<out>.tmp` from a killed run stops the next build with exit 2 rather than being deleted.

## Edit of the accepted gold specs for the public release (no change of meaning)

The three accepted gold specs (v1.0, v1.1, v1.2) are frozen; this edit removed working-file identifiers from them and changed nothing else. It is recorded here because the accepted text was touched.

- Every identifier of a private work item was removed: the parenthesised ones after the titles of the deferred items, the ones in the sentences about the deferred delivery recipe, the deferred telemetry, the deferred sender and the dashboard's missing authentication, and the one in decision "Decided 2026-10-12: (b)". The sentences read the same without them.
- Section GD14 (v1.0, v1.1, v1.2) pointed at a range of those identifiers; it now points at the list headed "Deferred by the owner" in the same spec. That heading read "filed as backlog rows" and now reads "Deferred by the owner:".
- In the finding tables of v1.1 and v1.2, rows 12 and 17 named a work item as their class (c) outcome; they now say "Handled after this version, as stronger negative controls in the test suite". The class line "(c) no spec change, or a backlog row" now ends "or deferred", and the phase 3 line "The backlog row for phase 3 requires G01-G25" now reads "Phase 3 requires G01-G25".
- v1.2, front matter and the "Changes from v1.1" paragraph: the source of the change is named as "the first end-to-end demo (2026-10-15)" instead of by work item.
- Findings 12 and 17 of this change list now read the same way, and the headings and the `raised_by` line of this file lost their identifiers.

## Superseded markers (front matter only, 2026-10-17)

- F-36: the front matter of v1.0 and v1.1 now reads `status: superseded` with a `superseded_by:` line naming v1.1 and v1.2, as the Anthropic v1.0 spec already did; their bodies are unchanged. `tools/test_superseded_docs.py` checks that every document named in a later `supersedes:` line is marked this way.

### Pre-publication review, batch 4 (public names), 2026-10-31

- 2026-10-31 · MF-13 · the accepted gold specs name the columns `org_id` and `group_id` and the option `--org`. The bodies are frozen and keep the old names. Gold now writes `provider_account_id` (was `org_id`), `cell_kind` (was `group_id`) and `schema_version` 2.0 (was 1.1; a renamed column needs a new major), and `meterline-gold build` takes `--org-files` (was `--org`). Only names changed: every value is byte-identical to the build before the change. See `change-list-meterline-prepub-b4.md`.
- 2026-10-31 · MF-13 · deviation in the test of the `hidden` marker (G26): the recorded v1.1 hashes (`expected-v1.1-sha256.txt`, `tiny-sim-v1.1-sha256.txt`) stay as recorded. The accepted spec v1.2 section d defines the comparison as "the file with the `hidden` column removed and every `schema_version` field written `1.0`"; the test also maps `provider_account_id` back to `org_id` and `cell_kind` back to `group_id` in the header row, and reads `schema_version` 2.0 as the 1.1 of the recorded bytes, before it hashes, so the recorded proof still holds.
