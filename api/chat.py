"""Vercel function for chat. Serves /api/chat.

Deliberately thin: everything that can be wrong lives in chatstore.py, which
needs no HTTP server to exercise. This file only turns requests into calls and
exceptions into status codes.

It is also the only code in the project that takes writes from the open
internet, which is why it holds credentials for the chat bucket alone and
reads the tracker's own data over a plain public URL.
"""

import json
import os
import sys

from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# The project root is not reliably on sys.path for a function in /api, and the
# failure is an import error at request time on a deployment that worked
# locally. Say where the modules are rather than hoping.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import chatstore  # noqa: E402


def _writable():
    """Previews share the production bucket, so only production and local
    development may write. Turnstile's hostname allowlist catches most of
    this too; this is the part that does not depend on configuration."""
    return (not chatstore.deployed()
            or os.environ.get("VERCEL_ENV") == "production")


class handler(BaseHTTPRequestHandler):

    # ---------------------------------------------------------- plumbing

    def _send(self, status, payload=None):
        body = json.dumps(payload or {}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("content-length") or 0)
        # checked before reading, so an oversized request costs nothing
        if n > chatstore.MAX_BODY:
            raise chatstore.Rejected("message is too large")
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except (ValueError, UnicodeDecodeError):
            raise chatstore.Rejected("malformed request")

    def _ip(self):
        # passed to Cloudflare for the challenge and never stored: the bucket
        # is public and the repository is public
        return (self.headers.get("x-forwarded-for") or "").split(",")[0].strip()

    def _token(self):
        auth = self.headers.get("authorization") or ""
        return auth[7:] if auth.lower().startswith("bearer ") else ""

    def _run(self, fn):
        """One place where every failure becomes a status code."""
        try:
            fn()
        except chatstore.Rejected as e:
            self._send(400, {"error": str(e)})
        except chatstore.Busy as e:
            self.send_response(503)
            self.send_header("retry-after", "2")
            self.send_header("content-type", "application/json")
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
        except chatstore.NotConfigured as e:
            # never a silent 200: a missing env var that looked like success
            # would swallow every message posted
            self._send(500, {"error": str(e)})
        except Exception as e:                       # noqa: BLE001
            self._send(500, {"error": f"{type(e).__name__}"})

    def log_message(self, *a):                       # quiet in local dev
        pass

    # ----------------------------------------------------------- methods

    def do_GET(self):
        """?op=auth — lets the settings panel confirm a moderation token at
        the moment it is entered, rather than on a first failed delete."""
        def go():
            q = parse_qs(urlparse(self.path).query)
            if (q.get("op") or [""])[0] != "auth":
                return self._send(400, {"error": "unknown op"})
            self._send(200 if chatstore.is_admin(self._token()) else 401,
                       {"ok": chatstore.is_admin(self._token())})
        self._run(go)

    def do_POST(self):
        def go():
            if not _writable():
                return self._send(403, {"error": "posting is disabled here"})
            b = self._body()
            thread = chatstore.check_thread(b.get("scope"), b.get("slug"))
            chatstore.verify_turnstile(b.get("turnstile"), self._ip())
            msg = chatstore.post(thread, b.get("name"), b.get("tag"),
                                 b.get("text"))
            # the whole thread comes back, so the poster sees their own
            # message at once instead of waiting for the next poll to clear
            # the five-second edge cache
            self._send(200, {"message": msg, "thread": chatstore.read(thread)})
        self._run(go)

    def do_DELETE(self):
        def go():
            if not chatstore.is_admin(self._token()):
                return self._send(401, {"error": "not authorised"})
            b = self._body()
            thread = chatstore.check_thread(b.get("scope"), b.get("slug"))
            n = (chatstore.clear(thread) if b.get("all")
                 else chatstore.remove(thread, b.get("id")))
            self._send(200, {"removed": n, "thread": chatstore.read(thread)})
        self._run(go)
