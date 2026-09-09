"""Renders public/index.html -- a static page that reads its data at runtime.

Nothing is baked into the page but the code, so an hourly reading replaces a
couple of JSON objects in Blob and the site is current without a redeploy.

    index.json          loaded once: roster + each event's current numbers
    events/<slug>.json  loaded when you open an event

Three views over the same data: a Calendar of the month, a List of everything,
and Charts (a card per event). Opening one event shows a line per ticket
category (best ask, all-in, left axis) over a bar per category (tickets
available, right axis) on a shared hourly x-axis.
"""

import os
from pathlib import Path

import blob

HERE = Path(__file__).parent
# Chart.js is vendored so the page works offline and needs no third-party CDN.
VENDOR = HERE / "public" / "vendor" / "chart.umd.min.js"
CDN = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.5.0/chart.umd.min.js"

# dataviz reference palette, fixed slot order (never cycled).
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
                "#008300", "#4a3aa7", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181",
               "#008300", "#9085e9", "#e66767"]


def data_base():
    """Where the page fetches its JSON. Blob when configured, the local
    public/data/ mirror otherwise."""
    blob.load_env()
    override = os.environ.get("DATA_BASE")
    if override:
        return override.rstrip("/") + "/"
    base = blob.public_base()
    return base + blob.PREFIX if base else "data/"


HTML = r"""<!doctype html>
<html lang="en" data-theme="">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CrowdVolt NYC Tracker</title>
<meta name="description" content="Best ask and ticket availability per category for New York events, hourly.">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='26' font-size='26'>🎟️</text></svg>">
<script src="__CHART_JS__"></script>
<style>
:root {
  color-scheme: light;
  --plane:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
  --good:#0ca30c; --crit:#d03b3b; --wash:rgba(11,11,11,.045);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink-2:#c3c2b7;
    --muted:#898781; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
    --wash:rgba(255,255,255,.055);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink-2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
  --wash:rgba(255,255,255,.055);
}
* { box-sizing:border-box; }
body { margin:0; background:var(--plane); color:var(--ink);
       font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
       -webkit-font-smoothing:antialiased; }
button { font:inherit; color:inherit; }
a { color:inherit; }

header { display:flex; flex-wrap:wrap; gap:6px 18px; align-items:baseline;
         padding:12px 28px 9px; }
.brand { display:flex; align-items:baseline; gap:10px; }
h1 { font-size:18px; margin:0; letter-spacing:-.015em; font-weight:650; }
h1 em { font-style:normal; color:var(--muted); font-weight:500; }
.sub { color:var(--muted); font-size:12.5px; }
.spacer { flex:1 1 auto; }

.controls { display:flex; gap:8px; align-items:center; padding:0 28px 10px;
            border-bottom:1px solid var(--border); flex-wrap:wrap; }
.seg { display:inline-flex; border:1px solid var(--border); border-radius:9px;
       overflow:hidden; background:var(--surface); }
.seg button, .ghost { font-size:13px; color:var(--ink-2); background:transparent;
                      border:0; padding:6px 13px; cursor:pointer; }
.seg button[aria-pressed="true"] { background:var(--wash); color:var(--ink); font-weight:600; }
.ghost { border:1px solid var(--border); border-radius:9px; background:var(--surface); }
.ghost:hover, .seg button:hover { background:var(--wash); }
input[type=search] { font:inherit; font-size:13px; padding:6px 11px; border-radius:9px;
  border:1px solid var(--border); background:var(--surface); color:var(--ink); min-width:180px; }
label.chk { display:inline-flex; align-items:center; gap:6px; color:var(--muted); font-size:13px; }

main { padding:20px 28px 70px; max-width:1280px; }
/* the calendar is the one view that wants the whole window: full width, and
   tall enough that a month fits on one screen without the page scrolling */
body.tab-calendar main { max-width:none; padding:12px 28px 20px; }
#calendar { display:flex; flex-direction:column; min-height:0; }
.hide { display:none !important; }
.empty { color:var(--muted); padding:44px 0; }

/* stat strip -- inline in the header, so it costs a line of text, not a band */
.stats { display:flex; flex-wrap:wrap; align-items:baseline; color:var(--muted);
         font-size:12.5px; }
.stat b { font-weight:650; color:var(--ink); font-variant-numeric:tabular-nums; }
.stat + .stat::before { content:'·'; padding:0 7px; opacity:.55; }

/* genre legend + filter */
.genres { display:flex; flex-wrap:wrap; gap:5px; margin:0 0 10px; }
.gchip { display:inline-flex; align-items:center; gap:6px; padding:2px 9px 2px 7px;
         border:1px solid var(--border); border-radius:999px; background:var(--surface);
         font-size:11.5px; color:var(--ink-2); cursor:pointer; }
.gchip:hover { background:var(--wash); }
.gchip[aria-pressed="true"] { color:var(--ink); border-color:var(--ink-2);
                              box-shadow:inset 0 0 0 1px var(--ink-2); }
.gchip .gdot { width:8px; height:8px; border-radius:50%; flex:none; }
.gchip u { text-decoration:none; color:var(--muted); font-variant-numeric:tabular-nums; }

/* calendar */
.cal { display:grid; grid-template-columns:repeat(7,1fr);
       grid-template-rows:auto repeat(var(--calrows,6), minmax(0,1fr)); gap:1px;
       background:var(--border); border:1px solid var(--border);
       border-radius:12px; overflow:hidden;
       height:var(--calh, 620px); min-height:380px; }
.cal .dow { background:var(--plane); color:var(--muted); font-size:11.5px;
            text-align:center; padding:7px 0; letter-spacing:.02em; }
/* each day scrolls on its own, so a busy Saturday cannot stretch the row */
.cal .day { background:var(--surface); padding:4px 5px 5px; min-height:0;
            overflow-y:auto; overscroll-behavior:contain;
            scrollbar-width:thin; scrollbar-color:var(--axis) transparent; }
.cal .day::-webkit-scrollbar { width:6px; }
.cal .day::-webkit-scrollbar-thumb { background:var(--axis); border-radius:3px; }
.cal .day .n { position:sticky; top:0; background:inherit; z-index:1; }
.cal .day.out { background:var(--plane); }
.cal .day .n { color:var(--muted); font-size:11.5px; font-variant-numeric:tabular-nums;
               padding:0 2px 1px; }
.cal .day.today .n { color:var(--ink); font-weight:700; }
.evt { display:block; width:100%; text-align:left; background:transparent; border:0;
       border-left:3px solid transparent; border-radius:3px 7px 7px 3px;
       padding:3px 6px 3px 7px; margin-top:2px; cursor:pointer; font-size:12px;
       line-height:1.35; }
.evt:hover { background:var(--wash); }
.evt .thumb { width:16px; height:16px; border-radius:4px; object-fit:cover;
              background:var(--wash); }
.evt .nm { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.evt .pr { color:var(--muted); font-variant-numeric:tabular-nums; font-size:11px;
           white-space:nowrap; }
.evt.done { opacity:.45; }
/* in the grid an event is one line -- name left, price right -- so twice as
   many fit in a day before the cell has to scroll */
.cal .evt { display:flex; align-items:center; gap:5px; padding:2px 5px;
            margin-top:1px; font-size:11.5px; }
.cal .evt .thumb { width:14px; height:14px; border-radius:3px; flex:none; }
.cal .evt .nm { flex:1 1 auto; min-width:0; }
.cal .evt .pr { flex:none; min-width:26px; text-align:right; }
.cal .evt .mv { flex:none; font-style:normal; font-size:10.5px; min-width:26px;
                text-align:right; font-variant-numeric:tabular-nums; }

/* list */
table { border-collapse:collapse; width:100%; font-size:13px; font-variant-numeric:tabular-nums; }
th, td { text-align:right; padding:8px 11px; border-bottom:1px solid var(--grid); white-space:nowrap; }
th:first-child, td:first-child, th.l, td.l { text-align:left; }
th { color:var(--muted); font-weight:500; position:sticky; top:0; background:var(--plane);
     cursor:pointer; user-select:none; }
th[aria-sort]::after { content:'↑'; padding-left:5px; color:var(--ink-2); }
th[aria-sort="descending"]::after { content:'↓'; }
tbody tr { cursor:pointer; }
tbody tr:hover { background:var(--wash); }
/* the two free-text columns give way first, so the table stays inside its
   column rather than pushing the page sideways */
#list td.ev { max-width:255px; }
#list td.vn { max-width:155px; }
#list td.ev, #list td.vn { overflow:hidden; text-overflow:ellipsis; }
.up { color:var(--crit); } .down { color:var(--good); }
.sig { font-size:11px; margin-right:4px; font-style:normal; }
td .sig { margin-left:6px; margin-right:0; }
.badge { font-size:11.5px; color:var(--muted); border:1px solid var(--border);
         border-radius:999px; padding:1px 8px; }

/* card + detail */
.card { background:var(--surface); border:1px solid var(--border);
        border-radius:13px; padding:18px 18px 12px; margin-bottom:20px; }
.head { display:flex; gap:13px; align-items:flex-start; margin-bottom:14px; }
.head img { width:56px; height:56px; border-radius:10px; object-fit:cover;
            flex:none; background:var(--wash); }
.head .txt { min-width:0; }
.card h3 { font-size:16px; margin:0 0 2px; letter-spacing:-.01em; }
.card h3 a { text-decoration:none; }
.card h3 a:hover { text-decoration:underline; }
.meta { color:var(--muted); font-size:13px; }
.meta .sep { padding:0 5px; opacity:.5; }
.pills { display:flex; gap:6px; flex-wrap:wrap; margin-top:5px; }
.pill { font-size:11.5px; color:var(--muted); border:1px solid var(--border);
        border-radius:999px; padding:1px 8px; }
td .thumb, .lthumb { width:22px; height:22px; border-radius:5px; object-fit:cover;
                     vertical-align:-6px; margin-right:8px; background:var(--wash); }
/* artless events keep the same footprint so names stay on one left edge */
.ph { display:inline-block; background:var(--wash); }
.gdot { display:inline-block; width:9px; height:9px; border-radius:50%;
        margin-right:7px; vertical-align:0; }
.now { display:flex; flex-wrap:wrap; gap:8px 10px; margin:0 0 14px; padding:0; list-style:none; }
.now li { display:flex; }
.chip { display:flex; align-items:center; gap:8px; padding:7px 11px;
        border:1px solid var(--border); border-radius:9px; font-size:13px;
        background:transparent; cursor:pointer; transition:opacity .12s; }
.chip:hover { border-color:var(--axis); }
.chip[aria-pressed="true"] { box-shadow:inset 0 0 0 1px var(--ink-2); border-color:var(--ink-2); }
.chip[data-off] { opacity:.4; }
.swatch { width:10px; height:10px; border-radius:3px; flex:none; }
.chip b { font-weight:600; font-variant-numeric:tabular-nums; }
.chip span { color:var(--muted); font-variant-numeric:tabular-nums; }
.wrap { position:relative; height:320px; }
.wrap.qty { display:none; height:150px; }
.split .wrap.price { height:220px; }
.split .wrap.qty { display:block; }
.axisnote { color:var(--muted); font-size:12px; margin:6px 2px 0;
            display:flex; justify-content:space-between; }
details { margin-top:10px; border-top:1px solid var(--border); padding-top:8px; }
summary { cursor:pointer; color:var(--ink-2); font-size:13px; }
details table { margin-top:10px; font-size:12.5px; }
details th { position:static; background:transparent; cursor:default; }
details tbody tr { cursor:default; }
.tag { font-size:11.5px; color:var(--muted); border:1px solid var(--border);
       border-radius:999px; padding:2px 9px; margin-left:8px; vertical-align:2px; }

/* modal */
dialog { border:1px solid var(--border); border-radius:15px; background:var(--surface);
         color:var(--ink); padding:0; width:min(1080px,94vw); max-height:92vh; }
dialog::backdrop { background:rgba(0,0,0,.5); }
dialog .card { border:0; margin:0; background:transparent; }
.close { position:absolute; top:13px; right:15px; z-index:1; }
.modalctl { display:flex; gap:8px; flex-wrap:wrap; padding:16px 18px 0; }
/* agenda: the mobile shape of the calendar. A seven-column month grid on a
   phone is unreadable, so the same events become a scrolling day list. */
.agenda { display:flex; flex-direction:column; gap:2px; }
.agenda .d { position:sticky; top:0; z-index:1; background:var(--plane);
             color:var(--muted); font-size:12px; padding:10px 2px 4px;
             border-bottom:1px solid var(--border); }
.agenda .d.today { color:var(--ink); font-weight:650; }
/* on a phone the same event keeps its two lines -- there is width for the
   name and the numbers read better under it than squeezed beside it */
.agenda .evt { border-left-width:4px; padding:9px 10px; font-size:13.5px;
               border-radius:4px 9px 9px 4px; }
.agenda .evt .thumb { margin-right:6px; vertical-align:-3px; }
.agenda .evt .pr { display:block; padding-left:22px; font-size:12px; }

@media (max-width:820px) {
  /* title and Theme share the top line, the numbers get the next one, and the
     timestamp trails them -- rather than Theme drifting beside the timestamp */
  header { padding:12px 16px 8px; gap:3px 10px; align-items:center; }
  .brand { flex:1 1 auto; }
  .spacer { display:none; }
  #theme { order:1; }
  .stats { order:2; }
  #updated { order:3; font-size:11.5px; }
  h1 { font-size:17px; }
  .controls { padding:0 16px 12px; gap:6px; }
  .stats { font-size:12px; }
  .stat + .stat::before { padding:0 5px; }
  main, body.tab-calendar main { padding:14px 16px 48px; }
  input[type=search] { flex:1 1 100%; min-width:0; order:99; }
  label.chk { order:100; }                          /* below the search field */
  .seg button, .ghost { padding:8px 13px; }        /* fat-finger targets */
  .gchip { padding:7px 12px 7px 10px; }
  label.chk { padding:6px 0; }
  .head img { width:46px; height:46px; }
  .wrap { height:260px; }
  .split .wrap.price { height:190px; }
  /* the modal's data table scrolls sideways instead of squashing */
  details table { display:block; overflow-x:auto; white-space:nowrap; max-width:100%; }
  dialog { width:100vw; max-width:100vw; max-height:100dvh; height:100dvh;
           border:0; border-radius:0; }
  /* the dialog is the viewport here, so Close rides along instead of
     scrolling away with the top of the card */
  .close { position:fixed; }
  .modalctl { padding:14px 96px 0 14px; }           /* clears the fixed Close */
  .axisnote { font-size:11px; gap:10px; }
  .card { padding:14px 14px 10px; }
  .now li, .chip { max-width:100%; }
  .chip { min-width:0; }
}
/* Fourteen columns need about 1224px. Narrower than that the table takes its
   own horizontal scrollbar rather than widening the document -- and since it
   is scrolling anyway, the clipped columns get their full text back. Above it
   the table fits and the sticky header floats against the viewport. */
@media (max-width:1300px) {
  #list table { display:block; overflow-x:auto; white-space:nowrap; max-width:100%;
                overscroll-behavior-x:contain; }
  #list td.ev, #list td.vn { max-width:none; }
}
@media (max-width:420px) {
  .stats .stat:nth-child(n+4) { display:none; }
  .modalctl { gap:6px; }
}
</style>
</head>
<body data-palette="__PALETTE__">
<header>
  <div class="brand"><h1>CrowdVolt <em>NYC</em> Tracker</h1></div>
  <div class="stats" id="stats"></div>
  <div class="spacer"></div>
  <span class="sub" id="updated"></span>
  <button class="ghost" id="theme">Theme</button>
</header>

<div class="controls">
  <span class="seg" role="group" aria-label="View">
    <button data-tab="calendar" aria-pressed="true">Calendar</button>
    <button data-tab="list" aria-pressed="false">List</button>
    <button data-tab="charts" aria-pressed="false">Charts</button>
  </span>
  <span class="calctl seg" id="monthNav" role="group" aria-label="Month">
    <button id="prevM" aria-label="Previous month">‹</button>
    <button id="thisM">Today</button>
    <button id="nextM" aria-label="Next month">›</button>
  </span>
  <span class="calctl sub" id="calnote"></span>
  <input type="search" id="q" placeholder="Filter events, venues, cities">
  <span class="chartctl seg" role="group" aria-label="Time range">
    <button data-range="24" aria-pressed="false">24h</button>
    <button data-range="168" aria-pressed="false">7d</button>
    <button data-range="0" aria-pressed="true">All</button>
  </span>
  <span class="chartctl seg" role="group" aria-label="Chart layout">
    <button data-view="combined" aria-pressed="true">Combined</button>
    <button data-view="split" aria-pressed="false">Split axes</button>
  </span>
  <div class="spacer"></div>
  <label class="chk"><input type="checkbox" id="showRetired"> show retired</label>
</div>

<main>
  <section id="calendar"></section>
  <section id="list" class="hide"></section>
  <section id="charts" class="hide"></section>
</main>

<dialog id="modal"><button class="ghost close" id="modalClose">Close</button>
  <div id="modalBody"></div>
</dialog>

<script>
const DATA_BASE = "__DATA_BASE__";
const LIGHT = __SERIES_LIGHT__, DARK = __SERIES_DARK__;

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = t => String(t ?? '').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const isDark = () => document.documentElement.dataset.theme === 'dark' ||
  (document.documentElement.dataset.theme !== 'light' &&
   matchMedia('(prefers-color-scheme: dark)').matches);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const colors = () => isDark() ? DARK : LIGHT;
const money = v => v == null ? '—' : (v < 0 ? '−$' : '$') + Math.abs(Math.round(v));
const delta = v => {
  if (v == null) return '—';
  const n = Math.round(v);
  return n === 0 ? '—' : (n > 0 ? '+$' : '−$') + Math.abs(n);
};
const lastDefined = a => { for (let i = a.length - 1; i >= 0; i--) if (a[i] != null) return a[i]; return null; };

let INDEX = {}, ORDER = [], GENERATED = null;
let GENRE_SLOT = {}, GENRE_RANK = [];   // genre -> palette slot, computed once
const pickedGenres = new Set();
const HIST = {};                    // slug -> history, fetched on demand
let tab = 'calendar', range = 0, view = 'combined';
let showRetired = false, query = '', sortKey = 'date', sortDir = 1;
let month = new Date(); month.setDate(1);
const filters = {};                 // slug -> category name
const charts = [];
let openSlug = null;

/* ---------------- data ---------------- */

async function boot() {
  try {
    const idx = await fetch(DATA_BASE + 'index.json', {cache: 'no-cache'}).then(r => r.json());
    INDEX = idx.events || {};
    GENERATED = idx.generated_at || null;
  } catch (e) {
    $('main').innerHTML = `<p class="empty">Could not load <code>${esc(DATA_BASE)}index.json</code>.
      Run <code>python3 track.py</code>, or <code>python3 track.py --serve</code> locally.</p>`;
    return;
  }
  ORDER = Object.keys(INDEX);
  buildGenres();
  $('#updated').textContent = GENERATED
    ? 'updated ' + new Date(GENERATED).toLocaleString([], {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'})
    : '';
  renderStats();
  render();
}

async function history(slug) {
  if (!HIST[slug]) {
    HIST[slug] = fetch(`${DATA_BASE}events/${encodeURIComponent(slug)}.json`)
      .then(r => r.ok ? r.json() : {stamps: [], types: {}})
      .catch(() => ({stamps: [], types: {}}));
  }
  return HIST[slug];
}

/* ---------------- selectors ---------------- */

const evOf = slug => ({slug, ...INDEX[slug]});
const imgOf = e => e.img_blob || e.img || null;
const genreOf = e => (e.genres && e.genres[0]) || null;

/* One glyph, three states, only when the data actually says something:
   at its cheapest all week, drifting down, or drifting up. */
function signalOf(e) {
  const c = e.current || {};
  const d = c.change24 ?? c.change3d ?? null;
  const pct = d != null && c.floor ? d / (c.floor - d) : null;
  if (c.at_low && c.readings > 3 && (c.change7d ?? 0) < 0) return {
    icon: '🔥', cls: 'down', title: `cheapest in 7 days (${money(c.floor)})`};
  if (pct != null && pct <= -0.03) return {
    icon: '▾', cls: 'down', title: `down ${money(Math.abs(d))} recently`};
  if (pct != null && pct >= 0.03) return {
    icon: '▴', cls: 'up', title: `up ${money(d)} recently`};
  return null;
}
const signal = e => {
  const s = signalOf(e);
  return s ? `<span class="sig ${s.cls}" title="${esc(s.title)}">${s.icon}</span>` : '';
};

/* The calendar's right-hand slot: how far the price moved in a day, as the
   glyph plus its size. A price that has not moved says nothing rather than
   printing a dash in every cell; the flame still outranks a plain arrow. The
   element is always emitted, empty or not, so the prices beside it stay in a
   column. */
function move24(e) {
  const s = signalOf(e), d = Math.round(e.current?.change24 ?? 0);
  let cls = '', txt = '', tip = '';
  if (s && s.icon === '🔥') { cls = 'down'; txt = '🔥'; tip = s.title; }
  else if (d) {
    cls = d > 0 ? 'up' : 'down';
    txt = (d > 0 ? '▴' : '▾') + money(Math.abs(d));
    tip = (d > 0 ? 'up ' : 'down ') + money(Math.abs(d)) + ' in the last 24h';
  }
  return `<em class="mv ${cls}"${tip ? ` title="${esc(tip)}"` : ''}>${txt}</em>`;
}

/* The genre -> colour map is built once, from every tracked event, so that
   filtering never repaints the genres that survive the filter. Past the
   palette's eight slots genres fold into a neutral "other" rather than
   inventing hues. */
function buildGenres() {
  const counts = {};
  ORDER.map(evOf).forEach(e => {
    const g = genreOf(e);
    if (g) counts[g] = (counts[g] || 0) + 1;
  });
  GENRE_RANK = Object.entries(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  GENRE_SLOT = {};
  GENRE_RANK.slice(0, 7).forEach(([g], i) => GENRE_SLOT[g] = i);
}
const genreColor = g => g != null && g in GENRE_SLOT ? colors()[GENRE_SLOT[g]] : css('--muted');
const genreLabel = e => genreOf(e) || 'other';
const genreKey = e => (genreOf(e) in GENRE_SLOT) ? genreOf(e) : 'other';
const thumb = (e, cls) => {
  const src = imgOf(e);
  return src ? `<img class="${cls}" src="${esc(src)}" alt="" loading="lazy" decoding="async">`
             : `<i class="${cls} ph"></i>`;
};
function visible() {
  const q = query.trim().toLowerCase();
  return ORDER.map(evOf)
    .filter(e => showRetired || e.status === 'active')
    .filter(e => !pickedGenres.size || pickedGenres.has(genreKey(e)))
    .filter(e => !q || [e.name, e.venue, e.city, ...(e.genres || [])]
      .join(' ').toLowerCase().includes(q));
}
const floorOf = e => e.current?.floor ?? null;
const ticketsOf = e => e.current?.tickets ?? 0;
/* What the cheapest ask wants over what somebody last actually paid. Below the
   last sale is the good side of this one -- the opposite polarity to the
   change columns, where it is a rising price that is the bad news. */
const vsSale = e => {
  const c = e.current || {};
  return c.floor != null && c.last_sale != null ? Math.round(c.floor - c.last_sale) : null;
};
function eventDate(e) {
  if (!e.local_date) return null;
  const [y, m, d] = e.local_date.split('-').map(Number);
  return new Date(y, m - 1, d);
}
const dayKey = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
                    + '-' + String(d.getDate()).padStart(2, '0');
const label = ts => new Date(ts).toLocaleString([], {month:'short', day:'numeric',
                                                     hour:'numeric', minute:'2-digit'});
const startOfToday = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; };

/* series arrays out of the column-oriented history */
function seriesOf(h) {
  return Object.entries(h.types || {}).map(([name, t], i) => ({name, idx: i, ...t}));
}
function slice(h) {
  const n = (h.stamps || []).length, from = range ? Math.max(0, n - range) : 0;
  return {
    labels: (h.stamps || []).slice(from).map(label),
    series: seriesOf(h).map(s => ({
      name: s.name, idx: s.idx,
      ask: s.all_in.slice(from), base: s.ask.slice(from),
      bid: s.bid.slice(from), qty: s.qty.slice(from), listings: s.listings.slice(from),
    })),
  };
}
const shownSeries = (slug, d) =>
  filters[slug] ? d.series.filter(s => s.name === filters[slug]) : d.series;

/* ---------------- chart plumbing ---------------- */

function datasets(series, kind) {
  const pal = colors(), hue = s => pal[s.idx % pal.length], out = [];
  if (kind !== 'qty-only') series.forEach(s => out.push({
    type:'line', label: s.name + ' — ask', data: s.ask,
    borderColor: hue(s), backgroundColor: hue(s), borderWidth: 2,
    pointRadius: 0, pointHoverRadius: 5, tension: .25, spanGaps: true,
    yAxisID: 'y', order: 1,
  }));
  if (kind !== 'ask-only') series.forEach(s => out.push({
    type:'bar', label: s.name + ' — tickets', data: s.qty,
    backgroundColor: hue(s) + (kind === 'qty-only' ? 'cc' : '2b'),
    borderColor: hue(s) + (kind === 'qty-only' ? 'cc' : '45'),
    borderWidth: 1, borderRadius: 3, borderSkipped: false,
    categoryPercentage: .8, barPercentage: .9,
    yAxisID: kind === 'qty-only' ? 'y' : 'y1', order: 2,
  }));
  return out;
}

function priceBounds(series) {
  const vals = series.flatMap(s => s.ask).filter(v => v != null);
  if (!vals.length) return {};
  const lo = Math.min(...vals), hi = Math.max(...vals);
  // headroom below the floor price so the line is not glued to the axis,
  // without flattening the movement the way a $0 baseline does
  const pad = Math.max(2, (hi - lo) * 0.35, hi * 0.04);
  return {min: Math.max(0, Math.floor((lo - pad) / 5) * 5),
          max: Math.ceil((hi + pad) / 5) * 5};
}

function options(kind, bounds) {
  // on a phone the plot is barely 300px wide: two rotated axis titles would
  // cost a fifth of it, and the note under the chart already says which side
  // is which
  const tight = isPhone();
  const grid = {color: css('--grid'), drawTicks: false};
  const ticks = {color: css('--muted'), font: {size: tight ? 10 : 11},
                 maxRotation: 0, autoSkipPadding: tight ? 26 : 18};
  const axisTitle = t => ({display: !tight, text: t,
                           color: css('--muted'), font: {size: 11}});
  const scales = {
    x: {grid: {...grid, display: false}, border: {color: css('--axis')},
        ticks: {...ticks, display: kind !== 'ask-only'}},
    y: {
      position:'left', grid, border: {display: false},
      // the price axis sits just below the floor rather than at zero: a $6
      // move on a $90 ticket is the whole story and a $0 baseline flattens it.
      // A count of tickets is a different quantity and does start at zero.
      ...(kind === 'qty-only' ? {beginAtZero: true} : bounds || {}),
      ticks: {...ticks, callback: v => kind === 'qty-only' ? v : '$' + v},
      title: axisTitle(kind === 'qty-only' ? 'Tickets available' : 'Best ask, all-in'),
    },
  };
  if (kind === 'both') scales.y1 = {
    position:'right', grid: {display: false}, border: {display: false},
    beginAtZero: true, ticks: {...ticks, precision: 0},
    title: axisTitle('Tickets available'),
  };
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: {mode: 'index', intersect: false}, scales,
    plugins: {
      legend: {display: kind !== 'qty-only',
        labels: {color: css('--ink-2'), boxWidth: 10, boxHeight: 10, usePointStyle: true,
                 font: {size: tight ? 11 : 12}, padding: tight ? 10 : 16,
                 filter: it => !it.text.endsWith('— tickets') || kind === 'qty-only'}},
      tooltip: {
        backgroundColor: css('--surface'), titleColor: css('--ink'), bodyColor: css('--ink-2'),
        borderColor: css('--border'), borderWidth: 1, padding: 10, boxPadding: 4,
        usePointStyle: true,
        callbacks: {label: c => c.dataset.label.endsWith('— tickets')
          ? `${c.dataset.label.replace(' — tickets','')}: ${c.parsed.y} available`
          : `${c.dataset.label.replace(' — ask','')}: ${money(c.parsed.y)} all-in`},
      },
    },
  };
}

/* ---------------- one event ---------------- */

function nowStrip(slug, cats) {
  const pal = colors(), picked = filters[slug];
  return cats.map((c, i) =>
    `<li><button class="chip" data-cat="${esc(c.name)}"
      aria-pressed="${picked === c.name}" ${!picked || picked === c.name ? '' : 'data-off="1"'}
      title="${picked === c.name ? 'Show all categories' : 'Show only ' + esc(c.name)}">
      <i class="swatch" style="background:${pal[i % pal.length]}"></i>
      ${esc(c.name)} <b>${c.ask == null ? 'no asks' : money(c.ask)}</b>
      <span>· ${c.qty} ticket${c.qty === 1 ? '' : 's'}</span></button></li>`).join('');
}

function dataTable(slug, h) {
  const d = slice(h), series = shownSeries(slug, d);
  if (!series.length) return '<p class="sub">No readings yet.</p>';
  const head = ['Time', ...series.flatMap(s => [s.name + ' ask', s.name + ' qty'])];
  const body = d.labels.map((l, r) => '<tr><td>' + esc(l) + '</td>' +
    series.map(s => `<td>${s.ask[r] == null ? '—' : money(s.ask[r])}</td>` +
                    `<td>${s.qty[r] ?? '—'}</td>`).join('') + '</tr>').reverse().join('');
  return `<table><thead><tr>${head.map(x => `<th>${esc(x)}</th>`).join('')}</tr></thead>
          <tbody>${body}</tbody></table>`;
}

function cardShell(e, id) {
  const where = [e.venue, e.city].filter(Boolean).join(' · ');
  const tag = e.status === 'active' ? '' :
    `<span class="tag">${esc(String(e.status).replace('_',' '))}${e.retired_reason
      ? ' — ' + esc(e.retired_reason.split(': ').pop()) : ''}</span>`;
  const c = e.current || {};
  const spread = c.floor != null && c.bid != null ? c.floor - c.bid : null;
  const vs = vsSale(e);
  const pills = [
    c.at_low && c.readings > 3 && '🔥 cheapest in 7 days',
    c.last_sale != null && `last sale ${money(c.last_sale)}`,
    vs ? `${money(Math.abs(vs))} ${vs > 0 ? 'above' : 'below'} last sale` : null,
    c.bid != null && `best bid ${money(c.bid)}`,
    spread != null && `spread ${money(spread)}`,
    c.bidders ? `${c.bidders} bidder${c.bidders === 1 ? '' : 's'}` : null,
    ...(e.genres || []).slice(0, 3),
    e.platform, e.ticket_limit && `limit ${e.ticket_limit}`,
    (e.performers || []).map(p => p.name).join(', ') || null,
  ].filter(Boolean).map(t => `<span class="pill">${esc(t)}</span>`).join('');
  const art = imgOf(e)
    ? `<img src="${esc(imgOf(e))}" alt="" loading="lazy" decoding="async">` : '';
  return `<div class="head">${art}<div class="txt">
      <h3><a href="${esc(e.url)}" target="_blank" rel="noreferrer">${esc(e.name || e.slug)}</a>${tag}</h3>
      <div class="meta">${esc(where)}${where && e.doors ? '<span class="sep">·</span>' : ''}${esc(e.doors || '')}</div>
      <div class="pills">${pills}</div>
    </div></div>
    <ul class="now">${nowStrip(e.slug, e.current?.cats || [])}</ul>
    <div class="wrap price"><canvas id="c-${id}"></canvas></div>
    <div class="wrap qty"><canvas id="q-${id}"></canvas></div>
    <div class="axisnote"><span>Left: best ask (all-in)</span><span>Right: tickets available</span></div>
    <details class="tbl"><summary>Data table</summary><div></div></details>`;
}

async function wireCard(root, e, id) {
  const h = await history(e.slug);
  if (!root.isConnected) return;
  const d = slice(h), series = shownSeries(e.slug, d);
  const combined = view === 'combined';
  const c = $(`#c-${id}`, root);
  if (!c) return;
  // plenty of events are tracked before anything is ever listed on them. An
  // axis running $0 to $1 over an empty plot says less than one line of text.
  const live = series.some(s => s.ask.some(v => v != null) || s.qty.some(v => v));
  if (!live) {
    $$('.wrap', root).forEach(w => w.classList.add('hide'));
    $('.axisnote', root).innerHTML = `<span>${(h.stamps || []).length
      ? 'Nothing listed yet — no asks and no tickets in any reading so far.'
      : 'No readings yet.'}</span>`;
  } else {
    charts.push(new Chart(c, {
      data: {labels: d.labels, datasets: datasets(series, combined ? 'both' : 'ask-only')},
      options: options(combined ? 'both' : 'ask-only', priceBounds(series)),
    }));
    if (!combined) charts.push(new Chart($(`#q-${id}`, root), {
      data: {labels: d.labels, datasets: datasets(series, 'qty-only')},
      options: options('qty-only'),
    }));
    $('.axisnote', root).style.display = combined ? 'flex' : 'none';
  }
  // the table is built on first open -- rendering every row of every event up
  // front is what turns a hundred tracked events into a heavy page
  const det = $('.tbl', root);
  const fill = () => { if (det.open) $('div', det).innerHTML = dataTable(e.slug, h); };
  det.ontoggle = fill; fill();
  $$('.chip', root).forEach(chip => chip.onclick = () => {
    filters[e.slug] = filters[e.slug] === chip.dataset.cat ? undefined : chip.dataset.cat;
    render();
  });
}

/* ---------------- views ---------------- */

function renderStats() {
  const all = ORDER.map(evOf), act = all.filter(e => e.status === 'active');
  const withAsk = act.filter(e => floorOf(e) != null);
  const tickets = act.reduce((n, e) => n + ticketsOf(e), 0);
  const median = withAsk.length
    ? withAsk.map(floorOf).sort((a, b) => a - b)[Math.floor(withAsk.length / 2)] : null;
  const t0 = startOfToday();
  const soon = act.filter(e => {
    const d = eventDate(e); return d && d >= t0 && (d - t0) / 86400000 <= 7;
  }).length;
  $('#stats').innerHTML = [
    [act.length, 'events', 'events tracked'],
    [soon, 'in 7d', 'starting in the next 7 days'],
    [tickets.toLocaleString(), 'tickets', 'tickets listed'],
    [median == null ? '—' : money(median), 'median', 'median floor price'],
    [all.length - act.length, 'retired', 'no longer tracked'],
  ].map(([v, k, t]) => `<span class="stat" title="${esc(t)}"><b>${v}</b> ${k}</span>`).join('');
}

function genreBar() {
  if (!GENRE_RANK.length) return '';
  const rows = GENRE_RANK.slice(0, 7).map(([g, n]) => [g, n]);
  // "other" is the filter bucket for everything outside the palette's slots,
  // so its count has to include the events that carry no genre at all
  const otherN = GENRE_RANK.slice(7).reduce((a, [, n]) => a + n, 0)
    + ORDER.map(evOf).filter(e => !genreOf(e)).length;
  if (otherN) rows.push(['other', otherN]);
  return `<div class="genres">${rows.map(([g, n]) =>
    `<button class="gchip" data-genre="${esc(g)}" aria-pressed="${pickedGenres.has(g)}">
      <i class="gdot" style="background:${genreColor(g)}"></i>${esc(g)} <u>${n}</u>
    </button>`).join('')}</div>`;
}

function wireGenreBar(host) {
  $$('.gchip', host).forEach(b => b.onclick = () => {
    const g = b.dataset.genre;
    pickedGenres.has(g) ? pickedGenres.delete(g) : pickedGenres.add(g);
    render();
  });
}

const isPhone = () => matchMedia('(max-width: 820px)').matches;

function fitCalendar() {
  const cal = $('.cal');
  if (!cal) return;
  const top = cal.getBoundingClientRect().top + scrollY;
  const gap = parseFloat(getComputedStyle($('main')).paddingBottom) || 0;
  document.documentElement.style.setProperty(
    '--calh', Math.max(380, Math.floor(innerHeight - top - gap)) + 'px');
}

function renderAgenda(host) {
  const rows = visible().filter(e => eventDate(e))
    .sort((a, b) => (a.starts_ts || 0) - (b.starts_ts || 0));
  const todayK = dayKey(new Date());
  const groups = {};
  rows.forEach(e => (groups[dayKey(eventDate(e))] ||= []).push(e));
  const body = Object.entries(groups).map(([k, list]) => {
    const d = new Date(k + 'T00:00:00');
    return `<div class="d${k === todayK ? ' today' : ''}">${
      d.toLocaleDateString([], {weekday:'short', month:'short', day:'numeric'})}</div>` +
      list.map(e => `<button class="evt${e.status === 'active' ? '' : ' done'}"
        data-slug="${esc(e.slug)}" style="border-left-color:${genreColor(genreOf(e))}">
        <span class="nm">${thumb(e, 'thumb')}${esc(e.name || e.slug)}</span>
        <span class="pr">${signal(e)}${floorOf(e) == null ? 'no asks' : money(floorOf(e))} · ${
          ticketsOf(e)} tix${e.venue ? ' · ' + esc(e.venue) : ''}</span>
      </button>`).join('');
  }).join('');
  $('#calnote').textContent = `${rows.length} upcoming`;
  host.innerHTML = genreBar() +
    (body ? `<div class="agenda">${body}</div>` : '<p class="empty">Nothing matches.</p>');
  wireGenreBar(host);
  $$('.evt', host).forEach(b => b.onclick = () => openEvent(b.dataset.slug));
  fitCalendar();
}

function renderCalendar() {
  const host = $('#calendar');
  if (isPhone()) return renderAgenda(host);
  const first = new Date(month), y = first.getFullYear(), m = first.getMonth();
  const offset = (first.getDay() + 6) % 7;                        // weeks start Monday
  const start = new Date(y, m, 1 - offset);
  // only the weeks the month actually touches -- a fixed six-row grid spends a
  // seventh of the screen on a row that belongs entirely to the next month
  const weeks = Math.ceil((offset + new Date(y, m + 1, 0).getDate()) / 7);
  const buckets = {};
  visible().forEach(e => {
    const d = eventDate(e);
    if (d) (buckets[dayKey(d)] ||= []).push(e);
  });
  const today = dayKey(new Date());
  let cells = '';
  for (let i = 0; i < weeks * 7; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    const k = dayKey(d), list = (buckets[k] || []).sort((a, b) =>
      (floorOf(a) ?? 1e9) - (floorOf(b) ?? 1e9));
    cells += `<div class="day${d.getMonth() === m ? '' : ' out'}${k === today ? ' today' : ''}">
      <div class="n">${d.getDate()}</div>
      ${list.map(e => `<button class="evt${e.status === 'active' ? '' : ' done'}"
          data-slug="${esc(e.slug)}" style="border-left-color:${genreColor(genreOf(e))}"
          title="${esc([e.name, e.venue, genreOf(e)].filter(Boolean).join(' — ') + ' — '
            + (floorOf(e) == null ? 'no asks' : money(floorOf(e)) + ' floor')
            + ', ' + ticketsOf(e) + ' tickets')}">
          ${thumb(e, 'thumb')}<span class="nm">${esc(e.name || e.slug)}</span>
          <span class="pr">${floorOf(e) == null ? '—' : money(floorOf(e))}</span>${move24(e)}
        </button>`).join('')}
    </div>`;
  }
  const dows = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(x => `<div class="dow">${x}</div>`).join('');
  const shown = visible().filter(e => {
    const d = eventDate(e); return d && d.getFullYear() === y && d.getMonth() === m;
  }).length;
  $('#calnote').textContent = first.toLocaleString([], {month:'long', year:'numeric'})
    + ' · ' + shown + (shown === 1 ? ' event' : ' events');
  host.innerHTML = genreBar() + `<div class="cal" style="--calrows:${weeks}">${dows}${cells}</div>`;
  wireGenreBar(host);
  $$('.evt', host).forEach(b => b.onclick = () => openEvent(b.dataset.slug));
  fitCalendar();
}

const COLUMNS = [
  ['date',    'Date',    'l', e => e.starts_ts ?? Infinity],
  ['in',      'In',      'l', e => e.starts_ts ?? Infinity],
  ['name',    'Event',   'l', e => (e.name || e.slug).toLowerCase()],
  ['sale',    'Last sale', '', e => e.current?.last_sale ?? -1],
  // nothing to compare sorts to the end rather than reading as a big discount
  ['vsale',   'vs sale', '', e => vsSale(e) ?? Infinity],
  ['venue',   'Venue',   'l', e => (e.venue || '').toLowerCase()],
  ['city',    'City',    'l', e => (e.city || '').toLowerCase()],
  ['genre',   'Genre',   'l', e => genreLabel(e).toLowerCase()],
  ['floor',   'Floor',   '',  e => floorOf(e) ?? Infinity],
  ['change',  '24h',     '',  e => e.current?.change24 ?? 0],
  ['change3', '3d',      '',  e => e.current?.change3d ?? 0],
  ['change7', '7d',      '',  e => e.current?.change7d ?? 0],
  ['tickets', 'Tickets', '',  e => ticketsOf(e)],
  ['cats',    'Cats',    '',  e => (e.current?.cats || []).length],
];

function renderList() {
  const get = Object.fromEntries(COLUMNS.map(c => [c[0], c[3]]));
  const rows = visible().sort((a, b) => {
    const x = get[sortKey](a), y = get[sortKey](b);
    return (x > y ? 1 : x < y ? -1 : 0) * sortDir;
  });
  const t0 = startOfToday();
  const body = rows.map(e => {
    const c = e.current || {}, d = eventDate(e), p = floorOf(e), vs = vsSale(e);
    const days = d ? Math.round((d - t0) / 86400000) : null;
    return `<tr data-slug="${esc(e.slug)}">
      <td class="l">${d ? d.toLocaleDateString([], {month:'short', day:'numeric'}) : '—'}</td>
      <td class="l">${days == null ? '' : days < 0 ? 'past' : days === 0 ? 'today' : days + 'd'}</td>
      <td class="l ev" title="${esc(e.name || e.slug)}">${thumb(e, 'lthumb')}${esc(e.name || e.slug)}${signal(e)}</td>
      <td>${e.current?.last_sale == null ? '—' : money(e.current.last_sale)}</td>
      <td class="${!vs ? '' : vs > 0 ? 'up' : 'down'}"${vs ? ` title="floor is ${
        money(Math.abs(vs))} ${vs > 0 ? 'above' : 'below'} the last sale"` : ''}>${
        vs ? delta(vs) : '—'}</td>
      <td class="l vn" title="${esc(e.venue || '')}">${esc(e.venue || '')}</td>
      <td class="l">${esc(e.city || '')}</td>
      <td class="l"><i class="gdot" style="background:${genreColor(genreOf(e))}"></i>${esc(genreLabel(e))}</td>
      <td>${p == null ? '—' : money(p)}</td>
      ${[c.change24, c.change3d, c.change7d].map(v =>
        `<td class="${!v || !Math.round(v) ? '' : v > 0 ? 'up' : 'down'}">${delta(v)}</td>`).join('')}
      <td>${ticketsOf(e)}</td>
      <td>${(e.current?.cats || []).length}</td>
      <td class="l">${e.status === 'active' ? '' : `<span class="badge">${esc(e.status)}</span>`}</td>
    </tr>`;
  }).join('');
  $('#list').innerHTML = genreBar() + (rows.length ? `<table><thead><tr>${
    COLUMNS.map(([k, t, cls]) => `<th data-sort="${k}" class="${cls}"${
      sortKey === k ? ` aria-sort="${sortDir > 0 ? 'ascending' : 'descending'}"` : ''}>${t}</th>`).join('')
    }<th class="l"></th></tr></thead><tbody>${body}</tbody></table>`
    : '<p class="empty">Nothing matches.</p>');
  wireGenreBar($('#list'));
  $$('#list th[data-sort]').forEach(th => th.onclick = () => {
    const k = th.dataset.sort;
    sortDir = sortKey === k ? -sortDir : 1;
    sortKey = k;
    renderList();
  });
  $$('#list tbody tr').forEach(tr => tr.onclick = () => openEvent(tr.dataset.slug));
}

function renderCharts() {
  const host = $('#charts');
  host.innerHTML = '';
  const list = visible();
  if (!list.length) { host.innerHTML = '<p class="empty">Nothing matches.</p>'; return; }
  list.forEach((e, i) => {
    const el = document.createElement('section');
    el.className = 'card';
    el.innerHTML = cardShell(e, 'card' + i);
    host.appendChild(el);
    // history is fetched, and the chart built, as the card scrolls into view
    const io = new IntersectionObserver(ents => {
      if (ents.some(x => x.isIntersecting)) { io.disconnect(); wireCard(el, e, 'card' + i); }
    }, {rootMargin: '300px'});
    io.observe(el);
  });
}

function chartToolbar() {
  const seg = (attr, opts) => '<span class="seg" role="group">' + opts.map(([v, t]) =>
    `<button ${attr}="${v}" aria-pressed="${
      (attr === 'data-range' && range === +v) ||
      (attr === 'data-view' && view === v)}">${t}</button>`).join('') + '</span>';
  return `<div class="modalctl">
    ${seg('data-range', [['24','24h'],['168','7d'],['0','All']])}
    ${seg('data-view', [['combined','Combined'],['split','Split axes']])}
  </div>`;
}

function openEvent(slug) {
  const e = INDEX[slug] && evOf(slug);
  if (!e) return;
  openSlug = slug;
  const body = $('#modalBody');
  body.innerHTML = chartToolbar() + `<div class="card">${cardShell(e, 'modal')}</div>`;
  const dlg = $('#modal');
  if (!dlg.open) dlg.showModal();
  wireCard(body, e, 'modal');
  wireSegments();
}

/* ---------------- render + wiring ---------------- */

function render() {
  charts.splice(0).forEach(c => c.destroy());
  document.body.classList.toggle('split', view === 'split');
  document.body.classList.toggle('tab-calendar', tab === 'calendar');
  ['calendar','list','charts'].forEach(t => $('#' + t).classList.toggle('hide', t !== tab));
  $$('.chartctl').forEach(el => el.classList.toggle('hide', tab !== 'charts'));
  $$('.calctl').forEach(el => el.classList.toggle('hide', tab !== 'calendar'));
  // the agenda is one continuous upcoming list, so a month stepper means
  // nothing there -- keep the note, drop the arrows
  $('#monthNav').classList.toggle('hide', tab !== 'calendar' || isPhone());
  if (tab === 'calendar') renderCalendar();
  if (tab === 'list') renderList();
  if (tab === 'charts') renderCharts();
  if ($('#modal').open && openSlug) openEvent(openSlug);
}

const SEGMENTS = {
  'data-tab':   v => tab = v,
  'data-range': v => range = +v,
  'data-view':  v => view = v,
};
function wireSegments() {
  // the modal renders its own copy of the chart controls; both sets share the
  // same attributes, so wiring by attribute keeps them in step
  Object.entries(SEGMENTS).forEach(([attr, apply]) => {
    $$(`[${attr}]`).forEach(b => b.onclick = () => {
      const v = b.getAttribute(attr);
      apply(v);
      $$(`[${attr}]`).forEach(x => x.setAttribute('aria-pressed', x.getAttribute(attr) === v));
      render();
    });
  });
}
wireSegments();
$('#prevM').onclick = () => { month.setMonth(month.getMonth() - 1); renderCalendar(); };
$('#nextM').onclick = () => { month.setMonth(month.getMonth() + 1); renderCalendar(); };
$('#thisM').onclick = () => { month = new Date(); month.setDate(1); renderCalendar(); };
$('#showRetired').onchange = e => { showRetired = e.target.checked; renderStats(); render(); };
let qt;
$('#q').oninput = e => { query = e.target.value; clearTimeout(qt); qt = setTimeout(render, 150); };
$('#theme').onclick = () => {
  document.documentElement.dataset.theme = isDark() ? 'light' : 'dark';
  render();
};
$('#modalClose').onclick = () => $('#modal').close();
// clicking the backdrop lands on the dialog element itself -- the only way out
// on a phone, where there is no Escape key
$('#modal').addEventListener('click', e => { if (e.target === e.currentTarget) e.currentTarget.close(); });
$('#modal').addEventListener('close', () => { openSlug = null; render(); });
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
matchMedia('(max-width: 820px)').addEventListener('change', render);
addEventListener('resize', () => { if (tab === 'calendar') fitCalendar(); });
addEventListener('keydown', e => { if (e.key === 'Escape' && $('#modal').open) $('#modal').close(); });
boot();
</script>
</body>
</html>
"""


def build_dashboard(store_or_con, out_path):
    """Writes the static page. It carries no data -- it fetches it at runtime."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    html = (HTML
            .replace("__CHART_JS__", "vendor/chart.umd.min.js" if VENDOR.exists() else CDN)
            .replace("__DATA_BASE__", data_base())
            .replace("__PALETTE__", ",".join(SERIES_LIGHT[:5]))
            .replace("__SERIES_LIGHT__", str(SERIES_LIGHT).replace("'", '"'))
            .replace("__SERIES_DARK__", str(SERIES_DARK).replace("'", '"')))
    out.write_text(html, encoding="utf-8")
    return out
