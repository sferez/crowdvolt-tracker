"""Renders public/index.html -- a static page that reads its data at runtime.

Nothing is baked into the page but the code, so an hourly reading replaces a
couple of JSON objects in the bucket and the site is current without a redeploy.

    index.json          loaded once: roster + each event's current numbers
    recent.json         loaded once: readings since the last consolidation
    events/<slug>.json  loaded when you open an event, merged with the above

Four views over the same data: a Calendar of the month, a List of everything,
Charts (a card per event), and Deals (everything priced below fair value). Opening one event shows a line per ticket
category (best ask, all-in, left axis) over a bar per category (tickets
available, right axis) on a shared hourly x-axis.
"""

import os
from pathlib import Path

import chatstore
import r2

HERE = Path(__file__).parent
# Chart.js is vendored so the page works offline and needs no third-party CDN.
VENDOR = HERE / "public" / "vendor" / "chart.umd.min.js"
CDN = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.5.0/chart.umd.min.js"

# shown in the Contact dialog, and the only place the address is written
CONTACT_EMAIL = "winds_amid.1b@icloud.com"

# dataviz reference palette, fixed slot order (never cycled).
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
                "#008300", "#4a3aa7", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181",
               "#008300", "#9085e9", "#e66767"]


def data_base():
    """Where the page fetches its JSON: the object store when one is
    configured, the local public/data/ mirror otherwise."""
    r2.load_env()
    override = os.environ.get("DATA_BASE")
    if override:
        return override.rstrip("/") + "/"
    remote = r2.Remote()
    return remote.base + remote.prefix if remote.enabled else "data/"


def chat_base():
    """Where the page reads chat threads. Its own bucket, so the endpoint
    that takes writes from the internet cannot reach the price data."""
    override = os.environ.get("CHAT_BASE")
    if override:
        return override.rstrip("/") + "/"
    remote = r2.Remote(prefix=chatstore.PREFIX, env="CHAT_")
    return remote.base + remote.prefix if remote.enabled else ""


HTML = r"""<!doctype html>
<html lang="en" data-theme="">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CrowdVolt NYC Tracker</title>
<meta name="description" content="Best ask and ticket availability per category for New York events, hourly.">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='26' font-size='26'>🎟️</text></svg>">
<!-- Ahead of the stylesheet on purpose: a remembered theme applied after
     first paint is a visible flash of the other one. Validated, not trusted,
     and silent if storage is unavailable. -->
<script>
try {
  var t = localStorage.getItem('cv.theme.v1');
  if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
} catch (e) {}
</script>
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
.titlerow { display:flex; align-items:center; gap:8px; margin-bottom:3px; }
/* square, quiet, and the same height as the text beside it */
.ghost.icon { width:26px; height:26px; padding:0; display:inline-flex;
              align-items:center; justify-content:center; color:var(--muted); }
.ghost.icon:hover { color:var(--ink); }
.ghost.icon svg { width:14px; height:14px; }
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
/* collapsed to its icon until wanted, so the controls row is not mostly an
   empty text box. Stays open while it holds a query -- collapsing a filter
   that is still filtering would hide why the list looks short. */
.search { display:flex; align-items:center; gap:6px; }
/* hidden outright rather than animated to zero width: an input collapsed with
   width:0 inside a flex row is at the mercy of the shrink algorithm, and was
   reopening to 2px */
.search input { display:none; font:inherit; font-size:13px; border-radius:9px;
  border:1px solid var(--border); background:var(--surface); color:var(--ink);
  padding:6px 11px; width:210px; }
.search.open input { display:block; }
.search.on #qbtn { color:var(--ink); border-color:var(--ink-2); }
@media (max-width:820px) { .search input { width:100%; } }
label.chk { display:inline-flex; align-items:center; gap:6px; color:var(--muted); font-size:13px; }

main { padding:20px 28px 70px; max-width:1280px; }
/* the calendar is the one view that wants the whole window: full width, and
   tall enough that a month fits on one screen without the page scrolling */
body.tab-calendar main { max-width:none; padding:12px 28px 20px; }
/* the table carries sixteen columns now; the extra width is what keeps it out
   of a horizontal scrollbar on a normal laptop screen */
body.tab-list main { max-width:1420px; }
#calendar { display:flex; flex-direction:column; min-height:0; }
.hide { display:none !important; }
.empty { color:var(--muted); padding:44px 0; }

/* stat strip -- inline in the header, so it costs a line of text, not a band */
.stats { display:flex; flex-wrap:wrap; align-items:baseline; color:var(--muted);
         font-size:12.5px; }
.stat b { font-weight:650; color:var(--ink); font-variant-numeric:tabular-nums; }
.stat + .stat::before { content:'·'; padding:0 7px; opacity:.55; }
/* the guide is a sibling page rather than a figure, so it rides in this line
   at the labels' weight and never picks up the numbers' bold. Its own class,
   not .stat, so the phone rule that trims the strip to three figures does not
   count it as a fourth and take it with them. */
.statsep { padding:0 7px; opacity:.55; }
.statlink { color:var(--muted); text-decoration:underline dotted;
            text-underline-offset:3px; }
.statlink:hover { color:var(--ink-2); }
/* Contact is a button because it opens a dialog rather than going anywhere,
   but it has to read as the same kind of thing as the Help link beside it */
button.statlink { font:inherit; background:none; border:0; padding:0;
                  cursor:pointer; }

/* top movers: one line, one item at a time, so it informs without becoming a
   second dashboard. Rotation pauses on hover so a row can actually be read
   and clicked. */
.movers { display:flex; align-items:center; gap:11px; margin:0;
          padding:10px 14px; border:1px solid var(--border); border-radius:11px;
          background:var(--surface); font-size:14px;
          /* pinned right: the stats line makes the brand wide enough that
             space-between alone leaves them touching */
          flex:1 1 620px; max-width:min(680px, 58%); min-width:0; margin-left:auto;
          align-self:center; }
.movers .lbl { color:var(--muted); font-size:11px; letter-spacing:.04em;
               text-transform:uppercase; font-weight:700; white-space:nowrap;
               border:1px solid var(--border); border-radius:5px;
               padding:2px 6px; flex:none; }
.movers .mv { display:flex; align-items:center; gap:9px; flex:1 1 0; min-width:0;
              overflow:hidden;
              background:transparent; border:0; padding:2px 0; cursor:pointer;
              color:var(--ink); font:inherit; text-align:left; }
.movers .mv:hover .nm { text-decoration:underline; }
.movers .mv img { width:26px; height:26px; border-radius:6px; object-fit:cover;
                  flex:none; background:var(--wash); }
/* Three items competing for one line. The name and the context both give way
   -- the context first -- so neither can run under the dots when a fair price
   makes the line longer. The amount never shrinks: it is the point. */
.movers .nm { flex:1 1 auto; min-width:2.5rem; overflow:hidden;
              text-overflow:ellipsis; white-space:nowrap; }
.movers .amt { flex:none; font-weight:650; font-variant-numeric:tabular-nums;
               white-space:nowrap; }
.movers .ctx { flex:0 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
               color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap; }
/* the pinned tag is a fact about which price the amount belongs to, so it
   holds its width while the name beside it gives way */
.movers .cat { flex:none; max-width:92px; overflow:hidden; text-overflow:ellipsis; }
.movers .dots { display:flex; gap:5px; flex:none; margin-left:2px; }
.movers .dots button { width:6px; height:6px; padding:0; border-radius:50%;
                       border:0; background:var(--axis); cursor:pointer; }
.movers .dots button[aria-current="true"] { background:var(--ink-2); }
@media (max-width:820px) {
  /* full width under the title rather than squeezed beside it. The base rule
     caps it at 58% and pushes it right to keep it off the brand, neither of
     which applies once it has a line to itself. */
  .movers { flex:1 1 100%; max-width:none; margin-left:0; }
  .movers .ctx { display:none; }
}

/* the device badge: an identity the page invents for this browser, which is
   the honest picture -- there is no account, and nothing here follows you */
#profile { flex:none; width:32px; height:32px; padding:0;
           border-radius:50%; font:650 11px/1 inherit; letter-spacing:.02em;
           cursor:pointer; display:inline-flex; align-items:center;
           justify-content:center; }
#profile:hover { filter:brightness(1.06); }
/* .movers already claims the free space with margin-left:auto. A second auto
   margin here would split it and float the banner into the middle, so this
   one only takes over when there is no banner to push against. */
.movers.hide ~ #profile { margin-left:auto; }

/* The two of them are one cluster, not two things that happen to be near
   each other: the header's 18px column gap between the banner and the bell
   was also the gap between the bell and the badge, which left the bell
   belonging to neither. */
.tools { display:flex; align-items:center; gap:7px; flex:none; align-self:center; }
.movers.hide ~ .tools { margin-left:auto; }

/* same size and same shape as the badge beside it -- one neutral, one in the
   device's colour */
#bell { position:relative; width:32px; height:32px; border-radius:50%; }
/* the count is already a red dot; a red border as well competes with the
   banner's outline for the same attention */
#bell.lit { color:var(--crit);
            background:color-mix(in srgb, var(--crit) 12%, var(--surface)); }
#bell .dot { position:absolute; top:-5px; right:-5px; min-width:14px; height:14px;
             padding:0 3px; border-radius:999px; background:var(--crit);
             color:#fff; font:650 9.5px/14px inherit; font-style:normal;
             text-align:center; }
/* "≤ $95" says what the control does without a legend. Unset it is the same
   shape with the number missing, which reads as an invitation. */
.abtn { font:600 11px/1 inherit; padding:3px 5px; margin-left:7px;
        border-radius:6px; cursor:pointer; vertical-align:1px;
        color:var(--muted); background:var(--wash);
        border:1px dashed var(--border); }
.abtn:hover { color:var(--ink); border-style:solid; }
.abtn.on { color:var(--good); border:1px solid var(--good); background:transparent; }
.aedit { display:inline-flex; align-items:center; gap:1px; margin-left:7px;
         padding:2px 5px; border-radius:6px; vertical-align:1px;
         border:1px solid var(--good); background:var(--surface); }
.aedit .pre { font:600 11px/1 inherit; color:var(--good); }
.aedit input { width:52px; border:0; outline:none; background:transparent;
               color:var(--ink); font:600 12px/1 inherit; padding:1px 0; }
/* the arrows would be a third of the field's width */
.aedit input::-webkit-outer-spin-button,
.aedit input::-webkit-inner-spin-button { -webkit-appearance:none; margin:0; }

.notebox { margin:10px 0 2px; }
.notebox input { width:100%; box-sizing:border-box; padding:8px 11px;
                 border:1px solid var(--border); border-radius:9px;
                 background:var(--wash); color:var(--ink); font:inherit;
                 font-size:13px; }
.notebox input:focus { outline:none; border-color:var(--axis);
                       background:var(--surface); }
.nmark { font-style:normal; color:var(--muted); margin-left:6px; cursor:help; }

/* the tray: two lists of the same shaped row -- what it is on the left, what
   the number is on the right */
#tray { width:min(520px,92vw); }
.tsec { margin:14px 0 5px; font-size:11px; letter-spacing:.04em;
        text-transform:uppercase; font-weight:700; }
.settings h3.second { margin-top:22px; padding-top:16px;
                      border-top:1px solid var(--border); }
.trow { display:flex; align-items:center; gap:12px; width:100%; text-align:left;
        padding:8px 10px; margin:0 -10px; border:0; border-radius:9px;
        background:transparent; color:var(--ink); font:inherit; cursor:pointer; }
.trow:hover { background:var(--wash); }
.trow .tl { flex:1 1 auto; min-width:0; }
.trow .tr { flex:none; text-align:right; }
.trow b { font-weight:650; font-size:13px; display:block;
          overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.trow .c { display:block; color:var(--muted); font-size:11.5px;
           overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.trow.hit { background:color-mix(in srgb, var(--good) 9%, transparent); }
.trow.hit:hover { background:color-mix(in srgb, var(--good) 16%, transparent); }
.trow.dim { opacity:.6; }
/* inside prose the control is a picture of itself, not a button */
.settings .abtn { cursor:default; margin:0 1px; }

#chat { width:min(560px,92vw); }
.chat { margin-top:14px; border-top:1px solid var(--border); padding-top:10px; }
.chat > summary { cursor:pointer; font-size:13px; color:var(--ink-2);
                  font-weight:650; list-style:none; }
.chat > summary::-webkit-details-marker { display:none; }
.chat > summary::before { content:'▸'; margin-right:6px; color:var(--muted); }
.chat[open] > summary::before { content:'▾'; }
.chat > summary .n { color:var(--muted); font-weight:500; margin-left:2px; }
.chat .note { margin:8px 0 10px; }
/* a bounded, scrolling log: a long thread must not push the composer off
   the bottom of a modal that is already tall */
.clog { max-height:260px; overflow-y:auto; display:flex; flex-direction:column;
        gap:10px; padding-right:4px; }
.cmsg { font-size:13px; }
.cwho { display:flex; align-items:baseline; gap:7px; }
.cwho b { font-weight:650; font-size:12.5px; }
/* the discriminator, because 144 names collide long before the ids do */
.ctag { font-size:10px; color:var(--muted); font-variant-numeric:tabular-nums;
        border:1px solid var(--border); border-radius:4px; padding:0 3px; }
.cyou { font-size:10px; color:var(--good); border:1px solid var(--good);
        border-radius:4px; padding:0 4px; }
.cwhen { font-size:11px; color:var(--muted); margin-left:auto; }
.cdel { background:none; border:0; cursor:pointer; color:var(--muted);
        font-size:15px; line-height:1; padding:0 2px; }
.cdel:hover { color:var(--crit); }
/* pre-wrap, so the newlines someone typed survive -- and break-word, so a
   200-character unbroken string cannot widen the dialog */
.ctext { margin:2px 0 0; white-space:pre-wrap; overflow-wrap:anywhere;
         line-height:1.45; }
.cform { margin-top:12px; }
.cform textarea { width:100%; box-sizing:border-box; resize:vertical;
                  padding:8px 11px; border:1px solid var(--border);
                  border-radius:9px; background:var(--wash); color:var(--ink);
                  font:inherit; font-size:13px; }
.cform textarea:focus { outline:none; border-color:var(--axis);
                        background:var(--surface); }
.cts:not(:empty) { margin-bottom:8px; }
.crow2 { display:flex; align-items:center; gap:10px; margin-top:8px; }
.cerr { flex:1 1 auto; color:var(--crit); font-size:12px; }
.crow2 .ghost { flex:none; }
#modKey { width:150px; padding:4px 7px; border:1px solid var(--border);
          border-radius:7px; background:var(--wash); color:var(--ink);
          font:inherit; font-size:12px; }
#modOk { font-style:normal; font-size:11.5px; margin-left:7px; }
#modOk.good { color:var(--good); } #modOk.bad { color:var(--crit); }

#settings { width:min(430px,92vw); }
.settings { padding:20px; }
.settings h3 { margin:0 0 14px; font-size:15px; }
.who { display:flex; align-items:center; gap:11px; margin-bottom:12px; }
.who .av { width:38px; height:38px; border-radius:50%; flex:none;
           display:inline-flex; align-items:center; justify-content:center;
           font:650 13px/1 inherit; }
.who b { display:block; font-size:14px; font-weight:650; }
.settings .note { color:var(--muted); font-size:12.5px; line-height:1.5;
                  margin:0 0 16px; }
.prefs { display:flex; flex-direction:column; gap:12px; }
/* label left, control right, and the control keeps its size when the label
   is the longer of the two */
.prefrow { display:flex; align-items:center; gap:12px; }
.prefrow .k { flex:1 1 auto; font-size:13px; color:var(--ink-2); }
.prefrow .seg, .prefrow .v { flex:none; }
.prefrow .v { font-size:12.5px; color:var(--muted); }
.prefrow .seg button { font-size:12px; padding:5px 9px; }
.linkish { background:none; border:0; padding:0; font:inherit; cursor:pointer;
           color:var(--ink-2); text-decoration:underline dotted;
           text-underline-offset:3px; }
.linkish:hover { color:var(--ink); }

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
/* Widths are set per render from the widest value the month actually holds
   (see alignCalCols): fixed enough to form columns, not so fixed that a
   four-figure price in one month costs every other month its event names. */
.cal .evt .pr { flex:none; width:var(--prw, auto); min-width:26px;
                text-align:right; }
.cal .evt .mv { flex:none; font-style:normal; font-size:10.5px;
                width:var(--mvw, auto); min-width:26px;
                text-align:right; font-variant-numeric:tabular-nums; }
/* badge and amount travel together, tighter than the cell's own 5px gap, so
   they read as one annotation on the price rather than two columns */
.cal .evt .mvbox { display:flex; align-items:center; gap:3px; flex:none; }
.cal .evt .sig { font-size:9.5px; margin:0; flex:none; line-height:1;
                 width:11px; text-align:center; }

/* list */
table { border-collapse:collapse; width:100%; font-size:13px; font-variant-numeric:tabular-nums; }
th, td { text-align:right; padding:8px 11px; border-bottom:1px solid var(--grid); white-space:nowrap; }
th:first-child, td:first-child, th.l, td.l { text-align:left; }
th { color:var(--muted); font-weight:500; position:sticky; top:0; background:var(--plane);
     cursor:pointer; user-select:none; }
th[aria-sort]::after { content:'↑'; padding-left:5px; color:var(--ink-2); }
/* the group row: quiet enough to scan past, present enough to parse by */
tr.grp th { font-size:10.5px; letter-spacing:.06em; text-transform:uppercase;
            color:var(--muted); font-weight:600; text-align:left; cursor:default;
            padding:9px 11px 3px; border-bottom:0; white-space:nowrap; }
tr.grp + tr th { top:29px; padding-top:2px; }
thead tr.grp th { top:0; }
/* one hairline per group, carried down every row so the blocks read as blocks */
th.ge, td.ge { border-left:1px solid var(--border); }
th[aria-sort="descending"]::after { content:'↓'; }
tbody tr { cursor:pointer; }
tbody tr:hover { background:var(--wash); }
/* the two free-text columns give way first, so the table stays inside its
   column rather than pushing the page sideways */
#list td.ev { max-width:255px; }
#list td.vn { max-width:155px; }
/* Deals carries one more numeric column, and buys the room back from the two
   free-text ones -- its rows are few and every name keeps its full tooltip */
#deals td.ev { max-width:195px; }
#deals td.vn { max-width:115px; }
#list td.ev, #list td.vn, #deals td.ev, #deals td.vn { overflow:hidden; text-overflow:ellipsis; }
.dealnote { color:var(--muted); font-size:12.5px; max-width:88ch; margin:0 0 12px; }
.up { color:var(--crit); } .down { color:var(--good); }
/* − and + rather than ▾ ▴: the triangles are a few pixels of ink at this
   size and disappear into the row */
.sig { font-size:11px; margin-right:4px; font-style:normal; font-weight:700; }
.fav { background:none; border:0; padding:0; cursor:pointer; line-height:1;
       color:var(--muted); font-size:14px;
       /* centred on the row, not sitting on the text baseline */
       display:inline-flex; align-items:center; justify-content:center;
       width:18px; height:18px; vertical-align:middle; margin-right:4px; }
.fav:hover { color:var(--ink-2); }
.fav.on { color:#eda100; }
.fav.big { font-size:17px; margin-left:6px; vertical-align:1px; }
/* the calendar has no room for a star, but a favourite should still be
   findable, and a pinned category has to be visible or the price lies */
.fdot { display:inline-block; width:5px; height:5px; border-radius:50%;
        background:#eda100; margin-right:5px; vertical-align:2px; }
.evt .cat { color:var(--muted); font-size:10px; border:1px solid var(--border);
            border-radius:3px; padding:0 3px; margin-left:4px; }
.evt .cat.lost { color:var(--crit); }
/* A grid row is one flex line in a 150px cell, and a pinned row carries one
   fact more than that fits: which category the price belongs to. The fact is
   the whole point of pinning, so rather than clip the name to an ellipsis or
   let the cell scroll sideways, a pinned row -- and only a pinned row -- takes
   a second line, with the tag under the name and the numbers still right. */
.cal .evt .cat { flex:none; max-width:88px; overflow:hidden;
                 text-overflow:ellipsis; }
.cal .evt.pinned { flex-wrap:wrap; row-gap:1px; }
/* zero basis, or a long name is wider than the line it is meant to share with
   the thumbnail and wraps below it, spending a third line on nothing */
.cal .evt.pinned .nm { flex:1 1 0; }
/* The tag and the two numbers are one line of their own, and a nested flex row
   rather than three loose items: flexbox breaks lines before it shrinks
   anything, so left in the outer wrapping row the move would hop to a third
   line instead of the tag giving up a few characters. Inside a nowrap row the
   tag shrinks and ellipses instead -- the tooltip still has the full name --
   and the auto margin keeps the price and the move flush right, in the column
   the unpinned rows above put them in. */
.cal .evt.pinned .ln2 { flex:1 0 100%; display:flex; align-items:center;
                        gap:5px; min-width:0; }
.cal .evt.pinned .ln2 .cat { margin-right:auto; flex:0 1 auto; min-width:2.4em; }
/* the agenda's price line is one nowrap run at 500px; letting it wrap is
   cheaper than letting a venue name push the page sideways */
.agenda .evt .pr { white-space:normal; }
.agenda .evt .mv { font-style:normal; font-variant-numeric:tabular-nums; }
/* the pinned-category tag: the row is showing GA, not the floor, and says so */
.pin { font-size:10.5px; color:var(--muted); border:1px solid var(--border);
       border-radius:4px; padding:1px 5px; margin-left:6px; white-space:nowrap; }
.lost { color:var(--crit); }
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
.card h3 a.src { text-decoration:none; }
/* small enough to sit on the title's baseline without competing with it */
a.src { display:inline-flex; align-items:center; justify-content:center;
        width:22px; height:22px; border-radius:6px; margin-left:6px;
        vertical-align:-4px; border:1px solid var(--border);
        background:var(--surface); transition:border-color .12s, transform .12s; }
a.src:hover { border-color:var(--ink-2); transform:translateY(-1px); }
a.src img { width:14px; height:14px; object-fit:contain; }
.meta { color:var(--muted); font-size:13px; }
.meta .sep { padding:0 5px; opacity:.5; }
.pills { display:flex; gap:6px; flex-wrap:wrap; margin-top:5px; }
.pill { font-size:11.5px; color:var(--muted); border:1px solid var(--border);
        border-radius:999px; padding:1px 8px; }
/* the pin sits in the pill row and takes its spacing from the flex gap */
.pills .pin { margin-left:0; align-self:center; }
/* the descriptive half of the old pill wall: no border, no chrome, just a
   quiet line of words under the numbers that matter */
.tags { display:flex; flex-wrap:wrap; gap:3px 12px; margin-top:6px;
        color:var(--muted); font-size:11.5px; }
.qtag { display:inline-flex; align-items:center; white-space:nowrap; }
/* an author `display` outranks the UA rule for [hidden], so the collapsed
   tags need saying again here or "+n more" hides nothing */
.qtag[hidden] { display:none; }
.qtag .gdot { width:7px; height:7px; margin-right:5px; }
.qmore { background:transparent; border:0; padding:0; font-size:11.5px;
         color:var(--ink-2); cursor:pointer; text-decoration:underline; }
.qmore:hover { color:var(--ink); }

/* The tier ladder. When the category table above already carries the fair
   value the ladder is folded away -- its remaining job is the run-up, and its
   summary line alone says how hard the event sold. When no category could be
   matched it is opened instead and becomes the comparison. */
details.ladder { margin:0 0 14px; border-top:0; padding-top:0; }
div.ladder { margin:0 0 14px; }
.ladder > summary { color:var(--muted); font-size:12px; }
.ladder .lnote { color:var(--muted); font-size:12px; max-width:56ch; }
.ladder .lad { margin-top:6px; max-width:400px; max-height:196px;
               overflow-y:auto; overscroll-behavior:contain; scrollbar-width:thin;
               scrollbar-color:var(--axis) transparent; }
.ladder .lad::-webkit-scrollbar { width:6px; }
.ladder .lad::-webkit-scrollbar-thumb { background:var(--axis); border-radius:3px; }
td .thumb, .lthumb { width:22px; height:22px; border-radius:5px; object-fit:cover;
                     vertical-align:middle; margin-right:8px; background:var(--wash); }
td.ev { display:flex; align-items:center; gap:0; }
/* artless events keep the same footprint so names stay on one left edge */
.ph { display:inline-block; background:var(--wash); }
.gdot { display:inline-block; width:9px; height:9px; border-radius:50%;
        margin-right:7px; vertical-align:0; }
.swatch { width:10px; height:10px; border-radius:3px; flex:none; }
/* One small table doing three jobs at once: the chart's legend -- the swatch
   is the series colour -- the fair-value comparison, and the category filter,
   since every row is a button that isolates its category. As three separate
   widgets they said the same thing three times and none of them lined up. */
.cats { width:100%; font-size:12.5px; margin:2px 0 14px; }
.cats th, .cats td { padding:5px 9px; border-bottom:1px solid var(--grid); }
.cats th { position:static; background:transparent; cursor:default;
           font-weight:500; font-size:11.5px; }
.cats th:first-child, .cats td:first-child { padding-left:0; }
.cats th:last-child, .cats td:last-child { padding-right:0; }
/* the name takes whatever is left over and clips, so four numeric columns and
   a long category name still fit a phone without the modal scrolling sideways */
.cats .nm { width:100%; max-width:0; overflow:hidden; text-overflow:ellipsis; }
.cats .swatch { display:inline-block; margin-right:8px; vertical-align:0; }
.cats tbody tr { transition:opacity .12s; }
.cats tbody tr[aria-pressed="true"] { background:var(--wash); }
.cats tbody tr[data-off] { opacity:.42; }
.cats tbody tr:focus-visible { outline:2px solid var(--ink-2); outline-offset:-2px; }
/* the number the whole comparison exists for, so it carries the weight */
.cats td.d { font-weight:650; }
/* a fair value we could not confirm is still worth showing, but it must not
   read with the same confidence as one we could */
.cats td.soft { color:var(--muted); text-decoration:underline dotted;
                text-underline-offset:3px; cursor:help; }
/* a face value nobody can buy at any more: struck the way a gone tier is in
   the ladder, but never hidden -- the delta against it is the whole point */
td.past { color:var(--ink-2); text-decoration:line-through; }
.cats td.past.soft { color:var(--muted); cursor:help; text-underline-offset:3px;
                     text-decoration:line-through underline dotted; }
.cats .out { color:var(--muted); font-size:11.5px; }
/* a caveat that belongs to one figure, under it rather than beside it, so it
   costs a row's height and not the column's width */
.note { display:block; font-style:normal; font-size:10.5px;
        color:var(--muted); line-height:1.3; }
/* the marker on a figure that belongs to the event rather than to the row.
   Inline, because a hundred rows of the List cannot each afford a second line */
td.ew { cursor:help; }
td.ew i { font-style:normal; font-size:9.5px; color:var(--muted); margin-left:5px;
          border:1px solid var(--border); border-radius:3px; padding:0 3px;
          vertical-align:1px; }
/* the ladder in the same table language, minus the interactivity */
.tiers { margin:0; }
.tiers tbody tr { cursor:default; }
.tiers tbody tr:hover { background:transparent; }
.tiers tbody tr.gone { opacity:.55; }
.tiers tbody tr.gone .nm, .tiers tbody tr.gone .pr { text-decoration:line-through; }
.wrap { position:relative; height:320px; }
/* the right-hand axis only exists when the supply bars are on */
body:not(.supply) .axisnote .rt { display:none; }
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
/* the contact card is a note, not a workspace, so it opts out of the wide
   default the event modal sets */
#contact { width:min(340px,92vw); }
.contact { padding:20px; }
.contact h3 { margin:0 0 6px; font-size:15px; }
.contact p { margin:0 0 14px; color:var(--muted); font-size:12.5px; line-height:1.5; }
/* the address is selectable text as well as a copy target: the button is the
   fast path, not the only one */
.mailrow { display:block; overflow:auto; white-space:nowrap;
           font-size:13px; color:var(--ink); background:var(--wash);
           border:1px solid var(--border); border-radius:9px; padding:9px 11px; }
.btnrow { display:flex; gap:8px; justify-content:flex-end; margin-top:10px; }
#copyMail.done { color:var(--good); border-color:var(--good); }
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
  /* Taken out of the flow here. In it, the badge competes with the brand and
     the banner for one wrapping line and lands wherever the three widths
     happen to fall; the corner is where it belongs and this is the only way
     to say so. The header's padding keeps the stats clear of it. */
  header { position:relative; padding-right:98px; }
  .tools { position:absolute; top:10px; right:16px; margin:0; gap:6px; }
  #bell, #profile { width:36px; height:36px; }
  .spacer { display:none; }
  #theme { order:1; }
  .stats { order:2; }
  #updated { order:3; font-size:11.5px; }
  h1 { font-size:17px; }
  .controls { padding:0 16px 12px; gap:6px; }
  .stats { font-size:12px; }
  .stat + .stat::before { padding:0 5px; }
  main, body.tab-calendar main { padding:14px 16px 48px; }
  .search { order:99; min-width:0; }
  .search.open { flex:1 1 100%; }
  label.chk { order:100; }                          /* below the search field */
  .seg button, .ghost { padding:8px 13px; }        /* fat-finger targets */
  #profile { width:36px; height:36px; }
  .gchip { padding:7px 12px 7px 10px; }
  label.chk { padding:6px 0; }
  .head img { width:46px; height:46px; }
  .wrap { height:260px; }
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
  .cats th, .cats td { padding:6px 6px; }        /* four numeric columns on a phone */
}
/* Sixteen columns need about 1364px, which is why the list view is allowed a
   wider main than the rest of the page. Narrower than that the table takes its
   own horizontal scrollbar rather than widening the document -- and since it
   is scrolling anyway, the clipped columns get their full text back. Above it
   the table fits and the sticky header floats against the viewport. */
@media (max-width:1440px) {
  /* Favourites is the same sixteen columns and was never named here, so the
     one tab you reach for after pinning something was the one that pushed the
     document sideways on a phone */
  #list table, #deals table, #favs table {
    display:block; overflow-x:auto; white-space:nowrap;
    max-width:100%; overscroll-behavior-x:contain; }
  #list td.ev, #list td.vn, #deals td.ev, #deals td.vn { max-width:none; }
}
@media (max-width:420px) {
  .stats .stat:nth-child(n+4) { display:none; }
  .modalctl { gap:6px; }
}
</style>
</head>
<body data-palette="__PALETTE__">
<header>
  <div class="brand">
    <div class="titlerow">
      <h1>CrowdVolt <em>NYC</em> Tracker</h1>
      <button class="ghost icon" id="theme" aria-label="Switch theme"></button>
    </div>
    <div class="stats" id="stats"></div>
  </div>
  <div id="movers" class="movers hide" aria-live="polite"></div>
  <div class="tools">
    <button id="chatBtn" class="ghost icon" aria-label="Chat"></button>
    <button id="bell" class="ghost icon" aria-label="Alerts and what changed"></button>
    <button id="profile" aria-label="Your settings"></button>
  </div>
</header>

<div class="controls">
  <span class="seg" role="group" aria-label="View">
    <button data-tab="favs" class="hide" aria-pressed="false">Favourites</button>
    <button data-tab="calendar" aria-pressed="true">Calendar</button>
    <button data-tab="list" aria-pressed="false">List</button>
    <button data-tab="deals" aria-pressed="false">Deals</button>
    <button data-tab="charts" aria-pressed="false">Charts</button>
  </span>
  <span class="calctl seg" id="monthNav" role="group" aria-label="Month">
    <button id="prevM" aria-label="Previous month">‹</button>
    <button id="thisM">Today</button>
    <button id="nextM" aria-label="Next month">›</button>
  </span>
  <span class="calctl sub" id="calnote"></span>
  <div class="search" id="search">
    <button class="ghost icon" id="qbtn" aria-label="Search" aria-expanded="false">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
    </button>
    <input type="search" id="q" placeholder="Filter events, venues, cities" tabindex="-1">
  </div>
  <span class="chartctl seg" role="group" aria-label="Time range">
    <button data-range="24" aria-pressed="false">24h</button>
    <button data-range="168" aria-pressed="false">7d</button>
    <button data-range="0" aria-pressed="true">All</button>
  </span>
  <span class="chartctl seg" role="group" aria-label="Chart layout">
    <button data-view="ask" aria-pressed="true">Price only</button>
    <button data-view="both" aria-pressed="false">+ ticket supply</button>
  </span>
  <div class="spacer"></div>
  <label class="chk"><input type="checkbox" id="showRetired">
    show retired <span id="retiredN" class="sub"></span></label>
</div>

<main>
  <section id="favs" class="hide"></section>
  <section id="calendar"></section>
  <section id="list" class="hide"></section>
  <section id="deals" class="hide"></section>
  <section id="charts" class="hide"></section>
</main>

<dialog id="modal"><button class="ghost close" id="modalClose">Close</button>
  <div id="modalBody"></div>
</dialog>

<dialog id="chat"><div class="settings" id="chatBody"></div></dialog>

<dialog id="tray"><div class="settings" id="trayBody"></div></dialog>

<dialog id="settings"><div class="settings" id="settingsBody"></div></dialog>

<dialog id="contact"><div class="contact">
  <h3>Contact</h3>
  <p>Bugs, missing events, ideas — I'd love to hear from you.</p>
  <code class="mailrow" id="mailAddr">__CONTACT_EMAIL__</code>
  <div class="btnrow"><button class="ghost" id="copyMail">Copy address</button>
    <button class="ghost" id="contactClose">Close</button></div>
</div></dialog>

<script>
const DATA_BASE = "__DATA_BASE__";
const CHAT_BASE = "__CHAT_BASE__";
/* Turnstile refuses a bare IP in Hostname Management, so a real site key can
   never authorise 127.0.0.1 and local development would always see error
   110200. Cloudflare publishes dummy keys that pass on any hostname for
   exactly this; the real key is used everywhere else. Chosen at runtime
   rather than at build time so the committed page always carries the
   production key and cannot be shipped with a test one by accident. */
const LOCAL = ['127.0.0.1', 'localhost', '0.0.0.0'].includes(location.hostname);
const TURNSTILE_KEY = LOCAL ? '1x00000000000000000000AA' : "__TURNSTILE_KEY__";
const LIGHT = __SERIES_LIGHT__, DARK = __SERIES_DARK__;

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
// ' is escaped too. Every attribute in this page is double-quoted, so it was
// not needed -- but chat is the first text one visitor writes and another
// reads, and that invariant is not something to make it depend on.
const esc = t => String(t ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
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
/* Tier prices come off the primary platform with the fees baked in, so they
   are rarely round. The ladder is the one place that shows them exactly --
   everywhere else a price is rounded like every other price on the page. */
const exact = v => v == null ? '—' : '$' + (Number.isInteger(v) ? v : v.toFixed(2));
const lastDefined = a => { for (let i = a.length - 1; i >= 0; i--) if (a[i] != null) return a[i]; return null; };
/* Below fair value at all is the signal: it is a binary fact -- you can buy
   the same ticket for less than the platform that sold it first is charging --
   and it needs no minimum to be true. A fifth or more below is a different
   claim, loud enough to earn its own glyph. Both are fractions rather than
   sums, because five dollars is a tenth of a $41 ticket and a rounding error
   on a $400 one. */
const STANDOUT = -0.20;
const pctOf = v => {
  const p = Math.abs(v) * 100;
  return (p < 1 ? p.toFixed(1) : Math.round(p)) + '%';
};

let INDEX = {}, ORDER = [], GENERATED = null, RECENT = {};

/* Favourites live in this browser only -- there is no login to hang them off,
   so they do not follow you to another device. A favourite is either the whole
   event, or one of its ticket categories: pick GA and every view that would
   have shown you the event floor shows GA instead, because a $20 early-bird
   tier is not the answer to "what does GA cost". */
/* An identity for this browser, invented on first visit and kept with the
   rest of the preferences. It exists to make the one thing worth
   understanding about all of this visible rather than merely stated: open the
   tracker somewhere else and it is a different name, because it is a
   different, empty set of settings. */
const DEVICE_KEY = 'cv.device.v1';
// baked in from chatstore.ADJ/NOUN so the page and the server cannot drift:
// if they did, every message from a browser on the newer list would be
// rejected as a bad name, which nobody finds until a stranger complains
const D_ADJ = __D_ADJ__, D_NOUN = __D_NOUN__;

function deviceId() {
  let v;
  try { v = localStorage.getItem(DEVICE_KEY); } catch (e) {}
  if (!/^\d+$/.test(v || '')) {
    v = String(Math.floor(Math.random() * 1e9));
    try { localStorage.setItem(DEVICE_KEY, v); } catch (e) {}
  }
  return +v;
}

function device() {
  const n = deviceId(), pal = colors();
  const adj = D_ADJ[n % D_ADJ.length];
  // a second, coarser slice of the same number, so the two words do not move
  // in lockstep and neighbouring ids do not read as near-duplicates
  const noun = D_NOUN[Math.floor(n / D_ADJ.length) % D_NOUN.length];
  return {name: `${adj} ${noun}`, mono: adj[0] + noun[0],
          hue: pal[Math.floor(n / (D_ADJ.length * D_NOUN.length)) % pal.length]};
}

/* ---------------- chat ---------------------------------------------------

   Threads are read straight from their own bucket, like every other piece of
   data here; only posting goes through a server, because that needs a key.

   The awkward part is that `render()` rebuilds #modalBody wholesale, so a
   panel inside the event modal is destroyed and recreated under you -- while
   you are typing, if a favourite gets starred or the breakpoint changes. So
   nothing lives in the DOM that is not also held here. */

const CHAT = {};          // thread -> the last thread document we saw
const DRAFTS = {};        // thread -> half-typed text
const MOD_KEY = 'cv.mod.v1', SEEN_CHAT_KEY = 'cv.chatseen.v1';
let CHAT_FOCUS = null;    // thread whose composer had focus before a rebuild
let TS_ID = null;         // the Turnstile widget, which a rebuild also eats
let TS_WAIT = null;       // resolver for a challenge running right now
let chatTimer = null;

const modToken = () => { try { return localStorage.getItem(MOD_KEY) || ''; }
                         catch (e) { return ''; } };

/* A 4-character discriminator, mirroring chatstore.tag_for exactly. Hashed
   rather than sliced off the id: 36^4 divides by 12*12, so the low base-36
   digits are decided by the same arithmetic that picks the two words, and
   the tag would carry far less than it appears to. */
function tagOf(n) {
  let h = 2166136261;
  for (const c of String(n)) {
    h = Math.imul(h ^ c.charCodeAt(0), 16777619) >>> 0;
  }
  let v = h % (36 ** 4), out = '';
  for (let i = 0; i < 4; i++) { out = (v % 36).toString(36) + out; v = Math.floor(v / 36); }
  return out.toUpperCase();
}

const chatMe = () => ({...device(), tag: tagOf(deviceId())});

/* A colour per speaker.

   Hashed from the name and tag, never sent with the message -- a client that
   could pick its own colour would eventually pick the one that looks like
   somebody else. But a hash alone is not enough: hashing into the eight-slot
   chart palette collided on the third speaker, and hashing onto the raw hue
   circle produced hsl(163) and hsl(165), which are different numbers and the
   same colour. Two people in one colour is the single thing this is for.

   So: hash to a slot, then linear-probe to the next free one. Everyone in a
   thread gets a distinct slot while there are slots left, and a speaker keeps
   theirs as long as the cast does not change. Slots are laid out by the
   golden angle, so neighbouring slots -- the ones probing hands out -- are on
   opposite sides of the wheel rather than next to each other.

   Saturation and lightness are fixed per theme, so every hue stays legible
   against the surface. Identity rests on the name and tag; colour only makes
   a thread quicker to skim. */
const HUE_SLOTS = 16;

function chatSlots(msgs) {
  const slots = new Map(), used = new Set();
  for (const m of msgs) {
    const k = m.n + '\u0000' + m.g;
    if (slots.has(k)) continue;
    let h = 2166136261;
    for (const c of k) h = Math.imul(h ^ c.charCodeAt(0), 16777619) >>> 0;
    let slot = h % HUE_SLOTS;
    while (used.has(slot) && used.size < HUE_SLOTS) slot = (slot + 1) % HUE_SLOTS;
    used.add(slot);
    slots.set(k, slot);
  }
  return slots;
}

function hueOf(slot) {
  const deg = Math.round((slot * 137.508) % 360);
  return isDark() ? `hsl(${deg} 72% 70%)` : `hsl(${deg} 62% 36%)`;
}
const chatUrl = t => CHAT_BASE + t + '.json';

/* One row, built as nodes rather than a string.

   Everywhere else on this page interpolates through esc(), which is fine
   because every attribute is double-quoted. This is the first text one
   visitor writes and another reads, and making that safety depend on an
   invariant a future edit could break is not a trade worth taking. */
function chatRow(m, thread, slots, dupes) {
  const row = document.createElement('div');
  row.className = 'cmsg';
  const me = chatMe();
  const mine = m.g === me.tag && m.n === me.name;

  const head = document.createElement('div');
  head.className = 'cwho';
  const hue = hueOf(slots.get(m.n + '\u0000' + m.g) || 0);
  const who = document.createElement('b');
  who.textContent = m.n;
  who.style.color = hue;
  head.append(who);
  // The tag is carried on every message but shown only when two people in
  // this thread actually picked the same name. With ~395,000 of them that is
  // vanishingly rare, and a hash beside every name the rest of the time is
  // clutter paid for a case that does not happen.
  if (dupes && dupes.has(m.n)) {
    const tag = document.createElement('span');
    tag.className = 'ctag';
    tag.textContent = m.g;
    tag.style.color = hue;
    tag.style.borderColor = `color-mix(in srgb, ${hue} 45%, transparent)`;
    head.append(tag);
  }
  if (mine) {
    const you = document.createElement('span');
    you.className = 'cyou';
    you.textContent = 'you';
    head.append(you);
  }
  const when = document.createElement('time');
  when.className = 'cwhen';
  when.textContent = new Date(m.t * 1000)
    .toLocaleString([], {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
  head.append(when);
  if (modToken()) {
    const del = document.createElement('button');
    del.className = 'cdel';
    del.type = 'button';
    del.title = 'Remove this message';
    del.textContent = '×';
    del.onclick = ev => { ev.stopPropagation(); chatDelete(thread, m.id); };
    head.append(del);
  }

  const body = document.createElement('p');
  body.className = 'ctext';
  // textContent, always. Links are deliberately not made clickable: an
  // anonymous stranger's URL on a ticket-price page is the likeliest way
  // this feature gets abused.
  body.textContent = m.m;

  row.append(head, body);
  return row;
}

function paintChatList(thread, root = document) {
  const list = $(`[data-clog="${thread}"]`, root);
  if (!list) return;
  const doc = CHAT[thread];
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  list.textContent = '';
  const msgs = (doc && doc.msgs) || [];
  if (!msgs.length) {
    const empty = document.createElement('p');
    empty.className = 'sub';
    empty.textContent = doc ? 'Nothing here yet. Say something.' : 'Loading…';
    list.append(empty);
  } else {
    const slots = chatSlots(msgs);
    // names seen under more than one tag: the only case where the tag earns
    // its place on screen
    const byName = new Map();
    msgs.forEach(m => (byName.get(m.n) || byName.set(m.n, new Set()).get(m.n)).add(m.g));
    const dupes = new Set([...byName].filter(([, gs]) => gs.size > 1).map(([n]) => n));
    msgs.forEach(m => list.append(chatRow(m, thread, slots, dupes)));
  }
  if (atBottom) list.scrollTop = list.scrollHeight;
  const n = $(`[data-ccount="${thread}"]`, root);
  if (n) n.textContent = doc ? (doc.total || 0) : '';
}

/* Rendered from CHAT/DRAFTS, never from the DOM, so a rebuild is invisible. */
function chatPanel(thread, {open = false, extra = ''} = {}) {
  if (!CHAT_BASE) return '';
  const doc = CHAT[thread];
  const live = !!TURNSTILE_KEY || CHAT_BASE.startsWith('http://127.0.0.1');
  const body = `
    <p class="note">Everyone here is anonymous — your browser picked the name,
      and it is not an account.</p>
    <div class="clog" data-clog="${esc(thread)}"></div>
    ${live ? `<div class="cform">
      <div class="cts" data-cts="${esc(thread)}"></div>
      <textarea data-cbox="${esc(thread)}" rows="2" maxlength="500"
        placeholder="Say something"></textarea>
      <div class="crow2">
        <span class="cerr" data-cerr="${esc(thread)}"></span>
        ${extra}
        <button class="ghost" data-csend="${esc(thread)}">Send</button>
      </div>
    </div>` : `<p class="note">Posting is unavailable on this build.</p>`}`;
  if (open) return `<div class="chat">${body}</div>`;
  return `<details class="chat"${doc && doc.msgs && doc.msgs.length ? ' open' : ''}>
    <summary>Discussion <span class="n" data-ccount="${esc(thread)}">${
      doc ? (doc.total || 0) : ''}</span></summary>${body}</details>`;
}

/* Turnstile is loaded on first use, not in <head>: the page vendored its own
   chart library specifically so it calls no third party, and a token minted
   at page load has expired by the time anyone types. */
function turnstileReady() {
  if (!TURNSTILE_KEY) return Promise.resolve(false);
  if (window.turnstile) return Promise.resolve(true);
  if (!window.__tsLoad) {
    window.__tsLoad = new Promise(res => {
      const sc = document.createElement('script');
      sc.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
      sc.async = true;
      sc.onload = () => res(true);
      sc.onerror = () => res(false);
      document.head.append(sc);
    });
  }
  return window.__tsLoad;
}

async function mountTurnstile(thread, root) {
  const host = $(`[data-cts="${thread}"]`, root);
  if (!host || !await turnstileReady()) return;
  host.textContent = '';
  // the widget lived in a subtree a rebuild has just thrown away, so it is
  // always re-rendered rather than assumed to have survived
  TS_ID = window.turnstile.render(host, {
    sitekey: TURNSTILE_KEY, action: 'chat', size: 'flexible',
    // Nothing is shown unless the visitor genuinely has to do something, and
    // nothing runs until they press Send -- a token minted when the panel
    // opened would have expired by the time a message was written, and a
    // permanent verification box over a chat box is a lot of furniture for
    // something most people never trip.
    appearance: 'interaction-only',
    execution: 'render',
    callback: token => { if (TS_WAIT) { TS_WAIT(token); TS_WAIT = null; } },
    // without this a failed challenge is a silent grey box and a Send that
    // never works; 110200 in the console is not an answer for a visitor
    'error-callback': code => {
      chatErr(thread, `challenge unavailable (${code}) — posting is off`, root);
      const send = $(`[data-csend="${thread}"]`, root);
      if (send) send.disabled = true;
      return true;
    },
    'expired-callback': () => window.turnstile.reset(TS_ID),
  });
}

function wireChat(root, thread) {
  const box = $(`[data-cbox="${thread}"]`, root);
  paintChatList(thread, root);
  if (!box) return;
  box.value = DRAFTS[thread] || '';
  box.oninput = () => { DRAFTS[thread] = box.value; };
  box.onfocus = () => { CHAT_FOCUS = thread; };
  // isConnected: a rebuild removes this textarea, and Chrome fires blur on
  // the way out. Clearing the flag there would lose the caret on exactly the
  // rebuild the flag exists to survive.
  box.onblur = () => { if (box.isConnected && CHAT_FOCUS === thread) CHAT_FOCUS = null; };
  // the modal's category rows are role="button" and answer Enter and Space
  // by re-rendering everything, so keystrokes must stop here
  box.onkeydown = ev => {
    ev.stopPropagation();
    if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey)) {
      ev.preventDefault();
      chatSend(thread, root);
    }
  };
  const send = $(`[data-csend="${thread}"]`, root);
  if (send) send.onclick = ev => { ev.stopPropagation(); chatSend(thread, root); };
  if (CHAT_FOCUS === thread) {
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  }
  mountTurnstile(thread, root);
  chatFetch(thread).then(() => paintChatList(thread, root));
}

/* The token for this post: already held, or obtained by running the
   challenge now. Resolves to '' if it cannot be got, so the server decides
   rather than the page silently refusing. */
function chatToken() {
  if (!TS_ID || !window.turnstile) return Promise.resolve('');
  const have = window.turnstile.getResponse(TS_ID);
  if (have) return Promise.resolve(have);
  return new Promise(res => {
    TS_WAIT = res;
    try { window.turnstile.execute(TS_ID); } catch (e) { TS_WAIT = null; res(''); }
    // an interactive challenge the visitor ignores must not leave Send
    // spinning for ever
    setTimeout(() => { if (TS_WAIT === res) { TS_WAIT = null; res(''); } }, 25000);
  });
}

const chatErr = (thread, msg, root = document) => {
  const el = $(`[data-cerr="${thread}"]`, root);
  if (el) el.textContent = msg || '';
};

async function chatFetch(thread) {
  if (!CHAT_BASE) return;
  try {
    const r = await fetch(chatUrl(thread), {cache: 'no-cache'});
    if (!r.ok) { if (r.status === 404) CHAT[thread] = CHAT[thread] || {msgs: [], total: 0}; return; }
    const doc = await r.json();
    const have = CHAT[thread];
    // <=, not !==: your own message is shown the moment the POST returns, and
    // a poll can hand back an edge copy older than that. Going backwards on
    // screen is worse than being a few seconds late.
    if (have && doc.updated <= (have.updated || 0)) return;
    CHAT[thread] = doc;
  } catch (e) { /* a failed poll is not worth surfacing */ }
}

function activeThread() {
  if ($('#chat').open) return 'general';
  if ($('#modal').open && openSlug) return 'event/' + openSlug;
  return null;
}

/* Its own clock. refresh() is a 20-minute price poll that deliberately skips
   while the modal is open; chat wants the opposite, and must never call
   render() -- that would rebuild the modal and take the composer with it. */
async function chatPoll() {
  if (document.hidden || !CHAT_BASE) return;
  const t = activeThread();
  if (!t) return;
  const before = CHAT[t] && CHAT[t].updated;
  await chatFetch(t);
  if (CHAT[t] && CHAT[t].updated !== before) paintChatList(t);
}

async function chatSend(thread, root) {
  const box = $(`[data-cbox="${thread}"]`, root);
  const send = $(`[data-csend="${thread}"]`, root);
  const text = (box.value || '').trim();
  if (!text) return;
  const me = chatMe();
  const [scope, slug] = thread === 'general' ? ['general', null]
                                             : ['event', thread.slice(6)];
  chatErr(thread, '', root);
  if (send) { send.disabled = true; send.textContent = 'Sending…'; }
  try {
    const r = await fetch('/api/chat', {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({scope, slug, text, name: me.name, tag: me.tag,
                            turnstile: await chatToken()}),
    });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) { chatErr(thread, out.error || `failed (${r.status})`, root); return; }
    box.value = '';
    DRAFTS[thread] = '';
    // the response carries the whole thread, so your message appears at once
    // rather than after the next poll clears the edge cache
    CHAT[thread] = out.thread;
    paintChatList(thread, root);
  } catch (e) {
    chatErr(thread, 'could not reach the server', root);
  } finally {
    if (send) { send.disabled = false; send.textContent = 'Send'; }
    // tokens are single-use; a replay is rejected as a duplicate
    if (TS_ID && window.turnstile) window.turnstile.reset(TS_ID);
  }
}

async function chatDelete(thread, id) {
  const [scope, slug] = thread === 'general' ? ['general', null]
                                             : ['event', thread.slice(6)];
  try {
    const r = await fetch('/api/chat', {
      method: 'DELETE',
      headers: {'content-type': 'application/json',
                'authorization': 'Bearer ' + modToken()},
      body: JSON.stringify({scope, slug, id}),
    });
    const out = await r.json().catch(() => ({}));
    if (r.ok) { CHAT[thread] = out.thread; paintChatList(thread); }
    else chatErr(thread, out.error || `failed (${r.status})`);
  } catch (e) { chatErr(thread, 'could not reach the server'); }
}

const FAV_KEY = 'cv.favs.v1';
let FAVS = {};

function loadFavs() {
  try { FAVS = JSON.parse(localStorage.getItem(FAV_KEY) || '{}') || {}; }
  catch (e) { FAVS = {}; }
}
function saveFavs() {
  try { localStorage.setItem(FAV_KEY, JSON.stringify(FAVS)); } catch (e) {}
}
const isFav = slug => slug in FAVS;
const favCat = slug => FAVS[slug] || null;      // null = the whole event

function toggleFav(slug, cat) {
  if (cat === undefined) {
    if (isFav(slug)) delete FAVS[slug]; else FAVS[slug] = null;
  } else if (favCat(slug) === cat) {
    FAVS[slug] = null;                          // keep the event, drop the category
  } else {
    FAVS[slug] = cat;
  }
  saveFavs();
}

/* ---------------- notes, alerts, and what moved while you were away -------

   Three more things kept in this browser. Each is a plain object under its own
   key, read once at boot and written on every change: none of it is big enough
   to be worth batching, and a note lost to a crash is worse than a redundant
   write. */

const NOTE_KEY = 'cv.notes.v1';
const ALERT_KEY = 'cv.alerts.v1';
const SEEN_KEY = 'cv.seen.v1';
let NOTES = {}, ALERTS = {};

function readStore(key) {
  try {
    const v = JSON.parse(localStorage.getItem(key) || '{}');
    return v && typeof v === 'object' ? v : {};
  } catch (e) { return {}; }
}
function writeStore(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)); } catch (e) {}
}

const noteOf = slug => NOTES[slug] || '';

function setNote(slug, text) {
  text = String(text || '').trim().slice(0, 280);
  if (text) NOTES[slug] = text; else delete NOTES[slug];
  writeStore(NOTE_KEY, NOTES);
}

/* An alert is a ceiling on one ticket category: notify me when this can be had
   for this or less. Per category rather than per event because the event floor
   is whatever tier happens to be cheapest, which is not the ticket you want. */
const alertsOf = slug => ALERTS[slug] || {};
const alertFor = (slug, cat) => alertsOf(slug)[cat];

function setAlert(slug, cat, target) {
  const t = Number(target);
  if (!Number.isFinite(t) || t <= 0) {
    if (ALERTS[slug]) {
      delete ALERTS[slug][cat];
      if (!Object.keys(ALERTS[slug]).length) delete ALERTS[slug];
    }
  } else {
    (ALERTS[slug] = ALERTS[slug] || {})[cat] = Math.round(t);
  }
  writeStore(ALERT_KEY, ALERTS);
}

const alertCount = () =>
  Object.values(ALERTS).reduce((n, m) => n + Object.keys(m).length, 0);

/* Every alert, resolved against what the board says now.

   `state` is the whole point: a target is met, or waiting, or unanswerable
   because the category stopped being listed or the event stopped being
   tracked. An alert is never quietly dropped for any of those -- it is the
   user's, and silently deleting their data to keep a list tidy is not a
   trade this page gets to make. */
function alertRows() {
  const out = [];
  Object.entries(ALERTS).forEach(([slug, cats]) => {
    Object.entries(cats).forEach(([cat, target]) => {
      const e = INDEX[slug] && evOf(slug);
      if (!e) { out.push({slug, cat, target, state: 'gone', e: null}); return; }
      const c = (e.current?.cats || []).find(x => x.name === cat);
      const ask = c ? c.ask : null;
      const state = e.status !== 'active' ? 'retired'
                  : !c ? 'unlisted'
                  : ask == null ? 'noask'
                  : ask <= target ? 'hit' : 'waiting';
      out.push({slug, cat, target, ask, qty: c ? c.qty : null, state, e});
    });
  });
  // met first, then the closest to being met, so the list is ordered by how
  // much it wants your attention
  const rank = {hit: 0, waiting: 1, noask: 2, unlisted: 3, retired: 4, gone: 5};
  return out.sort((a, b) => rank[a.state] - rank[b.state]
    || (a.ask ?? Infinity) - a.target - ((b.ask ?? Infinity) - b.target));
}

const alertsHit = () => alertRows().filter(r => r.state === 'hit');

/* ---- what changed since your last visit ----

   Two snapshots, not one. Rotating on every load would mean the first reload
   after a three-day gap replaced the three-day-old baseline with one from
   thirty seconds ago, wiping the very deltas you had just opened the page to
   read. `prev` is the visit before this one and is what gets compared; `cur`
   only becomes `prev` once a real gap has passed. */
const VISIT_GAP_MS = 30 * 60 * 1000;
let SEEN_BASE = null;                 // slug -> [value, pinnedCat] as last seen

function snapPrices() {
  const rows = {};
  ORDER.forEach(slug => {
    const e = evOf(slug);
    if (e.status !== 'active') return;
    const w = rowView(e);
    if (w.floor != null) rows[slug] = [w.floor, w.pinned || null];
  });
  return rows;
}

/* Called once, from boot, after the index is in. Never from the poll: a
   refresh is the same visit continuing. */
function rollVisit() {
  const st = readStore(SEEN_KEY);
  const now = Date.now();
  const cur = st.cur && st.cur.rows ? st.cur : null;
  const prev = st.prev && st.prev.rows ? st.prev : null;
  SEEN_BASE = prev ? prev.rows : null;
  if (!cur) {                                   // first ever visit
    writeStore(SEEN_KEY, {prev: null, cur: {at: now, rows: snapPrices()}});
    return;
  }
  if (now - (cur.at || 0) < VISIT_GAP_MS) return;   // still the same visit
  writeStore(SEEN_KEY, {prev: cur, cur: {at: now, rows: snapPrices()}});
  SEEN_BASE = cur.rows;
}

/* Only movements worth reading. Everything on the board drifts over three
   days, so a favourite always counts and anything else has to clear the same
   bar the movers banner uses. */
function movedSince() {
  if (!SEEN_BASE) return [];
  const out = [];
  ORDER.forEach(slug => {
    const was = SEEN_BASE[slug];
    if (!was) return;
    const e = evOf(slug);
    if (e.status !== 'active') return;
    const w = rowView(e);
    if (w.floor == null) return;
    // a pin changed since then compares two different tickets, so it does not
    // get compared at all
    if ((was[1] || null) !== (w.pinned || null)) return;
    const d = w.floor - was[0];
    if (!d) return;
    if (!isFav(slug) && Math.abs(d) < MOVER_MIN_DROP) return;
    out.push({e, slug, was: was[0], now: w.floor, d, pinned: w.pinned});
  });
  return out.sort((a, b) => Math.abs(b.d) - Math.abs(a.d));
}

/* The numbers a row should show: the favourited category's if there is one and
   it is still listed, otherwise the event's own. `pinned` says which happened,
   so a row showing GA can say so rather than mislabelling it "floor".

   Every figure a category carries is answered here, because a half-routed row
   is the worst of both worlds: a $150 two-day ask beside the event's 24h move
   is two different tickets printed as one line. The only numbers that stay
   event-wide are the ones CrowdVolt only publishes per event -- the last sale
   and the bidder count -- and those say so wherever they appear. */
function rowView(e) {
  const c = e.current || {};
  const want = favCat(e.slug);
  const cat = want && (c.cats || []).find(x => x.name === want);
  if (!cat) {
    return {floor: c.floor, fair: c.primary, vs_fair: c.vs_primary,
            vs_fair_pct: c.vs_primary_pct, fair_basis: c.primary_basis,
            change1h: c.change1h, change2h: c.change2h, change4h: c.change4h,
            change24: c.change24, change3d: c.change3d, change7d: c.change7d,
            low7d: c.low7d, at_low: c.at_low, bid: c.bid, spread: c.spread,
            readings: c.readings, tickets: c.tickets, pinned: null,
            lost: want ? want : null};
  }
  return {floor: cat.ask, fair: cat.fair, vs_fair: cat.vs_fair,
          vs_fair_pct: cat.vs_fair_pct, fair_basis: cat.fair_basis,
          change1h: cat.change1h, change2h: cat.change2h, change4h: cat.change4h,
          change24: cat.change24, change3d: cat.change3d, change7d: cat.change7d,
          low7d: cat.low7d, at_low: cat.at_low, bid: cat.bid, spread: cat.spread,
          readings: cat.readings, tickets: cat.qty, pinned: cat.name, lost: null};
}

/* The tag that stops a pinned row lying about what it is quoting. One helper
   so every view says it the same way, and so a view that forgets it is a
   missing call rather than a differently worded excuse. `short` is for the
   calendar, where a cell is 150px wide and the tooltip carries the full name. */
const PIN_TIP = c => `${c} is pinned — every view shows that category's `
  + `numbers instead of the event's cheapest`;
const LOST_TIP = c => `${c} is no longer listed — showing the event floor instead`;
const pinTag = (w, short) => w.pinned
  ? `<span class="pin cat" title="${esc(PIN_TIP(w.pinned))}">${
      esc(short ? shortCat(w.pinned) : w.pinned)}</span>`
  : w.lost
  ? `<span class="pin cat lost" title="${esc(LOST_TIP(w.lost))}">${
      esc(short ? shortCat(w.lost) : w.lost)} gone</span>`
  : '';
/* The same fact where there is room to spell it out: a card is not a calendar
   cell, and "showing X" answers the question the bare name only hints at. */
const pinPill = w => w.pinned
  ? `<span class="pin" title="${esc(PIN_TIP(w.pinned))}">showing ${esc(w.pinned)}</span>`
  : w.lost
  ? `<span class="pin lost" title="${esc(LOST_TIP(w.lost))}">${
      esc(w.lost)} gone — showing the event floor</span>`
  : '';
let GENRE_SLOT = {}, GENRE_RANK = [];   // genre -> palette slot, computed once
const pickedGenres = new Set();
const HIST = {};                    // slug -> history, fetched on demand
// The bars are the noisiest thing on the plot and the only reason a second
// axis exists, so the chart opens on price alone and supply is asked for --
// unless you asked for it last time, which is remembered per browser.
const VIEW_KEY = 'cv.view.v1';
let tab = 'calendar', range = 0, view = 'ask';
try {
  // validated rather than trusted: a key left by another version should fall
  // back to the default, not wedge the chart on a layout that no longer exists
  const v = localStorage.getItem(VIEW_KEY);
  if (v === 'ask' || v === 'both') view = v;
} catch (e) {}
let showRetired = false, query = '';
const listSort = {key: 'date', dir: 1};
const dealSort = {key: 'vsfpct', dir: 1};   // deepest discount first
let month = new Date(); month.setDate(1);
const filters = {};                 // slug -> category name
const charts = [];
let openSlug = null;

/* ---------------- data ---------------- */

async function boot() {
  try {
    // the buffer holds every reading since the last daily consolidation, so a
    // chart is current even though the per-event files are rewritten once a day
    const [idx, recent] = await Promise.all([
      fetch(DATA_BASE + 'index.json', {cache: 'no-cache'}).then(r => r.json()),
      fetch(DATA_BASE + 'recent.json', {cache: 'no-cache'})
        .then(r => r.ok ? r.json() : {}).catch(() => ({})),
    ]);
    INDEX = idx.events || {};
    RECENT = recent || {};
    GENERATED = idx.generated_at || null;
  } catch (e) {
    $('main').innerHTML = `<p class="empty">Could not load <code>${esc(DATA_BASE)}index.json</code>.
      Run <code>python3 track.py</code>, or <code>python3 track.py --serve</code> locally.</p>`;
    return;
  }
  ORDER = Object.keys(INDEX);
  buildGenres();
  loadFavs();
  NOTES = readStore(NOTE_KEY);
  ALERTS = readStore(ALERT_KEY);
  // after loadFavs: the snapshot records the number each row was showing, and
  // a pinned category is what a pinned row shows
  rollVisit();
  renderTabs();
  renderStats();
  renderMovers();
  renderBell();
  renderChatBtn();
  chatFetch('general').then(renderChatBtn);
  render();
}

/* Concatenate the buffer onto the consolidated file. Mirrors store._merge:
   readings only ever arrive in order, but a category present in one and not
   the other needs padding so every series stays the length of `stamps`. */
function mergeRecent(base, extra) {
  base = base || {stamps: [], types: {}, event: {}};
  if (!extra || !(extra.stamps || []).length) return base;
  const n0 = (base.stamps || []).length, n1 = extra.stamps.length;
  const pad = (a, n) => { a = (a || []).slice(); while (a.length < n) a.push(null); return a; };
  const out = {stamps: (base.stamps || []).concat(extra.stamps), types: {}, event: {}};

  new Set([...Object.keys(base.types || {}), ...Object.keys(extra.types || {})])
    .forEach(name => {
      const a = (base.types || {})[name] || {}, b = (extra.types || {})[name] || {};
      const t = {uqid: b.uqid || a.uqid};
      if (b.linked_count || a.linked_count) t.linked_count = b.linked_count || a.linked_count;
      ['ask','all_in','ask_qty','bid','bid_qty','qty','listings'].forEach(f => {
        t[f] = pad(a[f], n0).concat(pad(b[f], n1));
      });
      out.types[name] = t;
    });
  new Set([...Object.keys(base.event || {}), ...Object.keys(extra.event || {})])
    .forEach(k => {
      out.event[k] = pad((base.event || {})[k], n0).concat(pad((extra.event || {})[k], n1));
    });
  return out;
}

async function history(slug) {
  if (!HIST[slug]) {
    HIST[slug] = fetch(`${DATA_BASE}events/${encodeURIComponent(slug)}.json`)
      .then(r => r.ok ? r.json() : {stamps: [], types: {}, event: {}})
      .catch(() => ({stamps: [], types: {}, event: {}}))
      .then(h => mergeRecent(h, RECENT[slug]));
  }
  return HIST[slug];
}

/* ---------------- selectors ---------------- */

const evOf = slug => ({slug, ...INDEX[slug]});
const imgOf = e => e.img_blob || e.img || null;
/* Chips, colours and filters key off the broad group, not the raw genre.
   CrowdVolt's artists carry 96 distinct genres and 62 appear exactly once, so
   filtering on the literal string scatters one scene across chips nobody would
   click. The full list still shows on the event itself. */
const genreOf = e => e.genre_group || ((e.genres && e.genres[0]) || null);
const genresFull = e => e.genres || [];
/* "GA (2-Day Access)" will not fit a calendar cell; the tooltip carries the
   full name. */
const shortCat = n => {
  const t = String(n).replace(/general admission/i, 'GA').replace(/[()]/g, '');
  return t.length > 12 ? t.slice(0, 11) + '…' : t;
};

/* One glyph, five states, only when the data actually says something: a
   standout deal, a deal, at its cheapest all week, drifting down, drifting up.

   The two deal tiers outrank the flame deliberately. "Cheapest in 7 days" is a
   claim about the event's own recent history, and can still be a bad price;
   "below what the same ticket costs on the platform that sold it first" is
   measured against the alternative you actually have, which is the harder
   fact. Any amount below counts -- it is a binary claim, not a big one.

   A signal marked `badge` stands on its own and takes the calendar's
   right-hand slot outright, where the arrows share it with the amount. Flagged
   rather than listed by name, so a new signal cannot be forgotten there. */
/* The flame is a claim about a price against its own history, so it needs
   three things: to be at the low, enough readings for "the low" to mean
   anything, and a week that actually fell — being at a low you have sat at all
   week is not news.

   One predicate because the card's pill and the calendar's badge had drifted:
   the pill omitted the falling-week test, so a card could show a flame the
   calendar did not. Both now ask the same question of the same numbers, and a
   pinned category answers it on its own merits. */
function atWeekLow(w) {
  return !!(w.at_low && w.readings > 3 && (w.change7d ?? 0) < 0);
}

function signalOf(e) {
  // A pinned category has to answer these questions about itself: an event
  // whose cheapest tier is a bargain says nothing about the VIP you pinned.
  // Every figure below is that category's own wherever one is pinned.
  const w = rowView(e);
  const d = w.change24 ?? w.change3d ?? null;
  const pct = d != null && w.floor ? d / (w.floor - d) : null;
  // No reading count to satisfy: this compares two current numbers, so unlike
  // the flame it needs no history to be true. Below a sold-out primary counts
  // too -- under what the last buyer paid at face, with resale the only way
  // left in, is exactly the bargain worth flagging.
  const under = vsPrimaryPct(e);
  // The bolt is not the star in a brighter green: at 11px in a calendar cell
  // colour is the first thing to go, so the two tiers differ in shape before
  // they differ in anything else.
  if (under != null && under <= STANDOUT) return {
    icon: '⚡', cls: 'down', badge: true, title: dealTitle(w, under)};
  if (under != null && under < 0) return {
    icon: '★', cls: 'down', badge: true, title: dealTitle(w, under)};
  if (atWeekLow(w)) return {
    icon: '🔥', cls: 'down', badge: true, title: `${
      w.pinned ? w.pinned + ': ' : ''}cheapest in 7 days (${money(w.floor)})`};
  if (pct != null && pct <= -0.03) return {
    icon: '−', cls: 'down', title: `down ${money(Math.abs(d))} recently`};
  if (pct != null && pct >= 0.03) return {
    icon: '+', cls: 'up', title: `up ${money(d)} recently`};
  return null;
}
/* Only the badges -- the deal, the star, the week low. The bare -/+ signals
   say nothing an amount beside them does not say better, so anywhere the
   amount is shown they would just be the same fact twice. */
const badgeSignal = e => {
  const s = signalOf(e);
  return s && s.badge
    ? `<span class="sig ${s.cls}" title="${esc(s.title)}">${s.icon}</span>` : '';
};
const signal = e => {
  const s = signalOf(e);
  return s ? `<span class="sig ${s.cls}" title="${esc(s.title)}">${s.icon}</span>` : '';
};

/* The calendar's right-hand slot: how far the price moved in a day, as the
   glyph plus its size. A price that has not moved says nothing rather than
   printing a dash in every cell; the flame still outranks a plain arrow. The
   element is always emitted, empty or not, so the prices beside it stay in a
   column. */
/* The badge and the amount answer different questions -- "is this worth a
   look" and "what has it done today" -- so a cell carries both. The badge used
   to stand in for the number, which meant the events most worth looking at
   were the only ones whose movement you could not see.

   The empty <em> when nothing moved is deliberate: it holds the column open
   so the prices above and below it still line up. */
function move24(e, reserve) {
  const s = signalOf(e), d = Math.round(rowView(e).change24 ?? 0);
  // The badge slot is held open on every row of a month that uses one, so the
  // price and the amount stay in their columns instead of stepping sideways on
  // whichever rows happen to carry an icon. A month with no badges at all
  // reserves nothing and gives the width back to the names.
  const badge = s && s.badge
    ? `<i class="sig ${s.cls}" title="${esc(s.title)}">${s.icon}</i>`
    : reserve ? '<i class="sig"></i>' : '';
  if (!d) return `<span class="mvbox">${badge}<em class="mv"></em></span>`;
  const tip = (d > 0 ? 'up ' : 'down ') + money(Math.abs(d)) + ' in the last 24h';
  return `<span class="mvbox">${badge}<em class="mv ${d > 0 ? 'up' : 'down'}"
    title="${esc(tip)}">${(d > 0 ? '+' : '−') + money(Math.abs(d))}</em></span>`;
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
const floorOf = e => rowView(e).floor ?? null;
const ticketsOf = e => rowView(e).tickets ?? 0;
/* What the cheapest ask wants over what somebody last actually paid. Below the
   last sale is the good side of this one -- the opposite polarity to the
   change columns, where it is a rising price that is the bad news. */
/* CrowdVolt publishes one last sale per event and none per category, so the
   figure is only attributable when the event has a single category to sell --
   which is exactly what `last_sale_scope` records. Everywhere else the trade
   could have been any tier, and subtracting it from the cheapest one invents a
   saving: an event last sold at $258 with tiers asking $181 and $258 plainly
   sold the dearer one, and "−$77" against the cheaper is a bargain that never
   existed. So there is no delta at all there, pinned or not -- the ambiguity
   belongs to the event, not to what you chose to look at. */
const saleKnown = e => (e.current || {}).last_sale_scope === 'category';
const SALE_VAGUE = 'this event lists several ticket categories and CrowdVolt reports '
  + 'one last sale for the whole event, so there is no telling which category sold — '
  + 'no comparison against an asking price would be honest';
const vsSale = e => {
  const c = e.current || {}, w = rowView(e);
  if (!saleKnown(e)) return null;
  return w.floor != null && c.last_sale != null ? Math.round(w.floor - c.last_sale) : null;
};
/* The event-wide fair value -- the cheapest way to buy this new, or once the
   primary has sold out, what it was going for when it ran out -- and how the
   resale floor compares. Negative is the good side again: below the fair value
   the listing is a real saving. Events on a platform we cannot read carry no
   number at all: a blank, never a zero. */
const primaryOf = e => rowView(e).fair ?? null;
const primaryGone = e => rowView(e).fair_basis === 'last-sold';
const vsPrimary = e => {
  const v = rowView(e).vs_fair;
  return v == null ? null : Math.round(v);
};
/* The same comparison as a fraction of what the ticket is worth. (Derived
   here when the reading predates the field: it is exactly `vs_primary` over
   `primary`, so the two agree to the last decimal.) */
const vsPrimaryPct = e => {
  const w = rowView(e);
  if (w.vs_fair_pct != null) return w.vs_fair_pct;
  return w.vs_fair != null && w.fair ? w.vs_fair / w.fair : null;
};
/* Under a dollar, say the cents: "$0 below fair value" reads as no saving at
   all when the point is that there is one. */
const dealTitle = (w, under) => {
  const v = Math.abs(w.vs_fair);
  return `${w.pinned ? w.pinned + ': ' : ''}${v < 1 ? exact(v) : money(v)} (${
    pctOf(under)}) below ${
    w.fair_basis === 'last-sold'
      ? `the last face value — ${money(w.fair)} before the primary sold out`
      : `fair value${w.fair != null ? ` — ${money(w.fair)} to buy direct` : ''}`}`;
};
const vsPrimaryTip = (v, gone) => v > 0
  ? (gone ? `resale asks ${money(v)} more than the last face value before it sold out`
          : `resale is ${money(v)} more than buying direct — buy from the primary instead`)
  : (gone ? `resale floor is ${money(Math.abs(v))} below the last face value`
          : `resale floor is ${money(Math.abs(v))} below buying direct`);
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
    // event-level, and parallel to stamps like the other event series
    fair: ((h.event || {}).fair || []).slice(from),
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

const FAIR = 'fair value';

/* Fair value over time, drawn against the resale line it belongs to.

   Colour normally names a series, but the entity here is the ticket category:
   its resale ask and its fair value are two measures of the same thing, so the
   hue says "same ticket" and the dash says "different source". A neutral line
   would read as an unrelated third series, which is the opposite of the point.

   The shape is what a single figure cannot show. A tier sells out, the next
   opens dearer, the primary climbs while an ask sits still -- an event heating
   up is exactly those two lines diverging.

   `spanGaps` because the series only reaches back as far as the readings do,
   and a visible point when there is only one of them, since a one-point line
   drawn with no point markers is a line you cannot see. */
function fairSets(e, series, fair, n) {
  const pal = colors();
  return series.map(s => {
    const val = fairVal(fairOf(e, s.name));
    if (val == null) return null;
    // real shape where the event-level series is honestly this category's own
    // history, and otherwise its own level, flat -- never somebody else's shape
    const data = fairTracks(e, s.name, fair) ? fair : new Array(n).fill(val);
    return {
      type: 'line', label: `${s.name} — ${FAIR}`, endLabel: false, data,
      borderColor: pal[s.idx % pal.length], backgroundColor: pal[s.idx % pal.length],
      borderWidth: 1.5, borderDash: [5, 4], tension: 0, spanGaps: true,
      pointRadius: data.filter(v => v != null).length === 1 ? 3 : 0,
      pointHoverRadius: 4, yAxisID: 'y', order: 1,
    };
  }).filter(Boolean);
}

/* Whether the event-level series is honestly this category's own history. It
   records the cheapest comparable ticket across the whole event, so on a VIP
   row it would sit at the GA price and quietly contradict the number in the
   table beside it. Where the two agree the series is that category's own past;
   where they do not, the category keeps its own figure and simply has no
   movement to show, which a flat line says accurately. */
function fairTracks(e, cat, fair) {
  const last = lastDefined(fair), val = fairVal(fairOf(e, cat));
  // within a dollar -- rounding, nothing more. Any further apart and the line
  // would end somewhere other than the figure in the row beside it
  return last != null && val != null && Math.abs(last - val) <= 1;
}

function datasets(series, kind) {
  const pal = colors(), hue = s => pal[s.idx % pal.length], out = [];
  series.forEach(s => out.push({
    type:'line', label: s.name + ' — ask', endLabelText: s.name, data: s.ask,
    borderColor: hue(s), backgroundColor: hue(s), borderWidth: 2,
    pointRadius: 0, pointHoverRadius: 5, tension: .25, spanGaps: true,
    yAxisID: 'y', order: 1,
  }));
  if (kind === 'both') series.forEach(s => out.push({
    type:'bar', label: s.name + ' — tickets', data: s.qty,
    backgroundColor: hue(s) + '2b', borderColor: hue(s) + '45',
    borderWidth: 1, borderRadius: 3, borderSkipped: false,
    categoryPercentage: .8, barPercentage: .9,
    yAxisID: 'y1', order: 2,
  }));
  return out;
}

function priceBounds(series, extra) {
  // the fair-value line is a real series now, so its values belong on the axis
  // like any other -- an ask sitting at twice the face is the whole story
  const vals = series.flatMap(s => s.ask)
    .concat(extra || []).filter(v => v != null);
  if (!vals.length) return {};
  const lo = Math.min(...vals), hi = Math.max(...vals);
  // headroom below the floor price so the line is not glued to the axis,
  // without flattening the movement the way a $0 baseline does
  const pad = Math.max(2, (hi - lo) * 0.35, hi * 0.04);
  return {min: Math.max(0, Math.floor((lo - pad) / 5) * 5),
          max: Math.ceil((hi + pad) / 5) * 5};
}

/* Names the lines where they end, instead of in a legend.

   A legend makes the eye travel to a key and back for every line; a label at
   the end of the line is read in place. It also means identity never rests on
   colour alone, which a legend beside a colour swatch does.

   Labels are nudged apart when two lines finish close together, and the plot
   reserves right-hand room for them so nothing is clipped. */
function labelPlugin() {
  return {
    id: 'endLabels',
    afterDatasetsDraw(chart, _args, opts) {
      const {ctx, chartArea} = chart;
      const placed = [];
      ctx.save();
      ctx.font = `600 ${isPhone() ? 10 : 11}px ${getComputedStyle(document.body).fontFamily}`;
      ctx.textBaseline = 'middle';
      chart.data.datasets.forEach((ds, i) => {
        if (ds.type !== 'line' || ds.endLabel === false) return;
        const meta = chart.getDatasetMeta(i);
        if (meta.hidden) return;
        // the last point that actually has a value: a series can end in nulls
        const pts = meta.data.filter((p, j) => ds.data[j] != null);
        const last = pts[pts.length - 1];
        if (!last) return;
        let y = last.y;
        while (placed.some(p => Math.abs(p - y) < 13)) y += 13;
        y = Math.min(Math.max(y, chartArea.top + 7), chartArea.bottom - 7);
        placed.push(y);
        // the supply bars bring a right-hand axis, which sits between the
        // plot and the labels -- start past it or the two overprint
        const x = chartArea.right + (chart.scales.y1 ? chart.scales.y1.width : 0) + 7;
        const room = chart.width - x - 5;
        // trim to the room actually reserved rather than hoping it fits:
        // "General Admission" overruns, and a clipped label is worse than an
        // elided one because you cannot tell it has been cut
        let text = ds.endLabelText || ds.label || '';
        if (ctx.measureText(text).width > room) {
          while (text.length > 1 && ctx.measureText(text + '…').width > room) {
            text = text.slice(0, -1);
          }
          text += '…';
        }
        ctx.fillStyle = ds.borderColor;
        ctx.fillText(text, x, y);
      });
      ctx.restore();
    },
  };
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
        // always shown: this used to be the upper panel of a split pair, where
        // the lower one carried the dates. It is the only chart now.
        ticks},
    y: {
      position:'left', grid, border: {display: false},
      // the price axis sits just below the floor rather than at zero: a $6
      // move on a $90 ticket is the whole story and a $0 baseline flattens it.
      // A count of tickets is a different quantity and does start at zero.
      ...(bounds || {}),
      ticks: {...ticks, callback: v => '$' + v},
      title: axisTitle('Best ask, all-in'),
    },
  };
  if (kind === 'both') scales.y1 = {
    position:'right', grid: {display: false}, border: {display: false},
    beginAtZero: true, ticks: {...ticks, precision: 0},
    title: axisTitle('Tickets available'),
  };
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    // room for the names that sit past the last point
    layout: {padding: {right: tight ? 74 : 118}},
    interaction: {mode: 'index', intersect: false}, scales,
    plugins: {
      // the category table is the legend now -- its swatch is the series
      // colour -- so the price chart does not need a second one
      // every line names itself where it ends, so there is no key to consult
      legend: {display: false},
      tooltip: {
        backgroundColor: css('--surface'), titleColor: css('--ink'), bodyColor: css('--ink-2'),
        borderColor: css('--border'), borderWidth: 1, padding: 10, boxPadding: 4,
        usePointStyle: true,
        callbacks: {label: c => c.dataset.label.endsWith(FAIR)
          ? `${c.dataset.label.replace(` — ${FAIR}`, '')} — ${FAIR}: ${money(c.parsed.y)}`
          : c.dataset.label.endsWith('— tickets')
          ? `${c.dataset.label.replace(' — tickets','')}: ${c.parsed.y} available`
          : `${c.dataset.label.replace(' — ask','')}: ${money(c.parsed.y)} all-in`},
      },
    },
  };
}

/* ---------------- one event ---------------- */

/* The fair value for one resale category: the cheapest comparable ticket still
   buyable on the platform that sold the event first. Per category, because
   "GA" and "VIP" are not the same question. */
const fairOf = (e, cat) => ((e.primary || {}).by_category || {})[cat] || null;

/* The number to compare against. A primary that has sold out is not "no fair
   value" -- it is the case where the comparison matters most, because resale
   is then the only way in and the markup can be enormous. So when nothing is
   on sale any more the figure falls back to what the primary was charging when
   it ran out, and `basis` records which of the two it is. (`value` postdates
   the first version of this field, hence the fall back to `fair`.) */
const fairVal = fv => {
  if (!fv) return null;
  if (fv.value !== undefined) return fv.value;
  // written before `value` existed: make the same fall back here, so a
  // half-backfilled reading still answers instead of showing a dash
  return fv.fair != null ? fv.fair : fv.last_face ?? null;
};
const isPast = fv => fairVal(fv) != null &&
  (fv.basis === 'last-sold' || (fv.basis === undefined && fv.fair == null));
const PAST_NOTE = v => `every tier has sold out — ${money(v)} is what it was going for `
  + 'when it ran out, not a price you can still buy at';

/* The honest test of confidence is not whether two counts of different things
   agree -- CrowdVolt's ticket types and DICE's tiers are not the same objects,
   and comparing them cries wolf. It is whether picking a different plausible
   pool of tiers would have produced a different price. When every candidate
   lands on the same number there is nothing to warn about, so most rows say
   nothing at all: silence is the signal for a value we are sure of. */
function fairNote(fv) {
  if (!fv || !fv.ambiguous) return null;
  const val = fairVal(fv);
  const alts = (fv.alternatives || []).filter(v => v != null && v !== val);
  return 'more than one primary tier could be this ticket'
    + (alts.length ? ` — it could also be read as ${alts.map(exact).join(' or ')}` : '');
}

/* One row per resale category: the chart's legend, the fair-value comparison
   and the category filter in a single small table. A row is a button -- click
   to isolate that category, click again for all of them back. */
/* A line of your own about the event. Only in the modal: an editable field on
   each of a hundred and seventy cards is a lot of page for something you write
   once, and the marker in the list is enough to say a note exists. */
function noteBox(e) {
  const n = noteOf(e.slug);
  return `<div class="notebox"><input id="noteFor" type="text" maxlength="280"
    value="${esc(n)}" data-note="${esc(e.slug)}"
    placeholder="Add a note — only you will see it"
    aria-label="Your note about this event"></div>`;
}

function wireNote(root = document) {
  const i = $('[data-note]', root);
  if (!i) return;
  const save = () => {
    const before = noteOf(i.dataset.note);
    setNote(i.dataset.note, i.value);
    // only repaint when the marker in the list would actually change
    if (!before !== !noteOf(i.dataset.note)) render();
  };
  i.onkeydown = ev => {
    ev.stopPropagation();
    if (ev.key === 'Enter') { ev.preventDefault(); i.blur(); }
  };
  i.onblur = save;
}

function catTable(e) {
  const cats = e.current?.cats || [];
  if (!cats.length) return '';
  const pal = colors(), picked = filters[e.slug];
  const rows = cats.map((c, i) => {
    const fv = fairOf(e, c.name), fair = fairVal(fv), past = isPast(fv);
    const star = favBtn(e.slug, c.name);
    // struck, not hidden: the same treatment the ladder gives a gone tier, and
    // the delta against it stays the loudest thing in the row
    // a fair value you can only reach by buying two tickets is not a
    // per-ticket price, and saying so is cheaper than quietly overstating it
    const bundle = fair != null && fv.bundles_only;
    const notes = fair == null ? [] : [
      past && PAST_NOTE(fair),
      bundle && 'every comparable tier on the primary is sold as a bundle — '
        + 'this is not a price you can pay for a single ticket',
      fairNote(fv),
    ].filter(Boolean);
    const cls = [past && 'past', fair != null && fairNote(fv) && 'soft'].filter(Boolean).join(' ');
    const face = (fair != null ? money(fair)
      : fv && fv.sold_out ? '<span class="out">sold out</span>' : '—')
      + (bundle ? '<i class="note">bundle only</i>' : '');
    const d = fair != null && c.ask != null ? Math.round(c.ask - fair) : null;
    const on = picked === c.name;
    return `<tr class="crow" data-cat="${esc(c.name)}" role="button" tabindex="0"
      aria-pressed="${on}"${!picked || on ? '' : ' data-off="1"'}
      title="${on ? 'Show all categories' : 'Show only ' + esc(c.name)}">
      <td class="l nm">${star}<i class="swatch" style="background:${
        pal[i % pal.length]}"></i>${esc(c.name)}${alertBtn(e.slug, c.name)}</td>
      <td>${c.ask == null ? '—' : money(c.ask)}</td>
      <td${cls ? ` class="${cls}"` : ''}${notes.length ? ` title="${esc(notes.join(' · '))}"` : ''}>${face}</td>
      <td class="d ${!d ? '' : d > 0 ? 'up' : 'down'}"${d ? ` title="${
        esc(d > 0 ? `${money(d)} more than buying this new — buy from the primary instead`
                  : `${money(Math.abs(d))} cheaper than buying this new`)}"` : ''}>${
        d ? delta(d) : '—'}</td>
      <td>${c.qty}</td>
    </tr>`;
  }).join('');
  return `<table class="cats"><thead><tr>
    <th class="l nm">Category</th>
    <th title="cheapest ask right now, all-in">Ask</th>
    <th title="cheapest comparable ticket still on sale on the platform that sold it first — or, once every tier has gone, what it was going for when it sold out">Fair value</th>
    <th title="the ask against that fair value — below it is the good side">vs fair</th>
    <th>Tickets</th></tr></thead><tbody>${rows}</tbody></table>`;
}

/* The primary platform's tiers, cheapest first. Deliberately text and not a
   chart: four numbers and a strike-through say "Tier 1 gone, Tier 2 gone, only
   the Final is left" faster than any plot of four points could, and the plot
   the card already has is about the resale.

   Only the modal gets it. It is context for an event you have chosen to look
   at, and the Charts view is already a hundred cards deep. */
/* Whether the fair-value column actually answered anything. When it did not --
   no category could be matched to a tier -- the ladder is the fallback, and it
   is opened rather than folded away: reading a $91 ask against a real ladder
   by eye is far better than a column of dashes. */
const mapped = e => (e.current?.cats || []).some(c => fairVal(fairOf(e, c.name)) != null);

function tierLadder(e) {
  const p = e.primary || {}, tiers = p.tiers || [];
  if (!tiers.length) return '';
  const gone = tiers.filter(t => t.status !== 'on-sale').length;
  const rows = tiers.map(t => {
    const live = t.status === 'on-sale';
    return `<tr class="${live ? 'live' : 'gone'}">
      <td class="l nm">${esc(t.name || 'Tier')}</td>
      <td class="pr">${exact(t.price)}</td>
      <td class="${live ? '' : 'out'}">${live ? 'on sale'
        : esc(String(t.status || 'sold out').replace(/[-_]/g, ' '))}</td></tr>`;
  }).join('');
  const table = `<div class="lad"><table class="cats tiers"><thead><tr>
    <th class="l nm">Tier</th><th>Price</th><th>Status</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
  // the two facts worth having without opening it: what the event first went
  // on sale at, and how much of the ladder is already gone
  const sum = `${esc(p.platform || 'Primary')} tiers — from ${exact(tiers[0].price)}, ${
    gone ? `${gone} of ${tiers.length} sold out` : `all ${tiers.length} on sale`}`;
  return mapped(e)
    ? `<details class="ladder"><summary>${sum}</summary>${table}</details>`
    : `<div class="ladder"><div class="lnote">${sum}. No resale category could be `
      + `matched to a tier, so compare the asks above against the ladder itself.`
      + `</div>${table}</div>`;
}

/* Where to go to actually buy the thing. The title used to be the CrowdVolt
   link, which read as a link to more detail rather than to another site; an
   explicit mark per destination says where you are being sent, and the primary
   link only exists for the platforms we can price -- if we could not read
   their prices we have no id to link to either. */
const SOURCE_ICONS = {
  CrowdVolt: DATA_BASE + 'icons/crowdvolt.png',
  DICE: DATA_BASE + 'icons/dice.png',
  Eventbrite: DATA_BASE + 'icons/eventbrite.png',
};

function sourceLinks(e) {
  const out = [];
  if (e.url) out.push([e.url, 'CrowdVolt', 'resale listings on CrowdVolt']);
  const p = e.primary || {};
  if (p.url && SOURCE_ICONS[p.platform]) {
    out.push([p.url, p.platform, `buy direct on ${p.platform}`]);
  }
  return out.map(([href, label, title]) =>
    `<a class="src" href="${esc(href)}" target="_blank" rel="noreferrer noopener"
        title="${esc(title)}" aria-label="${esc(title)}"
     ><img src="${esc(SOURCE_ICONS[label])}" alt="${esc(label)}" decoding="async"></a>`
  ).join('');
}

const STAR_ON = '★', STAR_OFF = '☆';

/* "at or below this" as the control's own label, rather than a bell that has
   to be learnt. Unset it is an empty ceiling you can click and fill in. */
function alertBtn(slug, cat) {
  const t = alertFor(slug, cat);
  return `<button class="abtn${t != null ? ' on' : ''}" data-alert="${esc(slug)}"
    data-acat="${esc(cat)}" title="${esc(t != null
      ? `Alert set: tell me when ${cat} is ${money(t)} or less — click to change`
      : `Alert me when ${cat} drops to a price I set`)}"
    >≤&thinsp;${t != null ? esc(money(t)) : '$'}</button>`;
}

function favBtn(slug, cat, extra = '') {
  const on = cat === undefined ? isFav(slug) : favCat(slug) === cat;
  const what = cat === undefined ? 'this event'
             : (on ? `${cat} — click to unpin` : `pin ${cat} to every view`);
  return `<button class="fav${on ? ' on' : ''} ${extra}" data-fav="${esc(slug)}"
    ${cat === undefined ? '' : `data-favcat="${esc(cat)}"`}
    aria-pressed="${on}" title="${esc(on ? `Remove ${what}` : `Favourite ${what}`)}"
    >${on ? STAR_ON : STAR_OFF}</button>`;
}

/* The editor opens inside a <tr role="button" tabindex="0"> that answers
   click, Enter and Space by re-rendering the whole modal -- which would throw
   away the input mid-type. Every key and click inside the editor therefore
   stops where it is. */
function wireAlerts(root = document) {
  $$('[data-alert]', root).forEach(b => {
    b.onclick = ev => { ev.stopPropagation(); openAlertEditor(b); };
    b.onkeydown = ev => ev.stopPropagation();
  });
}

function openAlertEditor(btn) {
  const slug = btn.dataset.alert, cat = btn.dataset.acat;
  const cur = alertFor(slug, cat);
  const e = INDEX[slug] && evOf(slug);
  const ask = ((e && e.current?.cats) || []).find(x => x.name === cat)?.ask;
  const box = document.createElement('span');
  box.className = 'aedit';
  box.innerHTML = `<span class="pre">≤&thinsp;$</span><input type="number" min="1"
    step="1" inputmode="numeric" value="${cur != null ? cur : ''}"
    placeholder="${ask != null ? Math.max(1, Math.floor(ask * 0.9)) : ''}"
    aria-label="Alert me at or below this price">`;
  btn.replaceWith(box);
  const input = $('input', box);
  const restore = () => {
    const fresh = document.createElement('span');
    fresh.innerHTML = alertBtn(slug, cat);
    box.replaceWith(fresh.firstElementChild);
    wireAlerts(fresh.parentElement || document);
  };
  const commit = () => {
    if (done) return;
    done = true;
    setAlert(slug, cat, input.value);
    renderBell();
    restore();
    wireAlerts(document);
  };
  let done = false;
  ['click', 'keyup'].forEach(t => box.addEventListener(t, ev => ev.stopPropagation()));
  input.onkeydown = ev => {
    ev.stopPropagation();
    if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
    // Escape leaves whatever was already stored alone
    if (ev.key === 'Escape') { done = true; restore(); wireAlerts(document); }
  };
  input.onblur = commit;
  input.focus();
  input.select();
}

/* One handler for every star on the page, so a new one cannot be forgotten. */
function wireFavs(root = document) {
  $$('[data-fav]', root).forEach(b => b.onclick = ev => {
    ev.stopPropagation();
    toggleFav(b.dataset.fav, 'favcat' in b.dataset ? b.dataset.favcat : undefined);
    renderTabs();
    // the stat strip and the movers read pinned numbers too, so a star has to
    // repaint them or they keep quoting the event floor until the next poll
    renderStats();
    renderMovers();
    render();
  });
}

function cardShell(e, id) {
  const where = [e.venue, e.city].filter(Boolean).join(' · ');
  const tag = e.status === 'active' ? '' :
    `<span class="tag">${esc(String(e.status).replace('_',' '))}${e.retired_reason
      ? ' — ' + esc(e.retired_reason.split(': ').pop()) : ''}</span>`;
  const c = e.current || {}, w = rowView(e);
  // a bid of zero is not a bid: CrowdVolt reports 0 where nobody has bid at
  // all, and a spread measured against it is a spread against nothing. The
  // spread arrives precomputed, and is null unless there is a real bid
  const spread = w.spread ?? (w.floor != null && w.bid ? w.floor - w.bid : null);
  const vs = vsSale(e);
  /* Two groups, not one wall. The market numbers are what the page is for, so
     they keep the bordered pill and come first; the descriptive tags are
     context and drop to a quiet line under them, with anything past the first
     few behind a "+n more" rather than spending a second row on a cast list.

     No primary pills here: the category table below states that comparison per
     category and more precisely, and the ladder's summary carries the face
     value. A pill repeating either would be the same number twice. */
  const pills = [
    atWeekLow(w) && '🔥 cheapest in 7 days',
    c.last_sale != null && (saleKnown(e)
      ? `last sale ${money(c.last_sale)}`
      : [`last sale ${money(c.last_sale)} · event-wide`, SALE_VAGUE]),
    vs ? `${money(Math.abs(vs))} ${vs > 0 ? 'above' : 'below'} last sale` : null,
    w.bid ? `best bid ${money(w.bid)}` : null,
    spread != null && `spread ${money(spread)}`,
    // there is no per-category bidder count, so on a pinned card the number is
    // marked the same way the last sale is rather than posing as the tier's own
    c.bidders ? (w.pinned
      ? [`${c.bidders} bidder${c.bidders === 1 ? '' : 's'} · event-wide`,
         `${c.bidders} bidder${c.bidders === 1 ? '' : 's'} across the whole event — `
         + 'CrowdVolt does not break bidders down by category']
      : `${c.bidders} bidder${c.bidders === 1 ? '' : 's'}`) : null,
  ].filter(Boolean).map(t => Array.isArray(t)
    ? `<span class="pill" title="${esc(t[1])}">${esc(t[0])}</span>`
    : `<span class="pill">${esc(t)}</span>`).join('');
  // genres keep the coloured dot they carry everywhere else on the page
  const tags = [
    ...(e.genres || []).map(g => [g, genreColor(g)]),
    e.platform && [e.platform],
    e.ticket_limit && [`limit ${e.ticket_limit}`],
    ...(e.performers || []).map(p => [p.name]),
  ].filter(Boolean);
  const KEEP = 4;
  const tagRow = !tags.length ? '' : `<div class="tags">${
    tags.map(([t, dot], i) => `<span class="qtag"${i < KEEP ? '' : ' hidden'}>${
      dot ? `<i class="gdot" style="background:${dot}"></i>` : ''}${esc(t)}</span>`).join('')}${
    tags.length > KEEP ? `<button class="qmore">+${tags.length - KEEP} more</button>` : ''}</div>`;
  const art = imgOf(e)
    ? `<img src="${esc(imgOf(e))}" alt="" loading="lazy" decoding="async">` : '';
  return `<div class="head">${art}<div class="txt">
      <h3>${esc(e.name || e.slug)}${tag}${sourceLinks(e)}${favBtn(e.slug, undefined, 'big')}</h3>
      <div class="meta">${esc(where)}${where && e.doors ? '<span class="sep">·</span>' : ''}${esc(e.doors || '')}</div>
      <div class="pills">${pinPill(w)}${pills}</div>${tagRow}
    </div></div>
    ${id === 'modal' ? noteBox(e) : ''}
    ${catTable(e)}${id === 'modal' ? tierLadder(e) : ''}
    <div class="wrap price"><canvas id="c-${id}"></canvas></div>
    <div class="axisnote"><span>Best ask, all-in</span><span class="rt">Right: tickets available</span></div>
    ${id === 'modal' ? chatPanel('event/' + e.slug) : ''}`;
}

/* The modal is one long-lived element that gets rewritten, so two opens in
   quick succession can leave a superseded wireCard still waiting on its
   history fetch. When it wakes it draws onto the canvas the newer open has
   since put there, and Chart.js refuses a canvas that already carries a chart.
   Whichever of the two lands last is reading the same state and draws the same
   picture, so clearing the canvas first is enough -- and it costs nothing in
   the ordinary case, where there is no chart to clear. */
function free(canvas) {
  const old = canvas && Chart.getChart(canvas);
  if (old) {
    const i = charts.indexOf(old);
    if (i >= 0) charts.splice(i, 1);
    old.destroy();
  }
  return canvas;
}

async function wireCard(root, e, id) {
  // wired before the history fetch: revealing the rest of a cast list should
  // not wait on a network round trip it has nothing to do with
  const more = $('.qmore', root);
  if (more) more.onclick = () => {
    $$('.qtag[hidden]', root).forEach(t => t.hidden = false);
    more.remove();
  };
  // for the same reason: typing a note has nothing to do with the chart's
  // history, and the early return below would skip them altogether
  wireAlerts(root);
  wireNote(root);
  if (id === 'modal' && CHAT_BASE) wireChat(root, 'event/' + e.slug);
  // the canvas this call set out to draw on. The modal is one long-lived
  // element that gets rewritten, so a second open while this one is still
  // waiting on its history leaves the first drawing onto a canvas that is no
  // longer on the page -- and the newer canvas blank
  const canvas = $(`#c-${id}`, root);
  const h = await history(e.slug);
  if (!root.isConnected) return;
  const d = slice(h), series = shownSeries(e.slug, d);
  // The table is the chart's legend, so its swatches have to be the chart's
  // colours. It is built from `current.cats`, which drops a category that has
  // never had an ask or a ticket, while the chart colours by position in the
  // history -- so past such a gap the two would disagree. Restyle from the
  // series the chart will actually use.
  const pal = colors();
  $$('.crow', root).forEach(row => {
    const s = d.series.find(x => x.name === row.dataset.cat), sw = $('.swatch', row);
    if (s && sw) sw.style.background = pal[s.idx % pal.length];
  });
  const withBars = view === 'both';
  const c = $(`#c-${id}`, root);
  if (!c || c !== canvas) return;    // superseded while the fetch was in flight
  // plenty of events are tracked before anything is ever listed on them. An
  // axis running $0 to $1 over an empty plot says less than one line of text.
  const live = series.some(s => s.ask.some(v => v != null) || s.qty.some(v => v));
  if (!live) {
    $$('.wrap', root).forEach(w => w.classList.add('hide'));
    $('.axisnote', root).innerHTML = `<span>${(h.stamps || []).length
      ? 'Nothing listed yet — no asks and no tickets in any reading so far.'
      : 'No readings yet.'}</span>`;
  } else {
    const sets = datasets(series, withBars ? 'both' : 'ask-only');
    // two categories is four lines and readable; five would be ten and a mess.
    // Counted over the categories that actually drew an ask line, not every one
    // the history has ever seen -- and isolating a category leaves exactly one.
    // A category with nothing listed gets no fair line either: there is no
    // resale price there for it to be the reference for
    const drawn = series.filter(s => s.ask.some(v => v != null));
    const fairs = drawn.length <= 2 ? fairSets(e, drawn, d.fair, d.labels.length) : [];
    sets.push(...fairs);
    const withFair = fairs.length > 0;
    charts.push(new Chart(free(c), {
      data: {labels: d.labels, datasets: sets},
      options: options(withBars ? 'both' : 'ask-only',
                       priceBounds(series, fairs.flatMap(f => f.data))),
      plugins: [labelPlugin()],
    }));
    $('.axisnote', root).style.display = withBars ? 'flex' : 'none';
    // there is no chart legend any more, so the dashed line says its own name
    if (withFair) $('.axisnote span:last-child', root)
      .insertAdjacentHTML('beforebegin', `<span>Dashed: ${FAIR}</span>`);
  }
  wireFavs(root);
  $$('.crow', root).forEach(row => {
    const pick = () => {
      filters[e.slug] = filters[e.slug] === row.dataset.cat ? undefined : row.dataset.cat;
      render();
    };
    row.onclick = pick;
    // a table row is not a button by default, so it has to answer the keys one
    row.onkeydown = ev => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick(); }
    };
  });
}

/* ---------------- views ---------------- */

/* The biggest recent drops, newest window first. Falls back to a longer one
   rather than showing an empty strip: on a quiet hour nothing has moved in
   two, but something usually has in a day, and the label says which.

   $10 is the floor for calling something a drop -- a dollar or two off an $80
   ticket is a seller relisting, not news, and a banner that cries wolf gets
   ignored. Nothing qualifying means no banner at all. */
const MOVER_MIN_DROP = 10;
const MOVER_WINDOWS = [['change2h', '2h'], ['change24', '24h'],
                       ['change7d', '7d']];
let moverIdx = 0, moverTimer = null;

function moverList() {
  for (const [field, label] of MOVER_WINDOWS) {
    const rows = ORDER.map(evOf)
      .filter(e => e.status === 'active' && (rowView(e)[field] ?? 0) <= -MOVER_MIN_DROP)
      .sort((a, b) => rowView(a)[field] - rowView(b)[field])
      .slice(0, 5);
    if (rows.length) return {rows, field, label};
  }
  return null;
}

function renderMovers() {
  const host = $('#movers');
  const m = moverList();
  if (!m) { host.classList.add('hide'); return; }
  host.classList.remove('hide');
  if (moverIdx >= m.rows.length) moverIdx = 0;
  const e = m.rows[moverIdx];
  const w = rowView(e), drop = w[m.field];
  const pct = w.floor != null && w.floor - drop ? drop / (w.floor - drop) : null;

  host.innerHTML = `<span class="lbl" title="Biggest price drops in the ${
      esc(m.label)} — click to open">${esc(m.label)}</span>
    <button class="mv" data-slug="${esc(e.slug)}">
      ${imgOf(e) ? `<img src="${esc(imgOf(e))}" alt="" decoding="async">` : ''}
      <span class="nm">${esc(e.name || e.slug)}</span>${pinTag(w, true)}
      <span class="amt down">${delta(drop)}${pct ? ` (${Math.round(pct * 100)}%)` : ''}</span>
      <span class="ctx">now ${money(w.floor)}${
        w.pinned ? ` for ${esc(w.pinned)}` : ''}${
        w.fair != null ? ` · fair ${money(w.fair)}` : ''}</span>
    </button>
    ${m.rows.length > 1 ? `<span class="dots">${m.rows.map((_, i) =>
      `<button data-i="${i}" aria-current="${i === moverIdx}"
        aria-label="Mover ${i + 1} of ${m.rows.length}"></button>`).join('')}</span>` : ''}`;

  $('.mv', host).onclick = () => openEvent(e.slug);
  $$('.dots button', host).forEach(b => b.onclick = () => {
    moverIdx = +b.dataset.i; scheduleMovers(); renderMovers();
  });
  host.onmouseenter = () => clearTimeout(moverTimer);
  host.onmouseleave = scheduleMovers;
  if (m.rows.length > 1) scheduleMovers();
}

function scheduleMovers() {
  clearTimeout(moverTimer);
  moverTimer = setTimeout(() => {
    const m = moverList();
    if (!m) return;
    moverIdx = (moverIdx + 1) % m.rows.length;
    renderMovers();
  }, 6000);
}

function renderStats() {
  const all = ORDER.map(evOf), act = all.filter(e => e.status === 'active');
  // the count belongs on the control that reveals them, not in the headline
  $('#retiredN').textContent = all.length - act.length
    ? `(${all.length - act.length})` : '';
  const withAsk = act.filter(e => floorOf(e) != null);
  const tickets = act.reduce((n, e) => n + ticketsOf(e), 0);
  const median = withAsk.length
    ? withAsk.map(floorOf).sort((a, b) => a - b)[Math.floor(withAsk.length / 2)] : null;
  // the timestamp reads as one more fact about the data, and folding it in
  // here is what lets the page end without a footer
  const stamp = GENERATED
    ? new Date(GENERATED).toLocaleString([], {hour: 'numeric', minute: '2-digit'})
    : null;
  $('#stats').innerHTML = [
    [act.length, 'events', 'events tracked'],
    [tickets.toLocaleString(), 'tickets', 'tickets listed'],
    [median == null ? '—' : money(median), 'median', 'median floor price'],
    ...(stamp ? [[stamp, 'last read', 'when the tracker last read prices']] : []),
  ].map(([v, k, t]) => `<span class="stat" title="${esc(t)}"><b>${v}</b> ${k}</span>`).join('')
    // emitted here rather than added once at boot: this line is rewritten on
    // every poll and every "show retired" toggle, and a link added around it
    // would disappear the first time either happened
    + `<span class="statsep">·</span><a class="statlink" href="help.html"
        title="How to read this dashboard">Help</a>`
    + `<span class="statsep">·</span><button class="statlink" id="contactBtn"
        title="Get in touch">Contact</button>`;
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

/* The same amount the grid shows, inline: the agenda is the calendar on a
   phone, so it owes the same number rather than the badge alone. */
function agendaMove(e) {
  const d = Math.round(rowView(e).change24 ?? 0);
  return d ? ` <em class="mv ${d > 0 ? 'up' : 'down'}">${
    (d > 0 ? '+' : '−') + money(Math.abs(d))}</em>` : '';
}

function renderAgenda(host) {
  const rows = visible().filter(e => eventDate(e))
    .sort((a, b) => (a.starts_ts || 0) - (b.starts_ts || 0));
  const todayK = dayKey(new Date());
  const groups = {};
  rows.forEach(e => (groups[dayKey(eventDate(e))] ||= []).push(e));
  // favourites first within their own day, as in the month grid -- the agenda
  // is the same calendar, so it owes the same ordering
  Object.values(groups).forEach(l => l.sort((a, b) => isFav(b.slug) - isFav(a.slug)));
  const body = Object.entries(groups).map(([k, list]) => {
    const d = new Date(k + 'T00:00:00');
    return `<div class="d${k === todayK ? ' today' : ''}">${
      d.toLocaleDateString([], {weekday:'short', month:'short', day:'numeric'})}</div>` +
      list.map(e => `<button class="evt${e.status === 'active' ? '' : ' done'}"
        data-slug="${esc(e.slug)}" style="border-left-color:${genreColor(genreOf(e))}">
        <span class="nm">${thumb(e, 'thumb')}${esc(e.name || e.slug)}</span>
        <span class="pr">${badgeSignal(e)}${floorOf(e) == null ? 'no asks' : money(floorOf(e))}${
          pinTag(rowView(e), true)}${agendaMove(e)} · ${
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

/* Turns the price and the amount into columns.

   Measured rather than computed: the strings are drawn with tabular figures
   but "$" and the minus sign are not digits, so counting characters would be
   a guess. This runs on a grid that has just been written and carries no
   width yet, so what it measures is each column's natural width -- freezing
   the widest is what stops the numbers stepping sideways row to row. */
function alignCalCols(host) {
  const cal = $('.cal', host);
  if (!cal) return;
  const widest = sel => Math.ceil(
    Math.max(0, ...$$(sel, cal).map(el => el.getBoundingClientRect().width)));
  const pr = widest('.evt .pr'), mv = widest('.evt .mv');
  if (pr) cal.style.setProperty('--prw', pr + 'px');
  if (mv) cal.style.setProperty('--mvw', mv + 'px');
}

function renderCalendar() {
  const host = $('#calendar');
  // the month buttons live in the static control bar, so they outlive a failed
  // boot -- which replaces <main> wholesale with the "could not load" message
  if (!host) return;
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
  // one decision for the whole grid rather than per row: a column only exists
  // if something in view puts something in it
  const reserve = Object.values(buckets).flat().some(e => {
    const sg = signalOf(e); return sg && sg.badge;
  });
  let cells = '';
  for (let i = 0; i < weeks * 7; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    // favourites first within the day; pinning across days would fight the
    // grid, which exists to put events on their date
    const k = dayKey(d), list = (buckets[k] || []).sort((a, b) =>
      (isFav(b.slug) - isFav(a.slug)) || (floorOf(a) ?? 1e9) - (floorOf(b) ?? 1e9));
    cells += `<div class="day${d.getMonth() === m ? '' : ' out'}${k === today ? ' today' : ''}">
      <div class="n">${d.getDate()}</div>
      ${list.map(e => {
        // one rowView for the whole cell: the tag, the title and the price all
        // have to be talking about the same ticket
        const w = rowView(e), tag = pinTag(w, true);
        return `<button class="evt${e.status === 'active' ? '' : ' done'}${tag ? ' pinned' : ''}"
          data-slug="${esc(e.slug)}" style="border-left-color:${genreColor(genreOf(e))}"
          title="${esc([e.name, e.venue, genreOf(e)].filter(Boolean).join(' — ') + ' — '
            + (w.floor == null ? 'no asks'
               : money(w.floor) + (w.pinned ? ` for ${w.pinned}` : ' floor'))
            + ', ' + ticketsOf(e) + ' tickets')}">
          ${thumb(e, 'thumb')}<span class="nm">${
            isFav(e.slug) && !favCat(e.slug) ? '<i class="fdot"></i>' : ''}${
            esc(e.name || e.slug)}</span>${tag ? `<span class="ln2">${tag}` : ''
          }<span class="pr">${w.floor == null ? '—' : money(w.floor)}</span>${move24(e, reserve)}${
            tag ? '</span>' : ''}
        </button>`;
      }).join('')}
    </div>`;
  }
  const dows = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(x => `<div class="dow">${x}</div>`).join('');
  // just the month: the count restated what the grid already shows, while the
  // month is the only thing saying where the ‹ › arrows have taken you. The
  // agenda's note keeps its count -- a continuous list has neither heading
  // nor a visible extent to read it off.
  $('#calnote').textContent = first.toLocaleString([], {month:'long', year:'numeric'});
  host.innerHTML = genreBar() + `<div class="cal" style="--calrows:${weeks}">${dows}${cells}</div>`;
  alignCalCols(host);
  wireGenreBar(host);
  $$('.evt', host).forEach(b => b.onclick = () => openEvent(b.dataset.slug));
  fitCalendar();
}

/* Each column says how to sort by it and how to draw its cell, so the Deals
   view is a different column list over the same rows rather than a second copy
   of the markup. */
const cellDate = e => {
  const d = eventDate(e);
  return `<td class="l">${d ? d.toLocaleDateString([], {month:'short', day:'numeric'}) : '—'}</td>`;
};
const cellIn = e => {
  const d = eventDate(e), days = d ? Math.round((d - startOfToday()) / 86400000) : null;
  return `<td class="l">${days == null ? '' : days < 0 ? 'past'
    : days === 0 ? 'today' : days + 'd'}</td>`;
};
// the note itself rides in the tooltip: a marker that only says "there is a
// note" makes you open the event to find out what you told yourself
const noteMark = e => noteOf(e.slug)
  ? `<i class="nmark" title="${esc(noteOf(e.slug))}">✎</i>` : '';
const cellName = e => `<td class="l ev" title="${esc(e.name || e.slug)}">${favBtn(e.slug)}${
    thumb(e, 'lthumb')}${esc(e.name || e.slug)}${signal(e)}${pinTag(rowView(e))}${
    noteMark(e)}</td>`;
const cellVsSale = e => {
  const vs = vsSale(e), c = e.current || {}, w = rowView(e);
  // a dash where a sale exists but cannot be attributed is not "no data", and
  // saying which of the two it is is the whole point of withholding the number
  const why = vs
    ? `${w.pinned || 'the floor'} is ${money(Math.abs(vs))} ${
        vs > 0 ? 'above' : 'below'} the last sale`
    : c.last_sale != null && !saleKnown(e) ? SALE_VAGUE : '';
  return `<td class="${!vs ? '' : vs > 0 ? 'up' : 'down'}"${
    why ? ` title="${esc(why)}"` : ''}>${vs ? delta(vs) : '—'}</td>`;
};
/* The trade itself is real and worth knowing, so it stays -- but on an event
   with more than one category it is marked as the event's rather than the
   row's, so nobody reads it as the pinned category's own last price. */
const cellSale = e => {
  const c = e.current || {};
  if (c.last_sale == null) return '<td>—</td>';
  return saleKnown(e) ? `<td>${money(c.last_sale)}</td>`
    : `<td class="ew" title="${esc(SALE_VAGUE)}">${money(c.last_sale)}<i>event</i></td>`;
};
const cellFair = (e, plain) => {
  const pr = primaryOf(e), gone = pr != null && primaryGone(e);
  return `<td${gone ? ` class="past" title="${esc(PAST_NOTE(pr))}"` : ''}>${
    pr == null ? '—' : money(pr)}${
    gone && plain ? '<i class="note">primary sold out</i>' : ''}</td>`;
};
const cellVsFair = e => {
  const vp = vsPrimary(e), gone = primaryGone(e);
  return `<td class="${!vp ? '' : vp > 0 ? 'up' : 'down'}"${
    vp ? ` title="${esc(vsPrimaryTip(vp, gone))}"` : ''}>${vp ? delta(vp) : '—'}</td>`;
};
/* The same saving as a share of the price. Twenty dollars off a $40 ticket and
   off a $400 one are not the same news, and only this column says so. */
const cellVsFairPct = e => {
  const v = vsPrimaryPct(e);
  return `<td class="${!v ? '' : v > 0 ? 'up' : 'down'}">${
    v == null ? '—' : (v > 0 ? '+' : '−') + pctOf(v)}</td>`;
};
const cellChange = k => e => {
  const v = rowView(e)[k];
  return `<td class="${!v || !Math.round(v) ? '' : v > 0 ? 'up' : 'down'}">${delta(v)}</td>`;
};
const plainTd = (cls, txt) => `<td class="${cls}">${txt}</td>`;

// Grouped so the table answers one question at a time. The old flat order
// interleaved three unrelated kinds of delta -- against the last sale, against
// face value, and against the past -- so a reader had to remember which "vs"
// column meant what. Each column now names its group, and the header spans it.
const COLUMNS = [
  ['date',    'Date',    'l', e => e.starts_ts ?? Infinity, cellDate, 'When'],
  ['in',      'In',      'l', e => e.starts_ts ?? Infinity, cellIn, 'When'],
  ['name',    'Event',   'l', e => (e.name || e.slug).toLowerCase(), cellName, 'Event'],
  ['venue',   'Venue',   'l', e => (e.venue || '').toLowerCase(),
    e => `<td class="l vn" title="${esc(e.venue || '')}">${esc(e.venue || '')}</td>`, 'Event'],
  ['city',    'City',    'l', e => (e.city || '').toLowerCase(),
    e => plainTd('l', esc(e.city || '')), 'Event'],
  ['genre',   'Genre',   'l', e => genreLabel(e).toLowerCase(),
    e => plainTd('l', `<i class="gdot" style="background:${
      genreColor(genreOf(e))}"></i>${esc(genreLabel(e))}`), 'Event'],
  // the anchor: what it costs right now, sat immediately left of the two
  // things worth measuring it against
  ['floor',   'Floor',   '',  e => floorOf(e) ?? Infinity,
    e => `<td>${floorOf(e) == null ? '—' : money(floorOf(e))}</td>`, 'Asking'],
  ['sale',    'Last sale', '', e => e.current?.last_sale ?? -1, cellSale,
    'vs what it is worth'],
  // nothing to compare sorts to the end rather than reading as a big discount
  ['vsale',   'Δ',       '', e => vsSale(e) ?? Infinity, cellVsSale, 'vs what it is worth'],
  // the same two questions asked of the primary platform instead of the last
  // resale: what this ticket is worth new, and whether the resale beats it
  ['prim',    'Fair value', '', e => primaryOf(e) ?? Infinity,
    e => cellFair(e, false), 'vs what it is worth'],
  ['vsprim',  'Δ',       '', e => vsPrimary(e) ?? Infinity, cellVsFair, 'vs what it is worth'],
  // ascending, so the eye reads left to right through time
  ['change4', '4h',      '',  e => rowView(e).change4h ?? 0, cellChange('change4h'), 'Moved'],
  ['change',  '24h',     '',  e => rowView(e).change24 ?? 0, cellChange('change24'), 'Moved'],
  ['change3', '3d',      '',  e => rowView(e).change3d ?? 0, cellChange('change3d'), 'Moved'],
  ['change7', '7d',      '',  e => rowView(e).change7d ?? 0, cellChange('change7d'), 'Moved'],
  ['tickets', 'Tickets', '',  e => ticketsOf(e), e => `<td>${ticketsOf(e)}</td>`, 'Supply'],
  ['cats',    'Cats',    '',  e => (e.current?.cats || []).length,
    e => `<td>${(e.current?.cats || []).length}</td>`, 'Supply'],
];

/* The Deals view carries the List's columns so a row still reads the same and
   still opens the same modal, plus the percentage it is sorted by, and it says
   in words when a fair value belongs to a primary that has sold out. */
const DEAL_COLUMNS = COLUMNS.flatMap(c =>
  c[0] === 'prim' ? [[...c.slice(0, 4), e => cellFair(e, true), c[5]]] :
  c[0] === 'vsprim' ? [c, ['vsfpct', 'Δ%', '', e => vsPrimaryPct(e) ?? Infinity,
                           cellVsFairPct, 'vs what it is worth']] : [c]);

function sortRows(rows, cols, sort) {
  const get = Object.fromEntries(cols.map(c => [c[0], c[3]]));
  return rows.slice().sort((a, b) => {
    const x = get[sort.key](a), y = get[sort.key](b);
    return (x > y ? 1 : x < y ? -1 : 0) * sort.dir;
  });
}

function tableFor(cols, rows, sort) {
  // where each group starts, so both header rows and every body row can carry
  // the same dividing rule
  const starts = new Set(cols.map((c, i) => c[5] !== (cols[i - 1] || [])[5] ? i : -1));
  const edge = i => starts.has(i) && i > 0 ? ' ge' : '';

  const body = rows.map(e => `<tr data-slug="${esc(e.slug)}">${
    cols.map((c, i) => {
      const td = c[4](e);
      return edge(i) ? td.replace(/^<td/, '<td class="ge"').replace(
        /^<td class="ge" class="([^"]*)"/, '<td class="ge $1"') : td;
    }).join('')}<td class="l">${e.status === 'active' ? ''
      : `<span class="badge">${esc(e.status)}</span>`}</td></tr>`).join('');

  const groups = [];
  cols.forEach((c, i) => {
    if (starts.has(i)) groups.push([c[5], 1]);
    else groups[groups.length - 1][1]++;
  });

  return `<table><thead>
    <tr class="grp">${groups.map(([label, span], gi) =>
      `<th colspan="${span}" class="${gi ? 'ge' : ''}">${esc(label)}</th>`).join('')
      }<th></th></tr>
    <tr>${cols.map(([k, t, cls], i) =>
      `<th data-sort="${k}" class="${cls}${edge(i)}"${sort.key === k
        ? ` aria-sort="${sort.dir > 0 ? 'ascending' : 'descending'}"` : ''}>${t}</th>`).join('')
      }<th class="l"></th></tr></thead><tbody>${body}</tbody></table>`;
}

function wireTable(host, sort, redraw) {
  wireGenreBar(host);
  $$('th[data-sort]', host).forEach(th => th.onclick = () => {
    const k = th.dataset.sort;
    sort.dir = sort.key === k ? -sort.dir : 1;
    sort.key = k;
    redraw();
  });
  $$('tbody tr', host).forEach(tr => tr.onclick = () => openEvent(tr.dataset.slug));
  wireFavs(host);
}

function renderList() {
  const rows = sortRows(visible(), COLUMNS, listSort);
  $('#list').innerHTML = genreBar() + (rows.length
    ? tableFor(COLUMNS, rows, listSort) : '<p class="empty">Nothing matches.</p>');
  wireTable($('#list'), listSort, renderList);
}

/* Everything currently asking less than it would cost to buy new, dearest
   discount first. A view rather than a filter on the List: it wants its own
   default sort, a column the List has no room for, and a sentence of its own
   about what the comparison is worth -- none of which belong in the List's
   toolbar, and none of which disturb the List by being here. */
function renderDeals() {
  const host = $('#deals');
  const deals = visible().filter(e => (vsPrimaryPct(e) ?? 0) < 0);
  const rows = sortRows(deals, DEAL_COLUMNS, dealSort);
  const priced = ORDER.map(evOf).filter(e => primaryOf(e) != null).length;
  const gone = rows.filter(primaryGone).length;
  const note = !rows.length
    ? `Nothing is asking below its fair value right now, out of the ${priced} event${
        priced === 1 ? '' : 's'} we can price at all.`
    : `${rows.length} of the ${priced} events we can price ${rows.length === 1 ? 'is' : 'are'
      } asking below fair value, biggest saving first.`
      + (gone ? ` ${gone} of ${rows.length === 1 ? 'them' : 'those'} compare${
          gone === 1 ? 's' : ''} against a primary that has already sold out — nobody can pay`
          + ' that price any more, so the saving is against history rather than against a'
          + ' ticket you could still buy instead.' : '');
  host.innerHTML = genreBar() + `<p class="dealnote">${note}</p>`
    + (rows.length ? tableFor(DEAL_COLUMNS, rows, dealSort) : '');
  wireTable(host, dealSort, renderDeals);
}

function renderCharts() {
  const host = $('#charts');
  host.innerHTML = '';
  const list = visible().sort((a, b) => isFav(b.slug) - isFav(a.slug));
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

/* Buttons carrying the attribute wireSegments() binds by, so a set built here
   stays in step with the one in the header without being told about it. */
function segHTML(attr, opts, cur) {
  return '<span class="seg" role="group">' + opts.map(([v, t]) =>
    `<button ${attr}="${v}" aria-pressed="${String(cur) === String(v)}">${t}</button>`
  ).join('') + '</span>';
}

function chartToolbar() {
  return `<div class="modalctl">
    ${segHTML('data-range', [['24','24h'],['168','7d'],['0','All']], range)}
    ${segHTML('data-view', [['ask','Price only'],['both','+ ticket supply']], view)}
  </div>`;
}

const BELL = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/>
  <path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>`;

/* The count is how many alerts are met *right now*, not how many have ever
   fired. For an hourly price feed that is the more useful question -- a target
   that was met at 3am and is not any more is not something to go and act on --
   and it means the badge needs no read/unread state to get out of step. */
function renderBell() {
  const n = alertsHit().length, b = $('#bell');
  b.innerHTML = BELL + (n ? `<i class="dot">${n}</i>` : '');
  b.classList.toggle('lit', n > 0);
  const moved = movedSince().length;
  b.title = [n ? `${n} price alert${n === 1 ? '' : 's'} met` : 'No alerts met',
             moved ? `${moved} event${moved === 1 ? '' : 's'} moved since your last visit` : '']
    .filter(Boolean).join(' · ');
}

const ALERT_WHY = {
  waiting: r => `now ${money(r.ask)}`,
  hit: r => `now ${money(r.ask)}${r.qty ? ` · ${r.qty} left` : ''}`,
  noask: () => 'nothing listed right now',
  unlisted: () => 'this category is no longer listed',
  retired: () => 'no longer tracked',
  gone: () => 'no longer tracked',
};

function trayRow(cls, slug, left, right) {
  return `<button class="trow ${cls}" data-open="${esc(slug)}">
    <span class="tl">${left}</span><span class="tr">${right}</span></button>`;
}

function renderTray() {
  const rows = alertRows(), hit = rows.filter(r => r.state === 'hit');
  const rest = rows.filter(r => r.state !== 'hit');
  const moved = movedSince(), shown = moved.slice(0, 15);

  const alertList = !rows.length
    ? `<p class="note">No alerts yet. Open an event and click the
       <span class="abtn on">≤&thinsp;$</span> beside a ticket category to be told
       when it reaches a price you name.</p>`
    : (hit.length
        ? `<p class="sub tsec">Met now</p>` + hit.map(r => trayRow('hit', r.slug,
            `<b>${esc(r.e.name || r.slug)}</b><span class="c">${esc(r.cat)}</span>`,
            `<b class="down">${money(r.ask)}</b><span class="c">your target ${
              money(r.target)}</span>`)).join('')
        : '')
      + (rest.length
        ? `<p class="sub tsec">Watching</p>` + rest.map(r => trayRow(
            r.state === 'waiting' ? '' : 'dim', r.slug,
            `<b>${esc(r.e ? (r.e.name || r.slug) : r.slug)}</b><span class="c">${
              esc(r.cat)}</span>`,
            `<span>≤&thinsp;${money(r.target)}</span><span class="c">${
              esc(ALERT_WHY[r.state](r))}</span>`)).join('')
        : '');

  const movedList = !SEEN_BASE
    ? `<p class="note">Nothing to compare yet — come back after a few hours and
       this will say what moved while you were away.</p>`
    : !moved.length
    ? `<p class="note">Nothing worth reporting has moved since your last visit.</p>`
    : shown.map(m => trayRow('', m.slug,
        `<b>${esc(m.e.name || m.slug)}</b>${m.pinned
          ? `<span class="c">${esc(m.pinned)}</span>` : ''}`,
        `<b class="${m.d > 0 ? 'up' : 'down'}">${delta(m.d)}</b><span class="c">${
          money(m.was)} → ${money(m.now)}</span>`)).join('')
      + (moved.length > shown.length
        ? `<p class="note">…and ${moved.length - shown.length} more.</p>` : '');

  $('#trayBody').innerHTML = `
    <h3>Price alerts</h3>${alertList}
    <h3 class="second">Since your last visit</h3>${movedList}
    <div class="btnrow"><button class="ghost" id="trayClose">Close</button></div>`;

  $('#trayClose').onclick = () => $('#tray').close();
  $$('#trayBody [data-open]').forEach(b => b.onclick = () => {
    const slug = b.dataset.open;
    if (!INDEX[slug]) return;
    $('#tray').close();
    openEvent(slug);
  });
}

const BUBBLE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4
  a8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.1A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z"/></svg>`;

/* A dialog rather than a sixth tab. render() rebuilds whichever section is
   showing on every theme change, favourite and poll, so a thread living in
   one would be torn down constantly; a dialog is outside its reach. */
function renderChat() {
  // Close rides in the composer's own row rather than a second right-aligned
  // row under it, which read as two stacked buttons
  $('#chatBody').innerHTML = `
    <h3>Chat</h3>
    ${chatPanel('general', {open: true,
                            extra: '<button class="ghost" id="chatClose">Close</button>'})}`;
  $('#chatClose').onclick = () => $('#chat').close();
  wireChat($('#chatBody'), 'general');
}

function renderChatBtn() {
  const b = $('#chatBtn');
  if (!b) return;
  b.classList.toggle('hide', !CHAT_BASE);
  const doc = CHAT['general'];
  let seen = 0;
  try { seen = +(localStorage.getItem(SEEN_CHAT_KEY) || 0); } catch (e) {}
  const fresh = ((doc && doc.msgs) || []).filter(m => m.t > seen).length;
  b.innerHTML = BUBBLE + (fresh ? `<i class="dot">${fresh > 9 ? '9+' : fresh}</i>` : '');
  b.classList.toggle('lit', fresh > 0);
  b.title = fresh ? `${fresh} new message${fresh === 1 ? '' : 's'}` : 'Chat';
}

function markChatSeen() {
  const doc = CHAT['general'], msgs = (doc && doc.msgs) || [];
  if (!msgs.length) return;
  try { localStorage.setItem(SEEN_CHAT_KEY, String(msgs[msgs.length - 1].t)); }
  catch (e) {}
  renderChatBtn();
}

/* Everything the page remembers about you, in one place. Read it as the
   answer to "what does this thing know" -- which is: three preferences, all
   of them in this browser's own storage. */
function renderSettings() {
  const d = device(), n = Object.keys(FAVS).length;
  const pinned = Object.values(FAVS).filter(Boolean).length;
  const favText = !n ? 'none yet'
    : `${n} event${n === 1 ? '' : 's'}`
      + (pinned ? `, ${pinned} pinned to a category` : '');
  $('#settingsBody').innerHTML = `
    <h3>Your settings</h3>
    <div class="who">
      <span class="av" style="background:${d.hue}26;color:${d.hue};
            border:1px solid ${d.hue}59">${esc(d.mono)}</span>
      <div><b>${esc(d.name)}</b>
        <span class="sub">this browser's name for itself</span></div>
    </div>
    <p class="note">Everything below lives in this browser only. There is no
      account: open the tracker on your phone or in another browser and it
      starts fresh, under a different name, with none of this carried over.</p>
    <div class="prefs">
      <div class="prefrow"><span class="k">Theme</span>
        ${segHTML('data-theme-pref',
                  [['','System'],['light','Light'],['dark','Dark']], themePref())}</div>
      <div class="prefrow"><span class="k">Charts open on</span>
        ${segHTML('data-view',
                  [['ask','Price only'],['both','+ supply']], view)}</div>
      <div class="prefrow"><span class="k">Favourites</span>
        <span class="v">${n ? `<button class="linkish" id="goFavs">${esc(favText)}</button>`
                            : esc(favText)}</span></div>
      <div class="prefrow"><span class="k">Price alerts</span>
        <span class="v">${alertCount()
          ? `<button class="linkish" id="goAlerts">${alertCount()} set</button>`
          : 'none set'}</span></div>
      ${CHAT_BASE ? `<div class="prefrow"><span class="k">Moderation</span>
        <span class="v"><input type="password" id="modKey" placeholder="admin token"
          value="${esc(modToken())}" autocomplete="off"><i id="modOk"></i></span></div>` : ''}
      <div class="prefrow"><span class="k">Notes</span>
        <span class="v">${Object.keys(NOTES).length
          ? `on ${Object.keys(NOTES).length} event${
              Object.keys(NOTES).length === 1 ? '' : 's'}`
          : 'none written'}</span></div>
    </div>
    <div class="btnrow"><button class="ghost" id="settingsClose">Close</button></div>`;
  wireSegments();
  $('#settingsClose').onclick = () => $('#settings').close();
  const go = $('#goFavs');
  if (go) go.onclick = () => {
    $('#settings').close();
    setTab('favs'); render();
  };
  const ga = $('#goAlerts');
  if (ga) ga.onclick = () => {
    $('#settings').close();
    renderTray(); $('#tray').showModal();
  };
  const mk = $('#modKey');
  if (mk) {
    mk.onkeydown = ev => ev.stopPropagation();
    mk.onchange = async () => {
      const v = mk.value.trim();
      try { v ? localStorage.setItem(MOD_KEY, v) : localStorage.removeItem(MOD_KEY); }
      catch (e) {}
      // checked the moment it is entered, rather than leaving you to find
      // out from a delete that quietly does nothing
      const ok = $('#modOk');
      if (!v) { ok.textContent = ''; return; }
      try {
        const r = await fetch('/api/chat?op=auth',
                              {headers: {'authorization': 'Bearer ' + v}});
        ok.textContent = r.ok ? 'verified' : 'not recognised';
        ok.className = r.ok ? 'good' : 'bad';
      } catch (e) { ok.textContent = 'could not check'; ok.className = 'bad'; }
    };
  }
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

function renderTabs() {
  const n = Object.keys(FAVS).length;
  const b = $('[data-tab="favs"]');
  b.classList.toggle('hide', n === 0);
  b.textContent = `Favourites ${n}`;
  if (!n && tab === 'favs') { tab = 'calendar'; setTab('calendar'); }
}

function setTab(t) {
  tab = t;
  $$('[data-tab]').forEach(x => x.setAttribute('aria-pressed', x.dataset.tab === t));
}

function renderFavs() {
  const host = $('#favs');
  const rows = sortRows(visible().filter(e => isFav(e.slug)), COLUMNS, listSort);
  host.innerHTML = rows.length
    ? `<p class="sub note">${rows.length} favourite${rows.length === 1 ? '' : 's'}.
       A star on a ticket category pins that category, so every view shows it
       instead of the event's cheapest.</p>` + tableFor(COLUMNS, rows, listSort)
    : '<p class="empty">Nothing matches your filters.</p>';
  wireTable(host, listSort, renderFavs);
}

function render() {
  charts.splice(0).forEach(c => c.destroy());
  document.body.classList.toggle('supply', view === 'both');
  document.body.classList.toggle('tab-calendar', tab === 'calendar');
  document.body.classList.toggle('tab-list', tab === 'list' || tab === 'deals');
  ['favs','calendar','list','deals','charts'].forEach(t => {
    const el = $('#' + t); if (el) el.classList.toggle('hide', t !== tab);
  });
  $$('.chartctl').forEach(el => el.classList.toggle('hide', tab !== 'charts'));
  $$('.calctl').forEach(el => el.classList.toggle('hide', tab !== 'calendar'));
  // the agenda is one continuous upcoming list, so a month stepper means
  // nothing there -- keep the note, drop the arrows
  $('#monthNav').classList.toggle('hide', tab !== 'calendar' || isPhone());
  if (tab === 'favs') renderFavs();
  if (tab === 'calendar') renderCalendar();
  if (tab === 'list') renderList();
  if (tab === 'deals') renderDeals();
  if (tab === 'charts') renderCharts();
  if ($('#modal').open && openSlug) openEvent(openSlug);
}

const SEGMENTS = {
  'data-tab':   v => tab = v,
  'data-range': v => range = +v,
  'data-view':  v => {
    view = v;
    try { localStorage.setItem(VIEW_KEY, v); } catch (e) {}
  },
  'data-theme-pref': v => applyTheme(v),
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
  // the header's buttons are static markup, so a remembered view has to be
  // pressed into them once at boot; the modal builds its own from `view`
  $$('[data-view]').forEach(b =>
    b.setAttribute('aria-pressed', b.getAttribute('data-view') === view));
}
wireSegments();
$('#prevM').onclick = () => { month.setMonth(month.getMonth() - 1); renderCalendar(); };
$('#nextM').onclick = () => { month.setMonth(month.getMonth() + 1); renderCalendar(); };
$('#thisM').onclick = () => { month = new Date(); month.setDate(1); renderCalendar(); };
$('#showRetired').onchange = e => { showRetired = e.target.checked; renderStats(); render(); };
let qt;
const searchBox = $('#search');

function openSearch(focus = true) {
  searchBox.classList.add('open');
  $('#qbtn').setAttribute('aria-expanded', 'true');
  $('#q').tabIndex = 0;
  if (focus) $('#q').focus();
}
function closeSearch() {
  if (query.trim()) return;            // never hide an active filter
  searchBox.classList.remove('open');
  $('#qbtn').setAttribute('aria-expanded', 'false');
  $('#q').tabIndex = -1;
}

$('#qbtn').onclick = () => {
  searchBox.classList.contains('open') ? ($('#q').value ? null : closeSearch()) : openSearch();
};
$('#q').oninput = e => {
  query = e.target.value;
  searchBox.classList.toggle('on', !!query.trim());
  clearTimeout(qt);
  qt = setTimeout(render, 150);
};
$('#q').onblur = () => closeSearch();
$('#q').onkeydown = e => {
  if (e.key === 'Escape') { e.target.value = ''; query = ''; searchBox.classList.remove('on'); render(); closeSearch(); }
};
/* Shows what you will get, not what you have: a sun in dark mode means
   "switch to light". */
const SUN = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4
  M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>`;
const MOON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>`;

function paintTheme() {
  const b = $('#theme');
  b.innerHTML = isDark() ? SUN : MOON;
  b.title = isDark() ? 'Switch to light' : 'Switch to dark';
  paintProfile();
  if ($('#settings').open) renderSettings();
}

/* The icon is a two-state switch -- whatever you are looking at, the other
   one. Choosing "follow the system" is a third state, so it lives in the
   settings panel rather than in a one-button cycle nobody can predict. */
const THEME_KEY = 'cv.theme.v1';

function applyTheme(t) {
  document.documentElement.dataset.theme = t || '';
  try {
    if (t) localStorage.setItem(THEME_KEY, t);
    else localStorage.removeItem(THEME_KEY);   // absent = follow the system
  } catch (e) {}
  paintTheme();
}
// wireSegments() renders for itself, so the panel's control uses applyTheme
// directly and only the icon needs this
function setTheme(t) { applyTheme(t); render(); }
const themePref = () => {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === 'light' || t === 'dark' ? t : '';
  } catch (e) { return ''; }
};

$('#theme').onclick = () => setTheme(isDark() ? 'light' : 'dark');

function paintProfile() {
  const d = device(), b = $('#profile');
  b.textContent = d.mono;
  b.title = `Your settings · ${d.name}`;
  b.style.background = d.hue + '26';
  b.style.color = d.hue;
  b.style.border = `1px solid ${d.hue}59`;
}
$('#chatBtn').onclick = () => { renderChat(); $('#chat').showModal(); };
$('#chat').addEventListener('click', e => {
  if (e.target === e.currentTarget) e.currentTarget.close();
});
$('#chat').addEventListener('close', markChatSeen);
$('#bell').onclick = () => { renderTray(); $('#tray').showModal(); };
$('#tray').addEventListener('click', e => {
  if (e.target === e.currentTarget) e.currentTarget.close();
});
$('#profile').onclick = () => { renderSettings(); $('#settings').showModal(); };
$('#settings').addEventListener('click', e => {
  if (e.target === e.currentTarget) e.currentTarget.close();
});
paintProfile();
paintTheme();
$('#modalClose').onclick = () => $('#modal').close();
/* the stats line is rewritten on every poll, so the Contact button in it is a
   different element each time -- delegate rather than bind to one that is
   about to be replaced */
document.addEventListener('click', e => {
  if (e.target.closest('#contactBtn')) $('#contact').showModal();
});
$('#contactClose').onclick = () => $('#contact').close();
$('#contact').addEventListener('click', e => {
  if (e.target === e.currentTarget) e.currentTarget.close();
});
$('#copyMail').onclick = async e => {
  const btn = e.currentTarget, mail = $('#mailAddr').textContent;
  try {
    await navigator.clipboard.writeText(mail);
  } catch {
    // clipboard access is refused outside a secure context, and over plain
    // http this page is one. Select the address so a manual copy is one
    // keystroke rather than a drag across a nine-character string
    const r = document.createRange();
    r.selectNodeContents($('#mailAddr'));
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
    btn.textContent = 'Select all'; return;
  }
  btn.textContent = 'Copied'; btn.classList.add('done');
  setTimeout(() => { btn.textContent = 'Copy address'; btn.classList.remove('done'); }, 1600);
};
// clicking the backdrop lands on the dialog element itself -- the only way out
// on a phone, where there is no Escape key
$('#modal').addEventListener('click', e => { if (e.target === e.currentTarget) e.currentTarget.close(); });
$('#modal').addEventListener('close', () => { openSlug = null; render(); });
/* Prices are read hourly, so re-read them here more often than that and the
   page is never far behind. A soft refresh rather than location.reload():
   whatever tab, filter, search or open event you were looking at survives it.
   Skipped while a tab is hidden, and while the modal is open -- pulling the
   chart out from under someone reading it is worse than being 20 minutes
   stale. */
const REFRESH_MS = 20 * 60 * 1000;

async function refresh() {
  if (document.hidden || $('#modal').open) return;
  try {
    const [idx, recent] = await Promise.all([
      fetch(DATA_BASE + 'index.json', {cache: 'no-cache'}).then(r => r.json()),
      fetch(DATA_BASE + 'recent.json', {cache: 'no-cache'})
        .then(r => r.ok ? r.json() : {}).catch(() => ({})),
    ]);
    if (!idx.events || idx.generated_at === GENERATED) return;
    INDEX = idx.events;
    RECENT = recent || {};
    GENERATED = idx.generated_at;
    ORDER = Object.keys(INDEX);
    Object.keys(HIST).forEach(k => delete HIST[k]);   // histories moved on too
    buildGenres();
    renderStats();
    renderMovers();
    // a target crossed while the tab sits open should light the bell without
    // a reload. The visit baseline is deliberately not touched: a poll is the
    // same visit continuing, not a new one
    renderBell();
    render();
  } catch (e) { /* a failed poll is not worth breaking the page over */ }
}

setInterval(refresh, REFRESH_MS);
// chat moves on a different timescale from prices, and unlike refresh() it
// has to keep running while the modal is open
const CHAT_MS = 15000;
setInterval(chatPoll, CHAT_MS);
setInterval(() => { if (!document.hidden && !$('#chat').open)
                      chatFetch('general').then(renderChatBtn); }, 60000);
// catch up immediately on returning to the tab rather than waiting out the timer
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });

matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
matchMedia('(max-width: 820px)').addEventListener('change', render);
addEventListener('resize', () => { if (tab === 'calendar') fitCalendar(); });
addEventListener('keydown', e => { if (e.key === 'Escape' && $('#modal').open) $('#modal').close(); });
boot();
</script>
</body>
</html>
"""


def build_dashboard(_store, out_path):
    """Writes the static page. It carries no data -- it fetches it at runtime."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    html = (HTML
            .replace("__CHART_JS__", "vendor/chart.umd.min.js" if VENDOR.exists() else CDN)
            .replace("__CONTACT_EMAIL__", CONTACT_EMAIL)
            .replace("__DATA_BASE__", data_base())
            .replace("__CHAT_BASE__", chat_base())
            # public by design; absent means the page renders chat read-only
            # rather than a composer that fails on submit
            .replace("__TURNSTILE_KEY__", os.environ.get("TURNSTILE_SITE_KEY", ""))
            .replace("__D_ADJ__", str(chatstore.ADJ).replace("'", '"'))
            .replace("__D_NOUN__", str(chatstore.NOUN).replace("'", '"'))
            .replace("__PALETTE__", ",".join(SERIES_LIGHT[:5]))
            .replace("__SERIES_LIGHT__", str(SERIES_LIGHT).replace("'", '"'))
            .replace("__SERIES_DARK__", str(SERIES_DARK).replace("'", '"')))
    out.write_text(html, encoding="utf-8")
    return out
