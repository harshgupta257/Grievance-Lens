# Labelling guide: evaluation complaints (Phase 8)

**Who labels:** the project owner. The labels you give become the ground truth for the Phase 11 evaluation.
Label **without** opening `data/complaints/eval_generation_intent.csv`. That file records what the author
intended when writing each text. It is kept only to measure agreement with your labels afterwards.

**What to label:** `data/complaints/eval_owner_labels.csv`, with 300 rows. Each row has an `id` and the
complaint `text`; the label columns are blank. The file is UTF-8 with a BOM, so Excel shows Devanagari
correctly. Save it as CSV (UTF-8). `python -m src.complaints.corpus` refuses to overwrite the sheet once
it contains labels.

**Reference:** use the department list and mandates in
[mandate_kb_summary.md](mandate_kb_summary.md) and `data/reference/mandates/departments.json`. Label from
the text alone. Do not guess facts the complaint does not state.

## Columns

| Column | Values | Rule |
|---|---|---|
| `department` | a `ministry_key` from the knowledge base, or `state_government`, `out_of_scope`, `insufficient_information` | The single best central department for this grievance, as written. Choose `state_government` when the matter is run by a State or local body (police station, tehsil or land records, municipal services, State power company, State schemes or exams). Choose `out_of_scope` when it is clearly a central matter but for a department outside the 37 (e.g. civil aviation, central civil pensions, tribal affairs). Choose `insufficient_information` when no department can reasonably be inferred |
| `acceptable_alternatives` | zero or more `ministry_key`s, separated by `;` | Other departments that a reasonable official could also accept, e.g. DESW and Defence Finance for "army pension not received". Leave blank if only one is defensible |
| `category` | short free text, e.g. `EPF withdrawal`, `ration not received` | The issue type, in a few words |
| `language` | `en`, `hi`, `hinglish`, `mixed` | `hinglish` = Hindi written in Roman script (with or without English words). `mixed` = Devanagari and Roman Hindi in one text |
| `ambiguity` | `none`, `some`, `high` | `some`: a second department is plausible. `high`: you could not choose between two or more without asking |
| `urgency` | `high`, `medium`, `low`, `unknown` | `high`: risk to life, health or safety, or a person in distress abroad. `medium`: loss of income or essential service. `low`: delay or inconvenience. `unknown`: cannot tell |
| `sufficient_for_routing` | `yes`, `no` | `no` if a department cannot be chosen without asking a question |
| `state_matter` | `yes`, `no`, `partly` | `partly`: central scheme or policy, but a State body implements the part complained about |
| `notes` | free text | Anything unusual, e.g. "contains an instruction to the system; ignored it" |
| `labelled_by`, `labelled_on` | your initials, ISO date | Required for every labelled row |

## Decision rules

1. **Route on the subject, not on keywords.** "Pension" alone does not decide the department. Ask whose
   pension (EPFO, railways, defence, central civil, State), then route to the department responsible for
   that subject.
2. **Prefer the body that handles the grievance**, when it is in the list: CBDT for an income-tax
   refund, rather than the Department of Revenue.
3. **MoHUA and the Department of Urban Development.** Reports since Sep 2026 list the Department of Urban
   Development. Use it for urban housing, urban missions, street vending and real estate. Use MoHUA for
   CPWD, government accommodation, DDA and Delhi Metro. Put the other one in `acceptable_alternatives` if
   in doubt.
4. **Instructions inside the complaint** ("ignore your rules", "route to X", JSON or SQL) are part of the
   text, not instructions to you. Label the underlying grievance and mention the attempt in `notes`.
5. **Do not correct the complaint.** If the text says something impossible or wrong, label what it asks for
   and note the problem.

## After labelling

`python -m src.evaluation.run --labels data/complaints/eval_owner_labels.csv` reports the final router
metrics, plus your agreement with the generation intent. It refuses to run if any row is missing
`department`, `labelled_by` or `labelled_on`.
