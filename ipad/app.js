import { CaptionState, appendLine, fileName } from './captions.js';

const $ = (id) => document.getElementById(id);
const els = {
  status: $('status'), notice: $('notice'), start: $('startBtn'), copyLast: $('copyLast'),
  settingsBtn: $('settingsBtn'), settings: $('settings'), lang: $('lang'), size: $('size'),
  sizeValue: $('sizeValue'), lines: $('lines'), linesValue: $('linesValue'), theme: $('theme'),
  keepAwake: $('keepAwake'), captions: $('captions'), placeholder: $('placeholder'),
  transcriptToggle: $('transcriptToggle'), transcriptBody: $('transcriptBody'), transcript: $('transcript'),
  copyAll: $('copyAll'), share: $('share'), save: $('save'), clear: $('clear'),
};

const LANGS = [
  ['en-US', 'English (US)'], ['en-GB', 'English (UK)'], ['en-AU', 'English (Australia)'], ['en-IN', 'English (India)'],
  ['zh-CN', '中文 普通话 (中国大陆)'], ['zh-TW', '中文 國語 (台灣)'], ['zh-HK', '粵語 (香港)'],
  ['ja-JP', '日本語'], ['ko-KR', '한국어'], ['es-ES', 'Español (España)'], ['es-MX', 'Español (México)'],
  ['fr-FR', 'Français'], ['de-DE', 'Deutsch'], ['it-IT', 'Italiano'], ['pt-BR', 'Português (Brasil)'],
  ['ru-RU', 'Русский'], ['hi-IN', 'हिन्दी'], ['ar-SA', 'العربية'], ['vi-VN', 'Tiếng Việt'], ['th-TH', 'ไทย'],
];

// ---- Settings (kept on this iPad only) ----
const DEFAULTS = { lang: pickDefaultLang(), size: 48, lines: 4, theme: 'dark', keepAwake: true, showTranscript: false };
const settings = { ...DEFAULTS, ...load('cb.settings', {}) };

function load(key, fallback) {
  try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: ignore */ }
}
function pickDefaultLang() {
  const nav = (navigator.language || 'en-US').toLowerCase();
  const exact = LANGS.find(([c]) => c.toLowerCase() === nav);
  if (exact) return exact[0];
  const base = LANGS.find(([c]) => c.toLowerCase().split('-')[0] === nav.split('-')[0]);
  return base ? base[0] : 'en-US';
}
function saveSettings() { store('cb.settings', settings); }

function applySettings() {
  document.body.dataset.theme = settings.theme;
  document.documentElement.style.setProperty('--cap-size', settings.size + 'px');
  document.querySelector('meta[name="theme-color"]').content = settings.theme === 'light' ? '#ffffff' : '#111214';
  els.sizeValue.textContent = settings.size + ' pt';
  els.linesValue.textContent = settings.lines;
  els.transcriptBody.hidden = !settings.showTranscript;
  els.transcriptToggle.setAttribute('aria-expanded', String(settings.showTranscript));
  state.lang = settings.lang;
  render();
}

// ---- Captions ----
const state = new CaptionState(settings.lang);
els.transcript.value = load('cb.transcript', '');

function render() {
  const lines = state.tail(settings.lines);
  els.placeholder.hidden = lines.length > 0;
  els.captions.querySelectorAll('p:not(.placeholder)').forEach((p) => p.remove());
  lines.forEach((text, i) => {
    const p = document.createElement('p');
    p.textContent = text;
    const isLive = i === lines.length - 1 && !!state.live;
    p.className = isLive ? 'live' : (i === lines.length - 1 ? '' : 'old');
    els.captions.appendChild(p);
  });
  els.captions.scrollTop = els.captions.scrollHeight;
}

function commitLine() {
  const line = state.commit();
  if (line) {
    els.transcript.value = appendLine(els.transcript.value, line);
    els.transcript.scrollTop = els.transcript.scrollHeight;
    store('cb.transcript', els.transcript.value);
  }
  render();
}

// ---- Speech recognition ----
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const standalone = window.navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
let rec = null;
let running = false;
let restartTimer = null;
let recentEnds = [];

let baseStatus = { text: 'Ready', live: false };
function setStatus(text, live, temporary) {
  if (!temporary) baseStatus = { text, live: !!live };
  els.status.textContent = text;
  els.status.classList.toggle('live', !!live);
}
function showNotice(html) {
  els.notice.innerHTML = html;
  els.notice.hidden = !html;
}

function createRecognition() {
  const r = new Recognition();
  r.lang = settings.lang;
  r.continuous = true;
  r.interimResults = true;
  r.maxAlternatives = 1;
  r.onstart = () => setStatus('Listening', true);
  r.onresult = (e) => { state.update(e.results); render(); };
  r.onerror = (e) => handleError(e.error);
  r.onend = () => {
    commitLine();
    if (!running) return;
    // Safari ends a session after a pause or about a minute. Start a new one,
    // backing off if it keeps ending straight away.
    const now = Date.now();
    recentEnds = recentEnds.filter((t) => now - t < 10000);
    recentEnds.push(now);
    const delay = recentEnds.length > 5 ? 3000 : 150;
    setStatus('Listening', true);
    clearTimeout(restartTimer);
    restartTimer = setTimeout(startSession, delay);
  };
  return r;
}

function startSession() {
  if (!running || document.hidden) return;
  try {
    rec = createRecognition();
    rec.start();
  } catch (err) {
    clearTimeout(restartTimer);
    restartTimer = setTimeout(startSession, 1000);
  }
}

function handleError(code) {
  if (code === 'not-allowed' || code === 'service-not-allowed') {
    stop();
    const hint = standalone
      ? ' If you opened CaptionBridge from the Home Screen, try opening the same page in Safari instead.'
      : '';
    showNotice('<strong>The microphone or speech recognition is blocked.</strong> On the iPad, open Settings &gt; Apps &gt; Safari &gt; Microphone and choose Ask or Allow, and make sure Settings &gt; Privacy &amp; Security &gt; Speech Recognition is allowed for Safari. Then tap Start again.' + hint);
  } else if (code === 'network') {
    setStatus('No connection to speech service, retrying…', true);
  } else if (code === 'language-not-supported') {
    stop();
    showNotice('This iPad cannot recognise the chosen language. Pick another language in Settings.');
  }
  // 'no-speech' and 'aborted' are normal; onend restarts the session.
}

function start() {
  if (!Recognition) return;
  showNotice('');
  running = true;
  recentEnds = [];
  els.start.textContent = 'Stop';
  els.start.classList.add('running');
  setStatus('Starting…', true);
  startSession();
  keepAwake();
}

function stop() {
  running = false;
  clearTimeout(restartTimer);
  if (rec) { try { rec.stop(); } catch { /* already stopped */ } }
  commitLine();
  els.start.textContent = 'Start';
  els.start.classList.remove('running');
  setStatus('Stopped');
  releaseWake();
}

// ---- Keep the screen on ----
let wakeLock = null;
async function keepAwake() {
  if (!settings.keepAwake || !running || !('wakeLock' in navigator) || document.hidden) return;
  try { wakeLock = await navigator.wakeLock.request('screen'); } catch { wakeLock = null; }
}
function releaseWake() {
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    if (rec && running) { try { rec.abort(); } catch { /* ignore */ } }
    commitLine();
    if (running) setStatus('Paused while in the background', true);
  } else if (running) {
    clearTimeout(restartTimer);
    restartTimer = setTimeout(startSession, 200);
    keepAwake();
  }
});

// ---- Copy, share, save ----
async function copyText(text, label) {
  if (!text) { flash('Nothing to copy yet'); return; }
  try {
    await navigator.clipboard.writeText(text);
    flash(label);
  } catch {
    // Fallback for older Safari.
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
    flash(label);
  }
}
let flashTimer = null;
function flash(text) {
  setStatus(text, running, true);
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => setStatus(baseStatus.text, baseStatus.live, true), 1800);
}

els.copyLast.onclick = () => copyText(state.lastLine(), 'Copied the latest line');
els.copyAll.onclick = () => copyText(els.transcript.value.trim(), 'Copied the transcript');
els.share.onclick = async () => {
  const text = els.transcript.value.trim();
  if (!text) { flash('Nothing to share yet'); return; }
  if (navigator.share) {
    try { await navigator.share({ title: 'Captions', text }); } catch { /* cancelled */ }
  } else {
    copyText(text, 'Sharing is not available here, so the transcript was copied');
  }
};
els.save.onclick = () => {
  const text = els.transcript.value;
  if (!text.trim()) { flash('Nothing to save yet'); return; }
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = fileName();
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
};
els.clear.onclick = () => {
  if (!els.transcript.value.trim() && state.lines.length === 0) return;
  if (!confirm('Clear the transcript and the captions on screen?')) return;
  els.transcript.value = '';
  store('cb.transcript', '');
  state.clear();
  render();
};
els.transcript.addEventListener('input', () => store('cb.transcript', els.transcript.value));

// ---- Settings UI ----
for (const [code, label] of LANGS) {
  const o = document.createElement('option');
  o.value = code; o.textContent = label;
  els.lang.appendChild(o);
}
els.lang.value = settings.lang;
els.size.value = settings.size;
els.lines.value = settings.lines;
els.theme.value = settings.theme;
els.keepAwake.checked = settings.keepAwake;

els.lang.onchange = () => {
  settings.lang = els.lang.value; saveSettings(); applySettings();
  if (running) { try { rec.stop(); } catch { /* onend restarts with the new language */ } }
};
els.size.oninput = () => { settings.size = Number(els.size.value); saveSettings(); applySettings(); };
els.lines.oninput = () => { settings.lines = Number(els.lines.value); saveSettings(); applySettings(); };
els.theme.onchange = () => { settings.theme = els.theme.value; saveSettings(); applySettings(); };
els.keepAwake.onchange = () => {
  settings.keepAwake = els.keepAwake.checked; saveSettings();
  if (settings.keepAwake) keepAwake(); else releaseWake();
};
els.settingsBtn.onclick = () => {
  const open = els.settings.hidden;
  els.settings.hidden = !open;
  els.settingsBtn.setAttribute('aria-expanded', String(open));
};
els.transcriptToggle.onclick = () => {
  settings.showTranscript = !settings.showTranscript; saveSettings(); applySettings();
};
els.start.onclick = () => (running ? stop() : start());

// ---- Start-up ----
applySettings();
if (!Recognition) {
  els.start.disabled = true;
  setStatus('Speech recognition unavailable');
  showNotice('This browser has no speech recognition. On iPad, open this page in <strong>Safari</strong> (iPadOS 14.5 or later).');
}
if ('serviceWorker' in navigator && location.protocol === 'https:') {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

// For automated tests only.
window.__captionbridge = { state, start, stop, commitLine };
