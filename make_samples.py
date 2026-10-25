#!/usr/bin/env python3
"""Write demo emails + expected.json (the dates that SHOULD end up on the calendar).

  python make_samples.py sample_emails

Replace these with 15-20 real or mock emails from your own college inbox and edit expected.json
to match. Emails whose date is ambiguous or relative are expected to give [] (they belong in
review.md, not on the calendar).
"""
import json
import os
import sys
from email.message import EmailMessage

SENT = "Mon, 26 Oct 2026 09:00:00 +0530"

SAMPLES = [
    ("01_datesheet.eml", "End semester examination schedule",
     "Dear students,\n\nThe end semester examinations will be held as follows:\n\n"
     "Data Structures - 16 November 2026, 10:00 AM to 1:00 PM, Hall B\n"
     "DBMS - 19/11/2026, 2:00 PM - 5:00 PM, Hall B\n\nPlease carry your ID card.\n",
     ["2026-11-16", "2026-11-19"]),
    ("02_techfest.eml", "Aarambh tech fest - registration open",
     "Tech fest Aarambh will be held on 7 November 2026 at 9:00 AM in the Main Auditorium.\n"
     "Last date for registration: 3 November 2026.\n",
     ["2026-11-07", "2026-11-03"]),
    ("03_assignment.eml", "DBMS assignment submission",
     "Please submit the DBMS assignment on the portal by 30/10/2026, 5:00 PM.\n",
     ["2026-10-30"]),
    ("04_viva_ambiguous.eml", "Viva schedule",
     "The DBMS viva will be held on 04/11/2026 at 11:00 AM in Lab 3.\n",
     []),
    ("05_meeting_relative.eml", "Placement cell meeting",
     "Placement cell meeting next Friday at 3 PM in Room 204.\n",
     []),
    ("06_library.eml", "Library hours",
     "The library has new arrivals this week. Thank you.\n",
     []),
    ("07_seminar.eml", "Guest seminar on machine learning",
     "Guest seminar on 12 December at 11:30 AM in Seminar Hall.\n",
     ["2026-12-12"]),
    ("08_os_exam.eml", "Operating Systems end semester exam",
     "The Operating Systems end semester exam will be held on 16 November 2026, 11:00 AM to 2:00 PM in Hall C.\n",
     ["2026-11-16"]),   # overlaps the Data Structures exam on purpose
]


def write(folder):
    os.makedirs(folder, exist_ok=True)
    for fn, subject, body, _ in SAMPLES:
        msg = EmailMessage()
        msg["From"], msg["To"] = "office@college.example", "students@college.example"
        msg["Subject"], msg["Date"] = subject, SENT
        msg.set_content(body)
        with open(os.path.join(folder, fn), "wb") as f:
            f.write(msg.as_bytes())
    with open(os.path.join(folder, "expected.json"), "w", encoding="utf-8") as f:
        json.dump({fn: dates for fn, _, _, dates in SAMPLES}, f, indent=2)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sample_emails"
    write(out)
    print(f"Wrote {len(SAMPLES)} emails and expected.json to {out}/")
