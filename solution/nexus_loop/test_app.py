"""HTTP contract tests for the decision screen."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

from nexus_loop.app import ReportStore, make_handler
from http.server import ThreadingHTTPServer

MIN_REPORT = {
    "team": "test",
    "corpus": "x",
    "generated_at": "2026-01-01T00:00:00Z",
    "system_notes": "",
    "metrics": [],
    "findings": [{"id": "f1", "is_regression": True}],
    "diagnoses": [{"id": "d1", "finding_id": "f1"}],
    "gaps": [],
    "standard": [],
    "prescriptions": [{
        "id": "p01_ok",
        "diagnosis_id": "d1",
        "change_type": "tool.validate",
        "target": "t",
        "description": "d",
        "predicted_delta": {"metric": "resolution_rate", "from": 0.1, "to": 0.2},
        "decision": {
            "asking_approval_for": "x",
            "risk_if_diagnosis_wrong": "y",
            "would_not_ship_if": "z",
        },
        "cohort": {},
    }],
    "verifications": [],
    "self_assessment": {"cycles": 0, "prescription_accuracy": {}, "downweighted": []},
}


class ScreenHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.report = os.path.join(self.tmp.name, "loop-report.json")
        Path(self.report).write_text(json.dumps(MIN_REPORT), encoding="utf-8")
        store = ReportStore(self.report, schema_path=None)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def _conn(self):
        return HTTPConnection("127.0.0.1", self.port, timeout=5)

    def _json(self, method, path, body=None, headers=None):
        c = self._conn()
        raw = None if body is None else json.dumps(body).encode()
        hdrs = dict(headers or {})
        if raw is not None:
            hdrs.setdefault("Content-Type", "application/json")
            hdrs["Content-Length"] = str(len(raw))
        c.request(method, path, body=raw, headers=hdrs)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def test_screen_is_offline_and_escapes(self):
        st, _, body = self._json("GET", "/")
        page = body.decode()
        self.assertEqual(st, 200)
        self.assertIn('src="/static/app.js"', page)
        self.assertIn('href="/static/app.css"', page)
        for name in ("app.js", "app.css"):
            st, hdrs, data = self._json("GET", "/static/" + name)
            self.assertEqual(st, 200)
            text = data.decode()
            # no CDN, no web fonts, no remote scripts
            self.assertNotRegex(text, r"https?://")
        _, hdrs, js = self._json("GET", "/static/app.js")
        self.assertIn("javascript", {k.lower(): v for k, v in hdrs.items()}["content-type"])
        js = js.decode()
        self.assertIn("function esc(v)", js)
        self.assertIn('fetch("/decision"', js)
        self.assertIn('fetch("/report.json"', js)
        self.assertIn("A reason is required.", js)
        self.assertNotIn(' style="', js)

    def _js(self):
        st, _, data = self._json("GET", "/static/app.js")
        self.assertEqual(st, 200)
        return data.decode()

    def test_required_asks_are_marked_in_metrics(self):
        js = self._js()
        self.assertIn('const REQUIRED_ASKS = ["A01", "A04", "A09", "A11"];', js)
        self.assertIn('REQUIRED_ASKS.includes(ask)', js)
        self.assertIn("One row per question", js)

    def test_rail_groups_are_dropdowns(self):
        js = self._js()
        self.assertIn('<details class="rail-group"', js)
        for label in ("Needs a decision", "Checked and dismissed", "Refused to answer"):
            self.assertIn(label, js)

    def test_fonts_are_bundled_and_served(self):
        _, _, css = self._json("GET", "/static/app.css")
        self.assertIn('url("fonts/InterVariable.woff2")', css.decode())
        for name in ("InterVariable.woff2", "JetBrainsMono-Variable.woff2"):
            st, hdrs, data = self._json("GET", "/static/fonts/" + name)
            self.assertEqual(st, 200, name)
            self.assertEqual({k.lower(): v for k, v in hdrs.items()}["content-type"], "font/woff2")
            self.assertEqual(data[:4], b"wOF2")

    def test_manager_english_and_idle_gate_copy(self):
        js = self._js()
        self.assertIn("function toManagerEnglish(text)", js)
        self.assertIn('"tool.contract_break": "The tool said success but sent an empty answer"', js)
        self.assertIn("function audienceFor(f, d)", js)
        self.assertIn('A11: "Failover does not exist here. There is no rate to show."', js)
        self.assertIn('"Nothing to approve. We will not invent a failover rate."', js)
        self.assertIn('return "Nothing to approve. This is not a problem.";', js)
        self.assertIn("Required event:", js)  # stripped from gap prose; the spec is shown structurally
        self.assertIn("Technical names", js)

    def test_both_impact_shapes_are_rendered(self):
        js = self._js()
        for field in ("would_have_resolved_at_baseline", "resolved_conversations_slowed",
                      "extra_turns", "excess_cost_usd", "unplanned_handoffs", "abandoned"):
            self.assertIn("ds." + field, js)

    def test_standard_match_treats_null_cohort_keys_as_wildcards(self):
        self.assertIn("sc[k] == null || sc[k] === c[k]", self._js())

    def test_csp_has_no_inline_or_remote_sources(self):
        _, hdrs, _ = self._json("GET", "/")
        csp = {k.lower(): v for k, v in hdrs.items()}["content-security-policy"]
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("https:", csp)
        self.assertIn("script-src 'self'", csp)

    def test_static_whitelist(self):
        for bad in ("/static/../app.py", "/static/%2e%2e/app.py", "/static/app.py",
                    "/static/fonts/../../app.py", "/static/", "/static/nope.css"):
            st, _, _ = self._json("GET", bad)
            self.assertEqual(st, 404, bad)

    def test_get_page_and_security_headers(self):
        st, hdrs, body = self._json("GET", "/")
        lower = {k.lower(): v for k, v in hdrs.items()}
        self.assertEqual(st, 200)
        self.assertIn(b"<html", body.lower())
        self.assertEqual(lower.get("x-content-type-options"), "nosniff")
        self.assertEqual(lower.get("x-frame-options"), "DENY")
        self.assertIn("default-src 'none'", lower.get("content-security-policy", ""))

    def test_health_does_not_leak_path(self):
        st, _, data = self._json("GET", "/health")
        self.assertEqual(st, 200)
        obj = json.loads(data)
        self.assertTrue(obj["ok"])
        self.assertTrue(obj["report"])
        self.assertNotIn(self.tmp.name, data.decode())

    def test_unknown_path_404(self):
        st, _, data = self._json("GET", "/etc/passwd")
        self.assertEqual(st, 404)
        self.assertEqual(json.loads(data)["error"], "not_found")

    def test_wrong_method_405(self):
        st, hdrs, _ = self._json("POST", "/report.json")
        lower = {k.lower(): v for k, v in hdrs.items()}
        self.assertEqual(st, 405)
        self.assertIn("GET", lower.get("allow", ""))

    def test_decision_write_back_and_empty_reason(self):
        st, _, data = self._json("POST", "/decision", {
            "prescription_id": "p01_ok", "verdict": "accepted",
            "decided_by": "testbed", "reason": "ok",
        })
        self.assertEqual(st, 200, data)
        st2, _, _ = self._json("POST", "/decision", {
            "prescription_id": "p01_ok", "verdict": "rejected",
            "decided_by": "testbed", "reason": "",
        })
        self.assertEqual(st2, 400)

    def test_payload_too_large(self):
        c = self._conn()
        c.putrequest("POST", "/decision")
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", str(70 * 1024))
        c.endheaders()
        c.send(b"x" * 1024)
        r = c.getresponse()
        self.assertEqual(r.status, 413)
        c.close()

    def test_script_in_reason_is_stored_as_text(self):
        payload = {
            "prescription_id": "p01_ok", "verdict": "deferred",
            "decided_by": "operator",
            "reason": "<script>alert(1)</script>",
        }
        st, _, data = self._json("POST", "/decision", payload)
        self.assertEqual(st, 200, data)
        saved = json.loads(Path(self.report).read_text())
        self.assertEqual(saved["prescriptions"][0]["approval"]["reason"], payload["reason"])

    def test_corrupt_report_returns_error_not_drop(self):
        Path(self.report).write_text("{ not json", encoding="utf-8")
        st, _, data = self._json("GET", "/report.json")
        self.assertEqual(st, 500)
        self.assertEqual(json.loads(data)["error"], "report_invalid")

    def test_concurrent_decisions_both_persist(self):
        two = json.loads(json.dumps(MIN_REPORT))
        two["prescriptions"].append({
            "id": "p02_ok",
            "diagnosis_id": "d1",
            "change_type": "kb.add",
            "target": "t2",
            "description": "d2",
            "predicted_delta": {"metric": "resolution_rate", "from": 0.1, "to": 0.2},
            "decision": {
                "asking_approval_for": "x",
                "risk_if_diagnosis_wrong": "y",
                "would_not_ship_if": "z",
            },
            "cohort": {},
        })
        Path(self.report).write_text(json.dumps(two), encoding="utf-8")

        results = []
        results_lock = threading.Lock()

        def post(pid):
            st, _, _ = self._json("POST", "/decision", {
                "prescription_id": pid, "verdict": "accepted",
                "decided_by": "judge", "reason": "concurrent",
            })
            with results_lock:
                results.append(st)

        threads = [threading.Thread(target=post, args=(pid,))
                   for pid in ("p01_ok", "p02_ok")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results, [200, 200])
        saved = json.loads(Path(self.report).read_text())
        by_id = {p["id"]: p.get("approval") for p in saved["prescriptions"]}
        self.assertEqual(by_id["p01_ok"]["verdict"], "accepted")
        self.assertEqual(by_id["p02_ok"]["verdict"], "accepted")
        log = Path(os.path.join(self.tmp.name, "decisions.log")).read_text().splitlines()
        self.assertEqual(len([line for line in log if line.strip()]), 2)

    def test_console_has_copy_decision_note(self):
        st, _, js = self._json("GET", "/static/app.js")
        self.assertEqual(st, 200)
        code = js.decode()
        self.assertIn("function decisionNote(", code)
        self.assertIn("function allDecisionsNote(", code)
        self.assertNotIn('id="generate"', self._json("GET", "/")[2].decode())
        self.assertNotIn('fetch("/generate"', code)


if __name__ == "__main__":
    unittest.main()
