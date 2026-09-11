# CrowdVolt NYC Tracker

Hourly resale prices and ticket availability for every CrowdVolt event in New
York City, with a static dashboard that tells you whether a price is actually
good.

Python standard library only. No database, no API key, no bearer token, no
build step.

---

## What it does

**Tracks every NYC event, hourly.** ~205 events, found by sweeping CrowdVolt's
sitemap once a day. There is no list to curate: new events are picked up
automatically, and past, sold-out and dead ones retire themselves.

**Prices each ticket category, not just the event.** Best ask, best bid, spread
and tickets available, per category, all-in — fees on both sides so the
comparison is like for like.

**Says what the ticket is worth new.** For events sold first on DICE (116 of
them) the tracker reads the primary platform's own tiers and compares, so a $91
ask can be reported as $8 *under* face value rather than just "$91". When no
comparable tier can be matched the value is blank — never a guess between two
different tickets.

**Four views over the same data.**

| | |
|---|---|
| **Calendar** | month grid, each event on its date in its own timezone, with artwork, price, 24h change and deal badges. A scrolling agenda on a phone. |
| **List** | every event, sortable, grouped so the table answers one question at a time — *when*, *event*, *asking*, *vs what it is worth*, *moved*, *supply*. |
| **Deals** | everything priced below face value, best first, in both dollars and percent. |
| **Charts** | a card per event: a line per ticket category over an optional bar per category for supply. |

**Tells you what changed.** A movers strip carries the biggest drops of the
last 2h (falling back to 24h, then 7d, always labelled). Deal badges mark
anything under face value, 20%+ under, or at a 7-day low.

**Remembers things, per browser.** Favourites — including pinning one ticket
category so every view quotes *that* ticket instead of the event's cheapest.
Notes on an event. Price alerts per ticket category, with a bell that counts
the ones currently met. A "since your last visit" list of what moved while you
were away. All in `localStorage`; there is no account and nothing syncs.

**Has a chat,** general and per event: anonymous, with a name your browser
invents, gated by Cloudflare Turnstile and moderatable with an admin token.
Messages live in their own bucket, deliberately outside the daily public
snapshot, so a message that is removed is actually gone.

**Keeps its own history.** Column-oriented JSON per event, thinned to 6-hourly
past 14 days, with a daily diffable backup committed to an orphan branch.

---

## Quick start

```bash
python3 discover.py       # find every NYC event (~5 min, run once a day)
python3 track.py          # read everything due, rebuild the page
python3 track.py --serve  # serve public/ and open the dashboard
```

That works with no configuration — the store stays in `public/data/` and the
page reads it from there.

To host it, put five R2 values in `.env.local` (gitignored) and the same five
in GitHub repo secrets:

```sh
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=
R2_PUBLIC_BASE=
```

Chat, if you want it, needs a **second bucket** and its own token — R2 scopes
tokens per bucket, so this is what stops the public write endpoint reaching
the price data:

```sh
R2_CHAT_ACCESS_KEY_ID=
R2_CHAT_SECRET_ACCESS_KEY=
R2_CHAT_BUCKET=
R2_CHAT_PUBLIC_BASE=
TURNSTILE_SITE_KEY=            # public, baked into the page
TURNSTILE_SECRET_KEY=          # server only, on the host
CHAT_ADMIN_TOKEN=              # reveals the delete control
```

Without them chat is simply absent and everything else works.

Then import the repo on Vercel — `vercel.json` serves `public/` with no build
step — and enable the two workflows. Data and deploys are fully decoupled: an
hourly reading replaces two JSON objects in the bucket and the live page picks
them up on its next load, so the site only redeploys when the code changes.

## Commands

| | |
|---|---|
| `track.py` | read every due event, push, rebuild the page |
| `track.py --all` | read everything, ignoring what is due |
| `track.py --dry-run` | fetch and print, write nothing |
| `track.py --status` | what is tracked, due, and retired |
| `track.py --serve` | serve the dashboard locally |
| `track.py --revive SLUG` | put a retired event back |
| `track.py --consolidate` | fold the write buffer into the history files |
| `discover.py` | sweep for NYC events and start tracking them |
| `discover.py --anywhere` | every city, not just NYC |
| `crowdvolt.py <url-or-slug>` | one-off look at any event, stores nothing |
| `snapshot.py` | back the store up locally |

---

## Before you run it wide

`crowdvolt.com/robots.txt` allows a named list of search and AI crawlers and
ends with `User-agent: * / Disallow: /`. A personal price watcher is not on
that list. This tracker is deliberately polite — an honest `User-Agent`, one
request at a time, ~1.5s between events — but it is not blessed. Nothing here
retries behind a new identity, rotates anything, or works around a block. If
you want to scale it up or share it, ask CrowdVolt for API access first.

The site's own XHR API needs a bearer token minted behind Cloudflare Turnstile.
This project does not go near it, and does not need to: the server-rendered
page carries the same numbers unauthenticated, in one request, with no token to
expire. See [ARCHITECTURE.md](ARCHITECTURE.md).

## How it works

[ARCHITECTURE.md](ARCHITECTURE.md) — where the data comes from, how face value
is matched to a resale category, the storage layout and why write volume is the
binding constraint.

## Licence

[MIT](LICENSE). The code only: the data it reads belongs to CrowdVolt and the
primary ticketing platforms.

The chat nickname vocabulary in `chatstore.py` is adapted from
[unique-names-generator](https://github.com/andreasonny83/unique-names-generator)
(MIT, © 2018-2022 AndreaSonny), filtered for tone.
