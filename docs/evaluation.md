# Evaluation (Phase 11)

```powershell
python -m src.evaluation.run                                   # proxy labels until the owner has labelled
python -m src.evaluation.run --labels data/complaints/eval_owner_labels.csv   # final, owner labels
```

Numbers: [evaluation_results.md](evaluation_results.md) (generated). Per-item predictions by id, confusion
matrices and drafter rubric sheets are in `data/processed/evaluation/`. They contain no complaint text.

> **Status: proxy evaluation only.** The owner has not labelled the evaluation set yet, so the reference
> labels are the author's *generation intent*, not independent labels. The LLM router was **not run** (no
> API key). All data are **synthetic**. None of these numbers is evidence of performance on real citizen
> complaints.

## 1. Protocol

| | |
|---|---|
| Evaluation set | 300 synthetic complaints (`data/complaints/eval_texts.jsonl`): 222 standard (6 per department, 2 per language), 24 ambiguous, 18 State matters, 14 out of scope, 12 too vague, 10 prompt-injection. Written separately from the development data; the build rejects duplicates |
| Isolation | Router thresholds, stop words, the retrieval method and the baseline's C and threshold were all fixed on development data (`src/routing/thresholds.json`, Phase 9) before the first evaluation run. The evaluation script changes nothing |
| Reference | Proxy: `eval_generation_intent.csv`. Final: the owner's labels (`--labels`), checked for completeness, plus agreement with the generation intent |
| Routable complaint | Its reference is one of the 37 knowledge-base departments. For ambiguous items, any listed acceptable department counts as correct |
| Systems | Router (lexical, the default chosen on development data); router (hybrid); baseline (TF-IDF + logistic regression, trained on the 281 `train` rows); router with LLM (not run) |

**Router metrics**, on the 256 routable items unless stated:

| Metric | Definition |
|---|---|
| Top-1 | Share where the routed department is acceptable. **An abstention counts as wrong** |
| Top-3 | Share where an acceptable department is among the system's 3 best candidates (shown to the user as alternatives) |
| Macro-F1 | Mean of per-department F1, over the reference departments |
| Abstention rate | Share of routable items where the system did not route |
| Accuracy when answering | Top-1 among the items where it did route |
| Non-routable items | The 44 State, out-of-scope and too-vague items: did the system abstain? |
| Intervals | 95% Wilson intervals for top-1 and per language. Per-department figures (6–9 items each) are reported as counts only |

**Drafter metrics** (50 drafts, a stratified sample by language, seed 2026, routed offline):

| Criterion | How measured |
|---|---|
| Completeness | Automated: subject, addressee and all 7 sections are present |
| Faithfulness | Automated: the complaint is reproduced verbatim |
| Unsupported factual claims | Automated: the factuality screen ([drafting.md](drafting.md) section 2) |
| Missing-information handling | Automated, but satisfied by construction (every missing detail becomes a placeholder), so it is not a finding |
| Routing correctness | Automated: the addressee against the reference |
| Clarity | **Human**: `drafter_human_rubric.csv` (1–5 scales); not yet done |

The automated checks are screens, not proof that a draft is correct.

## 2. Results (proxy) and what they mean

| System | Top-1 | 95% CI | Top-3 | Macro-F1 | Abstains (routable) | Non-routable abstained |
|---|---|---|---|---|---|---|
| Router, lexical (default) | 62.1% | 56.0–67.8 | 86.3% | 0.660 | 14.8% | 24 / 44 |
| Router, hybrid | 66.4% | 60.4–71.9 | 88.7% | 0.702 | 14.8% | 28 / 44 |
| Baseline | 9.4% | 6.4–13.6 | 67.6% | 0.150 | 89.8% | 43 / 44 |
| Router, LLM | not run | | | | | |

1. **The router gets about 6 in 10 complaints right at rank 1, and about 9 in 10 within its top 3.** That
   is why the interface shows alternatives and asks the user to confirm. On the 80 development items it was
   right 80% of the time (64/80), so the gap shows how much development data flatters a tuned system, even
   one with only three tuned numbers.
2. **The hybrid did better than lexical on the evaluation set.** On development data it was the other
   way round (utility 0.74 vs 0.80). The default stays lexical: switching because of evaluation results
   would turn the evaluation set into tuning data. The two intervals overlap heavily. A fresh labelled
   set is the right way to choose again.
3. **Language.** Lexical is similar across English, Hindi and Hinglish (60–65%). The hybrid helps English
   most (75.6%) but not Hindi (58.0%).
4. **Abstention is weak where it matters most.** The lexical router abstained on 14 of 18 State matters
   and 8 of 14 out-of-scope items, but on only **2 of 12 too-vague** complaints: vague text still
   retrieves something. It also abstains on 15% of routable items.
5. **The baseline mostly refuses.** About 7 training examples per class across 40 classes are too few. Its
   tuned threshold makes it abstain on 90% of routable items. That explains its 43/44 "correct
   abstentions", so it is not a usable router. It is reported because the brief requires a transparent
   baseline, and as a floor.
6. **Prompt injection.** 9 of 10 injection items were flagged and 9 of 10 routed to the underlying
   grievance's department. No embedded instruction changed the output: the offline router has no
   instruction-following component.
7. **Drafter.** All 50 drafts are complete, reproduce the complaint verbatim, and have **0 unsupported
   factual tokens and 0 unsupported claim words**. The addressee was correct in 29 of 50, the router's
   accuracy carried into the draft. Clarity is not yet rated.

**Failure cases** (from `predictions_offline.csv` and the confusions in the results):
- **Vocabulary collisions:**
  - "company" pulled Telecommunications and Labour complaints to Corporate Affairs (4 + 3 cases);
  - "card" and "bank" pull Aadhaar, ration and tax items to Banking;
  - "exam" and "result" pull school items to the Staff Selection Commission.
- **Near-duplicate mandates:** MoHUA vs the Department of Urban Development (urban housing), CBDT vs the
  Department of Revenue. Both pairs are partly accepted as alternatives.
- **Vague complaints** are routed instead of questioned (10 of 12).

## 3. Limitations of this design

- **Proxy labels:** the reference is one author's intent. The same author wrote the keywords, the
  development data and the evaluation data, so the absolute numbers are likely optimistic. Hinglish
  spellings and phrasing are limited to one writer's habits.
- **Small samples:** per-department figures (6–9 items) are anecdotal, and language-level intervals are
  ±10 points wide.
- **Not real-world:** the class balance is designed, and the texts are short, single-issue and clean.
  Real CPGRAMS traffic is dominated by a few departments (Part A), so real top-1 could be higher or lower.
- **LLM not measured:** retrieval + LLM is the brief's main approach, and it has no measured result here.
  Running it costs money and needs the owner's key and approval. The script runs it automatically once
  `LLM_PROVIDER=anthropic` is set.

## 4. Completing the evaluation

1. The owner labels `data/complaints/eval_owner_labels.csv` ([labelling_guide.md](labelling_guide.md)),
   without opening the generation-intent file.
2. Optional: configure the LLM (`.env`). The evaluation makes about 300 calls, all cached; the cost
   estimate goes to `data/interim/llm/usage.jsonl`.
3. Run `python -m src.evaluation.run --labels data/complaints/eval_owner_labels.csv`.
4. Rate clarity in `drafter_human_rubric.csv`.
5. **Do not re-tune on these results.** Further tuning needs a new held-out set.
