#!/usr/bin/env python3
"""
Hourly CrowdVolt tracker.

  1. reads every active event -- hourly, all of them. Resale prices are set by
     individual sellers and can move at any time.
  2. appends best ask + tickets available, per ticket category, to
     events/<slug>.json
  3. retires events that are past or have stopped selling
  4. rebuilds public/index.html

discover.py is what puts events in the store in the first place.

Run it hourly (see README). Safe to run by hand any time.

    python3 track.py                 # normal run
    python3 track.py --dry-run       # fetch + print, write nothing
    python3 track.py --dashboard     # rebuild the dashboard only
    python3 track.py --status        # what is being tracked
    python3 track.py --revive SLUG   # resume a retired event
    python3 track.py --serve         # open the dashboard locally
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import crowdvolt
import primary
from store import Store
from dashboard import build_dashboard

sys.stdout.reconfigure(line_buffering=True)   # so a cron / CI log streams

HERE = Path(__file__).parent
PUBLIC = HERE / "public"
DASHBOARD = PUBLIC / "index.html"

REQUEST_DELAY = 2.0      # seconds between events, be a good citizen

# How stale a face value may get before it is re-read. Unlike a resale ask, a
# primary price only moves when a tier sells out -- slowly, then all at once as
# the date approaches -- so this stays on a distance-based window while the
# resale side is read hourly. These reads ride along with a price read we are
# making anyway.
FACE_TTL_HOURS = 24
FACE_TTL_HOURS_SOON = 6
SOON_DAYS = 7
EMPTY_RUNS_BEFORE_RETIRE = 6   # ~6h of zero listings before we call it dead
ERROR_RUNS_BEFORE_RETIRE = 12  # ~12h of failures before we give up

def revive(st, slug):
    was = st.revive(slug)
    if was is None:
        print(f"not tracked: {slug}", file=sys.stderr)
        return
    st.save()
    print(f"tracking {slug} again (was {was[0]}: {was[1]})")


def face_is_stale(e, started):
    """True when this event's primary prices are old enough to be worth
    re-reading. An event a week out can sell through a tier in an afternoon;
    one three months out will not move for weeks."""
    face = e.get("primary") or {}
    if not e.get("dice_id"):
        return False
    checked = face.get("checked_at")
    if not checked:
        return True
    days_out = ((e.get("starts_ts") or 0) - started.timestamp()) / 86400
    ttl = FACE_TTL_HOURS_SOON if days_out <= SOON_DAYS else FACE_TTL_HOURS
    try:
        age = (started - datetime.fromisoformat(checked)).total_seconds() / 3600
    except (ValueError, TypeError):
        return True
    return age >= ttl


def refresh_face(st, slug, snap, started, now):
    """Re-read the primary's prices when ours have gone stale.

    Face value moves when a tier sells out and the next one opens dearer -- so
    it climbs over time, and the right thing is to take the current price
    rather than remember the cheapest we ever saw. "What would I pay to buy
    this new today" is the question a resale price is being judged against;
    the cheapest tier ever offered is kept separately as `original`.
    """
    e = st.events[slug]
    if not face_is_stale(e, started):
        return
    face = primary.dice_tiers(e["dice_id"], expect_date=e.get("local_date"))
    if not face:
        return
    face["id"] = e["dice_id"]
    face["checked_at"] = now
    face["by_category"] = {
        row["ticket_type"]: primary.fair_value(face, row["ticket_type"],
                                               row.get("linked_count"))
        for row in snap.get("ticket_types") or []}
    e["primary"] = face


def retirement_reason(snap, empty_streak, ever_had_tickets):
    if snap["is_past"]:
        return "past: event date has passed"
    hours = snap.get("hours_til_event")
    if hours is not None and hours <= 0:
        return "past: doors have opened"
    # "stopped selling" means it sold before. An event added months early, with
    # nothing listed yet, is not sold out -- keep watching it.
    if ever_had_tickets and empty_streak >= EMPTY_RUNS_BEFORE_RETIRE:
        return f"sold_out: no listings for {empty_streak} consecutive runs"
    return None


def run(dry_run=False, delay=REQUEST_DELAY, everything=False):
    started = datetime.now(timezone.utc)
    now = started.isoformat(timespec="seconds")
    st = Store()
    active = st.active()
    slugs = active if everything else st.due(started)
    if not active:
        print("nothing to track -- run discover.py first")
        return st
    if not slugs:
        print(f"[{now}] {len(active)} active, none due this run")
        return st

    print(f"[{now}] {len(slugs)} of {len(active)} active event(s) due")
    for i, slug in enumerate(slugs):
        if i:
            time.sleep(delay)
        try:
            snap = crowdvolt.snapshot(slug)
        except crowdvolt.FetchError as e:
            # Log and move on -- never retry behind a different identity.
            streak = st.bump(slug, "error_streak")
            st.mark_read(slug, now)
            print(f"  !! {slug}: {e} (failure {streak})", file=sys.stderr)
            if streak >= ERROR_RUNS_BEFORE_RETIRE:
                st.retire(slug, f"gone: {ERROR_RUNS_BEFORE_RETIRE} consecutive failures", now)
                print(f"  -- retired {slug}: unreachable", file=sys.stderr)
            st.save()
            continue

        rows = snap["ticket_types"]
        total = sum(r["tickets_available"] for r in rows)
        best = [r for r in rows if r["best_ask"] is not None]
        summary = ", ".join(f"{r['ticket_type']} ${r['best_ask_all_in']}"
                            f" x{r['tickets_available']}" for r in best) or "no asks"
        print(f"  {slug[:52]:52} {summary}")

        if dry_run:
            continue

        st.update_meta(slug, snap, now)
        st.events[slug]["related"] = snap.get("related") or []
        refresh_face(st, slug, snap, started, now)
        if snap.get("img") and not st.events[slug].get("img_blob"):
            mirrored = st.mirror_image(slug, snap["img"])
            if mirrored:
                st.events[slug]["img_blob"] = mirrored
        st.add_snapshot(slug, snap["fetched_at"], rows, snap)
        empty = st.bump(slug, "empty_streak", reset=total > 0)
        reason = retirement_reason(snap, empty, st.events[slug].get("ever_had_tickets"))
        if reason:
            st.retire(slug, reason, now)
            print(f"  -- retired {slug}: {reason}")
        st.save()

    st.save()
    sent = st.push()
    if sent:
        print(f"pushed {len(sent)} file(s) to the store")
    return st


def print_status(st):
    now = datetime.now(timezone.utc)
    due = set(st.due(now))
    rows = sorted(st.events.items(), key=lambda kv: (kv[1].get("starts_ts") or 1 << 62))
    for slug, e in rows:
        n = len(st.history(slug)["stamps"])
        mark = "*" if slug in due else " "
        days = e.get("starts_ts") and round((e["starts_ts"] - now.timestamp()) / 86400)
        note = "" if e.get("status") == "active" else f"  ({e.get('retired_reason')})"
        print(f"{mark} {e.get('status','?'):9} {str(days) + 'd' if days is not None else '   ':>5}"
              f" {n:4} readings  {e.get('name') or slug}{note}")
    print(f"\n{len(st.active())} active, {len(due)} due now, {len(st.events)} total")


def serve(port=8000):
    import http.server, functools, webbrowser
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(PUBLIC))
    print(f"serving {PUBLIC} at http://127.0.0.1:{port}  (ctrl-c to stop)")
    webbrowser.open(f"http://127.0.0.1:{port}")
    http.server.HTTPServer(("127.0.0.1", port), handler).serve_forever()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="fetch and print, write nothing")
    p.add_argument("--dashboard", action="store_true", help="rebuild public/index.html only")
    p.add_argument("--status", action="store_true", help="list what is tracked")
    p.add_argument("--revive", metavar="SLUG", help="resume tracking a retired event")
    p.add_argument("--delay", type=float, default=REQUEST_DELAY,
                   help=f"seconds between events (default {REQUEST_DELAY})")
    p.add_argument("--all", action="store_true",
                   help="read every active event, even one read minutes ago")
    p.add_argument("--consolidate", action="store_true",
                   help="fold the buffer into the per-event history files")
    p.add_argument("--serve", action="store_true", help="serve public/ and open it")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()

    if args.serve:
        serve(args.port)
        return

    if args.consolidate:
        st = Store()
        print(f"consolidated {st.consolidate()} event(s)")
        st.save()
        st.push()
        return

    if args.revive:
        revive(Store(), crowdvolt.slug_of(args.revive))
        return

    if args.dashboard or args.status:
        st = Store()
        if args.status:
            print_status(st)
        if args.dashboard:
            build_dashboard(st, DASHBOARD)
            print(f"wrote {DASHBOARD}")
        return

    st = run(dry_run=args.dry_run, delay=args.delay, everything=args.all)
    if not args.dry_run:
        build_dashboard(st, DASHBOARD)
        print(f"wrote {DASHBOARD}")


if __name__ == "__main__":
    main()
