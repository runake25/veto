/* Veto console — data, components, routing. No dependencies, no build step.
   Rendered from one dataset so every total on screen is a sum of rows. */

/* ---- helpers ----------------------------------------------------------- */
const money = (n) => "$" + Number(n).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
const pad2 = (n) => String(n).padStart(2, "0");
const hhmm = (mins) => pad2(Math.floor((mins % 1440) / 60)) + ":" + pad2(mins % 60);
const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const charterById = (id) => DATA.charters.find((c) => c.id === id);
const agentById = (id) => AGENTS.find((a) => a.id === id);
const agentRequests = (id) => DATA.requests.filter((r) => r.agent === id);
const RULES = ["monthly_cap", "per_order_cap", "per_unit_cap", "approval_threshold", "vendor_allowlist", "category_allowlist", "catalogue"];
function agentTotals() {
  return AGENTS.map(function (a) {
    const mine = agentRequests(a.id);
    return {
      a: a,
      count: mine.length,
      held: mine.filter((r) => isOpen(r.status)).length,
      spent: mine.filter((r) => settled(r.status)).reduce((s, r) => s + totalOf(r), 0),
      heldSum: mine.filter((r) => isOpen(r.status)).reduce((s, r) => s + totalOf(r), 0),
      refused: mine.filter((r) => r.status === "vetoed").reduce((s, r) => s + totalOf(r), 0)
    };
  });
}
function tagTotals() {
  const map = new Map();
  DATA.requests.forEach(function (r) {
    const t = map.get(r.tag) || { tag: r.tag, count: 0, committed: 0, refused: 0 };
    t.count += 1;
    if (r.status === "vetoed") t.refused += totalOf(r); else t.committed += totalOf(r);
    map.set(r.tag, t);
  });
  return Array.from(map.values()).sort((x, y) => y.committed - x.committed);
}

/* How one catalogue item fares against the agent's own rules and its charter.
   "fits"   the agent may order it without a human
   "consent" it is inside the allowance but needs a signature
   "refused" the rule set forbids it outright */
function verdict(agent, item) {
  const c = charterById(agent.charter);
  const rules = agent.rules;
  if (rules.indexOf("catalogue") !== -1 && !CATALOGUE.some((i) => i.sku === item.sku)) return { kind: "refused", why: "not in the catalogue" };
  if (rules.indexOf("category_allowlist") !== -1 && ["pantry", "stationery", "equipment"].indexOf(item.category) === -1) return { kind: "refused", why: "category not allowed" };
  if (rules.indexOf("vendor_allowlist") !== -1 && c.vendors.indexOf(item.vendor) === -1) return { kind: "refused", why: item.vendor + " is not on the approved list" };
  if (rules.indexOf("per_unit_cap") !== -1 && c.perUnit && item.unit > c.perUnit) return { kind: "refused", why: money(item.unit) + " a unit against a " + money(c.perUnit) + " cap" };
  if (rules.indexOf("per_order_cap") !== -1 && item.unit > c.perOrder) return { kind: "refused", why: "over the " + money(c.perOrder) + " order cap" };
  if (rules.indexOf("approval_threshold") !== -1 && item.unit > c.threshold) return { kind: "consent", why: "over the " + money(c.threshold) + " threshold, so it needs a human" };
  return { kind: "fits", why: "inside every rule this agent holds" };
}
function agentFits(agent) {
  return CATALOGUE.filter((i) => verdict(agent, i).kind !== "refused");
}
const totalOf = (r) => r.lines.reduce((s, l) => s + l.qty * l.unit, 0);
const settled = (s) => s === "auto" || s === "captured" || s === "approved";
const isOpen = (s) => s === "awaiting";
const STATUS = { auto: "Auto-approved", captured: "Captured", approved: "Approved", awaiting: "Awaiting", vetoed: "Vetoed" };

function ruleShort(r) {
  if (!r.reasons.length) return "within policy";
  const x = r.reasons[0];
  if (x.rule === "per_unit_cap") return money(x.observed) + " > " + money(x.limit) + " a unit";
  if (x.rule === "approval_threshold") return money(x.observed) + " > " + money(x.limit) + " threshold";
  if (x.rule === "vendor_allowlist") return "vendor not approved";
  if (x.rule === "per_order_cap") return money(x.observed) + " > " + money(x.limit) + " cap";
  return x.rule;
}
function ruleWhy(r) {
  if (!r.reasons.length) return "inside the charter";
  const x = r.reasons[0];
  if (x.rule === "per_unit_cap") return money(x.observed) + " a unit against a " + money(x.limit) + " cap";
  if (x.rule === "approval_threshold") return "over the " + money(x.limit) + " approval threshold";
  if (x.rule === "vendor_allowlist") return x.observed + " is not on the approved list";
  return ruleShort(r);
}
function statusMark(s) {
  if (s === "awaiting") return '<span class="pill pill-await">Awaiting</span>';
  if (s === "vetoed") return '<span class="pill pill-veto">Vetoed</span>';
  return '<span class="plain-status">' + STATUS[s] + "</span>";
}

/* the consent seal: single-use, bound to the cart hash, expiring */
const CONSENT_TTL = "10 min";
function sealFor(r) {
  let h = 2166136261;
  const src = r.id + ":" + totalOf(r).toFixed(2) + ":" + r.status;
  for (let i = 0; i < src.length; i++) { h ^= src.charCodeAt(i); h = Math.imul(h, 16777619); }
  const hex = (h >>> 0).toString(16).padStart(8, "0");
  return "7f" + hex.slice(0, 4) + "ac1d" + hex.slice(4) + "9e3b";
}

/* ---- state ------------------------------------------------------------- */
const S = {
  view: "ledger",
  decided: [],
  filterStatus: "all",
  filterCharter: "all",
  filterVendor: "all",
  minAmount: "",
  paletteOpen: false,
  paletteQuery: "",
  paletteIndex: 0,
  agent: "pantry",
  query: "",
  open: null
};

/* ---- motion ------------------------------------------------------------
   One rule: animate what the user caused, never a re-render. `paint` takes an
   opts bag, figures are tweened from their last rendered value, and rows that
   survive a mutation are FLIPped so the list slides instead of blinking. */
let booted = false;
let lastView = null;
let animateNext = false;
const lastNums = {};

function fmtNum(n, fmt) {
  if (fmt === "money") return money(n);
  return String(Math.round(n));
}

function tweenNumbers(animate) {
  const els = document.querySelectorAll("[data-num]");
  for (let i = 0; i < els.length; i++) {
    const el = els[i];
    const key = el.getAttribute("data-num");
    const target = parseFloat(el.getAttribute("data-v"));
    const fmt = el.getAttribute("data-fmt") || "int";
    const from = lastNums[key];
    lastNums[key] = target;
    if (!animate || from === undefined || Math.abs(from - target) < 0.005) continue;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) continue;
    const t0 = performance.now();
    const dur = 320;
    el.textContent = fmtNum(from, fmt);
    const step = function (t) {
      const k = Math.min(1, (t - t0) / dur);
      const e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmtNum(from + (target - from) * e, fmt);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
}

function captureTops(selector) {
  const map = new Map();
  document.querySelectorAll(selector).forEach(function (el) {
    const k = el.getAttribute("data-key");
    if (k) map.set(k, el.getBoundingClientRect().top);
  });
  return map;
}

function playShifts(prev, selector) {
  if (!prev || !prev.size) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  document.querySelectorAll(selector).forEach(function (el) {
    const k = el.getAttribute("data-key");
    if (!k || !prev.has(k)) return;
    const dy = prev.get(k) - el.getBoundingClientRect().top;
    if (Math.abs(dy) < 1) return;
    el.classList.add("is-shifting");
    el.style.transform = "translateY(" + dy.toFixed(1) + "px)";
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        el.style.transform = "translateY(0)";
        setTimeout(function () {
          el.classList.remove("is-shifting");
          el.style.transform = "";
        }, 300);
      });
    });
  });
}

/* ---- derived totals ---------------------------------------------------- */
function charterTotals() {
  return DATA.charters.map((c) => {
    const mine = DATA.requests.filter((r) => r.charter === c.id);
    const spent = mine.filter((r) => settled(r.status)).reduce((s, r) => s + totalOf(r), 0);
    const committed = mine.filter((r) => isOpen(r.status)).reduce((s, r) => s + totalOf(r), 0);
    return Object.assign({}, c, { spent, committed, count: mine.length });
  });
}
function workspaceTotals() {
  const t = charterTotals();
  const open = DATA.requests.filter((r) => isOpen(r.status));
  const vetoed = DATA.requests.filter((r) => r.status === "vetoed");
  const done = DATA.requests.filter((r) => settled(r.status));
  return {
    spent: t.reduce((s, c) => s + c.spent, 0),
    committed: t.reduce((s, c) => s + c.committed, 0),
    cap: t.reduce((s, c) => s + c.cap, 0),
    openCount: open.length,
    openSum: open.reduce((s, r) => s + totalOf(r), 0),
    vetoedCount: vetoed.length,
    vetoedSum: vetoed.reduce((s, r) => s + totalOf(r), 0),
    doneCount: done.length,
    total: DATA.requests.length
  };
}
const pendingFor = (charterId) => DATA.requests.filter((r) => r.status === "awaiting" && r.charter === charterId);

/* ---- icons (drawn, one set, 1.5px stroke) ------------------------------ */
const I = {
  mark: '<svg class="brand-mark" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3.4 4.2 9.9 15.6l6.5-11.4"/><path d="M12.4 17.4h5"/></svg>',
  ledger: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2.6" y="2.9" width="10.8" height="10.2" rx="1.6"/><path d="M2.6 6.3h10.8M5.6 9h4.8"/></svg>',
  requests: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M2.6 8.9v3.2c0 .6.5 1.1 1.1 1.1h8.6c.6 0 1.1-.5 1.1-1.1V8.9"/><path d="M2.6 8.9 4.4 3.4c.1-.3.4-.5.7-.5h5.8c.3 0 .6.2.7.5l1.8 5.5"/><path d="M2.6 8.9h3l.7 1.4h3.4l.7-1.4h3"/></svg>',
  charters: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M2.6 12.6h10.8"/><path d="M4.7 12.6V7.4M8 12.6V3.9M11.3 12.6V9.6"/></svg>',
  approvals: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="5.4"/><path d="M5.7 8.1 7.3 9.7l3-3.2"/></svg>',
  search: '<svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="7.2" cy="7.2" r="4.2"/><path d="m10.4 10.4 3 3"/></svg>',
  help: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="8" cy="8" r="5.4"/><path d="M6.5 6.4a1.6 1.6 0 1 1 2.2 1.5c-.4.2-.6.5-.6.9v.2"/><path d="M8 11.1v.01"/></svg>',
  chevron: '<svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m4.5 6.2 3.5 3.6 3.5-3.6"/></svg>',
  copy: '<svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5.4" y="5.4" width="7.4" height="7.4" rx="1.4"/><path d="M10.6 5.4V4.2c0-.7-.6-1.3-1.3-1.3H4.2c-.7 0-1.3.6-1.3 1.3v5.1c0 .7.6 1.3 1.3 1.3h1.2"/></svg>',
  warn: '<svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M8 2.8 14 13H2z"/><path d="M8 6.6v2.8" stroke-linecap="round"/><path d="M8 11.2v.01" stroke-linecap="round"/></svg>'
};

Object.assign(I, EXTRA_ICONS);

/* ---- the authority rail: this product's signature object --------------- */
function railFor(r) {
  const base = r.at;
  const steps = [];
  const lines = r.lines.length;
  const id = (t) => '<code class="mono">' + t + "</code>";
  steps.push({ what: "Slash /veto", time: hhmm(base), ref: r.channel + " · " + r.requester + " · " + agentById(r.agent).name, state: "done" });
  steps.push({
    what: lines + (lines === 1 ? " line" : " lines") + " extracted",
    time: hhmm(base), ref: "text only, no prices taken", state: "done"
  });
  steps.push({ what: "Priced " + money(totalOf(r)), time: hhmm(base + 1), ref: "charter catalogue", state: "done" });
  steps.push({
    what: "Rules evaluated", time: hhmm(base + 1),
    ref: r.reasons.length ? "7 checks · " + id(r.reasons[0].rule) : "7 checks · all clear",
    state: "done"
  });

  if (r.status === "vetoed") {
    steps.push({ what: "Vetoed", time: r.decision ? r.decision.at : hhmm(base + 2), ref: r.decision ? r.decision.by + ", on the record" : "approver", state: "stopped" });
    steps.push({ what: "No consent issued", ref: "nothing was signed", state: "void" });
    steps.push({ what: "No order", ref: "PayPal was never called", state: "void" });
    steps.push({ what: "No capture", ref: "zero moved", state: "void" });
    return steps;
  }

  if (r.status === "auto") {
    steps.push({ what: "Auto-approved", time: hhmm(base + 2), ref: "inside every limit", state: "done" });
    steps.push({ what: "No consent needed", time: hhmm(base + 2), ref: "policy took it", state: "void" });
  } else if (r.status === "awaiting") {
    steps.push({ what: "Awaiting consent", ref: "needs a human on the record", state: "live" });
  } else {
    steps.push({ what: "Consent issued", time: hhmm(base + 3), ref: id("sha256:" + sealFor(r).slice(0, 9) + "…"), state: "done" });
  }
  steps.push({ what: "Order created", time: r.order ? hhmm(base + 4) : null, ref: r.order ? id(r.order) : "waiting on consent", state: r.order ? "done" : "todo" });
  steps.push({ what: "Authorised", time: r.auth ? hhmm(base + 5) : null, ref: r.auth ? id(r.auth) : "waiting", state: r.auth ? "done" : "todo" });
  steps.push({ what: "Captured", time: r.capture ? hhmm(base + 6) : null, ref: r.capture ? id(r.capture) : "no money has moved", state: r.capture ? "done" : "todo" });
  if (r.status === "awaiting") steps[steps.length - 1].ref = "no money has moved";
  if (r.capture) steps[steps.length - 1].time = hhmm(base + 6);
  return steps;
}

function railHtml(r) {
  return railFor(r).map(function (s) {
    return (
      '<li class="rail-step is-' + (s.state || "todo") + '"><span class="rail-mark"></span>' +
      '<div class="rail-head"><span class="rail-what">' + esc(s.what) + "</span>" +
        (s.time ? '<span class="rail-time">' + s.time + "</span>" : "") + "</div>" +
      (s.ref ? '<div class="' + (s.state === "void" ? "rail-note" : "rail-ref") + '">' + s.ref + "</div>" : "") +
      "</li>"
    );
  }).join("");
}

/* ---- track: spent, in flight, overrun ---------------------------------- */
function trackHtml(t, opts, animate) {
  const cap = t.cap || 1;
  const spentPct = Math.min(100, (t.spent / cap) * 100);
  const commitPct = Math.min(100 - spentPct, (t.committed / cap) * 100);
  const over = Math.max(0, t.spent + t.committed - cap);
  const overPct = Math.min(100, (over / cap) * 100);
  const o = opts || {};
  const w0 = animate ? "0%" : spentPct.toFixed(2) + "%";
  let segs = '<span class="seg seg-spent" data-w="' + spentPct.toFixed(2) + '%" style="width:' + w0 + '"></span>';
  if (commitPct > 0) segs += '<span class="seg seg-commit" data-w="' + commitPct.toFixed(2) + '%" style="left:' + spentPct.toFixed(2) + "%;width:" + (animate ? "0%" : commitPct.toFixed(2) + "%") + '"></span>';
  if (overPct > 0) segs += '<span class="seg seg-over" style="left:' + (100 - overPct).toFixed(2) + "%;width:" + overPct.toFixed(2) + '%"></span>';
  return '<div class="track">' + segs + "</div>" + (o.foot === false ? "" : "");
}

/* ---- chrome ------------------------------------------------------------ */
function navItem(key, label, icon, href, count) {
  const active = S.view === key || (key === "ledger" && S.view === "ledger");
  return (
    '<a class="nav-item" href="' + href + '"' + (active ? ' aria-current="page"' : "") + ' data-nav="' + key + '">' +
    I[icon] + '<span class="lbl">' + label + "</span>" +
    (count ? '<span class="nav-count">' + count + "</span>" : "") + "</a>"
  );
}

function shell(inner, opts) {
  const o = opts || {};
  const w = workspaceTotals();
  const nav =
    '<div class="sidebar">' +
      '<button class="switcher" type="button" aria-label="Switch workspace">' + I.charters +
        "<span>" + DATA.org + "</span><span class=\"chev\">" + I.chevron + "</span></button>" +
      '<div class="nav-label">Spending</div>' +
      navItem("ledger", "Ledger", "ledger", "#/", 0) +
      navItem("approvals", "Approvals", "approvals", "#/approvals", w.openCount) +
      navItem("requests", "Requests", "requests", "#/requests", 0) +
      '<div class="nav-label">Agents</div>' +
      navItem("agents", "Agents", "agents", "#/agents", AGENTS.length) +
      navItem("catalogue", "Find a product", "catalogue", "#/catalogue", 0) +
      navItem("charters", "Charters", "charters", "#/charters", 0) +
      '<div class="nav-label">Insight</div>' +
      navItem("analytics", "Analytics", "analytics", "#/analytics", 0) +
      '<div class="sidebar-foot">' +
        '<div class="side-note"><b>Intake is Slack only</b><span>Every request starts as <code class="mono">/veto</code> in an agent’s channel. Nothing is created here.</span></div>' +
        '<button class="userrow" type="button">' +
          '<span class="avatar">PR</span>' +
          '<span class="who"><b>' + DATA.approver + '</b><span>Ops · approver</span></span>' +
        "</button>" +
      "</div>" +
    "</div>";

  return (
    '<div class="app">' +
      '<header class="topbar">' +
        '<div class="brand">' + I.mark + '<span class="brand-name">Veto</span></div>' +
        '<button class="searchbar" type="button" data-open-palette aria-label="Search requests, charters and vendors">' +
          I.search + '<span class="grow">Search requests, charters, vendors</span><kbd class="kbd">⌘K</kbd>' +
        "</button>" +
        '<div class="topbar-right">' +
          '<button class="iconbtn" type="button" aria-label="Help">' + I.help + "</button>" +
          '<span class="avatar" aria-hidden="true">PR</span>' +
        "</div>" +
      "</header>" +
      '<div class="body">' + nav + '<div class="sheet-wrap"><main class="sheet" id="main">' + inner + "</main></div></div>" +
    "</div>"
  );
}

/* ---- ledger ------------------------------------------------------------ */
function titleRow(w) {
  const finding =
    w.openCount === 1
      ? "One request is waiting on you, " + money(w.openSum) + " of it. The rest of today ran inside the charter."
      : w.openCount + " requests are waiting on you, " + money(w.openSum) + " in total. One line breaks the " + money(15) + " a unit cap by " + money(3) + ".";
  return (
    '<div class="titlerow">' +
      '<div class="lead"><h1>Ledger</h1><p class="sub">' + finding + "</p></div>" +
      '<div class="actions"><span class="stamp">Updated 15:04</span>' +
        '<button class="btn btn-primary" type="button" data-nav="approvals">Review ' + w.openCount + "</button></div>" +
    "</div>"
  );
}

function kpiStrip(w) {
  return (
    '<section class="kpis" aria-label="This month in numbers">' +
      '<div class="cell lead"><div class="cell-label">Awaiting your consent (' + w.openCount + ")</div>" +
        '<div class="cell-value"><span data-num="open" data-v="' + w.openSum + '" data-fmt="money">' + money(w.openSum) + '</span><span class="unit">held</span></div></div>' +
      '<div class="cell"><div class="cell-label">Spent this month</div>' +
        '<div class="cell-value"><span data-num="spent" data-v="' + w.spent + '" data-fmt="money">' + money(w.spent) + '</span><span class="unit">of ' + money(w.cap) + "</span></div></div>" +
      '<div class="cell"><div class="cell-label">Vetoed this month</div>' +
        '<div class="cell-value"><span data-num="vetoed" data-v="' + w.vetoedCount + '" data-fmt="int">' + w.vetoedCount + '</span><span class="unit">' + money(w.vetoedSum) + " refused</span></div></div>" +
      '<div class="cell"><div class="cell-label">Auto-approved</div>' +
        '<div class="cell-value"><span data-num="done" data-v="' + w.doneCount + '" data-fmt="int">' + w.doneCount + '</span><span class="unit">of ' + w.total + " requests</span></div></div>" +
    "</section>"
  );
}

function queueCard(rows) {
  if (!rows.length) {
    return (
      '<section class="card"><div class="card-head"><h2 class="card-title">Awaiting your consent</h2></div>' +
      '<div class="card-body"><div class="empty"><p>Nothing is waiting on you. Every request today cleared the charter.</p>' +
      '<div class="act"><button class="btn btn-sm" type="button" data-nav="ledger">See the whole ledger</button></div></div></div></section>'
    );
  }
  return (
    '<section class="card">' +
      '<div class="card-head"><h2 class="card-title">Awaiting your consent <span class="count">' + rows.length + "</span></h2>" +
        '<span class="aside">oldest first</span></div>' +
      '<div class="card-body"><div class="qlist">' +
        rows.map(function (r) {
          return (
            '<div class="qrow" data-row="' + r.id + '" data-key="' + r.id + '">' +
              '<button class="q-open" type="button" data-open="' + r.id + '">' +
                '<span class="q-name-row"><span class="q-id">' + r.id + '</span><span class="q-name">' + esc(r.title) + "</span></span>" +
              "</button>" +
              '<div class="q-meta"><span class="q-seg">' + esc(agentById(r.agent).name) + '</span>' +
                '<span class="q-seg why">' + esc(ruleWhy(r)) + "</span></div>" +
              '<div class="q-amount num">' + money(totalOf(r)) + "</div>" +
              '<div class="q-acts">' +
                '<button class="btn btn-sm" type="button" data-act="approve" data-id="' + r.id + '">Approve</button>' +
                '<button class="btn btn-sm btn-ghost btn-danger" type="button" data-act="veto" data-id="' + r.id + '">Veto</button>' +
              "</div>" +
            "</div>"
          );
        }).join("") +
      "</div>" + decidedStrip() + "</div>" +
      '<div class="card-foot">Every approval issues one single-use consent, bound to the cart hash.</div>' +
    "</section>"
  );
}

/* What you just signed or stopped, kept in place as a receipt. It grows as the
   queue empties, so the card keeps its height and the decision is still legible. */
function decidedStrip() {
  const d = S.decided.slice(-3).reverse();
  if (!d.length) return "";
  return (
    '<div class="decided">' +
      '<div class="decided-head">Decided just now</div>' +
      d.map(function (x) {
        const yes = x.action === "approve";
        return '<a class="drow" href="#' + x.id + '">' +
          '<span class="d-act ' + (yes ? "d-yes" : "d-no") + '">' + (yes ? "Approved" : "Vetoed") + "</span>" +
          '<span class="q-id">' + x.id + "</span>" +
          '<span class="d-title">' + esc(x.title) + "</span>" +
          '<span class="d-amt num">' + money(x.amount) + "</span></a>";
      }).join("") +
    "</div>"
  );
}

function chartersCard() {
  const t = charterTotals();
  return (
    '<section class="card">' +
      '<div class="card-head"><h2 class="card-title">Charters</h2>' +
        '<span class="aside"><a class="link" href="#/charters">Manage</a></span></div>' +
      '<div class="card-body">' +
        t.map(function (c) {
          return (
            '<div class="trk">' +
              '<div class="trk-head"><span class="trk-name">' + c.name + "</span>" +
                (c.committed ? '<span class="trk-flight">' + money(c.committed) + " in flight</span>" : "") +
                '<span class="trk-fig"><b>' + money(c.spent) + "</b> of " + money(c.cap) + "</span></div>" +
              trackHtml(c) +
            "</div>"
          );
        }).join("") +
      "</div>" +
      '<div class="card-foot legend"><span class="lg"><i class="sw sw-spent"></i>spent this month</span>' +
        '<span class="lg"><i class="sw sw-flight"></i>committed, not signed</span></div>' +
    "</section>"
  );
}

function filteredRequests() {
  const min = parseFloat(S.minAmount);
  return DATA.requests.filter(function (r) {
    if (S.filterStatus !== "all" && r.status !== S.filterStatus) return false;
    if (S.filterCharter !== "all" && r.charter !== S.filterCharter) return false;
    if (S.filterVendor !== "all" && r.vendor !== S.filterVendor) return false;
    if (!isNaN(min) && totalOf(r) < min) return false;
    return true;
  });
}

function options(values, selected) {
  return values.map(function (v) {
    const val = v.value === undefined ? v : v.value;
    const label = v.label === undefined ? v : v.label;
    return '<option value="' + val + '"' + (val === selected ? " selected" : "") + ">" + esc(label) + "</option>";
  }).join("");
}

function tableBlock() {
  const rows = filteredRequests();
  const vendors = Array.from(new Set(DATA.requests.map((r) => r.vendor))).sort();
  const toolbar =
    '<div class="toolbar">' +
      '<label class="visually-hidden" for="f-status">Filter by status</label>' +
      '<select class="select" id="f-status" data-filter="status">' +
        options([{ value: "all", label: "All statuses" }, { value: "awaiting", label: "Awaiting" }, { value: "vetoed", label: "Vetoed" },
                 { value: "captured", label: "Captured" }, { value: "auto", label: "Auto-approved" }], S.filterStatus) + "</select>" +
      '<label class="visually-hidden" for="f-charter">Filter by charter</label>' +
      '<select class="select" id="f-charter" data-filter="charter">' +
        options([{ value: "all", label: "All charters" }].concat(DATA.charters.map((c) => ({ value: c.id, label: c.name }))), S.filterCharter) + "</select>" +
      '<label class="visually-hidden" for="f-vendor">Filter by vendor</label>' +
      '<select class="select" id="f-vendor" data-filter="vendor">' +
        options([{ value: "all", label: "All vendors" }].concat(vendors.map((v) => ({ value: v, label: v }))), S.filterVendor) + "</select>" +
      '<label class="visually-hidden" for="f-amount">Minimum amount</label>' +
      '<input class="amount" id="f-amount" data-filter="amount" inputmode="decimal" placeholder="≥ $50" value="' + esc(S.minAmount) + '">' +
      '<span class="count">' + rows.length + " of " + DATA.requests.length + " requests</span>" +
    "</div>";

  const body = rows.length
    ? rows.map(function (r) {
        return (
          '<a class="trow" href="#' + r.id + '" data-open="' + r.id + '" data-key="' + r.id + '">' +
            '<span class="c-req"><span class="id">' + r.id + '</span><span class="title">' + esc(r.title) + "</span></span>" +
            '<span class="c-charter">' + esc(charterById(r.charter).name) + "</span>" +
            '<span class="c-vendor">' + esc(r.vendor) + "</span>" +
            '<span class="c-rule">' + esc(ruleShort(r)) + "</span>" +
            '<span class="c-amount num">' + money(totalOf(r)) + "</span>" +
            '<span class="c-status">' + statusMark(r.status) + "</span>" +
            '<span class="c-meta-inline">' + esc(charterById(r.charter).name) + " · " + esc(r.vendor) + " · " + esc(ruleShort(r)) + "</span>" +
          "</a>"
        );
      }).join("")
    : '<div class="empty"><p>No request matches these filters.</p>' +
      '<div class="act"><button class="btn btn-sm" type="button" data-act="clear-filters">Clear filters</button></div></div>';

  return (
    '<div class="listwrap">' + toolbar +
      '<div class="table"><div class="thead">' +
        '<span class="c-req">Request</span><span class="c-charter">Charter</span><span class="c-vendor">Vendor</span>' +
        '<span class="c-rule">Rule</span><span class="c-amount r">Amount</span><span class="c-status">Status</span>' +
      "</div>" +
      '<div class="tbody">' + body + "</div>" +
      '<div class="card-foot">' + DATA.period + " · all amounts include tax" +
        '<span class="end"><button class="btn btn-sm" type="button" data-act="export">Export CSV</button></span></div>' +
      "</div></div>"
  );
}

/* ---- agents: the unit of authority ------------------------------------ */
const tagChip = (t) => '<span class="tag">' + esc(t) + "</span>";

function agentsTable() {
  const rows = agentTotals();
  return (
    '<div class="atable">' +
      '<div class="arow arow-head"><span>Agent</span><span>Charter</span><span>Rules</span><span>Tags</span>' +
        '<span class="r">Requests</span><span class="r">Held</span><span class="r">Spent</span></div>' +
      rows.map(function (t) {
        return (
          '<a class="arow" href="#/agents/' + t.a.id + '" data-open-agent="' + t.a.id + '">' +
            '<span class="a-name-cell"><span class="a-name">' + esc(t.a.name) + "</span>" +
              '<span class="a-scope">' + esc(t.a.scope) + "</span></span>" +
            "<span>" + esc(charterById(t.a.charter).name) + "</span>" +
            '<span class="a-rules">' + t.a.rules.length + " of 7</span>" +
            '<span class="a-tags">' + t.a.tags.map(tagChip).join("") + "</span>" +
            '<span class="r num">' + t.count + "</span>" +
            '<span class="r num">' + (t.held || "—") + "</span>" +
            '<span class="r c-amount num">' + money(t.spent) + "</span>" +
          "</a>"
        );
      }).join("") +
      '<div class="card-foot">An agent can only apply the rules it holds. Slack is the only way in: <code class="mono">/veto</code> in its channel opens it.</div>' +
    "</div>"
  );
}

function agentsView() {
  const t = agentTotals();
  const held = t.reduce((s, x) => s + x.held, 0);
  const heldSum = t.reduce((s, x) => s + x.heldSum, 0);
  const refused = t.reduce((s, x) => s + x.refused, 0);
  const tags = new Set();
  AGENTS.forEach((a) => a.tags.forEach((x) => tags.add(x)));
  return (
    '<div class="sheet-inner">' +
      '<div class="titlerow"><div class="lead"><h1>Agents</h1>' +
        "<p class=\"sub\">Five agents, each with its own scope, its own slice of a charter and its own rules. " +
        "A slash command in Slack is the only thing that opens one.</p></div>" +
        '<div class="actions"><span class="stamp">' + DATA.period + "</span>" +
        '<a class="btn btn-primary" href="#/catalogue">Find a product</a></div></div>' +
      '<section class="kpis" aria-label="Agents in numbers">' +
        '<div class="cell lead"><div class="cell-label">Agents open</div>' +
          '<div class="cell-value">' + t.length + '<span class="unit">one channel each</span></div></div>' +
        '<div class="cell"><div class="cell-label">Awaiting consent</div>' +
          '<div class="cell-value">' + held + '<span class="unit">' + money(heldSum) + " held</span></div></div>" +
        '<div class="cell"><div class="cell-label">Refused by a rule</div>' +
          '<div class="cell-value">' + money(refused) + '<span class="unit">this month</span></div></div>' +
        '<div class="cell"><div class="cell-label">Tags in use</div>' +
          '<div class="cell-value">' + tags.size + '<span class="unit">across agents</span></div></div>' +
      "</section>" +
      agentsTable() +
      '<div class="how">' +
        '<div><span class="k">1 · In Slack</span><span class="v"><code class="mono">/veto</code> three boxes of capsules, from kettle supply</span></div>' +
        '<div><span class="k">2 · The agent</span><span class="v">extracts the items, prices them and applies only the rules it holds</span></div>' +
        '<div><span class="k">3 · A human</span><span class="v">signs anything over its threshold, and every decision leaves a receipt</span></div>' +
      "</div>" +
    "</div>"
  );
}

function agentView(id) {
  const a = agentById(id);
  if (!a) return '<div class="sheet-inner"><div class="empty"><p>No agent by that name.</p></div></div>';
  const c = charterById(a.charter);
  const tot = agentTotals().find((x) => x.a.id === id);
  const mine = agentRequests(id).slice().sort((x, y) => y.at - x.at);
  const fits = agentFits(a).length;
  const rules = RULES.map(function (r) {
    const on = a.rules.indexOf(r) !== -1;
    const limit = r === "monthly_cap" ? money(c.cap) + " a month"
      : r === "per_order_cap" ? money(c.perOrder) + " an order"
      : r === "per_unit_cap" ? (c.perUnit ? money(c.perUnit) + " a unit" : "not set on this charter")
      : r === "approval_threshold" ? money(c.threshold) + ", over it a human signs"
      : r === "vendor_allowlist" ? c.vendors.length + " approved vendors"
      : "not configured";
    return (
      '<div class="rule-row' + (on ? "" : " is-off") + '">' +
        '<button class="switch" type="button" role="switch" aria-checked="' + (on ? "true" : "false") +
          '" data-rule="' + r + '" data-agent="' + a.id + '" aria-label="' + esc(RULE_LABEL[r] || r) + '"><span></span></button>' +
        '<div class="rule-copy"><b>' + esc(RULE_LABEL[r] || r) + "</b><span>" + esc(limit) + "</span></div>" +
        '<span class="rule-state">' + (on ? "on" : "off") + "</span>" +
      "</div>"
    );
  }).join("");

  return (
    '<div class="sheet-inner">' +
      '<div class="crumbs"><a href="#/agents">Agents</a><span class="sep">/</span><span>' + esc(a.name) + "</span></div>" +
      '<div class="record-head"><div class="lead"><h1>' + esc(a.name) + "</h1>" +
        '<div class="sub"><span>' + esc(c.name) + '</span><span class="dot">·</span><span>' + esc(a.owner) +
          '</span><span class="dot">·</span><span class="cmd-inline"><code class="mono">/veto</code> in <code class="mono">' + esc(a.channel) + "</code></span></div></div>" +
        '<div class="actions"><button class="btn" type="button" data-act="copy-cmd" data-cmd="/veto ' + esc(a.channel) + '">Copy command</button>' +
          (tot.held ? '<a class="btn btn-primary" href="#/approvals">Review ' + tot.held + "</a>" : "") + "</div></div>" +
      '<div class="record-cols">' +
        '<div class="record-main">' +
          '<section class="card"><div class="card-head"><h2 class="card-title">Rules this agent holds</h2>' +
            '<span class="aside">' + a.rules.length + " of " + RULES.length + "</span></div>" +
            '<div class="rules-list">' + rules + "</div>" +
            '<div class="card-foot">Switch one off and the agent stops applying it, from the next slash command on.</div></section>' +
          '<section class="card"><div class="card-head"><h2 class="card-title">Tags</h2>' +
            '<span class="aside">carried into analytics</span></div>' +
            '<div class="tags-block">' + a.tags.map(tagChip).join("") +
              '<p class="hint">Every request this agent raises is tagged with these, so committed and refused money can be grouped by them in analytics.</p></div>' +
            '<div class="card-foot">Scope: ' + esc(a.scope) + "</div></section>" +
        "</div>" +
        '<div class="record-side agent-side">' +
          '<section class="card"><div class="card-head"><h2 class="card-title">Requests from this agent</h2>' +
            '<span class="aside">' + mine.length + "</span></div>" +
            '<div class="agent-reqs">' + (mine.length ? mine.map(function (r) {
              return '<a class="areq" href="#' + r.id + '">' +
                '<span class="areq-main"><span class="q-id">' + r.id + "</span>" +
                  '<span class="areq-title">' + esc(r.title) + "</span></span>" +
                '<span class="areq-meta">' + esc(r.tag) + " · " + hhmm(r.at) + "</span>" +
                '<span class="c-amount num">' + money(totalOf(r)) + "</span>" +
                '<span class="c-status">' + statusMark(r.status) + "</span></a>";
            }).join("") : '<div class="empty"><p>This agent has raised nothing this month.</p></div>') + "</div>" +
            '<div class="card-foot">Nothing else reaches it. ' + esc(a.owner) + " owns the scope; " + esc(c.approver) + " signs.</div>" +
          "</section>" +
          '<div class="fits"><span class="fits-n">' + fits + " of " + CATALOGUE.length + "</span>" +
            '<span class="fits-t">catalogue items pass<br>these rules right now</span>' +
            '<a class="link" href="#/catalogue/' + a.id + '">See them</a></div>' +
        "</div>" +
      "</div>" +
    "</div>"
  );
}

/* ---- find a product: the search is the rule set ------------------------ */
function catalogueView() {
  const agent = agentById(S.agent) || AGENTS[0];
  const q = S.query.trim().toLowerCase();
  const rows = CATALOGUE.filter(function (i) {
    if (!q) return true;
    return (i.name + " " + i.vendor + " " + i.category + " " + i.sku).toLowerCase().indexOf(q) !== -1;
  });
  const fits = rows.filter((i) => verdict(agent, i).kind !== "refused").length;
  const VERDICT = { fits: ["pill pill-fit", "Fits"], consent: ["pill pill-await", "Needs consent"], refused: ["pill pill-veto", "Refused"] };
  return (
    '<div class="sheet-inner">' +
      '<div class="titlerow"><div class="lead"><h1>Find a product</h1>' +
        '<p class="sub">The catalogue an agent may search. An item that breaks one of its rules is marked, not hidden, so you can see why the search stops where it does.</p></div>' +
        '<div class="actions"><span class="stamp">' + CATALOGUE.length + " items · October catalogue</span></div></div>" +
      '<div class="agent-pick" role="tablist" aria-label="Agent">' +
        AGENTS.map(function (a) {
          return '<button class="agent-chip' + (a.id === agent.id ? " is-on" : "") + '" type="button" role="tab" aria-selected="' +
            (a.id === agent.id ? "true" : "false") + '" data-agent-pick="' + a.id + '">' + esc(a.name) + "</button>";
        }).join("") +
      "</div>" +
      '<div class="searchrow">' +
        '<label class="searchfield">' + I.search +
          '<input id="cat-search" type="search" autocomplete="off" placeholder="Search the catalogue: coffee, paper, keyboard, vendor…" value="' + esc(S.query) + '">' +
        "</label>" +
        '<span class="searchrow-count">' + fits + " of " + rows.length + " fit " + esc(agent.name) + "</span>" +
      "</div>" +
      '<div class="table cat-table"><div class="thead">' +
        "<span>Item</span><span>Vendor</span><span>Category</span><span class=\"r\">Unit</span><span>Verdict</span>" +
      "</div>" +
      '<div class="tbody">' + (rows.length ? rows.map(function (i) {
        const v = verdict(agent, i);
        const [cls, label] = VERDICT[v.kind];
        return '<div class="cat-row">' +
          '<span class="cat-item"><span class="cat-name">' + esc(i.name) + '</span><span class="cat-sku mono">' + esc(i.sku) + "</span></span>" +
          "<span>" + esc(i.vendor) + "</span>" +
          '<span class="tag">' + esc(i.category) + "</span>" +
          '<span class="r num">' + money(i.unit) + "</span>" +
          '<span class="cat-verdict"><span class="' + cls + '">' + label + "</span>" +
            (v.kind === "fits" ? "" : '<span class="cat-why">' + esc(v.why) + "</span>") + "</span>" +
        "</div>";
      }).join("") : '<div class="empty"><p>Nothing in the catalogue matches that search.</p></div>') + "</div>" +
      '<div class="card-foot">Ordering happens in Slack: <code class="mono">/veto …</code> in <code class="mono">' + esc(agent.channel) + "</code>. " +
        "This screen is where you check what that agent is allowed to find.</div></div>" +
    "</div>"
  );
}

/* ---- analytics: tags and agents --------------------------------------- */
function analyticsView() {
  const w = workspaceTotals();
  const tags = tagTotals();
  const maxTag = Math.max.apply(null, tags.map((t) => t.committed));
  const committed = tags.reduce((s, t) => s + t.committed, 0);
  const rows = agentTotals();
  const rate = Math.round((w.vetoedCount / w.total) * 100);
  const amounts = DATA.requests.map(totalOf).sort((a, b) => a - b);
  const median = (amounts[6] + amounts[7]) / 2;
  return (
    '<div class="sheet-inner">' +
      '<div class="titlerow"><div class="lead"><h1>Analytics</h1>' +
        '<p class="sub">October 2026, grouped by the tags each agent carries and by the agent itself. Refused money is kept apart from money that moved.</p></div>' +
        '<div class="actions"><span class="stamp">Updated 15:04</span>' +
        '<button class="btn" type="button" data-act="export">Export CSV</button></div></div>' +
      '<section class="kpis" aria-label="The month in numbers">' +
        '<div class="cell lead"><div class="cell-label">Committed (' + DATA.requests.length + " requests)</div>" +
          '<div class="cell-value">' + money(committed) + '<span class="unit">of ' + money(w.cap) + "</span></div></div>" +
        '<div class="cell"><div class="cell-label">Refused by a rule</div>' +
          '<div class="cell-value">' + money(w.vetoedSum) + '<span class="unit">' + w.vetoedCount + " requests</span></div></div>" +
        '<div class="cell"><div class="cell-label">Refusal rate</div>' +
          '<div class="cell-value">' + rate + '%<span class="unit">of every request</span></div></div>' +
        '<div class="cell"><div class="cell-label">Median request</div>' +
          '<div class="cell-value">' + money(median) + '<span class="unit">half are smaller</span></div></div>' +
      "</section>" +
      '<div class="cols analytics-cols">' +
        '<section class="card"><div class="card-head"><h2 class="card-title">Committed by tag</h2>' +
          '<span class="aside">refused money excluded</span></div>' +
          '<div class="card-body bar-list">' + tags.map(function (t) {
            return '<div class="barrow">' +
              '<div class="barrow-top"><span class="barrow-key"><span class="tag">' + esc(t.tag) + "</span>" +
                '<span class="barrow-sub">' + t.count + (t.count === 1 ? " request" : " requests") + "</span></span>" +
                '<span class="barrow-val num">' + money(t.committed) + "</span></div>" +
              '<div class="barrow-track"><span style="width:' + ((t.committed / maxTag) * 100).toFixed(1) + '%"></span></div>' +
              (t.refused ? '<div class="barrow-foot"><span>' + money(t.refused) + " refused</span></div>" : "") +
            "</div>";
          }).join("") + "</div>" +
          '<div class="card-foot">A tag is written when the agent raises the request, so the grouping is the agent’s, not a guess.</div>' +
        "</section>" +
        '<section class="card"><div class="card-head"><h2 class="card-title">By agent</h2>' +
          '<span class="aside">' + rows.length + "</span></div>" +
          '<div class="card-body abar-list">' + rows.map(function (r) {
            const share = r.spent / Math.max.apply(null, rows.map((x) => x.spent));
            return '<a class="arow-mini" href="#/agents/' + r.a.id + '">' +
              '<span class="arow-mini-top"><span class="a-name">' + esc(r.a.name) + '</span>' +
                '<span class="barrow-val num">' + money(r.spent) + "</span></span>" +
              '<div class="barrow-track"><span style="width:' + (share * 100).toFixed(1) + '%"></span></div>' +
              '<span class="arow-mini-foot">' + r.count + " requests" + (r.held ? " · " + r.held + " held" : "") +
                (r.refused ? " · " + money(r.refused) + " refused" : "") + "</span></a>";
          }).join("") + "</div>" +
          '<div class="mix">' +
            '<div><span class="k">Auto-approved</span><div class="v">' + DATA.requests.filter((r) => r.status === "auto").length + "</div></div>" +
            '<div><span class="k">Captured</span><div class="v">' + DATA.requests.filter((r) => r.status === "captured").length + "</div></div>" +
            '<div><span class="k">Awaiting</span><div class="v">' + w.openCount + "</div></div>" +
            '<div><span class="k">Vetoed</span><div class="v">' + w.vetoedCount + "</div></div>" +
          "</div>" +
          '<div class="card-foot">' + money(w.spent) + " settled across " + rows.length + " agents.</div>" +
        "</section>" +
      "</div>" +
      '<div class="stopped">' + stoppedRules() + "</div>" +
    "</div>"
  );
}

/* which rule stopped a request, counted, with the money it held back */
function stoppedRules() {
  const map = new Map();
  DATA.requests.forEach(function (r) {
    if (!r.reasons.length) return;
    const k = r.reasons[0].rule;
    const v = map.get(k) || { n: 0, sum: 0 };
    v.n += 1;
    v.sum += totalOf(r);
    map.set(k, v);
  });
  return Array.from(map.entries()).sort((a, b) => b[1].n - a[1].n).map(function (e) {
    return '<div><span class="k">' + esc(RULE_LABEL[e[0]] || e[0]) + "</span>" +
      '<div class="v">' + e[1].n + (e[1].n === 1 ? " request · " : " requests · ") + money(e[1].sum) + "</div></div>";
  }).join("");
}

function requestsView() {
  const w = workspaceTotals();
  return (
    '<div class="sheet-inner">' +
      '<div class="titlerow"><div class="lead"><h1>Requests</h1>' +
        '<p class="sub">Every request this month, the rule it was judged on, and where the money stands.</p></div>' +
        '<div class="actions"><span class="stamp">' + w.total + " requests · " + DATA.period + "</span></div></div>" +
      tableBlock() +
    "</div>"
  );
}

function ledgerView() {
  const w = workspaceTotals();
  const rows = DATA.requests.filter((r) => isOpen(r.status)).sort((a, b) => a.at - b.at);
  return (
    '<div class="sheet-inner">' +
      titleRow(w) + kpiStrip(w) +
      '<div class="cols">' + queueCard(rows) + chartersCard() + "</div>" +
      tableBlock() +
    "</div>"
  );
}

/* ---- charters view ----------------------------------------------------- */
function chartersView() {
  const t = charterTotals();
  return (
    '<div class="sheet-inner">' +
      '<div class="titlerow"><div class="lead"><h1>Charters</h1>' +
        '<p class="sub">Four budget lines for ' + DATA.period + ", each with its own limits and its own approver. An agent can only spend inside one of these.</p></div>" +
        '<div class="actions"><span class="stamp">Set by Finance · 1 Oct</span>' +
        '<button class="btn btn-primary" type="button" data-act="export">Export CSV</button></div></div>' +
      '<div class="kpis" aria-label="This month in numbers">' +
        '<div class="cell"><div class="cell-label">Committed across charters</div><div class="cell-value">' + money(t.reduce((s, c) => s + c.spent, 0)) + '<span class="unit">of ' + money(t.reduce((s, c) => s + c.cap, 0)) + "</span></div></div>" +
        '<div class="cell"><div class="cell-label">In flight</div><div class="cell-value">' + money(t.reduce((s, c) => s + c.committed, 0)) + '<span class="unit">awaiting consent</span></div></div>' +
        '<div class="cell"><div class="cell-label">Charters over 80%</div><div class="cell-value">' + t.filter((c) => (c.spent / c.cap) > 0.8).length + '<span class="unit">of ' + t.length + "</span></div></div>" +
        '<div class="cell"><div class="cell-label">Approvers</div><div class="cell-value">' + Array.from(new Set(t.map((c) => c.approver))).length + '<span class="unit">people</span></div></div>' +
      "</div>" +
      '<div class="cols charters-cols">' +
        t.map(function (c) {
          const pend = pendingFor(c.id);
          return (
            '<section class="card charter-card">' +
              '<div class="card-head"><h2 class="card-title">' + c.name + "</h2>" +
                '<span class="aside">' + c.approver + "</span></div>" +
              '<div class="charter-body">' +
                '<div class="charter-fig"><b>' + money(c.spent) + "</b><span> of " + money(c.cap) + " this month</span></div>" +
                trackHtml(c) +
                '<div class="track-foot"><span>' + (c.committed ? money(c.committed) + " in flight" : "&nbsp;") +
                  "</span><span>" + Math.round((c.spent / c.cap) * 100) + "% used</span></div>" +
                '<dl class="rules">' +
                  "<div><dt>Monthly cap</dt><dd>" + money(c.cap) + "</dd></div>" +
                  "<div><dt>Per order</dt><dd>" + money(c.perOrder) + "</dd></div>" +
                  "<div><dt>Per unit</dt><dd>" + (c.perUnit ? money(c.perUnit) : "—") + "</dd></div>" +
                  "<div><dt>Requests this month</dt><dd>" + c.count + "</dd></div>" +
                "</dl>" +
                '<div class="charter-pending">' + (pend.length
                  ? pend.length + (pend.length === 1 ? " request is" : " requests are") + " waiting: " + money(pend.reduce((s, r) => s + totalOf(r), 0))
                  : "Nothing waiting on this line") + "</div>" +
              "</div>" +
            "</section>"
          );
        }).join("") +
      "</div>" +
    "</div>"
  );
}

/* ---- record view ------------------------------------------------------- */
function recordView(r) {
  const c = charterById(r.charter);
  const total = totalOf(r);
  const note = r.reasons.find((x) => x.rule === "per_unit_cap");
  const after = {
    cap: c.cap,
    spent: charterTotals().find((x) => x.id === c.id).spent + total,
    committed: Math.max(0, charterTotals().find((x) => x.id === c.id).committed - total)
  };
  const cat = charterTotals().find((x) => x.id === c.id);
  const lines = r.lines.map(function (l) {
    const bad = note && l.unit > note.limit;
    return (
      '<div class="line' + (bad ? " bad" : "") + '">' +
        '<span class="item" title="' + esc(l.item) + '">' + esc(l.item) + "</span>" +
        '<span class="qty">' + l.qty + "</span>" +
        '<span class="unit num">' + money(l.unit) + "</span>" +
        '<span class="total num">' + money(l.qty * l.unit) + "</span>" +
      "</div>"
    );
  }).join("");

  const cart =
    '<section class="card">' +
      '<div class="card-head"><h2 class="card-title">Cart</h2>' +
        '<span class="aside">' + esc(r.vendor) + " · priced from the charter catalogue</span></div>" +
      '<div class="lines">' +
        '<div class="line-head"><span>Item</span><span class="r">Qty</span><span class="r">Unit</span><span class="r">Line</span></div>' +
        lines +
        (note ? '<div class="line-note"><span class="mark">' + I.warn + "</span>" + esc(money(note.observed)) +
          " a unit, against the " + esc(money(note.limit)) + " cap on " + esc(c.name.toLowerCase()) + ".</div>" : "") +
        '<div class="lines-total">Cart total <span class="note">' + (r.lines.length) + " lines · " + esc(r.vendor) + "</span>" +
          "<b>" + money(total) + "</b></div>" +
      "</div>" +
    "</section>";

  const decision =
    '<section class="card">' +
      '<div class="card-head"><h2 class="card-title">Why it stopped here</h2>' +
        '<span class="aside">' + (r.reasons.length ? r.reasons.length + " rule" + (r.reasons.length > 1 ? "s" : "") + " fired" : "no rule fired") + "</span></div>" +
      '<dl class="kv">' +
        "<dt>Agent</dt><dd>" + esc(agentById(r.agent).name) + ' <span class="note">· /veto in ' + esc(r.channel) + "</span></dd>" +
        "<dt>Charter</dt><dd>" + esc(c.name) + ' <span class="note">· approver ' + esc(c.approver) + "</span></dd>" +
        "<dt>Tags</dt><dd>" + '<span class="tag">' + esc(r.tag) + "</span>" + ' <span class="note">· carried into analytics</span></dd>' +
        "<dt>Rule</dt><dd>" + (r.reasons.length
          ? '<span class="mono-val">' + esc(r.reasons[0].rule) + "</span>" + (note ? '<span class="breach">' + esc(money(note.observed)) + " against " + esc(money(note.limit)) + "</span>" : "")
          : "Within every limit") + "</dd>" +
        (r.amendment
          ? "<dt>Amended</dt><dd>" + esc(r.amendment.by) + " at " + esc(r.amendment.at) + ' <span class="note">“' + esc(r.amendment.text) + '” ' + money(r.amendment.from) + " → " + money(r.amendment.to) + "</span></dd>"
          : "") +
        (r.decision
          ? "<dt>Decision</dt><dd>" + esc(r.decision.by) + " at " + esc(r.decision.at) + ' <span class="note">“' + esc(r.decision.note) + '”</span></dd>'
          : "") +
      "</dl>" +
      (isOpen(r.status)
        ? '<div class="effect"><div class="effect-label">If you approve' + "</div>" +
            '<div class="effect-line"><b>' + money(after.spent) + "</b><span class=\"muted\">of " + money(c.cap) + " spent on " + esc(c.name) + " this month, " + money(c.cap - after.spent) + " left</span></div>" +
            trackHtml(after) +
            '<div class="effect-foot"><span>' + money(after.spent) + " spent</span><span>" + money(after.committed) + " still in flight</span></div>" +
            '<div class="effect-line"><span class="muted">Consent is single-use and expires in ' + CONSENT_TTL + ". The order is created only after you sign.</span></div>" +
          "</div>"
        : "") +
    "</section>";

  const signed = r.status !== "awaiting" && r.status !== "vetoed";
  const consent =
    '<section class="consent">' +
      '<div class="consent-head"><h2>Consent</h2>' +
        '<span class="consent-flag">' + (isOpen(r.status) ? "Not issued" : r.status === "vetoed" ? "Never issued" : "Signed") + "</span></div>" +
      (signed
        ? '<div class="hash"><code>sha256:' + sealFor(r) + '</code>' +
            '<button class="copy" type="button" data-act="copy" data-hash="' + sealFor(r) + '">Copy</button></div>'
        : '<p class="consent-none">' + (r.status === "vetoed"
            ? "No consent was ever issued for this cart. Nothing can be ordered against it."
            : "Signing issues one hash of this exact cart, bound to " + esc(c.approver) + ", single use, expiring in " + CONSENT_TTL + ".") + "</p>") +
      '<div class="consent-grid">' +
        '<div><span class="k">Approver</span><span class="v">' + (isOpen(r.status) ? "You" : esc(c.approver)) + "</span></div>" +
        '<div><span class="k">Bound to</span><span class="v">' + esc(r.id) + " · " + money(total) + "</span></div>" +
        '<div><span class="k">Expires</span><span class="v">' + (isOpen(r.status) ? CONSENT_TTL + " after signing" : r.status === "vetoed" ? "—" : "24 Oct, 14:34") + "</span></div>" +
        '<div><span class="k">Use</span><span class="v">Single use</span></div>' +
      "</div>" +
    "</section>";

  const actions = isOpen(r.status)
    ? '<button class="btn btn-primary" type="button" data-act="approve" data-id="' + r.id + '">Approve</button>' +
      '<button class="btn btn-danger" type="button" data-act="veto" data-id="' + r.id + '">Veto</button>' +
      '<button class="btn" type="button" data-act="amend">Amend cap</button>'
    : '<button class="btn" type="button" data-nav="ledger">Back to the ledger</button>';

  return (
    '<div class="sheet-inner">' +
      '<div class="crumbs"><a href="#/">Ledger</a><span class="sep">/</span><span>' + r.id + "</span></div>" +
      '<div class="record-head">' +
        '<div class="lead"><h1><span class="id">' + r.id + "</span>" + esc(r.title) + "</h1>" +
          '<div class="sub"><span class="who-agent">' + esc(agentById(r.agent).name) + '</span><span class="dot">·</span><span>' + esc(c.name) + '</span><span class="dot">·</span><span>' + esc(r.requester) +
            '</span><span class="dot">·</span><span>' + esc(r.channel) + '</span><span class="dot">·</span>' +
            statusMark(r.status) + "</div></div>" +
        '<div class="actions">' + actions + "</div>" +
      "</div>" +
      '<div class="record-cols">' +
        '<div class="record-main">' + cart + decision + "</div>" +
        '<div class="record-side">' +
          '<section class="card"><div class="card-head"><h2 class="card-title">Authority</h2>' +
            '<span class="aside">Slack to capture</span></div>' +
            '<ol class="rail">' + railHtml(r) + "</ol></section>" +
          consent +
        "</div>" +
      "</div>" +
    "</div>"
  );
}

/* ---- palette ----------------------------------------------------------- */
function paletteItems() {
  const w = workspaceTotals();
  const open = DATA.requests.filter((r) => isOpen(r.status));
  const items = [];
  open.forEach(function (r) {
    items.push({ group: "Actions", label: "Approve " + r.title, q: r.id + " · " + money(totalOf(r)), hint: "↵", act: "approve", id: r.id });
    items.push({ group: "Actions", label: "Veto " + r.title, q: r.id + " · " + money(totalOf(r)), hint: "↵", act: "veto", id: r.id });
  });
  items.push({ group: "Actions", label: "Export ledger as CSV", q: w.total + " requests · " + DATA.period, hint: "↵", act: "export" });
  items.push({ group: "Actions", label: "Clear all filters", q: "status, charter, vendor, amount", hint: "↵", act: "clear-filters" });
  DATA.requests.forEach(function (r) {
    items.push({ group: "Requests", label: r.title, q: r.id + " · " + charterById(r.charter).name + " · " + money(totalOf(r)), hint: "↵", nav: "#" + r.id });
  });
  DATA.charters.forEach(function (c) {
    items.push({ group: "Charters", label: c.name, q: money(c.cap) + " a month · " + c.approver, hint: "↵", nav: "#/charters" });
  });
  CATALOGUE.slice(0, 6).forEach(function (i) {
    items.push({ group: "Catalogue", label: i.name, q: i.vendor + " · " + money(i.unit), hint: "↵", act: "cat", q2: i.name });
  });
  Array.from(new Set(DATA.requests.map((r) => r.vendor))).sort().forEach(function (v) {
    items.push({ group: "Vendors", label: v, q: DATA.requests.filter((r) => r.vendor === v).length + " requests", hint: "↵", act: "vendor", id: v });
  });
  return items;
}

function paletteMatches() {
  const q = S.paletteQuery.trim().toLowerCase();
  const all = paletteItems();
  if (!q) return all;
  return all.filter((it) => (it.label + " " + it.q).toLowerCase().indexOf(q) !== -1);
}

function paletteListHtml() {
  const rows = paletteMatches();
  if (!rows.length) return '<div class="palette-empty">Nothing matches “' + esc(S.paletteQuery) + '”.</div>';
  let lastGroup = null, html = "";
  rows.forEach(function (it, i) {
    if (it.group !== lastGroup) { html += '<div class="palette-group">' + it.group + "</div>"; lastGroup = it.group; }
    html += '<button class="palette-item" type="button" role="option" id="pi-' + i + '" aria-selected="' + (i === S.paletteIndex ? "true" : "false") +
      '" data-pi="' + i + '">' + esc(it.label) +
      (it.q ? '<span class="q">' + esc(it.q) + "</span>" : "") +
      (it.hint ? '<span class="hint">' + esc(it.hint) + "</span>" : "") + "</button>";
  });
  return html;
}

function paletteHtml() {
  return (
    '<div class="palette-field">' + I.search +
      '<input id="palette-input" type="text" role="combobox" aria-expanded="true" aria-controls="palette-list" ' +
      'aria-autocomplete="list" autocomplete="off" spellcheck="false" placeholder="Search or run a command" value="' + esc(S.paletteQuery) + '">' +
      '<kbd class="esc">esc</kbd>' +
    "</div>" +
    '<div class="palette-list" id="palette-list" role="listbox" aria-label="Commands and records">' + paletteListHtml() + "</div>" +
    '<div class="palette-foot"><span><b>↑↓</b> to move</span><span><b>↵</b> to run</span><span><b>esc</b> to close</span>' +
      '<span class="palette-count" id="palette-count">' + paletteMatches().length + " results</span></div>"
  );
}

function renderPaletteList() {
  const list = document.getElementById("palette-list");
  if (list) list.innerHTML = paletteListHtml();
  const count = document.getElementById("palette-count");
  if (count) count.textContent = paletteMatches().length + " results";
}

/* ---- mutations --------------------------------------------------------- */
function approve(id) {
  const r = DATA.requests.find((x) => x.id === id);
  if (!r || !isOpen(r.status)) return;
  const prev = captureTops(".qrow[data-key]");
  const row = document.querySelector('.qrow[data-row="' + id + '"]');
  const mutate = function () {
    r.status = "approved";
    r.consent = sealFor(r);
    r.order = "9XT" + String(4000 + (r.at % 900)) + "PL" + String(200 + (r.at % 700));
    r.auth = "0AW" + String(20000 + (r.at % 9000)) + "M4" + String(10000 + (r.at % 8000));
    r.capture = "3C6" + String(40000 + (r.at % 9000)) + "X1" + String(10000 + (r.at % 8000));
    S.paletteOpen = false;
    if (location.hash === "#palette") history.replaceState(null, "", "#/");
    S.decided.push({ id: r.id, action: "approve", title: r.title, amount: totalOf(r) });
    paint({ animate: true, stamp: S.view === "record" });
    playShifts(prev, ".qrow[data-key]");
    toast("Approved " + r.id + " · consent " + sealFor(r).slice(0, 9) + "… sent to PayPal");
  };
  /* the row leaves toward the action, then the list closes the gap */
  if (row && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    row.classList.add("is-leaving");
    setTimeout(mutate, 150);
  } else mutate();
}

function veto(id) {
  const r = DATA.requests.find((x) => x.id === id);
  if (!r || !isOpen(r.status)) return;
  const prev = captureTops(".qrow[data-key]");
  const row = document.querySelector('.qrow[data-row="' + id + '"]');
  const mutate = function () {
    r.status = "vetoed";
    r.decision = { by: DATA.approver, at: "15:0" + Math.floor(Math.random() * 9), note: "vetoed on the record, no consent issued" };
    S.paletteOpen = false;
    if (location.hash === "#palette") history.replaceState(null, "", "#/");
    S.decided.push({ id: r.id, action: "veto", title: r.title, amount: totalOf(r) });
    paint({ animate: true, stamp: S.view === "record" });
    playShifts(prev, ".qrow[data-key]");
    toast("Vetoed " + r.id + " · no consent, no order, no capture");
  };
  if (row && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    row.classList.add("is-leaving");
    setTimeout(mutate, 150);
  } else mutate();
}

let toastTimer = null;
function toast(msg) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { el.classList.remove("on"); }, 4200);
}

/* ---- routing ----------------------------------------------------------- */
function parseHash() {
  const h = decodeURIComponent((location.hash || "").replace(/^#\/?/, ""));
  if (h === "palette") { S.view = "ledger"; S.open = null; S.paletteOpen = true; return; }
  S.paletteOpen = false;
  if (/^req-\d+$/i.test(h)) { S.view = "record"; S.open = h.toUpperCase(); return; }
  if (h === "charters") { S.view = "charters"; S.open = null; return; }
  if (h === "agents") { S.view = "agents"; S.open = null; return; }
  if (h.indexOf("agents/") === 0) { S.view = "agent"; S.open = h.slice(7); return; }
  if (h === "catalogue" || h.indexOf("catalogue/") === 0) {
    S.view = "catalogue";
    const who = h.split("/")[1];
    if (who && agentById(who)) S.agent = who;
    S.open = null;
    return;
  }
  if (h === "analytics") { S.view = "analytics"; S.open = null; return; }
  if (h === "approvals") { S.view = "ledger"; S.filterStatus = "awaiting"; S.open = null; return; }
  if (h === "requests") { S.view = "requests"; S.open = null; return; }
  S.view = "ledger"; S.open = null;
}
function route(hash, replace) {
  if (replace) history.replaceState(null, "", hash);
  else if (hash !== location.hash) location.hash = hash;
  parseHash();
  paint({ view: true });
  if (S.paletteOpen) focusPalette();
}

function paint(opts) {
  const o = opts || {};
  animateNext = !!o.animate;
  const switching = !!o.view && lastView !== null && lastView !== S.view;
  const root = document.getElementById("root");
  let inner;
  if (S.view === "record") {
    const r = DATA.requests.find((x) => x.id === S.open);
    inner = r ? recordView(r) : '<div class="sheet-inner"><div class="empty"><p>That request is not on this month’s ledger.</p></div></div>';
  } else if (S.view === "charters") {
    inner = chartersView();
  } else if (S.view === "requests") {
    inner = requestsView();
  } else if (S.view === "agents") {
    inner = agentsView();
  } else if (S.view === "agent") {
    inner = agentView(S.open);
  } else if (S.view === "catalogue") {
    inner = catalogueView();
  } else if (S.view === "analytics") {
    inner = analyticsView();
  } else {
    inner = ledgerView();
  }
  root.innerHTML = shell(inner);
  const scrim = document.getElementById("scrim");
  const pal = document.getElementById("palette");
  if (S.paletteOpen) {
    scrim.classList.add("on");
    pal.classList.add("on");
    pal.innerHTML = paletteHtml();
    document.body.classList.add("palmode");
  } else {
    scrim.classList.remove("on");
    pal.classList.remove("on");
    document.body.classList.remove("palmode");
  }
  const TITLES = { record: S.open, charters: "Charters", requests: "Requests", agents: "Agents", agent: "Agent", catalogue: "Find a product", analytics: "Analytics" };
  document.title = (TITLES[S.view] || "Ledger") + " · Veto";
  lastView = S.view;

  if (!booted) {
    document.body.classList.add("is-booting");
    setTimeout(function () { document.body.classList.remove("is-booting"); }, 240);
    booted = true;
  }
  if (switching) {
    const si = document.querySelector(".sheet-inner");
    if (si) {
      si.classList.add("is-entering");
      setTimeout(function () { si.classList.remove("is-entering"); }, 220);
    }
  }
  if (o.stamp) {
    const c = document.querySelector(".consent");
    if (c) c.classList.add("is-stamping");
  }
  tweenNumbers(!!o.animate);
  if (o.animate) {
    document.querySelectorAll(".seg[data-w]").forEach(function (seg) {
      requestAnimationFrame(function () { seg.style.width = seg.getAttribute("data-w"); });
    });
  }
  if (S.paletteOpen) focusPalette();
}

function focusPalette() {
  const input = document.getElementById("palette-input");
  if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
}

function closePalette() {
  if (!S.paletteOpen) return;
  S.paletteOpen = false;
  document.getElementById("scrim").classList.remove("on");
  const pal = document.getElementById("palette");
  pal.classList.remove("on");
  if (location.hash === "#palette") history.replaceState(null, "", "#/");
  paint();
}

function runItem(it) {
  if (!it) return;
  if (it.act === "approve") return approve(it.id);
  if (it.act === "veto") return veto(it.id);
  if (it.act === "export") return exportCsv();
  if (it.act === "clear-filters") { clearFilters(); return closePalette(); }
  if (it.act === "cat") { S.query = it.q || ""; S.paletteOpen = false; location.hash = "#/catalogue"; parseHash(); paint({ view: true }); return; }
  if (it.act === "vendor") { S.filterVendor = it.id; S.filterStatus = "all"; S.filterCharter = "all"; return closePalette(); }
  if (it.nav) { S.paletteOpen = false; location.hash = it.nav; parseHash(); paint({ view: true }); }
}

function toggleRule(agentId, rule) {
  const a = agentById(agentId);
  if (!a) return;
  const i = a.rules.indexOf(rule);
  if (i === -1) a.rules.push(rule); else a.rules.splice(i, 1);
  paint();
  toast((RULE_LABEL[rule] || rule) + (i === -1 ? " assigned to " : " taken off ") + a.name);
}

function clearFilters() {
  S.filterStatus = "all";
  S.filterCharter = "all";
  S.filterVendor = "all";
  S.minAmount = "";
  parseHash();
  paint();
}

/* ---- events ------------------------------------------------------------ */
document.addEventListener("click", function (e) {
  const t = e.target.closest("[data-open], [data-act], [data-nav], [data-open-palette], [data-pi], [data-agent-pick], [data-rule]");

  if (e.target.closest(".scrim")) { closePalette(); return; }
  if (!t) return;

  if (t.hasAttribute("data-agent-pick")) {
    S.agent = t.getAttribute("data-agent-pick");
    S.query = "";
    history.replaceState(null, "", "#/catalogue/" + S.agent);
    paint();
    return;
  }
  if (t.hasAttribute("data-rule")) {
    toggleRule(t.getAttribute("data-agent"), t.getAttribute("data-rule"));
    return;
  }

  if (t.hasAttribute("data-open-palette")) {
    e.preventDefault();
    S.paletteQuery = "";
    S.paletteIndex = 0;
    S.paletteOpen = true;
    history.replaceState(null, "", "#palette");
    paint();
    focusPalette();
    return;
  }
  if (t.hasAttribute("data-pi")) {
    runItem(paletteMatches()[Number(t.getAttribute("data-pi"))]);
    return;
  }
  if (t.hasAttribute("data-open") && !t.hasAttribute("data-act")) {
    const href = t.getAttribute("href");
    if (href) return; /* the anchor's own hash navigation handles it */
    e.preventDefault();
    route("#" + t.getAttribute("data-open"));
    return;
  }
  if (t.hasAttribute("data-nav")) {
    e.preventDefault();
    route("#/" + (t.getAttribute("data-nav") === "ledger" ? "" : t.getAttribute("data-nav")));
    return;
  }
  const act = t.getAttribute("data-act");
  if (!act) return;
  e.preventDefault();
  if (act === "approve" || act === "veto") {
    const line = DATA.requests.find((x) => x.id === t.getAttribute("data-id"));
    if (!line) return;
    if (S.view === "record") { S.open = line.id; }
    act === "approve" ? approve(t.getAttribute("data-id")) : veto(t.getAttribute("data-id"));
    return;
  }
  if (act === "clear-filters") return clearFilters();
  if (act === "export") return exportCsv();
  if (act === "copy") {
    const h = t.getAttribute("data-hash");
    if (navigator.clipboard) navigator.clipboard.writeText("sha256:" + h).catch(function () {});
    toast("Consent hash copied");
    return;
  }
  if (act === "copy-cmd") {
    const cmd = t.getAttribute("data-cmd");
    if (navigator.clipboard) navigator.clipboard.writeText(cmd).catch(function () {});
    toast("Copied " + cmd);
    return;
  }
  if (act === "cat") {
    S.query = t.getAttribute("data-q") || "";
    S.paletteOpen = false;
    location.hash = "#/catalogue";
    parseHash();
    paint();
    return;
  }
  if (act === "amend") {
    toast("Caps are changed in Charters by Finance. This request keeps the rule it was judged on.");
    return;
  }
});

document.addEventListener("change", function (e) {
  const el = e.target.closest("[data-filter]");
  if (!el) return;
  const f = el.getAttribute("data-filter");
  if (f === "status") S.filterStatus = el.value;
  if (f === "charter") S.filterCharter = el.value;
  if (f === "vendor") S.filterVendor = el.value;
  paint();
});

document.addEventListener("input", function (e) {
  const el = e.target.closest("[data-filter='amount']");
  if (el) {
    S.minAmount = el.value;
    const scroll = document.querySelector(".tbody") ? document.querySelector(".tbody").scrollTop : 0;
    paint();
    const back = document.getElementById("f-amount");
    if (back) { back.focus(); back.setSelectionRange(back.value.length, back.value.length); }
    const tb = document.querySelector(".tbody");
    if (tb) tb.scrollTop = scroll;
    return;
  }
  if (e.target.id === "cat-search") {
    S.query = e.target.value;
    paint();
    const back = document.getElementById("cat-search");
    if (back) { back.focus(); back.setSelectionRange(back.value.length, back.value.length); }
    return;
  }
  if (e.target.id === "palette-input") {
    S.paletteQuery = e.target.value;
    S.paletteIndex = 0;
    renderPaletteList();
    focusPalette();
  }
});

document.addEventListener("keydown", function (e) {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (S.paletteOpen) return closePalette();
    S.paletteQuery = "";
    S.paletteIndex = 0;
    S.paletteOpen = true;
    history.replaceState(null, "", "#palette");
    paint();
    focusPalette();
    return;
  }
  if (!S.paletteOpen) return;
  if (e.key === "Escape") { e.preventDefault(); closePalette(); return; }
  const rows = paletteMatches();
  if (e.key === "ArrowDown") { e.preventDefault(); S.paletteIndex = Math.min(rows.length - 1, S.paletteIndex + 1); syncPaletteSelection(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); S.paletteIndex = Math.max(0, S.paletteIndex - 1); syncPaletteSelection(); }
  else if (e.key === "Enter") { e.preventDefault(); runItem(rows[S.paletteIndex]); }
});

function syncPaletteSelection() {
  const items = document.querySelectorAll(".palette-item");
  items.forEach(function (el, i) {
    el.setAttribute("aria-selected", i === S.paletteIndex ? "true" : "false");
    if (i === S.paletteIndex) {
      el.scrollIntoView({ block: "nearest" });
    }
  });
  const input = document.getElementById("palette-input");
  if (input) input.focus();
}

window.addEventListener("hashchange", function () { parseHash(); paint({ view: true }); if (S.paletteOpen) focusPalette(); });

/* ---- boot -------------------------------------------------------------- */
parseHash();
paint();
if (S.paletteOpen) {
  focusPalette();
  requestAnimationFrame(focusPalette);
  window.addEventListener("load", function () { if (S.paletteOpen) focusPalette(); }, { once: true });
}
