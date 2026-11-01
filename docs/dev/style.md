# Writing style for meterline documents

How the READMEs, guides, specs and change lists are worded, so that a rule has one reading. Most of it is
checked by tests in `tools/`; the rest is for whoever writes the next spec version. Words with a project meaning
are defined in the [glossary](../glossary.md).

## Sentences and lists

- A sentence has at most 45 words, and its verb comes early.
- A table or a list opens with a sentence that says what it holds, never straight after a heading.
- A Known limits section groups its items by kind under bold labels: how the tool behaves, what the source does
  not give, what is not verified, what is out of scope, personal data.
- A fact is stated once, in its home, and linked from elsewhere; a copied sentence drifts.

## Modal verbs

These words carry one meaning each, in rules and in prose:

- **must** and **must not**: an obligation; breaking it is a defect or an input error.
- **may**: permission; the thing is allowed, not required. "May" is not used for possibility.
- **can**: ability or possibility ("a reader can tell", "a month record can end in the future").
- **should**: a recommendation that the tool does not check.
- "Is allowed with X only" is written "must be used with X only"; "never" in a rule carries its reason in the
  same sentence or a link to it.

## That and which

A restrictive clause, one that picks out which thing is meant and cannot be dropped, uses "that" without a
comma: "the day that the window starts on". A clause that only adds a fact uses ", which" with a comma. A
restrictive clause after a preposition ("the day on which") is clear but heavier; the next version of a spec
rewrites it with "that" where the sentence allows.

## And, or

- A list that mixes "and" and "or" is split into a numbered list, or each grouping is written out.
- "Either A or B" means exactly one. "A or B, or both" says so in words.
- "and/or" is never used.
- A qualifier at the start of a list ("of the changed model") says which items it covers: "usage records of the
  changed model, and organisation totals of any model".

## Rules with several conditions

A rule with three or more conditions is a numbered list, one condition per line, with "all of" or "any of" in
the sentence before it. The same holds for a sequence of steps: the release steps in
[RELEASING.md](RELEASING.md) are numbered one per line.

## Time windows

- Every date and day is UTC; a bound says whether it is inclusive ("from d to e inclusive").
- A window of months says whether it counts **calendar** months ("the 24 calendar months before the month of
  the pull, starting on the first day of the earliest") or a **rolling** span of days ("365 days counted back
  from the pull day").
- "A year" in a rule is written as a number of days or of calendar months.

## Words with a defined meaning

- **published**: a connector publishes a batch when it writes it under `records/`; a gold cell is published
  when its figures are written. A suppressed row is *written*, not published.
- **suppressed**: a cell of `groups_month` whose figures a privacy rule withholds; it carries a reason.
- **hidden**: a value of a row in the files with a `hidden` column, withheld by a privacy rule. "Hides" is not
  used for suppression.
- **final** (`is_final`) and **complete** (a month whose last day is on or before `data_through`) are not
  interchangeable.
- The evidence levels of a connector are defined in the [glossary](../glossary.md#evidence-levels) and used word
  for word.
- The purpose sentence is the first sentence of the root README, used unchanged at the top of every package
  README.

## Wording findings in the accepted specs

Accepted specs are frozen, so their wording is not edited. The findings below are recorded in the change list
of each spec with the reading the code implements; the next version of the spec takes the rewording.

| Spec | Section | Wording | Reading the code implements, and the rewording |
|---|---|---|---|
| gold v1.2 | 4.1, 4.2, 5.3, 6.5, 6.6, 7.1 | six restrictive clauses with a preposition and "which" ("above which", "at which", "on which", "for which") | Unambiguous as written; the next version uses "that" (for example "the amount that a person's daily cost must exceed"). |
| gold v1.2 | 4.3 | "`month_cost_ref_above` is allowed with audience `central` only" | An obligation: the condition must be used with audience `central` only; any other audience is an input error. |
| gold v1.2 | 7.3 | heading "Suppression: what is published" | The section lists what a suppressed row keeps; the rewording is "Suppression: what a suppressed row keeps". |
| gold v1.2 | 7.4 b2 | "P fails in three cases: ...; ...; or ..." in one sentence | Any of three conditions; numbered one per line in the gold change list. |
| gold v1.2 | 8.1, 8.2 | the position, outputs and the spike rule ("Fires when all three hold: a; b; and c") inline | All three spike conditions must hold; numbered one per line in the gold change list. |
| v2 v1.0 | 6.3 rule 5, 6.4 rule 8 | "not more than one year back", "not more than 24 months back"; "for which raw data exists" | One year is 365 days counted back from the pull day (rolling); 24 months are calendar months. See the v2 change list. |
| sim v1.0 | 15, S03 | "Only Anthropic `usage` records of the changed model ..., and `org_totals` records ..., differ" | The model qualifies the usage records only: organisation totals of any model may differ. See the sim change list. |
