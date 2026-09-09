#!/usr/bin/env python3
"""
Hourly CrowdVolt tracker.

  1. reads watchlist.txt (paste event URLs in there, one per line)
  2. reads every event that is due -- a show this week every hour, one this
     month every 3 hours, anything further out every 6 (see store.TIERS)
  3. appends best ask + tickets available, per ticket category, to
     public/data/events/<slug>.json
  4. retires events that are past or have stopped selling
  5. rebuilds public/index.html

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
from store import Store
from dashboard import build_dashboard

sys.stdout.reconfigure(line_buffering=True)   # so a cron / CI log streams

HERE = Path(__file__).parent
WATCHLIST = HERE / "watchlist.txt"
PUBLIC = HERE / "public"
DASHBOARD = PUBLIC / "index.html"

REQUEST_DELAY = 2.0      # seconds between events, be a good citizen
EMPTY_RUNS_BEFORE_RETIRE = 6   # ~6h of zero listings before we call it dead
ERROR_RUNS_BEFORE_RETIRE = 12  # ~12h of failures before we give up

WATCHLIST_TEMPLATE = """\
# CrowdVolt watchlist -- paste event URLs here, one per line.
# Anything added is picked up on the next run; lines starting with # are ignored.
# Events that are past or have stopped selling are retired automatically,
# you do not need to remove them from this file.
#
# https://www.crowdvolt.com/event/crankdat-brooklyn-storehouse-new-york-brooklyn-october-25-2026
"""


def read_watchlist():
    if not WATCHLIST.exists():
        WATCHLIST.write_text(WATCHLIST_TEMPLATE)
        print(f"created {WATCHLIST.name} -- paste event URLs into it", file=sys.stderr)
        return []
    out = []
    for line in WATCHLIST.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(crowdvolt.url_of(line))
    return list(dict.fromkeys(out))


def sync_watchlist(st, now, write=True):
    """New URLs in the file start being tracked. A retired event is NOT revived
    just because its line is still in the file -- the file is meant to be left
    alone, so auto-revival would put a dead or mistyped URL into an endless
    retire/revive loop. Use --revive <slug> to pick one back up."""
    added = []
    for url in read_watchlist():
        slug = crowdvolt.slug_of(url)
        if slug not in st.events:
            added.append(slug)
            if write:
                st.ensure(slug, url, now)
    if write:
        st.save()
    return added


def revive(st, slug):
    was = st.revive(slug)
    if was is None:
        print(f"not tracked: {slug}", file=sys.stderr)
        return
    st.save()
    print(f"tracking {slug} again (was {was[0]}: {was[1]})")


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

    added = sync_watchlist(st, now, write=not dry_run)
    for slug in added:
        print(f"+ now tracking {slug}")

    active = st.active()
    slugs = active if everything else st.due(started)
    if not active:
        print("nothing to track -- add event URLs to watchlist.txt or run discover.py")
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
        print(f"pushed {len(sent)} file(s) to Blob")
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
    p.add_argument("--dashboard", action="store_true", help="rebuild dashboard.html only")
    p.add_argument("--status", action="store_true", help="list what is tracked")
    p.add_argument("--revive", metavar="SLUG", help="resume tracking a retired event")
    p.add_argument("--delay", type=float, default=REQUEST_DELAY,
                   help=f"seconds between events (default {REQUEST_DELAY})")
    p.add_argument("--all", action="store_true",
                   help="read every active event, ignoring the read-frequency tiers")
    p.add_argument("--serve", action="store_true", help="serve public/ and open it")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()

    if args.serve:
        serve(args.port)
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
