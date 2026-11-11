#!/usr/bin/env node
/* Capture the console's main views at desktop and phone widths, plus basic page metrics.
   Prototype data only: point it at a DISPOSABLE console (see web/README.md, seed_browser_smoke.py).

     node web/scripts/capture_views.mjs --out <dir> --seeded http://127.0.0.1:8791 --cold http://127.0.0.1:8792 \
          --project-id <id> --slug <check-slug> --app-id <id> [--password qa-throwaway-pass-1]

   Writes <dir>/<NN>-<name>-<width>.png and <dir>/metrics.json (document height, horizontal overflow, console
   errors, number of primary buttons in view, and the computed page background/text colours). Playwright is found
   the same way browser_smoke.mjs finds it (PLAYWRIGHT_NODE_MODULES, scripts/.smoke, ./node_modules).
   Use ONLY=login,project to limit the groups. Not a test: it takes pictures and measures; it asserts nothing. */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const roots = [process.env.PLAYWRIGHT_NODE_MODULES, path.join(HERE, ".smoke", "node_modules"), path.join(process.cwd(), "node_modules")].filter(Boolean);
let pw = null;
for (const root of roots) { try { pw = createRequire(path.resolve(root) + path.sep)("playwright"); break; } catch { /* next */ } }
if (!pw) { console.error("Cannot find playwright; set PLAYWRIGHT_NODE_MODULES."); process.exit(2); }

const args = {};
for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = process.argv[i + 1];
const OUT = path.resolve(args.out || "capture-out");
const ONLY = (process.env.ONLY || "").split(",").filter(Boolean);
const want = (g) => !ONLY.length || ONLY.includes(g);
const PASSWORD = args.password || "qa-throwaway-pass-1";
fs.mkdirSync(OUT, { recursive: true });

const WIDTHS = [[1440, 900], [390, 844]];
const metrics = [];
let n = 0;

async function newPage(browser, base, width, height) {
  const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: "reduce" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
  page.on("requestfailed", (r) => errors.push("requestfailed: " + r.url()));
  return { ctx, page, errors };
}
async function login(page, base, user) {
  await page.goto(base + "/login/");
  await page.fill("#id_username", user);
  await page.fill("#id_password", PASSWORD);
  await Promise.all([page.waitForURL((u) => !u.pathname.startsWith("/login")), page.click("form.signin-form button[type=submit]")]);
}
async function ready(page) {
  await page.waitForFunction(() => document.getElementById("project-count")?.dataset.ready === "true", null, { timeout: 15000 }).catch(() => {});
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(400);
}
async function shot(page, errors, name, width, extra = {}) {
  n += 1;
  const file = `${String(n).padStart(2, "0")}-${name}-${width}.png`;
  await page.screenshot({ path: path.join(OUT, file) });
  const m = await page.evaluate(() => {
    const de = document.documentElement, cs = getComputedStyle(document.body);
    const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden"; };
    return {
      docHeight: de.scrollHeight, viewport: [innerWidth, innerHeight], overflowX: de.scrollWidth - de.clientWidth,
      bodyBg: cs.backgroundColor, bodyColor: cs.color, bodyFont: cs.fontFamily.split(",")[0],
      primaryButtonsVisible: [...document.querySelectorAll("button.primary, a.primary")].filter(vis).length,
      h1: document.querySelector("h1")?.textContent.trim().slice(0, 80) || null,
    };
  });
  metrics.push({ file, ...m, ...extra, errorsSoFar: errors.length });
  console.log("shot", file);
}

const browser = await pw.chromium.launch();
try {
  for (const [w, h] of WIDTHS) {
    if (want("login") && args.cold) {
      const { ctx, page, errors } = await newPage(browser, args.cold, w, h);
      await page.goto(args.cold + "/login/"); await page.waitForTimeout(300);
      await shot(page, errors, "login", w);
      await login(page, args.cold, "qa-admin"); await ready(page);
      await shot(page, errors, "console-empty", w);
      await page.locator("#open-project-dialog:visible, #empty-create:visible").first().click(); await page.waitForSelector("#project-dialog[open]");
      await shot(page, errors, "create-dialog", w);
      await page.fill("#project-name", "Dented cans"); await page.fill("#project-task", "Find dents in can photos");
      await page.click("#create-project-form button[type=submit]");
      await page.waitForFunction(() => document.getElementById("workspace-title")?.textContent.includes("Dented cans"), null, { timeout: 10000 }).catch(() => {});
      await page.waitForTimeout(500);
      await shot(page, errors, "new-project-checklist", w);
      metrics.push({ group: "login", width: w, errors });
      await ctx.close();
    }
    if (want("project") && args.seeded) {
      const { ctx, page, errors } = await newPage(browser, args.seeded, w, h);
      await login(page, args.seeded, "smoke"); await ready(page);
      await shot(page, errors, "console-list", w);
      await page.goto(`${args.seeded}/#project=${args.projectId}`); await ready(page);
      await page.waitForTimeout(600);
      const panels = ["checklist-panel", "labels-panel", "image-assets-panel", "dataset-panel", "train-panel", "apps-panel"];
      for (const id of panels) {
        const link = page.locator(`a[data-panel="${id}"]`).first();
        if (await link.count()) { await link.click({ force: true }).catch(() => {}); await page.waitForTimeout(500); }
        await shot(page, errors, id.replace("-panel", ""), w);
      }
      const ann = page.locator(".asset-annotate").first();
      await page.locator('a[data-panel="image-assets-panel"]').first().click({ force: true }).catch(() => {});
      await page.waitForTimeout(400);
      if (await ann.count()) {
        await ann.click(); await page.waitForSelector("#annotation-dialog[open]").catch(() => {});
        await page.waitForTimeout(900);
        await shot(page, errors, "annotation", w);
        await page.keyboard.press("Escape"); await page.waitForTimeout(300);
      }
      metrics.push({ group: "project", width: w, errors });
      await ctx.close();
    }
    if (want("check") && args.seeded && args.slug) {
      const { ctx, page, errors } = await newPage(browser, args.seeded, w, h);
      await login(page, args.seeded, "smoke");
      await page.goto(`${args.seeded}/check/${args.slug}`); await page.waitForTimeout(800);
      await shot(page, errors, "check-operator", w);
      await page.goto(`${args.seeded}/projects/${args.projectId}/check-screen/setup`); await page.waitForTimeout(800);
      await shot(page, errors, "check-builder", w);
      metrics.push({ group: "check", width: w, errors });
      await ctx.close();
    }
    if (want("access") && args.seeded) {
      const { ctx, page, errors } = await newPage(browser, args.seeded, w, h);
      await login(page, args.seeded, "smoke");
      await page.goto(`${args.seeded}/access`); await page.waitForTimeout(600);
      await shot(page, errors, "access", w);
      await page.goto(`${args.seeded}/nope-404`); await page.waitForTimeout(300);
      await shot(page, errors, "error-404", w);
      metrics.push({ group: "access", width: w, errors });
      await ctx.close();
    }
  }
} finally {
  fs.writeFileSync(path.join(OUT, "metrics.json"), JSON.stringify(metrics, null, 2));
  await browser.close();
}
