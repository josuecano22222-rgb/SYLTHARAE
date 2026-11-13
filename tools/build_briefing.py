from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.dml import MSO_THEME_COLOR
from pathlib import Path

OUT=Path('deliverables'); OUT.mkdir(exist_ok=True)
prs=Presentation(); prs.slide_width= Inches(13.333); prs.slide_height= Inches(7.5)
BLANK=prs.slide_layouts[6]
BG=RGBColor(10,18,30); PANEL=RGBColor(20,33,49); PANEL2=RGBColor(27,44,62); WHITE=RGBColor(235,243,249); MUTED=RGBColor(154,177,195); CYAN=RGBColor(56,208,202); BLUE=RGBColor(79,153,235); AMBER=RGBColor(255,190,92); RED=RGBColor(255,116,126); GREEN=RGBColor(104,215,164)
FONT='Aptos'; MONO='Aptos Mono'
slide_no=0

def shape(slide,x,y,w,h,fill=PANEL, radius=True, line=None):
    s=slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    s.fill.solid(); s.fill.fore_color.rgb=fill; s.line.fill.background() if not line else None
    if line: s.line.color.rgb=line
    if radius:
        try:s.adjustments[0]=.12
        except:pass
    return s

def text(slide,x,y,w,h,txt,size=16,color=WHITE,bold=False,font=FONT,align=None):
    box=slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)); tf=box.text_frame; tf.clear(); tf.word_wrap=True
    tf.margin_left=Inches(.02);tf.margin_right=Inches(.02);tf.margin_top=Inches(.02);tf.margin_bottom=Inches(.02)
    p=tf.paragraphs[0];p.text=txt;p.font.name=font;p.font.size=Pt(size);p.font.bold=bold;p.font.color.rgb=color
    if align is not None:p.alignment=align
    return box

def base(title, section='TECHNICAL BRIEFING', source='Evidence: repository documentation and implementation; paths noted on slide'):
    global slide_no; slide_no+=1; s=prs.slides.add_slide(BLANK); s.background.fill.solid(); s.background.fill.fore_color.rgb=BG
    # accent rail + section label
    shape(s,0,0,.09,7.5,CYAN,False); text(s,.5,.2,7,.25,section.upper(),9,CYAN,True)
    text(s,.5,.55,12.2,.58,title,27,WHITE,True)
    shape(s,.5,1.28,.55,.035,CYAN,False)
    text(s,.52,7.12,10,.2,source,8,MUTED)
    text(s,12.12,7.05,.65,.25,f'{slide_no:02}',10,CYAN,True,align=PP_ALIGN.RIGHT)
    return s

def cards(title, items, section='SYSTEM', source='Evidence: docs/ARCHITECTURE.md; see appendix source index'):
    s=base(title,section,source); n=len(items); cols=2 if n>3 else 2; rows=(n+cols-1)//cols; gap=.2; x0=.55; y0=1.55; cw=(12.2-gap)/2; ch=(5.35-(rows-1)*gap)/rows
    for i,(head,body) in enumerate(items):
        x=x0+(i%cols)*(cw+gap); y=y0+(i//cols)*(ch+gap)
        shape(s,x,y,cw,ch,PANEL)
        text(s,x+.2,y+.16,cw-.4,.36,head,15,CYAN,True)
        text(s,x+.2,y+.62,cw-.4,ch-.78,body,12.5,WHITE)
    return s

def flow(title,steps,section='WORKFLOWS',source='Code-derived workflow; not independently executed in this briefing'):
    s=base(title,section,source); n=len(steps); gap=.16; w=(12.2-gap*(n-1))/n; y=2.0
    for i,(h,b) in enumerate(steps):
        x=.55+i*(w+gap); shape(s,x,y,w,3.3,PANEL)
        shape(s,x+.17,y+.18,.43,.43,CYAN); text(s,x+.17,y+.205,.43,.26,str(i+1),13,BG,True,align=PP_ALIGN.CENTER)
        text(s,x+.17,y+.8,w-.34,.55,h,14,CYAN,True); text(s,x+.17,y+1.46,w-.34,1.65,b,11.5,WHITE)
        if i<n-1:
            ln=s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x+w), Inches(y+1.65), Inches(x+w+gap), Inches(y+1.65));ln.line.color.rgb=CYAN;ln.line.width=Pt(1.5);ln.line.end_arrowhead=True
    return s

def diagram(title,nodes,section='ARCHITECTURE',source='Evidence: docs/ARCHITECTURE.md'):
    s=base(title,section,source); coords=[(.6,2.2),(3.05,2.2),(5.5,2.2),(7.95,2.2),(10.4,2.2), (3.05,4.35),(5.5,4.35),(7.95,4.35)]
    for i,(head,body) in enumerate(nodes):
        x,y=coords[i]; shape(s,x,y,2.15,1.32,PANEL2 if i<5 else PANEL)
        text(s,x+.12,y+.12,1.9,.35,head,12,CYAN,True,align=PP_ALIGN.CENTER);text(s,x+.12,y+.55,1.9,.62,body,9.5,WHITE,align=PP_ALIGN.CENTER)
    # Main horizontal arrows
    for i in range(4):
        x=2.75+i*2.45; ln=s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x), Inches(2.86), Inches(x+.27), Inches(2.86)); ln.line.color.rgb=CYAN;ln.line.end_arrowhead=True
    text(s,.7,6.1,11.8,.45,'Boundary: browser → HTTP/proxy → single Flask process → PostgreSQL + filesystem. Background work is threaded in-process.',12,MUTED)
    return s

# Cover
s=prs.slides.add_slide(BLANK);s.background.fill.solid();s.background.fill.fore_color.rgb=BG
for i in range(8):
    sh=shape(s,8.5+i*.42,1.2+i*.32,3.8-i*.26,4.8-i*.31, PANEL if i%2 else PANEL2,True)
    sh.fill.transparency=30+i*5
shape(s,.75,.9,.1,5.5,CYAN,False);text(s,1.15,1.05,8,.35,'SYLTHARAE-PRO  /  TECHNICAL SYSTEM BRIEFING',12,CYAN,True)
text(s,1.12,1.8,8.2,1.1,'Information intelligence\nacross mixed file collections',30,WHITE,True)
text(s,1.15,3.35,6.9,.85,'A code- and documentation-grounded examination of architecture, workflows, operations, security, and adoption considerations.',17,MUTED)
shape(s,1.15,5.1,3.5,.52,PANEL2);text(s,1.32,5.22,3.15,.22,'Repository snapshot • 2.2.0 • 2026-10-16',11,CYAN,True)
text(s,1.15,6.7,7,.25,'Research basis: checked-out repository at aa2b865a24c429e242a457a60d2aef84661389b2',9,MUTED)
slide_no+=1

cards('Executive summary',[
('What it is','A Python / Flask and PostgreSQL information-intelligence application for ingesting, extracting, indexing, analyzing, preserving, and connecting mixed file collections.'),
('What stands out','Broad ingestion and forensic readers sit alongside search, detection, geo enrichment, analyst categorization, monitoring, reporting, jobs, and a browser UI.'),
('Operating model','One Flask application and one process per database. Slow tasks run as bounded worker threads and persist job state/events in PostgreSQL.'),
('Adoption lens','Evaluate the supported formats and workflow fit, PostgreSQL operations, file retention/backup, security configuration, and AGPL obligations before production use.')],source='Evidence: README.md; docs/ARCHITECTURE.md; pyproject.toml; run_web.py')
cards('Agenda & evidence policy',[
('01 / Orient','Purpose, users, technical profile, value boundaries.'),('02 / Understand','Architecture, repository, request path, ingestion, search, detection, reporting.'),('03 / Operate','Prerequisites, configuration, installation paths, example flows, troubleshooting.'),('04 / Evaluate','Security, quality, deployment, use cases, limitations, evidence index.'),('Evidence labels','Implemented = source/test-backed. Documented = stated in repository documentation. Code-derived walkthroughs are not claimed as runtime-tested. Proposed = recommendation only.')],section='NAVIGATION')
cards('Project at a glance',[
('Runtime & framework','Python >=3.10 (version metadata declares 3.10–3.12); Flask >=3,<4; server-rendered Jinja UI plus JSON APIs.'),('Persistence','PostgreSQL via psycopg2; migrations are authoritative. Filesystem holds ingested bytes and runtime data.'),('Interfaces','Browser UI; numerous route modules; CLI entry point; import utility. Verify the specific workflows before relying on an individual command.'),('Optional capabilities','PDF, Office, OCR, ebook, audio, media, PST readers and Waitress via extras; system binaries/language packs may also be needed.')],source='Evidence: pyproject.toml; version.py; docs/ARCHITECTURE.md')
cards('Purpose, context & intended users',[
('Problem addressed','Mixed-format collections are difficult to search and connect when content remains trapped in files, archives, scans, mail, media, and databases.'),('Core workflow','Bring files into a managed collection; extract text and metadata; resolve content identity and occurrences; search and analyze; preserve traceable results.'),('Likely operators','Analysts, investigators, knowledge workers, collection administrators, and technical operators—roles inferred from UI domains and data model, not a published persona study.'),('Scope caution','The repository supports a general-purpose information intelligence workflow; it does not document a verified deployment sector, customer, or outcome.')],source='Evidence: README.md; docs/DOMAIN_MODEL.md; docs/ARCHITECTURE.md')
cards('Value proposition: evidence vs inference',[
('Demonstrated characteristics','Content hash/context/path identity; durable job history; migration-led schema; registries for interface, user actions, and screen contracts; versioned detection/reporting records.'),('Operational implication','These choices can improve traceability, resumability, schema discipline, and consistency—provided operators configure and maintain the supporting database/filesystem.'),('Differentiating design','A single application combines collection ingestion/search with downstream signal, rules/scenarios, geo, and reporting modules; this is a repository observation, not a comparative market claim.'),('Not established','No verified benchmarks, customer adoption, quantified productivity, scale guarantees, compliance certification, or production-readiness attestation found in the inspected evidence.')],section='EXECUTIVE')
diagram('High-level system architecture',[('Browser','UI / API client'),('Nginx / proxy','TLS, limits'),('WSGI / Flask','single process'),('Routes + services','HTTP → domain'),('PostgreSQL','state / search'),('Pipeline','read · extract · store'),('Readers','format handlers'),('Jobs','thread pool + events')])
cards('Repository anatomy',[
('Application surface','apps/web (app, serving, deployment); Api/routes (HTTP endpoints); templates + static (UI); apps/cli and run_cli.py.'),('Domain & processing','services/ (ingestion, jobs, detection, reporting, geo, retention, monitoring); pipeline/ (reader and progress workflow); reader_file/ (format readers).'),('Foundation','core/ (security, search, registries, analytics, OCR, safety, reporting); database/ (bootstrap, migrations, query/data services); concurrency/.'),('Operations & proof','settings/, .env.example, install.py, scripts/, deploy/, offline-bundle/, docs/, tests/ (unit, integration, security, JS).')],source='Evidence: repository tree; docs/ARCHITECTURE.md')
cards('Component responsibilities & boundaries',[
('Routes','Parse and validate HTTP input, enforce endpoint authorization, call services, shape status/JSON/page responses.'),('Services / core','Orchestrate domain work and own invariants; route-independent domain logic. Services coordinate ingestion, jobs, detection, reports, geo, and retention.'),('Pipeline / readers','Read → extract → store. Reader registry dispatches format handlers; storage pipeline writes extracted content, identity, provenance and progress.'),('Database / UI','Migrations and repositories own PostgreSQL state. Jinja/JavaScript presents pages and calls APIs; interface/action/experience registries describe portions of the UI contract.')],source='Evidence: docs/ARCHITECTURE.md; core/interfaces; core/experience')
flow('Request lifecycle: browser to response', [('Request','Browser sends HTTP(S) request through proxy.'),('Gates','Setup state, session/authentication, authorization, rate limiting and lockout checks.'),('Route','Route validates fields and invokes a service; query values are parameterized.'),('Service','Domain logic reads/writes PostgreSQL or creates a long-running job.'),('Response','Rendered page or JSON; failures mapped to status and message, not traceback body.')],section='WORKFLOWS',source='Code-derived: docs/ARCHITECTURE.md; apps/web/app.py; Api/routes/*')
flow('Ingestion: input to durable searchable content',[('Select input','Upload or server path (server paths require configured ingestion roots).'),('Queue work','Ingestion route creates a persisted job; job manager records lifecycle and events.'),('Read & extract','Integrated reader dispatches formats; text, metadata, OCR or archive extraction as available.'),('Identity + store','Hash bytes; combine hash with source/side context; attach path occurrences; deduplicate with database unique constraints.'),('Follow up','Progress ledger supports resume; detection hooks may run; job events are persisted and shown to UI.')],section='WORKFLOWS',source='Code-derived: docs/ARCHITECTURE.md; services/ingesting; pipeline; database/services/dedup_service.py')
flow('Search: query to paginated result',[('Query','User submits text and any filters.'),('Resolve words','Tokenizer and word resolution use indexed word keys.'),('Candidate set','Inverted word-to-content association table identifies candidates.'),('Filter & rank','Apply source/side/date/category constraints; retrieve content and create snippets/ranking.'),('Return page','Cursor pagination; response reports exact/timed/skip count mode so total precision is explicit.')],section='WORKFLOWS',source='Code-derived: docs/ARCHITECTURE.md; core/search; docs/search/PERFORMANCE_ARCHITECTURE.md')
flow('Signals, rules, scenarios & reports',[('Detect','Versioned detector run writes content signals and provenance.'),('Enrich','Place/geo mentions can resolve through gazetteer and map-related services.'),('Evaluate','Monitoring rule engine and scenario engine persist evaluations/outcomes.'),('Notify','Alerts/notifications follow qualifying evaluations.'),('Report','Definition registry creates report run, datasets, analyses and immutable artifacts with checksums.')],section='WORKFLOWS',source='Code-derived: docs/ARCHITECTURE.md; services/detection; services/monitoring; services/reporting')
cards('Data model & information handling',[
('Identity layers','hashes = bytes; hash_contexts = bytes in source + side; paths = occurrences. Same content at multiple locations is represented once with multiple occurrences.'),('Content representation','contents stores search representation; contents_raw stores verbatim extracted text. Original file bytes are retained in the application storage model.'),('Provenance & lifecycle','Detection runs and signals retain detector provenance; job_events preserve activity; reporting and selected analyst history are append-only/trigger protected.'),('Data movement caution','Database backups must include contents_raw; file bytes/runtime data also need filesystem backup. A database-only backup is incomplete for restoring originals.')],source='Evidence: docs/ARCHITECTURE.md; docs/DATABASE.md; database/migrations/')
cards('Jobs, concurrency & recoverability',[
('Lifecycle','QUEUED → RUNNING, with PAUSED/CANCELLING and terminal COMPLETED, COMPLETED_WITH_WARNINGS, CANCELLED, FAILED states.'),('Execution','Bounded worker threads inside the single application process; max concurrent jobs configured through settings.'),('Events & progress','JobRepository persists event stream; throttling samples high-frequency events. Browser pages can reload and recover history from PostgreSQL.'),('Failure semantics','Cancellation is cooperative. A job stuck CANCELLING remains visibly unresolved; process loss and multi-process deployment require careful evaluation because workers are in-process.')],source='Evidence: docs/ARCHITECTURE.md; services/jobs/manager.py; job_state.py')
cards('Search and analytical capabilities',[
('Full-text discovery','PostgreSQL-backed word index/inverted associations, filters, snippets, ranking and cursor pagination; query cost/total-count mode is surfaced.'),('Analytics & narrative','core/analytics provides kinds, measures, profiles, thresholds and narrative modules; route/service inventory includes analytics and exports.'),('Information linking','Detection signals, identifiers, temporal and place intelligence, relationships, analyst categories and lineage enable cross-record exploration.'),('Limits to claims','No benchmark performance numbers asserted here. Dataset size, query mix, hardware, Postgres configuration, OCR load and count mode affect real outcomes.')],source='Evidence: docs/ARCHITECTURE.md; core/search; core/analytics; docs/perf/')
cards('Reader ecosystem & extraction caveats',[
('Format breadth','Repository includes handlers for office, PDF, email/mailboxes, archives, images/OCR, ebook, audio/video, databases, diagrams and structured data; exact support depends on handler and installed extras.'),('OCR selection','Tesseract preferred, then RapidOCR. Missing engines/language packs produce retryable unavailable-engine status rather than silent text success.'),('Archives & safety','Archive safety and nested extraction controls exist; encrypted or unsupported formats may require external tools/credentials and can fail.'),('Operational consequence','Inventory actual formats and language needs; run OCR self-check; assess hostile-file exposure, resource costs, retention, and extraction fidelity before broad ingestion.')],source='Evidence: pyproject.toml; docs/CONFIGURATION.md; core/ocr; core/archive_safety.py')
cards('Configuration model & key settings',[
('Precedence','OS environment → .env → data/settings.json → config.example.json → dataclass defaults. Restart after editing .env.'),('Core runtime','FLASK_ENV, FLASK_DEBUG, FLASK_HOST/PORT, WSGI_SERVER, WAITRESS_THREADS, FLASK_SECRET_KEY, SOURCE_CODE_URL.'),('Database & admin','DB_HOST/PORT/NAME/USER/PASSWORD; APP_ADMIN_USERNAME/PASSWORD for first admin; password minimum 12.'),('Data & trust','INGESTION_ROOTS confines server-path reads; TRUSTED_PROXY_COUNT must match proxy topology; APP_DATA_DIR defines runtime storage.')],source='Evidence: docs/CONFIGURATION.md; .env.example; apps/web/deployment.py')
flow('Install & first-start sequence (documented path)',[('Obtain source','Clone/download the repository and select a reviewed version. This briefing examines the checked-out snapshot; no release tag was found.'),('Prepare Python','Create isolated Python 3.10–3.12 environment; install base and required optional extras for intended formats.'),('Prepare PostgreSQL','Provision DB and credentials; set environment / .env. Ensure application can create or upgrade schema per deployment policy.'),('Configure safely','Set strong persistent secret, production mode, proxy count, ingestion roots, storage paths and backup policy.'),('Launch & initialize','Run python run_web.py; setup gate routes uninitialized instance to wizard. Verify service/DB, admin login and readiness before adding data.')],section='OPERATIONS',source='Documented/code-derived; exact production steps in docs/OPERATIONS.md; no live server test performed')
cards('Practical commands & optional extras',[
('Install base','python -m pip install -e .  (from repository root, in a virtual environment). Package name is file-analysis; Python >=3.10.'),('Install feature sets','pip install -e ".[pdf,office,ocr,ebook,audio,media,server]" — choose only needed extras; PST extra has native build prerequisites.'),('Run web','python run_web.py  |  optional console script: file-analysis-web. Defaults Flask dev server; Waitress opt-in via WSGI_SERVER=waitress.'),('Readiness & tests','python verify_readiness.py; python -m pytest tests/unit -q (integration/security suites and requirements described in docs/TESTING.md). Commands are repository-documented; startup and ingestion were exercised, but the listed test suites were not run.')],section='OPERATIONS',source='Evidence: pyproject.toml; docs/OPERATIONS.md; docs/TESTING.md; run_web.py')
cards('Deployment topology & operational constraints',[
('Supported shape','systemd service + nginx reverse proxy in deploy/. Nginx TLS/static/request limits; app receives trusted proxy headers only when configured.'),('Single-process rule','Exactly one application process per database. Multi-worker Gunicorn/uWSGI is not supported because jobs run as in-process threads.'),('Data & backups','Back up PostgreSQL plus filesystem data (especially originals/runtime files). Restore-test database and file consistency.'),('Upgrade discipline','database.bootstrap/migration runner owns schema changes; migrations each transact and record schema_migrations. Historical database/SYLTHARAE.sql is not deploy authority.')],section='OPERATIONS',source='Evidence: docs/ARCHITECTURE.md; docs/OPERATIONS.md; docs/DATABASE.md; deploy/')
cards('Troubleshooting decision tree',[
('Cannot start','Check apps/web/deployment.py startup findings; invalid production debug setting is refused. Validate DB connection, env precedence, secret and WSGI option.'),('First-run redirects','Expected setup gate before schema/accounts are initialized. Complete wizard; do not bypass partially initialized state.'),('Server-path ingest fails','INGESTION_ROOTS empty disables server path ingestion; use upload or add approved absolute root. Check path safety/permissions.'),('Scan has no text','Run python tools/ci/ocr_selfcheck.py; verify Tesseract binary and eng/heb/ara packs, or RapidOCR availability. Retry after installing dependency.'),('Jobs stall / overloaded','Inspect persisted job status/events; ensure single process; review thread capacity, database availability and resource limits; cancellation is cooperative.')],section='OPERATIONS',source='Evidence: docs/CONFIGURATION.md; docs/ARCHITECTURE.md; docs/OPERATIONS.md')
cards('Security controls evidenced in repository',[
('Identity & access','Authentication/session management, authorization decorators, lockouts and rate limits; endpoint and interface security tests exist.'),('Web protections','CSRF via Flask-WTF; security headers/CSP; deployment validation; proxy trust explicitly configured; session/secure cookie settings vary by environment.'),('Data access','Parameterized SQL is policy; path safety confines server reads; archive safety and upload constraints mitigate unsafe inputs.'),('Audit & integrity','Audit logging; immutable/trigger-protected selected history; checksums for report artifacts. Controls require configuration and do not establish overall security certification.')],section='SECURITY',source='Evidence: docs/SECURITY.md; core/security; tests/security; core/path_safety.py')
cards('Security boundaries & risk considerations',[
('Trust boundaries','Browser/proxy ↔ Flask; app ↔ PostgreSQL; app ↔ filesystem; uploaded documents and optional decoders are untrusted inputs.'),('Secrets','Never commit .env; provision stable FLASK_SECRET_KEY and DB credentials using an enterprise secret store. Auto-generation is fallback, not secret lifecycle management.'),('Proxy hazards','Set TRUSTED_PROXY_COUNT only for the actual trusted chain; a wrong value can corrupt client IP attribution and rate-limit/lockout behavior.'),('Residual validation','Repository controls/tests are evidence, not an independent penetration test. Review dependency advisories, deployment hardening, threat model, privacy, retention and access boundaries.')],section='SECURITY',source='Evidence: docs/SECURITY.md; docs/CONFIGURATION.md; .env.example')
cards('Quality, testing & observability',[
('Automated checks','pytest unit, integration (disposable PostgreSQL), security, JS smoke tests; Ruff and Bandit are declared dev tools.'),('Observable work','Job status/event history, application logs, performance monitoring, health/readiness routes, error monitoring/recovery modules.'),('Evidence distinction','Presence of test files and CI/config does not prove all tests passed in this snapshot. Test execution was not performed for this briefing.'),('Operational gap','No quantified capacity guarantees or SLOs established. Run representative load, restore, upgrade, failure and security exercises on target infrastructure.')],section='QUALITY',source='Evidence: docs/TESTING.md; pyproject.toml; docs/OPERATIONS.md')
cards('Distinctive architectural choices & trade-offs',[
('Registries/contracts','Interface, action, and experience contract registries support generated documentation and tests to detect drift.'),('Database-led integrity','Migrations are authoritative; deduplication uses unique constraints; foreign-key/deletion semantics and append-only records encode invariants.'),('Resumable work','Progress ledger and persistent job events favor visibility/recovery over request-bound processing.'),('Trade-offs','Single process simplifies shared in-memory coordination but caps horizontal scaling of workers; PostgreSQL and filesystem form a coupled recovery boundary.')],source='Evidence: docs/ARCHITECTURE.md; tests/unit; database/migrations')
cards('Enterprise evaluation scenarios (hypothetical)',[
('Casework collection','Ingest a controlled evidence set → preserve source/side/path identity → search and detect signals. Validate provenance, chain-of-custody needs, access segregation and retention.'),('Records discovery','Index mixed office/email/archive material and use search/filter/export. Validate supported formats, language/OCR quality, legal hold and data minimization.'),('Situation monitoring','Evaluate signals against rules/scenarios; surface notifications and build reports. Validate false-positive handling, detector versions, analyst review and alert delivery.'),('Knowledge repository','Use source/side organization, category, lineage and reports to connect distributed files. Validate data ownership, usability, backup/recovery and concurrency limits.')],section='APPLICATIONS',source='Hypothetical scenarios derived from documented capabilities; no customer deployments asserted')
cards('Use-case validation plan',[
('1 / Fixture set','Build representative, legally approved files across formats, languages, corruptions, duplicates and archive nesting; define expected extraction and provenance.'),('2 / Functional outcomes','Verify ingestion → search → categorization/detection → rule/report → export, including permission checks and failure cases.'),('3 / Operations','Benchmark realistic query/ingestion mix; test job cancellation/restart, database outage, disk pressure, backup and restore.'),('4 / Governance','Threat-model uploads/decoders; verify retention, audit access, secrets, AGPL obligations, privacy, data residency and operator controls.')],section='APPLICATIONS')
cards('Limitations, risks & open questions',[
('Scale & resilience','Single-process threaded jobs limit horizontal worker scaling; no HA architecture or capacity guarantee established. What workload envelope is acceptable?'),('Format fidelity','Reader support is dependency- and version-sensitive; OCR quality, language packs, malformed/encrypted media behavior need corpus validation.'),('Deployment verification','No startup, install, functional smoke, or suite run performed for this deliverable. Verify supported OS, PostgreSQL versions, service upgrades and rollback on target.'),('Governance','Clarify access model/tenant isolation, retention/legal hold, privacy classification, source custody, AGPL compliance and incident response for intended deployment.')],section='ASSESSMENT',source='Evidence: docs/OPERATIONS.md; docs/CONFIGURATION.md; pyproject.toml; assessment items are open questions')
cards('Prioritized improvement roadmap (recommendations)',[
('P0 / Adoption gate','Run reproducible install→ingest→search→backup/restore acceptance on target OS/DB; document supported versions and workload thresholds.'),('P1 / Resilience','If scale demands it, externalize job execution and define durable queue semantics; add multi-process-safe coordination and idempotent retry tests.'),('P1 / Security','Independent threat model and penetration test; dependency/SBOM policy; secret manager integration; retention and authorization review.'),('P2 / Operability','Add baseline dashboards/SLOs, capacity and OCR corpus benchmarks, tested upgrade/rollback playbooks, and verified admin/operator runbooks.')],section='ASSESSMENT',source='Recommendations, not current capabilities')
cards('Conclusion: what to carry forward',[
('Verified strengths','Broad file-centric pipeline; persisted jobs and provenance; PostgreSQL migrations and identity model; search, detection, geo, reporting; explicit security/registry/testing machinery.'),('Material constraints','Single-process worker model, coupled DB/filesystem recovery, optional native/OCR dependencies, and workload-specific behavior require validation.'),('Decision posture','Promising candidate for a controlled technical evaluation; this evidence alone is insufficient to declare production-ready, secure, scalable, or compliant.'),('Next actions','Select workload and formats → provision isolated test environment → execute validation plan → review security/licensing → approve operational architecture.')],section='CLOSE')

# Authentic screenshots captured from the running local Flask application.
def mock_screen(title, mode='search', notes=(), source='Live application capture • local demo instance • 2026-10-16'):
    s=base(title,'PRODUCT TOUR',source)
    shape(s,.55,1.55,3.55,5.15,PANEL)
    text(s,.82,1.78,3.0,.35,'LIVE PRODUCT CAPTURE',10,CYAN,True)
    text(s,.82,2.15,3.0,.35,'AUTHENTIC • NOT A MOCKUP',9,GREEN,True)
    yy=2.66
    for i,note in enumerate(notes):
        shape(s,.82,yy,.31,.31,CYAN);text(s,.82,yy+.02,.31,.22,str(i+1),9,BG,True,align=PP_ALIGN.CENTER)
        text(s,1.26,yy-.03,2.57,.65,note,10.5,WHITE)
        yy+=.87
    image_map={'search':'screenshots/20_search_results.png','jobs':'screenshots/19_jobs_completed.png','settings':'screenshots/11_settings.png'}
    image_path=OUT/image_map[mode]
    frame_x,frame_y,frame_w=4.38,1.55,8.35
    shape(s,frame_x-.06,frame_y-.06,frame_w+.12,5.50,RGBColor(35,49,63),True)
    s.shapes.add_picture(str(image_path), Inches(frame_x), Inches(frame_y), width=Inches(frame_w))
    return s

def screenshot_showcase(title, image_name, notes, source='Authentic live UI screenshot • local dev instance • 2026-10-16'):
    s=base(title,'PRODUCT TOUR',source)
    shape(s,.55,1.55,3.55,5.15,PANEL)
    text(s,.82,1.78,3.0,.35,'LIVE PRODUCT CAPTURE',10,CYAN,True)
    text(s,.82,2.15,3.0,.35,'REAL UI • SAMPLE INSTANCE',9,GREEN,True)
    yy=2.66
    for i,note in enumerate(notes):
        shape(s,.82,yy,.31,.31,CYAN);text(s,.82,yy+.02,.31,.22,str(i+1),9,BG,True,align=PP_ALIGN.CENTER)
        text(s,1.26,yy-.03,2.57,.65,note,10.5,WHITE)
        yy+=.87
    frame_x,frame_y,frame_w=4.38,1.55,8.35
    shape(s,frame_x-.06,frame_y-.06,frame_w+.12,5.50,RGBColor(35,49,63),True)
    s.shapes.add_picture(str(OUT/'screenshots'/image_name), Inches(frame_x), Inches(frame_y), width=Inches(frame_w))
    return s

def responsive_showcase():
    s=base('Responsive interface: real desktop and mobile captures','PRODUCT TOUR','Authentic screenshots from /files at desktop 1600×1050 and mobile 390×844 CSS viewport; captured 2026-10-16')
    shape(s,.55,1.62,6.95,4.75,RGBColor(35,49,63),True)
    s.shapes.add_picture(str(OUT/'screenshots/03_file_library.png'), Inches(.62), Inches(1.69), width=Inches(6.81))
    shape(s,7.72,1.62,2.20,4.75,RGBColor(35,49,63),True)
    s.shapes.add_picture(str(OUT/'screenshots/41_mobile_file_library.png'), Inches(7.79), Inches(1.69), width=Inches(2.06))
    shape(s,10.18,1.62,2.58,4.75,PANEL)
    text(s,10.4,1.86,2.15,.35,'OBSERVED',11,CYAN,True)
    text(s,10.4,2.35,2.15,3.7,'The same File Library route served at two actual viewport sizes. The narrow layout exposes the menu control and responsive page shell.\\n\\nScreenshots are real rendered app pages; content reflects the demonstration database. This is a visual spot-check, not exhaustive breakpoint/accessibility testing.',10,WHITE)
    text(s,.65,6.52,12,.28,'Source: templates/base.html • templates/file/files_list.html • static/css/responsive-fixes.css • static/js/responsive-fixes.js',9,MUTED)
    return s

screenshot_showcase('A live look at the SYLTHARAE application','02_dashboard_navigation_open.png',[
'Operations widget surfaces new ingestion, imports, jobs and search entry points; here it reports the completed demo intake.',
'The dashboard provides overview/comprehensive/charts navigation; actual demo data comes from a synthetic two-file batch.',
'The shared shell includes breadcrumbs, account context, notifications and a registry-backed navigation menu.',
'Capture from the running application at 1600 × 1050 CSS viewport. Database and app were local sandbox services; user is a temporary demo admin.'
],source='Authentic capture from running Flask app; source: templates/base.html; templates/Analysis/dashboard.html; docs/INTERFACE_REGISTRY.md')
mock_screen('Search Files','search',['The screenshot shows two real demo records for query CASE-DEMO-042.','Visible controls include standard, advanced and enhanced search views, query, scope, export actions and result table.','Each row exposes filename, type, source, side, date and record actions; result count is 2 in this test collection.','Observed live result. The sample data is synthetic; implementation: Api/routes/search.py; core/search; Api/services/search_service.py.'],source='Authentic live search capture; synthetic demo corpus; templates/Search/search.html')
mock_screen('Operations • Jobs','jobs',['This is the actual Jobs interface from the running demo instance.','The completed job is available in detail, with a separate list view in the gallery.','The job table and lifecycle actions are provided by the application—not a mock screen.','See the dedicated ingestion and completion sequence for test-data context.'],source='Authentic local application screen capture; templates/Operations/jobs.html; synthetic demo data')
mock_screen('Settings','settings',['The screenshot shows the actual Settings page and its tabs: General, Display, Themes, Search, Processing, Navigation, Geographic Locations, Notifications, Database, Storage, System.','The General tab displays branding settings; tab-specific values vary by active panel.','Environment variables outrank .env and persisted settings; processing controls mostly reside in settings store.','The screen is an authentic capture from the local instance; see docs/CONFIGURATION.md for precedence and supported names.'],source='Authentic live capture from Settings • templates/Settings/settings.html')
screenshot_showcase('Ingestion studio: preflight before write','18_ingestion_preflight.png',[
'The four-step intake flow binds files, source/side, processing options and launch review.',
'Synthetic demo batch: 2 uploaded text files; selected source “Arena Synthetic Case File” and side “Synthetic Training Set”.',
'Actual preflight returned 2 discovered, 2 to read, 0 unreadable, 770 B. Dry-run does not write ingested content.',
'Backend: Api/routes/operations_api.py, services/ingesting, pipeline reader, progress ledger. A later confirmed run completed successfully.'
],source='Authentic preflight capture from local running app; synthetic data only; 2026-10-16')
screenshot_showcase('Search result: content enters discovery','20_search_results.png',[
'Query CASE-DEMO-042 returned two actual demo records in the result table.',
'Table exposes file name, type, size, source, side, date and record actions; export controls are visible above.',
'The ingested examples are synthetic training files, not customer or operational records.',
'Capture shows app response after actual ingestion; search route and repository implementation: Api/routes/search.py; core/search; Api/services/search_service.py.'
],source='Authentic live result screenshot, synthetic sample corpus, 2026-10-16')
screenshot_showcase('Completed ingestion job: persisted outcome','18_job_detail_live.png',[
'Actual job reached COMPLETED at 100%; work ledger reports 4 of 4 units complete.',
'Job detail shows source, side, intake path, user, start/completion timestamps and current file.',
'Observed processing used two synthetic text files and one local PostgreSQL development cluster.',
'Job ID in the screenshot is local demo data. State/event implementation: services/jobs/job_state.py; services/jobs/manager.py; services/jobs/repository.py.'
],source='Authentic live capture from completed local ingestion job; synthetic dataset')
screenshot_showcase('Operations, monitoring & analytics interfaces','09_monitoring.png',[
'This capture is the running Monitoring interface; dashboards for reports, signals, maps and analytics are separately implemented screens.',
'Empty/low-data state is a real UI state; the demo intake has no configured monitoring rules or geographic detections.',
'Feature interfaces: Signals, Detection, Map Viewer, Reports, Notifications and Schedules; screenshots are included in the companion gallery.',
'Interface source references: templates/Monitoring/monitoring.html; templates/Signals/*; templates/map/map_viewer.html; templates/Reports/*.'
],source='Authentic live Monitoring page capture; empty-state context is real for demo database')
cards('Front-end implementation map',[
('Shell & navigation','templates/base.html; templates/components/sidebar_nav.html; core/interfaces/*; static/css/styles.css; static/js/modules/core/navigation-swap.js'),('Search & collection','templates/Search/*; templates/file/*; templates/components/search_input.html, file_library_table.html, filter_bar.html, unified_pagination.html'),('Operational screens','templates/Operations/*; templates/Monitoring/*; templates/Signals/*; templates/Notifications/*; static/js/pages/jobs-page.js'),('Configuration & reports','templates/Settings/*; templates/Reports/*; templates/map/map_viewer.html; templates/auth/*; static/css/Settings.css; static/css/reports.css')],section='PRODUCT TOUR',source='Repository paths inspected; see deliverables/interface_inventory.md')
responsive_showcase()

# Appendix
cards('Appendix A — Feature inventory (1/2)',[
('Collection & extraction','Upload/server-path ingestion; readers and OCR; archive traversal; metadata/text extraction; progress/resume; duplicates. Files: services/ingesting, pipeline/, reader_file/, core/ocr.'),('Search & navigation','Tokenization, word/inverted index, filtering, snippets, cursor pagination, saved searches/history, file navigation/preview. Files: core/search, Api/services/search*, Api/routes/search.py.'),('Content identity & lineage','Hash/context/path model; deduplication; provenance, path revisions, lineage and original-file access. Files: database/services/dedup_service.py; database/migrations; Api/services/lineage_service.py.'),('Analyst enrichment','Categories, identifiers, temporal/place signals, gazetteer/geo, maps, translations. Files: services/detection, services/geo, core/detection, services/map* and Api/routes.')],section='APPENDIX',source='Feature matrix companion: deliverables/feature_matrix.csv')
cards('Appendix A — Feature inventory (2/2)',[
('Jobs & operations','Persistent jobs/events, pause/cancel, schedules, retention, health/performance/error monitoring, notifications. Files: services/jobs, services/retention, core/monitoring, Api/routes.'),('Monitoring & reporting','Rules/scenarios, evaluations/outcomes, report registry/runs/datasets/analyses/artifacts, exports. Files: services/monitoring, services/reporting, core/reporting.'),('Access & experience','Authentication, authorization, audit, CSRF, rate limits, UI registries/contracts, localization, preferences. Files: core/security, core/experience, core/interfaces, translations/.'),('Import/export & tooling','Import/export services, CLI/import utilities, setup/installer/readiness, deployment templates, offline bundle. Files: apps/, install.py, verify_readiness.py, deploy/, offline-bundle/.')],section='APPENDIX',source='Companion feature matrix maps capability groups to evidence and slide coverage')
cards('Appendix B — Configuration quick reference',[
('Required to connect','DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD (or equivalent service-injected values). Confirm account/db provisioning and PostgreSQL access.'),('Production web','FLASK_ENV=production; FLASK_DEBUG=false; stable FLASK_SECRET_KEY; WSGI_SERVER=waitress (install server extra); proxy count and bind host intentionally set.'),('Ingestion / OCR','INGESTION_ROOTS for server paths; TESSERACT_CMD/TESSDATA_PREFIX as needed; install OCR extras and language packs; run OCR self-check.'),('Runtime data','APP_DATA_DIR for logs/uploads/cache/checkpoints/settings; plan filesystem capacity, permissions and backup alongside PostgreSQL.')],section='APPENDIX',source='Evidence: docs/CONFIGURATION.md; .env.example')
cards('Appendix C — Dependencies & external prerequisites',[
('Core Python','Flask, Flask-Babel, Flask-Limiter, Flask-WTF, Flask-Compress, psycopg2-binary, dotenv, psutil, PyYAML and reader-related packages.'),('Optional Python sets','pdf: PyMuPDF/pypdf; office: python-docx/openpyxl/python-pptx/xlrd/odfpy; OCR: pytesseract/Pillow/RapidOCR/OpenCV headless; ebook/audio/media/PST/server extras.'),('Infrastructure','PostgreSQL; filesystem storage; optional nginx/systemd; optional Waitress. Tesseract binary and traineddata; PST requires native C dependencies on many hosts.'),('Supply chain','Version constraints are ranges rather than a single lockfile in pyproject; inspect requirements.txt/THIRD_PARTY_NOTICES.md and establish organization-specific pinning/scanning.')],section='APPENDIX',source='Evidence: pyproject.toml; requirements.txt; THIRD_PARTY_NOTICES.md; deploy/')
cards('Appendix D — Quality evidence & research limits',[
('Repository snapshot','Checked out commit aa2b865a24c429e242a457a60d2aef84661389b2; project version 2.2.0; version.py build date 2026-09-26.'),('History/release limits','Local history available only as a single shallow/base commit; no tags listed in checkout. Release history not independently reviewed.'),('Executed in sandbox','Installed Python extras; started bundled PostgreSQL; applied all 39 migrations; completed setup; captured live UI; ingested two synthetic files, ran preflight, confirmed job completion, and searched results.'),('Not executed','The repository test suites, OCR self-check, full-format matrix, load test, backup/restore and independent security review were not performed. No production readiness is implied.'),('External audit limits','No customer validation, compliance evidence, independent security review, benchmark, or production support SLA established.')],section='APPENDIX',source='Repository metadata and inspection performed 2026-10-16')
cards('Appendix E — Glossary & sources',[
('Hash / context / path','Content digest / digest in source+side / concrete location occurrence.'),('Signal / rule / scenario','Detector-produced observation / condition evaluated over signals / modeled multi-condition situation.'),('Job / event','Durable work lifecycle state / progress or activity record emitted during that lifecycle.'),('Primary source set','README.md; docs/ARCHITECTURE.md, CONFIGURATION.md, DATABASE.md, OPERATIONS.md, SECURITY.md, TESTING.md; pyproject.toml; .env.example; run_web.py; database/migrations; services/jobs; pipeline/.')],section='APPENDIX',source='Stable source URLs: https://github.com/RussellTCrivello/SYLTHARAE-Pro/tree/aa2b865a24c429e242a457a60d2aef84661389b2')

# Registry-indexed screen appendix: six index pages plus a full-size capture page per interface.
import re
registry_text=Path('docs/INTERFACE_REGISTRY.md').read_text()
reg_rows=re.findall(r'^\| `([^`]+)` \| (.*?) \| (.*?) \| `([^`]+)` \|',registry_text,re.M)
# Template source map, where one is not a direct route-template name.
template_map={
'dashboard':'templates/Analysis/dashboard.html','file_library':'templates/file/files_list.html','search':'templates/Search/search.html','sources':'templates/Sources/sources_list.html','sides':'templates/Side/sides_list.html','keywords':'templates/Keyword/keywords_list.html','words':'templates/Word/Word_list.html','categories':'templates/Category/categories_list.html','email_words':'templates/email_words/email_words.html','notifications':'templates/Notifications/notifications.html','signal_horizon':'templates/Signals/signals.html','monitoring':'templates/Monitoring/monitoring.html','input_ingestion':'templates/Operations/input.html','import_center':'templates/Operations/import_center.html','archives':'templates/file/File_Management_Analysis_System.html','path_analysis':'templates/Analysis/path_analysis.html','map_viewer':'templates/map/map_viewer.html','batch_analysis':'templates/Analysis/analysis_batch.html','analyst_categorization':'templates/Analyst/analyst_categorization.html','classification':'templates/Analysis/file_classification.html','comprehensive_dashboard':'templates/Analysis/comprehensive_dashboard.html','reports':'templates/Reports/reports.html','reports_dashboard':'templates/Reports/dashboard.html','schedules':'templates/Scheduling/schedules.html','charts_dashboard':'templates/Analysis/charts_dashboard.html','detection':'templates/Signals/detection.html','jobs':'templates/Operations/jobs.html','import_export_console':'templates/ImportExport/import_export.html','retention':'templates/Retention/retention.html','users':'templates/auth/users.html','audit_log':'templates/auth/audit.html','settings':'templates/Settings/settings.html','translation_manager':'templates/Settings/translation_manager.html','concurrency_monitor':'templates/concurrency/dashboard.html'}
if reg_rows:
    for page in range(0,len(reg_rows),6):
        subset=reg_rows[page:page+6]
        s=base(f'Appendix F — Interface index ({page+1}–{page+len(subset)} of {len(reg_rows)})','APPENDIX','Registry: docs/INTERFACE_REGISTRY.md; purpose is summarized by domain and template source')
        for idx,(iid,name,domain,route) in enumerate(subset):
            col=idx%2; row=idx//2; x=.55+col*6.2; y=1.52+row*1.75
            shape(s,x,y,5.9,1.53,PANEL)
            text(s,x+.16,y+.12,5.55,.25,f'{name}  /  {domain}',12,CYAN,True)
            text(s,x+.16,y+.46,5.55,.26,f'ID: {iid}   •   endpoint: {route}',9,WHITE)
            text(s,x+.16,y+.78,5.55,.58,f"Source: {template_map.get(iid,'template/source to verify')}\\nRole: {domain.lower()} workflow; see interface registry for role, visibility, dependencies and shortcut.",8.5,MUTED)

# Full-size visual reference: one authentic screen per page (not a thumbnail sheet).
screen_capture_map={
'dashboard':'02_dashboard.png','file_library':'03_file_library.png','search':'20_search_results.png','sources':'17_sources.png','sides':'18_sides.png','keywords':'19_keywords.png','words':'20_words.png','categories':'21_categories.png','email_words':'22_email_words.png','notifications':'23_notifications.png','signal_horizon':'07_signals.png','monitoring':'09_monitoring.png','input_ingestion':'05_input_ingestion.png','import_center':'30_import_center.png','archives':'31_archives.png','path_analysis':'24_path_analysis.png','map_viewer':'12_map_viewer.png','batch_analysis':'25_batch_analysis.png','analyst_categorization':'13_analyst_categories.png','classification':'47_classification.png','comprehensive_dashboard':'26_comprehensive_dashboard.png','reports':'10_reports.png','reports_dashboard':'28_reports_dashboard.png','schedules':'29_schedules.png','charts_dashboard':'27_charts_dashboard.png','detection':'08_detection.png','jobs':'06_jobs.png','import_export_console':'14_import_export.png','retention':'15_retention.png','users':'34_users.png','audit_log':'16_audit_log.png','settings':'11_settings.png','translation_manager':'32_translation_manager.png','concurrency_monitor':'33_concurrency_monitor.png'}
purpose_map={
'dashboard':'Collection overview, analytics navigation and operations summary.','file_library':'Browse indexed records, filter, select and open file actions.','search':'Query content; inspect results; use search modes, pagination and exports.','sources':'Manage source entities associated with ingested records.','sides':'Manage side/collection context paired with sources.','keywords':'Review and maintain keyword vocabulary and relationships.','words':'Explore indexed word vocabulary and associations.','categories':'Manage classification categories and their content relationships.','email_words':'Explore extracted email-like terms.','notifications':'Review alert and notification records.','signal_horizon':'Explore detected signals across time/horizon.','monitoring':'Manage or review monitoring rules and evaluations.','input_ingestion':'Upload or select input; classify source/side; configure processing; run preflight.','import_center':'Import domain or backup data through an operations screen.','archives':'File management and analysis workspace.','path_analysis':'Analyze directory/path relationships.','map_viewer':'Geographic exploration of place-related data.','batch_analysis':'Submit and inspect batch analysis operations.','analyst_categorization':'Assign analyst categories to records.','classification':'Review file classification work.','comprehensive_dashboard':'Switchable analytical dashboard views.','reports':'Browse and run report definitions.','reports_dashboard':'View report-oriented dashboard summaries.','schedules':'Manage scheduled report or operation runs.','charts_dashboard':'Chart-centric analytical view.','detection':'Configure or invoke signal detection workflows.','jobs':'Review durable background jobs and lifecycle status.','import_export_console':'Administrator-facing structured import/export surfaces.','retention':'Review retention policy and schedule operations.','users':'Manage user accounts and roles.','audit_log':'Review audited actions.','settings':'Configure application, theme, processing, database and storage preferences.','translation_manager':'Maintain translated UI catalog surfaces.','concurrency_monitor':'Observe thread/job/resource concurrency state.'}
for iid,name,domain,route in reg_rows:
    shot=screen_capture_map.get(iid)
    if not shot or not (OUT/'screenshots'/shot).exists():
        continue
    source_path=template_map.get(iid,'See docs/INTERFACE_REGISTRY.md')
    screenshot_showcase(f'Interface reference — {name}',shot,[
        f'{domain.title()} interface • canonical route: {route}.',
        purpose_map.get(iid,'User-facing interface for the declared product domain.'),
        f'Implementation reference: {source_path}. Capture shows the actual running application state; data/empty states are from the synthetic demo instance.'
    ],source=f'Authentic live screen capture • interface ID {iid} • {source_path}')

# Additional non-registry screens that are operationally important.
for title,shot,path,purpose in [
 ('Authentication — sign in','00_login_authentic.png','templates/auth/login.html','Sign-in form and localized session entry.'),
 ('First-run setup wizard','01_setup_wizard_authentic.png','templates/Setup/install_wizard.html','Five-step first-run setup: system check, database, admin/security, settings, install.'),
 ('File detail & extracted metadata','45_file_detail.png','templates/file/file_detail.html','Record-level details, provenance, actions and extracted metadata.'),
 ('Full-text reader','46_full_content.png','templates/file/full_content.html','Read extracted full content associated with an indexed file.')]:
    screenshot_showcase(f'Interface reference — {title}',shot,[
        'Operational/secondary interface captured from the live local application.',
        purpose,
        f'Source: {path}. Screenshot is authentic; content is the synthetic demo record or the actual setup/login state.'
    ],source=f'Authentic live screen capture • {path}')

prs.core_properties.title='SYLTHARAE-Pro | Enterprise Technical Briefing'; prs.core_properties.subject='Evidence-based architecture and operational review'; prs.core_properties.author='Arena.ai Agent Mode'
prs.save(OUT/'SYLTHARAE-Pro_Technical_Briefing.pptx')
print(f'Wrote {len(prs.slides)} slides')
