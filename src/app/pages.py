"""Generate the website's static HTML pages from one template (python -m src.app.pages).

Every page shares the head (SEO and Open Graph tags), the navigation slot, the footer's legal line and the
scripts. site.js fills the navigation, the footer columns and the data. The complaint assistant
(assistant.html) is written by hand because its form is static; keep its head and footer in step with
TEMPLATE. The landing page's text is static here so that it is readable without JavaScript and by search
engines; only live figures are filled in by site.js.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).with_name("static")
SITE_NAME = "Grievance Lens"

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{description}">
  <meta name="robots" content="index, follow">
  <meta name="theme-color" content="#e9eaec">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="Grievance Lens (unofficial)">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{description}">
  <meta name="twitter:card" content="summary">
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="/static/styles.css">
  <script src="/static/theme.js"></script>
</head>
<body data-page="{page}">
  <a class="skip" href="#main">Skip to content</a>
  <div id="site-nav"></div>
  <main id="main" class="wide">
{header}{body}
  </main>
  <footer id="site-footer" class="sitefoot">
{legal}
  </footer>
  <script src="/static/site.js"></script>
</body>
</html>
"""

LEGAL = """    <div class="legal">
      <p><strong>Grievance Lens is an independent portfolio project, not a government website.</strong> It is not
        affiliated with DARPG or CPGRAMS and never submits complaints. Data: DARPG monthly CPGRAMS reports for Central
        Ministries/Departments (#1–#53, May 2022 – Sep 2026). © 2026 Grievance Lens.</p>
      <nav aria-label="Legal"><a href="/privacy">Privacy policy</a><a href="/terms">Terms of use</a><a href="/about#contact">Contact</a></nav>
    </div>"""

HEADER = """    <header class="pagehead">
      <h1 id="pagetitle">{heading}</h1>
      <p>{lede}</p>
    </header>
"""

ROOT = '    <div id="page-root"></div>'


def icon(name: str) -> str:
    """Inline Lucide-style icon (stroke, currentColor); site.js has the same set for generated markup."""
    paths = {
        "chart": '<path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>',
        "landmark": '<path d="M3 22h18"/><path d="M6 18v-7"/><path d="M10 18v-7"/><path d="M14 18v-7"/>'
        '<path d="M18 18v-7"/><path d="M12 2 20 7H4z"/>',
        "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>'
        '<path d="M9 13h6"/><path d="M9 17h6"/>',
        "arrow": '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
        "check": '<path d="M20 6 9 17l-5-5"/>',
        "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
        "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
        "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5"/>'
        '<path d="M3 12c0 1.7 4 3 9 3s9-1.3 9-3"/>',
    }
    return (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true" focusable="false">{paths[name]}</svg>'
    )


def tier_items(items: list[str]) -> str:
    return "".join(f"<li>{icon('check')}<span>{t}</span></li>" for t in items)


HOME = f"""    <section class="hero" aria-labelledby="hero-title">
      <div class="reveal">
        <span class="eyebrow"><span class="pulse" aria-hidden="true"></span>Data current to <span data-fill="month">Sep 2026</span></span>
        <h1 id="hero-title">Public grievances, <span class="dim">read like a</span> system status.</h1>
        <p class="sub">An independent look at how Central Government ministries handle citizens' grievances,
          built from 53 monthly DARPG reports, plus an assistant that helps you prepare your own complaint.</p>
        <div class="actions">
          <a class="btn primary" href="/trends">Explore the data {icon("arrow")}</a>
          <a class="btn" href="/assistant">{icon("file")} File a complaint</a>
        </div>
        <div class="meta">
          <span>{icon("shield")} Not a government website</span>
          <span>{icon("lock")} Nothing you type is stored</span>
          <span>{icon("database")} Every figure traced to its report page</span>
        </div>
      </div>
      <div class="stage reveal d2" id="stage" aria-hidden="true">
        <div class="ring"></div><div class="ring r2"></div><div class="floor"></div>
        <div class="monolith" id="monolith">
          <div class="face front"><div class="scan"></div></div><div class="face side"></div><div class="face cap"></div>
        </div>
        <div class="glass status-chip"><span class="pulse"></span><span>Pending at month end<b data-fill="pending">—</b></span></div>
        <div class="status-panel">
          <div class="head"><span>Latest report</span><span class="ok"><span class="pulse"></span><span data-fill="report">#53</span></span></div>
          <div class="metric"><span>Received</span><b data-fill="receipts">—</b></div>
          <div class="metric"><span>Disposed of</span><b data-fill="disposals">—</b></div>
          <div class="metric"><span>Disposed per 100 received</span><b data-fill="ratio">—</b></div>
        </div>
      </div>
    </section>

    <section class="glass statusbar reveal d3" aria-label="Coverage">
      <div><div class="k"><span class="pulse"></span>Reports analysed</div><div class="v" data-fill="reports">53</div></div>
      <div><div class="k">Ministries and departments</div><div class="v" data-fill="ministries">92</div></div>
      <div><div class="k">Months covered</div><div class="v" data-fill="months">57</div></div>
      <div><div class="k">Complaint assistant</div><div class="v" data-fill="assistant">Online</div></div>
    </section>

    <section class="section" aria-labelledby="features-title">
      <div class="section-head">
        <span class="eyebrow">What you can do</span>
        <h2 id="features-title">Three instruments, one source of truth.</h2>
        <p>Every number comes from DARPG's published monthly reports, with the report and page it came from.</p>
      </div>
      <div class="cards3">
        <article class="glass feature">
          <div class="icon">{icon("chart")}</div>
          <h3>Track national trends</h3>
          <p>Grievances received, disposed of and pending every month since 2022, with statistical trend tests
            rather than eyeballed lines.</p>
          <a class="more" href="/trends">See the trends {icon("arrow")}</a>
        </article>
        <article class="glass feature">
          <div class="icon">{icon("landmark")}</div>
          <h3>Compare ministries fairly</h3>
          <p>All 92 bodies with their backlog history, list appearances and a scorecard that always shows its
            uncertainty.</p>
          <a class="more" href="/ministries">Browse ministries {icon("arrow")}</a>
        </article>
        <article class="glass feature">
          <div class="icon">{icon("file")}</div>
          <h3>Prepare your complaint</h3>
          <p>Describe the problem in English, Hindi or Hinglish. Get the likely department and a draft to submit
            yourself on CPGRAMS.</p>
          <a class="more" href="/assistant">Open the assistant {icon("arrow")}</a>
        </article>
      </div>
    </section>

    <section class="section" aria-labelledby="pulse-title">
      <div class="section-head">
        <span class="eyebrow">Live readout</span>
        <h2 id="pulse-title">Received vs disposed of, month by month.</h2>
        <p>Hover the chart for exact values. The table view is below it.</p>
      </div>
      <div class="card"><div id="home-chart"><p class="loading">Loading data…</p></div></div>
    </section>

    <section class="section" aria-labelledby="findings-title">
      <div class="section-head">
        <span class="eyebrow">What the data says</span>
        <h2 id="findings-title">Three findings, with their sources.</h2>
        <p>Computed from the reports, not opinions. Each card names the test or table behind it.</p>
      </div>
      <div class="cards3" id="findings"><p class="loading">Loading findings…</p></div>
    </section>

    <section class="section" aria-labelledby="ways-title">
      <div class="section-head">
        <span class="eyebrow">Ways to use it</span>
        <h2 id="ways-title">Free, with no account.</h2>
        <p>Pick the path that fits what you need today.</p>
      </div>
      <div class="tiers">
        <article class="glass tier">
          <div class="name">Explore</div>
          <div class="price">Free <small>/ no sign-up</small></div>
          <p class="desc">For citizens, journalists and students.</p>
          <ul>{tier_items(["National trends and tests", "All 92 ministries", "Hover values and table views"])}</ul>
          <a class="btn" href="/trends">Explore the data</a>
        </article>
        <article class="glass tier featured">
          <span class="flag">Start here</span>
          <div class="name">File a complaint</div>
          <div class="price">Free <small>/ you submit it</small></div>
          <p class="desc">For anyone with a grievance against a Central ministry.</p>
          <ul>{tier_items(["Likely department, with alternatives", "Draft in English or Hindi", "Never stored, never submitted for you"])}</ul>
          <a class="btn" href="/assistant">Prepare a complaint</a>
        </article>
        <article class="glass tier">
          <div class="name">Analyse</div>
          <div class="price">Open <small>/ methods</small></div>
          <p class="desc">For researchers who want to check the work.</p>
          <ul>{tier_items(["Methods and limits explained", "Scorecard with rank intervals", "Every figure traced to a page"])}</ul>
          <a class="btn" href="/about">Read the methods</a>
        </article>
      </div>
    </section>

    <section class="final-cta" aria-labelledby="cta-title">
      <span class="eyebrow">Ready when you are</span>
      <h2 id="cta-title">Have a grievance with a Central ministry?</h2>
      <p>Find the right department and a clear draft in about two minutes. You stay in control: nothing is stored
        and nothing is submitted for you.</p>
      <div class="actions">
        <a class="btn" href="/assistant">Prepare a complaint {icon("arrow")}</a>
        <a class="btn ghost" href="https://pgportal.gov.in/" target="_blank" rel="noopener">Official CPGRAMS portal</a>
      </div>
    </section>
"""

ABOUT = """    <section class="card">
      <h2>Data</h2>
      <p>53 monthly reports published by the Department of Administrative Reforms and Public Grievances (DARPG) on how
        Central Ministries/Departments handle grievances on CPGRAMS (May 2022 – Sep 2026). 36,718 values were extracted
        from the PDFs; each keeps its report, page and table. 92 ministries and departments appear under 121 printed names.</p>
      <p>Twelve randomly sampled values were checked against the PDF pages (12 of 12 match), and the whole pipeline was
        re-run from the PDFs into a fresh database with identical results. These checks were done by an AI assistant at
        the project owner's request, not by an independent person.</p>
    </section>
    <section class="card">
      <h2>Methods</h2>
      <ul>
        <li>Each month's figure comes from the latest report that prints it; partial months are flagged.</li>
        <li>Trends use the Mann–Kendall test with an autocorrelation correction, Sen's slope for size, and false-discovery
          control across ministries.</li>
        <li>Lists are compared only within one threshold, rankings only within one method era and group, and absence
          from a list is never treated as zero.</li>
        <li>The scorecard is a project construct, not an official rating, and is always shown with its 90% rank interval.</li>
      </ul>
    </section>
    <section class="card">
      <h2>The complaint assistant</h2>
      <p>The assistant suggests a department using the official <em>Allocation of Business Rules</em> (Cabinet Secretariat)
        and project-written keywords, asks a question when it is unsure, and drafts a complaint using only what you wrote.
        On 300 synthetic test complaints it chose the right department first 65% of the time and had it in its top three
        89% of the time, which is why it always shows alternatives. You submit complaints yourself on the official
        <a href="https://pgportal.gov.in/" target="_blank" rel="noopener">CPGRAMS portal</a>.</p>
    </section>
    <section class="card">
      <h2>What this cannot tell you</h2>
      <ul>
        <li>Why a backlog rose or fell: the data are observational.</li>
        <li>How your own complaint will be handled: these are aggregate figures.</li>
        <li>Anything about State Government grievances: only Central Ministries/Departments are covered.</li>
      </ul>
    </section>
    <section class="card" id="contact">
      <h2>Contact</h2>
      <p>Spotted a wrong figure or have a question about the project? <span id="contact-line">Contact details
        appear here once the project's public links are set.</span> For a grievance against a ministry, use the
        <a href="https://pgportal.gov.in/" target="_blank" rel="noopener">official CPGRAMS portal</a>: this site cannot
        act on complaints.</p>
    </section>"""

PRIVACY = """    <section class="card">
      <h2>In short</h2>
      <ul>
        <li>This site does <strong>not store</strong> your complaint. It has no database of complaints, no accounts,
          no cookies, no analytics and no third-party scripts.</li>
        <li>It <strong>never submits</strong> anything to the government. You copy the draft and submit it yourself
          on the official <a href="https://pgportal.gov.in/" target="_blank" rel="noopener">CPGRAMS portal</a>.</li>
        <li>Please do not type your name, address, phone number, Aadhaar or bank details. The draft leaves
          placeholders for anything personal.</li>
      </ul>
    </section>
    <section class="card">
      <h2>What you type in the complaint assistant</h2>
      <ul>
        <li>Your complaint and the details you add are sent to this site's server to suggest a department and build a
          draft. They are processed in memory and discarded when the response is sent.</li>
        <li>The server's logs record only the page or action requested, the result code and the time taken, never the
          text you typed.</li>
        <li>To limit abuse, your IP address is kept in memory for about a minute to count requests. The hosting
          provider also keeps standard access logs (IP address, page and time) under its own policy.</li>
        <li>The draft exists only in your browser tab until you copy it. Closing the tab removes it.</li>
      </ul>
    </section>
    <section class="card">
      <h2>AI provider</h2>
      <p id="ai-status" class="notice">Checking which AI provider is in use…</p>
      <p>By default no AI service is used: the department is suggested by this site's own search and rules, and
        nothing leaves the server. If an AI provider is switched on, the complaint is sent to it to choose between
        the suggested departments, after these steps:</p>
      <ul>
        <li>Phone numbers, Aadhaar-like numbers, PAN, e-mail addresses, bank account and card numbers and IFSC codes
          are replaced with placeholders before sending. Names and addresses written in free text are
          <strong>not</strong> detected, which is why you should leave them out.</li>
        <li>Only the complaint and the candidate departments are sent, never your other details.</li>
        <li>Only paid API plans are used, under which the provider does not use the text to train or improve its
          models. Providers keep requests for a limited time to detect abuse.</li>
        <li>To save cost, the AI's answer (the department chosen and a short reason) is kept on the server so that an
          identical complaint gets the same answer without a second call. The reason can paraphrase your complaint.
          This cache is deleted whenever the server restarts.</li>
      </ul>
    </section>
    <section class="card">
      <h2>The statistics pages</h2>
      <p>They show published, aggregate figures about ministries. They contain no information about any individual
        complainant.</p>
    </section>"""

TERMS = """    <section class="card">
      <h2>What this site is</h2>
      <p>Grievance Lens is an independent portfolio project. It is <strong>not</strong> a government website, is not
        affiliated with or endorsed by DARPG, CPGRAMS or any ministry, and cannot act on complaints.</p>
    </section>
    <section class="card">
      <h2>Using the statistics</h2>
      <ul>
        <li>Figures are copied or derived from DARPG's public monthly reports and may contain extraction errors. Check
          the original report before relying on a figure; each page names its source.</li>
        <li>The scorecard and trend labels are this project's analysis, not official ratings.</li>
        <li>You may quote the figures with a link to this site and to the original DARPG report.</li>
      </ul>
    </section>
    <section class="card">
      <h2>Using the complaint assistant</h2>
      <ul>
        <li>Department suggestions can be wrong. Read the alternatives and the official mandate before choosing.</li>
        <li>You are responsible for what you submit. Check every detail of the draft, fill in the placeholders and
          submit it yourself on the official <a href="https://pgportal.gov.in/" target="_blank" rel="noopener">CPGRAMS
          portal</a>.</li>
        <li>Do not use the assistant for emergencies. For a risk to life or safety, contact the emergency services
          (112 in India).</li>
        <li>Do not submit false, abusive or automated requests; the service limits request rates.</li>
      </ul>
    </section>
    <section class="card">
      <h2>No warranty</h2>
      <p>The site is provided as is, without any warranty. The project is not liable for decisions made using it.
        These terms may change; the version on this page applies. See also the <a href="/privacy">privacy policy</a>.</p>
    </section>"""

# file -> (data-page, <title>, heading, lede / meta description, body); heading None = no page header
PAGES = {
    "index.html": (
        "home",
        "Grievance Lens: an independent look at public grievances in India",
        None,
        "How Central Government ministries handle citizens' grievances, from 53 monthly DARPG reports, plus an "
        "assistant that helps you prepare a complaint to submit yourself on CPGRAMS. Unofficial.",
        HOME,
    ),
    "trends.html": (
        "trends",
        "National trends",
        "National trends",
        "Grievances received, disposed of and pending across all Central Ministries/Departments.",
        ROOT,
    ),
    "ministries.html": (
        "ministries",
        "Ministries",
        "Ministries and departments",
        "All 92 bodies that appear in the reports. Select one to see its full history.",
        ROOT,
    ),
    "ministry.html": (
        "ministry",
        "Ministry",
        "Ministry",
        "Published figures, backlog history, ranking history and list appearances.",
        ROOT,
    ),
    "scorecard.html": (
        "scorecard",
        "Scorecard",
        "Ministry scorecard",
        "Four published indicators combined within each volume group, with the uncertainty of every rank.",
        ROOT,
    ),
    "lists.html": (
        "lists",
        "Listings",
        "Repeated listings",
        "Which ministries appear again and again in the lists DARPG publishes each month.",
        ROOT,
    ),
    "demand.html": (
        "demand",
        "Demand vs backlog",
        "Demand vs backlog",
        "Separating backlogs that grow because more grievances arrive from those that grow anyway.",
        ROOT,
    ),
    "about.html": (
        "about",
        "About",
        "About this project",
        "What the data are, how they were processed, what they cannot tell you, and how to get in touch.",
        ABOUT,
    ),
    "privacy.html": (
        "privacy",
        "Privacy policy",
        "Privacy policy",
        "What happens to what you type, in plain words.",
        PRIVACY,
    ),
    "terms.html": (
        "terms",
        "Terms of use",
        "Terms of use",
        "The rules for using this site, in plain words.",
        TERMS,
    ),
}


def render(page: str, title: str, heading: str | None, lede: str, body: str) -> str:
    full_title = title if page == "home" else f"{title} · {SITE_NAME}"
    header = HEADER.format(heading=heading, lede=lede) if heading else ""
    return TEMPLATE.format(
        page=page, title=full_title, description=lede, header=header, body=body, legal=LEGAL
    )


def main() -> None:
    for name, spec in PAGES.items():
        (STATIC / name).write_text(render(*spec), encoding="utf-8", newline="\n")
    print(f"wrote {len(PAGES)} pages to {STATIC}")


if __name__ == "__main__":
    main()
