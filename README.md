# ExamRadar

A local-first "exam radar": it reads your emails (pasted, sample, or pulled read-only from Gmail), uses a small on-device LLM to copy out exams, deadlines and events, verifies every date in code, shows them on a calendar in your browser, and **reminds you** before each exam or deadline.

|  **Track:** Best Local Agentic Tool  |  **Team:** Guaranteed Losers - Prajesh Sivakumar + Shounak Pimparkar + Abhimanyu Suwalka + Pranit Ambad

## Problem Statement & Target User

**Who:** College and university students whose exam date sheets, quiz notices, assignment deadlines, viva slots and fest announcements arrive scattered across dozens of emails.

**Why it hurts:** Dates are written in many styles (`30/10/2026`, `3 November 2026`, "next Friday"), buried in very long mails, and easy to miss. A missed exam or submission is costly. Cloud AI tools would need access to the student's whole inbox, which is a privacy concern, and a wrongly guessed date is worse than no date at all.

## Approach, Architecture & Demo

**The one main task:** turn a pile of emails into a trustworthy calendar of exams, deadlines and events, and announce them before they happen.

**Narrow-agent design:**

1. **Model only copies.** A local Ollama model extracts `title`, `kind`, `date_text`, `time_text`, `venue` and a verbatim `quote` using a strict JSON schema. It never converts or calculates dates.
2. **Code does the checking.** `mail2cal.py` parses the dates, resolves relative words ("tomorrow", "Friday") against the email's own sent date, confirms the quote really exists in the email, and detects clashes between events on the same day.
3. **Nothing is silently guessed.** Confident events go to the calendar (`calendar.ics`). Ambiguous ones (e.g. `04/11/2026` could be day/month or month/day) or unverifiable ones go to a **Needs checking** list (`review.md`) and are never announced.

```
Emails (paste / sample / Gmail IMAP read-only)
        |
        v
  app.py (local server, 127.0.0.1)  -->  Ollama (local LLM, JSON schema)
        |                                        |
        v                                        v
  mail2cal.py: date parsing, quote check, clash detection
        |
        +--> Browser UI: Schedule, Calendar (month/week), Agenda, Needs checking
        +--> reminders.js: toast / desktop reminders (3 days, tomorrow, today, 1 hour)
        +--> calendar.ics  (Google / Outlook / Apple Calendar, with a 1-day alarm)
```

**UI features:**
- **Schedule view** (default): small month on the left, upcoming items grouped as Today / Tomorrow / This week / Later.
- **Calendar, Agenda and Needs checking tabs**, with clash warnings, subject sidebar, per-event "Add to Google Calendar / Outlook" and `.ics` download.
- **Reminders:** a card for each exam, deadline or quiz that is 3 days away, tomorrow, today or 1 hour away. Each stage is announced once, with a Snooze 1 h button and optional desktop notifications via the bell button. Reminders run while ExamRadar is open.
- **Minimal theme** (default) plus Neon, Aurora, Sunset and Paper, an animated radar and countdown, keyboard shortcuts (`?`), installable as an app (PWA), works offline. No CDN, fonts or frameworks.

**Demo:** Screenshots Link: https://drive.google.com/drive/folders/1qLKwL4JAFnYMNZD3dYbErtLvSAJRYsAf?usp=sharing

## Model Details (name, size, licence, runtime, quantization, hardware)

- **Model, parameters, licence, runtime, quantization:**
  - Model: `qwen3:1.7b` (default for the web app, set in `app.py`). The CLI (`mail2cal.py`) defaults to `qwen2.5:7b`,`gemma3:4b`, and any Ollama model can be chosen with `--model`.
  - Parameters: ~1.7B (Qwen3 1.7B) / ~7B (Qwen2.5 7B) 
  - Licence: Apache-2.0 for Qwen3 1.7B and Qwen2.5 7B (verify on the Ollama model page for the exact tag you pull)
  - Runtime: [Ollama](https://ollama.com) (local HTTP API on `localhost:11434`)
  - Quantization: whatever the Ollama tag ships with (typically Q4_K_M). `python mail2cal.py bench ...` prints the exact value for your tag.
- **Hardware (RAM / OS / CPU or GPU) & External APIs (local vs. internet):**
  - Hardware: 24 GB RAM, Windows 11, GPU model: NVIDIA GeForce RTX 5050 Laptop GPU (8 GB). The 1.7B model is light enough for a typical laptop CPU.
  - OS: Windows with Python 3.10+
  - **Local only:** the AI runs fully on-device. No cloud AI and no API keys.
  - The only optional network use is the Gmail download (read-only IMAP over SSL to `imap.gmail.com`), which you start yourself.

## Setup Instructions

1. **Prerequisites**
   - Python 3.10+
   - [Ollama](https://ollama.com) installed and running (`ollama serve`)
   - A Chromium-based browser is recommended for `--app` mode and install-as-app (any modern browser works for the page itself)
2. **Install dependencies**
   - None. The app uses only the Python standard library.
   - Optional, only for the UI test script: `pip install playwright && playwright install chromium`
3. **Download model**
   ```bash
   ollama pull qwen3:1.7b
   ```
4. **Start app**
   ```bash
   python app.py            # opens http://127.0.0.1:8000
   python app.py --app      # opens in its own app-style Chromium window
   ```
   Other options: `--model qwen2.5:7b --port 8001 --no-browser`
5. **Try example input**
   - In the page, press **Import emails**, click **Load sample emails** (8 demo emails from `sample_emails/`), then press **Find my exams**.
   - Or use the CLI:
     ```bash
     python mail2cal.py run sample_emails/ --model qwen3:1.7b --out out/
     ```
     This writes `out/calendar.ics`, `out/events.json` and `out/review.md`.
   - Click the bell in the top bar to allow desktop reminders.
   - **Gmail (optional):** create a Google *App password* (needs 2-Step Verification), then use **Import emails > Gmail**. Messages are opened read-only (`BODY.PEEK`), so nothing is marked read, moved or deleted. The password is used for one fetch and never saved.

## Results (screenshots / metrics from 3 runs)

> Fill the table from your own machine: `python mail2cal.py bench sample_emails/ --model qwen3:1.7b --runs 3 --expected sample_emails/expected.json` writes `bench.md` with the numbers below. Add screenshots with `python browser_check.py shots`.

| Run | Date accuracy vs `expected.json` | Latency (s) | Tokens/sec |
|-----|----------------------------------|-------------|------------|
| 1   | [ ]                              | [ ]         | [ ]        |
| 2   | [ ]                              | [ ]         | [ ]        |
| 3   | [ ]                              | [ ]         | [ ]        |

**What the saved sample output (`out/`) shows:** the 8 sample emails produce a calendar of confident events (e.g. DBMS assignment on 2026-10-30, tech-fest registration deadline on 2026-11-03), while two are deliberately held back for a human: an ambiguous numeric date (`04/11/2026`) and a relative date ("next Friday") whose text was not copied verbatim.

**Reliability:** 49 automated tests pass (`python -m unittest test_app test_mail2cal`) using a fake Ollama and fake Gmail, so no network or model is needed. `browser_check.py` drives the real UI in headless Chromium.

**Known limitations:**
- Small models can miss events or copy text imperfectly. Such cases land in "Needs checking" instead of the calendar.
- Ambiguous numeric dates are read as day/month and flagged for confirmation.
- Relative dates depend on the email's sent date.
- Reminders only fire while ExamRadar is open. Nothing runs in the background, and emails are read only when you press **Find my exams** (the Gmail app password is deliberately never stored). The `.ics` alarm (1 day before) covers the closed-app case.
- Only the first 150 KB of each Gmail message is read.
- School/work Google accounts may block app passwords (use the Paste tab instead).
- Speed depends heavily on your CPU/GPU.

## Graphify Integration (link to graph) & Future Scope / Licence

Graph: `graphify-out/graph.html` | Shared in Discord: yes | Licence: Apache-2.0

**Future scope:**
- Background reminders that work when the app is closed
- Recurring events and multi-day exams
- More languages and regional date formats
- Direct Google / Outlook Calendar sync
- PDF and attachment date-sheet parsing

## Project Structure

- `app.py`: local server (paste, Gmail, streaming extraction, `--app` launcher)
- `mail2cal.py`: LLM extraction, date checks, clash detection, IMAP fetch, `.ics` writer, benchmark
- `web/`: UI (`index.html`, `css/app.css`, `css/minimal.css`, `js/app.js`, `js/reminders.js`, PWA manifest, service worker, icons)
- `make_samples.py`, `sample_emails/`: demo emails and `expected.json`
- `test_app.py`, `test_mail2cal.py`, `browser_check.py`: tests
