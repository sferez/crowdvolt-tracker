#!/usr/bin/env python3
"""
Find NYC events and start tracking them, automatically.

CrowdVolt has no event-list endpoint a script can reach, but the sitemap lists
every venue, and a venue page carries its address, coordinates and links to its
upcoming events. So:

    sitemap.xml  ->  venue pages  ->  which venues are in NYC
                                  ->  their event slugs
                 ->  event page (once)  ->  name, date, first reading

Venue -> city is cached for a day and each event page is read exactly once, so
a daily run costs ~133 venue reads plus one read per genuinely new event.

    python3 discover.py                     # every NYC event, no date limit
    python3 discover.py --within-days 60    # only the next two months
    python3 discover.py --dry-run
    python3 discover.py --anywhere          # every city, not just NYC
"""

import argparse
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import crowdvolt
import primary
from store import Store

sys.stdout.reconfigure(line_buffering=True)   # so a cron log streams

# Bounding box over the five boroughs. A box beats a list of place names --
# venues file themselves under Brooklyn, Queens, Ridgewood, Long Island City,
# Astoria, NYC, New York -- and the box catches all of them.
NYC_BBOX = (40.48, 40.93, -74.30, -73.68)   # lat_min, lat_max, lng_min, lng_max
NYC_CITIES = {"new york", "nyc", "brooklyn", "queens", "bronx", "the bronx",
              "manhattan", "staten island", "long island city", "ridgewood",
              "astoria", "flushing", "maspeth", "woodside", "sunnyside"}

VENUE_TTL_HOURS = 24
REQUEST_DELAY = 1.2


def in_nyc(city, state, lat, lng):
    """Either signal is enough; neither gets a veto.

    Coordinates catch the neighbourhoods no name list would (Ridgewood, Forest
    Hills, Long Island City). The city name catches the venues whose
    coordinates CrowdVolt stores rounded to whole degrees -- Outer Heaven and
    The Chocolate Factory are both filed at (41, -74), some 30km north of the
    city -- which is why the box alone silently dropped them.

    OR-ing the two is still tight: the name test reads the venue's own city
    field, not the slug, so "anish-kumar-monarch-sat-sep-19-new-york" is
    correctly rejected on its "San Francisco".
    """
    lo_la, hi_la, lo_ln, hi_ln = NYC_BBOX
    by_box = (lat is not None and lng is not None
              and lo_la <= lat <= hi_la and lo_ln <= lng <= hi_ln)
    by_name = ((city or "").strip().lower() in NYC_CITIES
               and (state or "").strip().upper() in ("NY", "NEW YORK", ""))
    return by_box or by_name


def read_venue(slug):
    """Venue name, address, coordinates and the events it links to."""
    payload = crowdvolt.to_payload(
        crowdvolt.fetch(f"{crowdvolt.BASE}/venue/{slug}", rsc=False))
    obj = crowdvolt._object_at(payload, "coordinates") or {}
    coords = obj.get("coordinates") or {}
    address = obj.get("address") or {}
    return {
        "slug": slug, "name": obj.get("name"),
        "city": address.get("city"), "state": address.get("state"),
        "lat": coords.get("lat"), "lng": coords.get("lng"),
        "events": sorted(set(re.findall(r"/event/([a-z0-9][a-z0-9\-]+)", payload))),
    }


def sitemap(kind):
    xml = crowdvolt.fetch(crowdvolt.SITEMAP, rsc=False)
    return sorted(set(re.findall(
        rf"<loc>https://www\.crowdvolt\.com/{kind}/([^<]+)</loc>", xml)))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--within-days", type=int, default=0,
                   help="only track events this many days out (0 = no limit, the default)")
    p.add_argument("--anywhere", action="store_true", help="skip the NYC filter")
    p.add_argument("--dry-run", action="store_true", help="show what would be added")
    p.add_argument("--refresh-venues", action="store_true", help="ignore the venue cache")
    p.add_argument("--skip-artists", action="store_true",
                   help="do not look up genres for new artists")
    p.add_argument("--eventbrite", action="store_true",
                   help="also read face value from Eventbrite (rate-limited, "
                        "and its availability flag is unreliable)")
    p.add_argument("--max-new", type=int, default=400,
                   help="stop after this many new events (default 400)")
    args = p.parse_args()

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds")
    st = Store()
    cache = st.venues()
    venues, venue_events = cache["venues"], cache["venue_events"]

    all_slugs = sitemap("venue")
    cutoff = (now_dt - timedelta(hours=0 if args.refresh_venues else VENUE_TTL_HOURS)).isoformat()
    stale = [s for s in all_slugs
             if (venues.get(s) or {}).get("checked_at", "") <= cutoff]
    print(f"{len(all_slugs)} venues in the sitemap, {len(stale)} to (re)read")

    for i, slug in enumerate(stale):
        if i:
            time.sleep(REQUEST_DELAY)
        try:
            v = read_venue(slug)
        except crowdvolt.FetchError as e:
            print(f"  !! venue {slug}: {e}", file=sys.stderr)
            continue
        v["in_area"] = bool(args.anywhere or in_nyc(v["city"], v["state"], v["lat"], v["lng"]))
        v["checked_at"] = now
        venue_events[slug] = v.pop("events")
        venues[slug] = v
        if v["in_area"]:
            print(f"  {v['city']}, {v['state']}: {v['name']} -- {len(venue_events[slug])} events")
    if not args.dry_run:
        st.save_venues({"venues": venues, "venue_events": venue_events})

    # The sitemap is the only complete list. Venue pages miss events at venues
    # that have no venue page of their own (Randall's Island Park, say), so the
    # sitemap's own event list is the ground truth and the venue crawl and the
    # "related events" strip are just extra leads on top.
    wanted = set(sitemap("event"))
    wanted |= {e for slug, evs in venue_events.items()
               if (venues.get(slug) or {}).get("in_area") for e in evs}
    for e in st.events.values():
        wanted.update(e.get("related") or [])
    skipped = st.skipped()
    candidates = sorted(wanted - set(st.events) - set(skipped))
    print(f"{len(candidates)} candidate event(s) not yet known"
          + (f" ({len(skipped)} already checked and outside the area)" if skipped else ""))

    horizon = args.within_days * 24 if args.within_days else None
    added = deferred = rejected = 0
    for i, slug in enumerate(candidates[:args.max_new]):
        if i:
            time.sleep(REQUEST_DELAY)
        try:
            snap = crowdvolt.snapshot(slug)
        except crowdvolt.FetchError as e:
            print(f"  !! {slug}: {e}", file=sys.stderr)
            continue
        hours = snap.get("hours_til_event")
        days = round((hours or 0) / 24)
        # NYC-ness comes from the event's own venue coordinates, not from
        # whether we happened to crawl its venue page -- and not from the slug
        # either: "anish-kumar-monarch-sat-sep-19-new-york" is in San Francisco.
        here = args.anywhere or in_nyc(snap.get("city"), snap.get("state"),
                                       snap.get("lat"), snap.get("lng"))
        if not here:
            # Record and move on before doing any metadata or artwork work --
            # none of it would ever be used. One line in skipped.json is the
            # whole memory we need to never read this page again.
            print(f"    {slug[:56]:56} {snap.get('city') or 'unknown city'} — skipped")
            if not args.dry_run:
                skipped[slug] = {"city": snap.get("city"), "state": snap.get("state"),
                                 "checked_at": now}
                st.save_skipped(skipped)
            rejected += 1
            continue

        if snap["is_past"] or (hours is not None and hours <= 0):
            status, note = "past", "already happened"
        elif horizon and hours is not None and hours > horizon:
            status, note = "deferred", f"{days}d out, outside the {args.within_days}d window"
        else:
            status, note = "active", f"in {days}d"
        print(f"  {'+ ' if status == 'active' else '  '}{slug[:56]:56} {note}")
        if args.dry_run:
            continue
        st.ensure(slug, snap["url"], now)
        st.update_meta(slug, snap, now)
        st.events[slug]["related"] = snap.get("related") or []
        face = primary.lookup(snap, expect_date=st.events.get(slug, {}).get("local_date"))
        if face:
            face["checked_at"] = now
            st.events[slug]["primary"] = face
        mirrored = st.mirror_image(slug, snap.get("img"))
        if mirrored:
            st.events[slug]["img_blob"] = mirrored
        if status == "active":
            # the page is already in hand -- store the reading rather than
            # making track.py fetch it again an hour from now
            st.add_snapshot(slug, snap["fetched_at"], snap["ticket_types"], snap)
            added += 1
        else:
            st.retire(slug, f"{status}: {note}", now)
            deferred += 1
        st.save()

    if not args.dry_run:
        resolve_primary_ids(st)
        refresh_primary(st, now, eventbrite=args.eventbrite)
    if not (args.dry_run or args.skip_artists):
        enrich_genres(st)

    promoted = 0 if args.dry_run else promote_deferred(st, horizon, now)
    print(f"\n{added} now tracked, {deferred} deferred, {rejected} outside NYC"
          + (f", {promoted} deferred event(s) came into range" if promoted else ""))
    if not args.dry_run:
        # the one place per-event objects are written -- everything else in the
        # day appended to the buffer instead
        folded = st.consolidate()
        if folded:
            print(f"consolidated {folded} event(s) into their history files")
        st.save()
        sent = st.push()
        if sent:
            print(f"pushed {len(sent)} file(s) to the store")
    return st


def resolve_primary_ids(st):
    """Fill in DICE ids CrowdVolt did not store.

    Roughly half the events CrowdVolt labels DICE carry no `dice_event_uqid`,
    which would leave them without a face value over a missing field rather
    than anything real. Matched on venue and local date, and only when exactly
    one candidate survives -- three Crankdat nights at one venue is the normal
    case, so a near-miss must yield nothing rather than the wrong night.
    """
    # a stored id that is not a 24-character ObjectId never resolves -- two
    # events carry 6-character codes the API rejects outright -- so treat those
    # as missing and let the search have a go
    def usable(i):
        return bool(i) and len(str(i)) == 24
    gaps = [s for s, e in st.events.items()
            if e.get("status") == "active" and not usable(e.get("dice_id"))
            and e.get("platform") == "DICE"]
    if not gaps:
        return 0
    print(f"looking up {len(gaps)} missing DICE id(s)")
    found = 0
    for i, slug in enumerate(gaps):
        if i:
            time.sleep(0.8)
        e = st.events[slug]
        did = primary.dice_find(e.get("name"), e.get("venue"), e.get("local_date"))
        if did:
            e["dice_id"] = did
            found += 1
    print(f"recovered {found} id(s)")
    return found


def refresh_primary(st, now, eventbrite=False):
    """Re-read face value for tracked events.

    Tiers sell out as an event approaches, so the "cheapest you can still buy
    on the primary" moves -- it is the number worth comparing a resale listing
    against, and a stale one is worse than none. Events with no readable
    platform are skipped without a request.
    """
    # Eventbrite is off by default. It works, but on two shakier footings than
    # DICE: its JSON-LD advertises "InStock" on pages that say sold out, and it
    # needs a search plus a page fetch per candidate -- about fifteen requests
    # an event -- which earns a 429 well before a full pass finishes. Enable it
    # with --eventbrite once those are solved.
    todo = [s for s, e in st.events.items()
            if e.get("status") == "active"
            and (e.get("dice_id")
                 or (eventbrite and (e.get("platform") or "").lower() == "eventbrite"))]
    if not todo:
        return 0
    print(f"refreshing face value for {len(todo)} event(s)")
    changed = 0
    for i, slug in enumerate(todo):
        if i:
            time.sleep(0.4)          # DICE is a different host and a light call
        e = st.events[slug]
        if e.get("dice_id"):
            face = primary.dice_tiers(e["dice_id"], expect_date=e.get("local_date"))
            if face:
                face["id"] = e["dice_id"]
        else:
            # Eventbrite has no id in CrowdVolt's payload, so it is found by
            # name, venue and date every time -- slower, hence the longer pause
            face = primary.eventbrite_tiers(e.get("name"), e.get("venue"),
                                            e.get("local_date"))
            time.sleep(1.0)
        if not face:
            continue
        face["checked_at"] = now
        # a fair value per resale category, not just one for the whole event:
        # comparing a VIP listing against the cheapest GA tier is worse than
        # showing nothing
        h = st.history(slug)
        face["by_category"] = {
            c: primary.fair_value(face, c, t.get("linked_count"))
            for c, t in (h.get("types") or {}).items()}
        was = (e.get("primary") or {}).get("on_sale")
        e["primary"] = face
        # keep the dashboard's cached numbers in step
        st._refresh_current(slug, h)
        if was != face.get("on_sale"):
            changed += 1
    st.save()
    print(f"{changed} event(s) changed price on the primary")
    return changed


# CrowdVolt's artist pages spell the same genre several ways -- "house" and
# "House", "UK garage", "UK-garage" and "uk garage" -- and the dashboard
# filters on the literal string, so a house chip counted 13 events while three
# more sat under a separate "House" chip. Fold them before storing.
_GENRE_TYPOS = {"techo": "techno", "pregressive house": "progressive house",
                "pregressive breaks": "progressive breaks"}


def norm_genre(g):
    g = re.sub(r"\s+", " ", (g or "").strip().lower().replace("-", " ")
               .replace("&", "and"))
    return _GENRE_TYPOS.get(g, g)


# CrowdVolt's artists carry 96 distinct genres, 62 of which appear exactly
# once. That is a long tail, not a taxonomy: it splits one scene across chips
# nobody would think to click. These fold it into eight groups -- the number of
# colours the palette can keep distinguishable for a colourblind reader, so the
# ceiling is a real one rather than a preference.
#
# Order matters: the first pattern that matches wins, so the specific families
# ("tech house", "melodic techno") are tested before the broad ones.
GENRE_GROUPS = [
    ("tech house", r"tech house|deep tech|afro tech|\btech\b"),
    ("techno",     r"techno|hard ?groove|\bminimal\b"),
    ("house",      r"house|disco"),
    ("bass",       r"dubstep|riddim|drum and bass|\bdnb\b|neurofunk|deathstep|"
                   r"bass music|melodic bass|experimental bass|festival bass|"
                   r"future bass|\btrap\b|bassline"),
    ("garage",     r"garage"),
    ("trance",     r"trance|hard ?style|hardcore|hard bounce|\brave\b|"
                   r"eurodance|\bebm\b|industrial"),
    # electronic is tested BEFORE edm so that "indie dance" is read as indie
    # rather than as dance -- the bare word is doing too much work otherwise
    ("electronic",   r"electro|breakbeat|breaks|indie|electronica|downtempo|"
                     r"organic|chill|\bnu disco\b|experimental|ambient"),
    # festival-scale dance music, kept apart from that broader tail: breakbeat
    # and downtempo are not what anyone means by EDM
    ("edm festival", r"\bedm\b|big room|festival|\bdance\b|hard bounce"),
]


def genre_group(genre):
    """The broad scene a genre belongs to, or None if it has no genre."""
    if not genre:
        return None
    g = norm_genre(genre)
    for name, pattern in GENRE_GROUPS:
        if re.search(pattern, g):
            return name
    return "other"


def enrich_genres(st):
    """Genre lives on the artist page, so read each artist once and hang the
    result on every event they play."""
    cache = st.artists()
    wanted = {p["slug"] for e in st.events.values()
              for p in e.get("performers") or [] if p.get("slug")}
    missing = sorted(wanted - set(cache))
    if missing:
        print(f"{len(missing)} artist(s) to look up")
    for i, slug in enumerate(missing):
        if i:
            time.sleep(REQUEST_DELAY)
        try:
            cache[slug] = crowdvolt.performer(slug)
        except crowdvolt.FetchError as e:
            print(f"  !! artist {slug}: {e}", file=sys.stderr)
            cache[slug] = {"slug": slug, "genres": []}
    st.save_artists(cache)

    tagged = 0
    for e in st.events.values():
        genres, listeners = [], None
        for p in e.get("performers") or []:
            a = cache.get(p.get("slug")) or {}
            for g in a.get("genres") or []:
                g = norm_genre(g)
                if g and g not in genres:
                    genres.append(g)
            if a.get("monthly_listeners"):
                listeners = max(listeners or 0, a["monthly_listeners"])
        if genres:
            e["genres"] = genres
            # the chips, colours and filters all key off this one field
            e["genre_group"] = genre_group(genres[0])
            tagged += 1
        if listeners:
            e["monthly_listeners"] = listeners
    # normalise what is already stored too: an event whose artist never made it
    # into the cache keeps whatever it was first tagged with, which is how one
    # "Dubstep" survived a pass that folded every other spelling
    for e in st.events.values():
        g = [norm_genre(x) for x in (e.get("genres") or [])]
        if g:
            e["genres"] = list(dict.fromkeys(g))
            e["genre_group"] = genre_group(g[0])

    print(f"{tagged} event(s) tagged with a genre")
    return tagged


def promote_deferred(st, horizon_hours, now):
    """Events parked outside the window start tracking once they come into it.
    Their date is already stored, so this costs no requests."""
    if not horizon_hours:
        horizon_hours = 1 << 20
    limit = (datetime.now(timezone.utc) + timedelta(hours=horizon_hours)).timestamp()
    n = 0
    for slug, e in st.events.items():
        if e.get("status") != "deferred" or not e.get("starts_ts"):
            continue
        if datetime.now(timezone.utc).timestamp() < e["starts_ts"] <= limit:
            st.revive(slug)
            n += 1
    return n


if __name__ == "__main__":
    main()
