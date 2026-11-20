// Browser proof of finite line drawing and direction confirmation (T14): draw a line,
// name it, label the two sides, reverse the direction with the explicit button, watch
// the crossing-example preview flip with the direction, then save, reload and check
// that everything persisted. Degenerate lines are refused with a plain message.
//
// Run against a disposable Athena server. The caller supplies:
//   ATHENA_QA_BASE             e.g. http://127.0.0.1:8123
//   ATHENA_QA_SESSION_FILE     a file containing one signed-in sessionid cookie value
//   PLAYWRIGHT_NODE_MODULES    a node_modules folder that contains playwright
//   ATHENA_QA_VIDEO            optional; without it this script makes its own synthetic MP4
//                              with ffmpeg (same recipe as make_clip in tests_source_recordings.py)
import { createRequire } from 'node:module';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const run = promisify(execFile);
const require = createRequire(import.meta.url);
const { chromium } = require(require.resolve('playwright', { paths: [process.env.PLAYWRIGHT_NODE_MODULES] }));
const base = process.env.ATHENA_QA_BASE;
const session = fs.readFileSync(process.env.ATHENA_QA_SESSION_FILE, 'utf8').trim();

async function makeClip() {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'athena-line-'));
  const file = path.join(folder, 'clip.mp4');
  await run('ffmpeg', [
    '-hide_banner', '-loglevel', 'error',
    '-f', 'lavfi', '-i', 'color=c=blue:s=64x48:r=5',
    '-t', '4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
    '-movflags', '+faststart', '-y', file
  ]);
  return file;
}

const videoFile = process.env.ATHENA_QA_VIDEO || await makeClip();
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
try {
  await context.addCookies([{ name: 'sessionid', value: session, url: base }]);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));

  // A draft with one recording, walked through the guided stepper like the T13 script.
  await page.goto(base + '/operations');
  await page.getByText(/No drafts yet|Select a draft|Showing up to/).waitFor();
  await page.locator('#draft-name').fill('Line direction walk');
  await page.locator('#draft-save').click();
  await page.getByText('Saved draft. Changes are saved when you select Save draft.').waitFor();
  await page.getByTestId('guide-continue').click();
  await page.locator('#recording-section').waitFor({ state: 'visible' });
  await page.locator('#recording-file').setInputFiles(videoFile);
  await page.locator('#recording-upload').click();
  await page.getByText('Recording saved. Counting is not available yet.').waitFor();
  await page.waitForFunction(() => document.querySelector('#recording-preview').readyState >= 2);
  await page.getByTestId('guide-continue').click();
  await page.locator('#line-section').waitFor({ state: 'visible' });

  // 1. A degenerate line is refused before anything reaches the server.
  for (const [id, value] of [['line-start-x', '50'], ['line-start-y', '50'], ['line-end-x', '50'], ['line-end-y', '50'], ['line-name', 'Door line'], ['line-positive', 'Shop'], ['line-negative', 'Street']]) {
    await page.locator(`#${id}`).fill(value);
  }
  const savedWhileDegenerate = page.waitForResponse(() => true, { timeout: 1500 }).then(r => r).catch(() => null);
  await page.locator('#line-save').click();
  await page.getByText('Draw the line longer: the two endpoints are too close together to give the line a direction.').waitFor();
  assert.equal(await savedWhileDegenerate, null, 'a degenerate line must not be saved');

  // 2. Name the line and label the two sides; the crossing preview follows the labels.
  await page.locator('#line-end-x').fill('80');
  await page.locator('#line-end-y').fill('50');
  await page.getByTestId('line-preview-step').click(); // first step: still on the Street side
  assert.match(await page.locator('#line-preview-text').innerText(), /Step 2 of 5: the person moves from the "Street" side towards the "Shop" side/);
  assert.equal(await page.locator('#line-preview-canvas').getAttribute('data-preview-side'), 'negative');
  await page.getByTestId('line-preview-step').click();
  await page.getByTestId('line-preview-step').click(); // middle of the crossing
  assert.match(await page.locator('#line-preview-text').innerText(), /Step 4 of 5/);
  await page.getByTestId('line-preview-step').click(); // arrived on the Shop side
  assert.equal(await page.locator('#line-preview-canvas').getAttribute('data-preview-side'), 'positive');
  assert.match(await page.locator('#line-preview-text').innerText(), /counts as moving towards "Shop"/);
  let saved = page.waitForResponse(response => response.url().includes('/api/operation-drafts/') && response.request().method() === 'PUT');
  await page.locator('#line-save').click();
  const firstSave = await saved;
  assert.equal(firstSave.status(), 200);
  const draftId = (await firstSave.json()).draft.id;

  // 3. Reverse the direction with the explicit button: endpoints swap, side labels
  //    move with them, and the crossing preview flips which side is the arrow side.
  await page.getByTestId('line-reverse').click();
  await page.getByText('Direction reversed: the start and end points swapped, and the side labels moved with them.').waitFor();
  assert.equal(await page.locator('#line-start-x').inputValue(), '80');
  assert.equal(await page.locator('#line-start-y').inputValue(), '50');
  assert.equal(await page.locator('#line-end-x').inputValue(), '50');
  assert.equal(await page.locator('#line-end-y').inputValue(), '50');
  assert.equal(await page.locator('#line-positive').inputValue(), 'Street');
  assert.equal(await page.locator('#line-negative').inputValue(), 'Shop');
  // The preview reset to the (new) negative side, which is now "Shop": the flip is
  // visible in both the indicator attribute and the plain-language text.
  assert.equal(await page.locator('#line-preview-canvas').getAttribute('data-preview-side'), 'negative');
  assert.match(await page.locator('#line-preview-text').innerText(), /from the "Shop" side towards the "Street" side/);
  saved = page.waitForResponse(response => response.url().includes('/api/operation-drafts/') && response.request().method() === 'PUT');
  await page.locator('#line-save').click();
  const reversedSave = await saved;
  assert.equal(reversedSave.status(), 200);

  // 4. Reload and resume: the named line, its side labels and the reversed direction
  //    all persisted, in normalized coordinates on the server.
  await page.reload();
  await page.locator(`#draft-list [data-draft-id="${draftId}"]`).click();
  await page.waitForFunction(() => document.querySelector('#recording-preview').readyState >= 2);
  await page.getByTestId('guide-step-line').click().catch(async () => {
    // The stepper may already sit on the line step after resuming.
    await page.locator('#line-section').waitFor({ state: 'visible' });
  });
  await page.locator('#line-section').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#line-name').inputValue(), 'Door line');
  assert.equal(await page.locator('#line-start-x').inputValue(), '80');
  assert.equal(await page.locator('#line-start-y').inputValue(), '50');
  assert.equal(await page.locator('#line-end-x').inputValue(), '50');
  assert.equal(await page.locator('#line-end-y').inputValue(), '50');
  assert.equal(await page.locator('#line-positive').inputValue(), 'Street');
  assert.equal(await page.locator('#line-negative').inputValue(), 'Shop');
  assert.match(await page.locator('#line-preview-text').innerText(), /from the "Shop" side towards the "Street" side/);
  const serverDraft = await page.evaluate(async id => (await (await fetch(`/api/operation-drafts/${id}`)).json()).draft, draftId);
  assert.deepEqual(serverDraft.line.start, [0.8, 0.5], 'saved coordinates must stay normalized, never viewport pixels');
  assert.deepEqual(serverDraft.line.end, [0.5, 0.5]);
  assert.equal(serverDraft.line.name, 'Door line');
  assert.equal(serverDraft.line.positive_label, 'Street');
  assert.equal(serverDraft.line.negative_label, 'Shop');
  assert.equal(serverDraft.revision, 4, 'creation, two line saves and the dragged endpoint mean revision 4');

  // 5. Dragging an endpoint moves it: a pointer drag on the editing canvas shifts the
  //    start endpoint and leaves the direction buttons honest about unsaved state.
  await page.locator('#line-frame').click();
  await page.getByText(/Frame ready/).waitFor();
  const canvas = page.locator('#line-canvas');
  const box = await canvas.boundingBox();
  assert.ok(box, 'the line canvas must be laid out');
  // The start endpoint sits at (80%, 50%) of the canvas.
  const startX = box.x + box.width * 0.8, startY = box.y + box.height * 0.5;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.5, { steps: 5 });
  await page.mouse.up();
  await page.getByText('Endpoint moved. Save the draft to keep this change.').waitFor();
  const draggedX = await page.locator('#line-start-x').inputValue();
  assert.ok(Math.abs(Number(draggedX) - 70) < 1.5, `dragging the start endpoint must move it (got ${draggedX}%)`);
  saved = page.waitForResponse(response => response.url().includes('/api/operation-drafts/') && response.request().method() === 'PUT');
  await page.locator('#line-save').click();
  assert.equal((await saved).status(), 200);

  // Leave the disposable server clean. (Exit the stepper first: it hides the draft section.)
  await page.getByTestId('guide-exit').click();
  await page.locator('#draft-delete').click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete draft' }).click();
  await page.getByText('Draft deleted.').waitFor();
  await page.locator(`#draft-list [data-draft-id="${draftId}"]`).waitFor({ state: 'detached' });
  assert.deepEqual(errors, []);
  console.log('PASS: line drawing names a line, labels both sides, reverses direction by button with a flipping crossing preview, refuses degenerate lines, and persists everything across a reload.');
} finally {
  await context.close();
  await browser.close();
}
