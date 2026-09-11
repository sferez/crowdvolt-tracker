"""Cloudflare R2 object store.

R2 speaks S3, so this is a small SigV4 signer rather than a client library --
the project has no dependencies and adding boto3 to sign four kinds of request
is not worth it.

Why R2 and not the Vercel Blob store this replaced: the workload is
write-heavy and tiny. Blob's Hobby plan allows 2,000 writes a month, which an
hourly tracker exhausts in half a day; R2 allows a million and charges nothing
for egress.

Reads go to the bucket's public URL, unsigned and CDN-cached. Only writes are
signed, so a browser can fetch the data with no credentials anywhere near it.

Environment:
    R2_ACCOUNT_ID          from the Cloudflare dashboard
    R2_ACCESS_KEY_ID       an R2 API token with Object Read & Write
    R2_SECRET_ACCESS_KEY
    R2_BUCKET              the bucket name
    R2_PUBLIC_BASE         its public URL (r2.dev subdomain or custom domain)
"""

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# Cloudflare's WAF rejects urllib's default User-Agent on a custom domain, so
# reads identify themselves. Writes are signed and go to the S3 endpoint, which
# does not care.
UA = "crowdvolt-tracker/1.0"

REGION = "auto"
SERVICE = "s3"
PREFIX = "crowdvolt/"

# Data files are read back by the next run, so they must not sit in a cache.
# History and artwork are immutable once written.
CACHE_SECONDS = {"index.json": 0, "recent.json": 0, "skipped.json": 0,
                 "venues.json": 0, "artists.json": 0}
DEFAULT_CACHE_SECONDS = 300


def load_env(path=None):
    """Read .env.local into os.environ without overwriting real env vars."""
    path = Path(path or Path(__file__).parent / ".env.local")
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _sign(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


class Remote:
    """Thin R2 client. Absent credentials every method is a no-op, so the
    tracker keeps working purely locally."""

    def __init__(self, prefix=PREFIX, env=""):
        """`env` selects a second set of credentials -- `env="CHAT_"` reads
        R2_CHAT_BUCKET and friends. R2 tokens are scoped per bucket, not per
        prefix, so the only way to stop the public chat endpoint from being
        able to rewrite the price history is to give it a different bucket
        and a token that reaches nothing else. The account id is shared."""
        load_env()
        self.prefix = prefix
        g = lambda k: os.environ.get(f"R2_{env}{k}")
        self.account = g("ACCOUNT_ID") or os.environ.get("R2_ACCOUNT_ID")
        self.key = g("ACCESS_KEY_ID")
        self.secret = g("SECRET_ACCESS_KEY")
        self.bucket = g("BUCKET")
        # tolerate a bare hostname: the Cloudflare UI shows the custom domain
        # without a scheme, and pasting it verbatim would break every fetch
        base = (g("PUBLIC_BASE") or "").strip()
        if base and not base.startswith(("http://", "https://")):
            base = "https://" + base
        self.base = base.rstrip("/") + "/" if base else None
        self.host = f"{self.account}.r2.cloudflarestorage.com" if self.account else None
        self.enabled = bool(self.account and self.key and self.secret
                            and self.bucket and self.base)

    # ------------------------------------------------------------ signing

    def _request(self, method, key, body=b"", query="", headers=None, cache=None,
                 raw=False):
        """One signed S3 request. SigV4, UNSIGNED-PAYLOAD is not allowed for
        PUT here so the body hash is always computed.

        `raw` returns (status, headers, body) instead of just the body, and
        turns an HTTP error into a value rather than an exception. Conditional
        writes need both: a 404 is "no such thread yet" and a 412 is "someone
        wrote first", and neither is a failure. Off by default, so the four
        existing callers behave exactly as they did."""
        now = datetime.now(timezone.utc)
        stamp, date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()
        path = f"/{self.bucket}/{urllib.parse.quote(key)}" if key else f"/{self.bucket}"

        h = {"host": self.host, "x-amz-content-sha256": payload_hash,
             "x-amz-date": stamp}
        h.update({k.lower(): v for k, v in (headers or {}).items()})
        if cache is not None:
            h["cache-control"] = f"public, max-age={cache}"

        signed = ";".join(sorted(h))
        canon_headers = "".join(f"{k}:{h[k]}\n" for k in sorted(h))
        canon = (f"{method}\n{path}\n{query}\n{canon_headers}\n{signed}\n{payload_hash}")

        scope = f"{date}/{REGION}/{SERVICE}/aws4_request"
        to_sign = ("AWS4-HMAC-SHA256\n" + stamp + "\n" + scope + "\n"
                   + hashlib.sha256(canon.encode()).hexdigest())
        k = _sign(f"AWS4{self.secret}".encode(), date)
        for part in (REGION, SERVICE, "aws4_request"):
            k = _sign(k, part)
        sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()

        h["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={self.key}/{scope}, "
                              f"SignedHeaders={signed}, Signature={sig}")
        url = f"https://{self.host}{path}" + (f"?{query}" if query else "")
        req = urllib.request.Request(url, data=body or None, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                # r.headers, not dict(r.headers): the mapping is
                # case-insensitive and R2's header casing is not worth betting on
                return (r.status, r.headers, r.read()) if raw else r.read()
        except urllib.error.HTTPError as e:
            if not raw:
                raise
            return (e.code, e.headers, e.read())

    # --------------------------------------------------------------- read

    def get(self, rel, fresh=False):
        """Unsigned public read. `fresh` busts the CDN for state files a run is
        about to append to."""
        if not self.enabled:
            return None
        url = self.base + self.prefix + rel
        if fresh:
            url += f"?_={int(datetime.now(timezone.utc).timestamp())}"
        try:
            req = urllib.request.Request(
                url, headers={"cache-control": "no-cache", "user-agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError,
                json.JSONDecodeError, TimeoutError):
            return None

    def list(self):
        if not self.enabled:
            return []
        out, token = [], None
        while True:
            # safe="" matters: SigV4 canonicalises "/" in a query value as
            # %2F, and signing the literal slash produces a signature the
            # server disagrees with
            enc = lambda v: urllib.parse.quote(str(v), safe="")
            params = {"list-type": "2", "prefix": self.prefix}
            if token:
                params["continuation-token"] = token
            q = "&".join(f"{k}={enc(v)}" for k, v in sorted(params.items()))
            body = self._request("GET", "", query=q)
            ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
            root = ET.fromstring(body)
            out += [e.text[len(self.prefix):] for e in root.iter(f"{ns}Key")]
            trunc = root.find(f"{ns}IsTruncated")
            token_el = root.find(f"{ns}NextContinuationToken")
            if trunc is None or trunc.text != "true" or token_el is None:
                return out
            token = token_el.text

    def get_signed(self, rel):
        """Signed read straight from the S3 endpoint, returning (etag, doc).

        Not `get()`, for two reasons that both end in lost messages. `get()`
        reads through the public CDN, so it can hand back a copy that is
        seconds old -- and a read-modify-write cycle that starts from a stale
        copy writes that staleness back over whatever it missed. And `get()`
        collapses 404, DNS failure, timeout and bad JSON into one `None`, so a
        momentary blip would look exactly like "this thread does not exist
        yet" and overwrite a live thread with an empty one.

        (None, None) means genuinely absent. Anything else raises."""
        if not self.enabled:
            return None, None
        status, headers, body = self._request("GET", self.prefix + rel, raw=True)
        if status == 404:
            return None, None
        if status != 200:
            raise RuntimeError(f"R2 GET {rel}: {status}")
        return headers.get("ETag"), json.loads(body)

    # -------------------------------------------------------------- write

    def put_cond(self, rel, payload, etag=None, cache=0):
        """Compare-and-swap. `etag` from a prior `get_signed`; None means the
        object must not exist yet. Returns the new ETag, or None when the
        precondition failed and the caller should re-read and try again.

        The ETag goes back verbatim, quotes included -- stripping them yields
        a precondition that can never match."""
        if not self.enabled:
            return None
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        cond = {"if-match": etag} if etag else {"if-none-match": "*"}
        status, headers, _ = self._request(
            "PUT", self.prefix + rel, body,
            headers={"content-type": "application/json", **cond},
            cache=cache, raw=True)
        # 412 is the documented answer; some paths report a conditional
        # conflict as 409, and accepting both costs nothing
        if status in (409, 412):
            return None
        if status // 100 != 2:
            raise RuntimeError(f"R2 PUT {rel}: {status}")
        return headers.get("ETag")

    def put(self, rel, payload):
        if not self.enabled:
            return None
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        self._request("PUT", self.prefix + rel, body,
                      headers={"content-type": "application/json"},
                      cache=CACHE_SECONDS.get(rel, DEFAULT_CACHE_SECONDS))
        return self.base + self.prefix + rel

    def delete(self, rel):
        if not self.enabled:
            return
        self._request("DELETE", self.prefix + rel)

    def put_bytes(self, rel, data, content_type, max_age=31536000):
        if not self.enabled:
            return None
        self._request("PUT", self.prefix + rel, data,
                      headers={"content-type": content_type}, cache=max_age)
        return self.base + self.prefix + rel
