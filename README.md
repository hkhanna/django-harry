# harry

Email, logging, and observability primitives for Django projects.

harry is a set of independent modules that share one package. Each module is
adoptable on its own — installing the package costs nothing until you wire a
module into your settings, and no module requires another. The goal is that
every project that installs harry sends email, logs, and answers "is it up?"
the same way.

| Module | What it does | Needs |
|---|---|---|
| [`harry.email`](#email) | Transactional email via anymail, persisted to your database with delivery tracking | anymail + a transactional ESP |
| [`harry.logconfig`](#logging) | Structured logging config: readable in development, JSON in production | nothing (stdlib only) |
| [`harry.middleware`](#request-logging) | One structured access log line per request | nothing |
| [`harry.views.health`](#health-endpoint) | DB-checking healthcheck endpoint | nothing |
| [`harry.observability`](#tracing) | OpenTelemetry tracing + log↔trace correlation (optional) | the `harry[otel]` extra |

## Installation

harry is not on PyPI; install it from git. In the consuming project's
`pyproject.toml`:

```toml
dependencies = [
    "harry @ git+https://github.com/hkhanna/django-harry",
]
```

If the project will use [Tracing](#tracing), install the `otel` extra instead:

```toml
dependencies = [
    "harry[otel] @ git+https://github.com/hkhanna/django-harry",
]
```

## Email

Transactional email through [django-anymail](https://anymail.dev/), with every
message persisted as an `EmailMessage` record in your database and delivery
tracked via ESP webhooks.

**Requires:** anymail and a transactional ESP account. Nothing else from harry.

### Setup

#### 1. Add to INSTALLED_APPS

```python
INSTALLED_APPS = [
    # ...
    "anymail",
    "harry.email",
]
```

#### 2. Configure your email provider

harry uses anymail to send email through any transactional ESP. Configure your
provider in `settings.py`:

```python
# Example using Mailgun
EMAIL_BACKEND = "anymail.backends.mailgun.EmailBackend"
ANYMAIL = {
    "MAILGUN_API_KEY": env("MAILGUN_API_KEY"),
    "MAILGUN_WEBHOOK_SIGNING_KEY": env("MAILGUN_WEBHOOK_SIGNING_KEY")
}
```

See the [anymail docs](https://anymail.dev/en/stable/) for the full list of
supported providers and their settings.

#### 3. Configure site defaults

harry pulls sender defaults and template context from a `SITE_CONFIG` dict in
settings. Values here are used as fallbacks when not provided per-message.

```python
SITE_CONFIG = {
    "name": "My App",
    "default_from_name": "My App",
    "default_from_email": "hello@myapp.com",
    "company": "My App Inc.",
    "company_address": "123 Main St",
    "company_city_state_zip": "New York, NY 10001",
    "contact_email": "support@myapp.com",
    "logo_url": "https://myapp.com/logo.png",
    "logo_url_link": "https://myapp.com",
}

MAX_SUBJECT_LENGTH = 78  # Subjects are truncated to this length
```

#### 4. Create email templates

Each email needs a template prefix that maps to template files in your Django
template directories:

```
templates/
  account/
    welcome_subject.txt       # Subject line (rendered as a template)
    welcome_message.txt       # Plain text body (required)
    welcome_message.html      # HTML body (optional)
```

Templates receive `SITE_CONFIG` values as context by default, plus any custom
context you pass. For example, `welcome_message.txt`:

```
Hi {{ to_name }},

Welcome to {{ site_name }}!

Thanks,
{{ company }}
{{ company_address }}
{{ company_city_state_zip }}
```

#### 5. Run migrations

```bash
python manage.py migrate
```

#### 6. Set up delivery tracking webhooks (optional)

harry stores webhook events from your ESP so you can track whether emails were
delivered, opened, bounced, or marked as spam. Add anymail's webhook URLs to
your root `urls.py`:

```python
from django.urls import include, path

urlpatterns = [
    # ...
    path("anymail/", include("anymail.urls")),
]
```

Then configure a webhook secret in settings:

```python
ANYMAIL = {
    "MAILGUN_API_KEY": env("MAILGUN_API_KEY"),
    "MAILGUN_WEBHOOK_SIGNING_KEY": env("MAILGUN_WEBHOOK_SIGNING_KEY")
    "WEBHOOK_SECRET": env("ANYMAIL_WEBHOOK_SECRET"),
}
```

Generate a webhook secret:

```bash
python -c "from django.utils.crypto import get_random_string; print(':'.join(get_random_string(16) for _ in range(2)))"
```

Register the webhook URL with your ESP, using the secret as HTTP basic auth
credentials:

```
https://<part1>:<part2>@yourdomain.com/anymail/mailgun/tracking/
```

### Usage

The primary API is a set of service functions.

#### Basic send

```python
from harry.email.services import (
    email_message_create,
    email_message_queue,
)

email = email_message_create(
    created_by=user,
    to_name="Alice",
    to_email="alice@example.com",
    template_prefix="account/welcome",
    template_context={"coupon_code": "HELLO10"},
)

email_message_queue(email_message=email)
```

`email_message_queue` prepares the message (applies defaults, renders the
subject, validates) and sends it. It returns `True` if the email was sent, or
`False` if it was suppressed by cooldown (see below).

#### Setting the subject

The subject is rendered from `{template_prefix}_subject.txt` by default. You
can also set it explicitly:

```python
email = email_message_create(
    to_name="Alice",
    to_email="alice@example.com",
    template_prefix="account/welcome",
    subject="Welcome aboard!",
)
```

#### Custom sender and reply-to

```python
email = email_message_create(
    to_name="Alice",
    to_email="alice@example.com",
    template_prefix="account/welcome",
    sender_name="Support Team",
    sender_email="support@myapp.com",
    reply_to_name="Support",
    reply_to_email="support@myapp.com",
)
```

If `sender_name` and `sender_email` are omitted, they fall back to
`SITE_CONFIG["default_from_name"]` and `SITE_CONFIG["default_from_email"]`.

#### Attachments

Attach files after preparing the message:

```python
from harry.email.services import (
    email_message_create,
    email_message_prepare,
    email_message_attach,
    email_message_queue,
)

email = email_message_create(
    to_name="Alice",
    to_email="alice@example.com",
    template_prefix="billing/invoice",
)
email_message_prepare(email_message=email)

# From a file object
with open("invoice.pdf", "rb") as f:
    email_message_attach(
        email_message=email,
        file=f,
        filename="invoice.pdf",
        mimetype="application/pdf",
    )

# From bytes
email_message_attach(
    email_message=email,
    file=b"Name,Amount\nAlice,100",
    filename="data.csv",
    mimetype="text/csv",
)

email_message_queue(email_message=email)
```

Note: `email_message_attach` requires the message to be in `READY` status. Call
`email_message_prepare` first, then attach files, then call
`email_message_queue` (which skips re-preparing an already-`READY` message).

#### Cooldown / rate limiting

`email_message_queue` has built-in duplicate suppression. By default, it
prevents sending the same template to the same recipient by the same user more
than once within 180 seconds.

```python
# Defaults: 180s cooldown, 1 allowed, scoped to user + template + recipient
email_message_queue(email_message=email)

# Custom cooldown: allow 3 of the same template to the same recipient in 60s
email_message_queue(
    email_message=email,
    cooldown_period=60,
    cooldown_allowed=3,
    scopes=["template_prefix", "to"],
)

# No cooldown scoping: suppress if ANY email was sent in the last 60s
email_message_queue(
    email_message=email,
    cooldown_period=60,
    cooldown_allowed=1,
    scopes=[],
)
```

Available scopes: `"created_by"`, `"template_prefix"`, `"to"`. Suppressed
emails are saved with status `CANCELED`.

#### Duplicating a sent email

```python
from harry.email.services import email_message_duplicate

duplicate = email_message_duplicate(original=email)
# duplicate is a new EmailMessage in READY status with all attachments copied
email_message_queue(email_message=duplicate)
```

#### Querying email history

All emails are persisted as Django model instances:

```python
from harry.email.models import EmailMessage

# All emails to a recipient
EmailMessage.objects.filter(to_email="alice@example.com")

# Failed emails
EmailMessage.objects.filter(status="error")

# Bounced emails in the last 24 hours
from django.utils import timezone
from datetime import timedelta

EmailMessage.objects.filter(
    status="bounced",
    sent_at__gte=timezone.now() - timedelta(hours=24),
)
```

### Reference: email status lifecycle

Every `EmailMessage` moves through these statuses:

```
                 CANCELED
                 ▲
                 │
NEW ──> READY ──┼──> PENDING ──> ACCEPTED ──> DELIVERED
                │      │                         │
                ▼      ▼                         ├──> OPENED ──> CLICKED
              ERROR  ERROR                       ├──> BOUNCED
                                                 ├──> REJECTED
                                                 ├──> COMPLAINED
                                                 └──> UNSUBSCRIBED
```

Unrecognized ESP event types are stored as `UNKNOWN`.

| Status | Meaning |
|---|---|
| `NEW` | Created, not yet prepared |
| `READY` | Prepared with defaults applied, validated, ready to send |
| `PENDING` | Handed off to the email backend |
| `ACCEPTED` | Backend accepted the message |
| `DELIVERED` | ESP confirmed delivery to recipient's mail server |
| `OPENED` | Recipient opened the email (if tracking is enabled) |
| `CLICKED` | Recipient clicked a link in the email (if tracking is enabled) |
| `BOUNCED` | Delivery failed (hard or soft bounce) |
| `REJECTED` | ESP rejected the message before delivery |
| `COMPLAINED` | Recipient marked the email as spam |
| `UNSUBSCRIBED` | Recipient unsubscribed via email headers |
| `UNKNOWN` | ESP reported an unrecognized event type |
| `CANCELED` | Suppressed by cooldown |
| `ERROR` | Something went wrong during preparation or sending |

## Logging

A reusable logging configuration so every project that installs harry logs the
same way: human-readable console output in development, structured JSON in
production.

**Requires:** nothing. It has no extra dependencies — it builds a standard
Django [`LOGGING`](https://docs.djangoproject.com/en/5.2/topics/logging/)
dictionary from stdlib logging.

### Setup

In your project's `settings.py`:

```python
from harry.logconfig import build_logging_config

LOGGING = build_logging_config()
```

That's it. By default the configuration:

- logs human-readable, colorless console output in development and **structured JSON** in
  production;
- writes to **stdout** (12-factor) — no file handlers, so the platform (systemd, Docker, your
  log collector) owns persistence and rotation;
- keeps `disable_existing_loggers` off so Django's and third-party loggers keep working;
- configures the `django`, `django.request`, `django.security`, and `harry` loggers plus the
  root logger.

### Reference: configuration

`build_logging_config()` resolves each setting from its keyword argument, then an environment
variable, then a per-environment default:

| Argument | Env var | Values | Default |
|---|---|---|---|
| `env` | `DJANGO_ENV` | `local` / `test` / `prod` | `local` |
| `level` | `DJANGO_LOG_LEVEL` | any logging level | `DEBUG` (local), `WARNING` (test), `INFO` (prod) |
| `fmt` | `DJANGO_LOG_FORMAT` | `console` / `json` | `console` (local/test), `json` (prod) |

```python
# Add or override loggers for your project without losing the defaults:
LOGGING = build_logging_config(
    extra_loggers={"myapp": {"level": "INFO", "handlers": ["console"], "propagate": False}},
)
```

The merge is shallow: an entry in `extra_loggers` replaces a built-in logger wholesale, so
when overriding one (say, `django.request`) spell out `handlers` and `propagate` too.

`env` is read from `os.environ`, not `settings.DEBUG`, because the builder runs while
`settings.py` is still being evaluated. Set `DJANGO_ENV` in each environment.

A production log line looks like:

```json
{"ts": "2026-06-30T12:34:56.789012+00:00", "level": "INFO", "logger": "harry.email.services", "msg": "Sent email message", "message_id": "…"}
```

Anything you pass via `logger.info("…", extra={"message_id": mid})` is merged into the JSON.

Where the JSON goes after stdout is a deployment concern, not a logging one —
see "[Deployment](#deployment-shipping-logs-and-traces-to-signoz)".

## Request logging

`RequestLogMiddleware` emits one structured access line per request — readable
in development (`GET /invoices/42 200 (12ms)`), structured in production:

```json
{"ts": "…", "level": "INFO", "logger": "harry.request", "msg": "GET /invoices/42 200 (12ms)",
 "method": "GET", "path": "/invoices/42", "status": 200, "duration_ms": 12, "user_id": 7}
```

**Requires:** nothing — it works with any `LOGGING` configuration, though the
structured output shown assumes [harry's logging config](#logging).

### Setup

Add the middleware to `MIDDLEWARE`, after `AuthenticationMiddleware` (it reads
`request.user`):

```python
MIDDLEWARE = [
    # ...
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # ...
    "harry.middleware.RequestLogMiddleware",
]
```

Each request now emits one line at `INFO` on the `harry.request` logger.

There is no on/off setting: membership in `MIDDLEWARE` is the switch. To silence access
lines in one environment without touching `MIDDLEWARE`, raise the logger's level:

```python
LOGGING = build_logging_config(
    extra_loggers={"harry.request": {"level": "WARNING", "handlers": ["console"], "propagate": False}},
)
```

### Reference: fields and ignore paths

Field notes:

- `duration_ms` measures middleware entry to response return — for streaming responses
  that is time-to-headers, not time-to-last-byte.
- `user_id` is the authenticated user's primary key, else `null`.
- Client IP, user agent, and response size are deliberately absent — your reverse proxy's
  access log owns those. The query string is also excluded: it's the classic place
  password-reset tokens and magic links leak into logs. The request id concept is absent
  too — when [tracing](#tracing) is on, `trace_id` is the per-request id on every line.

#### Ignoring noise endpoints

Requests whose path is in `REQUEST_LOG_IGNORE_PATHS` get no access line. Entries ending
in `/` match as path prefixes; all other entries match exactly — never by substring, so
an entry can't silently swallow a real route. The default:

```python
REQUEST_LOG_IGNORE_PATHS = {
    "/favicon.ico",
    "/robots.txt",
    "/apple-touch-icon.png",
    "/apple-touch-icon-precomposed.png",
    "/.well-known/",   # trailing slash → prefix match
}
```

Healthcheck paths are deliberately *not* ignored: a probe every 30 seconds is a useful
status/latency heartbeat. If you find it too chatty, add your healthcheck path
to the set (overriding replaces the default set wholesale).

## Health endpoint

One shared healthcheck view so "is it up, can it reach its database" is
answered identically in every project. It's the target for an **external uptime
monitor** — the one alert internal tooling can't provide.

**Requires:** nothing.

### Setup

Wire it yourself (nothing registers the URL automatically):

```python
from django.urls import path

from harry.views import health

urlpatterns = [
    # ...
    path("health/", health),
]
```

An unauthenticated `GET /health/` (no CSRF token needed) returns `200` with
`{"status": "ok"}` when the default database answers `SELECT 1`, and `503` with
`{"status": "error", "detail": "database unavailable"}` when it doesn't — never a
stack trace or connection string. The check is deliberately database-only: cache,
storage, and external-API checks make healthchecks flaky and page you for
dependencies that have their own monitoring.

Point your uptime monitor at the URL as a deployment step; expect probes every
15–30 seconds — the view is fast and side-effect free.

Healthcheck probes *do* get access lines from `RequestLogMiddleware` (the path is
deliberately not in the default ignore set), giving you a steady status/latency
heartbeat. If that's too chatty, add your health path to
`REQUEST_LOG_IGNORE_PATHS` (see
"[Ignoring noise endpoints](#ignoring-noise-endpoints)" above).

## Tracing

Everything so far works without OpenTelemetry. This module is the opt-in
enhancer: with it, every request gets a distributed trace, and every log line —
access lines included — carries the request's `trace_id`/`span_id`, so your
logs link to their traces. Without it, nothing above loses any functionality.

**Requires:** the `harry[otel]` extra (see [Installation](#installation)). No
OpenTelemetry package is a hard dependency of harry. Enhances
[Logging](#logging) and [Request logging](#request-logging); required by
neither.

### Setup

Install the extra:

```toml
dependencies = [
    "harry[otel] @ git+https://github.com/hkhanna/django-harry",
]
```

Call `init_observability()` in `settings.py`:

```python
# settings.py
from harry.observability import init_observability

init_observability()  # OTEL_SERVICE_NAME + OTEL_EXPORTER_OTLP_ENDPOINT from env
```

```bash
OTEL_SERVICE_NAME=myapp \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod \
gunicorn myproject.wsgi
```

That one call wires everything:

- a `TracerProvider` with an OTLP span exporter — endpoint, headers, and protocol (gRPC
  by default; set `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` for HTTP) come from the
  standard `OTEL_EXPORTER_OTLP_*` env vars, the service name from the argument
  (`init_observability(service_name="myapp")`) or `OTEL_SERVICE_NAME`;
- Django, psycopg, and requests instrumentation — each enabled only when the library is
  installed;
- log↔trace correlation — trace ids are stamped onto every `LogRecord`, and harry's JSON
  formatter promotes them to `trace_id`/`span_id`/`service.name`.

Because initialization is programmatic — there is no `opentelemetry-instrument` wrapper —
the same `settings.py` works identically under gunicorn, `manage.py` commands, and any
future task runner without changing how processes are launched. Calling it twice (e.g.
under the autoreloader) is a no-op, and calling it without the extra installed raises an
`ImportError` that tells you to install `harry[otel]`. The extra is also the single place
the interdependent `opentelemetry-*` package versions are managed — don't pin them
yourself.

<details>
<summary>Manual path (without the <code>harry[otel]</code> extra)</summary>

To assemble your own instrumentation set instead, OpenTelemetry's zero-code path works
with harry unchanged — the formatter promotes the trace ids either way:

```bash
pip install opentelemetry-distro opentelemetry-exporter-otlp
opentelemetry-bootstrap -a install   # detects Django etc. and installs instrumentations
```

```bash
OTEL_SERVICE_NAME=myapp \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod \
OTEL_PYTHON_LOG_CORRELATION=true \
opentelemetry-instrument gunicorn myproject.wsgi
```

`OTEL_PYTHON_LOG_CORRELATION=true` is the piece that stamps trace ids onto log records;
without it you get traces but uncorrelated logs.

</details>

## Deployment: shipping logs and traces to SigNoz

harry only writes JSON to stdout; shipping it anywhere is a deployment choice,
made on the server rather than in the app. The production assumption is
[SigNoz](https://signoz.io/). This section covers the pieces that live outside
the app process: the host's OpenTelemetry Collector and the reverse proxy.

### Shipping logs with the host Collector

Because you'll already run an OpenTelemetry Collector on the host for OS/VPS
metrics and system logs, the simplest setup is to let that **same Collector**
pick up the application logs — no OpenTelemetry packages needed in the app
itself:

1. Run Django under **systemd** (e.g. gunicorn). harry's JSON goes to stdout, which systemd
   captures in the **journal**.
2. Install the OpenTelemetry Collector on the host and give it one pipeline for host metrics
   and one for logs (full sketch below).

Four mappings in the log pipeline do the real work — they are the deployment
half of the contract `harry.logconfig.JSONFormatter` defines:

- **`level` → severity.** journald stamps everything on stdout as INFO, which
  would flatten an app ERROR to INFO in SigNoz — a `json_parser` operator
  re-parses the real level out of harry's JSON.
- **`ts` → record timestamp**, so the record carries the time the app logged,
  not the time the collector read the journal.
- **`trace_id`/`span_id` → the log data model's trace fields.** This mapping —
  not just a shared string attribute — is what makes log↔trace click-through
  work in SigNoz. The fields are present when [Tracing](#tracing) is on; no
  in-app log exporter and no change to harry required.
- **`msg` → log body**, giving a clean human message in SigNoz, with
  level/logger and any `extra` fields remaining as queryable attributes.

The canonical collector configuration — kept in sync with `JSONFormatter`'s
field names — lives in
[docs/observability-signoz.md](docs/observability-signoz.md), alongside
everything else on the SigNoz side (alerts, uptime, retention).

### One trace across the full lifecycle

For the proxy, app server, and Django to share a single trace id, the proxy must *join
the trace*. With Caddy, that's the `tracing` directive:

```
example.com {
    tracing
    reverse_proxy 127.0.0.1:8000
}
```

Caddy continues an inbound W3C trace context or starts a new trace, exports its own span
(honoring the standard `OTEL_EXPORTER_OTLP_*` / `OTEL_SERVICE_NAME` env vars), injects
`traceparent` into the proxied request — so Django's instrumentation continues the same
trace — and stamps `traceID`/`spanID` onto its own access logs. The result in SigNoz:
Caddy's span, Django's spans, and every app log line share one trace id.

To also surface the id in gunicorn's access log:

```python
# gunicorn.conf.py
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sus traceparent=%({traceparent}i)s'
```

Neither piece is required: any reverse proxy (or none) and any WSGI/ASGI server
work. Without Caddy `tracing` (or another tracing proxy), the trace originates in
Django — requests still trace and logs still correlate, but the proxy hop is not part of
the trace. "One id, full lifecycle" specifically requires the proxy to participate.

## Observability conventions

The sections above document the machinery harry ships. This section is the standard for
how projects that install harry *use* it — what the code should do, plus the launch
steps that can't be code (SigNoz alerts, uptime monitors, cron pings). This README is
the canonical home for these conventions: it travels with the package into every
project, so a consuming project's `CLAUDE.md` should carry a one-line pointer
("Observability follows django-harry's README conventions.") rather than restate them.

### Logging conventions

**Log events with fields, not prose.** The message is a snake_case verb phrase naming
what happened (`payment_failed`, `user_registered`); everything variable goes in
`extra`:

```python
logger.info(
    "payment_failed",
    extra={"user_id": user.id, "order_id": order.id, "amount": amount},
)
```

`JSONFormatter` lifts `extra` keys to top-level JSON keys, so `user_id`, `order_id`,
and `amount` are directly queryable in SigNoz. `"Payment failed for user 7"` is not.

**No structlog — decided.** `harry.logconfig` (stdlib logging + `JSONFormatter`)
already provides structured JSON events, trace-id promotion, and canonical-field
clobber protection with zero dependencies. The `extra={}` syntax is the accepted cost
of staying on stdlib. Do not re-open this without a concrete failure of the current
setup. The rationale is recorded in
[ADR 0001](docs/adr/0001-stdlib-logging-not-structlog.md).

**Level semantics.**

| Level | Means |
|---|---|
| `ERROR` | A human should eventually look at this |
| `WARNING` | Unexpected but handled |
| `INFO` | Notable business event |
| `DEBUG` | Off in production (the `prod` profile's default level is `INFO`) |

**Always bind identifying context.** Every event carries `user_id` and the primary
entity id involved (`order_id`, `message_id`, …). Request-level correlation comes from
`trace_id` (stamped by OpenTelemetry, see "[Tracing](#tracing)"), not from hand-added
request-id fields.

**Never log secrets** — tokens, query strings, or full request bodies. These are the
same exclusions `RequestLogMiddleware` makes deliberately; don't reintroduce them via
`extra`.

**No bare `print()` in app code.** It bypasses levels, formatting, and shipping.

### Tracing conventions

Auto-instrumentation from `init_observability()` already covers views, the ORM, and
outbound HTTP. Add a manual span only around a business operation worth timing on its
own:

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)


def generate_invoice(order):
    with tracer.start_as_current_span("generate_invoice"):
        ...
```

Exceptions must reach the active span — auto-instrumentation records unhandled ones.
Never swallow an exception silently: if you catch and handle it, log it with context at
`WARNING` or `ERROR` so it still surfaces.

### Alerting principles

These live in SigNoz and the uptime monitor, so they can't ship as code — they're
configured per project at launch (see the checklist below).

- Alert on **symptoms users feel** — errors, latency, downtime — not causes like CPU or
  disk. Causes belong on dashboards, not pagers.
- Every alert must be **actionable**. An alert ignored twice gets deleted or fixed.
- The **standard set** per service: four SigNoz alerts —
  1. Error rate above threshold for 5 minutes
  2. New exception type
  3. p95 latency above threshold
  4. Any ERROR-level log for 5 minutes (catches failures outside request
     spans — management commands, startup, cron)

  — plus an external uptime check on `/health/` (see
  "[Health endpoint](#health-endpoint)"). Definitions, thresholds, and the
  provisioning script are in
  [docs/observability-signoz.md](docs/observability-signoz.md).

## Per-project integration checklist

Everything harry needs from a project that installs it. Each group maps to one
feature — skip the group if the project skips the feature. Items are terse on
purpose; the linked section is the source of *how*.

**Email** ("[Email](#email)")

- [ ] `anymail` and `harry.email` in `INSTALLED_APPS`; ESP configured via `EMAIL_BACKEND` + `ANYMAIL`
- [ ] `SITE_CONFIG` and `MAX_SUBJECT_LENGTH` set
- [ ] Email templates created (`*_subject.txt`, `*_message.txt`)
- [ ] Migrations run
- [ ] Anymail webhook URLs wired; webhook secret registered with the ESP

**Logging** ("[Logging](#logging)")

- [ ] `LOGGING = build_logging_config()` in settings
- [ ] `DJANGO_ENV=prod` set in production

**Request logging** ("[Request logging](#request-logging)")

- [ ] `RequestLogMiddleware` in `MIDDLEWARE`, after `AuthenticationMiddleware`

**Health endpoint** ("[Health endpoint](#health-endpoint)")

- [ ] `/health/` wired in `urls.py`

**Tracing** ("[Tracing](#tracing)")

- [ ] `harry[otel]` installed; `init_observability()` called in settings
- [ ] `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, and
      `OTEL_RESOURCE_ATTRIBUTES=deployment.environment=…` set

**Launch — configured outside the code** ("[Deployment](#deployment-shipping-logs-and-traces-to-signoz)",
"[Alerting principles](#alerting-principles)"; the step-by-step SigNoz-side
procedure is in [docs/observability-signoz.md](docs/observability-signoz.md))

- [ ] Access lines and traces visible in SigNoz; log lines carry `trace_id`
      and click through to their trace
- [ ] `/health/` registered with the external uptime monitor
- [ ] The four standard alerts configured in SigNoz
      (`scripts/signoz-alerts.py provision <service>`)
- [ ] Cron/scheduled jobs ping [Healthchecks.io](https://healthchecks.io/) on
      completion

## Development

### Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install dependencies (--all-extras so the OpenTelemetry integration tests run)
uv sync --all-extras
```

### Running Tests

```bash
uv run pytest
```

### Code Quality

Format and lint code:

```bash
make format
```

Run type checking:

```bash
make mypy
```

### Database Migrations

Create new migrations:

```bash
make migrations
```
