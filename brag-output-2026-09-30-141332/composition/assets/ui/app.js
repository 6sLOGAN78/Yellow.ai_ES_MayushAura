/* Nexus Loop operator console.
   Reads GET /report.json, renders four tabs, writes POST /decision, then re-reads
   the report so the screen always shows what is on disk. All report text is
   escaped before it reaches innerHTML; bar widths are set through CSSOM so the
   page runs under a CSP without 'unsafe-inline'. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TABS = ["findings", "refusals", "metrics", "decisions"];
  const ASKS = {
    A01: ["Handled without a human", "How many conversations were handled without a human?"],
    A02: ["Better or worse month over month", "Are we better or worse than last month, beyond traffic mix?"],
    A03: ["Costliest intent per resolution", "Which intent costs the most per resolved conversation?"],
    A04: ["How often a tool failed", "How often did a tool fail?"],
    A05: ["Where returns drop out", "Where do customers drop out of the returns journey?"],
    A06: ["Is the knowledge base answering", "Is the knowledge base answering, and how good are the answers?"],
    A07: ["Did the model upgrade help", "Did the model upgrade help?"],
    A08: ["Spend this month", "What did we spend serving customers this month, and on what?"],
    A09: ["Abandon reason", "Why did users abandon — did they give up from frustration?"],
    A10: ["Review this week", "Which conversations should a human review this week?"],
    A11: ["Failover rate", "What is our failover rate?"],
  };
  const GAP_VERDICT = {
    NOT_MEASURABLE: "Not measurable",
    REQUIRES_NEW_JUDGE: "Needs a new judge",
    CARDINALITY_REFUSED: "Refused: cardinality",
  };
  /* The four asks the problem statement requires; the rest are answered in Metrics. */
  const REQUIRED_ASKS = ["A01", "A04", "A09", "A11"];

  /* Manager English: the operator reads labels, the codes stay in "Technical names". */
  const AUDIENCE = {
    agent_builder: "Agent team",
    platform_owner: "Platform team",
    business_owner: "Business owner",
  };
  const METRIC = {
    resolution_rate: "Share we finished",
    turns_to_resolve: "Turns to finish",
    tool_p95_latency_ms: "Tool wait time",
    containment_rate: "Share we handled end to end",
    quality_score: "Scorecard quality",
  };
  const CAUSE = {
    "tool.contract_break": "The tool said success but sent an empty answer",
    "kb.gap": "The knowledge base has nothing for this question",
    "prompt.regression": "The prompt started taking more turns",
    "traffic_mix": "Traffic mix, not quality",
    "load": "A short load spike",
    "judge_change": "The scorecard changed, not the agent",
  };
  const CAUSE_AUDIENCE = {
    load: ["platform_owner"],
    traffic_mix: ["business_owner"],
    judge_change: ["platform_owner"],
    "tool.outage": ["platform_owner"],
    "routing.error": ["platform_owner"],
    "kb.gap": ["agent_builder", "business_owner"],
    "tool.contract_break": ["agent_builder", "platform_owner"],
    "prompt.regression": ["agent_builder"],
    "model.change": ["agent_builder", "platform_owner"],
    unknown: ["agent_builder", "platform_owner"],
  };
  const PHRASE_ENGLISH = [
    ["tool_call p95 duration_ms", "Tool wait time"],
    ["tool p95 latency", "Tool wait time"],
    ["duration_ms", "wait (ms)"],
    ["sessions/day", "sessions a day"],
    ["timeout rate", "timeouts"],
    ["platform owner", "Platform team"],
    ["agent builder", "Agent team"],
    ["business owner", "Business owner"],
    ["judge_version", "scorecard version"],
    ["config_timeline", "setup timeline"],
    ["quality_rubric", "quality rubric"],
    ["agent_kind", "agent kind"],
    ["tool_call", "tool call"],
    ["quality_score", "Scorecard quality"],
    ["aggregate containment", "Share we handled end to end"],
    ["containment rate", "Share we handled end to end"],
    ["Resolution is flat", "Share we finished is flat"],
    ["volume runs at", "Traffic ran at"],
    ["tool telemetry", "tool measurements"],
    ["coverage-limited to the", "only from the"],
  ];
  const GAP_LEDE = {
    A11: "Failover does not exist here. There is no rate to show.",
    A09: "We count who left. We will not invent why.",
    A02: "Too many distinct customers to split this list.",
  };
  const FIDELITY = { measured: "success", derived: "info", judged: "warning", estimated: "neutral", simulated: "neutral" };
  const VERDICT_UI = {
    accepted: ["Approved", "success"], rejected: ["Rejected", "critical"], deferred: ["Deferred", "warning"],
  };
  const REPLAY_UI = { improved: ["Improved", "success"], no_effect: ["No effect", "neutral"], regressed: ["Regressed", "critical"] };

  const I = {
    lock: '<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="3" y="7" width="10" height="7" rx="1.5"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/></svg>',
    clock: '<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.5 1.5"/></svg>',
    alert: '<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M8 2 1.5 13.5h13z"/><path d="M8 6.5v3M8 11.5v.01"/></svg>',
    check: '<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m3 8.5 3 3 7-7"/></svg>',
    info: '<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="M8 7.5V11M8 5v.01"/></svg>',
    copy: '<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 5.5V3.5a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2"/></svg>',
    chev: '<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m6 3.5 4.5 4.5L6 12.5"/></svg>',
    minus: '<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="M5.5 8h5"/></svg>',
  };

  const state = { rep: null, tab: "findings", key: null, open: {}, railOpen: {}, byTab: {}, error: null };

  /* ---------------- formatting ---------------- */
  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  const isNum = (v) => typeof v === "number" && isFinite(v);
  function num(v, d) {
    if (!isNum(v)) return "—";
    return v.toLocaleString("en-US", { minimumFractionDigits: d || 0, maximumFractionDigits: d == null ? 4 : d });
  }
  function int(v) { return isNum(v) ? Math.round(v).toLocaleString("en-US") : "—"; }
  function pct(v, d) { return isNum(v) ? (v * 100).toFixed(d == null ? 1 : d) + "%" : "—"; }
  function usd(v, d) { return isNum(v) ? "$" + v.toLocaleString("en-US", { minimumFractionDigits: d == null ? 2 : d, maximumFractionDigits: d == null ? 2 : d }) : "not recorded"; }
  function isRateName(s) { return /rate|share|containment|resolution|answered with nothing/i.test(String(s || "")); }
  function metricVal(metric, v) {
    if (!isNum(v)) return "—";
    if (isRateName(metric) && v >= 0 && v <= 1) return pct(v);
    if (/latency|duration|_ms/i.test(String(metric || ""))) return num(v, 0) + " ms";
    if (/quality|score/i.test(String(metric || ""))) return num(v, 2) + " / 5";
    return num(v, Math.abs(v) < 10 ? null : 1);
  }
  function human(s) { return String(s || "").replace(/_/g, " "); }
  const audienceLbl = (ids) => {
    const a = (ids || []).map((x) => AUDIENCE[x] || human(x));
    return a.length > 1 ? a.slice(0, -1).join(", ") + " and " + a[a.length - 1] : (a[0] || "");
  };
  function audienceFor(f, d) {
    if (f && f.audience && f.audience.length) return audienceLbl(f.audience);
    return audienceLbl(CAUSE_AUDIENCE[d && d.cause_class] || []);
  }
  function metricLbl(id) {
    if (METRIC[id]) return METRIC[id];
    const t = human(id).trim();
    return t ? t.charAt(0).toUpperCase() + t.slice(1) : t;
  }
  const causeLbl = (id) => CAUSE[id] || String(id || "unknown").replace(/[._]/g, " ");
  /* longest phrase first so "containment rate" wins over "containment" */
  const ENGLISH_PAIRS = (() => {
    const pairs = [];
    [METRIC, CAUSE, AUDIENCE].forEach((map) => Object.keys(map).forEach((k) => pairs.push([k, map[k]])));
    PHRASE_ENGLISH.forEach((row) => pairs.push(row));
    pairs.sort((a, b) => b[0].length - a[0].length);
    const seen = new Set();
    return pairs.filter(([k]) => !seen.has(k.toLowerCase()) && seen.add(k.toLowerCase()))
      .map(([k, v]) => [new RegExp(k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"), v]);
  })();
  function toManagerEnglish(text) {
    if (text == null) return "";
    let out = String(text);
    ENGLISH_PAIRS.forEach(([re, v]) => { out = out.replace(re, v); });
    return out.replace(/\s*->\s*/g, " → ");
  }
  function firstBeat(text, max) {
    const t = String(text || "").replace(/\s+/g, " ").trim();
    if (!t) return "";
    const cut = t.split(/(?<=[.!?])\s+|:\s+|;\s+/)[0] || t;
    const cap = max || 140;
    return cut.length <= cap ? cut : cut.slice(0, cap - 1) + "…";
  }
  function gapClaim(g) {
    if (!g) return "We will not invent this number";
    if (g.ask_id === "A11") return "We will not invent a failover rate";
    if (g.ask_id === "A09") return "We will not invent why people left";
    if (g.ask_id === "A02") return "We will not break this down by customer";
    return "We will not invent this number";
  }
  function gapLede(g) {
    if (g && GAP_LEDE[g.ask_id]) return GAP_LEDE[g.ask_id];
    return firstBeat(g && g.why, 140) || "We will not invent this number.";
  }
  function idleGateCopy(kind, g) {
    if (kind === "gap") {
      if (g && (g.ask_id === "A11" || g.verdict === "NOT_MEASURABLE")) {
        return g.ask_id === "A11"
          ? "Nothing to approve. We will not invent a failover rate."
          : "Nothing to approve. We will not invent this number.";
      }
      return "Nothing to approve. " + gapClaim(g) + ".";
    }
    return "Nothing to approve. This is not a problem.";
  }
  function replayLbl(v) {
    if (!v) return "No test this run";
    if (v.verdict === "improved") return "The test got better";
    if (v.verdict === "no_effect") return "The test did not move";
    if (v.verdict === "regressed") return "The test got worse";
    return human(v.verdict) || "Test result";
  }
  function techBlock(rows) {
    const bits = (rows || []).filter((r) => r && r.v != null && String(r.v) !== "");
    if (!bits.length) return "";
    return `<details class="fold tech-foot"><summary>${I.chev}Technical names</summary><div class="fold-body"><dl class="kv">${
      bits.map((r) => `<dt>${esc(r.k)}</dt><dd><code translate="no">${esc(r.v)}</code></dd>`).join("")}</dl></div></details>`;
  }
  function when(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return esc(iso);
    return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";
  }
  /* escape first, then mark snake_case field names so they read as data */
  function rich(s) {
    return esc(s).replace(/\b[a-z][a-z0-9]*(?:[_.][a-z0-9]+)+\b/g, '<span class="hl">$&</span>');
  }
  const pill = (text, cls) => `<span class="pill ${cls || ""}">${esc(text)}</span>`;
  const bar = (frac, cls, thin) =>
    `<span class="bar${thin ? " thin" : ""}"><i class="${cls || ""}" data-w="${isNum(frac) ? Math.max(0, Math.min(1, frac)) : 0}"></i></span>`;
  function fidelityPill(f) { return f ? pill(f, FIDELITY[f] || "neutral") : ""; }
  function fold(summary, body, open) {
    return `<details class="fold"${open ? " open" : ""}><summary>${I.chev}${esc(summary)}</summary><div class="fold-body">${body}</div></details>`;
  }
  function kv(rows) {
    const r = rows.filter((x) => x && x[1] != null && x[1] !== "");
    return r.length ? `<dl class="kv">${r.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>` : "";
  }
  function flatten(o, prefix, out) {
    out = out || [];
    if (o && typeof o === "object") {
      const entries = Array.isArray(o) ? o.map((v, i) => [String(i), v]) : Object.entries(o);
      for (const [k, v] of entries) flatten(v, prefix ? prefix + "." + k : k, out);
    } else out.push([prefix || "value", o]);
    return out;
  }
  function leaf(v) {
    if (v === null) return "null";
    if (isNum(v)) return num(v);
    return String(v);
  }

  /* ---------------- report indexes ---------------- */
  function idx(rep) {
    const by = (arr, key) => Object.fromEntries((arr || []).map((x) => [x[key], x]));
    const diagByFinding = by(rep.diagnoses, "finding_id");
    const rxByDiag = by(rep.prescriptions, "diagnosis_id");
    const verByRx = {};
    for (const v of rep.verifications || []) verByRx[v.prescription_id] = v; // latest wins
    const findings = rep.findings || [];
    return {
      regs: findings.filter((f) => f.is_regression),
      dismissed: findings.filter((f) => !f.is_regression),
      gaps: rep.gaps || [],
      diag: (f) => diagByFinding[f.id] || null,
      rx: (f) => { const d = diagByFinding[f.id]; return d ? rxByDiag[d.id] || null : null; },
      ver: (p) => (p ? verByRx[p.id] || null : null),
      findingForRx: (p) => {
        const d = (rep.diagnoses || []).find((x) => x.id === p.diagnosis_id);
        return d ? findings.find((f) => f.id === d.finding_id) || null : null;
      },
    };
  }
  function cohortText(c) {
    return Object.entries(c || {}).filter(([k]) => !/day/.test(k)).map(([, v]) => v).join(" · ");
  }
  function windowText(w) {
    return w && isNum(w.from_day) ? `days ${w.from_day}–${w.to_day}` : "";
  }
  function standardFor(rep, f) {
    if (!f) return null;
    const c = f.cohort || {};
    return (rep.standard || []).find((st) => {
      if (st.tenant !== f.tenant || st.metric !== f.metric) return false;
      const sc = st.cohort || {};
      return Object.keys(sc).every((k) => sc[k] == null || sc[k] === c[k]);
    }) || null;
  }
  function standardLine(st, metric) {
    if (!st) return "";
    const m = metric || st.metric;
    if (isRateName(m)) return `Healthy conversations here finish at about ${metricVal(st.metric, st.median)}; the best day was ${metricVal(st.metric, st.best)}.`;
    return `Healthy conversations here finish in about ${metricVal(st.metric, st.median)} ${metricLbl(m).toLowerCase()}; the best is ${metricVal(st.metric, st.best)}.`;
  }

  /* Coverage and fidelity of the metric behind a finding, taken from the
     report's own metric passport (never recomputed). */
  function askForCause(c) {
    if (!c) return null;
    if (c.indexOf("tool.") === 0) return "A04";
    if (c.indexOf("kb.") === 0) return "A06";
    if (c === "prompt.regression" || c === "model.change") return "A02";
    return null;
  }
  function coverageFor(rep, f, d) {
    const c = d && d.cause_class;
    const ask = askForCause(c);
    if (!ask) return null;
    const slug = String(f.tenant || "").replace(/[^A-Za-z0-9]/g, "_");
    const ms = (rep.metrics || []).filter((m) => m.ask_id === ask);
    const prefer = c === "tool.contract_break" ? "empty_success" : null;
    const m = (prefer && ms.find((x) => x.id.indexOf(prefer) >= 0 && x.id.indexOf(slug) >= 0))
      || ms.find((x) => x.id.indexOf(slug) >= 0) || ms[0];
    if (!m || !m.coverage || !isNum(m.coverage.value)) return null;
    return { value: m.coverage.value, fidelity: m.fidelity, ask: ask, scope: f.tenant || null };
  }

  /* ---------------- top bar ---------------- */
  function renderTop(rep, ix) {
    const askCount = new Set((rep.metrics || []).map((m) => m.ask_id).concat((rep.gaps || []).map((g) => g.ask_id))).size;
    const answeredAsks = new Set((rep.metrics || []).map((m) => m.ask_id));
    const fullyRefused = (rep.gaps || []).filter((g) => !answeredAsks.has(g.ask_id)).length;
    const counts = { findings: ix.regs.length, refusals: fullyRefused, metrics: askCount,
      decisions: (rep.prescriptions || []).filter((p) => p.approval).length };
    for (const t of TABS) {
      const b = document.querySelector(`.tab[data-tab="${t}"]`);
      b.setAttribute("aria-selected", String(state.tab === t));
      b.querySelector(".count").textContent = counts[t];
    }
    const vers = rep.verifications || [];
    const cycles = rep.self_assessment && rep.self_assessment.cycles;
    $("topmeta").innerHTML =
      `${rep.corpus ? pill("Corpus " + rep.corpus, "") : ""}` +
      `<span>Generated ${esc(when(rep.generated_at))}</span>` +
      `<span class="row" data-style="gap:6px"><span class="dot ${vers.length ? "ok" : "off"}"></span>` +
      `${vers.length ? `Replay · ${int(vers.length)} run${vers.length === 1 ? "" : "s"}` : "Replay not run" + (isNum(cycles) ? ` · ${int(cycles)} cycles` : "")}</span>`;
    // inline style attributes are blocked by CSP; move them to CSSOM
    $("topmeta").querySelectorAll("[data-style]").forEach((el) => { const s = el.getAttribute("data-style"); el.removeAttribute("data-style"); el.style.cssText = s; });
  }

  /* ---------------- rail ---------------- */
  function railItem(key, dotOrIcon, t, s, extra) {
    return `<button class="rail-item" data-key="${esc(key)}" aria-current="${state.key === key}">
      ${dotOrIcon}<span class="stack" data-style="gap:0"><span class="t">${esc(t)}</span><span class="s">${esc(s)}</span></span>${extra || ""}</button>`;
  }
  function renderRail(rep, ix) {
    const regItems = ix.regs.map((f) => {
      const p = ix.rx(f);
      const st = p && p.approval ? VERDICT_UI[p.approval.verdict] : null;
      return railItem("f:" + f.id, `<span class="dot ${esc(f.severity)}"></span>`, headlineShort(f),
        `${f.tenant} · ${cohortText(f.cohort)}`, st ? `<span class="state">${pill(st[0], st[1])}</span>` : "");
    });
    const disItems = ix.dismissed.map((f) =>
      railItem("f:" + f.id, '<span class="dot low"></span>', headlineShort(f), `${f.tenant} · ${windowText(f.window)}`));
    const gapItems = ix.gaps.slice().sort((a, b) => (a.ask_id === "A11" ? -1 : b.ask_id === "A11" ? 1 : 0)).map((g) =>
      railItem("g:" + g.ask_id, I.lock, gapClaim(g), `${g.ask_id} · ${GAP_VERDICT[g.verdict] || human(g.verdict)}`));
    const [kind, id] = splitKey(state.key);
    const holds = {
      decide: kind === "f" && ix.regs.some((f) => f.id === id),
      dismissed: kind === "f" && ix.dismissed.some((f) => f.id === id),
      refused: kind === "g",
    };
    const group = (gid, label, items, empty) => {
      const open = gid in state.railOpen ? state.railOpen[gid] : holds[gid];
      return `<details class="rail-group" data-group="${gid}"${open ? " open" : ""}>
        <summary><span>${esc(label)}</span><span class="count">${items.length}</span>${I.chev}</summary>
        <div class="rail-list">${items.join("") || `<p class="small muted rail-empty">${esc(empty)}</p>`}</div></details>`;
    };
    $("rail").innerHTML =
      group("decide", "Needs a decision", regItems, "No regressions found.") +
      group("dismissed", "Checked and dismissed", disItems, "No lookalikes checked.") +
      group("refused", "Refused to answer", gapItems, "Every ask has a number.");
    fixInlineStyles($("rail"));
  }
  function headlineShort(f) { return String(f.headline || f.id).split(/\s+[—-]\s+|,\s/)[0]; }

  /* ---------------- findings ---------------- */
  /* Impact comes in two shapes: outcome faults lose resolutions; the prompt
     regression leaves outcomes flat and costs turns and money instead. */
  function impactTiles(f) {
    const im = f.impact || {}, ds = im.downstream || {};
    const n = im.conversations_affected;
    const tiles = [["Conversations", int(n), isNum(im.share_of_traffic) ? `${pct(im.share_of_traffic, 0)} of ${f.tenant || "tenant"} traffic` : "in the window"]];
    if (isNum(ds.would_have_resolved_at_baseline)) tiles.push(["Lost resolutions", int(ds.would_have_resolved_at_baseline), "would have finished at baseline"]);
    else if (isNum(ds.resolved_conversations_slowed)) tiles.push(["Finished, but slower", int(ds.resolved_conversations_slowed), "outcomes did not move"]);
    if (isNum(ds.extra_turns)) tiles.push(["Extra turns", int(ds.extra_turns), "over the baseline"]);
    if (isNum(ds.unplanned_handoffs)) tiles.push(["Handoffs", int(ds.unplanned_handoffs), "reached a human unplanned"]);
    if (isNum(ds.abandoned)) tiles.push(["Abandoned", int(ds.abandoned), isNum(n) && n ? pct(ds.abandoned / n) + " of cohort" : "left without finishing"]);
    if (isNum(ds.excess_cost_usd)) tiles.push(["Excess cost", usd(ds.excess_cost_usd), "spent on the extra turns"]);
    tiles.push(["Recorded cost", usd(im.cost_usd), isNum(im.days_running) ? `over ${int(im.days_running)} days` : windowText(f.window)]);
    return tiles;
  }

  function renderRegression(rep, ix, f) {
    const d = ix.diag(f), p = ix.rx(f), v = ix.ver(p);
    const im = f.impact || {};
    const std = standardFor(rep, f);
    const who = audienceFor(f, d);
    const worse = isNum(f.observed) && isNum(f.expected) && (/turn|cost|fail|error|abandon|latency/i.test(f.metric) ? f.observed > f.expected : f.observed < f.expected);
    const tiles = impactTiles(f);
    // cost always stays visible: the brief asks what the problem has cost
    const cost = tiles.find((t) => t[0] === "Recorded cost");
    const others = tiles.filter((t) => t !== cost);
    const shown = others.slice(0, 3).concat(cost ? [cost] : []), rest = others.slice(3);
    const ac = d && d.attributed_change;
    const cov = coverageFor(rep, f, d);
    const main = `
      <div class="header">
        <div class="pills">${pill(({ critical: "Critical", high: "High", medium: "Medium", low: "Low" })[f.severity] || f.severity, esc(f.severity))}${cov ? pill("coverage " + pct(cov.value, 0) + (cov.scope ? " · " + cov.scope : ""), cov.value >= 0.95 ? "outline" : "warning") : ""}${cov && cov.fidelity ? fidelityPill(cov.fidelity) : ""}</div>
        <h1 class="display">${esc(f.headline || metricLbl(f.metric))}</h1>
        <p class="muted">${esc([f.tenant, cohortText(f.cohort), windowText(f.window), isNum(im.days_running) ? `running ${im.days_running} days` : ""].filter(Boolean).join(" · "))}</p>
      </div>
      <section class="card" aria-label="Impact">
        <div class="tiles">${shown.map(([l, n, sub]) => `<div class="tile"><span class="label">${esc(l)}</span><span class="metric">${esc(n)}</span><span class="sub">${esc(sub)}</span></div>`).join("")}</div>
        ${rest.length ? `<p class="small muted tiles-more">${rest.map(([l, n]) => `${esc(l)} <strong>${esc(n)}</strong>`).join(" · ")}</p>` : ""}
        ${im.derivation || f.baseline ? fold("How these were counted, and the baseline", `${im.derivation ? `<p>${rich(im.derivation)}</p>` : ""}${f.baseline ? `<p data-style="margin-top:8px"><strong>Baseline.</strong> ${esc(f.baseline)}</p>` : ""}`) : ""}
      </section>
      <div class="callout">${I.clock}<div><p><strong>Needs to act:</strong> ${esc(who || "Someone")}</p>${f.if_nothing_changes ? `<p>${esc(f.if_nothing_changes)}</p>` : ""}</div></div>
      <section class="card">
        <h2 class="title">What moved</h2>
        <div class="compare"><span class="metric">${esc(metricVal(f.metric, f.expected))}</span><span class="arrow">→</span><span class="metric to ${worse ? "bad" : "good"}">${esc(metricVal(f.metric, f.observed))}</span></div>
        <p class="muted">${esc(metricLbl(f.metric))}, before this broke → now.${std ? " " + esc(standardLine(std, f.metric)) : ""}</p>
        ${f.baseline ? `<p class="small muted">Baseline: ${esc(f.baseline)}</p>` : ""}
        ${std ? fold("The standard this cohort is held to", kv([
          ["Best / median", esc(`${metricVal(std.metric, std.best)} / ${metricVal(std.metric, std.median)}`)],
          ["Gap to best", esc(metricVal(std.metric, std.deficit))],
          ["Exemplars", esc(int(std.exemplar_n))], ["Golden set", std.golden_set_version ? `<code>${esc(std.golden_set_version)}</code>` : null],
          ["Derivation", rich(std.derivation)]])) : ""}
      </section>
      <section class="card">
        <h2 class="title">Evidence</h2>
        ${evidenceList(f.evidence)}
      </section>
      ${d ? `<section class="card">
        <h2 class="title">Why we think so</h2>
        <p class="lede">${esc(causeLbl(d.cause_class))} <span class="muted">· ${esc(pct(d.confidence, 0))} confidence</span></p>
        <p class="marker">${esc(changeLine(ac))}</p>
        ${(d.evidence || []).length ? fold("How this was computed", evidenceList(d.evidence, 99)) : ""}
      </section>` : ""}
      ${p ? `<section class="card">
        <h2 class="title">The fix</h2>
        <p>${rich(toManagerEnglish(p.description))}</p>
        ${p.predicted_delta ? `<p class="muted">Predicted ${esc(metricLbl(p.predicted_delta.metric).toLowerCase())}: ${esc(metricVal(p.predicted_delta.metric, p.predicted_delta.from))} → <strong>${esc(metricVal(p.predicted_delta.metric, p.predicted_delta.to))}</strong></p>` : ""}
        <div class="divider"></div>
        ${replaySummary(rep, p, v)}
      </section>` : ""}
      ${techBlock([
        { k: "cause", v: d && d.cause_class },
        { k: "metric", v: f.metric },
        { k: "change", v: p && p.change_type },
        { k: "target", v: p && p.target },
        { k: "autonomy", v: p && p.autonomy_rung },
        { k: "attributed kind", v: ac && ac.kind },
        { k: "test run", v: v && v.replay_run_id },
        { k: "finding", v: f.id },
      ])}`;
    return `<div class="workspace"><div class="stack">${main}</div>${decisionPanel(p, f)}</div>`;
  }
  function changeLine(ac) {
    if (!ac) return "Nothing in the setup changed with this";
    const day = ac.day != null ? ` on day ${ac.day}` : "";
    const vers = ac.from != null && ac.to != null ? ` (${ac.from} → ${ac.to})` : "";
    return `${ac.target || "Something"} changed${day}${vers}`;
  }
  function evidenceList(items, limit) {
    const all = items || [];
    const cap = limit || 3;
    const li = (e) => `<li><span>${rich(toManagerEnglish(e))}</span><button class="iconbtn" data-copy="${esc(e)}" title="Copy">${I.copy}<span class="sr-only">Copy</span></button></li>`;
    const head = `<ul class="evidence">${all.slice(0, cap).map(li).join("")}</ul>`;
    const more = all.slice(cap);
    return more.length ? head + fold(`${more.length} more check${more.length === 1 ? "" : "s"}`, `<ul class="evidence">${more.map(li).join("")}</ul>`) : head;
  }
  function replaySummary(rep, p, v) {
    if (!v) {
      return `<p class="row-line">${pill("Not replayed", "neutral")}<span class="muted">${esc(replayLbl(null))}. The report is still the decision record.</span></p>`;
    }
    const ui = REPLAY_UI[v.verdict] || [human(v.verdict), "neutral"];
    const m = v.metric || (p.predicted_delta && p.predicted_delta.metric);
    const golden = v.golden_set_pass === true ? "known-good conversations still passed"
      : v.golden_set_pass === false ? "a known-good conversation got worse" : "";
    return `<p class="row-line">${pill(ui[0], ui[1])}<span>${esc(replayLbl(v))}: ${esc(metricVal(m, v.before))} → <strong>${esc(metricVal(m, v.after))}</strong></span></p>
      <p class="small muted">${esc([golden, isNum(v.prediction_error) ? "gap vs prediction " + num(v.prediction_error, 3) + " pts" : ""].filter(Boolean).join(" · "))}${golden || isNum(v.prediction_error) ? ". " : ""}The test’s “before” is simulated, not our measured number.</p>`;
  }

  /* A paste-ready note for a recorded decision: the human's verdict, reason and
     timestamp, plus the finding it applies to. Copied via the global [data-copy]. */
  function decisionNote(f, p, a) {
    if (!f || !p || !a) return "";
    const verdict = (VERDICT_UI[a.verdict] || [a.verdict])[0];
    const ds = (f.impact || {}).downstream || {};
    const lines = [
      `${verdict}: ${p.target || "this change"}`,
      `Finding: ${f.headline || f.id}`,
      `Slice: ${[f.tenant, cohortText(f.cohort), windowText(f.window)].filter(Boolean).join(" · ")}`,
      `Reason: "${a.reason}"`,
      `By ${a.decided_by} at ${when(a.at)}`,
    ];
    if (isNum(f.observed) && isNum(f.expected)) {
      lines.push(`What moved: ${metricLbl(f.metric)} ${metricVal(f.metric, f.expected)} → ${metricVal(f.metric, f.observed)}`);
    }
    const down = [
      isNum(ds.would_have_resolved_at_baseline) ? `${int(ds.would_have_resolved_at_baseline)} lost resolutions` : null,
      isNum(ds.unplanned_handoffs) ? `${int(ds.unplanned_handoffs)} unplanned handoffs` : null,
      isNum(ds.abandoned) ? `${int(ds.abandoned)} abandoned` : null,
      isNum(ds.extra_turns) ? `${int(ds.extra_turns)} extra turns` : null,
    ].filter(Boolean);
    if (down.length) lines.push(`Impact: ${down.join(", ")}`);
    if (p.description) lines.push(`Fix: ${p.description}`);
    if (p.decision && p.decision.risk_if_diagnosis_wrong) lines.push(`Risk if wrong: ${p.decision.risk_if_diagnosis_wrong}`);
    lines.push(`Open: ${location.origin}${location.pathname}#/findings/${encodeURIComponent(f.id)}`);
    return lines.join("\n");
  }
  function allDecisionsNote(rep, ix) {
    const decided = (rep.prescriptions || []).filter((p) => p.approval);
    if (!decided.length) return "";
    const lines = [`Decisions for ${rep.team || "this run"} · corpus ${rep.corpus || "—"}`, ""];
    for (const p of decided) {
      const f = ix.findingForRx(p);
      const a = p.approval;
      const v = (VERDICT_UI[a.verdict] || [a.verdict])[0];
      lines.push(`${v}: ${f ? (f.headline || f.id) : p.id}`);
      lines.push(`  Reason: "${a.reason}"`);
      lines.push(`  By ${a.decided_by} at ${when(a.at)}`);
    }
    const dismissed = (ix.dismissed || []).length;
    const answered = new Set((rep.metrics || []).map((m) => m.ask_id));
    const fullyRefused = (ix.gaps || []).filter((g) => !answered.has(g.ask_id)).length;
    const partly = (ix.gaps || []).length - fullyRefused;
    lines.push("");
    if (dismissed) lines.push(`Dismissed signals: ${dismissed} looked like problems and are not.`);
    if (fullyRefused) lines.push(`No measurement: ${fullyRefused} question${fullyRefused === 1 ? "" : "s"} we will not invent.`);
    if (partly) lines.push(`Partly refused: ${partly} question${partly === 1 ? "" : "s"} (a breakdown we will not split).`);
    return lines.join("\n");
  }

  function decisionPanel(p, f) {
    if (!p) return `<aside class="panel"><h2 class="title">No fix proposed</h2><p>Nothing to approve on this finding yet.</p></aside>`;
    const dec = p.decision || {};
    const a = p.approval;
    const who = storeGet("nl.who") || "";
    const ui = a ? VERDICT_UI[a.verdict] || [a.verdict, "neutral"] : null;
    const note = a ? decisionNote(f, p, a) : "";
    return `<aside class="panel" aria-label="Your decision">
      <h2 class="title">${esc(dec.asking_approval_for || p.description)}</h2>
      ${a ? `<div class="decided"><div class="row" data-style="gap:8px">${pill(ui[0], ui[1])}<span class="small muted">by ${esc(a.decided_by)} · ${esc(when(a.at))}</span></div><p data-style="margin-top:6px"><q>${esc(a.reason)}</q></p><div data-style="margin-top:10px"><button class="btn sm secondary" data-copy="${esc(note)}">${I.copy}Copy decision note</button></div></div>` : ""}
      <div>
        ${fold("Risk if we are wrong", `<p>${rich(dec.risk_if_diagnosis_wrong || "Not stated.")}</p>`, true)}
        ${fold("We would not ship if", `<p>${rich(dec.would_not_ship_if || "Not stated.")}</p>`, true)}
      </div>
      <div class="divider"></div>
      <form id="decide" class="stack" data-style="gap:12px" novalidate data-pid="${esc(p.id)}">
        <div class="field"><label for="who">Your name</label><input id="who" class="input" maxlength="80" autocomplete="name" value="${esc(who)}" placeholder="operator"></div>
        <div class="field" id="reason-field"><label for="reason">Reason (required)</label>
          <textarea id="reason" class="textarea" rows="4" maxlength="4000" required aria-required="true" aria-describedby="reason-err" placeholder="Why this call, in one or two sentences"></textarea>
          <span class="err" id="reason-err" role="alert">A reason is required.</span></div>
        <div id="decide-error"></div>
        <div class="btn-stack">
          <button type="submit" class="btn block success" data-verdict="accepted">${I.check}Approve</button>
          <button type="submit" class="btn block danger" data-verdict="rejected">Reject</button>
          <button type="submit" class="btn block ghost" data-verdict="deferred">Defer</button>
        </div>
        <p class="small muted">Your decision is written into loop-report.json.</p>
      </form>
    </aside>`;
  }

  function renderDismissed(rep, ix, f) {
    const d = ix.diag(f);
    const who = audienceFor(f, d);
    const delta = isNum(f.observed) && isNum(f.expected) ? f.observed - f.expected : null;
    const deltaTxt = delta == null ? "" : isRateName(f.metric) ? `${delta >= 0 ? "+" : "−"}${Math.abs(delta * 100).toFixed(1)} pts` : `${delta >= 0 ? "+" : "−"}${num(Math.abs(delta), 3)}`;
    return `<div class="workspace"><div class="stack">
      <div class="header">
        <div class="pills">${pill("Not a problem", "success")}</div>
        <h1 class="display">${esc(f.headline || metricLbl(f.metric))}</h1>
        <p class="muted">${esc([f.tenant, cohortText(f.cohort), windowText(f.window)].filter(Boolean).join(" · "))}</p>
      </div>
      <section class="card">
        <p class="lede">${rich(toManagerEnglish(f.not_a_regression_because || "Checked and dismissed."))}</p>
        <div class="compare"><span class="metric">${esc(metricVal(f.metric, f.expected))}</span><span class="arrow">→</span><span class="metric">${esc(metricVal(f.metric, f.observed))}</span></div>
        <p class="muted">${esc(metricLbl(f.metric))}, usual → this window${deltaTxt ? ` (${esc(deltaTxt)})` : ""}. ${d ? esc(causeLbl(d.cause_class)) + "." : ""}</p>
      </section>
      <section class="card"><h2 class="title">Why we are sure</h2>${evidenceList(f.evidence)}</section>
      ${techBlock([{ k: "cause", v: d && d.cause_class }, { k: "metric", v: f.metric }, { k: "finding", v: f.id }])}
    </div>
    <aside class="panel"><h2 class="title">${esc(idleGateCopy("dismissed"))}</h2>
      <p class="small muted">${who ? `A closed all-clear for the ${esc(who)}. ` : ""}Nobody should ship a fix for this.</p></aside></div>`;
  }

  function renderGap(rep, g) {
    const ask = ASKS[g.ask_id] || [g.ask_id, g.ask_id];
    const ev = g.required_event;
    const spec = ev ? JSON.stringify(ev, null, 2) : "";
    return `<div class="workspace single"><div class="stack">
      <div class="header">
        <div class="row" data-style="gap:12px"><span class="lock">${I.lock}</span>${pill(GAP_VERDICT[g.verdict] || human(g.verdict), "upper")}<span class="mono muted">${esc(g.ask_id)}</span></div>
        <h1 class="quote">“${esc(ask[1])}”</h1>
        <p class="muted">${esc(ask[0])}</p>
      </div>
      <p class="lede">${esc(gapLede(g))}</p>
      <section class="card"><h2 class="title">${esc(gapClaim(g))}</h2><p>${rich(toManagerEnglish(gapWhy(g)))}</p></section>
      ${g.nearest_proxy ? `<section class="card"><div class="grid2">
        <div><span class="label">The tempting number</span><p data-style="margin-top:6px;color:var(--text)">${rich(toManagerEnglish(g.nearest_proxy))}</p></div>
        <div><span class="label">${g.ask_id === "A11" ? "Why a retry is not failover" : "Why it misleads"}</span><p data-style="margin-top:6px">${rich(toManagerEnglish(g.why_the_proxy_misleads || ""))}</p></div>
      </div></section>` : ""}
      ${ev ? `<section class="spec" aria-label="Event needed">
        <div class="spec-head"><h2 class="title">Event needed to answer this</h2><button class="btn sm" data-copy="${esc(spec)}">${I.copy}Copy spec</button></div>
        <div class="spec-body">
          <div><span class="k">name </span><span class="v">${esc(ev.name)}</span> <span class="k">· grain </span><span class="v">${esc(ev.grain)}</span> <span class="k">· owner </span><span class="v">${esc(ev.owner)}</span></div>
          <table>${(ev.fields || []).map((x) => `<tr><td class="v">${esc(typeof x === "string" ? x : x.name)}</td><td class="t">${esc(typeof x === "string" ? "" : x.type || "")}</td></tr>`).join("")}</table>
        </div></section>` : ""}
      <div class="callout info">${I.info}<p>${esc(idleGateCopy("gap", g))}</p></div>
      ${techBlock([
        { k: "ask", v: g.ask_id }, { k: "verdict", v: g.verdict }, { k: "nearest proxy", v: g.nearest_proxy },
        { k: "event", v: ev && ev.name }, { k: "grain", v: ev && ev.grain },
        { k: "fields", v: ev && (ev.fields || []).map((x) => (typeof x === "string" ? x : x.name)).join(", ") },
        { k: "owner", v: ev && ev.owner },
      ])}
    </div></div>`;
  }
  /* the event spec is shown structurally, so drop its prose repeat from "why" */
  function gapWhy(g) {
    const raw = String((g && g.why) || "—");
    return raw.replace(/\s*Required event:[\s\S]*$/i, "").trim() || raw;
  }

  /* ---------------- metrics ---------------- */
  function answerFor(m) {
    const v = m.value;
    try {
      if (isNum(v)) return [metricVal(m.name, v)];
      switch (m.ask_id) {
        case "A02":
          return Object.entries(v).map(([t, x]) => x.change_beyond_mix != null
            ? `${t}: ${fmtPts(x.change_beyond_mix)} beyond mix`
            : `${t}: resolution ${pct(x.resolution[0])} → ${pct(x.resolution[1])}`);
        case "A03": {
          const rows = Object.entries(v);
          const numeric = rows.filter(([, x]) => isNum(x)).sort((a, b) => b[1] - a[1]);
          const top = numeric[0];
          const out = top ? [`${top[0]} ${usd(top[1], 3)}`] : ["no priced resolution"];
          const unavailable = rows.filter(([, x]) => !isNum(x)).map(([k, x]) => `${k}: ${human(x)}`);
          if (unavailable.length) out.push(`${unavailable.length} intent(s) without a cost: ${unavailable.join(", ")}`);
          return out;
        }
        case "A08": return Object.entries(v).map(([k, x]) => `${k.replace(/^days_/, "").replace(/_/g, "–")} days: ${usd(x, 0)}`);
        case "A05": return [`${pct(v.reach_rate[v.reach_rate.length - 1], 0)} reach ${human(v.milestones[v.milestones.length - 1])}`, `n = ${int(v.n)}`];
        case "A06":
          return Object.entries(v).map(([k, x]) => x.kb_hit_rate != null ? `${k}: hit ${pct(x.kb_hit_rate, 0)}` : `scorecard ${k}: ${num(x.mean, 2)} (n ${int(x.n)})`);
        case "A07": return v.map((u) => `${u.tenant}: ${u.from} → ${u.to}, resolution ${pct(u.resolution[0])} → ${pct(u.resolution[1])}`);
        case "A10": return [`${int(v.count)} conversations`];
      }
    } catch (e) { /* fall through to generic */ }
    const flat = flatten(v);
    return flat.slice(0, 2).map(([k, x]) => `${k}: ${leaf(x)}`).concat(flat.length > 2 ? [`+${flat.length - 2} more`] : []);
  }
  function fmtPts(d) { return `${d >= 0 ? "+" : "−"}${Math.abs(d * 100).toFixed(1)} pts`; }

  function metricDetail(m) {
    const cov = m.coverage || {}, cal = m.calibration, plan = m.plan || {};
    return `<div class="metric-detail"><p class="label mono">${esc(m.id)}</p><div class="grid2">
      <div>${kv([
        ["Name", esc(toManagerEnglish(m.name))],
        ["Filter", plan.filter ? `<span class="mono">${esc(plan.filter)}</span>` : null],
        ["Source", plan.source ? `<span class="mono">${esc(plan.source)}</span>` : null],
        ["Denominator", esc(plan.denominator)],
        ["Breakdowns", (plan.breakdowns || []).map((b) => `<code>${esc(b)}</code>`).join(" ")],
        ["Grain", esc(m.grain)],
        ["Coverage", isNum(cov.value) ? `${pct(cov.value, 0)} — ${rich(cov.basis)}` : null],
        ["Excluded", (cov.excluded || []).map((x) => `<code>${esc(x)}</code>`).join(" ")],
        ["Calibration", cal ? esc(`agreement ${pct(cal.agreement)} with human labels, n = ${int(cal.n)}${cal.judge_version ? ", judge " + cal.judge_version : ""}`) : null],
        ["Alternatives", (plan.alternatives_offered || []).map((x) => esc(typeof x === "string" ? x : JSON.stringify(x))).join("<br>")]
      ])}</div>
      <div>${kv(flatten(m.breakdown != null ? m.breakdown : m.value).slice(0, 40).map(([k, x]) => [k, `<span class="mono">${esc(leaf(x))}</span>`]))}</div>
    </div></div>`;
  }

  function renderMetrics(rep) {
    const metrics = rep.metrics || [], gaps = rep.gaps || [];
    const byAsk = {};
    const bucket = (a) => (byAsk[a] = byAsk[a] || { ms: [], gap: null });
    for (const m of metrics) bucket(m.ask_id).ms.push(m);
    for (const g of gaps) bucket(g.ask_id).gap = g;
    const ids = Object.keys(byAsk).sort();
    let answered = 0, partly = 0, refused = 0;
    const rows = ids.map((ask, i) => {
      const { ms, gap } = byAsk[ask];
      const status = ms.length && gap ? "Partly refused" : ms.length ? "Answered" : "Refused";
      if (status === "Answered") answered += 1;
      else if (status === "Partly refused") partly += 1;
      else refused += 1;
      const open = !!state.open["ask" + i];
      const covs = ms.map((m) => (m.coverage || {}).value).filter(isNum);
      const cov = covs.length ? Math.min(...covs) : null;
      const fids = [...new Set(ms.map((m) => m.fidelity).filter(Boolean))];
      const answers = ms.length <= 1
        ? (ms[0] ? answerFor(ms[0]) : []).map((t) => `<span class="line">${esc(t)}</span>`).join("")
        : ms.map((m) => `<div class="ansgroup"><span class="small muted">${esc(toManagerEnglish(m.name))}</span>${answerFor(m).map((t) => `<span class="line mono">${esc(t)}</span>`).join("")}</div>`).join("");
      const refusedLine = gap
        ? `<div class="ansgroup"><span class="small muted">Refused: ${esc(gapClaim(gap))}</span><span class="line muted">${esc(gapLede(gap))}</span></div>`
        : "";
      const detail = `<tr class="expand"><td colspan="6">${ms.map(metricDetail).join("") || `<p class="muted">No metric: ${esc(gapClaim(gap))}. ${esc(gapLede(gap))}</p>`}</td></tr>`;
      return `<tr class="clickable${open ? " open" : ""}" data-open="ask${i}" tabindex="0" aria-expanded="${open}">
        <td class="num-cell"><span class="caret">${I.chev}</span> <span class="mono">${esc(ask)}</span>${REQUIRED_ASKS.includes(ask) ? `<div>${pill("required", "info")}</div>` : ""}</td>
        <td>${esc((ASKS[ask] || [ask, ask])[1])}</td>
        <td class="answer">${answers}${refusedLine}</td>
        <td>${fids.length ? fids.map(fidelityPill).join(" ") : '<span class="muted">—</span>'}</td>
        <td>${isNum(cov) ? `<span class="cov"><span class="mono">${pct(cov, 0)}</span>${bar(cov, cov >= 0.95 ? "good" : "warn", true)}</span>` : '<span class="muted">—</span>'}</td>
        <td>${pill(status, status === "Answered" ? "success" : status === "Partly refused" ? "warning" : "critical")}</td></tr>${open ? detail : ""}`;
    });
    const std = rep.standard || [];
    return `<div class="page stack">
      <div class="header"><h1 class="display">What we measured, and how far to trust it</h1>
        <div class="pills">${pill(ids.length + " questions", "outline")}${pill(answered + " answered", "success")}${partly ? pill(partly + " partly refused", "warning") : ""}${pill(refused + " refused", "critical")}</div>
        <p class="muted">One row per question. Click a row for the metric passport: definition, source, denominator, coverage basis and what was excluded. Fidelity: <strong>measured</strong> counted from logs, <strong>derived</strong> computed from measured numbers, <strong>judged</strong> scored by a model judge.</p></div>
      <section class="card flush"><div class="table-wrap"><table class="data metrics">
        <thead><tr><th>Ask</th><th>Question</th><th>Answer</th><th>Fidelity</th><th>Coverage</th><th>Status</th></tr></thead>
        <tbody>${rows.join("")}</tbody></table></div></section>
      ${std.length ? `<section class="card flush"><div data-style="padding:16px 20px 4px"><h2 class="title">Standards: the best this cohort has already done</h2></div><div class="table-wrap"><table class="data">
        <thead><tr><th>Tenant</th><th>Cohort</th><th>Metric</th><th>Best</th><th>Median</th><th>Deficit</th><th>Exemplars</th></tr></thead>
        <tbody>${std.map((s) => `<tr><td>${esc(s.tenant)}</td><td class="mono">${esc(cohortText(s.cohort))}</td><td class="mono">${esc(metricLbl(s.metric))}</td>
          <td class="mono">${esc(metricVal(s.metric, s.best))}</td><td class="mono">${esc(metricVal(s.metric, s.median))}</td><td class="mono">${esc(metricVal(s.metric, s.deficit))}</td><td class="mono">${int(s.exemplar_n)}</td></tr>`).join("")}</tbody></table></div></section>` : ""}
    </div>`;
  }

  /* ---------------- decisions ---------------- */
  function renderDecisions(rep, ix) {
    const rx = rep.prescriptions || [];
    const sa = rep.self_assessment || {};
    const vers = rep.verifications || [];
    const hits = vers.filter((v) => v.verdict === "improved").length;
    const errs = vers.map((v) => v.prediction_error).filter(isNum);
    const meanErr = errs.length ? errs.reduce((a, b) => a + b, 0) / errs.length : null;
    const rows = rx.map((p) => {
      const f = ix.findingForRx(p);
      const v = ix.ver(p);
      const a = p.approval;
      const r = v ? REPLAY_UI[v.verdict] || [v.verdict, "neutral"] : ["Not replayed", "neutral"];
      const d = a ? VERDICT_UI[a.verdict] || [a.verdict, "neutral"] : ["Pending", "warning"];
      const pd = p.predicted_delta || {};
      return `<tr>
        <td class="mono">${esc(p.id.split("_")[0])}</td>
        <td>${esc(f ? headlineShort(f) : p.diagnosis_id)}<div class="small muted">${esc(f ? f.tenant : "")}</div></td>
        <td>${esc(p.target)}<div class="small muted mono">${esc(p.change_type)}</div></td>
        <td class="mono num-cell">${esc(metricVal(pd.metric, pd.from))} → ${esc(metricVal(pd.metric, pd.to))}</td>
        <td>${pill(r[0], r[1])}${v && v.golden_set_pass === false ? `<div class="small muted">a known-good conversation got worse</div>` : ""}</td>
        <td><div class="row" data-style="gap:8px">${pill(d[0], d[1])}${f ? `<button class="btn sm secondary" data-key="f:${esc(f.id)}">${a ? "Change" : "Decide"}</button>` : ""}</div></td>
        <td>${esc(a ? a.decided_by : "—")}</td>
        <td class="num-cell">${a ? esc(when(a.at)) : "—"}</td>
        <td>${a ? esc(a.reason) : '<span class="muted">—</span>'}</td></tr>`;
    });
    const audit = rx.filter((p) => p.approval).sort((a, b) => String(b.approval.at).localeCompare(String(a.approval.at)));
    const trusted = isNum(sa.cycles) && sa.cycles >= 3;
    return `<div class="page stack">
      <div class="header"><div class="row" data-style="justify-content:space-between;align-items:baseline;width:100%"><h1 class="display">What humans decided</h1>${audit.length ? `<button class="btn sm secondary" data-copy="${esc(allDecisionsNote(rep, ix))}">${I.copy}Copy all decisions</button>` : ""}</div>
        <p class="muted">Every decision below is read back from loop-report.json.</p></div>
      <section class="card flush"><div class="table-wrap"><table class="data">
        <thead><tr><th>Fix</th><th>Finding</th><th>Change</th><th>Predicted</th><th>Replay</th><th>Decision</th><th>By</th><th>When</th><th>Reason</th></tr></thead>
        <tbody>${rows.join("") || '<tr><td colspan="9" class="muted">No fixes were proposed this run.</td></tr>'}</tbody></table></div></section>
      <section class="card"><div class="card-head"><h2 class="title">How far to trust the replays</h2><span class="right">${pill("simulated", "neutral")}</span></div>
        <div class="stats">
          <div><span class="label">Replay cycles</span><div class="v">${esc(int(sa.cycles))}</div><p class="small muted">${trusted ? "enough cycles to weigh the priors" : "fewer than 3, not trusted yet"}</p></div>
          <div><span class="label">Replays that improved</span><div class="v">${vers.length ? `${hits} of ${vers.length}` : "—"}</div><p class="small muted">verdict from the replay endpoint</p></div>
          <div><span class="label">Prediction error</span><div class="v">${meanErr == null ? "—" : esc(num(meanErr, 3)) + " pts"}</div><p class="small muted">predicted minus replayed delta${(sa.downweighted || []).length ? " · de-weighted: " + esc(sa.downweighted.join(", ")) : ""}</p></div>
        </div>
        ${sa.notes ? `<p class="small muted" data-style="margin-top:14px">${esc(sa.notes)}</p>` : ""}
      </section>
      <div class="grid2">
        <section class="card"><div class="card-head"><h2 class="title">Audit log</h2></div>
          ${audit.length ? `<ul class="audit">${audit.map((p) => `<li><span class="muted">${esc(when(p.approval.at))}</span><span><strong>${esc((VERDICT_UI[p.approval.verdict] || [p.approval.verdict])[0])}</strong> <code>${esc(p.id)}</code> by ${esc(p.approval.decided_by)}</span></li>`).join("")}</ul>` : '<p class="muted">No decisions yet.</p>'}
        </section>
        <section class="card"><div class="card-head"><h2 class="title">What is real, stubbed and simulated</h2></div>${systemNotes(rep.system_notes)}</section>
      </div>
    </div>`;
  }
  function systemNotes(s) {
    const text = String(s || "").trim();
    if (!text) return '<p class="muted">No system notes in this report.</p>';
    const parts = text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
    const cls = { REAL: "success", STUBBED: "warning", SIMULATED: "neutral" };
    return `<div class="notes small">${parts.map((p) => {
      const m = p.match(/^([A-Z][A-Z0-9 ]{2,24}):\s*/);
      return m ? `<p><span class="tag">${pill(m[1], "upper " + (cls[m[1]] || "critical"))}</span>${rich(p.slice(m[0].length))}</p>` : `<p class="muted">${rich(p)}</p>`;
    }).join("")}</div>`;
  }

  /* ---------------- main render ---------------- */
  function render() {
    const prev = document.activeElement;
    const prevKey = prev && prev.getAttribute ? (prev.getAttribute("data-key") || prev.getAttribute("data-open")) : null;
    const rep = state.rep;
    const view = $("view");
    if (!rep) {
      $("shell").classList.remove("shell");
      $("rail").hidden = true;
      view.innerHTML = state.error && state.error.kind === "missing"
        ? `<div class="empty">${I.alert}<h1 class="title">No report loaded</h1><p class="muted">Generate one, then reload this page.</p>
            <span class="code">python run.py --kit kit --team demo --out out/loop-report.json</span></div>`
        : state.error
          ? `<div class="empty">${I.alert}<h1 class="title">Report unavailable</h1><p class="muted">Fix or regenerate out/loop-report.json, then reload.</p></div>`
          : `<div class="skeleton" aria-busy="true" aria-label="Loading report"><i class="s"></i><i class="h"></i><i class="b"></i><i></i><i class="b"></i></div>`;
      return;
    }
    const ix = idx(rep);
    renderTop(rep, ix);
    const withRail = state.tab === "findings" || state.tab === "refusals";
    $("shell").classList.toggle("shell", withRail);
    $("rail").hidden = !withRail;
    if (withRail) {
      const rememberedKey = state.byTab[state.tab];
      if (!validKey(ix, state.key, state.tab)) state.key = (rememberedKey && validKey(ix, rememberedKey, state.tab)) ? rememberedKey : defaultKey(ix, state.tab);
      state.byTab[state.tab] = state.key;
      renderRail(rep, ix);
      const [kind, id] = splitKey(state.key);
      let body;
      if (kind === "f") {
        const f = (rep.findings || []).find((x) => x.id === id);
        body = f.is_regression ? renderRegression(rep, ix, f) : renderDismissed(rep, ix, f);
      } else if (kind === "g") {
        body = renderGap(rep, ix.gaps.find((g) => g.ask_id === id));
      } else {
        body = state.tab === "refusals"
          ? `<div class="empty"><h1 class="title">Every ask has a number</h1><p class="muted">No refusals in this report.</p></div>`
          : `<div class="empty"><h1 class="title">No regressions found</h1><p class="muted">${ix.dismissed.length} lookalikes checked.</p></div>`;
      }
      view.innerHTML = body;
    } else if (state.tab === "metrics") {
      view.innerHTML = renderMetrics(rep);
    } else {
      view.innerHTML = renderDecisions(rep, ix);
    }
    fixInlineStyles(view);
    view.querySelectorAll("i[data-w]").forEach((el) => {
      requestAnimationFrame(() => { el.style.width = (parseFloat(el.dataset.w) * 100).toFixed(1) + "%"; });
    });
    document.title = "Nexus Loop · " + state.tab[0].toUpperCase() + state.tab.slice(1);
    if (prevKey) {
      let el = null;
      try { el = view.querySelector('[data-open="' + CSS.escape(prevKey) + '"]') || $("rail").querySelector('[data-key="' + CSS.escape(prevKey) + '"]'); } catch (e) { el = null; }
      if (el && el.focus) el.focus();
    }
  }
  function fixInlineStyles(root) {
    root.querySelectorAll("[data-style]").forEach((el) => {
      const s = el.getAttribute("data-style"); el.removeAttribute("data-style"); el.style.cssText = s;
    });
  }
  const splitKey = (k) => { const i = String(k || "").indexOf(":"); return i < 0 ? [null, null] : [k.slice(0, i), k.slice(i + 1)]; };
  function validKey(ix, key, tab) {
    const [kind, id] = splitKey(key);
    if (tab === "refusals") return kind === "g" && ix.gaps.some((g) => g.ask_id === id);
    return kind === "f" && ix.regs.concat(ix.dismissed).some((f) => f.id === id);
  }
  function defaultKey(ix, tab) {
    if (tab === "refusals") { const g = ix.gaps.find((x) => x.ask_id === "A11") || ix.gaps[0]; return g ? "g:" + g.ask_id : null; }
    // open on the story the demo tells: the fix that was tested on replay, else the silent tool failure
    const tested = ix.regs.find((f) => ix.ver(ix.rx(f)));
    const tool = ix.regs.find((f) => (ix.diag(f) || {}).cause_class === "tool.contract_break");
    const f = tested || tool || ix.regs[0] || ix.dismissed[0];
    return f ? "f:" + f.id : null;
  }

  /* ---------------- routing ---------------- */
  function readHash() {
    let h; try { h = decodeURIComponent(location.hash.replace(/^#\/?/, "")); } catch (e) { h = location.hash.replace(/^#\/?/, ""); }
    const [tab, ...rest] = h.split("/");
    if (h && !TABS.includes(tab)) return;   // e.g. #view from the skip link
    state.tab = TABS.includes(tab) ? tab : "findings";
    const key = rest.join("/");
    state.key = key ? (state.tab === "refusals" ? "g:" : "f:") + key : null;
  }
  function go(tab, key) {
    const id = key ? splitKey(key)[1] : "";
    const h = "#/" + tab + (id ? "/" + encodeURIComponent(id) : "");
    if (location.hash === h) { readHash(); render(); } else location.hash = h;
  }
  function openKey(key) {
    const [kind] = splitKey(key);
    go(kind === "g" ? "refusals" : "findings", key);
    window.scrollTo(0, 0);
  }

  /* ---------------- data ---------------- */
  async function load() {
    try {
      const r = await Promise.resolve(new Response(JSON.stringify(window.__BRAG_REPORT), {status: 200}));
      let body = null;
      try { body = await r.json(); } catch (e) { body = null; }
      if (r.status === 404) { state.rep = null; state.error = { kind: "missing" }; banner(""); }
      else if (!r.ok || !body) {
        state.error = { kind: "invalid" };
        banner(`Could not load the report: ${body && body.error === "report_invalid" ? "the report file is not valid JSON" : "server returned " + r.status}. Nothing was changed.`);
      } else { state.rep = body; state.error = null; banner(""); }
    } catch (e) {
      state.error = { kind: "offline" };
      banner("Could not reach the screen server. Is python -m nexus_loop.app still running?");
    }
    render();
  }
  function banner(msg) {
    $("banner").innerHTML = msg ? `<div class="callout error" role="alert">${I.alert}<p>${esc(msg)}</p></div>` : "";
  }

  async function submitDecision(form, verdict, btn) {
    const reasonEl = $("reason"), whoEl = $("who");
    const reason = reasonEl.value.trim();
    const field = $("reason-field");
    if (!reason) { field.classList.add("invalid"); reasonEl.setAttribute("aria-invalid", "true"); reasonEl.focus(); return; }
    field.classList.remove("invalid");
    reasonEl.setAttribute("aria-invalid", "false");
    const who = whoEl.value.trim() || "operator";
    if (!/^[\w .@+-]{1,80}$/.test(who)) { showDecideError("Name may use letters, digits, spaces and . @ + - only."); whoEl.focus(); return; }
    storeSet("nl.who", who);
    const buttons = form.querySelectorAll("button");
    buttons.forEach((b) => (b.disabled = true));
    const label = btn.innerHTML;
    btn.innerHTML = '<span class="spin"></span>Saving…';
    showDecideError("");
    try {
      const r = await fetch("/decision", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prescription_id: form.dataset.pid, verdict, decided_by: who, reason }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || body.error || "HTTP " + r.status);
      toast(`${(VERDICT_UI[verdict] || [verdict])[0]} · saved to loop-report.json`);
      await load();
      const gh = $("gate") && $("gate").querySelector("h2");
      if (gh) { gh.setAttribute("tabindex", "-1"); gh.focus(); }
    } catch (e) {
      buttons.forEach((b) => (b.disabled = false));
      btn.innerHTML = label;
      showDecideError("Could not save: " + e.message + ". Nothing changed.");
    }
  }
  function showDecideError(msg) {
    const el = $("decide-error");
    if (el) el.innerHTML = msg ? `<div class="callout error small" role="alert">${I.alert}<p>${esc(msg)}</p></div>` : "";
  }
  let toastTimer = 0;
  function toast(msg) {
    const t = $("toast"); t.textContent = msg; t.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
  }
  function storeGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function storeSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }

  /* ---------------- events ---------------- */
  document.addEventListener("click", (ev) => {
    const tab = ev.target.closest(".tab");
    if (tab) return go(tab.dataset.tab);
    const copy = ev.target.closest("[data-copy]");
    if (copy) {
      const text = copy.getAttribute("data-copy");
      (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(() => toast("Copied"), () => toast("Copy not available here"));
      return;
    }
    const sum = ev.target.closest("details.rail-group > summary");
    if (sum) { const d = sum.parentElement; requestAnimationFrame(() => { state.railOpen[d.dataset.group] = d.open; }); return; }
    const keyEl = ev.target.closest("[data-key]");
    if (keyEl) return openKey(keyEl.dataset.key);
    const row = ev.target.closest("tr[data-open]");
    if (row) { state.open[row.dataset.open] = !state.open[row.dataset.open]; render(); }
  });
  document.addEventListener("keydown", (ev) => {
    const row = ev.target.closest && ev.target.closest("tr[data-open], tr[data-key]");
    if (row && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); row.click(); return; }
    if (ev.target.closest && ev.target.closest(".rail-item") && (ev.key === "ArrowDown" || ev.key === "ArrowUp")) {
      ev.preventDefault();
      const items = [...document.querySelectorAll(".rail-item")];
      const i = items.indexOf(ev.target.closest(".rail-item"));
      const next = items[Math.max(0, Math.min(items.length - 1, i + (ev.key === "ArrowDown" ? 1 : -1)))];
      if (next) { openKey(next.dataset.key); requestAnimationFrame(() => { const el = document.querySelector(`.rail-item[data-key="${CSS.escape(next.dataset.key)}"]`); if (el) el.focus(); }); }
    }
  });
  let lastSubmitter = null;
  document.addEventListener("submit", (ev) => {
    if (ev.target.id !== "decide") return;
    ev.preventDefault();
    const btn = ev.submitter || lastSubmitter;
    if (btn && btn.dataset.verdict) submitDecision(ev.target, btn.dataset.verdict, btn);
  });
  document.addEventListener("pointerdown", (ev) => { const b = ev.target.closest("button[data-verdict]"); if (b) lastSubmitter = b; });
  document.addEventListener("input", (ev) => { if (ev.target.id === "reason" && ev.target.value.trim()) $("reason-field").classList.remove("invalid"); });
  window.addEventListener("hashchange", () => { readHash(); render(); });

  readHash();
  load();
})();
