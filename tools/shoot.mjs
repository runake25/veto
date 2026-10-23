#!/usr/bin/env node
/* Screenshot + interaction driver for the React app, over the DevTools protocol.

   No dependencies: Node 22+ ships fetch and WebSocket. It talks to the runner
   directly (or through the Vite proxy), mints a demo session, plants its cookie,
   then loads the app at the requested hash route and writes a PNG.

   node tools/shoot.mjs --path /agents/agt_a83d4faf --out shots/agent.png
   node tools/shoot.mjs --path / --out shots/overview.png --full
   node tools/shoot.mjs --path /agents/agt_a83d4faf --out shots/dry.png \
     --eval "document.querySelector('.dry-form .btn').click()" --wait ".dry-result"

   --base   app origin (default http://localhost:5180, the Vite dev server)
   --api    runner origin used only to mint the session (default = --base)
   --wait   selector to wait for after load / after --eval
   --eval   JS expression run once the page is ready (awaited)
   --settle extra milliseconds before the shot (default 220)
   --hover  selector to move the pointer over before the shot (CSS :hover)
   --full   capture the whole scroll height instead of the viewport
   --w --h  viewport (default 1440 x 900)
   --scale  device pixel ratio (default 2)
   --check  print failures instead of writing a screenshot
*/

import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9412;

const arg = (name, fallback) => {
  const eq = process.argv.find((a) => a.startsWith(`--${name}=`));
  if (eq) return eq.slice(name.length + 3);
  const at = process.argv.indexOf(`--${name}`);
  if (at !== -1 && process.argv[at + 1] && !process.argv[at + 1].startsWith("--")) {
    return process.argv[at + 1];
  }
  return fallback;
};
const has = (name) => process.argv.includes(`--${name}`);

const BASE = arg("base", "http://localhost:5180").replace(/\/+$/, "");
const API = (arg("api", BASE) || BASE).replace(/\/+$/, "");
const PATH = arg("path", "/");
const OUT = resolve(arg("out", "shots/shot.png"));
const WAIT = arg("wait", "");
const EVAL = arg("eval", "");
const SETTLE = Number(arg("settle", "600"));
const FULL = has("full");
const W = Number(arg("w", "1440"));
const H = Number(arg("h", "900"));
const SCALE = Number(arg("scale", "2"));
const CHECK = has("check");
const ECHO = arg("echo", "");
const HOVER = arg("hover", "");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForPort() {
  for (let i = 0; i < 120; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) return r.json();
    } catch {
      /* not up yet */
    }
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
    const r = await this.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (r.exceptionDetails) {
      throw new Error(
        r.exceptionDetails.text + ": " + (r.exceptionDetails.exception?.description || "")
      );
    }
    return r.result.value;
  }
}

async function demoCookie() {
  const res = await fetch(`${API}/api/v1/demo-session`, { method: "POST" });
  if (!res.ok) throw new Error(`demo-session failed: ${res.status}`);
  const raw = res.headers.getSetCookie?.() ?? [];
  const pair = raw.map((c) => c.split(";")[0]).find((c) => c.includes("="));
  if (!pair) throw new Error("demo-session did not set a cookie");
  const [name, ...rest] = pair.split("=");
  return { name, value: rest.join("=") };
}

async function waitFor(cdp, selector, timeout = 40000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await cdp.eval(`!!document.querySelector(${JSON.stringify(selector)})`)) return true;
    await sleep(80);
  }
  throw new Error(`timed out waiting for ${selector}`);
}

const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    "--disable-gpu",
    "--hide-scrollbars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--force-color-profile=srgb",
    `--remote-debugging-port=${PORT}`,
    "--user-data-dir=/tmp/veto-shoot-profile",
    "about:blank",
  ],
  { stdio: "ignore" }
);

let failure = null;
try {
  await waitForPort();
  const tab = await (
    await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })
  ).json();
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener("open", res);
    ws.addEventListener("error", rej);
  });
  const cdp = new Cdp(ws);

  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Network.enable");
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width: W,
    height: H,
    deviceScaleFactor: SCALE,
    mobile: false,
  });
  await cdp.send("Emulation.setFocusEmulationEnabled", { enabled: true });

  const cookie = await demoCookie();
  const host = new URL(BASE).hostname;
  await cdp.send("Network.setCookie", {
    name: cookie.name,
    value: cookie.value,
    domain: host,
    path: "/",
    httpOnly: true,
    sameSite: "Lax",
  });

  const url = `${BASE}/app/#${PATH.startsWith("/") ? PATH : "/" + PATH}`;
  await cdp.send("Page.navigate", { url });

  // the app boots off /api/v1/me, then renders; the skeleton has no h1, so that
  // is the first proof the real screen (and its handlers) are in place
  await waitFor(cdp, "h1");
  await cdp.eval("document.fonts ? document.fonts.ready.then(() => true) : true");
  if (WAIT && !EVAL) await waitFor(cdp, WAIT);
  if (EVAL) {
    await cdp.eval(EVAL);
    if (WAIT) await waitFor(cdp, WAIT);
  }
  if (HOVER) {
    await waitFor(cdp, HOVER);
    const point = await cdp.eval(
      `(() => { const el = document.querySelector(${JSON.stringify(HOVER)}); if (!el) return null; const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`
    );
    if (!point) throw new Error(`hover target not found: ${HOVER}`);
    await cdp.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: point.x, y: point.y });
    await sleep(240);
  }

  await sleep(SETTLE);

  if (ECHO) {
    console.log(JSON.stringify(await cdp.eval(ECHO), null, 2));
  } else if (CHECK) {
    const title = await cdp.eval("document.querySelector('h1')?.textContent ?? ''");
    console.log(`OK ${PATH}  h1=${JSON.stringify(title)}`);
  } else {
    let clip;
    if (FULL) {
      const metrics = await cdp.send("Page.getLayoutMetrics");
      const size = metrics.cssContentSize ?? metrics.contentSize;
      clip = { x: 0, y: 0, width: W, height: Math.ceil(size.height), scale: 1 };
    } else {
      clip = { x: 0, y: 0, width: W, height: H, scale: 1 };
    }
    const shot = await cdp.send("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: true,
      clip,
    });
    mkdirSync(dirname(OUT), { recursive: true });
    writeFileSync(OUT, Buffer.from(shot.data, "base64"));
    console.log(`wrote ${OUT} (${W}x${FULL ? "full" : H} @${SCALE}x)`);
  }
} catch (err) {
  failure = err;
  console.error(`FAIL ${err.message}`);
} finally {
  chrome.kill("SIGTERM");
}

process.exit(failure ? 1 : 0);
