"""What the gold datasets mean: the one table behind the dataset schemas and the dataset reference page.

Column names and types are not here: they come from ``write.SCHEMAS``, the table the CSV writer formats with. This
module holds only what the writer does not know: a one-line description and unit per column, which columns are never
empty, the joins between datasets, which audience folders carry which form of a dataset, which columns the ``hidden``
marker covers, and the rule for which cost column to sum. ``meterline_gold.contract`` combines the two into
``docs/schemas/gold-dataset-*.schema.json`` and ``docs/reference/gold-datasets.md``; a test fails when a column of
``write.SCHEMAS`` has no entry here. The meaning of each column is the gold spec, sections 6 to 8.
"""

UNITS = ("currency", "tokens", "count", "ratio", "date", "month", "key", "text", "flag")

# the unit a column kind gets unless the column name says otherwise (maps take the unit of their values)
UNIT_BY_KIND = {"money": "currency", "quantity": "count", "count": "count", "ratio": "ratio", "date": "date",
                "bool": "flag", "text": "text", "ntext": "text", "nx": "text", "dq": "text", "json": "text",
                "map_count": "count", "map_money": "currency"}
KEY_COLUMNS = frozenset({
    "person_key", "manager_key", "head_key", "actor_key", "unit_id", "parent_id", "cell_id", "parent_cell_id",
    "cell_kind", "cell_key", "compare_cell", "provider_account_id", "rule_id", "scenario_id", "subject_key", "key"})
UNIT_BY_NAME = {"month": "month", "valid_from": "date", "valid_to": "date", "eval_day": "date"}

# column -> description; a dataset that means something else by a name overrides it in OVERRIDES
COLUMNS = {
    "schema_version": "GOLD_SCHEMA_VERSION of the build that wrote the file; the same on every row.",
    "month": "Calendar month, YYYY-MM.",
    "day": "Calendar day, YYYY-MM-DD (UTC).",
    "provider": "Provider the usage or seats came from, as the silver records name it.",
    "provider_account_id": "Provider account label from the silver records; empty only where seats are matched on provider alone.",
    "person_key": "Pseudonymous person key: a keyed hash of the person id, stable for one hashing key.",
    "manager_key": "person_key of the person's manager on the attribution day.",
    "head_key": "person_key of the head of the unit.",
    "actor_key": "Keyed hash of the provider's own actor id, for an actor that is not resolved to a person.",
    "actor_kind": "What the actor is: user, proxy user, end user, service account, API key or unassigned; mixed if merged records differ.",
    "identity_status": "How the actor was resolved: resolved, non_person or unresolved.",
    "identity_method": "How the identity map matched the actor; mixed if merged records differ.",
    "method": "How the identity map matches the actor: login, email, id or non_person.",
    "product": "Product within the provider; empty when the provider has none.",
    "model": "Model name as the provider reports it; empty when the record has none.",
    "model_tier": "Tier of the model in the price list, such as premium; empty for an unknown model.",
    "requests": "Number of requests.",
    "tokens_input_uncached": "Input tokens not read from or written to a cache.",
    "tokens_cache_read": "Input tokens read from a cache.",
    "tokens_cache_write": "Input tokens written to a cache.",
    "tokens_cache_write_5m": "Cache-write tokens in the 5-minute cache.",
    "tokens_cache_write_1h": "Cache-write tokens in the 1-hour cache.",
    "tokens_input_total": "All input tokens: uncached, cache read and cache write.",
    "tokens_output": "Output tokens.",
    "credits": "AI credits consumed, for providers that meter in credits.",
    "cost_source": "Cost as the provider reports it, in cost_source_currency and on the cost_source_basis.",
    "cost_source_basis": "What cost_source is: billed, consumption or estimate; mixed if the rows differ.",
    "cost_source_currency": "ISO 4217 currency of cost_source.",
    "cost_ref": "Reference cost: usage priced from the price list you supplied, in the reference currency.",
    "cost_ref_status": "How cost_ref was priced: priced, included, unpriced or unknown_model.",
    "cost_ref_by_provider": "JSON object of provider to the person's cost_ref at that provider.",
    "is_final": "True when the provider will no longer revise the figure.",
    "source_records": "Number of silver records merged into the row.",
    "model_provider": "Provider that served the model, when it differs from provider (a proxy such as litellm routes calls "
                      "to a provider); mixed if the merged records differ; empty when the record does not say.",
    "overlap_cost_ref": "Part of usage_cost_ref that a proxy routed to a provider whose own connector is also in this build: "
                        "those calls are in both providers' rows. 0 for every other row; empty where the row is hidden.",
    "sessions": "Number of sessions.",
    "messages": "Number of messages.",
    "suggestions_shown": "Suggestions shown to the person.",
    "suggestions_accepted": "Suggestions the person accepted.",
    "lines_added": "Lines of code added.",
    "lines_removed": "Lines of code removed.",
    "edits_accepted": "Edits the person accepted.",
    "edits_rejected": "Edits the person rejected.",
    "commits": "Commits made.",
    "pull_requests": "Pull requests opened.",
    "month_status": "complete for a finished month, running for the month in progress.",
    "attribution_day": "Last employed day of the month: the day whose organisation row attributes the person.",
    "unit_id": "Organisation unit id from the organisation files.",
    "function": "Function of the person or unit, from the organisation files.",
    "level": "Level of the person, from the organisation files.",
    "cost_center": "Cost centre of the person or unit, from the organisation files.",
    "population": "Population label of the person, from the organisation files.",
    "employed_days": "Days of the month on which the person is employed.",
    "active_days": "Days of the month with any usage or activity row.",
    "active_days_by_tool": "JSON object of tool (provider or provider/product) to the person's active days with it.",
    "rows_unpriced": "Number of the person's person_day rows that could not be priced (cost_ref_status unpriced).",
    "rows_unknown_model": "Number of the person's person_day rows whose model is not in the price list.",
    "premium_share": "Share of the person's cost_ref that went to premium-tier models.",
    "cache_rate": "Cache-read tokens divided by all input tokens, where both are known.",
    "intensity_band": "Band of the person's month cost from the policy's bands.",
    "projected_cost_ref": "Month-end projection of cost_ref for a running month; empty when complete.",
    "seat_source": "Where the seat holders come from: assignments or identities.",
    "eval_day": "Day on which seats are evaluated for the month.",
    "seats_billed": "Distinct seat holders on any day of the month.",
    "seats_on_eval_day": "Seat holders on the evaluation day.",
    "active_seats": "Seat holders with usage or activity of the provider during the month.",
    "idle_seats": "Seat holders on the evaluation day with no usage in the idle window.",
    "seats_reported_max": "Highest seat count the provider itself reported in the month.",
    "seat_price": "Price per seat per month from the price list.",
    "seat_cost": "Seat fee: seats_billed times seat_price. A fee, not usage.",
    "currency": "ISO 4217 reference currency of the row.",
    "included_per_seat": "Credits included per seat each month.",
    "pool_credits": "Credit pool: seats_billed times included_per_seat.",
    "gross_credits": "Credits billed in the month, before the pool is applied.",
    "pool_used_credits": "Credits covered by the pool.",
    "overage_credits": "Credits beyond the pool.",
    "overage_amount": "Billed amount for the credits beyond the pool.",
    "pool_used_share": "pool_used_credits divided by pool_credits.",
    "budget_amount": "Budget for the provider and provider account from the policy.",
    "budget_state": "none without a budget, crossed once overage passed it, otherwise within.",
    "budget_crossed_on": "First day on which the overage passed the budget.",
    "usage_cost_source": "Sum of cost_source over the provider's usage rows.",
    "usage_cost_ref": "Sum of cost_ref over the provider's usage rows.",
    "total_cost_ref": "usage_cost_ref plus seat_cost.",
    "cost_basis_label": "Plain-language label of the basis of usage_cost_source.",
    "invoice_usage": "Invoiced usage amount from the invoice file, if one was given.",
    "actor_cost_source": "Sum of cost_source allocated to actors.",
    "org_cost_source": "Organisation-wide cost the provider reports (OrgTotal records).",
    "org_cost_basis": "cost_basis of the organisation totals; mixed if they differ.",
    "unallocated_cost_source": "org_cost_source minus actor_cost_source; empty when the bases differ.",
    "unreconciled_share": "unallocated_cost_source divided by org_cost_source.",
    "actor_credits": "Credits allocated to actors.",
    "org_credits": "Organisation-wide credits the provider reports.",
    "unallocated_credits": "org_credits minus actor_credits.",
    "ref_vs_source_gap": "cost_ref minus actor_cost_source; may be negative.",
    "ref_vs_source_share": "Absolute ref_vs_source_gap divided by actor_cost_source.",
    "invoice_usage_gap": "invoice_usage minus org_cost_source.",
    "invoice_seats": "Invoiced seat amount from the invoice file, if one was given.",
    "invoice_seats_gap": "invoice_seats minus seat_cost.",
    "metric": "Name of the data-quality metric.",
    "value": ("Value of the metric, written in the metric's own type (count, money, ratio or date). "
              "The metric sets the type, not the column: read metric before you cast or sum."),
    "cell_id": "Id of the grid cell: org:, unit:<unit_id> or attr:<attribute>=<value>|...",
    "cell_kind": "Kind of grid cell: org, unit or attr.",
    "cell_key": "Cell id without its kind prefix.",
    "depth": "Depth of the cell in the grid: 0 for the organisation, 1 for a root unit.",
    "parent_cell_id": "cell_id of the parent cell; empty for the organisation cell.",
    "suppressed": "True when the privacy algorithm withholds the cell's figures.",
    "suppression_reason": "Why a cell is suppressed: k, differencing or secondary.",
    "persons": "Number of persons in the cell.",
    "active_persons": "Persons with at least one active day; withheld when it would expose a few people.",
    "p50_cost_ref": "Median cost_ref per person in the cell (nearest rank).",
    "p75_cost_ref": "75th percentile of cost_ref per person in the cell.",
    "p90_cost_ref": "90th percentile of cost_ref per person in the cell.",
    "p99_cost_ref": "99th percentile of cost_ref per person in the cell.",
    "top_decile_share": "Share of the organisation's cost_ref spent by its top tenth of persons; organisation cell only.",
    "compare_cell": "Comparison cell id: the compare_by attributes and their values.",
    "own_cost_ref": "The person's own month cost_ref.",
    "comparison_band": "Where the person sits against the published cut points of their comparison cell.",
    "hidden": "True when a privacy rule withheld at least one covered value of the row; empty values then mean withheld.",
    "scenario_id": "Id of the shadow scenario from the policy.",
    "cap": "The person's spending cap in the scenario.",
    "stopped_on": "First day the position passed the cap or envelope; empty if it never did.",
    "excess": "How far the position at the end of the month is over the cap or envelope; 0.00 if not over.",
    "top_ups": "Number of top-up amounts needed to cover the excess.",
    "top_up_total": "top_ups times the scenario's top-up amount.",
    "envelope": "The unit's spending envelope in the scenario.",
    "position": "Month-to-date cost_ref of the unit at the end of the month.",
    "persons_stopped": "Persons whose position passed their cap.",
    "persons_excess": "Sum of the persons' excess.",
    "persons_top_up_total": "Sum of the persons' top_up_total.",
    "units": "Number of units with an envelope in the scenario.",
    "units_stopped": "Units whose position passed their envelope.",
    "units_excess": "Sum of the units' excess.",
    "units_top_up_total": "Sum of the units' top_up_total.",
    "event_type": "breaker, spike, scenario_alert or proxy_overlap.",
    "subject_kind": "person, unit or proxy.",
    "subject_key": "person_key or unit_id, as subject_kind says; for proxy, the proxy's provider and the provider it routes "
                   "to, joined by a slash.",
    "threshold": "Limit the event crossed.",
    "trailing_sum": "Sum of the day costs in the spike's trailing window; empty on other events.",
    "trailing_days": "Days in the spike's trailing window; empty on other events.",
    "audience": "Audience the rule delivers to: central or manager.",
    "rule_id": "Id of the named-exception rule from the policy.",
    "condition": "What the rule tests: month_cost_ref_above, breaker_events_at_least or spike_events_at_least.",
    "valid_from": "First day the row is valid.",
    "valid_to": "Last day the row is valid; empty if open.",
    "attrs": "JSON object of the person's kept attributes.",
    "dimension": "Dimension of the unit tree the unit belongs to.",
    "key": "Short key of the unit within its dimension.",
    "name": "Display name of the unit.",
    "kind": "Kind of the unit, such as team or division.",
    "parent_id": "unit_id of the parent unit; empty for a root.",
    "given_name": "Given name of the person.",
    "family_name": "Family name of the person.",
    "email": "Email address of the person.",
}

# (dataset, column) -> the cookbook section the reference page links from that column (a trap in how the column is read)
TRAPS = frozenset({("data_quality", "value"), ("units", "valid_from"), ("units", "valid_to")})
TRAPS_LINK = "../how-to/finops-kpis.md#column-traps"

# (dataset, column) -> the cookbook KPI the reference page links from that column: the column is a share that is not the
# reference unallocated share (the cookbook KPI one_unallocated_share, whose heading is the anchor)
REFERENCE_SHARE = frozenset({("data_quality", "metric"), ("reconciliation_month", "unreconciled_share")})
REFERENCE_SHARE_LINK = "../how-to/finops-kpis.md#reference-unallocated-share"

# (dataset, column) -> description, where a dataset means something else by a name
OVERRIDES = {
    ("person_day", "cost_ref"): "Reference cost of the row; empty when any merged record could not be priced.",
    ("person_day", "person_key"): "Pseudonymous person key; empty when the actor is not resolved to a person.",
    ("person_day", "actor_key"): "Keyed hash of the provider's actor id; empty for a resolved person with one account.",
    ("person_month", "cost_ref"): "Sum of the person's cost_ref over the month and all providers; 0.00 when nothing is priced.",
    ("person_month", "requests"): "Sum of the person's requests in the month.",
    ("identity_map", "person_key"): "Pseudonymous person key; empty when method is non_person.",
    ("identity_map", "method"): "How the identity map matches the actor: login, email, id or non_person.",
    ("groups_month", "cost_ref"): "Sum of cost_ref over the cell's persons.",
    ("groups_month", "persons"): "Number of attributed persons in the cell.",
    ("groups_month", "active_days"): "Sum of the cell's persons' active days.",
    ("comparison_groups", "persons"): "Number of persons in the comparison cell.",
    ("scenario_persons", "cost_ref"): "The person's position: month-to-date cost_ref at the end of the month.",
    ("scenario_summary", "persons"): "Number of persons with a cap in the scenario.",
    ("alert_events", "threshold"): "Limit the event crossed, in the reference currency.",
    ("named_exceptions", "threshold"): "The rule's threshold, as written in the policy.",
    ("named_exceptions", "value"): "The person's value for the rule: a cost_ref or an event count, as condition says.",
    ("named_exceptions", "manager_key"): "person_key of the person's manager; empty if none.",
    ("named_exceptions", "person_key"): "person_key of the person the rule selected.",
    ("data_quality", "month"): "Calendar month, YYYY-MM; empty on the freshness metrics.",
    ("units", "function"): "Function of the unit, from the organisation files.",
    ("units", "valid_from"): "First day the row is valid. A unit has one row per validity period: take the row valid on the day.",
    ("units", "valid_to"): "Last day the row is valid; empty if open. A join on unit_id alone repeats people: check both ends of the range.",
    ("seats_month", "provider_account_id"): "Provider account label; empty when the provider has none or several.",
    ("seats_month", "is_final"): "True when the month is complete. The seat fee of a running month is the full month's fee.",
    ("costs_month", "is_final"): "True when the month is complete and every usage row of it is final.",
    ("pools_month", "is_final"): "True when the month is complete and the provider will no longer revise the billing rows.",
    ("alert_events", "value"): "Day cost or position that fired the event; for proxy_overlap, the amount counted twice.",
    ("reconciliation_month", "unreconciled_share"): (
        "unallocated_cost_source divided by org_cost_source: the organisation remainder only. It is not the reference "
        "unallocated share (the cookbook KPI one_unallocated_share), which also counts the cost of non-person and "
        "unresolved actors."),
    ("data_quality", "metric"): (
        "Name of the data-quality metric. The metric unallocated_share is the not-a-person cost over the reference "
        "cost (cost_ref): it is not the reference unallocated share (the cookbook KPI one_unallocated_share), which "
        "is the one to report."),
}

# the columns each dataset never leaves empty (the rest may be empty); a test checks them against real builds
NEVER_EMPTY = {
    "people": "person_key valid_from unit_id population",
    "units": "unit_id valid_from dimension key",
    "identity_map": "provider method actor_kind valid_from",
    "directory": "person_key",
    "person_day": "day month provider provider_account_id identity_status actor_kind cost_ref_status is_final source_records",
    "person_day_activity": "day month provider provider_account_id identity_status source_records",
    "person_month": "month person_key month_status attribution_day unit_id employed_days active_days "
                    "active_days_by_tool cost_ref cost_ref_by_provider rows_unpriced rows_unknown_model "
                    "intensity_band is_final",
    "seats_month": "month provider seat_source eval_day seats_billed seats_on_eval_day active_seats idle_seats currency "
                   "month_status is_final",
    "pools_month": "month provider seats_billed pool_credits budget_state currency is_final month_status",
    "costs_month": "month provider currency usage_cost_ref total_cost_ref overlap_cost_ref month_status is_final",
    "reconciliation_month": "month provider provider_account_id currency",
    "data_quality": "provider provider_account_id metric value",
    "groups_month": "month cell_id cell_kind depth",
    "comparison_groups": "month compare_cell persons",
    "comparison": "month compare_cell own_cost_ref",
    "scenario_persons": "scenario_id month person_key cap cost_ref excess",
    "scenario_units": "scenario_id month unit_id envelope position excess",
    "scenario_summary": "scenario_id month persons persons_stopped persons_excess units units_stopped units_excess",
    "alert_events": "day event_type subject_kind subject_key value threshold",
    "named_exceptions": "month rule_id audience person_key condition threshold value",
}

# ---- the files: (file stem, write.SCHEMAS name, dataset, audiences that read this form) -----------------------
# The file name is gold-dataset-<stem>.schema.json. Where an audience's form has other columns than the central
# one, the stem starts with that audience; two audiences that share a form each get their own file.
VARIANTS = [
    ("people", "people", "people", ("central",)),
    ("units", "units", "units", ("central",)),
    ("identity_map", "identity_map", "identity_map", ("central",)),
    ("directory", "directory", "directory", ("delivery",)),
    ("person_day", "person_day", "person_day", ("central", "person")),
    ("person_day_activity", "person_day_activity", "person_day_activity", ("central",)),
    ("person_month", "person_month", "person_month", ("central",)),
    ("person-person_month", "person_month_person", "person_month", ("person",)),
    ("seats_month", "seats_month", "seats_month", ("central",)),
    ("exec-seats_month", "exec_seats_month", "seats_month", ("exec",)),
    ("pools_month", "pools_month", "pools_month", ("central",)),
    ("exec-pools_month", "exec_pools_month", "pools_month", ("exec",)),
    ("costs_month", "costs_month", "costs_month", ("central",)),
    ("exec-costs_month", "exec_costs_month", "costs_month", ("exec",)),
    ("reconciliation_month", "reconciliation_month", "reconciliation_month", ("central",)),
    ("data_quality", "data_quality", "data_quality", ("central",)),
    ("groups_month", "groups_month", "groups_month", ("central",)),
    ("exec-groups_month", "groups_month_marked", "groups_month", ("exec",)),
    ("manager-groups_month", "groups_month_marked", "groups_month", ("manager",)),
    ("comparison_groups", "comparison_groups", "comparison_groups", ("central",)),
    ("comparison", "comparison", "comparison", ("person",)),
    ("scenario_persons", "scenario_persons", "scenario_persons", ("central",)),
    ("scenario_units", "scenario_units", "scenario_units", ("central",)),
    ("scenario_summary", "scenario_summary", "scenario_summary", ("central",)),
    ("exec-scenario_summary", "exec_scenario_summary", "scenario_summary", ("exec",)),
    ("alert_events", "alert_events", "alert_events", ("central",)),
    ("named_exceptions", "named_exceptions", "named_exceptions", ("central", "manager")),
]

# audience -> folder pattern that holds the files of that audience
FOLDER = {"central": "central/", "exec": "exec/", "manager": "manager/{manager_key}/",
          "person": "person/{person_key}/", "delivery": "delivery/"}

# the columns the hidden marker covers, per file stem (spec 7.6.2): empty there on a hidden = true row means withheld
HIDDEN_COVERED = {
    "exec-costs_month": ("usage_cost_source", "cost_source_basis", "usage_cost_ref", "total_cost_ref", "overlap_cost_ref"),
    "exec-pools_month": ("gross_credits", "pool_used_credits", "overage_credits", "overage_amount",
                         "pool_used_share", "budget_state", "budget_crossed_on"),
    "exec-seats_month": ("active_seats", "idle_seats"),
    "exec-scenario_summary": ("persons_stopped",),
    "comparison": ("comparison_band",),
}

# column -> the dataset columns it can be joined to (a join key, not a database constraint)
_THREE = ("month", "provider", "provider_account_id")
JOINS = {
    "people": {"unit_id": ["units.unit_id"], "manager_key": ["people.person_key"]},
    "units": {"parent_id": ["units.unit_id"], "head_key": ["people.person_key"]},
    "identity_map": {"person_key": ["people.person_key"]},
    "directory": {"person_key": ["people.person_key"]},
    "person_day": {"person_key": ["people.person_key"], "actor_key": ["identity_map.actor_key"]},
    "person_day_activity": {"person_key": ["people.person_key"], "actor_key": ["identity_map.actor_key"]},
    "person_month": {"person_key": ["people.person_key"], "unit_id": ["units.unit_id"],
                     "manager_key": ["people.person_key"]},
    "seats_month": {c: ["costs_month." + c] for c in _THREE},
    "pools_month": {c: ["seats_month." + c] for c in _THREE},
    "costs_month": {c: ["reconciliation_month." + c] for c in _THREE},
    "reconciliation_month": {c: ["costs_month." + c] for c in _THREE},
    "data_quality": {"provider": ["costs_month.provider"], "provider_account_id": ["costs_month.provider_account_id"]},
    "groups_month": {"parent_cell_id": ["groups_month.cell_id"], "cell_key": ["units.unit_id"]},
    "comparison_groups": {"compare_cell": ["comparison.compare_cell"]},
    "comparison": {"compare_cell": ["comparison_groups.compare_cell"]},
    "scenario_persons": {"person_key": ["people.person_key"], "scenario_id": ["scenario_summary.scenario_id"],
                         "month": ["scenario_summary.month"]},
    "scenario_units": {"unit_id": ["units.unit_id"], "scenario_id": ["scenario_summary.scenario_id"],
                       "month": ["scenario_summary.month"]},
    "scenario_summary": {"scenario_id": ["scenario_persons.scenario_id"]},
    "alert_events": {"subject_key": ["people.person_key", "units.unit_id"]},
    "named_exceptions": {"person_key": ["people.person_key"], "manager_key": ["people.person_key"]},
}

# dataset -> (title, grain in one line, rule for which cost column to sum)
DATASET_TEXT = {
    "people": ("People", "One row per person and validity period.",
               "No cost column."),
    "units": ("Units", "One row per organisation unit and validity period.",
              "No cost column."),
    "identity_map": ("Identity map", "One row per provider actor and validity period.",
                     "No cost column."),
    "directory": ("Directory", "One row per person with a person_month row; the only file with names and emails.",
                  "No cost column."),
    "person_day": ("Person per day", "One row per day, provider, provider account, person or actor, product and model.",
                   "`model_provider` is the provider that served the model, where a proxy routes calls on. "
                   "Sum `cost_ref` to total or compare across providers and models: it prices every row from one price "
                   "list in one currency, and is empty when a record could not be priced (see `cost_ref_status`). Sum "
                   "`cost_source` only within one `cost_source_basis` and one `cost_source_currency`: billed, "
                   "consumption and estimated amounts do not add up."),
    "person_day_activity": ("Person activity per day",
                            "One row per day, provider, provider account, person or actor and product.",
                            "No cost column."),
    "person_month": ("Person per month", "One row per person with an employed day in the month.",
                     "`cost_ref` is the person's month total across providers; `cost_ref_by_provider` splits it. Seat "
                     "fees are in no person's cost. `projected_cost_ref` projects a running month: do not add it to "
                     "`cost_ref`."),
    "seats_month": ("Seats per month", "One row per month, provider and provider account with seats.",
                    "`seat_cost` is a fee, not usage. Add it to usage only through `costs_month.total_cost_ref`."),
    "pools_month": ("Credit pools per month", "One row per month, provider and provider account with a credit pool.",
                    "`overage_amount` is billed money for credits beyond the pool and is part of no `cost_ref`. The "
                    "credit columns are quantities, not currency."),
    "costs_month": ("Costs per month", "One row per month, provider and provider account.",
                    "For spend by provider and month sum `usage_cost_ref` (reference cost) or `total_cost_ref` (usage "
                    "plus seats). Never add `seat_cost` to `total_cost_ref`: it is already inside. Sum "
                    "`usage_cost_source` only within one `cost_source_basis`. A proxy row's `overlap_cost_ref` is the part "
                    "of its cost that another provider's row also holds: subtract it before adding the two. A row with "
                    "`is_final` false belongs to a running month, whose `seat_cost` is the full month's fee."),
    "reconciliation_month": ("Reconciliation per month", "One row per month, provider and provider account.",
                             "`org_cost_source` is the provider's organisation-wide total and `actor_cost_source` the "
                             "part allocated to actors; compare them, do not add them. `cost_ref` is a different "
                             "basis from both."),
    "data_quality": ("Data quality", "One row per month, provider, provider account and metric.",
                     "`value` mixes counts, amounts, ratios and dates; read `metric` first and sum nothing across "
                     "metrics."),
    "groups_month": ("Groups per month", "One row per month and grid cell.",
                     "A cell's `cost_ref` already sums its persons, and cells overlap: a unit cell contains its "
                     "sub-unit cells. Sum the cells of one `cell_kind` at one `depth`, never across depths."),
    "comparison_groups": ("Comparison groups", "One row per month and comparison cell.",
                          "No cost to sum: the cut points are quantiles, not totals."),
    "comparison": ("Own comparison", "One row per month: the person's own cost and band.",
                   "`own_cost_ref` is the person's own `cost_ref`; there is nothing to add across rows."),
    "scenario_persons": ("Scenario per person", "One row per scenario, month and person with a cap.",
                         "`cost_ref` is the position at the end of the month, not a new cost; `excess` is not "
                         "spend."),
    "scenario_units": ("Scenario per unit", "One row per scenario, month and unit with an envelope.",
                       "`position` is a month-to-date cost, `excess` a shadow figure; neither is added to spend."),
    "scenario_summary": ("Scenario summary", "One row per scenario and month.",
                         "The `*_excess` and `*_top_up_total` columns are what the scenario would have done; they "
                         "are never spend."),
    "alert_events": ("Alert events", "One row per event; nothing is sent.",
                     "`value` and `threshold` are day or position costs; do not sum events. A `proxy_overlap` event "
                     "carries the amount that two providers' rows both count."),
    "named_exceptions": ("Named exceptions", "One row per month, rule and selected person.",
                         "`value` is a cost for `month_cost_ref_above` and a count otherwise; do not sum across "
                         "rules."),
}
