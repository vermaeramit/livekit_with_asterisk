#!/usr/bin/env python3
"""A stub API for testing campaign tools. Standard library only.

    python3 server-configs/tool-stub-api.py            # listens on 0.0.0.0:9099

Third-party echo services turned out to be a poor way to test this: httpbin
answered 503, and postman-echo and dummyjson both answered 403 with Cloudflare's
error 1010 - not because of the URL, but because urllib and aiohttp send a
default User-Agent that WAFs reject. Half an hour went into endpoints rather
than into the thing being tested.

More importantly, a public echo service cannot test the cases that actually
matter here:

    /service?reg=XYZ        a normal, realistic JSON response
    /slow?ms=4000           a response that arrives after the tool's timeout
    /fail?code=503          a specific error status
    /huge?kb=64             a response far larger than max_response_bytes

The timeout path is the one worth exercising. It is what a caller hears as
silence, and it is the only path where the agent has to say something sensible
without any data.

Bind is 0.0.0.0 on purpose: the agent reaches it on 127.0.0.1, but admin-api
runs in a container and needs the host address (10.130.9.243:9099). Same URL
works for both if you use the host address.
"""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PORT = 9099


class Handler(BaseHTTPRequestHandler):
    # ThreadingHTTPServer + a slow endpoint means one hung request must not
    # block the next one - which is exactly what a concurrency test needs.
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, payload, raw: bytes | None = None):
        body = raw if raw is not None else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""

        if u.path == "/slow":
            time.sleep(int(q.get("ms", 4000)) / 1000)
            return self._send(200, {"status": "ok", "note": "deliberately slow"})

        if u.path == "/fail":
            return self._send(int(q.get("code", 500)), {"error": "deliberate failure"})

        if u.path == "/huge":
            return self._send(200, None, raw=b'{"filler":"' + b"x" * (int(q.get("kb", 64)) * 1024) + b'"}')

        if u.path == "/service":
            reg = q.get("reg", "")
            if not reg:
                # What a real API does with a missing argument, so the
                # {{placeholder}} mismatch is visible rather than guessed at.
                return self._send(400, {"error": "reg is required"})
            return self._send(200, {
                "registration": reg,
                "customer": "Amit Verma",
                "model": "XTREME 125R",
                "service": {
                    "status": "In progress",
                    "job_card": "JC-88213",
                    "expected_ready": "today 6 PM",
                    "advisor": "Rakesh",
                },
                # Deliberate noise: a real response has fields the model does not
                # need, which is what response_path is for.
                "internal": {"dealer_code": "HMC-4471", "created_by": "sys"},
            })

        if u.path == "/book":
            return self._send(200, {
                "booked": True,
                "slot": (json.loads(body).get("slot") if body else None),
                # Echoed so a repeated call is visible: two identical requests
                # with the same key mean the idempotency header is working.
                "idempotency_key": self.headers.get("Idempotency-Key"),
                "user_agent": self.headers.get("User-Agent"),
            })

        self._send(404, {"error": "unknown path",
                         "paths": ["/service", "/book", "/slow", "/fail", "/huge"]})

    do_GET = do_POST = do_PUT = _handle

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}  ua={self.headers.get('User-Agent')}")


if __name__ == "__main__":
    print(f"stub API on 0.0.0.0:{PORT}")
    print("  /service?reg=MH12AB1234   /book (POST)   /slow?ms=4000   /fail?code=503")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
