# Graph Report - examradar  (2026-10-25)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 325 nodes · 790 edges · 18 communities (13 shown, 5 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 15 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- mail2cal.py
- app.py
- app.js
- AppTests
- Handler
- reminders.js
- drawCalendar
- el
- find_clashes
- drawSchedule
- parse_date
- refreshFiltered
- plural
- TestPipeline
- FakeIMAP
- FakeOllama
- .run_with

## God Nodes (most connected - your core abstractions)
1. `AppTests` - 24 edges
2. `el()` - 24 edges
3. `openEvent()` - 18 edges
4. `drawSchedule()` - 16 edges
5. `TestPipeline` - 14 edges
6. `drawRadar()` - 14 edges
7. `fmtDate()` - 14 edges
8. `drawCalendar()` - 13 edges
9. `drawMonth()` - 13 edges
10. `todayKey()` - 13 edges

## Surprising Connections (you probably didn't know these)
- `email_summary()` --calls--> `looks_relevant()`  [EXTRACTED]
  app.py → mail2cal.py

## Import Cycles
- None detected.

## Communities (18 total, 5 thin omitted)

### Community 0 - "mail2cal.py"
Cohesion: 0.07
Nodes (27): _body(), build_ics(), build_review(), cmd_bench(), cmd_fetch(), cmd_run(), dedupe(), dedupe_review() (+19 more)

### Community 1 - "app.py"
Cohesion: 0.08
Nodes (12): email_summary(), ev_json(), find_chromium(), gmail_error(), _hm(), main(), make_server(), open_page() (+4 more)

### Community 2 - "app.js"
Cohesion: 0.10
Nodes (24): animateNumber(), applyTheme(), dialogs, DOW, drawStats(), drawThemeMenu(), eventTimes(), focusDate() (+16 more)

### Community 3 - "AppTests"
Cohesion: 0.14
Nodes (6): AppTests, free_port(), get(), post(), post_stream(), raw_email()

### Community 4 - "Handler"
Cohesion: 0.12
Nodes (6): emails_to_text(), friendly_error(), Handler, parse_pasted(), _parse_sent(), ParsePasted

### Community 5 - "reminders.js"
Cohesion: 0.21
Nodes (16): card(), check(), debug(), desktopNotify(), drop(), due(), init(), onBell() (+8 more)

### Community 6 - "drawCalendar"
Cohesion: 0.25
Nodes (16): addDays(), byDate(), drawCalendar(), drawDayPanel(), drawMonth(), drawWeek(), goToday(), key() (+8 more)

### Community 7 - "el"
Cohesion: 0.29
Nodes (15): downloadIcs(), drawAgenda(), drawAgendaList(), drawAll(), drawLegend(), drawNext(), drawReview(), el() (+7 more)

### Community 8 - "find_clashes"
Cohesion: 0.27
Nodes (4): find_clashes(), _span(), ev(), TestClashes

### Community 9 - "drawSchedule"
Cohesion: 0.29
Nodes (13): colorOf(), daysUntil(), drawRadar(), drawSchedule(), fmtDate(), hueOf(), kindColor(), parseKey() (+5 more)

### Community 10 - "parse_date"
Cohesion: 0.21
Nodes (5): _mk(), _no_year(), parse_date(), parse_times(), TestParsing

### Community 11 - "refreshFiltered"
Cohesion: 0.24
Nodes (12): drawClashes(), drawSubjects(), gotoDate(), icon(), refreshFiltered(), setDrawer(), setSubject(), setTab() (+4 more)

### Community 12 - "plural"
Cohesion: 0.27
Nodes (12): drawGmailList(), fill(), getJson(), hideAlert(), loadModels(), plural(), run(), setBusy() (+4 more)

## Knowledge Gaps
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AppTests` connect `AppTests` to `app.py`?**
  _High betweenness centrality (0.049) - this node is a cross-community bridge._
- **Should `mail2cal.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07402031930333818 - nodes in this community are weakly interconnected._
- **Why does `TestPipeline` connect `TestPipeline` to `mail2cal.py`, `app.py`, `find_clashes`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **Should `app.py` be split into smaller, more focused modules?**
  _Cohesion score 0.0782608695652174 - nodes in this community are weakly interconnected._
- **Why does `find_clashes()` connect `find_clashes` to `mail2cal.py`, `Handler`?**
  _High betweenness centrality (0.027) - this node is a cross-community bridge._
- **Should `app.js` be split into smaller, more focused modules?**
  _Cohesion score 0.09852216748768473 - nodes in this community are weakly interconnected._
- **Should `AppTests` be split into smaller, more focused modules?**
  _Cohesion score 0.1396011396011396 - nodes in this community are weakly interconnected._