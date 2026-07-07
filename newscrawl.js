#!/usr/bin/env node
// newscrawl.js — a self-animating live-news marquee for Claude Code's statusLine.
//
// Claude Code's statusLine runs a command and prints its stdout. It re-invokes
// that command every ~1s while working, re-spawning a fresh process each time
// (stateless, 3s timeout). A verb spinner can't tell a story: it only swaps a
// verb when a new operation starts, so during a long quiet wait it freezes.
//
// This renders a horizontally-scrolling ticker of full headlines + summaries.
// The trick: the visible window is derived from WALL-CLOCK time, not from a
// counter we'd have to persist. offset = floor(now/step) % len. So every fresh
// invocation independently computes the correct frame for "now" — irregular
// statusLine cadence just lowers the framerate, it never desyncs the crawl.
//
// It never touches the network. A separate poller (spin.py, or refresh() below
// run from cron) writes the cache; this renderer only reads it, so it stays
// well under the statusLine timeout.
//
//   node newscrawl.js --render        # print one frame (what statusLine calls)
//   node newscrawl.js --refresh <url> # fetch feed -> cache (run from a poller)
//   node newscrawl.js --demo          # animate in-terminal so you can see it
//   node newscrawl.js --selftest      # internal checks
//
// Wire into statusLine by appending its stdout as a segment (see README notes).

const fs = require('fs');
const os = require('os');
const path = require('path');

const CACHE = process.env.SPIN_NEWS_CACHE
  || path.join(os.homedir(), '.claude', 'spinner-news-cache.json');

// Marquee tuning. Width can't come from statusLine (no COLUMNS in its stdin
// payload), so take it from env or fall back to a conservative default that
// leaves room for the model/dir/context segments on a normal terminal.
const WIDTH = parseInt(process.env.SPIN_STATUS_WIDTH || '48', 10);
const STEP_MS = parseInt(process.env.SPIN_STATUS_STEP_MS || '250', 10); // ms per 1-col shift
const SEP = '   •   ';
const DIM = '\x1b[2m', RESET = '\x1b[0m';

// --- cache read/write -------------------------------------------------------

// Cache record: { items, ts, url }. The renderer is a PURE READER — it never
// fetches or spawns. A separate poller (spin.py, this file's --refresh run from
// a scheduler) owns writes. That separation keeps render fast and side-effect
// free, and keeps the statusLine (a startup hook) from executing anything.
function readCache() {
  try {
    // Strip a UTF-8 BOM — some writers (PowerShell's Set-Content -Encoding utf8,
    // .NET) prepend one, and JSON.parse rejects a leading U+FEFF.
    const raw = fs.readFileSync(CACHE, 'utf8').replace(/^﻿/, '');
    const data = JSON.parse(raw);
    return Array.isArray(data.items) ? data.items : [];
  } catch (e) {
    return [];
  }
}

function writeCache(items, url) {
  const tmp = CACHE + '.tmp';
  fs.mkdirSync(path.dirname(CACHE), { recursive: true });
  const record = { items, ts: nowMs(), url: url || process.env.SPIN_NEWS_URL || null };
  fs.writeFileSync(tmp, JSON.stringify(record));
  fs.renameSync(tmp, CACHE); // atomic: a reader sees old or new, never half
}

// --- marquee ----------------------------------------------------------------

// One long ribbon of "TITLE — summary" chunks, separated. A trailing separator
// makes the wrap-around seamless (end flows back into the start).
function ribbon(items) {
  const chunks = items
    .map(it => {
      const title = String(it.title || '').trim();
      const summary = String(it.summary || '').trim();
      return summary ? `${title} — ${summary}` : title;
    })
    .filter(Boolean);
  if (!chunks.length) return '';
  return chunks.join(SEP) + SEP;
}

// Visible window of `width` columns starting at `offset`, wrapping around the
// ribbon so the ticker is an endless loop. Pure function of (text,width,offset)
// — that's what makes it testable and time-drivable.
function window(text, width, offset) {
  if (!text) return '';
  const n = text.length;
  const start = ((offset % n) + n) % n;
  const doubled = text + text; // cheap wrap without modulo-per-char
  return doubled.slice(start, start + Math.min(width, n));
}

function frameAt(items, ms, width = WIDTH, stepMs = STEP_MS) {
  const text = ribbon(items);
  if (!text) return '';
  const offset = Math.floor(ms / stepMs);
  return window(text, width, offset);
}

function render() {
  const frame = frameAt(readCache(), nowMs());
  if (!frame) return; // nothing cached -> print nothing, statusLine stays clean
  process.stdout.write(`${DIM}📰 ${frame}${RESET}`);
}

// --- refresh (poller side; only place that touches the network) -------------

function refresh(url) {
  return new Promise((resolve) => {
    const https = url.startsWith('https') ? require('https') : require('http');
    const req = https.get(url, { timeout: 8000 }, (res) => {
      let body = '';
      res.on('data', c => body += c);
      res.on('end', () => {
        try {
          const data = JSON.parse(body);
          writeCache(normalize(data.items || []), url);
          resolve(true);
        } catch (e) { resolve(false); }
      });
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

function normalize(raw) {
  const out = [];
  for (const it of raw || []) {
    if (it && typeof it === 'object') {
      const title = String(it.title || '').trim();
      const summary = String(it.summary || it.description || '').trim();
      if (title) out.push({ title, summary });
    } else if (it != null) {
      const title = String(it).trim();
      if (title) out.push({ title, summary: '' });
    }
  }
  return out;
}

// nowMs is isolated so tests can stub deterministic time.
function nowMs() { return Date.now(); }

// --- selftest ---------------------------------------------------------------

function selftest() {
  const items = [
    { title: 'Fed holds rates', summary: 'Powell signals a pause' },
    { title: 'Markets rip higher', summary: 'Tech leads the rally' },
  ];
  const text = ribbon(items);
  if (!text.endsWith(SEP)) throw new Error('ribbon must end with a separator for seamless wrap');

  // A frame is always exactly WIDTH cols (until the ribbon is shorter than WIDTH).
  const w = 20;
  const f0 = window(text, w, 0);
  if (f0.length !== w) throw new Error(`frame width ${f0.length} != ${w}`);

  // Time drives the scroll: a later timestamp yields a shifted window, and the
  // shift equals floor(dt/step) columns — the core self-sync guarantee.
  const a = frameAt(items, 0, w, 100);
  const b = frameAt(items, 100, w, 100);   // exactly one step later
  const c = frameAt(items, 300, w, 100);   // three steps later
  if (a === b) throw new Error('frame did not advance after one step');
  if (window(text, w, 1) !== b) throw new Error('one-step frame mismatch');
  if (window(text, w, 3) !== c) throw new Error('three-step frame mismatch');

  // Wrap-around: advancing by the ribbon length returns to the start frame.
  if (frameAt(items, 0, w, 100) !== frameAt(items, text.length * 100, w, 100))
    throw new Error('did not wrap to the same frame after a full loop');

  // Empty cache renders nothing (statusLine stays clean).
  if (frameAt([], 0) !== '') throw new Error('empty cache should render empty');

  console.log('ok');
}

// --- demo (in-terminal, so you can watch the crawl) -------------------------

function demo() {
  const items = readCache();
  const seed = items.length ? items : [
    { title: 'Fed holds rates steady', summary: 'Powell signals a possible pause on hikes' },
    { title: 'Markets rip higher', summary: 'Tech leads a broad rally into the close' },
    { title: 'Heat wave grips the east', summary: 'Grid operators warn of record demand' },
  ];
  const start = nowMs();
  const timer = setInterval(() => {
    const frame = frameAt(seed, nowMs() - start);
    process.stdout.write(`\r\x1b[K${DIM}📰 ${frame}${RESET}`);
  }, 80);
  process.on('SIGINT', () => { clearInterval(timer); process.stdout.write('\r\x1b[K\n'); process.exit(0); });
}

// --- cli --------------------------------------------------------------------

async function main() {
  const arg = process.argv[2];
  if (arg === '--selftest') return selftest();
  if (arg === '--demo') return demo();
  if (arg === '--refresh') {
    const url = process.argv[3] || process.env.SPIN_NEWS_URL;
    if (!url) { console.error('need a url: --refresh <url> or SPIN_NEWS_URL'); process.exit(2); }
    const ok = await refresh(url);
    console.log(ok ? `cached ${readCache().length} items -> ${CACHE}` : 'refresh failed (offline?)');
    return;
  }
  // default + --render: print one frame for statusLine to embed
  render();
}

module.exports = { ribbon, window, frameAt, normalize, readCache, writeCache };

if (require.main === module) main();
