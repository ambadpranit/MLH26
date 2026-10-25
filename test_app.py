"""End-to-end tests for app.py using a FAKE Ollama server, so no real model is needed.

  python test_app.py
"""
import imaplib
import json
import re
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import app
import mail2cal as m
import make_samples
from test_mail2cal import CANNED


class FakeOllama(BaseHTTPRequestHandler):
    """Answers /api/tags and /api/chat the way Ollama does, using the canned model replies."""

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send({"models": [{"name": "fake:1b"}, {"name": "alpha:2b"}]})
        else:
            self._send({}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body["model"] == "missing:1b":
            return self._send({"error": "model 'missing:1b' not found"}, 404)
        subject = re.search(r"Subject: (.*)", body["messages"][-1]["content"])[1]
        self._send({"message": {"content": json.dumps({"events": CANNED.get(subject, [])})},
                    "eval_count": 50, "eval_duration": 2 * 10 ** 9})


def serve(handler_or_server):
    t = threading.Thread(target=handler_or_server.serve_forever, daemon=True)
    t.start()
    return handler_or_server


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def get(base, path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def post_stream(base, payload, content_type="application/json"):
    req = urllib.request.Request(base + "/api/extract", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": content_type})
    with urllib.request.urlopen(req) as r:
        return [json.loads(line) for line in r.read().decode().splitlines() if line.strip()]


def raw_email(subject, body, sender="Exam Cell <exams@college.edu>", sent="Mon, 26 Oct 2026 09:00:00 +0530"):
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["Date"] = subject, sender, sent
    msg.set_content(body)
    return msg.as_bytes()


class FakeIMAP:
    """Stands in for imaplib.IMAP4_SSL: a tiny 3-email inbox that records how it was used."""
    GOOD_PASSWORD = "abcdefghijklmnop"
    calls = []
    inbox = {}

    def __init__(self, host, port=993, timeout=None):
        FakeIMAP.calls.append(("connect", host))

    def login(self, user, password):
        FakeIMAP.calls.append(("login", user, password))
        if password != self.GOOD_PASSWORD:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

    def select(self, folder, readonly=False):
        FakeIMAP.calls.append(("select", folder, readonly))
        return "OK", [b"3"]

    def search(self, charset, *criteria):
        FakeIMAP.calls.append(("search",) + criteria)
        return "OK", [b" ".join(str(n).encode() for n in sorted(FakeIMAP.inbox))]

    def fetch(self, num, what):
        FakeIMAP.calls.append(("fetch", num, what))
        raw = FakeIMAP.inbox[int(num)]
        return "OK", [(b"%s (BODY[]<0> {%d}" % (num, len(raw)), raw), b")"]

    def logout(self):
        FakeIMAP.calls.append(("logout",))


def post(base, path, payload):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = serve(ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama))
        cls.old_host = m.HOST
        m.HOST = f"http://127.0.0.1:{cls.fake.server_address[1]}"
        cls.inbox = tempfile.mkdtemp()
        make_samples.write(cls.inbox)
        cls.srv = serve(app.make_server("127.0.0.1", 0, "fake:1b", cls.inbox))
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        m.HOST = cls.old_host
        cls.srv.shutdown()
        cls.fake.shutdown()
        cls.srv.server_close()
        cls.fake.server_close()

    # ---- pages and config
    def test_page_and_config(self):
        with urllib.request.urlopen(self.base + "/") as r:
            html = r.read().decode()
        self.assertIn("Find my exams", html)
        cfg = get(self.base, "/api/config")
        self.assertEqual(cfg, {"default_model": "fake:1b", "has_inbox": True, "has_sample": True, "has_gmail": True, "version": "2.0"})

    def test_models_come_from_ollama(self):
        d = get(self.base, "/api/models")
        self.assertTrue(d["ok"])
        self.assertEqual(d["models"], ["alpha:2b", "fake:1b"])

    # ---- the main flow
    def test_extract_streams_progress_then_results(self):
        text = get(self.base, "/api/sample")["text"]
        lines = post_stream(self.base, {"text": text, "model": "fake:1b", "sent": "2026-10-26"})
        progress = [x for x in lines if x["type"] == "progress"]
        self.assertEqual([p["done"] for p in progress], list(range(0, 9)))      # 0 then 1..8
        self.assertEqual(lines[-1]["type"], "done")
        d = lines[-1]
        titles = [e["title"] for e in d["events"]]
        self.assertEqual(len(titles), 7)
        self.assertEqual(d["events"][0]["date"], "2026-10-30")                   # sorted by date
        self.assertEqual([c["level"] for c in d["clashes"]], ["clash"])
        a, b = d["clashes"][0]["a"], d["clashes"][0]["b"]
        self.assertEqual({titles[a], titles[b]}, {"Data Structures exam", "Operating Systems exam"})
        self.assertEqual({r["title"] for r in d["review"]}, {"DBMS viva", "Placement cell meeting"})
        self.assertEqual(d["ics"].count("BEGIN:VEVENT"), 7)
        self.assertEqual(d["stats"]["skipped"], 1)
        self.assertAlmostEqual(d["stats"]["tps"], 25.0, places=3)                # 7 calls x 50 tok / 2 s

    def test_hallucination_never_reaches_the_calendar(self):
        text = "Subject: Exam notice\n\nExams will be announced soon."
        CANNED["Exam notice"] = [{"title": "Final exam", "kind": "exam", "date_text": "5 December 2026",
                                  "time_text": "", "venue": "", "quote": "Exam on 5 December 2026"}]
        try:
            d = post_stream(self.base, {"text": text, "model": "fake:1b"})[-1]
        finally:
            del CANNED["Exam notice"]
        self.assertEqual(d["events"], [])
        self.assertEqual(d["review"][0]["level"], "rejected")

    def test_empty_text_reports_zero_emails(self):
        d = post_stream(self.base, {"text": "  \n ", "model": "fake:1b"})[-1]
        self.assertEqual((d["type"], d["stats"]["emails"]), ("done", 0))

    # ---- failure paths
    def test_unknown_model_gives_helpful_error(self):
        last = post_stream(self.base, {"text": "Subject: Viva schedule\n\nViva soon.", "model": "missing:1b"})[-1]
        self.assertEqual(last["type"], "error")
        self.assertIn("ollama pull missing:1b", last["message"])

    def test_ollama_down_gives_helpful_error(self):
        old, m.HOST = m.HOST, f"http://127.0.0.1:{free_port()}"
        try:
            self.assertFalse(get(self.base, "/api/models")["ok"])
            last = post_stream(self.base, {"text": "Subject: Exam\n\nExam 5 December 2026", "model": "x"})[-1]
        finally:
            m.HOST = old
        self.assertEqual(last["type"], "error")
        self.assertIn("Cannot reach Ollama", last["message"])

    def test_rejects_other_hosts_and_non_json_posts(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            get(self.base, "/api/config", headers={"Host": "evil.example"})
        self.assertEqual(cm.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            post_stream(self.base, {"text": "x"}, content_type="text/plain")
        self.assertEqual(cm.exception.code, 415)

    # ---- fetched inbox
    def test_inbox_text_round_trips_into_the_same_emails(self):
        d = get(self.base, "/api/inbox")
        self.assertEqual(d["count"], len(make_samples.SAMPLES))
        emails = app.parse_pasted(d["text"], date(2026, 1, 1))
        self.assertEqual([e["subject"] for e in emails], [s[1] for s in make_samples.SAMPLES])
        self.assertTrue(all(e["sent"] == date(2026, 10, 26) for e in emails))

    # ---- Gmail (IMAP) - the real imaplib is swapped for FakeIMAP, so no network and no account needed
    def _with_fake_gmail(self):
        samples = {s[1]: s[2] for s in make_samples.SAMPLES}
        FakeIMAP.calls = []
        FakeIMAP.inbox = {1: raw_email("DBMS assignment submission", samples["DBMS assignment submission"]),
                          2: raw_email("Lunch menu this week", "Pizza on Friday.", sender="Canteen <c@x.edu>"),
                          3: raw_email("End semester examination schedule", samples["End semester examination schedule"])}
        self._real_imap, imaplib.IMAP4_SSL = imaplib.IMAP4_SSL, FakeIMAP
        self.addCleanup(lambda: setattr(imaplib, "IMAP4_SSL", self._real_imap))

    def test_gmail_fetch_lists_emails_newest_first_and_flags_relevant_ones(self):
        self._with_fake_gmail()
        d = post(self.base, "/api/gmail/fetch", {"user": "me@gmail.com", "password": "abcd efgh ijkl mnop", "days": 30})
        self.assertTrue(d["ok"])
        self.assertEqual([e["subject"] for e in d["emails"]],
                         ["End semester examination schedule", "Lunch menu this week", "DBMS assignment submission"])
        self.assertEqual([e["relevant"] for e in d["emails"]], [True, False, True])
        self.assertEqual(d["emails"][0]["from"], "Exam Cell <exams@college.edu>")
        self.assertEqual(d["emails"][0]["sent"], "2026-10-26")
        self.assertNotIn("body", d["emails"][0])                                # bodies stay on the server
        self.assertNotIn("abcdefghijklmnop", json.dumps(d))                     # password is never echoed
        # read-only: the mailbox is opened readonly, messages fetched with PEEK, and the session is closed
        self.assertIn(("select", "INBOX", True), FakeIMAP.calls)
        self.assertTrue(all("PEEK" in c[2] for c in FakeIMAP.calls if c[0] == "fetch"))
        self.assertEqual(FakeIMAP.calls[-1], ("logout",))
        self.assertIn(("login", "me@gmail.com", "abcdefghijklmnop"), FakeIMAP.calls)    # spaces stripped

    def test_gmail_emails_go_through_the_same_pipeline(self):
        self._with_fake_gmail()
        ids = [e["id"] for e in post(self.base, "/api/gmail/fetch",
                                     {"user": "me@gmail.com", "password": FakeIMAP.GOOD_PASSWORD})["emails"]]
        lines = post_stream(self.base, {"gmail_ids": ids, "model": "fake:1b"})
        d = lines[-1]
        self.assertEqual(d["type"], "done")
        self.assertEqual(d["stats"]["emails"], 3)
        self.assertEqual(d["stats"]["skipped"], 1)                              # the lunch menu never reaches the model
        self.assertEqual([e["date"] for e in d["events"]], ["2026-10-30", "2026-11-16", "2026-11-19"])
        self.assertEqual(d["events"][0]["subject"], "DBMS assignment submission")

    def test_gmail_ids_can_be_combined_with_pasted_text(self):
        self._with_fake_gmail()
        ids = [e["id"] for e in post(self.base, "/api/gmail/fetch",
                                     {"user": "me@gmail.com", "password": FakeIMAP.GOOD_PASSWORD})["emails"]][:1]
        d = post_stream(self.base, {"text": "Subject: Hello\n\nNothing here", "gmail_ids": ids + ["gmail-999", 7]})[-1]
        self.assertEqual(d["stats"]["emails"], 2)                               # unknown ids are ignored

    def test_gmail_wrong_password_explains_app_passwords(self):
        self._with_fake_gmail()
        d = post(self.base, "/api/gmail/fetch", {"user": "me@gmail.com", "password": "my normal password"})
        self.assertFalse(d["ok"])
        self.assertIn("App Password", d["error"])
        self.assertNotIn("my normal password", d["error"])
        self.assertEqual(FakeIMAP.calls[-1], ("logout",))

    def test_gmail_bad_input_and_unreachable_server(self):
        for payload in ({"user": "", "password": "x"}, {"user": "a@b.c", "password": ""},
                        {"user": "a@b.c", "password": "x", "host": "evil.com/../x"},
                        {"user": "a@b.c", "password": "x", "days": "many"}):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                post(self.base, "/api/gmail/fetch", payload)
            self.assertEqual(cm.exception.code, 400, payload)

        def boom(*a, **k):
            raise socket.gaierror("no dns")
        real, imaplib.IMAP4_SSL = imaplib.IMAP4_SSL, boom
        try:
            d = post(self.base, "/api/gmail/fetch", {"user": "a@b.c", "password": "x"})
        finally:
            imaplib.IMAP4_SSL = real
        self.assertFalse(d["ok"])
        self.assertIn("Could not connect to imap.gmail.com", d["error"])

    def test_gmail_days_and_limit_are_clamped(self):
        self._with_fake_gmail()
        d = post(self.base, "/api/gmail/fetch", {"user": "me@gmail.com", "password": FakeIMAP.GOOD_PASSWORD,
                                                 "days": 99999, "limit": 2})
        self.assertEqual(d["days"], 180)
        self.assertEqual(len(d["emails"]), 2)                                   # newest two only
        self.assertEqual(d["total"], 3)
        since = next(c for c in FakeIMAP.calls if c[0] == "search")[2]
        self.assertRegex(since, r"^\d{2}-[A-Z][a-z]{2}-\d{4}$")

    # ---- installable web app + Chromium app window
    def test_app_files_are_served_and_nothing_else(self):
        with urllib.request.urlopen(self.base + "/manifest.webmanifest") as r:
            self.assertIn("manifest", r.headers["Content-Type"])
            man = json.loads(r.read())
        self.assertEqual((man["display"], man["start_url"]), ("standalone", "/"))
        for icon in man["icons"]:                                              # every icon in the manifest exists
            with urllib.request.urlopen(self.base + icon["src"]) as r:
                self.assertEqual(r.headers["Content-Type"], "image/png")
                self.assertTrue(r.read().startswith(b"\x89PNG"))
        with urllib.request.urlopen(self.base + "/sw.js") as r:
            self.assertIn("javascript", r.headers["Content-Type"])
        for bad in ("/app.py", "/web/index.html", "/../app.py", "/%2e%2e/app.py", "/icons/../../app.py", "/test_app.py"):
            with self.assertRaises(urllib.error.HTTPError, msg=bad) as cm:
                urllib.request.urlopen(self.base + bad)
            self.assertEqual(cm.exception.code, 404, bad)

    # ---- 2.0: self-contained frontend + hardening
    def test_frontend_assets_are_served_locally(self):
        with urllib.request.urlopen(self.base + "/css/app.css") as r:
            self.assertIn("text/css", r.headers["Content-Type"])
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("--accent", r.read().decode())
        with urllib.request.urlopen(self.base + "/js/app.js") as r:
            self.assertIn("javascript", r.headers["Content-Type"])
            self.assertIn("/api/extract", r.read().decode())
        with urllib.request.urlopen(self.base + "/") as r:                    # the page must not need the internet
            html = r.read().decode()
            self.assertEqual(r.headers["Referrer-Policy"], "no-referrer")
        self.assertNotRegex(html, r"https?://cdn\.|jsdelivr|unpkg")
        for bad in ("/css/../app.py", "/js/nope.js", "/css/", "/js/%2e%2e/app.py"):
            with self.assertRaises(urllib.error.HTTPError, msg=bad) as cm:
                urllib.request.urlopen(self.base + bad)
            self.assertEqual(cm.exception.code, 404, bad)

    def test_garbage_content_length_is_a_400_not_a_crash(self):
        for value in ("abc", "-5"):
            conn = socket.create_connection(("127.0.0.1", self.srv.server_address[1]), timeout=5)
            conn.sendall(("POST /api/extract HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                          f"Content-Length: {value}\r\nConnection: close\r\n\r\n").encode())
            reply = conn.recv(4096).decode()
            conn.close()
            self.assertIn(" 400 ", reply.splitlines()[0], value)
        self.assertTrue(get(self.base, "/api/models")["ok"])                     # server is still alive

    def test_find_chromium_prefers_what_is_installed(self):
        import shutil
        real = shutil.which
        try:
            shutil.which = lambda n: "/usr/bin/chromium" if n == "chromium" else None
            self.assertEqual(app.find_chromium(), "/usr/bin/chromium")
        finally:
            shutil.which = real

    def test_app_mode_launches_chromium_with_app_flag(self):
        calls = []
        real_find, real_popen, real_wb = app.find_chromium, app.subprocess.Popen, app.webbrowser.open
        try:
            app.find_chromium = lambda: "/usr/bin/chromium"
            app.subprocess.Popen = lambda cmd, **k: calls.append(cmd)
            app.webbrowser.open = lambda u: calls.append(("browser", u))
            app.open_page("http://127.0.0.1:8000", True)
            app.open_page("http://127.0.0.1:8000", False)
            app.find_chromium = lambda: None
            app.open_page("http://127.0.0.1:8000", True)                         # no Chromium: falls back
        finally:
            app.find_chromium, app.subprocess.Popen, app.webbrowser.open = real_find, real_popen, real_wb
        self.assertEqual(calls[0][:2], ["/usr/bin/chromium", "--app=http://127.0.0.1:8000"])
        self.assertEqual(calls[1], ("browser", "http://127.0.0.1:8000"))
        self.assertEqual(calls[2], ("browser", "http://127.0.0.1:8000"))


class ParsePasted(unittest.TestCase):
    D = date(2026, 10, 26)

    def test_splits_on_dashes_and_reads_headers(self):
        text = ("From: office@x.edu\nSubject: Exam one\nDate: 2026-11-02\n\nBody one\n\n---\n\n"
                "Subject: Exam two\n\nBody two\n=====\nBody three line")
        e = app.parse_pasted(text, self.D)
        self.assertEqual([x["subject"] for x in e], ["Exam one", "Exam two", "Body three line"])
        self.assertEqual(e[0]["sent"], date(2026, 11, 2))
        self.assertEqual(e[1]["sent"], self.D)               # falls back to the date box
        self.assertEqual(e[0]["body"], "Body one")

    def test_no_headers_uses_first_line_as_subject(self):
        e = app.parse_pasted("Placement meeting next Friday at 3 PM in Room 204.", self.D)
        self.assertEqual(len(e), 1)
        self.assertTrue(e[0]["subject"].startswith("Placement meeting"))
        self.assertIn("Room 204", e[0]["body"])

    def test_signature_dashes_do_not_split(self):
        e = app.parse_pasted("Exam on 5 December 2026\n-- \nRegistrar\n", self.D)
        self.assertEqual(len(e), 1)

    def test_blank_input(self):
        self.assertEqual(app.parse_pasted("\n  \n---\n  ", self.D), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
