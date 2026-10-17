#!/usr/bin/env node
/* Browser smoke test for the Athena console: the check that fails when the page is dead.

   Why it exists: the Django tests read the JavaScript as text, so one `const` used before its declaration made
   every button dead while 414 tests stayed green (docs/blog/2026-10-tests-green-console-dead.md). This script
   loads the real pages in a real browser and fails on:

     - any uncaught script error (`pageerror`)
     - any console error, and any Content Security Policy violation
     - any failed or 4xx/5xx request
     - a console that rendered but did not start (project counter stuck at "00", no project list, empty panels)
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
const failures = [];
const notes = [];
const checks = [];
let where = "start";
const fail = (message) => { failures.push({ where, message }); console.log(`  FAIL [${where}] ${message}`); };
const pass = (message) => { checks.push({ where, message }); };
const expect = (condition, okMessage, failMessage) => (condition ? pass(okMessage) : fail(failMessage || okMessage));
const hard = setTimeout(() => { console.error("HARD TIMEOUT (240 s): the smoke test hung"); process.exit(2); }, 240000);

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

async function step(name, fn) {
  where = name;
  console.log(`- ${name}`);
  try { await fn(); } catch (error) { fail(`step crashed: ${String(error.message).split("\n")[0].slice(0, 300)}`); }
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
  // A dead console shows the server's HTML with the counter stuck at "00" and the badge at "Checking...".
  await page.waitForFunction(() => document.getElementById("project-count")?.textContent !== "00" && document.getElementById("autolabel-badge")?.textContent.trim() !== "Checking…", null, { timeout: 15000 })
    .then(() => pass("console script started: project counter and auto-suggest badge are filled in"))
    .catch(() => fail("the console did not start: project counter is still 00 or the badge still says Checking… (a script probably failed on load)"));
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
  await audit("seeded project page");
  await fieldSizesAtWidths("seeded project page");
});

await step("Teach panel: start-from choice (W15)", async () => {
  const hidden = await page.evaluate(() => document.getElementById("tr-start-from").hidden);
  if (hidden) { notes.push("start-from control hidden: the selected teaching tool offers no ready-made starts here"); return; }
  const info = await page.evaluate(() => {
    const radios = [...document.querySelectorAll("#tr-start-from-options input[type=radio]")];
    return {
      open: document.getElementById("tr-start-from").open,
      scratchChecked: document.getElementById("tr-pre-none").checked,
      disabled: radios.filter((r) => r.disabled).map((r) => r.value),
      ackVisible: !document.getElementById("tr-pre-licence").hidden
    };
  });
  expect(!info.open, "the start-from control is collapsed by default", "the start-from control is open by default");
  expect(info.scratchChecked && !info.ackVisible, "from scratch is chosen and no licence box is shown by default", "wrong default start choice");
});

await step("picture (annotation) dialog", async () => {
  await page.locator(".asset-annotate").first().click();
  await page.waitForSelector("#annotation-dialog[open]");
  await dialogChecks("annotation dialog", "#annotation-dialog", ".asset-annotate");
});

await step("remove-picture confirmation (cancelled, nothing removed)", async () => {
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
  const button = page.locator('button[aria-label^="Approve Model"]:not([disabled])').first();
  await button.scrollIntoViewIfNeeded();
  await button.click();
  await page.waitForSelector("#decision-dialog[open]");
  await dialogChecks("approve dialog", "#decision-dialog", 'button[aria-label^="Approve Model"]', { closeWith: "#decision-cancel" });
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
