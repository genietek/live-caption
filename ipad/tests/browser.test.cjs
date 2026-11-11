// Drives the page in Chromium with a fake speech engine standing in for Safari's.
// Usage: serve the ipad folder on http://localhost:8765, then: node tests/browser.test.cjs [screenshot.png]
const { chromium, devices } = require('playwright');
const assert = require('assert/strict');

const fake = () => {
  class FakeRec {
    constructor() { window.__recs = (window.__recs || []); window.__recs.push(this); }
    start() { window.__active = this; setTimeout(() => this.onstart && this.onstart(), 0); }
    stop() { this.end(); }
    abort() { this.end(); }
    end() { if (this.ended) return; this.ended = true; setTimeout(() => this.onend && this.onend(), 0); }
    emit(items) {
      const results = items.map(([t, f]) => Object.assign([{ transcript: t }], { isFinal: f }));
      this.onresult({ results });
    }
  }
  window.webkitSpeechRecognition = FakeRec;
  window.SpeechRecognition = FakeRec;
};

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ...devices['iPad Pro 11 landscape'] });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.addInitScript(fake);
  await page.goto('http://localhost:8765/');
  await page.click('#startBtn');
  await page.waitForFunction(() => window.__active);
  assert.equal(await page.textContent('#startBtn'), 'Stop');

  await page.evaluate(() => window.__active.emit([['Good morning everyone', true], ['welcome to', false]]));
  assert.equal(await page.textContent('.captions p.live'), 'Good morning everyone welcome to');

  // Safari ends the session; the app keeps the line and starts a new session.
  await page.evaluate(() => window.__active.end());
  await page.waitForFunction(() => window.__recs.length === 2);
  await page.evaluate(() => window.__active.emit([['Today we talk about captions', false]]));
  await page.click('#transcriptToggle');
  assert.equal(await page.inputValue('#transcript'), 'Good morning everyone welcome to');

  await page.click('#startBtn');
  await page.waitForFunction(() => document.querySelector('#transcript').value.includes('captions'));
  assert.equal(await page.inputValue('#transcript'), 'Good morning everyone welcome to\nToday we talk about captions');
  assert.equal(await page.textContent('#startBtn'), 'Start');

  // Settings persist across reloads.
  await page.click('#settingsBtn');
  await page.selectOption('#theme', 'yellow');
  await page.fill('#size', '64');
  await page.reload();
  assert.equal(await page.getAttribute('body', 'data-theme'), 'yellow');
  assert.equal(await page.inputValue('#transcript'), 'Good morning everyone welcome to\nToday we talk about captions');

  // Back to defaults for the screenshot.
  await page.click('#settingsBtn');
  await page.selectOption('#theme', 'dark');
  await page.fill('#size', '48');
  await page.click('#settingsBtn');
  await page.click('#startBtn');
  await page.waitForFunction(() => window.__active && !window.__active.ended);
  await page.evaluate(() => window.__active.emit([['and this line is still being spoken right now', false]]));
  assert.equal(await page.isVisible('#settings'), false);
  if (process.argv[2]) await page.screenshot({ path: process.argv[2] });

  assert.deepEqual(errors, []);
  await browser.close();
  console.log('browser test passed');
})().catch((e) => { console.error(e); process.exit(1); });
