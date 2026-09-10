#!/usr/bin/env python3
"""
Pull the whole data store out of Blob into a local directory.

The object store keeps no version history: one bad write and the readings are
gone. A daily snapshot committed to an orphan `data` branch gives a durable,
diffable backup for about 64KB gzipped a day -- and, incidentally, keeps the repo active
enough that GitHub does not disable the scheduled workflows.

Artwork is deliberately not snapshotted. It is static, re-mirrorable from the
source at any time, and would add megabytes a week for nothing.

    python3 snapshot.py                       # Blob -> snapshot/
    python3 snapshot.py --out backup/
    python3 snapshot.py --restore backup/     # backup/ -> Blob
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import r2


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="snapshot", help="directory to write into")
    p.add_argument("--restore", metavar="DIR",
                   help="push a snapshot directory back into Blob (asks first)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = p.parse_args()

    remote = r2.Remote()
    if not remote.enabled:
        sys.exit("no Blob credentials -- nothing to snapshot")

    if args.restore:
        return restore(remote, Path(args.restore), args.yes)

    out = Path(args.out)
    (out / "events").mkdir(parents=True, exist_ok=True)

    paths = [p for p in remote.list() if p.endswith(".json")]
    print(f"{len(paths)} JSON object(s) in the store")

    written = skipped = 0
    for rel in sorted(paths):
        data = remote.get(rel, fresh=True)
        if data is None:
            print(f"  !! could not read {rel}", file=sys.stderr)
            skipped += 1
            continue
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # pretty-printed here, unlike the compact copies in Blob: this one is
        # read by humans and diffed by git, where one value per line is worth
        # the extra bytes
        dest.write_text(json.dumps(data, indent=1, sort_keys=True))
        written += 1

    events = json.loads((out / "index.json").read_text()).get("events", {}) \
        if (out / "index.json").exists() else {}
    readings = sum(len(json.loads(f.read_text()).get("stamps", []))
                   for f in (out / "events").glob("*.json"))
    manifest = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": written,
        "events": len(events),
        "readings": readings,
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {written} file(s) to {out}/ "
          f"({len(events)} events, {readings} readings)"
          + (f", {skipped} unreadable" if skipped else ""))


def restore(remote, src, assume_yes):
    """Push a snapshot back into Blob.

    A backup you have never restored from is a guess, not a backup. This is
    the other half: point it at a checkout of the `data` branch and it puts
    those files back. Deliberately manual and confirmed -- it overwrites live
    readings, and nothing should ever call it on a schedule.
    """
    files = sorted(p for p in src.rglob("*.json") if p.name != "MANIFEST.json")
    if not files:
        sys.exit(f"no JSON files under {src}/")

    manifest = src / "MANIFEST.json"
    if manifest.exists():
        m = json.loads(manifest.read_text())
        print(f"snapshot taken {m.get('taken_at')}: "
              f"{m.get('events')} events, {m.get('readings')} readings")

    live = json.loads(json.dumps(remote.get("index.json", fresh=True) or {}))
    print(f"about to overwrite the live store ({len(live.get('events', {}))} events) "
          f"with {len(files)} file(s) from {src}/")
    if not assume_yes and input("type 'restore' to confirm: ").strip() != "restore":
        sys.exit("cancelled")

    for f in files:
        rel = str(f.relative_to(src))
        remote.put(rel, json.loads(f.read_text()))
        print(f"  restored {rel}")
    print(f"restored {len(files)} file(s)")


if __name__ == "__main__":
    main()
