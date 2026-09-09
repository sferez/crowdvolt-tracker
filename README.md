# CrowdVolt NYC Tracker

Hourly best-ask and ticket-availability tracking for every CrowdVolt event in
New York City, with a static dashboard. No account, no bearer token, no API
key, no database server.

```
 crowdvolt.com      discover.py (daily, NYC sweep)     Vercel Blob        Vercel
   (SSR page)  ──>  track.py   (hourly, tiered)   ──>  index.json    ──>  static page
                                                       events/*.json      calendar · list · charts
```

The site is static and never redeploys. An hourly reading replaces two or three
JSON objects in Blob; the page picks them up on the next load.

---

## 1. Where the data comes from

CrowdVolt's event pages are server-rendered, and the React Server Component
payload carries the whole order book. One plain `GET` of the event URL gives:

| Path in the payload | What it holds |
|---|---|
| `event.tt_data.types[]` | per ticket category: `lowest_ask_price`, `all_in_lowest_ask_price`, `lowest_ask_qty`, `highest_bid_price`, `highest_bid_qty` |
| `initialBook.sell[]` | every ask: `price`, `all_in_price`, `qty`, `ticket_type` |
| `initialBook.buy[]` | every bid, same shape |
| `last_sale`, `high_price_all_in`, `looking_to_go`, `looking_to_sell` | the event-wide numbers, kept as their own time series |
| `square_img_link`, `performer_bubbles`, `app_name`, `venue_obj`, `ticket_limit` | artwork, line-up, source platform, address — read once into metadata |
| `discover_more_events` | related events at the same venue: free leads for discovery |

Genre is not on the event page. It lives on `/performer/<slug>`, so `discover.py`
reads each artist once (`artists.json`) and hangs the genres on every event they
play — one read per artist, ever, rather than one per event. Artist pages also
carry a bio, Spotify link and monthly listeners.

Event artwork is mirrored into Blob on first sight (`img/<slug>.jpg`, a 400px
imgix thumbnail). Not for CORS — an `<img>` renders cross-origin fine — but so
the dashboard does not hotlink someone else's CDN, does not break when a source
URL rotates, and does not make every viewer's browser call crowdvolt.com.

`crowdvolt.py` asks for the page with an `RSC: 1` header, which returns just
that payload (~115 KB instead of ~380 KB of HTML), then bracket-matches the
event object out of it. Tickets available per category is the sum of `qty` over
`initialBook.sell` for that category — it reconciles exactly with the event's
own `tickets_remaining` / `looking_to_sell`.

`event_uqid` is simply the URL slug.

### The `what.crowdvolt.com` API, and its bearer token

The XHR endpoints the site's own client uses — `/api/event/details`,
`/api/book/get` — need a bearer token, and that token is minted behind
Cloudflare Turnstile: the page loads `challenges.cloudflare.com` and keeps a
`turnstile_token` and a `fingerprint_id` in `localStorage`, which feed
`/api/user/bootstrap` and `/api/auth/refresh`. The host itself also sits behind
a Cloudflare managed challenge — it answers a scripted request with 403 or 503
whatever the token says.

Minting and refreshing that token from a script means automating bot detection,
so this project does not go near it. It also does not need to: the SSR page
returns the same numbers unauthenticated, in one request, and there is no token
to expire, rotate, or get invalidated at 3am.

---

## 2. Read this before running it wide

`crowdvolt.com/robots.txt` allows a named list of search and AI crawlers and
ends with `User-agent: * / Disallow: /`. A personal price watcher is not on that
allowlist. This tracker is deliberately polite — an honest `User-Agent`, one
request at a time, ~1.5 s between events, and the CDN caches each page for 30 s
anyway — but it is not blessed. If you want to scale it up or share it, ask
CrowdVolt for API access first.

Nothing here retries behind a new identity, rotates anything, or works around a
block. A failing event is logged and skipped; twelve failures in a row and it is
dropped.

### Where to run it

| | Verdict |
|---|---|
| **Your Mac** (a `cron` or `launchd` entry calling `track.py`) | No third-party terms in play. Misses readings while asleep, which is only a gap in the chart. |
| **GitHub Actions** (workflows included, schedules live once secrets are set) | Works, and scheduled scrapers are a very common pattern. Strictly read, the Actions terms cover "your software development workflow" and prohibit using Actions as general-purpose compute; a pure data-collection cron sits in that grey area. Nobody appears to get enforced against for a small hourly job, but it is not squarely inside the terms. Free minutes: unlimited on a public repo, 2,000/month on a private one — the tiered schedule below keeps a private repo inside that. |
| **Vercel Cron** | Not viable here. Hobby allows cron **once per day**, and a function tops out well short of a 139-event crawl. Vercel is the right place for the page and Blob, not the crawler. |

The honest ranking: run it on your own machine, keep the Actions workflow as a
backstop, or accept the grey area knowingly. All three write to the same Blob
store, so you can switch without changing anything else.

---

## 3. Read frequency (tiers)

Reading 139 events hourly is ~3,300 requests a day for information that mostly
has not changed. `store.TIERS` reads by distance instead:

| Event is | Re-read every |
|---|---|
| within 7 days | 1 hour |
| within 30 days | 3 hours |
| further out | 6 hours |

That is ~54 requests an hour instead of 139 — about a third of the traffic, with
full hourly resolution exactly where prices actually move. `track.py --all`
ignores the tiers for a one-off full sweep.

---

## 4. Setup

Python 3.9+ standard library only. Chart.js is vendored, so no CDN at runtime.

```bash
# secrets (gitignored) -- the same two values are GitHub repo secrets
cat > .env.local <<'ENV'
BLOB_READ_WRITE_TOKEN=...
BLOB_STORE_ID=store_...
ENV

python3 discover.py          # find every NYC event  (~5 min, once a day)
python3 track.py             # read the ones that are due, push to Blob
python3 track.py --serve     # open the dashboard locally
```

Events enter the store through `discover.py` only — there is no manual list to
curate. It sweeps the sitemap daily and adds anything new in the city.

Without `.env.local` everything still works — the store just stays in
`public/data/` and the page reads it from there.

### Deploying

1. `git push` to GitHub.
2. Import the repo on Vercel. No build step: `vercel.json` serves `public/`.
3. Add `BLOB_READ_WRITE_TOKEN` and `BLOB_STORE_ID` as **repo secrets** (Settings
   → Secrets → Actions) so the workflows can write.
4. Enable the two workflows in the Actions tab, or leave them off and run
   `launchd` locally instead.

The page fetches from the Blob public base, which is CORS-open and CDN-cached
(60 s for `index.json`, 5 min for event history). Deploys and data are fully
decoupled — the site only redeploys when you change the code.

---

## 5. Commands

| Command | What it does |
|---|---|
| `python3 track.py` | read every due event, push to Blob, rebuild the page |
| `python3 track.py --all` | ignore the tiers, read everything |
| `python3 track.py --dry-run` | fetch and print, write nothing |
| `python3 track.py --status` | what is tracked, what is due, what was retired |
| `python3 track.py --serve` | serve `public/` and open it |
| `python3 track.py --revive SLUG` | resume a retired event |
| `python3 snapshot.py` | back the store up locally |
| `python3 snapshot.py --restore DIR` | push a snapshot back into Blob (confirmed) |
| `python3 discover.py` | find NYC events and start tracking them |
| `python3 discover.py --within-days 60` | only the next two months |
| `python3 discover.py --anywhere` | every city, not just NYC |
| `python3 crowdvolt.py <url-or-slug>` | one-off look at any event, no storage |

---

## 6. Discovery

CrowdVolt has no event-list endpoint a script can reach, but the sitemap lists
every venue, and a venue page carries its address, coordinates and links to its
upcoming events. Discovery walks **sitemap → venue pages → which venues are in
NYC → their events**, and reads each new event once (that first read is stored,
so `track.py` does not refetch it).

"NYC" is a bounding box over the five boroughs (40.48–40.93 N, 74.30–73.68 W),
not a list of place names — venues file themselves under Brooklyn, Queens,
Ridgewood, Long Island City, Astoria, NYC, New York, and the box catches all of
them. `--anywhere` turns the filter off.

Venue pages are cached for 24 h, so a daily run costs ~133 venue reads plus one
read per genuinely new event. There is no date limit by default.

---

## 7. Adding and retiring events

Add: `discover.py` does it. Retire: automatic. You never have to prune anything.

| Rule | Status |
|---|---|
| `is_past` is true, or doors have opened | `past` |
| zero listings for 6 consecutive readings, for an event that *had* listings | `sold_out` |
| 12 consecutive fetch failures | `gone` |
| discovered outside an explicit `--within-days` window | `deferred` |

The 6-reading wait means a temporary dry spell does not kill tracking on an
event that gets relisted, and an event added months early with nothing listed
yet is never mistaken for sold out. Retired events keep their history — tick
**show retired** on the dashboard. `track.py --revive <slug>` puts one back.

---

## 8. The dashboard

- **Calendar** — a full-screen month grid, each event on the day it happens *in
  its own timezone*, with artwork, floor price and ticket count. Days scroll
  inside their own cell, so a busy Saturday cannot stretch the row. On a phone
  the same events become a scrolling agenda instead of a squashed 7-column grid.
- **List** — sortable: date, days out, last sale, venue, city, genre, floor,
  24 h / 3 d / 7 d change, tickets, categories.
- **Charts** — a card per event, fetched and drawn as you scroll.

**Genre** comes from the artists on the bill. The seven most common genres take
the seven leading palette slots, everything else folds into a neutral "other" —
the map is built once from every tracked event, so filtering never repaints the
genres that survive it. The chips above the calendar and list are the filter.

**Signals** on the calendar and list, only when the data says something:

| | |
|---|---|
| 🔥 | floor is the cheapest it has been in 7 days, and falling |
| ▾ | down 3%+ recently |
| ▴ | up 3%+ recently |

Price changes are measured **by timestamp, not by reading count** — an event on
the 6-hourly tier has four readings a day, so "24 readings ago" would be six
days ago. If there is no reading near the window, the change reads `—` rather
than inventing one.

An open event shows a line per ticket category (best ask, all-in, left axis)
over a bar per category (tickets available, right axis) on a shared hourly
x-axis, a clickable chip per category — click one to isolate it, click again for
all — and a data table.

**Padded axis** (default) leaves headroom below the floor price. **From $0**
anchors the price axis at zero so bar height and line height share a baseline,
at the cost of flattening the movement. Two scales on one frame is what makes
the price/supply relationship readable, but the choice of scales can imply a
correlation — **Split axes** stacks the two measures as separate panels over a
shared x-axis if you would rather read them uncoupled.

---

## 9. Storage

No Postgres, no S3 bill. Everything is JSON:

```
index.json           roster + metadata + each event's current numbers
events/<slug>.json   that event's history, column-oriented
artists.json         artist -> genres, bio, Spotify, monthly listeners
venues.json          discovery's venue -> city cache
img/<slug>.jpg       mirrored event artwork
```

History is column-oriented (`{"stamps": [...], "types": {name: {"ask": [...]}}}`)
so a reading appends one value per array instead of rewriting row objects, and
the browser gets arrays it can hand straight to a chart. Readings older than 14
days are thinned to 6-hourly, which bounds each file at a few hundred points.

The same files live in `public/data/` locally and in Vercel Blob when
credentials are present; Blob is the source of truth for the hosted site. Total
size at 139 events is single-digit MB — well inside the Hobby free allowance.

**Backups.** Blob keeps no version history, so one bad write would be
unrecoverable. The daily sweep ends by pushing a pretty-printed copy of the
whole store to an orphan **`data` branch** — about 64 KB gzipped a day,
diffable, and independent of Blob.

It is a normal commit, not a force-push, so the branch accumulates a dated
trail you can `git diff` between any two days to see which prices moved. Files
are replaced wholesale, so an event deleted in Blob disappears from the next
snapshot rather than lingering — and the deletion shows up in the diff. It
lives on its own branch so `main` stays code-only and Vercel never redeploys
for a data change. Artwork is deliberately excluded: static, re-mirrorable, and
megabytes a week for nothing.

The flow is one-way, Blob → GitHub. To go the other way:

```bash
git worktree add /tmp/restore origin/data   # or any older commit on it
python3 snapshot.py --restore /tmp/restore  # asks before overwriting
```

Restore is manual and confirmed on purpose — it overwrites live readings, and
nothing should ever call it on a schedule. `python3 snapshot.py` alone takes a
snapshot locally without involving CI.

That daily commit also keeps the repo active. GitHub disables scheduled
workflows after 60 days without repository activity, and a workflow *run* does
not count — without it, both schedules would quietly die after two months.

**Caching, the hard way.** Vercel Blob's CDN will serve a stale body on a plain
URL even with `max-age=0` (observed: `age: 134` on a file rewritten seconds
earlier). State files are therefore uploaded with `max-age 0` *and* read with a
cache-buster, and `store.py` refuses a remote copy whose `generated_at` predates
the local mirror. Without all three, a run can read the state it just replaced
and push the result over newer data — which is exactly how four events once
vanished from the roster.

**One writer at a time.** The two workflows share a `concurrency` group, so CI
cannot race itself. If you also run `launchd` locally, pick one or the other.

**Swapping the backend** is one file: `blob.py` exposes `get` / `put` / `list` /
`delete`, and `store.py` calls nothing else. Committing JSON to the repo, or an
S3 bucket, would be a drop-in replacement.

---

## 10. Files

| | |
|---|---|
| `crowdvolt.py` | fetching + payload parsing; usable standalone |
| `store.py` | the JSON store, tiers, thinning |
| `blob.py` | Vercel Blob client |
| `track.py` | the hourly reading |
| `discover.py` | the daily NYC sweep |
| `dashboard.py` | renders `public/index.html` |
| `snapshot.py` | pulls the store out of Blob for backup |
| `.github/workflows/` | hourly reading, daily sweep + snapshot |
