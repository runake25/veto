#!/usr/bin/env node
/* Record the product tour: drive the built console in a real Chrome, capture the
   screen as it moves, and write demo/veto-demo.mp4.

   node ui/record.mjs                 # record + encode
   node ui/record.mjs --keep-frames   # also leave /tmp/veto-frames behind

   Silent by design; demo/VIDEO-SCRIPT.md carries the words to say over it.
   Uses the same CDP approach as ui/shoot.mjs — no dependencies. */

import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9422;
const ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const PAGE = pathToFileURL(resolve(ROOT, "index.html")).href;
const W = 1440, H = 900;
const KEEP = process.argv.includes("--keep-frames");
const FRAMES = KEEP ? "/tmp/veto-frames" : mkdtempSync(resolve(tmpdir(), "veto-frames-"));
const OUT = resolve(ROOT, "demo/veto-demo.mp4");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForPort() {
  for (let i = 0; i < 120; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) return;
    } catch { /* not up */ }
    await sleep(100);
  }
  throw new Error("Chrome did not open a debugging port");
}

class Cdp {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.onFrame = null;
    ws.addEventListener("message", (ev) => {
      const m = JSON.parse(ev.data);
      if (m.method === "Page.screencastFrame") { this.onFrame?.(m.params); return; }
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id);
        this.pending.delete(m.id);
        m.error ? reject(new Error(m.error.message)) : resolve(m.result);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((res, rej) => this.pending.set(id, { resolve: res, reject: rej }));
  }
  async eval(expression) {
    const r = await this.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r.result.value;
  }
}

const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
  "--no-default-browser-check", "--force-color-profile=srgb", "--mute-audio",
  `--remote-debugging-port=${PORT}`, "--user-data-dir=/tmp/veto-record-profile",
  "--window-size=" + W + "," + H, "about:blank",
], { stdio: "ignore" });

mkdirSync(FRAMES, { recursive: true });
const frames = [];
let cdp;

try {
  await waitForPort();
  const tab = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json();
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.addEventListener("open", res); ws.addEventListener("error", rej); });
  cdp = new Cdp(ws);
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await cdp.send("Emulation.setFocusEmulationEnabled", { enabled: true });

  let n = 0;
  cdp.onFrame = async ({ data, sessionId, metadata }) => {
    const name = `f${String(n++).padStart(5, "0")}.jpg`;
    writeFileSync(resolve(FRAMES, name), Buffer.from(data, "base64"));
    frames.push({ name, ts: metadata.timestamp });
    try { await cdp.send("Page.screencastFrameAck", { sessionId }); } catch { /* closed */ }
  };

  const goto = async (hash) => {
    await cdp.send("Page.navigate", { url: PAGE + hash });
    await sleep(700);
  };

  /* a caption bar in the product's own type, so the video explains itself */
  const caption = async (text, sub) => {
    await cdp.eval(`(() => {
      let el = document.getElementById('rec-cap');
      if (!el) {
        el = document.createElement('div');
        el.id = 'rec-cap';
        el.style.cssText = 'position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:9999;' +
          'background:#17191b;color:#fff;border-radius:8px;padding:11px 16px 12px;max-width:760px;' +
          'font-family:"IBM Plex Sans",system-ui,sans-serif;box-shadow:0 10px 30px rgba(23,25,27,.28);' +
          'opacity:0;transition:opacity .22s ease;pointer-events:none;text-align:center';
        document.body.appendChild(el);
      }
      el.innerHTML = '<div style="font-size:15px;font-weight:600;letter-spacing:-.01em">${text.replace(/`/g, "")}</div>' +
        (${JSON.stringify(sub || "")} ? '<div style="font-size:12px;color:#a9adb2;margin-top:3px">' + ${JSON.stringify(sub || "")} + '</div>' : '');
      el.style.opacity = '1';
    })()`);
    await sleep(280);
  };

  await goto("#/");
  await cdp.eval(`document.querySelector('[data-num="open"]').closest('.sheet').scrollTop = 0`);
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 82, maxWidth: W, maxHeight: H, everyNthFrame: 1 });

  // 1 · the ledger
  await caption("Veto — a consent layer for agent spending", "Every purchase an agent proposes waits for the charter, and for a human when it should.");
  await sleep(2600);

  // 2 · approve one: the stamp
  await caption("Approving is a signature, not a click", "The row leaves, the consent lands, the money moves from in-flight to spent.");
  await cdp.eval(`document.querySelector('.qrow [data-act="approve"]').click()`);
  await sleep(2600);

  // 3 · the record
  await goto("#req-1042");
  await caption("Every request carries its reason", "The cart, the rule that fired, the agent that raised it, and the chain from Slack to capture.");
  await sleep(3200);

  // 4 · sign it, watch the rail finish
  await caption("Signing issues a single-use consent", "Bound to the cart hash. The rail runs order → authorise → capture behind it.");
  await cdp.eval(`document.querySelector('.record-head [data-act="approve"]').click()`);
  await sleep(3000);

  // 5 · agents hold the rules
  await goto("#/agents/desk");
  await caption("Each agent holds its own rules", "Seven exist. An agent applies only the ones it was given — turn one on and the consequence is immediate.");
  await sleep(2800);
  await cdp.eval(`document.querySelector('[data-rule="per_unit_cap"]').click()`);
  await sleep(2400);

  // 6 · the search is the rule set
  await goto("#/catalogue/desk");
  await caption("So the catalogue search is the rule set", "13 of 14 items pass — switch the unit cap on and it drops to 4. Nothing is hidden; the rule is named.");
  await sleep(3400);

  // 7 · analytics by tag
  await goto("#/analytics");
  await caption("Every request carries its tag into analytics", "Money that moved and money that was refused, kept apart, by tag and by agent.");
  await sleep(3000);

  // 8 · the palette
  await goto("#/");
  await caption("⌘K, because approvers work at a keyboard", "Type, arrow, enter. The palette runs the same actions as the page.");
  await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "k", code: "KeyK", modifiers: 4, windowsVirtualKeyCode: 75 });
  await sleep(400);
  await cdp.eval(`(() => { const i = document.getElementById('palette-input'); i.value = 'request'; i.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  await sleep(2400);
  await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
  await sleep(600);

  await cdp.send("Page.stopScreencast");
  await sleep(200);

  /* ---- encode: frame durations from the real timestamps ---- */
  const list = [];
  for (let i = 0; i < frames.length; i++) {
    const next = frames[i + 1] ? frames[i + 1].ts : frames[i].ts + 0.08;
    const dur = Math.max(0.02, Math.min(6.0, next - frames[i].ts));
    list.push(`file '${resolve(FRAMES, frames[i].name)}'`, `duration ${dur.toFixed(3)}`);
  }
  list.push(`file '${resolve(FRAMES, frames[frames.length - 1].name)}'`);
  const listPath = resolve(FRAMES, "list.txt");
  writeFileSync(listPath, list.join("\n") + "\n");

  const ff = spawnSync("ffmpeg", [
    "-y", "-hide_banner", "-loglevel", "error",
    "-f", "concat", "-safe", "0", "-i", listPath,
    "-vf", "scale=1440:900:flags=lanczos,format=yuv420p",
    "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
    "-movflags", "+faststart", OUT,
  ], { encoding: "utf8" });
  if (ff.status !== 0) throw new Error("ffmpeg failed: " + (ff.stderr || "").slice(-400));
  const secs = frames.length ? (frames[frames.length - 1].ts - frames[0].ts).toFixed(1) : "0";
  console.log(`recorded ${frames.length} frames over ${secs}s → ${OUT}`);
} finally {
  try { cdp?.ws.close(); } catch { /* gone */ }
  chrome.kill("SIGKILL");
  if (!KEEP) rmSync(FRAMES, { recursive: true, force: true });
}
