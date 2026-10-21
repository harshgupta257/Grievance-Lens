# Complaint drafting engine (Phase 10)

```powershell
python -m src.drafting.drafter "mera pf claim 3 mahine se pending hai" --department ministry_of_labour_and_employment
python -m src.drafting.examples      # five verified examples -> docs/draft_examples.md
```

## 1. Design: deterministic, never invents

The drafter (`src/drafting/drafter.py`) fills a fixed template. It does not use an LLM. That is a deliberate
choice (DESIGN.md D-042): a draft is something the citizen will sign and submit, and a template can guarantee
that the draft contains only what the citizen wrote.

| Draft part | Where it comes from |
|---|---|
| Subject | The router's category, if known; otherwise `[ADD A SHORT SUBJECT]` |
| Addressed to | The routed department; if the router abstained, `[CHOOSE THE DEPARTMENT ON THE CPGRAMS PORTAL]` |
| 1. Details of the grievance | **The complaint exactly as written** (only whitespace and control characters cleaned) |
| 2. Dates and timeline | The user's clarification; else the user's own sentences that contain a date or duration; else a placeholder |
| 3. Reference numbers | The user's clarification; else reference values found verbatim in the complaint (e.g. "Claim ID PF-2026-0457"); else a placeholder |
| 4–6. Office, previous attempts, action requested | The user's clarification, else a placeholder. **The drafter never writes a requested action for the user** |
| 7. Documents | A fixed, generic list of suggestions, plus any supporting details the user gave |
| Closing | Fixed sentence; `[YOUR NAME]` and `[DATE]` placeholders. The tool never asks for or stores a name |

- **Language:** English or Hindi headings, standard sentences and placeholders. Hindi placeholders repeat
  the English instruction in brackets.
- **No translation:** the citizen's text is never translated, because translation could change its
  meaning. A Hinglish complaint stays Hinglish inside an English or Hindi frame.
- **No added claims:** no allegations, threats or legal conclusions are added. The template contains none,
  and the screen below checks that.

**Review checklist.** It comes with every draft, in plain language:
- replace every [bracketed] text,
- check every date, amount and number,
- add nothing you cannot support,
- remove unneeded personal data,
- check the department,
- submit it yourself on the official portal.

**Historical statistics.** The `department_statistics` block (`src/performance/stats.py`):
- **Source:** it comes from the Part A exports and is returned **beside** the draft, never inside it
  (tested).
- **What it carries:** each figure has its period and source report. It also lists what is unavailable
  (for example "average closing time: not published for this department") and the standard caveats,
  including that the figures do not predict how a complaint will be handled.

## 2. Factuality screen

Code: `src/drafting/factcheck.py`.
- **What it checks:** every *factual token* in the draft (numbers in Latin or Devanagari digits, amounts,
  reference-like codes, English month names) must occur in the user's input (complaint, clarifications,
  supporting details) or in fixed text (template, department name, category, portal address).
- **Claim words:** allegation, threat and legal words ("corrupt", "fraud", "legal action", "court",
  "compensation", plus Hindi equivalents) are flagged unless the user wrote them.
- **Normalisation:** separators are removed (18,000 = 18000; 1,23,456 = 123456) and Devanagari digits are
  converted.
- **Limitations:**
  - It is a **screen**, not proof. It does not check meaning: a sentence of the user's that is moved into
    the wrong section would pass.
  - Numbers written in words ("five kilograms", "पांच किलो") are not tokens.
  - A token that appears in the input in a different role would pass.

## 3. Verification (five examples)

Full inputs, routes, drafts and screens are in [draft_examples.md](draft_examples.md):

| # | Example | Routed to (offline) | Placeholders left | Screen |
|---|---|---|---|---|
| 1 | English, details in text plus two clarifications | **Department of Financial Services (Insurance Division)**, wrong (an EPF claim belongs to Labour) | 3 | passed |
| 2 | Hindi, Hindi draft, no details | Department of Food and Public Distribution | 7 | passed |
| 3 | Hinglish, English draft, partial clarifications | Ministry of Petroleum and Natural Gas | 5 | passed |
| 4 | State matter (police FIR) | abstained (State matter); department left to the user | 9 | passed |
| 5 | Hindi with an amount, Hindi clarifications | Department of Financial Services (Banking Division) | 6 | passed |

In all five examples, every date, amount and reference number in the draft appears in the input. A
deliberately corrupted copy of example 1 was also screened. It had an invented "Rs 45,000 by 30 June 2026",
a threat of legal action and an allegation of corruption. The screen flagged exactly `45000`, `30` and
`june` (2026 is in the input, so it was not flagged), plus `corrupt` and `legal action`.

**What these examples show:**
1. **Routing errors flow straight into the draft's "To:" line.** Example 1 was addressed to DFS
   (Insurance) because "claim" and "settlement" are strong insurance words in the knowledge base. This is
   why the draft shows the department as a suggestion, the checklist tells the user to check it, and the
   alternatives are displayed.
2. **The timeline extractor is literal.** In example 2 it repeated the whole sentence because "हर महीने"
   ("every month") is a time phrase. That is harmless, because it is the user's own words, but it is
   redundant.
3. **Department names stay in English inside Hindi drafts.** The knowledge base has official Hindi names
   only in Roman script.

## 4. Remaining limitations

- No LLM polishing: drafts are structured but not fluent. An optional LLM rewrite would need the same
  screen, plus a meaning check, before it could be trusted.
- The subject line is often a placeholder. The router's category matching works on English words only.
- The drafter cannot know whether a requested action is reasonable. It only records what the user asks.
