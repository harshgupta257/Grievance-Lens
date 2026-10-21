# Knowledge-base review (2026-10-22)

**Reviewer:** the AI assistant (Claude), at the project owner's request. **This is not an independent review:**
the same assistant wrote the entries. A review by someone who has worked with CPGRAMS is still recommended.

## What was checked

1. **Structure (automated, every build):** 37 entries; every responsibility cites the entity's own AoB items;
   every official URL has a recorded check; Hindi keywords contain Devanagari; overlaps point to known entries.
   `python -m src.knowledge.build` passes.
2. **The one inferred mapping (Department of Defence Finance).** The official Ministry of Defence page
   [mod.gov.in/dod/finance-division](https://www.mod.gov.in/dod/finance-division) was checked (HTTP 200,
   2026-10-22). Its content is rendered by script and was read via search-result extracts:
   - the Ministry's Finance Division is headed by the **Secretary (Defence Finance)**;
   - the **Controller General of Defence Accounts** makes payments on that Secretary's behalf.

   The entry now names the Finance Division with the Defence Accounts Department, is marked `direct`, and
   keeps its AoB link (Department of Defence item 15).
3. **State-level handling.** Three overlap notes were added where a central law is implemented by State
   bodies:
   - births and deaths registration (MHA);
   - construction-worker welfare boards (Labour);
   - real-estate regulatory authorities (Urban Development).

   Overlap notes are not used in retrieval, so these corrections cannot change routing scores.
4. **Keyword collisions (audit, not acted on).** Of 950 distinct keyword tokens, 26 appear in four or more
   departments' keyword lists:
   - *paisa* (12 departments), *card* (11), *scheme* (7), *galat* (7), *कार्ड* (6), *service* (6),
     *refund* (6), *policy* (6), *pension* (6), *kharab* (6);
   - *registration*, *payment* and *delayed* (5 each).

   These generic words explain the evaluation's most common confusions, such as Aadhaar, ration and tax
   complaints pulled towards Banking by "card" or "paisa".

## What was deliberately not changed

The keyword lists were **not** pruned. Removing the colliding words now would be tuning on errors seen in
the evaluation set, and would inflate the next evaluation run. The recommended route is:
1. prune or down-weight generic tokens using **development data only**;
2. re-tune the thresholds;
3. evaluate on a **new** held-out set.
