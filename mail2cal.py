#!/usr/bin/env python3
"""
mail2cal - read emails locally, extract exam dates / events with a local Ollama
model, and write an .ics calendar you can import into Google/Outlook/Apple Calendar.

  python mail2cal.py run   inbox/ --model qwen2.5:7b --out out/
  python mail2cal.py bench inbox/ --model qwen2.5:7b --runs 3 --expected inbox/expected.json
  python mail2cal.py fetch --host imap.gmail.com --user you@gmail.com --days 30 --out inbox/

Design (the "narrow agent" part):
  * The model only COPIES text: title, date as written, time as written, venue, quote.
  * Code does all date math, checks every quote exists in the email, and splits results into
      calendar.ics   - confident events
      review.md      - anything uncertain or unverifiable (never silently guessed)
  * Read-only on mail. Nothing is sent, deleted, or uploaded anywhere.
"""
import argparse
import email
import glob
import hashlib
import html
import json
import mailbox
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from email import policy
from email.utils import parsedate_to_datetime

HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
if not HOST.startswith("http"):
    HOST = "http://" + HOST

# --------------------------------------------------------------------------- Ollama

def _post(path, payload, timeout=300):
    req = urllib.request.Request(HOST + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as ex:  # e.g. model not pulled
        raise RuntimeError(ex.read().decode(errors="replace")) from None


def _get(path):
    with urllib.request.urlopen(HOST + path, timeout=10) as r:
        return json.loads(r.read())


SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "kind": {"type": "string", "enum": ["exam", "deadline", "event", "meeting", "other"]},
            "date_text": {"type": "string"},
            "time_text": {"type": "string"},
            "venue": {"type": "string"},
            "quote": {"type": "string"},
        },
        "required": ["title", "kind", "date_text", "time_text", "venue", "quote"],
    }}},
    "required": ["events"],
}

SYSTEM = (
    "You extract calendar events (exams, deadlines, events, meetings) from ONE email.\n"
    "Rules:\n"
    "- Only include things that have a date written in the email.\n"
    "- Copy title words, date_text, time_text, venue and quote EXACTLY from the email. "
    "Do not reformat or convert dates. Do not calculate dates.\n"
    "- quote is the single line or sentence that contains the date.\n"
    "- If a time or venue is not written, use an empty string.\n"
    "- If the email has no dated event, return {\"events\": []}."
)


def llm_extract(model, subject, sent_iso, body, retries=1):
    """Ask the local model for events. Returns (events or None, stats). Retries once on bad JSON."""
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Email sent on: {sent_iso}\nSubject: {subject}\n\n{body[:5000]}"}]
    stats = {"tokens": 0, "ns": 0}
    for _ in range(retries + 1):
        r = _post("/api/chat", {"model": model, "messages": msgs, "stream": False, "format": SCHEMA,
                                "options": {"temperature": 0, "num_ctx": 4096}})
        stats["tokens"] += r.get("eval_count", 0)
        stats["ns"] += r.get("eval_duration", 0)
        try:
            events = json.loads(r["message"]["content"])["events"]
            assert isinstance(events, list)
            return events, stats
        except (ValueError, KeyError, TypeError, AssertionError):
            msgs.append({"role": "user", "content": "That was not valid JSON for the schema. "
                                                    "Reply again with only the JSON object."})
    return None, stats

# --------------------------------------------------------------------------- email loading

def _body(msg):
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except Exception:
        text = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def parse_message(name, msg):
    """One parsed email.message -> the dict the pipeline works on."""
    try:
        sent = parsedate_to_datetime(msg["Date"]).date()
    except Exception:
        sent = date.today()
    return {"id": name, "subject": str(msg["Subject"] or ""), "from": str(msg["From"] or ""),
            "sent": sent, "body": _body(msg)}


def load_emails(path):
    """Load .eml files from a folder (or one .eml/.mbox file, e.g. a Google Takeout export)."""
    if os.path.isfile(path):
        files = [path]
    else:
        files = sorted(glob.glob(os.path.join(path, "*.eml")) + glob.glob(os.path.join(path, "*.mbox")))
    out = []
    for f in files:
        if f.endswith(".mbox"):
            box = mailbox.mbox(f, factory=lambda fh: email.message_from_binary_file(fh, policy=policy.default))
            msgs = [(f"{os.path.basename(f)}#{i}", m) for i, m in enumerate(box)]
            box.close()
        else:
            with open(f, "rb") as fh:
                msgs = [(os.path.basename(f), email.message_from_binary_file(fh, policy=policy.default))]
        for name, m in msgs:
            out.append(parse_message(name, m))
    return out


def imap_date(d):
    """IMAP wants 05-Oct-2026 in English whatever the computer's language is."""
    return f"{d.day:02d}-{_M[d.month - 1].title()}-{d.year}"


def fetch_imap(host, user, password, days=30, limit=150, port=993):
    """Read-only IMAP download of recent INBOX mail (Gmail with an app password, or any IMAP server).
    Returns (emails, total_in_window). Newest first. Messages are opened with BODY.PEEK on a
    read-only mailbox, so nothing is marked as read, moved or deleted. Only the first 150 KB of each
    message is downloaded, which is plenty for the text and keeps big attachments out."""
    import imaplib
    c = imaplib.IMAP4_SSL(host, port, timeout=30)
    try:
        c.login(user, password)
        c.select("INBOX", readonly=True)
        _, data = c.search(None, "SINCE", imap_date(date.today() - timedelta(days=days)))
        nums = data[0].split()
        out = []
        for num in reversed(nums[-limit:]):
            try:
                typ, msg = c.fetch(num, "(BODY.PEEK[]<0.150000>)")
                if typ != "OK":
                    continue
                raw = next((p[1] for p in msg if isinstance(p, tuple)), None)
                if raw:
                    out.append(parse_message(f"gmail-{num.decode()}",
                                             email.message_from_bytes(raw, policy=policy.default)))
            except (imaplib.IMAP4.error, ValueError):
                continue                      # skip one unreadable message, keep the rest
        return out, len(nums)
    finally:
        try:
            c.logout()
        except Exception:
            pass


def looks_relevant(e):
    """Cheap keyword test (no model): does this email even mention exams, deadlines, events...?"""
    return bool(KEYWORDS.search(norm(e["subject"] + " " + e["body"])))


def cmd_fetch(a):
    """Read-only IMAP download into .eml files. Use an app password in env var MAIL_PASS."""
    import imaplib
    pw = os.environ.get("MAIL_PASS") or sys.exit("Set MAIL_PASS to an app password (not your main password).")
    c = imaplib.IMAP4_SSL(a.host, timeout=30)
    c.login(a.user, pw)
    c.select("INBOX", readonly=True)
    since = (date.today() - timedelta(days=a.days)).strftime("%d-%b-%Y")
    _, data = c.search(None, "SINCE", since)
    os.makedirs(a.out, exist_ok=True)
    nums = data[0].split()
    for i, num in enumerate(nums):
        _, msg = c.fetch(num, "(BODY.PEEK[])")
        with open(os.path.join(a.out, f"{i:04d}.eml"), "wb") as f:
            f.write(msg[0][1])
    c.logout()
    print(f"Saved {len(nums)} emails to {a.out}/")

# --------------------------------------------------------------------------- date / time parsing (code, not the model)

_M = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MONTH_RE = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
            r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _mk(y, mo, d, flag=None):
    try:
        return date(y, mo, d), flag
    except ValueError:
        return None, "invalid calendar date"


def _no_year(mo, d, sent):
    for y in (sent.year, sent.year + 1):
        try:
            c = date(y, mo, d)
        except ValueError:
            continue
        if c >= sent - timedelta(days=30):
            return c, None
    return None, "invalid calendar date"


def parse_date(text, sent):
    """Return (date or None, flag). A flag means 'a human should confirm this'."""
    t = " ".join(text.lower().replace(",", " ").split())

    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3]))

    m = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b", t)
    if m:
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        y += 2000 if y < 100 else 0
        if mo > 12 >= d:                       # 11/25/2026 can only be month-first
            return _mk(y, d, mo)
        flag = "numeric date, read as day/month - confirm" if (d <= 12 and mo <= 12 and d != mo) else None
        return _mk(y, mo, d, flag)

    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:-|–|—|to|till|until)\s*\d{{1,2}}(?:st|nd|rd|th)?"
                  rf"\s+(?:of\s+)?{MONTH_RE}\b(?:\s+(\d{{4}}))?", t)
    if m:
        mo = _M.index(m[2][:3]) + 1
        dt, _ = _mk(int(m[3]), mo, int(m[1])) if m[3] else _no_year(mo, int(m[1]), sent)
        return dt, "date range - start date used, check the end date"

    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{MONTH_RE}\b\.?(?:\s+(\d{{4}}))?", t)
    if m:
        d, mo = int(m[1]), _M.index(m[2][:3]) + 1
        return _mk(int(m[3]), mo, d) if m[3] else _no_year(mo, d, sent)

    m = re.search(rf"\b{MONTH_RE}\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:\s+(\d{{4}}))?", t)
    if m:
        mo, d = _M.index(m[1][:3]) + 1, int(m[2])
        return _mk(int(m[3]), mo, d) if m[3] else _no_year(mo, d, sent)

    if re.search(r"\btoday\b", t):
        return sent, "relative date 'today' - confirm"
    if re.search(r"\btomorrow\b", t):
        return sent + timedelta(days=1), "relative date 'tomorrow' - confirm"
    m = re.search(r"\b(" + "|".join(WEEKDAYS) + r")\b", t)
    if m:
        delta = (WEEKDAYS.index(m[1]) - sent.weekday()) % 7 or 7
        return sent + timedelta(days=delta), f"relative date '{text.strip()}' - confirm"
    return None, "no usable date in the date text"


def parse_times(text):
    """All clock times in a string, as [(hour, minute), ...]. Handles '10:00 AM', '2 pm', '14:30'."""
    found = []
    for m in re.finditer(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?m\b|\b([01]?\d|2[0-3]):([0-5]\d)\b", text.lower()):
        if m.group(3):
            h, mi = int(m.group(1)), int(m.group(2) or 0)
            if not 1 <= h <= 12 or mi > 59:
                continue
            h = (h % 12) + (12 if m.group(3) == "p" else 0)
        else:
            h, mi = int(m.group(4)), int(m.group(5))
        found.append((h, mi))
    return found

# --------------------------------------------------------------------------- verification pipeline

KEYWORDS = re.compile(r"exam|quiz|viva|deadline|\bdue\b|submi|assignment|seminar|workshop|interview|meeting|"
                      r"schedule|date ?sheet|timetable|\bevent|fest\b|registration|last date|lecture|webinar|"
                      r"holiday|hackathon|conference|\btest\b|\bclass(es)?\b|result", re.I)


def norm(s):
    return re.sub(r"\s+", " ", s.lower()).strip()


def loose(s):
    """Letters and digits only, lower case, single spaces. 'Date: Wed' and 'Date | Wed' and '**Date** Wed'
    all become 'date wed', so table cells, bullets and markdown in an email do not break the proof-reading."""
    return " ".join(re.sub(r"[\W_]+", " ", s.lower()).split())


def quote_in_email(quote, date_text, text):
    """Is this quote really from the email? Exact (ignoring punctuation) is best. A small model often re-adds a colon,
    a bullet or a dash that the email did not have, or drops a word, so also accept a quote whose words are almost
    all in the email AND whose date text is really there. A made-up date never passes the second test."""
    q, t = loose(quote), loose(text)
    if not q:
        return False
    if q in t:
        return True
    words = q.split()
    have = set(t.split())
    return len(words) >= 3 and sum(w in have for w in words) / len(words) >= 0.8 and bool(loose(date_text)) and loose(date_text) in t


def process_email(e, model, llm):
    """One email -> (confident events, review items, model stats, status)."""
    text = norm(e["subject"] + " " + e["body"])
    if not KEYWORDS.search(text):
        return [], [], {"tokens": 0, "ns": 0}, "skipped"
    events, stats = llm(model, e["subject"], e["sent"].isoformat(), e["body"])
    if events is None:
        item = {"source": e["id"], "title": e["subject"], "level": "rejected", "quote": "",
                "date_text": "", "time_text": "",
                "reasons": ["model gave invalid JSON twice - read this email yourself"]}
        return [], [item], stats, "model-failed"

    good, review = [], []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        item = {"source": e["id"], "subject": e["subject"], "kind": ev.get("kind", "other")}
        for k in ("title", "date_text", "time_text", "venue", "quote"):
            item[k] = str(ev.get(k) or "").strip()
        reasons, hard = [], False

        if not quote_in_email(item["quote"], item["date_text"], text):
            reasons.append("quote not found in the email (possible hallucination)")
            hard = True
        elif loose(item["date_text"]) not in loose(text):
            reasons.append("date text was not copied verbatim from the email")
        if not item["title"]:
            reasons.append("missing title")

        d, flag = parse_date(item["date_text"], e["sent"])
        if d is None:
            reasons.append(flag)
        else:
            if flag:
                reasons.append(flag)
            if not (e["sent"] - timedelta(days=2) <= d <= e["sent"] + timedelta(days=400)):
                reasons.append(f"date {d.isoformat()} is outside the plausible window - confirm")

        start = end = None
        if item["time_text"]:
            ts = parse_times(item["time_text"])
            if not ts:
                reasons.append(f"time not understood: '{item['time_text']}'")
            else:
                start, end = ts[0], (ts[1] if len(ts) > 1 else None)

        item.update(date=d, start=start, end=end)
        if reasons:
            item.update(reasons=reasons, level="rejected" if hard else "confirm")
            review.append(item)
        else:
            good.append(item)
    return good, review, stats, "ok"


def dedupe(events):
    seen, out = set(), []
    for ev in events:
        k = (norm(ev["title"]), ev["date"], ev["start"])
        if k not in seen:
            seen.add(k)
            out.append(ev)
    return sorted(out, key=lambda ev: (ev["date"], ev["start"] or (0, 0)))


def dedupe_review(items):
    seen, out = set(), []
    for it in items:
        k = (norm(it.get("title", "")), loose(it.get("date_text", "")), loose(it.get("quote", "")), tuple(it.get("reasons", [])))
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


DEFAULT_MINUTES = {"exam": 180}   # assumed length when an email gives only a start time


def _span(ev):
    """(start_min, end_min, end_was_written) for an event that has a start time."""
    s = ev["start"][0] * 60 + ev["start"][1]
    if ev.get("end"):
        e = ev["end"][0] * 60 + ev["end"][1]
        if e > s:
            return s, e, True
    return s, s + DEFAULT_MINUTES.get(ev["kind"], 60), False


def find_clashes(events):
    """Pairs of events on the same day whose times overlap. Pure code, no model.
    Deadlines don't block time, so they are ignored.
      level 'clash' = both time ranges were written and overlap (or both start at the same minute)
      level 'maybe' = overlap only under an assumed length, or one of the two has no time written"""
    out = []
    blocking = [ev for ev in events if ev["kind"] != "deadline"]
    for i, a in enumerate(blocking):
        for b in blocking[i + 1:]:
            if a["date"] != b["date"]:
                continue
            if not a["start"] or not b["start"]:
                out.append({"a": a, "b": b, "level": "maybe",
                            "reason": "same day, and one of them has no time written"})
                continue
            (s1, e1, w1), (s2, e2, w2) = _span(a), _span(b)
            if s1 == s2 or (s1 < e2 and s2 < e1):
                sure = s1 == s2 or (w1 and w2)
                out.append({"a": a, "b": b, "level": "clash" if sure else "maybe",
                            "reason": "the written times overlap" if sure
                            else "overlap depends on an assumed length (no end time written)"})
    return out


def run_pipeline(emails, model, llm=llm_extract, log=True, progress=None):
    good, review, tot = [], [], {"tokens": 0, "ns": 0, "skipped": 0}
    for i, e in enumerate(emails, 1):
        t0 = time.time()
        g, r, st, status = process_email(e, model, llm)
        good += g
        review += r
        tot["tokens"] += st["tokens"]
        tot["ns"] += st["ns"]
        tot["skipped"] += status == "skipped"
        if log:
            print(f"[{i}/{len(emails)}] {e['id']}: {status}, {len(g)} to calendar, {len(r)} to review "
                  f"({time.time() - t0:.1f}s)", file=sys.stderr)
        if progress:
            progress(i, len(emails), e, status, len(g), len(r))
    return dedupe(good), dedupe_review(review), tot

# --------------------------------------------------------------------------- output files

def esc(s):
    return (s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
             .replace("\r", "").replace("\n", "\\n"))


def fold(line):
    """RFC 5545 line folding at 75 octets, without splitting a UTF-8 character."""
    b, parts, limit = line.encode(), [], 75
    while len(b) > limit:
        cut = limit
        while (b[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(b[:cut].decode())
        b, limit = b[cut:], 74
    parts.append(b.decode())
    return "\r\n ".join(parts)


def build_ics(events):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//mail2cal//local//EN", "CALSCALE:GREGORIAN"]
    for ev in events:
        uid = hashlib.sha1(f"{ev['title']}|{ev['date']}|{ev['start']}".encode()).hexdigest()[:20]
        L += ["BEGIN:VEVENT", f"UID:{uid}@mail2cal", f"DTSTAMP:{stamp}", "SUMMARY:" + esc(ev["title"])]
        if ev["start"]:
            s = datetime.combine(ev["date"], dtime(*ev["start"]))
            en = (datetime.combine(ev["date"], dtime(*ev["end"]))
                  if ev["end"] and ev["end"] > ev["start"] else s + timedelta(hours=1))
            L += [f"DTSTART:{s:%Y%m%dT%H%M%S}", f"DTEND:{en:%Y%m%dT%H%M%S}"]   # floating = your local time
        else:
            L += [f"DTSTART;VALUE=DATE:{ev['date']:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{ev['date'] + timedelta(days=1):%Y%m%d}"]
        if ev["venue"]:
            L.append("LOCATION:" + esc(ev["venue"]))
        L.append("DESCRIPTION:" + esc(f"From email: {ev['subject']}\nQuote: {ev['quote']}"))
        if ev["kind"] in ("exam", "deadline"):
            L += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Reminder", "TRIGGER:-P1D", "END:VALARM"]
        L.append("END:VEVENT")
    L.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in L) + "\r\n"


def build_review(review):
    out = ["# Needs a human", "", "Nothing on this page was added to the calendar.", ""]
    for r in review:
        out += [f"- **{r['title'] or '(no title)'}** ({r['level']}) - from `{r['source']}`",
                f"  - why: {'; '.join(r['reasons'])}",
                f"  - as written: {r['date_text']} {r['time_text']}".rstrip(),
                f"  - quote: {r['quote']}", ""]
    return "\n".join(out)


def cmd_run(a):
    emails = load_emails(a.inbox)
    if not emails:
        sys.exit(f"No .eml or .mbox files found in {a.inbox}")
    good, review, st = run_pipeline(emails, a.model)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "calendar.ics"), "w", newline="", encoding="utf-8") as f:
        f.write(build_ics(good))
    with open(os.path.join(a.out, "review.md"), "w", encoding="utf-8") as f:
        f.write(build_review(review))
    with open(os.path.join(a.out, "events.json"), "w", encoding="utf-8") as f:
        json.dump([{**g, "date": g["date"].isoformat()} for g in good], f, indent=2, ensure_ascii=False)
    tps = st["tokens"] / (st["ns"] / 1e9) if st["ns"] else 0
    print(f"\n{len(emails)} emails read, {st['skipped']} skipped (no event keywords).")
    print(f"{len(good)} events -> {a.out}/calendar.ics   |   {len(review)} need a human -> {a.out}/review.md")
    print(f"Model speed: {tps:.1f} tokens/sec")

# --------------------------------------------------------------------------- benchmark (hackathon submission table)

def score(good, expected):
    got = {(g["source"], g["date"].isoformat()) for g in good}
    want = {(k, d) for k, ds in expected.items() for d in ds}
    tp = len(got & want)
    return (tp / len(got) if got else 1.0), (tp / len(want) if want else 1.0)


def hardware():
    h = {"os": platform.platform(), "cpu": platform.processor() or platform.machine(),
         "cores": os.cpu_count(), "ram_gb": "?", "gpu": "none detected"}
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                h["cpu"] = line.split(":", 1)[1].strip()
                break
        for line in open("/proc/meminfo"):
            if line.startswith("MemTotal"):
                h["ram_gb"] = round(int(line.split()[1]) / 1048576, 1)
                break
    except OSError:
        pass
    if platform.system() == "Darwin":
        try:
            h["cpu"] = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                                      text=True, timeout=5).stdout.strip() or h["cpu"]
            h["ram_gb"] = round(int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                                   text=True, timeout=5).stdout) / 2**30, 1)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            h["gpu"] = out.stdout.strip().replace("\n", " | ")
    except (OSError, subprocess.SubprocessError):
        pass
    return h


def model_info(model):
    d = _post("/api/show", {"model": model, "name": model}).get("details", {})
    return {"family": d.get("family", "?"), "parameter_size": d.get("parameter_size", "?"),
            "quantization": d.get("quantization_level", "?")}


def cmd_bench(a):
    emails = load_emails(a.inbox)
    exp = json.load(open(a.expected, encoding="utf-8")) if a.expected else None
    info, hw = model_info(a.model), hardware()
    try:
        ver = _get("/api/version").get("version", "?")
    except Exception:
        ver = "?"
    rows = []
    for i in range(1, a.runs + 1):
        t0 = time.time()
        good, review, st = run_pipeline(emails, a.model, log=False)
        wall = time.time() - t0
        tps = st["tokens"] / (st["ns"] / 1e9) if st["ns"] else 0.0
        p, r = score(good, exp) if exp else (None, None)
        rows.append((i, wall, st["tokens"], tps, p, r, len(review)))
        print(f"run {i}: {tps:.1f} tok/s, {wall:.1f}s" + (f", precision {p:.2f}, recall {r:.2f}" if exp else ""))
    f2 = lambda x: "-" if x is None else f"{x:.2f}"
    md = [f"# Benchmark: {a.model}", "",
          f"- Model: {a.model} ({info['family']}, {info['parameter_size']} parameters, {info['quantization']})",
          f"- Ollama version: {ver}",
          f"- Hardware: {hw['cpu']}, {hw['cores']} cores, {hw['ram_gb']} GB RAM, GPU: {hw['gpu']}",
          f"- OS: {hw['os']}",
          f"- Task: {len(emails)} emails -> calendar (temperature 0, tokens/sec = output tokens / generation time)", "",
          "| Run | Wall time (s) | Output tokens | Tokens/sec | Precision | Recall | Sent to review |",
          "|---|---|---|---|---|---|---|"]
    for i, wall, tok, tps, p, r, nrev in rows:
        md.append(f"| {i} | {wall:.1f} | {tok} | {tps:.1f} | {f2(p)} | {f2(r)} | {nrev} |")
    md.append(f"| **mean** | {sum(x[1] for x in rows) / len(rows):.1f} | | "
              f"**{sum(x[3] for x in rows) / len(rows):.1f}** | | | |")
    with open("bench.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\nWrote bench.md")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="emails -> calendar.ics + review.md")
    r.add_argument("inbox", help="folder of .eml files, or one .mbox/.eml file")
    r.add_argument("--model", default="qwen2.5:7b")
    r.add_argument("--out", default="out")
    r.set_defaults(fn=cmd_run)
    b = sub.add_parser("bench", help="repeat the run N times and write bench.md")
    b.add_argument("inbox")
    b.add_argument("--model", default="qwen2.5:7b")
    b.add_argument("--runs", type=int, default=3)
    b.add_argument("--expected", help="JSON {email file: [YYYY-MM-DD, ...]} for accuracy scoring")
    b.set_defaults(fn=cmd_bench)
    f = sub.add_parser("fetch", help="download recent emails over IMAP (read-only)")
    f.add_argument("--host", default="imap.gmail.com")
    f.add_argument("--user", required=True)
    f.add_argument("--days", type=int, default=30)
    f.add_argument("--out", default="inbox")
    f.set_defaults(fn=cmd_fetch)
    a = ap.parse_args()
    try:
        a.fn(a)
    except urllib.error.URLError as ex:
        sys.exit(f"Cannot reach Ollama at {HOST} ({ex.reason}). Start it with: ollama serve")
    except RuntimeError as ex:
        sys.exit(f"Ollama error: {ex}\nIf the model is missing, run: ollama pull {getattr(a, 'model', 'MODEL')}")


if __name__ == "__main__":
    main()
