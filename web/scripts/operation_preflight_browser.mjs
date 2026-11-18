// Browser proof of the environment preflight card (T08): every check renders with
// its state, a blocked check names its owner and recovery, and Check again re-reads.
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
  await page.goto(base + '/operations');
  await page.getByText('No drafts yet.').waitFor();
  await page.locator('#draft-name').fill('Preflight walk');
  await page.locator('#draft-save').click();
  await page.getByText('Saved draft. Changes are saved when you select Save draft.').waitFor();
  // Exit setup un-hides every section, including the preflight card, without
  // touching the draft — the intentional "show me everything" path.
  await page.getByTestId('guide-exit').click();
  await page.locator('#preflight-section').waitFor({ state: 'visible' });
  await page.getByText('Is this server ready?').waitFor();
  // Every check renders exactly once, with a Ready or Blocked chip and its detail.
  const ids = ['video_checking', 'storage', 'teaching', 'checkscreen', 'vision'];
  for (const id of ids) {
    const row = page.locator(`[data-testid="preflight-${id}"]`);
    await row.waitFor();
    const chip = await row.locator('.a-chip').innerText();
    assert.ok(/^(Ready|Blocked)$/i.test(chip), `check ${id} must be Ready or Blocked, saw ${chip}`);
    const detail = await row.locator('.a-help').first().innerText();
    assert.ok(detail.length > 10, `check ${id} must explain its state`);
  }
  // On a disposable unconfigured server the model check is blocked and says who fixes it.
  const visionRow = page.locator('[data-testid="preflight-vision"]');
  assert.equal(await visionRow.getAttribute('data-ok'), '0');
  const blockedText = await visionRow.innerText();
  assert.match(blockedText, /who manages Athena|administrator/i, 'a blocked check must name its owner');
  assert.match(blockedText, /AI model page|Settings/, 'a blocked check must point at recovery');
  // The summary line counts the blocked parts.
  const summary = await page.locator('#preflight-status').innerText();
  assert.match(summary, /part[s]? of this server need[s]? attention/);
  // Check again re-reads the server state.
  const preflightCall = page.waitForResponse(response => response.url().includes('/api/operation-preflight'));
  await page.getByTestId('preflight-refresh').click();
  assert.equal((await preflightCall).status(), 200);
  await page.locator('[data-testid="preflight-storage"]').waitFor();
  // The API itself is owner-and-recovery shaped.
  const body = await page.evaluate(async () => {
    const response = await fetch('/api/operation-preflight', { headers: { Accept: 'application/json' } });
    return await response.json();
  });
  assert.equal(body.checks.length, 5);
  for (const check of body.checks) {
    assert.ok(check.title && check.detail, 'every check has a title and detail');
    if (!check.ok) {
      assert.ok(check.owner.trim(), 'blocked checks name an owner');
      assert.ok(check.recovery.trim(), 'blocked checks say how to recover');
    }
  }
  assert.equal(body.ready, body.checks.every(c => c.ok));
  assert.deepEqual(body.blockers.map(c => c.id), body.checks.filter(c => !c.ok).map(c => c.id));
  assert.equal(errors.length, 0, `page errors: ${errors.join('; ')}`);
  console.log('PREFLIGHT E2E OK');
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
