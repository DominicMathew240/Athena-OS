// Browser proof of the shared replay surface (T13): play/pause/scrub, a scale-correct overlay
// for the saved line and reference marks, per-object visibility toggles and readable timestamps,
// all without losing configuration when the viewport changes size.
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
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'athena-replay-'));
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

  // A draft with one recording and one saved line, exactly like the drafts flow.
  await page.goto(base + '/operations');
  await page.getByText('No drafts yet.').waitFor();
  await page.locator('#draft-name').fill('Replay surface walk');
  await page.locator('#draft-save').click();
  await page.getByText('Saved draft. Changes are saved when you select Save draft.').waitFor();
  await page.locator('#recording-file').setInputFiles(videoFile);
  await page.locator('#recording-upload').click();
  await page.getByText('Recording saved. Counting is not available yet.').waitFor();
  const video = page.locator('#recording-preview');
  await page.waitForFunction(() => document.querySelector('#recording-preview').readyState >= 2);
  assert.equal(await video.evaluate(v => v.videoWidth), 64);
  // The replay surface owns playback; the video must not carry native controls.
  assert.equal(await video.evaluate(v => v.controls), false);
  for (const [id, value] of [['line-start-x', '20'], ['line-start-y', '50'], ['line-end-x', '80'], ['line-end-y', '50'], ['line-positive', 'Entering'], ['line-negative', 'Leaving']]) {
    await page.locator(`#${id}`).fill(value);
  }
  let saved = page.waitForResponse(response => response.url().includes('/api/operation-drafts/') && response.request().method() === 'PUT');
  await page.locator('#line-save').click();
  assert.equal((await saved).status(), 200);
  // Two reference marks at known times, so the overlay has marks to draw.
  await video.evaluate(v => { v.currentTime = 1; });
  await page.waitForFunction(() => Math.abs(document.querySelector('#recording-preview').currentTime - 1) < .05);
  await page.locator('#mark-positive-event').click();
  await video.evaluate(v => { v.currentTime = 2.5; });
  await page.waitForFunction(() => Math.abs(document.querySelector('#recording-preview').currentTime - 2.5) < .05);
  await page.locator('#mark-negative-event').click();
  saved = page.waitForResponse(response => response.url().includes('/api/operation-drafts/') && response.request().method() === 'PUT');
  await page.locator('#line-save').click();
  const savedResponse = await saved;
  assert.equal(savedResponse.status(), 200);
  const draftId = (await savedResponse.json()).draft.id;

  // Play and pause through the surface's own controls.
  await page.getByTestId('replay-play').click();
  await page.getByTestId('replay-pause').waitFor();
  await page.waitForFunction(() => document.querySelector('#recording-preview').currentTime > .3);
  await page.getByTestId('replay-pause').click();
  await page.getByTestId('replay-play').waitFor();
  await page.waitForFunction(() => document.querySelector('#recording-preview').paused);

  // Scrub through the surface's range input; the timestamp follows and stays readable.
  const duration = await video.evaluate(v => v.duration);
  await page.locator('.replay-scrub').fill('0.5');
  await page.waitForFunction(() => Math.abs(document.querySelector('#recording-preview').currentTime - document.querySelector('#recording-preview').duration / 2) < .25);
  assert.match(await page.getByTestId('replay-time').innerText(), /^\d+:\d{2}\.\d$/);
  const shownDuration = await page.getByTestId('replay-duration').innerText();
  assert.match(shownDuration, /^\d+:\d{2}\.\d$/);
  const [minutes, rest] = shownDuration.split(':');
  assert.ok(Math.abs(Number(minutes) * 60 + Number(rest) - duration) < .25, `shown duration ${shownDuration} must match the video's ${duration}s`);

  // The overlay draws the saved line (20%..80% across, mid-height) and both reference marks.
  const overlay = page.locator('.replay-overlay');
  const white = async (x, y) => {
    const pixel = await overlay.evaluate((canvas, point) => Array.from(canvas.getContext('2d').getImageData(point[0], point[1], 1, 1).data), [x, y]);
    return pixel[3] > 0 && pixel[0] > 200 && pixel[1] > 200 && pixel[2] > 200;
  };
  await page.waitForFunction(() => {
    const data = document.querySelector('.replay-overlay').getContext('2d').getImageData(32, 24, 1, 1).data;
    return data[3] > 0 && data[0] > 200;
  });
  assert.ok(await white(32, 24), 'the saved line must be drawn across the middle of the picture');
  assert.ok(await white(32, 29), 'the positive-side arrow must be drawn below a left-to-right line');
  const mark = async (x, y) => {
    const pixel = await overlay.evaluate((canvas, point) => Array.from(canvas.getContext('2d').getImageData(point[0], point[1], 1, 1).data), [x, y]);
    return pixel[3] > 0 && Math.abs(pixel[0] - 127) < 30 && Math.abs(pixel[1] - 209) < 30 && Math.abs(pixel[2] - 192) < 30;
  };
  assert.ok(await mark(16, 2), 'a reference mark must be drawn at 1s of a 4s recording');
  assert.ok(await mark(40, 2), 'a reference mark must be drawn at 2.5s of a 4s recording');

  // Per-object visibility toggle: view state only, never a save.
  const toggle = page.getByTestId('replay-toggle-line');
  await toggle.waitFor();
  assert.ok(await toggle.isChecked(), 'the saved line starts visible');
  const savedDuringToggle = [];
  page.on('request', request => { if (request.url().includes('/api/operation-drafts/') && request.method() !== 'GET') savedDuringToggle.push(request.url()); });
  await toggle.uncheck();
  await page.waitForFunction(() => document.querySelector('.replay-overlay').getContext('2d').getImageData(32, 24, 1, 1).data[3] === 0);
  assert.ok(await mark(16, 2), 'hiding the line must not hide the reference marks');
  await toggle.check();
  await page.waitForFunction(() => {
    const data = document.querySelector('.replay-overlay').getContext('2d').getImageData(32, 24, 1, 1).data;
    return data[3] > 0 && data[0] > 200;
  });
  assert.deepEqual(savedDuringToggle, [], 'toggling visibility must not save anything');

  // Resize the viewport: the overlay tracks the video, coordinates and configuration do not move.
  const savedDuringResize = [];
  const resizeListener = request => { if (request.url().includes('/api/operation-drafts/') && request.method() !== 'GET') savedDuringResize.push(request.url()); };
  page.on('request', resizeListener);
  for (const viewport of [{ width: 390, height: 844 }, { width: 1024, height: 700 }]) {
    await page.setViewportSize(viewport);
    await page.waitForTimeout(350);
    const videoBox = await video.boundingBox();
    const overlayBox = await overlay.boundingBox();
    assert.ok(videoBox && overlayBox, 'video and overlay must both be laid out');
    for (const [key, value] of Object.entries({ x: 1, y: 1, width: 1, height: 1 })) {
      assert.ok(Math.abs(videoBox[key] - overlayBox[key]) <= value, `overlay must sit exactly on the video (${key})`);
    }
    assert.ok(await white(32, 24), `the line must still be drawn after resizing to ${viewport.width}x${viewport.height}`);
    assert.ok(await mark(16, 2), `reference marks must still be drawn after resizing to ${viewport.width}x${viewport.height}`);
    assert.match(await page.getByTestId('replay-time').innerText(), /^\d+:\d{2}\.\d$/, 'timestamps must stay readable after a resize');
    assert.equal(await page.locator('#line-start-x').inputValue(), '20', 'resizing must not change the saved geometry inputs');
    assert.equal(await page.locator('#line-end-x').inputValue(), '80', 'resizing must not change the saved geometry inputs');
    assert.equal(await page.locator('#line-positive').inputValue(), 'Entering', 'resizing must not change the saved labels');
  }
  page.off('request', resizeListener);
  assert.deepEqual(savedDuringResize, [], 'resizing must never re-save the draft');

  // Reload and resume: the configuration is exactly what was saved, in original coordinates.
  await page.reload();
  await page.locator('#draft-list').getByRole('button', { name: 'Replay surface walk' }).click();
  await page.waitForFunction(() => document.querySelector('#recording-preview').readyState >= 2);
  await page.waitForFunction(() => {
    const data = document.querySelector('.replay-overlay').getContext('2d').getImageData(32, 24, 1, 1).data;
    return data[3] > 0 && data[0] > 200;
  });
  assert.equal(await page.locator('#line-start-x').inputValue(), '20');
  assert.equal(await page.locator('#line-start-y').inputValue(), '50');
  assert.equal(await page.locator('#line-end-x').inputValue(), '80');
  assert.equal(await page.locator('#line-end-y').inputValue(), '50');
  assert.equal(await page.locator('#line-positive').inputValue(), 'Entering');
  assert.equal(await page.locator('#line-negative').inputValue(), 'Leaving');
  assert.equal(await page.locator('#reference-event-list li').count(), 2);
  assert.ok(await page.getByTestId('replay-toggle-line').isChecked(), 'visibility is view state and resets to visible after a reload');
  const serverDraft = await page.evaluate(async id => (await (await fetch(`/api/operation-drafts/${id}`)).json()).draft, draftId);
  assert.deepEqual(serverDraft.line.start, [0.2, 0.5], 'saved coordinates must stay normalized, never viewport pixels');
  assert.deepEqual(serverDraft.line.end, [0.8, 0.5], 'saved coordinates must stay normalized, never viewport pixels');
  assert.equal(serverDraft.reference_events.length, 2);

  // Leave the disposable server clean.
  await page.locator('#draft-delete').click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete draft' }).click();
  await page.getByText('No drafts yet.').waitFor();
  assert.deepEqual(errors, []);
  console.log('PASS: replay surface plays, scrubs and toggles with a scale-correct overlay; resizing and reloading keep normalized coordinates and configuration.');
} finally {
  await context.close();
  await browser.close();
}
