# CrowdVolt NYC Tracker

Hourly best-ask and ticket-availability tracking for every CrowdVolt event in
New York City, with a static dashboard. No account, no bearer token, no API
key, no database server.

```
 crowdvolt.com      discover.py (daily, NYC sweep)     Cloudflare R2        Vercel
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

### Fair value: what the same ticket costs new

A resale price alone cannot tell you whether $91 is a bargain. CrowdVolt is an
order book — it knows what people are asking, not what a ticket originally cost
— so the tracker fetches the primary platform's own prices and compares.

Every event carries the id it holds on whichever platform sold the tickets
first. DICE, the largest of them here, serves its tiers from a public
unauthenticated endpoint, giving two numbers per ticket category:

- **fair value** — the cheapest comparable tier you could still buy right now.
  This is the number a resale ask is judged against, because it is the
  alternative you actually have. When every tier has sold out it falls back to
  what the primary was charging when it ran out, marked as such — that is the
  case where resale premiums are largest, so a blank there would hide the most
  interesting answer.
- **original** — the cheapest tier ever offered, the anchor for how far an
  event has run up since it went on sale.

Opening an event gives a CrowdVolt mark linking to its resale listings, and —
where we can price it — a second mark linking straight to the primary seller,
so "this is $8 under face" is one click from acting on.

Roughly half of CrowdVolt's DICE events do not store the DICE id. Those are
recovered by searching DICE for the name and venue and accepting a result only
when the venue **and** the local date agree and exactly one candidate survives:
three nights of the same act at one venue is the normal case, so a near miss
must return nothing rather than the wrong night's price.

Matching a resale category to a primary tier is the fiddly part, and the
lessons are written into `primary.fair_value`:

| | |
|---|---|
| exact name match wins | "GA (2-Day Access)" is DICE's "GA 2-Day Access", not its one-night GA |
| `increment` is a minimum purchase | a $30 tier you must buy two of is not a $30 ticket |
| CrowdVolt's own grouping breaks ties | it folds four GA tiers into one resale category; a GA listing competes with all four |
| `+` is a product, not punctuation | "GA+" priced as "GA" undercharges the dearer ticket |
| the whole-event tier pool is a last resort | it once priced a $467 VIP ticket at $122 |
| a match needs the same grade AND the same night(s) | "Platinum 2-Day" against a one-night GA tier reported a $1,366 premium; GA, GA+, VIP and Platinum are different products, as are Saturday, Sunday and a 2-day pass |
| no comparable tier means no number | a blank is worth more than a confident comparison between two different tickets |
| venue names match on words, dates on equality | DICE calls it "Westlight Rooftop at The William Vale" where CrowdVolt says "Westlight at The William Vale"; the date stays an exact gate |

Where a different reading would give a different price, the value is flagged
ambiguous and the alternative is shown rather than quietly picking one. Where
the platform is not one we can read — AXS, Ticketmaster, Eventbrite and the
rest, for which CrowdVolt stores no id at all — the fair value is blank, never
a guess.

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

Reading 172 events hourly is ~3,300 requests a day for information that mostly
has not changed. `store.TIERS` reads by distance instead:

| Event is | Re-read every |
|---|---|
| within 7 days | 1 hour |
| within 30 days | 3 hours |
| further out | 6 hours |

That is ~62 requests an hour instead of 172 — 36% of the traffic, with full
hourly resolution exactly where prices actually move. `track.py --all` ignores
the tiers for a one-off full sweep.

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
(state files are not cached at all, 5 min for event history). Deploys and data are fully
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
- **List** — grouped so the table answers one question at a time: *when*, *event*,
  *asking* (the floor), *vs what it is worth* (last sale and fair value, each
  with its own delta beside it), *moved* (4 h / 24 h / 3 d / 7 d), *supply*.
  Every column sorts. The grouping exists because three different kinds of
  delta — against the last sale, against face value, and against the past —
  read as interchangeable when they sit in one flat row.
- **Charts** — a card per event, fetched and drawn as you scroll.

**Genre** comes from the artists on the bill. The seven most common genres take
the seven leading palette slots, everything else folds into a neutral "other" —
the map is built once from every tracked event, so filtering never repaints the
genres that survive it. The chips above the calendar and list are the filter.

The header carries everything: title, a sun/moon theme toggle beside it, the
stat line (ending with when prices were last read), and the movers strip
opposite. There is no footer — nothing here is worth a scroll of its own.

The page re-reads the data every 20 minutes without reloading itself, so
whatever tab, filter, search or scroll position you were on survives — it
skips the poll while the tab is hidden or an event is open, and catches up the
moment you come back to it.

A **top movers** strip sits at the top right, opposite the title: the five biggest price drops,
one at a time, rotating every six seconds and pausing on hover so a row can
actually be read and clicked — clicking opens that event. It looks at the last
2 hours first and falls back to 24 hours, then 7 days, labelling which window
it is showing rather than pretending a day-old move is fresh. Only drops of
**$10 or more** qualify: a dollar or two off an $80 ticket is a seller
relisting, not news, and a banner that cries wolf gets ignored. Nothing
qualifying means no banner at all.

**Favourites** are a star on any list row, chart card or open event, kept in
`localStorage` — so they live in one browser and do not follow you to another
device; there is no login to hang them off. A **Favourites** tab appears once
you have one, and favourites sort to the top of their own day in the calendar
and ahead of the other cards. They are not pinned in the List, because the
Favourites tab *is* that list filtered.

Starring a **ticket category** does something more: every view that would have
shown the event's cheapest ticket shows that category instead. Pin the 2-day
pass on an event and the calendar stops telling you $87 when the pass you want
is $150 against a $120 face. The row carries a tag naming the pinned category,
because calling $150 a "floor" would be false — it is not the cheapest thing
on sale, it is the thing you asked about. If a pinned category stops being
listed the row falls back to the event floor and says so rather than going
blank.

There is also a **Deals** tab: every event currently priced below fair value,
best first, showing both the percentage and the dollar saving — $20 off a $40
ticket and off a $400 one are very different claims. It states plainly when a
saving is measured against a primary that has already sold out, since nobody
can pay that price any more.

Opening an event gives one table per ticket category that is simultaneously the
chart legend, the fair-value comparison and the category filter: click a row to
isolate that category.

**Signals** on the calendar and list, only when the data says something:

| | |
|---|---|
| ⚡ | 20%+ below fair value |
| ★ | below fair value at all |
| 🔥 | floor is the cheapest it has been in 7 days, and falling |
| ▾ ▴ | down / up 3%+ recently |

Price changes are measured **by timestamp, not by reading count** — an event on
the 6-hourly tier has four readings a day, so "24 readings ago" would be six
days ago. If there is no reading near the window, the change reads `—` rather
than inventing one.

An open event shows a line per ticket category (best ask, all-in, left axis)
over a bar per category (tickets available, right axis) on a shared hourly
x-axis, and a data table.

The price axis leaves headroom below the floor price rather than anchoring at
zero, which would flatten the movement. Two scales on one frame is what makes
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

The same files live in `public/data/` locally and in Cloudflare R2 when
credentials are present; Blob is the source of truth for the hosted site. Total
size at 172 events is single-digit MB — well inside the Hobby free allowance.

**Backups.** R2 keeps no version history, so one bad write would be
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

**Write volume is the constraint, not storage.** The store holds ~4 MB and
moves ~90 MB a month; what costs is the number of objects written. Rewriting
all 172 event files every hour was 124,000 writes a month, which exhausted the
previous store's monthly allowance in thirteen hours. Readings now land in
`recent.json` and are folded into the per-event files once a day: **two writes
an hour instead of 173**, about 6,600 a month for identical data.

**Caching.** A CDN will serve a stale body on a plain URL even with
`max-age=0`. State files are therefore uploaded with `max-age 0` *and* read
with a cache-buster, and `store.py` refuses a remote copy whose `generated_at`
predates the local mirror. Without all three, a run can read the state it just
replaced and push the result over newer data — which is exactly how four events
once vanished from the roster.

**One writer at a time.** The two workflows share a `concurrency` group, so CI
cannot race itself. If you also run `launchd` locally, pick one or the other.

**Swapping the backend** is one file: `r2.py` exposes `get` / `put` /
`put_bytes` / `list`, and `store.py` calls nothing else. Committing JSON to the repo, or an
S3 bucket, would be a drop-in replacement.

---

## 10. Files

| | |
|---|---|
| `crowdvolt.py` | fetching + payload parsing; usable standalone |
| `store.py` | the JSON store, tiers, thinning |
| `r2.py` | Cloudflare R2 client (S3 SigV4, no dependencies) |
| `track.py` | the hourly reading |
| `discover.py` | the daily NYC sweep |
| `dashboard.py` | renders `public/index.html` |
| `primary.py` | face value from the primary platform, and category matching |
| `snapshot.py` | pulls the store out of Blob for backup |
| `.github/workflows/` | hourly reading, daily sweep + snapshot |
