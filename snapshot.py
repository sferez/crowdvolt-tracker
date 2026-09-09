#!/usr/bin/env python3
"""
Pull the whole data store out of Blob into a local directory.

Blob keeps no version history: one bad write and the readings are gone. A
daily snapshot committed to an orphan `data` branch gives a durable, diffable
backup for about 64KB gzipped a day -- and, incidentally, keeps the repo active
enough that GitHub does not disable the scheduled workflows.

Artwork is deliberately not snapshotted. It is static, re-mirrorable from the
source at any time, and would add megabytes a week for nothing.

    python3 snapshot.py                 # -> snapshot/
    python3 snapshot.py --out backup/
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import blob


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="snapshot", help="directory to write into")
    args = p.parse_args()

    remote = blob.Remote()
    if not remote.enabled:
        sys.exit("no Blob credentials -- nothing to snapshot")

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


if __name__ == "__main__":
    main()
