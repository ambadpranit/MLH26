#!/usr/bin/env python3
"""
ExamRadar - a local web page for mail2cal.

Paste emails (or connect Gmail), press one button, get a web calendar of your exams with clash
warnings and a "needs checking" list. The page talks only to this small server on your laptop,
and this server talks only to your local Ollama (and, if you choose, to Gmail over read-only IMAP).
No cloud AI, no API keys.

  python app.py                          # opens http://127.0.0.1:8000
  python app.py --model qwen3:1.7b       # model pre-selected in the page
  python app.py --inbox inbox_real       # adds a "Load fetched inbox" button
                                         # (folder made by: python mail2cal.py fetch ... --out inbox_real)
"""
import argparse
import imaplib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import webbrowser
from datetime import date
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mail2cal as m

try:
    import make_samples
except ImportError:          # the sample button simply hides itself
    make_samples = None

MAX_BODY = 2_000_000
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html")
WEB = os.path.dirname(PAGE)
STATIC_TYPES = {".webmanifest": "application/manifest+json", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml",
                ".ico": "image/x-icon"}
HOST_OK = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")
SEPARATOR = re.compile(r"(?m)^\s*(?:-{3,}|={3,})\s*$")
HEADER = re.compile(r"\s*(subject|date|from|to|cc)\s*:\s*(.*)$", re.I)
LOOPBACK = ("127.0.0.1", "localhost", "[::1]", "::1")

# --------------------------------------------------------------------------- pasted text -> emails

def _parse_sent(value, default):
    try:
        return parsedate_to_datetime(value).date()
    except Exception:
        pass
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return default


def parse_pasted(text, default_sent):
    """Split pasted text into emails. Emails are separated by a line of --- (or ===).
    Optional Subject:/Date: lines at the top of an email are used; From:/To:/Cc: are ignored.
    Without a Subject: line, the first line of the email is used as the subject."""
    out = []
    for chunk in SEPARATOR.split(text):
        lines = chunk.strip().splitlines()
        if not lines:
            continue
        subject, sent, i = "", default_sent, 0
        while i < len(lines):
            h = HEADER.match(lines[i])
            if not h:
                break
            key, val = h[1].lower(), h[2].strip()
            if key == "subject":
                subject = val
            elif key == "date":
                sent = _parse_sent(val, default_sent)
            i += 1
        body = "\n".join(lines[i:]).strip()
        if not subject:
            subject = next((x.strip() for x in body.splitlines() if x.strip()), "")[:80]
        if body or subject:
            out.append({"id": f"pasted-{len(out) + 1}", "subject": subject, "sent": sent, "body": body})
    return out


def emails_to_text(emails):
    return "\n\n---\n\n".join(
        f"Subject: {e['subject']}\nDate: {e['sent'].isoformat()}\n\n{e['body']}" for e in emails)

# --------------------------------------------------------------------------- results -> JSON

def _hm(t):
    return f"{t[0]:02d}:{t[1]:02d}" if t else None


def ev_json(ev):
    d = ev.get("date")
    return {"title": ev.get("title", ""), "kind": ev.get("kind", "other"),
            "date": d.isoformat() if d else None, "start": _hm(ev.get("start")), "end": _hm(ev.get("end")),
            "venue": ev.get("venue", ""), "quote": ev.get("quote", ""), "subject": ev.get("subject", ""),
            "source": ev.get("source", ""), "date_text": ev.get("date_text", ""),
            "time_text": ev.get("time_text", ""), "level": ev.get("level"), "reasons": ev.get("reasons", [])}


def friendly_error(ex, model):
    if isinstance(ex, urllib.error.URLError):
        return (f"Cannot reach Ollama at {m.HOST}. Open the Ollama app (or run: ollama serve) "
                f"and try again.")
    if isinstance(ex, RuntimeError):
        msg = str(ex)
        try:
            msg = json.loads(msg).get("error", msg)
        except (ValueError, AttributeError):
            pass
        hint = f" Run: ollama pull {model}" if "not found" in msg.lower() else ""
        return f"Ollama said: {msg}.{hint}"
    return f"{type(ex).__name__}: {ex}"

def gmail_error(ex, host):
    """Plain-English reason for a failed mailbox login or download. Never echoes the password."""
    if isinstance(ex, imaplib.IMAP4.error):
        msg = str(ex)
        if re.search(r"AUTHENTICATIONFAILED|invalid credentials|application-specific|web login", msg, re.I):
            return ("Gmail refused the login. Use a 16-letter App Password (not your normal password): "
                    "turn on 2-Step Verification, then create one at myaccount.google.com/apppasswords.")
        return f"The mail server said: {msg}"
    if isinstance(ex, (socket.timeout, TimeoutError)):
        return f"Timed out talking to {host}. Check your internet connection and try again."
    if isinstance(ex, OSError):
        return f"Could not connect to {host} ({ex.__class__.__name__}). Check your internet connection."
    return f"{type(ex).__name__}: {ex}"


def email_summary(e):
    snippet = re.sub(r"\s+", " ", e["body"]).strip()[:160]
    return {"id": e["id"], "subject": e["subject"] or "(no subject)", "from": e.get("from", ""),
            "sent": e["sent"].isoformat(), "relevant": m.looks_relevant(e), "snippet": snippet}

# --------------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "ExamRadar"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self):
        """Refuse requests whose Host header is not this machine (blocks DNS-rebinding snooping)."""
        if not self.server.loopback_only:
            return True
        host = self.headers.get("Host", "").rsplit(":", 1)[0] if not self.headers.get("Host", "").startswith("[") \
            else self.headers.get("Host", "").rsplit("]", 1)[0] + "]"
        if host in LOOPBACK:
            return True
        self._json({"error": "forbidden host"}, 403)
        return False

    def do_GET(self):
        if not self._allowed():
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            try:
                with open(PAGE, "rb") as fh:
                    data = fh.read()
            except OSError:
                return self._json({"error": "web/index.html is missing next to app.py"}, 500)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)
        elif path == "/api/config":
            self._json({"default_model": self.server.default_model, "has_inbox": bool(self.server.inbox),
                        "has_sample": make_samples is not None, "has_gmail": True, "version": "2.0"})
        elif path == "/api/models":
            try:
                names = sorted(x["name"] for x in m._get("/api/tags").get("models", []))
                self._json({"ok": True, "models": names, "host": m.HOST})
            except Exception as ex:
                self._json({"ok": False, "models": [], "error": friendly_error(ex, "")})
        elif path == "/api/sample" and make_samples is not None:
            emails = [{"subject": s, "sent": date(2026, 10, 26), "body": b} for _, s, b, _ in make_samples.SAMPLES]
            self._json({"text": emails_to_text(emails).replace(
                "Date: 2026-10-26", "Date: " + make_samples.SENT)})
        elif path == "/api/inbox" and self.server.inbox:
            try:
                emails = m.load_emails(self.server.inbox)
                self._json({"text": emails_to_text(emails), "count": len(emails)})
            except OSError as ex:
                self._json({"error": f"Could not read {self.server.inbox}: {ex}"}, 500)
        elif not self._static(path):
            self._json({"error": "not found"}, 404)

    def _static(self, path):
        """Serve the few app files (manifest, service worker, icons) from web/. Nothing else, no '..'."""
        full = os.path.realpath(os.path.join(WEB, path.lstrip("/")))
        kind = STATIC_TYPES.get(os.path.splitext(full)[1])
        allowed = {os.path.realpath(WEB)} | {os.path.realpath(os.path.join(WEB, d)) for d in ("icons", "css", "js")}
        if not kind or os.path.dirname(full) not in allowed:
            return False
        try:
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError:
            return False
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_POST(self):
        if not self._allowed():
            return
        route = self.path.split("?", 1)[0]
        if route not in ("/api/extract", "/api/gmail/fetch"):
            return self._json({"error": "not found"}, 404)
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._json({"error": "expected application/json"}, 415)
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "bad Content-Length"}, 400)
        if n < 0:
            return self._json({"error": "bad Content-Length"}, 400)
        if n > MAX_BODY:
            return self._json({"error": "too much text"}, 413)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
            assert isinstance(payload, dict)
        except (ValueError, AssertionError):
            return self._json({"error": "bad JSON"}, 400)
        (self._gmail_fetch if route == "/api/gmail/fetch" else self._extract)(payload)

    def _gmail_fetch(self, p):
        """Read-only download of recent mail. The app password is used for this one call and dropped.
        Bodies stay in this server's memory; the page only gets short summaries to tick."""
        user, password = str(p.get("user") or "").strip(), str(p.get("password") or "").replace(" ", "")
        host = str(p.get("host") or "imap.gmail.com").strip()
        if not user or not password:
            return self._json({"ok": False, "error": "Enter your Gmail address and an app password."}, 400)
        if not HOST_OK.match(host):
            return self._json({"ok": False, "error": "That mail server name does not look right."}, 400)
        try:
            days = min(max(int(p.get("days") or 30), 1), 180)
            limit = min(max(int(p.get("limit") or 150), 1), 500)
        except (TypeError, ValueError):
            return self._json({"ok": False, "error": "days and limit must be numbers."}, 400)
        try:
            emails, total = m.fetch_imap(host, user, password, days, limit)
        except Exception as ex:
            return self._json({"ok": False, "error": gmail_error(ex, host)}, 200)
        self.server.fetched = {e["id"]: e for e in emails}
        self._json({"ok": True, "total": total, "days": days, "emails": [email_summary(e) for e in emails]})

    def _extract(self, payload):
        """Stream newline-delimited JSON: progress lines, then one 'done' (or 'error') line."""
        model = str(payload.get("model") or self.server.default_model)
        emails = parse_pasted(str(payload.get("text", "")), _parse_sent(str(payload.get("sent") or ""), date.today()))
        ids = payload.get("gmail_ids")
        if isinstance(ids, list):
            have = self.server.fetched
            emails += [have[i] for i in ids if isinstance(i, str) and i in have]
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def send(obj):
            self.wfile.write((json.dumps(obj) + "\n").encode())
            self.wfile.flush()

        try:
            send({"type": "progress", "done": 0, "total": len(emails)})
            t0 = time.time()
            good, review, st = m.run_pipeline(
                emails, model, log=False,
                progress=lambda i, n, e, status, ng, nr: send(
                    {"type": "progress", "done": i, "total": n, "email": e["subject"], "status": status}))
            clashes = m.find_clashes(good)
            index = {id(ev): k for k, ev in enumerate(good)}
            secs = time.time() - t0
            send({"type": "done",
                  "events": [ev_json(ev) for ev in good],
                  "clashes": [{"a": index[id(c["a"])], "b": index[id(c["b"])],
                               "level": c["level"], "reason": c["reason"]} for c in clashes],
                  "review": [ev_json(r) for r in review],
                  "ics": m.build_ics(good),
                  "stats": {"emails": len(emails), "skipped": st["skipped"], "model": model,
                            "seconds": secs, "tokens": st["tokens"],
                            "tps": st["tokens"] / (st["ns"] / 1e9) if st["ns"] else 0}})
        except (BrokenPipeError, ConnectionResetError):
            return                      # the browser tab was closed
        except Exception as ex:
            try:
                send({"type": "error", "message": friendly_error(ex, model)})
            except OSError:
                pass


def make_server(host, port, model, inbox=None):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.default_model, srv.inbox, srv.fetched = model, inbox, {}
    srv.loopback_only = host in ("127.0.0.1", "localhost", "::1")
    return srv


def find_chromium():
    """Path of an installed Chromium-based browser (Chrome, Chromium, Edge, Brave), or None."""
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
                 "microsoft-edge", "microsoft-edge-stable", "brave-browser"):
        path = shutil.which(name)
        if path:
            return path
    for path in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                 "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"):
        if os.path.exists(path):
            return path
    for base in filter(None, (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                              os.environ.get("LOCALAPPDATA"))):
        for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe",
                    r"BraveSoftware\Brave-Browser\Application\brave.exe", r"Chromium\Application\chrome.exe"):
            if os.path.exists(os.path.join(base, rel)):
                return os.path.join(base, rel)
    return None


def open_page(url, as_app):
    """--app opens a Chromium window with no tabs or address bar, like a desktop program."""
    exe = find_chromium() if as_app else None
    if exe:
        try:
            subprocess.Popen([exe, f"--app={url}", "--window-size=1280,860"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass
    if as_app:
        print("No Chrome/Chromium/Edge found, opening your normal browser instead.", file=sys.stderr)
    webbrowser.open(url)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen3:1.7b", help="model pre-selected in the page")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1", help="leave as is unless you know why you need to change it")
    ap.add_argument("--inbox", help="folder of fetched .eml files, adds a 'Load fetched inbox' button")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--app", action="store_true",
                    help="open in a Chromium 'app window' (no tabs or address bar) instead of a browser tab")
    a = ap.parse_args()
    srv = make_server(a.host, a.port, a.model, a.inbox)
    url = f"http://{a.host}:{a.port}"
    print(f"ExamRadar is running at {url}   (Ctrl+C to stop)")
    print(f"Using Ollama at {m.HOST}")
    if not a.no_browser:
        threading.Timer(0.8, lambda: open_page(url, a.app)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
