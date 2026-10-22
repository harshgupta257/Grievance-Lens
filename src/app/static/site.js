// Website shell and analytics pages. Wrapped in a closure so it can sit beside the assistant's app.js.
// All text from the API is inserted with textContent / text nodes, never as HTML.
(() => {
  "use strict";

  const NAV = [
    ["/", "Overview"], ["/trends", "Trends"], ["/ministries", "Ministries"], ["/lists", "Listings"],
    ["/scorecard", "Scorecard"], ["/demand", "Demand vs backlog"], ["/about", "About"],
  ];
  const SVGNS = "http://www.w3.org/2000/svg";
  const nf = new Intl.NumberFormat("en-IN");
  const fmt = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "—" : nf.format(Number(v.toFixed ? v.toFixed(d) : v)));
  const pct = (v, d = 1) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);

  function h(tag, attrs = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v);
    }
    for (const c of kids.flat(Infinity)) if (c !== null && c !== undefined) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return n;
  }
  function s(tag, attrs = {}) {
    const n = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  }
  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`Could not load data (${r.status}).`);
    return r.json();
  }
  function failed(target, err) { target.replaceChildren(h("p", { class: "error", text: err.message })); }

  // ---------------- navigation ----------------
  function renderNav() {
    const slot = document.getElementById("site-nav");
    if (!slot) return;
    const here = location.pathname.replace(/\/$/, "") || "/";
    const active = here === "/ministry" ? "/ministries" : here;
    slot.replaceChildren(h("nav", { class: "sitenav", "aria-label": "Main" },
      h("div", { class: "wrap" },
        h("a", { class: "brand", href: "/", "aria-label": "Grievance Lens, unofficial: home" }, "Grievance Lens ", h("span", { class: "unofficial", text: "Unofficial" })),
        NAV.map(([href, label]) => h("a", { class: "nav", href, "aria-current": href === active ? "page" : null, text: label })),
        h("a", { class: "cta", href: "/assistant", "aria-current": here === "/assistant" ? "page" : null, text: "File a complaint" }))));
  }

  // ---------------- charts ----------------
  function tooltip(box) {
    const t = h("div", { class: "tooltip", role: "status", hidden: "" });
    box.append(t);
    return {
      show(lines, x, y) {
        t.replaceChildren(...lines.map(([k, v]) => h("div", {}, k ? h("span", { class: "k", text: k + " " }) : null, v)));
        t.hidden = false;
        const bw = box.clientWidth, tw = t.offsetWidth;
        t.style.left = `${Math.min(Math.max(0, x + 12), bw - tw)}px`;
        t.style.top = `${Math.max(0, y - 10)}px`;
      },
      hide() { t.hidden = true; },
    };
  }

  function niceMax(v) {
    const p = 10 ** Math.floor(Math.log10(v || 1));
    return Math.ceil(v / p) * p;
  }

  // line chart: one x axis (labels), one y scale; series = [{name, color, values}]
  function lineChart(box, { labels, series, height = 260, yFormat = (v) => fmt(v), note }) {
    box.replaceChildren();
    const W = 760, H = height, L = 64, R = series.length > 1 ? 92 : 16, T = 12, B = 30;
    const all = series.flatMap((x) => x.values).filter((v) => v !== null);
    const ymax = niceMax(Math.max(...all)), ymin = 0;
    const X = (i) => L + (i * (W - L - R)) / Math.max(1, labels.length - 1);
    const Y = (v) => T + (H - T - B) * (1 - (v - ymin) / (ymax - ymin));
    if (series.length > 1) {
      box.append(h("div", { class: "legend" }, series.map((x) => h("span", {}, h("span", { class: "sw", style: `background:${x.color}` }), x.name))));
    }
    const wrap = h("div", { class: "chart" });
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": series.map((x) => x.name).join(" and ") + " by month" });
    for (let k = 0; k <= 4; k++) {
      const v = ymin + ((ymax - ymin) * k) / 4, y = Y(v);
      svg.append(s("line", { class: "gridline", x1: L, x2: W - R, y1: y, y2: y }));
      const t = s("text", { class: "axis-label", x: L - 8, y: y + 4, "text-anchor": "end" }); t.textContent = yFormat(v); svg.append(t);
    }
    const step = Math.ceil(labels.length / 8);
    labels.forEach((lab, i) => {
      const last = labels.length - 1;
      if (i !== last && (i % step || last - i < step * 0.6)) return; // keep the end tick, drop one crowding it
      const t = s("text", { class: "axis-label", x: X(i), y: H - 8, "text-anchor": "middle" }); t.textContent = lab; svg.append(t);
    });
    series.forEach((x) => {
      let d = "", pen = false;
      x.values.forEach((v, i) => {
        if (v === null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${X(i).toFixed(1)} ${Y(v).toFixed(1)} `; pen = true;
      });
      svg.append(s("path", { d, fill: "none", stroke: x.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    });
    if (series.length > 1) {
      // direct labels at the line ends, nudged apart so they never overlap (min 14px)
      const lastI = labels.length - 1;
      const placed = series.map((x) => ({ name: x.name, y: Y(x.values[lastI]) + 4 })).sort((a, b) => a.y - b.y);
      for (let k = 1; k < placed.length; k++) placed[k].y = Math.max(placed[k].y, placed[k - 1].y + 14);
      placed.forEach((p) => { const t = s("text", { class: "series-label", x: X(lastI) + 8, y: p.y }); t.textContent = p.name; svg.append(t); });
    }
    const cross = s("line", { class: "crosshair", y1: T, y2: H - B, visibility: "hidden" });
    const dots = series.map((x) => { const c = s("circle", { r: 4, fill: x.color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }); svg.append(c); return c; });
    svg.append(cross);
    const hit = s("rect", { x: L, y: T, width: W - L - R, height: H - T - B, fill: "transparent" });
    svg.append(hit);
    wrap.append(svg);
    box.append(wrap);
    const tip = tooltip(wrap);
    const move = (ev) => {
      const r = svg.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W;
      const i = Math.max(0, Math.min(labels.length - 1, Math.round(((px - L) * (labels.length - 1)) / (W - L - R))));
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible");
      series.forEach((x, j) => {
        const v = x.values[i];
        if (v === null) { dots[j].setAttribute("visibility", "hidden"); return; }
        dots[j].setAttribute("cx", X(i)); dots[j].setAttribute("cy", Y(v)); dots[j].setAttribute("visibility", "visible");
      });
      tip.show([["", labels[i]], ...series.map((x) => [x.name, x.values[i] === null ? "—" : yFormat(x.values[i])])],
        (X(i) / W) * r.width, (T / H) * r.height);
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerleave", () => { tip.hide(); cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); });
    if (note) box.append(h("p", { class: "chartnote", text: note }));
  }

  // horizontal bars: rows = [{label, value, href, tip}]
  function barChart(box, { rows, max, valueFormat = (v) => fmt(v), color = "var(--series-1)", label }) {
    box.replaceChildren();
    const W = 760, rowH = 26, L = 300, R = 70, T = 4;
    const H = T + rows.length * rowH + 4;
    const vmax = max || Math.max(...rows.map((r) => r.value));
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": label || "Bar chart" });
    const wrap = h("div", { class: "chart" });
    const tip = tooltip(wrap);
    rows.forEach((r, i) => {
      const y = T + i * rowH;
      const t = s("text", { class: "axis-label", x: L - 8, y: y + 17, "text-anchor": "end" });
      t.textContent = r.label.length > 46 ? r.label.slice(0, 44) + "…" : r.label;
      svg.append(t);
      const w = Math.max(2, ((W - L - R) * r.value) / vmax);
      const bar = s("rect", { x: L, y: y + 5, width: w, height: rowH - 10, rx: 4, fill: color });
      svg.append(bar);
      const v = s("text", { class: "axis-label", x: L + w + 6, y: y + 17 }); v.textContent = valueFormat(r.value); svg.append(v);
      const hit = s("rect", { x: 0, y, width: W, height: rowH, fill: "transparent", style: r.href ? "cursor:pointer" : "" });
      hit.addEventListener("pointermove", (ev) => {
        const b = svg.getBoundingClientRect();
        tip.show(r.tip || [["", r.label], ["Value", valueFormat(r.value)]], ev.clientX - b.left, ev.clientY - b.top);
      });
      hit.addEventListener("pointerleave", () => tip.hide());
      if (r.href) hit.addEventListener("click", () => { location.href = r.href; });
      svg.append(hit);
    });
    wrap.prepend(svg);
    box.append(wrap);
  }

  // rank with interval: rows = [{label, rank, lo, hi, href}], rank 1 at left
  function intervalChart(box, { rows, n }) {
    box.replaceChildren();
    const W = 760, rowH = 22, L = 300, R = 20, T = 22;
    const H = T + rows.length * rowH + 6;
    const X = (r) => L + ((r - 1) * (W - L - R)) / Math.max(1, n - 1);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Rank in group with 90% interval" });
    const wrap = h("div", { class: "chart" });
    const tip = tooltip(wrap);
    [1, Math.round(n / 4), Math.round(n / 2), Math.round((3 * n) / 4), n].forEach((r) => {
      svg.append(s("line", { class: "gridline", x1: X(r), x2: X(r), y1: T - 4, y2: H }));
      const t = s("text", { class: "axis-label", x: X(r), y: 12, "text-anchor": "middle" }); t.textContent = `rank ${r}`; svg.append(t);
    });
    rows.forEach((r, i) => {
      const y = T + i * rowH + rowH / 2;
      const t = s("text", { class: "axis-label", x: L - 8, y: y + 4, "text-anchor": "end" });
      t.textContent = r.label.length > 46 ? r.label.slice(0, 44) + "…" : r.label; svg.append(t);
      svg.append(s("line", { x1: X(r.lo), x2: X(r.hi), y1: y, y2: y, stroke: "var(--series-1)", "stroke-opacity": 0.45, "stroke-width": 4, "stroke-linecap": "round" }));
      svg.append(s("circle", { cx: X(r.rank), cy: y, r: 5, fill: "var(--series-1)", stroke: "var(--surface)", "stroke-width": 2 }));
      const hit = s("rect", { x: 0, y: y - rowH / 2, width: W, height: rowH, fill: "transparent", style: "cursor:pointer" });
      hit.addEventListener("pointermove", (ev) => {
        const b = svg.getBoundingClientRect();
        tip.show([["", r.label], ["Rank", `${r.rank} of ${n}`], ["90% interval", `${r.lo}–${r.hi}`], ["Score", fmt(r.score, 1)]], ev.clientX - b.left, ev.clientY - b.top);
      });
      hit.addEventListener("pointerleave", () => tip.hide());
      hit.addEventListener("click", () => { location.href = r.href; });
      svg.append(hit);
    });
    wrap.prepend(svg);
    box.append(wrap);
  }

  // scatter: points = [{x, y, label, href, tip}], refs = numbers drawn as dashed lines on both axes
  function scatter(box, { points, xLabel, yLabel, refs = [] }) {
    box.replaceChildren();
    const W = 760, H = 470, L = 64, R = 16, T = 16, B = 50;
    const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
    const xr = Math.max(Math.abs(Math.min(...xs)), Math.abs(Math.max(...xs)), 3) * 1.08;
    const yr = Math.max(Math.abs(Math.min(...ys)), Math.abs(Math.max(...ys)), 3) * 1.08;
    const X = (v) => L + ((v + xr) / (2 * xr)) * (W - L - R);
    const Y = (v) => T + (1 - (v + yr) / (2 * yr)) * (H - T - B);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${yLabel} against ${xLabel}` });
    const wrap = h("div", { class: "chart" });
    const tip = tooltip(wrap);
    svg.append(s("line", { class: "gridline", x1: X(0), x2: X(0), y1: T, y2: H - B }));
    svg.append(s("line", { class: "gridline", x1: L, x2: W - R, y1: Y(0), y2: Y(0) }));
    refs.forEach((v) => {
      svg.append(s("line", { class: "refline", x1: X(v), x2: X(v), y1: T, y2: H - B }));
      svg.append(s("line", { class: "refline", x1: L, x2: W - R, y1: Y(v), y2: Y(v) }));
    });
    const ticks = (r) => { const st = r > 12 ? 5 : r > 6 ? 2 : 1, out = []; for (let v = -Math.floor(r / st) * st; v <= r; v += st) out.push(v); return out; };
    ticks(xr).forEach((v) => { const t = s("text", { class: "axis-label", x: X(v), y: H - B + 14, "text-anchor": "middle" }); t.textContent = `${v > 0 ? "+" : ""}${v}%`; svg.append(t); });
    ticks(yr).forEach((v) => { const t = s("text", { class: "axis-label", x: L - 6, y: Y(v) + 4, "text-anchor": "end" }); t.textContent = `${v > 0 ? "+" : ""}${v}%`; svg.append(t); });
    const corner = (txt, x, y, anchor) => { const t = s("text", { class: "axis-label", x, y, "text-anchor": anchor }); t.textContent = txt; svg.append(t); };
    corner("demand rising · backlog rising", W - R - 4, T + 12, "end");
    corner("demand falling · backlog rising", L + 4, T + 12, "start");
    corner("demand rising · backlog falling", W - R - 4, H - B - 6, "end");
    corner("demand falling · backlog falling", L + 4, H - B - 6, "start");
    const xl = s("text", { class: "axis-label", x: (L + W - R) / 2, y: H - 10, "text-anchor": "middle" }); xl.textContent = xLabel; svg.append(xl);
    const yl = s("text", { class: "axis-label", x: 14, y: (T + H - B) / 2, "text-anchor": "middle", transform: `rotate(-90 14 ${(T + H - B) / 2})` }); yl.textContent = yLabel; svg.append(yl);
    points.forEach((p) => {
      const c = s("circle", { cx: X(p.x), cy: Y(p.y), r: 5, fill: "var(--series-1)", "fill-opacity": 0.85, stroke: "var(--surface)", "stroke-width": 2, style: "cursor:pointer" });
      c.addEventListener("pointermove", (ev) => { const b = svg.getBoundingClientRect(); tip.show(p.tip, ev.clientX - b.left, ev.clientY - b.top); });
      c.addEventListener("pointerleave", () => tip.hide());
      c.addEventListener("click", () => { location.href = p.href; });
      svg.append(c);
    });
    wrap.prepend(svg);
    box.append(wrap);
  }

  function table(head, rows, numericCols = []) {
    return h("div", { class: "tablewrap" }, h("table", {},
      h("thead", {}, h("tr", {}, head.map((c, i) => h("th", { class: numericCols.includes(i) ? "num" : null, text: c })))),
      h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c, i) => h("td", { class: numericCols.includes(i) ? "num" : null }, c)))))));
  }
  const mlink = (key, name) => (key ? h("a", { href: `/ministry?key=${encodeURIComponent(key)}`, text: name }) : name);
  const trendBadge = (t) => (!t ? "—" : h("span", { class: "badge" + (t === "increasing" ? " up" : t === "decreasing" ? " down" : ""), text: t }));
  const tile = (label, value, sub) => h("div", { class: "tile" }, h("div", { class: "label", text: label }), h("div", { class: "value", text: value }), sub ? h("div", { class: "sub", text: sub }) : null);

  // ---------------- pages ----------------
  async function pageHome(root) {
    const o = await getJSON("/api/overview");
    const l = o.latest, c = o.coverage;
    root.replaceChildren(
      h("div", { class: "tiles" },
        tile(`Grievances received (${l.month})`, fmt(l.receipts), "Central Ministries/Departments"),
        tile(`Disposed of (${l.month})`, fmt(l.disposals), `report #${l.source_report}`),
        tile("Pending at month end", fmt(l.pending), l.month),
        tile("Reports analysed", fmt(c.reports), `${c.first_report_month} to ${c.last_report_month} · ${c.ministries} ministries`)),
      h("section", { class: "card" }, h("h2", { text: "Grievances received and disposed of each month" }), h("div", { id: "home-chart" })),
      h("section", { class: "card" }, h("h2", { text: "Explore" }),
        h("div", { class: "linkcards" },
          [["/trends", "National trends", "Monthly receipts, disposals and backlog, with statistical trend tests."],
           ["/ministries", "Ministries and departments", "All 92 bodies: listings, backlog trend, scorecard rank and full history."],
           ["/lists", "Repeated listings", "Who appears again and again in the published pendency and closing-time lists."],
           ["/scorecard", "Scorecard", "A transparent within-group comparison, always shown with its uncertainty."],
           ["/demand", "Demand vs backlog", "Is a backlog growing because more grievances arrive?"],
           ["/assistant", "File a complaint", "Find the right department and prepare a draft to submit yourself on CPGRAMS."]]
            .map(([href, t, d]) => h("a", { class: "linkcard", href }, h("strong", { text: t }), h("span", { text: d }))))),
    );
    const m = o.monthly;
    lineChart(document.getElementById("home-chart"), {
      labels: m.map((r) => r.month),
      series: [{ name: "Received", color: "var(--series-1)", values: m.map((r) => r.receipts) },
               { name: "Disposed", color: "var(--series-2)", values: m.map((r) => r.disposals) }],
      note: "Source: DARPG monthly CPGRAMS reports; each month uses the latest report that prints it. The March 2024 peak is one department (Rural Development: 344,265 of 476,498).",
    });
  }

  async function pageTrends(root) {
    const o = await getJSON("/api/overview");
    const m = o.monthly;
    const tests = o.trend_tests;
    const sentence = (t) => (t.conclusion === "no significant trend" ? "no significant trend" : `${t.conclusion}, about ${fmt(t.sen_slope)} a month (q = ${t.q_value.toFixed(3)})`);
    root.replaceChildren(
      h("section", { class: "card" }, h("h2", { text: "Received and disposed of, per month" }), h("div", { id: "c1" })),
      h("section", { class: "card" }, h("h2", { text: "Pending at month end" }), h("div", { id: "c2" })),
      h("section", { class: "card" }, h("h2", { text: "Is there a trend? (Apr 2023 – Sep 2026)" }),
        h("p", { class: "hint", text: "Mann–Kendall test corrected for autocorrelation, with Sen's slope; q-values control false discoveries." }),
        table(["Series", "Result"], tests.filter((t) => t.subject !== "disposal_to_receipt_ratio").map((t) => [t.subject, sentence(t)]).concat(
          tests.filter((t) => t.subject === "disposal_to_receipt_ratio").map((t) => ["disposals ÷ receipts", sentence(t)]))),
        h("p", { class: "hint", text: `Across 90 ministries in the monthly panel (Jun 2024 – Jan 2026): ${o.ministry_trends.increasing || 0} rising backlogs, ${o.ministry_trends.decreasing || 0} falling, ${o.ministry_trends["no significant trend"] || 0} no significant trend.` })),
      h("section", { class: "card" }, h("h2", { text: "By calendar year (complete months only)" }),
        table(["Year", "Months", "Mean received / month", "Mean disposed / month", "Disposed ÷ received", "Pending at last month"],
          o.by_year.map((y) => [y.year, y.complete_months, fmt(y.mean_monthly_receipts), fmt(y.mean_monthly_disposals), y.disposal_to_receipt_ratio.toFixed(3), fmt(y.pending_last_complete_month)]),
          [1, 2, 3, 4, 5])),
    );
    const labels = m.map((r) => r.month);
    lineChart(document.getElementById("c1"), {
      labels,
      series: [{ name: "Received", color: "var(--series-1)", values: m.map((r) => r.receipts) },
               { name: "Disposed", color: "var(--series-2)", values: m.map((r) => r.disposals) }],
    });
    lineChart(document.getElementById("c2"), {
      labels, series: [{ name: "Pending", color: "var(--series-1)", values: m.map((r) => r.pending) }],
      note: "Pending is a stock (a count on one day), so it is shown on its own scale rather than with monthly flows.",
    });
    document.getElementById("c2").append(h("details", { class: "datatable" }, h("summary", { text: "Show the monthly data table" }),
      table(["Month", "Received", "Disposed", "Pending", "Source report"], m.map((r) => [r.month + (r.has_partial_value ? " (partial)" : ""), fmt(r.receipts), fmt(r.disposals), fmt(r.pending), `#${r.source_report}`]), [1, 2, 3])));
  }

  async function pageMinistries(root) {
    const rows = await getJSON("/api/ministries");
    const search = h("input", { type: "search", placeholder: "Search ministries…", "aria-label": "Search ministries" });
    const group = h("select", { "aria-label": "GRAI group" }, h("option", { value: "", text: "All groups" }), h("option", { value: "A", text: "Group A (larger)" }), h("option", { value: "B", text: "Group B (smaller)" }));
    const body = h("div");
    const draw = () => {
      const q = search.value.toLowerCase();
      const list = rows.filter((r) => r.name.toLowerCase().includes(q) && (!group.value || r.grai_group === group.value));
      body.replaceChildren(h("p", { class: "hint", text: `${list.length} of ${rows.length}` }), table(
        ["Ministry / department", "Group", "Listed >1,000 pending (of 53 reports)", "Pending, Jan 2026", "Backlog trend", "Scorecard rank", "Assistant"],
        list.map((r) => [mlink(r.ministry_key, r.name), r.grai_group || "—", r.over_1000_reports ?? "—", fmt(r.panel_pending_end), trendBadge(r.backlog_trend),
          r.rank_in_group ? `${r.rank_in_group} (${r.grai_group})` : "—", r.in_assistant ? "✓" : ""]),
        [2, 3, 5]));
    };
    search.addEventListener("input", draw); group.addEventListener("change", draw);
    root.replaceChildren(h("section", { class: "card" }, h("div", { class: "toolbar" }, search, group), body));
    draw();
  }

  async function pageMinistry(root) {
    const key = new URLSearchParams(location.search).get("key");
    if (!key) { root.replaceChildren(h("p", {}, "Choose a ministry on the ", h("a", { href: "/ministries", text: "Ministries" }), " page.")); return; }
    const d = await getJSON(`/api/ministries/${encodeURIComponent(key)}`);
    const p = d.performance;
    document.getElementById("pagetitle").textContent = p.department;
    document.title = `${p.department} · Grievance Lens`;
    const parts = [];
    parts.push(h("div", { class: "tiles" },
      tile("Latest report listing it", p.latest_reporting_month || "—"),
      ...(p.backlog_trend ? [tile("Pending, Jan 2026", fmt(p.backlog_trend.pending_end), `from ${fmt(p.backlog_trend.pending_start)} in Jun 2024`), tile("Backlog trend", p.backlog_trend.trend, p.backlog_trend.period)] : [])));
    if (d.mandate) {
      parts.push(h("section", { class: "card" }, h("h2", { text: "What this department handles" }),
        h("p", { class: "hint", text: `${d.mandate.official_name}${d.mandate.parent_ministry ? " · " + d.mandate.parent_ministry : ""}. Based on ${d.mandate.official_items} items of the official Allocation of Business Rules.` }),
        h("ul", {}, d.mandate.responsibilities.map((r) => h("li", { text: r.text }))),
        h("p", {}, h("a", { class: "button", href: "/assistant", text: "File a complaint with the assistant" }), " ",
          d.mandate.official_urls.map((u) => h("a", { href: u, target: "_blank", rel: "noopener", text: new URL(u).hostname + " ↗" })))));
    }
    parts.push(h("section", { class: "card" }, h("h2", { text: "Published figures" }),
      table(["Measure", "Value", "Period", "Source"], p.metrics.map((m) => [m.label, m.value, m.period, m.source])),
      p.unavailable.length ? h("div", {}, h("h3", { text: "Not available" }), h("ul", {}, p.unavailable.map((u) => h("li", { text: u })))) : null));
    if (p.pending_series.length > 1) parts.push(h("section", { class: "card" }, h("h2", { text: "Pending at month end (monthly panel)" }), h("div", { id: "pend" })));
    if (d.grai.length) parts.push(h("section", { class: "card" }, h("h2", { text: "Position in the official GRAI ranking" }), h("div", { id: "grai" })));
    if (d.list_appearances.length) {
      parts.push(h("section", { class: "card" }, h("h2", { text: "Appearances in published lists" }),
        h("p", { class: "hint", text: "Each box is one report (#1 to #53); filled = listed. Not being listed means below the list's threshold, not zero." }),
        d.list_appearances.map((a) => h("div", { style: "margin-bottom:12px" }, h("div", { text: `${a.list_name}: ${a.reports.length} of ${a.reports_with_list} reports` }),
          h("div", { class: "strip", role: "img", "aria-label": `Listed in reports ${a.reports.join(", ")}` },
            Array.from({ length: 53 }, (_, i) => h("span", { class: a.reports.includes(i + 1) ? "on" : null, title: `Report #${i + 1}` })))))));
    }
    parts.push(h("section", { class: "card" }, h("h3", { text: "Read with care" }), h("ul", { class: "hint" }, p.caveats.map((c) => h("li", { text: c })))));
    root.replaceChildren(...parts);
    if (p.pending_series.length > 1) {
      lineChart(document.getElementById("pend"), { labels: p.pending_series.map((r) => r.period_end.slice(0, 7)),
        series: [{ name: "Pending", color: "var(--series-1)", values: p.pending_series.map((r) => r.pending) }],
        note: "Source: GRAI tables in DARPG reports #26–#45. Hover for values." });
    }
    if (d.grai.length) {
      lineChart(document.getElementById("grai"), { labels: d.grai.map((r) => r.report_month),
        series: [{ name: "Percentile in group", color: "var(--series-1)", values: d.grai.map((r) => r.rank_percentile) }], height: 220,
        yFormat: (v) => `${Math.round(v)}`,
        note: "100 = ranked first in its group. The ranking method changed several times, so compare only nearby months." });
    }
  }

  async function pageScorecard(root) {
    const sc = await getJSON("/api/scorecard");
    const groupSel = h("select", { "aria-label": "Group" }, h("option", { value: "A", text: "Group A (500+ grievances)" }), h("option", { value: "B", text: "Group B (fewer)" }));
    const box = h("div"), tbl = h("div");
    const draw = () => {
      // percentile bounds of simulated ranks can be fractional; show whole ranks
      const rows = sc[groupSel.value].map((r) => ({ ...r, rank_random_p05: r.rank_random_p05 === null ? null : Math.round(r.rank_random_p05), rank_random_p95: r.rank_random_p95 === null ? null : Math.round(r.rank_random_p95) }));
      const scored = rows.filter((r) => r.score !== null);
      intervalChart(box, { n: scored.length, rows: scored.map((r) => ({ label: r.name, rank: r.rank_in_group, lo: r.rank_random_p05, hi: r.rank_random_p95, score: r.score, href: `/ministry?key=${encodeURIComponent(r.ministry_key)}` })) });
      tbl.replaceChildren(table(["Rank (90% interval)", "Ministry", "Score", "GRAI pct", "Backlog trend pct", "Backlog vs demand pct", "Slow-closing pct"],
        rows.map((r) => [r.score === null ? "not scored" : `${r.rank_in_group} (${r.rank_random_p05}–${r.rank_random_p95})`, mlink(r.ministry_key, r.name), fmt(r.score, 1),
          fmt(r.grai_percentile_pctl), fmt(r.backlog_trend_pct_pctl), fmt(r.backlog_months_pctl), fmt(r.slow_closing_share_pctl)]), [2, 3, 4, 5, 6]));
    };
    groupSel.addEventListener("change", draw);
    root.replaceChildren(
      h("p", { class: "notice", text: "A project construct, not an official rating. Ministries are compared only within their volume group; the line shows where the rank lands under 1,000 different weightings (90% interval)." }),
      h("section", { class: "card" }, h("div", { class: "toolbar" }, groupSel), box),
      h("section", { class: "card" }, h("h2", { text: "Components (percentiles within the group; 100 = best)" }), tbl));
    draw();
  }

  async function pageLists(root) {
    const d = await getJSON("/api/listings");
    const sel = h("select", { "aria-label": "List" }, d.lists.map((l) => h("option", { value: l.list_code, text: `${l.list_name} (reports #${l.first_report}–#${l.last_report})` })));
    const caveat = h("p", { class: "hint" }), box = h("div"), tbl = h("div");
    const draw = () => {
      const l = d.lists.find((x) => x.list_code === sel.value);
      const rows = d.by_list[sel.value] || [];
      caveat.textContent = l.caveat + (l.reports_missing_in_span ? ` Not published in report(s): ${l.reports_missing_in_span}.` : "");
      barChart(box, { label: l.list_name, max: l.reports_with_list,
        rows: rows.slice(0, 15).map((r) => ({ label: r.name, value: r.appearances, href: `/ministry?key=${encodeURIComponent(r.ministry_key)}`,
          tip: [["", r.name], ["Listed", `${r.appearances} of ${r.reports_with_list} reports`], ["Longest unbroken run", `${r.longest_consecutive_run}`]] })),
        valueFormat: (v) => `${v} / ${l.reports_with_list}` });
      tbl.replaceChildren(table(["Ministry", "Times listed", "Share", "Longest run", "Still listed (run to latest)", "Median value when listed", "Note"],
        rows.map((r) => [mlink(r.ministry_key, r.name), `${r.appearances} / ${r.reports_with_list}`, pct(r.share_of_reports, 0), r.longest_consecutive_run, r.run_to_last_list, fmt(r.median_value), r.reorganisation_note || ""]), [1, 2, 3, 4, 5]));
    };
    sel.addEventListener("change", draw);
    root.replaceChildren(
      h("section", { class: "card" }, h("div", { class: "toolbar" }, sel), caveat, h("h2", { text: "Most frequently listed (top 15)" }), box),
      h("section", { class: "card" }, h("h2", { text: "All ministries ever listed" }), tbl));
    draw();
  }

  async function pageDemand(root) {
    const d = await getJSON("/api/demand-backlog");
    const cls = d.ministries.filter((m) => m.status === "classified");
    const counts = {};
    cls.forEach((m) => { counts[m.quadrant] = (counts[m.quadrant] || 0) + 1; });
    const box = h("div");
    root.replaceChildren(
      h("p", { class: "notice", text: "Rising demand is not poor performance. Each dot is a ministry (Sep 2024 – Jan 2026); dashed lines mark ±1% a month, the band treated as stable." }),
      h("section", { class: "card" }, box),
      h("section", { class: "card" }, h("h2", { text: "How many ministries in each pattern" }),
        table(["Pattern", "Ministries"], Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, v]) => [k, v]), [1]),
        h("p", { class: "hint", text: `${d.ministries.length - cls.length} ministries had too few grievances to classify reliably.` })),
      h("section", { class: "card" }, h("h2", { text: "All classified ministries" }),
        table(["Ministry", "Mean received / month", "Demand change % / month", "Backlog change % / month", "Pattern"],
          cls.sort((a, b) => b.mean_monthly_receipts - a.mean_monthly_receipts).map((m) => [mlink(m.ministry_key, m.name), fmt(m.mean_monthly_receipts), m.demand_rel_slope_pct.toFixed(2), m.backlog_rel_slope_pct.toFixed(2), m.quadrant]), [1, 2, 3])));
    scatter(box, { refs: [1, -1], xLabel: "Change in grievances received (% of average per month)", yLabel: "Change in pending (% per month)",
      points: cls.map((m) => ({ x: m.demand_rel_slope_pct, y: m.backlog_rel_slope_pct, href: `/ministry?key=${encodeURIComponent(m.ministry_key)}`,
        tip: [["", m.name], ["Demand", `${m.demand_rel_slope_pct.toFixed(1)}% / month`], ["Backlog", `${m.backlog_rel_slope_pct.toFixed(1)}% / month`], ["Avg received", fmt(m.mean_monthly_receipts)]] })) });
  }

  const PAGES = { home: pageHome, trends: pageTrends, ministries: pageMinistries, ministry: pageMinistry, scorecard: pageScorecard, lists: pageLists, demand: pageDemand };

  // privacy page: say which AI provider, if any, this server is configured to use right now
  async function privacyStatus() {
    const el = document.getElementById("ai-status");
    if (!el) return;
    try {
      const st = await getJSON("/health");
      const names = { anthropic: "Anthropic (Claude)", gemini: "Google (Gemini)" };
      el.textContent = st.llm_provider === "none"
        ? "Right now no AI provider is used. Your complaint does not leave this server."
        : `Right now the AI provider in use is ${names[st.llm_provider] || st.llm_provider} (model ${st.llm_model}).`;
    } catch (err) {
      el.textContent = "Could not check which AI provider is in use. Assume one may be, and follow the advice below.";
    }
  }

  renderNav();
  privacyStatus();
  const page = document.body.dataset.page;
  const root = document.getElementById("page-root");
  if (PAGES[page] && root) {
    root.replaceChildren(h("p", { class: "loading", text: "Loading data…" }));
    PAGES[page](root).catch((err) => failed(root, err));
  }
})();
