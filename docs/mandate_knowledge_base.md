# Mandate knowledge base (Phase 7)

The knowledge base tells the Part B assistant which central department is responsible for what, so it can
suggest where a grievance belongs. It covers 37 CPGRAMS entities. Each entry carries the department's
**official mandate text**, plus clearly labelled **project-authored** material (plain-language
responsibilities, typical grievance categories, and English, Hindi and Hinglish keywords).

> **Keywords and categories are not proof of jurisdiction.** They help retrieval find candidates. The
> official text and the cited items are what support a recommendation, and the assistant must still check
> them and ask or abstain when the evidence is weak. The project owner has not reviewed the entries yet.

Reproduce (PowerShell, from the project root, venv active):

```powershell
python -m src.knowledge.aob --download   # first time only: fetch the official PDF and verify its SHA-256
python -m src.knowledge.aob              # parse the Second Schedule -> data/reference/mandates/aob_items.csv
python -m src.knowledge.build            # validate + resolve -> data/processed/knowledge/mandates.jsonl
python -m src.knowledge.retrieve --check --methods lexical   # 10 fixed queries -> docs/retrieval_check.md
```

For the dense and hybrid methods, install the optional `requirements-embeddings.txt` and run `--check`
without `--methods`.

## 1. Official source

**The Government of India (Allocation of Business) Rules, 1961**, as consolidated by the Cabinet
Secretariat up to Amendment Series no. 386 (22 July 2026). These are the official rules that allocate
government business among ministries and departments. The Second Schedule lists, for every
department, the subjects allocated to it as numbered items.

- **Retrieval:** the PDF was downloaded once, on 21 Oct 2026, in a single request. Its URL and SHA-256
  are in [`data/reference/mandates/sources.csv`](../data/reference/mandates/sources.csv). The PDF itself
  is not committed (`data/raw/` is git-ignored).
- **Parsing:** `src/knowledge/aob.py` parses pages 16–190 into 1,912 items in 92 sections, each with its
  section, part, item number and PDF page. Item text is kept verbatim. The parser removes only page
  footers, footnote blocks and footnote reference marks, and joins wrapped lines.
- **Known parser limitations:** all of these are in sections outside the scope. Three duplicate item
  keys and one leaked footnote are in the Ministry of Ports, Shipping and Waterways. One footnote leaked
  into an Animal Husbandry item. The National Security Council Secretariat entry (p190) is filed under
  NITI Aayog, because its heading is not recognised.

**Why this source.** It is a single official document that covers every department, uses one consistent
format and carries a date. The alternative was individual department websites. Their "functions" pages
differ in format and update dates, and several block automated access. Websites are used here only to
record each department's official address.

**Official websites.** One request was made per site, on 21 Oct 2026, and every result is in
[`url_checks.csv`](../data/reference/mandates/url_checks.csv). 32 sites returned HTTP 200. The others:
- **Income Tax Department:** returned 403 to automated clients. Its address was confirmed from links on
  the Department of Revenue's own site.
- **CBIC:** its server sends an incomplete TLS certificate chain. Certificate checking was not switched
  off. Its domain was confirmed from the same Department of Revenue page.
- **MCA:** returned 403. Its address comes from government directory listings, and the knowledge base
  says so.
- **consumeraffairs.nic.in:** timed out. The department's `.gov.in` site loaded and was used instead.

## 2. Scope

The entries cover the **35 entities with the highest mean monthly receipts** in the Part A monthly panel
(Sep 2024 – Jan 2026), which together account for about 91% of panel receipts. Two more are added:
- the **Staff Selection Commission** (rank 36),
- the **Department of Urban Development**, which replaced MoHUA in CPGRAMS reports from Sep 2026.

Each entry is keyed by Part A's `ministry_key`, so the assistant can show that department's
historical figures.

Several CPGRAMS entities are not AoB departments, so they are linked to the AoB items that name them:

| CPGRAMS entity | Linked to | Mapping |
|---|---|---|
| Central Board of Direct Taxes | Department of Revenue items 1, 4, 14 | direct |
| Central Board of Indirect Taxes and Customs | Department of Revenue items 1, 11, 12, 14, 21 (the AoB still uses the former name "Central Board of Excise and Customs") | direct |
| Unique Identification Authority of India | MeitY item 14 (the AoB names UIDAI only) | direct |
| Staff Selection Commission | DoPT items 5A, 5 | direct |
| DFS (Banking Division), DFS (Insurance Division) | DFS parts II and I | direct |
| Ministry of Home Affairs | all six MHA departments | direct |
| Ministry of Housing and Urban Affairs | Departments of Capital Development and Urban Development | direct |
| Ministry of Drinking Water and Sanitation | Department of Drinking Water and Sanitation (Jal Shakti) | direct |
| **Department of Defence Finance** | **Department of Defence item 15 (Defence Accounts Department)** | **inferred: needs review** |

The AoB has no "Department of Defence Finance". Linking that CPGRAMS entity to the Defence Accounts
Department is my inference, and it is flagged in the data.

**Not in scope** (named in overlap notes where relevant): the Department of Pension and Pensioners'
Welfare, which is not a CPGRAMS entity in these reports; the Department of Military Affairs; Fertilizers;
Tribal Affairs; Minority Affairs; Empowerment of Persons with Disabilities; Land Resources; and the
remaining low-volume entities.

## 3. What each entry contains

Files:
- [`data/reference/mandates/departments.json`](../data/reference/mandates/departments.json): the edited
  entries.
- `data/processed/knowledge/mandates.jsonl`: the resolved records.
- [`mandate_kb_summary.md`](mandate_kb_summary.md): a coverage table, generated.

| Field | Source |
|---|---|
| `canonical_name` | Part A (Phase 3 name resolution) |
| `official_name`, `parent_ministry`, `aob_hindi_names` | AoB section headings (Hindi names in Roman script, e.g. "Dak Vibhag") |
| `official_mandate` | **Verbatim AoB items**, with section, part, item number and PDF page; copied by the build, never edited |
| `official_urls` | Checked addresses (date, HTTP status, how confirmed) |
| `grievance_responsibilities` | Project-written plain-language summary. **Every line cites AoB items**, and the build rejects a citation that is not among the entity's own items |
| `common_grievance_categories` | Project-authored. There is no public official list of CPGRAMS categories |
| `keywords` (`en`, `hi`, `hinglish`) | Project-authored. Hindi entries must contain Devanagari |
| `overlaps` | Project-authored notes on shared or confusable responsibilities, e.g. pensions (EPFO / railways / defence / civil), housing (urban / rural), water (urban / rural), visas (MEA / MHA), cyber crime (MHA) vs cyber security (MeitY) |
| `mapping_status` | `direct`, or `inferred` (Defence Finance) |

**State subjects.** Many grievances concern matters handled by State Governments: police, ration shops,
State roads and transport offices, State hospitals, panchayats, and water supply works. In these cases the
central department usually sets policy or funds a scheme. The overlap notes say so for each department.
The router (Phase 9) needs an explicit "probably a State Government matter" outcome.

The build (`python -m src.knowledge.build`) refuses to write anything if any of these fail:
- a `ministry_key` is not a Part A ministry,
- an AoB reference is missing or omitted,
- a responsibility cites an item outside the entity's own references,
- a URL has no accepted check,
- a keyword list is empty,
- a Hindi list has no Devanagari text,
- an overlap names an unknown entry.

## 4. Retrieval

`src/knowledge/retrieve.py` splits each department into **chunks**:
- its names,
- each official AoB item,
- each project-authored responsibility, category and keyword list.

It scores every chunk against the grievance, and a department's score is its best chunk score plus half
its second-best.

Every result keeps its supporting chunks, labelled *official* (with the AoB citation) or
*project-authored*. This lets Phase 9 explain a recommendation, and show when it rests only on the
project's own keywords.

| Method | How | Needs |
|---|---|---|
| `lexical` (default) | TF-IDF on character 3–5-grams within words, plus words 1–2-grams. Character n-grams tolerate Hinglish spelling variants (*nahi / nahin*) | scikit-learn; deterministic, no download |
| `dense` | `intfloat/multilingual-e5-small` sentence embeddings, cosine similarity | optional `requirements-embeddings.txt`; about 470 MB model download on first use; knowledge-base embeddings cached in `data/interim/knowledge/` |
| `hybrid` | Reciprocal rank fusion (k = 60) of the lexical and dense rankings | both |

**No vector database.** 37 departments produce 1,287 chunks (847 of them official items), small enough for an exact search in
memory with NumPy and SciPy. FAISS or pgvector would add moving parts and give nothing back at this size.

**Privacy.** The retrieval module does not log or store complaint text. `--check` writes only the 10
fixed test queries.

## 5. Retrieval check: 10 fixed queries

I wrote the queries and their acceptable departments **before the first retrieval run**. They are in
`CHECK_QUERIES` in `src/knowledge/retrieve.py`:
- 4 English, 3 Hindi (Devanagari) and 3 Hinglish,
- one deliberately ambiguous query (an army pension: DESW or Defence Accounts are both acceptable).

The retriever was not tuned to them. Full output is in
[`retrieval_check.md`](retrieval_check.md) (generated).

| # | Lang | Query (abridged) | Acceptable | lexical top-1 | dense top-1 | hybrid top-1 |
|---|---|---|---|---|---|---|
| 1 | en | EPF withdrawal claim pending | Labour | Labour ✓ | Labour ✓ | Labour ✓ |
| 2 | en | toll charged twice, FASTag | MoRTH | MoRTH ✓ | MoRTH ✓ | MoRTH ✓ |
| 3 | en | income tax refund not credited | CBDT | CBDT ✓ | CBDT ✓ | CBDT ✓ |
| 4 | en | speed post parcel not delivered | Posts | Posts ✓ | Posts ✓ | Posts ✓ |
| 5 | hi | father's army pension not received | DESW or Defence Finance | DESW ✓ | **Labour** (DESW 2nd) | DESW ✓ |
| 6 | hi | ration shop short-supplies; name not added | DFPD | DFPD ✓ | DFPD ✓ | DFPD ✓ |
| 7 | hi | Aadhaar date of birth wrong | UIDAI | UIDAI ✓ | UIDAI ✓ | UIDAI ✓ |
| 8 | hinglish | gas cylinder not delivered | MoPNG | MoPNG ✓ | MoPNG ✓ | MoPNG ✓ |
| 9 | hinglish | passport not received after police verification | MEA | MEA ✓ | MEA ✓ | MEA ✓ |
| 10 | hinglish | NREGA wages not paid | Rural Development | Rural Dev ✓ | **UIDAI** (Rural Dev 2nd) | Rural Dev ✓ |

| Method | Correct at rank 1 | Correct in top 3 |
|---|---|---|
| lexical | 10 / 10 | 10 / 10 |
| lexical, official text only (diagnostic) | 3 / 10 | 4 / 10 |
| dense (multilingual-e5-small) | 8 / 10 | 10 / 10 |
| hybrid | 10 / 10 | 10 / 10 |

> **Update (Phase 9):** retrieval now drops English, Hinglish and Hindi function words before matching
> ([routing.md](routing.md) section 2). That change was chosen on development data. The totals above are
> unchanged on re-run; the margin figures below were measured before the change.

**These scores are optimistic, and the details matter more than the totals:**

1. **I wrote both the queries and the keywords.** They share phrases ("speed post", "passport police
   verification", "nrega ... nahi mila"). In 9 of the 10 lexical results, the strongest evidence is a
   project-authored keyword or category. With official text alone, lexical retrieval gets 3 of 10. All
   three Hindi queries score **exactly 0** against the English AoB text, so their order is meaningless.
   The official text supports the recommendation, but on its own it does not find the department.
2. **Margins are often thin.**
   - Lexical, Q10: Rural Development 0.260, Panchayati Raj 0.247, Labour 0.246. The near-ties come
     from Hinglish filler words ("nahi", "kiya", "mila") that appear in many keyword lists.
   - Dense, Q5: Labour 1.258 vs DESW 1.257.
   - Dense, Q10: UIDAI 1.277 vs Rural Development 1.276.

   e5 similarities fall in a narrow band, so raw dense scores cannot drive an abstain decision.
3. **The hybrid got all 10 right**, and its two dense misses were rescued by lexical rank. Ten queries
   are far too few to tell the methods apart.

**What this means for later phases (not changed now, to avoid tuning on these 10 queries):**
- **Phase 8:** an independently labelled evaluation set, written separately from these keywords and
  kept out of retrieval tuning, is needed before any accuracy claim.
- **Phase 9:**
  - Choose the method on the Phase 8 development split. The candidates are a Hinglish stop-word list,
    weights for official vs authored chunks, and lexical vs hybrid.
  - Use top-k candidates plus LLM selection, not top-1.
  - Abstain or ask when the margin is small or the evidence is only keywords.
  - Add an explicit "probably a State Government matter" outcome.
- **Owner review:** the project owner should review the entries, especially the inferred Defence Finance
  mapping.

## 6. Limitations

- **Coverage:** 37 of 92 CPGRAMS entities. A grievance that belongs to an entity outside the scope can
  only be matched to the nearest in-scope department. Phase 9 must allow "not covered".
- **Responsibility vs handling:** the AoB lists subjects, not grievance-handling arrangements. The
  department that handles a grievance in practice (a field office, a public-sector company, a State) can
  differ from the department the subject is allocated to.
- **Project-authored material:** categories and keywords are mine, not official, and have not been
  reviewed. The AoB text is English only; Hindi coverage depends entirely on project-authored keywords
  and on the multilingual model.
- **Point-in-time sources:** the AoB is as amended up to 22 July 2026, and websites were checked on
  21 Oct 2026. Both can change, and `--download` refuses a PDF whose checksum differs.
