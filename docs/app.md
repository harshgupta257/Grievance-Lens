# Application (Phase 12)

```powershell
uvicorn src.app.main:app --host 127.0.0.1 --port 8000      # then open http://127.0.0.1:8000/
python -m src.app.demo --base http://127.0.0.1:8000        # three end-to-end requests -> docs/app_demo.md
```

The app needs no database and no API key. It reads the knowledge base
(`data/processed/knowledge/mandates.jsonl`), the routing thresholds and the Part A exports
(`data/processed/powerbi/`). LLM routing turns on only when `.env` sets `LLM_PROVIDER=anthropic`
([routing.md](routing.md)).

## 1. Architecture

- **Backend:** FastAPI (`src/app/main.py`). It validates settings at startup (`RouterSettings`) and loads
  the router once.
- **Frontend:** one static HTML, CSS and JavaScript page (`src/app/static/`), served by the same
  application.
- **Why not Streamlit:**
  - Streamlit reruns the whole script on every interaction, and gives less control over accessibility,
    focus and the loading, empty and error states the brief asks for.
  - It would add a heavy dependency.
  - A static page on the same origin needs no build step and can use a strict Content Security Policy
    (DESIGN.md D-045).

| Endpoint | Purpose | Notes |
|---|---|---|
| `GET /health` | Status, number of departments, LLM provider and model, retrieval method, `submits_complaints: false` | |
| `POST /route` | `{text, language}` → `RouteResult` | Rate-limited per client (60/min offline, 10/min with an LLM); 90 s timeout; `422` with a readable message for invalid input |
| `POST /draft` | `{complaint, language, department_key, category, clarifications, supporting_details}` → `Draft` | Unknown departments or clarification fields → `422`. Statistics are returned beside the draft |
| `GET /ministry/{ministry_key}/performance` | Verified department figures, unavailable items and caveats | `404` for unknown departments |
| `GET /departments` | The 37 departments (key and name) | |
| `GET /` | The web page | |

**Safety:**
- **Logging and storage:** logs record only method, path, status and time, never request bodies
  (tested). No complaint text is stored.
- **Response headers:** `Content-Security-Policy` (scripts and connections to the app itself only),
  `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
- **Page rendering:** all API text is inserted with `textContent`, never as HTML.
- **No submission:** there is no submission endpoint (tested).

## 2. User workflow (as built)

1. Paste a complaint (with a character counter and a 15-character minimum) and choose a language, or let
   it be detected.
2. **Suggested department:** shown with the issue category, urgency, detected language, and a confidence
   labelled *"not a calibrated probability"*.
   - **Evidence:** each line is labelled *Official text* (Allocation of Business Rules, with item and page)
     or *Project keywords*.
   - **Notices:** prompt-injection and State-matter notices appear when relevant. A high-urgency complaint
     gets a pointer to emergency services (112).
3. **When the router abstains:**
   - The page says so and shows the clarifying question.
   - The evidence is labelled "Closest matches found (not a recommendation)".
   - The default choice is *"I will choose the department on the CPGRAMS portal"*.
4. **Department choice:** you can pick the suggestion, an alternative, or "choose on the portal".
5. **Historical figures for the chosen department:**
   - the latest reporting month,
   - a table of measures with their period and source,
   - the backlog trend with a sparkline,
   - a "Not available" list,
   - links to the source reports,
   - the caveats.
6. **Clarifications:** add reference numbers, dates, the office, previous attempts and the action wanted.
   Missing details are suggested.
7. **Prepare a draft (English or Hindi):** an editable text box, the number of placeholders still to fill,
   the automatic factuality check, a **Copy** button, a link to the official portal, and the review
   checklist.
8. **You submit it yourself** on pgportal.gov.in.

**States:** loading (buttons disabled, status text), empty (sections hidden), validation errors, API
failures (network, 422, 429, 5xx messages) and low confidence (abstention).

**Accessibility:**
- **Structure:** labelled fields, a skip link and `aria-live` status regions.
- **Interaction:** visible focus, keyboard-only operation, and charts with a text alternative.
- **Display:** dark mode, and a responsive layout down to 390 px.

## 3. Verification

**API.** `tests/test_api.py` has 14 tests covering:
- health,
- routing and its validation errors,
- drafts, including unknown departments and fields,
- performance, including the 404,
- the page and its security headers,
- that nothing is submitted,
- that logs never contain complaint text,
- the rate limiter and the 429 response.

**Server, end to end.** On 22 Oct 2026 uvicorn ran locally on port 8765.
`python -m src.app.demo` made three end-to-end requests, all HTTP 200 ([app_demo.md](app_demo.md)):

| Complaint | Route | Draft |
|---|---|---|
| English, speed post parcel | Department of Posts (confidence 0.79, uncalibrated) | 4 placeholders; factuality screen clean |
| Hindi, MGNREGA wages | Department of Rural Development (0.85) | Hindi draft, 6 placeholders; clean |
| Hinglish, land encroachment plus police refusing a case | **Ministry of Home Affairs**, which is wrong: this is a State matter. The State-matter rules miss "police case" and "zameen" | 8 placeholders; clean |

The third complaint is also in the evaluation set. It was used here only for demonstration; the rules
were not changed because of it.

**Browser.** Headless Chrome was driven with Playwright, from a scratch environment that is not a project
dependency. The run covered: empty page → validation error → routed result with figures → draft →
abstention → mobile width. There were **no console errors** after two fixes:
- source-report links had rendered as raw URLs, caused by flattening nested arrays only one level;
- the favicon request returned 404.

Review of the screenshots led to these changes on the abstention screen:
- it no longer pre-selects an alternative department;
- it labels the evidence "closest matches";
- it shows the State notice once;
- a new complaint now resets the draft form.

Screenshots are in [screenshots/](screenshots/):

| | |
|---|---|
| ![Routed result with verified figures](screenshots/3_routed_with_figures.png) | ![Draft review](screenshots/4_draft.png) |
| ![Abstention (State matter)](screenshots/5_abstain.png) | ![Mobile](screenshots/6_mobile.png) |

## 4. Not verified, or limited

- **The LLM path inside the app** has not run against the live API (no key). It is exercised only by unit
  tests with a fake client.
- **Rate limiting** is in memory and per process. Behind several workers or a proxy it would need a
  shared store and the real client IP.
- **No authentication and no deployment.** The app is meant to run locally. No public URL exists.
- **Accessibility:** checked by construction and by reading the screenshots. No screen-reader or
  automated accessibility audit was run.
