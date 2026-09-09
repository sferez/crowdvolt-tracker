"""Vercel Blob as the data store.

The site is static and never redeploys; the hourly job reads the previous
state out of Blob, appends one reading, and writes the changed files back.
Git holds the code, Blob holds the data, and neither one has to churn for the
other.

Auth comes from BLOB_READ_WRITE_TOKEN in the environment (a .env.local file
next to this one is loaded if present). Nothing here writes a token anywhere.
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://blob.vercel-storage.com"
API_VERSION = "7"
PREFIX = "crowdvolt/"

# The state files are rewritten every run and read back by the next one, so
# they get max-age 0 -- not a short TTL. A 60s TTL still lets a run started
# inside that window read the copy it is about to replace, which is exactly
# how four events got silently dropped from the roster once.
CACHE_SECONDS = {"index.json": 0, "skipped.json": 0,
                 "venues.json": 0, "artists.json": 0}
DEFAULT_CACHE_SECONDS = 300


def load_env(path=None):
    """Read .env.local into os.environ without overwriting real env vars."""
    path = Path(path or Path(__file__).parent / ".env.local")
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def token():
    load_env()
    return os.environ.get("BLOB_READ_WRITE_TOKEN")


def public_base():
    """The public https://<store>.public.blob.vercel-storage.com/ base."""
    load_env()
    base = os.environ.get("BLOB_PUBLIC_BASE")
    if base:
        return base.rstrip("/") + "/"
    store = (os.environ.get("BLOB_STORE_ID") or "").replace("store_", "")
    return f"https://{store.lower()}.public.blob.vercel-storage.com/" if store else None


class Remote:
    """Thin Blob client. Absent credentials, every method is a no-op so the
    tracker keeps working purely locally."""

    def __init__(self, prefix=PREFIX):
        self.prefix = prefix
        self.tok = token()
        self.base = public_base()
        self.enabled = bool(self.tok and self.base)

    # ------------------------------------------------------------------ read

    def get(self, rel, fresh=False):
        """Public read -- no auth needed, and CDN-cached.

        `fresh` appends a cache-buster: a request header alone does not
        reliably miss a CDN edge, and reading a stale state file means a run
        appends to data it has already superseded.
        """
        if not self.enabled:
            return None
        url = self.base + self.prefix + rel
        if fresh:
            url += f"?_={int(time.time())}"
        try:
            req = urllib.request.Request(url, headers={"cache-control": "no-cache"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError,
                json.JSONDecodeError, TimeoutError):
            return None

    def list(self):
        if not self.enabled:
            return []
        out, cursor = [], None
        while True:
            url = f"{API}/?prefix={self.prefix}&limit=1000" + (f"&cursor={cursor}" if cursor else "")
            req = urllib.request.Request(url, headers={
                "authorization": f"Bearer {self.tok}", "x-api-version": API_VERSION})
            with urllib.request.urlopen(req, timeout=30) as r:
                page = json.loads(r.read().decode())
            out += [b["pathname"][len(self.prefix):] for b in page.get("blobs", [])]
            cursor = page.get("cursor")
            if not page.get("hasMore"):
                return out

    # ----------------------------------------------------------------- write

    def put_bytes(self, rel, data, content_type, max_age=31536000):
        """Upload raw bytes (event artwork). Immutable, so cache hard."""
        if not self.enabled:
            return None
        req = urllib.request.Request(
            f"{API}/{self.prefix}{rel}", data=data, method="PUT",
            headers={
                "authorization": f"Bearer {self.tok}",
                "x-api-version": API_VERSION,
                "x-content-type": content_type,
                "x-add-random-suffix": "0",
                "x-cache-control-max-age": str(max_age),
            })
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())["url"]

    def put(self, rel, payload):
        if not self.enabled:
            return None
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        req = urllib.request.Request(
            f"{API}/{self.prefix}{rel}", data=body, method="PUT",
            headers={
                "authorization": f"Bearer {self.tok}",
                "x-api-version": API_VERSION,
                "x-content-type": "application/json",
                "x-add-random-suffix": "0",          # stable, predictable URLs
                "x-cache-control-max-age": str(
                    CACHE_SECONDS.get(rel, DEFAULT_CACHE_SECONDS)),
            })
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())["url"]

    def delete(self, rels):
        if not self.enabled or not rels:
            return
        body = json.dumps({"urls": [self.base + self.prefix + r for r in rels]}).encode()
        req = urllib.request.Request(
            f"{API}/delete", data=body, method="POST",
            headers={"authorization": f"Bearer {self.tok}",
                     "x-api-version": API_VERSION,
                     "content-type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
