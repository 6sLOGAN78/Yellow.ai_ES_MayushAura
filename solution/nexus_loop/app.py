"""Decision screen: one stdlib HTTP server, report as the source of truth.

GET  /              the operator screen (static/index.html)
GET  /report.json   the current loop-report.json
GET  /health        liveness; does not leak filesystem paths
POST /decision      write approval back: read → update that prescription only →
                    temp file → atomic replace → re-validate → append decisions.log

Unknown paths 404. Wrong methods 405. The screen is the only client.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

from .io import write_json_atomic
from .validate import extra_checks, load_schema, validate

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
VERDICTS = {"accepted", "rejected", "deferred"}
MAX_BODY = 64 * 1024
MAX_REASON = 4000
MAX_ID = 128
MAX_WHO = 80
SAFE_ID = re.compile(r"^[\w.:-]{1,128}$")
SAFE_WHO = re.compile(r"^[\w .@+-]{1,80}$")

SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
    ("Content-Security-Policy",
     "default-src 'none'; script-src 'self'; "
     "style-src 'self'; "
     "font-src 'self'; "
     "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
     "base-uri 'none'; frame-ancestors 'none'"),
)

# the operator console's own assets (app.js, app.css, bundled fonts)
STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
}
SAFE_STATIC = re.compile(r"^(?:fonts/)?[A-Za-z0-9][\w.-]{0,80}$")


class ReportStore:
    def __init__(self, report_path: str, schema_path: Optional[str] = None):
        self.report_path = os.path.abspath(report_path)
        self.schema_path = schema_path
        self.schema = load_schema(schema_path) if schema_path and os.path.exists(schema_path) else None
        self.log_path = os.path.join(os.path.dirname(self.report_path), "decisions.log")
        self._lock = threading.Lock()

    def load(self) -> dict:
        with open(self.report_path, encoding="utf-8") as fh:
            return json.load(fh)

    def decide(self, prescription_id: str, verdict: str, decided_by: str, reason: str) -> dict:
        if verdict not in VERDICTS:
            raise ValueError("verdict must be one of %s" % sorted(VERDICTS))
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("a reason is required")
        if len(reason) > MAX_REASON:
            raise ValueError("reason is too long")
        pid = (prescription_id or "").strip()
        if not pid or not SAFE_ID.match(pid):
            raise ValueError("prescription_id is required")
        who = (decided_by or "operator").strip() or "operator"
        if len(who) > MAX_WHO or not SAFE_WHO.match(who):
            raise ValueError("decided_by is invalid")
        with self._lock:
            return self._decide_locked(pid, verdict, who, reason)

    def _decide_locked(self, prescription_id: str, verdict: str, decided_by: str, reason: str) -> dict:
        report = self.load()
        target = None
        for p in report.get("prescriptions") or []:
            if p.get("id") == prescription_id:
                target = p
                break
        if target is None:
            raise KeyError("no prescription %s" % prescription_id)
        at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        target["approval"] = {
            "verdict": verdict,
            "decided_by": decided_by,
            "reason": reason,
            "at": at,
        }
        problems = extra_checks(report)
        if self.schema:
            problems = validate(report, self.schema) + problems
        if problems:
            raise ValueError("report would be invalid after write-back: " + "; ".join(problems[:8]))
        write_json_atomic(self.report_path, report, allow_nan=False)
        os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": at, "prescription_id": prescription_id, "verdict": verdict,
                "decided_by": decided_by, "reason": reason,
            }, sort_keys=True) + "\n")
        return target["approval"]

def make_handler(store: ReportStore):
    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str, extra=None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in SECURITY_HEADERS:
                self.send_header(k, v)
            if extra:
                for k, v in extra:
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict, extra=None) -> None:
            self._send(code, json.dumps(obj, indent=2).encode("utf-8"),
                       "application/json; charset=utf-8", extra)

        def _allow(self, code: int, allow: str, obj: dict) -> None:
            self._json(code, obj, extra=(("Allow", allow),))

        def _static(self, name: str):
            # whitelist extensions, no traversal, only STATIC_DIR (and fonts/)
            ext = os.path.splitext(name)[1].lower()
            if ext not in STATIC_TYPES or not SAFE_STATIC.match(name) or ".." in name:
                return self._json(404, {"error": "not_found"})
            try:
                with open(os.path.join(STATIC_DIR, *name.split("/")), "rb") as fh:
                    body = fh.read()
            except OSError:
                return self._json(404, {"error": "not_found"})
            return self._send(200, body, STATIC_TYPES[ext])

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/decision":
                return self._allow(405, "POST", {"error": "method_not_allowed"})
            if path in ("/", "/index.html"):
                page = os.path.join(STATIC_DIR, "index.html")
                try:
                    with open(page, "rb") as fh:
                        body = fh.read()
                except OSError as e:
                    return self._json(500, {"error": "screen_unavailable", "detail": str(e)})
                return self._send(200, body, "text/html; charset=utf-8")
            if path == "/report.json":
                try:
                    body = json.dumps(store.load()).encode("utf-8")
                except OSError as e:
                    return self._json(404, {"error": "report_missing", "detail": str(e)})
                except ValueError as e:
                    # a corrupt report raises JSONDecodeError (a ValueError); return a
                    # clear error instead of letting the handler raise and drop the
                    # connection, which would leave the screen permanently dead
                    return self._json(500, {"error": "report_invalid", "detail": str(e)})
                return self._send(200, body, "application/json; charset=utf-8")
            if path == "/health":
                present = os.path.isfile(store.report_path)
                ok = present
                try:
                    if present:
                        store.load()
                except (OSError, ValueError):
                    ok = False
                return self._json(200, {"ok": ok, "report": ok})
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            return self._json(404, {"error": "not_found"})

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/decision":
                return self._post_decision()
            allow = "GET" if (
                path in ("/", "/index.html", "/report.json", "/health")
                or path.startswith("/static/")
            ) else "GET, POST"
            return self._allow(405, allow, {"error": "method_not_allowed"})

        def _post_decision(self):
            raw = self._read_body()
            if raw is None:
                return
            try:
                body = json.loads((raw.decode("utf-8") or "{}"))
            except (ValueError, UnicodeDecodeError) as e:
                return self._json(400, {"error": "bad_json", "detail": str(e)})
            if not isinstance(body, dict):
                return self._json(400, {"error": "bad_json", "detail": "object required"})
            try:
                approval = store.decide(
                    str(body.get("prescription_id") or ""),
                    str(body.get("verdict") or ""),
                    str(body.get("decided_by") or "operator"),
                    str(body.get("reason") or ""),
                )
            except KeyError as e:
                return self._json(404, {"error": "unknown_prescription", "detail": str(e)})
            except ValueError as e:
                return self._json(400, {"error": "invalid_decision", "detail": str(e)})
            except OSError as e:
                return self._json(500, {"error": "report_unavailable", "detail": str(e)})
            return self._json(200, {"ok": True, "approval": approval})

        def _read_body(self):
            raw_len = self.headers.get("Content-Length") or "0"
            try:
                n = int(raw_len)
            except ValueError:
                self._json(400, {"error": "bad_length"})
                return None
            if n < 0 or n > MAX_BODY:
                self._json(413, {"error": "payload_too_large"})
                return None
            return self.rfile.read(n) if n else b"{}"

        def do_OPTIONS(self):
            path = urlparse(self.path).path
            allow = "POST" if path == "/decision" else "GET"
            self._send(204, b"", "text/plain; charset=utf-8", extra=(("Allow", allow),))

        def do_PUT(self):
            self.do_PATCH()

        def do_PATCH(self):
            self.do_DELETE()

        def do_DELETE(self):
            self._allow(405, "GET, POST", {"error": "method_not_allowed"})

        def log_message(self, *a):
            pass
    return H


def serve(report_path: str, schema_path: Optional[str] = None, port: int = 8765,
          host: str = "127.0.0.1") -> None:
    store = ReportStore(report_path, schema_path)
    httpd = ThreadingHTTPServer((host, port), make_handler(store))
    httpd.serve_forever()


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Nexus Loop decision screen")
    ap.add_argument("--report", default="out/loop-report.json")
    ap.add_argument("--schema", default="tools/nexus-loop-kit/schema/loop-report.schema.json")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    print("decision screen http://%s:%d  (report %s)" % (a.host, a.port, a.report))
    serve(a.report, a.schema, a.port, a.host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
