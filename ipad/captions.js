// Pure caption logic, with no browser APIs, so it can be unit tested in Node.

// Languages written without spaces between words.
const NO_SPACE_LANGS = ['zh', 'ja', 'th', 'yue'];

export function usesSpaces(lang) {
  const base = String(lang || '').toLowerCase().split('-')[0];
  return !NO_SPACE_LANGS.includes(base);
}

export function joinParts(parts, lang) {
  const sep = usesSpaces(lang) ? ' ' : '';
  return parts
    .map((p) => String(p || '').trim())
    .filter((p) => p.length > 0)
    .join(sep);
}

function normalize(s) {
  return String(s || '').trim().replace(/\s+/g, ' ').toLowerCase();
}

// Turns a SpeechRecognition results list into the text heard so far in this session.
// Safari sometimes repeats earlier text inside a later result, so a result that already
// contains everything before it replaces that text instead of being added after it.
export function sessionText(results, lang) {
  const parts = [];
  for (let i = 0; i < (results ? results.length : 0); i++) {
    const r = results[i];
    const alt = r && r[0];
    const t = alt && typeof alt.transcript === 'string' ? alt.transcript.trim() : '';
    if (!t) continue;
    const sofar = normalize(joinParts(parts, lang));
    if (sofar && normalize(t).startsWith(sofar)) {
      parts.length = 0;
    }
    parts.push(t);
  }
  return joinParts(parts, lang);
}

// Appends a finished caption line to the transcript text, one line per caption.
export function appendLine(transcript, line) {
  const clean = String(line || '').trim();
  if (!clean) return transcript || '';
  const base = String(transcript || '').replace(/\s+$/, '');
  return base ? base + '\n' + clean : clean;
}

// Tracks finished lines plus the line being spoken right now.
export class CaptionState {
  constructor(lang) {
    this.lang = lang || 'en-US';
    this.lines = [];
    this.live = '';
  }

  update(results) {
    this.live = sessionText(results, this.lang);
    return this.live;
  }

  // Ends the current line. Returns the finished text, or '' if nothing was heard.
  commit() {
    const line = this.live.trim();
    this.live = '';
    if (line) {
      this.lines.push(line);
      if (this.lines.length > 200) this.lines.splice(0, this.lines.length - 200);
    }
    return line;
  }

  clear() {
    this.lines = [];
    this.live = '';
  }

  // The most recent lines to show on screen, including the live one.
  tail(maxLines) {
    const all = this.live ? [...this.lines, this.live] : [...this.lines];
    return all.slice(-Math.max(1, maxLines || 1));
  }

  lastLine() {
    return this.live || this.lines[this.lines.length - 1] || '';
  }
}

export function fileName(date) {
  const d = date || new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `captions-${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.txt`;
}
