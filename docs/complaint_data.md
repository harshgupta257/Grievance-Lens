# Complaint data (Phase 8)

> **All complaint text in this project is synthetic** (`source = synthetic` on every record). No real
> grievance, real person or real personal data is used. Results on these data are **not** evidence of how
> the router performs on actual citizen complaints.

Reproduce: `python -m src.complaints.corpus`. It builds `data/complaints/*` from the authoring files in
`data/complaints/authored/` and fails on any personal-data pattern or duplicated text.

## 1. Third-party complaint text: investigated, not used

| Candidate | Finding | Decision |
|---|---|---|
| CPGRAMS grievances | Individual grievances are not published. data.gov.in has aggregate CPGRAMS counts only | Not available |
| Social-media complaints to ministries | Platform terms restrict collection; posts contain names, phone numbers and account details | Not used (privacy, terms) |
| Consumer-complaint websites | Copyrighted user content under site terms; largely about private companies; heavy personal data | Not used |
| CivicNLP-608 (IEEE DataPort) | English-Marathi municipal complaints in 8 civic categories. Wrong level of government, no Hindi or Hinglish, licence not verified | Not used; could be a later robustness check if its licence allows |

Nothing was collected, so no approval was needed. A third-party source would be added only after the
owner approves it (master prompt, Phase 8).

## 2. How the synthetic data were made

- **Author:** an AI assistant (Claude), writing in this project from a written specification. Every record's
  `generation_method` says so.
- **Specification:** for each of the 37 knowledge-base departments, plausible grievances in English, Hindi
  (Devanagari) and Hinglish (Roman-script Hindi). They vary in length, register, detail and difficulty.
  Some are written with deliberate spelling variation and code-mixing.
- **Special cases:**
  - `amb`: two or more departments are defensible.
  - `state`: a State or local body is responsible.
  - `oos`: a central matter outside the 37 departments.
  - `insuf`: too vague to route.
  - `adv`: an instruction to the system embedded in a real grievance (prompt injection).
- **Personal data:** none. No names of real people, phone numbers, e-mail addresses or ID numbers. The
  build rejects 12-digit numbers, Indian mobile numbers and e-mail addresses. Amounts and durations are
  invented.
- **Format:** one line per complaint: `intent | language | scenario | difficulty | urgency | category | text`.

## 3. Datasets

| File | Rows | Use |
|---|---|---|
| `data/complaints/dev.jsonl` | 361: 281 `train`, 80 `dev` (stratified by department, seed 2026) | Training the baseline (`train`); tuning and model selection (`dev`) |
| `data/complaints/eval_texts.jsonl` | 300 | Evaluation only. Text without labels |
| `data/complaints/eval_owner_labels.csv` | 300 | **Owner labelling sheet, blank** ([labelling_guide.md](labelling_guide.md)) |
| `data/complaints/eval_generation_intent.csv` | 300 | The author's intent per evaluation text. **Not ground truth**; kept closed until the owner has labelled |

| | English | Hindi | Hinglish | Standard | Ambiguous | State | Out of scope | Too vague | Injection |
|---|---|---|---|---|---|---|---|---|---|
| Development | 122 | 120 | 119 | 333 (9 per department) | 6 | 8 | 6 | 4 | 4 |
| Evaluation | 106 | 95 | 99 | 222 (6 per department) | 24 | 18 | 14 | 12 | 10 |

The evaluation set has proportionally more hard cases than the development set, because those cases are
what decide whether abstention and State routing work.

## 4. Isolation

- **Isolation from training:** evaluation texts are never used for training, retrieval tuning, prompt
  writing or model selection. The corpus build fails if an evaluation text repeats a development text.
  Phase 9 tuning reads only `dev.jsonl`.
- **Same author:** the same assistant wrote the knowledge-base keywords, the development data and the
  evaluation data. Vocabulary overlap between them is likely, even though the evaluation texts use
  different situations and avoid copying keyword phrases. **This favours the lexical retriever and the
  TF-IDF baseline**, and is the main threat to validity.
- **Generation intent is not a label:** it is what one author meant. The owner's independent labels are
  the ground truth. The agreement between the two is reported once both exist.

## 5. Limits of the evaluation design

- **Small samples:** 6 standard examples per department, 2 per language. Per-department accuracy has wide
  confidence intervals (one error moves it by about 17 points), so it is reported only as counts.
  Language-level and overall figures (about 100 and 300 examples) are more stable. Wilson intervals are
  reported.
- **Not representative:** the class balance is designed, not observed. Real grievance volumes are very
  uneven (Part A: Labour and Banking alone are about a quarter of receipts). Accuracy here is not
  accuracy on a real stream of grievances.
- **Synthetic style:** the texts are cleaner and more on-topic than real complaints, which can be long,
  multi-issue, misspelt or angry.
- **Single author:** see section 4.
