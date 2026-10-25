"""Tests for everything except the real model: date parsing, guardrails, retry path, .ics output.
The model is replaced by a stub that returns what a good model should return.

  python test_mail2cal.py
"""
import mailbox
import os
import tempfile
import unittest
from datetime import date
from email.message import EmailMessage

import mail2cal as m
import make_samples

SENT = date(2026, 10, 26)

CANNED = {
    "End semester examination schedule": [
        {"title": "Data Structures exam", "kind": "exam", "date_text": "16 November 2026",
         "time_text": "10:00 AM to 1:00 PM", "venue": "Hall B",
         "quote": "Data Structures - 16 November 2026, 10:00 AM to 1:00 PM, Hall B"},
        {"title": "DBMS exam", "kind": "exam", "date_text": "19/11/2026",
         "time_text": "2:00 PM - 5:00 PM", "venue": "Hall B",
         "quote": "DBMS - 19/11/2026, 2:00 PM - 5:00 PM, Hall B"}],
    "Aarambh tech fest - registration open": [
        {"title": "Tech fest Aarambh", "kind": "event", "date_text": "7 November 2026", "time_text": "9:00 AM",
         "venue": "Main Auditorium",
         "quote": "Tech fest Aarambh will be held on 7 November 2026 at 9:00 AM in the Main Auditorium."},
        {"title": "Aarambh registration closes", "kind": "deadline", "date_text": "3 November 2026",
         "time_text": "", "venue": "", "quote": "Last date for registration: 3 November 2026."}],
    "DBMS assignment submission": [
        {"title": "DBMS assignment submission", "kind": "deadline", "date_text": "30/10/2026",
         "time_text": "5:00 PM", "venue": "",
         "quote": "Please submit the DBMS assignment on the portal by 30/10/2026, 5:00 PM."}],
    "Viva schedule": [
        {"title": "DBMS viva", "kind": "exam", "date_text": "04/11/2026", "time_text": "11:00 AM",
         "venue": "Lab 3", "quote": "The DBMS viva will be held on 04/11/2026 at 11:00 AM in Lab 3."}],
    "Placement cell meeting": [
        {"title": "Placement cell meeting", "kind": "meeting", "date_text": "next Friday", "time_text": "3 PM",
         "venue": "Room 204", "quote": "Placement cell meeting next Friday at 3 PM in Room 204."}],
    "Guest seminar on machine learning": [
        {"title": "Guest seminar", "kind": "event", "date_text": "12 December", "time_text": "11:30 AM",
         "venue": "Seminar Hall", "quote": "Guest seminar on 12 December at 11:30 AM in Seminar Hall."}],
    "Operating Systems end semester exam": [
        {"title": "Operating Systems exam", "kind": "exam", "date_text": "16 November 2026",
         "time_text": "11:00 AM to 2:00 PM", "venue": "Hall C",
         "quote": "The Operating Systems end semester exam will be held on 16 November 2026, "
                  "11:00 AM to 2:00 PM in Hall C."}],
}


def stub(model, subject, sent_iso, body):
    if subject.startswith("Library"):
        raise AssertionError("keyword prefilter should have skipped this email")
    return CANNED[subject], {"tokens": 10, "ns": 10 ** 9}


class TestParsing(unittest.TestCase):
    def check(self, text, want, flag_part=None):
        d, flag = m.parse_date(text, SENT)
        self.assertEqual(d, want, text)
        if flag_part:
            self.assertIn(flag_part, flag or "", text)
        else:
            self.assertIsNone(flag, text)

    def test_confident_formats(self):
        self.check("16 November 2026", date(2026, 11, 16))
        self.check("19/11/2026", date(2026, 11, 19))
        self.check("2026-11-05", date(2026, 11, 5))
        self.check("Nov 5th, 2026", date(2026, 11, 5))
        self.check("12 December", date(2026, 12, 12))
        self.check("5 January", date(2027, 1, 5))        # already passed this year -> next year
        self.check("11/25/2026", date(2026, 11, 25))      # 25 cannot be a month

    def test_flagged_for_human(self):
        self.check("04/11/2026", date(2026, 11, 4), "day/month")
        self.check("12-16 November 2026", date(2026, 11, 12), "range")
        self.check("next Friday", date(2026, 10, 30), "relative")
        self.check("tomorrow", date(2026, 10, 27), "relative")

    def test_unusable(self):
        self.assertIsNone(m.parse_date("30 February 2026", SENT)[0])
        self.assertIsNone(m.parse_date("sometime soon", SENT)[0])

    def test_times(self):
        self.assertEqual(m.parse_times("10:00 AM to 1:00 PM"), [(10, 0), (13, 0)])
        self.assertEqual(m.parse_times("2:00 PM - 5:00 PM"), [(14, 0), (17, 0)])
        self.assertEqual(m.parse_times("14:30"), [(14, 30)])
        self.assertEqual(m.parse_times("9 AM"), [(9, 0)])
        self.assertEqual(m.parse_times("12 AM"), [(0, 0)])
        self.assertEqual(m.parse_times("12:30 PM"), [(12, 30)])
        self.assertEqual(m.parse_times("evening"), [])


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        make_samples.write(cls.dir)
        cls.emails = m.load_emails(cls.dir)
        cls.good, cls.review, cls.stats = m.run_pipeline(cls.emails, "stub", llm=stub, log=False)

    def test_loads_all_samples(self):
        self.assertEqual(len(self.emails), len(make_samples.SAMPLES))

    def test_calendar_matches_expected(self):
        import json
        with open(os.path.join(self.dir, "expected.json")) as f:
            expected = json.load(f)
        self.assertEqual(m.score(self.good, expected), (1.0, 1.0))
        self.assertEqual(len(self.good), 7)

    def test_ambiguous_and_relative_go_to_review_not_calendar(self):
        why = {r["title"]: " ".join(r["reasons"]) for r in self.review}
        self.assertIn("day/month", why["DBMS viva"])
        self.assertIn("relative", why["Placement cell meeting"])
        self.assertNotIn("DBMS viva", [g["title"] for g in self.good])

    def test_prefilter_skips_irrelevant_mail(self):
        self.assertEqual(self.stats["skipped"], 1)

    def test_hallucinated_quote_is_rejected(self):
        e = {"id": "x", "subject": "Exam notice", "sent": SENT, "body": "Exams will be announced soon."}
        fake = lambda *a: ([{"title": "Final exam", "kind": "exam", "date_text": "5 December 2026",
                             "time_text": "", "venue": "", "quote": "Exam on 5 December 2026"}],
                           {"tokens": 1, "ns": 1})
        good, review, _, _ = m.process_email(e, "stub", fake)
        self.assertEqual(good, [])
        self.assertEqual(review[0]["level"], "rejected")

    # ---- quote proof-reading must survive table/bullet/markdown layouts, but still catch invented dates
    def _run_stub(self, body, quote, date_text="Wednesday, 28 October 2026"):
        e = {"id": "x", "subject": "Mid-Semester Examination", "sent": SENT, "body": body}
        fake = lambda *a: ([{"title": "Mid-Semester Examination", "kind": "exam", "date_text": date_text,
                             "time_text": "10:00 AM - 12:00 PM", "venue": "", "quote": quote}], {"tokens": 1, "ns": 1})
        return m.process_email(e, "stub", fake)

    def test_model_adding_a_colon_to_a_table_cell_is_still_trusted(self):
        body = "Mid-Semester Examination schedule\nDate Wednesday, 28 October 2026 10:00 AM - 12:00 PM\nVenue Hall B"
        good, review, _, _ = self._run_stub(body, "Date: Wednesday, 28 October 2026")
        self.assertEqual((len(good), len(review)), (1, 0))
        self.assertEqual(good[0]["date"].isoformat(), "2026-10-28")

    def test_markdown_bullets_and_bold_do_not_break_the_quote(self):
        body = "Exam details:\n* **Date:** Wednesday, 28 October 2026\n* **Time:** 10:00 AM - 12:00 PM"
        good, review, _, _ = self._run_stub(body, "Date: Wednesday, 28 October 2026")
        self.assertEqual((len(good), len(review)), (1, 0))

    def test_invented_date_is_still_rejected_even_with_a_similar_quote(self):
        body = "Mid-Semester Examination schedule\nDate Wednesday, 28 October 2026 10:00 AM - 12:00 PM"
        good, review, _, _ = self._run_stub(body, "Date: Friday, 6 November 2026", "Friday, 6 November 2026")
        self.assertEqual(good, [])
        self.assertEqual(review[0]["level"], "rejected")

    def test_identical_review_cards_are_shown_once(self):
        bad = {"title": "x", "date_text": "d", "quote": "q", "reasons": ["r"]}
        self.assertEqual(len(m.dedupe_review([dict(bad), dict(bad)])), 1)

    def test_sample_clash_is_found(self):
        clashes = m.find_clashes(self.good)
        self.assertEqual(len(clashes), 1)
        c = clashes[0]
        self.assertEqual(c["level"], "clash")
        self.assertEqual({c["a"]["title"], c["b"]["title"]}, {"Data Structures exam", "Operating Systems exam"})

    def test_ics_is_wellformed(self):
        ics = m.build_ics(self.good)
        lines = ics.split("\r\n")
        self.assertTrue(all(len(x.encode()) <= 75 for x in lines))
        flat = ics.replace("\r\n ", "")
        self.assertEqual(flat.count("BEGIN:VEVENT"), flat.count("END:VEVENT"))
        self.assertEqual(flat.count("BEGIN:VEVENT"), 7)
        self.assertIn("DTSTART:20261116T100000", flat)
        self.assertIn("DTEND:20261116T130000", flat)
        self.assertIn("DTSTART;VALUE=DATE:20261103", flat)          # no time -> all-day
        self.assertIn("Quote: Data Structures - 16 November 2026\\, 10:00 AM", flat)


class TestRecovery(unittest.TestCase):
    def run_with(self, replies):
        calls = []

        def fake(path, payload, timeout=300):
            calls.append(1)
            return replies[min(len(calls), len(replies)) - 1]

        old, m._post = m._post, fake
        try:
            return m.llm_extract("x", "s", "2026-10-26", "b"), len(calls)
        finally:
            m._post = old

    def test_retries_once_on_bad_json(self):
        good = {"message": {"content": '{"events": []}'}, "eval_count": 5, "eval_duration": 10 ** 9}
        (events, _), n = self.run_with([{"message": {"content": "not json"}}, good])
        self.assertEqual((events, n), ([], 2))

    def test_gives_up_after_two_bad_replies(self):
        (events, _), n = self.run_with([{"message": {"content": "nope"}}])
        self.assertEqual((events, n), (None, 2))


def ev(title, kind, day, start=None, end=None):
    return {"title": title, "kind": kind, "date": date(2026, 11, day), "start": start, "end": end}


class TestClashes(unittest.TestCase):
    def test_overlapping_written_ranges_clash(self):
        c = m.find_clashes([ev("A", "exam", 16, (10, 0), (13, 0)), ev("B", "exam", 16, (12, 0), (14, 0))])
        self.assertEqual([x["level"] for x in c], ["clash"])

    def test_back_to_back_is_not_a_clash(self):
        self.assertEqual(m.find_clashes([ev("A", "exam", 16, (10, 0), (13, 0)),
                                         ev("B", "exam", 16, (13, 0), (15, 0))]), [])

    def test_different_days_never_clash(self):
        self.assertEqual(m.find_clashes([ev("A", "exam", 16, (10, 0), (13, 0)),
                                         ev("B", "exam", 17, (10, 0), (13, 0))]), [])

    def test_same_start_time_is_a_sure_clash_even_without_end(self):
        c = m.find_clashes([ev("A", "exam", 16, (10, 0)), ev("B", "exam", 16, (10, 0))])
        self.assertEqual([x["level"] for x in c], ["clash"])

    def test_assumed_length_gives_maybe(self):
        c = m.find_clashes([ev("A", "exam", 16, (10, 0)), ev("B", "exam", 16, (12, 0), (13, 0))])
        self.assertEqual([x["level"] for x in c], ["maybe"])      # exam assumed 3 h long
        c = m.find_clashes([ev("A", "event", 16, (10, 0)), ev("B", "event", 16, (12, 0), (13, 0))])
        self.assertEqual(c, [])                                    # other kinds assumed 1 h

    def test_missing_time_gives_maybe(self):
        c = m.find_clashes([ev("A", "exam", 16, (10, 0), (13, 0)), ev("B", "event", 16)])
        self.assertEqual([x["level"] for x in c], ["maybe"])

    def test_deadlines_do_not_block_time(self):
        self.assertEqual(m.find_clashes([ev("A", "exam", 16, (10, 0), (13, 0)),
                                         ev("B", "deadline", 16, (11, 0))]), [])


class TestMbox(unittest.TestCase):
    def test_loads_mbox(self):
        path = os.path.join(tempfile.mkdtemp(), "export.mbox")
        box = mailbox.mbox(path)
        for i in range(2):
            msg = EmailMessage()
            msg["Subject"], msg["Date"] = f"Exam {i}", "Mon, 26 Oct 2026 09:00:00 +0530"
            msg.set_content("Exam on 5 December 2026")
            box.add(msg)
        box.flush()
        box.close()
        emails = m.load_emails(path)
        self.assertEqual([e["subject"] for e in emails], ["Exam 0", "Exam 1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
