# Assoportail — Complete Project Plan

> Association management portal for a pinball machine installation charity.
> Reference document — updated as the project evolves.

---

## 1. Project Context

An association installs pinball machines in healthcare centers hosting patients.
The portal centralizes operations: events, machines, partner centers, members,
treasury, tasks, meetings, mailing, and document management.

---

## 2. Developer Preferences & Standards

### Language & Tooling
- Code and comments: **English only**
- UI / user-facing strings: **French**
- Linter / formatter: **Ruff** (replaces flake8 + isort + black)
- Type hints: required on all public functions and methods
- Docstrings: Google style, on all public modules, classes, functions
- No god files — one responsibility per module
- No inline JavaScript — all JS in dedicated static files
- No Bootstrap cards — lists only in UI
- HTMX 2.x for dynamic interactions
- Alpine.js 3.x for client-side state
- CSP enforced everywhere via `flask-talisman`

### Architecture
- **Modular monolith**: Flask blueprints, one per domain
- SQLAlchemy 2.0 declarative style (no legacy `Query` API)
- Pydantic 2.x for external data validation (API responses, email parsing, imports)
- WTForms + Flask-WTF for HTML form validation (CSRF included)
- Celery 5.x for async tasks (email polling, mailing, reminders)
- No circular imports — extensions instantiated in `extensions.py`
- Dedicated document junction tables per entity (no generic foreign keys)
- Document junction table factory/mixin for DRY generation

### Validation Boundaries
- **WTForms**: all HTML forms (user-facing, CSRF-protected, error rendering)
- **Pydantic**: external data (HelloAsso API, Gmail API, CSV imports, Celery task payloads)
- Never duplicate validation between the two

### Security (pre-commit blackhat review checklist)
Before every commit, review:
- [ ] No secrets or tokens in code or templates
- [ ] All user inputs validated (WTForms for forms, Pydantic for external data)
- [ ] No raw SQL — SQLAlchemy ORM only (no string interpolation)
- [ ] File uploads: extension whitelist, magic number (MIME) verification, filename sanitized, stored outside webroot
- [ ] File size limits enforced per type (photos: 10 MB, videos: 50 MB, documents: 20 MB)
- [ ] CSRF protection on all state-changing forms
- [ ] Authentication required on all non-public routes
- [ ] Role checks (bureau vs member) on sensitive endpoints
- [ ] No reflected user input in templates (Jinja2 autoescaping enabled)
- [ ] HTTP headers: CSP, HSTS, X-Frame-Options, X-Content-Type-Options
- [ ] OAuth tokens stored encrypted (`MultiFernet` for key rotation support), never logged
- [ ] Rate limiting on auth endpoints (`flask-limiter`)
- [ ] No directory traversal in file serving (use `send_from_directory` safely)
- [ ] Celery tasks do not expose internal exceptions to clients
- [ ] Dependencies pinned and audited (pip-audit in CI)
- [ ] EmailRule regex patterns: validated on save, timeout on execution, max 500 chars
- [ ] CenterFeedback public forms: signed URL token, honeypot field, IP rate limiting

### Testing Philosophy
- **All tests use PostgreSQL** — no SQLite (partial unique indexes, JSON operators, RETURNING)
- **Dev/CI**: PostgreSQL via `docker-compose.dev.yml` or `testcontainers-python`
- **Smoke tests**: one per blueprint — verify the route loads, returns 2xx or expected redirect
- **Unit tests**: concrete real-world scenarios, never abstract stubs
  - Example: "member with expired subscription cannot access restricted page"
  - Example: "uploading a photo renames it to ISO date format"
  - Example: "email matching rule pattern creates a task with correct priority"
  - Example: "regex rule with catastrophic backtracking times out gracefully"
- No "test that function returns something" anti-patterns
- Coverage target: 80% minimum, 100% on security-critical paths
- **Accessibility**: `pytest-playwright` + `axe-core`, one page per blueprint in CI

### CI/CD (GitHub Actions)
Triggered on every push and pull request:

```
.github/workflows/
  ci.yml          # lint + test + security audit
  deploy.yml      # build Docker image + deploy on merge to main
```

**CI pipeline steps:**
1. `ruff check .` — linting
2. `ruff format --check .` — formatting
3. `pip-audit` — dependency vulnerability scan
4. `pytest --cov` — tests with coverage report (PostgreSQL via service container)
5. `bandit -r app/` — static security analysis
6. Docker build smoke test (image builds successfully)
7. `pytest -m accessibility` — axe-core accessibility checks via Playwright

**Deploy pipeline** (on merge to main/master):
1. Build and tag Docker image
2. Push to registry (ghcr.io)
3. SSH deploy to VPS (docker compose pull + up)

---

## 3. Technology Stack

| Layer | Choice | Version |
|---|---|---|
| Language | Python | 3.13 |
| Framework | Flask | 3.1 |
| ORM | SQLAlchemy | 2.0 |
| Migrations | Flask-Migrate (Alembic) | 4.x |
| Auth | Flask-Login | 0.6 |
| Forms | WTForms + Flask-WTF | 1.2 |
| Validation | Pydantic | 2.x |
| Rate limiting | flask-limiter | 3.x |
| Task queue | Celery | 5.4 |
| Broker / cache | Redis | 7.x |
| Database | PostgreSQL | 17 |
| Frontend CSS | Bootstrap | 5.3 (no cards) |
| Frontend dynamics | HTMX | 2.0 |
| Frontend state | Alpine.js | 3.x |
| Security headers | flask-talisman | 1.1 |
| Email (in/out) | Gmail API OAuth2 | google-api-python-client 2.x |
| OAuth encryption | cryptography (MultiFernet) | latest |
| Members API | HelloAsso API | REST, token in .env |
| Linter | Ruff | latest |
| Security scan | Bandit | latest |
| Dep audit | pip-audit | latest |
| Accessibility | pytest-playwright + axe-core | latest |
| Test containers | testcontainers-python | latest |
| WSGI server | Gunicorn | 23.x |
| Containerization | Docker + Compose V2 | latest |

---

## 4. Project Structure

```
assoportail/
├── app/
│   ├── __init__.py                  # application factory
│   ├── extensions.py                # db, login_manager, celery, talisman, limiter
│   ├── config.py                    # Config classes (Dev, Prod, Test)
│   ├── audit.py                     # AuditLog model + SQLAlchemy event listeners
│   ├── documents.py                 # Document model + junction table factory
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── member.py
│   │   ├── machine.py
│   │   ├── center.py
│   │   ├── event.py
│   │   ├── task.py
│   │   ├── meeting.py
│   │   ├── email.py
│   │   ├── mailing.py
│   │   └── treasury.py
│   ├── blueprints/
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   ├── routes.py
│   │   │   ├── forms.py
│   │   │   └── templates/auth/
│   │   ├── members/
│   │   ├── machines/
│   │   ├── centers/
│   │   ├── events/
│   │   ├── documents/
│   │   ├── tasks/
│   │   ├── meetings/
│   │   ├── mailbox/
│   │   ├── mailing/
│   │   ├── treasury/
│   │   └── dashboard/
│   ├── templates/
│   │   ├── base.html
│   │   ├── components/              # reusable Jinja2 macros
│   │   │   ├── list_item.html
│   │   │   ├── modal.html
│   │   │   ├── pagination.html
│   │   │   └── flash.html
│   │   └── errors/
│   │       ├── 403.html
│   │       ├── 404.html
│   │       └── 500.html
│   ├── static/
│   │   ├── js/
│   │   │   ├── htmx.min.js
│   │   │   ├── alpine.min.js
│   │   │   └── app.js               # minimal glue, no inline logic
│   │   └── css/
│   │       └── app.css
│   └── tasks/                       # Celery tasks
│       ├── __init__.py
│       ├── email_polling.py
│       ├── email_rules.py
│       ├── mailing.py
│       └── reminders.py
├── tests/
│   ├── conftest.py                  # fixtures, test app, test db (PostgreSQL)
│   ├── smoke/
│   │   └── test_routes_smoke.py
│   ├── unit/
│   │   ├── test_auth.py
│   │   ├── test_members.py
│   │   ├── test_machines.py
│   │   ├── test_events.py
│   │   ├── test_documents.py
│   │   ├── test_tasks.py
│   │   ├── test_email_rules.py
│   │   └── test_treasury.py
│   └── accessibility/
│       └── test_a11y.py             # pytest-playwright + axe-core
├── migrations/
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── deploy.yml
├── docker/
│   ├── app.dockerfile
│   └── celery.dockerfile
├── docker-compose.yml               # production: all services
├── docker-compose.dev.yml           # dev: PostgreSQL + Redis only
├── .env.example
├── pyproject.toml                   # Ruff, pytest, coverage config
├── requirements.txt                 # pinned production deps
├── requirements-dev.txt             # dev/test deps
└── PLAN.md                          # this file
```

---

## 5. Data Models

### Auth & Members

```
User
  id, email, password_hash, first_name, last_name
  role: enum(member, bureau)
  gender: enum(male, female, other, not_specified)
  phone, address
  is_active, created_at, updated_at

Membership
  id, user_id (FK)
  source: enum(helloasso, cash)
  amount (Decimal)
  started_at, expires_at
  renewed_at (nullable)
  status: computed hybrid_property
    → "pending" if pending HelloAsso validation (stored flag: is_pending)
    → "active" if expires_at > now() and not is_pending
    → "expired" if expires_at <= now()
  helloasso_order_id (nullable)
  is_pending (bool, default False)
  notes
```

### Machines & Centers

```
Machine
  id, model, manufacturer, serial_number, year
  status: enum(stock, installed, maintenance, retired)
  notes, created_at

Center
  id, name, address, city, zip_code
  contact_name, contact_email, contact_phone
  status: enum(prospect, active, inactive, lost)
  notes, created_at

MachineInstallation
  id
  machine_id (FK → Machine)
  center_id (FK → Center)
  installed_at, removed_at (nullable)
  notes
  -- Partial unique index: (machine_id) WHERE removed_at IS NULL

MaintenanceRecord
  id, machine_id (FK)
  date, description, cost (Decimal)
  maintainer_name (free text)
  maintainer_user_id (FK User, nullable)
  source_task_id (FK Task, nullable)    ← link from breakdown report
  documents (via MachineDocument junction)

CenterFeedback
  id, center_id (FK Center)
  submitted_by (str — free text, not necessarily a User)
  submitted_at
  content (text)
  rating (int 1-5, nullable)
  is_published (bool, default False)
  published_by (FK User, nullable)
  published_at (nullable)
  -- Access: signed URL per center (itsdangerous), honeypot field, IP rate limit
```

### Events

```
Event
  id, title, description
  status: enum(planned, in_progress, completed, cancelled)
  event_date, location
  created_by (FK User)
  attendees (M2M → User)
  documents (via EventDocument junction)

Expense
  id, event_id (FK), user_id (FK)
  type: enum(travel, supply, other)
  amount (Decimal), description
  receipt_document_id (FK Document, nullable)
  submitted_at, validated_at, validated_by (FK User, nullable)

CashBox
  id, event_id (FK)
  opened_at, closed_at (nullable)
  opening_amount (Decimal)
  closing_amount (Decimal, nullable)
  -- Computed properties (not stored):
  --   expected_amount = opening_amount + sum(entries.amount)
  --   discrepancy = closing_amount - expected_amount
  reconciled_by (FK User, nullable)
  reconciled_at (nullable)
  reconciliation_note (text, nullable)

CashEntry
  id, cashbox_id (FK)
  type: enum(donation, sale, other)
  amount (Decimal)
  note, recorded_by (FK User)
  recorded_at
```

### Documents

```
Document
  id, original_filename, stored_filename
  type: enum(invoice, photo, video, report, contract, other)
  category (free text tag)
  mime_type, size_bytes
  uploaded_by (FK User), uploaded_at
  description

-- Stored filename convention: YYYY-MM-DD_<type>_<slug>.ext
-- Storage path: /data/uploads/<type>s/<stored_filename>
-- Upload validation: extension whitelist + magic number MIME check
-- Size limits: photos 10 MB, videos 50 MB, documents 20 MB

-- Junction tables (generated via factory/mixin):
EventDocument         (event_id, document_id)
MachineDocument       (machine_id, document_id)
CenterDocument        (center_id, document_id)
MeetingDocument       (meeting_id, document_id)
ExpenseDocument       (expense_id, document_id)
MaintenanceDocument   (maintenance_record_id, document_id)
InboundEmailAttachment(inbound_email_id, document_id)
CenterFeedbackDocument(center_feedback_id, document_id)
```

### Tasks

```
Task
  id, title, description
  status: enum(open, in_progress, done, cancelled)
  priority: enum(low, normal, high, urgent)
  created_by (FK User), assigned_to (FK User, nullable)
  source: enum(manual, email, meeting, center_breakdown)
  source_email_id (FK, nullable)
  source_meeting_id (FK, nullable)
  source_center_id (FK Center, nullable)
  due_date (nullable), completed_at (nullable)
  created_at, updated_at

TaskComment
  id, task_id (FK), author_id (FK User)
  body, created_at
```

### Meetings

```
Meeting
  id, title, date, location
  attendees (M2M → User)
  minutes (text)
  tasks (relation → Task)
  documents (via MeetingDocument junction)
  created_by (FK User)
```

### Email & Automation

```
InboundEmail
  id, gmail_message_id (unique)
  subject, sender, recipients
  body_text, body_html
  received_at, imported_at
  category (str, nullable)
  processed (bool)
  generated_task_id (FK Task, nullable)
  documents (via InboundEmailAttachment junction)

EmailRule
  id, name, is_active
  priority (int, lower = evaluated first)
  match_mode: enum(all, any)
  conditions (JSON):
    [{field: "subject"|"body"|"sender", operator: "contains"|"regex"|"equals", value: "..."}]
  actions (JSON, ordered list):
    [{type: "create_task", assignee_role: "bureau", priority: "high"},
     {type: "forward", to: "email@example.com"},
     {type: "categorize", category: "invoice"},
     {type: "add_label", label: "..."}]
  -- Regex validation: compiled on save, max 500 chars, timeout on execution

EmailRuleLog
  id, rule_id (FK), email_id (FK)
  actions_triggered (JSON)
  applied_at
```

### Mailing

```
MailingCampaign
  id, name, subject, body_html
  status: enum(draft, scheduled, sending, sent, failed)
  scheduled_at (nullable), sent_at (nullable)
  created_by (FK User)
  recipients_filter (JSON, e.g. {role: "all", status: "active"})
  stats_sent, stats_bounced, stats_opened

MailingRecipient
  id, campaign_id (FK), user_id (FK)
  email (snapshot at send time)
  status: enum(pending, sent, bounced, opened)
  sent_at, bounced_at, opened_at
  bounce_type: enum(hard, soft, nullable)
```

### Treasury

```
Transaction
  id, type: enum(income, expense)
  amount (Decimal), date, description
  category (str), created_by (FK User)
  source: enum(manual, event, expense, donation, membership)
  source_id (int, nullable)
  tags (M2M → Tag)

Tag
  id, label, color (hex)

-- Tiime export: deferred to V2
```

### Audit Trail

```
AuditLog
  id
  user_id (FK User, nullable — nullable for system actions)
  timestamp
  entity_type (str)    -- "user", "machine", "event", etc.
  entity_id (int)
  action: enum(create, update, delete)
  changes (JSON)       -- {"field": {"old": value, "new": value}}

-- Populated via SQLAlchemy event listeners (after_insert, after_update, after_delete)
-- Note: entity_type + entity_id is acceptable here — this is a log, not a relation
```

---

## 6. Gmail OAuth2 Integration

- Scopes: `gmail.readonly` (inbound), `gmail.send` (mailing), `gmail.modify` (labels)
- OAuth2 flow: web application flow (single association account, not per-user)
- Tokens stored in DB encrypted with `MultiFernet` (supports key rotation)
- Celery beat: poll inbox every N minutes (configurable)
- Rate limiting: 100 emails/hour outbound (configurable via `MAILING_RATE_LIMIT`)
- Bounce detection: parse NDR (Non-Delivery Report) messages in inbox
- Google Workspace account
- **Token health monitoring**:
  - Celery beat checks token validity on every poll cycle
  - Dashboard alert if token is invalid or refresh fails
  - Admin page to re-authorize OAuth flow
  - Key rotation: add new Fernet key to `ENCRYPTION_KEYS` list, old tokens decrypted with old key and re-encrypted on next use

---

## 7. HelloAsso Integration

- Token stored in `.env` as `HELLOASSO_API_TOKEN`
- Webhooks (if available) or polling for new memberships
- Creates `Membership` records with `source=helloasso`
- Manual cash memberships created via portal form
- Response data validated with Pydantic models

---

## 8. Permissions Model

| Feature | Member | Bureau |
|---|---|---|
| View events | ✓ | ✓ |
| Create/edit events | — | ✓ |
| View machines | ✓ | ✓ |
| Edit machines | — | ✓ |
| View tasks | ✓ | ✓ |
| Assign tasks | — | ✓ |
| Claim task (self) | ✓ | ✓ |
| View members list | — | ✓ |
| Edit members | — | ✓ |
| Treasury | — | ✓ |
| Email rules | — | ✓ |
| Mailing campaigns | — | ✓ |
| Submit center feedback | public (signed URL) | ✓ |
| Moderate center feedback | — | ✓ |
| View guestbook | ✓ | ✓ |
| View audit log | — | ✓ |

---

## 9. Frontend Principles

- **Bootstrap 5.3** — utility classes, lists, tables — **no cards**
- **No inline JavaScript** — all in `static/js/`
- **HTMX 2.0** for partial page updates, form submissions, modals
- **Alpine.js 3.x** for local UI state (dropdowns, confirmations, filters)
- **Lightbox** for photo/video gallery (Alpine.js based, no external dependency)
- **Accessibility**: WCAG 2.1 AA target
  - Semantic HTML (nav, main, section, article, aside)
  - ARIA labels on interactive elements
  - Keyboard navigation on all modals and dropdowns
  - Sufficient color contrast
  - Screen reader tested with axe-core in CI (`pytest-playwright`)
- **CSP**: enforced via flask-talisman, nonce-based for any inline scripts if unavoidable

---

## 10. Deployment

### Production — docker-compose.yml

```yaml
services:
  app:
    build: ./docker/app.dockerfile
    env_file: .env
    volumes:
      - uploads:/data/uploads
    depends_on: [db, redis]

  worker:
    build: ./docker/celery.dockerfile
    command: celery -A app.tasks worker --loglevel=info
    env_file: .env
    depends_on: [db, redis]

  beat:
    build: ./docker/celery.dockerfile
    command: celery -A app.tasks beat --loglevel=info
    env_file: .env
    depends_on: [redis]

  db:
    image: postgres:17-alpine
    env_file: .env
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redisdata:/data

volumes:
  uploads:
  pgdata:
  redisdata:

# Note: reverse proxy (TLS, domain) managed separately by operator
```

### Development — docker-compose.dev.yml

```yaml
# Only infrastructure services — app, worker, beat run natively
services:
  db:
    image: postgres:17-alpine
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: assoportail
      POSTGRES_PASSWORD: devpassword
      POSTGRES_DB: assoportail_dev
    volumes:
      - pgdata_dev:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

volumes:
  pgdata_dev:

# Dev workflow:
#   docker compose -f docker-compose.dev.yml up -d
#   flask run --debug
#   celery -A app.tasks worker --loglevel=debug
#   celery -A app.tasks beat --loglevel=debug
```

---

## 11. Development Phases

### Phase 1 — Foundation
- Project scaffold, pyproject.toml, Ruff config
- Docker Compose (dev + prod)
- `.env.example`
- Flask application factory + extensions (db, login, talisman, limiter)
- Config classes (Dev / Prod / Test)
- AuditLog model + SQLAlchemy event listeners
- Base templates (layout, nav, flash, error pages — Bootstrap 5)
- Auth blueprint (login, logout, register)
- Permission decorators (`@bureau_required`, `@member_required`)
- CI/CD GitHub Actions (ci.yml + deploy.yml)
- Smoke tests for auth routes

### Phase 2 — Members
- `User` and `Membership` models (with hybrid_property status)
- Members blueprint (list, detail, create, edit)
- HelloAsso webhook/polling integration (Pydantic validation)
- Cash membership form
- Renewal tracking and alerts
- Unit tests: subscription lifecycle, HelloAsso sync

### Phase 3 — Machines & Centers
- `Machine`, `Center`, `MachineInstallation`, `MaintenanceRecord` models
- Machines blueprint (inventory, installation history)
- Centers blueprint (prospection pipeline, partner page)
- Center breakdown reporting → auto Task creation (source=center_breakdown, alert)
- Task → MaintenanceRecord conversion flow (source_task_id)
- `CenterFeedback` model + signed URL submission + moderation
- Guestbook page
- Unit tests: installation lifecycle, maintenance cost tracking, feedback moderation

### Phase 4 — Events
- `Event`, `Expense`, `CashBox`, `CashEntry` models
- Events blueprint (planning, execution, attendance)
- Expense and cash box sub-forms
- CashBox reconciliation (computed discrepancy + human validation)
- Unit tests: fund reconciliation, expense validation

### Phase 5 — Documents
- `Document` model + junction table factory
- 8 junction tables (Event, Machine, Center, Meeting, Expense, Maintenance, Email, Feedback)
- Upload handler: extension whitelist, magic number check, size limits, renaming
- Documents blueprint (list, upload, download)
- Media gallery (photos + videos, lightbox)
- Unit tests: filename convention, MIME type validation, path safety, size limits

### Phase 6 — Tasks
- `Task`, `TaskComment` models
- Tasks blueprint (board view, detail, assign, comment)
- Unit tests: role-based assignment, status transitions

### Phase 7 — Meetings
- `Meeting` model
- Meetings blueprint (create, edit, attendees, minutes, linked tasks)
- Unit tests: task creation from meeting, attendance tracking

### Phase 8 — Mailbox & Automation
- Gmail OAuth2 flow + MultiFernet token storage
- Token health monitoring + re-auth admin page
- Inbox polling (Celery beat)
- `InboundEmail`, `EmailRule`, `EmailRuleLog` models
- Rule engine: condition evaluation, regex timeout, action dispatch
- Attachment → Document pipeline
- Unit tests: rule matching (regex, contains), timeout on bad regex, action dispatch, dry-run

### Phase 9 — Mailing
- `MailingCampaign`, `MailingRecipient` models
- Campaign creation, recipient selection, scheduling
- Gmail API send with rate limiting
- Bounce detection and logging
- Unit tests: rate limiter, bounce parsing, recipient filter

### Phase 10 — Treasury
- `Transaction`, `Tag` models
- Treasury blueprint (income/expense ledger, tag management)
- CashBox → Transaction auto-creation
- Expense → Transaction auto-creation
- Unit tests: balance calculation, tag filtering

### Phase 11 — Dashboard
- KPIs: active members, upcoming events, open tasks, machines installed
- Renewal alerts (members expiring in 30 days)
- Gmail token health status
- Recent activity feed (from AuditLog)
- Smoke tests: dashboard loads for both roles

---

## 12. Quality Gates (per phase)

After each phase, an architectural audit covers:

| Angle | Checklist |
|---|---|
| **Code quality** | Ruff passes, no TODOs left, type hints complete |
| **Security** | Blackhat review checklist passed, bandit clean |
| **Accessibility** | Semantic HTML, ARIA, keyboard nav, contrast |
| **Deployment** | Docker build succeeds, migrations apply cleanly |
| **Maintainability** | No circular imports, no god files, models < 200 LOC |
| **Tests** | Smoke tests pass, unit tests cover concrete scenarios, coverage ≥ 80% |
| **Audit** | AuditLog captures all create/update/delete on audited models |

---

## 13. Environment Variables (.env.example)

```bash
# Flask
FLASK_ENV=production
SECRET_KEY=change-me

# Database
DATABASE_URL=postgresql://user:password@db:5432/assoportail

# Redis / Celery
REDIS_URL=redis://redis:6379/0

# Gmail OAuth2
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_OAUTH_REDIRECT_URI=https://yourdomain.com/auth/google/callback

# OAuth token encryption (comma-separated for MultiFernet key rotation)
ENCRYPTION_KEYS=base64-fernet-key-1,base64-fernet-key-2

# HelloAsso
HELLOASSO_API_TOKEN=
HELLOASSO_ORGANIZATION_SLUG=

# File storage
UPLOAD_FOLDER=/data/uploads
MAX_UPLOAD_PHOTO=10485760       # 10 MB
MAX_UPLOAD_VIDEO=52428800       # 50 MB
MAX_UPLOAD_DOCUMENT=20971520    # 20 MB

# Mailing
MAILING_RATE_LIMIT=100          # emails per hour
MAILING_POLL_INTERVAL=300       # seconds between inbox polls

# Security
WTF_CSRF_SECRET_KEY=change-me
```

---

*Last updated: Phase 0 — Planning complete, all recommendations integrated.*
