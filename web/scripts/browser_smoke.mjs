#!/usr/bin/env node
/* Browser smoke test for the Athena console: the check that fails when the page is dead.

   Why it exists: the Django tests read the JavaScript as text, so one `const` used before its declaration made
   every button dead while 414 tests stayed green (docs/blog/2026-10-tests-green-console-dead.md). This script
   loads the real pages in a real browser and fails on:

     - any uncaught script error (`pageerror`)
     - any console error, and any Content Security Policy violation
     - any failed or 4xx/5xx request
     - a console that rendered but did not start (project count never marked ready, no project list, empty panels)
     - any visible control without an accessible name, a missing banner/main landmark, an unnamed form
     - a form field under 16px (iOS Safari zooms the page on focus), or a page that scrolls sideways, at 390, 820, 1024 and 1440 px wide
     - a dialog that lets Tab / Shift+Tab leave it, does not close on Esc, or does not give focus back
     - creating a project without the address (#project=...) following it (XB-1)

   Pages: sign-in, console (project list), a seeded project (every panel), the New project dialog, the picture
   (annotation) dialog, the remove-picture confirmation, the approve-a-model dialog, the check-screen builder and the
   operator screen. Nothing is deleted or approved; the data is a disposable seed (seed_browser_smoke.py).

   Needs: Node 18+, the `playwright` package and a browser (`npx playwright install chromium`). Playwright is a
   development tool only; it is not a dependency of the product. Run it through run_browser_smoke.sh, which builds
   the disposable database and server. Direct use:

     ATHENA_SMOKE_PASSWORD=... node scripts/browser_smoke.mjs --base http://127.0.0.1:8000 \
        --project-id <uuid> --slug smoke-check [--browser chromium|webkit] [--device "iPhone 13"] [--report out.json]

   Where Playwright is found: $PLAYWRIGHT_NODE_MODULES, then scripts/.smoke/node_modules, then ./node_modules.
   Exit code 0 = every check passed, 1 = at least one failure, 2 = could not run. The password is read from the
   environment and never printed. */
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

function loadPlaywright() {
  const roots = [process.env.PLAYWRIGHT_NODE_MODULES, path.join(HERE, ".smoke", "node_modules"), path.join(process.cwd(), "node_modules")].filter(Boolean);
  for (const root of roots) {
    if (!existsSync(root)) continue;
    try { return createRequire(path.resolve(root) + path.sep)("playwright"); } catch { /* try the next place */ }
  }
  console.error("Cannot find the `playwright` package. Install it outside the product, for example:\n"
    + "  npm install --prefix web/scripts/.smoke playwright && npx --prefix web/scripts/.smoke playwright install chromium\n"
    + "or point PLAYWRIGHT_NODE_MODULES at an existing node_modules folder.");
  process.exit(2);
}

function args() {
  const out = { browser: "chromium", device: "", report: "" };
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i].replace(/^--/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    out[key] = argv[i + 1];
  }
  if (!out.base || !out.projectId || !out.slug || !process.env.ATHENA_SMOKE_PASSWORD) {
    console.error("usage: ATHENA_SMOKE_PASSWORD=... node browser_smoke.mjs --base URL --project-id UUID --slug SLUG [--user smoke] [--browser chromium|webkit] [--device NAME] [--report FILE]");
    process.exit(2);
  }
  out.user = out.user || "smoke";
  return out;
}

const opts = args();
const pw = loadPlaywright();
const BASE = opts.base.replace(/\/$/, "");
let createdProject = null; // the project made in the "create a project" step: the second project the switching checks need
const failures = [];
const notes = [];
const checks = [];
let where = "start";
const fail = (message) => { failures.push({ where, message }); console.log(`  FAIL [${where}] ${message}`); };
const pass = (message) => { checks.push({ where, message }); };
const expect = (condition, okMessage, failMessage) => (condition ? pass(okMessage) : fail(failMessage || okMessage));
/* A stubbed answer (page.route) that is still waiting for the real server when its step ends reaches Playwright after the
   route has gone: "Route is already handled". That rejection belongs to no step and would end the whole run with a stack
   trace; it is counted as a note instead. Any other unhandled rejection still ends the run. */
process.on("unhandledRejection", (error) => {
  if (/Route is already handled|Target page, context or browser has been closed/.test(String(error && error.message))) { notes.push("a stubbed answer arrived after its step had ended (ignored)"); return; }
  console.error(error);
  process.exit(1);
});
const hard = setTimeout(() => { console.error("HARD TIMEOUT (420 s): the smoke test hung"); process.exit(2); }, 420000);

/* WebKit logs one CSP "error" per native <select> because it applies an inline style to it (XB-3, engine noise
   verified with a bare page). It is counted and reported, never ignored silently, and only in WebKit. */
const isWebKitSelectNoise = (text) => opts.browser === "webkit"
  && /style-src|Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline'/i.test(text);

const browserType = pw[opts.browser];
if (!browserType) { console.error(`Unknown browser ${opts.browser}`); process.exit(2); }
const browser = await browserType.launch();
let contextOptions = { viewport: { width: 1280, height: 900 } };
if (opts.device) {
  if (!pw.devices[opts.device]) { console.error(`Unknown device ${opts.device}`); process.exit(2); }
  contextOptions = { ...pw.devices[opts.device] };
}
const context = await browser.newContext(contextOptions);
context.setDefaultTimeout(15000);
await context.addInitScript(() => {
  window.__csp = [];
  document.addEventListener("securitypolicyviolation", (event) => {
    window.__csp.push(`${event.violatedDirective} blocked ${event.blockedURI || "inline"} at ${(event.sourceFile || "").split("/").pop()}:${event.lineNumber}`);
  });
});
const page = await context.newPage();
let webkitNoise = 0;
let allowedStatuses = [];
page.on("pageerror", (error) => fail(`uncaught script error: ${String(error.message).slice(0, 300)}`));
page.on("console", (message) => {
  const text = message.text();
  if (message.type() === "error" || (message.type() === "warning" && /Content Security Policy|Refused to/i.test(text))) {
    if (isWebKitSelectNoise(text)) { webkitNoise += 1; return; }
    // A deliberate 4xx/5xx (a step that lists the status in allowedStatuses) is logged by the browser as a console error too.
    const logged = /Failed to load resource: the server responded with a status of (\d+)/.exec(text);
    if (logged && allowedStatuses.includes(Number(logged[1]))) { notes.push(`expected status ${logged[1]} logged by the browser`); return; }
    fail(`console ${message.type()}: ${text.slice(0, 300)}`);
  }
});
page.on("requestfailed", (request) => {
  const reason = request.failure()?.errorText || "";
  if (/ERR_ABORTED|cancelled|NS_BINDING_ABORTED/i.test(reason)) { notes.push(`request cancelled (page moved on): ${request.url().replace(BASE, "")}`); return; }
  fail(`request failed: ${request.method()} ${request.url().replace(BASE, "")} (${reason})`);
});
page.on("response", (response) => {
  const status = response.status();
  if (status >= 400 && !allowedStatuses.includes(status)) fail(`HTTP ${status} for ${response.request().method()} ${response.url().replace(BASE, "")}`);
});


/* Let in-flight requests finish before leaving a page. Safari logs a console error ("Fetch API cannot load ... due to
   access control checks") for a request cancelled by navigation, which is not a defect of the page. */
const settle = () => page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
async function go(url) { await settle(); await page.goto(url); }
async function reloadPage() { await settle(); await page.reload(); }

// ---- checks that run inside the page -----------------------------------------------------------------
const SKIP_TYPES = ["checkbox", "radio", "file", "range", "hidden", "submit", "button", "color", "image", "reset"];

async function audit(label) {
  const result = await page.evaluate((skipTypes) => {
    const text = (node) => {
      const clone = node.cloneNode(true);
      clone.querySelectorAll("[aria-hidden=true], script, style").forEach((item) => item.remove());
      return (clone.textContent || "").replace(/\s+/g, " ").trim();
    };
    const nameOf = (el) => {
      const by = (el.getAttribute("aria-labelledby") || "").split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map(text).join(" ").trim();
      if (by) return by;
      if ((el.getAttribute("aria-label") || "").trim()) return el.getAttribute("aria-label").trim();
      if (el.labels && el.labels.length) { const t = [...el.labels].map(text).join(" ").trim(); if (t) return t; }
      if (el.tagName === "INPUT" && ["submit", "button", "reset"].includes(el.type) && el.value) return el.value;
      if (["BUTTON", "A", "SUMMARY"].includes(el.tagName) || el.getAttribute("role") === "button") { const t = text(el); if (t) return t; }
      const alt = el.querySelector("img[alt]"); if (alt && alt.alt.trim()) return alt.alt.trim();
      return (el.getAttribute("title") || "").trim();
    };
    const visible = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden" && !el.closest("[hidden], [inert]");
    const unnamed = [];
    document.querySelectorAll("button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button], canvas[tabindex]").forEach((el) => {
      if (!visible(el) || el.closest("[aria-hidden=true]")) return;
      if (!nameOf(el)) unnamed.push(`${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}${el.className && typeof el.className === "string" ? "." + el.className.split(" ")[0] : ""}`);
    });
    const unnamedForms = [...document.forms].filter((form) => !form.getAttribute("aria-label") && !form.getAttribute("aria-labelledby")).map((form) => form.id || form.action);
    const banners = [...document.querySelectorAll("header, [role=banner]")].filter((el) => !el.closest("main, aside, nav, section, article, [role=main]"));
    const mains = document.querySelectorAll("main, [role=main]").length;
    const small = [];
    document.querySelectorAll("input, select, textarea").forEach((el) => {
      if (el.tagName === "INPUT" && skipTypes.includes(el.type)) return;
      const size = parseFloat(getComputedStyle(el).fontSize);
      if (size < 16) small.push(`${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}=${size}px`);
    });
    const ids = {};
    document.querySelectorAll("[id]").forEach((el) => { ids[el.id] = (ids[el.id] || 0) + 1; });
    const duplicateIds = Object.keys(ids).filter((id) => ids[id] > 1);
    return { unnamed, unnamedForms, banners: banners.length, mains, small, duplicateIds, csp: window.__csp || [] };
  }, SKIP_TYPES);
  where = label;
  expect(result.unnamed.length === 0, "every visible control has an accessible name", `unnamed controls: ${result.unnamed.slice(0, 8).join(", ")}`);
  expect(result.unnamedForms.length === 0, "every form has a name", `unnamed forms: ${result.unnamedForms.join(", ")}`);
  expect(result.banners === 1, "exactly one top-level banner landmark", `expected 1 top-level banner landmark, found ${result.banners}`);
  expect(result.mains === 1, "exactly one main landmark", `expected 1 main landmark, found ${result.mains}`);
  expect(result.small.length === 0, "no form field under 16px", `form fields under 16px at this width: ${result.small.slice(0, 8).join(", ")}`);
  expect(result.duplicateIds.length === 0, "no duplicate ids", `duplicate ids: ${result.duplicateIds.join(", ")}`);
  for (const violation of result.csp) {
    if (isWebKitSelectNoise(violation) || (opts.browser === "webkit" && /style-src-attr/.test(violation))) { webkitNoise += 1; continue; }
    fail(`Content Security Policy violation: ${violation}`);
  }
  if (!result.csp.length) pass("no Content Security Policy violation");
}

/* Field sizes at the widths that matter (phone, iPad portrait, iPad landscape, desktop). Computed font size is
   read for every field in the document, including ones inside closed dialogs. */
async function fieldSizesAtWidths(label) {
  const original = page.viewportSize();
  for (const width of [390, 820, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const small = await page.evaluate((skipTypes) => [...document.querySelectorAll("input, select, textarea")]
      .filter((el) => !(el.tagName === "INPUT" && skipTypes.includes(el.type)))
      .map((el) => [`${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}`, parseFloat(getComputedStyle(el).fontSize)])
      .filter(([, size]) => size < 16).map(([name, size]) => `${name}=${size}px`), SKIP_TYPES);
    where = `${label} @${width}px`;
    expect(small.length === 0, "all form fields are at least 16px", `fields under 16px: ${small.slice(0, 8).join(", ")}`);
    const wide = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
    expect(wide.scroll <= wide.inner + 1, "no sideways scrolling", `the page scrolls sideways: content is ${wide.scroll}px wide in a ${wide.inner}px window`);
  }
  if (original) await page.setViewportSize(original);
}

/* Tab and Shift+Tab must stay inside an open modal dialog, wrap round, and Escape must close it and give focus back. */
async function dialogChecks(label, dialogSelector, openerSelector, { closeWith = "Escape" } = {}) {
  where = label;
  const state = () => page.evaluate((selector) => {
    const dialog = document.querySelector(selector);
    const active = document.activeElement;
    return {
      open: Boolean(dialog && dialog.open),
      modal: Boolean(dialog && dialog.matches(":modal")),
      inside: Boolean(dialog && active && dialog.contains(active)),
      count: window.Athena ? window.Athena.tabbableIn(dialog).length : 0,
      who: active ? `${active.tagName.toLowerCase()}${active.id ? "#" + active.id : ""}` : "none"
    };
  }, dialogSelector);
  const first = await state();
  expect(first.open && first.modal, "dialog opens as a modal", `dialog ${dialogSelector} did not open as a modal (open=${first.open}, modal=${first.modal})`);
  expect(first.inside, "focus starts inside the dialog", `focus is outside the dialog at the start (on ${first.who})`);
  expect(first.count >= 1, `dialog has ${first.count} tabbable controls`, "dialog has no tabbable control");
  const presses = Math.max(6, first.count * 2 + 3);
  for (const direction of ["Tab", "Shift+Tab"]) {
    const seen = new Set();
    let escaped = "";
    for (let i = 0; i < presses; i += 1) {
      await page.keyboard.press(direction);
      const now = await state();
      seen.add(now.who);
      if (!now.inside && !escaped) escaped = `${direction} press ${i + 1} moved focus to ${now.who}`;
    }
    expect(!escaped, `${direction} never leaves the dialog (${presses} presses, ${seen.size} different controls)`, `focus left the dialog: ${escaped}`);
    expect(seen.size >= Math.min(first.count, 2), `${direction} reaches more than one control`, `${direction} reached only ${[...seen].join(", ")}`);
  }
  await audit(`${label} (open)`);
  where = label;
  if (closeWith === "Escape") await page.keyboard.press("Escape");
  else await page.click(closeWith);
  await page.waitForFunction((selector) => !document.querySelector(selector).open, dialogSelector);
  // The page may rebuild the opener while the dialog closes (the list refreshes), so give focus a moment to settle.
  const opener = await page.waitForFunction((selector) => document.activeElement === document.querySelector(selector), openerSelector, { timeout: 4000 })
    .then(() => true).catch(() => false);
  const back = await page.evaluate(() => {
    const active = document.activeElement;
    return active && active !== document.body ? `${active.tagName.toLowerCase()}${active.id ? "#" + active.id : ""}.${(active.className || "").toString().split(" ")[0]}` : "body";
  });
  expect(opener, "closing gives focus back to what opened the dialog", `focus did not return to ${openerSelector} (it is on ${back})`);
}

/* A list the page rebuilds (for example after a dialog closes) is briefly empty: read its length only once it has
   stopped changing. */
async function stableCount(selector) {
  let last = -1;
  for (let i = 0; i < 40; i += 1) {
    const now = await page.locator(selector).count();
    if (now > 0 && now === last) return now;
    last = now;
    await page.waitForTimeout(250);
  }
  return last;
}

// The project page shows one part at a time (Get started, Labels, Pictures, ...). Open a part the way a person does.
async function openPart(panelId) {
  await page.locator(`#jump-nav a[data-panel="${panelId}"]`).click();
  await page.waitForFunction((id) => document.getElementById(id)?.classList.contains("is-current"), panelId, { timeout: 10000 });
}

// A debugging aid: ATHENA_SMOKE_ONLY=<regex> runs only the steps whose name matches (signing in always runs).
const onlySteps = process.env.ATHENA_SMOKE_ONLY ? new RegExp(process.env.ATHENA_SMOKE_ONLY) : null;
async function step(name, fn) {
  if (onlySteps && !onlySteps.test(name) && !/^(sign|console starts)/.test(name)) return;
  where = name;
  console.log(`- ${name}`);
  try { await fn(); } catch (error) {
    const line = (String(error.stack).split("\n").find((text) => text.includes("browser_smoke.mjs")) || "").match(/:(\d+):\d+\)?$/);
    fail(`step crashed: ${String(error.message).split("\n")[0].slice(0, 300)}${line ? ` (browser_smoke.mjs line ${line[1]})` : ""}`);
    // A debugging aid: ATHENA_SMOKE_SHOT_DIR=<folder> keeps a picture of the page at the moment a step crashed.
    if (process.env.ATHENA_SMOKE_SHOT_DIR) await page.screenshot({ path: path.join(process.env.ATHENA_SMOKE_SHOT_DIR, `${name.replace(/\W+/g, "-").slice(0, 60)}.png`) }).catch(() => {});
  }
}

// ---- the run -------------------------------------------------------------------------------------------
await step("sign-in page", async () => {
  await go(`${BASE}/login/`);
  await page.waitForSelector("#id_username");
  expect(await page.locator("a.skip-link").count() === 1, "sign-in page has a skip link", "sign-in page has no skip link");
  await audit("sign-in page");
  await fieldSizesAtWidths("sign-in page");
});

await step("sign in", async () => {
  await page.fill("#id_username", opts.user);
  await page.fill("#id_password", process.env.ATHENA_SMOKE_PASSWORD);
  await Promise.all([page.waitForURL((url) => !url.pathname.startsWith("/login")), page.click("form.signin-form button[type=submit]")]);
  expect(!page.url().includes("/login"), "signed in", "still on the sign-in page after signing in");
});

await step("console starts (fresh load)", async () => {
  await go(`${BASE}/`);
  // A dead console shows the server's HTML with the project count never marked ready (data-ready stays "false").
  await page.waitForFunction(() => document.getElementById("project-count")?.dataset.ready === "true", null, { timeout: 15000 })
    .then(() => pass("console script started: the project list loaded and the project count is marked ready"))
    .catch(() => fail("the console did not start: the project count (#project-count) never became ready (a script probably failed on load)"));
  const rows = await page.locator(".desktop-table tbody tr, .mobile-list > *").count();
  expect(rows >= 1, `project list rendered (${rows} rows)`, "no project rows rendered");
  await audit("console (project list)");
  await fieldSizesAtWidths("console");
});

await step("New project dialog", async () => {
  await page.click("#open-project-dialog");
  await page.waitForSelector("#project-dialog[open]");
  await dialogChecks("New project dialog", "#project-dialog", "#open-project-dialog");
});

await step("create a project; the address follows it (XB-1)", async () => {
  const name = `Smoke created ${Date.now().toString(36)} (synthetic)`;
  await page.click("#open-project-dialog");
  await page.waitForSelector("#project-dialog[open]");
  await page.fill("#project-name", name);
  await page.fill("#project-task", "Check the address after creating");
  await page.fill("#project-labels", "one, two");
  const [created] = await Promise.all([page.waitForResponse((r) => r.url().endsWith("/api/projects") && r.request().method() === "POST"), page.click("#create-project-form button[type=submit]")]);
  const newId = (await created.json()).project.id;
  createdProject = { id: newId, name };
  await page.waitForFunction((n) => document.getElementById("workspace-title")?.textContent.includes(n), name);
  const hash = await page.evaluate(() => location.hash);
  expect(hash === `#project=${newId}`, `the address names the new project (#project=${newId.slice(0, 8)}...)`, `after creating a project the address is "${hash}", not #project=${newId}`);
  await reloadPage();
  await page.waitForFunction((n) => document.getElementById("workspace-title")?.textContent.includes(n), name)
    .then(() => pass("reload reopens the project that was just created"))
    .catch(async () => fail(`reload opened "${await page.textContent("#workspace-title")}" instead of the new project`));
});

await step("seeded project: every panel", async () => {
  await go(`${BASE}/#project=${opts.projectId}`);
  await reloadPage(); // the address alone must reopen it, on a fresh load
  await page.waitForFunction(() => document.getElementById("workspace-title")?.textContent.includes("Smoke test bags"));
  pass("the address reopens the seeded project on a fresh load");
  // One part at a time: only "Get started" is on screen when a project opens.
  const parts = await page.evaluate(() => [...document.querySelectorAll(".workspace > .panel.is-current")].filter((el) => el.offsetParent !== null).map((el) => el.id));
  expect(parts.length === 1 && parts[0] === "checklist-panel", "a project opens on \"Get started\" alone, not on one long page", `expected only the checklist part on screen, found: ${parts.join(", ") || "none"}`);
  await openPart("image-assets-panel");
  await page.waitForSelector(".asset-annotate");
  for (const id of ["#dataset-panel", "#train-panel", "#image-assets-panel", "#tr-model-list"]) {
    const exists = await page.locator(id).count();
    expect(exists === 1, `${id} is on the page`, `${id} is missing`);
  }
  await page.waitForFunction(() => !document.getElementById("train-panel").hidden, null, { timeout: 15000 })
    .then(() => pass("Teach panel is shown for a picture project"))
    .catch(() => fail("Teach panel stayed hidden for a picture project"));
  await page.waitForFunction(() => document.querySelectorAll("#tr-model-list > li").length >= 2, null, { timeout: 15000 })
    .then(() => pass("both seeded models are listed"))
    .catch(() => fail("the seeded models were not listed in the Teach panel"));
  for (const part of ["checklist-panel", "labels-panel", "image-assets-panel", "dataset-panel", "train-panel", "apps-panel"]) {
    if (!(await page.locator(`#jump-nav a[data-panel="${part}"]:not([hidden])`).count())) continue;
    await openPart(part);
    await audit(`seeded project page, part ${part}`);
    await fieldSizesAtWidths(`seeded project page, part ${part}`);
  }
});

await step("Teach part: one yellow button, one closed administrator fold (W2-U)", async () => {
  await openPart("train-panel");
  const info = await page.evaluate(() => {
    const panel = document.getElementById("train-panel");
    const visible = (el) => el.checkVisibility({ checkVisibilityCSS: true });
    const start = document.getElementById("tr-start");
    const admin = document.getElementById("tr-admin");
    const drawersBeforeStart = [...panel.querySelectorAll("details")].filter((d) => visible(d) && (d.compareDocumentPosition(start) & Node.DOCUMENT_POSITION_FOLLOWING)).length;
    return {
      primary: [...panel.querySelectorAll("button.primary")].filter(visible).map((b) => b.textContent.trim()),
      adminOpen: admin.open,
      startAboveFold: start.getBoundingClientRect().bottom <= admin.getBoundingClientRect().top,
      drawersBeforeStart,
      oldCopy: /Most people can leave this closed|Start teaching/.test(panel.innerText),
      pickersInFold: ["tr-snapshot", "tr-tool", "tr-rounds", "tr-batch"].every((id) => admin.contains(document.getElementById(id))),
      form: document.getElementById("tr-form").hasAttribute("data-needs")
    };
  });
  expect(info.primary.length === 1 && info.primary[0] === "Teach the computer", "the Teach part has exactly one yellow button, \"Teach the computer\"", `yellow buttons in the Teach part: ${JSON.stringify(info.primary)}`);
  expect(!info.adminOpen && info.startAboveFold, "the administrator fold is closed and sits under the Teach button", "the fold is open or sits above the Teach button");
  expect(info.drawersBeforeStart <= 1, `at most one closed drawer before the Teach button (${info.drawersBeforeStart})`, `${info.drawersBeforeStart} drawers before the Teach button`);
  expect(info.pickersInFold && !info.oldCopy, "snapshot picker, tool picker and settings are inside the fold; the old copy is gone", "settings are outside the fold or the old copy is still shown");
  // The start-from choice is in the fold; it exists only when the selected tool offers ready-made starts.
  const hidden = await page.evaluate(() => document.getElementById("tr-start-from").hidden);
  if (hidden) { notes.push("start-from control hidden: the selected teaching tool offers no ready-made starts here"); return; }
  await page.locator("#tr-admin > summary").click();
  const choice = await page.evaluate(() => {
    const radios = [...document.querySelectorAll("#tr-start-from-options input[type=radio]")];
    return {
      scratchChecked: document.getElementById("tr-pre-none").checked,
      disabled: radios.filter((r) => r.disabled).map((r) => r.value),
      ackVisible: !document.getElementById("tr-pre-licence").hidden
    };
  });
  expect(choice.scratchChecked && !choice.ackVisible, "from scratch is chosen and no licence box is shown by default", "wrong default start choice");
  await page.locator("#tr-admin > summary").click();
});

/* ---- The Teach part against STUBBED server answers -------------------------------------------------------------
   The stand-in training backend cannot start from a ready-made model, so these steps answer the readiness, health,
   run and results requests themselves (page.route) with the shapes the real server sends. They check what the page
   does with an answer, not what the server says. Every stubbed request is a GET or POST to this disposable server only. */
const STUB_RUN_ID = "00000000-0000-4000-8000-00000000beef";
const NOT_FINAL = "Not a final decision. This is the computer’s opinion. A person decides.";
function stubReadyBody(real) {
  return {
    ...real, worker: { online: true, loading: false }, active_run_id: null,
    start_from: { resolved: "coco", reason_if_none: "", how_to_enable: "", available: [{ value: "coco", title: "A general object model (COCO)", summary: "A ready-made model that already finds everyday objects.", licence_plain: "Ready-made models come with their own terms. Ask a lawyer before commercial use.", available: true, ready: true, needs_acknowledgement: true, unavailable_reason: "", how_to_enable: "", size_note: "Already on this computer." }] },
    derived: { train_pictures: 9, estimate_for_start: "coco", seconds_per_picture_epoch: 0.2, estimate_note: "This is an estimate, not a promise.", quick: { epochs: 40, batch_size: 4, estimated_minutes: 12 }, better: { epochs: 80, batch_size: 4, estimated_minutes: 25 }, based_on: "latest_snapshot" },
    one_click: { supported: true, ready: true, blockers: [], snapshot: { latest_version: 1, up_to_date: true, would_create_new: false } }
  };
}
function stubRunBody(quality = "quick") {
  return {
    run: { id: STUB_RUN_ID, project_id: "stub", dataset: { id: "d", version: 1, manifest_sha256: "x" }, backend: "stand-in", config: { pretrained: "coco", epochs: 40, batch_size: 4, quality, pretrained_requested: "auto" }, status: "queued", created_at: new Date().toISOString(), progress: 0, model_version_ids: [], cancel_requested: false, error: "", logs: "" },
    snapshot: { id: "d", version: 1, newly_created: false, image_count: 12, box_count: 12 }, worker_online: true
  };
}
async function openTeachFresh() {
  await go(`${BASE}/#project=${opts.projectId}`); // the address names whichever project was open last
  await reloadPage();
  await page.waitForFunction((title) => document.getElementById("workspace-title")?.textContent.includes(title), "Smoke test bags");
  await openPart("train-panel");
  await page.waitForFunction(() => !/Checking your pictures/.test(document.getElementById("tr-headline").textContent), null, { timeout: 15000 });
}
const teachState = () => page.evaluate(() => ({
  disabled: document.getElementById("tr-start").disabled, busy: document.getElementById("tr-start").getAttribute("aria-busy"),
  help: document.getElementById("tr-start-help").textContent, helpState: document.getElementById("tr-start-help").dataset.state,
  tick: document.getElementById("tr-ack").checked, tickShown: !document.getElementById("tr-ack-box").hidden, choices: !document.getElementById("tr-quality").hidden,
  error: document.getElementById("tr-error").textContent.trim(), title: document.getElementById("workspace-title").textContent
}));

await step("Teach part: tick the box, read the plan, press Start once, an error leaves the button usable (STUBBED answers)", async () => {
  const A = opts.projectId;
  const posts = [];
  const onRequest = (request) => { if (request.method() === "POST" && /\/training\/runs$/.test(request.url())) posts.push({ url: request.url().replace(BASE, ""), body: request.postData() }); };
  let postMode = "ok";
  let started = false; // once the stubbed start was accepted, the stubbed run is in the server's list (until the step removes it)
  page.on("request", onRequest);
  await page.route(`**/api/projects/${A}/training/readiness*`, async (route) => route.fulfill({ json: stubReadyBody(await (await route.fetch()).json()) }));
  await page.route(`**/api/projects/*/training/runs`, async (route) => {
    if (route.request().method() !== "POST") {
      if (!started) return route.continue();
      const real = await (await route.fetch()).json();
      return route.fulfill({ json: { ...real, runs: [stubRunBody("better").run, ...real.runs] } });
    }
    if (postMode === "fail") return route.fulfill({ status: 503, json: { error: "The teaching tool is busy right now." } });
    started = true;
    return route.fulfill({ status: 202, json: stubRunBody("better") });
  });
  allowedStatuses = [503, 404]; // deliberate: the stubbed refusal, and the stubbed run the real server has never heard of
  try {
    await openTeachFresh();
    await page.waitForFunction(() => !document.getElementById("tr-ack-box").hidden, null, { timeout: 10000 });
    const unticked = await teachState();
    expect(unticked.disabled && !unticked.tick && unticked.choices, "before the tick the button is off and Quick/Better are shown", `before the tick: ${JSON.stringify(unticked)}`);
    expect(/Tick the box above/.test(unticked.help) && unticked.helpState === "hint", "the reason before the tick is a calm hint, not a fault", `help before the tick: "${unticked.help}" (${unticked.helpState})`);
    await page.check("#tr-ack");
    const ticked = await teachState();
    expect(!ticked.disabled && ticked.helpState === "plan", "ticking the box switches the button on", `after the tick: ${JSON.stringify(ticked)}`);
    expect(/It will start from a ready-made model that already finds everyday objects/.test(ticked.help) && /about 12 minutes/.test(ticked.help), "the plan sentence names the start and the Quick time", `plan sentence: "${ticked.help}"`);
    await page.check("#tr-quality-better");
    const better = await teachState();
    expect(/about 25 minutes/.test(better.help), "choosing Better changes the time in the plan sentence", `plan sentence after Better: "${better.help}"`);
    expect(posts.length === 0, "nothing was sent before Start was pressed", `${posts.length} start request(s) before Start`);

    await page.click("#tr-start");
    await page.waitForFunction(() => !document.getElementById("tr-run").hidden, null, { timeout: 10000 });
    expect(posts.length === 1, "one press of Start sent exactly one request", `${posts.length} start requests for one press`);
    const sent = posts[0] ? JSON.parse(posts[0].body) : {};
    expect(posts[0]?.url === `/api/projects/${A}/training/runs`, "the request went to the project that is open", `the request went to ${posts[0]?.url}`);
    expect(sent.config?.quality === "better" && sent.acknowledge_pretrained_terms === true && sent.snapshot === "latest", "the request carries the choice, the tick and the latest saved copy", `request body: ${posts[0]?.body}`);
    // The server has never heard of the stubbed run, so the page's first look at it answers 404: it must say so and offer a way out.
    await page.waitForFunction(() => /Lost track of this run/.test(document.getElementById("tr-run-text").textContent), null, { timeout: 10000 });
    expect(await page.locator("#tr-run-again").isVisible(), "a run the server cannot find says \"Lost track of this run\" and offers Check again", "no Check again offer for a lost run");
    started = false; // the run is gone from the server's list now
    await page.click("#tr-run-again");
    await page.waitForFunction(() => document.getElementById("tr-run-again").hidden, null, { timeout: 10000 })
      .then(() => pass("Check again finds the real latest run and clears the lost-run message"))
      .catch(() => fail("the lost-run message stayed after Check again"));

    // An error answer: the message names it and the button is usable again, with the tick kept.
    postMode = "fail";
    started = false;
    await openTeachFresh();
    await page.waitForFunction(() => !document.getElementById("tr-ack-box").hidden, null, { timeout: 10000 });
    await page.check("#tr-ack");
    const count = posts.length;
    await page.click("#tr-start");
    await page.waitForFunction(() => /Teaching did not start/.test(document.getElementById("tr-error").textContent), null, { timeout: 10000 });
    const refused = await teachState();
    expect(posts.length === count + 1, "a refused start was one request", `${posts.length - count} requests for one refused start`);
    expect(!refused.disabled && refused.busy === "false" && refused.tick, "after an error the button is usable again and the tick is kept", `after the error: ${JSON.stringify(refused)}`);
    expect(/busy right now/.test(refused.error), "the error carries the server's plain sentence", `error text: "${refused.error}"`);
    await page.click("#tr-start");
    await page.waitForFunction(() => /Teaching did not start/.test(document.getElementById("tr-error").textContent));
    expect(posts.length === count + 2, "pressing Start again after an error sends a new request", `${posts.length - count} requests after two presses`);

    // "Check again" answers in words and keeps the keyboard focus on the button.
    await page.focus("#tr-recheck");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => /Checked just now/.test(document.getElementById("tr-recheck-note").textContent), null, { timeout: 10000 });
    const checked = await page.evaluate(() => ({ note: document.getElementById("tr-recheck-note").textContent, focus: document.activeElement?.id, label: document.getElementById("tr-recheck").textContent }));
    expect(/Checked just now: ready to teach|Checked just now: ready once you tick|Checked just now: still switched off/.test(checked.note), "Check again says what it found", `Check again note: "${checked.note}"`);
    expect(checked.focus === "tr-recheck" && checked.label === "Check again", "Check again keeps the keyboard focus and its name", `focus is on ${checked.focus}, label "${checked.label}"`);
  } finally {
    page.off("request", onRequest);
    await page.unroute(`**/api/projects/*/training/runs`);
    await page.unroute(`**/api/projects/${A}/training/readiness*`);
    allowedStatuses = [];
  }
});

await step("switching project: nothing is sent for the old project while the new one loads (STUBBED slow answer)", async () => {
  const A = opts.projectId;
  if (!createdProject) { notes.push("switch check skipped: no second project was created"); return; }
  const B = createdProject.id;
  const posts = [];
  const onRequest = (request) => { if (request.method() === "POST" && /\/training\/runs$/.test(request.url())) posts.push(request.url().replace(BASE, "")); };
  page.on("request", onRequest);
  await page.route(`**/api/projects/${A}/training/readiness*`, async (route) => route.fulfill({ json: stubReadyBody(await (await route.fetch()).json()) }));
  let releasePost; const postGate = new Promise((resolve) => { releasePost = resolve; });
  let holdPost = false;
  await page.route(`**/api/projects/${A}/training/runs`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    if (holdPost) await postGate;
    return route.fulfill({ status: 202, json: stubRunBody() });
  });
  const clickProject = async () => {
    // The list is a table on a wide screen and cards on a phone; both have an Open button named after the project.
    await page.locator(`button[aria-label="Open project: ${createdProject.name}"]:visible`).first().click();
    await page.waitForFunction((n) => document.getElementById("workspace-title")?.textContent.includes(n), createdProject.name);
  };
  try {
    // 1. the new project's pictures are slow to arrive
    await openTeachFresh();
    await page.waitForFunction(() => !document.getElementById("tr-ack-box").hidden, null, { timeout: 10000 });
    await page.check("#tr-ack");
    expect(!(await teachState()).disabled, "the old project can start (tick set)", "the old project's button was not usable to begin with");
    let releaseAssets; const gate = new Promise((resolve) => { releaseAssets = resolve; });
    await page.route(`**/api/projects/${B}/assets`, async (route) => { await gate; await route.continue(); });
    await clickProject();
    const during = await teachState();
    expect(during.title.includes(createdProject.name), "the page already shows the new project's name", `title is "${during.title}"`);
    expect(!during.tick && during.disabled && !during.choices, "while the new project loads the old tick is cleared and Start is off", `while loading: ${JSON.stringify(during)}`);
    await page.evaluate(() => { document.getElementById("tr-start").click(); document.getElementById("tr-form").requestSubmit(); if (typeof trStartOneClick === "function") return trStartOneClick(); });
    await page.waitForTimeout(400);
    expect(posts.length === 0, "pressing Start (and calling the start function) while the new project loads sends nothing", `${posts.length} start request(s): ${posts.join(", ")}`);
    releaseAssets();
    await page.waitForFunction(() => !/Checking your pictures/.test(document.getElementById("tr-headline").textContent), null, { timeout: 15000 });
    await page.unroute(`**/api/projects/${B}/assets`);

    // 2. a start for the old project is still in flight when the person opens another project
    holdPost = true;
    await openTeachFresh();
    await page.waitForFunction(() => !document.getElementById("tr-ack-box").hidden, null, { timeout: 10000 });
    await page.check("#tr-ack");
    await page.click("#tr-start");
    await page.waitForFunction(() => document.getElementById("tr-start").getAttribute("aria-busy") === "true");
    await clickProject();
    releasePost();
    await page.waitForTimeout(600);
    const later = await teachState();
    expect(later.busy !== "true" && later.help !== "Starting…" && !later.error, "a late answer for the old project does not leave the new one stuck or show its error", `after the late answer: ${JSON.stringify(later)}`);
    expect(posts.length === 1 && posts[0] === `/api/projects/${A}/training/runs`, "the one start went to the project it was pressed on", `start requests: ${posts.join(", ")}`);
  } finally {
    page.off("request", onRequest);
    releasePost();
    await page.unroute(`**/api/projects/${A}/training/runs`).catch(() => {});
    await page.unroute(`**/api/projects/${A}/training/readiness*`).catch(() => {});
    await page.unroute(`**/api/projects/${B}/assets`).catch(() => {});
  }
});

await step("Teach part: what needs a look is not folded away, and the headline is green only when nothing does (STUBBED health answers)", async () => {
  const A = opts.projectId;
  const none = "Nothing is boxed as 'rust' yet, so the computer cannot learn it. Draw boxes around 'rust' in your pictures.";
  const advice = ["Tip one is plain words.", "Tip two is plain words.", "Tip three is plain words.", none];
  let mode = "server";
  await page.route(`**/api/projects/${A}/datasets/health`, async (route) => {
    const real = await (await route.fetch()).json();
    const body = { ...real, ready_to_teach: true, blockers: [], advice: mode === "clean" ? [] : advice, labels: mode === "clean" ? [] : [{ label: "rust", accepted_boxes: 0, pictures: 0, verdict: "none", message: none }], near_duplicates: { groups_crossing_splits: 0, message: "" } };
    delete body.priority_notes; delete body.headline;
    if (mode === "server") { body.priority_notes = [none]; body.headline = { level: "ready_with_warnings", text: "Ready to teach, but read the warnings below first." }; }
    if (mode === "clean") body.headline = { level: "ready", text: "Your pictures look ready to teach from." };
    await route.fulfill({ json: body });
  });
  const read = () => page.evaluate((sentence) => {
    const items = [...document.querySelectorAll("#tr-facts > li")];
    const row = items.find((li) => li.textContent.includes(sentence.slice(0, 30)) && !li.closest("details"));
    return { state: document.getElementById("tr-headline").dataset.state, text: document.getElementById("tr-headline").textContent, unfolded: Boolean(row), fold: Boolean(document.querySelector("#tr-facts details")) };
  }, none);
  try {
    for (const next of ["server", "fallback", "clean"]) {
      mode = next;
      await openTeachFresh();
      const got = await read();
      if (next === "clean") {
        expect(got.state === "ready" && !got.fold, "with nothing to look at the headline is green", `clean answer: ${JSON.stringify(got)}`);
      } else {
        expect(got.state === "warn" && got.unfolded, `${next === "server" ? "with the server's own notes" : "with the notes worked out from the labels"}: amber headline and the empty label in plain view`, `${next}: ${JSON.stringify(got)}`);
        expect(got.fold, "the ordinary tips stay behind \"Show N more notes\"", `${next}: no fold for the other tips`);
      }
    }
  } finally {
    await page.unroute(`**/api/projects/${A}/datasets/health`).catch(() => {});
  }
});

await step("results card: one \"not a final decision\" line, a row per picture, the picture opens with the boxes marked (STUBBED answers)", async () => {
  const A = opts.projectId;
  const ids = await page.evaluate(async (id) => (await (await fetch(`/api/projects/${id}/assets`)).json()).assets.slice(0, 2).map((a) => a.id), A);
  const [a0, a1] = ids;
  const plain = {
    model_id: "stub", evaluated: true, reason_if_not: "", counts: { test_pictures: 5, test_boxes: 6 },
    labels: [{ label: "bag", test_boxes: 6, found: 3, missed: 3, wrong_boxes: 1, sentence: "Found 3 of 6 “bag” boxes; drew 1 box that was not “bag”." }],
    overall: { found: 3, of: 6, wrong_boxes: 1, verdict: "needs_more_examples", headline: `Needs more examples: it found 3 of 6 test boxes (50%) and drew 1 wrong box. Add pictures of what it missed, then teach it again. ${NOT_FINAL}`, reasons: [] },
    misses: { misses_available: true, reason: "", total: 4, distinct_pictures: 2, pictures: { total: 2, shown: 2 }, items: [
      { asset_id: a0, kind: "missed", label: "bag", score: null, box: [0.1, 0.1, 0.3, 0.3] },
      { asset_id: a0, kind: "missed", label: "bag", score: null, box: [0.55, 0.5, 0.2, 0.2] },
      { asset_id: a1, kind: "missed", label: "bag", score: null, box: [0.1, 0.1, 0.3, 0.3] },
      { asset_id: a0, kind: "false_alarm", label: "bag", score: 0.9, box: [0.6, 0.1, 0.2, 0.2] }
    ] },
    limits: []
  };
  let plainMode = "ok";
  await page.route("**/models/*/results/plain", async (route) => (plainMode === "ok" ? route.fulfill({ json: plain }) : route.fulfill({ status: 500, json: { error: "not today" } })));
  const open = async () => {
    await openTeachFresh();
    await page.locator('#tr-model-list button[aria-label^="Show results for"]').first().click();
    await page.waitForSelector("#tr-model-list .model-results:not([hidden]) .result-not-final", { timeout: 15000 });
  };
  try {
    await open();
    const card = await page.evaluate(() => {
      const results = document.querySelector("#tr-model-list .model-results:not([hidden])");
      const cardNode = results.closest(".model-card");
      return {
        notFinal: results.querySelectorAll(".result-not-final").length, headline: results.querySelector(".result-headline").textContent,
        rows: results.querySelectorAll(".miss-row").length, lines: [...results.querySelectorAll(".miss-row")].map((row) => row.querySelectorAll(".miss-items li").length),
        older: cardNode.querySelector(".model-summary").hidden, counts: [...results.querySelectorAll(".result-misses .asset-help")].map((n) => n.textContent).join(" | "), text: results.textContent
      };
    });
    expect(card.notFinal === 1 && !/Not a final decision/.test(card.headline), "the verdict carries the \"not a final decision\" line once, on its own line", `not-final lines: ${card.notFinal}; headline: "${card.headline}"`);
    expect(card.rows === 2 && card.lines.join() === "3,1", "the list has a row per picture (2 pictures, 4 boxes), not a row per box", `rows ${card.rows}, boxes per row ${card.lines.join()}`);
    expect(/Showing 2 of 2 pictures \(4 boxes in all\)/.test(card.counts), "it counts pictures and boxes separately", `counts: ${card.counts}`);
    expect(/did not match one you drew/.test(card.text), "a false alarm is described as a box that did not match one the person drew", "false-alarm wording missing");
    expect(card.older, "the older one-line summary is hidden while the plain verdict is shown", "the older summary line is still visible above the plain verdict");

    await page.locator("#tr-model-list .miss-row button").first().click();
    await page.waitForSelector("#annotation-dialog[open]");
    await page.waitForFunction(() => document.getElementById("annotation-status").textContent.includes("box") || document.getElementById("annotation-status").textContent.includes("No boxes"), null, { timeout: 10000 });
    const note = await page.evaluate(() => ({ text: document.getElementById("annotation-mark-note").textContent, shown: !document.getElementById("annotation-mark-note").hidden }));
    expect(note.shown && /Marked in white/.test(note.text) && /did not find/.test(note.text) && /did not match one you drew/.test(note.text), "the picture says what is marked and how to read the outlines", `mark note: ${JSON.stringify(note)}`);
    // The outline really is drawn: sample the canvas just outside the left edge of the first missed box.
    const drawn = await page.waitForFunction(() => {
      const canvas = document.getElementById("annotation-canvas");
      const bounds = canvas.getBoundingClientRect();
      if (!bounds.width) return false;
      const ratio = canvas.width / bounds.width;
      const pixel = canvas.getContext("2d").getImageData(Math.round((0.1 * bounds.width - 4) * ratio), Math.round(0.25 * bounds.height * ratio), 1, 1).data;
      return pixel[3] > 200 && pixel[0] > 235 && pixel[1] > 235 && pixel[2] > 220;
    }, null, { timeout: 8000 }).then(() => true).catch(() => false);
    expect(drawn, "a white outline is drawn on the picture where the computer missed a box", "no white outline at the missed box on the picture");
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.getElementById("annotation-dialog").open);
    // The window's "close" event arrives a moment after it stops being open.
    const cleared = await page.waitForFunction(() => document.getElementById("annotation-mark-note").hidden, null, { timeout: 4000 }).then(() => true).catch(() => false);
    expect(cleared, "closing the picture clears the marks", "the mark note stayed after the picture closed");

    // The plain answer cannot be had: the older figures are shown, and the line is still there.
    plainMode = "fail"; allowedStatuses = [500];
    await page.reload();
    await page.waitForFunction(() => document.getElementById("workspace-title")?.textContent.includes("Smoke test bags"));
    await openPart("train-panel");
    await page.waitForSelector('#tr-model-list button[aria-label^="Show results for"]');
    await page.locator('#tr-model-list button[aria-label^="Show results for"]').first().click();
    await page.waitForSelector("#tr-model-list .model-results:not([hidden]) .result-not-final", { timeout: 15000 });
    const fallback = await page.evaluate(() => {
      const results = document.querySelector("#tr-model-list .model-results:not([hidden])");
      return { notFinal: results.querySelectorAll(".result-not-final").length, older: results.closest(".model-card").querySelector(".model-summary").hidden, headline: results.querySelector(".result-headline")?.textContent || "" };
    });
    expect(fallback.notFinal === 1, "when the plain answer is missing the card still says \"not a final decision\"", `fallback card has ${fallback.notFinal} not-final lines`);
    expect(!fallback.older && /was checked on/.test(fallback.headline), "and the older summary comes back as the fallback", `fallback: ${JSON.stringify(fallback)}`);
  } finally {
    allowedStatuses = [];
    await page.unroute("**/models/*/results/plain").catch(() => {});
  }
});

/* ---- W3-G2: the no-test-pictures warning, the server's own reasons, the verdict in the approve dialog, and who sees the choices ---- */
const NO_HELD_OUT = "No pictures would be kept back to check the computer on, so you would get no results to read, and the model could not be moved to trial or approved.";
const readinessUrl = () => `**/api/projects/${opts.projectId}/training/readiness*`;
const healthUrl = () => `**/api/projects/${opts.projectId}/datasets/health`;

await step("Teach part: a project with no test pictures is warned in the plan sentence, and the first download is named (STUBBED answers)", async () => {
  let mode = "server"; // server | fallback | none | download
  await page.route(readinessUrl(), async (route) => {
    const body = stubReadyBody(await (await route.fetch()).json());
    if (mode === "server") body.one_click.warnings = [NO_HELD_OUT];
    if (mode === "download") {
      Object.assign(body.start_from.available[0], { ready: false, size_note: "About 74 MB. It is downloaded once, from download.pytorch.org, and then kept in Athena’s data folder." });
      body.one_click.warnings = [];
    }
    await route.fulfill({ json: body });
  });
  await page.route(healthUrl(), async (route) => {
    const real = await (await route.fetch()).json();
    await route.fulfill({ json: { ...real, ready_to_teach: true, blockers: [], held_out: { ...(real.held_out || {}), would_have_test_pictures: mode !== "fallback" } } });
  });
  const read = () => page.evaluate(() => {
    const help = document.getElementById("tr-start-help");
    const warn = [...help.querySelectorAll(".train-plan-warning")];
    return { disabled: document.getElementById("tr-start").disabled, text: help.textContent, warnings: warn.map((n) => n.textContent), last: help.lastElementChild?.classList.contains("train-plan-warning") || false, border: warn[0] ? getComputedStyle(warn[0]).borderLeftColor : "" };
  });
  try {
    for (const next of ["server", "fallback", "none", "download"]) {
      mode = next;
      await openTeachFresh();
      await page.waitForFunction(() => !document.getElementById("tr-ack-box").hidden, null, { timeout: 10000 });
      await page.check("#tr-ack");
      const got = await read();
      if (next === "server" || next === "fallback") {
        expect(!got.disabled, `${next === "server" ? "with the server's warning" : "worked out from the data check"}: the Teach button stays on (advice, not a stop)`, `${next}: button off: ${JSON.stringify(got)}`);
        expect(got.warnings.length === 1 && /No pictures would be kept back/.test(got.warnings[0]) && got.last, `${next}: one amber warning ends the plan sentence`, `${next}: ${JSON.stringify(got)}`);
        expect(/It will start from/.test(got.text) && got.text.indexOf("It will start from") < got.text.indexOf("No pictures would be kept back"), `${next}: the plan sentence comes first, the warning after it`, `${next}: ${got.text}`);
        expect(got.border === "rgb(245, 196, 0)", `${next}: the warning line is amber`, `${next}: border ${got.border}`);
        if (next === "server") await audit("Teach part with the no-test-pictures warning");
      } else {
        expect(got.warnings.length === 0, `${next}: no warning when nothing is wrong`, `${next}: ${JSON.stringify(got)}`);
      }
      if (next === "download") {
        expect(/The first time, it also downloads the ready-made model \(about 74 MB\); that download is not in the time estimate\./.test(got.text), "a model not on the computer yet: the plan names the first-time download", `plan: ${got.text}`);
      } else {
        expect(!/downloads/.test(got.text), `${next}: nothing is said about a download when the weights are already on this computer`, `plan: ${got.text}`);
      }
    }
  } finally {
    await page.unroute(readinessUrl()).catch(() => {});
    await page.unroute(healthUrl()).catch(() => {});
  }
});

function stubPlainBody(a0, over = {}) {
  return {
    model_id: "stub", evaluated: true, reason_if_not: "", counts: { test_pictures: 5, test_boxes: 6 },
    labels: [{ label: "bag", test_boxes: 6, found: 3, missed: 3, wrong_boxes: 0, sentence: "Found 3 of 6 “bag” boxes; drew no wrong boxes." }],
    overall: { found: 3, of: 6, wrong_boxes: 0, verdict: "needs_more_examples", headline: `Needs more examples: it found 3 of 6 test boxes (50%). ${NOT_FINAL}`, reasons: [] },
    misses: { misses_available: true, reason: "", total: 1, distinct_pictures: 1, pictures: { total: 1, shown: 1 }, no_longer_in_project: 0, items: [{ asset_id: a0, kind: "missed", label: "bag", score: null, box: [0.1, 0.1, 0.3, 0.3] }] },
    limits: [], ...over
  };
}
async function firstPictureId() {
  return page.evaluate(async (id) => (await (await fetch(`/api/projects/${id}/assets`)).json()).assets[0].id, opts.projectId);
}

await step("results card: the server's own reason for a missing list is shown as written; pictures taken out are counted (STUBBED answers)", async () => {
  const a0 = await firstPictureId();
  const REASON = "The saved list of wrong pictures could not be read, so it is not shown.";
  let mode = "reason";
  await page.route("**/models/*/results/plain", async (route) => {
    const body = stubPlainBody(a0);
    if (mode === "reason") body.misses = { misses_available: false, reason: REASON, items: [], total: 0, no_longer_in_project: 0, pictures: { total: 0, shown: 0, exact: true }, distinct_pictures: 0 };
    if (mode === "empty") body.misses = { ...body.misses, misses_available: false, reason: "", items: [] };
    if (mode === "gone") body.misses = { ...body.misses, no_longer_in_project: 3 };
    await route.fulfill({ json: body });
  });
  try {
    for (const next of ["reason", "empty", "gone"]) {
      mode = next;
      await openTeachFresh();
      await page.locator('#tr-model-list button[aria-label^="Show results for"]').first().click();
      await page.waitForSelector("#tr-model-list .model-results:not([hidden]) .result-not-final", { timeout: 15000 });
      const got = await page.evaluate(() => {
        const results = document.querySelector("#tr-model-list .model-results:not([hidden])");
        return { nomiss: results.querySelector(".result-nomiss")?.textContent || "", rows: results.querySelectorAll(".miss-row").length, text: results.textContent };
      });
      if (next === "reason") expect(got.nomiss === REASON, "when the list is missing the card says the server's reason, word for word", `reason line: "${got.nomiss}"`);
      if (next === "empty") expect(got.nomiss === "Per-picture details are not stored for this model.", "with no reason from the server the older sentence stays", `fallback line: "${got.nomiss}"`);
      if (next === "gone") expect(got.rows === 1 && /3 more boxes were measured on pictures that are no longer in the project\./.test(got.text), "boxes on pictures taken out of the project are counted under the list", `gone: rows ${got.rows}; ${got.text.slice(-200)}`);
    }
  } finally {
    await page.unroute("**/models/*/results/plain").catch(() => {});
  }
});

await step("approve dialog: the verdict comes first, a weak verdict needs the tick, and the server's 409 reveals it (STUBBED verdict and answers)", async () => {
  const a0 = await firstPictureId();
  let verdict = "not_ready";
  let approveMode = "refuse";
  const posts = [];
  const onRequest = (request) => { if (request.method() === "POST" && /\/approve$/.test(request.url())) posts.push(request.postData()); };
  page.on("request", onRequest);
  await page.route("**/models/*/results/plain", async (route) => {
    const body = stubPlainBody(a0);
    body.overall = { ...body.overall, verdict, headline: `${verdict === "not_ready" ? "Not ready: it found 1 of 6 test boxes (17%)." : "Needs more examples: it found 3 of 6 test boxes (50%)."} ${NOT_FINAL}` };
    if (verdict === "not_ready") body.overall.found = 1;
    await route.fulfill({ json: body });
  });
  await page.route("**/models/*/approve", async (route) => {
    const sent = JSON.parse(route.request().postData() || "{}");
    if (approveMode === "needs-tick" && sent.acknowledge_weak_results !== true) {
      return route.fulfill({ status: 409, json: { error: "This model cannot be approved yet: the results are too thin or too weak to rely on. Nothing was changed.", needs_acknowledgement: true } });
    }
    return route.fulfill({ status: 422, json: { error: "Stubbed: the smoke test stops here and approves nothing." } });
  });
  allowedStatuses = [409, 422];
  const openApprove = async () => {
    await openTeachFresh();
    const button = page.locator('button[aria-label^="Approve Model"]:not([disabled])').first();
    await button.scrollIntoViewIfNeeded();
    await button.click();
    await page.waitForSelector("#decision-dialog[open]");
  };
  const dialogState = () => page.evaluate(() => ({
    headline: document.querySelector("#decision-body .result-headline")?.textContent || "", notFinal: document.querySelectorAll("#decision-body .result-not-final").length,
    first: document.getElementById("decision-body").firstElementChild?.className || "", ackShown: !document.getElementById("decision-ack-row").hidden,
    ackText: document.getElementById("decision-ack-text").textContent, confirmDisabled: document.getElementById("decision-confirm").disabled,
    error: document.getElementById("decision-error").textContent
  }));
  try {
    await openApprove();
    const weak = await dialogState();
    expect(/^Not ready/.test(weak.headline) && weak.first === "result-headline" && weak.notFinal === 1 && !/Not a final decision/.test(weak.headline), "the dialog opens with the verdict headline and the one \"not a final decision\" line", `weak dialog: ${JSON.stringify(weak)}`);
    expect(weak.ackShown && /the results are too thin or too weak to rely on; I understand and still want to approve/.test(weak.ackText), "a Not ready verdict shows the tick-box", `weak dialog: ${JSON.stringify(weak)}`);
    await page.fill("#decision-note", "Smoke test note.");
    expect((await dialogState()).confirmDisabled, "with a note but no tick, Approve for use stays off", "confirm switched on without the tick");
    await page.check("#decision-ack");
    expect(!(await dialogState()).confirmDisabled, "ticking switches Approve for use on", "confirm stayed off after the tick");
    await audit("approve dialog with a verdict and the tick");
    approveMode = "refuse";
    await page.click("#decision-confirm");
    await page.waitForFunction(() => /smoke test stops here/.test(document.getElementById("decision-error").textContent), null, { timeout: 10000 });
    expect(posts.length === 1 && JSON.parse(posts[0]).acknowledge_weak_results === true, "the request carries acknowledge_weak_results: true", `posted: ${posts.join(" | ")}`);
    await page.click("#decision-cancel");
    await page.waitForFunction(() => !document.getElementById("decision-dialog").open);

    verdict = "needs_more_examples";
    approveMode = "needs-tick";
    posts.length = 0;
    await openApprove();
    const mild = await dialogState();
    expect(/^Needs more examples/.test(mild.headline) && !mild.ackShown, "a middling verdict shows the headline and no tick", `mild dialog: ${JSON.stringify(mild)}`);
    await page.fill("#decision-note", "Smoke test note.");
    await page.click("#decision-confirm");
    await page.waitForFunction(() => !document.getElementById("decision-ack-row").hidden, null, { timeout: 10000 });
    const revealed = await dialogState();
    expect(revealed.confirmDisabled && /too thin or too weak/.test(revealed.error) && !/acknowledge_weak_results|API/.test(revealed.error), "the server's 409 reveals the tick with a plain sentence, not a raw error", `after the 409: ${JSON.stringify(revealed)}`);
    approveMode = "refuse";
    await page.check("#decision-ack");
    await page.click("#decision-confirm");
    await page.waitForFunction(() => /smoke test stops here/.test(document.getElementById("decision-error").textContent), null, { timeout: 10000 });
    expect(posts.length === 2 && JSON.parse(posts[1]).acknowledge_weak_results === true, "after the tick the second request carries the flag", `posted: ${posts.join(" | ")}`);
    await page.click("#decision-cancel");
  } finally {
    allowedStatuses = [];
    page.off("request", onRequest);
    await page.unroute("**/models/*/results/plain").catch(() => {});
    await page.unroute("**/models/*/approve").catch(() => {});
  }
});

await step("Teach part for a viewer and a labeler: no tick or Quick/Better, and the card names the missing role (STUBBED role marks)", async () => {
  let role = "viewer";
  await page.route(`${BASE}/`, async (route) => {
    if (route.request().resourceType() !== "document") return route.continue();
    const response = await route.fetch();
    const marks = role === "viewer" ? 'data-role="viewer" data-can-label="0" data-can-train="0" data-can-approve="0"' : 'data-role="labeler" data-can-label="1" data-can-train="0" data-can-approve="0"';
    const html = (await response.text()).replace(/data-role="[a-z]+" data-can-label="\d" data-can-train="\d" data-can-approve="\d"/, marks);
    await route.fulfill({ response, body: html });
  });
  await page.route(readinessUrl(), async (route) => route.fulfill({ json: stubReadyBody(await (await route.fetch()).json()) }));
  try {
    for (const next of ["viewer", "labeler"]) {
      role = next;
      await openTeachFresh();
      await page.waitForSelector("#tr-model-list .model-why", { timeout: 15000 });
      const got = await page.evaluate(() => ({
        role: document.body.dataset.role, quality: !document.getElementById("tr-quality").hidden, tick: !document.getElementById("tr-ack-box").hidden,
        disabled: document.getElementById("tr-start").disabled, help: document.getElementById("tr-start-help").textContent,
        why: [...document.querySelectorAll("#tr-model-list .model-why")].map((n) => n.textContent).join(" | ")
      }));
      expect(got.role === next && !got.quality && !got.tick && got.disabled, `a ${next} sees no Quick/Better and no licence tick, and the button is off`, `${next}: ${JSON.stringify(got)}`);
      expect(got.help.length > 20, `a ${next} still gets the reason sentence under the button`, `${next}: help "${got.help}"`);
      if (next === "viewer") expect(/Your account can look but not change things/.test(got.why) && !/can label pictures/.test(got.why), "a viewer's model card says a viewer can look but not change things", `viewer card reason: ${got.why}`);
      else expect(/Your account cannot approve a model for use/.test(got.why) && !/can look but not change/.test(got.why), "a labeler's model card says what a labeler cannot do", `labeler card reason: ${got.why}`);
    }
  } finally {
    await page.unroute(`${BASE}/`).catch(() => {});
    await page.unroute(readinessUrl()).catch(() => {});
    await openTeachFresh().catch(() => {}); // back to the real role marks for the steps that follow
  }
});

await step("picture (annotation) dialog", async () => {
  await openPart("image-assets-panel");
  await page.locator(".asset-annotate").first().click();
  await page.waitForSelector("#annotation-dialog[open]");
  await dialogChecks("annotation dialog", "#annotation-dialog", ".asset-annotate");
  // A common laptop screen: the whole picture and the Done button must be on screen without scrolling the dialog.
  const original = page.viewportSize();
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.locator(".asset-annotate").first().click();
  await page.waitForSelector("#annotation-dialog[open]");
  await page.waitForTimeout(400);
  const fit = await page.evaluate(() => {
    const box = (selector) => { const r = document.querySelector(selector).getBoundingClientRect(); return { top: r.top, bottom: r.bottom }; };
    const stage = box("#annotation-stage"); const done = box("#done-annotation");
    return { vh: innerHeight, stageBottom: Math.round(stage.bottom), stageTop: Math.round(stage.top), doneBottom: Math.round(done.bottom), stageHeight: Math.round(stage.bottom - stage.top) };
  });
  expect(fit.stageBottom <= fit.vh && fit.stageTop >= 0, `at 1366x768 the whole picture is on screen (${fit.stageHeight}px tall)`, `at 1366x768 the picture is cut off: it runs ${fit.stageTop} to ${fit.stageBottom}px in a ${fit.vh}px window`);
  expect(fit.doneBottom <= fit.vh, "at 1366x768 the Done button is on screen", `at 1366x768 the Done button is off screen (bottom ${fit.doneBottom}px of ${fit.vh}px)`);
  expect(fit.stageHeight >= 240, "the picture area is big enough to draw in (240px or more)", `the picture area is only ${fit.stageHeight}px tall`);
  await page.keyboard.press("Escape");
  await page.setViewportSize(original);
});

await step("remove-picture confirmation (cancelled, nothing removed)", async () => {
  await openPart("image-assets-panel");
  const before = await stableCount(".asset-annotate");
  await page.locator(".asset-remove").first().click();
  await page.waitForSelector("dialog.confirm-dialog[open]");
  where = "remove-picture confirmation";
  const info = await page.evaluate(() => {
    const dialog = document.querySelector("dialog.confirm-dialog[open]");
    const buttons = [...dialog.querySelectorAll("button")];
    return { first: buttons[0]?.textContent.trim(), focusOnFirst: document.activeElement === buttons[0], role: dialog.getAttribute("role") };
  });
  expect(info.focusOnFirst, `the safe answer ("${info.first}") has the focus`, `focus is not on the safe answer ("${info.first}")`);
  expect(info.role === "alertdialog", "confirmation is an alertdialog", "confirmation has no alertdialog role");
  await dialogChecksConfirm();
  const after = await stableCount(".asset-annotate");
  expect(after === before, "no picture was removed", `picture count changed from ${before} to ${after}`);
});

async function dialogChecksConfirm() {
  // Tab / Shift+Tab inside the confirmation, then answer with the safe (first) button.
  for (const direction of ["Tab", "Shift+Tab"]) {
    let escaped = "";
    for (let i = 0; i < 6; i += 1) {
      await page.keyboard.press(direction);
      const inside = await page.evaluate(() => Boolean(document.querySelector("dialog.confirm-dialog[open]")?.contains(document.activeElement)));
      if (!inside && !escaped) escaped = `${direction} press ${i + 1}`;
    }
    expect(!escaped, `${direction} stays inside the confirmation`, `focus left the confirmation: ${escaped}`);
  }
  await page.locator("dialog.confirm-dialog[open] button").first().click();
  await page.waitForFunction(() => !document.querySelector("dialog.confirm-dialog"));
}

await step("approve-a-model dialog (cancelled)", async () => {
  await openPart("train-panel");
  const button = page.locator('button[aria-label^="Approve Model"]:not([disabled])').first();
  await button.scrollIntoViewIfNeeded();
  await button.click();
  await page.waitForSelector("#decision-dialog[open]");
  await dialogChecks("approve dialog", "#decision-dialog", 'button[aria-label^="Approve Model"]', { closeWith: "#decision-cancel" });
});

/* ---- W5-U: the administrator's download switch, the measured-score wording, the builder's suggestion, a second tool --------------
   The Access section is driven against the real disposable server (switch on, then off again). Everything that needs a
   measured model, a second teaching tool or another person's change is answered by page.route with the shapes the real
   server sends (marked STUBBED); those steps check what the page does with an answer, not what the server says. */
const DL_URL = "**/api/console/training-downloads";
const TINY_PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64");
const downloadsText = () => page.evaluate(() => ({
  state: document.getElementById("downloads-state").textContent, saveDisabled: document.getElementById("downloads-save").disabled,
  help: document.getElementById("downloads-save-help").textContent, onOnly: !document.getElementById("downloads-on-only").hidden,
  error: document.getElementById("downloads-error").textContent, errorOpen: document.getElementById("downloads-error").dataset.open,
  reload: !document.getElementById("downloads-reload").hidden, last: document.getElementById("downloads-last").textContent,
  licenceOpen: document.getElementById("downloads-licence").open
}));

await step("Access: Ready-made model downloads (the tick and a note are needed to switch on; one request; off again)", async () => {
  const puts = [];
  const onRequest = (request) => { if (request.method() === "PUT" && /training-downloads/.test(request.url())) puts.push(JSON.parse(request.postData() || "{}")); };
  page.on("request", onRequest);
  try {
    await go(`${BASE}/access#ready-made-model-downloads`);
    await page.waitForFunction(() => !/Loading/.test(document.getElementById("downloads-state")?.textContent || "Loading"), null, { timeout: 15000 })
      .then(() => pass("the downloads section loaded the setting"))
      .catch(() => fail("the downloads section never showed the setting"));
    expect(await page.locator("#downloads-heading").textContent() === "Ready-made model downloads", "the section is called exactly what the server's text says", "the section heading is not “Ready-made model downloads”");
    expect(await page.evaluate(() => document.activeElement?.id) === "ready-made-model-downloads", "the link's address puts focus on the section", "the section did not take focus from the address");
    let got = await downloadsText();
    expect(/switched off/.test(got.state) && got.saveDisabled, "it starts as switched off and Save is off until something changes", `start: ${JSON.stringify(got)}`);
    await page.check("#downloads-on");
    got = await downloadsText();
    expect(got.onOnly && got.saveDisabled && /Tick the box/.test(got.help) && got.licenceOpen, "choosing On shows the tick, opens the licence text, and says what is missing", `after On: ${JSON.stringify(got)}`);
    await page.check("#downloads-ack");
    await page.fill("#downloads-note", "ab");
    got = await downloadsText();
    expect(got.saveDisabled && /at least 3/.test(got.help), "a note under 3 characters keeps Save off", `short note: ${JSON.stringify(got)}`);
    await page.fill("#downloads-note", "Owner agreed, smoke test");
    await audit("Access: downloads section, turning on");
    await fieldSizesAtWidths("Access: downloads section");
    expect(!(await page.locator("#downloads-save").isDisabled()), "with the tick and a note Save is on", "Save stayed off with the tick and a note");
    await page.locator("#downloads-save").dblclick();
    await page.waitForFunction(() => /switched on/.test(document.getElementById("downloads-state").textContent), null, { timeout: 15000 });
    expect(puts.length === 1 && puts[0].allow === true && puts[0].acknowledge_terms === true && puts[0].based_on === false && puts[0].note === "Owner agreed, smoke test", "a double click sent one request, with the tick, the note and what the page saw (based_on)", `requests: ${JSON.stringify(puts)}`);
    got = await downloadsText();
    expect(/Note: Owner agreed, smoke test/.test(got.last) && got.saveDisabled, "the last change is shown with its note", `after save: ${JSON.stringify(got)}`);

    // another person changed it meanwhile: a plain sentence and a way to look again (STUBBED 409)
    allowedStatuses = [409];
    await page.route(DL_URL, async (route) => (route.request().method() === "PUT"
      ? route.fulfill({ status: 409, json: { error: "Someone changed this setting while you had the page open (downloads are now off), so nothing was changed. Reload the page to see it, then choose again.", allow: false, source: "console" } })
      : route.continue()));
    try {
      await page.check("#downloads-off");
      await page.click("#downloads-save");
      await page.waitForFunction(() => document.getElementById("downloads-error").dataset.open === "true", null, { timeout: 10000 });
      got = await downloadsText();
      expect(/Someone changed this setting/.test(got.error) && got.reload && got.saveDisabled, "a stale page gets the server's sentence and a button to show the current setting", `409: ${JSON.stringify(got)}`);
      expect(await page.evaluate(() => document.activeElement?.id) === "downloads-reload", "focus moves to that button", "focus did not move to the reload button");
    } finally {
      await page.unroute(DL_URL).catch(() => {});
    }
    await page.click("#downloads-reload");
    await page.waitForFunction(() => document.getElementById("downloads-reload").hidden, null, { timeout: 10000 });
    got = await downloadsText();
    expect(/switched on/.test(got.state) && got.error === "", "showing the current setting clears the problem", `after reload: ${JSON.stringify(got)}`);

    // and off again, so the disposable data ends as it began
    await page.check("#downloads-off");
    await page.click("#downloads-save");
    await page.waitForFunction(() => /switched off/.test(document.getElementById("downloads-state").textContent), null, { timeout: 15000 });
    const off = puts[puts.length - 1];
    expect(off.allow === false && !("acknowledge_terms" in off), "switching off asks for no tick", `off request: ${JSON.stringify(off)}`);
  } finally {
    allowedStatuses = [];
    page.off("request", onRequest);
  }
});

function stubBackendsBody(real, { downloadsAllowed = false } = {}) {
  const body = JSON.parse(JSON.stringify(real));
  body.downloads = { allowed: downloadsAllowed, reported_by: "console" };
  const second = { ...(body.backends.find((b) => b.name === "dfine-coco") || {}) };
  const shape = {
    name: "dfine-coco", description: "D-FINE (nano or small) on CPU. Opt-in second tool.", license_note: "Code: D-FINE is under the Apache License 2.0. Ask a lawyer before commercial use.",
    default_config: { epochs: 10, batch_size: 4, learning_rate: 0.0001, seed: 0, threads: 0, size: "nano", pretrained: "coco" },
    available_in_web_process: true, worker_reports_available: null, availability: { ok: true, message: "", how_to_enable: "" },
    start_options: [{ value: "coco", title: "Start from a general object model (COCO), D-FINE", summary: "Always starts from a ready-made model.", licence_plain: "Ready-made models come with their own terms. Ask a lawyer before commercial use.", available: true, ready: true, needs_acknowledgement: true, unavailable_reason: "", how_to_enable: "", size_note: "Already on this computer." }]
  };
  const index = body.backends.findIndex((b) => b.name === "dfine-coco");
  if (index >= 0) body.backends[index] = { ...second, ...shape }; else body.backends.push(shape);
  body.backends.forEach((b) => { if (b.name !== "dfine-coco") b.available_in_web_process = true; });
  return body;
}

await step("Teach part: an administrator sees the link to Access when downloads are off, anyone else only the sentence (STUBBED answers)", async () => {
  const backendsUrl = `**/api/projects/${opts.projectId}/training/backends`;
  await page.route(backendsUrl, async (route) => route.fulfill({ json: stubBackendsBody(await (await route.fetch()).json(), { downloadsAllowed: false }) }));
  const DOWNLOADS_SAID = "Downloading ready-made models is switched off, the built-in default. No one has chosen yet.";
  const downloadsUrl = "**/api/console/training-downloads";
  await page.route(downloadsUrl, (route) => route.fulfill({ json: { allow: false, source: "default", summary: DOWNLOADS_SAID, can_change: false } }));
  await page.route(readinessUrl(), async (route) => {
    const body = stubReadyBody(await (await route.fetch()).json());
    body.start_from = { resolved: null, available: [], reason_if_none: "No ready-made starting model can be used on this computer right now: none has been downloaded before, and downloading is switched off.", how_to_enable: "An administrator can switch this on in the console, under “Access”, in “Ready-made model downloads”; then press “Check again” here." };
    body.one_click = { ...body.one_click, ready: false, blockers: [body.start_from.reason_if_none] };
    await route.fulfill({ json: body });
  });
  try {
    await openTeachFresh();
    await page.waitForSelector("#tr-steps:not([hidden])", { timeout: 10000 });
    await page.locator("#tr-steps > summary").click();
    const admin = await page.evaluate(() => ({ links: [...document.querySelectorAll('#train-panel a[href="/access#ready-made-model-downloads"]')].map((a) => a.textContent), admin: document.getElementById("train-panel").dataset.admin, text: document.getElementById("tr-admin-note").textContent }));
    expect(admin.admin === "1" && admin.links.length === 1 && admin.links[0] === "Open Access settings", "an administrator gets one “Open Access settings” link in the administrator steps", `administrator: ${JSON.stringify(admin)}`);
    expect(/under “Access”, in “Ready-made model downloads”/.test(admin.text), "the server's own sentence is still shown beside it", `steps text: ${admin.text.slice(0, 200)}`);
    await page.evaluate(() => { document.getElementById("train-panel").dataset.admin = "0"; });
    await page.click("#tr-recheck");
    await page.waitForFunction(() => /Checked just now/.test(document.getElementById("tr-recheck-note").textContent), null, { timeout: 10000 });
    const other = await page.evaluate(() => ({ links: document.querySelectorAll('#train-panel a[href^="/access"]').length, text: document.getElementById("tr-admin-note").textContent }));
    expect(other.links === 0 && /under “Access”, in “Ready-made model downloads”/.test(other.text), "anyone else keeps the plain sentence and gets no link", `non-administrator: ${JSON.stringify(other)}`);
    expect((other.text.match(/Ready-made model downloads/g) || []).length === 1, "the place is named once in the steps: the server's sentence is not repeated by the page", `non-administrator steps: ${other.text.slice(0, 300)}`);
    await page.locator("#tr-admin > summary").click();
    const fold = await page.evaluate(() => { const note = document.getElementById("tr-downloads-note"); return { hidden: note.hidden, text: note.textContent, controls: note.querySelectorAll("a, button, input").length, visible: note.getBoundingClientRect().height > 0 }; });
    expect(!fold.hidden && fold.visible && fold.controls === 0 && fold.text.startsWith(DOWNLOADS_SAID) && /Only an administrator can change this\./.test(fold.text), "the administrator disclosure shows the current setting read-only, in the server's words, and says who can change it", `fold note: ${JSON.stringify(fold)}`);
  } finally {
    await page.unroute(downloadsUrl).catch(() => {});
    await page.unroute(backendsUrl).catch(() => {});
    await page.unroute(readinessUrl()).catch(() => {});
  }
});

await step("administrator fold: a second tool shows its own licence and a size choice, and the main button keeps the default tool (STUBBED answers)", async () => {
  const backendsUrl = `**/api/projects/${opts.projectId}/training/backends`;
  const posts = [];
  const onRequest = (request) => { if (request.method() === "POST" && /\/training\/runs$/.test(request.url())) posts.push(JSON.parse(request.postData() || "{}")); };
  page.on("request", onRequest);
  await page.route(backendsUrl, async (route) => route.fulfill({ json: stubBackendsBody(await (await route.fetch()).json()) }));
  await page.route(readinessUrl(), async (route) => route.fulfill({ json: stubReadyBody(await (await route.fetch()).json()) }));
  await page.route("**/api/projects/*/training/runs", async (route) => (route.request().method() === "POST" ? route.fulfill({ status: 202, json: stubRunBody() }) : route.continue()));
  allowedStatuses = [404];
  try {
    await openTeachFresh();
    await page.locator("#tr-admin > summary").click();
    const defaultTool = await page.locator("#tr-tool").inputValue();
    await page.selectOption("#tr-tool", "dfine-coco");
    const got = await page.evaluate(() => ({
      licence: document.getElementById("tr-licence-body").textContent, size: document.getElementById("tr-model-size").closest("label").hidden ? "hidden" : document.getElementById("tr-model-size").value,
      sizeChoices: [...document.querySelectorAll("#tr-model-size option")].map((o) => o.textContent), imgsz: document.getElementById("tr-size").closest("label").hidden,
      visibleStarts: [...document.querySelectorAll("#tr-start-from-options .start-choice")].filter((e) => !e.hidden).map((e) => e.dataset.value), checked: document.querySelector("#tr-start-from-options input:checked")?.value,
      rounds: document.getElementById("tr-rounds").value, batch: document.getElementById("tr-batch").value
    }));
    expect(/Code: D-FINE is under the Apache License 2\.0/.test(got.licence), "the second tool's own licence note is in the licence section", `licence: ${got.licence.slice(0, 160)}`);
    expect(got.size === "nano" && got.sizeChoices.join("|") === "Smaller (nano)|Larger (small)" && got.imgsz, "its size is a plain choice and the picture-size field it does not have is hidden", `size: ${JSON.stringify(got)}`);
    expect(got.visibleStarts.join() === "coco" && got.checked === "coco" && got.rounds === "10" && got.batch === "4", "only its own start is offered and chosen, with its own starting values", `starts: ${JSON.stringify(got)}`);
    await audit("administrator fold with the second tool");
    await page.selectOption("#tr-model-size", "small");
    await page.check("#tr-pre-ack");
    await page.click("#tr-start-exact");
    await page.waitForFunction(() => !document.getElementById("tr-run").hidden, null, { timeout: 10000 });
    const exact = posts[0] || {};
    expect(exact.backend === "dfine-coco" && JSON.stringify(Object.keys(exact.config || {}).sort()) === JSON.stringify(["batch_size", "epochs", "pretrained", "size"]) && exact.config.size === "small" && exact.acknowledge_pretrained_terms === true, "“Teach with exactly these settings” sends only keys the second tool knows", `exact request: ${JSON.stringify(exact)}`);
    await openTeachFresh();
    await page.locator("#tr-admin > summary").click();
    await page.selectOption("#tr-tool", "dfine-coco");
    await page.waitForFunction(() => !document.getElementById("tr-ack-box").hidden, null, { timeout: 10000 });
    await page.check("#tr-ack");
    await page.click("#tr-start");
    await page.waitForTimeout(400);
    const main = posts[1] || {};
    expect(main.backend === defaultTool && JSON.stringify(main.config) === '{"quality":"quick"}' && main.snapshot === "latest", "the main button teaches with the default tool whatever is chosen in the fold", `main request: ${JSON.stringify(main)} (default ${defaultTool})`);
  } finally {
    allowedStatuses = [];
    page.off("request", onRequest);
    await page.unroute(backendsUrl).catch(() => {});
    await page.unroute(readinessUrl()).catch(() => {});
    await page.unroute("**/api/projects/*/training/runs").catch(() => {});
  }
});

const STUB_OPERATING_POINTS = {
  available: true, source: "validation split", records: 1733, ground_truth_boxes: 630, recall_ceiling: 0.8825,
  suggestions: [
    { target_recall: 0.8, reachable: true, threshold: 0.235, enough_data: true, recommended: true, validation: { precision: 0.52, recall: 0.8, n_gt: 630 }, test: null, interval90: { recall: null, precision: [0.6129, 0.8801], resamples_used: 200, basis: "checking" }, plain: "A cut-off of 0.235 found 80% of the 630 boxes on the Checking pictures and 79% of the 995 boxes on the Final test pictures; 52% of the boxes it drew there were right. A person chooses the setting." },
    { target_recall: 0.9, reachable: false, threshold: null, enough_data: true, recommended: false, validation: null, test: null, interval90: null, plain: "This model never finds about 12% of the objects, whatever the setting, so a target of 90% cannot be reached." },
    { target_recall: 0.95, reachable: false, threshold: null, enough_data: true, recommended: false, validation: null, test: null, interval90: null, plain: "This model never finds about 12% of the objects, whatever the setting, so a target of 95% cannot be reached." }
  ], bucket_precision: []
};
const STUB_WORDING = {
  available: true, reason: "", split: "Final test pictures",
  buckets: [
    { low: 0, high: 0.5, predictions: 300, correct: 30, precision: 0.1, enough: true, sentence: "Scores between 0 and 0.5 were right 10% of the time on the Final test pictures (300 boxes)." },
    { low: 0.5, high: 0.8, predictions: 9, correct: 4, precision: 0.44, enough: false, sentence: "Scores between 0.5 and 0.8: only 9 boxes on the Final test pictures, too few to say how often they were right." },
    { low: 0.8, high: 1, predictions: 400, correct: 364, precision: 0.91, enough: true, sentence: "Scores between 0.8 and 1 were right 91% of the time on the Final test pictures (400 boxes)." }
  ]
};

await step("results card: “How far to trust the scores” is a closed disclosure with the server's measured sentences (STUBBED answers)", async () => {
  const a0 = await firstPictureId();
  let mode = "measured";
  allowedStatuses = [404]; // the stubbed run started in the step before may still be asked about once, as the page changes
  await page.route("**/models/*/results/plain", async (route) => {
    const body = stubPlainBody(a0);
    if (mode === "measured") Object.assign(body, { operating_points: STUB_OPERATING_POINTS, wording: STUB_WORDING });
    if (mode === "older") Object.assign(body, { operating_points: { available: false, reason: "How often each score was right is not measured for this model (it was checked before this was added, or its backend could not score the pictures)." }, wording: { available: false, reason: "x", split: "Final test pictures", buckets: [] } });
    await route.fulfill({ json: body });
  });
  try {
    for (const next of ["measured", "older", "absent"]) {
      mode = next;
      await openTeachFresh();
      await page.locator('#tr-model-list button[aria-label^="Show results for"]').first().click();
      if (next === "absent") { // an older server that sends neither block: no disclosure rather than a guess
        await page.waitForSelector("#tr-model-list .model-results:not([hidden]) .result-not-final", { timeout: 15000 });
        expect(await page.locator("#tr-model-list .model-results:not([hidden]) .result-trust").count() === 0, "(absent) a server that sends neither block gets no disclosure", "a disclosure was shown without any data");
        continue;
      }
      await page.waitForSelector("#tr-model-list .model-results:not([hidden]) .result-trust", { timeout: 15000 });
      const closed = await page.evaluate(() => { const d = document.querySelector("#tr-model-list .model-results:not([hidden]) .result-trust"); return { open: d.open, title: d.querySelector("summary").textContent, buttons: d.querySelectorAll("button").length, after: Boolean(document.querySelector("#tr-model-list .model-results:not([hidden]) .result-labels")?.compareDocumentPosition(d) & Node.DOCUMENT_POSITION_FOLLOWING) }; });
      expect(!closed.open && closed.title === "How far to trust the scores" && closed.buttons === 0 && closed.after, `(${next}) the disclosure is closed, titled, below the label lines, and holds no button`, `${next}: ${JSON.stringify(closed)}`);
      await page.locator("#tr-model-list .model-results:not([hidden]) .result-trust summary").click();
      if (next === "measured") await audit("results disclosure (open)");
      const text = await page.evaluate(() => document.querySelector("#tr-model-list .model-results:not([hidden]) .result-trust").innerText);
      if (next === "measured") {
        expect(/Scores between 0\.8 and 1 were right 91%/.test(text) && !/Scores between 0\.5 and 0\.8/.test(text), "only score ranges with enough boxes are listed", `text: ${text}`);
        expect(/A cut-off of 0\.235 found 80%/.test(text) && /cannot be reached/.test(text) && (text.match(/A person chooses the setting\./g) || []).length === 1, "each target is worded as the server says, and “A person chooses the setting.” appears once", `text: ${text}`);
        expect((text.match(/Of the boxes it drew, about 61% to 89% were right\./g) || []).length === 1, "the range of boxes that were right is worded once, low end down and high end up", `text: ${text}`);
        expect((text.match(/This is one setting for all labels\./g) || []).length === 1, "the cut-off is said to be one setting for all labels, once", `text: ${text}`);
        expect(await page.locator('#tr-model-list .model-results:not([hidden]) .result-trust li[data-recommended="true"]').count() === 1, "only the one the server calls recommended is marked so", "a cut-off was marked recommended without the server saying so");
      } else {
        expect(/Not measured for this model: /.test(text) && !/cut-off/.test(text), `(${next}) an unmeasured model says so, with a reason`, `${next} text: ${text}`);
      }
    }
  } finally {
    allowedStatuses = [];
    await page.unroute("**/models/*/results/plain").catch(() => {});
  }
});

// ---- W9-L3: the improve loop (corrections, origin badge, comparison with the model in use, readiness notes) ----------
const CMP_SENTENCE = `Better on these test pictures: the new model found 5 of 6 test boxes (83%) and the model in use found 3 (50%). Only one of the two found 2 boxes: the new model alone found 2, the model in use alone found 0. ${NOT_FINAL}`;
const CMP_UNFAIR = "2 of the 5 test pictures were also used to teach the model in use, so its score on them is not a fair test.";
const stripNotFinal = (text) => String(text || "").replace(/\s*Not a final decision\b[\s\S]*$/i, "").trim();
const countNotFinal = (text) => (String(text).match(/not a final decision/gi) || []).length;
function stubComparison(kind) {
  const base = { available: true, schema: "athena.comparison/1", baseline: { model_id: "x", version: 1, backend: "stand-in", trained_on_pictures: 9 }, test_pictures: 5, test_boxes: 6, fair: true, unfair_reason: "", overlap_pictures: 0,
    baseline_result: { found: 3, of: 6, wrong_boxes: 2 }, candidate_result: { found: 5, of: 6, wrong_boxes: 1 }, per_label: [{ label: "bag", baseline_found: 3, candidate_found: 5, of: 6 }],
    difference: { found: 2, wrong_boxes: -1, only_candidate_found: 2, only_baseline_found: 0 }, verdict: "better", plain: CMP_SENTENCE };
  if (kind === "worse") return { ...base, verdict: "worse", plain: "Worse on these test pictures: numbers invented for this check. " + NOT_FINAL };
  if (kind === "same") return { ...base, verdict: "about_the_same", plain: "About the same on these test pictures: numbers invented for this check. " + NOT_FINAL };
  if (kind === "unfair") return { ...base, fair: false, unfair_reason: CMP_UNFAIR, overlap_pictures: 2, verdict: "cannot_tell", plain: `Cannot tell, and this comparison is not fair: ${CMP_UNFAIR} ${NOT_FINAL}` };
  if (kind === "caps") return { ...base, plain: "Better on these test pictures: numbers invented for this check. NOT A FINAL DECISION. THIS IS THE COMPUTER’S OPINION. A PERSON DECIDES." };
  if (kind === "unavailable") return { available: false, reason: "No comparison with the model in use: no model is in use yet." };
  return base;
}

await step("results card: “Compared with the model in use” shows the server's sentence, two rows and a verdict word, or why not (STUBBED answers)", async () => {
  const a0 = await firstPictureId();
  let mode = "better";
  await page.route("**/models/*/results/plain", async (route) => {
    const body = stubPlainBody(a0);
    if (mode !== "absent") body.comparison = stubComparison(mode);
    await route.fulfill({ json: body });
  });
  try {
    for (const next of ["better", "worse", "same", "unfair", "caps", "unavailable", "absent"]) {
      mode = next;
      await openTeachFresh();
      await page.locator('#tr-model-list button[aria-label^="Show results for"]').first().click();
      await page.waitForSelector("#tr-model-list .model-results:not([hidden]) .result-not-final", { timeout: 15000 });
      const got = await page.evaluate(() => {
        const root = document.querySelector("#tr-model-list .model-results:not([hidden])");
        const block = root.querySelector(".result-compare");
        const kids = [...root.children];
        const at = (selector) => kids.findIndex((kid) => kid.matches(selector));
        return {
          block: Boolean(block), none: root.querySelector(".result-compare-none")?.textContent || "", verdict: block?.dataset.verdict || "", badge: block?.querySelector(".compare-verdict")?.textContent.trim() || "",
          sentence: block?.querySelector(".result-compare-plain")?.textContent || "", rows: [...(block?.querySelectorAll("tbody tr") || [])].map((tr) => [...tr.children].map((cell) => cell.textContent.trim())),
          unfairLines: block?.querySelectorAll(".result-compare-unfair").length ?? 0, text: block?.textContent || "", rootText: root.textContent,
          order: { headline: at(".result-headline"), labels: at(".result-labels"), compare: at(".result-compare"), trust: at(".result-trust") }
        };
      });
      if (next === "absent") { expect(!got.block && got.none === "", "(absent) a server that sends no comparison gets nothing at all", `absent: ${JSON.stringify(got)}`); continue; }
      if (next === "unavailable") { expect(!got.block && got.none === "No comparison with the model in use: no model is in use yet.", "(unavailable) the server's reason is shown as written, with no table", `unavailable: ${JSON.stringify(got)}`); continue; }
      const word = { better: "Better", caps: "Better", worse: "Worse", same: "About the same", unfair: "Cannot tell" }[next];
      expect(got.block && got.badge.endsWith(word) && got.verdict === { better: "better", caps: "better", worse: "worse", same: "about_the_same", unfair: "cannot_tell" }[next], `(${next}) the verdict is a word, ${word}`, `${next}: ${JSON.stringify(got)}`);
      expect(got.sentence === stripNotFinal(stubComparison(next).plain) && got.sentence.length > 20, `(${next}) the sentence is the server's, word for word, without its closing not-a-final-decision lines`, `sentence: ${got.sentence}`);
      expect(countNotFinal(got.rootText) === 1, `(${next}) the not-a-final-decision line appears once on the results card`, `times: ${countNotFinal(got.rootText)}`);
      expect(JSON.stringify(got.rows) === JSON.stringify([["New model", "5 of 6", "1"], ["Model in use (version 1)", "3 of 6", "2"]]), `(${next}) two rows: the new model and the model in use`, `rows: ${JSON.stringify(got.rows)}`);
      expect(got.order.headline < got.order.compare && got.order.labels < got.order.compare && (got.order.trust < 0 || got.order.compare < got.order.trust), `(${next}) the block sits below the headline and label lines and above “How far to trust the scores”`, `order: ${JSON.stringify(got.order)}`);
      if (next === "unfair") expect(got.text.split(CMP_UNFAIR).length - 1 === 1 && got.unfairLines === 0, "(unfair) the reason for the unfair comparison is on screen once", `unfair: ${got.text}`);
      if (next === "better") { await audit("results card with the comparison block"); await fieldSizesAtWidths("results card with the comparison block"); }
    }
  } finally {
    await page.unroute("**/models/*/results/plain").catch(() => {});
  }
});

await step("approve dialog: the comparison sentence comes first, as information only (STUBBED answers)", async () => {
  const a0 = await firstPictureId();
  let mode = "better";
  await page.route("**/models/*/results/plain", async (route) => {
    const body = stubPlainBody(a0);
    if (mode !== "absent") body.comparison = stubComparison(mode);
    await route.fulfill({ json: body });
  });
  try {
    for (const next of ["better", "unavailable"]) {
      mode = next;
      await openTeachFresh();
      const button = page.locator('button[aria-label^="Approve Model"]:not([disabled])').first();
      await button.scrollIntoViewIfNeeded();
      await button.click();
      await page.waitForSelector("#decision-dialog[open]");
      const got = await page.evaluate(() => {
        const body = document.getElementById("decision-body");
        const kids = [...body.children];
        return { first: kids[0]?.className || "", text: kids[0]?.textContent || "", headline: kids.findIndex((kid) => kid.classList.contains("result-headline")), controls: body.querySelectorAll("input, button").length, fits: (() => { const r = document.getElementById("decision-dialog").getBoundingClientRect(); return r.top >= -1 && r.bottom <= innerHeight + 1; })(), all: body.textContent, notFinal: (body.textContent.match(/not a final decision/gi) || []).length };
      });
      if (next === "better") {
        expect(got.first.includes("compare-in-dialog") && got.text === `Compared with the model in use: ${stripNotFinal(CMP_SENTENCE)}` && got.headline > 0, "the sentence is the first thing in the dialog, word for word, above the verdict headline", `dialog: ${JSON.stringify(got)}`);
        expect(got.notFinal === 1, "the approve dialog says it is not a final decision once", `times: ${got.notFinal}`);
        expect(got.controls === 0, "the sentence adds no tick-box and no button", `controls in the body: ${got.controls}`);
        expect(got.fits, "the dialog still fits the window", "the dialog does not fit the window");
        await audit("approve dialog with the comparison sentence");
      } else {
        expect(!/Compared with the model in use/.test(got.all), "with no comparison there is no such sentence", `dialog: ${got.all.slice(0, 200)}`);
      }
      await page.click("#decision-cancel");
      await page.waitForFunction(() => !document.getElementById("decision-dialog").open);
    }
  } finally {
    await page.unroute("**/models/*/results/plain").catch(() => {});
  }
});

await step("teach part: the notes the server sends sit under the plan sentence in muted words (STUBBED readiness)", async () => {
  const NOTES = ["The picture files were not re-checked for this preview. They are checked again when teaching starts."];
  await page.route("**/training/readiness*", async (route) => { const real = await (await route.fetch()).json(); const body = stubReadyBody(real); body.one_click.notes = NOTES; body.one_click.files_rechecked = false; await route.fulfill({ json: body }); });
  try {
    await openTeachFresh();
    await page.waitForFunction(() => !document.getElementById("tr-ack-box").hidden, null, { timeout: 15000 });
    await page.check("#tr-ack");
    await page.waitForFunction(() => /It will start from/.test(document.getElementById("tr-start-help").textContent), null, { timeout: 15000 });
    const got = await page.evaluate(() => { const help = document.getElementById("tr-start-help"); return { notes: [...help.querySelectorAll(".train-plan-note")].map((n) => n.textContent), plan: [...help.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(""), noteColour: help.querySelector(".train-plan-note") ? getComputedStyle(help.querySelector(".train-plan-note")).color : "", planColour: getComputedStyle(help).color }; });
    expect(JSON.stringify(got.notes) === JSON.stringify(NOTES) && /^It will start from/.test(got.plan), "the plan sentence is followed by the server's note", `notes: ${JSON.stringify(got)}`);
    expect(got.noteColour !== got.planColour, "the note is in a dimmer colour than the plan sentence", `colours: ${got.noteColour} / ${got.planColour}`);
    await audit("teach part with readiness notes");
  } finally {
    await page.unroute("**/training/readiness*").catch(() => {});
  }
});

async function openPicturesFresh() {
  await go(`${BASE}/#project=${opts.projectId}`);
  await reloadPage();
  await page.waitForFunction((title) => document.getElementById("workspace-title")?.textContent.includes(title), "Smoke test bags");
  await openPart("image-assets-panel");
}

await step("pictures part: corrections from check screens, one outline button, the outcome in words, the skipped picture named (STUBBED answers)", async () => {
  const SKIP = { observation_id: "o3", app_name: "Line 4 check", taken_at: "2026-10-23T13:24:58+00:00", model_version: { id: "m", version: 1 }, kinds: { true_positive: 1, false_positive: 0, missed: 0 }, state: "cannot_import", reason: "picture_file_missing", message: "The picture file from the check screen is no longer in storage." };
  const PEND = (id) => ({ observation_id: id, app_name: "Line 4 check", taken_at: "2026-10-23T13:25:58+00:00", model_version: { id: "m", version: 1 }, kinds: { true_positive: 1, false_positive: 1, missed: 0 }, state: "pending", reason: null, message: "" });
  let state = "waiting";
  const posts = [];
  const answer = () => state === "waiting" ? { pending: 2, imported: 0, cannot_import: 1, total: 3, truncated: false, batch_limit: 50, items: [PEND("o1"), PEND("o2"), SKIP] }
    : state === "done" ? { pending: 0, imported: 2, cannot_import: 1, total: 3, truncated: false, batch_limit: 50, items: [SKIP] }
      : { pending: 0, imported: 0, cannot_import: 0, total: 0, truncated: false, batch_limit: 50, items: [] };
  await page.route("**/feedback/corrections", (route) => route.fulfill({ json: answer() }));
  await page.route("**/feedback/corrections/import", async (route) => {
    posts.push(route.request().postData());
    await new Promise((resolve) => setTimeout(resolve, 400)); // long enough for a second press to arrive
    if (state === "fail") return route.fulfill({ status: 500, json: { error: "The server could not finish. Nothing more was done." } });
    state = "done";
    await route.fulfill({ json: { imported: [{ observation_id: "o1", asset_id: "a", boxes_created: 2, missed_without_box: 0, false_alarms: 1 }, { observation_id: "o2", asset_id: "b", boxes_created: 0, missed_without_box: 1, false_alarms: 0 }], skipped: [{ observation_id: "o3", reason: "picture_file_missing", message: SKIP.message }], totals: { requested: 3, imported: 2, skipped: 1, boxes_created: 2, missed_without_box: 1, remaining_pending: 0 } } });
  });
  try {
    await openPicturesFresh();
    await page.waitForFunction(() => /waiting/.test(document.getElementById("corrections-count").textContent), null, { timeout: 15000 });
    const before = await page.evaluate(() => {
      const button = document.getElementById("corrections-import");
      const yellow = [...document.querySelectorAll("#image-assets-panel button")].filter((b) => b.checkVisibility() && getComputedStyle(b).backgroundColor === "rgb(245, 196, 0)").map((b) => b.textContent.trim());
      return { count: document.getElementById("corrections-count").textContent, skipped: [...document.querySelectorAll("#corrections-skipped li")].map((li) => li.textContent), button: button.textContent, cls: button.className, yellow, help: document.getElementById("corrections-help").textContent };
    });
    expect(/^2 pictures with corrections are waiting\. 1 cannot be added:/.test(before.count) && before.skipped.length === 1 && before.skipped[0].includes(SKIP.message), "the card counts what waits and names the picture that cannot be added, with the server's reason", `before: ${JSON.stringify(before)}`);
    expect(before.button === "Add 2 corrected pictures for review" && before.cls === "secondary" && before.yellow.length === 1 && /Add pictures/.test(before.yellow[0]), "the new button is an outline button and Add pictures stays the part's one yellow action", `button: ${JSON.stringify(before)}`);
    expect(/Needs review/.test(before.help) && /The model in use is not changed\./.test(before.help), "what happens next is explained once, in plain words", `help: ${before.help}`);
    await audit("pictures part with the corrections card");
    await fieldSizesAtWidths("pictures part with the corrections card");
    await page.dblclick("#corrections-import");
    await page.waitForFunction(() => /Added 2 pictures/.test(document.getElementById("corrections-result").textContent), null, { timeout: 15000 });
    await page.waitForFunction(() => /^No corrections are waiting\./.test(document.getElementById("corrections-count").textContent), null, { timeout: 15000 });
    const after = await page.evaluate(() => ({ result: document.getElementById("corrections-result").innerText, count: document.getElementById("corrections-count").textContent, focus: document.activeElement?.id, button: !document.getElementById("corrections-import").hidden, listed: document.querySelectorAll("#corrections-skipped li").length }));
    expect(posts.length === 1 && posts[0] === "{}", "a double press sends one request, with the default body", `posts: ${JSON.stringify(posts)}`);
    expect(/Added 2 pictures for review, with 2 boxes marked Needs review\./.test(after.result) && /1 false alarm was noted/.test(after.result) && /1 item the computer missed has no box yet/.test(after.result) && /1 picture was not added:/.test(after.result) && after.result.includes(SKIP.message), "the outcome is in plain words and names the picture that was not added", `result: ${after.result}`);
    expect(after.focus === "corrections-result" && !after.button && after.listed === 0 && /1 cannot be added \(named below\)\./.test(after.count), "focus is on the result, the button is gone, and the skipped picture is named once", `after: ${JSON.stringify(after)}`);
    expect(!/observation|artifact|SHA|undefined|null|NaN/i.test(after.result + after.count), "no raw wording in the card", `${after.result} ${after.count}`);
    // a failure is said in plain words and the button can be pressed again
    state = "waiting"; posts.length = 0;
    await openPicturesFresh();
    await page.waitForFunction(() => /waiting/.test(document.getElementById("corrections-count").textContent), null, { timeout: 15000 });
    state = "fail";
    allowedStatuses = [500];
    await page.click("#corrections-import");
    await page.waitForFunction(() => /were not added/.test(document.getElementById("corrections-result").textContent), null, { timeout: 15000 });
    const failed = await page.evaluate(() => ({ result: document.getElementById("corrections-result").textContent, disabled: document.getElementById("corrections-import").disabled }));
    expect(/The corrections were not added\. The server could not finish\./.test(failed.result) && !failed.disabled, "a failed import says so in plain words and leaves the button ready", `failed: ${JSON.stringify(failed)}`);
    allowedStatuses = [];
    // nothing yet
    state = "none";
    await openPicturesFresh();
    await page.waitForFunction(() => /No corrections yet/.test(document.getElementById("corrections-count").textContent), null, { timeout: 15000 });
    expect(await page.locator("#corrections-import").isHidden() && (await page.locator("#corrections-count").textContent()) === "No corrections yet. They appear here when someone marks a result right or wrong on a check screen.", "with nothing to add the card says so and shows no button", "the empty card is wrong");
  } finally {
    allowedStatuses = [];
    await page.unroute("**/feedback/corrections").catch(() => {});
    await page.unroute("**/feedback/corrections/import").catch(() => {});
  }
});

await step("pictures list: a picture from a check screen has a text badge, and one with no boxes says nothing to teach from yet (STUBBED origin)", async () => {
  await page.route(new RegExp(`/api/projects/${opts.projectId}/assets$`), async (route) => {
    const body = await (await route.fetch()).json();
    body.assets = body.assets.map((asset, index) => (index === 0 ? { ...asset, origin: "operator_feedback", from_observation: "o1", accepted_boxes: 0, pending_boxes: 0 } : index === 1 ? { ...asset, origin: "operator_feedback", from_observation: "o2", accepted_boxes: 0, pending_boxes: 2 } : { ...asset, origin: "upload" }));
    await route.fulfill({ json: body });
  });
  try {
    await openPicturesFresh();
    await page.waitForSelector("#asset-list .origin-badge", { timeout: 15000 });
    const got = await page.evaluate(() => ({
      badges: [...document.querySelectorAll("#asset-list .origin-badge")].map((b) => b.textContent.trim()), marks: document.querySelectorAll("#asset-list .origin-badge .origin-mark").length,
      nothing: [...document.querySelectorAll("#asset-list .asset-nothing")].map((p) => p.textContent), review: document.querySelectorAll("#asset-list .count-chip.review").length
    }));
    expect(got.badges.length === 2 && got.badges.every((text) => text === "From a check screen") && got.marks === 2, "pictures that came from a check screen carry the words and a drawn mark", `badges: ${JSON.stringify(got)}`);
    expect(got.nothing.length === 1 && got.nothing[0] === "This picture was marked wrong but nothing was marked as missed, so there is no box to teach from. Draw a box if something should be found here, or leave it.", "only the picture with no boxes and nothing waiting says there is nothing to teach from yet", `nothing: ${JSON.stringify(got)}`);
    expect(got.review === 1, "the Needs review count still shows", `review chips: ${got.review}`);
    await audit("pictures list with the check-screen badge");
  } finally {
    await page.unroute(new RegExp(`/api/projects/${opts.projectId}/assets$`)).catch(() => {});
  }
});

await step("pictures part: bulk accept warns about boxes from check screens, and the picture list can show only those (STUBBED answers)", async () => {
  const assetsRoute = new RegExp(`/api/projects/${opts.projectId}/assets$`);
  const autolabelRoute = new RegExp(`/api/projects/${opts.projectId}/autolabel$`);
  const WARN = "2 of these boxes come from check-screen corrections. Check them first: the computer’s boxes there were confirmed or added by an operator, not found by the auto-suggest.";
  let withFeedback = true;
  const reviews = [];
  await page.route(assetsRoute, async (route) => {
    const body = await (await route.fetch()).json();
    body.assets = body.assets.map((asset, index) => (index === 1 && withFeedback ? { ...asset, origin: "operator_feedback", from_observation: "o2", accepted_boxes: 0, pending_boxes: 2 } : index === 0 ? { ...asset, origin: "upload", pending_boxes: 3 } : { ...asset, origin: "upload" }));
    await route.fulfill({ json: body });
  });
  await page.route(autolabelRoute, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({ json: { enabled: true, worker_online: true, worker_loading: false, jobs: [], pending_boxes: withFeedback ? 5 : 3 } });
  });
  // An older server refuses the extra "preview" field with a 400 and changes nothing: the question then falls back to what the list shows.
  allowedStatuses = [400];
  await page.route("**/annotations/review", async (route) => {
    if (JSON.parse(route.request().postData() || "{}").preview) return route.fulfill({ status: 400, json: { error: "Request contains an unsupported field." } });
    reviews.push(route.request().postData());
    await route.fulfill({ json: { updated: 3, pending_boxes: 0 } });
  });
  try {
    await openPicturesFresh();
    await page.waitForFunction(() => !document.getElementById("autolabel-review").hidden && document.querySelectorAll("#asset-list .asset-row").length >= 2, null, { timeout: 15000 });
    const total = await page.locator("#asset-list .asset-row").count();
    const before = await page.evaluate(() => ({ note: document.getElementById("autolabel-feedback-note").textContent, noteShown: !document.getElementById("autolabel-feedback-note").hidden, filterShown: !document.getElementById("asset-filter").hidden, button: document.getElementById("asset-filter-feedback").textContent, pressed: document.getElementById("asset-filter-feedback").getAttribute("aria-pressed") }));
    expect(before.noteShown && before.note === WARN, "beside the bulk-accept button the page says how many boxes come from check screens", `note: ${JSON.stringify(before)}`);
    expect(before.filterShown && before.button === "Show only pictures from check screens" && before.pressed === "false", "the picture list offers a plain toggle button, off to begin with", `filter: ${JSON.stringify(before)}`);
    await audit("pictures part with the check-screen note and filter");
    await fieldSizesAtWidths("pictures part with the check-screen note and filter");
    // the filter works from the keyboard and keeps the other pictures reachable
    await page.focus("#asset-filter-feedback");
    await page.keyboard.press("Enter");
    const filtered = await page.evaluate(() => ({ shown: [...document.querySelectorAll("#asset-list .asset-row")].filter((row) => !row.hidden).map((row) => row.dataset.origin || ""), pressed: document.getElementById("asset-filter-feedback").getAttribute("aria-pressed"), status: document.getElementById("asset-status").textContent, focus: document.activeElement?.id }));
    expect(filtered.shown.length === 1 && filtered.shown[0] === "operator_feedback" && filtered.pressed === "true" && filtered.focus === "asset-filter-feedback" && /Showing only the 1 from check screens\./.test(filtered.status), "the toggle (Enter) shows only the pictures from check screens, says so, and keeps the focus", `filtered: ${JSON.stringify(filtered)}`);
    await page.keyboard.press("Space");
    const all = await page.evaluate(() => [...document.querySelectorAll("#asset-list .asset-row")].filter((row) => !row.hidden).length);
    expect(all === total, "pressing it again shows every picture", `shown ${all} of ${total}`);
    // bulk accept asks first; the safe answer comes first, has the focus and sends nothing
    await page.click("#autolabel-accept");
    await page.waitForSelector("dialog.confirm-dialog[open]", { timeout: 10000 });
    const asked = await page.evaluate(() => { const d = document.querySelector("dialog.confirm-dialog[open]"); const buttons = [...d.querySelectorAll("button")]; return { title: d.querySelector("h2").textContent, text: d.querySelector(".confirm-body").innerText, buttons: buttons.map((b) => b.textContent), focus: document.activeElement?.textContent }; });
    expect(asked.text.includes(WARN) && JSON.stringify(asked.buttons) === JSON.stringify(["Check them first", "Accept anyway"]) && asked.focus === "Check them first" && reviews.length === 0, "pressing Accept shows the line and a question first; the safe answer has the focus and nothing was sent", `dialog: ${JSON.stringify(asked)} reviews ${reviews.length}`);
    await audit("bulk accept question about check-screen boxes");
    // focus stays inside the question
    const seen = new Set();
    for (let i = 0; i < 4; i += 1) { await page.keyboard.press("Tab"); seen.add(await page.evaluate(() => (document.activeElement?.closest("dialog.confirm-dialog") ? document.activeElement.textContent : "OUTSIDE"))); }
    expect(!seen.has("OUTSIDE") && seen.size === 2, "Tab stays inside the question", `seen: ${[...seen]}`);
    await page.getByRole("button", { name: "Check them first" }).click();
    await page.waitForFunction(() => !document.querySelector("dialog.confirm-dialog"));
    const checked = await page.evaluate(() => ({ pressed: document.getElementById("asset-filter-feedback").getAttribute("aria-pressed"), focus: document.activeElement?.id, shown: [...document.querySelectorAll("#asset-list .asset-row")].filter((row) => !row.hidden).length }));
    expect(reviews.length === 0 && checked.pressed === "true" && checked.focus === "asset-filter-feedback" && checked.shown === 1, "“Check them first” sends nothing, turns the filter on and puts the focus on it", `after: ${JSON.stringify(checked)} reviews ${reviews.length}`);
    // Escape also sends nothing
    await page.click("#autolabel-accept");
    await page.waitForSelector("dialog.confirm-dialog[open]");
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.querySelector("dialog.confirm-dialog"));
    expect(reviews.length === 0, "Escape sends nothing", `reviews ${reviews.length}`);
    // going on is a person's choice and still works
    await page.click("#autolabel-accept");
    await page.waitForSelector("dialog.confirm-dialog[open]");
    await page.getByRole("button", { name: "Accept anyway" }).click();
    for (let i = 0; i < 50 && reviews.length < 1; i += 1) await page.waitForTimeout(100);
    expect(reviews.length === 1 && JSON.parse(reviews[0]).action === "accept", "“Accept anyway” sends the one request", `reviews: ${JSON.stringify(reviews)}`);
    // with no waiting boxes from check screens there is no line and no question
    withFeedback = false;
    reviews.length = 0;
    await openPicturesFresh();
    await page.waitForFunction(() => !document.getElementById("autolabel-review").hidden && document.querySelectorAll("#asset-list .asset-row").length >= 1, null, { timeout: 15000 });
    await page.click("#autolabel-accept");
    for (let i = 0; i < 50 && reviews.length < 1; i += 1) await page.waitForTimeout(100);
    await page.waitForTimeout(300);
    const plain = await page.evaluate(() => ({ dialog: Boolean(document.querySelector("dialog.confirm-dialog")), note: document.getElementById("autolabel-feedback-note").hidden, filter: document.getElementById("asset-filter").hidden }));
    expect(!plain.dialog && plain.note && plain.filter && reviews.length === 1, "with no pictures from check screens, bulk accept is unchanged and there is no filter", `plain: ${JSON.stringify(plain)} reviews ${reviews.length}`);
  } finally {
    allowedStatuses = [];
    await page.unroute(assetsRoute).catch(() => {});
    await page.unroute(autolabelRoute).catch(() => {});
    await page.unroute("**/annotations/review").catch(() => {});
  }
});

await step("pictures part: a picture added with boxes left out says so, and a cut-off list says what to do (STUBBED answers)", async () => {
  const LEFT = { observation_id: "o1", app_name: "Line 4 check", taken_at: "2026-10-23T13:25:58+00:00", model_version: { id: "m", version: 1 }, kinds: { true_positive: 1, false_positive: 0, missed: 1 }, state: "imported", reason: null, message: "", asset_id: "a", boxes_created: 1, missed_without_box: 0, boxes_skipped: 1, skipped_reasons: ["label_not_in_project"] };
  const PEND = { observation_id: "o2", app_name: "Line 4 check", taken_at: "2026-10-23T13:26:58+00:00", model_version: { id: "m", version: 1 }, kinds: { true_positive: 1, false_positive: 0, missed: 0 }, state: "pending", reason: null, message: "" };
  let mode = "new";
  const answer = () => (mode === "new" ? { pending: 1, imported: 1, cannot_import: 0, total: 3000, scanned: 2000, truncated: true, batch_limit: 50, items: [PEND, LEFT] } : { pending: 1, imported: 1, cannot_import: 0, total: 2, batch_limit: 50, items: [PEND, { ...LEFT, boxes_skipped: undefined, skipped_reasons: undefined }] });
  await page.route("**/feedback/corrections", (route) => route.fulfill({ json: answer() }));
  try {
    await openPicturesFresh();
    await page.waitForFunction(() => /waiting/.test(document.getElementById("corrections-count").textContent), null, { timeout: 15000 });
    const got = await page.evaluate(() => ({ count: document.getElementById("corrections-count").textContent, left: [...document.querySelectorAll("#corrections-leftout li")].map((li) => li.textContent), leftShown: !document.getElementById("corrections-leftout").hidden, button: document.getElementById("corrections-import").textContent }));
    expect(got.left.length === 1 && got.left[0] === "Line 4 check, " + got.left[0].split(", ").slice(1).join(", ") && got.left[0].endsWith(": 1 box was left out: its label is not in this project."), "an added picture with a box left out names the picture and says why, in plain words", `left: ${JSON.stringify(got)}`);
    expect(/Showing the newest 2[,.\s\u00a0\u202f]?000; add these first, then press again for more\.$/.test(got.count) && got.button === "Add 1 corrected picture for review", "a cut-off list says to add these first and press again", `count: ${got.count} button ${got.button}`);
    await audit("pictures part with boxes left out and the cut-off line");
    mode = "old";
    await openPicturesFresh();
    await page.waitForFunction(() => /waiting/.test(document.getElementById("corrections-count").textContent), null, { timeout: 15000 });
    const old = await page.evaluate(() => ({ count: document.getElementById("corrections-count").textContent, left: document.querySelectorAll("#corrections-leftout li").length, shown: !document.getElementById("corrections-leftout").hidden }));
    expect(old.left === 0 && !old.shown && !/Showing the newest/.test(old.count), "an answer without these keys shows neither line", `old: ${JSON.stringify(old)}`);
  } finally {
    await page.unroute("**/feedback/corrections").catch(() => {});
  }
});

// ---- W12-G3: corrections findable, honest captions, guarded bulk actions, switching check screens, a worse model ----------
const HOSTILE = "<img src=x onerror=\"window.__xss=1\"> <b>bold</b>";
const SPACED = (text) => String(text).replace(/[\s\u00a0]+/g, " ").trim();
const CORR_ITEM = (id, over = {}) => ({ observation_id: id, app_name: "Line 4 check", taken_at: "2026-10-23T13:25:58+00:00", model_version: { id: "m", version: 1 }, kinds: { true_positive: 1, false_positive: 0, missed: 0 }, state: "pending", reason: null, message: "", decision: null, decision_note: "", ...over });
const CORR_BODY = (over = {}) => ({ pending: 0, imported: 0, cannot_import: 0, total: 0, scanned: 0, truncated: false, batch_limit: 50, items: [], ...over });
const projectRoute = (suffix) => new RegExp(`/api/projects/${opts.projectId}/${suffix}$`);
async function openProjectStart() {
  await go(`${BASE}/#project=${opts.projectId}`);
  await reloadPage();
  await page.waitForFunction((title) => document.getElementById("workspace-title")?.textContent.includes(title), "Smoke test bags");
}

await step("G3 project start: the corrections count is on the Pictures chip and in Get started, its button opens the card above the auto-suggest area (STUBBED count)", async () => {
  let waiting = 11;
  let requests = 0;
  const onRequest = (request) => { if (request.method() === "GET" && /\/feedback\/corrections$/.test(request.url())) requests += 1; };
  page.on("request", onRequest);
  await page.route("**/feedback/corrections", (route) => route.fulfill({ json: waiting ? CORR_BODY({ pending: waiting, total: waiting, scanned: waiting, items: [CORR_ITEM("o1")] }) : CORR_BODY() }));
  try {
    const original = page.viewportSize();
    const widths = [{ width: original?.width || 1280, height: original?.height || 900 }, { width: 390, height: 844 }];
    let progressWaiting = "";
    for (const size of widths) {
      await page.setViewportSize(size);
      waiting = 11; requests = 0;
      await openProjectStart();
      await page.waitForFunction(() => !document.getElementById("jump-corrections").hidden, null, { timeout: 15000 });
      await page.waitForTimeout(1500); // a poll would show up as a second request
      const start = await page.evaluate(() => {
        const chip = document.querySelector('#jump-nav a[data-panel="image-assets-panel"]');
        const row = document.querySelector("#checklist li");
        return { chip: chip.textContent, chipVisible: chip.getBoundingClientRect().width > 0, state: row.dataset.state, text: row.textContent, button: row.querySelector("button")?.textContent || "", progress: document.getElementById("checklist-progress").textContent, intro: document.getElementById("checklist-intro").textContent, inList: document.querySelectorAll("#checklist li").length };
      });
      const label = `@${size.width}px`;
      expect(SPACED(start.chip) === "Pictures · 11 corrections" && start.chipVisible, `${label} the Pictures chip says “Pictures · 11 corrections” in words`, `chip: ${JSON.stringify(start.chip)}`);
      expect(start.state === "attention" && /Corrections waiting/.test(start.text) && /11 waiting/.test(start.text) && /11 pictures with corrections are waiting/.test(start.text) && start.button === "Open the corrections", `${label} Get started starts with a “Corrections waiting” row: the count and a button`, `row: ${JSON.stringify(start)}`);
      expect(!/^Every step is done\./.test(start.intro), `${label} Get started no longer says every step is done while corrections wait`, `intro: ${start.intro}`);
      progressWaiting = start.progress;
      expect(requests === 1, `${label} the count is read once per project open (no polling)`, `requests: ${requests}`);
      await page.click("#checklist .checklist-corrections button");
      await page.waitForFunction(() => document.getElementById("image-assets-panel").classList.contains("is-current"), null, { timeout: 10000 });
      await page.waitForTimeout(900); // the smooth scroll
      const opened = await page.evaluate(() => {
        const card = document.getElementById("corrections-panel");
        const rect = card.getBoundingClientRect();
        const after = (node) => Boolean(node) && Boolean(card.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING);
        const yellow = [...document.querySelectorAll("#image-assets-panel button")].filter((b) => b.checkVisibility() && getComputedStyle(b).backgroundColor === "rgb(245, 196, 0)").map((b) => b.textContent.trim());
        return { focus: document.activeElement?.id, top: rect.top, bottom: rect.bottom, vh: innerHeight, prev: card.previousElementSibling?.id, autolabelAfter: after(document.getElementById("autolabel-panel")), listAfter: after(document.getElementById("asset-list")), yellow,
          steps: [...document.querySelectorAll("#image-assets-panel > .panel-steps button")].map((b) => b.textContent) };
      });
      expect(opened.focus === "corrections-heading" && opened.top >= 0 && opened.top < opened.vh * 0.5 && opened.bottom > 120, `${label} the button opens Pictures and puts the focus and the view on the corrections card`, `opened: ${JSON.stringify(opened)}`);
      expect(opened.prev === "image-upload-form" && opened.autolabelAfter && opened.listAfter, `${label} the card sits above the auto-suggest area and the picture list`, `order: ${JSON.stringify(opened)}`);
      expect(opened.yellow.length === 1 && /Add pictures/.test(opened.yellow[0]), `${label} the part keeps one yellow action`, `yellow: ${JSON.stringify(opened.yellow)}`);
      expect(opened.steps.every((text) => !/correction/i.test(text)) && opened.steps.length > 0, `${label} Back and Next name the parts, not the count`, `steps: ${JSON.stringify(opened.steps)}`);
      await audit(`pictures part with the corrections count ${label}`);
      // nothing waiting: no badge, no row, and the card keeps its old place below the list controls
      waiting = 0;
      await openProjectStart();
      await page.waitForFunction(() => /No corrections yet/.test(document.getElementById("corrections-count").textContent), null, { timeout: 15000 });
      await page.waitForFunction((text) => document.getElementById("checklist-progress").textContent === text, progressWaiting, { timeout: 8000 }).catch(() => {}); // the check screens load after the count
      const none = await page.evaluate(() => ({ chipCount: !document.getElementById("jump-corrections").hidden, row: document.querySelectorAll("#checklist .checklist-corrections").length, progress: document.getElementById("checklist-progress").textContent, next: document.getElementById("corrections-panel").nextElementSibling?.id }));
      expect(!none.chipCount && none.row === 0 && none.next === "asset-status", `${label} with nothing waiting there is no badge, no row, and the card stays where it was`, `none: ${JSON.stringify(none)}`);
      expect(none.progress === progressWaiting, `${label} the extra row is not counted in “N of M steps done”`, `progress ${progressWaiting} vs ${none.progress}`);
    }
    await page.setViewportSize(original);
  } finally {
    page.off("request", onRequest);
    await page.unroute("**/feedback/corrections").catch(() => {});
  }
});

await step("G3 picture editor: a box an operator marked on a check screen is never called the computer's (STUBBED boxes)", async () => {
  const box = (id, over) => ({ id, label: "bag", origin: "model", status: "suggested", confidence: 0.91, model_name: "operator feedback (model v1)", reviewed_at: null, x: 0.1, y: 0.1, width: 0.2, height: 0.2, created_at: "2026-10-24T01:00:00+00:00", updated_at: "2026-10-24T01:00:00+00:00", ...over });
  const boxes = [box("11111111-1111-4111-8111-111111111111", {}), box("22222222-2222-4222-8222-222222222222", { status: "accepted", confidence: null, x: 0.4 }),
    box("33333333-3333-4333-8333-333333333333", { model_name: "auto helper", confidence: 0.62, x: 0.6 }), box("44444444-4444-4444-8444-444444444444", { model_name: "auto helper", status: "accepted", confidence: 0.8, x: 0.1, y: 0.5 })];
  await page.route(/\/api\/projects\/[^/]+\/assets\/[^/]+\/annotations$/, (route) => (route.request().method() === "GET" ? route.fulfill({ json: { annotations: boxes } }) : route.continue()));
  try {
    await openPicturesFresh();
    await page.waitForSelector("#asset-list .asset-annotate", { timeout: 15000 });
    await page.locator("#asset-list .asset-annotate").first().click();
    await page.waitForSelector("#annotation-list .proposal-tag", { timeout: 15000 });
    const got = await page.evaluate(() => ({ tags: [...document.querySelectorAll("#annotation-list .proposal-tag")].map((t) => t.textContent), status: document.getElementById("annotation-status").textContent }));
    expect(JSON.stringify(got.tags) === JSON.stringify([
      "Marked on a check screen by an operator, waiting for your review · computer is 91% sure",
      "Marked on a check screen by an operator, accepted by you",
      "Needs your review · computer is 62% sure",
      "Suggested by computer, accepted by you · computer is 80% sure"]), "each box says who made it: an operator on a check screen, or the computer; waiting or accepted", `tags: ${JSON.stringify(got.tags)}`);
    expect(got.status === "4 boxes in this picture: 2 accepted, 1 marked on a check screen by an operator, waiting for your review, 1 suggested by the computer and waiting for your review.", "the line under the picture counts the two kinds of waiting boxes separately", `status: ${got.status}`);
    await audit("picture editor with the origin captions");
    await page.click("#done-annotation");
  } finally {
    await page.unroute(/\/api\/projects\/[^/]+\/assets\/[^/]+\/annotations$/).catch(() => {});
  }
});

await step("G3 pictures part: a bulk answer is counted before it is asked for, kept on screen, and a reject warns about check-screen boxes (STUBBED counts)", async () => {
  const assetsRoute = projectRoute("assets");
  const autolabelRoute = projectRoute("autolabel");
  const previews = [];
  const reviews = [];
  let rejectFromScreens = 4;
  await page.route(assetsRoute, async (route) => {
    const body = await (await route.fetch()).json();
    body.assets = body.assets.map((asset, index) => (index === 1 ? { ...asset, origin: "operator_feedback", from_observation: "o2", accepted_boxes: 0, pending_boxes: 4 } : index === 0 ? { ...asset, origin: "upload", pending_boxes: 3 } : { ...asset, origin: "upload" }));
    await route.fulfill({ json: body });
  });
  await page.route(autolabelRoute, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    await route.fulfill({ json: { enabled: true, worker_online: true, worker_loading: false, jobs: [], pending_boxes: 7 } });
  });
  await page.route("**/annotations/review", async (route) => {
    const sent = JSON.parse(route.request().postData() || "{}");
    const left = { total: 4, without_score: 4, from_operator_feedback: 4 };
    if (sent.preview) {
      previews.push(sent);
      return route.fulfill({ json: sent.action === "accept"
        ? { updated: 0, pending_boxes: 7, preview: true, would_update: 3, from_operator_feedback: 1, left_for_review: left }
        : { updated: 0, pending_boxes: 7, preview: true, would_update: 7, from_operator_feedback: rejectFromScreens, left_for_review: { total: 0, without_score: 0, from_operator_feedback: 0 } } });
    }
    reviews.push(sent);
    await new Promise((resolve) => setTimeout(resolve, 150));
    await route.fulfill({ json: sent.action === "accept" ? { updated: 3, pending_boxes: 4, preview: false, would_update: 3, from_operator_feedback: 1, left_for_review: left } : { updated: 7, pending_boxes: 0, preview: false, would_update: 7, from_operator_feedback: rejectFromScreens, left_for_review: { total: 0, without_score: 0, from_operator_feedback: 0 } } });
  });
  const asked = () => page.evaluate(() => { const d = document.querySelector("dialog.confirm-dialog[open]"); return d ? { title: d.querySelector("h2").textContent, text: d.querySelector(".confirm-body").innerText, buttons: [...d.querySelectorAll("button")].map((b) => b.textContent), focus: document.activeElement?.textContent } : null; });
  try {
    await openPicturesFresh();
    await page.waitForFunction(() => !document.getElementById("autolabel-review").hidden && document.querySelectorAll("#asset-list .asset-row").length >= 2, null, { timeout: 15000 });
    await page.evaluate(() => { window.__outcomes = []; new MutationObserver(() => window.__outcomes.push(document.getElementById("autolabel-outcome").textContent)).observe(document.getElementById("autolabel-outcome"), { childList: true, characterData: true, subtree: true }); });
    await page.selectOption("#autolabel-accept-threshold", "0");
    await page.click("#autolabel-accept");
    await page.waitForSelector("dialog.confirm-dialog[open]", { timeout: 10000 });
    const accept = await asked();
    expect(previews.length === 1 && previews[0].action === "accept" && previews[0].preview === true && previews[0].min_confidence === 0 && reviews.length === 0, "pressing Accept first asks the server to count, and changes nothing", `previews ${JSON.stringify(previews)} reviews ${reviews.length}`);
    expect(accept.text.includes("3 boxes would be accepted now. 4 would be left for you to check one by one: 4 have no score.") && accept.buttons.join("|") === "Check them first|Accept anyway" && accept.focus === "Check them first", "the question gives the exact numbers, the safe answer first and focused", `dialog: ${JSON.stringify(accept)}`);
    await page.getByRole("button", { name: "Accept anyway" }).click();
    await page.waitForFunction(() => /^Accepted 3\./.test(document.getElementById("autolabel-outcome").textContent), null, { timeout: 10000 });
    await page.waitForFunction(() => /pictures? in this project/.test(document.getElementById("asset-status").textContent), null, { timeout: 10000 });
    await page.waitForTimeout(1200);
    const outcome = await page.evaluate(() => { const el = document.getElementById("autolabel-outcome"); return { text: el.textContent, live: el.getAttribute("aria-live"), role: el.getAttribute("role"), seen: window.__outcomes.filter(Boolean), last: window.__outcomes[window.__outcomes.length - 1], shown: el.getClientRects().length > 0 }; });
    expect(outcome.text === "Accepted 3. Left 4 for you to check one by one: 4 have no score (they were marked on check screens)." && outcome.shown && outcome.live === "polite" && outcome.role === "status", "the outcome names what was accepted and what was left, and why, in a polite live line", `outcome: ${JSON.stringify(outcome)}`);
    expect(outcome.last === outcome.text && outcome.seen.length >= 1, "the outcome is still there after the picture list reloaded under it (nothing wrote over it)", `seen: ${JSON.stringify(outcome)}`);
    await audit("pictures part after a bulk accept");
    // reject: the check-screen warning
    await page.click("#autolabel-reject");
    await page.waitForSelector("dialog.confirm-dialog[open]", { timeout: 10000 });
    const reject = await asked();
    expect(previews[1]?.action === "reject" && previews[1].preview === true && reviews.length === 1, "pressing Reject asks the server to count first, and changes nothing", `previews ${JSON.stringify(previews)} reviews ${reviews.length}`);
    expect(reject.title === "Some of these boxes were marked on check screens" && reject.text.startsWith("4 of these boxes were marked on check screens. Rejecting them removes them for good, and those corrections cannot be added again.") && reject.buttons.join("|") === "Keep them|Reject anyway" && reject.focus === "Keep them", "Reject all warns that check-screen boxes cannot be asked for again, with “Keep them” focused", `dialog: ${JSON.stringify(reject)}`);
    await audit("reject question about check-screen boxes");
    await page.getByRole("button", { name: "Keep them" }).click();
    await page.waitForFunction(() => !document.querySelector("dialog.confirm-dialog"));
    expect(reviews.length === 1, "“Keep them” sends nothing", `reviews ${reviews.length}`);
    await page.click("#autolabel-reject");
    await page.waitForSelector("dialog.confirm-dialog[open]");
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.querySelector("dialog.confirm-dialog"));
    expect(reviews.length === 1, "Escape sends nothing", `reviews ${reviews.length}`);
    await page.click("#autolabel-reject");
    await page.waitForSelector("dialog.confirm-dialog[open]");
    await page.getByRole("button", { name: "Reject anyway" }).click();
    await page.waitForFunction(() => /^Rejected 7 suggested boxes\./.test(document.getElementById("autolabel-outcome").textContent), null, { timeout: 10000 });
    expect(reviews.length === 2 && reviews[1].action === "reject", "“Reject anyway” sends the one request and its outcome stays on screen", `reviews: ${JSON.stringify(reviews)}`);
    // with no check-screen boxes in the count, the older question is unchanged
    rejectFromScreens = 0;
    await page.click("#autolabel-reject");
    await page.waitForSelector("dialog.confirm-dialog[open]");
    const plain = await asked();
    expect(plain.title === "Reject every suggested box?" && plain.buttons.join("|") === "Keep the suggestions|Reject all suggestions", "with none from check screens the older question stays as it was", `dialog: ${JSON.stringify(plain)}`);
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.querySelector("dialog.confirm-dialog"));
  } finally {
    await page.unroute(assetsRoute).catch(() => {});
    await page.unroute(autolabelRoute).catch(() => {});
    await page.unroute("**/annotations/review").catch(() => {});
  }
});

await step("G3 check screen: one plain line says where an answer goes, and the wrong-box line only follows a wrong answer (STUBBED result)", async () => {
  const appId = await appIdFor(opts.slug);
  const preview = (await page.evaluate(async ([project, id]) => (await (await fetch(`/api/projects/${project}/inference/apps/${id}/preview`)).json()).preview, [opts.projectId, appId]));
  const OBS = "00000000-0000-4000-8000-0000000000b2";
  let decision = null;
  const render = () => ({ render: { ...preview, result: {
    observation_id: OBS, status: "succeeded", state: "needs_review", status_text: "Please look closely", awaiting_human_decision: true, machine_state: "flagged",
    detections: [{ index: 0, label: "damaged", score: 0.95, box: { x: 0.1, y: 0.1, width: 0.3, height: 0.3 } }, { index: 1, label: "damaged", score: 0.6, box: { x: 0.5, y: 0.1, width: 0.3, height: 0.3 } }],
    hidden_below_threshold: 0, hidden_other_labels: 0, decision, result_rejected: false, feedback: [],
    image: { sha256: "x", width: 1, height: 1, content_type: "image/png", filename: "p.png" }, created_at: new Date().toISOString(), created_by: "u1", error: "" } } });
  const posts = [];
  await page.route("**/inference/apps/*/run", (route) => route.fulfill({ status: 202, json: { observation_id: OBS, worker_online: true } }));
  await page.route(`**/inference/apps/*/observations/${OBS}`, (route) => route.fulfill({ json: render() }));
  await page.route(`**/inference/apps/*/observations/${OBS}/image`, (route) => route.fulfill({ contentType: "image/png", body: TINY_PNG }));
  await page.route(`**/inference/apps/*/observations/${OBS}/feedback`, (route) => { posts.push(route.request().postData()); route.fulfill({ status: 201, json: { ok: true } }); });
  await page.route(`**/inference/apps/*/observations/${OBS}/decision`, (route) => { const sent = JSON.parse(route.request().postData() || "{}"); posts.push(route.request().postData()); decision = { decision: sent.decision, at: new Date().toISOString(), note: sent.note || "" }; route.fulfill({ json: render() }); });
  const SAVED = "Saved for your supervisor. It does not change the computer by itself.";
  const WRONG = "A wrong-box answer cannot teach the computer anything on its own; drawing a box where something was missed can.";
  try {
    await go(`${BASE}/check/${opts.slug}`);
    await page.waitForFunction(() => (document.getElementById("cs-title")?.textContent || "").trim().length > 0, null, { timeout: 15000 });
    await page.setInputFiles("#cs-file", { name: "p.png", mimeType: "image/png", buffer: TINY_PNG });
    await page.click("#cs-check");
    await page.waitForSelector("#cs-detections li", { timeout: 15000 });
    const text = (selector) => page.evaluate((s) => document.querySelector(s).textContent, selector);
    // wrong
    await page.locator("#cs-detections li").nth(0).getByRole("button", { name: /^This is wrong/ }).click();
    await page.locator("#cs-wrong-note-0").fill("a shadow, not damage");
    await page.getByRole("button", { name: "Save: this is wrong" }).first().click();
    await page.waitForFunction(() => /Saved: you said this is wrong/.test(document.getElementById("cs-det-msg-0").textContent), null, { timeout: 10000 });
    expect(await text("#cs-det-msg-0") === `Saved: you said this is wrong. ${SAVED} ${WRONG}`, "a wrong finding: one plain line, then the wrong-box line", `wrong: ${await text("#cs-det-msg-0")}`);
    // right
    await page.locator("#cs-detections li").nth(1).getByRole("button", { name: /^This is right/ }).click();
    await page.waitForFunction(() => /Saved: you said this is right/.test(document.getElementById("cs-det-msg-1").textContent), null, { timeout: 10000 });
    expect(await text("#cs-det-msg-1") === `Saved: you said this is right. ${SAVED}`, "a right finding: the plain line only", `right: ${await text("#cs-det-msg-1")}`);
    // missed, no box
    await page.click("#cs-missed summary");
    await page.check("#cs-box-unknown");
    await page.fill("#cs-missed-note", "a dent on the left");
    await page.click("#cs-missed-save");
    await page.waitForFunction(() => /Saved: the computer missed this/.test(document.getElementById("cs-missed-msg").textContent), null, { timeout: 10000 });
    expect(await text("#cs-missed-msg") === `Saved: the computer missed this. ${SAVED} You can add another.`, "a missed object: the plain line, and the invitation to add another", `missed: ${await text("#cs-missed-msg")}`);
    expect(/Saved for your supervisor\./.test(await text("#cs-missed-list")) && !/later review/.test(await text("#cs-missed-list")), "the list of missed objects says the same", `list: ${await text("#cs-missed-list")}`);
    // the whole result
    await page.fill("#cs-note", "not damage at all");
    await page.locator('button[data-decision="reject"]').click();
    await page.waitForFunction(() => /Saved for your supervisor/.test(document.getElementById("cs-decision-msg").textContent), null, { timeout: 10000 });
    expect(await text("#cs-decision-msg") === `${SAVED} ${WRONG}`, "a rejected result: the plain line, then the wrong-box line", `reject: ${await text("#cs-decision-msg")}`);
    await page.locator('button[data-decision="confirm"]').click();
    await page.waitForFunction(() => document.getElementById("cs-decision-msg").textContent === "", null, { timeout: 10000 });
    expect(posts.length === 5, "five answers were sent, one each", `posts: ${posts.length}`);
    const banner = await page.evaluate(() => document.getElementById("cs-feedback-note").innerText);
    expect(/About your right\/wrong answers/.test(banner) && /not change the computer model/.test(banner), "the older note at the bottom of the result stays", `banner: ${banner}`);
    for (const size of [{ width: 390, height: 844 }, { width: 1280, height: 900 }]) {
      await page.setViewportSize(size);
      const fit = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: innerWidth, right: Math.max(...["cs-det-msg-0", "cs-det-msg-1", "cs-missed-msg", "cs-decision-msg"].map((id) => document.getElementById(id).getBoundingClientRect().right)), msgHeight: document.getElementById("cs-det-msg-0").getBoundingClientRect().height }));
      expect(fit.scroll <= fit.inner + 1 && fit.right <= fit.inner + 1, `@${size.width}px the saved lines fit without sideways scrolling`, `fit: ${JSON.stringify(fit)}`);
    }
    await audit("operator screen with the saved lines");
  } finally {
    for (const pattern of ["**/inference/apps/*/run", `**/inference/apps/*/observations/${OBS}`, `**/inference/apps/*/observations/${OBS}/image`, `**/inference/apps/*/observations/${OBS}/feedback`, `**/inference/apps/*/observations/${OBS}/decision`]) await page.unroute(pattern).catch(() => {});
  }
});

await step("G3 pictures part: a picture from a rejected result says so and shows the note as text; the import answer, the cut-off line and the left-out boxes use the server's words (STUBBED answers)", async () => {
  const ids = await page.evaluate(async (id) => (await (await fetch(`/api/projects/${id}/assets`)).json()).assets.slice(0, 3).map((a) => a.id), opts.projectId);
  const longNote = `${HOSTILE} ${"x".repeat(400)}`;
  const items = [
    CORR_ITEM("o1", { state: "imported", asset_id: ids[0], decision: "reject", decision_note: longNote, boxes_created: 0, missed_without_box: 0, boxes_skipped: 0, skipped_reasons: [] }),
    CORR_ITEM("o2", { state: "imported", asset_id: ids[1], decision: "second_look", decision_note: "", boxes_created: 0, boxes_skipped: 0, skipped_reasons: [] }),
    CORR_ITEM("o3", { state: "imported", asset_id: ids[2], decision: null, boxes_created: 0, boxes_skipped: 0, skipped_reasons: [] })];
  let mode = "scanned";
  const answer = () => CORR_BODY({ pending: 1, imported: 3, total: 2000, truncated: true, scanned: mode === "scanned" ? 900 : undefined, cannot_import_scanned: mode === "older" ? 700 : mode === "scanned" ? 900 : undefined, items: [CORR_ITEM("o9"), ...items] });
  const SERVER_SENTENCE = "The box does not stay inside the picture, so no box was made. (server wording)";
  const posts = [];
  await page.route("**/feedback/corrections", (route) => route.fulfill({ json: answer() }));
  await page.route("**/feedback/corrections/import", (route) => {
    posts.push(route.request().postData());
    return route.fulfill({ json: { imported: [{ observation_id: "o9", asset_id: ids[0], boxes_created: 0, missed_without_box: 0, false_alarms: 0, decision: "reject", decision_note: "x", skipped_boxes: [{ reason: "box_outside_image", message: SERVER_SENTENCE }, { reason: "box_outside_image", message: SERVER_SENTENCE }] }],
      skipped: [], totals: { requested: 1, imported: 1, skipped: 0, boxes_created: 0, pictures_from_decisions: 1, missed_without_box: 0, remaining_pending: 0 } } });
  });
  await page.route(projectRoute("assets"), async (route) => {
    const body = await (await route.fetch()).json();
    body.assets = body.assets.map((asset) => (ids.includes(asset.id) ? { ...asset, origin: "operator_feedback", from_observation: "o", accepted_boxes: 0, pending_boxes: 0 } : asset));
    await route.fulfill({ json: body });
  });
  try {
    await openPicturesFresh();
    await page.waitForFunction(() => document.querySelectorAll("#asset-list .asset-decision > p").length >= 3, null, { timeout: 15000 });
    const got = await page.evaluate(([firstId, secondId, thirdId]) => {
      const slot = (id) => [...document.querySelectorAll("#asset-list .asset-row")].find((row) => row.dataset.assetId === id);
      const rows = [firstId, secondId, thirdId].map((id) => { const row = slot(id); return { nothing: [...row.querySelectorAll(".asset-nothing")].map((p) => p.textContent), note: row.querySelector(".asset-decision-note")?.textContent || "", images: row.querySelectorAll(".asset-decision img, .asset-decision b").length }; });
      return { rows, xss: window.__xss ?? null, count: document.getElementById("corrections-count").textContent };
    }, ids);
    const NOTHING = "This picture was marked wrong but nothing was marked as missed, so there is no box to teach from. Draw a box if something should be found here, or leave it.";
    expect(got.rows[0].nothing[0] === NOTHING && got.rows[0].nothing[1] === "An operator rejected the whole result for this picture." && got.rows[0].note.startsWith("Operator’s note: <img src=x onerror=\"window.__xss=1\"> <b>bold</b>") && got.rows[0].note.endsWith("…") && got.rows[0].note.length <= "Operator’s note: ".length + 301, "a rejected picture says so and shows the operator's note as plain text, cut at 300 characters", `row: ${JSON.stringify(got.rows[0]).slice(0, 400)}`);
    expect(got.rows[0].images === 0 && got.xss === null, "the note is text: no element was made from it and nothing ran", `images ${got.rows[0].images} xss ${got.xss}`);
    expect(got.rows[1].nothing[0] === NOTHING && got.rows[1].nothing[1] === "An operator asked for a second look at the result for this picture." && got.rows[1].note === "", "a second-look picture says so, with no note line when there is no note", `row: ${JSON.stringify(got.rows[1])}`);
    expect(got.rows[2].nothing.length === 1 && got.rows[2].nothing[0] === NOTHING && got.rows[2].note === "", "a false-alarm-only picture gets the new sentence and nothing more", `row: ${JSON.stringify(got.rows[2])}`);
    expect(/Showing the newest 900; add these first/.test(got.count), "the cut-off line names the number the server scanned", `count: ${got.count}`);
    expect(!/2000|2,000/.test(got.count), "it does not print the total as “the newest N”", `count: ${got.count}`);
    mode = "older";
    await openPicturesFresh();
    await page.waitForFunction(() => /Showing the newest/.test(document.getElementById("corrections-count").textContent), null, { timeout: 15000 });
    expect(/Showing the newest 700; add these first/.test(await page.locator("#corrections-count").textContent()), "an older name of the same number is used when it is the only one", "no scanned count");
    mode = "none";
    await openPicturesFresh();
    await page.waitForFunction(() => /Showing the newest/.test(document.getElementById("corrections-count").textContent), null, { timeout: 15000 });
    expect(/Showing the newest ones; add these first/.test(await page.locator("#corrections-count").textContent()), "with no number at all it says “the newest ones”", `count: ${await page.locator("#corrections-count").textContent()}`);
    // the import answer
    mode = "scanned";
    await openPicturesFresh();
    await page.waitForFunction(() => !document.getElementById("corrections-import").hidden, null, { timeout: 15000 });
    await page.click("#corrections-import");
    await page.waitForFunction(() => /Added 1 picture/.test(document.getElementById("corrections-result").textContent), null, { timeout: 15000 });
    const result = await page.evaluate(() => ({ text: document.getElementById("corrections-result").innerText, left: [...document.querySelectorAll("#corrections-result .corrections-leftout li")].map((li) => li.textContent) }));
    expect(/1 picture was added only because an operator rejected the whole result or asked for a second look\. They have no boxes, so there is nothing to teach from yet\./.test(result.text) && !/1 picture has no boxes/.test(result.text), "the import answer names pictures that came in only because of a decision, once", `result: ${result.text}`);
    expect(result.left.length === 1 && result.left[0].endsWith(`: 2 boxes were left out. ${SERVER_SENTENCE}`), "the left-out boxes keep the short lead and add the server's sentence word for word, once", `left: ${JSON.stringify(result.left)}`);
    await audit("pictures part with a rejected picture and the import answer");
    await fieldSizesAtWidths("pictures part with a rejected picture and the import answer");
  } finally {
    await page.unroute("**/feedback/corrections").catch(() => {});
    await page.unroute("**/feedback/corrections/import").catch(() => {});
    await page.unroute(projectRoute("assets")).catch(() => {});
  }
});

const MODEL_PATH = new RegExp(`/api/projects/${"[0-9a-f-]{36}"}/models/[0-9a-f-]{36}$`);
await step("G3 approve dialog: the check screens that use the model being replaced are named, switching is a tick that starts on, and the answer says what was switched (STUBBED answers)", async () => {
  const a0 = await firstPictureId();
  const models = await page.evaluate(async (id) => (await (await fetch(`/api/projects/${id}/models`)).json()).models, opts.projectId);
  const staged = models.find((m) => m.stage === "staged");
  expect(Boolean(staged), "the seed has a model ready for trial", "no staged model in the seed");
  let wouldRepoint = [{ app_id: "a1", name: "Line 4 check", can_repoint: true }];
  let withDetail = true;
  const approvals = [];
  await page.route("**/models/*/results/plain", (route) => route.fulfill({ json: stubPlainBody(a0) }));
  await page.route(MODEL_PATH, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const body = await (await route.fetch()).json();
    if (body.model.id === staged.id) { if (withDetail) body.model.would_repoint = wouldRepoint; else delete body.model.would_repoint; } // an older server does not send the key
    await route.fulfill({ json: body });
  });
  await page.route("**/models/*/approve", async (route) => {
    approvals.push(JSON.parse(route.request().postData() || "{}"));
    await route.fulfill({ json: { model: { id: staged.id, version: staged.version, stage: "staged" }, repointed: approvals.length === 1 ? [{ app_id: "a1", name: "Line 4 check" }] : [], repoint_skipped: [{ app_id: "a2", name: "Line 5 check", reason: "It checks different labels." }], apps_still_on_retired_model: [] } });
  });
  const open = async () => {
    await openTeachFresh();
    const button = page.locator('button[aria-label^="Approve Model"]:not([disabled])').first();
    await button.scrollIntoViewIfNeeded();
    await button.click();
    await page.waitForSelector("#decision-dialog[open]");
    await page.waitForTimeout(150);
  };
  const state = () => page.evaluate(() => { const body = document.getElementById("decision-body"); const rect = document.getElementById("decision-dialog").getBoundingClientRect(); return {
    text: body.innerText, tickShown: !document.getElementById("decision-repoint-row").hidden, tickText: document.getElementById("decision-repoint-text").textContent, ticked: document.getElementById("decision-repoint").checked,
    blocked: [...body.querySelectorAll(".decision-blocked li")].map((li) => li.textContent), images: body.querySelectorAll("img, b").length, fits: rect.top >= -1 && rect.bottom <= innerHeight + 1, firstScreens: body.querySelector(".decision-screens")?.textContent || "" }; });
  try {
    // one screen that can be switched
    await open();
    let got = await state();
    expect(got.firstScreens === "1 check screen uses the model being replaced: Line 4 check. If you do nothing it stops working until someone switches it.", "the dialog names the check screen that stops working if nothing is done", `screens: ${got.firstScreens}`);
    expect(got.tickShown && got.tickText === "Switch this check screen to the new model now" && got.ticked, "the tick is shown and starts on when every screen can be switched", `tick: ${JSON.stringify(got)}`);
    expect(!/exact model they were set up with/.test(got.text) && /Apart from that, approving changes nothing else/.test(got.text), "the old sentence that said screens keep their model is gone", `text: ${got.text}`);
    expect(got.fits, "the dialog still fits the window", "the dialog does not fit the window");
    await audit("approve dialog with the switch tick");
    await page.fill("#decision-note", "Smoke test note.");
    await page.click("#decision-confirm");
    await page.waitForFunction(() => /Switched 1 check screen/.test(document.getElementById("tr-models-flash").textContent), null, { timeout: 15000 });
    expect(approvals[0].repoint_apps === true && approvals[0].note === "Smoke test note.", "the request carries repoint_apps: true when the tick is on", `approval: ${JSON.stringify(approvals)}`);
    const flash = await page.evaluate(() => ({ text: document.getElementById("tr-models-flash").textContent, shown: document.getElementById("tr-models-flash").getClientRects().length > 0, live: document.getElementById("tr-models-flash").getAttribute("role") }));
    expect(flash.text === "Model 2 is now Approved. Switched 1 check screen. Left alone: Line 5 check (It checks different labels.)." && flash.shown && flash.live === "status", "the answer says what was switched and what was left alone, in a status line", `flash: ${JSON.stringify(flash)}`);
    await audit("models after approval with the switch answer");
    // the tick off: nothing is asked for
    await open();
    await page.uncheck("#decision-repoint");
    await page.fill("#decision-note", "Smoke test note.");
    await page.click("#decision-confirm");
    for (let i = 0; i < 50 && approvals.length < 2; i += 1) await page.waitForTimeout(100);
    expect(approvals.length === 2 && !("repoint_apps" in approvals[1]), "with the tick off the request does not ask for a switch", `approvals: ${JSON.stringify(approvals)}`);
    // one that cannot be switched: listed with its reason, the tick starts off
    wouldRepoint = [{ app_id: "a1", name: "Line 4 check", can_repoint: true }, { app_id: "a2", name: HOSTILE, can_repoint: false, reason: "It checks different labels." }];
    await open();
    got = await state();
    expect(got.blocked.length === 1 && got.blocked[0] === `${HOSTILE}: It checks different labels. It will not be switched.` && got.tickText === "Switch this check screen to the new model now" && !got.ticked, "a screen that cannot be switched is listed with its reason, and the tick starts off", `mixed: ${JSON.stringify(got)}`);
    expect(got.images === 0 && (await page.evaluate(() => window.__xss ?? null)) === null, "names from the server are text, never markup", `images ${got.images}`);
    await audit("approve dialog with a screen that cannot be switched");
    await page.click("#decision-cancel");
    await page.waitForFunction(() => !document.getElementById("decision-dialog").open);
    // nothing can be switched: no tick at all
    wouldRepoint = [{ app_id: "a2", name: "Line 5 check", can_repoint: false, reason: "Its model file is missing." }];
    await open();
    got = await state();
    expect(!got.tickShown && got.blocked.length === 1, "when nothing can be switched there is no tick, only the reason", `none: ${JSON.stringify(got)}`);
    await page.click("#decision-cancel");
    await page.waitForFunction(() => !document.getElementById("decision-dialog").open);
    // an older server: the screens are named from the model list and nothing is offered
    withDetail = false;
    await open();
    got = await state();
    expect(!got.tickShown && /check screens? use(s)? the model being replaced/.test(got.text) && /Switching is not done from here/.test(got.text), "without the server's list the dialog still names the screens and offers no switch that might not exist", `old: ${JSON.stringify(got)}`);
    await page.click("#decision-cancel");
    await page.waitForFunction(() => !document.getElementById("decision-dialog").open);
  } finally {
    await page.unroute("**/models/*/results/plain").catch(() => {});
    await page.unroute(MODEL_PATH).catch(() => {});
    await page.unroute("**/models/*/approve").catch(() => {});
  }
});

await step("G3 results card and approve dialog: a model that did worse than the one in use is not called good (STUBBED comparison)", async () => {
  const a0 = await firstPictureId();
  let kind = "worse";
  const approvals = [];
  const WEAK = "This model did worse than the model in use on the same test pictures. It found 62 of 90 test boxes and the model in use found 71.";
  await page.route("**/models/*/results/plain", async (route) => {
    const body = stubPlainBody(a0);
    body.overall = { ...body.overall, verdict: "good_enough_to_try", found: 62, of: 90, headline: `Good enough to try: it found 62 of 90 test boxes (68%). ${NOT_FINAL}` };
    body.comparison = stubComparison(kind);
    await route.fulfill({ json: body });
  });
  await page.route("**/models/*/approve", async (route) => {
    const sent = JSON.parse(route.request().postData() || "{}");
    approvals.push(sent);
    if (kind === "weak409" && sent.acknowledge_weak_results !== true) return route.fulfill({ status: 409, json: { error: "Nothing was changed.", needs_acknowledgement: true, weak_results: WEAK } });
    return route.fulfill({ status: 422, json: { error: "Stubbed: the smoke test stops here and approves nothing." } });
  });
  allowedStatuses = [409, 422];
  try {
    await openTeachFresh();
    await page.locator('#tr-model-list button[aria-label^="Show results for"]').first().click();
    await page.waitForSelector("#tr-model-list .model-results:not([hidden]) .result-not-final", { timeout: 15000 });
    const worse = await page.evaluate(() => {
      const root = document.querySelector("#tr-model-list .model-results:not([hidden])");
      const headline = root.querySelector(".result-headline");
      const line = root.querySelector(".result-worse");
      const colour = (el) => getComputedStyle(el).backgroundColor;
      return { headline: headline.textContent, state: headline.dataset.state || "", headlineBg: colour(headline), next: headline.nextElementSibling?.className, line: line?.textContent || "", lineBg: line ? colour(line) : "", compare: root.querySelector(".compare-verdict")?.textContent.trim() || "" };
    });
    expect(/^Good enough to try/.test(worse.headline) && worse.state === "" && worse.headlineBg !== "rgba(111, 209, 154, 0.12)" && worse.next === "result-worse", "the headline is not in the green style, and the amber line comes directly under it", `worse: ${JSON.stringify(worse)}`);
    expect(/Worse than the model in use on the same test pictures\./.test(worse.line) && worse.lineBg === "rgba(245, 196, 0, 0.12)" && /Worse$/.test(worse.compare), "the line says it in words, in the amber (yellow) tint, next to the Worse word", `line: ${JSON.stringify(worse)}`);
    await audit("results card for a worse model");
    await fieldSizesAtWidths("results card for a worse model");
    // approve: the tick comes with the dialog, comparison first
    const button = page.locator('button[aria-label^="Approve Model"]:not([disabled])').first();
    await button.scrollIntoViewIfNeeded();
    await button.click();
    await page.waitForSelector("#decision-dialog[open]");
    const dialog = await page.evaluate(() => ({ first: document.getElementById("decision-body").firstElementChild?.className || "", verdict: document.getElementById("decision-body").firstElementChild?.dataset.verdict || "", headlineState: document.querySelector("#decision-body .result-headline")?.dataset.state || "", tick: !document.getElementById("decision-ack-row").hidden, tickText: document.getElementById("decision-ack-text").textContent, confirmDisabled: document.getElementById("decision-confirm").disabled, fits: (() => { const r = document.getElementById("decision-dialog").getBoundingClientRect(); return r.top >= -1 && r.bottom <= innerHeight + 1; })() }));
    expect(dialog.first.includes("compare-in-dialog") && dialog.verdict === "worse" && dialog.headlineState === "" && dialog.tick && /did worse than the model in use on the same test pictures/.test(dialog.tickText), "the approve dialog puts the comparison first, asks for the tick, and does not show the green style", `dialog: ${JSON.stringify(dialog)}`);
    await page.fill("#decision-note", "Smoke test note.");
    expect((await page.evaluate(() => document.getElementById("decision-confirm").disabled)), "with a note but no tick, Approve for use stays off", "confirm switched on without the tick");
    await page.check("#decision-ack");
    await audit("approve dialog for a worse model");
    await page.click("#decision-confirm");
    await page.waitForFunction(() => /smoke test stops here/.test(document.getElementById("decision-error").textContent), null, { timeout: 10000 });
    expect(approvals.length === 1 && approvals[0].acknowledge_weak_results === true, "the request carries acknowledge_weak_results: true", `approvals: ${JSON.stringify(approvals)}`);
    await page.click("#decision-cancel");
    await page.waitForFunction(() => !document.getElementById("decision-dialog").open);
    // better and about the same: unchanged
    for (const next of ["better", "same"]) {
      kind = next;
      await openTeachFresh();
      await page.locator('#tr-model-list button[aria-label^="Show results for"]').first().click();
      await page.waitForSelector("#tr-model-list .model-results:not([hidden]) .result-not-final", { timeout: 15000 });
      const plain = await page.evaluate(() => ({ state: document.querySelector("#tr-model-list .model-results:not([hidden]) .result-headline").dataset.state, worse: document.querySelectorAll("#tr-model-list .result-worse").length }));
      expect(plain.state === "ok" && plain.worse === 0, `(${next}) the green headline and no amber line, as before`, `${next}: ${JSON.stringify(plain)}`);
    }
    // the server's own sentence when it asks for the tick after the fact (a dialog opened without the comparison)
    kind = "weak409";
    approvals.length = 0;
    await openTeachFresh();
    const again = page.locator('button[aria-label^="Approve Model"]:not([disabled])').first();
    await again.scrollIntoViewIfNeeded();
    await again.click();
    await page.waitForSelector("#decision-dialog[open]");
    await page.fill("#decision-note", "Smoke test note.");
    await page.click("#decision-confirm");
    await page.waitForFunction(() => !document.getElementById("decision-ack-row").hidden, null, { timeout: 10000 });
    const revealed = await page.evaluate(() => ({ error: document.getElementById("decision-error").textContent, disabled: document.getElementById("decision-confirm").disabled, focus: document.activeElement?.id }));
    expect(revealed.error.includes(WEAK) && revealed.disabled && revealed.focus === "decision-ack" && !/acknowledge_weak_results|API/.test(revealed.error), "a 409 shows the server's sentence, reveals the tick and puts the focus on it", `after the 409: ${JSON.stringify(revealed)}`);
    await page.click("#decision-cancel");
    await page.waitForFunction(() => !document.getElementById("decision-dialog").open);
  } finally {
    allowedStatuses = [];
    await page.unroute("**/models/*/results/plain").catch(() => {});
    await page.unroute("**/models/*/approve").catch(() => {});
  }
});

await step("G3 results card: percentages are rounded down, never up (STUBBED numbers)", async () => {
  await page.route(projectRoute("models"), async (route) => {
    const body = await (await route.fetch()).json();
    body.models = body.models.map((model) => (model.evaluation && model.evaluation.available ? { ...model, evaluation: { ...model.evaluation, recall: 0.996, precision: 0.504 } } : model));
    await route.fulfill({ json: body });
  });
  try {
    await openTeachFresh();
    const summaries = await page.evaluate(() => [...document.querySelectorAll("#tr-model-list .model-summary")].map((p) => p.textContent).filter((text) => /found \d+%/.test(text)));
    expect(summaries.length > 0 && summaries.every((text) => /found 99% of the boxes people drew, and was right 50% of the time/.test(text)), "99.6% reads 99%, not 100%, and 50.4% reads 50%", `summaries: ${JSON.stringify(summaries)}`);
  } finally {
    await page.unroute(projectRoute("models")).catch(() => {});
  }
});

async function appIdFor(slug) {
  return page.evaluate(async ([project, wanted]) => (await (await fetch(`/api/projects/${project}/inference/apps`)).json()).apps.find((a) => a.slug === wanted)?.id, [opts.projectId, slug]);
}
const MEASURED_WORDING = { measured: true, note: "Measured on the Final test pictures only, not on new cameras, lighting or products. The score is the computer's own number, not a probability. A person decides.", bands: [
  { low: 0, high: 0.5, label: "right about 1 in 10 times in our check", precision: 0.1, n: 300, enough: true },
  { low: 0.5, high: 0.8, label: "too few examples to say", precision: 0.44, n: 9, enough: false },
  { low: 0.8, high: 1, label: "right about 9 in 10 times in our check", precision: 0.91, n: 400, enough: true }] };
const UNMEASURED_WORDING = { measured: false, bands: [], note: "Scores are the computer's own numbers; they have not been checked against pictures it has not seen." };

await step("check screen: scores are worded from what was measured, or shown as the computer's own number (STUBBED result)", async () => {
  const appId = await appIdFor(opts.slug);
  const preview = (await page.evaluate(async ([project, id]) => (await (await fetch(`/api/projects/${project}/inference/apps/${id}/preview`)).json()).preview, [opts.projectId, appId]));
  const OBS = "00000000-0000-4000-8000-0000000000a1";
  let wording = MEASURED_WORDING;
  const render = () => ({ render: { ...preview, confidence_wording: wording, result: {
    observation_id: OBS, status: "succeeded", state: "needs_review", status_text: "Please look closely", awaiting_human_decision: true, machine_state: "flagged",
    detections: [
      { index: 0, label: "damaged", score: 0.95, box: { x: 0.1, y: 0.1, width: 0.3, height: 0.3 } },
      { index: 1, label: "damaged", score: 0.6, box: { x: 0.5, y: 0.1, width: 0.3, height: 0.3 } },
      { index: 2, label: "damaged", score: 0.8, box: { x: 0.2, y: 0.55, width: 0.3, height: 0.3 } }],
    hidden_below_threshold: 0, hidden_other_labels: 0, decision: null, result_rejected: false, feedback: [],
    image: { sha256: "x", width: 1, height: 1, content_type: "image/png", filename: "p.png" }, created_at: new Date().toISOString(), created_by: "u1", error: "" } } });
  await page.route("**/inference/apps/*/run", (route) => route.fulfill({ status: 202, json: { observation_id: OBS, worker_online: true } }));
  await page.route(`**/inference/apps/*/observations/${OBS}`, (route) => route.fulfill({ json: render() }));
  await page.route(`**/inference/apps/*/observations/${OBS}/image`, (route) => route.fulfill({ contentType: "image/png", body: TINY_PNG }));
  try {
    for (const next of ["measured", "unmeasured"]) {
      wording = next === "measured" ? MEASURED_WORDING : UNMEASURED_WORDING;
      await go(`${BASE}/check/${opts.slug}`);
      await page.waitForFunction(() => (document.getElementById("cs-title")?.textContent || "").trim().length > 0, null, { timeout: 15000 });
      await page.setInputFiles("#cs-file", { name: "p.png", mimeType: "image/png", buffer: TINY_PNG });
      await page.click("#cs-check");
      await page.waitForSelector("#cs-detections li", { timeout: 15000 });
      const got = await page.evaluate(() => ({ rows: [...document.querySelectorAll("#cs-detections .cs-det-sure")].map((e) => e.textContent), note: document.getElementById("cs-hidden-note").textContent, banner: document.querySelector(".cs-not-final")?.textContent || "", page: document.body.innerText }));
      expect(!/Very sure|Fairly sure|Somewhat sure|Not very sure/.test(got.page), `(${next}) none of the old fixed sureness words appear`, `old words found: ${got.page.slice(0, 200)}`);
      expect(/Not a final decision/.test(got.banner), `(${next}) the “not a final decision” line stays`, `banner: ${got.banner}`);
      if (next === "measured") {
        expect(got.rows[0] === "Right about 9 in 10 times in our check · score 0.95" && got.rows[1] === "score 0.60 · not enough checking to say how often this is right" && got.rows[2] === "Right about 9 in 10 times in our check · score 0.80", "each score is shown with its range's measured words; a thin range says there is not enough checking (edges: 0.8 belongs to the upper range)", `rows: ${JSON.stringify(got.rows)}`);
        expect((got.note.match(/Measured on the Final test pictures only/g) || []).length === 1, "the server's note is shown once", `note: ${got.note}`);
        await audit("operator screen with measured wording");
      } else {
        expect(got.rows.every((row) => /^score 0\.\d\d \(the computer’s own number, not checked\)$/.test(row)) && got.rows.length === 3, "with nothing measured each score is the computer's own number, not checked", `rows: ${JSON.stringify(got.rows)}`);
        expect((got.note.match(/Scores are the computer's own numbers/g) || []).length === 1, "the server's note is shown once", `note: ${got.note}`);
      }
    }
  } finally {
    await page.unroute("**/inference/apps/*/run").catch(() => {});
    await page.unroute(`**/inference/apps/*/observations/${OBS}`).catch(() => {});
    await page.unroute(`**/inference/apps/*/observations/${OBS}/image`).catch(() => {});
  }
});

await step("check-screen builder: a suggested setting is offered only when it is measured, and never applied by itself (STUBBED preview)", async () => {
  const appId = await appIdFor(opts.slug);
  let hint = { value: 0.235, target_recall: 0.8, basis: "validation split", enough_data: true, plain: "A cut-off of 0.235 found 80% of the 630 boxes on the Checking pictures. A person chooses the setting." };
  const writes = [];
  const onRequest = (request) => { if (request.method() !== "GET" && /\/inference\/apps/.test(request.url())) writes.push(`${request.method()} ${request.url().replace(BASE, "")}`); };
  page.on("request", onRequest);
  await page.route(`**/inference/apps/${appId}/preview`, async (route) => { const body = await (await route.fetch()).json(); body.preview.suggested_threshold = hint; await route.fulfill({ json: body }); });
  try {
    for (const next of ["usable", "thin", "none"]) {
      if (next === "thin") hint = { value: null, target_recall: 0.8, basis: "validation split", enough_data: false, plain: "Too few checking boxes to choose a cut-off you can trust: 16 drawn boxes on the Checking pictures, and at least 400 are needed." };
      if (next === "none") hint = { value: null, target_recall: null, basis: "not measured", enough_data: false, plain: "No cut-off is suggested: How often each score was right is not measured for this model." };
      await go(`${BASE}/projects/${opts.projectId}/check-screen/setup`);
      await page.waitForFunction(() => /cut-off/.test(document.getElementById("cs-suggest-text")?.textContent || ""), null, { timeout: 15000 }); // "Save the screen first" shows until the preview arrives
      const before = await page.evaluate(() => ({ text: document.getElementById("cs-suggest-text").textContent, button: !document.getElementById("cs-suggest-use").hidden, threshold: document.getElementById("cs-threshold").value }));
      expect(before.text === hint.plain, `(${next}) the server's sentence is shown next to the setting`, `text: ${before.text}`);
      expect(before.button === (next === "usable"), `(${next}) the button is ${next === "usable" ? "shown" : "hidden"}`, `button shown: ${before.button}`);
      if (next === "usable") {
        await page.click("#cs-suggest-use");
        const after = await page.evaluate(() => ({ threshold: document.getElementById("cs-threshold").value, unsaved: !document.getElementById("cs-unsaved").hidden, msg: document.getElementById("cs-suggest-msg").textContent }));
        expect(after.threshold === "23" && after.unsaved && /not saved until/.test(after.msg) && writes.length === 0, "pressing it fills the field (23 for 0.235) and marks the form as unsaved; nothing is saved", `after: ${JSON.stringify(after)}; writes ${writes.join(",")}`);
        await audit("builder with a suggestion");
        await fieldSizesAtWidths("builder with a suggestion");
      } else {
        expect(before.threshold === (await page.evaluate(() => document.getElementById("cs-threshold").value)), `(${next}) the setting was not touched`, "setting changed");
      }
    }
  } finally {
    page.off("request", onRequest);
    await page.unroute(`**/inference/apps/${appId}/preview`).catch(() => {});
  }
});

await step("check-screen builder", async () => {
  await go(`${BASE}/projects/${opts.projectId}/check-screen/setup`);
  await page.waitForFunction(() => document.querySelectorAll("#cs-form select option, #cs-form input").length > 3, null, { timeout: 15000 })
    .then(() => pass("builder script started and filled the form"))
    .catch(() => fail("builder form is empty: the builder script probably failed"));
  await audit("check-screen builder");
  await fieldSizesAtWidths("check-screen builder");
});

await step("operator screen", async () => {
  await go(`${BASE}/check/${opts.slug}`);
  await page.waitForFunction(() => (document.getElementById("cs-title")?.textContent || "").trim().length > 0, null, { timeout: 15000 })
    .then(() => pass("operator screen script started and filled the title"))
    .catch(() => fail("operator screen title is empty: the script probably failed"));
  await audit("operator screen");
  await fieldSizesAtWidths("operator screen");
});

clearTimeout(hard);
await browser.close();

console.log(`\n${opts.browser}${opts.device ? ` (${opts.device})` : ""}: ${checks.length} checks passed, ${failures.length} failed.`);
if (webkitNoise) console.log(`note: ${webkitNoise} WebKit-internal style-src CSP messages ignored (known engine noise, XB-3)`);
notes.slice(0, 5).forEach((n) => console.log(`note: ${n}`));
if (opts.report) writeFileSync(opts.report, JSON.stringify({ browser: opts.browser, device: opts.device, passed: checks.length, failures, notes, webkitNoise }, null, 1));
if (failures.length) {
  console.log("\nFAILED:");
  failures.forEach((f) => console.log(`  [${f.where}] ${f.message}`));
  process.exit(1);
}
console.log("SMOKE OK");
