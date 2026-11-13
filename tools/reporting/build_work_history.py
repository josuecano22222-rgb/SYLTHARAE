"""Build the SYLTHARAE-Pro work-history / task-register workbook.

Regenerates deliverables/SYLTHARAE-Pro_Work_History_and_Task_Register.xlsx from
the task rows below. The rows are the source of truth; the workbook is output.
Run:  python tools/reporting/build_work_history.py   (needs openpyxl)

Plain-language titles and self-contained descriptions: no task description
depends on any document or code reference to be understood.

Read-only with respect to the repository: nothing here writes into the checkout.
"""
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

OUT = str(Path(__file__).resolve().parents[2] / "deliverables"
          / "SYLTHARAE-Pro_Work_History_and_Task_Register.xlsx")
PROJECT = "SYLTHARAE-Pro"

# ---------------------------------------------------------------- vocabularies
STATUSES = ["Open", "Working", "Pending Review", "Overdue", "Template",
            "Completed", "Cancelled"]
PRIORITIES = ["Low", "Medium", "High", "Urgent"]

UNV = "Unverified"
START = date(2026, 10, 23)
TODAY = date(2026, 10, 25)

# ---------------------------------------------------------------- project info
PROJECT_INFO = [
    ("Project Name", "SYLTHARAE-Pro"),
    ("Status", "Working"),
    ("Project Type",
     "Repair and quality assurance of an existing software product: a web "
     "application that finds, reads, extracts and connects information from large "
     "mixed collections of files (documents, spreadsheets, e-mail, archives, "
     "images, audio, video, e-books, databases and diagrams)."),
    ("Priority", "High"),
    ("Intermediary",
     "An artificial-intelligence coding agent working inside the repository owner's "
     "own workspace, under the owner's direction and account. Every saved change in "
     "the project carries the owner's name."),
    ("Yes",
     "Yes. The work described in the task register was carried out by an AI agent "
     "acting for the owner: it inspected the software, ran the checks, wrote the "
     "repairs and recorded the evidence. The owner set the rules of engagement, "
     "approved the repairs and kept the final decisions."),
    ("Expected Start Date", START),
    ("Expected End Date", UNV),
]

# ---------------------------------------------------------------- groups
G1 = "1. Getting set up: environment, audit record and tools"
G2 = "2. First three rounds of verification and repair"
G3 = "3. Round 4: probing and measuring the running system"
G4 = "4. Round 5: carrying out the approved repairs"
G5 = "5. Round 6: bringing the repaired parts together"
G6 = "6. Round 10: better tests, honest failure handling, runbook"
G7 = "7. Round 11: a real category hierarchy and its screen"
G8 = "8. Round 12: repairing broken screens and making the tree efficient"
G9 = "9. Round 13: fixing the two defects a person would actually hit"
G10 = "10. Round 14: one button, one action, and a re-check of the rest"
G11 = "11. Open work: unfinished, blocked or never attempted"

P1 = "1.0 Stage: environment, audit record and tools"
P2 = "2.0 Stage: first three rounds of verification and repair"
P3 = "3.0 Stage: Round 4 - probing and measuring the running system"
P4 = "4.0 Stage: Round 5 - carrying out the approved repairs"
P5 = "5.0 Stage: Round 6 - bringing the repaired parts together"
PTR = "5.1 Sub-stage: the translation feature"
P6 = "6.0 Stage: Round 10 - better tests, honest failure handling, runbook"
P7 = "7.0 Stage: Round 11 - a real category hierarchy and its screen"
P8 = "8.0 Stage: Round 12 - repairing broken screens and the tree"
P9 = "9.0 Stage: Round 13 - fixing the two defects people hit"
P10 = "10.0 Stage: Round 14 - action ownership and re-checks"
P11 = "11.0 Stage: open work carried forward"
TOP = "- (top-level task)"

# ---------------------------------------------------------------- task rows
# (title, type, group, status, priority, parent, start, end, description)
ROWS = [
    # ================= group 1 =================
    (P1, "Planning", G1, "Completed", "High", TOP, date(2026, 10, 23), date(2026, 10, 24),
     "The opening stage, covering the first two days. The software's existing code was "
     "put under version control so every later change could be compared against it; a "
     "working test environment (programming runtime, database server and a real "
     "browser) was built from nothing; the whole application was surveyed and its "
     "problems recorded; and the automated checking tools that every later claim "
     "depends on were written. Everything in this stage is finished."),

    ("Put the existing application under version control and start a separate repair branch",
     "Setup", G1, "Completed", "High", P1, date(2026, 10, 23), date(2026, 10, 23),
     "The application is large - roughly four thousand files and about five hundred and "
     "sixty thousand lines - and it arrived with no usable history: it was captured in a "
     "single step, so there was no record of what changed, when or why. That single "
     "capture became the fixed reference point, and all repair work was placed on a "
     "separate branch so that nothing on the main line changed while the work was being "
     "done. The software identifies itself as version 2.2.0, which is a declared version "
     "number rather than a released one."),

    ("Build a working test environment: programming runtime, database server and a real browser",
     "Setup", G1, "Completed", "High", P1, date(2026, 10, 23), date(2026, 10, 24),
     "The software cannot start without a database server, and it cannot be judged "
     "properly without a real browser. The starting machine had neither. A Python "
     "environment was assembled from the project's own list of required packages, a "
     "PostgreSQL 16.2 database server was started from a portable package, and a Chromium "
     "browser was made to run without a screen by supplying the missing system libraries "
     "as stand-ins, so pages could be loaded and buttons really clicked. The machine "
     "forgets everything between sessions, so this was rebuilt repeatedly, and the rebuild "
     "was written down step by step so it is repeatable rather than guesswork."),

    ("Survey the whole application and record, in plain terms, what is broken",
     "Research", G1, "Completed", "High", P1, date(2026, 10, 23), date(2026, 10, 24),
     "A first full survey of the software was produced: what it is meant to do, how it is "
     "built, which interfaces it offers to users and to other systems, and what could not "
     "be checked at all. Seven problems were recorded, each with the exact steps that "
     "reproduce it, the underlying cause, what it means for a real user, and a suggested "
     "repair. The four most serious were: the software cannot start unless a database "
     "server is already running; its changelog describes database changes that do not "
     "exist in the code; the existing test suite could not be run; and no real browser "
     "testing was possible at all. Every entry states how it was established."),

    ("Build the automated checkers, probes and measuring tools used for every later claim",
     "Tooling", G1, "Completed", "High", P1, date(2026, 10, 24), date(2026, 10, 24),
     "Twenty-nine small programmes were written so that statements about the software "
     "could be produced by a machine rather than by reading code and forming an opinion. "
     "They list every web address the application publishes; send valid, missing, "
     "malformed and other people's identifiers to every address that accepts one; fire "
     "hostile query strings and broken request bodies; check that database statements "
     "match the real database and that screens are wired to the code behind them; count "
     "and time real queries; inject failures such as a full disk or a killed process; and "
     "operate the real browser. These tools produced nearly every number in this register."),

    # ================= group 2 =================
    (P2, "Verification", G2, "Completed", "High", TOP, date(2026, 10, 23), date(2026, 10, 24),
     "Stage covering the first three verification rounds: run the existing test suite, "
     "repair what it exposed with a test that pins each repair, then replace opinion with "
     "measurement by generating complete lists of everything the software contains, with a "
     "verdict on every single entry. Finished within two days."),

    ("Repair the first defects the existing test suite exposed, one test per repair",
     "Testing", G2, "Completed", "High", P2, date(2026, 10, 23), date(2026, 10, 23),
     "The existing test suite was run properly for the first time: 4,320 tests, of which "
     "121 failed, 12 could not even start and 134 were skipped. The first genuine defect "
     "was repaired: the software recorded progress messages using a list of twenty-one "
     "message types while the database accepted only eleven of them, so honest warnings "
     "could not be stored at all and the write was rejected. The two lists were reconciled "
     "and a dedicated test now fails if they ever drift apart again. A self-check that "
     "reports whether the installation is healthy gave thirty-one checks and no failures "
     "once the database it expects existed; that gap was a setup problem, not a product "
     "defect, and is recorded as such."),

    ("List every interface, database statement and text string, and give each one a verdict",
     "Verification", G2, "Completed", "High", P2, date(2026, 10, 24), date(2026, 10, 24),
     "Instead of sampling a few files, the software's own machinery was interrogated by "
     "script to produce twelve complete lists: web addresses (485, plus 342 exercised at "
     "run time), user-facing interfaces (35), page templates (79), database tables and "
     "columns (40), database statements (2,324), translatable text identifiers (7,141), "
     "configuration settings (68), stylesheets (45), browser scripts (178) and modules "
     "(422). That is 11,188 entries. Every entry was given one of six verdicts - verified "
     "working, repaired with a test, defect open, needs a decision, blocked by the "
     "environment, or unused code - and not one was left unexamined."),

    ("Repair the reporting screens, alerts, priorities, background jobs and access gaps",
     "Development", G2, "Completed", "High", P2, date(2026, 10, 24), date(2026, 10, 24),
     "Thirteen repairs in one batch. Report screens now render on the server and can export "
     "a case dossier. Alerts show the text that was actually stored instead of a mismatched "
     "label. Identifier detection uses one consistent record type. The priority ladder is "
     "now identical in the database query and in the application code, capped at high for "
     "outcomes, with the reason for each priority stored alongside it. A cancelled job is "
     "marked by a flag, and its progress messages are translated into the vocabulary the "
     "database accepts. A user's mistake now returns a clear client error instead of an "
     "internal server error, and the settings export is restricted to administrators. "
     "Document reading no longer writes columns that do not exist and keeps the reason a "
     "record is partial. Two screens gained their missing main heading, one screen posted "
     "to the wrong address, and a refused scenario definition now answers with a clear "
     "explanation rather than a bare failure."),

    # ================= group 3 =================
    (P3, "Measurement", G3, "Completed", "Medium", TOP, date(2026, 10, 24), date(2026, 10, 24),
     "Stage covering the round in which the audit stopped being a reading exercise. Every "
     "route was attacked with real requests, hostile input was fired at every search box, a "
     "realistic hundred-thousand-record database was loaded and measured, and the system was "
     "deliberately broken mid-work to see what it does. The round ended with a written list "
     "of the problems found and the decisions the owner needed to make."),

    ("Send valid, missing, broken and other-people's identifiers to all 155 addresses that take one",
     "Verification", G3, "Completed", "High", P3, date(2026, 10, 24), date(2026, 10, 24),
     "Six hundred and sixty attempts across one hundred and fifty-five web addresses that "
     "accept an identifier. None produced another user's data, a server error or a stack "
     "trace. Nine addresses answered 'not implemented', which is correct for features that "
     "are declared but not built. Sixty-five could not be tried because the test data they "
     "need did not exist; those are recorded as untested, never as passing. As a control, "
     "the same probe was run against an earlier state of the code, where it produced eight "
     "genuine findings, all since repaired - which is what shows the probe can actually "
     "find things."),

    ("Fire deliberately hostile input at every search, filter and paging control, and fix what breaks",
     "Testing", G3, "Completed", "Medium", P3, date(2026, 10, 24), date(2026, 10, 24),
     "6,440 hostile query strings were sent to every screen that accepts search, sorting, "
     "filtering or paging options: no findings and no stack traces. The companion sweep with "
     "deliberately broken request bodies found real problems and produced repairs. Control "
     "characters and non-object data are now refused before reaching the database. Asking "
     "for a page far beyond the end returns an empty page instead of a server error. Numbers "
     "arriving from a typed address are read without trusting them. Creating a duplicate name "
     "answers with a clear conflict message. Cache statistics report fingerprints instead of "
     "the database queries being cached. The notification page-size limit now matches the "
     "limit the software itself states."),

    ("Measure the system under a realistic 100,000-record load, then kill it in the middle of work",
     "Measurement", G3, "Completed", "High", P3, date(2026, 10, 24), date(2026, 10, 24),
     "A database was loaded with a hundred thousand records and the ten heaviest queries were "
     "timed in detail, together with how many separate queries each screen issues. Behaviour "
     "under failure was then tested by stopping the database during an import and by killing "
     "and restarting the application in the middle of a job, to see what the user is told and "
     "whether work is lost. The results changed the plan: the heaviest queries each read two "
     "large tables in full, three sorting operations spill to disk, and the time an import "
     "takes grows faster than the file does above roughly half a megabyte."),

    ("Add tests for four repairs that had shipped without one, and fix three broken screens",
     "Testing", G3, "Completed", "High", P3, date(2026, 10, 24), date(2026, 10, 24),
     "Four earlier repairs had no test guarding them, so a later change could silently undo "
     "them; each now has one that fails if the behaviour is lost. Three further defects were "
     "found and repaired. The batch-report screen returned an internal error for every "
     "request. Asking a schedule to run immediately crashed instead of saying plainly that "
     "the scheduler is not available. A usage check on the 'sides' screen read a database "
     "column that does not exist."),

    ("Write up eight measured problems and ten open choices, each with a recommended option",
     "Planning", G3, "Completed", "High", P3, date(2026, 10, 24), date(2026, 10, 24),
     "The work had reached points where a judgement call was needed that the engineer was not "
     "entitled to make alone, so the choices were written out instead of assumed. Eight "
     "problems came from measurement: a batch import that claims success for files it "
     "dropped; imports that vanish when the program restarts; import time that grows too "
     "fast; slow database queries; a health check that hangs during an outage; an outage that "
     "silently signs users out; and a filter that cannot be indexed at scale. Ten further "
     "choices concerned tests and documentation. Each entry states the evidence, a "
     "recommended default, and what choosing differently would cost. Nothing was changed "
     "while the owner decided."),

    # ================= group 4 =================
    (P4, "Development", G4, "Completed", "High", TOP, date(2026, 10, 24), date(2026, 10, 24),
     "Stage in which the owner's answers were turned into repairs, plus one extra defect found "
     "on the way. Each item was shown failing first and passing afterwards, so no repair rests "
     "on a promise."),

    ("Stop a batch import from claiming success for files it silently dropped",
     "Development", G4, "Completed", "High", P4, date(2026, 10, 24), date(2026, 10, 24),
     "A person submitting thirty files was told thirty had been accepted, while only five were "
     "actually being processed and twenty-five were silently discarded because the system "
     "allows five imports at a time. Every submitted file is now accounted for: each one ends "
     "up queued or deferred, and the totals always add up - accepted equals queued plus "
     "deferred, and accepted plus rejected equals total. A file that cannot start immediately "
     "is reported as deferred so the person knows to submit it again, instead of losing it "
     "without a trace."),

    ("Make the health check answer immediately during a database outage",
     "Development", G4, "Completed", "High", P4, date(2026, 10, 24), date(2026, 10, 24),
     "When the database was unreachable, the health check waited out a thirty-second "
     "connection timeout and then told whoever asked the name of the internal database driver "
     "class - information that helps an attacker and no operator. It now answers within a "
     "fixed short deadline, and the answer no longer names internal software. The first "
     "attempt did not fully solve it; a second pass found the real cause, which was a "
     "database-backed translation layer running before the health check itself."),

    ("Tell users the truth when their session store is unreachable",
     "Development", G4, "Completed", "Medium", P4, date(2026, 10, 24), date(2026, 10, 24),
     "During a database outage every user was signed out and told simply that they were not "
     "authenticated, which reads as a forgotten password and invites people to reset "
     "credentials that were never the problem. The answer now says that authentication is "
     "temporarily unavailable, gives a reason, and tells the browser how long to wait before "
     "trying again. A user whose session really was deleted still receives the ordinary "
     "not-authenticated answer, so the two situations can still be told apart."),

    ("Make the time an import takes grow in proportion to the size of the document",
     "Development", G4, "Completed", "High", P4, date(2026, 10, 24), date(2026, 10, 24),
     "Importing a document took more than three times as long each time the file doubled in "
     "size: a four-megabyte file took five minutes, and an eight-megabyte file produced "
     "nothing within five minutes before giving up. Profiling showed two places where every "
     "item was compared against every other item. Both were rewritten to sweep through the "
     "data once, and the timings before and after were recorded so the improvement can be "
     "checked rather than believed."),

    ("Record that imports do not survive a restart, and why that was not changed",
     "Documentation", G4, "Completed", "Medium", P4, date(2026, 10, 24), date(2026, 10, 24),
     "A restart or crash loses every import that was queued or running: nothing resumes them, "
     "and no record survives to say they ever existed. Making them durable would require a new "
     "database table, which the agreed rules for this phase forbid. Rather than change it "
     "quietly, the behaviour was written down for users: imports run inside the web process, "
     "at most five at a time, and a second worker would keep a completely separate queue that "
     "nobody can see from the first. Making imports durable is recorded as a proposal for a "
     "later phase."),

    ("Point the failing tests at the real database instead of renaming the database",
     "Testing", G4, "Completed", "High", P4, date(2026, 10, 24), date(2026, 10, 24),
     "Seventeen failing tests demanded database constraint names, columns and migration "
     "numbers that this project does not have and never did. Renaming the database to match "
     "would have broken every existing installation, to satisfy names that no working code "
     "reads. The tests were corrected instead: they now assert the rule rather than the name - "
     "for example by reading the actual definition of a constraint - and they use the chain of "
     "database changes that really exists. Each rewrite kept the behaviour being asserted, and "
     "each was shown failing first and passing afterwards."),

    ("Ship the missing provenance file for the bundled place-name data",
     "Development", G4, "Completed", "Medium", P4, date(2026, 10, 24), date(2026, 10, 24),
     "The bundled list of place names could not be packaged because the file explaining where "
     "the data comes from was missing, so the data would have shipped with no traceable "
     "origin. The provenance file was written with facts the packaging step can check, so the "
     "data now carries its source with it. A deeper redesign of how places are stored and "
     "matched was deliberately set aside for a later decision rather than half-done here."),

    ("State the software licence plainly, using only facts the project can verify",
     "Documentation", G4, "Completed", "Medium", P4, date(2026, 10, 24), date(2026, 10, 24),
     "The project's own configuration already declared its licence - the GNU Affero General "
     "Public License, version 3 or later - but the front page did not state it, did not "
     "explain the obligation that comes with letting people use the software over a network, "
     "and did not mention the source-code link every page must offer. All three are now "
     "stated using facts the project can check. No licence text was invented, and third-party "
     "packages whose licence metadata is unclear are listed as unclear rather than assumed "
     "acceptable."),

    ("Record five duplicate database indexes instead of dropping them",
     "Documentation", G4, "Completed", "Low", P4, date(2026, 10, 24), date(2026, 10, 24),
     "Five index definitions duplicate work another index already does, which costs disk space "
     "and slows down every write. Removing them is a change to the database structure and was "
     "outside the agreed rules for this phase, so each duplicate was written down with the "
     "surviving twin identified beside it. The decision to drop them stays with the owner, "
     "because dropping the wrong one would slow a query nobody is watching."),

    ("Fix a date filter that checked the wrong end of its own time window",
     "Development", G4, "Completed", "High", P4, date(2026, 10, 24), date(2026, 10, 24),
     "Notifications about a date that is about to arrive were being filtered against the wrong "
     "date column: the end of the time window was compared where the reference date belongs, "
     "so a warning about something due soon could be wrongly included or wrongly left out. The "
     "comparison was corrected, and a test that fails on the old code and passes on the new "
     "one now pins it. The defect was found while fixing the import performance problem, not by "
     "the test suite - which is exactly why the suite cannot be the only evidence."),

    ("Classify every failing test so that no failure is left unexplained",
     "Testing", G4, "Completed", "High", P4, date(2026, 10, 24), date(2026, 10, 24),
     "Every test that failed in this round was placed in exactly one category with a written "
     "reason: a real product defect, a test that had to be corrected because it described "
     "something untrue, something that cannot run in this environment, or something waiting on "
     "a decision from the owner. No failure was left unclassified. The principle is simple: an "
     "unexplained red test is not evidence of anything, and a suite that is allowed to stay red "
     "silently stops being a warning system."),

    ("Remove one machine's private file paths from shared reports",
     "Testing", G4, "Completed", "Low", P4, date(2026, 10, 24), date(2026, 10, 24),
     "Reports produced on the working machine contained that machine's absolute folder paths, "
     "which broke an existing check that exists to stop private paths and internal hostnames "
     "leaking into shared material. The reports were rewritten to describe locations "
     "relatively, so they can be shared without disclosing how the machine is laid out."),

    ("Build the missing reporting module that two tools expect - stopped when there was nothing to build on",
     "Development", G4, "Open", "Medium", P4, date(2026, 10, 24), UNV,
     "Two maintenance tools start by loading a reporting module that has never existed in this "
     "project's history, so both fail immediately. The plan of record said to build it as a "
     "thin wrapper around existing reporting code. On inspection there was nothing to wrap: the "
     "keyword-significance dataset, the report catalogue and the run engine the wrapper is "
     "supposed to expose do not exist anywhere. Writing them would mean inventing analytical "
     "SQL and then certifying it with our own assertion, which is precisely the kind of "
     "self-satisfying evidence this engagement exists to avoid. Work stopped and the decision "
     "went to the owner. Both tools still fail today; the two tests that referred to the module "
     "were later retired from the plan."),

    # ================= group 5 =================
    (P5, "Development", G5, "Completed", "High", TOP, date(2026, 10, 24), date(2026, 10, 24),
     "Stage covering a long consolidation batch in which each subsystem was brought to one "
     "consistent rule, followed by a single complete test run kept as the reference point of "
     "record, the delivery of a translation feature end to end, and the recovery of work that a "
     "machine reset had dropped from the branch history."),

    ("Make the health and readiness checks report only what they have actually measured",
     "Development", G5, "Completed", "Medium", P5, date(2026, 10, 24), date(2026, 10, 24),
     "The screens that tell an operator whether the system is healthy asserted success in "
     "places where nothing had been checked - the worst kind of green light, because it looks "
     "like assurance. They now report what was measured, how it was measured and how fresh the "
     "answer is, so 'healthy' means a check actually ran rather than a check was skipped."),

    ("Give alerts one rule for who may see them, one unread count and one display form",
     "Development", G5, "Completed", "Medium", P5, date(2026, 10, 24), date(2026, 10, 24),
     "A user's unread count could disagree with the list it summarises, because the count and "
     "the list were built by different rules. Visibility, counting and the text shown in the "
     "list now all come from one rule, so the number on the screen always matches the number of "
     "items the same screen will show. The same problem also caused alerts to display a label "
     "that did not match their stored text, which is fixed by the same change."),

    ("Make background-job records readable after the fact, and their reporting honest",
     "Development", G5, "Completed", "High", P5, date(2026, 10, 24), date(2026, 10, 24),
     "Job progress used to live only in the memory of the process that ran it, so once that "
     "process was gone there was no record of what had happened or why. Progress is now "
     "recorded in a form that can be read back later. The import accounting repaired earlier is "
     "applied the same way on every import path, so two screens can no longer report different "
     "totals for the same set of files."),

    ("Make one definition drive the scenario rules, the screen and the database query",
     "Development", G5, "Completed", "Medium", P5, date(2026, 10, 24), date(2026, 10, 24),
     "A scenario was described once for the rules engine, again for the screen, and a third "
     "time for the query builder, so the three could drift apart and silently disagree - the "
     "analyst seeing one thing while the engine evaluated another. They now share a single "
     "definition, so what is displayed, what is evaluated and what the database is asked cannot "
     "diverge."),

    ("Rewrite overlap detection to sweep the data once, and keep evidence provenance intact",
     "Development", G5, "Completed", "Medium", P5, date(2026, 10, 24), date(2026, 10, 24),
     "Detectors were merging overlapping matches by comparing every match against every other "
     "match, which becomes unusable on long documents; that was replaced with a single ordered "
     "sweep. Two related gaps were closed at the same time: the kind of an identifier is now "
     "carried inside its evidence rather than only in a type name that can be renamed, and "
     "place data carries its source with it so a result can be traced back to the list it came "
     "from."),

    ("Build the reporting behaviour on the existing reporting services, with no database change",
     "Development", G5, "Completed", "Medium", P5, date(2026, 10, 24), date(2026, 10, 24),
     "The reporting capability described in the plan was implemented on top of the reporting "
     "services that already exist: what was missing was the reporting logic, not new tables. "
     "This is deliberately not the module that the two maintenance tools expect, which remains "
     "unbuilt and open; the two are different things and are recorded separately so that one "
     "cannot be mistaken for the other."),

    ("Check every database read behind the places and saved-search screens against the real database",
     "Verification", G5, "Completed", "Medium", P5, date(2026, 10, 24), date(2026, 10, 24),
     "Every read used by the places screens and the saved-search screens was compared statement "
     "by statement against the database that actually exists, so a query referring to a column "
     "that is not there could not survive to the moment a user clicks. The mismatches found "
     "were corrected rather than documented."),

    ("Apply the same access checks to the authentication, export and translation addresses",
     "Development", G5, "Completed", "Medium", P5, date(2026, 10, 24), date(2026, 10, 24),
     "Some web addresses checked who was calling, some checked what that caller was allowed to "
     "do, and some checked neither. Sign-in, permissions, forged-request protection and export "
     "rights were made uniform across these three areas, so no address is protected merely by "
     "being hard to guess. Uniformity matters more than the individual checks: a gap in one "
     "family is invisible until someone finds it."),

    ("Record every installed package, its licence, and whether that licence is compatible",
     "Research", G5, "Completed", "Medium", P5, date(2026, 10, 24), date(2026, 10, 24),
     "One hundred and sixteen installed packages were recorded with the version and licence "
     "each declares, alongside a verdict on whether that licence is compatible with releasing "
     "this software under the GNU Affero GPL. A generated notice file lists the third-party "
     "components that ship with the product. Packages whose licence metadata is unclear are "
     "listed as unclear, not assumed harmless, because an unclear licence is a legal risk and "
     "not a clerical detail."),

    ("Write down the data-retention rules and the index definitions readiness depends on",
     "Documentation", G5, "Completed", "Medium", P5, date(2026, 10, 24), date(2026, 10, 24),
     "Two questions operators kept asking had no written answer: how long data is kept, and "
     "which database indexes the readiness check relies on. Both are now written down, so a "
     "readiness result can be defended instead of inferred, and so that a future change to "
     "either can be recognised as a change rather than discovered by accident."),

    ("Bring every audit report, inventory and finding into one committed record",
     "Documentation", G5, "Completed", "High", P5, date(2026, 10, 24), date(2026, 10, 24),
     "The reports, the twelve complete inventories and the findings lists had been produced over "
     "several days and were scattered across a working machine that is wiped between sessions. "
     "They were committed together with the raw evidence each one rests on, so that anyone "
     "picking this up later - including the owner - can trace a claim back to the run that "
     "produced it. Without that, every number in this register would be an assertion."),

    (PTR, "Development", G5, "Completed", "High", P5, date(2026, 10, 24), date(2026, 10, 24),
     "Sub-stage inside the consolidation round: a translation feature delivered end to end in a "
     "single afternoon - engines, memory, web interface, screen and placement in the reading "
     "flow. Three design decisions governed it: the stored original text is never modified; the "
     "translation memory lives in a local file rather than in the frozen database; and every "
     "machine translation is labelled as unverified so it can never be mistaken for a checked "
     "one."),

    ("Choose translation engines so that no restrictive licence is quietly relied on",
     "Development", G5, "Completed", "Medium", PTR, date(2026, 10, 24), date(2026, 10, 24),
     "The translation feature defaults to an engine with permissive licensing. A "
     "higher-quality neural engine is available only as an option and is flagged as "
     "non-commercial, so a deployment cannot drift into depending on it without noticing. If an "
     "engine cannot fetch the model it needs, the user is told why rather than receiving a "
     "generic failure that suggests the software is broken."),

    ("Make translation read stored text only, with no path that can alter it",
     "Development", G5, "Completed", "Medium", PTR, date(2026, 10, 24), date(2026, 10, 24),
     "The translation service can only read text the system has already stored; it has no path "
     "that modifies, replaces or overwrites the original. That constraint is built into the "
     "service rather than left to each caller to respect, so a bug or a rushed change elsewhere "
     "cannot corrupt source material that may be the only copy of an intercepted document."),

    ("Split sentences, detect the language, and remember what has already been translated",
     "Development", G5, "Completed", "High", PTR, date(2026, 10, 24), date(2026, 10, 24),
     "Long passages are split into sentences so they can be translated and compared in readable "
     "pieces, the language is detected rather than assumed from a setting, and every completed "
     "translation is remembered. Repeating the same passage therefore costs nothing and returns "
     "the identical wording, which is what makes a translation reproducible. The memory is a "
     "local file outside the database, so the frozen database structure is untouched."),

    ("Decide who may correct a translation, what a correction outranks, and what is recorded",
     "Development", G5, "Completed", "Medium", PTR, date(2026, 10, 24), date(2026, 10, 24),
     "Only permitted roles may correct a translation. A human correction always outranks a "
     "machine result and is never overwritten by a later machine run, so a translator's work "
     "cannot be quietly lost. Each stored entry records who produced it, when, and whether it "
     "was machine or human. Those three rules are enforced in one place, so every route that "
     "touches the memory obeys the same rules."),

    ("Accept translation requests only by form submission, with forgery protection, limits and an audit record",
     "Development", G5, "Completed", "Medium", PTR, date(2026, 10, 24), date(2026, 10, 24),
     "Translation requests are accepted only as deliberate form submissions, require the same "
     "anti-forgery token as every other action that changes state, enforce length limits, "
     "validate their input, and write an audit record naming who asked for what. The result is "
     "that translation cannot be triggered by a link, an image or a page on another site, and "
     "cannot be used to burn through a third-party engine's quota anonymously."),

    ("Let the reader compare, accept or correct a translation, always labelled as unverified",
     "Development", G5, "Completed", "Medium", PTR, date(2026, 10, 24), date(2026, 10, 24),
     "The reader selects text, sees the translation beside the original, and can accept it in "
     "one click or correct it in place. Machine output is labelled as unverified so it can never "
     "be mistaken for a checked translation, which matters on legal and intelligence material. "
     "Accepting is deliberately cheaper than correcting, so the common case stays fast while "
     "the careful case remains available."),

    ("Report translation failures as service-unavailable with a reason, never as a crash",
     "Development", G5, "Completed", "High", PTR, date(2026, 10, 24), date(2026, 10, 24),
     "Two failure paths were returning a generic internal error: a translation memory file that "
     "could not be read, and an engine that could not download its model. Both now answer that "
     "the service is temporarily unavailable, with the reason, which tells an operator what to "
     "fix. A memory file that cannot be read is never silently treated as empty, because an "
     "empty memory would retranslate everything and quietly discard human corrections."),

    ("Put the translation panel where the reader already is",
     "Development", G5, "Completed", "Low", PTR, date(2026, 10, 24), date(2026, 10, 24),
     "The translation control existed but could only be reached through a separate address, so "
     "in practice nobody would find it. It now appears in the reading flow, next to the text it "
     "acts on, which is the only placement that makes a translation feature usable."),

    ("Re-run the hostile-input and route probes after the new translation addresses were added",
     "Testing", G5, "Completed", "Medium", P5, date(2026, 10, 24), date(2026, 10, 24),
     "Nine new web addresses had been added, so the earlier sweeps no longer covered the whole "
     "surface and their clean result had stopped meaning anything. Both sweeps were re-run "
     "against everything, with the same test data and settings as before, so the comparison is "
     "like for like and the clean result covers the new code as well as the old."),

    ("Keep one complete test run, with a browser walk of every screen, as the reference point",
     "Verification", G5, "Completed", "High", P5, date(2026, 10, 24), date(2026, 10, 24),
     "A single complete run was recorded so that later rounds could be compared against "
     "something stable: 4,399 tests with 51 failures, 11 errors and 14 skips, plus a real "
     "browser walk of every screen saved with screenshots. Every failing test was then given a "
     "category - thirty-eight needed documents that are deliberately not published, twenty-four "
     "were waiting on decisions, and none was an unexplained product defect. The last number is "
     "the one that matters: it said the remaining red was bureaucratic, not dangerous."),

    ("Rejoin the work that a machine reset had dropped from the branch",
     "Coordination", G5, "Completed", "Medium", P5, date(2026, 10, 24), date(2026, 10, 24),
     "The working machine had been wiped and the branch rebuilt from scratch, so work that "
     "existed only on the remote copy - two whole rounds of earlier repairs - was no longer in "
     "the branch's history and could have been silently lost or duplicated. It was merged back "
     "in, so the record is complete and no repair exists twice in two slightly different forms."),

    # ================= group 6 =================
    (P6, "Development", G6, "Completed", "High", TOP, date(2026, 10, 25), date(2026, 10, 25),
     "Stage covering the round that corrected the tests themselves, made three failure paths "
     "honest, hardened installation, broke the system on purpose to see how it behaves, "
     "measured speed at full scale, and produced the operator runbook."),

    ("Correct five groups of tests without weakening a single assertion",
     "Testing", G6, "Completed", "High", P6, date(2026, 10, 25), date(2026, 10, 25),
     "Five groups of tests asserted things that are simply not true of this database: a column "
     "that does not exist, index names that differ, and migration numbers belonging to another "
     "project. They were rewritten against what actually exists while keeping the same "
     "behaviours under test - six, eight, twenty-two and twelve tests respectively, plus all the "
     "volume guards. In the index case the test now checks the columns and their order, which is "
     "what actually determines speed, instead of a name that determines nothing. Each group was "
     "shown failing before the rewrite and passing after it."),

    ("Handle a full disk, an unreadable memory file and stale job records honestly",
     "Development", G6, "Completed", "High", P6, date(2026, 10, 25), date(2026, 10, 25),
     "Three failure paths were either dishonest or missing. A full upload volume returned a "
     "generic server error instead of saying storage is exhausted. An unreadable translation "
     "memory returned an internal error instead of saying the memory is unavailable. Jobs left "
     "marked as running by a killed process stayed running for ever, so the queue silently "
     "looked busy when it was empty. All three now report the true condition, and stale running "
     "jobs are cleared the next time the software starts."),

    ("Check index columns rather than index names, and pin the identifier record type",
     "Testing", G6, "Completed", "Medium", P6, date(2026, 10, 25), date(2026, 10, 25),
     "One test asserted the names of database indexes, which proves nothing about speed - two "
     "indexes can have the same name and different columns, or the reverse. It now reads the "
     "database's own description of each index and asserts its columns in order. A second test "
     "now pins the record type the identifier detector must produce, so a rename cannot silently "
     "break every filter that depends on it."),

    ("Make installation fail loudly and clearly on a clean machine",
     "Development", G6, "Completed", "High", P6, date(2026, 10, 25), date(2026, 10, 25),
     "Several installation paths failed quietly. A database with the wrong text encoding was "
     "accepted and would misbehave later. An installation could finish without ever creating the "
     "administrator account needed to use the system, leaving a product nobody could sign in to. "
     "The command-line installer did not work at all on a clean copy of the project. Each path "
     "now refuses to continue and says why. In addition the scheduler's one required package is "
     "declared, a system-settings address is readable by every signed-in role, a place note is "
     "refused rather than silently dropped, and one test was made independent of the rest of the "
     "suite so it cannot fail because of test order."),

    ("Prove how the system behaves by breaking it on purpose",
     "Testing", G6, "Completed", "High", P6, date(2026, 10, 25), date(2026, 10, 25),
     "Failure was injected deliberately rather than imagined: an upload volume with only eight "
     "megabytes free, the database stopped in the middle of an import, the web process killed "
     "outright, and the application restarted mid-job. Each run recorded what the user was told "
     "and what survived. This is behaviour, not a statistical guarantee - each drill was run "
     "once, on a small machine - and it is recorded that way, so nobody mistakes three "
     "successful drills for resilience under load."),

    ("Measure speed at 100,000 records, walk every screen in three right-to-left languages, and sweep for security gaps",
     "Measurement", G6, "Completed", "High", P6, date(2026, 10, 25), date(2026, 10, 25),
     "Speed was measured against a realistic hundred-thousand-record database. Every screen was "
     "then loaded and operated in a real browser at three screen widths in Arabic, Hebrew and "
     "Farsi, because a right-to-left layout breaks differently from a left-to-right one and "
     "nobody had ever looked. Security sweeps were run across the same surface. Every result was "
     "recorded, and each failure was either repaired or listed as open - nothing was quietly "
     "dropped because it was inconvenient."),

    ("Build a benchmark for translation speed and memory, and be explicit that it is not a quality test",
     "Tooling", G6, "Completed", "Medium", P6, date(2026, 10, 25), date(2026, 10, 25),
     "A small tool measures how long translation takes and how much memory it uses, and writes "
     "the result in a form a spreadsheet can read. It has only ever been run against a "
     "placeholder engine, because the real translation engine is not installed on the machine, "
     "so its figures describe the harness and not the engine. Nothing about translation quality "
     "has been measured at any point, and the tool says so rather than presenting a number that "
     "would be misread as a model benchmark."),

    ("Write the operator runbook: install, upgrade, backup, restore, health and logs",
     "Documentation", G6, "Completed", "High", P6, date(2026, 10, 25), date(2026, 10, 25),
     "An operator with a working database now has a step-by-step procedure for installing the "
     "software, upgrading it, taking a backup, restoring that backup, checking health and reading "
     "logs. The section on failure behaviour states only what was actually measured, and the "
     "runbook also says plainly what it cannot tell you - restoring into a different database "
     "version, for instance, has never been tried."),

    # ================= group 7 =================
    (P7, "Development", G7, "Completed", "High", TOP, date(2026, 10, 25), date(2026, 10, 25),
     "Stage covering the round that made a real category hierarchy possible for the first time, "
     "delivered the web interfaces behind it, measured the tree at scale before designing the "
     "screen, and built that screen with list and map views."),

    ("Replace the rule that limited categories to two levels with a guard against loops",
     "Development", G7, "Completed", "High", P7, date(2026, 10, 25), date(2026, 10, 25),
     "The database enforced a 'two levels only' rule with a trigger, which made any deeper "
     "category structure impossible - a real limitation for anyone organising a large collection. "
     "A new database change replaces that trigger with a guard that permits any depth while "
     "refusing a loop: a category cannot become its own ancestor, directly or through any chain. "
     "This is the only database change made in the whole engagement, it is reversible, and its "
     "reversal and re-application were both exercised on a database that already contained data."),

    ("Add the web interfaces needed to browse, adopt, move and delete categories in a tree",
     "Development", G7, "Completed", "High", P7, date(2026, 10, 25), date(2026, 10, 25),
     "New addresses return categories level by level with their depth, insert a new category "
     "above an existing one, move a category while refusing any move that would create a loop, "
     "and delete with the necessary checks. Loop refusal is enforced by the database itself, so "
     "no caller - a screen, a script or a future integration - can bypass it by accident."),

    ("Measure the category tree at 10,000 and 100,000 categories before building the screen",
     "Measurement", G7, "Completed", "Medium", P7, date(2026, 10, 25), date(2026, 10, 25),
     "The screen was designed after measuring rather than before. The tree was loaded at ten "
     "thousand and at a hundred thousand categories and timed, and the numbers showed that "
     "returning the whole tree in one answer would not work at the larger size. That single "
     "measurement decided the shape of everything that followed: levels had to be fetched on "
     "demand."),

    ("Build the category hierarchy screen with list and map views",
     "Development", G7, "Completed", "High", P7, date(2026, 10, 25), date(2026, 10, 25),
     "A new screen shows categories as a tree in a list and on a map, lets a person choose a "
     "parent when creating a category, drag a category onto another to move it, operate the "
     "whole thing from the keyboard, and export the tree as a file for a spreadsheet. Loop "
     "prevention and permission checks apply to every route into the data, not only to the "
     "buttons on the screen."),

    ("Drive the new category screens in a real browser and record what happened",
     "Verification", G7, "Completed", "High", P7, date(2026, 10, 25), date(2026, 10, 25),
     "The new screens were operated in a real browser rather than assumed to work: creating a "
     "category, moving one under another, navigating by keyboard and exporting the tree were each "
     "performed, and the outcome of each was recorded together with the request it produced. "
     "Automated tests can pass while a screen is unusable; only a browser catches that."),

    ("Write the operator and developer documentation and put it up for human review",
     "Documentation", G7, "Pending Review", "Medium", P7, date(2026, 10, 25), date(2026, 10, 25),
     "Three documents were written for people rather than for tests: how to configure a "
     "deployment, how to work on the code itself, and what the security model protects and what "
     "it does not. They are finished and committed, but no human has read them yet, so they are "
     "held as pending review and treated as unapproved. A document nobody has read is not "
     "evidence, and publishing one as though it were would repeat the original problem of "
     "documentation drifting away from reality."),

    # ================= group 8 =================
    (P8, "Development", G8, "Completed", "High", TOP, date(2026, 10, 25), date(2026, 10, 25),
     "Stage covering the round that repaired screens which had stopped rendering, replaced the "
     "whole-tree request with on-demand levels, brought the published list of web addresses back "
     "in line with reality, and proved the new database change can be undone."),

    ("Regenerate the list of every published web address by machine instead of by hand",
     "Verification", G8, "Completed", "Medium", P8, date(2026, 10, 25), date(2026, 10, 25),
     "The published list of web addresses had been edited by hand and had drifted from what the "
     "software actually offers. It is now produced by a script that asks the running application "
     "itself, so it cannot be wrong: 501 addresses, including the three new category-tree "
     "addresses, and no addresses that exist only while tests are running. A hand-maintained "
     "list of interfaces is a liability; a generated one is evidence."),

    ("Fetch category levels on demand so that one screenful costs one level, not the whole tree",
     "Development", G8, "Completed", "High", P8, date(2026, 10, 25), date(2026, 10, 25),
     "Returning an entire category tree in one answer is fine for a few hundred categories and "
     "hopeless for a hundred thousand - the browser would stall and the server would spend its "
     "effort serialising data nobody is looking at. Three addresses now return only the roots, "
     "only one level's children, and the counts. Opening a page now costs one level of work "
     "regardless of how large the tree is."),

    ("Repair the category list screen and make the parent selectors correct and immediate",
     "Development", G8, "Completed", "High", P8, date(2026, 10, 25), date(2026, 10, 25),
     "The category list screen had stopped rendering at all, and its parent column could not name "
     "a parent whose row happened to sit on another page, so a hierarchy looked flat whenever it "
     "spanned a page boundary. Both are fixed: the screen renders again, and parents are named "
     "correctly even when they are on a different page. The parent selector also paints "
     "immediately with what is already known instead of showing an empty box until a request "
     "returns, which made people think it had nothing to offer."),

    ("Give the route sweeps real category identifiers and re-run them",
     "Testing", G8, "Completed", "Medium", P8, date(2026, 10, 25), date(2026, 10, 25),
     "The parameterised sweeps had been sending the literal text of a placeholder where a real "
     "identifier belongs, so they were proving only that the software rejects nonsense - "
     "impressive-looking and worthless. Real identifiers were supplied from a live database and "
     "all three sweeps were re-run. Their clean result now means something, because the requests "
     "were real."),

    ("Assert the current database revision and prove the new category change can be undone",
     "Testing", G8, "Completed", "High", P8, date(2026, 10, 25), date(2026, 10, 25),
     "Two test fixtures insisted the newest database revision was number three, so the fourth "
     "change broke sixteen tests the moment it landed. They now assert whatever the current "
     "revision actually is, so the next change will not break them. The new database change was "
     "then applied, undone and re-applied on a database that already held data, which is the only "
     "case that matters: an empty database proves almost nothing about a migration."),

    ("Regenerate the database description so it describes the database that exists",
     "Documentation", G8, "Completed", "Medium", P8, date(2026, 10, 25), date(2026, 10, 25),
     "The published description of the database became wrong the moment the category rule "
     "changed. It was regenerated from the chain of changes that actually builds the database, so "
     "the description cannot disagree with the code. This is the difference between documentation "
     "that is generated and documentation that is remembered."),

    ("Keep test-only addresses out of the published list, and stop the browser's cache being committed",
     "Tooling", G8, "Completed", "Low", P8, date(2026, 10, 25), date(2026, 10, 25),
     "The address generator had started recording addresses that exist only while tests are "
     "running, which would have made the published list describe something no deployment has. It "
     "now excludes them. The browser used for testing writes a cache folder into the project "
     "directory; that folder is now ignored so it cannot be committed by accident and cannot make "
     "an unrelated file look changed."),

    ("Re-run the full test suite on a clean copy and compare it with the previous round",
     "Verification", G8, "Completed", "High", P8, date(2026, 10, 25), date(2026, 10, 25),
     "Two complete runs were made on fresh copies of the project with fresh databases: 67 failing "
     "unit tests and 7 failing integration tests, exactly the same identifiers as the previous "
     "round, and not one new failure introduced. Twelve unit tests and one integration test that "
     "had failed before now pass. The same numbers twice, from a clean copy rather than a used "
     "one, is what makes them worth quoting."),

    # ================= group 9 =================
    (P9, "Development", G9, "Completed", "High", TOP, date(2026, 10, 25), date(2026, 10, 25),
     "Stage covering the round that fixed two defects a real person would hit within minutes of "
     "using the software, and recorded the state of the test suite after the fixes."),

    ("Repair the Add-category dialog on the category list screen",
     "Development", G9, "Completed", "High", P9, date(2026, 10, 25), date(2026, 10, 25),
     "Creating a category from the list screen's dialog could not be completed at all: a check on "
     "the chosen parent rejected valid choices, so the save never happened and the person was left "
     "with a form that did nothing. The check was corrected, and creating a category from that "
     "screen was then performed in a real browser and confirmed to produce the category. This "
     "defect existed despite the surrounding screens being tested, which is why browser work is "
     "part of the method."),

    ("Stop the software behaving differently depending on which folder it is started from",
     "Development", G9, "Completed", "Medium", P9, date(2026, 10, 25), date(2026, 10, 25),
     "A marker file that tells the software it has been installed was being looked up relative to "
     "whatever folder the process happened to start in. Started from a different folder, the "
     "application could behave as though it had never been installed, or as though a fresh "
     "installation were needed. It is now resolved from the data folder, which is the same "
     "wherever the process starts - a small change that removes a whole class of confusing "
     "support reports."),

    ("Record the state of the test suite and the repaired dialog after the two fixes",
     "Verification", G9, "Completed", "High", P9, date(2026, 10, 25), date(2026, 10, 25),
     "The full suite was run again after the two repairs: the failing test identifiers were "
     "unchanged from the previous round, and the browser pass that creates a category from the "
     "list screen succeeded. Both facts are recorded so the repairs can be judged against the "
     "state that existed before them, rather than against a reassuring recollection."),

    # ================= group 10 =================
    (P10, "Development", G10, "Completed", "High", TOP, date(2026, 10, 25), date(2026, 10, 25),
     "Stage covering the current round: making sure each button does exactly one thing, driving "
     "the category insertion path in a real browser, finding out whether the text-recognition "
     "failures were real defects, and re-measuring the system at full scale so the numbers can be "
     "compared with the earlier run."),

    ("Give every clickable action exactly one owner on each screen",
     "Development", G10, "Completed", "High", P10, date(2026, 10, 25), date(2026, 10, 25),
     "Eight action names were defined in more than one script, so which one a button actually ran "
     "depended on the order the scripts happened to load - a fault that appears on some screens "
     "and not others, and that no amount of testing one screen would catch. Each name now has "
     "exactly one owner on each screen. A real browser then opened all eighteen affected screens, "
     "clicked each control and recorded which request or effect each click produced: seventy "
     "checks with no unexpected result. Eight remaining differences were classified, four as real "
     "defects on two screens and four as gaps in the test data rather than in the product."),

    ("Prove in a browser that a new category can be inserted above an existing one",
     "Verification", G10, "Completed", "High", P10, date(2026, 10, 25), date(2026, 10, 25),
     "The 'adopt' path - inserting a new parent above a category that already exists - had only "
     "ever been exercised by script, which proves the interface works but not that the screen can "
     "reach it. It was performed through the screens in a real browser: all ten checks passed, "
     "the request returned a created result, and the new parent correctly reported its two "
     "children. This closes a gap that an earlier round had explicitly listed as unverified."),

    ("Find out whether the text-recognition failures were real defects, and repair the actual cause",
     "Verification", G10, "Completed", "High", P10, date(2026, 10, 25), date(2026, 10, 25),
     "Thirty-one tests about reading text out of images and scanned pages were failing, which "
     "looked like a broken feature. The cause was the machine, not the product: it had no "
     "text-recognition engine installed, and the image library the alternative engine needs was "
     "the wrong build. With the correct library installed, the eight affected test files gave 188 "
     "tests with two failures and no errors. Every remaining failure was then explained "
     "individually: twenty-nine were the missing engine, two need a document that is deliberately "
     "not written yet, and none was a defect in the text-recognition code itself. The value here "
     "is the classification - thirty-one scary red tests became zero defects and two known gaps."),

    ("Re-measure at 100,000 records and compare the result with the earlier run",
     "Measurement", G10, "Completed", "Medium", P10, date(2026, 10, 25), date(2026, 10, 25),
     "The hundred-thousand-record database was rebuilt in twenty-nine seconds, thirty-three "
     "requests were served, and they issued two hundred and sixty database statements in total. "
     "The last page of five hundred results took twenty-one milliseconds against twelve for the "
     "first, the heaviest management screens took a quarter of a second, an identifier filter "
     "took under a tenth of a second, and no screen issued a separate query per row. The earlier "
     "run is shown beside the new one, because a performance number on its own means nothing."),

    ("Put the whole repair branch up for review instead of merging it",
     "Coordination", G10, "Pending Review", "High", P10, date(2026, 10, 25), UNV,
     "All the work sits on a separate branch and has been opened for review rather than merged. "
     "The change is large, it touches behaviour a person can see, and the main line must stay "
     "untouched until someone has looked at it. Review is the current state: nothing has been "
     "merged, nothing is promised for a particular day, and the owner decides whether it goes in, "
     "waits, or is revised."),

    # ================= group 11 =================
    (P11, "Planning", G11, "Working", "High", TOP, date(2026, 10, 25), UNV,
     "Stage holding everything that is unfinished, blocked on a decision, or was never attempted. "
     "Each entry states what is missing and why, in plain terms, and no entry claims a completion "
     "date that nobody has evidence for. Nothing here is a guess about what somebody intended: "
     "each line describes a gap that can be seen in the software as it stands."),

    ("Finish the full test comparison on a clean copy of the review branch",
     "Testing", G11, "Working", "High", P11, date(2026, 10, 25), UNV,
     "A clean copy of the branch was made and the unit half of the test suite has finished: 3,268 "
     "tests, of which 3,218 passed, 28 failed, 11 could not start and 11 were skipped - thirty-"
     "nine failing identifiers. Compared with the previous round, twenty-nine identifiers now "
     "pass, all of them text-recognition tests that had been failing only because the engine was "
     "missing, and one is newly failing. The integration half was started but had not finished, "
     "so there is still no complete two-part figure for the current state of the branch. Until "
     "there is, the newest numbers in this register are the unit half only."),

    ("Repair the test broken by this round's action renaming",
     "Testing", G11, "Completed", "High", P11, date(2026, 10, 25), date(2026, 10, 25),
     "One test was newly failing because of this round's own work. It insisted on a specific line "
     "of code that published a select-all action under an old name, and that line had been replaced "
     "when action names were given single owners. The product behaviour was correct and was left "
     "alone: the files screen owns the name and the shared module exports its copy under a suffix "
     "that the control using it names explicitly. The test was the stale side, so it now checks the "
     "ownership rule instead of the text of one assignment - the suffixed export exists, the shared "
     "module does not claim the name the screen owns, and the control that uses the shared copy "
     "names it - which is stricter than what it replaced. It was shown failing before the change "
     "and passing after it, in five passing tests with three skipped for lack of a JavaScript "
     "runtime, alongside the ten action-ownership tests."),
    ("Write the four written references that have never existed",
     "Documentation", G11, "Open", "Medium", P11, date(2026, 10, 24), UNV,
     "Several checks expect four written references: a register of every interface the software "
     "offers, the evidence behind that register, a map of which parts are genuinely product-ready, "
     "and a description of the subject matter the software models. None of the four has ever "
     "existed, so eleven interface checks and two text-recognition checks cannot pass, and "
     "thirteen failures are waiting on a decision rather than on code. Writing them was "
     "deliberately not done to turn tests green, because a document written only to satisfy a test "
     "is the same drift the whole engagement is fighting."),

    ("Translate the 562 untranslated phrases into the four supported languages",
     "Documentation", G11, "Open", "Medium", P11, date(2026, 10, 24), UNV,
     "Five hundred and sixty-two phrases that appear in the interface have no translation into "
     "Arabic, Hebrew, Farsi or Croatian, and the checks that insist no entry is empty or marked "
     "uncertain still fail for all four languages. Machine translation was refused deliberately: a "
     "wrong translation on a legal or intelligence screen is worse than an untranslated phrase, "
     "because the reader cannot tell that it is wrong. This waits for a human translator, and no "
     "date is given because the translator's availability is not something this project can see."),

    ("Start the scheduler that is declared but has never run",
     "Development", G11, "Open", "Medium", P11, date(2026, 10, 24), UNV,
     "The software declares a scheduler for time-based work, but at every start it fails to load, "
     "and nothing scheduled has ever run - the same error appears in every server log. Wiring it "
     "up was explicitly out of scope until the owner answers three open questions, so no scheduled "
     "task has ever been verified as working. It stays listed because an unreachable feature that "
     "looks present is worse than a missing one."),

    ("Fix the bulk-delete button on the keywords and words screens",
     "Development", G11, "Open", "Medium", P11, date(2026, 10, 25), UNV,
     "On two screens the bulk-delete control never becomes usable: the shared action bar switches "
     "the button off and never restores the instruction that tells it what to run, so no delete "
     "request is ever sent. Four automated browser checks on those two screens record that the "
     "request is never issued. The cause is identified precisely; the repair has not been written, "
     "and nothing is claimed about it beyond the observation."),

    ("Re-run the whole text-recognition test family with an engine installed",
     "Testing", G11, "Open", "Low", P11, date(2026, 10, 25), UNV,
     "Only the eight test files that directly touch text recognition were re-run once the engine "
     "was working. The rest of that family - scanned pages, images embedded inside documents, "
     "rotated pages and searchable output - still carries failures recorded before the engine was "
     "fixed. Their present state is therefore unknown rather than passing, and saying otherwise "
     "would be exactly the kind of inherited claim this engagement exists to eliminate."),

    ("Lift the 500-category limit in the list screen's parent selector",
     "Development", G11, "Open", "Low", P11, date(2026, 10, 25), UNV,
     "The parent selector on the category list screen asks for at most five hundred categories in "
     "a single request. Above that number a person cannot choose every possible parent, and the "
     "list silently stops being complete - the failure mode where the missing options look like "
     "options that do not exist. The tree screens already handle this correctly by fetching levels "
     "on demand; the list screen has not been converted."),

    ("Build and install the offline package on a Windows machine",
     "Testing", G11, "Open", "Low", P11, date(2026, 10, 24), UNV,
     "There is an offline installation kit for machines with no internet access, and a check that "
     "its list of required packages matches what is actually installed. Parts of that check fail, "
     "and the kit itself has never been built, installed or used on Windows, because no Windows "
     "machine was available. Nothing about the offline installation path has been verified, so it "
     "should be treated as written but untested."),

    ("Measure translation quality with a real engine",
     "Measurement", G11, "Open", "Low", P11, date(2026, 10, 24), UNV,
     "Translation has been measured only for speed and memory, and only against a placeholder "
     "engine. Whether the translation is any good has never been measured, because the real engine "
     "is not installed. Until someone does that work, no statement about translation quality can "
     "honestly be made, and the existing timings must not be presented as if they were one."),

    ("Test the software under real concurrent load",
     "Testing", G11, "Open", "Medium", P11, date(2026, 10, 24), UNV,
     "Every speed figure in this register comes from one user on a small machine. There has been "
     "no multi-user soak test and no test of what happens when the database connection pool is "
     "exhausted. The software is documented as supporting exactly one process per database, and "
     "that limit itself has never been load-tested, so the safe operating envelope is an assertion "
     "rather than a measurement."),

    ("Restore a backup into a different version of the database server",
     "Testing", G11, "Open", "Low", P11, date(2026, 10, 24), UNV,
     "Backup and restore were tested from one version of PostgreSQL into the same version. "
     "Restoring into a newer or older major version - the situation an operator actually faces "
     "when rebuilding a server - has never been attempted. The runbook says so instead of implying "
     "that any restore will work."),

    ("Redesign how place names are stored and matched",
     "Planning", G11, "Open", "Low", P11, date(2026, 10, 24), UNV,
     "Places are currently identified by a generated key that the rest of the system treats as a "
     "stopgap, and matching a place mentioned in a document against the bundled list is cruder "
     "than it should be. A proper design - typed place categories, single-word terms and "
     "multi-word phrases treated differently - was set aside by instruction, so nothing about it "
     "has been designed or tested. It is a database change, so it also needs the freeze lifted."),

    ("Exercise the 65 addresses that had no usable test data",
     "Testing", G11, "Open", "Low", P11, date(2026, 10, 24), UNV,
     "Of one hundred and fifty-five addresses that accept an identifier, sixty-five could not be "
     "tried because the records they need did not exist in the test database. They are recorded as "
     "untested rather than as passing, and they remain the largest single gap in coverage of the "
     "software's web surface. Closing it means building realistic test data through the "
     "application's own interfaces rather than inserting rows directly."),

    ("Make the interface-coverage check generate its evidence from a clean application",
     "Testing", G11, "Open", "Low", P11, date(2026, 10, 24), UNV,
     "One check fails for a reason inside the check itself. It builds the list of published "
     "addresses during the same test session in which the security tests have registered extra "
     "addresses, so the list it compares against contains routes that no real installation has. "
     "The check should build a fresh application before generating its comparison, which would "
     "make it measure the product instead of the test session."),

    ("Build the report dataset and run engine that two maintenance tools load at start-up",
     "Development", G11, "Open", "Medium", P11, date(2026, 10, 24), UNV,
     "Two maintenance tools - one for a translation report and one for measuring keyword "
     "significance - begin by loading a reporting module that does not exist, so both fail "
     "immediately. Building it means writing the analytical query for keyword significance, a "
     "catalogue of reports and a run engine: a project in its own right, not a patch, and "
     "therefore a decision for the owner rather than something to slip in. This is the same gap "
     "recorded earlier as stopped work; it is repeated here so that no reader believes it closed."),
]

# ---------------------------------------------------------------- validation
allowed_status = set(STATUSES)
allowed_priority = set(PRIORITIES)
lo, hi = date(2026, 10, 1), TODAY
seen = set()
for row in ROWS:
    title, typ, group, status, priority, parent, start, end, desc = row
    assert status in allowed_status, (title, status)
    assert priority in allowed_priority, (title, priority)
    assert title not in seen, title
    seen.add(title)
    for d in (start, end):
        if isinstance(d, date):
            assert lo <= d <= hi, (title, d)
    assert start != UNV, ("a task must have a start date", title)

# ---------------------------------------------------------------- workbook
INK = "1F2A37"
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
LABEL_FILL = PatternFill("solid", fgColor="2F5597")
SECTION_FILL = PatternFill("solid", fgColor="D9E2F3")
PARENT_FILL = "EDF2FA"
EST_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
STATUS_STYLE = {
    "Completed": ("1E6B34", "E3F2E7"),
    "Working": ("1F4E9C", "DEE8F8"),
    "Pending Review": ("7A4A00", "FDEBC8"),
    "Open": ("595959", "EFEFEF"),
}
PRIORITY_STYLE = {
    "Urgent": ("9C2A1F", "FBE3E0"),
    "High": ("8A3B00", "FDF0E1"),
    "Medium": ("1F4E9C", "EAF0FA"),
    "Low": ("595959", "F4F4F4"),
}
DATE_FMT = "yyyy-mm-dd"

wb = Workbook()

# ---------------------------------------------------- sheet 1: project info
ws = wb.active
ws.title = "Project Information"
ws.sheet_view.showGridLines = False
ws.column_dimensions["A"].width = 26
ws.column_dimensions["B"].width = 98

ws.merge_cells("A1:B1")
c = ws["A1"]
c.value = "PROJECT INFORMATION"
c.font = Font(bold=True, size=14, color="FFFFFF")
c.fill = HEADER_FILL
c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
ws.row_dimensions[1].height = 32

for col, label in (("A", "Field"), ("B", "Value")):
    cell = ws[f"{col}2"]
    cell.value = label
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = LABEL_FILL
    cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
ws.row_dimensions[2].height = 20

r = 3
for field, value in PROJECT_INFO:
    fcell = ws.cell(row=r, column=1, value=field)
    fcell.font = Font(bold=True, color=INK)
    fcell.alignment = Alignment(vertical="top", indent=1)
    vcell = ws.cell(row=r, column=2, value=value)
    vcell.alignment = Alignment(wrap_text=True, vertical="top", indent=1)
    for col in (1, 2):
        cc = ws.cell(row=r, column=col)
        cc.border = BORDER
        cc.fill = SECTION_FILL if r % 2 == 1 else PatternFill("solid", fgColor="FFFFFF")
    if isinstance(value, date):
        vcell.number_format = DATE_FMT
        ws.row_dimensions[r].height = 22
    elif value == UNV:
        vcell.fill = EST_FILL
        vcell.font = Font(italic=True, color="7F6000")
        ws.row_dimensions[r].height = 22
    else:
        ws.row_dimensions[r].height = 46 if len(value) < 180 else 62
    r += 1

ws.merge_cells(start_row=r + 1, start_column=1, end_row=r + 1, end_column=2)
note = ws.cell(row=r + 1, column=1,
               value="Expected End Date is recorded as Unverified because no plan anywhere states a "
                     "completion date. Using today's date would have invented one. An amber cell means "
                     "the value is an estimate or is not evidenced; the Task Register uses the same "
                     "marking.")
note.font = Font(italic=True, size=9, color="595959")
note.alignment = Alignment(wrap_text=True, vertical="top")
ws.row_dimensions[r + 1].height = 34

# ---------------------------------------------------- sheet 2: task register
ws2 = wb.create_sheet("Task Register")
HEADERS = [
    "Subject or Tasks",
    "Project",
    "Type",
    "Does it fall under a group?",
    "Status",
    "Priority",
    "Main Task (Original/Parent)",
    "Expected Start Date",
    "Expected End Date",
    "Details and Task Description",
]
ws2.append(HEADERS)
for i in range(1, len(HEADERS) + 1):
    cell = ws2.cell(row=1, column=i)
    cell.font = Font(bold=True, color="FFFFFF", size=10)
    cell.fill = HEADER_FILL
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = BORDER
ws2.row_dimensions[1].height = 36

row = 2
for title, typ, group, status, priority, parent, start, end, desc in ROWS:
    is_parent = parent == TOP
    is_estimated = end == UNV
    values = [title, PROJECT, typ, group, status, priority, parent, start, end, desc]
    for col, value in enumerate(values, start=1):
        cell = ws2.cell(row=row, column=col, value=value)
        cell.border = BORDER
        cell.alignment = Alignment(vertical="top", wrap_text=True,
                                   indent=1 if (is_parent or parent != TOP) else 0)
        cell.font = Font(size=10, color=INK)
        if isinstance(value, date):
            cell.number_format = DATE_FMT
            cell.alignment = Alignment(horizontal="center", vertical="top")
        if value == UNV:
            cell.alignment = Alignment(horizontal="center", vertical="top")
            cell.fill = EST_FILL
            cell.font = Font(italic=True, size=9, color="7F6000")
        elif col in (2, 3, 5, 6):
            cell.alignment = Alignment(horizontal="center", vertical="top", wrap_text=True)

    if is_parent:
        for col in range(1, 11):
            ws2.cell(row=row, column=col).fill = PatternFill("solid", fgColor=PARENT_FILL)
        ws2.cell(row=row, column=1).font = Font(bold=True, size=10, color=INK)
    else:
        ws2.cell(row=row, column=1).font = Font(bold=True, size=10, color="2F5597")

    fg, bg = STATUS_STYLE[status]
    sc = ws2.cell(row=row, column=5)
    sc.font = Font(bold=True, size=9, color=fg)
    sc.fill = PatternFill("solid", fgColor=bg)

    fg, bg = PRIORITY_STYLE[priority]
    pc = ws2.cell(row=row, column=6)
    pc.font = Font(bold=True, size=9, color=fg)
    pc.fill = PatternFill("solid", fgColor=bg)

    if is_estimated:
        for col in (8, 9):
            cc = ws2.cell(row=row, column=col)
            cc.fill = EST_FILL
            cc.font = Font(italic=True, size=9, color="7F6000")

    import math
    lines = max(math.ceil(len(title) / 44), math.ceil(len(desc) / 88),
                math.ceil(len(group) / 30), math.ceil(len(parent) / 38), 2)
    ws2.row_dimensions[row].height = min(190, lines * 12.6 + 8)
    row += 1

last_row = row - 1
for col, width in {"A": 50, "B": 15, "C": 14, "D": 32, "E": 15, "F": 10,
                   "G": 34, "H": 12, "I": 12, "J": 100}.items():
    ws2.column_dimensions[col].width = width
ws2.freeze_panes = "B2"
ws2.auto_filter.ref = f"A1:J{last_row}"
ws2.sheet_view.zoomScale = 90

# ---------------------------------------------------- sheet 3: how to read
ws3 = wb.create_sheet("How to Read This")
ws3.sheet_view.showGridLines = False
ws3.column_dimensions["A"].width = 3
ws3.column_dimensions["B"].width = 118

ws3.merge_cells("B1:B1")
t = ws3["B1"]
t.value = "HOW TO READ THIS REGISTER"
t.font = Font(bold=True, size=14, color="FFFFFF")
t.fill = HEADER_FILL
t.alignment = Alignment(vertical="center", indent=1)
ws3.row_dimensions[1].height = 32


def block(title, lines, start_row):
    c = ws3.cell(row=start_row, column=2, value=title)
    c.font = Font(bold=True, size=12, color="1F3864")
    start_row += 1
    for line in lines:
        cell = ws3.cell(row=start_row, column=2, value=line)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.font = Font(size=10, color=INK)
        start_row += 1
    return start_row + 1


r = 3
r = block("What this workbook is", [
    "It is a record of work done on one software product between 23 and 25 October 2026, written so "
    "that a reader who knows nothing about software can follow it. Each task says what was wrong, "
    "what was done about it and what was checked afterwards. Nothing in it requires another file to "
    "be understood.",
    "It is not a plan or a promise: dates are either the day the work actually happened or an amber "
    "cell saying the date is not evidenced.",
], r)

r = block("The status values, and what each one means here", [
    "Completed - the work is done and was checked afterwards. Used only where a test, a measurement "
    "or an observed result exists.",
    "Working - started and not finished. The description says exactly how far it got.",
    "Pending Review - finished by the person who did it, but not yet read, approved or merged by "
    "someone else. It is treated as unapproved until that happens.",
    "Open - known, described and not yet started, or stopped deliberately because a decision is "
    "needed first.",
    "Overdue - not used. Nothing in this record has an agreed due date, so calling a task overdue "
    "would invent a commitment nobody made.",
    "Template - not used. This register records real work rather than reusable task patterns.",
    "Cancelled - not used. Nothing was abandoned after starting; the one task that was stopped is "
    "kept as Open for the reason given in its description.",
], r)

r = block("The priority values", [
    "Urgent - must be settled before the branch under review can be accepted. Not used in the "
    "final version: the one item that earned it, the single test this round broke, has been "
    "repaired and is recorded as completed.",
    "High - the work affects whether a person can use the software, whether data can be lost, or "
    "whether a claim can be trusted.",
    "Medium - real defects and real gaps, but with a workaround or a limited blast radius.",
    "Low - clean-up, documentation and improvements that do not change whether the software works.",
    "Priorities were taken from the severity already assigned when each problem was found, not "
    "assigned afresh for this workbook.",
], r)

r = block("How the dates work", [
    "Every date in the two date columns falls inside October 2026, and no date is later than "
    "25 October 2026, the day this register was produced.",
    "A plain date is a real date: the day the work actually started or finished, taken from the "
    "project's own history.",
    "An amber cell reading Unverified is the honest answer that no evidenced date exists. The "
    "description says what is missing and, where it can be judged, roughly how much work remains "
    "in words rather than in an invented date.",
    "The work described here began on 23 October 2026. Nothing earlier is claimed, because the "
    "project's history contains no earlier record of work.",
    "For a task that has never been started, the start column shows the day the gap was first "
    "recorded as known work, and the end column says Unverified because nothing has been finished "
    "and no completion has been agreed.",
], r)

r = block("How the tasks are organised", [
    "The eleven numbered rows ending in '.0 Stage:' are stage headings. Each one groups the work of "
    "a single stretch of effort, and each child task names its stage in the 'Main Task (Original/"
    "Parent)' column.",
    "The translation feature is a sub-stage inside stage 5, and its eight tasks name it as their "
    "parent, so there are three levels in that part of the register.",
    "The 'Does it fall under a group?' column repeats the stage name on every child row so that a "
    "filtered or sorted view stays readable.",
    "The 'Type' column uses plain words: Development, Testing, Verification, Research, Planning, "
    "Measurement, Documentation, Tooling, Setup and Coordination.",
], r)

r = block("What is covered", [
    "One hundred and five rows: eleven stage headings, one sub-stage heading, ninety-three "
    "individual tasks, covering every piece of work recorded in the project between 23 and 25 "
    "October 2026 - research, planning, building, testing, measuring, documenting and coordinating.",
    "Seventy-five individual tasks are completed, eighty-six rows in total once the finished stage "
    "headings are counted. Every completed task states the check that followed it.",
    "Eighteen individual tasks are still open - two in progress, two awaiting review, fourteen not "
    "started - and each one states plainly what is missing and what it would take.",
    "The register distinguishes work that changed the product from work that checked it: forty-five "
    "rows are development, forty are testing, verification or measurement, ten are documentation and "
    "the rest are planning, setup, tooling or coordination.",
], r)

r = block("What is not covered, and why", [
    "Anything before 23 October 2026. The software is older than this record, but its history was "
    "captured in a single step with no dates, so no earlier work can be dated without inventing it.",
    "Anything after 25 October 2026. No future commitment is recorded, and no open task has a "
    "promised completion date.",
    "Work that left no trace. Three early rounds were lost when the working machine was wiped and "
    "had to be rebuilt; the repairs survived, but their individual dates did not always do so.",
    "The two documents that describe how to run and develop the software have been written but not "
    "read by a human, so they appear as pending review rather than as completed documentation.",
    "Four written references, the translations into four languages, the scheduled-work feature and "
    "the scaled tests are genuinely unfinished. They are listed as open rather than quietly omitted.",
], r)

wb.save(OUT)
print("wrote", OUT)
print("rows:", len(ROWS), "register rows end at", last_row)
