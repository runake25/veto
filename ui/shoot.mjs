#!/usr/bin/env node
/* Drive the built index.html in a real Chrome over the DevTools protocol.
   Used to (a) prove the console actually works and (b) capture shots/ from the live page.

   node ui/shoot.mjs            # run the interaction checks, then write shots/*.png
   node ui/shoot.mjs --check    # interaction checks only, exit 1 on failure

   No dependencies: Node 22+ ships fetch and WebSocket. */

import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9411;
const ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const PAGE = pathToFileURL(resolve(ROOT, "index.html")).href;
const W = 1440, H = 900, DPR = 2;
const capArg = process.argv.find((a) => a.startsWith("--capture="));
const hashArg = (process.argv.find((a) => a.startsWith("--hash=")) || "--hash=#/").slice(7);
const sizeArg = process.argv.find((a) => a.startsWith("--size="));
const CHECK_ONLY = process.argv.includes("--check");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForPort() {
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) return r.json();
    } catch { /* not up yet */ }
    await sleep(100);
  }
  throw new Error("Chrome did not open a debugging port");
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  async eval(expression) {
    const r = await this.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ": " + (r.exceptionDetails.exception?.description || ""));
    return r.result.value;
  }
}

const results = [];
function check(name, pass, detail = "") {
  results.push({ name, pass, detail });
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
}

const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
  "--no-default-browser-check", "--disable-extensions", "--force-color-profile=srgb",
  `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/veto-shoot-profile",
  "--allow-file-access-from-files", "about:blank"
], { stdio: "ignore" });

let cdp;
try {
  await waitForPort();
  const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json();
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.addEventListener("open", res); ws.addEventListener("error", rej); });
  cdp = new Cdp(ws);

  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: DPR, mobile: false });
  await cdp.send("Emulation.setFocusEmulationEnabled", { enabled: true });

  const goto = async (hash) => {
    await cdp.send("Page.navigate", { url: "about:blank" });
    await sleep(90);
    await cdp.send("Page.navigate", { url: PAGE + hash });
    await sleep(420);
  };

  /* ---------- interaction checks ---------- */
  await goto("#/");
  check("ledger renders its h1", (await cdp.eval("document.querySelector('h1')?.textContent")) === "Ledger");
  check("four KPI cells", (await cdp.eval("document.querySelectorAll('.kpis .cell').length")) === 4);
  check("three requests await consent", (await cdp.eval("document.querySelectorAll('.qrow').length")) === 3);
  check("table lists every request", (await cdp.eval("document.querySelectorAll('.trow').length")) === 14);

  // the screen's totals reconcile with its own rows
  const sums = await cdp.eval(`(() => {
    const money = (t) => parseFloat(t.replace(/[$,]/g, ""));
    const rows = [...document.querySelectorAll('.trow')];
    const by = (label) => rows.filter((r) => r.querySelector('.c-status').textContent.trim() === label);
    const settled = by("Captured").concat(by("Auto-approved"));
    const all = rows.reduce((a, r) => a + money(r.querySelector('.c-amount').textContent), 0);
    const cells = [...document.querySelectorAll('.kpis .cell-value')];
    const kpi = [money(cells[1].firstChild.textContent), money(cells[0].firstChild.textContent), money(cells[2].querySelector('.unit').textContent)];
    return {
      settled: settled.reduce((a, r) => a + money(r.querySelector('.c-amount').textContent), 0),
      all, kpi
    };
  })()`);
  check("the three KPI figures add up to the table total",
    Math.abs(sums.kpi[0] + sums.kpi[1] + sums.kpi[2] - sums.all) < 0.005,
    `${sums.kpi[0]} + ${sums.kpi[1]} + ${sums.kpi[2]} = ${sums.all.toFixed(2)}`);
  check("settled rows add up to the spent figure", Math.abs(sums.settled - sums.kpi[0]) < 0.005, `$${sums.settled.toFixed(2)}`);

  // filters narrow the table and the count in scope
  await cdp.eval(`(() => { const s = document.querySelector('[data-filter="status"]'); s.value = 'awaiting'; s.dispatchEvent(new Event('change', {bubbles:true})); })()`);
  await sleep(150);
  check("status filter narrows the table", (await cdp.eval("document.querySelectorAll('.trow').length")) === 3, "3 awaiting");
  check("toolbar count follows the filter", (await cdp.eval("document.querySelector('.toolbar .count').textContent")).startsWith("3 of 14"));
  await cdp.eval(`(() => { const i = document.getElementById('f-amount'); i.value = '1000'; i.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  await sleep(150);
  check("empty state when no row matches", (await cdp.eval("!!document.querySelector('.empty')")), "amount ≥ $1,000");
  await goto("#/");

  // motion: controls answer the press, a view change has a visible arrival
  const motion = await cdp.eval(`(() => {
    const btn = document.querySelector('.btn');
    const t = getComputedStyle(btn).transitionDuration;
    return { transitional: t !== '0s' && t !== '', t };
  })()`);
  check("controls carry a state transition", motion.transitional, motion.t);
  await cdp.eval(`document.querySelector('.trow').click()`);
  await sleep(70);
  check("a view change animates in", await cdp.eval("!!document.querySelector('.sheet-inner.is-entering')"));
  await goto("#/");

  // motion: the approval is a stamp — the row leaves, the figures move
  const leaving = await cdp.eval(`(() => {
    document.querySelector('.qrow [data-act="approve"]').click();
    const r = document.querySelector('.qrow');
    return r ? r.classList.contains('is-leaving') : false;
  })()`);
  check("the approved row leaves before the list closes", leaving);
  await sleep(600);
  const moved = await cdp.eval(`({ open: document.querySelector('[data-num="open"]').getAttribute('data-v'),
    spent: document.querySelector('[data-num="spent"]').getAttribute('data-v') })`);
  check("the figures move to their new value", moved.open === "178.5" && moved.spent === "998.7", "held " + moved.open + ", spent " + moved.spent);

  // motion honours the system setting
  await cdp.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await goto("#/");
  const reduced = await cdp.eval(`getComputedStyle(document.querySelector('.btn')).transitionDuration`);
  check("reduced motion turns the animation off", parseFloat(reduced) <= 0.02, reduced);
  await cdp.send("Emulation.setEmulatedMedia", { features: [] });
  await goto("#/");

  const before = await cdp.eval("document.querySelectorAll('.qrow').length");
  await cdp.eval(`document.querySelector('.qrow [data-act="approve"]').click()`);
  await sleep(500);
  const after = await cdp.eval("document.querySelectorAll('.qrow').length");
  check("approve moves the request out of the queue", after === before - 1, `${before} → ${after}`);
  check("approve shows a receipt toast", /consent/i.test(await cdp.eval("document.getElementById('toast').textContent")));

  // palette is operable
  await goto("#palette");
  check("palette opens on its route", await cdp.eval("document.getElementById('palette').classList.contains('on')"));
  check("search field holds focus", (await cdp.eval("document.activeElement?.id")) === "palette-input");
  const allItems = await cdp.eval("document.querySelectorAll('.palette-item').length");
  await cdp.eval(`(() => { const i = document.getElementById('palette-input'); i.value = 'northwind'; i.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  await sleep(160);
  const typed = await cdp.eval("document.querySelectorAll('.palette-item').length");
  check("typing filters the palette", typed > 0 && typed < allItems, `${allItems} → ${typed} on “northwind”`);
  await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
  await sleep(300);
  check("Enter runs the selected row", /^#?REQ-\d+$/i.test(await cdp.eval("location.hash")), await cdp.eval("location.hash"));

  await goto("#palette");
  await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await sleep(250);
  check("Escape closes the palette", !(await cdp.eval("document.getElementById('palette').classList.contains('on')")));

  // hash routing opens a record directly
  await goto("#req-1042");
  check("hash opens a record", /Coffee capsules/.test(await cdp.eval("document.querySelector('h1').textContent")));
  const rail = await cdp.eval(`(() => {
    const steps = [...document.querySelectorAll('.rail-step')];
    return { n: steps.length, last: steps.at(-1).querySelector('.rail-what').textContent, state: steps.at(-1).className };
  })()`);
  check("the rail runs from the slash command to the capture", rail.n === 8 && rail.last === "Captured", `${rail.n} steps, ends at “${rail.last}”`);
  check("the rail starts with the Slack slash command",
    (await cdp.eval("document.querySelector('.rail-step .rail-what').textContent")) === "Slash /veto");
  check("the record names the agent that raised it",
    (await cdp.eval(`[...document.querySelectorAll('.kv dt')].some((d) => d.textContent === "Agent") && [...document.querySelectorAll('.kv dt')].some((d) => d.textContent === "Tags")`)));
  check("the capture step is still open", /is-todo/.test(rail.state));
  check("the cart's total is the sum of its lines", (await cdp.eval(`(() => {
    const n = [...document.querySelectorAll('.line .total')].map(e => +e.textContent.replace(/[$,]/g,''));
    const shown = +document.querySelector('.lines-total b').textContent.replace(/[$,]/g,'');
    return Math.abs(n.reduce((a,b)=>a+b,0) - shown) < 0.005;
  })()`)));

  // the agent model: agents own rules and tags, and the catalogue search obeys the rules
  await goto("#/agents");
  check("agents view lists every agent", (await cdp.eval("document.querySelectorAll('.arow').length")) === 6, "5 agents + header");
  check("every agent names its channel and rules",
    (await cdp.eval(`[...document.querySelectorAll('.arow:not(.arow-head)')].every((r) => r.textContent.indexOf("of 7") !== -1)`)));
  await goto("#/agents/desk");
  const seen = await cdp.eval(`({ rules: document.querySelectorAll('.rule-row').length,
    on: document.querySelectorAll('.switch[aria-checked="true"]').length,
    fits: document.querySelector('.fits-n').textContent })`);
  check("an agent page shows all seven rules and its hold on them", seen.rules === 7 && seen.on === 3, JSON.stringify(seen));
  await cdp.eval(`document.querySelector('[data-rule="per_unit_cap"]').click()`);
  await sleep(200);
  const afterToggle = await cdp.eval(`({ on: document.querySelectorAll('.switch[aria-checked="true"]').length,
    fits: document.querySelector('.fits-n').textContent })`);
  check("switching a rule on changes what the agent may find", afterToggle.on === seen.on + 1 && afterToggle.fits !== seen.fits, seen.fits + " → " + afterToggle.fits);

  await goto("#/catalogue");
  check("catalogue lists the whole catalogue", (await cdp.eval("document.querySelectorAll('.cat-row').length")) === 14);
  await cdp.eval(`(() => { const i = document.getElementById('cat-search'); i.value = 'keyboard'; i.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  await sleep(250);
  check("searching the catalogue narrows it", (await cdp.eval("document.querySelectorAll('.cat-row').length")) === 1);
  const verdicts = await cdp.eval(`(() => { document.getElementById('cat-search').value = ''; document.getElementById('cat-search').dispatchEvent(new Event('input', {bubbles:true})); return 1; })()`);
  await sleep(200);
  check("items the agent cannot buy are marked, not hidden",
    (await cdp.eval("document.querySelectorAll('.pill-veto').length")) > 0 && (await cdp.eval("document.querySelectorAll('.cat-row').length")) === 14);
  await cdp.eval(`document.querySelector('[data-agent-pick="lunch"]').click()`);
  await sleep(200);
  check("choosing another agent re-decides every item", (await cdp.eval("document.querySelector('.agent-chip.is-on').textContent")) === "Client lunch");

  await goto("#/analytics");
  check("analytics groups by tag and by agent",
    (await cdp.eval("document.querySelectorAll('.barrow').length")) >= 5 && (await cdp.eval("document.querySelectorAll('.arow-mini').length")) === 5);
  check("the analytics bands name the rules that stopped requests",
    (await cdp.eval("document.querySelectorAll('.stopped > div').length")) === 3);

  // every destination in the sidebar is a real view
  await goto("#/charters");
  check("charters view lists all four budget lines", (await cdp.eval("document.querySelectorAll('.charter-card').length")) === 4);
  const t0 = await cdp.eval(`(() => {
    const t = document.querySelector('.charter-card .track');
    if (!t) return null;
    const r = t.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height), segs: t.querySelectorAll('.seg').length,
      spentW: Math.round((t.querySelector('.seg-spent') || { getBoundingClientRect: () => ({ width: 0 }) }).getBoundingClientRect().width) };
  })()`);
  check("the charter track draws its segments", !!t0 && t0.w > 100 && t0.h >= 6 && t0.segs >= 1 && t0.spentW > 4, JSON.stringify(t0));
  await goto("#/requests");
  check("requests view lists every request", (await cdp.eval("document.querySelectorAll('.trow').length")) === 14);
  await goto("#/approvals");
  check("approvals view presets the status filter", (await cdp.eval("document.querySelector('[data-filter=\"status\"]').value")) === "awaiting");

  // keyboard: Cmd/Ctrl+K from the ledger
  await goto("#/");
  await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "k", code: "KeyK", modifiers: 4, windowsVirtualKeyCode: 75 });
  await sleep(250);
  check("⌘K opens the palette from anywhere", await cdp.eval("document.getElementById('palette').classList.contains('on')"));

  // phone: the layout must not scroll sideways
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: false });
  await goto("#/");
  const over = await cdp.eval(`(() => {
    const w = document.documentElement.clientWidth;
    return [...document.querySelectorAll('body *')]
      .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.right > w + 1 && getComputedStyle(el).position !== 'fixed'; })
      .slice(0, 6).map((el) => el.tagName.toLowerCase() + '.' + String(el.className || '').replace(/\s+/g, '.') + ' right=' + Math.round(el.getBoundingClientRect().right));
  })()`);
  const diag = await cdp.eval(`(() => {
    const t = document.querySelector('.topbar'), b = document.querySelector('.brand');
    const cs = getComputedStyle(t);
    return { cw: document.documentElement.clientWidth, inner: window.innerWidth,
      topbar: Math.round(t.getBoundingClientRect().width), cols: cs.gridTemplateColumns,
      brand: Math.round(b.getBoundingClientRect().width), search: getComputedStyle(document.querySelector('.searchbar')).display,
      app: Math.round(document.querySelector('.app').getBoundingClientRect().width) };
  })()`);
  check("phone layout does not overflow sideways", over.length === 0, JSON.stringify(diag) + " " + over.join(" | "));
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: DPR, mobile: false });

  const motionArg = process.argv.find((a) => a.startsWith("--motion="));
  if (motionArg) {
    const dir = motionArg.slice(9);
    const { mkdirSync: mk } = await import("node:fs");
    mk(dir, { recursive: true });
    await goto(hashArg || "#/");
    await cdp.eval(`(document.querySelector('.qrow [data-act="approve"]') || document.querySelector('.record-head [data-act="approve"]')).click()`);
    const delays = [40, 120, 220, 420];
    let waited = 0;
    for (let i = 0; i < delays.length; i++) {
      await sleep(delays[i] - waited);
      waited = delays[i];
      const { data } = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      const f = dir + "/approve-" + String(delays[i]).padStart(3, "0") + "ms.png";
      writeFileSync(f, Buffer.from(data, "base64"));
      console.log("frame " + f);
    }
  }

  if (capArg) {
    const [cw, ch] = (sizeArg ? sizeArg.slice(7) : "1440x900").split("x").map(Number);
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: cw, height: ch, deviceScaleFactor: 2, mobile: false });
    await goto(hashArg);
    const { data } = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    writeFileSync(capArg.slice(10), Buffer.from(data, "base64"));
    console.log("captured " + capArg.slice(10));
  }

  const failed = results.filter((r) => !r.pass);
  console.log(`\n${results.length - failed.length}/${results.length} interaction checks passed`);

  /* ---------- screenshots, from the live page ---------- */
  if (!CHECK_ONLY && !failed.length) {
    mkdirSync(resolve(ROOT, "shots"), { recursive: true });
    const shot = async (hash, file, prep) => {
      await goto(hash);
      if (prep) { await cdp.eval(prep); await sleep(500); }
      const { data } = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      writeFileSync(resolve(ROOT, file), Buffer.from(data, "base64"));
      console.log("wrote " + file);
    };
    await shot("#/", "shots/desktop.png");
    await shot("#req-1042", "shots/state-1.png");
    await shot("#palette", "shots/state-2.png", "document.getElementById('palette-input').focus()");
  }

  if (failed.length) process.exitCode = 1;
} finally {
  try { cdp?.ws.close(); } catch { /* already gone */ }
  chrome.kill("SIGKILL");
}
