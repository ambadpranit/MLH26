"""Drives the real UI in headless Chromium against a fake Ollama (no model needed).
   python browser_check.py [screenshot_dir]      (needs: pip install playwright && playwright install chromium)"""
import sys, os
from playwright.sync_api import sync_playwright
import app, mail2cal as m, make_samples
from test_app import FakeOllama, serve
from http.server import ThreadingHTTPServer
import tempfile

OUT = sys.argv[1] if len(sys.argv) > 1 else "shots"
os.makedirs(OUT, exist_ok=True)
fake = serve(ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama))
m.HOST = f"http://127.0.0.1:{fake.server_address[1]}"
inbox = tempfile.mkdtemp(); make_samples.write(inbox)
srv = serve(app.make_server("127.0.0.1", 0, "fake:1b", inbox))
base = f"http://127.0.0.1:{srv.server_address[1]}"
problems, checks = [], []

def check(name, cond):
    checks.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)

with sync_playwright() as p:
    br = p.chromium.launch()
    for label, vp in (("desktop", {"width": 1400, "height": 900}), ("mobile", {"width": 390, "height": 844})):
        ctx = br.new_context(viewport=vp, device_scale_factor=1)
        page = ctx.new_page()
        page.on("console", lambda msg: problems.append(("console", msg.text)) if msg.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(("pageerror", str(e))))
        page.clock.install(time=__import__("datetime").datetime(2026, 10, 26, 10, 0))   # fixed "today" for stable shots
        page.goto(base)
        page.wait_for_selector("#impModal[open]", timeout=5000)
        check(f"[{label}] import dialog auto-opens on first visit", True)
        check(f"[{label}] default theme is minimal", page.evaluate("document.documentElement.dataset.theme") == "minimal")
        page.wait_for_timeout(500)   # let the pop-in animation finish
        page.screenshot(path=f"{OUT}/{label}-1-import.png")
        page.evaluate("applyTheme('neon')")   # radar + stat cards are part of the Neon look; Minimal hides them
        page.click("#sample")
        page.wait_for_function("document.getElementById('emails').value.length > 100")
        check(f"[{label}] sample emails load into textarea", True)
        page.click("#go")
        page.wait_for_function("document.getElementById('stEvents').dataset.v === '7'", timeout=15000)
        page.wait_for_timeout(900)
        check(f"[{label}] 7 events extracted", page.inner_text("#stEvents") == "7")
        check(f"[{label}] clash counted", page.inner_text("#stClash") == "1")
        check(f"[{label}] needs-checking counted", page.inner_text("#stReview") == "2")
        check(f"[{label}] radar has blips", page.locator("#radar .blip").count() >= 5)
        check(f"[{label}] countdown shown", page.locator("#next .cd").count() == 1)
        page.screenshot(path=f"{OUT}/{label}-2-dashboard.png", full_page=True)

        # ---- schedule view + reminders (new)
        check(f"[{label}] import lands on the Schedule tab", page.is_visible("#tabSchedule") and not page.is_visible("#tabCal"))
        check(f"[{label}] schedule lists the upcoming items", page.locator("#tabSchedule .sch-row").count() >= 5)
        check(f"[{label}] mini calendar shows event dots", page.locator("#tabSchedule .sch-day .dots").count() >= 3)
        check(f"[{label}] nothing is announced while every date is 4+ days away", page.locator(".rtoast").count() == 0)
        page.evaluate("ExamReminders.check({ now: new Date(2026, 9, 28, 9, 0) })")      # 28 Oct: the DBMS deadline (30 Oct) is 2 days out
        check(f"[{label}] a reminder card pops up when something is within 3 days", page.locator(".rtoast .rt-title:has-text('DBMS assignment')").count() == 1)
        n_cards = page.locator(".rtoast").count()
        page.evaluate("ExamReminders.check({ now: new Date(2026, 9, 28, 9, 5) })")
        check(f"[{label}] re-checking never repeats a reminder", page.locator(".rtoast").count() == n_cards)
        page.evaluate("document.querySelectorAll('.rtoast').forEach(n => n.remove())")
        page.evaluate("applyTheme('minimal')"); page.wait_for_timeout(300)
        page.screenshot(path=f"{OUT}/{label}-2b-schedule-minimal.png")
        page.evaluate("applyTheme('neon')")
        page.evaluate("setTab('cal')")

        # calendar navigation
        title0 = page.inner_text("#calTitle")
        page.click("#next-btn"); title1 = page.inner_text("#calTitle")
        check(f"[{label}] next month changes title", title0 != title1)
        page.click("#prev"); page.click("#today")
        check(f"[{label}] today returns to October", "October 2026" in page.inner_text("#calTitle"))
        check(f"[{label}] today's number is visible (has text + highlight)", page.evaluate("(()=>{const n=document.querySelector('.day.today .num');const cs=getComputedStyle(n);return n.textContent==='26' && cs.backgroundImage.includes('gradient')})()"))

        # open an event from the radar and the calendar
        page.locator("#radar .blip").first.click()
        page.wait_for_selector("#evModal[open]")
        check(f"[{label}] radar blip opens event dialog", page.inner_text("#evBody h3") != "")
        page.wait_for_timeout(500)
        page.screenshot(path=f"{OUT}/{label}-3-event.png")
        with page.expect_download() as dl:
            page.click("#evIcs")
        ics = open(dl.value.path(), "rb").read().decode("utf-8")   # binary: keep the CRLF line endings
        check(f"[{label}] single-event .ics is valid", ics.startswith("BEGIN:VCALENDAR") and ics.count("BEGIN:VEVENT") == 1 and "\r\n" in ics)
        page.keyboard.press("Escape")

        # subject filter
        subs = page.locator("#subjects .subj")
        check(f"[{label}] subjects listed", subs.count() >= 3)
        page.evaluate("document.getElementById('menuBtn').click()") if label == "mobile" else None
        subs.nth(1).click()
        check(f"[{label}] subject filter shows chip", page.is_visible("#filterChip"))
        page.screenshot(path=f"{OUT}/{label}-4-filtered.png")
        page.click("#filterChip button")
        check(f"[{label}] chip clears filter", not page.is_visible("#filterChip"))

        # week view + colour by subject
        page.click("#viewSwitch [data-view=week]")
        check(f"[{label}] week grid renders 7 columns", page.locator(".wcol").count() == 7)
        page.wait_for_timeout(500)
        page.screenshot(path=f"{OUT}/{label}-5-week.png")
        page.click("#colorSwitch [data-color=subject]")
        page.click("#viewSwitch [data-view=month]")

        # agenda search
        page.evaluate("document.querySelector('#bnav [data-tab=agenda]').click()") if label == "mobile" else page.click("#mainTabs [data-tab=agenda]")
        n_all = page.locator("#tabAgenda .row").count()
        page.fill("#search", "dbms")
        n_f = page.locator("#tabAgenda .row").count()
        check(f"[{label}] agenda search filters ({n_all}->{n_f})", 0 < n_f < n_all)
        page.fill("#search", "zzzz")
        check(f"[{label}] agenda search empty state", "Nothing matches" in page.inner_text("#tabAgenda"))
        page.fill("#search", "")
        page.screenshot(path=f"{OUT}/{label}-6-agenda.png", full_page=True)

        # review tab
        page.evaluate("document.querySelector('#bnav [data-tab=review]').click()") if label == "mobile" else page.click("#mainTabs [data-tab=review]")
        check(f"[{label}] review cards listed", page.locator(".rcard").count() == 2)
        page.screenshot(path=f"{OUT}/{label}-7-review.png", full_page=True)

        # themes
        for theme in ("aurora", "sunset", "paper", "neon"):
            page.click("#themeBtn"); page.click(f"#themeMenu button:has-text('{theme.title()}')")
            check(f"[{label}] theme {theme} applied", page.evaluate("document.documentElement.dataset.theme") == theme)
            if theme in ("paper", "aurora"):
                page.evaluate("document.querySelector('[data-tab=cal]').click()")
                page.screenshot(path=f"{OUT}/{label}-8-theme-{theme}.png")

        # persistence: reload restores calendar
        page.reload(); page.wait_for_function("document.getElementById('stEvents').dataset.v === '7'")
        check(f"[{label}] calendar restored after reload", True)

        # keyboard
        if label == "desktop":
            page.keyboard.press("?"); check("[desktop] ? opens shortcuts", page.is_visible("#helpModal"))
            page.keyboard.press("Escape")
            page.keyboard.press("w"); check("[desktop] W switches to week", page.locator(".wcol").count() == 7)
            page.keyboard.press("m"); check("[desktop] M switches to month", page.locator(".mgrid").count() == 1)

        # forget
        page.click("#forget")
        page.wait_for_function("document.getElementById('stEvents').textContent === '0'")   # count-up animation
        check(f"[{label}] clear saved calendar empties stats", page.inner_text("#stEvents") == "0")
        ctx.close()
    br.close()

print("\nbrowser problems:", problems or "none")
bad = [n for n, ok in checks if not ok]
print(f"{len(checks) - len(bad)}/{len(checks)} checks passed")
sys.exit(1 if bad or problems else 0)
