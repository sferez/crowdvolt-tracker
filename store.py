"""The tracker's state, as plain JSON.

    index.json          roster + each event's current price and ticket count
    events/<slug>.json  that event's history, column-oriented
    venues.json         discovery's venue -> city cache

Column-oriented history ({"stamps": [...], "types": {name: {"ask": [...]}}})
means an hourly reading appends one value per array rather than rewriting a
list of row objects -- small writes, and the browser gets arrays it can hand
straight to a chart.

The same files live in two places: public/data/ locally, and Vercel Blob when
credentials are present. Blob is the source of truth for the hosted site --
the static page never redeploys, the hourly job just replaces two or three
JSON objects. Without credentials everything still works, purely local.
"""

import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import blob

ROOT = Path(__file__).parent / "public" / "data"

SERIES_FIELDS = ["ask", "all_in", "ask_qty", "bid", "bid_qty", "qty", "listings"]

# Event-wide numbers that are worth a history of their own, alongside the
# per-category ones. last_sale is the only trade signal the page carries.
EVENT_FIELDS = {"last_sale": "last_sale", "high": "event_high_ask_all_in",
                "bidders": "bidders_count", "listings": "listings_count",
                "tickets": "tickets_remaining", "bid_all_in": "event_max_bid_all_in"}

# History is kept hourly for two weeks, then thinned to 6-hourly. Keeps each
# event file bounded, so the repo does not grow without limit.
FULL_RESOLUTION = 336
OLDER_STRIDE = 6

# How often an event is re-read, by how far away it is. A show three months
# out does not move hourly, and reading it hourly is 24x the requests for the
# same information.
TIERS = [(7, 1), (30, 3), (None, 6)]   # (days out at most, hours between reads)


def _read(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True))


def _slot(slug, every):
    """Which hour-of-the-cycle this event belongs to. Stable across runs."""
    if every <= 1:
        return 0
    return int(hashlib.md5(slug.encode()).hexdigest()[:8], 16) % every


def _parse(ts):
    return datetime.fromisoformat(ts) if ts else None


class Store:
    def __init__(self, root=ROOT, remote=True):
        self.root = Path(root)
        self.index_path = self.root / "index.json"
        self.events_dir = self.root / "events"
        self.venues_path = self.root / "venues.json"
        self.remote = blob.Remote() if remote else None
        self.dirty = set()
        idx = self._load("index.json", self.index_path, {})
        self.events = idx.get("events", {})
        self.meta = {k: v for k, v in idx.items() if k != "events"}

    # Remote wins on load: another machine (or the GitHub Action) may have
    # taken readings since this checkout last ran.
    STATE_FILES = {"index.json", "skipped.json", "venues.json", "artists.json"}

    def _load(self, rel, path, default):
        if self.remote and self.remote.enabled:
            got = self.remote.get(rel, fresh=rel in self.STATE_FILES)
            if got is not None and not _is_stale(got, _read(path, None)):
                _write(path, got)
                return got
        return _read(path, default)

    def _store(self, rel, path, data):
        _write(path, data)
        self.dirty.add(rel)

    def push(self):
        """Upload everything this run changed. No-op without credentials."""
        if not (self.remote and self.remote.enabled) or not self.dirty:
            return []
        sent = []
        for rel in sorted(self.dirty):
            data = _read(self.root / rel, None)
            if data is not None and self.remote.put(rel, data):
                sent.append(rel)
        self.dirty.clear()
        return sent

    # ---------------------------------------------------------------- roster

    def ensure(self, slug, url, now):
        if slug not in self.events:
            self.events[slug] = {"url": url, "status": "active", "added_at": now,
                                 "empty_streak": 0, "error_streak": 0,
                                 "ever_had_tickets": False}
            return True
        return False

    def update_meta(self, slug, snap, now):
        e = self.events.setdefault(slug, {})
        starts = _starts_epoch(snap)
        e.update({
            "url": snap["url"], "name": snap["name"], "venue": snap["venue"],
            "venue_slug": snap.get("venue_slug"),
            "city": snap["city"], "state": snap["state"], "area": snap.get("area"),
            "doors": snap["doors_open"], "starts_ts": starts,
            "tz": snap["time_zone"], "local_date": _local_date(starts, snap["time_zone"]),
            "lat": snap.get("lat"), "lng": snap.get("lng"),
            # cosmetics, read once and reused by every view
            "img": snap.get("img"), "performers": snap.get("performers") or [],
            "platform": snap.get("platform"),
            # CrowdVolt does not store this for about half its DICE events;
            # discover.py recovers it by search. Overwriting unconditionally
            # erased that on the next hourly run and froze the face value.
            "dice_id": snap.get("dice_id") or e.get("dice_id"),
            "ticket_limit": snap.get("ticket_limit"),
            "is_festival": snap.get("is_festival"),
            "last_ok_at": now, "last_read_at": now, "error_streak": 0,
        })
        e.setdefault("added_at", now)
        e.setdefault("status", "active")

    def retire(self, slug, reason, now):
        e = self.events.setdefault(slug, {})
        e["status"] = reason.split(":")[0]
        e["retired_at"] = now
        e["retired_reason"] = reason

    def revive(self, slug):
        e = self.events.get(slug)
        if not e:
            return None
        was = e.get("status"), e.get("retired_reason")
        e.update({"status": "active", "error_streak": 0, "empty_streak": 0})
        e.pop("retired_at", None)
        e.pop("retired_reason", None)
        return was

    def bump(self, slug, field, reset=False):
        e = self.events.setdefault(slug, {})
        e[field] = 0 if reset else e.get(field, 0) + 1
        return e[field]

    def active(self):
        return sorted(s for s, e in self.events.items() if e.get("status") == "active")

    def due(self, now):
        """Active events whose tier says they are ready for another reading.

        Each event gets a fixed slot within its tier, derived from its slug, so
        a tier's events spread evenly across its window instead of all coming
        due in the same hour. Without this the cohorts stay synchronised
        forever -- everything was seeded within a few minutes of each other, so
        an hourly run would alternate between 26 events and 140, which is both
        a burst of traffic and a bill.
        """
        out = []
        for slug in self.active():
            e = self.events[slug]
            days = ((e.get("starts_ts") or 0) - now.timestamp()) / 86400
            every = next(h for limit, h in TIERS if limit is None or days <= limit)
            last = _parse(e.get("last_read_at"))
            if last is None:
                out.append(slug)
                continue
            elapsed = now - last
            # a missed run (GitHub's scheduler is best-effort) should not cost a
            # whole extra window -- catch up once we are well past due
            if elapsed >= timedelta(hours=every * 2):
                out.append(slug)
                continue
            if now.hour % every != _slot(slug, every):
                continue
            # a little slack so an 07:03 run still counts as "an hour later"
            if elapsed >= timedelta(hours=every, minutes=-10):
                out.append(slug)
        return out

    def mark_read(self, slug, now):
        self.events.setdefault(slug, {})["last_read_at"] = now

    # --------------------------------------------------------------- history

    def _event_path(self, slug):
        return self.events_dir / f"{slug}.json"

    def history(self, slug):
        """Remote wins, always.

        Reading the local mirror when Blob has newer data is how you lose a
        week: run on the Mac for a while, switch to the Action for a while,
        then run locally once -- a stale local file gets one reading appended
        and pushed over the top. One cached GET per due event is cheap
        insurance. (Still: one runner at a time. Two writers inside the event
        files' 300 s cache TTL can lose a reading whatever this does.)
        """
        rel = f"events/{slug}.json"
        h = self._load(rel, self._event_path(slug), None)
        if h is None:
            return {"stamps": [], "types": {}, "event": {}}
        h.setdefault("event", {})
        return h

    def add_snapshot(self, slug, ts, rows, snap=None):
        h = self.history(slug)
        stamps, types = h["stamps"], h["types"]
        if stamps and stamps[-1] == ts:
            return h
        stamps.append(ts)
        n = len(stamps)
        ev = h.setdefault("event", {})
        for key, src in EVENT_FIELDS.items():
            col = ev.setdefault(key, [None] * (n - 1))
            while len(col) < n - 1:
                col.append(None)
            col.append((snap or {}).get(src))

        # Fair value gets its own series alongside the resale price. It is not
        # a constant: a tier sells out, the next opens dearer, and the primary
        # climbs while a resale ask sits still -- which is the whole story of
        # an event heating up, and invisible from a single current figure.
        fair_col = ev.setdefault("fair", [None] * (n - 1))
        while len(fair_col) < n - 1:
            fair_col.append(None)
        # cheapest category in this reading -- the one the floor comes from
        priced = [r for r in rows if r.get("best_ask_all_in") is not None]
        floor_name = (min(priced, key=lambda r: r["best_ask_all_in"])["ticket_type"]
                      if priced else None)
        fair_col.append(self._fair_now(slug, floor_name))
        seen = set()
        for r in rows:
            name = r["ticket_type"]
            seen.add(name)
            t = types.setdefault(name, {"uqid": r["ticket_type_uqid"],
                                        **{f: [None] * (n - 1) for f in SERIES_FIELDS}})
            if r.get("linked_count"):
                t["linked_count"] = r["linked_count"]
            for f in SERIES_FIELDS:
                t[f].append(r[_FIELD_MAP[f]])
        for name, t in types.items():          # categories missing this run
            for f in SERIES_FIELDS:
                while len(t[f]) < n:
                    t[f].append(None)
        _thin(h)
        self._store(f"events/{slug}.json", self._event_path(slug), h)
        self._refresh_current(slug, h)
        return h

    def _fair_now(self, slug, floor_cat=None):
        """Fair value for the category that sets the floor, to record alongside
        it.

        This has to use the same category as `_refresh_current`, or the series
        and the table disagree: the event-wide fallback recorded ZHU's $321 VIP
        tier next to its $90 GA floor -- the very comparison the rest of this
        file exists to avoid -- and every run appended another one.
        """
        face = (self.events.get(slug) or {}).get("primary") or {}
        by_cat = face.get("by_category") or {}
        if floor_cat and by_cat.get(floor_cat):
            return by_cat[floor_cat].get("value")
        if face.get("on_sale") is not None:
            return face["on_sale"]
        return None

    def _refresh_current(self, slug, h):
        """The dashboard's calendar and list read only index.json, so each
        event's latest numbers are cached there rather than recomputed from
        139 history files."""
        ev = h.get("event") or {}
        cats, floor, tickets = [], None, 0
        for name, t in h["types"].items():
            ask, qty = _last(t["all_in"]), _last(t["qty"]) or 0
            if ask is None and not qty and not any(v is not None for v in t["all_in"]):
                continue
            cats.append({"name": name, "ask": ask, "qty": qty})
            tickets += qty
            if ask is not None:
                floor = ask if floor is None else min(floor, ask)
        e = self.events.setdefault(slug, {})
        bidders_now = _last(ev.get("bidders") or [])

        low7 = _floor_low(h, 168)
        face = e.get("primary") or {}
        # Both ends of this subtraction must describe the SAME ticket. Taking
        # the floor from one category and the fair value from an event-wide
        # min/max compared a $90 GA ask against a $321 VIP tier and reported a
        # $231 bargain. The floor is set by one category, so the fair value has
        # to be that category's.
        floor_cat = min((c for c in cats if c["ask"] is not None),
                        key=lambda c: c["ask"], default=None)
        fv = ((face.get("by_category") or {}).get(floor_cat["name"])
              if floor_cat else None) or {}
        primary_now = fv.get("value")
        basis = fv.get("basis")

        # The bid shown beside the floor has to belong to the same ticket. The
        # event-wide maximum belongs to whichever category is dearest, so ZHU
        # displayed a $192 VIP bid next to a spread computed from its $90 GA --
        # two different tickets, presented as one market.
        # Trust the concrete bid over the count. CrowdVolt's event-level
        # bidder count lags for a few events, reading 0 while that category
        # plainly has a live bid -- nulling the bid on the strength of the
        # count then left a spread on the page with nothing to explain it.
        bid_now = _bid_of(h, floor_cat)
        bidders_now = bidders_now or None
        e["current"] = {"floor": floor, "tickets": tickets, "cats": cats,
                        "change24": _change(h, 24),
                        "change3d": _change(h, 72),
                        "change7d": _change(h, 168),
                        # "cheapest it has been all week", the signal worth an
                        # icon on the calendar
                        "low7d": low7,
                        "at_low": floor is not None and low7 is not None and floor <= low7,
                        "last_sale": _last(ev.get("last_sale") or []),
                        # what the same ticket costs on the platform that sold
                        # it first -- the alternative you actually have
                        "primary": primary_now,
                        "primary_basis": basis,
                        "original": face.get("original"),
                        "vs_primary": (None if primary_now is None or floor is None
                                       else round(floor - primary_now, 2)),
                        # relative, because a flat dollar threshold is wrong at
                        # both ends: $5 off is 12% of a $41 ticket and 1% of a
                        # $400 one. Massano sat $2.32 under fair value -- 5.6%,
                        # plainly a deal -- and an absolute cutoff hid it.
                        "vs_primary_pct": (None if primary_now in (None, 0) or floor is None
                                           else round((floor - primary_now) / primary_now, 4)),
                        "bid": bid_now,
                        "bidders": bidders_now,
                        # Within one category and on one basis. The event-wide
                        # max bid belongs to whichever category is dearest --
                        # against the cheapest category's ask that produced
                        # "spread -$284", a book that would have cleared.
                        "spread": (None if bid_now is None
                                   else _spread(h, floor_cat)),
                        "readings": len(h["stamps"])}
        if tickets:
            e["ever_had_tickets"] = True

    # ---------------------------------------------------------------- images

    def mirror_image(self, slug, src):
        """Copy an event's artwork into Blob once and serve it from there.

        Not a CORS fix -- an <img> renders cross-origin fine -- but it means the
        dashboard does not hotlink someone else's CDN, does not break when a
        source URL is rotated, and does not make every viewer's browser call
        crowdvolt.com.
        """
        if not (self.remote and self.remote.enabled) or not src:
            return None
        # imgix serves a thumbnail if asked; a 400px square is plenty
        url = src + ("&" if "?" in src else "?") + "w=400" if "imgix" in src else src
        try:
            req = urllib.request.Request(url, headers={"user-agent": "crowdvolt-price-watch/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            return None
        ext = {"image/png": "png", "image/webp": "webp",
               "image/avif": "avif"}.get(ctype, "jpg")
        return self.remote.put_bytes(f"img/{slug}.{ext}", data, ctype)

    # --------------------------------------------------------------- skipped

    def skipped(self):
        """Events checked once and found to be outside the area.

        Their own file, not the roster: the dashboard downloads index.json on
        every page load, and a rejected event's name, artwork and line-up are
        dead weight there. All we need to remember is that we looked.
        """
        return self._load("skipped.json", self.root / "skipped.json", {})

    def save_skipped(self, data):
        self._store("skipped.json", self.root / "skipped.json", data)

    # --------------------------------------------------------------- artists

    def artists(self):
        return self._load("artists.json", self.root / "artists.json", {})

    def save_artists(self, data):
        self._store("artists.json", self.root / "artists.json", data)

    # ---------------------------------------------------------------- venues

    def venues(self):
        return self._load("venues.json", self.venues_path,
                          {"venues": {}, "venue_events": {}})

    def save_venues(self, data):
        self._store("venues.json", self.venues_path, data)

    # ------------------------------------------------------------------ save

    def save(self):
        self._store("index.json", self.index_path,
                    {**self.meta, "events": self.events,
                     "generated_at": datetime.now(timezone.utc)
                     .isoformat(timespec="seconds")})


_FIELD_MAP = {"ask": "best_ask", "all_in": "best_ask_all_in", "ask_qty": "best_ask_qty",
              "bid": "best_bid", "bid_qty": "best_bid_qty",
              "qty": "tickets_available", "listings": "listings"}


def _is_stale(remote, local):
    """Belt and braces on top of max-age 0: if the copy that came back is
    older than the local mirror, it is a cached read of something we have
    already superseded. Keep the local one rather than appending to it and
    pushing the result over newer data."""
    if not isinstance(remote, dict) or not isinstance(local, dict):
        return False
    r, l = remote.get("generated_at"), local.get("generated_at")
    return bool(r and l and r < l)


def _bid_of(h, floor_cat):
    """Best bid for the category that sets the floor."""
    if not floor_cat:
        return None
    t = (h.get("types") or {}).get(floor_cat["name"]) or {}
    return _last(t.get("bid") or []) or None


def _spread(h, floor_cat):
    """Ask minus bid for the category that sets the floor, both pre-fee.

    CrowdVolt adds fees to an ask and subtracts them from a bid, so mixing
    all-in and base across the two sides inverts the sign on top of the
    cross-category error.
    """
    bid = _bid_of(h, floor_cat)
    if bid is None:
        return None
    t = (h.get("types") or {}).get(floor_cat["name"]) or {}
    ask = _last(t.get("ask") or [])
    return None if ask is None else round(ask - bid, 2)


def _last(a):
    for v in reversed(a):
        if v is not None:
            return v
    return None


def _floor_at(h, i):
    vals = [t["all_in"][i] for t in h["types"].values()
            if i < len(t["all_in"]) and t["all_in"][i] is not None]
    return min(vals) if vals else None


def _index_at(h, hours_ago):
    """Index of the reading nearest `hours_ago` hours before the last one.

    Position is not time: an event on the 6-hourly tier has four readings a
    day, so "24 readings back" would be six days back. Always go by stamp.
    """
    stamps = h["stamps"]
    if not stamps:
        return None
    try:
        last = datetime.fromisoformat(stamps[-1])
    except ValueError:
        return None
    target = last - timedelta(hours=hours_ago)
    best, best_gap = None, None
    for i, ts in enumerate(stamps):
        try:
            gap = abs((datetime.fromisoformat(ts) - target).total_seconds())
        except ValueError:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = i, gap
    # no reading within half the window either side -- do not invent a change
    if best is None or best_gap > hours_ago * 1800:
        return None
    return best


def _change(h, hours):
    if len(h["stamps"]) < 2:
        return None
    i = _index_at(h, hours)
    if i is None or i == len(h["stamps"]) - 1:
        return None
    now, then = _floor_at(h, len(h["stamps"]) - 1), _floor_at(h, i)
    return None if now is None or then is None else round(now - then, 2)


def _floor_low(h, hours):
    """Lowest floor price seen in the last `hours`, and whether we are at it."""
    n = len(h["stamps"])
    start = _index_at(h, hours)
    start = 0 if start is None else start
    seen = [v for v in (_floor_at(h, i) for i in range(start, n)) if v is not None]
    return min(seen) if seen else None


def _thin(h):
    """Recent readings stay hourly; older ones drop to every sixth.

    Thinning by index was wrong: it runs on every append, so the cut point
    advanced one step at a time and `i % 6` was evaluated against an ever
    shifting origin -- the result deleted the old readings outright instead of
    downsampling them. Age is a stable property of a reading, so deciding by
    stamp is idempotent however many times it runs.
    """
    n = len(h["stamps"])
    if n <= FULL_RESOLUTION:
        return
    try:
        newest = datetime.fromisoformat(h["stamps"][-1])
    except (ValueError, IndexError):
        return
    cutoff = newest - timedelta(hours=FULL_RESOLUTION)

    keep, last_kept = [], None
    for i, ts in enumerate(h["stamps"]):
        try:
            when = datetime.fromisoformat(ts)
        except ValueError:
            keep.append(i)
            continue
        if when >= cutoff:
            keep.append(i)
        elif last_kept is None or (when - last_kept) >= timedelta(hours=OLDER_STRIDE):
            keep.append(i)
            last_kept = when
    if len(keep) == n:
        return
    h["stamps"] = [h["stamps"][i] for i in keep]
    for t in h["types"].values():
        for f in SERIES_FIELDS:
            t[f] = [t[f][i] for i in keep]
    for key, col in (h.get("event") or {}).items():
        h["event"][key] = [col[i] for i in keep if i < len(col)]


def _local_date(starts_ts, tz):
    """The day the show happens *where it happens*. A 10PM New York gig is not
    a next-day event because you are reading this from Paris."""
    if not starts_ts:
        return None
    try:
        zone = ZoneInfo(tz) if tz else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
    return datetime.fromtimestamp(starts_ts, zone).strftime("%Y-%m-%d")


def _starts_epoch(snap):
    """"2026-10-25 02:00:00" (UTC) -> unix seconds."""
    raw = snap.get("starts_at")
    if not raw:
        return None
    try:
        return int(datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
                   .replace(tzinfo=timezone.utc).timestamp())
    except (ValueError, TypeError):
        return None
