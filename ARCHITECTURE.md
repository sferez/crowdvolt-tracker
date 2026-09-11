# Architecture

```
crowdvolt.com        discover.py (daily NYC sweep)      Cloudflare R2       Vercel
 (SSR page)     ──>  track.py    (hourly reading)  ──>  index.json     ──>  static page
 api.dice.fm         primary.py  (face value)           recent.json         calendar · list
                                                        events/*.json       deals · charts
```

Nothing is baked into the page but code. A reading replaces two JSON objects in
the bucket; the live site picks them up on the next load without redeploying.

---

## 1. Where the data comes from

CrowdVolt's event pages are server-rendered, and the React Server Component
payload carries the whole order book. One `GET` of the event URL — with an
`RSC: 1` header, which returns the payload alone (~115 KB instead of ~380 KB of
HTML) — gives everything:

| In the payload | What it holds |
|---|---|
| `event.tt_data.types[]` | per category: lowest ask, all-in ask, ask qty, highest bid |
| `initialBook.sell[]` / `buy[]` | every ask and bid: `price`, `all_in_price`, `qty`, `ticket_type` |
| `last_sale`, `looking_to_go`, `looking_to_sell` | event-wide numbers, kept as their own series |
| `square_img_link`, `performer_bubbles`, `app_name`, `venue_obj` | artwork, line-up, source platform, address — read once into metadata |
| `discover_more_events` | related events at the same venue: free discovery leads |

`crowdvolt.py` bracket-matches the event object out of the payload with a
string-aware brace matcher. `event_uqid` is just the URL slug. Tickets
available per category is the sum of `qty` over that category's asks, which
reconciles exactly with the event's own `tickets_remaining`.

**Why not the API.** The XHR endpoints the site's own client uses
(`/api/event/details`, `/api/book/get`) need a bearer token minted behind
Cloudflare Turnstile — the page keeps a `turnstile_token` and `fingerprint_id`
in `localStorage` and feeds them to `/api/user/bootstrap`. The host also sits
behind a managed challenge and answers a scripted request with 403 or 503
whatever the token says. Minting that token from a script means automating bot
detection, so this project does not. The SSR page returns the same numbers.

**Genre** is not on the event page; it lives on `/performer/<slug>`. Artists
are read once each into `artists.json` and their genres hang on every event
they play — one read per artist ever, not one per event.

**Artwork** is mirrored into the bucket on first sight (a 400px thumbnail),
written once and never rewritten. Not for CORS — an `<img>` renders
cross-origin fine — but so the page does not hotlink someone else's CDN, does
not break when a source URL rotates, and does not make every viewer's browser
call crowdvolt.com.

## 2. Face value

A resale price alone cannot tell you whether $91 is a bargain. CrowdVolt is an
order book: it knows what people are asking, not what a ticket cost new. So
each event carries the id it holds on whichever platform sold it first, and
DICE — the largest here, 116 of 205 events — serves its tiers from a public
unauthenticated endpoint. That gives two numbers per category:

- **fair value** — the cheapest comparable tier you could still buy right now,
  because that is the alternative you actually have. Once every tier has sold
  out it falls back to what the primary was charging when it ran out, marked as
  such: that is where resale premiums are largest, so a blank would hide the
  most interesting answer.
- **original** — the cheapest tier ever offered, the anchor for how far an
  event has run up since going on sale.

About half of the DICE events do not store the id. Those are recovered by
searching DICE for name and venue, accepting a result only when venue **and**
local date agree and exactly one candidate survives — three nights of the same
act at one venue is the normal case, so a near miss must return nothing rather
than the wrong night's price.

Matching a resale category to a primary tier is the fiddly part, and every rule
in `primary.fair_value` is there because its absence produced a wrong number:

| | |
|---|---|
| exact name match wins | "GA (2-Day Access)" is DICE's "GA 2-Day Access", not its one-night GA |
| `increment` is a minimum purchase | a $30 tier you must buy two of is not a $30 ticket |
| CrowdVolt's grouping breaks ties | it folds four GA tiers into one resale category |
| `+` is a product, not punctuation | "GA+" priced as "GA" undercharges the dearer ticket |
| the whole-event tier pool is a last resort | it once priced a $467 VIP ticket at $122 |
| same grade **and** same night(s), or nothing | "Platinum 2-Day" against a one-night GA tier reported a $1,366 premium |
| venues match on words, dates on equality | DICE's "Westlight Rooftop at The William Vale" is CrowdVolt's "Westlight at The William Vale"; the date stays an exact gate |

Where a different reading would give a different price the value is flagged
ambiguous and the alternative shown. Where the platform is one we cannot read —
AXS, Eventbrite, Resident Advisor and the rest, for which CrowdVolt stores no
usable id — fair value is blank.

## 3. Discovery and retirement

CrowdVolt has no event-list endpoint, but the sitemap lists every event and
every venue. Discovery walks the **event sitemap** directly, reads anything new
once (that read is stored, so `track.py` does not refetch it), and decides
whether it is in New York from the venue's coordinates **or** its city name —
coordinates alone once vetoed two correct venues that had rounded theirs to
`41,-74`.

Venue pages are cached for 24 hours and non-NYC events are remembered as
rejected, so a daily sweep costs one read per genuinely new event. There is no
date limit.

Retirement is automatic; nothing needs pruning:

| Rule | Status |
|---|---|
| past, or doors have opened | `past` |
| zero listings for 6 consecutive readings, having previously had some | `sold_out` |
| 12 consecutive fetch failures | `gone` |

The 6-reading wait means a dry spell does not kill an event that gets
relisted, and an event added months early with nothing listed yet is never
mistaken for sold out. Retired events keep their history.

## 4. Storage, and why writes are the constraint

Everything is JSON:

```
index.json           roster + metadata + each event's current numbers
recent.json          the write buffer: every reading since the last consolidation
events/<slug>.json   that event's history, column-oriented
artists.json         artist -> genres, bio, Spotify, monthly listeners
venues.json          venue -> city cache, and the non-NYC rejections
img/<slug>.jpg       mirrored artwork
```

History is column-oriented (`{"stamps": [...], "types": {name: {"ask": [...]}}}`)
so a reading appends one value per array instead of rewriting row objects, and
the browser gets arrays it can hand straight to a chart. Readings older than 14
days are thinned to 6-hourly, bounding each file at a few hundred points.

**Write volume, not size, is what costs.** The store holds a few MB. Rewriting
all ~200 event files every hour was ~124,000 writes a month, which exhausted
the previous store's monthly allowance in thirteen hours. Readings now land in
`recent.json` and are folded into the per-event files once a day: **two writes
an hour instead of two hundred**, about 6,600 a month for identical data. The
dashboard loads `recent.json` alongside `index.json` and merges it onto an
event's history when you open one, so a chart is current even though its
history file is a day old.

**Caching.** A CDN will serve a stale body on a plain URL even with
`max-age=0`. State files are therefore uploaded with `max-age 0` *and* read
with a cache-buster, and `store.py` refuses a remote copy whose `generated_at`
predates the local mirror. Without all three, a run can read the state it just
replaced and push the result over newer data — which is how four events once
vanished from the roster.

**Backups.** R2 keeps no version history, so one bad write would be
unrecoverable. The daily sweep ends by committing a pretty-printed copy of the
whole store to an orphan `data` branch — ~64 KB gzipped a day, diffable between
any two days to see which prices moved. It is a normal commit, not a
force-push. It lives on its own branch so `main` stays code-only and Vercel
never redeploys for a data change. Artwork is excluded: static, re-mirrorable,
and megabytes a week for nothing.

Restore is manual and confirmed, and nothing should ever call it on a schedule:

```bash
git worktree add /tmp/restore origin/data
python3 snapshot.py --restore /tmp/restore
```

That daily commit also keeps the repo active — GitHub disables scheduled
workflows after 60 days without repository activity, and a workflow *run* does
not count.

**One writer at a time.** The two workflows share a `concurrency` group so CI
cannot race itself. Swapping the backend is one file: `r2.py` exposes
`get`/`put`/`put_bytes`/`list` and `store.py` calls nothing else.

## 5. Chat

A general thread and one per event, in a **separate R2 bucket**. Two reasons,
and both are structural rather than a policy someone has to remember:

- R2 tokens are scoped per bucket, not per prefix. `api/chat.py` is the only
  code here that takes writes from the open internet; with the tracker's own
  key, a bug in it could rewrite `index.json` and every `events/*.json`.
- `snapshot.py` lists everything under the tracker's prefix and the daily
  workflow commits it to a public branch. Chat there would mean a moderated
  message surviving in git history for good.

One JSON object per thread, not one per message: the browser reads threads
straight from the bucket and cannot list one, so per-message objects would
turn every poll into a server call. The cost is a read-modify-write, so every
write is a **compare-and-swap** — a signed `GET` for the body and its ETag,
then a `PUT` carrying `If-Match`, retried on a 412. The read has to be the
signed one: `Remote.get()` goes through the CDN and returns `None` for a 404,
a timeout and bad JSON alike, so a blip would look exactly like "no thread
yet" and overwrite a live one with an empty document.

Posting goes through `api/chat.py` on Vercel, which holds the chat bucket's
credentials and nothing else — it checks that an event slug is real by reading
the tracker's public `index.json` over plain HTTP, like any other visitor.
Turnstile gates writes on every deployment and is skipped only when the
`VERCEL` variable is absent, so there is no switch that can ship the endpoint
unprotected. Rate limiting comes from the thread itself, which the write has
just read anyway: a cooldown per browser, a cap on how many of the last five
messages are yours, a 500-character limit.

Identities are a name the browser invents from a 1068 × 353 vocabulary
(~377,000), with a hashed 4-character tag carried on every message and shown
only when two people in one thread actually collide. Colour is assigned by
hashing to a slot and linear-probing to the next free one, so everyone in a
thread is a visibly different hue rather than merely a different number.
Message text is rendered with `textContent`, never interpolation — it is the
only content on the page written by one visitor and read by another.

## 6. The dashboard

`dashboard.py` renders a single static `public/index.html` that fetches its
data at runtime. Chart.js is vendored, so no CDN is needed.

Some decisions worth knowing:

- **Price changes are measured by timestamp, not by reading count**, so a gap
  in the readings reads `—` rather than inventing a number.
- **A pinned ticket category is resolved once per row**, in `rowView()`, and
  every figure the row shows comes from it. A half-routed row is the worst
  outcome: a $150 two-day ask beside the event's 24h move is two different
  tickets printed as one line.
- **Genres fold into eight groups.** The artists carry 96 distinct genres and
  62 appear exactly once — a long tail, not a taxonomy. The map is built once
  from every tracked event, so filtering never repaints the survivors.
- **Chart lines name themselves at their own right-hand end** instead of in a
  legend, so identity never rests on colour alone.
- **The page re-reads its data every 20 minutes** without reloading, so
  whatever tab, filter, search or scroll position you were on survives. It
  skips the poll while the tab is hidden or an event is open.

## 7. Files

| | |
|---|---|
| `crowdvolt.py` | fetching + payload parsing; usable standalone |
| `primary.py` | face value from the primary platform, and category matching |
| `store.py` | the JSON store, consolidation, thinning |
| `r2.py` | Cloudflare R2 client (S3 SigV4, no dependencies) |
| `track.py` | the hourly reading |
| `discover.py` | the daily NYC sweep |
| `dashboard.py` | renders `public/index.html` |
| `snapshot.py` | pulls the store out for backup, and restores it |
| `chatstore.py` | chat threads: validation, moderation, compare-and-swap |
| `api/chat.py` | the Vercel function; a thin adapter over `chatstore` |
| `.github/workflows/` | hourly reading; daily sweep + snapshot |
