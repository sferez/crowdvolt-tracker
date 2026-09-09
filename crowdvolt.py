#!/usr/bin/env python3
"""
CrowdVolt price reader.

The event page is server-rendered and ships the whole order book in its React
Server Component payload -- no login, no bearer token, no API key:

    event.tt_data.types[]   -> per ticket category: lowest_ask_price,
                               all_in_lowest_ask_price, lowest_ask_qty,
                               highest_bid_price, highest_bid_qty
    initialBook.sell[]      -> every ask: price, all_in_price, qty, ticket_type
    initialBook.buy[]       -> every bid, same shape

So "best ask per category" and "tickets available per category" both come out
of one plain GET.

Standalone use, for a one-off look at any event:
    ./crowdvolt.py <slug-or-url> [more...]
"""

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE = "https://www.crowdvolt.com"
SITEMAP = f"{BASE}/sitemap.xml"

# Identify honestly. crowdvolt.com/robots.txt allows a named list of search/AI
# crawlers and disallows everything else -- see README before running at volume.
UA = "crowdvolt-price-watch/1.0 (personal price monitor)"



class FetchError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------

def fetch(url, rsc=True, timeout=20):
    """GET an event page. rsc=True asks for the flight payload only
    (~115KB vs ~380KB of HTML) -- same data, less bandwidth."""
    headers = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    if rsc:
        headers["RSC"] = "1"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}", status=e.code) from e
    except Exception as e:
        raise FetchError(f"{type(e).__name__}: {e}") from e


# --------------------------------------------------------------------------
# payload extraction
# --------------------------------------------------------------------------

_NEXT_F = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', re.S)


def to_payload(text):
    """Return the flight payload, from either the RSC response (already the
    payload) or a full HTML document (payload split across __next_f pushes)."""
    if '"tt_data"' in text:
        return text
    chunks = []
    for m in _NEXT_F.finditer(text):
        try:
            chunks.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            pass
    return "".join(chunks)


def enclosing_object(text, target):
    """The JSON object literal enclosing index `target`.

    One forward pass, string- and escape-aware, keeping a stack of open brace
    positions -- safer than trying to regex around the key.
    """
    stack, in_str, esc, start = [], False, False, None
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            stack.append(i)
        elif ch == "}":
            if stack:
                opened = stack.pop()
                if start is not None and opened <= start and i > target:
                    return text[opened:i + 1]
        if i == target and stack:
            start = stack[-1]
    return None


def _object_at(payload, key):
    idx = payload.find(f'"{key}"')
    if idx == -1:
        return None
    blob = enclosing_object(payload, idx)
    if not blob:
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# shaping
# --------------------------------------------------------------------------

def slug_of(s):
    s = s.strip().split("?")[0].rstrip("/")
    return s.rsplit("/", 1)[-1] if s else ""


def url_of(s):
    s = s.strip().split("?")[0]
    return s.rstrip("/") if s.startswith("http") else f"{BASE}/event/{slug_of(s)}"


def book_by_type(book, side):
    """Aggregate one side of the order book per ticket category."""
    agg = {}
    for o in (book or {}).get(side) or []:
        tt = o.get("ticket_type")
        qty = o.get("qty") or 0
        a = agg.setdefault(tt, {"orders": 0, "qty": 0, "best": None, "best_all_in": None})
        a["orders"] += 1
        a["qty"] += qty
        price, all_in = o.get("price"), o.get("all_in_price")
        if price is None:
            continue
        better = (a["best"] is None
                  or (price < a["best"] if side == "sell" else price > a["best"]))
        if better:
            a["best"], a["best_all_in"] = price, all_in
    return agg


_MONEY = re.compile(r"(\d+(?:\.\d+)?)")


def _money(v):
    """last_sale arrives as "$$87" and friends. Pull the number out."""
    if v is None:
        return None
    m = _MONEY.search(str(v))
    return float(m.group(1)) if m else None


def snapshot(url_or_slug):
    """One reading of an event: metadata + a row per ticket category."""
    url = url_of(url_or_slug)
    payload = to_payload(fetch(url))
    root = _object_at(payload, "initialBook") or {}
    event = root.get("event") or _object_at(payload, "tt_data") or {}
    if not event:
        raise FetchError("no event data in payload (page shape changed?)")

    book = root.get("initialBook") or {}
    sells = book_by_type(book, "sell")
    bids = book_by_type(book, "buy")
    venue = event.get("venue_obj") or {}

    meta = {
        "slug": slug_of(url),
        "url": url,
        "name": event.get("name"),
        "venue": venue.get("name"),
        "venue_slug": venue.get("slug"),
        "city": venue.get("city"),
        "state": venue.get("state"),
        "area": event.get("area_name"),
        "address": venue.get("address"),
        "postal_code": venue.get("postal_code"),
        "lat": venue.get("latitude"),
        "lng": venue.get("longitude"),
        # artwork and line-up: fetched once, and what makes the dashboard
        # look like something rather than a spreadsheet
        "img": event.get("square_img_link") or event.get("img_link"),
        "og_img": event.get("og_img_link"),
        "venue_img": venue.get("image_url"),
        "performers": [{"name": p.get("name"), "slug": p.get("slug"),
                        "img": p.get("image_url")}
                       for p in event.get("performer_bubbles") or []
                       if p.get("is_visible", True)],
        # which platform the ticket actually lives on (DICE, AXS, Posh...)
        # and its id there, which is how face value is looked up
        "platform": event.get("app_name"),
        "dice_id": event.get("dice_event_uqid"),
        "ticket_limit": event.get("ticket_limit"),
        "is_festival": event.get("is_festival"),
        "is_multi_day": event.get("is_multi_day"),
        # free discovery: the page names related events at the same venue
        "related": [e.get("uqid") for e in event.get("discover_more_events") or []
                    if e.get("uqid")],
        "doors_open": event.get("doors_open"),
        # UTC wall-clock start, e.g. "2026-10-25 02:00:00" -- the calendar needs
        # a real instant, not the display string
        "starts_at": event.get("doors_open_time"),
        "doors_close_time": event.get("doors_close_time"),
        "time_zone": event.get("time_zone"),
        "is_past": bool(event.get("is_past")),
        "hours_til_event": event.get("hours_til_event"),
        "tickets_remaining": event.get("tickets_remaining"),
        "listings_count": event.get("looking_to_sell"),
        "bidders_count": event.get("looking_to_go"),
        # event-wide numbers worth a time series of their own
        "event_min_ask": event.get("min_ask"),
        "event_min_ask_all_in": event.get("min_ask_all_in"),
        "event_max_bid": event.get("max_bid"),
        "event_max_bid_all_in": event.get("max_bid_all_in"),
        "event_high_ask": event.get("high_price"),
        "event_high_ask_all_in": event.get("high_price_all_in"),
        # the only trade signal on the page: what someone actually paid
        "last_sale": _money(event.get("last_sale")),
    }

    rows, seen = [], set()
    for t in (event.get("tt_data") or {}).get("types") or []:
        uqid = t.get("uqid")
        if uqid in seen:
            continue
        seen.add(uqid)
        name = t.get("name")
        s, b = sells.get(name, {}), bids.get(name, {})
        rows.append({
            "ticket_type": name,
            "ticket_type_uqid": uqid,
            "visible": t.get("visible"),
            # how many of the promoter's ticket types CrowdVolt folded into
            # this one resale category -- structural evidence for validating a
            # tier match, and unlike price it cannot be gamed by a seller
            "linked_count": len(t.get("linked_tt_uqids") or []),
            # tt_data is the site's own summary; the book is the raw listings.
            # They agree, but tt_data survives a truncated book.
            "best_ask": t.get("lowest_ask_price"),
            "best_ask_all_in": t.get("all_in_lowest_ask_price"),
            "best_ask_qty": t.get("lowest_ask_qty"),
            "best_bid": t.get("highest_bid_price"),
            "best_bid_qty": t.get("highest_bid_qty"),
            # from initialBook.sell / .buy
            "tickets_available": s.get("qty", 0),
            "listings": s.get("orders", 0),
            "bid_qty": b.get("qty", 0),
            "bids": b.get("orders", 0),
        })

    meta["ticket_types"] = rows
    meta["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return meta


def performer(slug):
    """Artist detail. Genre lives here, not on the event page -- so a genre
    filter costs one read per artist, once, rather than per event."""
    payload = to_payload(fetch(f"{BASE}/performer/{slug}", rsc=False))
    obj = _object_at(payload, "genres") or {}
    return {
        "slug": slug,
        "name": obj.get("name"),
        "genres": [g.get("name") for g in obj.get("genres") or [] if g.get("name")],
        "img": obj.get("image_url"),
        "bio": obj.get("bio"),
        "spotify": obj.get("spotify_url"),
        "instagram": obj.get("instagram_url"),
        "monthly_listeners": obj.get("monthly_listeners"),
        "followers": obj.get("follower_count"),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print(snap):
    where = " / ".join(x for x in [snap["venue"], snap["city"]] if x)
    print(f"\n{snap['name']}  --  {where}  --  {snap['doors_open']}")
    for r in snap["ticket_types"]:
        ask = ("no asks" if r["best_ask"] is None
               else f"${r['best_ask']} (${r['best_ask_all_in']} all-in)")
        bid = "-" if r["best_bid"] is None else f"${r['best_bid']}"
        print(f"  {str(r['ticket_type'])[:28]:28}  ask {ask:26}  bid {bid:6}  "
              f"{r['tickets_available']:3} tickets in {r['listings']} listings")


def main(argv):
    args = argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    for t in args:
        try:
            _print(snapshot(t))
        except FetchError as e:
            print(f"!! {slug_of(t)}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
