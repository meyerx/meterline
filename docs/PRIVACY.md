# Privacy and personal data

- **Date:** 2026-10-17, revised 2026-10-30. **Written against:** commit `79fccaf` of `main`, plus the change set that adds this file. The revision of 2026-10-30 was checked against the code of that day.
- **For:** anyone who runs meterline on real data (the **adopter**). It says what personal data each layer holds, why, who should see it, how long to keep it and how to erase one person. It is not legal advice: the rules quoted are pointers for your own data-protection officer or counsel.
- **Read with:** [`THREAT-MODEL.md`](THREAT-MODEL.md) (what could go wrong and what stops it), and the privacy section of [`../packages/meterline-gold/README.md`](../packages/meterline-gold/README.md#privacy) (pseudonyms, suppression and their limits).

## What this data is

meterline pulls per-person usage of AI tools (tokens, requests, credits, cost, sessions, accepted suggestions, seats) and turns it into per-person and per-group datasets. In most adopters' hands the persons are staff, so **per-person usage data is employee-monitoring data**. That has three consequences before a first live run:

1. **Employee representatives.** Where the law or an agreement gives a works council, a staff committee or a union a say over tools that can monitor staff, consult them before the first run. Examples of such rules: Germany, BetrVG § 87(1) no. 6 (co-determination over technical devices able to monitor behaviour or performance); Switzerland, the Code of Obligations art. 328b (an employer processes staff data only as far as it concerns the job) and Ordinance 3 to the Labour Act art. 26 (no system whose purpose is to monitor behaviour); in the EU generally, GDPR art. 88 lets member states set stricter employment rules.
2. **Tell staff** what is collected, for which purpose, who sees what, and how long it is kept. The inventory below is written so it can be reused for that notice.
3. **Decide the purpose first** (cost allocation, licence planning, budget alerts) and use the narrowest audience that serves it. gold computes alerts and scenarios that enforce nothing; keep it that way unless the purpose and the consultation cover more.

## Roles: controller and processor

- **The adopter is the controller.** The organisation that runs meterline decides why and how the data is processed, and answers for it.
- **meterline is software, not a service.** It runs on the adopter's machine, sends nothing to its maintainer and has no telemetry. The maintainer receives no data and is neither controller nor processor.
- **A contractor who runs meterline for the adopter is a processor** and needs a processing agreement (GDPR art. 28; nLPD art. 9).
- **The providers** (Anthropic, GitHub, the operator of a LiteLLM proxy) already hold the usage data under the adopter's own contracts with them. meterline only pulls it back; it adds no new recipient at the provider side.

## Records of processing

The adopter's record of processing activities (GDPR art. 30; nLPD art. 12) needs an entry for meterline. Both laws exempt some small organisations; a register of staff that runs every day is regular processing, so check the exemption before relying on it. The entry can be filled from this file:

| Element | Where to find it here |
|---|---|
| Purposes | [What this data is](#what-this-data-is), point 3, and the Purpose column of the inventory. |
| Categories of persons | Staff and contractors who hold an account at a connected provider; for LiteLLM with `--end-users`, the end users an application names. |
| Categories of data | The inventory below. No special-category data is read or written. |
| Recipients | The Audience column of the inventory: the operator, central analysts, managers, each person, executives. |
| Transfers abroad | [Transfers](#transfers). |
| Retention | [Retention](#retention). |
| Security measures | [`THREAT-MODEL.md`](THREAT-MODEL.md), STRIDE table and attack tree. |

## DPIA

A data-protection impact assessment (GDPR art. 35; nLPD art. 22) is needed when processing is likely to be high risk. Systematic monitoring of staff is on most supervisory authorities' lists of such processing. Plan a DPIA before the first live run, and certainly before per-person figures go to managers or feed a decision about a person. [`THREAT-MODEL.md`](THREAT-MODEL.md) and this file are inputs to it. The design choices that reduce risk, to cite in it:

- persons appear in gold only as keyed pseudonyms; names and emails sit in one file, `delivery/directory.csv`, that no audience gets;
- a group figure is published only for at least `k` persons, with a suppression loop that a property test attacks;
- no file is sorted by a measure, and a person sees only their own figures and a band against their group;
- scenarios and alerts are computed, never enforced, and notify no one;
- every output is written readable by its owner only (files `0600`, folders `0700`).

## Personal-data inventory

Every file that holds a person's data, the columns that identify a person (directly, or through a key while `METERLINE_GOLD_KEY` exists), why it exists and who should see it. With `--parquet`, each CSV or JSONL file has a Parquet twin with the same columns. Field meanings: [`records.md`](records.md) for the records, [the gold spec](dev/meterline-gold-core-SPEC-v1.2-2026-10-15.md) section 6 for gold.

| Layer | File | Personal columns | Purpose | Audience |
|---|---|---|---|---|
| Bronze | `raw/<endpoint>/<window>/.../<pulled_at>.json.gz` | Whatever the provider returned: user ids, `email_address` (Anthropic), `api_key_id`, `account_id` and `service_account_id` (Anthropic Admin, see [the Admin connector](#the-anthropic-admin-connector)), `user_email` (LiteLLM `/user/list`), `user_login` (Copilot), and any field the connector does not map. The per-person folder names of LiteLLM and Copilot are a short unkeyed sha256 of the user id or login (16 hexadecimal digits). | Evidence of what the provider sent, and the only input of `rebuild`. | Operator only. |
| Bronze | `runs/<batch_id>.json`, `state.json` | No identifier of a person, except as listed here. The unkeyed labels above (16 hexadecimal digits) sit in the `raw_files` paths and in the `windows` entries (LiteLLM `user:...`, `end:...`; Copilot `person:...`). A shorter unkeyed sha256 of an id (12 hexadecimal digits) sits in `token_anomalies` (LiteLLM) and `feature_sum_mismatch` (Copilot). The text of a failure in `failures` can name a field the provider sent, which can look like an email (see [Error messages](#error-messages-that-name-an-identifier)). | What a run did, and where to resume. | Operator only. |
| Silver | `records/<kind>/<batch_id>.jsonl` | `actor_id`, `actor_email`, `actor_login`, `actor_kind`, `actor_deleted`; on seat assignments also `plan`, `assigned_at`, `last_active_at`, `last_authenticated_at`, `pending_cancellation_date`, `granted_by`; per-person usage, activity and cost measures. | One common record per actor, product, model and period, for gold or any other reader. | Operator and the gold build. |
| Silver (second copy) | `<data_root>/silver/records/<kind>/<connector>-<batch_id>.jsonl`, made only by `examples/03-daily-job/job.py` (`stage_silver`) | The same fields as the silver files it copies. | Gold reads one silver folder, so the example job copies every connector's records into it before each build. | Operator and the gold build. |
| Input | `org/people.csv`, `org/units.csv`, `org/memberships.csv` | `person_id`, `manager_id`, `head_person_id`, unit, function, level, cost centre, population, the attributes kept. | Who belongs where, so cost can be grouped. Written by the adopter. | Operator and the gold build. |
| Input | `org/identities.csv`, `org/directory.csv` | `person_id` with each provider `actor_id`, `actor_login`, `actor_email`; given and family name and email. | Link a provider account to a person; names for delivery. | Operator and the gold build. |
| Gold | `central/person_day.csv`, `central/person_day_activity.csv`, `central/identity_map.csv` | `person_key`, `actor_key` | Per-person usage by day, and which accounts were joined to whom. | Central analysts. |
| Gold | `central/person_month.csv`, `central/people.csv`, `central/named_exceptions.csv` | `person_key`, `manager_key` | Per-person month, the organisation as of each month, the rule hits. | Central analysts. |
| Gold | `central/scenario_persons.csv` | `person_key` | Who a shadow cap would have stopped. | Central analysts. |
| Gold | `central/alert_events.csv` | `subject_key` | Alert crossings, of a person or of a unit. | Central analysts. |
| Gold | `central/units.csv` | `head_key` | The unit tree and its heads. | Central analysts. |
| Gold | `central/groups_month.csv`, `central/comparison_groups.csv`, `central/costs_month.csv`, `central/seats_month.csv`, `central/pools_month.csv`, `central/reconciliation_month.csv`, `central/data_quality.csv`, `central/scenario_summary.csv`, `central/scenario_units.csv` | none (group, unit and organisation figures) | Cost and usage of groups, seats, pools and data quality. | Central analysts. |
| Gold | `delivery/directory.csv` | `person_key`, `given_name`, `family_name`, `email` | Names and addresses for a sender, which is not built. | Nobody yet. Never served. |
| Gold | `manager/<manager_key>/groups_month.csv` | none | The unit cells a manager heads, hidden below `k` (a `hidden` column, never the reason). The folder name is the manager's key. | That manager. |
| Gold | `manager/<manager_key>/named_exceptions.csv` | `person_key`, `manager_key` | Count-valued rule hits about the manager's reports: a manager needs to know who. | That manager. |
| Gold | `person/<person_key>/person_day.csv` | `person_key`, `actor_key` | The person's own usage by day. | That person. |
| Gold | `person/<person_key>/person_month.csv` | `person_key` | The person's own month. It carries no other person's key: no manager key. | That person. |
| Gold | `person/<person_key>/comparison.csv` | none | The person's own cost and a band against their comparison group. The folder name is the person's key. | That person. |
| Gold | `exec/groups_month.csv`, `exec/costs_month.csv`, `exec/seats_month.csv`, `exec/pools_month.csv`, `exec/scenario_summary.csv` | none | Group and organisation figures down to the exec depth, with small populations hidden. | Executives. |
| Gold | `manifest.json` | Every person and manager key, through the output paths it lists. | Inputs, outputs and their hashes, suppression counts. | Central analysts only; never delivered. |

### The Anthropic Admin connector

`meterline-anthropic-admin` reads the usage and cost reports of the Anthropic Admin API. Its identifiers are not persons by name, but some of them can lead to one.

- **`api_key_id`** becomes the silver `actor_id`, with `actor_kind` `api_key`. A row that names no key gets `actor_kind` `unassigned`. A key can belong to one person, so treat the id as personal data. In gold it is an `actor_key`, and a row of `org/identities.csv` can map it to a `person_id`.
- **`actor_email` and `actor_login`** are always empty in Admin records, because the connector reads no name or email.
- **`workspace_id`, `account_id` and `service_account_id`** stay in the raw pages as the provider sent them, and none of them is copied to a record. The connector uses `workspace_id` to check and to filter. It accepts the two account fields and does not read them. The raw page header also holds the workspace id you filtered by, if any. Whether one of these identifies a person depends on how your organisation names its accounts and workspaces, so treat `account_id` as personal data.
- **The cost report's `description`** is free text from the provider. It stays in the raw pages and never reaches a record (`test_prompt_content_never_written_admin`).

Erasing a person from Admin data therefore means the raw pages (steps 3 and 4 below), and a row of `org/identities.csv` if you mapped their key.

### Why `actor_email` is kept in raw and silver

- **Raw is evidence.** A raw page is kept exactly as the provider sent it, so `rebuild` reproduces the published records byte for byte and any figure can be traced to the page it came from. Removing a field from raw would break both.
- **Silver does not need it to join a person.** Gold matches a silver account to a person by a keyed hash (HMAC) of `provider`, `actor_kind` and `actor_id`, and looks for the same hash among the `actor_id` values of `org/identities.csv`. The `method` column of that file (for example `email`) records how you found the match. It does not change the match. A LiteLLM user id is often an email, so an `actor_id` can be one. Gold reads the `actor_email` and `actor_login` of silver for one purpose: the build refuses to write any output file that holds one of them (the leak check). Removing `actor_email` from every silver record changed no gold file except `manifest.json`, which holds the hash of the input (checked on 2026-10-30).
- **Silver is not an audience layer.** Records go only to the operator and the gold build. gold never copies an email from silver into an output: the only emails it writes are those of `org/directory.csv`, into `delivery/directory.csv`.

Gold never needs the email of silver. The record format has the field and the provider sends it, so the connector keeps it. If you do not need it for anything else, shorten the retention of raw and silver.

### Error messages that name an identifier

Logs, manifests and the messages a run prints carry counts, statuses and file names. Three known exceptions can name an identifier:

- **The unknown-field report.** When a response holds a field the connector does not know, every connector names it in the `unknown_fields` list of `runs/<batch_id>.json` and in one warning on stderr, for example `data[].results[].<name>`. A field name that looks like an email would appear there. A key under a map whose keys are data is written `*`. The report names fields, never their values, and the run publishes as usual.
- **The Admin check on workspaces.** When one API key shows usage in two workspaces on the same day for the same model, the run fails and the message names the `api_key_id`. It goes to stderr and to `failures` in the manifest.
- **The gold identity-overlap error.** When two rows of `org/identities.csv` claim the same account for overlapping dates, the message names the provider, the kind and the account id, which can be an email. It goes to the stderr of your own run and comes from your own file.

Treat the stderr of a run, the `failures` of a manifest and its `unknown_fields` as sensitive too.

### Fields that must never be added

A change that adds any of these to a record, a manifest, a log or a gold file is a privacy bug (see the bug bar in [`THREAT-MODEL.md`](THREAT-MODEL.md)):

- prompt or response text, file names, code, or any other content of what a person asked or got (tests `test_prompt_content_never_written_*`);
- a name or an email in any gold file other than `delivery/directory.csv` (test `test_done_check_G12_*`);
- another person's key or figure in a person folder (tests `test_review_decision9_person_files.py`, `test_done_check_G15_*`);
- IP addresses, device identifiers or locations (no test yet; the policy's `people.attrs_keep` can carry an attribute such as `location` from your organisation files into `central/people.csv`, which is your choice and not a field read from a provider);
- times finer than a UTC day in gold, with raw keeping what the provider sends (no test yet);
- a ranking or an ordering of persons by a measure (test `test_done_check_G13_sorted_by_key_not_by_measure`);
- health, absence, pay, performance ratings or any other assessment of a person (no test yet).

## Retention

meterline deletes nothing on its own. Raw pages are never replaced or deleted by a connector, record batches accumulate, and every gold build is a new folder. So the adopter sets a retention period per layer and deletes by hand or by a scheduled job. Write the periods into the record of processing.

| Layer | What meterline keeps | What decides how long | How to delete |
|---|---|---|---|
| `raw/` (bronze) | Every page of every run. | Only `rebuild` needs it: keep it while a month can still be corrected at the provider and re-checked. | Delete a run's raw pages and its `runs/<batch_id>.json` together (a rebuild refuses a published run whose raw files are missing), then run `rebuild`. |
| `runs/`, `state.json` | One manifest per run; one state file. | Small. They hold unkeyed labels and may hold a failure message that names an identifier. `state.json` must stay while the connector runs, or the next run starts over. | With the raw pages they describe. |
| `records/` (silver) | Every batch; readers take the latest per key. | As long as the purpose needs per-person history (a year-on-year comparison needs about 13 months). | `rebuild` re-creates `records/` from the raw pages that remain, so trimming raw trims silver. |
| `<data_root>/silver/` (only if you run `examples/03-daily-job`) | A second copy of every connector's records, made again before each gold build. | The same as `records/`. | Delete the folder. The next run of the job makes it again from `records/`. |
| Organisation files | Whatever the adopter exports from its directory. | Refresh them each build; old exports are no longer needed once a build has used them. | Delete old exports. |
| `builds/` (the job's gold folders) | One folder per run, `builds/<date>` or `builds/<date>-<n>`, never overwritten and never deleted by the job: it grows by one build per run. With `central/` and `manifest.json` in each. | The purpose: cost allocation for a closed month rarely needs more than the period the finance rules require. | Delete old builds by hand; the build a `frozen.json` entry names may go too, because freezing never reads it again. |
| `frozen/`, `frozen.json` | One folder per frozen month (its rows, as CSV, with `SHA256SUMS`) and the entry that says where it came from. Kept. | As long as the delivered months are needed; they are the history of the manager and person folders. | Delete a month's folder and its entry together, never one of them. |
| `published`, `published-*` | The link the adopter serves, and the folder it points at plus the previous one. | One run. | The job removes the older ones. |
| `central/` and `manifest.json` of a gold folder you build by hand | Each build's folder, until deleted. | The purpose: as above. | Delete superseded builds; keep one build per delivered month. |
| `delivery/` | `directory.csv` in each build. | Nothing uses it yet. | Delete it with its build, or right after a build if no sender reads it. |
| `manager/`, `person/`, `exec/` | Each build's audience folders, plus every copy delivered or served. Of the `published` folder, only the people and managers of the latest build get a folder: a person who left, or a manager who no longer manages, has no published folder, and their history stays in `central/` and `frozen/`. | As short as the audience needs; deliver each complete month once. | Delete the served copy when the next month replaces it; ask recipients to do the same. |
| Backups | Whatever the backup system keeps. | The backup system's own retention. | The same periods as above, or the backup expiry if it is shorter. |

## Delivery rule

A month is refreshed on every published run until it is frozen, which happens once every source has final data for it and `freeze_after_days` have passed, or at the latest `freeze_after_days + freeze_cap_extra_days` days after its end at the next run; from then on it is delivered once and does not change, except that an erased person's rows are removed, and after a change of the hashing key manager and person folders show only months frozen under the new key. Serve only the `published` folder, never `builds/` or `frozen/`, and give managers and executives only its latest state; keeping earlier states is the adopter's choice, not the tool's. A reader who keeps every refresh of an in-progress month can sometimes learn one person's usage between two refreshes, one person's month-to-date amount when a correction or an organisation change moves that person, a cell published in one refresh and hidden in the next, or a named exception that appears and then disappears. The adopter accepts this risk and has it validated by its data protection officer.

## Erasure

To erase one person (a request under GDPR art. 17 or nLPD art. 32, or the end of the retention period for someone who left). There is no erase command yet; these steps are manual, and the rule of step 7 is exercised by a test on invented data (`test_the_manual_erasure_procedure_leaves_no_trace_and_exec_untouched`). Work on a copy first, and keep the copy until the checks of step 8 pass.

1. **Stop scheduled runs** and gather the person's identifiers from `org/identities.csv` and `org/directory.csv`: `person_id`, each provider `actor_id`, `actor_login` and `actor_email`.
2. **Erase at the provider too, or expect the data back.** meterline pulls what the provider still reports. A `daily` run reads only recent days, so erased history stays erased unless a `backfill` re-reads those days; a person who still uses the tool reappears in the next run.
3. **Bronze (`raw/`).** For each connector data directory, list the pages that name the person:
   ```sh
   find raw -name '*.json.gz' -exec zgrep -l -F -e 'ID' -e 'LOGIN' -e 'EMAIL' {} +
   ```
   Rewrite each listed page without that person's entries, keeping its file name and gzip format. LiteLLM and Copilot also keep pages that hold only that person, under a folder named by an unkeyed label; compute it with
   ```sh
   python3 -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:16])" 'USER_ID'
   ```
   (LiteLLM: the user id as the proxy returns it; Copilot: the login in lower case.) Keep that 16-digit label: step 5 and step 8 need it. To remove the label as well, delete that folder and remove its paths from the `raw_files` list of each `runs/<batch_id>.json` that names them. The label is also in the `windows` list of those manifests, which step 5 covers.
4. **Silver (`records/`).** Run the connector's `rebuild` (add `--parquet` if you keep Parquet). It re-creates `records/` from the edited pages and replaces the old tree only when every run passes its checks; if a check fails it changes nothing and says which run. If you run `examples/03-daily-job/job.py`, it also keeps a second copy of the records in `<data_root>/silver/` (made by `stage_silver`, files `0600`, folders `0700`). `rebuild` does not touch that copy. Delete the folder now; the next run of the job makes it again from the rebuilt `records/`.
5. **`runs/` and `state.json`.** By design they hold no name or email of a person, but they can hold the person's unkeyed labels and a failure message. Remove from each `runs/<batch_id>.json` the entries of `windows` that end in the person's 16-digit label from step 3. Remove the entries of `token_anomalies` (LiteLLM) and `feature_sum_mismatch` (Copilot) that end in the person's 12-digit hash: the first 12 digits of the same sha256, taken of the LiteLLM actor id or of the Copilot numeric user id. Remove any `failures` entry that names an identifier of the person. `state.json` can hold the folder label in its resume block only while a run is unfinished.
6. **Organisation files.** Remove the person's rows from `people.csv`, `identities.csv`, `directory.csv` and `memberships.csv`. If they manage others or head a unit, set the new manager or head first, or `people.csv` and `units.csv` will no longer agree.
7. **Gold.** Erasing one person never changes the hashing key and never recomputes the group figures of a month that was already delivered; rotation of `METERLINE_GOLD_KEY` stays for a compromised key, and this step does not rotate it. With the layout `meterline-job` writes, with the job stopped: in `builds/`, delete every folder, since a frozen month never reads its source build again (the next run builds again from the edited silver and organisation files); in `frozen/`, for each month remove every row whose `person_key` is the person's key from `central/` and from every `manager/*/named_exceptions.csv`, delete `person/<key>/` and `manager/<key>/`, empty every `manager_key` cell that holds the key in the rows of `central/`, rewrite that month's `SHA256SUMS`, and add an `erasures` entry to its entry in `frozen.json`, leaving `groups_month` and every `exec/` file byte for byte as they are; then delete every `published-*` folder and the `published` link, and the next run makes them again from the cleaned `frozen/`. Copies already handed to a reader keep the old pseudonymous key, which cannot be linked to the person without the hashing key; recall them if you can. Store the key apart from the outputs (a secret store, not the gold folder or the data directory) and never in git. If you rotate the key because it was compromised, the history of manager and person folders restarts at the rotation month and older months stay central only.
8. **Check.** Search every data directory (the `<data_root>/silver/` folder included, if the example job made one), every remaining gold folder and the organisation files for each identifier, for the person's labels and for the person's old `person_key`. `LABEL16` and `LABEL12` are the 16-digit label of step 3 and the 12-digit hash of step 5:
   ```sh
   grep -r -l -F -e 'ID' -e 'LOGIN' -e 'EMAIL' -e 'LABEL16' -e 'LABEL12' -e 'pk_OLDKEY' .
   find . -name '*.gz' -exec zgrep -l -F -e 'ID' -e 'LOGIN' -e 'EMAIL' {} +
   find . -name 'LABEL16'
   ```
   All three must print nothing. Then erase backups by their own procedure and record the erasure.

## Transfers

meterline **pulls** data; it pushes none. The only network destinations are the provider APIs the adopter configures: `api.anthropic.com`, `api.github.com` and GitHub's report download host, and the adopter's own LiteLLM proxy (`LITELLM_BASE_URL`). What goes out is the credential and the query: dates, and, for per-person calls, the person's LiteLLM user id or GitHub login, which that provider already holds. gold, the record format and the simulator open no network connection.

**A future email sender changes this.** A sender (for example a cloud mail service) would send names, addresses and per-person figures to a new recipient, possibly in another country. Before one is built or switched on: sign a processing agreement with the mail provider (GDPR art. 28; nLPD art. 9); assess the transfer if the provider or its servers sit outside the adopter's country (GDPR chapter V; nLPD art. 16 and 17); add it to the record of processing and the DPIA; and update [`THREAT-MODEL.md`](THREAT-MODEL.md) (its first update trigger).
