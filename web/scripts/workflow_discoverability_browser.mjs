// Browser proof of T07: the image workflow stays discoverable next to the new
// operations console, and task-linked help explains how the two relate.
// Run against a disposable Athena server: ATHENA_QA_BASE, ATHENA_QA_SESSION_FILE
// and PLAYWRIGHT_NODE_MODULES must be supplied by the caller.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require(require.resolve('playwright', { paths: [process.env.PLAYWRIGHT_NODE_MODULES] }));
const base = process.env.ATHENA_QA_BASE;
const session = fs.readFileSync(process.env.ATHENA_QA_SESSION_FILE, 'utf8').trim();
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' });
try {
  await context.addCookies([{ name: 'sessionid', value: session, url: base }]);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));

  // 1. The sidebar links every workflow, including the new People counting entry.
  await page.goto(base + '/operations');
  const navOperations = page.locator('[data-testid="nav-operations-link"]');
  await navOperations.waitFor();
  assert.equal(await navOperations.getAttribute('aria-current'), 'page');
  for (const testid of ['nav-home-link', 'nav-inspections-link', 'nav-detectors-link', 'nav-docs-link']) {
    await page.locator(`[data-testid="${testid}"]`).waitFor();
  }

  // 2. The operations page explains its relationship to inspections and detectors.
  const note = page.locator('[data-testid="operations-relationship"]');
  await note.waitFor();
  const noteText = await note.innerText();
  assert.match(noteText, /photo inspection/i);
  assert.match(noteText, /custom detector/i);
  const detectorsHref = await note.locator('a', { hasText: 'custom detector' }).first().getAttribute('href');
  assert.equal(detectorsHref, '/detectors');

  // 3. Clicking through: the operations sidebar link really navigates; the image
  //    workflow pages stay reachable from navigation while on operations.
  await page.locator('[data-testid="nav-detectors-link"]').click();
  await page.waitForURL('**/detectors');
  await page.locator('[data-testid="nav-operations-link"]').click();
  await page.waitForURL('**/operations');

  // 4. The docs wiki teaches the relationship for every audience, and the
  //    operations section links back into the workflow.
  await page.goto(base + '/docs');
  const opsSection = page.locator('#operations');
  await opsSection.waitFor();
  const sectionText = await opsSection.innerText();
  assert.match(sectionText, /cannot yet count people/i, 'the wiki must keep the honest limit');
  assert.match(sectionText, /custom detector/i);
  assert.match(sectionText, /counting line/i);
  // Search finds the new section too (keywords work).
  await page.locator('#wiki-search').fill('counting');
  await page.waitForTimeout(300);
  const visible = await page.locator('#operations').isVisible();
  assert.ok(visible, 'searching "counting" keeps the operations guide visible');
  await page.locator('#wiki-clear').click();

  // 5. The overview mini-card links all three workflows.
  const overviewCard = page.locator('#overview', { hasText: 'People counting operations' });
  await overviewCard.waitFor();
  await overviewCard.getByRole('link', { name: 'Open people counting' }).click();
  await page.waitForURL('**/operations');

  assert.equal(errors.length, 0, `page errors: ${errors.join('; ')}`);
  console.log('DISCOVERABILITY E2E OK');
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
