// Grievance Routing Assistant: page logic. No complaint text is stored in the browser or sent anywhere except
// this application's own API. All text from the API is inserted with textContent (never as HTML).
"use strict";

const $ = (id) => document.getElementById(id);
let lastRoute = null;
let lastComplaint = "";
let chosenKey = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v; else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

function setStatus(id, text, kind = "") {
  const s = $(id);
  s.textContent = text;
  s.className = "status" + (kind ? " " + kind : "");
}

async function api(path, body) {
  let res;
  try {
    res = await fetch(path, body ? {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    } : {});
  } catch {
    throw new Error("Could not reach the service. Check your connection and try again.");
  }
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    if (data && typeof data.error === "string") throw new Error(data.error);
    if (res.status === 422) throw new Error("Please check your input and try again.");
    if (res.status === 429) throw new Error("Too many requests. Please wait a minute and try again.");
    throw new Error("Something went wrong on the server (" + res.status + "). Please try again.");
  }
  return data;
}

$("complaint").addEventListener("input", () => {
  $("complaint-count").textContent = $("complaint").value.length + " / 4000";
});

$("route-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("complaint").value.trim();
  if (text.length < 15) {
    setStatus("route-status", "Please describe your grievance in at least 15 characters.", "error");
    $("complaint").focus();
    return;
  }
  const btn = $("route-btn");
  btn.disabled = true;
  setStatus("route-status", "Finding the right department…");
  ["result", "perf", "draft-section", "draft-out"].forEach((id) => { $(id).hidden = true; });
  try {
    const r = await api("/route", { text, language: $("language").value });
    lastRoute = r;
    lastComplaint = text;
    renderRoute(r);
    setStatus("route-status", r.abstain ? "We need a little more information." : "Done.", r.abstain ? "" : "ok");
  } catch (err) {
    setStatus("route-status", err.message, "error");
  } finally {
    btn.disabled = false;
  }
});

function renderRoute(r) {
  const body = $("result-body");
  body.replaceChildren();
  if (r.injection_detected) {
    body.append(el("p", { class: "notice" },
      "Your text contained what looks like instructions to this system. They were ignored; only your grievance was used."));
  }
  if (r.abstain) {
    body.append(
      el("p", { class: "dept" }, "We could not confidently choose a department"),
      el("p", {}, r.reason || ""),
      el("p", { class: "notice" }, r.clarifying_question || "Please add more detail about the office or scheme involved."),
    );
  } else {
    body.append(el("p", { class: "dept" }, r.department));
    body.append(el("p", {}, r.reason));
  }
  const chips = el("div", { class: "chips" });
  if (r.category && r.category !== "unknown") chips.append(el("span", { class: "chip" }, "Issue: " + r.category));
  chips.append(el("span", { class: "chip" + (r.urgency === "high" ? " high" : "") }, "Urgency: " + r.urgency));
  chips.append(el("span", { class: "chip" }, "Language: " + r.language_detected));
  chips.append(el("span", { class: "chip", title: r.confidence_note },
    "Confidence " + r.confidence.toFixed(2) + " (not a calibrated probability)"));
  body.append(chips);
  if (r.urgency === "high") {
    body.append(el("p", { class: "notice" },
      "If anyone's life or safety is at risk, contact emergency services (112) first. A grievance is not an emergency service."));
  }
  if (r.possible_state_matter && r.abstain_reason !== "state_matter") {
    body.append(el("p", { class: "notice" },
      "This may be handled by a State Government or local office (for example police, land records or the municipality). " +
      "Check before submitting to a central department."));
  }
  if (r.evidence && r.evidence.length) {
    body.append(el("h3", {}, r.abstain ? "Closest matches found (not a recommendation)" : "Why"));
    body.append(el("ul", { class: "evidence" }, r.evidence.map((ev) => el("li", {},
      el("span", { class: "chip " + (ev.official ? "official" : "authored") }, ev.official ? "Official text" : "Project keywords"),
      " " + ev.text + " ", el("span", { class: "hint" }, "(" + ev.citation + ")"),
    ))));
  }
  body.append(el("p", { class: "hint" }, r.method.startsWith("retrieval+llm")
    ? "Chosen with the help of an AI model, from departments found in the knowledge base."
    : "Chosen by matching your text with the official responsibilities of each department (no AI model)."));

  // department choice: suggestion first, then alternatives, then "decide on the portal"
  const opts = $("dept-options");
  opts.replaceChildren();
  const list = [];
  if (r.department_key) list.push([r.department_key, r.department, "suggested"]);
  r.alternative_keys.forEach((k, i) => list.push([k, r.alternatives[i], "alternative"]));
  list.push(["", "I will choose the department on the CPGRAMS portal", ""]);
  // after an abstention nothing is pre-selected except "choose on the portal"
  const preselect = r.abstain ? list.length - 1 : 0;
  list.forEach(([key, name, tag], i) => {
    const id = "dept-" + i;
    const input = el("input", { type: "radio", name: "dept", id, value: key });
    if (i === preselect) input.checked = true;
    input.addEventListener("change", () => selectDepartment(key, name));
    opts.append(el("label", { for: id }, input, name, tag ? el("span", { class: "hint" }, " (" + tag + ")") : ""));
  });
  $("result").hidden = false;
  selectDepartment(list[preselect][0], list[preselect][1]);
  // a new complaint starts a fresh draft form
  $("draft-form").reset();
  setStatus("draft-status", "");
  const missing = r.missing_information || [];
  $("missing-hint").textContent = missing.length ? "Useful to add: " + missing.join("; ") + "." : "";
  $("draft-section").hidden = false;
  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function selectDepartment(key, name) {
  chosenKey = key || null;
  const perf = $("perf");
  if (!key) { perf.hidden = true; return; }
  $("perf-name").textContent = name;
  const body = $("perf-body");
  body.replaceChildren(el("p", { class: "hint" }, "Loading figures…"));
  perf.hidden = false;
  try {
    renderPerf(await api("/ministry/" + encodeURIComponent(key) + "/performance"));
  } catch (err) {
    body.replaceChildren(el("p", { class: "status error" }, "Figures unavailable: " + err.message));
  }
}

function sparkline(series) {
  const w = 420, h = 70, pad = 6;
  const ys = series.map((p) => p.pending);
  const min = Math.min(...ys), max = Math.max(...ys);
  const x = (i) => pad + (i * (w - 2 * pad)) / Math.max(1, series.length - 1);
  const y = (v) => h - pad - ((v - min) * (h - 2 * pad)) / Math.max(1, max - min);
  const d = series.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(p.pending).toFixed(1)).join(" ");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("class", "spark");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Month-end pending from ${ys[0]} to ${ys[ys.length - 1]} over ${ys.length} reports`);
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", d);
  svg.append(path);
  return svg;
}

function renderPerf(p) {
  const body = $("perf-body");
  body.replaceChildren();
  if (!p.available) {
    body.append(el("p", {}, "No verified figures are available for this department."));
    return;
  }
  body.append(el("p", {}, "Latest reporting month with this department: ", el("strong", {}, p.latest_reporting_month || "unknown")));
  if (p.metrics.length) {
    body.append(el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Measure"), el("th", {}, "Value"), el("th", {}, "Period"), el("th", {}, "Source"))),
      el("tbody", {}, p.metrics.map((m) => el("tr", {}, el("td", {}, m.label), el("td", {}, m.value), el("td", {}, m.period), el("td", {}, m.source)))),
    ));
  }
  if (p.backlog_trend) {
    const t = p.backlog_trend;
    body.append(el("h3", {}, "Backlog trend, " + t.period));
    body.append(el("p", {}, `Month-end pending went from ${t.pending_start.toLocaleString()} to ${t.pending_end.toLocaleString()}. ` +
      `Statistical trend: ${t.trend}` + (t.relative_slope_pct_per_month !== null ? ` (${t.relative_slope_pct_per_month}% of the median level per month).` : ".")));
    if (p.pending_series.length > 1) body.append(sparkline(p.pending_series));
  }
  if (p.unavailable.length) {
    body.append(el("h3", {}, "Not available"));
    body.append(el("ul", {}, p.unavailable.map((u) => el("li", {}, u))));
  }
  if (p.sources.length) {
    body.append(el("p", { class: "hint" }, "Source reports: ",
      p.sources.map((s, i) => [i ? ", " : "", el("a", { href: s.source_url, target: "_blank", rel: "noopener" },
        "#" + s.report_number + " (" + s.report_month + ")")])));
  }
  body.append(el("ul", { class: "hint" }, p.caveats.map((c) => el("li", {}, c))));
}

$("draft-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!lastRoute) return;
  const clar = {};
  ["reference_numbers", "dates", "location", "previous_attempts", "action_requested"].forEach((name) => {
    const v = $("draft-form").elements[name].value.trim();
    if (v) clar[name] = v;
  });
  const btn = $("draft-btn");
  btn.disabled = true;
  setStatus("draft-status", "Preparing your draft…");
  try {
    const d = await api("/draft", {
      complaint: lastComplaint,
      language: $("draft-lang").value,
      department_key: chosenKey,
      category: chosenKey && chosenKey === lastRoute.department_key ? lastRoute.category : null,
      clarifications: clar,
      supporting_details: $("c-docs").value.trim() || null,
    });
    renderDraft(d);
    setStatus("draft-status", "Draft ready. Review it below.", "ok");
  } catch (err) {
    setStatus("draft-status", err.message, "error");
  } finally {
    btn.disabled = false;
  }
});

function renderDraft(d) {
  $("draft-text").value = d.text;
  const checks = $("draft-checks");
  checks.replaceChildren();
  checks.append(el("p", {}, d.placeholders.length
    ? `${d.placeholders.length} part(s) in [square brackets] still need your details or should be deleted.`
    : "No placeholders left."));
  checks.append(el("p", { class: d.factcheck.unsupported_tokens.length || d.factcheck.unsupported_claim_words.length ? "status error" : "status ok" },
    d.factcheck.unsupported_tokens.length || d.factcheck.unsupported_claim_words.length
      ? "Check these items, which were not in what you wrote: " + d.factcheck.unsupported_tokens.concat(d.factcheck.unsupported_claim_words).join(", ")
      : "Automatic check: every number and date in the draft comes from what you wrote."));
  $("checklist").replaceChildren(...d.review_checklist.map((c) => el("li", {}, c)));
  $("draft-out").hidden = false;
  $("draft-out").scrollIntoView({ behavior: "smooth", block: "start" });
}

$("copy-btn").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("draft-text").value);
    $("copy-status").textContent = "Copied.";
  } catch {
    $("draft-text").select();
    $("copy-status").textContent = "Select the text and copy it with Ctrl+C.";
  }
});
